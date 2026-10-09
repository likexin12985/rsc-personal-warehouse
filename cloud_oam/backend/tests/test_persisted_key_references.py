"""Provider-aware reference scanning against synthetic, metadata-only SQLite.

The business tables intentionally contain only the projected metadata columns.
These tests neither establish hosted PG16 readiness nor contact-envelope crypto
validity; existing persistence guards own the latter contract.
"""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest
from sqlalchemy import DateTime, bindparam, create_engine, event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.demand_models import MaterialRequest, MaterialRequestRevision
from app.foundation_models import KmsDataKeyPin
from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
from app.openbao_transit_candidate import OpenBaoReviewedPin
from app.persisted_key_references import (
    AliyunPersistedKeyReference,
    PersistedKeyReferenceCatalog,
    PersistedKeyReferenceUnavailable,
    _contact_projection,
    scan_persisted_key_references,
)


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
INSTANCE = "reference-scan-test"
CONTACT_TABLES = ("material_requests", "material_request_revisions")


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (KmsDataKeyPin, OpenBaoDataKeyPin, ApplicationKeyVersionClaim):
        model.__table__.create(engine)
    with engine.begin() as connection:
        # No integer affinity lets synthetic malformed auth versions remain
        # visible instead of SQLite silently converting numeric strings.
        connection.exec_driver_sql(
            "CREATE TABLE auth_idempotency_operations ("
            "id INTEGER PRIMARY KEY, status TEXT, expires_at DATETIME, "
            "encryption_key_version)"
        )
        for name in CONTACT_TABLES:
            connection.exec_driver_sql(
                f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, contact_snapshot_jsonb JSON)"
            )
    with Session(engine) as session:
        yield session
    engine.dispose()


def scan(db, **kwargs):
    return scan_persisted_key_references(db, **({
        "environment": "test", "openbao_provider_instance_id": INSTANCE, "now": NOW,
    } | kwargs))


def add_auth(db, version=8, *, status="completed", expires_at=None):
    statement = text(
        "INSERT INTO auth_idempotency_operations "
        "(status, expires_at, encryption_key_version) VALUES (:status, :expires, :version)"
    ).bindparams(bindparam("expires", type_=DateTime(timezone=True)))
    db.execute(statement, {
        "status": status, "expires": expires_at if expires_at is not None else NOW + timedelta(hours=1),
        "version": version,
    })


def contact_v1(version=7, **changes):
    return {
        "schema": "rsc.material_request_contact.v1", "provider": "aliyun_kms",
        "kms_key_id": "kms/contact/history", "key_version": version,
        "ciphertext_b64": "synthetic-do-not-project", "nonce_b64": "synthetic-nonce",
        "aad_sha256": "a" * 64, "mobile_hmac": "synthetic-mobile-hmac",
        "contact_hmac": "synthetic-contact-hmac",
    } | changes


def contact_v2(version=8, **changes):
    return {
        "schema": "rsc.material_request_contact.v2", "provider": "openbao_transit_v1",
        "purpose": CONTACT, "environment": "test", "provider_instance_id": INSTANCE,
        "key_path": "transit/keys/rsc-material-request-contact",
        "application_key_version": version, "transit_key_version": 2,
        "ciphertext_b64": "synthetic-do-not-project", "nonce_b64": "synthetic-nonce",
        "aad_sha256": "a" * 64, "mobile_hmac": "synthetic-mobile-hmac",
        "contact_hmac": "synthetic-contact-hmac",
    } | changes


def add_contact(db, envelope, *, historical=False):
    name = CONTACT_TABLES[int(historical)]
    db.execute(text(f"INSERT INTO {name} (contact_snapshot_jsonb) VALUES (:value)"), {
        "value": json.dumps(envelope),
    })


def add_binding(db, purpose=AUTH, version=8, *, provider="openbao_transit_v1"):
    digest = hashlib.sha256(f"{purpose}:{version}:{provider}".encode()).hexdigest()
    common = {"purpose": purpose, "application_key_version": version,
              "ciphertext_sha256": digest, "created_at": NOW - timedelta(days=1)}
    db.execute(ApplicationKeyVersionClaim.__table__.insert(), common | {"provider": provider})
    if provider == "aliyun_kms":
        db.execute(KmsDataKeyPin.__table__.insert(), common | {
            "kms_key_id": "kms/contact/history" if purpose == CONTACT else "kms/auth/history",
            "kms_key_version_id": f"legacy-version-{version}",
        })
    else:
        db.execute(OpenBaoDataKeyPin.__table__.insert(), common | {
            "environment": "test", "provider_instance_id": INSTANCE,
            "key_path": "transit/keys/rsc-" + purpose.replace("_", "-"),
            "transit_key_version": 2,
            "context_sha256": "c" * 64, "associated_data_sha256": "d" * 64,
        })
    return digest


