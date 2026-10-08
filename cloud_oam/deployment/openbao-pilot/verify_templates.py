"""Validate the narrow OFFLINE template boundary; never start or configure Bao."""
from __future__ import annotations

import json
from pathlib import Path
import sys


EXPECTED_PATHS = frozenset({
    "transit/decrypt/rsc-authentication-idempotency",
    "transit/decrypt/rsc-material-request-contact",
})


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_document(path: Path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("invalid template file")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)


def validate_policy(document):
    if not isinstance(document, dict) or set(document) != {"path"}:
        raise ValueError("unexpected policy root")
    paths = document["path"]
    if not isinstance(paths, dict) or set(paths) != EXPECTED_PATHS:
        raise ValueError("policy must grant exactly the two named decrypt paths")
    for rule in paths.values():
        if not isinstance(rule, dict) or set(rule) != {"capabilities"}:
            raise ValueError("unexpected policy rule")
        if rule["capabilities"] != ["update"]:
            raise ValueError("decrypt policy must grant update only")


def validate_listener(document):
    if not isinstance(document, dict) or set(document) != {
        "ui", "raw_storage_endpoint", "log_level", "listener"
    }:
        raise ValueError("unexpected listener fragment root")
    if document["ui"] is not False or document["raw_storage_endpoint"] is not False:
        raise ValueError("UI and raw storage endpoint must be disabled")
    if document["log_level"] != "warn":
        raise ValueError("unreviewed logging mode")
    listeners = document["listener"]
    if not isinstance(listeners, list) or len(listeners) != 1:
        raise ValueError("exactly one Unix listener is allowed")
    entry = listeners[0]
    if not isinstance(entry, dict) or set(entry) != {"unix"}:
        raise ValueError("TCP and other listeners are outside this fragment")
    expected = {
        "address": "/run/rsc-openbao/api.sock",
        "socket_mode": "0660",
        "socket_user": "rsc-openbao",
        "socket_group": "rsc-key-client",
    }
    if entry["unix"] != expected:
        raise ValueError("unreviewed socket address, ownership, or permissions")


def validate_review_status(document):
    expected = {
        "schema": "rsc.openbao.offline-template-review.v1",
        "enabled": False,
        "deploymentReady": False,
        "selectedVersion": None,
        "verifiedDistributionSha256": None,
        "upstreamParserVerified": False,
        "effectivePolicyVerified": False,
        "actualListenersVerified": False,
        "storageRestoreVerified": False,
        "providerImplemented": False,
        "keyMaterialGenerated": False,
    }
    if not isinstance(document, dict) or set(document) != set(expected):
        raise ValueError("invalid offline review status")
    if any(type(document[key]) is not type(value) or document[key] != value
           for key, value in expected.items()):
        raise ValueError("offline review cannot claim runtime readiness or a selected version")


def verify(folder: Path):
    validate_policy(read_document(folder / "api-decrypt-policy.json.example"))
    validate_listener(read_document(folder / "listener.fragment.json.example"))
    validate_review_status(read_document(folder / "review-status.json"))
    return {
        "check": "openbao-offline-template-boundary",
        "status": "pass",
        "templateAllowedDecryptPaths": sorted(EXPECTED_PATHS),
        "listenerFragmentHasTcp": False,
        "deploymentReady": False,
        "upstreamParserVerified": False,
        "effectivePolicyVerified": False,
        "actualListenersVerified": False,
    }


def main():
    try:
        result = verify(Path(__file__).resolve().parent)
    except (OSError, ValueError, TypeError, KeyError):
        print("openbao-offline-template-boundary: fail", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
