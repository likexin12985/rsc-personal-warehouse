"""New same-byte/no-file-capability gateway boundary; no container execution."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime_bundle as bundle
import runtime_install as installer


class CaddyCopyTests(unittest.TestCase):
    def test_gateway_binds_pinned_plain_executable_without_adding_caps(self):
        files = bundle.build('rsc-pilot-runtime-01')
        gateway = json.loads(files['compose.json'])['services']['metadata']
        self.assertEqual(gateway['entrypoint'], ['/runtime/caddy'])
        self.assertEqual(gateway['image'], bundle.CADDY_IMAGE)
        self.assertEqual(gateway['user'], '23205:23205')
        self.assertEqual(gateway['cap_drop'], ['ALL']); self.assertEqual(gateway['cap_add'], [])
        mounted = [m for m in gateway['volumes'] if m['target'] == '/runtime/caddy']
        self.assertEqual(mounted, [bundle.bind(bundle.CADDY_BINARY, '/runtime/caddy', True)])
        self.assertEqual(json.loads(files['public-plan.json'])['caddyBinarySha256'], bundle.CADDY_SHA256)

    def test_caddy_copy_rejects_capability_any_xattr_wrong_hash_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'caddy'; path.write_bytes(b'public same-byte synthetic')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(installer, 'CADDY_SHA256', digest):
                for attributes in (['security.capability'], ['user.unknown']):
                    with (self.subTest(attributes=attributes), patch.object(installer.os, 'listxattr', return_value=attributes, create=True),
                            self.assertRaises(installer.Rejected)):
                        installer.caddy_source(path)
                with patch.object(installer.os, 'listxattr', return_value=[], create=True):
                    self.assertEqual(installer.caddy_source(path), path.read_bytes())
                    link = path.parent / 'link'; link.symlink_to(path)
                    with self.assertRaises(installer.Rejected): installer.caddy_source(link)
            with patch.object(installer.os, 'listxattr', return_value=[], create=True), self.assertRaises(installer.Rejected):
                installer.caddy_source(path)

    def test_caddy_host_copy_is_exclusive_0555_and_preserves_only_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'caddy'; installer.copy_new(path, b'public synthetic', 0o555)
            self.assertEqual(path.stat().st_mode & 0o777, 0o555)
            self.assertEqual(path.read_bytes(), b'public synthetic')
            with self.assertRaises(FileExistsError): installer.copy_new(path, b'changed', 0o555)


if __name__ == '__main__':
    unittest.main()