def assert_closed(db, **kwargs):
    with pytest.raises(PersistedKeyReferenceUnavailable) as failure:
        scan(db, **kwargs)
    assert failure.value.__cause__ is None
    assert failure.value.__context__ is None
    return failure.value


def test_empty_catalog_and_detached_immutable_mixed_history(db):
    empty = scan(db)
    assert type(empty) is PersistedKeyReferenceCatalog
    assert empty.aliyun == empty.openbao == ()
    for purpose in (AUTH, CONTACT):
        add_binding(db, purpose, 7, provider="aliyun_kms")
        add_binding(db, purpose, 8)
    add_auth(db, 7)
    add_auth(db, 8, status="failed")
    add_contact(db, contact_v2())
    add_contact(db, contact_v1(), historical=True)
    # Repeated business rows must not require duplicate registrations.
    add_contact(db, contact_v2(), historical=True)
    add_auth(db, 8)
    catalog = scan(db)
    assert type(catalog.aliyun) is type(catalog.openbao) is tuple
    assert len(catalog.aliyun) == len(catalog.openbao) == 2
    assert all(type(item) is AliyunPersistedKeyReference for item in catalog.aliyun)
    assert {(item.purpose, item.application_key_version, item.kms_key_id) for item in catalog.aliyun} == {
        (AUTH, 7, "kms/auth/history"), (CONTACT, 7, "kms/contact/history"),
    }
    assert all(item.kms_key_version_id == "legacy-version-7" for item in catalog.aliyun)
    assert all(type(item) is OpenBaoReviewedPin for item in catalog.openbao)
    assert {(item.coordinate.purpose, item.coordinate.application_key_version) for item in catalog.openbao} == {
        (AUTH, 8), (CONTACT, 8),
    }
    assert all((item.coordinate.environment, item.coordinate.provider_instance_id,
                item.transit_key_version, item.context_sha256, item.associated_data_sha256) ==
               ("test", INSTANCE, 2, "c" * 64, "d" * 64) for item in catalog.openbao)
    assert not db.identity_map
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        catalog.aliyun = ()
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        catalog.aliyun[0].kms_key_id = "mutated"


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("version", [7, 8])
def test_contact_rows_require_keys_independently_of_auth_activity(db, historical, version):
    provider = "aliyun_kms" if version == 7 else "openbao_transit_v1"
    add_binding(db, CONTACT, version, provider=provider)
    add_contact(db, contact_v1() if version == 7 else contact_v2(), historical=historical)
    catalog = scan(db)
    assert len(catalog.aliyun) + len(catalog.openbao) == 1


def test_only_unexpired_terminal_auth_records_are_references(db):
    add_auth(db, None, status="pending")
    add_auth(db, 0, expires_at=NOW - timedelta(microseconds=1))
    add_auth(db, None, status="failed", expires_at=NOW)
    add_binding(db, AUTH, 8)
    add_auth(db, 8, expires_at=NOW + timedelta(microseconds=1))
    catalog = scan(db)
    assert catalog.aliyun == ()
    assert len(catalog.openbao) == 1
    assert catalog.openbao[0].coordinate.application_key_version == 8


@pytest.mark.parametrize("version", [None, "8", "1 OR 1=1", 0, -1, 2_147_483_648, 8.5])
@pytest.mark.parametrize("status", ["completed", "failed"])
def test_live_auth_null_or_noncanonical_versions_fail_closed(db, version, status):
    add_auth(db, version, status=status)
    assert_closed(db)


@pytest.mark.parametrize("envelope", [
    None, [], "synthetic-envelope-secret", {},
    contact_v1(schema="unknown.v9"), contact_v1(provider="openbao_transit_v1"),
    contact_v1(key_version="7"), contact_v1(key_version=True),
    contact_v1(key_version=7.0), contact_v1(key_version=0),
    contact_v1(kms_key_id="kms/other"), contact_v1(kms_key_id=7),
    contact_v1(purpose=None), contact_v1(application_key_version=None),
    contact_v2(provider="aliyun_kms"), contact_v2(purpose=AUTH),
    contact_v2(environment="production"), contact_v2(provider_instance_id="different-instance"),
    contact_v2(key_path="transit/keys/rsc-authentication-idempotency"),
    contact_v2(application_key_version="8"), contact_v2(application_key_version=True),
    contact_v2(application_key_version=8.0), contact_v2(transit_key_version="2"),
    contact_v2(transit_key_version=True), contact_v2(transit_key_version=3),
    contact_v2(kms_key_id=None), contact_v2(key_version=None),
])
@pytest.mark.parametrize("historical", [False, True])
def test_unknown_or_mixed_contact_key_metadata_is_rejected(db, envelope, historical):
    add_binding(db, CONTACT, 7, provider="aliyun_kms")
    add_binding(db, CONTACT, 8)
    add_contact(db, envelope, historical=historical)
    assert_closed(db)


