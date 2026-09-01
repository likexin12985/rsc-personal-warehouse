from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_services.authentication_idempotency import (
    AuthenticationCipherConfigurationError,
    AuthenticationIdempotencyError,
    KmsAuthenticationKeyProvider,
    StaticAuthenticationKeyProvider,
    begin_authentication_operation,
    complete_authentication_failure,
    complete_authentication_operation,
    complete_authentication_success,
    create_authentication_response_cipher,
    create_configured_authentication_response_cipher,
)
from app.foundation_models import AuthIdempotencyOperation
from app.models import AuthSession, User


HMAC_SECRET = b"formal-auth-idempotency-test-hmac-secret-2026"
AES_KEY = bytes(range(32))
RAW_KEY = "idem-auth-network-retry-00000001"
MOBILE = "13800000123"
CODE = "246810"
DEVICE_ID = "device-raw-identifier-0001"
IP_ADDRESS = "198.51.100.44"
OLD_REFRESH_TOKEN = "old-refresh-token-raw-value-that-must-never-persist"
NEW_REFRESH_TOKEN = "new-refresh-token-raw-value-that-is-encrypted-only"
ACCESS_TOKEN = "access-token-raw-value-that-is-encrypted-only"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def cipher():
    return create_authentication_response_cipher(
        environment="test",
        key_provider=StaticAuthenticationKeyProvider(key=AES_KEY, version=7),
    )


def _begin(
    db: Session,
    cipher,
    *,
    raw_key: str = RAW_KEY,
    operation_type: str = "session_refresh",
    client_type: str = "miniprogram",
    scope: dict | None = None,
    request: dict | None = None,
    now: datetime | None = None,
    expires_at: datetime | None = None,
):
    effective_now = now or datetime.now(timezone.utc)
    return begin_authentication_operation(
        db,
        operation_type=operation_type,
        client_type=client_type,
        idempotency_key=raw_key,
        scope=scope
        or {
            "device_id": DEVICE_ID,
            "mobile": MOBILE,
        },
        request=request
        or {
            "code": CODE,
            "ip": IP_ADDRESS,
            "refresh_token": OLD_REFRESH_TOKEN,
        },
        hmac_secret=HMAC_SECRET,
        cipher=cipher,
        now=effective_now,
        expires_at=expires_at or effective_now + timedelta(seconds=90),
    )


def _complete_success(db: Session, cipher, begin, *, now: datetime | None = None):
    return complete_authentication_success(
        db,
        begin=begin,
        payload={
            "access_token": ACCESS_TOKEN,
            "refresh_token": NEW_REFRESH_TOKEN,
            "token_type": "bearer",
        },
        http_status=200,
        cipher=cipher,
        now=now or datetime.now(timezone.utc),
    )


def _auth_session(db: Session) -> AuthSession:
    user = User(
        mobile=f"139{uuid.uuid4().int % 100_000_000:08d}",
        name="幂等登录测试人员",
        password_hash="disabled",
        role="technician",
        province="江苏省",
        is_active=True,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    now = datetime.now(timezone.utc)
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        client_type="miniprogram",
        device_id="hmac:device-reference",
        device_name="测试设备",
        ip_address="hmac:" + "a" * 64,
        user_agent="test",
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=1),
    )
    db.add(session)
    db.flush()
    return session


def test_begin_persists_only_hmacs_and_pending_evidence(db: Session, cipher):
    result = _begin(db, cipher)

    assert result.disposition == "execute"
    assert result.response is None
    row = result.operation
    assert row.status == "pending"
    assert row.idempotency_key_hash != RAW_KEY
    assert len(row.idempotency_key_hash) == 64
    assert len(row.scope_hash) == 64
    assert len(row.request_hmac) == 64
    assert row.response_ciphertext is None
    assert row.response_nonce is None
    assert row.http_status is None

    persisted = json.dumps(
        {
            "type": row.operation_type,
            "client": row.client_type,
            "key": row.idempotency_key_hash,
            "scope": row.scope_hash,
            "request": row.request_hmac,
        }
    )
    for raw_secret in (
        RAW_KEY,
        MOBILE,
        CODE,
        DEVICE_ID,
        IP_ADDRESS,
        OLD_REFRESH_TOKEN,
    ):
        assert raw_secret not in persisted


