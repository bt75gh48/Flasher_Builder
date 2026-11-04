"""Native Linux and Windows build worker.

No GitHub runner or device connection is required. Private release assets
use GitHub CLI authentication on the current system.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid
import zipfile

if __package__:
    from . import ota_catalog
    from scripts.http_download import DownloadError, download_url, executable, redact_output
    from .remote_zip import ota_metadata, RangeError
    from .device_layout import template_subtree, image_folder
    from .host import tool, helper_command, configure_path
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import ota_catalog
    from scripts.http_download import DownloadError, download_url, executable, redact_output
    from remote_zip import ota_metadata, RangeError
    from device_layout import template_subtree, image_folder
    from host import tool, helper_command, configure_path

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
GIB = 1024 ** 3


class BuildError(Exception):
    pass


class Cancelled(BuildError):
    pass


class Catalog:
    """Read the explicit workflow tables without an extra YAML dependency."""
    def __init__(self, root=ROOT):
        self.root = Path(root)
        text = (self.root / '.github/workflows/build_tools.yml').read_text(encoding='utf-8')
        self.devices = dict(re.findall(r'\["([^"]+)"\]="([^"]+)"', text.split('declare -A DEV_MAP=(', 1)[1].split(')', 1)[0]))
        self.region_sizes = {k: (int(cn), int(other)) for k, cn, other in re.findall(r'\["([^"]+)"\]="(\d+) (\d+)"', text)}
        self.sizes = {k: sizes[0] for k, sizes in self.region_sizes.items()}
        self.builders = {tuple(key.split('|')): value + '.sh' for key, value in re.findall(r'BUILDER_MAP\["([^"]+)"\]="([^"]+)"', text)}
        if not self.devices or not self.builders:
            raise BuildError('The workflow device/builder tables could not be read.')

    def types(self, device):
        dev = self.devices[device]
        return list(dict.fromkeys(t for t, d, r in self.builders if d == dev))

    def regions(self, device, kind):
        dev = self.devices[device]
        available = {r for t, d, r in self.builders if (t, d) == (kind, dev)}
        standard = ('GLO', 'EU', 'IN', 'NA', 'CN')
        if available.difference(standard): return sorted(available)
        return [r for r in standard if r in available]

    def super_size(self, device, region):
        return self.region_sizes.get(device, (0, 0))[0 if region == 'CN' else 1]

    def selection(self, device, kind, region):
        dev = self.devices.get(device)
        builder = self.builders.get((kind, dev, region))
        if not builder:
            raise BuildError('This device, flasher type and region combination is unsupported.')
        subtree = template_subtree(dev, region)
        template = self.root / 'flasher_scripts' / dev / subtree / ('super' if 'Super' in kind else 'regional')
        if not template.is_dir():
            raise BuildError(f'The workflow lists this combination, but its template is missing: {template}')
        if 'Hybrid' in kind and not (template / 'META-INF').is_dir():
            raise BuildError(f'Hybrid recovery files are missing: {template / "META-INF"}')
        return dev, builder, template


def clean_source(value):
    return value.strip().strip('"\'').replace('\\u0026', '&')


def is_url(source):
    return urlsplit(source).scheme.lower() in ('http', 'https')


def validate_ota(path):
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo('payload.bin')
            if info.file_size < 24:
                raise BuildError('The OTA payload is empty or truncated.')
            with z.open(info) as f:
                if f.read(4) != b'CrAU':
                    raise BuildError('payload.bin has an invalid Android payload header.')
            metadata = z.read('META-INF/com/android/metadata').decode('utf-8')
    except (OSError, zipfile.BadZipFile, KeyError, UnicodeError) as e:
        raise BuildError(f'Choose a full Android OTA ZIP containing payload.bin and metadata: {e}') from e
    fields = dict(line.split('=', 1) for line in metadata.splitlines() if '=' in line)
    if fields.get('ota-type', 'AB') != 'AB':
        raise BuildError('This appears to be an incremental OTA. A FULL OTA is required.')
    if fields.get('pre-build') or fields.get('pre-build-incremental'):
        # Reconstructed full packages can retain their original delta metadata.
        # Accept them only when every partition is self-contained and complete.
        from flasher_builder.payload import verify_full_payload
        try:
            with zipfile.ZipFile(path) as z, z.open('payload.bin') as stream:
                verify_full_payload(stream, info.file_size)
        except (ValueError, TypeError, OSError, zipfile.BadZipFile) as e:
            raise BuildError(f'This appears to be an incremental OTA. A FULL OTA is required: {e}') from e
    version = fields.get('version_name') or fields.get('ota_version') or fields.get('post-build-incremental', '')
    matches = re.findall(r'\d+(?:\.\d+){1,4}', version)
    version = max(matches, key=len) if matches else re.sub(r'[^\w.()+-]', '_', version)[:80]
    if not version:
        raise BuildError('No version_name, ota_version or post-build-incremental found in OTA metadata.')
    return version, info.file_size, fields


class Worker:
    def __init__(self, cancel=None):
        self.cancel = Path(cancel) if cancel else None

    def check(self):
        if self.cancel and self.cancel.exists():
            raise Cancelled('Task cancelled.')

    def metadata(self, function, *args):
        """Keep cancellation responsive during bounded catalog/link HTTP requests."""
        result = queue.Queue()
        def task():
            try:
                result.put((True, function(*args)))
            except Exception as e:
                result.put((False, e))
        threading.Thread(target=task, daemon=True).start()
        while True:
            self.check()
            try:
                ok, value = result.get(timeout=.15)
                self.check()
                if ok:
                    return value
                raise value
            except queue.Empty:
                pass

    def run(self, args, cwd=None, env=None):
        self.check()
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        proc = subprocess.Popen([str(a) for a in args], cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                                bufsize=1, **options)
        lines = queue.Queue()
        def reader():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        try:
            done = False
            while not done or proc.poll() is None:
                self.check()
                try:
                    line = lines.get(timeout=.15)
                    if line is None:
                        done = True
                    else:
                        print(redact_output(line), end='', flush=True)
                except queue.Empty:
                    pass
            if proc.wait():
                raise BuildError(f'{Path(str(args[0])).name} failed (exit {proc.returncode}). See the log above.')
        finally:
            if proc.poll() is None:
                if os.name == 'nt':
                    # Helpers observe the same cancellation marker and clean up.
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                        if proc.poll() is None: proc.kill()
                        proc.wait()
                else:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
            proc.stdout.close()
            thread.join(timeout=1)

    def copy(self, source, dest):
        """Chunked copies keep cancellation responsive with multi-gigabyte OTAs."""
        with open(source, 'rb') as src, open(dest, 'wb') as dst:
            while True:
                self.check()
                chunk = src.read(4 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
        shutil.copystat(source, dest)

    def source(self, source, dest):
        source = clean_source(source)
        if not source:
            raise BuildError('Select a full OTA file or enter its HTTPS download URL.')
        if is_url(source):
            print(f'Downloading {dest.name} from {urlsplit(source).hostname}', flush=True)
            def valid_download(path):
                try:
                    validate_ota(path); return True
                except BuildError:
                    return False
            try:
                download_url(source, Path(str(dest) + '.part'), root=ROOT, check=self.check, verify=valid_download, headers=(
                    'User-Agent: Dalvik/2.1.0 (Linux; U; Android 16; CPH2653 Build/AP3A.240617.008)',
                    'userId: oplus-ota|15002017', 'language: en-US', 'brand: oneplus-exp'))
            except DownloadError as e:
                raise BuildError(str(e)) from e
            Path(str(dest) + '.part').replace(dest)
        else:
            path = Path(source).expanduser().resolve()
            if not path.is_file():
                raise BuildError(f'OTA file not found: {path}')
            validate_ota(path)
            print(f'Copying {path.name} into the job folder…', flush=True)
            self.copy(path, dest)
        return validate_ota(dest)


def check_environment(root=ROOT):
    if sys.platform not in ('linux', 'win32') or platform.machine().lower() not in ('x86_64', 'amd64'):
        raise BuildError('This package requires x86-64 Linux or Windows.')
    configure_path(root)
    if not executable('aria2c', root) and not executable('curl', root):
        raise BuildError('Download tools missing. Keep bundled aria2c beside the app or install curl.')
    for rel in (tool(root, 'otaripper'), tool(root, 'lpmake'), Path(root) / 'build_tools_assets/avbtool.py'):
        if not Path(rel).is_file():
            raise BuildError(f'Missing project asset: {rel}. Keep the full project beside the launcher.')
    print(f'Backend: {platform.system()} {platform.machine()} • Python {platform.python_version()}', flush=True)
    print('Required tools and bundled binaries found.', flush=True)
    print('Downloads: aria2c (8 connections), with curl fallback.', flush=True)
    print(f'Free space in project: {shutil.disk_usage(root).free / GIB:.1f} GiB', flush=True)


def normalize_assets(repo, target, dev, template):
    # Copy only the selected device. Build scripts may delete their OUT_DIR;
    # the GUI always gives them a fresh, private directory inside this job.
    for directory in ('build_scripts', 'scripts', 'otaripper', 'lpmake', 'build_tools_assets', 'Platform-Tools'):
        shutil.copytree(repo / directory, target / directory)
    for directory in ('bloat', 'flasher_scripts'):
        if directory == 'bloat' and (repo / 'bloat/assets-manifest.json').is_file():
            continue
        src = repo / directory / dev
        if src.exists():
            shutil.copytree(src, target / directory / dev)
    manifest = repo / 'bloat/assets-manifest.json'
    if manifest.is_file():
        (target / 'bloat').mkdir(exist_ok=True)
        shutil.copy2(manifest, target / 'bloat/assets-manifest.json')
    for p in target.rglob('*'):
        if p.is_file() and (p.suffix == '.sh' or p.name in ('otaripper', 'lpmake', 'update-binary', 'pack_super', 'unpack_super', 'pack_super_regional', 'volprompt')):
            if p.suffix == '.sh':
                p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
            p.chmod(p.stat().st_mode | 0o111)


def package(worker, result, dest):
    partial = dest.with_suffix('.zip.part')
    print('Compressing final ZIP…', flush=True)
    with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as z:
        for path in sorted(result.rglob('*')):
            worker.check()
            if path.is_file():
                # ZipInfo preserves executable modes for Linux flasher scripts.
                info = zipfile.ZipInfo.from_file(path, path.relative_to(result).as_posix())
                # Windows stat() cannot preserve Unix executable bits. Recovery
                # and Linux launchers in a Windows-built package still need them.
                executable = path.suffix == '.sh' or path.name in ('update-binary', 'aera-installer', 'pack_super', 'unpack_super', 'pack_super_regional', 'volprompt')
                if path.suffix not in ('.img', '.zip'):
                    with path.open('rb') as header:
                        executable = executable or header.read(4) == b'\x7fELF'
                info.create_system = 3
                info.external_attr = (0o100755 if executable else 0o100644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                info._compresslevel = 3
                with path.open('rb') as src, z.open(info, 'w', force_zip64=True) as dst:
                    while True:
                        worker.check()
                        chunk = src.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
    worker.check()
    digest = hashlib.sha256()
    with partial.open('rb') as f:
        while True:
            worker.check()
            chunk = f.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    partial.replace(dest)
    dest.with_suffix('.zip.sha256').write_text(f'{digest.hexdigest()}  {dest.name}\n', encoding='utf-8')


def require_local_bloat(args, repo, dev):
    if (repo / 'bloat/assets-manifest.json').is_file():
        source = Path(getattr(args, 'bloat_dir', '') or os.environ.get('BLOAT_SOURCE_DIR', str(repo / 'bloat'))).expanduser().resolve()
        Worker(args.cancel).run(helper_command(ROOT, 'materialize',
            '--root', repo, '--source', source, '--cache', source / '.cache', '--check-only',
            '--device', dev, '--kind', args.kind, '--region', args.region,
            *(['--cancel', args.cancel] if args.cancel else [])))


def selected_release(args, role):
    release_id = getattr(args, role + '_catalog_id', '')
    if not release_id:
        return None
    target = ota_catalog.targets(args.device, args.kind, args.region).get(role)
    if target is None:
        raise BuildError('A BASE OTA is not used for this selection.')
    return ota_catalog.find_release(Path(args.repo) / '.ota-catalog', release_id, *target)


def prepare_ota(worker, args, role, destination):
    entry = worker.metadata(selected_release, args, role) if getattr(args, role + '_catalog_id', '') else None
    source = getattr(args, role)
    if entry:
        print(f'Catalog OTA: {entry["device"]} / {entry["region"]} / {entry["version"]}', flush=True)
        size = entry.get('size')
        if isinstance(size, int) and size > 0 and shutil.disk_usage(destination.parent).free < size + GIB:
            raise BuildError('Not enough space to download this OTA. Choose another output drive.')
        worker.check()
        source = worker.metadata(ota_catalog.resolve_link, entry)
        worker.check()
    standalone = getattr(args, 'download_ota', False)
    if standalone: print('STEP 2/5 • Download full OTA', flush=True)
    result = worker.source(source, destination)
    if standalone: print('STEP 3/5 • Verify full OTA ZIP', flush=True)
    if entry:
        size = entry.get('size')
        if isinstance(size, int) and size > 0 and destination.stat().st_size != size:
            raise BuildError('Downloaded OTA size does not match the catalog. Retry the download.')
        checksums = {name: entry[name] for name in ('md5', 'sha256') if entry.get(name)}
        if checksums:
            if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-fA-F]{' + str(32 if name == 'md5' else 64) + '}', value)
                   for name, value in checksums.items()):
                raise BuildError('The catalog has an invalid OTA checksum.')
            print('Verifying catalog ' + ' and '.join(name.upper() for name in checksums) + ' checksums…', flush=True)
            if standalone: print('STEP 4/5 • Verify catalog checksum', flush=True)
            digests = {name: hashlib.new(name) for name in checksums}
            with destination.open('rb') as f:
                while chunk := f.read(4 * 1024 * 1024):
                    worker.check()
                    for digest in digests.values(): digest.update(chunk)
            if any(digests[name].hexdigest().lower() != value.lower() for name, value in checksums.items()):
                raise BuildError('Downloaded OTA checksum does not match the catalog. Retry the download.')
    return result


def plan_matched_odm(worker, args):
    data = worker.metadata(ota_catalog.load_catalog, Path(args.repo) / '.ota-catalog')
    target = worker.metadata(selected_release, args, 'ota') if getattr(args, 'ota_catalog_id', '') else None
    if target:
        # Fail before downloading a full target if no donor exists.
        ota_catalog.matching_odm(data, target)
    return data, target


def prepare_matched_odm(worker, args, metadata, download, plan):
    data, target = plan
    actual_target = ota_catalog.release_from_metadata(data, 'OP 15R', args.region, metadata)
    if target is None:
        target = actual_target
    elif actual_target['id'] != target['id']:
        raise BuildError('The target download returned a different OTA version. ODM matching stopped.')
    patch = ota_catalog.patch_date(metadata.get('post-security-patch-level'))
    if patch != ota_catalog.patch_date(target.get('security_patch')):
        raise BuildError('The target OTA security patch differs from its catalog entry. The ODM source cannot be matched safely.')
    donor = ota_catalog.matching_odm(data, target)
    print(f'Matched ODM source: {donor["device"]} / {donor["region"]} / {donor["version"]} / security patch {patch}', flush=True)
    url = worker.metadata(ota_catalog.resolve_link, donor)
    print('Verifying donor OTA metadata using HTTP ranges…', flush=True)
    try:
        donor_metadata = worker.metadata(ota_metadata, url, worker.check)
    except (RangeError, OSError, ValueError, KeyError, zipfile.BadZipFile) as e:
        raise BuildError('Could not verify the ODM source OTA using HTTP ranges. No full-OTA fallback is used.') from e
    if ota_catalog.patch_date(donor_metadata.get('post-security-patch-level')) != patch:
        raise BuildError('The donor OTA security patch differs from the target. ODM download stopped.')
    actual_donor = ota_catalog.release_from_metadata(data, 'OP ACE 6T', 'CN', donor_metadata)
    if actual_donor['id'] != donor['id']:
        raise BuildError('The donor link returned a different OTA version. ODM download stopped.')
    if __package__:
        from .native_pipeline import extract
    else:
        from native_pipeline import extract
    print('Downloading ONLY odm.img from the matched OTA; the full Ace 6T OTA is not downloaded.', flush=True)
    directory = extract(worker, Path(args.repo), url, download / 'matched-odm', ('odm',), strict=True)
    odm = directory / 'odm.img'
    if not odm.is_file() or not odm.stat().st_size or {p.name for p in directory.glob('*.img')} != {'odm.img'}:
        raise BuildError('Selective extraction did not produce exactly one non-empty odm.img.')
    digest = hashlib.sha256()
    with odm.open('rb') as stream:
        while chunk := stream.read(4 * 1024**2):
            worker.check(); digest.update(chunk)
    return odm, dict(catalog_id=donor['id'], device=donor['device'], region=donor['region'],
                     version=donor['version'], security_patch=patch, sha256=digest.hexdigest(), bytes=odm.stat().st_size)


def download_ota(args):
    import tempfile
    repo = Path(args.repo).resolve()
    Catalog(repo).selection(args.device, args.kind, args.region)
    role = args.download_role
    if not getattr(args, role + '_catalog_id', ''):
        raise BuildError('Select an OTA catalog version first.')
    destination = Path(args.download_path).expanduser().resolve()
    if destination.exists():
        raise BuildError('The destination already exists. Choose a new filename or use the existing local OTA.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    worker = Worker(args.cancel)
    with tempfile.TemporaryDirectory(prefix='.ota-download-', dir=destination.parent) as temporary:
        downloaded = Path(temporary) / 'OTA.zip'
        print('STEP 1/5 • Resolve selected OTA', flush=True)
        version, _, _ = prepare_ota(worker, args, role, downloaded)
        worker.check()
        downloaded.replace(destination)
    print(f'STEP 5/5 • OTA {version} downloaded and verified', flush=True)
    print('@@OTA_RESULT ' + json.dumps({'role': role, 'file': str(destination)}), flush=True)


@contextmanager
def build_log(job):
    """Keep failed-build diagnostics; remove a cancelled build's own job only."""
    # Tee every worker message and child output into a persistent log.
    class Tee:
        def __init__(self, original, log):
            self.original, self.log = original, log
        def write(self, text):
            self.original.write(text)
            self.log.write(text)
        def flush(self):
            self.original.flush()
            self.log.flush()
    old_out, old_err = sys.stdout, sys.stderr
    try:
        with (job / 'build.log').open('w', encoding='utf-8') as logfile:
            sys.stdout = sys.stderr = Tee(old_out, logfile)
            try:
                yield
            except BaseException as e:
                print(f'ERROR: {e}', flush=True)
                raise
            finally:
                sys.stdout, sys.stderr = old_out, old_err
    except Cancelled:
        print('Removing cancelled build output…', flush=True)
        shutil.rmtree(job)
        print('Cancelled build output removed.', flush=True)
        raise


