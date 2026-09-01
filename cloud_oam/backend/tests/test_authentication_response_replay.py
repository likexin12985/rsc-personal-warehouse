from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app import auth_sessions
from app.auth_sessions import SessionTokens, create_session, rotate_session
from app.config import get_settings
from app.database import Base
from app.formal_access import load_formal_principal
from app.formal_services.authentication_response_replay import (
    AuthenticationResponseReplayError,
    error_response_payload,
    logout_response_payload,
    restore_error_response,
    restore_logout_response,
    restore_session_response,
    session_response_payload,
)
from app.foundation_models import (
    AuthIdentity,
    AuthIdempotencyOperation,
    AuthRefreshToken,
    Organization,
    Person,
    Role,
    RoleAssignment,
)
from app.models import AuthSession, User
from app.schemas import FormalSelfOut
from app.security import hash_refresh_token


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


@dataclass(frozen=True)
class SessionReplayWorld:
    user: User
    assignment: RoleAssignment
    tokens: SessionTokens
    output_token: AuthRefreshToken
    operation: AuthIdempotencyOperation
    payload: dict[str, object]
    restored_user: FormalSelfOut
    input_token: AuthRefreshToken | None = None


def _formal_user(db: Session, user: User, *, now: datetime) -> FormalSelfOut:
    principal = load_formal_principal(db, user.id, now=now)
    person = db.get(Person, principal.person_id)
    assert person is not None
    organization = db.get(Organization, person.organization_id)
    assert organization is not None
    return FormalSelfOut(
        person_id=person.id,
        name=person.name,
        employee_no=person.employee_no,
        organization_code=organization.code,
        organization_name=organization.name,
        account_status=principal.account_status,
        employment_status=principal.employment_status,
        access_mode=principal.access_mode,
        authorization_version=principal.authorization_version,
        role_codes=list(principal.role_codes),
    )


def _seed_subject(db: Session) -> tuple[User, RoleAssignment]:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        code=f"ORG-REPLAY-{suffix}",
        name="认证响应回放测试区域",
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    db.add(organization)
    db.flush()
    person = Person(
        organization_id=organization.id,
        employee_no=f"EMP-REPLAY-{suffix}",
        name="认证回放测试工程师",
        employment_status="active",
    )
    role = Role(
        code="technician",
        name="工程师",
        is_external=False,
        status="active",
    )
    db.add_all([person, role])
    db.flush()
    user = User(
        person_id=person.id,
        account_status="active",
        authorization_version=1,
        mobile=f"13{uuid.uuid4().int % 1_000_000_000:09d}",
        name=person.name,
        password_hash="formal-password-login-disabled",
        role="technician",
        province="legacy-value-must-not-authorize",
        is_active=True,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    now = datetime.now(timezone.utc)
    identity = AuthIdentity(
        user_id=user.id,
        identity_type="mobile",
        provider_key="test",
        identifier_hash=hashlib.sha256(f"identity:{user.id}".encode()).hexdigest(),
        hash_version=1,
        verified_at=now - timedelta(minutes=1),
        status="active",
    )
    assignment = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        scope_type="person",
        scope_id=str(person.id),
        valid_from=now - timedelta(days=1),
        valid_to=None,
        status="active",
        assigned_by=user.id,
        reason="authentication response replay test",
    )
    db.add_all([identity, assignment])
    db.flush()
    return user, assignment


def _terminal_operation(
    db: Session,
    *,
    operation_type: str,
    client_type: str,
    status: str,
    http_status: int,
    created_at: datetime,
    completed_at: datetime,
    expires_at: datetime,
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | None = None,
    output_refresh_token_id: uuid.UUID | None = None,
) -> AuthIdempotencyOperation:
    nonce_seed = uuid.uuid4().hex
    operation = AuthIdempotencyOperation(
        operation_type=operation_type,
        client_type=client_type,
        idempotency_key_hash=hashlib.sha256(
            f"key:{nonce_seed}".encode()
        ).hexdigest(),
        scope_hash=hashlib.sha256(f"scope:{nonce_seed}".encode()).hexdigest(),
        request_hmac=hashlib.sha256(
            f"request:{nonce_seed}".encode()
        ).hexdigest(),
        status=status,
        response_ciphertext=b"ciphertext-with-tag",
        response_nonce=b"0123456789ab",
        response_sha256="a" * 64,
        encryption_key_version=1,
        http_status=http_status,
        auth_session_id=auth_session_id,
        input_refresh_token_id=input_refresh_token_id,
        output_refresh_token_id=output_refresh_token_id,
        created_at=created_at,
        completed_at=completed_at,
        expires_at=expires_at,
    )
    db.add(operation)
    db.flush()
    return operation


