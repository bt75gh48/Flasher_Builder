"""Run both recovery interfaces against regular files, never real block devices."""
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from flasher_builder.backend import ROOT

META=ROOT/'flasher_scripts/X9U/cos/super/META-INF'
REQUIRED=('boot','dtbo','init_boot','modem','recovery','vbmeta','vbmeta_system','vbmeta_vendor','vendor_boot')

@unittest.skipUnless(os.name=='posix' and shutil.which('cc') and shutil.which('unzip'),'Host recovery sandbox needs POSIX, cc and unzip')
class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();cls.tools=Path(cls.temporary.name)
        for name in ('zip_entry_streamer','sparse_stream_writer'):
            args=['cc','-O2',str(ROOT/f'build_tools_assets/x9u_hybrid/{name}.c'),'-o',str(cls.tools/name)]
            if name=='zip_entry_streamer':args.append('-lz')
            subprocess.run(args,check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    def test_large_sizes_do_not_use_shell_numeric_operators(self):
        script=(META/'com/google/android/aera-installer').read_text()
        functions='\n'.join(re.search(r'(?ms)^'+name+r'\(\) \{.*?^\}',script).group(0) for name in ('number_valid','size_le'))
        # Fail any accidental return to test's 32-bit -gt/-le comparisons.
        self.assertNotRegex(functions,r'\s-(gt|ge|lt|le)\s')
        for left,right,expected in (('20451426304','20451426304',0),('19671285760','20451426304',0),
                                    ('20451426304','19671285760',1),('3221225472','4294967296',0)):
            result=subprocess.run(['sh','-c',functions+'\nnumber_valid "$1" && size_le "$1" "$2"','sizes',left,right])
            self.assertEqual(result.returncode,expected,(left,right))
        for invalid in ('','0','000','12x','-1'):
            self.assertNotEqual(subprocess.run(['sh','-c',functions+'\nnumber_valid "$1"','sizes',invalid]).returncode,0)

    def run_installer(self,aera=True,cancel=False,replace=False,missing=False,oversized=False,crc_error=False,compression=zipfile.ZIP_STORED):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name)
        byname=root/'by-name';byname.mkdir();binary=root/'bin';binary.mkdir();responses=root/'responses';responses.mkdir()
        originals={}
        for name in (*REQUIRED,'abl','engineering_cdt'):
            targets=(name,) if name=='engineering_cdt' else (name+'_a',name+'_b')
            for target in targets:
                path=byname/target;path.write_bytes(b'OLD'.ljust(8192,b'O'));originals[target]=path.read_bytes()
        superfile=byname/'super';superfile.write_bytes(b'OLD'.ljust(8192,b'O'));originals['super']=superfile.read_bytes()
        if missing:(byname/'vendor_boot_b').unlink()
        slot=root/'slot.log'
        def script(path,text):path.write_text('#!/bin/sh\n'+text);path.chmod(0o755)
        script(binary/'getprop',"echo "+('aera' if aera else 'other')+'\n')
        script(binary/'blockdev','stat -c %s "$2"\n')
        script(binary/'bootctl',f'echo "$*" >> "{slot}"\n')
        answers=['no' if cancel else 'yes','no' if replace else 'yes']
        script(binary/'getevent',f'''n=$(cat "{root/'keys'}" 2>/dev/null || echo 0)
echo $((n+1)) > "{root/'keys'}"
case "$n" in 0) answer={answers[0]} ;; *) answer={answers[1]} ;; esac
if [ "$answer" = yes ]; then echo 'KEY_VOLUMEUP DOWN'; else echo 'KEY_VOLUMEDOWN DOWN'; fi
''')
        data=b'NORMAL ROM SUPER'.ljust(4096,b'S')
        sparse=struct.pack('<IHHHHIIII',0xed26ff3a,1,0,28,12,4096,1,1,0)+struct.pack('<HHII',0xcac1,0,1,4108)+data
        package=root/'package with spaces.zip'
        with zipfile.ZipFile(package,'w',compression=zipfile.ZIP_STORED) as z:
            info=zipfile.ZipInfo('super.img');info.compress_type=compression
            with z.open(info,'w',force_zip64=True) as stream:stream.write(sparse)
            z.writestr('META-INF/com/google/android/super-size',str(16384 if oversized else 4096)+'\n')
            for name in (*REQUIRED,'abl','engineering_cdt'):
                z.writestr('OTA_FILES_HERE/'+name+'.img',('NEW '+name).encode().ljust(4096,b'N'))
            z.writestr('OTA_FILES_HERE/my_preload.img',b'logical data must not be written separately')
            for name in ('zip_entry_streamer','sparse_stream_writer'):
                z.write(self.tools/name,'META-INF/com/aera/bin/'+name)
        if crc_error:
            with zipfile.ZipFile(package) as z:info=z.getinfo('super.img')
            with package.open('r+b') as f:
                f.seek(info.header_offset);header=f.read(30);name_len,extra_len=struct.unpack_from('<HH',header,26)
                f.seek(info.header_offset+30+name_len+extra_len+len(sparse)-1);last=f.read(1)
                f.seek(-1,1);f.write(bytes([last[0]^1]))
        core=(META/'com/google/android/aera-installer').read_text()
        core=core.replace('BYNAME=/dev/block/bootdevice/by-name',f'BYNAME="{byname}"')
        core=core.replace('BOOTCTL=/system/bin/bootctl',f'BOOTCTL="{binary/"bootctl"}"')
        core=core.replace('TMPDIR=/tmp/x9u-stock-flasher.$$',f'TMPDIR="{root}/installer.$$"')
        core=core.replace('AERA_RESPONSE_DIR=/tmp/aera-installer',f'AERA_RESPONSE_DIR="{responses}"')
        installer=root/'installer.sh';installer.write_text(core)
        ui=root/'ui.log';stop=threading.Event()
        def answer_prompts():
            while not stop.wait(.01):
                try:text=ui.read_text()
                except FileNotFoundError:continue
                for line in text.splitlines():
                    if not line.startswith('aera_prompt '):continue
                    ident=line.split(' ',1)[1].split('|',1)[0]
                    answer=answers[0] if ident.startswith('install-') else answers[1]
                    (responses/('response.'+ident)).write_text(answer+'\n')
        thread=threading.Thread(target=answer_prompts,daemon=True);thread.start()
        try:
            with ui.open('w') as fd:
                result=subprocess.run(['sh',str(installer),'3',str(fd.fileno()),str(package)],pass_fds=(fd.fileno(),),
                    env=os.environ|{'PATH':str(binary)+os.pathsep+os.environ['PATH']},capture_output=True,text=True,timeout=15)
        finally:stop.set();thread.join()
        return result,byname,originals,data,slot,ui.read_text()

    def test_aera_and_volume_interfaces_write_stock_images_and_keep_abl_by_default(self):
        for aera,compression in ((True,zipfile.ZIP_STORED),(False,zipfile.ZIP_STORED),(True,zipfile.ZIP_DEFLATED),(False,zipfile.ZIP_DEFLATED)):
            with self.subTest(aera=aera,compression=compression):
                result,blocks,originals,data,slot,ui=self.run_installer(aera=aera,compression=compression)
                self.assertEqual(result.returncode,0,result.stderr+ui)
                self.assertEqual((blocks/'super').read_bytes()[:4096],data)
                self.assertEqual((blocks/'abl_a').read_bytes(),originals['abl_a'])
                for name in REQUIRED:
                    for suffix in ('_a','_b'):
                        self.assertEqual((blocks/(name+suffix)).read_bytes(),('NEW '+name).encode().ljust(4096,b'N'))
                self.assertEqual((blocks/'engineering_cdt').read_bytes(),b'NEW engineering_cdt'.ljust(4096,b'N'))
                self.assertEqual(slot.read_text().strip(),'set-active-boot-slot 0')
                self.assertNotIn('Infinity-X',ui)

    def test_replace_abl_requires_the_explicit_choice(self):
        for aera in (True,False):
            result,blocks,_,_,_,ui=self.run_installer(aera=aera,replace=True)
            self.assertEqual(result.returncode,0,result.stderr+ui)
            for suffix in ('_a','_b'):self.assertEqual((blocks/('abl'+suffix)).read_bytes(),b'NEW abl'.ljust(4096,b'N'))

    def test_cancel_missing_partition_and_oversized_super_never_write_or_activate(self):
        for options in ({'cancel':True},{'cancel':True,'aera':False},{'missing':True},{'oversized':True}):
            with self.subTest(options=options):
                result,blocks,originals,_,slot,ui=self.run_installer(**options)
                self.assertEqual(result.returncode,0 if options.get('cancel') else 1,result.stderr+ui)
                self.assertFalse(slot.exists())
                for name,old in originals.items():
                    if (blocks/name).exists():self.assertEqual((blocks/name).read_bytes(),old)

    def test_zip_crc_failure_stops_before_boot_firmware_and_slot_changes(self):
        result,blocks,originals,_,slot,ui=self.run_installer(crc_error=True)
        self.assertEqual(result.returncode,1,result.stderr+ui)
        self.assertIn('ZIP integrity verification failed',ui)
        self.assertFalse(slot.exists())
        for name,old in originals.items():
            if name!='super':self.assertEqual((blocks/name).read_bytes(),old)