def pack_vulnerable_abl(worker, repo, image_directory, region='EU', bloat_directory=None):
    from scripts.materialize_bloat import bloat_folder
    folder = bloat_folder('X9U', 'Super Flasher', region)
    source = Path(bloat_directory or Path(repo)/'bloat') / 'X9U' / folder / 'abl-16.0.6.img'
    expected = '4ad7f1db0c92f0a358e28bf18170a20533011299827d8d9a25cffd2218f1415d'
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
        raise BuildError('The X9U ABL 16.0.6 bloat image is missing or has an incorrect checksum. Complete Download bloat in step 2.')
    worker.check()
    worker.copy(source, Path(image_directory) / 'abl.img')
    print('Packed X9U vulnerable ABL 16.0.6 as abl.img.', flush=True)
    return dict(version='16.0.6', sha256=expected, bytes=source.stat().st_size)


def build(args):
    repo = Path(args.repo).resolve()
    check_environment(repo)
    catalog = Catalog(repo)
    dev, builder, template = catalog.selection(args.device, args.kind, args.region)
    if dev == 'X9U' and getattr(args, 'vulnerable_abl', None) is None:
        raise BuildError('Choose Yes (--vulnerable-abl) or No (--stock-abl) for X9U ABL before building.')
    if getattr(args, 'vulnerable_abl', False) and dev != 'X9U':
        raise BuildError('The vulnerable ABL option is available only for X9U builds.')
    require_local_bloat(args, repo, dev)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < GIB and not getattr(args, 'fixture', False):
        raise BuildError('Less than 1 GiB available in the output folder.')
    needs_base = builder == 'BUILD_ACE6_OOS.sh'
    no_zip = bool(getattr(args, 'no_zip', False))
    if needs_base and not args.base and not getattr(args, 'base_catalog_id', ''):
        raise BuildError('Ace 6 OxygenOS needs the Ace 6 ColorOS BASE full OTA and the 15R India TARGET full OTA.')
    job = output / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    job.mkdir()
    print(f'Job folder: {job}', flush=True)
    with build_log(job):
        worker = Worker(args.cancel)
        download = job / 'download'
        download.mkdir()
        print('STEP 1/5 • Prepare OTA', flush=True)
        odm_plan = plan_matched_odm(worker, args) if ota_catalog.needs_matched_odm(args.device, args.kind, args.region) else None
        version, payload_size, metadata = prepare_ota(worker, args, 'ota', download / 'OTA.zip')
        if needs_base:
            _, base_size, _ = prepare_ota(worker, args, 'base', download / 'BASE.zip')
            payload_size += base_size
        print(f'OTA version: {version}; target devices: {metadata.get("pre-device", "not specified")}', flush=True)
        # Sparse output + raw images + archive can coexist. Check again
        # after downloading; do not start extraction on a nearly full disk.
        required = max(2 * GIB, payload_size * ((5 if needs_base else 4) - int(no_zip)))
        free = shutil.disk_usage(job).free
        if free < required and not getattr(args, 'fixture', False):
            raise BuildError(f'Not enough disk space: {free / GIB:.1f} GiB free; allow at least {required / GIB:.1f} GiB for this payload. Choose another output drive.')
        odm, odm_receipt = prepare_matched_odm(worker, args, metadata, download, odm_plan) if odm_plan is not None else (None, None)
        print('STEP 2/5 • Stage flasher and device assets', flush=True)
        assets = job / 'assets'
        normalize_assets(repo, assets, dev, template)
        if __package__:
            from .native_pipeline import stage, build_images
        else:
            from native_pipeline import stage, build_images
        source = Path(getattr(args, 'bloat_dir', '') or os.environ.get('BLOAT_SOURCE_DIR', str(repo / 'bloat'))).expanduser().resolve()
        result = stage(worker, assets, job / 'stage', dev, args.kind, args.region, source)
        print('STEP 3/5 • Extract partitions and build flasher', flush=True)
        result = build_images(worker, assets, result, download, dev, args.kind, args.region,
                              catalog.super_size(dev, args.region), builder, odm)
        if not result.is_dir():
            raise BuildError(f'Builder did not create the expected output: {result}')
        boot_dir = result / image_folder(dev, args.region)
        abl_receipt = None
        if getattr(args, 'vulnerable_abl', False):
            abl_receipt = pack_vulnerable_abl(worker, repo, boot_dir, args.region, source)
        if not (boot_dir / 'boot.img').is_file():
            raise BuildError('Build output has no boot.img. The OTA may be incomplete or incompatible.')
        if 'Super' in args.kind and not (result / 'super.img').is_file():
            raise BuildError('Build output has no super.img.')
        suffix = 'COS' if args.region == 'CN' else args.region
        filename = ('TEST-DO-NOT-FLASH ' if getattr(args, 'fixture', False) else '') + f'{args.kind} {dev} {suffix} {version}'
        if not no_zip: filename += '.zip'
        dest = job / filename
        if no_zip:
            print('STEP 4/5 • Finalize flasher folder', flush=True)
            worker.check()
            result.replace(dest)
        else:
            print('STEP 4/5 • Package ZIP and calculate SHA-256', flush=True)
            package(worker, result, dest)
        worker.check()
        manifest = dict(device=args.device, flasher_type=args.kind, region=args.region,
                        version=version, artifact=filename, created=time.strftime('%Y-%m-%dT%H:%M:%S'),
                        test_fixture=bool(getattr(args, 'fixture', False)), output_format='folder' if no_zip else 'zip')
        if odm_receipt is not None: manifest['odm_source'] = odm_receipt
        if abl_receipt is not None: manifest['abl_source'] = abl_receipt
        (job / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        print('Removing temporary working files…', flush=True)
        for directory in (assets, job / 'stage', download):
            shutil.rmtree(directory)
        worker.check()
        print('STEP 5/5 • Complete', flush=True)
        print(f'{"Folder" if no_zip else "ZIP"}: {dest}', flush=True)
        print('@@RESULT ' + json.dumps({'job': str(job), 'folder' if no_zip else 'zip': str(dest),
                                        'format': 'folder' if no_zip else 'zip'}), flush=True)
        return dest



def download_bloat(args):
    repo = Path(args.repo).resolve()
    dev, _, _ = Catalog(repo).selection(args.device, args.kind, args.region)
    destination = Path(args.bloat_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cache = Path(os.environ.get('BLOAT_CACHE_DIR', str(destination / '.cache'))).expanduser().resolve()
    print('STEP 1/5 • Check selected bloat files', flush=True)
    print(f'Bloat folder: {destination}', flush=True)
    print('STEP 2/5 • Download and verify bloat', flush=True)
    try:
        Worker(args.cancel).run(helper_command(ROOT, 'materialize',
            '--root', repo, '--destination', destination, '--cache', cache,
            '--device', dev, '--kind', args.kind, '--region', args.region,
            *(['--cancel', args.cancel] if args.cancel else [])))
    except Cancelled:
        raise Cancelled('Bloat download cancelled. Completed images have been kept in the chosen folder.')
    print('STEP 5/5 • Bloat files ready', flush=True)
    print('@@BLOAT_RESULT ' + json.dumps({'folder': str(destination)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=str(ROOT))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--download-bloat', action='store_true')
    parser.add_argument('--download-ota', action='store_true')
    parser.add_argument('--download-role', choices=('ota', 'base'), default='ota')
    parser.add_argument('--download-path', default='')
    parser.add_argument('--ota-catalog-id', default='')
    parser.add_argument('--base-catalog-id', default='')
    parser.add_argument('--bloat-dir', default='')
    parser.add_argument('--device', default='Oneplus 13')
    parser.add_argument('--kind', default='SuperHybrid Flasher')
    parser.add_argument('--region', default='GLO')
    parser.add_argument('--ota', default='')
    parser.add_argument('--base', default='')
    parser.add_argument('--output', default=str(ROOT / 'builds'))
    parser.add_argument('--no-zip', action='store_true', help='Keep the finished flasher folder instead of creating a ZIP.')
    abl_choice = parser.add_mutually_exclusive_group()
    abl_choice.add_argument('--vulnerable-abl', dest='vulnerable_abl', action='store_true', default=None, help='X9U only: replace the extracted abl.img with ABL 16.0.6.')
    abl_choice.add_argument('--stock-abl', dest='vulnerable_abl', action='store_false', help='X9U only: keep the extracted OTA ABL.')
    parser.add_argument('--cancel')
    args = parser.parse_args()
    try:
        if args.check:
            check_environment(Path(args.repo))
            print(f'{len(Catalog(args.repo).builders)} supported workflow combinations.', flush=True)
        elif args.download_bloat:
            if not args.bloat_dir:
                raise BuildError('Choose a bloat download folder.')
            download_bloat(args)
        elif args.download_ota:
            if not args.download_path:
                raise BuildError('Choose an OTA download destination.')
            download_ota(args)
        elif args.self_test:
            if __package__:
                from .selftest import run_self_test
            else:
                from selftest import run_self_test
            run_self_test(args)
        else:
            build(args)
        return 0
    except Cancelled as e:
        print(str(e), flush=True)
        return 130
    except (BuildError, ota_catalog.CatalogError, OSError, ValueError) as e:
        print(f'ERROR: {e}', flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
