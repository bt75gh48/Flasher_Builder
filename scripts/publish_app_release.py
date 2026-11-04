#!/usr/bin/env python3
"""Publish both tested desktop packages together, with SHA-256 receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlencode

REPOSITORY = ''


def find_release(tag):
    # Inspect drafts as well as published releases, without relying on git refs.
    releases = json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPOSITORY}/releases?per_page=100']))
    return next((release for release in releases if release['tag_name'] == tag), None)


def upload_files(release, files):
    assets = json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPOSITORY}/releases/' + str(release['id']) + '/assets?per_page=100']))
    existing = {asset['name']: asset for asset in assets}
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        old = existing.get(path.name)
        if old and old.get('digest') == 'sha256:' + digest and old.get('size') == path.stat().st_size:
            continue
        if old:
            subprocess.run(['gh', 'api', '--method', 'DELETE', f'repos/{REPOSITORY}/releases/assets/' + str(old['id'])], check=True)
        url = release['upload_url'].split('{')[0] + '?' + urlencode({'name': path.name})
        result = json.loads(subprocess.check_output(['gh', 'api', '--method', 'POST', url,
            '-H', 'Content-Type: application/octet-stream', '--input', str(path)]))
        if result.get('digest') != 'sha256:' + digest or result.get('size') != path.stat().st_size:
            raise ValueError('Uploaded attachment checksum mismatch: ' + path.name)
        print('Verified release attachment: ' + path.name, flush=True)


def download_files(release, directory):
    directory.mkdir(parents=True, exist_ok=True)
    for asset in release['assets']:
        name = asset['name']
        if Path(name).name != name: raise ValueError('Unsafe release attachment filename')
        with (directory / name).open('wb') as output:
            subprocess.run(['gh', 'api', f'repos/{REPOSITORY}/releases/assets/' + str(asset['id']),
                '-H', 'Accept: application/octet-stream'], stdout=output, check=True)


def main():
    global REPOSITORY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--repo', default=os.environ.get('GH_REPO', os.environ.get('GITHUB_REPOSITORY', '')))
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--upload', type=Path, nargs='+')
    parser.add_argument('--download', type=Path)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo): raise ValueError('Specify --repo OWNER/REPO')
    REPOSITORY = args.repo
    if not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', args.tag):
        raise ValueError('Use a version tag such as v1.0.0')
    if not re.fullmatch(r'[a-f0-9]{40}', args.commit): raise ValueError('Expected an exact commit SHA')
    if args.prepare:
        release = find_release(args.tag)
        if release is not None:
            if not release.get('draft'): raise ValueError('This version is already published; use a new tag.')
            subprocess.run(['gh', 'api', '--method', 'PATCH', f'repos/{REPOSITORY}/releases/' + str(release['id']),
                            '-f', 'tag_name=' + args.tag, '-f', 'target_commitish=' + args.commit], check=True)
        else:
            subprocess.run(['gh', 'release', 'create', args.tag, '--repo', REPOSITORY, '--target', args.commit, '--draft',
                            '--title', 'Flasher Builder ' + args.tag, '--notes', 'Native release builds and validation are in progress.'], check=True)
        return
    if args.upload or args.download:
        release = find_release(args.tag)
        if release is None or not release.get('draft'): raise ValueError('Expected a private draft release')
        if args.upload: upload_files(release, args.upload)
        if args.download: download_files(release, args.download)
        return
    if args.directory is None: raise ValueError('Release directory missing')
    directory = args.directory.resolve()
    for platform, artifact in [('linux', 'FlasherBuilder-linux-x86_64.tar.gz'), ('windows', 'FlasherBuilder-windows-x86_64.zip')]:
        report = json.loads((directory / f'validation-{platform}.json').read_text())
        if not all(report.get(k) is True for k in ('native_worker', 'frozen_gui', 'pipeline_passed')) or report.get('wsl_required') is not False:
            raise ValueError('Native package verification failed: ' + platform)
        if report.get('documentation_in_app') is not True:
            raise ValueError('Packaged documentation verification failed: ' + platform)
        if not all(report.get(k) is True for k in ('x9u_hybrid','x9u_abl_choice_required','shared_vulnerable_abl')):
            raise ValueError('Packaged X9U hybrid/ABL verification failed: ' + platform)
        if not all(report.get(k) is True for k in ('regional_super_sizes','multipart_bloat','regional_bloat','reconstructed_na')):
            raise ValueError('Packaged regional-size/multipart verification failed: ' + platform)
        if report.get('app_version') != args.tag.removeprefix('v'):
            raise ValueError('Packaged app version does not match release tag: ' + platform)
        if not all(report.get(k) is True for k in ('bundled_aria2','download_pipeline_passed','anonymous_bloat_download','remote_odm_streaming')):
            raise ValueError('Packaged download verification failed: ' + platform)
        digest = hashlib.sha256((directory / artifact).read_bytes()).hexdigest()
        if report.get('sha256') != digest or report.get('artifact') != artifact:
            raise ValueError('Validation receipt does not match package: ' + platform)
    if not (directory / 'FlasherBuilder-source-linux-windows.zip').is_file():
        raise ValueError('Source archive missing')
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != 'SHA256SUMS')
    checksums = directory / 'SHA256SUMS'
    checksums.write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n' for p in files), encoding='utf-8')
    files.append(checksums)
    notes = '''- Added SuperHybrid support to OPPO Find X9 Ultra Super Flasher packages, with fastboot and recovery installation in the same ZIP. Recovery supports AERA touch prompts and volume keys.
- Added step 4 for X9 Ultra: users must choose Yes or No to packing vulnerable ABL before building. Yes packs ABL 16.0.6 as abl.img; No keeps the OTA's ABL.
- ABL 16.0.6 now downloads with bloat for every X9 Ultra region, using one shared verified GitHub attachment.
- Fixed recovery size checks for large super partitions and added installer checks before writing images.

Linux and Windows packages include validation receipts and SHA256SUMS. Extract the entire platform archive and run FlasherBuilder (Linux) or FlasherBuilder.exe (Windows).
'''
    release = find_release(args.tag)
    if release is None or not release.get('draft'): raise ValueError('Expected an unpublished draft release.')
    upload_files(release, [checksums])
    with tempfile.TemporaryDirectory() as tmp:
        payload = Path(tmp) / 'release.json'
        payload.write_text(json.dumps(dict(draft=False, make_latest='true', body=notes,
            tag_name=args.tag, name='Flasher Builder ' + args.tag, target_commitish=args.commit)), encoding='utf-8')
        subprocess.run(['gh', 'api', '--method', 'PATCH', f'repos/{REPOSITORY}/releases/' + str(release['id']),
                        '--input', str(payload)], check=True)


if __name__ == '__main__': main()