def test_success_is_encrypted_and_exactly_replayed_with_canonical_json(
    db: Session, cipher
):
    started = datetime.now(timezone.utc)
    begin = _begin(
        db,
        cipher,
        scope={"mobile": MOBILE, "device_id": DEVICE_ID},
        request={"refresh_token": OLD_REFRESH_TOKEN, "code": CODE, "ip": IP_ADDRESS},
        now=started,
    )
    first = _complete_success(db, cipher, begin, now=started + timedelta(seconds=1))
    db.commit()

    row = db.get(AuthIdempotencyOperation, first.operation_id)
    assert row is not None
    assert row.status == "completed"
    assert row.encryption_key_version == 7
    assert len(row.response_nonce) == 12
    assert len(row.response_ciphertext) > 16
    assert NEW_REFRESH_TOKEN.encode() not in row.response_ciphertext
    assert ACCESS_TOKEN.encode() not in row.response_ciphertext
    assert RAW_KEY not in repr(row.__dict__)

    replay = _begin(
        db,
        cipher,
        scope={"device_id": DEVICE_ID, "mobile": MOBILE},
        request={"code": CODE, "ip": IP_ADDRESS, "refresh_token": OLD_REFRESH_TOKEN},
        now=started + timedelta(seconds=2),
    )
    assert replay.disposition == "replay"
    assert replay.replayed is True
    assert replay.response is not None
    assert replay.response.replayed is True
    assert replay.response.http_status == 200
    assert replay.response.payload == first.payload
    assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 1


def test_controlled_failure_is_encrypted_and_replayed(db: Session, cipher):
    started = datetime.now(timezone.utc)
    begin = _begin(db, cipher, operation_type="sms_login", now=started)

    with pytest.raises(AuthenticationIdempotencyError) as invalid_output:
        complete_authentication_operation(
            db,
            begin=begin,
            payload={"detail": "登录失败"},
            http_status=401,
            outcome="controlled_failure",
            cipher=cipher,
            output_refresh_token_id=uuid.uuid4(),
        )
    assert invalid_output.value.code == (
        "authentication_idempotency_failed_output_token_forbidden"
    )

    first = complete_authentication_failure(
        db,
        begin=begin,
        payload={"detail": "登录失败"},
        http_status=401,
        cipher=cipher,
        now=started + timedelta(seconds=1),
    )
    db.commit()
    assert first.status == "failed"

    replay = _begin(
        db,
        cipher,
        operation_type="sms_login",
        now=started + timedelta(seconds=2),
    )
    assert replay.response is not None
    assert replay.response.status == "failed"
    assert replay.response.http_status == 401
    assert replay.response.payload == {"detail": "登录失败"}


@pytest.mark.parametrize("operation_type", ["sms_login", "wechat_login"])
def test_login_replay_allows_output_session_reference_populated_at_completion(
    db: Session, cipher, operation_type
):
    started = datetime.now(timezone.utc)
    session = _auth_session(db)
    begin = _begin(db, cipher, operation_type=operation_type, now=started)
    first = complete_authentication_success(
        db,
        begin=begin,
        payload={
            "access_token": ACCESS_TOKEN,
            "refresh_token": NEW_REFRESH_TOKEN,
        },
        http_status=200,
        cipher=cipher,
        auth_session_id=session.id,
        now=started + timedelta(seconds=1),
    )
    db.commit()
    row = db.get(AuthIdempotencyOperation, first.operation_id)
    assert row.auth_session_id == session.id

    # The retry has not received the first response and therefore cannot know
    # the output session id.  Exact key/request evidence still replays it.
    replay = _begin(
        db,
        cipher,
        operation_type=operation_type,
        now=started + timedelta(seconds=2),
    )
    assert replay.response is not None
    assert replay.response.replayed is True
    assert replay.response.payload["refresh_token"] == NEW_REFRESH_TOKEN


def test_pending_same_request_conflicts_without_second_execution(db: Session, cipher):
    _begin(db, cipher)

    with pytest.raises(AuthenticationIdempotencyError) as caught:
        _begin(db, cipher)
    assert caught.value.code == "authentication_idempotency_pending"
    assert caught.value.category == "conflict"
    assert caught.value.http_status_code == 409
    assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 1