def _session_world(
    db: Session,
    *,
    operation_type: str = "sms_login",
) -> SessionReplayWorld:
    user, assignment = _seed_subject(db)
    input_token: AuthRefreshToken | None = None
    started_at = datetime.now(timezone.utc)
    initial_tokens = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id=f"device-{uuid.uuid4().hex}",
        device_name="回放测试设备",
    )
    tokens = initial_tokens
    if operation_type == "session_refresh":
        input_token = db.scalar(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash
                == hash_refresh_token(initial_tokens.refresh_token)
            )
        )
        assert input_token is not None
        _, tokens = rotate_session(
            db,
            initial_tokens.refresh_token,
            device_id=initial_tokens.auth_session.device_id,
        )
    output_token = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(tokens.refresh_token)
        )
    )
    assert output_token is not None
    completed_at = datetime.now(timezone.utc)
    restored_user = _formal_user(db, user, now=completed_at)
    operation = _terminal_operation(
        db,
        operation_type=operation_type,
        client_type="miniprogram",
        status="completed",
        http_status=200,
        created_at=started_at,
        completed_at=completed_at,
        expires_at=completed_at + timedelta(seconds=90),
        auth_session_id=tokens.auth_session.id,
        input_refresh_token_id=input_token.id if input_token else None,
        output_refresh_token_id=output_token.id,
    )
    payload = session_response_payload(user=restored_user, tokens=tokens)
    db.flush()
    return SessionReplayWorld(
        user=user,
        assignment=assignment,
        tokens=tokens,
        output_token=output_token,
        operation=operation,
        payload=payload,
        restored_user=restored_user,
        input_token=input_token,
    )


def _assert_replay_error(
    expected_code: str,
    expected_http_status: int,
    call,
) -> AuthenticationResponseReplayError:
    with pytest.raises(AuthenticationResponseReplayError) as captured:
        call()
    assert captured.value.code == expected_code
    assert captured.value.http_status_code == expected_http_status
    assert set(captured.value.as_detail()) == {"code", "category", "message"}
    return captured.value


def test_login_session_response_is_restored_only_from_current_database_facts(
    db: Session,
):
    world = _session_world(db)

    restored = restore_session_response(
        db,
        operation=world.operation,
        payload=world.payload,
    )

    assert restored.user == world.restored_user


@pytest.mark.parametrize(
    "legacy_ip_evidence",
    ("198.51.100.99", f"hmac:2:{'a' * 64}"),
)
def test_production_replay_rejects_plaintext_or_stale_session_ip_evidence(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    legacy_ip_evidence: str,
):
    world = _session_world(db)
    world.tokens.auth_session.ip_address = legacy_ip_evidence
    db.flush()
    runtime_settings = get_settings()
    monkeypatch.setattr(runtime_settings, "environment", "production")
    monkeypatch.setattr(
        runtime_settings,
        "identity_hash_secret",
        "response-replay-ip-secret-at-least-32-characters",
    )
    monkeypatch.setattr(runtime_settings, "identity_hash_version", 1)

    error = _assert_replay_error(
        "cached_session_ip_evidence_invalid",
        401,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )

    assert error.category == "unauthorized"
    assert world.tokens.auth_session.ip_address == legacy_ip_evidence
    assert world.tokens.auth_session.revoked_at is None


