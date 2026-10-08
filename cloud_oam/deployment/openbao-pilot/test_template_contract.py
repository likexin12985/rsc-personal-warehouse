"""Reject privilege and listener expansion in offline review artifacts."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from verify_templates import (
    read_document, validate_listener, validate_policy, validate_review_status, verify,
)


ROOT = Path(__file__).resolve().parent


class TemplateBoundaryTests(unittest.TestCase):
    def test_reviewed_templates_remain_not_deployable(self):
        result = verify(ROOT)
        self.assertFalse(result["deploymentReady"])
        self.assertFalse(result["actualListenersVerified"])

    def test_reject_extra_or_wildcard_permission(self):
        original = read_document(ROOT / "api-decrypt-policy.json.example")
        for path in ("transit/*", "sys/*", "transit/keys/rsc-material-request-contact/rotate",
                     "transit/datakey/wrapped/rsc-authentication-idempotency"):
            with self.subTest(path=path):
                value = deepcopy(original)
                value["path"][path] = {"capabilities": ["update"]}
                with self.assertRaises(ValueError):
                    validate_policy(value)

    def test_reject_any_additional_capability(self):
        original = read_document(ROOT / "api-decrypt-policy.json.example")
        for capability in ("sudo", "read", "create", "delete", "list", "patch"):
            with self.subTest(capability=capability):
                value = deepcopy(original)
                next(iter(value["path"].values()))["capabilities"].append(capability)
                with self.assertRaises(ValueError):
                    validate_policy(value)

    def test_reject_tcp_or_additional_listener(self):
        original = read_document(ROOT / "listener.fragment.json.example")
        for address in ("0.0.0.0:8200", "[::]:8200", "127.0.0.1:8200"):
            with self.subTest(address=address):
                value = deepcopy(original)
                value["listener"].append({"tcp": {"address": address}})
                with self.assertRaises(ValueError):
                    validate_listener(value)
        value = deepcopy(original)
        value["listener"] = [{"tcp": {"address": "127.0.0.1:8200"}}]
        with self.assertRaises(ValueError):
            validate_listener(value)

    def test_reject_widened_socket_or_logging(self):
        original = read_document(ROOT / "listener.fragment.json.example")
        for field, replacement in (("socket_mode", "0666"), ("socket_user", "root"),
                                   ("socket_group", "root"), ("address", "/tmp/openbao.sock")):
            with self.subTest(field=field):
                value = deepcopy(original)
                value["listener"][0]["unix"][field] = replacement
                with self.assertRaises(ValueError):
                    validate_listener(value)
        for field, replacement in (("log_level", "debug"), ("ui", True), ("raw_storage_endpoint", True)):
            value = deepcopy(original)
            value[field] = replacement
            with self.assertRaises(ValueError):
                validate_listener(value)

    def test_reject_fabricated_readiness_and_unverified_version(self):
        original = read_document(ROOT / "review-status.json")
        for field in ("enabled", "deploymentReady", "upstreamParserVerified", "providerImplemented"):
            value = deepcopy(original)
            value[field] = True
            with self.assertRaises(ValueError):
                validate_review_status(value)
        value = deepcopy(original)
        value["selectedVersion"] = "latest"
        with self.assertRaises(ValueError):
            validate_review_status(value)

    def test_duplicate_json_keys_are_not_silently_replaced(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "policy.json"
            path.write_text('{"path": {}, "path": {"sys/*": {"capabilities": ["sudo"]}}}')
            with self.assertRaises(ValueError):
                read_document(path)


if __name__ == "__main__":
    unittest.main()
