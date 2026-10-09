"""New disposable bootstrap file tests with public synthetic identifiers."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime_bootstrap_writer as writer

ROLE = '11111111-1111-1111-1111-111111111111'
SECRET = '22222222-2222-2222-2222-222222222222'


class BootstrapWriterTests(unittest.TestCase):
    def test_exclusive_private_files_fsync_and_readback_without_secret_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); directory.chmod(0o700)
            result = writer.write_once(directory, os.getuid(), os.getgid(), ROLE, SECRET)
            self.assertTrue(result['bothFilesFsyncReadbackVerified'])
            self.assertNotIn(ROLE, json.dumps(result)); self.assertNotIn(SECRET, json.dumps(result))
            self.assertEqual((directory/'role-id').read_text(), ROLE)
            self.assertEqual((directory/'secret-id').read_text(), SECRET)
            self.assertEqual((directory/'secret-id').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(writer.Rejected): writer.write_once(directory, os.getuid(), os.getgid(), ROLE, SECRET)

    def test_partial_prior_outcome_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); directory.chmod(0o700)
            target = directory/'role-id'; target.write_text(ROLE); target.chmod(0o600)
            with self.assertRaises(writer.Rejected): writer.write_once(directory, os.getuid(), os.getgid(), ROLE, SECRET)
            self.assertFalse((directory/'secret-id').exists()); self.assertEqual(target.read_text(), ROLE)

    def test_metadata_status_never_opens_credential_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary); directory.chmod(0o700)
            for name, value in [('role-id', ROLE), ('secret-id', SECRET)]:
                path = directory/name; path.write_text(value); path.chmod(0o600)
            with patch('builtins.open', side_effect=AssertionError('contents forbidden')):
                result = writer.entries(directory, os.getuid(), os.getgid())
            self.assertEqual(result, {'roleIdPresent': True, 'secretIdPresent': True})

    def test_symlink_hardlink_unknown_file_and_loose_mode_rejected(self):
        for flaw in ('symlink', 'hardlink', 'unknown', 'loose'):
            with self.subTest(flaw=flaw), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary); directory.chmod(0o700); target = directory/'role-id'
                if flaw == 'symlink': target.symlink_to('/unresolved')
                elif flaw == 'unknown': (directory/'other').write_text('public')
                else:
                    target.write_text(ROLE); target.chmod(0o644 if flaw == 'loose' else 0o600)
                    if flaw == 'hardlink': os.link(target, directory/'secret-id')
                with self.assertRaises(writer.Rejected): writer.entries(directory, os.getuid(), os.getgid())

    def test_input_rejects_root_credentials_and_non_uuid_before_write(self):
        with self.assertRaises(writer.Rejected):
            writer.handle({'operation': 'status', 'purpose': 'oss', 'rootToken': 'forbidden'})
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(writer.Rejected):
                writer.write_once(Path(temporary), os.getuid(), os.getgid(), ROLE, 'not-a-secret-id')
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