@pytest.mark.parametrize(
    "override",
    [
        {"operation_type": "session_logout"},
        {"client_type": "web"},
        {"scope": {"device_id": "different-device"}},
        {"request": {"refresh_token": "different-token"}},
    ],
)
def test_global_key_reuse_for_a_different_request_conflicts(
    db: Session, cipher, override
):
    started = datetime.now(timezone.utc)
    begin = _begin(db, cipher, now=started)
    _complete_success(db, cipher, begin, now=started + timedelta(seconds=1))
    db.commit()

    with pytest.raises(AuthenticationIdempotencyError) as caught:
        _begin(db, cipher, now=started + timedelta(seconds=2), **override)
    assert caught.value.code == "authentication_idempotency_key_conflict"
    assert caught.value.category == "conflict"


def test_expired_result_fails_closed_and_key_is_never_recycled(db: Session, cipher):
    started = datetime.now(timezone.utc)
    begin = _begin(
        db,
        cipher,
        now=started,
        expires_at=started + timedelta(seconds=30),
    )
    _complete_success(db, cipher, begin, now=started + timedelta(seconds=1))
    db.commit()

    with pytest.raises(AuthenticationIdempotencyError) as caught:
        _begin(db, cipher, now=started + timedelta(seconds=31))
    assert caught.value.code == "authentication_idempotency_expired"
    assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 1


def test_ciphertext_and_aad_tampering_fail_closed(db: Session, cipher):
    started = datetime.now(timezone.utc)
    begin = _begin(db, cipher, now=started)
    completed = _complete_success(
        db, cipher, begin, now=started + timedelta(seconds=1)
    )
    db.commit()

    row = db.get(AuthIdempotencyOperation, completed.operation_id)
    row.expires_at = row.expires_at + timedelta(seconds=1)
    db.commit()

    with pytest.raises(AuthenticationIdempotencyError) as caught:
        _begin(db, cipher, now=started + timedelta(seconds=2))
    assert caught.value.code == "authentication_idempotency_response_unavailable"
    assert caught.value.category == "service_unavailable"


def test_unknown_encryption_key_version_fails_closed(db: Session, cipher):
    started = datetime.now(timezone.utc)
    begin = _begin(db, cipher, now=started)
    completed = _complete_success(
        db, cipher, begin, now=started + timedelta(seconds=1)
    )
    db.commit()

    row = db.get(AuthIdempotencyOperation, completed.operation_id)
    row.encryption_key_version = 8
    db.commit()

    with pytest.raises(AuthenticationIdempotencyError) as caught:
        _begin(db, cipher, now=started + timedelta(seconds=2))
    assert caught.value.code == "authentication_idempotency_response_unavailable"


def test_noncanonical_or_oversized_documents_are_rejected(db: Session, cipher):
    with pytest.raises(AuthenticationIdempotencyError) as noncanonical:
        _begin(db, cipher, request={"quantity": float("nan")})
    assert noncanonical.value.code == "authentication_idempotency_document_invalid"

    with pytest.raises(AuthenticationIdempotencyError) as oversized:
        _begin(db, cipher, request={"payload": "x" * (64 * 1024)})
    assert oversized.value.code == "authentication_idempotency_document_too_large"

    with pytest.raises(AuthenticationIdempotencyError) as non_string_key:
        _begin(db, cipher, request={1: "ambiguous"})
    assert non_string_key.value.code == "authentication_idempotency_document_invalid"
    assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 0


