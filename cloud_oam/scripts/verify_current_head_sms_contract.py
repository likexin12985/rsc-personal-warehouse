#!/usr/bin/env python3
"""Verify current-head migration and SMS-only pilot source contracts.

This is a read-only source audit.  It does not connect to PostgreSQL, KMS,
PNVS/SMS, Feishu, or a deployment target.  A passing result is local static
evidence only and never closes an external release gate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


CLOUD = Path(__file__).resolve().parents[1]
ROOT = CLOUD.parent
EXPECTED_HEAD = "20261229_0180"
EXPECTED_SCOPE = "trial-mvp"
EXPECTED_FLAGS = (
    "allowSupplyPlanning: false",
    "allowLogisticsEvents: false",
    "showOamReceipt: false",
    "showReturnOperations: false",
    "showReleaseOperations: false",
    "showComplexLifecycle: false",
)
SMS_STATIC_FIELDS = (
    "OAM_SMS_ACCESS_KEY_ID",
    "OAM_SMS_ACCESS_KEY_SECRET",
    "OAM_SMS_SECURITY_TOKEN",
    "OAM_SMS_SECURITY_TOKEN_EXPIRES_AT",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_status_count() -> int:
    return len(subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines())


def source_contracts() -> dict[str, object]:
    from pilot_preflight import sms_default_credential_chain_configured

    preflight = (CLOUD / "scripts/pilot_preflight.py").read_text(encoding="utf-8")
    config = (CLOUD / "backend/app/config.py").read_text(encoding="utf-8")
    sms = (CLOUD / "backend/app/sms.py").read_text(encoding="utf-8")
    scope = (CLOUD / "frontend/src/trialMvpScope.ts").read_text(encoding="utf-8")
    release = (CLOUD / "frontend/build/verify-pilot-release.mjs").read_text(encoding="utf-8")

    checks = {
        "pilotScope": EXPECTED_SCOPE in preflight and EXPECTED_SCOPE in config,
        "passwordDisabled": "password_login_enabled: bool = False" in config
        and "password login is disabled by the SMS-only policy" in config,
        "wechatDisabled": "wechat_login_enabled: bool = False" in config
        and "WeChat login is disabled by the SMS-only policy" in config
        and "wechat_configuration_ready(self) -> bool" in config
        and "return False" in config[config.index("def wechat_configuration_ready"):],
        "defaultChainRequired": "sms_credential_mode: Literal[\"default_chain\", \"static\", \"sts\"] = \"default_chain\"" in config
        and "environment.get(\"OAM_SMS_CREDENTIAL_MODE\", \"\").strip() == \"default_chain\"" in preflight,
        "preflightStaticInputsRejected": sms_default_credential_chain_configured(
            {"OAM_SMS_CREDENTIAL_MODE": "default_chain"}
        ) and all(
            not sms_default_credential_chain_configured({
                "OAM_SMS_CREDENTIAL_MODE": "default_chain", name: "audit-placeholder",
            }) for name in SMS_STATIC_FIELDS
        ) and all(
            not sms_default_credential_chain_configured({"OAM_SMS_CREDENTIAL_MODE": mode})
            for mode in ("static", "sts", "")
        ),
        "sdkDefaultChain": "CredentialClient()" in sms
        and "autoretry=False" in sms
        and "max_attempts=1" in sms
        and "return_verify_code" in sms,
        "scopeFrozen": "Object.freeze" in scope
        and all(flag in scope for flag in EXPECTED_FLAGS),
        "scopeReleaseBound": "trialMvpScope.ts" in release
        and all(flag in release for flag in EXPECTED_FLAGS),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise AssertionError("source contract failures: " + ", ".join(failed))
    return {"checks": checks, "flags": list(EXPECTED_FLAGS), "staticCredentialFields": list(SMS_STATIC_FIELDS)}


def migration_head() -> dict[str, object]:
    sys.path[:0] = [str(CLOUD / "backend"), str(CLOUD / "backend/tests")]
    from migration_script_cache import cache_migration_compilation
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    import test_postgresql16_release_gate as gate
    import configure_inventory_control as control
    from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST

    config = Config(str(CLOUD / "alembic.ini"))
    with cache_migration_compilation(CLOUD / "backend/alembic/versions"):
        heads = list(ScriptDirectory.from_config(config).get_heads())
    if heads != [EXPECTED_HEAD]:
        raise AssertionError(f"unexpected Alembic heads: {heads!r}")
    if control.REQUIRED_HEAD != EXPECTED_HEAD or gate.HEAD_REVISION != EXPECTED_HEAD:
        raise AssertionError("CLI/PG16 gate head binding drift")
    readiness_hash = gate._head_runtime_ready_hash()
    if not re.fullmatch(r"[0-9a-f]{64}", readiness_hash):
        raise AssertionError("invalid readiness manifest hash")
    if OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][-1] != readiness_hash:
        raise AssertionError("current runtime readiness manifest drift")
    return {
        "alembicHeads": heads,
        "controlRequiredHead": control.REQUIRED_HEAD,
        "postgresql16GateHead": gate.HEAD_REVISION,
        "readinessManifestSha256": readiness_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New local evidence JSON path")
    args = parser.parse_args()
    # Keep completed evidence immutable, including when a command is retried.
    if args.output.exists():
        parser.error("output already exists; preserve the completed receipt")
    sources = [
        Path(__file__), CLOUD / "scripts/configure_inventory_control.py",
        CLOUD / "scripts/pilot_preflight.py", CLOUD / "backend/app/config.py",
        CLOUD / "backend/app/sms.py", CLOUD / "backend/app/oam_sync_scope_security.py",
        CLOUD / "backend/tests/test_postgresql16_release_gate.py",
        CLOUD / "frontend/src/trialMvpScope.ts",
        CLOUD / "frontend/build/verify-pilot-release.mjs",
    ]
    source_hashes = {str(path.relative_to(ROOT)): sha256(path) for path in sources}
    contracts = source_contracts()
    migration = migration_head()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = {
        "schema": "rsc.current-head-sms-contract.v1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "head": head,
        "workingTreeStatusCount": git_status_count(),
        "scope": EXPECTED_SCOPE,
        "migration": migration,
        "smsOnly": contracts,
        "sourceSha256": source_hashes,
        "verificationLimit": "Source-text indicators and synthetic credential preflight only; no semantic proof of all auth behavior or deployed configuration.",
        "externalGates": {
            "hostedPostgresql16": "unverified",
            "realPnvsSmsUat": "unverified",
            "deploymentRollback": "unverified",
        },
        "networkAccess": False,
        "databaseAccess": False,
        "testsRerun": False,
        "releaseDecision": "not_ready",
    }
    if source_hashes != {str(path.relative_to(ROOT)): sha256(path) for path in sources}:
        raise AssertionError("audited sources changed during verification")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"check": "current-head-sms-contract", "result": "PASS",
        "head": head, "migration": migration, "artifactSha256": sha256(args.output),
        "releaseDecision": "not_ready"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
