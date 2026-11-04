"""Catalog UI stays scoped to the selected build and reuses downloaded ZIPs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from flasher_builder.gui import App, STYLE
from flasher_builder.runtime import Runtime


def entry(id, device, region):
    return dict(id=id, device=device, region=region, model='model', version='16.0.1',
                source_url='https://example.invalid/downloadCheck', is_latest=True, size=1024**3)


class OtaGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion'); cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.manifest_patch = patch('flasher_builder.gui.load_manifest', return_value={'files': {}, 'assets': {}})
        self.manifest_patch.start()
        self.catalog = {'api_version': '1', 'releases': [entry('glo', 'OP 13', 'GLO'), entry('eu', 'OP 13', 'EU'),
            entry('target', 'OP 15R', 'IN'), entry('base', 'OP ACE 6', 'CN')]}
        self.fetch_patch = patch('flasher_builder.ota_catalog.load_catalog', return_value=self.catalog)
        self.fetch = self.fetch_patch.start()
        self.window = App(); self.window.settings = self.root / 'settings.json'
        self.window.fields['device'].setCurrentText('Oneplus 13')
        self.window.fields['kind'].setCurrentText('SuperHybrid Flasher'); self.window.set_region('GLO')
        self.window.fields['bloat_dir'].setText(str(self.root / 'bloat'))
        self.window.fields['output'].setText(str(self.root / 'output'))
        self.window.show(); self.wait_for(self.window.local_bloat_ready)
        self.window.catalog_tab.click()
        self.wait_for(lambda: not self.window.ota_fetching)

    def tearDown(self):
        self.window.timer.stop(); self.window.bloat_watch_timer.stop(); self.window.bloat_check_timer.stop()
        self.window.ota_fetch_token += 1; self.window.ota_link_token += 1; self.window.bloat_scan_token += 1
        self.window.close(); self.app.processEvents()
        self.fetch_patch.stop(); self.manifest_patch.stop(); self.tmp.cleanup()

    def wait_for(self, condition):
        end = time.monotonic() + 6
        while not condition() and time.monotonic() < end: QTest.qWait(20)
        self.assertTrue(condition(), self.window.ota_catalog_status.text())

    def test_region_filters_versions_and_manual_mode_does_not_use_catalog_id(self):
        self.assertEqual(self.window.values()['ota_catalog_id'], 'glo')
        self.assertEqual(self.window.ota_combos['ota'].count(), 1)
        self.window.set_region('EU')
        self.assertEqual(self.window.values()['ota_catalog_id'], 'eu')
        self.window.fields['ota'].setText('/tmp/manual.zip')
        self.wait_for(self.window.local_bloat_ready)
        self.window.file_tab.click()
        self.assertNotIn('ota_catalog_id', self.window.values())
        self.assertEqual(self.window.values()['ota'], '/tmp/manual.zip')
        self.fetch.assert_called_once()

    def test_x9u_step_four_requires_an_explicit_yes_or_no(self):
        self.assertTrue(self.window.abl_card.isHidden())
        self.window.fields['device'].setCurrentText('OPPO Find X9 Ultra')
        self.assertFalse(self.window.abl_card.isHidden())
        self.assertIsNone(self.window.values()['vulnerable_abl'])
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIn('Step 4',self.window.build_requirements_error())
        with self.assertRaisesRegex(ValueError,'Choose Yes or No'):
            Runtime().command(self.window.values(),'build',str(self.root/'cancel'))
        self.window.abl_yes.click()
        self.assertTrue(self.window.values()['vulnerable_abl'])
        self.assertIn('--vulnerable-abl',Runtime().command(self.window.values(),'build',str(self.root/'cancel')))
        self.window.abl_no.click()
        self.assertFalse(self.window.abl_yes.isChecked())
        self.assertFalse(self.window.values()['vulnerable_abl'])
        self.assertIn('--stock-abl',Runtime().command(self.window.values(),'build',str(self.root/'cancel')))
        self.window.fields['device'].setCurrentText('Oneplus 13')
        self.assertTrue(self.window.abl_card.isHidden())
        self.assertFalse(self.window.values()['vulnerable_abl'])
        self.window.fields['device'].setCurrentText('OPPO Find X9 Ultra')
        self.assertIsNone(self.window.values()['vulnerable_abl'])

    def test_na_feed_switches_for_both_devices_and_returns_to_regular_catalog(self):
        for device,id in (('OP 13','na13'),('OP 15','na15')):
            self.catalog['releases'].append(dict(entry(id,device,'NA'),package_type='reconstructed_full'))
        self.window.set_region('NA')
        self.wait_for(lambda:not self.window.ota_fetching and self.window.values().get('ota_catalog_id')=='na13')
        self.assertTrue(self.fetch.call_args.kwargs['na'])
        self.assertIn('Reconstructed full package',self.window.ota_details['ota'].text())
        self.window.fields['device'].setCurrentText('Oneplus 15')
        self.wait_for(lambda:self.window.values().get('ota_catalog_id')=='na15')
        self.window.fields['device'].setCurrentText('Oneplus 13')
        self.window.set_region('GLO')
        self.wait_for(lambda:not self.window.ota_fetching and self.window.values().get('ota_catalog_id')=='glo')
        self.assertFalse(self.fetch.call_args.kwargs['na'])

    def test_conversion_populates_target_and_base_without_mixing_devices(self):
        self.window.fields['device'].setCurrentText('Oneplus Ace 6'); self.window.set_region('IN')
        values = self.window.values()
        self.assertEqual(values['ota_catalog_id'], 'target')
        self.assertEqual(values['base_catalog_id'], 'base')
        self.assertFalse(self.window.ota_role_panels['base'].isHidden())
        self.window.set_region('CN')
        self.assertEqual(self.window.values()['ota_catalog_id'], 'base')
        self.assertEqual(self.window.values()['base_catalog_id'], '')
        self.assertTrue(self.window.ota_role_panels['base'].isHidden())

    def test_copy_link_and_discard_stale_async_result(self):
        with patch('flasher_builder.ota_catalog.resolve_link', return_value='https://cdn.example/fresh.zip'):
            self.window.copy_ota_link('ota')
            self.wait_for(lambda: not self.window.ota_link_pending)
        self.assertEqual(self.app.clipboard().text(), 'https://cdn.example/fresh.zip')
        old_token = self.window.ota_link_token
        self.window.set_region('EU')
        self.window.events.put(('ota-link', (old_token, 'ota', 'https://wrong.example/stale.zip', None)))
        self.window.poll()
        self.assertEqual(self.app.clipboard().text(), 'https://cdn.example/fresh.zip')

    def test_ace6t_catalog_shows_patch_matched_donor_and_blocks_unmatched_target(self):
        target = dict(entry('target-old', 'OP 15R', 'IN'), security_patch='2026-08-01')
        newer = dict(target, id='target-new', version='17.0.0.106', security_patch='2026-10-01')
        donor = dict(entry('donor', 'OP ACE 6T', 'CN'), version='PLR110_16.0.10.500(CN01)', security_patch='2026-08-01')
        self.catalog['releases'] = [target, newer, donor]
        self.window.fields['device'].setCurrentText('Oneplus Ace 6T'); self.window.set_region('IN')
        self.window.populate_ota_versions(); self.wait_for(self.window.local_bloat_ready)
        combo = self.window.ota_combos['ota']
        combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i)['id'] == 'target-old'))
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertIn(donor['version'], self.window.ota_details['ota'].text())
        self.assertEqual(self.window.values()['ota_catalog_id'], 'target-old')
        combo.setCurrentIndex(next(i for i in range(combo.count()) if combo.itemData(i)['id'] == 'target-new'))
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIn('2026-10-01', self.window.start_button.toolTip())
        self.assertIn('ODM unavailable', self.window.ota_details['ota'].text())
        self.assertTrue(self.window.ota_download_buttons['ota'].isEnabled())
        self.window.file_tab.click()
        self.assertFalse(self.window.start_button.isEnabled())
        self.window.fields['ota'].setText('https://example.invalid/full.zip')
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertIn('Only odm.img', self.window.source_hint.text())

    def test_downloaded_zip_reused_and_wsl_id_remains_literal(self):
        path = self.root / 'OTA saved.zip'; path.write_bytes(b'test')
        self.window.ota_local['glo'] = str(path); self.window.ota_version_changed('ota')
        values = self.window.values()
        self.assertEqual(values['ota'], str(path)); self.assertEqual(values['ota_catalog_id'], '')
        self.assertEqual(self.window.ota_download_buttons['ota'].text(), 'Already downloaded')
        path.unlink(); values = self.window.values()
        values.update(download_role='ota', download_path='Windows OTA.zip')
        with patch('flasher_builder.runtime.sys.platform', 'win32'):
            runtime = Runtime('Ubuntu')
        with patch.object(runtime, 'path', side_effect=lambda p: '/mapped/' + str(p)):
            command = runtime.command(values, 'ota', '/cancel')
        self.assertEqual(command[command.index('--ota-catalog-id') + 1], 'glo')
        self.assertNotIn('--ota', command)
        self.assertEqual(command[command.index('--download-path') + 1], '/mapped/Windows OTA.zip')

    def test_download_action_reuses_the_saved_file_in_build_values(self):
        import sys
        destination = self.root / 'downloaded OTA.zip'
        code = ('from pathlib import Path; Path(' + repr(str(destination)) + ').write_bytes(b"fixture"); '
                'print(\'@@OTA_RESULT {"role":"ota","file":"worker-path"}\')')
        with patch('flasher_builder.gui.QFileDialog.getSaveFileName', return_value=(str(destination), '')), \
                patch.object(Runtime, 'command', return_value=[sys.executable, '-u', '-c', code]) as command:
            self.window.ota_download_buttons['ota'].click()
            self.wait_for(lambda: not self.window.busy)
        self.assertEqual(command.call_args.args[1], 'ota')
        self.assertEqual(self.window.values()['ota'], str(destination))
        self.assertEqual(self.window.values()['ota_catalog_id'], '')
        self.assertEqual(self.window.status.text(), 'OTA downloaded · ready to build')

    def test_x9u_region_dropdown_exposes_every_catalog_region_and_preserves_selection(self):
        regions=['APC','CN','EU','ID','IN','JP','MX','MY','SG','TH','TW','VN']
        self.catalog['releases'].extend(entry('x9u-'+region,'OPPO FIND X9 ULTRA',region) for region in regions)
        self.window.fields['device'].setCurrentText('OPPO Find X9 Ultra')
        self.assertFalse(self.window.region_combo.isHidden())
        self.assertTrue(self.window.region_buttons_panel.isHidden())
        self.assertEqual([self.window.region_combo.itemData(i) for i in range(self.window.region_combo.count())],regions)
        for region in regions:
            self.window.region_combo.setCurrentIndex(self.window.region_combo.findData(region))
            self.assertEqual(self.window.values()['region'],region)
            self.assertEqual(self.window.values()['ota_catalog_id'],'x9u-'+region)
            self.assertEqual(self.window.os_badge.text(),'COLOR OS')
            expected = 'EU bloat is used for CN' if region == 'CN' else f'{region} bloat files'
            self.assertIn(expected,self.window.selection_info.text())
        self.window.fields['kind'].setCurrentText('Regional Flasher')
        self.assertEqual(self.window.values()['region'],'VN')
        self.assertEqual(self.window.region_combo.currentData(),'VN')
        self.window.fields['device'].setCurrentText('Oneplus 13')
        self.assertTrue(self.window.region_combo.isHidden())
        self.assertFalse(self.window.region_buttons_panel.isHidden())


if __name__ == '__main__': unittest.main()
