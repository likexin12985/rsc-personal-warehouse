"""Focused filesystem safety regressions; no disk image or password prompt."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('recovery_vault_under_test', Path(__file__).with_name('recovery-vault.py'))
vault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vault)


class ExistingObjectGuards(unittest.TestCase):
    def invoke(self, action, base):
        with patch.object(vault, 'BASE', base), patch.object(vault, 'preflight', return_value={'deviceNumber': base.stat().st_dev}), \
             patch.object(vault, 'command', side_effect=AssertionError('native_command_forbidden')), \
             patch('sys.argv', ['recovery-vault.py', action, '--vault-id', 'abcdef123456']), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            code = vault.main()
        return code, json.loads(output.getvalue())

    def test_create_rejects_existing_directory_without_overwriting_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = base / 'RSC-recovery-abcdef123456'
            directory.mkdir()
            receipt = directory / 'receipt.json'
            receipt.write_bytes(b'previous-attempt-must-remain-identical')
            image = directory / 'RSC-recovery.dmg'
            image.write_bytes(b'unknown-existing-image-must-not-delete')
            code, result = self.invoke('create', base)
            self.assertEqual(code, 1)
            self.assertEqual(result['safeCode'], 'existing_vault_must_not_be_recreated')
            self.assertEqual(receipt.read_bytes(), b'previous-attempt-must-remain-identical')
            self.assertEqual(image.read_bytes(), b'unknown-existing-image-must-not-delete')

    def test_status_parse_failure_is_readonly(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = base / 'RSC-recovery-abcdef123456'
            directory.mkdir()
            receipt = directory / 'receipt.json'
            receipt.write_bytes(b'interrupted incomplete receipt')
            code, result = self.invoke('status', base)
            self.assertEqual(code, 1)
            self.assertEqual(receipt.read_bytes(), b'interrupted incomplete receipt')
            self.assertEqual(list(directory.iterdir()), [receipt])

    def test_symlink_target_directory_is_never_followed_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / 'unrelated'
            real.mkdir()
            sentinel = real / 'receipt.json'
            sentinel.write_bytes(b'untouched')
            (base / 'RSC-recovery-abcdef123456').symlink_to(real, target_is_directory=True)
            for action in ('create', 'status'):
                code, result = self.invoke(action, base)
                self.assertEqual(code, 1)
                self.assertEqual(sentinel.read_bytes(), b'untouched')


if __name__ == '__main__':
    unittest.main()
