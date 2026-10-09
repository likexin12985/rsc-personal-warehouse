#!/usr/bin/env python3
"""Provider-runtime wiring on one new owned native PG16; no supplied DSN.

Uses six synthetic metadata tables, independently pinned synthetic registries,
real AES-GCM and explicit fake provider transports. The API is SELECT-only and
builds inside READ ONLY transactions. This is not hosted PG16, full 0181/ACL,
real OpenBao/KMS transport, deployment or release-approval evidence.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version as package_version
import json
from pathlib import Path
import runpy
import sys
import tempfile
import traceback
from unittest.mock import patch
import uuid


CLOUD = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
ALI = "aliyun_kms"
BAO = "openbao_transit_v1"
INSTANCE = "local-pg16-runtime"
TABLES = ("auth_idempotency_operations", "material_requests", "material_request_revisions",
          "application_key_version_claims", "kms_data_key_pins", "openbao_data_key_pins")


class RuntimeCheckFailure(RuntimeError):
    """Stage-only error: never database parameters, registry material or keys."""


def require(condition, stage):
    if not condition:
        raise RuntimeCheckFailure(stage)


def source_manifest():
    paths = {Path(__file__).resolve(), CLOUD / "backend/tests/local_pg16_cluster.py",
             CLOUD / "backend/tests/conftest.py", CLOUD / "backend/tests/local_test_runtime.py",
             CLOUD / "deployment/postgres-init/20-loss-uuid.sql",
             CLOUD.parent / "docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md"}
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
    parser.add_argument("--postgres-bin", required=True)
    parser.add_argument("--focus", choices=("full", "contact-preflight"), default="full",
                        help="Run the original fourteen checks or only the later contact-preflight repair checks")
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD / "backend"), str(CLOUD / "backend/tests")]
    # Local-only settings initialization; no fixture or pytest test is executed.
    runpy.run_path(str(CLOUD / "backend/tests/conftest.py"))
    from local_pg16_cluster import native_cluster
    from sqlalchemy import event, text
    from sqlalchemy.orm import Session
    from app.config import Settings
    from app.foundation_models import KmsDataKeyPin
    from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
    from app.openbao_transit_candidate import (
        OpenBaoKeyCoordinate, OpenBaoDecryptResponse, context_b64, associated_data_b64,
    )
    from app.openbao_registry_candidate import REGISTRY_SCHEMA as BAO_SCHEMA
    from app.persisted_key_references import read_required_key_claims, PersistedKeyReferenceUnavailable
    from app.formal_services.authentication_idempotency import (
        AuthenticationEncryptionKeyUnavailable, KmsAuthenticationKeyProvider,
        create_authentication_response_cipher,
    )
    from app.formal_services.material_request_contact import (
        protect_material_request_contact, reveal_material_request_contact,
    )
    import app.production_adapters as adapters
    import app.production_key_runtime as wiring

    evidence_root = CLOUD / "artifacts/provider-runtime-wiring-20261009"
    evidence_root.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix="local-pg16-runtime-", dir=evidence_root)).resolve()
    evidence.chmod(0o700)
    registry_root = evidence / "registries"
    registry_root.mkdir(mode=0o700)
    results, cluster = [], None
    stage, exit_code = "initialize", 1
    source_before = source_manifest()
    (evidence / "source-manifest-before.json").write_text(json.dumps(source_before, indent=2) + "\n")
    started = datetime.now(timezone.utc).isoformat()
    log = (evidence / "checks.log").open("x")

    def record(name, **values):
        line = json.dumps({"event": name, **values}, sort_keys=True)
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    def passed(name, **values):
        outcome = {"case": name, "passed": True, **values}
        results.append(outcome)
        record("check", **outcome)

    def limits(db):
        db.execute(text("SET LOCAL statement_timeout='5s'"))
        db.execute(text("SET LOCAL lock_timeout='2s'"))

    def snapshot(owner):
        with owner.connect() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            limits(db)
            return {name: db.scalar(text(
                "SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb)::text) "
                f"FROM {name} t")) for name in TABLES}

    def prohibited(*_args, **_kwargs):
        raise RuntimeCheckFailure("unexpected_side_effect")

    class Fixture:
        def __init__(self):
            self.ali, self.bao, self.pins, self.calls = [], [], [], []
            self.fail_bao = False
            self.fail_ali = False

        def seed(self, db, purpose, number, provider, *, key_id=None):
            label = f"{provider}:{purpose}:{number}"
            if provider == ALI:
                blob = base64.b64encode(label.encode() * 2).decode()
                entry = dict(purpose=purpose, kms_key_id=key_id or f"kms/{purpose}/v{number}",
                             application_key_version=number, kms_key_version_id=f"version-{number:08d}",
                             ciphertext_blob=blob)
                entry["encryption_context"] = adapters._encryption_context(purpose, entry["kms_key_id"], number)
                self.ali.append(entry)
                pin = {name: entry[name] for name in ("purpose", "kms_key_id", "application_key_version", "kms_key_version_id")}
                model = KmsDataKeyPin
            else:
                coordinate = OpenBaoKeyCoordinate(purpose, "test", INSTANCE, number)
                blob = "vault:v2:" + base64.b64encode(hashlib.sha512(label.encode()).digest()[:60]).decode()
                entry = dict(purpose=purpose, environment="test", provider_instance_id=INSTANCE,
                             application_key_version=number, key_path=coordinate.key_path,
                             transit_key_version=2, ciphertext=blob, context_b64=context_b64(coordinate),
                             associated_data_b64=associated_data_b64(coordinate))
                self.bao.append(entry)
                pin = {name: entry[name] for name in ("purpose", "environment", "provider_instance_id",
                                                    "application_key_version", "key_path", "transit_key_version")}
                pin.update(context_sha256=hashlib.sha256(base64.b64decode(entry["context_b64"])).hexdigest(),
                           associated_data_sha256=hashlib.sha256(base64.b64decode(entry["associated_data_b64"])).hexdigest())
                model = OpenBaoDataKeyPin
            pin.update(ciphertext_sha256=hashlib.sha256(blob.encode()).hexdigest(), created_at=NOW)
            db.execute(model.__table__.insert(), pin)
            db.execute(ApplicationKeyVersionClaim.__table__.insert(), dict(
                purpose=purpose, application_key_version=number, provider=provider,
                ciphertext_sha256=pin["ciphertext_sha256"], created_at=NOW))
            self.pins.append((purpose, number))

        def decrypt(self, *, request, timeout_seconds):
            self.calls.append((BAO, request.path))
            if self.fail_bao:
                raise RuntimeCheckFailure("synthetic_provider_unavailable")
            entry = next(row for row in self.bao if row["ciphertext"] == request.ciphertext)
            require(request.context == entry["context_b64"] and request.associated_data == entry["associated_data_b64"]
                    and timeout_seconds <= 3, "exact_openbao_request")
            return OpenBaoDecryptResponse(200, {"data": {"plaintext": base64.b64encode(
                hashlib.sha256(request.ciphertext.encode()).digest()).decode()}})

        def aliyun_client(self):
            fixture = self
            class Client:
                def decrypt(self, *, ciphertext_blob, encryption_context):
                    entry = next(row for row in fixture.ali if row["ciphertext_blob"] == ciphertext_blob)
                    require(encryption_context == entry["encryption_context"], "exact_aliyun_context")
                    fixture.calls.append((ALI, entry["kms_key_id"]))
                    if fixture.fail_ali:
                        raise RuntimeError("synthetic-private-provider-marker")
                    return adapters.KmsDecryptResult(base64.b64encode(hashlib.sha256(ciphertext_blob.encode()).digest()).decode(),
                                                     entry["kms_key_id"], entry["kms_key_version_id"])
            return Client()

        def settings(self, *, auth=BAO, contact=BAO, av=8, cv=8, key_id=None):
            bao_path, ali_path = registry_root / "bao.json", registry_root / "ali.json"
            bao_path.write_text(json.dumps(dict(schema=BAO_SCHEMA, provider=BAO, entries=self.bao)))
            ali_path.write_text(json.dumps(dict(schema=adapters.REGISTRY_SCHEMA, entries=self.ali)))
            bao_path.chmod(0o600)
            ali_path.chmod(0o600)
            self.loader = adapters.AliyunKmsEnvelopeKeyLoader(
                endpoint="kms.cn-hangzhou.aliyuncs.com", region="cn-hangzhou", registry_path=str(ali_path),
                client_factory=lambda **_kwargs: self.aliyun_client(),
            ) if self.ali else None
            return Settings(_env_file=None, environment="test", database_url="sqlite+pysqlite:///:memory:",
                auth_idempotency_encryption_provider=auth, auth_idempotency_encryption_key_version=av,
                auth_idempotency_kms_key_id=key_id or f"kms/{AUTH}/v{av}",
                material_request_contact_encryption_provider=contact, material_request_contact_encryption_key_version=cv,
                material_request_contact_kms_key_id=f"kms/{CONTACT}/v{cv}",
                openbao_provider_instance_id=INSTANCE, openbao_encrypted_data_key_registry_path=str(bao_path),
                openbao_socket_path="/run/bao/api.sock", openbao_token_file="/run/token/api-token",
                openbao_api_uid=11001, openbao_bao_uid=11002, openbao_bao_gid=11002,
                openbao_shared_gid=11004, openbao_token_projector_uid=11003)

    def auth(db, version):
        db.execute(text("INSERT INTO auth_idempotency_operations VALUES ('completed',:expiry,:version)"),
                   dict(expiry=NOW + timedelta(hours=1), version=version))

    def contact(db, version, *, legacy=False):
        value = dict(schema="rsc.material_request_contact.v1", provider=ALI,
                     kms_key_id=f"kms/{CONTACT}/v{version}", key_version=version) if legacy else dict(
            schema="rsc.material_request_contact.v2", provider=BAO, purpose=CONTACT, environment="test",
            provider_instance_id=INSTANCE, key_path="transit/keys/rsc-material-request-contact",
            application_key_version=version, transit_key_version=2)
        value.update(ciphertext_b64="synthetic-not-selected", nonce_b64="synthetic-not-selected",
                     aad_sha256="a" * 64, mobile_hmac="synthetic-not-selected", contact_hmac="synthetic-not-selected")
        name = "material_request_revisions" if legacy else "material_requests"
        db.execute(text(f"INSERT INTO {name} VALUES (CAST(:value AS jsonb))"), {"value": json.dumps(value)})

    def mixed(db, fixture):
        for purpose in (AUTH, CONTACT):
            for number in (6, 7):
                fixture.seed(db, purpose, number, ALI)
            fixture.seed(db, purpose, 8, BAO)
        for number in (6, 7):
            auth(db, number)
            contact(db, number, legacy=True)
        contact(db, 8)

    def build_case(api, owner, fixture, settings, label, *, reject=False, claims_only=False):
        before, statements = snapshot(owner), []
        def capture(_connection, _cursor, statement, parameters, _context, _many):
            statements.append((statement, parameters))
        result, rejected = None, False
        with Session(api) as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            limits(db)
            identity = db.execute(text("SELECT current_user,session_user,current_setting('transaction_read_only'),"
                                       "current_setting('server_version_num')::int")).one()
            require(tuple(identity[:3]) == ("star_oam_api", "star_oam_api", "on") and 160000 <= identity[3] < 170000,
                    "api_read_only_identity")
            pending = KmsDataKeyPin(purpose="invalid-pending-never-flushed")
            db.add(pending)
            event.listen(api, "before_cursor_execute", capture)
            for name in ("before_flush", "before_commit", "after_rollback"):
                event.listen(db, name, prohibited)
            try:
                with patch.object(wiring, "OpenBaoUnixDecryptTransport", lambda **_kwargs: fixture), \
                     patch.object(adapters, "get_configured_kms_loader", lambda _settings: fixture.loader):
                    try:
                        if claims_only:
                            result = read_required_key_claims(db, environment="test", required=frozenset(fixture.pins),
                                                             openbao_provider_instance_id=INSTANCE)
                        else:
                            result = wiring.build_production_key_runtime(db, settings, now=NOW)
                    except (adapters.ProductionAdapterConfigurationError, PersistedKeyReferenceUnavailable) as error:
                        require(error.__cause__ is error.__context__ is None, "static_error")
                        rejected = True
                require(rejected == reject, "unexpected_runtime_outcome")
                require(not fixture.calls, "startup_decrypted")
            finally:
                event.remove(api, "before_cursor_execute", capture)
                for name in ("before_flush", "before_commit", "after_rollback"):
                    event.remove(db, name, prohibited)
                db.expunge(pending)
        require(statements and len(statements) <= 5, "bounded_query_count")
        for statement, parameters in statements:
            require(statement.lstrip().upper().startswith("SELECT") and "FOR UPDATE" not in statement.upper(), "select_only")
            require(all(name not in statement and name not in repr(parameters) for name in (
                "ciphertext_b64", "nonce_b64", "aad_sha256", "mobile_hmac", "contact_hmac", "response_ciphertext", "ciphertext_blob")),
                "metadata_projection_only")
        require(snapshot(owner) == before, "runtime_changed_database")
        passed(label, expectedRejection=reject, selectCount=len(statements), apiReadOnly=True, dataUnchanged=True)
        return result

    def crypto_checks(api, owner, fixture, settings, runtime):
        before, statements = snapshot(owner), []
        def capture(*args):
            statements.append(args[2])
        event.listen(api, "before_cursor_execute", capture)
        try:
            first, second = runtime.authentication_cipher(settings), runtime.authentication_cipher(settings)
            require(first is not second and not fixture.calls, "request_local_cipher")
            require(first.active_key_version() == first.active_key_version() == 8 and len(fixture.calls) == 1, "request_local_cache")
            second.active_key_version()
            require(len(fixture.calls) == 2, "cross_request_dek_cache")
            for number in (6, 7):
                sealed = first.encrypt(b"synthetic-historical-response", aad=b"synthetic-binding", key_version=number)
                require(second.decrypt(sealed.ciphertext, nonce=sealed.nonce, aad=b"synthetic-binding", key_version=number)
                        == b"synthetic-historical-response", "historical_auth_cmk")
            require({f"kms/{AUTH}/v6", f"kms/{AUTH}/v7"}.issubset({item[1] for item in fixture.calls if item[0] == ALI}), "historical_cmk_routing")
            passed("snapshot_auth_real_aes_distinct_cmks_and_request_cache", noRequestSql=True)
            cipher = runtime.contact_cipher(settings)
            context = dict(mobile_hmac_secret="h" * 40, mobile_hash_version=1,
                           request_id=uuid.uuid4(), requester_person_id=uuid.uuid4())
            envelope = protect_material_request_contact(cipher=cipher, kms_key_id="", name="Synthetic",
                                                       mobile="13800138000", **context)
            require(envelope["schema"] == "rsc.material_request_contact.v2" and
                    reveal_material_request_contact(cipher=cipher, kms_key_id="", envelope=envelope, **context)["name"] == "Synthetic", "contact_v2_roundtrip")
            for number in (6, 7):
                key_id = f"kms/{CONTACT}/v{number}"
                legacy = create_authentication_response_cipher(environment="test", key_provider=KmsAuthenticationKeyProvider(
                    kms_key_id=key_id, active_version=number,
                    key_loader=lambda key_id, version: fixture.loader(CONTACT, key_id, version)))
                envelope = protect_material_request_contact(cipher=legacy, kms_key_id=key_id, name="Synthetic",
                                                           mobile="13800138000", **context)
                require(reveal_material_request_contact(cipher=cipher, kms_key_id="", envelope=envelope, **context)["mobile"]
                        == "13800138000", "contact_v1_historical_cmk")
            passed("snapshot_contact_v2_and_two_v1_cmks_real_aes", noRequestSql=True)
            count = len(fixture.calls)
            for operation in (
                lambda: runtime.authentication_cipher(settings.model_copy(update={"openbao_provider_instance_id": "wrong-instance"})),
                lambda: runtime.probe(AUTH, "0" * 64, 8),
                lambda: first.encrypt(b"synthetic", aad=b"synthetic", key_version=999),
            ):
                try:
                    operation()
                except AuthenticationEncryptionKeyUnavailable:
                    pass
                else:
                    raise RuntimeCheckFailure("unexpected_undeclared_binding_success")
            require(len(fixture.calls) == count, "untrusted_binding_reached_provider")
            passed("settings_drift_unknown_version_wrong_probe_rejected_before_provider")
            coordinate = next(value for value in runtime.required_coordinates if value[0] == AUTH and value[2] == 8)
            require(len(runtime.probe(*coordinate)) == 32 and len(runtime.probe(*coordinate)) == 32
                    and len(fixture.calls) == count + 2, "probe_cached_dek")
            passed("readiness_exact_full_binding_no_dek_cache")
            fixture.fail_bao = True
            count = len(fixture.calls)
            try:
                runtime.authentication_cipher(settings).active_key_version()
            except AuthenticationEncryptionKeyUnavailable:
                pass
            else:
                raise RuntimeCheckFailure("provider_failure_not_closed")
            require(fixture.calls[count:] == [(BAO, "/v1/transit/decrypt/rsc-authentication-idempotency")], "cross_provider_fallback")
            passed("provider_failure_never_falls_back")
        finally:
            event.remove(api, "before_cursor_execute", capture)
        require(not statements and snapshot(owner) == before, "request_used_database_or_mutated")

    def contact_preflight_checks(api, owner, fixture, settings, runtime):
        before, statements = snapshot(owner), []
        def capture(*args):
            statements.append(args[2])
        def rejected_without_provider_details(operation):
            try:
                operation()
            except Exception as error:
                require(error.__cause__ is error.__context__ is None, "provider_error_chain")
                rendered = "".join(traceback.format_exception(error))
                require("synthetic-private-provider-marker" not in rendered, "provider_error_disclosure")
            else:
                raise RuntimeCheckFailure("preflight_failure_not_closed")
        event.listen(api, "before_cursor_execute", capture)
        try:
            require(not fixture.calls, "construction_decrypted")
            cipher = runtime.contact_cipher(settings)
            cipher.preflight_active_key()
            require(fixture.calls == [(BAO, "/v1/transit/decrypt/rsc-material-request-contact")],
                    "contact_preflight_no_provider_probe")
            passed("contact_explicit_preflight_success_resolves_active_provider", noRequestSql=True)
            fixture.fail_bao = True
            count = len(fixture.calls)
            rejected_without_provider_details(lambda: runtime.contact_cipher(settings).preflight_active_key())
            require(fixture.calls[count:] == [(BAO, "/v1/transit/decrypt/rsc-material-request-contact")],
                    "contact_preflight_cross_provider_fallback")
            passed("contact_explicit_preflight_rejects_provider_failure", noFallback=True, staticError=True)
            fixture.fail_ali = True
            count = len(fixture.calls)
            rejected_without_provider_details(lambda: runtime.contact_cipher(settings).legacy_cipher.active_key_version())
            require(len(fixture.calls) == count + 1 and fixture.calls[-1][0] == ALI,
                    "legacy_provider_error_route")
            passed("contact_legacy_runtime_resolve_sanitizes_provider_failure", staticError=True)
        finally:
            event.remove(api, "before_cursor_execute", capture)
        require(not statements and snapshot(owner) == before, "request_used_database_or_mutated")

    try:
        with native_cluster(postgres_bin=args.postgres_bin, artifact_root=evidence / "cluster") as (cluster, engines):
            owner, api = engines["star_oam_migrator"], engines["star_oam_api"]
            record("owned_cluster_started", directory=str(cluster.relative_to(CLOUD)))
            stage = "create_minimal_schema"
            with owner.begin() as db:
                limits(db)
                for model in (KmsDataKeyPin, OpenBaoDataKeyPin, ApplicationKeyVersionClaim):
                    model.__table__.create(db)
                db.execute(text("CREATE TABLE auth_idempotency_operations (status text, expires_at timestamptz, encryption_key_version integer)"))
                for name in ("material_requests", "material_request_revisions"):
                    db.execute(text(f"CREATE TABLE {name} (contact_snapshot_jsonb jsonb)"))
                for name in TABLES:
                    db.execute(text(f"GRANT SELECT ON {name} TO star_oam_api"))
            with api.connect() as db:
                db.execute(text("SET TRANSACTION READ ONLY"))
                limits(db)
                for name in TABLES:
                    require(tuple(db.execute(text("SELECT has_table_privilege(current_user,:name,'SELECT'),"
                        "has_table_privilege(current_user,:name,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')"),
                        {"name": name}).one()) == (True, False), "api_select_only_grant")
                require(not any(db.execute(text("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
                                                "FROM pg_roles WHERE rolname=current_user")).one()), "api_role_flags")
            if args.focus == "contact-preflight":
                stage = "contact_preflight_repair"
                fixture = Fixture()
                with owner.begin() as db:
                    mixed(db, fixture)
                settings = fixture.settings()
                runtime = build_case(api, owner, fixture, settings, "contact_preflight_repair_runtime_readonly")
                contact_preflight_checks(api, owner, fixture, settings, runtime)
            else:
                stage = "mixed_positive"
                fixture = Fixture()
                with owner.begin() as db:
                    mixed(db, fixture)
                settings = fixture.settings()
                catalog = build_case(api, owner, fixture, settings, "explicit_claims_api_readonly", claims_only=True)
                require((len(catalog.aliyun), len(catalog.openbao)) == (4, 2), "explicit_claims_count")
                runtime = build_case(api, owner, fixture, settings, "mixed_runtime_api_readonly")
                crypto_checks(api, owner, fixture, settings, runtime)
                for flaw in ("missing_claim", "claim_hash_mismatch", "active_provider_mismatch", "active_cmk_mismatch",
                             "missing_legacy_registry_key", "missing_openbao_registry_key", "registry_hash_mismatch"):
                    stage = flaw
                    fixture = Fixture()
                    with owner.begin() as db:
                        limits(db)
                        db.execute(text("TRUNCATE " + ",".join(TABLES)))
                        mixed(db, fixture)
                        claim = ApplicationKeyVersionClaim.__table__
                        where = (claim.c.purpose == AUTH) & (claim.c.application_key_version == 7)
                        if flaw == "missing_claim":
                            db.execute(claim.delete().where(where))
                        elif flaw == "claim_hash_mismatch":
                            db.execute(claim.update().where(where).values(ciphertext_sha256="e" * 64))
                        elif flaw == "active_cmk_mismatch":
                            fixture.seed(db, AUTH, 9, ALI, key_id="kms/auth/real-cmk")
                    options = {}
                    if flaw == "active_provider_mismatch":
                        options["auth"] = ALI
                    elif flaw == "active_cmk_mismatch":
                        options.update(auth=ALI, av=9, key_id="kms/auth/wrong-cmk")
                    elif flaw == "missing_legacy_registry_key":
                        fixture.ali = [row for row in fixture.ali if not (row["purpose"] == AUTH and row["application_key_version"] == 7)]
                    elif flaw == "missing_openbao_registry_key":
                        fixture.bao = [row for row in fixture.bao if row["purpose"] != CONTACT]
                    elif flaw == "registry_hash_mismatch":
                        fixture.bao[0]["ciphertext"] = "vault:v2:" + base64.b64encode(b"Z" * 60).decode()
                    settings = fixture.settings(**options)
                    build_case(api, owner, fixture, settings, flaw, reject=True)
            stage = "source_stability"
            require(source_manifest() == source_before, "source_drift")
        stage = "shutdown_verified"
        state = json.loads((cluster / "cluster-state.json").read_text())
        require((state["status"], state["checks"], state["serverExitCode"]) == ("stopped", "passed", 0)
                and not (cluster / "data/postmaster.pid").exists(), "cluster_shutdown")
        exit_code = 0
        record("complete", passed=len(results), clusterStopped=True)
    except BaseException as error:
        record("failed", stage=stage, errorType=type(error).__name__)
    finally:
        source_after = source_manifest()
        (evidence / "source-manifest-after.json").write_text(json.dumps(source_after, indent=2) + "\n")
        if cluster is None:
            states = tuple((evidence / "cluster").glob("run-*/cluster-state.json"))
            if len(states) == 1:
                cluster = states[0].parent
        state = json.loads((cluster / "cluster-state.json").read_text()) if cluster else None
        receipt = dict(schema="rsc.local-pg16-key-runtime.v1", startedAt=started,
            finishedAt=datetime.now(timezone.utc).isoformat(), exitCode=exit_code, stage=stage,
            scope="owned-local-six-metadata-tables", hosted=False, full0181Migration=False,
            focus=args.focus,
            fullProductionAcl=False, realProviderTransport=False, releaseDecision="not_ready",
            providers="explicit synthetic KMS/OpenBao transports; real AES-GCM",
            memoryCapMiB=None, memoryCapNote="existing native_cluster helper has no memory-cap parameter",
            resultCount=len(results), results=results, sourceStable=source_before == source_after,
            sourceManifestSha256=hashlib.sha256(json.dumps(source_after, sort_keys=True).encode()).hexdigest(),
            postgres=state, python=sys.version.split()[0], sqlalchemy=package_version("sqlalchemy"),
            psycopg=package_version("psycopg"), noProductionConnections=True)
        (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        record("receipt", path=str((evidence / "receipt.json").relative_to(CLOUD)))
        log.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