@pytest.mark.parametrize("provider", ["aliyun_kms", "openbao_transit_v1"])
@pytest.mark.parametrize("purpose", [AUTH, CONTACT])
@pytest.mark.parametrize("kind", ["missing_claim", "missing_pin", "hash", "time", "wrong_provider", "both_pins"])
def test_referenced_claim_and_pin_must_be_unique_and_consistent(db, provider, purpose, kind):
    version = 8
    add_binding(db, purpose, version, provider=provider)
    if purpose == AUTH:
        add_auth(db, version)
    else:
        add_contact(db, contact_v1(version) if provider == "aliyun_kms" else contact_v2(version), historical=True)
    claims = ApplicationKeyVersionClaim.__table__
    pins = KmsDataKeyPin.__table__ if provider == "aliyun_kms" else OpenBaoDataKeyPin.__table__
    predicate = (claims.c.purpose == purpose) & (claims.c.application_key_version == version)
    if kind == "missing_claim":
        db.execute(claims.delete().where(predicate))
    elif kind == "missing_pin":
        db.execute(pins.delete())
    elif kind == "hash":
        db.execute(claims.update().where(predicate).values(ciphertext_sha256="0" * 64))
    elif kind == "time":
        db.execute(claims.update().where(predicate).values(created_at=NOW))
    elif kind == "wrong_provider":
        db.execute(claims.update().where(predicate).values(
            provider="openbao_transit_v1" if provider == "aliyun_kms" else "aliyun_kms"))
    else:
        common = {"purpose": purpose, "application_key_version": version,
                  "ciphertext_sha256": "e" * 64, "created_at": NOW - timedelta(days=1)}
        if provider == "aliyun_kms":
            db.execute(OpenBaoDataKeyPin.__table__.insert(), common | {
                "environment": "test", "provider_instance_id": INSTANCE,
                "key_path": "transit/keys/rsc-" + purpose.replace("_", "-"),
                "transit_key_version": 2, "context_sha256": "c" * 64,
                "associated_data_sha256": "d" * 64,
            })
        else:
            db.execute(KmsDataKeyPin.__table__.insert(), common | {
                "kms_key_id": "kms/other", "kms_key_version_id": "other-version-8",
            })
    assert_closed(db)


@pytest.mark.parametrize("kwargs", [
    {"environment": "production"}, {"openbao_provider_instance_id": None},
    {"openbao_provider_instance_id": "different-instance"},
])
def test_database_openbao_metadata_cannot_choose_its_own_deployment_identity(db, kwargs):
    add_binding(db)
    add_auth(db)
    assert_closed(db, **kwargs)


def test_aliyun_history_does_not_require_an_openbao_instance(db):
    add_binding(db, AUTH, 7, provider="aliyun_kms")
    add_auth(db, 7)
    assert len(scan(db, openbao_provider_instance_id=None).aliyun) == 1


@pytest.mark.parametrize("kwargs", [
    {"environment": "test' OR 1=1 --"}, {"environment": True},
    {"openbao_provider_instance_id": "../different"},
    {"now": NOW.replace(tzinfo=None)}, {"now": "2026-10-09"},
])
def test_bad_scanner_inputs_fail_before_query(db, kwargs):
    statements = []
    def capture(*args):
        statements.append(True)
    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        assert_closed(db, **kwargs)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert statements == []


def test_scanning_projects_only_metadata_without_autoflush_or_transaction_control(db):
    add_binding(db, AUTH, 8)
    add_binding(db, CONTACT, 8)
    add_auth(db)
    add_contact(db, contact_v2(), historical=True)
    pending = KmsDataKeyPin(purpose="invalid-pending-must-never-flush")
    db.add(pending)
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))
    def forbidden(*args):
        pytest.fail("reference scanning must not flush, commit, or rollback")
    event.listen(db.get_bind(), "before_cursor_execute", capture)
    for name in ("before_flush", "before_commit", "after_rollback"):
        event.listen(db, name, forbidden)
    try:
        catalog = scan(db)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
        for name in ("before_flush", "before_commit", "after_rollback"):
            event.remove(db, name, forbidden)
        db.expunge(pending)
    assert len(catalog.openbao) == 2
    assert statements
    for statement, parameters in statements:
        assert statement.lstrip().upper().startswith("SELECT")
        assert "FOR UPDATE" not in statement.upper()
        assert "response_ciphertext" not in statement
        assert "ciphertext_blob" not in statement
        for sensitive in ("ciphertext_b64", "nonce_b64", "mobile_hmac", "contact_hmac", "aad_sha256"):
            assert sensitive not in statement and sensitive not in repr(parameters)
        assert "synthetic-do-not-project" not in repr(parameters)
        assert "test' OR" not in statement
    contact_sql = [sql for sql, _ in statements if "material_request" in sql and "json" in sql.lower()]
    assert contact_sql
    assert all("json_type" in sql.lower() for sql in contact_sql)
    # Coordinates and the exact cutoff travel as parameters, not SQL literals.
    assert any(AUTH in repr(params) for _, params in statements)
    assert any("2026-10-09 12:00:00" in repr(params) for _, params in statements)


