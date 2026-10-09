"""New actual interpreter import smoke: no Linux install or existing-suite rerun."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class BytecodeBoundaryTests(unittest.TestCase):
    def test_real_python39_entry_imports_do_not_pollute_public_bundle(self):
        self.assertEqual(sys.version_info[:2], (3, 9))
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            names = ('runtime_bundle.py', 'runtime_preflight.py', 'runtime_install.py')
            for name in names:
                (stage / name).write_bytes((root / name).read_bytes())
            for entry in ('runtime_install.py', 'runtime_preflight.py'):
                # Deliberately no -B/environment fallback: the entry must protect
                # its own public source directory before importing dependencies.
                result = subprocess.run([sys.executable, str(stage / entry), '--help'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=10, env={'PATH': '/usr/bin:/bin'})
                self.assertEqual(result.returncode, 0)
                self.assertEqual(set(os.listdir(stage)), set(names))
            self.assertIn('ExecStartPre=/usr/bin/python3 -B ', (root / 'runtime_bundle.py').read_text())


if __name__ == '__main__':
    unittest.main()
