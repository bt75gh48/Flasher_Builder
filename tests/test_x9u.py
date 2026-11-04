"""Regional super sizes, ColorOS layouts and host flashers without hardware."""
import os
import queue
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from flasher_builder.backend import Catalog, ROOT, Worker, BuildError, build, pack_vulnerable_abl
from flasher_builder.device_layout import coloros, image_folder
from flasher_builder.native_pipeline import SUPER_PARTS
from flasher_builder.ota_catalog import targets
from scripts.materialize_bloat import load_manifest, selected_paths

REGIONS = ['APC','CN','EU','ID','IN','JP','MX','MY','SG','TH','TW','VN']

class FindX9UltraTests(unittest.TestCase):
    def test_vulnerable_abl_replaces_only_the_requested_output_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp);(target/'abl.img').write_bytes(b'original extracted ABL')
            (target/'boot.img').write_bytes(b'keep boot')
            receipt=pack_vulnerable_abl(Worker(),ROOT,target)
            self.assertEqual((target/'abl.img').read_bytes(),(ROOT/'bloat/X9U/EU_BLOAT/abl-16.0.6.img').read_bytes())
            self.assertEqual((target/'boot.img').read_bytes(),b'keep boot')
            self.assertEqual(receipt['version'],'16.0.6')
            self.assertEqual(receipt['sha256'],'4ad7f1db0c92f0a358e28bf18170a20533011299827d8d9a25cffd2218f1415d')
        with self.assertRaisesRegex(BuildError,'Choose Yes'):
            build(SimpleNamespace(repo=str(ROOT),device='OPPO Find X9 Ultra',kind='Super Flasher',region='IN',vulnerable_abl=None))
        with self.assertRaisesRegex(BuildError,'only for X9U'):
            build(SimpleNamespace(repo=str(ROOT),device='Oneplus 13',kind='SuperHybrid Flasher',region='GLO',vulnerable_abl=True))

    def test_supported_regions_templates_sizes_and_catalog_mapping(self):
        catalog=Catalog()
        self.assertEqual(catalog.types('OPPO Find X9 Ultra'),['Super Flasher','Regional Flasher'])
        for kind in catalog.types('OPPO Find X9 Ultra'):
            self.assertEqual(catalog.regions('OPPO Find X9 Ultra',kind),REGIONS)
            for region in REGIONS:
                dev,builder,template=catalog.selection('OPPO Find X9 Ultra',kind,region)
                self.assertEqual(dev,'X9U');self.assertIn('/cos/',template.as_posix())
                self.assertTrue(coloros(dev,region));self.assertEqual(image_folder(dev,region),'OTA_FILES_HERE')
                self.assertEqual(targets('OPPO Find X9 Ultra',kind,region),{'ota':('OPPO FIND X9 ULTRA',region)})
        self.assertEqual(catalog.super_size('X9U','CN'),19671285760)
        for region in (r for r in REGIONS if r!='CN'):
            self.assertEqual(catalog.super_size('X9U',region),20451426304)

    def test_bloat_manifest_selects_matching_regions_and_keeps_cn_eu_fallback(self):
        manifest=load_manifest(ROOT/'bloat/assets-manifest.json')
        expected_preloads = {
            'APC': '862c06a0d43f5c2fb2fc0bad2f6828e242f9ffb645443f02dc697318d5ba0d49',
            'ID': 'df8554d258e3fffd3ebcc448a33f33dd08941ef0597c163f65de4d008c95de4d',
            'IN': '9563374a18664c84121459812caac97f787b15cbd8f8a363e64d8528dc6c1989',
            'JP': '4c0353b6d800dc67c34440022afc5f3eae8f598616c6fe5fb604ba296bde7abe',
            'MX': '999734b7a596d7b41f553d0830d8322b30359843c665761b8d87f5c927226c05',
            'MY': '0bd4272e0a2da34390b1895f7ca7f1c270a901e22ffae2a6b08e2ae10e129651',
            'SG': 'c7010c11e616d9e2f16780e1b5614c46e13d4295aa872a54760ba7d6c7d310b1',
            'TH': '19dcbf4ddc2034fa58e429872b331ac35c14d4c2f95ef89bb3a101969e959522',
            'TW': '6ec67ccf8a01226cf32c255c81bf0ea54638ec1dcfb67ebdfe77da3f68dc579f',
            'VN': '7a4e17df48fa71062c5ef01db124be234a5b6aa26f092b3e8b042b906d4d7d6b',
        }
        for kind in ('Super Flasher', 'Regional Flasher'):
            for region in REGIONS:
                folder = 'EU' if region == 'CN' else region
                prefix = f'bloat/X9U/{folder}_BLOAT/'
                selected=selected_paths(manifest,'X9U',kind,region)
                self.assertEqual(set(selected),{prefix+name+'.img' for name in ('my_company','my_preload','abl-16.0.6')})
                self.assertEqual(manifest['files'][prefix+'abl-16.0.6.img'],'4ad7f1db0c92f0a358e28bf18170a20533011299827d8d9a25cffd2218f1415d')
                if region in expected_preloads:
                    self.assertEqual(manifest['files'][prefix+'my_preload.img'],expected_preloads[region])
        asset=manifest['assets'][manifest['files']['bloat/X9U/CN_BLOAT/my_preload.img']]
        self.assertEqual(asset['raw_bytes'],2399965184)
        self.assertEqual(len(asset['parts']),3)
        self.assertTrue(all(part['bytes']<2*1024**3 for part in asset['parts']))
        self.assertEqual(sum(part['bytes'] for part in asset['parts']),asset['bytes'])

    def run_flasher(self,root,kind='super',fail=False,incomplete=False):
        package=root/'package with spaces'
        shutil.copytree(ROOT/'flasher_scripts/X9U/cos'/kind,package)
        images=package/'OTA_FILES_HERE';images.mkdir()
        for name in (*SUPER_PARTS,'boot','dtbo','init_boot','modem','recovery','vbmeta','vbmeta_system','vbmeta_vendor','vendor_boot','xbl'):
            (images/(name+'.img')).write_bytes(b'test fixture only')
        if kind=='super' and not incomplete:(package/'super.img').write_bytes(b'test super only')
        binary=root/'bin';binary.mkdir();log=root/'fastboot.log'
        env=os.environ|{'PATH':str(binary)+os.pathsep+os.environ.get('PATH',''),'FLASH_LOG':str(log),'FAIL_FLASH':'1' if fail else '0'}
        if os.name=='nt':
            (binary/'fastboot.cmd').write_text('@echo off\necho %*>>"%FLASH_LOG%"\nif "%FAIL_FLASH%"=="1" if "%~1"=="flash" if "%~2"=="boot_b" exit /b 7\nexit /b 0\n')
            command=['cmd','/c',str(package/('Super_Flasher.bat' if kind=='super' else 'Regional_Flasher.bat'))]
        else:
            mock=binary/'fastboot';mock.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FLASH_LOG"\nif [ "$FAIL_FLASH" = 1 ] && [ "$1" = flash ] && [ "$2" = boot_b ]; then exit 7; fi\n')
            mock.chmod(0o755);command=['bash',str(package/('Super_Flasher.sh' if kind=='super' else 'Regional_Flasher.sh'))]
        if os.name=='nt':
            # PAUSE consumes one character, while SET /P reads available pipe
            # data in a chunk. Feed each answer only after its prompt, as a user
            # would; a preloaded multiline pipe can put newlines in variables.
            process=subprocess.Popen(command,cwd=package,env=env,stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            output=queue.Queue()
            def read():
                while char:=process.stdout.read(1):output.put(char)
                output.put(None)
            reader=threading.Thread(target=read,daemon=True);reader.start()
            prompts=[('Press any key to continue','x'),('Do you want to keep the current ABL? [Y/n]: ','Y\n'),
                     ('Do you want to wipe data? [y/N]: ','N\n'),('Press any key to continue','x')]
            text='';pending=0;start=0;deadline=time.monotonic()+20
            try:
                while time.monotonic()<deadline:
                    try:char=output.get(timeout=.1)
                    except queue.Empty:
                        if process.poll() is not None:break
                        continue
                    if char is None:break
                    text+=char
                    if pending<len(prompts) and prompts[pending][0] in text[start:]:
                        process.stdin.write(prompts[pending][1]);process.stdin.flush()
                        pending+=1;start=len(text)
                try:process.wait(timeout=max(1,deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill();process.wait(timeout=3)
                result=SimpleNamespace(returncode=process.returncode,stdout=text,stderr='')
            finally:
                if process.poll() is None:process.kill();process.wait(timeout=3)
                process.stdin.close();reader.join(timeout=1);process.stdout.close()
        else:
            result=subprocess.run(command,cwd=package,env=env,input='\nY\nN\n\n',capture_output=True,text=True,timeout=15)
        return result,log.read_text() if log.exists() else ''

    def test_super_flasher_uses_packed_super_and_never_reflashes_logical_images(self):
        with tempfile.TemporaryDirectory() as tmp:result,log=self.run_flasher(Path(tmp))
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(log.count('flash super super.img'),1)
        self.assertIn('OTA_FILES_HERE',log)
        for name in SUPER_PARTS:
            self.assertNotIn('OTA_FILES_HERE/'+name+'.img',log)
            self.assertNotIn('OTA_FILES_HERE\\'+name+'.img',log)
        self.assertNotIn('delete-logical-partition',log)

    def test_regional_flasher_prepares_all_partitions_before_flashing_them(self):
        with tempfile.TemporaryDirectory() as tmp:result,log=self.run_flasher(Path(tmp),'regional')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        lines=log.splitlines()
        creates=[i for i,line in enumerate(lines) if line.startswith('create-logical-partition')]
        flashes=[i for i,line in enumerate(lines) if any(line.startswith('flash '+name+'_a ') for name in SUPER_PARTS)]
        self.assertTrue(creates);self.assertTrue(flashes);self.assertLess(max(creates),min(flashes))

    def test_super_flasher_stops_on_failure_or_missing_super(self):
        for fail,incomplete in ((True,False),(False,True)):
            with self.subTest(fail=fail,incomplete=incomplete),tempfile.TemporaryDirectory() as tmp:
                result,log=self.run_flasher(Path(tmp),fail=fail,incomplete=incomplete)
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('flash super',log);self.assertNotIn('reboot',log)


if __name__=='__main__':unittest.main()
