"""Exercise actual OTA extraction, super packing and packaging with tiny fixtures.

The generated payload uses Android's update_metadata.proto wire format with
REPLACE operations. It contains test bytes, never flashable firmware.
"""
from __future__ import annotations
from argparse import Namespace
from contextlib import contextmanager
import hashlib
import gzip
import io
import json
import re
from pathlib import Path
import shutil
import struct
import time
import uuid
import zipfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

if __package__:
    from .backend import BuildError, Worker, build, check_environment
    from .host import helper_command
else:
    from __main__ import BuildError, Worker, build, check_environment
    from host import helper_command


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def number(field, value):
    return varint(field << 3) + varint(value)


def blob(field, data):
    return varint((field << 3) | 2) + varint(len(data)) + data


def make_ota(path, partitions, metadata=None, operation_type=0):
    manifest = number(3, 4096) + number(12, 0)
    payload_data = b''
    for name, data in partitions.items():
        assert len(data) % 4096 == 0
        extent = number(1, 0) + number(2, len(data) // 4096)
        operation = (number(1, operation_type) + number(2, len(payload_data)) + number(3, len(data))
                     + blob(6, extent) + number(7, len(data)) + blob(8, hashlib.sha256(data).digest()))
        info = number(1, len(data)) + blob(2, hashlib.sha256(data).digest())
        partition = blob(1, name.encode()) + blob(7, info) + blob(8, operation)
        manifest += blob(13, partition)
        payload_data += data
    payload = b'CrAU' + struct.pack('>QQI', 2, len(manifest), 0) + manifest + payload_data
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('payload.bin', payload)
        z.writestr('META-INF/com/android/metadata', metadata or 'ota-type=AB\npre-device=TEST-ONLY\nversion_name=TEST_1.2.3.4\n')
    return partitions


@contextmanager
def serve_ranges(directory):
    """Loopback fixture server recording every requested range and byte count."""
    requests = []
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass

        def send_head(self):
            self.remaining = None
            path = Path(self.translate_path(self.path))
            value = self.headers.get('Range')
            if not value or not path.is_file():
                if path.is_file(): requests.append((path.name, 0, path.stat().st_size-1))
                return super().send_head()
            match = re.fullmatch(r'bytes=(\d+)-(\d+)', value)
            if not match or not (0 <= int(match[1]) <= int(match[2]) < path.stat().st_size):
                self.send_error(416); return None
            start, end = int(match[1]), int(match[2])
            self.remaining = end-start+1
            requests.append((path.name, start, end))
            self.send_response(206); self.send_header('Content-Length', str(self.remaining))
            self.send_header('Content-Range', f'bytes {start}-{end}/{path.stat().st_size}')
            self.send_header('Accept-Ranges', 'bytes'); self.end_headers()
            stream = path.open('rb'); stream.seek(start); return stream

        def copyfile(self, source, destination):
            if self.remaining is None: return super().copyfile(source, destination)
            while self.remaining:
                data = source.read(min(65536, self.remaining))
                if not data: break
                destination.write(data); self.remaining -= len(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown(); server.server_close(); thread.join()


def sparse_range(image, start, length):
    """Read a small range without expanding a multi-gigabyte sparse super image."""
    stream = io.BytesIO(image)
    magic, major, minor, header_size, chunk_size, block_size, blocks, count, checksum = struct.unpack('<IHHHHIIII', stream.read(28))
    if magic != 0xed26ff3a or major != 1: raise BuildError('Self-test: invalid sparse header.')
    stream.seek(header_size)
    position = 0; result = bytearray()
    for _ in range(count):
        kind, reserved, chunk_blocks, total = struct.unpack('<HHII', stream.read(12))
        stream.seek(chunk_size-12, 1)
        data_start = stream.tell(); expanded = chunk_blocks*block_size
        left = max(start, position); right = min(start+length, position+expanded)
        if right > left:
            offset = left-position; size = right-left
            if kind == 0xcac1:
                stream.seek(data_start+offset); result.extend(stream.read(size))
            elif kind == 0xcac2:
                stream.seek(data_start); pattern = stream.read(4)
                result.extend((pattern*((offset%4+size+3)//4))[offset%4:offset%4+size])
            elif kind == 0xcac3: result.extend(b'\0'*size)
            else: raise BuildError('Self-test: unsupported sparse chunk.')
        position += expanded
        stream.seek(data_start + total-chunk_size)
        if len(result) == length: return bytes(result)
    raise BuildError('Self-test: sparse range is truncated.')


def verify_super(image, expected):
    # LP primary metadata starts after the reserved region and two geometries.
    header = sparse_range(image, 12288, 256)
    if struct.unpack_from('<I', header)[0] != 0x414c5030:
        raise BuildError('Self-test: missing logical partition metadata.')
    header_size = struct.unpack_from('<I', header, 8)[0]
    tables_size = struct.unpack_from('<I', header, 44)[0]
    tables = sparse_range(image, 12288+header_size, tables_size)
    if hashlib.sha256(tables).digest() != header[48:80]: raise BuildError('Self-test: LP table checksum mismatch.')
    offset, count, size = struct.unpack_from('<III', header, 80)
    ext_offset, ext_count, ext_size = struct.unpack_from('<III', header, 92)
    partitions = {}
    for index in range(count):
        entry = tables[offset+index*size:offset+(index+1)*size]
        name = entry[:36].split(b'\0')[0].decode()
        first, extents = struct.unpack_from('<II', entry, 40)
        if extents:
            extent = tables[ext_offset+first*ext_size:ext_offset+(first+1)*ext_size]
            sectors, kind, sector, device = struct.unpack('<QIQI', extent)
            if kind != 0: raise BuildError('Self-test: expected a linear LP extent.')
            partitions[name] = (sector*512, sectors*512)
    for name, data in expected.items():
        if name+'_a' not in partitions: raise BuildError('Self-test: partition missing from super: '+name)
        offset, size = partitions[name+'_a']
        if sparse_range(image, offset, len(data)) != data:
            raise BuildError('Self-test: wrong source bytes in super: '+name)
    return partitions


def run_self_test(args):
    repo = Path(args.repo).resolve()
    check_environment(repo)
    worker = Worker(args.cancel)
    base = Path(args.output).resolve() / ('self-test-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    base.mkdir(parents=True)
    fixture = base / 'fixture-repo'
    fixture.mkdir()
    print('SELF-TEST ONLY — generated images are NOT firmware. DO NOT FLASH.', flush=True)
    print(f'Self-test folder: {base}', flush=True)
    for directory in ('.github', 'build_scripts', 'scripts', 'otaripper', 'lpmake', 'build_tools_assets', 'Platform-Tools'):
        shutil.copytree(repo / directory, fixture / directory)
    for dev in ('13', '15', 'Ace6', 'Ace6T', 'NEO8', 'X9U'):
        shutil.copytree(repo / 'flasher_scripts' / dev, fixture / 'flasher_scripts' / dev)
        for region in ('GLO_BLOAT', 'EU_BLOAT', 'IN_BLOAT', 'NA_BLOAT', 'CN_BLOAT', 'OOS_BLOAT', 'COS_FILES_HERE'):
            folder = fixture / 'bloat' / dev / region
            folder.mkdir(parents=True)
            for name in ('my_preload', 'my_company', 'my_region', 'oplusstanvbk', 'system_dlkm_oki'):
                (folder / (name + '.img')).write_bytes(b'\0' * 4096)
    partitions = {name: (('TEST ONLY ' + name + '\n').encode() * 300)[:4096].ljust(4096, b'\0')
                  for name in ('boot', 'init_boot', 'system', 'system_ext', 'product', 'vendor', 'vendor_dlkm', 'odm', 'system_dlkm', 'my_region', 'my_bigball', 'my_carrier', 'my_engineering', 'my_heytap', 'my_manifest', 'my_product', 'my_stock', 'my_preload', 'my_company', 'system_dlkm_oki')}
    partitions['abl'] = b'TEST ONLY STOCK ABL\n'.ljust(4096, b'A')
    # Ace6's real builder reads and rewrites vbmeta using the bundled avbtool.
    vbmeta = base / 'vbmeta.img'
    worker.run(helper_command(repo, 'avbtool', 'make_vbmeta_image', '--output', vbmeta, '--padding_size', '4096'))
    for name in ('vbmeta', 'vbmeta_system', 'vbmeta_vendor'):
        partitions[name] = vbmeta.read_bytes()
    ota = base / 'TEST-ONLY-full-OTA.zip'
    make_ota(ota, partitions)
    # Exercise the actual bundled downloader in frozen builds as well as source
    # runs. This server exposes only generated test data on loopback.
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(base)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    downloaded = base / 'TEST-ONLY-downloaded-OTA.zip'
    try:
        print('SELF-TEST: bundled downloader over local HTTP', flush=True)
        worker.source(f'http://127.0.0.1:{server.server_port}/{ota.name}', downloaded)
        if downloaded.read_bytes() != ota.read_bytes():
            raise BuildError('Self-test: downloaded OTA bytes differ.')
    finally:
        server.shutdown(); server.server_close(); thread.join()
    ota = downloaded
    target_parts = dict(partitions, system=b'TARGET SYSTEM\0'.ljust(4096, b'T'),
                        product=b'TARGET PRODUCT\0'.ljust(4096, b'P'))
    target_ota = base / 'TEST-ONLY-target-OTA.zip'
    make_ota(target_ota, target_parts)
    na_ota = base/'TEST-ONLY-reconstructed-NA.zip'
    make_ota(na_ota,partitions,'ota-type=AB\npre-build=older-build\npre-build-incremental=older\nversion_name=TEST_1.2.3.4\n')
    cases = [('Oneplus 13', 'SuperHybrid Flasher', 'GLO'),
             ('Oneplus 13', 'RegionalHybrid Flasher', 'CN'),
             ('Oneplus 13', 'RegionalHybrid Flasher', 'GLO'),
             ('Oneplus Ace 6', 'Super Flasher', 'CN'),
             ('Oneplus Ace 6', 'SuperHybrid Flasher', 'IN'),
             ('Realme NEO8', 'Super Flasher', 'CN'),
             ('Oneplus 13', 'SuperHybrid Flasher', 'NA'),
             ('Oneplus 13', 'RegionalHybrid Flasher', 'NA'),
             ('Oneplus 15', 'SuperHybrid Flasher', 'NA'),
             ('Oneplus 15', 'RegionalHybrid Flasher', 'NA')]
    for device, kind, region in cases:
        worker.check()
        print(f'\nSELF-TEST: {device} / {kind} / {region}', flush=True)
        conversion = device == 'Oneplus Ace 6' and region == 'IN'
        dest = build(Namespace(repo=str(fixture), output=str(base / 'results'), device=device, kind=kind,
                               region=region, ota=str(na_ota if region == 'NA' else target_ota if conversion else ota), base=str(ota), cancel=args.cancel, fixture=True))
        with zipfile.ZipFile(dest) as z:
            boot = ('COS_FILES_HERE' if region == 'CN' else 'OOS_FILES_HERE') + '/boot.img'
            if z.read(boot) != partitions['boot']:
                raise BuildError('Self-test: extracted boot image differs from payload.')
            if 'Super' in kind:
                if z.read('super.img')[:4] != b'\x3a\xff\x26\xed':
                    raise BuildError('Self-test: super.img is not an Android sparse image.')
                verify_super(z.read('super.img'), dict(system=(target_parts if conversion else partitions)['system'],
                                                      product=(target_parts if conversion else partitions)['product'], vendor=partitions['vendor']))
                if device == 'Realme NEO8':
                    image = z.read('super.img')
                    expanded = struct.unpack_from('<II', image, 12)
                    if expanded[0] * expanded[1] != 18790481920:
                        raise BuildError('Self-test: NEO8 physical super size is incorrect.')
                    verify_super(image, {name: b'\0' * 4096 for name in ('my_preload', 'my_company', 'system_dlkm_oki')})
                    for script in ('Super_Flasher_COS.bat', 'Super_Flasher_COS.sh'):
                        if 'Realme NEO8' not in z.read(script).decode():
                            raise BuildError('Self-test: NEO8 flasher script missing from package.')
                if conversion:
                    for name in ('vbmeta', 'vbmeta_system', 'vbmeta_vendor'):
                        if struct.unpack_from('>I', z.read('OOS_FILES_HERE/'+name+'.img'), 120)[0] != 3:
                            raise BuildError('Self-test: base vbmeta flags were not patched.')
            bad = z.testzip()
            if bad:
                raise BuildError(f'Self-test: ZIP checksum failed: {bad}')
        expected = dest.with_suffix('.zip.sha256').read_text().split()[0]
        if hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
            raise BuildError('Self-test: package SHA-256 mismatch.')
    print('\nSELF-TEST: finished flasher folder without ZIP compression', flush=True)
    folder = build(Namespace(repo=str(fixture), output=str(base / 'results'), device='Oneplus 13',
                             kind='SuperHybrid Flasher', region='GLO', ota=str(ota), base='',
                             cancel=args.cancel, fixture=True, no_zip=True))
    if not folder.is_dir() or (folder / 'OOS_FILES_HERE/boot.img').read_bytes() != partitions['boot']:
        raise BuildError('Self-test: unzipped flasher boot image differs from payload.')
    verify_super((folder / 'super.img').read_bytes(), {name: partitions[name] for name in ('system', 'product', 'vendor')})
    if not (folder / 'Super_Flasher.bat').is_file():
        raise BuildError('Self-test: unzipped flasher scripts are missing.')
    info = json.loads((folder.parent / 'build.json').read_text(encoding='utf-8'))
    if info['output_format'] != 'folder' or info['artifact'] != folder.name:
        raise BuildError('Self-test: folder build history is incorrect.')
    if {p.name for p in folder.parent.iterdir()} != {folder.name, 'build.json', 'build.log'}:
        raise BuildError('Self-test: folder build left an archive or temporary working files.')
    odm_data = b'PATCH MATCHED ODM\n'.ljust(4096, b'D')
    donor = base / 'TEST-ONLY-donor.zip'
    make_ota(donor, {'unrelated': b'U' * (4 * 1024**2), 'odm': odm_data},
             'ota-type=AB\nversion_name=PLR110_16.0.10.500(CN01)\npost-security-patch-level=2026-08-01\n')
    with serve_ranges(base) as (address, requests):
        entries = [dict(id='matched-donor', device='OP ACE 6T', region='CN', model='PLR110',
                        version='PLR110_16.0.10.500(CN01)', security_patch='2026-08-01',
                        source_url=address+'/'+donor.name)]
        for region, model in (('IN', 'CPH2767'), ('GLO', 'CPH2769')):
            target = base / ('TEST-ONLY-15R-'+region+'.zip')
            version = model+'_16.0.10.500(EX01)'
            make_ota(target, dict(partitions, odm=b'WRONG TARGET ODM'.ljust(4096, b'W')),
                     f'ota-type=AB\nversion_name={version}\npost-security-patch-level=2026-08-01\n')
            entries.append(dict(id='target-'+region, device='OP 15R', region=region, model=model,
                                version=version, security_patch='2026-08-01', source_url=address+'/'+target.name))
        entries.append(dict(entries[0], id='wrong-newer-donor', version='PLR110_17.0.0.101(CN01)',
                            security_patch='2026-09-01', is_latest=True))
        cache = fixture / '.ota-catalog'; cache.mkdir()
        (cache / 'catalog.json').write_text(json.dumps(dict(fetched_at=time.time(), etag='',
            data=dict(api_version='1', releases=entries))), encoding='utf-8')
        for region in ('IN', 'GLO'):
            print(f'\nSELF-TEST: Ace 6T {region}, remote patch-matched ODM only', flush=True)
            dest = build(Namespace(repo=str(fixture), output=str(base / 'results'), device='Oneplus Ace 6T',
                kind='Super Flasher', region=region, ota='', ota_catalog_id='target-'+region,
                base='', cancel=args.cancel, fixture=True))
            with zipfile.ZipFile(dest) as archive:
                verify_super(archive.read('super.img'), dict(odm=odm_data, system=partitions['system']))
            receipt = json.loads((dest.parent / 'build.json').read_text(encoding='utf-8'))['odm_source']
            if receipt['catalog_id'] != 'matched-donor' or receipt['sha256'] != hashlib.sha256(odm_data).hexdigest():
                raise BuildError('Self-test: matched ODM provenance is incorrect.')
        ranges = [(start, end) for name, start, end in requests if name == donor.name]
        if not ranges or sum(end-start+1 for start, end in ranges) >= donor.stat().st_size:
            raise BuildError('Self-test: selective ODM extraction transferred too much data.')
        if any(start <= 2*1024**2 <= end for start, end in ranges):
            raise BuildError('Self-test: ODM extraction downloaded an unrelated payload partition.')
    # Exercise multipart transport in the actual source/frozen materializer.
    from scripts import materialize_bloat as materializer
    print('\nSELF-TEST: multipart bloat transport with per-part and original-image verification', flush=True)
    preload = b'X9U SHARED EU PRELOAD\n'.ljust(4096, b'C'); transport = gzip.compress(preload, mtime=0)
    raw_sha = hashlib.sha256(preload).hexdigest(); transport_sha = hashlib.sha256(transport).hexdigest()
    zero = b'\0'*4096; zero_sha = hashlib.sha256(zero).hexdigest()
    chunks = [transport[:len(transport)//2], transport[len(transport)//2:]]
    parts = []
    for number, chunk in enumerate(chunks, 1):
        name=f'TEST-ONLY-preload.part{number}';(base/name).write_bytes(chunk)
        parts.append(dict(asset_id=number,asset_name=name,bytes=len(chunk),sha256=hashlib.sha256(chunk).hexdigest()))
    manifest = dict(schema_version=1,repository='owner/test-fixture',tag='fixture',assets={
        raw_sha:dict(asset_id=1,asset_name='preload.img.gz',encoding='gzip',raw_bytes=len(preload),raw_sha256=raw_sha,
                     bytes=len(transport),sha256=transport_sha,parts=parts),
        zero_sha:dict(asset_id=3,asset_name='zero.img',encoding='identity',raw_bytes=len(zero),raw_sha256=zero_sha,
                      bytes=len(zero),sha256=zero_sha)},files={
        f'bloat/X9U/{region}_BLOAT/{name}.img':raw_sha if (region,name)==('EU','my_preload') else zero_sha
        for region in ('CN','EU') for name in ('my_preload','my_company')})
    regions = ('APC','CN','EU','ID','IN','JP','MX','MY','SG','TH','TW','VN')
    regional_preloads = {'CN':preload, 'EU':preload}
    for region in (r for r in regions if r not in ('CN','EU')):
        data = f'X9U {region} PRELOAD\n'.encode().ljust(4096,b'R')
        checksum = hashlib.sha256(data).hexdigest()
        regional_preloads[region] = data
        folder = fixture/'bloat/X9U'/f'{region}_BLOAT'; folder.mkdir(exist_ok=True)
        (folder/'my_preload.img').write_bytes(data)
        (folder/'my_company.img').write_bytes(zero)
        manifest['assets'][checksum] = dict(asset_id=4,asset_name=f'{region}-preload.img',encoding='identity',
            raw_bytes=len(data),raw_sha256=checksum,bytes=len(data),sha256=checksum)
        manifest['files'][f'bloat/X9U/{region}_BLOAT/my_preload.img'] = checksum
        manifest['files'][f'bloat/X9U/{region}_BLOAT/my_company.img'] = zero_sha
    (fixture/'bloat/assets-manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    (fixture/'bloat/X9U/EU_BLOAT/my_preload.img').unlink()
    with serve_ranges(base) as (address, requests):
        original_url, original_cancel = materializer.release_url, materializer.CANCEL_FILE
        try:
            materializer.release_url=lambda repo,tag,name:address+'/'+name
            materializer.CANCEL_FILE=worker.cancel
            materializer.materialize(fixture,base/'multipart-cache',manifest,'X9U','Super Flasher','CN')
        finally:
            materializer.release_url, materializer.CANCEL_FILE = original_url, original_cancel
    if (fixture/'bloat/X9U/EU_BLOAT/my_preload.img').read_bytes()!=preload:
        raise BuildError('Self-test: multipart bloat image bytes differ.')
    # The optional ABL is a shared bloat asset, rather than an app binary asset.
    abl_key = '4ad7f1db0c92f0a358e28bf18170a20533011299827d8d9a25cffd2218f1415d'
    published = materializer.load_manifest(repo/'bloat/assets-manifest.json')
    abl_asset = published['assets'][abl_key]
    abl_path = 'bloat/X9U/EU_BLOAT/abl-16.0.6.img'
    abl_manifest = dict(published, assets={abl_key:abl_asset}, files={abl_path:abl_key})
    materializer.materialize(fixture,base/'abl-cache',abl_manifest,'X9U','Super Flasher','EU',source=repo/'bloat')
    abl_data = (fixture/abl_path).read_bytes()
    manifest['assets'][abl_key] = abl_asset
    for region in regions:
        folder = fixture/'bloat/X9U'/f'{region}_BLOAT'
        (folder/'abl-16.0.6.img').write_bytes(abl_data)
        manifest['files'][f'bloat/X9U/{region}_BLOAT/abl-16.0.6.img'] = abl_key
    (fixture/'bloat/assets-manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    for kind in ('Super Flasher','Regional Flasher'):
        for region in regions:
            print(f'\nSELF-TEST: Find X9 Ultra / {kind} / {region}',flush=True)
            vulnerable_abl = kind == 'Super Flasher' and region == 'IN'
            dest=build(Namespace(repo=str(fixture),output=str(base/'results'),device='OPPO Find X9 Ultra',
                kind=kind,region=region,ota=str(ota),base='',cancel=args.cancel,fixture=True,vulnerable_abl=vulnerable_abl))
            expected_preload=regional_preloads[region]
            with zipfile.ZipFile(dest) as archive:
                if archive.read('OTA_FILES_HERE/boot.img')!=partitions['boot']:
                    raise BuildError('Self-test: X9U boot image folder is incorrect.')
                expected_abl = abl_data if vulnerable_abl else partitions['abl']
                if any(name.endswith('/abl-16.0.6.img') for name in archive.namelist()):
                    raise BuildError('Self-test: ABL support file leaked into the flashable package.')
                if archive.read('OTA_FILES_HERE/abl.img') != expected_abl:
                    raise BuildError('Self-test: X9U ABL selection is incorrect.')
                if 'Super' in kind:
                    expected_size = 19671285760 if region == 'CN' else 20451426304
                    if int(archive.read('META-INF/com/google/android/super-size')) != expected_size:
                        raise BuildError('Self-test: X9U recovery super size is incorrect.')
                    if 'OTA_FILES_HERE' not in archive.read('META-INF/com/google/android/aera-installer').decode():
                        raise BuildError('Self-test: X9U recovery image layout is incorrect.')
                elif any(name.startswith('META-INF/') for name in archive.namelist()):
                    raise BuildError('Self-test: regional X9U build contains recovery metadata.')
                if 'Super' in kind:
                    image=archive.read('super.img');block_size,blocks=struct.unpack_from('<II',image,12)
                    if block_size*blocks!=(19671285760 if region=='CN' else 20451426304):
                        raise BuildError('Self-test: regional X9U physical super size is incorrect.')
                    verify_super(image,dict(my_preload=expected_preload,my_company=zero,
                        system=partitions['system'],system_dlkm_oki=partitions['system_dlkm_oki']))
                else:
                    for name,data in (('my_preload',expected_preload),('my_company',zero)):
                        if archive.read('OTA_FILES_HERE/'+name+'.img')!=data:
                            raise BuildError('Self-test: OTA extraction overwrote staged X9U bloat.')
    (base / 'SELF-TEST-PASSED.txt').write_text('All pipeline builds, regional bloat mapping and multipart transport passed with the REAL otaripper, lpmake and avbtool.\nGenerated packages contain test bytes. DO NOT FLASH.\n', encoding='utf-8')
    print('\nSELF-TEST PASSED: extraction, three builders, sparse super image, vbmeta patching, ZIP integrity, SHA-256, unzipped folder output, remote patch-matched ODM only, regional super sizes and multipart bloat, regional bloat mapping, reconstructed NA packages, X9U hybrid recovery and shared ABL choices.', flush=True)
