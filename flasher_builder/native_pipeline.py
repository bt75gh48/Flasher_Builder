"""Portable implementation of the repository's staging and three builder scripts."""
from pathlib import Path, PurePosixPath
import os
import shutil
import struct

if __package__:
    from .backend import BuildError, ROOT
    from .host import tool, helper_command
    from .device_layout import template_subtree, image_folder
else:
    from __main__ import BuildError, ROOT
    from host import tool, helper_command
    from device_layout import template_subtree, image_folder
from scripts.materialize_bloat import bloat_folder, load_manifest, selected_paths

SUPER_PARTS = ('my_bigball', 'my_carrier', 'my_company', 'my_engineering', 'my_heytap',
    'my_manifest', 'my_preload', 'my_product', 'my_region', 'my_stock', 'odm', 'product',
    'system', 'system_dlkm', 'system_dlkm_gki', 'system_dlkm_oki', 'system_ext', 'vendor', 'vendor_dlkm')
EXTRACT_TO_SUPER = tuple(p for p in SUPER_PARTS if p not in ('my_company', 'my_preload', 'system_dlkm_gki', 'system_dlkm_oki'))
TARGET_PARTS = ('my_bigball', 'my_carrier', 'my_engineering', 'my_heytap', 'my_manifest',
                'my_product', 'my_region', 'my_stock', 'system', 'system_ext', 'product')
BASE_PARTS = ('odm', 'system_dlkm', 'vendor', 'vendor_dlkm')


def copy_tree(worker, source, destination):
    def copy(src, dst):
        worker.check(); worker.copy(src, dst)
        return dst
    shutil.copytree(source, destination, copy_function=copy, dirs_exist_ok=True)


def stage(worker, assets, destination, dev, kind, region, source):
    name = kind + (' CN' if region == 'CN' else '')
    result = destination / name
    template = assets / 'flasher_scripts' / dev / template_subtree(dev, region) / ('super' if 'Super' in kind else 'regional')
    copy_tree(worker, template, result)
    if dev == 'X9U' and 'Super' not in kind:
        shutil.rmtree(result / 'META-INF', ignore_errors=True)
    # Recovery's updater-script must retain Unix line endings on Windows too.
    for script in result.rglob('updater-script'):
        script.write_bytes(script.read_bytes().replace(b'\r\n', b'\n'))
    if (assets / 'Platform-Tools').is_dir():
        copy_tree(worker, assets / 'Platform-Tools', result / 'Platform-Tools')
    manifest_path = assets / 'bloat/assets-manifest.json'
    if manifest_path.is_file():
        worker.run(helper_command(ROOT, 'materialize', '--root', assets, '--cache', source / '.cache',
            '--source', source, '--local-only', '--device', dev, '--kind', kind, '--region', region,
            *(['--cancel', worker.cancel] if worker.cancel else [])))
        manifest = load_manifest(manifest_path)
    else:
        # Synthetic self-test fixtures (and legacy local image checkouts).
        manifest = {'files': {p.relative_to(assets).as_posix(): '' for p in (assets / 'bloat' / dev).rglob('*.img')}}
    main = bloat_folder(dev, kind, region)
    for relative in selected_paths(manifest, dev, kind, region):
        path = PurePosixPath(relative)
        if dev == 'X9U' and path.name == 'abl-16.0.6.img':
            continue  # Kept in the bloat cache; the explicit ABL choice applies it after extraction.
        if len(path.parts) != 4:
            continue  # Legacy nested archival copies are not staging inputs.
        folder = path.parts[-2]
        if folder == main:
            output = result / ('Super' if 'Super' in kind else image_folder(dev, region))
        elif folder == 'GLO_BLOAT' and region == 'CN':
            output = result / 'OOS_FILES_HERE'
        else:
            output = result / 'COS_FILES_HERE'
        output.mkdir(parents=True, exist_ok=True)
        worker.copy(assets / path, output / path.name)
    return result


def image_size(path):
    with path.open('rb') as f:
        header = f.read(28)
    if len(header) >= 28 and struct.unpack_from('<I', header)[0] == 0xed26ff3a:
        magic, major, minor, file_header, chunk_header, block_size, blocks, chunks, checksum = struct.unpack('<IHHHHIIII', header)
        if major != 1 or file_header < 28 or chunk_header < 12 or not block_size or block_size % 512:
            raise BuildError('Invalid Android sparse image: ' + path.name)
        return block_size * blocks
    return path.stat().st_size


