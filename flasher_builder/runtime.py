"""Launch the native worker on both Linux and Windows."""
from pathlib import Path
import sys
from .backend import ROOT, clean_source, is_url
from .host import worker_command


class Runtime:
    def __init__(self, distro=''):
        # Retain the argument for settings from releases that used WSL.
        self.prefix = []

    def path(self, value):
        return str(Path(value).expanduser().resolve())

    def command(self, values, action, cancel):
        root = self.path(ROOT)
        cmd = worker_command(ROOT) + ['--repo', root]
        if action == 'check':
            return cmd + ['--check']
        if action == 'bloat':
            return cmd + ['--download-bloat', '--bloat-dir', self.path(values['bloat_dir']),
                          '--cancel', self.path(cancel), '--device', values['device'],
                          '--kind', values['kind'], '--region', values['region']]
        cmd += ['--output', self.path(values['output']), '--cancel', self.path(cancel)]
        if action == 'test':
            return cmd + ['--self-test']
        if values.get('bloat_dir'):
            cmd += ['--bloat-dir', self.path(values['bloat_dir'])]
        for key in ('device', 'kind', 'region'):
            cmd += ['--' + key, values[key]]
        if action == 'ota':
            cmd += ['--download-ota', '--download-role', values['download_role'],
                    '--download-path', self.path(values['download_path'])]
        if action == 'build' and not values.get('zip_output', True):
            cmd += ['--no-zip']
        if action == 'build' and values['device'] == 'OPPO Find X9 Ultra':
            if values.get('vulnerable_abl') is None:
                raise ValueError('Choose Yes or No for vulnerable ABL before building.')
            cmd += ['--vulnerable-abl' if values['vulnerable_abl'] else '--stock-abl']
        for key in ('ota', 'base'):
            if values.get(key + '_catalog_id'):
                cmd += ['--' + key + '-catalog-id', values[key + '_catalog_id']]
                continue
            source = clean_source(values[key])
            if source:
                cmd += ['--' + key, source if is_url(source) else self.path(source)]
        return cmd