@pytest.mark.parametrize("ttl_seconds", [29, 121])
def test_replay_ttl_is_limited_to_thirty_through_one_hundred_twenty_seconds(
    db: Session, cipher, ttl_seconds
):
    now = datetime.now(timezone.utc)
    with pytest.raises(AuthenticationIdempotencyError) as caught:
        _begin(
            db,
            cipher,
            now=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    assert caught.value.code == "authentication_idempotency_expiry_invalid"


def test_invalid_success_or_failure_http_status_is_rejected(db: Session, cipher):
    begin = _begin(db, cipher)
    with pytest.raises(AuthenticationIdempotencyError) as redirected_success:
        complete_authentication_success(
            db,
            begin=begin,
            payload={},
            http_status=302,
            cipher=cipher,
        )
    assert redirected_success.value.code == (
        "authentication_idempotency_http_status_invalid"
    )

    with pytest.raises(AuthenticationIdempotencyError) as successful_failure:
        complete_authentication_failure(
            db,
            begin=begin,
            payload={},
            http_status=200,
            cipher=cipher,
        )
    assert successful_failure.value.code == (
        "authentication_idempotency_http_status_invalid"
    )


def test_caller_rollback_owns_pending_and_completed_writes(db: Session, cipher):
    begin = _begin(db, cipher)
    operation_id = begin.operation.id
    _complete_success(db, cipher, begin)
    assert db.get(AuthIdempotencyOperation, operation_id) is not None

    db.rollback()
    assert db.get(AuthIdempotencyOperation, operation_id) is None


def test_production_factory_rejects_static_or_missing_kms_provider():
    static = StaticAuthenticationKeyProvider(key=AES_KEY)
    with pytest.raises(AuthenticationCipherConfigurationError) as caught:
        create_authentication_response_cipher(
            environment="production",
            key_provider=static,
        )
    assert caught.value.code == "authentication_kms_required"

    settings = SimpleNamespace(
        environment="production",
        auth_idempotency_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id="kms-formal-auth-test",
        auth_idempotency_encryption_key_version=3,
    )
    with pytest.raises(AuthenticationCipherConfigurationError) as missing_loader:
        create_configured_authentication_response_cipher(
            settings=settings,
            kms_key_loader=None,
        )
    assert missing_loader.value.code == "authentication_kms_required"


def test_configured_kms_adapter_resolves_exact_version_without_env_key():
    calls: list[tuple[str, int]] = []

    def loader(key_id: str, version: int) -> bytes:
        calls.append((key_id, version))
        return AES_KEY

    settings = SimpleNamespace(
        environment="production",
        auth_idempotency_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id="kms-formal-auth-test",
        auth_idempotency_encryption_key_version=3,
    )
    cipher = create_configured_authentication_response_cipher(
        settings=settings,
        kms_key_loader=loader,
    )
    assert cipher.active_key_version() == 3
    envelope = cipher.encrypt(b"secret", aad=b"bound", key_version=3)
    assert len(envelope.nonce) == 12
    assert cipher.decrypt(
        envelope.ciphertext,
        nonce=envelope.nonce,
        aad=b"bound",
        key_version=3,
    ) == b"secret"
    assert calls == [("kms-formal-auth-test", 3)]
    assert isinstance(
        KmsAuthenticationKeyProvider(
            kms_key_id="kms-formal-auth-test",
            active_version=3,
            key_loader=loader,
        ),
        KmsAuthenticationKeyProvider,
    )


def test_cipher_reuses_active_key_when_loader_fails_after_first_resolution():
    calls: list[tuple[str, int]] = []

    def loader(key_id: str, version: int) -> bytes:
        calls.append((key_id, version))
        if len(calls) > 1:
            raise RuntimeError("simulated KMS outage after request preflight")
        return AES_KEY

    settings = SimpleNamespace(
        environment="production",
        auth_idempotency_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id="kms-formal-auth-test",
        auth_idempotency_encryption_key_version=3,
    )
    cipher = create_configured_authentication_response_cipher(
        settings=settings,
        kms_key_loader=loader,
    )

    assert cipher.active_key_version() == 3
    assert cipher.active_key_version() == 3
    envelope = cipher.encrypt(b"request response", aad=b"operation", key_version=3)
    assert cipher.decrypt(
        envelope.ciphertext,
        nonce=envelope.nonce,
        aad=b"operation",
        key_version=3,
    ) == b"request response"
    assert calls == [("kms-formal-auth-test", 3)]


def test_cipher_loads_each_historical_key_version_once_and_caches_it():
    calls: list[tuple[str, int]] = []
    keys = {
        2: bytes(reversed(range(32))),
        3: AES_KEY,
    }

    def loader(key_id: str, version: int) -> bytes:
        calls.append((key_id, version))
        return keys[version]

    settings = SimpleNamespace(
        environment="production",
        auth_idempotency_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id="kms-formal-auth-test",
        auth_idempotency_encryption_key_version=3,
    )
    cipher = create_configured_authentication_response_cipher(
        settings=settings,
        kms_key_loader=loader,
    )

    assert cipher.active_key_version() == 3
    active_envelope = cipher.encrypt(b"active", aad=b"v3", key_version=3)
    assert cipher.decrypt(
        active_envelope.ciphertext,
        nonce=active_envelope.nonce,
        aad=b"v3",
        key_version=3,
    ) == b"active"

    historical_envelope = cipher.encrypt(b"historical", aad=b"v2", key_version=2)
    assert cipher.decrypt(
        historical_envelope.ciphertext,
        nonce=historical_envelope.nonce,
        aad=b"v2",
        key_version=2,
    ) == b"historical"
    assert calls == [
        ("kms-formal-auth-test", 3),
        ("kms-formal-auth-test", 2),
    ]
