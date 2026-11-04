#!/usr/bin/env python3
"""Exercise the shipped worker and GUI from an extracted release archive."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    logs = []
    with tempfile.TemporaryDirectory(prefix='flasher-release-') as temporary:
        root = Path(temporary)
        if args.archive.suffix == '.zip':
            with zipfile.ZipFile(args.archive) as archive: archive.extractall(root)
        else:
            with tarfile.open(args.archive) as archive: archive.extractall(root)
        project = root / 'FlasherBuilder'
        exe = '.exe' if sys.platform == 'win32' else ''
        worker = project / ('FlasherBuilderWorker' + exe)
        app = project / ('FlasherBuilder' + exe)
        aria = project / 'tools/aria2' / ('aria2c' + exe)
        environment = os.environ | {'QT_QPA_PLATFORM': 'offscreen', 'PYTHONUTF8': '1'}
        environment.pop('PYTHONPATH', None); environment.pop('PYTHONHOME', None)
        if sys.platform == 'win32':
            # Prove the frozen app works without runner Python, Git Bash, or WSL on PATH.
            windows = Path(os.environ['SystemRoot'])
            environment['PATH'] = str(windows / 'System32') + os.pathsep + str(windows)
        for command in ([str(aria), '--version'], [str(worker), '--check'],
                        [str(app), '--smoke-test', '--test-output', str(root / 'smoke')]):
            result = subprocess.run(command, cwd=project, env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
            logs.append(result.stdout.decode('utf-8', errors='replace'))
            if result.returncode:
                raise RuntimeError('Packaged program failed:\n' + logs[-1])
        smoke = json.loads((root / 'smoke/qt-smoke-test.json').read_text())
        if not smoke['passed'] or not smoke['frozen']: raise RuntimeError('Frozen GUI smoke test failed.')
        if smoke.get('documentation_in_app') is not True:
            raise RuntimeError('Packaged documentation viewer failed.')
        if smoke.get('x9u_abl_choice_required') is not True:
            raise RuntimeError('Packaged X9U Yes/No ABL choice failed.')
        download_log = (root / 'smoke/qt-smoke-test.log').read_text(encoding='utf-8')
        if 'Downloading with aria2c' not in download_log or 'aria2c could not finish' in download_log:
            raise RuntimeError('Packaged aria2 download self-test failed.')
        if 'remote patch-matched ODM only' not in download_log:
            raise RuntimeError('Packaged selective remote ODM self-test failed.')
        if 'regional super sizes and multipart bloat' not in download_log:
            raise RuntimeError('Packaged regional-size/multipart self-test failed.')
        if 'regional bloat mapping' not in download_log:
            raise RuntimeError('Packaged regional bloat self-test failed.')
        if 'reconstructed NA packages' not in download_log:
            raise RuntimeError('Packaged reconstructed NA self-test failed.')
        if 'X9U hybrid recovery and shared ABL choices' not in download_log:
            raise RuntimeError('Packaged X9U hybrid/ABL self-test failed.')
        logs.append(download_log)
        # Prove that a clean install can download public bloat over HTTPS with
        # the bundled aria2 and without the runner's GitHub credentials.
        manifest = json.loads((project / 'bloat/assets-manifest.json').read_text())
        key, asset = min(((k,a) for k,a in manifest['assets'].items() if a['encoding'] == 'identity'),
                         key=lambda item:item[1]['bytes'])
        probe = root / 'public-download-probe'; (probe / 'bloat').mkdir(parents=True)
        probe_manifest = dict(manifest, assets={key:asset}, files={'bloat/TEST/CN_BLOAT/verification.img':key})
        (probe / 'bloat/assets-manifest.json').write_text(json.dumps(probe_manifest),encoding='utf-8')
        anonymous = dict(environment)
        for name in ('GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN'):
            anonymous.pop(name,None)
        anonymous['GH_CONFIG_DIR'] = str(root / 'empty-gh-config')
        result = subprocess.run([str(worker),'--helper','materialize','--root',str(probe),
            '--cache',str(root/'probe-cache'),'--destination',str(root/'probe-images'),
            '--device','TEST','--kind','Super Flasher','--region','CN'],
            cwd=project,env=anonymous,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
        probe_log = result.stdout.decode('utf-8',errors='replace'); logs.append(probe_log)
        if result.returncode or 'Downloading with aria2c' not in probe_log or 'aria2c could not finish' in probe_log:
            raise RuntimeError('Anonymous packaged aria2 HTTPS download failed:\n'+probe_log)
        image = root/'probe-images/TEST/CN_BLOAT/verification.img'
        if hashlib.sha256(image.read_bytes()).hexdigest() != asset['raw_sha256']:
            raise RuntimeError('Anonymous download checksum mismatch.')
        report = dict(platform=sys.platform, artifact=args.archive.name,
                      sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest(),
                      native_worker=True, frozen_gui=True, pipeline_passed=True,
                      wsl_required=False, otaripper_version='3.5.0', app_version=smoke['app_version'],
                      bundled_aria2=True, aria2_version='1.37.0', download_pipeline_passed=True,
                      anonymous_bloat_download=True, remote_odm_streaming=True, documentation_in_app=True,
                      regional_super_sizes=True, multipart_bloat=True, regional_bloat=True, reconstructed_na=True,
                      x9u_hybrid=True, x9u_abl_choice_required=True, shared_vulnerable_abl=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        args.report.with_suffix('.log').write_text('\n'.join(logs), encoding='utf-8')
        print(json.dumps(report), flush=True)


if __name__ == '__main__': main()
