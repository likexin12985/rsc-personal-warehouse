"""Additional verifier version/path negatives; no binary or crypto-gate reruns."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import verify_official_release as verifier


class ReleaseGuardrailTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.manifest = self.root / "manifest.json"

    def rejected(self, message, version="0.6.0"):
        with patch("sys.argv", ["verifier", "--downloads", str(self.downloads), "--manifest", str(self.manifest)]), patch.object(verifier.importlib.metadata, "version", return_value=version), patch.object(verifier.pgpy.PGPKey, "from_file") as key, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, message):
                verifier.main()
            key.assert_not_called()

    def test_unreviewed_verifier_version_rejected(self):
        self.rejected("unreviewed PGpy verifier version", version="0.6.1")

    def test_symlink_downloads_rejected(self):
        link = self.root / "download-link"
        link.symlink_to(self.downloads, target_is_directory=True)
        self.downloads = link
        self.rejected("symlink downloads directory rejected")

    def test_symlink_manifest_never_overwrites_target(self):
        target = self.root / "preserve.txt"
        target.write_text("preserve")
        self.manifest.symlink_to(target)
        self.rejected("symlink manifest rejected")
        self.assertEqual(target.read_text(), "preserve")

    def test_symlink_extraction_directory_rejected(self):
        (self.downloads / "darwin_arm64").symlink_to(self.root, target_is_directory=True)
        self.rejected("symlink extraction target rejected")

    def test_symlink_binary_never_overwrites_target(self):
        target = self.root / "preserve-binary"
        target.write_bytes(b"preserve")
        (self.downloads / "linux_amd64").mkdir()
        (self.downloads / "linux_amd64" / "bao").symlink_to(target)
        self.rejected("symlink extraction target rejected")
        self.assertEqual(target.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
