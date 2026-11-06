import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import control_panel as panel


class PanelTests(unittest.TestCase):
    def test_supported_uid_lengths_and_separators(self):
        for value in ('aa:bb:cc:dd', '01 02 03 04 05 06 07', '00-01-02-03-04-05-06-07-08-09'):
            result = panel.normalize_uid(value)
            self.assertEqual(result, result.upper())
        for value in ('', 'aabbcc', 'zz112233', '0011223344', 'CARD:AABBCCDD'):
            with self.assertRaises(ValueError):
                panel.normalize_uid(value)

    def test_private_config_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'config.json'
            with patch.object(panel, 'CONFIG', path):
                self.assertEqual(panel.load_config()['password'], 'password')
                config = dict(panel.DEFAULTS, uid='AABBCCDD', password='test-only')
                panel.save_config(config)
                self.assertEqual(panel.load_config(), config)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertFalse(path.with_suffix('.tmp').exists())

    def test_password_uses_stdin_then_enter(self):
        with patch.object(panel.subprocess, 'run') as run:
            panel.type_password('example$`secret')
            self.assertEqual(run.call_args_list[0].args[0], ['ydotool', 'type', '--file', '-'])
            self.assertEqual(run.call_args_list[0].kwargs['input'], 'example$`secret')
            self.assertEqual(run.call_args_list[1].args[0], ['ydotool', 'key', '28:1', '28:0'])

    def test_failed_typing_does_not_press_enter(self):
        with patch.object(panel.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'ydotool')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                panel.type_password('test-only')
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
