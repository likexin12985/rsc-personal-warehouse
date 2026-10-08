"""New negative tests only; do not execute Bao or repeat the runtime gate."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import isolated_openbao_harness as harness


class IsolatedSafetyTests(unittest.TestCase):
    def test_wrong_binary_digest_cannot_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "bao"
            binary.write_bytes(b"not the signed executable")
            with patch.object(harness.platform, "system", return_value="Darwin"), patch.object(harness.platform, "machine", return_value="arm64"), patch.object(harness.subprocess, "Popen") as process:
                with self.assertRaisesRegex(RuntimeError, "signed release binary digest"):
                    harness.LocalBao(binary)
                process.assert_not_called()

    def test_unknown_architecture_cannot_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "bao"
            binary.write_bytes(b"synthetic")
            with patch.object(harness.platform, "system", return_value="Linux"), patch.object(harness.platform, "machine", return_value="mips"), patch.object(harness.subprocess, "Popen") as process:
                with self.assertRaisesRegex(RuntimeError, "supported runtime platform"):
                    harness.LocalBao(binary)
                process.assert_not_called()

    def test_non_api_or_injected_path_never_connects(self):
        with patch.object(harness, "UnixConnection") as connection:
            for path in ("https://example.invalid/v1/sys/init", "/sys/init", "/v1/x\ninjected"):
                with self.subTest(path=path), self.assertRaisesRegex(RuntimeError, "local API path"):
                    harness.UnixClient("/tmp/not-used").request("GET", path)
            connection.assert_not_called()

    def test_unbounded_timeout_never_connects(self):
        with patch.object(harness, "UnixConnection") as connection:
            for timeout in (0, -1, 46, float("inf")):
                with self.subTest(timeout=timeout), self.assertRaisesRegex(RuntimeError, "bounded operation timeout"):
                    harness.UnixClient("/tmp/not-used").request("GET", "/v1/sys/health", timeout_seconds=timeout)
            connection.assert_not_called()

    def server(self):
        server = object.__new__(harness.LocalBao)
        server.process = types.SimpleNamespace(pid=123)
        server.cluster_port, server.metrics_port = 12345, 12346
        server.root_token = "synthetic-not-a-real-token"
        return server

    def test_missing_or_external_or_extra_actual_listener_rejected(self):
        for observed in ("", "n*:12345\nn127.0.0.1:12346\n", "n127.0.0.1:12345\nn127.0.0.1:12346\nn127.0.0.1:9999\n"):
            result = types.SimpleNamespace(returncode=0, stdout=observed)
            with self.subTest(observed=observed), patch.object(harness.platform, "system", return_value="Darwin"), patch.object(harness.subprocess, "run", return_value=result), patch.object(harness.http.client, "HTTPConnection") as connection:
                with self.assertRaisesRegex(RuntimeError, "exact metrics and cluster"):
                    self.server().listener_evidence()
                connection.assert_not_called()

    def test_metrics_listener_serving_privileged_api_rejected(self):
        result = types.SimpleNamespace(returncode=0, stdout="n127.0.0.1:12345\nn127.0.0.1:12346\n")
        with patch.object(harness.platform, "system", return_value="Darwin"), patch.object(harness.subprocess, "run", return_value=result), patch.object(harness.http.client, "HTTPConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            with self.assertRaisesRegex(RuntimeError, "metrics TCP denies privileged API"):
                self.server().listener_evidence()
            connection.return_value.close.assert_called_once()

    def test_timeout_error_never_echoes_body_or_token(self):
        with patch.object(harness, "UnixConnection") as connection:
            connection.return_value.getresponse.side_effect = TimeoutError("synthetic-sensitive-error")
            with self.assertRaises(RuntimeError) as caught:
                harness.UnixClient("/tmp/not-used").request("POST", "/v1/transit/decrypt/synthetic", {"ciphertext": "synthetic-secret"}, token="synthetic-token")
            self.assertNotIn("synthetic-sensitive-error", str(caught.exception))
            self.assertNotIn("synthetic-secret", str(caught.exception))
            self.assertNotIn("synthetic-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
