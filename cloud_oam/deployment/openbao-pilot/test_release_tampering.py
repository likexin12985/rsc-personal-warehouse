"""Negative verification using ONLY previously downloaded official public data.

Run in the disposable PGpy environment with RSC_OPENBAO_PUBLIC_FIXTURES pointing
to downloaded public key/checksums/signature. Never downloads or executes Bao.
"""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import verify_official_release as verifier


class ReleaseTamperingTests(unittest.TestCase):
    def setUp(self):
        source = Path(os.environ["RSC_OPENBAO_PUBLIC_FIXTURES"])
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ("openbao-gpg-pub-20240618.asc", "checksums.txt", "checksums.txt.gpgsig"):
            (self.root / name).write_bytes((source / name).read_bytes())
        self.manifest = self.root / "should-not-exist.json"

    def run_rejected(self, expected):
        argv = ["verifier", "--downloads", str(self.root), "--manifest", str(self.manifest)]
        with patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(SystemExit, expected):
                verifier.main()
        self.assertFalse(self.manifest.exists())
        self.assertFalse((self.root / "darwin_arm64" / "bao").exists())

    def test_altered_signed_checksums_rejected(self):
        path = self.root / "checksums.txt"
        path.write_bytes(path.read_bytes() + b"tampering\n")
        self.run_rejected("detached signature invalid")

    def test_untrusted_primary_fingerprint_rejected(self):
        with patch.object(verifier, "PRIMARY", "0" * 40):
            self.run_rejected("primary fingerprint mismatch")

    def test_untrusted_signing_subkey_rejected(self):
        with patch.object(verifier, "SIGNER", "0" * 40):
            self.run_rejected("signing key mismatch")

    def test_altered_archive_rejected_before_extraction(self):
        (self.root / "openbao_2.7.1_darwin_arm64.tar.gz").write_bytes(b"not a signed archive")
        self.run_rejected("archive does not match signed checksum")


if __name__ == "__main__":
    unittest.main()