def pack_super(worker, assets, directory, size):
    directory.mkdir(parents=True, exist_ok=True)
    for required in ('system', 'vendor', 'product'):
        path = directory / (required + '.img')
        if not path.is_file() or not path.stat().st_size:
            raise BuildError('Required partition missing: ' + path.name)
    group_size = size - 4 * 1024**2
    if group_size <= 0:
        raise BuildError('Invalid physical super partition size.')
    command = [tool(assets, 'lpmake'), '--metadata-size', '65536', '--metadata-slots', '3',
        '--virtual-ab', '--super-name', 'super', '--device', f'super:{size}',
        '--group', f'qti_dynamic_partitions_a:{group_size}', '--group', f'qti_dynamic_partitions_b:{group_size}']
    added = []
    for name in SUPER_PARTS:
        image = directory / (name + '.img')
        if image.is_file():
            expanded = image_size(image)
            command += ['--partition', f'{name}_a:readonly:{expanded}:qti_dynamic_partitions_a',
                        '--image', f'{name}_a={name}.img']
            added.append(name)
    for name in added:
        command += ['--partition', f'{name}_b:readonly:0:qti_dynamic_partitions_b']
    # Relative paths keep the AOSP Windows tool independent of host path encoding.
    command += ['--sparse', '--output', 'super.img']
    worker.run(command, cwd=directory, env=os.environ | {'TMPDIR': '.'})
    if not (directory / 'super.img').is_file():
        raise BuildError('lpmake did not create super.img.')


def extract(worker, assets, ota, directory, partitions=None, strict=False):
    directory.mkdir(parents=True, exist_ok=True)
    command = [tool(assets, 'otaripper'), '--no-open', '--output-dir', directory, ota]
    if partitions:
        command += ['--partitions', ','.join(partitions)]
    if strict: command += ['--strict', '--threads', '8']
    worker.run(command, cwd=directory.parent)
    # Recent otaripper releases create a versioned child even with -o.
    image_folders = {p.parent for p in directory.rglob('*.img')}
    if len(image_folders) != 1:
        raise BuildError('OTA extraction did not produce one unambiguous image directory.')
    return image_folders.pop()


def build_images(worker, assets, result, download, dev, kind, region, size, builder, odm=None):
    boot_dir = result / image_folder(dev, region)
    boot_dir.mkdir(parents=True, exist_ok=True)
    super_dir = result / 'Super'
    if builder == 'BUILD_ACE6_OOS.sh':
        work = download / '.ace6_oos_build'
        base = extract(worker, assets, download / 'BASE.zip', work / 'BASE')
        target = extract(worker, assets, download / 'OTA.zip', work / 'TARGET', TARGET_PARTS)
        super_dir.mkdir(parents=True, exist_ok=True)
        included = set()
        for directory, parts in ((target, TARGET_PARTS), (base, BASE_PARTS)):
            for name in parts:
                image = directory / (name + '.img')
                if image.is_file():
                    worker.copy(image, super_dir / image.name); included.add(image.name)
        for name in ('vbmeta', 'vbmeta_system', 'vbmeta_vendor'):
            image = base / (name + '.img')
            if not image.is_file(): raise BuildError('Missing BASE ' + image.name)
            worker.run(helper_command(ROOT, 'avbtool', 'make_vbmeta_image', '--output', boot_dir / image.name,
                '--include_descriptors_from_image', image, '--flags', '3', '--rollback_index', '0'))
            included.add(image.name)
        for image in base.glob('*.img'):
            if image.name not in included:
                image.replace(boot_dir / image.name)
        shutil.rmtree(work)
    else:
        images = extract(worker, assets, download / 'OTA.zip', download / 'extracted_images')
        if not (images / 'boot.img').is_file(): raise BuildError('Full OTA boot.img missing.')
        if 'Super' in kind:
            super_dir.mkdir(parents=True, exist_ok=True)
            if dev in ('Ace5', 'Ace6T') and region != 'CN':
                (images / 'odm.img').unlink(missing_ok=True)
            for name in (SUPER_PARTS if dev == 'X9U' else EXTRACT_TO_SUPER):
                image = images / (name + '.img')
                if image.is_file():
                    destination = super_dir / image.name
                    if dev == 'X9U' and destination.exists(): image.unlink()
                    else: image.replace(destination)
        for image in images.iterdir():
            if image.is_file():
                destination = boot_dir / image.name
                if dev == 'X9U' and destination.exists(): image.unlink()
                else: image.replace(destination)
        shutil.rmtree(images)
    if 'Super' in kind:
        if dev == 'Ace6T' and region in ('IN', 'GLO'):
            if odm is None or not odm.is_file() or not odm.stat().st_size:
                raise BuildError('The patch-matched Ace 6T ODM image is missing. A static ODM will not be used.')
            worker.copy(odm, super_dir / 'odm.img')
        pack_super(worker, assets, super_dir, size)
        (super_dir / 'super.img').replace(result / 'super.img')
        if dev == 'X9U':
            (result / 'META-INF/com/google/android/super-size').write_bytes((str(size) + '\n').encode('ascii'))
        shutil.rmtree(super_dir)
        for name in ('dtbo.img', 'dtbo_patched.img', 'dtbo_patcher.sh', 'dtbo_patcher.py', 'dtbo_patcher'):
            (result / name).unlink(missing_ok=True)
    final = download / result.name
    result.replace(final)
    return final
