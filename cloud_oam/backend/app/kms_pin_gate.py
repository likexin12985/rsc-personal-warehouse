"""Read-only deployment gate for the immutable KMS data-key pin ledger.

The command never inserts, updates, or deletes a pin.  ``--plan`` emits only
non-secret coordinates and SHA-256 fingerprints for two-person review.  The
default command reuses the API's exact structural/persisted-reference proof and
returns a fixed, desensitized failure when provisioning is incomplete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Sequence

from .config import get_settings
from .database import SessionLocal
from .production_adapters import (
    get_configured_kms_loader,
    validate_persisted_kms_key_references,
    validate_production_adapter_installation,
)


def _plan_document() -> dict[str, object]:
    settings = get_settings()
    if settings.environment != "production":
        raise RuntimeError("KMS pin planning is production-only")
    # Keep planning database-free, but reject a registry that cannot satisfy
    # the exact active production coordinates before people sign its digest.
    validate_production_adapter_installation(settings)
    loader = get_configured_kms_loader(settings)
    entries = [
        {
            "purpose": purpose,
            "kms_key_id": key_id,
            "application_key_version": version,
            "kms_key_version_id": pin.kms_key_version_id,
            "ciphertext_sha256": pin.ciphertext_sha256,
        }
        for (purpose, key_id, version), pin in sorted(loader.pin_manifest().items())
    ]
    canonical_entries = json.dumps(
        entries,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "schema": "rsc.kms.data-key-pin-plan.v1",
        "manifest_sha256": hashlib.sha256(canonical_entries).hexdigest(),
        "entries": entries,
    }


def _verify() -> None:
    settings = get_settings()
    if settings.environment != "production":
        raise RuntimeError("KMS pin gate is production-only")
    validate_production_adapter_installation(settings)
    with SessionLocal() as db:
        validate_persisted_kms_key_references(db, settings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KMS data-key pin deployment gate")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="print a non-secret deterministic review plan without database access",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.plan:
            print(
                json.dumps(
                    _plan_document(),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            _verify()
            print("kms-pin-gate: ready")
    except Exception:
        print("kms-pin-gate: not ready", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a module in deploys
    raise SystemExit(main())
