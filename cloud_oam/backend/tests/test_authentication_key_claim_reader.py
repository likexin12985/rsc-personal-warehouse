"""Independent DB-reader contracts on synthetic SQLite, not PG guard evidence."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.foundation_models import KmsDataKeyPin
from app.key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
from app.formal_services.authentication_idempotency import AuthenticationEncryptionKeyUnavailable
from app.formal_services.authentication_key_claim_reader import DatabaseAuthenticationClaimReader
from app.formal_services.authentication_key_claims import (
    AUTHENTICATION_PURPOSE, AliyunAuthenticationKeyBinding, OpenBaoAuthenticationKeyBinding,
)


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)
INSTANCE = "auth-db-reader-test"


def ali(version=7):
    return dict(purpose=AUTHENTICATION_PURPOSE, application_key_version=version,
        kms_key_id="kms/auth/history", kms_key_version_id="version-legacy-7",
        ciphertext_sha256="a" * 64, created_at=NOW)


def bao(version=8):
    return dict(purpose=AUTHENTICATION_PURPOSE, application_key_version=version,
        environment="test", provider_instance_id=INSTANCE,
        key_path="transit/keys/rsc-authentication-idempotency", transit_key_version=2,
        ciphertext_sha256="b" * 64, context_sha256="c" * 64,
        associated_data_sha256="d" * 64, created_at=NOW)


def claim(version=8):
    return dict(purpose=AUTHENTICATION_PURPOSE, application_key_version=version,
        provider="aliyun_kms" if version == 7 else "openbao_transit_v1",
        ciphertext_sha256=("a" if version == 7 else "b") * 64, created_at=NOW)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (KmsDataKeyPin, OpenBaoDataKeyPin, ApplicationKeyVersionClaim):
        model.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(KmsDataKeyPin.__table__.insert(), ali())
        connection.execute(OpenBaoDataKeyPin.__table__.insert(), bao())
        connection.execute(ApplicationKeyVersionClaim.__table__.insert(), [claim(7), claim(8)])
    with Session(engine) as session:
        yield session
    engine.dispose()


def reader(db, **kwargs):
    return DatabaseAuthenticationClaimReader(db, **dict(
        environment="test", openbao_provider_instance_id=INSTANCE,
    ) | kwargs)


def test_exact_independent_bindings_are_detached_without_reading_registry(db):
    read = reader(db)
    old, current = read(AUTHENTICATION_PURPOSE, 7), read(AUTHENTICATION_PURPOSE, 8)
    assert type(old) is AliyunAuthenticationKeyBinding
    assert (old.application_key_version, old.kms_key_id, old.kms_key_version_id, old.ciphertext_sha256) == (
        7, "kms/auth/history", "version-legacy-7", "a" * 64,
    )
    assert type(current) is OpenBaoAuthenticationKeyBinding
    pin = current.reviewed_pin
    assert pin.coordinate.application_key_version == 8 and pin.transit_key_version == 2
    assert pin.coordinate.environment == "test" and pin.coordinate.provider_instance_id == INSTANCE
    assert (pin.ciphertext_sha256, pin.context_sha256, pin.associated_data_sha256) == ("b" * 64, "c" * 64, "d" * 64)
    assert db.identity_map.keys() == set()


def test_reader_is_one_parameter_bound_select_without_autoflush_commit_or_rollback(db):
    calls = []
    pending = KmsDataKeyPin(purpose="never-flush-invalid-pending-object")
    db.add(pending)

    def capture(connection, cursor, statement, parameters, context, executemany):
        calls.append((statement, parameters))

    def forbidden(*args, **kwargs):
        pytest.fail("reader must not own transaction boundaries")

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    event.listen(db, "before_commit", forbidden)
    event.listen(db, "after_rollback", forbidden)
    try:
        value = reader(db)(AUTHENTICATION_PURPOSE, 8)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
        event.remove(db, "before_commit", forbidden)
        event.remove(db, "after_rollback", forbidden)
        db.expunge(pending)
    assert value.application_key_version == 8
    assert len(calls) == 1 and calls[0][0].lstrip().startswith("SELECT")
    statement, parameters = calls[0]
    assert AUTHENTICATION_PURPOSE not in statement and AUTHENTICATION_PURPOSE in parameters
    assert "response_ciphertext" not in statement and "ciphertext_blob" not in statement
    assert "FOR UPDATE" not in statement


@pytest.mark.parametrize("kind", ["missing", "orphan", "hash", "time", "wrong_provider", "both_pins"])
@pytest.mark.parametrize("version", [7, 8])
def test_inconsistent_claim_pin_facts_fail_closed(db, kind, version):
    # Deliberate synthetic SQLite drift: production C1 ALWAYS guards prohibit
    # these writes. The reader must still never substitute registry facts.
    claims = ApplicationKeyVersionClaim.__table__
    pin = KmsDataKeyPin.__table__ if version == 7 else OpenBaoDataKeyPin.__table__
    if kind == "missing":
        db.execute(claims.delete().where(claims.c.application_key_version == version))
    elif kind == "orphan":
        db.execute(pin.delete().where(pin.c.application_key_version == version))
    elif kind == "hash":
        db.execute(claims.update().where(claims.c.application_key_version == version).values(ciphertext_sha256="0" * 64))
    elif kind == "time":
        db.execute(claims.update().where(claims.c.application_key_version == version).values(created_at=NOW + timedelta(seconds=1)))
    elif kind == "wrong_provider":
        db.execute(claims.update().where(claims.c.application_key_version == version).values(
            provider="openbao_transit_v1" if version == 7 else "aliyun_kms"))
    elif version == 7:
        db.execute(OpenBaoDataKeyPin.__table__.insert(), dict(bao(7), ciphertext_sha256="e" * 64))
    else:
        db.execute(KmsDataKeyPin.__table__.insert(), dict(ali(8), ciphertext_sha256="e" * 64))
    with pytest.raises(AuthenticationEncryptionKeyUnavailable) as failure:
        reader(db)(AUTHENTICATION_PURPOSE, version)
    assert failure.value.__cause__ is None and failure.value.__context__ is None


@pytest.mark.parametrize("kwargs", [
    {"environment": "production"}, {"openbao_provider_instance_id": "other-instance"},
    {"openbao_provider_instance_id": None},
])
def test_openbao_deployment_binding_is_independent_of_database_values(db, kwargs):
    read = reader(db, **kwargs)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable):
        read(AUTHENTICATION_PURPOSE, 8)
    assert type(read(AUTHENTICATION_PURPOSE, 7)) is AliyunAuthenticationKeyBinding


@pytest.mark.parametrize("purpose,version", [
    ("material_request_contact", 8), ("authentication_idempotency' OR 1=1 --", 8),
    (AUTHENTICATION_PURPOSE, True), (AUTHENTICATION_PURPOSE, "8"),
    (AUTHENTICATION_PURPOSE, 0), (AUTHENTICATION_PURPOSE, 2_147_483_648),
])
def test_invalid_query_coordinate_never_executes_sql(db, purpose, version):
    calls = []
    def capture(*args):
        calls.append(True)
    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        with pytest.raises(AuthenticationEncryptionKeyUnavailable):
            reader(db)(purpose, version)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert calls == []


def test_database_error_does_not_preserve_statement_or_connection_exception(db, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic-dsn-secret-marker")
    monkeypatch.setattr(db, "execute", unavailable)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable) as failure:
        reader(db)(AUTHENTICATION_PURPOSE, 8)
    assert failure.value.__cause__ is None and failure.value.__context__ is None
    assert "synthetic-dsn" not in str(failure.value)