def test_database_failure_has_no_sensitive_cause_or_context(db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic-dsn-secret-marker")
    monkeypatch.setattr(db, "execute", broken)
    failure = assert_closed(db)
    assert "synthetic-dsn" not in str(failure)


@pytest.mark.parametrize("source", ["auth", "current", "history"])
@pytest.mark.parametrize("count", [128, 129])
def test_distinct_reference_bounds_reject_overflow_without_silent_truncation(db, source, count):
    purpose = AUTH if source == "auth" else CONTACT
    for version in range(1, count + 1):
        add_binding(db, purpose, version, provider="aliyun_kms")
        if source == "auth":
            add_auth(db, version)
        else:
            add_contact(db, contact_v1(version), historical=source == "history")
    if count == 129:
        assert_closed(db)
    else:
        result = scan(db)
        assert len(result.aliyun) == 128
        assert {ref.application_key_version for ref in result.aliyun} == set(range(1, 129))


def test_many_duplicate_business_rows_do_not_exhaust_distinct_reference_limit(db):
    add_binding(db, CONTACT, 8)
    for _ in range(130):
        add_contact(db, contact_v2(), historical=True)
    assert len(scan(db).openbao) == 1


@pytest.mark.parametrize("model", [MaterialRequest, MaterialRequestRevision])
def test_pg_projection_preserves_json_types_and_never_selects_sensitive_envelope(model):
    compiled = _contact_projection(model, "postgresql").compile(dialect=postgresql.dialect())
    statement = str(compiled)
    assert "jsonb_typeof" in statement
    assert "->>" in statement and " -> " in statement
    assert " AS INTEGER" not in statement.upper()
    assert " AS BOOLEAN" not in statement.upper()
    assert "ciphertext_b64" not in repr(compiled.params)
    assert "nonce_b64" not in repr(compiled.params)
    assert "mobile_hmac" not in repr(compiled.params)
    assert "contact_hmac" not in repr(compiled.params)
    assert "aad_sha256" not in repr(compiled.params)
    assert "application_key_version" in compiled.params.values()
    assert "transit_key_version" in compiled.params.values()
    # Every selected field is an extraction/type expression, never full JSONB.
    assert all(column is not model.__table__.c.contact_snapshot_jsonb
               for column in _contact_projection(model, "postgresql").selected_columns)


@pytest.mark.parametrize("provider,model", [
    ("aliyun_kms", ApplicationKeyVersionClaim),
    ("openbao_transit_v1", ApplicationKeyVersionClaim),
    ("aliyun_kms", KmsDataKeyPin),
    ("openbao_transit_v1", OpenBaoDataKeyPin),
])
def test_ambiguous_claim_or_pin_rows_fail_closed_even_without_db_uniqueness(db, provider, model):
    add_binding(db, AUTH, 8, provider=provider)
    add_auth(db)
    # Deliberately remove one constraint only in this disposable synthetic DB.
    # Production migrations forbid such drift; scanner must still reject it.
    name = model.__tablename__
    connection = db.connection()
    connection.exec_driver_sql(f"CREATE TABLE duplicate_snapshot AS SELECT * FROM {name}")
    connection.exec_driver_sql(f"DROP TABLE {name}")
    connection.exec_driver_sql(f"ALTER TABLE duplicate_snapshot RENAME TO {name}")
    connection.exec_driver_sql(f"INSERT INTO {name} SELECT * FROM {name}")
    assert_closed(db)


def test_combined_auth_and_contact_identity_limit_is_enforced(db):
    for version in range(1, 66):
        add_binding(db, AUTH, version, provider="aliyun_kms")
        add_auth(db, version)
        add_binding(db, CONTACT, version, provider="aliyun_kms")
        add_contact(db, contact_v1(version))
    assert_closed(db)


def test_current_and_historical_contact_cannot_disagree_on_same_application_version(db):
    add_binding(db, CONTACT, 8, provider="aliyun_kms")
    add_contact(db, contact_v1(8))
    add_contact(db, contact_v2(8), historical=True)
    assert_closed(db)
