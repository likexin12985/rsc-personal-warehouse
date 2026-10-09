#!/usr/bin/env python3
"""Check persisted-key metadata on a new owned native PG16, never a supplied DSN.

Creates six synthetic metadata tables, not the production migration chain.
The API role receives SELECT only and every scan uses a read-only transaction.
All evidence and the stopped cluster are retained under cloud_oam/artifacts.
This is not hosted PG16, 0181 migration, full ACL or production-ready evidence.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch


CLOUD = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
INSTANCE = "local-reference-scan"
TABLES = (
    "auth_idempotency_operations", "material_requests", "material_request_revisions",
    "application_key_version_claims", "kms_data_key_pins", "openbao_data_key_pins",
)


class ReferenceScanCheckFailure(RuntimeError):
    """Static stage-only failure; never database diagnostics or parameters."""


def require(condition, stage):
    if not condition:
        raise ReferenceScanCheckFailure(stage)


def source_manifest():
    paths = {
        Path(__file__).resolve(),
        CLOUD / "backend/tests/local_pg16_cluster.py",
        CLOUD / "backend/tests/conftest.py",
        CLOUD / "backend/tests/local_test_runtime.py",
        CLOUD / "deployment/postgres-init/20-loss-uuid.sql",
        CLOUD.parent / "docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md",
    }
    for name, module in tuple(sys.modules.items()):
        if name == "app" or name.startswith("app."):
            path = getattr(module, "__file__", None)
            if path and Path(path).suffix == ".py":
                paths.add(Path(path).resolve())
    require(all(path.is_relative_to(CLOUD.parent) for path in paths), "source_root")
    return {str(path.relative_to(CLOUD.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", required=True,
                        help="Directory containing explicit native PostgreSQL 16 binaries")
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD / "backend"), str(CLOUD / "backend/tests")]
    # Establish deterministic local-only import settings before loading app
    # modules; this does not run tests or connect an application database.
    runpy.run_path(str(CLOUD / "backend/tests/conftest.py"))
    from local_pg16_cluster import native_cluster
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session
    from app.foundation_models import KmsDataKeyPin
    from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
    from app.openbao_transit_candidate import OpenBaoTransitCandidate
    from app.production_adapters import AliyunKmsEnvelopeKeyLoader
    from app.persisted_key_references import (
        PersistedKeyReferenceUnavailable, scan_persisted_key_references,
    )

    evidence_root = CLOUD / "artifacts/provider-reference-scan-20261009"
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="local-pg16-checks-", dir=evidence_root))
    cluster = None
    results = []
    stage = "initialize"
    source_before = source_manifest()
    (evidence / "source-manifest-before.json").write_text(json.dumps(source_before, indent=2) + "\n")
    started = datetime.now(timezone.utc).isoformat()
    log = (evidence / "checks.log").open("x")

    def record(event_name, **values):
        line = json.dumps({"event": event_name, **values}, sort_keys=True)
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    def prohibited(*_args, **_kwargs):
        raise ReferenceScanCheckFailure("scanner_side_effect")

    def limits(db):
        db.execute(text("SET LOCAL statement_timeout='5s'"))
        db.execute(text("SET LOCAL lock_timeout='2s'"))

    def binding(db, purpose=AUTH, number=8, provider="openbao_transit_v1", key_id=None):
        digest = hashlib.sha256(f"local-pg16:{purpose}:{number}:{provider}".encode()).hexdigest()
        row = dict(purpose=purpose, application_key_version=number,
                   ciphertext_sha256=digest, created_at=NOW - timedelta(days=1))
        db.execute(ApplicationKeyVersionClaim.__table__.insert(), row | {"provider": provider})
        if provider == "aliyun_kms":
            db.execute(KmsDataKeyPin.__table__.insert(), row | {
                "kms_key_id": key_id or f"kms/{purpose}/historical-{number}",
                "kms_key_version_id": f"historical-version-{number}",
            })
        else:
            db.execute(OpenBaoDataKeyPin.__table__.insert(), row | {
                "environment": "test", "provider_instance_id": INSTANCE,
                "key_path": "transit/keys/rsc-" + purpose.replace("_", "-"),
                "transit_key_version": 2, "context_sha256": "c" * 64,
                "associated_data_sha256": "d" * 64,
            })

    def auth(db, number=8, *, status="completed", expires=None):
        db.execute(text("INSERT INTO auth_idempotency_operations "
                        "(status,expires_at,encryption_key_version) VALUES (:status,:expires,:number)"),
                   dict(status=status, expires=expires if expires is not None else NOW + timedelta(hours=1),
                        number=number))

    def envelope(number=8, *, legacy=False, **changes):
        metadata = {
            "schema": "rsc.material_request_contact.v1", "provider": "aliyun_kms",
            "kms_key_id": f"kms/{CONTACT}/historical-{number}", "key_version": number,
        } if legacy else {
            "schema": "rsc.material_request_contact.v2", "provider": "openbao_transit_v1",
            "purpose": CONTACT, "environment": "test", "provider_instance_id": INSTANCE,
            "key_path": "transit/keys/rsc-material-request-contact",
            "application_key_version": number, "transit_key_version": 2,
        }
        return metadata | {"ciphertext_b64": "synthetic-never-select", "nonce_b64": "synthetic-nonce",
                           "aad_sha256": "a" * 64, "mobile_hmac": "synthetic-mobile",
                           "contact_hmac": "synthetic-contact"} | changes

    def contact(db, value, *, history=False):
        name = "material_request_revisions" if history else "material_requests"
        db.execute(text(f"INSERT INTO {name} (contact_snapshot_jsonb) VALUES (CAST(:value AS jsonb))"),
                   {"value": json.dumps(value)})

    def snapshot(owner):
        with owner.connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            limits(db)
            return {name: db.scalar(text(
                "SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb)::text) "
                f"FROM {name} t")) for name in TABLES}

    def scan_case(api, owner, label, *, reject=False, expected=(0, 0), instance=INSTANCE):
        before = snapshot(owner)
        statements = []
        def capture(_connection, _cursor, statement, parameters, _context, _executemany):
            statements.append((statement, parameters))
        with Session(api) as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            limits(db)
            identity = db.execute(text(
                "SELECT current_user, session_user, current_setting('transaction_read_only'), "
                "current_setting('server_version_num')::int")).one()
            require(tuple(identity[:3]) == ("star_oam_api", "star_oam_api", "on")
                    and 160000 <= identity[3] < 170000, "api_read_only_identity")
            pending = KmsDataKeyPin(purpose="invalid-pending-no-flush")
            db.add(pending)
            event.listen(api, "before_cursor_execute", capture)
            for name in ("before_flush", "before_commit", "after_rollback"):
                event.listen(db, name, prohibited)
            try:
                rejected = False
                with patch.object(OpenBaoTransitCandidate, "resolve", prohibited), \
                        patch.object(AliyunKmsEnvelopeKeyLoader, "_resolve", prohibited):
                    try:
                        result = scan_persisted_key_references(
                            db, environment="test", openbao_provider_instance_id=instance, now=NOW)
                    except PersistedKeyReferenceUnavailable as error:
                        require(error.__cause__ is None and error.__context__ is None, "static_error")
                        rejected = True
                require(rejected == reject, "unexpected_scan_outcome")
                if not rejected:
                    require((len(result.aliyun), len(result.openbao)) == expected, "catalog_count")
            finally:
                event.remove(api, "before_cursor_execute", capture)
                for name in ("before_flush", "before_commit", "after_rollback"):
                    event.remove(db, name, prohibited)
                db.expunge(pending)
        require(statements and len(statements) <= 3, "bounded_query_count")
        for statement, parameters in statements:
            require(statement.lstrip().upper().startswith("SELECT") and "FOR UPDATE" not in statement.upper(),
                    "select_only")
            require(all(value not in statement and value not in repr(parameters) for value in (
                "ciphertext_b64", "nonce_b64", "aad_sha256", "mobile_hmac", "contact_hmac",
                "response_ciphertext", "ciphertext_blob")), "metadata_projection_only")
        require(snapshot(owner) == before, "scanner_changed_database")
        outcome = {"case": label, "passed": True, "expectedRejection": reject,
                   "selectCount": len(statements), "apiReadOnly": True, "dataUnchanged": True}
        results.append(outcome)
        record("check", **outcome)

    def seeded_contact(db, value, *, history=False):
        binding(db, CONTACT, 7, "aliyun_kms")
        binding(db, CONTACT, 8)
        contact(db, value, history=history)

    exit_code = 1
    try:
        with native_cluster(postgres_bin=args.postgres_bin, artifact_root=evidence / "cluster") as (cluster, engines):
            owner, api = engines["star_oam_migrator"], engines["star_oam_api"]
            record("owned_cluster_started", directory=str(cluster.relative_to(CLOUD)))
            stage = "create_minimal_schema"
            with owner.begin() as db:
                limits(db)
                for model in (KmsDataKeyPin, OpenBaoDataKeyPin, ApplicationKeyVersionClaim):
                    model.__table__.create(db)
                db.execute(text("CREATE TABLE auth_idempotency_operations ("
                                "status text, expires_at timestamptz, encryption_key_version integer)"))
                for name in ("material_requests", "material_request_revisions"):
                    db.execute(text(f"CREATE TABLE {name} (contact_snapshot_jsonb jsonb)"))
                for name in TABLES:
                    db.execute(text(f"GRANT SELECT ON {name} TO star_oam_api"))
            with api.connect() as db:
                db.execute(text("SET TRANSACTION READ ONLY"))
                limits(db)
                for name in TABLES:
                    privileges = db.execute(text(
                        "SELECT has_table_privilege(current_user,:name,'SELECT'), "
                        "has_table_privilege(current_user,:name,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                        {"name": name}).one()
                    require(tuple(privileges) == (True, False), "api_select_only_grant")
                require(not any(db.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                                                "FROM pg_roles WHERE rolname=current_user")).one()), "api_role_flags")
            cases = [("empty", lambda db: None, False, (0, 0), INSTANCE)]
            def mixed(db):
                binding(db, AUTH, 7, "aliyun_kms")
                binding(db, AUTH, 8)
                auth(db, 7)
                auth(db, 8, status="failed")
                binding(db, CONTACT, 7, "aliyun_kms")
                binding(db, CONTACT, 6, "aliyun_kms")
                binding(db, CONTACT, 8)
                contact(db, envelope())
                contact(db, envelope(7, legacy=True), history=True)
                contact(db, envelope(6, legacy=True), history=True)
            cases.append(("mixed_history_distinct_legacy_cmks", mixed, False, (3, 2), INSTANCE))
            cases.append(("historical_only", lambda db: seeded_contact(db, envelope(), history=True), False, (0, 1), INSTANCE))
            def expiry(db):
                binding(db)
                auth(db, None, status="pending")
                auth(db, None, expires=NOW)
                auth(db, 0, status="failed", expires=NOW - timedelta(microseconds=1))
                auth(db, 8, expires=NOW + timedelta(microseconds=1))
            cases.append(("expiry_and_pending_boundary", expiry, False, (0, 1), INSTANCE))
            for status in ("completed", "failed"):
                cases.append(("live_null_" + status, lambda db, s=status: auth(db, None, status=s), True, (0, 0), INSTANCE))
            malformed = {
                "unknown_schema": envelope(schema="rsc.unknown.v9"),
                "unknown_provider": envelope(provider="unknown"),
                "old_provider_mixed": envelope(7, legacy=True, provider="openbao_transit_v1"),
                "old_extra_null": envelope(7, legacy=True, purpose=None),
                "old_version_string": envelope(7, legacy=True, key_version="7"),
                "old_version_bool": envelope(7, legacy=True, key_version=True),
                "old_version_float": envelope(7, legacy=True, key_version=7.0),
                "old_version_null": envelope(7, legacy=True, key_version=None),
                "old_key_mismatch": envelope(7, legacy=True, kms_key_id="kms/different"),
                "new_version_string": envelope(application_key_version="8"),
                "new_version_bool": envelope(application_key_version=True),
                "new_version_float": envelope(application_key_version=8.0),
                "new_version_null": envelope(application_key_version=None),
                "new_extra_null": envelope(kms_key_id=None),
                "new_extra_old_version": envelope(key_version=8),
                "new_purpose": envelope(purpose=AUTH),
                "new_environment": envelope(environment="production"),
                "new_instance": envelope(provider_instance_id="different-instance"),
                "new_path": envelope(key_path="transit/keys/rsc-authentication-idempotency"),
                "new_transit_mismatch": envelope(transit_key_version=3),
                "new_transit_string": envelope(transit_key_version="2"),
                "new_transit_bool": envelope(transit_key_version=True),
                "null_envelope": None,
            }
            for label, value in malformed.items():
                cases.append((label, lambda db, v=value: seeded_contact(db, v, history=True), True, (0, 0), INSTANCE))
            def auth_bao(db):
                binding(db)
                auth(db)
            cases.append(("no_instance_for_auth_bao", auth_bao, True, (0, 0), None))
            cases.append(("no_instance_for_contact_bao", lambda db: seeded_contact(db, envelope()), True, (0, 0), None))
            cases.append(("legacy_without_bao_instance", lambda db: seeded_contact(db, envelope(7, legacy=True)), False, (1, 0), None))
            for provider in ("aliyun_kms", "openbao_transit_v1"):
                for flaw in ("missing_claim", "missing_pin", "hash", "time", "both_pins"):
                    def corrupt(db, p=provider, f=flaw):
                        binding(db, AUTH, 8, p)
                        auth(db)
                        claim = ApplicationKeyVersionClaim.__table__
                        pin = KmsDataKeyPin.__table__ if p == "aliyun_kms" else OpenBaoDataKeyPin.__table__
                        if f == "missing_claim":
                            db.execute(claim.delete())
                        elif f == "missing_pin":
                            db.execute(pin.delete())
                        elif f == "hash":
                            db.execute(claim.update().values(ciphertext_sha256="e" * 64))
                        elif f == "time":
                            db.execute(claim.update().values(created_at=NOW))
                        else:
                            common = dict(purpose=AUTH, application_key_version=8,
                                          ciphertext_sha256="e" * 64, created_at=NOW - timedelta(days=1))
                            if p == "aliyun_kms":
                                db.execute(OpenBaoDataKeyPin.__table__.insert(), common | dict(
                                    environment="test", provider_instance_id=INSTANCE,
                                    key_path="transit/keys/rsc-authentication-idempotency",
                                    transit_key_version=2, context_sha256="c" * 64, associated_data_sha256="d" * 64))
                            else:
                                db.execute(KmsDataKeyPin.__table__.insert(), common | dict(
                                    kms_key_id="kms/extra", kms_key_version_id="extra-version-8"))
                    cases.append((provider + "_" + flaw, corrupt, True, (0, 0), INSTANCE))
            for count in (128, 129):
                def bounded(db, n=count):
                    for number in range(1, n + 1):
                        binding(db, AUTH, number, "aliyun_kms")
                        auth(db, number)
                cases.append((f"distinct_{count}", bounded, count > 128, (128, 0), INSTANCE))
            for label, seed, rejected, counts, instance in cases:
                stage = label
                # Each scenario uses committed synthetic fixtures so the API
                # scans through its own direct login and READ ONLY transaction.
                with owner.begin() as db:
                    limits(db)
                    db.execute(text("TRUNCATE " + ",".join(TABLES)))
                    seed(db)
                scan_case(api, owner, label, reject=rejected, expected=counts, instance=instance)
            stage = "source_stability"
            require(source_manifest() == source_before, "source_drift")
        stage = "shutdown_verified"
        cluster_state = json.loads((cluster / "cluster-state.json").read_text())
        require((cluster_state["status"], cluster_state["checks"], cluster_state["serverExitCode"])
                == ("stopped", "passed", 0), "cluster_shutdown")
        exit_code = 0
        record("complete", passed=len(results), clusterStopped=True)
    except BaseException as error:
        record("failed", stage=stage, errorType=type(error).__name__)
    finally:
        source_after = source_manifest()
        (evidence / "source-manifest-after.json").write_text(json.dumps(source_after, indent=2) + "\n")
        if cluster is None:
            # A startup failure can occur before the context manager yields;
            # inspect only the new directory owned by this exact invocation.
            states = tuple((evidence / "cluster").glob("run-*/cluster-state.json"))
            if len(states) == 1:
                cluster = states[0].parent
        cluster_state = json.loads((cluster / "cluster-state.json").read_text()) if cluster else None
        receipt = {
            "schema": "rsc.local-pg16-reference-scan.v1", "startedAt": started,
            "finishedAt": datetime.now(timezone.utc).isoformat(), "exitCode": exit_code,
            "status": "passed" if exit_code == 0 else "failed", "lastStage": stage,
            "checks": results, "passedCount": len(results), "sourceHashesBefore": source_before,
            "sourceHashesAfter": source_after, "sourceUnchanged": source_before == source_after,
            "clusterDirectory": str(cluster.relative_to(CLOUD)) if cluster else None,
            "clusterState": cluster_state,
            "scope": "local-owned-pg16-synthetic-six-table-metadata-and-select-only-api",
            "githubReleaseGate": False, "hostedPg16": False, "migration0181Verified": False,
            "fullProductionAclVerified": False, "productionAcceptance": False,
            "releaseDecision": "not_ready", "python": sys.version,
            "dependencies": {name: package_version(name) for name in ("SQLAlchemy", "psycopg", "pydantic")},
        }
        log.close()
        receipt["logSha256"] = hashlib.sha256((evidence / "checks.log").read_bytes()).hexdigest()
        (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"evidence": str(evidence.relative_to(CLOUD)), "exitCode": exit_code}), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