def test_production_replay_accepts_current_hmac_session_ip_evidence(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    world = _session_world(db)
    runtime_settings = get_settings()
    monkeypatch.setattr(runtime_settings, "environment", "production")
    monkeypatch.setattr(
        runtime_settings,
        "identity_hash_secret",
        "response-replay-ip-secret-at-least-32-characters",
    )
    monkeypatch.setattr(runtime_settings, "identity_hash_version", 7)
    world.tokens.auth_session.ip_address = auth_sessions.protect_session_ip_address(
        "203.0.113.77"
    )
    db.flush()

    restored = restore_session_response(
        db,
        operation=world.operation,
        payload=world.payload,
    )

    assert restored.tokens.auth_session.id == world.tokens.auth_session.id
    assert restored.tokens.auth_session.ip_address.startswith("hmac:7:")
    assert restored.tokens.auth_session.id == world.tokens.auth_session.id
    assert restored.tokens.access_token == world.tokens.access_token
    assert restored.tokens.refresh_token == world.tokens.refresh_token
    assert restored.token_body().model_dump(mode="json") == {
        "access_token": world.tokens.access_token,
        "token_type": "bearer",
        "expires_in": world.tokens.access_expires_in,
        "refresh_token": world.tokens.refresh_token,
        "refresh_expires_in": world.tokens.refresh_expires_in,
        "session_id": world.tokens.auth_session.id,
        "user": world.restored_user.model_dump(mode="json"),
    }


def test_refresh_session_response_requires_the_exact_consumed_replacement_chain(
    db: Session,
):
    world = _session_world(db, operation_type="session_refresh")
    assert world.input_token is not None
    assert world.input_token.consumed_at is not None
    assert world.input_token.replaced_by_id == world.output_token.id

    restored = restore_session_response(
        db,
        operation=world.operation,
        payload=world.payload,
    )

    assert restored.tokens.refresh_token == world.tokens.refresh_token


def test_tampered_session_envelope_shape_fails_closed(db: Session):
    world = _session_world(db)
    tampered = deepcopy(world.payload)
    tampered["plaintext_debug"] = "must never be accepted"

    _assert_replay_error(
        "cached_response_schema_invalid",
        503,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=tampered,
        ),
    )


def test_tampered_access_token_is_never_returned(db: Session):
    world = _session_world(db)
    tampered = deepcopy(world.payload)
    access_token = str(tampered["access_token"])
    header, claims, signature = access_token.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(signature[-1])
    # HS256 produces 32 signature bytes. Its canonical final Base64URL digit
    # has two zero pad bits; incrementing only those bits preserves the decoded
    # MAC bytes and exercises the non-canonical alias that PyJWT accepts.
    assert last_index % 4 == 0
    signature_alias = signature[:-1] + alphabet[last_index + 1]
    tampered["access_token"] = ".".join((header, claims, signature_alias))

    _assert_replay_error(
        "cached_access_token_invalid",
        401,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=tampered,
        ),
    )


def test_expired_session_operation_never_replays_credentials(db: Session):
    world = _session_world(db)
    world.operation.expires_at = world.operation.completed_at + timedelta(seconds=1)
    replay_at = world.operation.expires_at + timedelta(microseconds=1)
    db.flush()

    _assert_replay_error(
        "cached_operation_expired",
        503,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
            now=replay_at,
        ),
    )


def test_revoked_session_never_replays_credentials(db: Session):
    world = _session_world(db)
    world.tokens.auth_session.revoked_at = datetime.now(timezone.utc)
    db.flush()

    _assert_replay_error(
        "cached_session_inactive",
        401,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )


def test_refresh_projection_mismatch_is_evidence_failure(db: Session):
    world = _session_world(db)
    world.tokens.auth_session.refresh_token_hash = hashlib.sha256(
        b"different-current-refresh-token"
    ).hexdigest()
    db.flush()

    _assert_replay_error(
        "cached_refresh_projection_invalid",
        503,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )


def test_multiple_active_refresh_tokens_are_not_treated_as_replayable(db: Session):
    world = _session_world(db)
    db.add(
        AuthRefreshToken(
            session_id=world.tokens.auth_session.id,
            token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            issued_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    _assert_replay_error(
        "cached_active_refresh_set_invalid",
        503,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )


def test_refresh_chain_mismatch_is_evidence_failure(db: Session):
    world = _session_world(db, operation_type="session_refresh")
    issued_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    unrelated_consumed_token = AuthRefreshToken(
        session_id=world.tokens.auth_session.id,
        token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        issued_at=issued_at,
        consumed_at=issued_at + timedelta(milliseconds=1),
    )
    db.add(unrelated_consumed_token)
    db.flush()
    world.operation.input_refresh_token_id = unrelated_consumed_token.id
    db.flush()

    _assert_replay_error(
        "cached_refresh_chain_invalid",
        503,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )


def test_current_principal_version_change_invalidates_cached_user(db: Session):
    world = _session_world(db)
    world.user.authorization_version += 1
    db.flush()

    _assert_replay_error(
        "cached_authorization_changed",
        401,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )


def test_current_principal_revocation_invalidates_cached_user(db: Session):
    world = _session_world(db)
    world.assignment.status = "revoked"
    world.assignment.revoked_at = datetime.now(timezone.utc)
    world.assignment.revoked_by = world.user.id
    db.flush()

    _assert_replay_error(
        "cached_account_authorization_invalid",
        401,
        lambda: restore_session_response(
            db,
            operation=world.operation,
            payload=world.payload,
        ),
    )


def test_failed_response_restores_only_safe_detail_and_retry_metadata(db: Session):
    now = datetime.now(timezone.utc)
    operation = _terminal_operation(
        db,
        operation_type="sms_login",
        client_type="web",
        status="failed",
        http_status=429,
        created_at=now - timedelta(seconds=2),
        completed_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
    )
    payload = error_response_payload(
        detail={
            "code": "authentication.rate_limited",
            "category": "invalid_request",
            "message": "请求过于频繁，请稍后重试",
            "retry_after": 30,
        }
    )

    restored = restore_error_response(
        operation=operation,
        payload=payload,
        now=now,
    )

    assert restored.http_status == 429
    assert restored.retry_after == 30
    assert restored.detail == payload["detail"]


def test_failed_response_tampering_and_terminal_mismatch_fail_closed(db: Session):
    now = datetime.now(timezone.utc)
    operation = _terminal_operation(
        db,
        operation_type="session_refresh",
        client_type="miniprogram",
        status="failed",
        http_status=401,
        created_at=now - timedelta(seconds=2),
        completed_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
    )
    payload = error_response_payload(
        detail={
            "code": "authentication.failed",
            "category": "unauthorized",
            "message": "登录已失效",
            "retry_after": 10,
        },
        retry_after=10,
    )
    tampered = deepcopy(payload)
    tampered["retry_after"] = 11

    _assert_replay_error(
        "cached_retry_after_mismatch",
        503,
        lambda: restore_error_response(
            operation=operation,
            payload=tampered,
            now=now,
        ),
    )

    operation.status = "completed"
    operation.http_status = 200
    _assert_replay_error(
        "cached_operation_status_invalid",
        503,
        lambda: restore_error_response(
            operation=operation,
            payload=payload,
            now=now,
        ),
    )


def test_expired_failed_response_is_not_replayed(db: Session):
    now = datetime.now(timezone.utc)
    operation = _terminal_operation(
        db,
        operation_type="wechat_login",
        client_type="miniprogram",
        status="failed",
        http_status=502,
        created_at=now - timedelta(seconds=4),
        completed_at=now - timedelta(seconds=3),
        expires_at=now - timedelta(seconds=1),
    )

    _assert_replay_error(
        "cached_operation_expired",
        503,
        lambda: restore_error_response(
            operation=operation,
            payload=error_response_payload(detail="微信接口暂时不可用"),
            now=now,
        ),
    )


def test_wechat_response_cannot_be_replayed_as_a_web_client_operation(db: Session):
    now = datetime.now(timezone.utc)
    operation = _terminal_operation(
        db,
        operation_type="wechat_login",
        client_type="web",
        status="failed",
        http_status=401,
        created_at=now - timedelta(seconds=2),
        completed_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
    )

    _assert_replay_error(
        "cached_operation_client_invalid",
        503,
        lambda: restore_error_response(
            operation=operation,
            payload=error_response_payload(detail="登录失败"),
            now=now,
        ),
    )


def test_logout_response_requires_matching_completed_logout_operation(db: Session):
    now = datetime.now(timezone.utc)
    operation = _terminal_operation(
        db,
        operation_type="session_logout",
        client_type="web",
        status="completed",
        http_status=200,
        created_at=now - timedelta(seconds=2),
        completed_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(seconds=60),
    )

    assert restore_logout_response(
        operation=operation,
        payload=logout_response_payload(),
        now=now,
    ) == {"ok": True}

    _assert_replay_error(
        "cached_logout_schema_invalid",
        503,
        lambda: restore_logout_response(
            operation=operation,
            payload={"schema": "formal_auth_logout_response_v1", "ok": False},
            now=now,
        ),
    )

    operation.operation_type = "sms_login"
    _assert_replay_error(
        "cached_operation_type_invalid",
        503,
        lambda: restore_logout_response(
            operation=operation,
            payload=logout_response_payload(),
            now=now,
        ),
    )


def test_expired_logout_response_is_not_replayed(db: Session):
    now = datetime.now(timezone.utc)
    operation = _terminal_operation(
        db,
        operation_type="session_logout",
        client_type="miniprogram",
        status="completed",
        http_status=200,
        created_at=now - timedelta(seconds=4),
        completed_at=now - timedelta(seconds=3),
        expires_at=now - timedelta(seconds=1),
    )

    _assert_replay_error(
        "cached_operation_expired",
        503,
        lambda: restore_logout_response(
            operation=operation,
            payload=logout_response_payload(),
            now=now,
        ),
    )
