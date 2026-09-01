from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth_sessions
from app.database import Base, get_db
from app.formal_services.authentication_identity import compute_identity_hash
from app.formal_services.authentication_rate_limit import (
    consume_authentication_login_rate_limits,
)
from app.formal_services.authentication_idempotency import (
    AuthenticationEncryptionKeyUnavailable,
    StaticAuthenticationKeyProvider,
    create_authentication_response_cipher,
    create_configured_authentication_response_cipher,
)
from app.formal_services.authentication_session_admin import idempotency_storage_key
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    AuthIdempotencyOperation,
    AuthLoginRateLimitBucket,
    AuthRefreshToken,
    LoginChallenge,
    Organization,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
)
from app.models import AuthSession, User, WechatIdentity
from app.routers import auth
from app.security import hash_refresh_token
from app.sms import SmsSendResult
from app.wechat import WechatLoginIdentity, WechatProviderError


IDENTITY_SECRET = "formal-auth-api-identity-hmac-secret-2026"
SMS_PROVIDER = "aliyun_pnvs"
WECHAT_APP_ID = "wx-formal-auth-api"
MOBILE = "13900000831"
OTHER_MOBILE = "13800000832"
ADMIN_MOBILE = "13700000833"
SMS_CODE = "97531864"
CLIENT_IP = "198.51.100.44"
AUTH_IDEMPOTENCY_HMAC_SECRET = (
    "formal-auth-api-idempotency-hmac-secret-2026"
)
AUTH_RATE_LIMIT_HMAC_SECRET = "formal-auth-api-rate-limit-hmac-secret-2026"
AUTH_IDEMPOTENCY_TEST_KEY = bytes(range(32))
NOW = datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc)
FORBIDDEN_IDENTITY_KEYS = {
    "mobile",
    "role",
    "province",
    "user_id",
    "identifier_hash",
    "openid",
    "unionid",
}
FORBIDDEN_SESSION_KEYS = FORBIDDEN_IDENTITY_KEYS | {
    "access_token",
    "device_id",
    "ip_address",
    "mobile_hash",
    "refresh_token",
    "refresh_token_hash",
    "user_agent",
}


class FakeSmsProvider:
    def __init__(self) -> None:
        self.send_calls: list[tuple[str, str]] = []
        self.verify_calls: list[tuple[str, str, str]] = []

    def send(self, mobile: str, out_id: str) -> SmsSendResult:
        self.send_calls.append((mobile, out_id))
        return SmsSendResult(biz_id=f"formal-provider-biz-{len(self.send_calls):04d}")

    def verify(self, mobile: str, code: str, out_id: str) -> bool:
        self.verify_calls.append((mobile, code, out_id))
        return code == SMS_CODE and any(call[1] == out_id for call in self.send_calls)


class FakeWechatProvider:
    def __init__(self, *, openid: str, unionid: str | None = None) -> None:
        self.identity = WechatLoginIdentity(
            app_id=WECHAT_APP_ID,
            openid=openid,
            unionid=unionid,
        )
        self.login_calls: list[str] = []
        self.phone_calls: list[str] = []

    def exchange_login_code(self, code: str) -> WechatLoginIdentity:
        self.login_calls.append(code)
        return self.identity

    def exchange_phone_code(self, code: str) -> str:
        self.phone_calls.append(code)
        return MOBILE


@dataclass(frozen=True)
class ApiWorld:
    client: TestClient
    session_factory: sessionmaker[Session]
    sms_provider: FakeSmsProvider


@dataclass(frozen=True)
class FormalSubject:
    user_id: str
    person_id: uuid.UUID
    mobile: str


@dataclass(frozen=True)
class SessionAdminWorld:
    admin: FormalSubject
    target: FormalSubject
    admin_access_token: str
    target_access_token: str
    admin_session_id: str
    target_session_id: str
    old_target_refresh_token: str
    current_target_refresh_token: str


@pytest.fixture
def api_world(monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        db.add(
            AuditChainHead(
                stream_key="authentication",
                last_event_id=None,
                last_hash=None,
                version=0,
            )
        )
        db.commit()

    production_settings = {
        "environment": "production",
        "cookie_secure": False,
        "identity_hash_secret": IDENTITY_SECRET,
        "identity_hash_version": 1,
        "sms_login_enabled": True,
        "sms_provider": SMS_PROVIDER,
        "sms_access_key_id": "formal-test-access-key-id",
        "sms_access_key_secret": "formal-test-access-key-secret",
        "sms_sign_name": "RSC个人仓",
        "sms_template_code": "SMS_FORMAL_TEST",
        "sms_scheme_name": "RSC个人仓登录",
        "wechat_login_enabled": True,
        "wechat_provider": "wechat",
        "wechat_app_id": WECHAT_APP_ID,
        "wechat_app_secret": "formal-test-wechat-secret",
        "auth_idempotency_ttl_seconds": 90,
        "auth_idempotency_hmac_secret": AUTH_IDEMPOTENCY_HMAC_SECRET,
        "auth_idempotency_encryption_provider": "aliyun_kms",
        "auth_idempotency_kms_key_id": "kms-formal-auth-api-test",
        "auth_idempotency_encryption_key_version": 1,
        "auth_login_rate_limit_hmac_secret": AUTH_RATE_LIMIT_HMAC_SECRET,
        "auth_login_rate_limit_hash_version": 1,
        "auth_login_rate_limit_window_seconds": 60,
        "auth_login_rate_limit_global_limit": 10000,
        "auth_login_rate_limit_ip_limit": 10000,
        "auth_login_rate_limit_identity_limit": 10000,
    }
    for field, value in production_settings.items():
        monkeypatch.setattr(auth.settings, field, value)
    if auth_sessions.settings is not auth.settings:
        for field, value in production_settings.items():
            monkeypatch.setattr(auth_sessions.settings, field, value)

    sms_provider = FakeSmsProvider()
    monkeypatch.setattr(auth, "get_sms_provider", lambda: sms_provider)
    authentication_response_cipher = create_authentication_response_cipher(
        environment="test",
        key_provider=StaticAuthenticationKeyProvider(
            key=AUTH_IDEMPOTENCY_TEST_KEY,
            version=1,
        ),
    )
    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        lambda: authentication_response_cipher,
        raising=False,
    )

    def _override_db():
        with session_factory() as db:
            yield db

    api = FastAPI()
    auth.install_formal_authentication_exception_handler(api)
    api.include_router(auth.router, prefix="/api")
    api.dependency_overrides[get_db] = _override_db
    with TestClient(api, client=(CLIENT_IP, 50000)) as client:
        yield ApiWorld(
            client=client,
            session_factory=session_factory,
            sms_provider=sms_provider,
        )
    engine.dispose()


def _seed_subject(db: Session, *, mobile: str = MOBILE) -> FormalSubject:
    suffix = uuid.uuid4().hex[:10].upper()
    organization = Organization(
        code=f"ORG-AUTH-{suffix}",
        name="正式认证接口测试区域",
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    db.add(organization)
    db.flush()
    person = Person(
        organization_id=organization.id,
        employee_no=f"EMP-AUTH-{suffix}",
        name="认证接口测试工程师",
        employment_status="active",
    )
    db.add(person)
    db.flush()
    user = User(
        person_id=person.id,
        account_status="active",
        authorization_version=1,
        mobile=mobile,
        name=person.name,
        password_hash="disabled",
        role="technician",
        province="江苏省",
        is_active=True,
        require_password_change=False,
    )
    role = db.scalar(select(Role).where(Role.code == "technician"))
    if role is None:
        role = Role(
            code="technician",
            name="工程师",
            is_external=False,
            status="active",
        )
        db.add(role)
    db.add(user)
    db.flush()
    db.add(
        RoleAssignment(
            user_id=user.id,
            role_id=role.id,
            scope_type="person",
            scope_id=str(person.id),
            valid_from=NOW - timedelta(days=1),
            valid_to=None,
            status="active",
            assigned_by=user.id,
            reason="formal authentication API test",
        )
    )
    db.flush()
    return FormalSubject(user_id=user.id, person_id=person.id, mobile=mobile)


def _add_identity(
    db: Session,
    subject: FormalSubject,
    *,
    identity_type: str,
    provider_key: str,
    identifier: str,
    hash_version: int = 1,
    status: str = "active",
) -> AuthIdentity:
    verified_at = NOW - timedelta(hours=1) if status != "pending" else None
    revoked_at = NOW if status == "revoked" else None
    identity = AuthIdentity(
        user_id=subject.user_id,
        identity_type=identity_type,
        provider_key=provider_key,
        identifier_hash=compute_identity_hash(
            secret=IDENTITY_SECRET,
            hash_version=hash_version,
            identity_type=identity_type,
            provider_key=provider_key,
            identifier=identifier,
        ),
        hash_version=hash_version,
        verified_at=verified_at,
        status=status,
        revoked_at=revoked_at,
    )
    db.add(identity)
    db.flush()
    return identity


def _seed_mobile_subject(db: Session, *, mobile: str = MOBILE) -> FormalSubject:
    subject = _seed_subject(db, mobile=mobile)
    _add_identity(
        db,
        subject,
        identity_type="mobile",
        provider_key=SMS_PROVIDER,
        identifier=mobile,
    )
    db.commit()
    return subject


def _seed_headquarters_admin(db: Session) -> FormalSubject:
    suffix = uuid.uuid4().hex[:10].upper()
    headquarters = Organization(
        code=f"HQ-AUTH-{suffix}",
        name="蔚来总部会话管理员测试",
        org_type="headquarters",
        province_code=None,
        status="active",
    )
    db.add(headquarters)
    db.flush()
    person = Person(
        organization_id=headquarters.id,
        employee_no=f"EMP-HQ-AUTH-{suffix}",
        name="总部会话管理员",
        employment_status="active",
    )
    db.add(person)
    db.flush()
    user = User(
        person_id=person.id,
        account_status="active",
        authorization_version=1,
        mobile=ADMIN_MOBILE,
        name=person.name,
        password_hash="disabled",
        role="admin",
        province=None,
        is_active=True,
        require_password_change=False,
    )
    role = Role(
        code="admin",
        name="蔚来总部管理员",
        is_external=False,
        status="active",
    )
    permission = Permission(
        resource="auth_session",
        action="manage",
        field_code="",
        description="正式会话管理",
    )
    db.add_all([user, role, permission])
    db.flush()
    subject = FormalSubject(
        user_id=user.id,
        person_id=person.id,
        mobile=ADMIN_MOBILE,
    )
    _add_identity(
        db,
        subject,
        identity_type="mobile",
        provider_key=SMS_PROVIDER,
        identifier=ADMIN_MOBILE,
    )
    db.add_all(
        [
            RoleAssignment(
                user_id=user.id,
                role_id=role.id,
                scope_type="national",
                scope_id="*",
                valid_from=NOW - timedelta(days=1),
                valid_to=None,
                status="active",
                assigned_by=user.id,
                reason="formal authentication session API test",
            ),
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                effect="allow",
            ),
        ]
    )
    db.commit()
    return subject


def _sms_request_headers(
    *,
    request_id: str,
    idempotency_key: str,
    client_type: str = "web",
) -> dict[str, str]:
    return {
        "X-Request-ID": request_id,
        "Idempotency-Key": idempotency_key,
        "X-Auth-Client": client_type,
    }


def _formal_mutation_headers(
    *,
    request_id: str,
    idempotency_key: str,
    client_type: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Request-ID": request_id,
        "Idempotency-Key": idempotency_key,
    }
    if client_type is not None:
        headers["X-Auth-Client"] = client_type
    return headers


def _request_sms(
    world: ApiWorld,
    *,
    mobile: str = MOBILE,
    request_id: str = "auth-api-sms-request-0001",
    idempotency_key: str = "formal-sms-request-key-0001",
    client_type: str = "web",
):
    return world.client.post(
        "/api/auth/sms/request",
        json={"mobile": mobile},
        headers=_sms_request_headers(
            request_id=request_id,
            idempotency_key=idempotency_key,
            client_type=client_type,
        ),
    )


def _login_sms_web(
    world: ApiWorld,
    *,
    mobile: str = MOBILE,
    request_id: str = "auth-api-sms-login-0001",
    idempotency_key: str = "formal-sms-login-key-0001",
):
    return world.client.post(
        "/api/auth/sms/login",
        json={"mobile": mobile, "code": SMS_CODE},
        headers=_formal_mutation_headers(
            request_id=request_id,
            idempotency_key=idempotency_key,
        ),
    )


def _login_sms_miniprogram(
    world: ApiWorld,
    *,
    mobile: str = MOBILE,
    device_id: str = "formal-mini-device-0001",
    request_id: str = "auth-api-mini-login-0001",
    idempotency_key: str = "formal-mini-login-key-0001",
):
    return world.client.post(
        "/api/auth/miniprogram/sms-login",
        json={
            "mobile": mobile,
            "code": SMS_CODE,
            "device_id": device_id,
            "device_name": "正式认证测试设备",
        },
        headers=_formal_mutation_headers(
            request_id=request_id,
            idempotency_key=idempotency_key,
        ),
    )


def _seed_session_admin_world(world: ApiWorld) -> SessionAdminWorld:
    target_device_id = "formal-target-device-0001"
    with world.session_factory() as db:
        target = _seed_mobile_subject(db)
        admin = _seed_headquarters_admin(db)
        target_user = db.get(User, target.user_id)
        admin_user = db.get(User, admin.user_id)
        assert target_user is not None and admin_user is not None

        first_target_tokens = auth_sessions.create_session(
            db,
            target_user,
            client_type="miniprogram",
            device_id=target_device_id,
            device_name="待下线工程师设备",
            ip_address="198.51.100.51",
            user_agent="formal-session-admin-target",
        )
        _, current_target_tokens = auth_sessions.rotate_session(
            db,
            first_target_tokens.refresh_token,
            device_id=target_device_id,
            ip_address="198.51.100.52",
            user_agent="formal-session-admin-target-current",
        )
        admin_tokens = auth_sessions.create_session(
            db,
            admin_user,
            client_type="web",
            device_id="formal-admin-device-0001",
            device_name="总部管理员当前设备",
            ip_address="198.51.100.53",
            user_agent="formal-session-admin-actor",
        )
        db.commit()
        return SessionAdminWorld(
            admin=admin,
            target=target,
            admin_access_token=admin_tokens.access_token,
            target_access_token=current_target_tokens.access_token,
            admin_session_id=admin_tokens.auth_session.id,
            target_session_id=current_target_tokens.auth_session.id,
            old_target_refresh_token=first_target_tokens.refresh_token,
            current_target_refresh_token=current_target_tokens.refresh_token,
        )


def _admin_headers(
    world: SessionAdminWorld,
    *,
    request_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {world.admin_access_token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _payload_keys(payload: object) -> set[str]:
    found_keys: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                found_keys.add(str(key))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found_keys


def _assert_response_has_no_legacy_identity(payload: object) -> None:
    assert FORBIDDEN_IDENTITY_KEYS.isdisjoint(_payload_keys(payload))


def _assert_session_response_is_redacted(payload: object) -> None:
    assert FORBIDDEN_SESSION_KEYS.isdisjoint(_payload_keys(payload))


def _assert_hmac_session_ip(value: str) -> None:
    marker, version, digest = value.split(":", 2)
    assert marker == "hmac"
    assert version == "1"
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


def _evidence_document(db: Session) -> str:
    challenges = list(db.scalars(select(LoginChallenge)))
    sessions = list(db.scalars(select(AuthSession)))
    refresh_tokens = list(db.scalars(select(AuthRefreshToken)))
    audits = list(db.scalars(select(AuditEvent)))
    transitions = list(db.scalars(select(StateTransitionEvent)))
    return json.dumps(
        {
            "challenges": [
                {
                    "mobile_hash": row.mobile_hash,
                    "code_hash": row.code_hash,
                    "provider": row.provider,
                    "provider_reference": row.provider_reference,
                    "idempotency_key": row.idempotency_key,
                    "requested_ip_hash": row.requested_ip_hash,
                    "status": row.status,
                }
                for row in challenges
            ],
            "sessions": [
                {
                    "refresh_token_hash": row.refresh_token_hash,
                    "client_type": row.client_type,
                    "device_id": row.device_id,
                    "device_name": row.device_name,
                    "ip_address": row.ip_address,
                    "user_agent": row.user_agent,
                }
                for row in sessions
            ],
            "refresh_tokens": [
                {
                    "token_hash": row.token_hash,
                    "issued_at": row.issued_at,
                    "consumed_at": row.consumed_at,
                    "revoked_at": row.revoked_at,
                }
                for row in refresh_tokens
            ],
            "audits": [
                {
                    "action": row.action,
                    "before": row.before_jsonb,
                    "after": row.after_jsonb,
                    "request_id": row.request_id,
                }
                for row in audits
            ],
            "transitions": [
                {
                    "from": row.from_status,
                    "to": row.to_status,
                    "reason": row.reason,
                    "idempotency_key": row.idempotency_key,
                    "metadata": row.metadata_jsonb,
                }
                for row in transitions
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _idempotency_evidence_document(db: Session) -> str:
    rows = list(
        db.scalars(
            select(AuthIdempotencyOperation).order_by(
                AuthIdempotencyOperation.created_at,
                AuthIdempotencyOperation.id,
            )
        )
    )
    return json.dumps(
        [
            {
                "id": str(row.id),
                "operation_type": row.operation_type,
                "client_type": row.client_type,
                "idempotency_key_hash": row.idempotency_key_hash,
                "scope_hash": row.scope_hash,
                "request_hmac": row.request_hmac,
                "status": row.status,
                "response_ciphertext": (
                    row.response_ciphertext.hex()
                    if row.response_ciphertext is not None
                    else None
                ),
                "response_nonce": (
                    row.response_nonce.hex()
                    if row.response_nonce is not None
                    else None
                ),
                "response_sha256": row.response_sha256,
                "encryption_key_version": row.encryption_key_version,
                "http_status": row.http_status,
                "auth_session_id": row.auth_session_id,
                "input_refresh_token_id": (
                    str(row.input_refresh_token_id)
                    if row.input_refresh_token_id is not None
                    else None
                ),
                "output_refresh_token_id": (
                    str(row.output_refresh_token_id)
                    if row.output_refresh_token_id is not None
                    else None
                ),
                "expires_at": row.expires_at,
                "completed_at": row.completed_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in rows
        ],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def test_formal_mobile_sms_login_is_minimal_consumed_and_fully_evidenced(
    api_world: ApiWorld,
):
    with api_world.session_factory() as db:
        subject = _seed_mobile_subject(db)

    requested = _request_sms(api_world)
    assert requested.status_code == 200, requested.text
    assert requested.json()["message"] == auth.SMS_REQUEST_MESSAGE
    assert len(api_world.sms_provider.send_calls) == 1
    assert api_world.sms_provider.send_calls[0][0] == MOBILE

    logged_in = _login_sms_web(api_world)
    assert logged_in.status_code == 200, logged_in.text
    _assert_response_has_no_legacy_identity(logged_in.json())
    assert logged_in.json()["person_id"] == str(subject.person_id)
    assert MOBILE not in logged_in.text
    assert subject.user_id not in logged_in.text

    current = api_world.client.get("/api/auth/me")
    assert current.status_code == 200, current.text
    _assert_response_has_no_legacy_identity(current.json())
    assert current.json() == logged_in.json()
    assert MOBILE not in current.text
    assert subject.user_id not in current.text

    with api_world.session_factory() as db:
        challenge = db.scalar(select(LoginChallenge))
        assert challenge is not None
        assert challenge.status == "consumed"
        assert challenge.consumed_at is not None
        assert challenge.code_hash is None
        assert challenge.mobile_hash != MOBILE
        assert challenge.requested_ip_hash != CLIENT_IP

        sessions = list(db.scalars(select(AuthSession)))
        refresh_tokens = list(db.scalars(select(AuthRefreshToken)))
        audits = list(db.scalars(select(AuditEvent)))
        transitions = list(db.scalars(select(StateTransitionEvent)))
        assert len(sessions) == 1
        _assert_hmac_session_ip(sessions[0].ip_address)
        assert len(refresh_tokens) == 1
        assert refresh_tokens[0].token_hash == sessions[0].refresh_token_hash
        assert len(audits) >= 6
        assert len(transitions) >= 4
        assert {
            "authentication.sms.challenge_consumed",
            "authentication.session.created",
        }.issubset({row.action for row in audits})
        assert any(
            row.aggregate_type == "login_challenge" and row.to_status == "consumed"
            for row in transitions
        )
        assert any(
            row.aggregate_type == "auth_session" and row.to_status == "active"
            for row in transitions
        )
        head = db.scalar(
            select(AuditChainHead).where(
                AuditChainHead.stream_key == "authentication"
            )
        )
        assert head is not None and head.version == len(audits)

        evidence = _evidence_document(db)
        for plaintext in (MOBILE, SMS_CODE, CLIENT_IP):
            assert plaintext not in evidence


def test_sms_login_rate_rejection_precedes_idempotency_provider_and_audit(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    with api_world.session_factory() as db:
        bind = db.get_bind()
    consumed = consume_authentication_login_rate_limits(
        bind,
        operation_type="sms_login",
        hmac_secret=AUTH_RATE_LIMIT_HMAC_SECRET,
        hash_version=1,
        window_seconds=60,
        global_limit=1,
        ip_address_value=CLIENT_IP,
        ip_limit=1,
        identity_identifier=MOBILE,
        identity_limit=1,
    )
    assert consumed.allowed is True
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_global_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_ip_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_identity_limit", 1)

    rejected = _login_sms_web(
        api_world,
        request_id="auth-api-sms-rate-reject-0001",
        idempotency_key="formal-sms-rate-reject-key-0001",
    )

    assert rejected.status_code == 429
    assert int(rejected.headers["Retry-After"]) >= 1
    assert api_world.sms_provider.verify_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        rows = list(db.scalars(select(AuthLoginRateLimitBucket)))
        assert rows
        serialized = json.dumps(
            [
                {
                    "scope_type": row.scope_type,
                    "hash_version": row.hash_version,
                    "scope_hmac": row.scope_hmac,
                }
                for row in rows
            ],
            sort_keys=True,
        )
        assert MOBILE not in serialized and CLIENT_IP not in serialized


def test_sms_login_window_change_fails_before_idempotency_provider_and_audit(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    with api_world.session_factory() as db:
        bind = db.get_bind()
    assert consume_authentication_login_rate_limits(
        bind,
        operation_type="sms_login",
        hmac_secret=AUTH_RATE_LIMIT_HMAC_SECRET,
        hash_version=1,
        window_seconds=60,
        global_limit=100,
        ip_address_value=CLIENT_IP,
        ip_limit=100,
        identity_identifier=MOBILE,
        identity_limit=100,
    ).allowed is True
    monkeypatch.setattr(
        auth.settings,
        "auth_login_rate_limit_window_seconds",
        30,
    )

    rejected = _login_sms_web(
        api_world,
        request_id="auth-api-sms-window-change-0001",
        idempotency_key="formal-sms-window-change-key-0001",
    )

    assert rejected.status_code == 503
    assert rejected.json()["detail"]["code"] == (
        "authentication_login_rate_limit_window_change_in_progress"
    )
    assert api_world.sms_provider.verify_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_sms_login_same_key_replays_exact_tokens_without_second_verification(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    device_id = "formal-sms-idempotent-device-0001"
    raw_key = "formal-sms-login-idempotent-key-0001"
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-sms-idempotent-request-0001",
        idempotency_key="formal-sms-idempotent-request-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text

    first = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-sms-idempotent-login-first-0001",
        idempotency_key=raw_key,
    )
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_global_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_ip_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_identity_limit", 1)
    replay = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-sms-idempotent-login-replay-0002",
        idempotency_key=raw_key,
    )

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["access_token"] == first.json()["access_token"]
    assert replay.json()["refresh_token"] == first.json()["refresh_token"]
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert len(api_world.sms_provider.verify_calls) == 1

    random_key = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-sms-idempotent-login-random-0003",
        idempotency_key="formal-sms-login-random-key-0003",
    )
    assert random_key.status_code == 429
    assert int(random_key.headers["Retry-After"]) >= 1
    assert random_key.json()["detail"]["code"] == (
        "authentication_login_rate_limited"
    )
    assert len(api_world.sms_provider.verify_calls) == 1

    with api_world.session_factory() as db:
        operations = list(
            db.scalars(
                select(AuthIdempotencyOperation).where(
                    AuthIdempotencyOperation.operation_type == "sms_login"
                )
            )
        )
        assert len(operations) == 1
        assert operations[0].status == "completed"
        assert operations[0].http_status == 200
        assert operations[0].idempotency_key_hash != raw_key
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "authentication.session.created")
            )
            == 1
        )
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "authentication.session.created")
            )
            == 1
        )


def test_sms_login_same_key_with_different_device_is_a_conflict(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    raw_key = "formal-sms-login-conflict-key-0001"
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-sms-conflict-request-0001",
        idempotency_key="formal-sms-conflict-request-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text

    first = _login_sms_miniprogram(
        api_world,
        device_id="formal-sms-conflict-device-0001",
        request_id="auth-api-sms-conflict-login-first-0001",
        idempotency_key=raw_key,
    )
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_global_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_ip_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_identity_limit", 1)
    conflict = _login_sms_miniprogram(
        api_world,
        device_id="formal-sms-conflict-device-0002",
        request_id="auth-api-sms-conflict-login-second-0002",
        idempotency_key=raw_key,
    )

    assert first.status_code == 200, first.text
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == (
        "authentication_idempotency_key_conflict"
    )
    assert len(api_world.sms_provider.verify_calls) == 1
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 1


@pytest.mark.parametrize(
    "identity_variant",
    [
        "legacy_user_mobile_only",
        "provider_mismatch",
        "hash_version_mismatch",
        "identifier_hash_mismatch",
        "pending",
        "revoked",
        "other_active_identity",
    ],
)
def test_mobile_login_never_falls_back_from_an_inexact_formal_identity(
    api_world: ApiWorld,
    identity_variant: str,
):
    with api_world.session_factory() as db:
        subject = _seed_subject(db)
        if identity_variant == "provider_mismatch":
            _add_identity(
                db,
                subject,
                identity_type="mobile",
                provider_key="other_sms_provider",
                identifier=MOBILE,
            )
        elif identity_variant == "hash_version_mismatch":
            _add_identity(
                db,
                subject,
                identity_type="mobile",
                provider_key=SMS_PROVIDER,
                identifier=MOBILE,
                hash_version=2,
            )
        elif identity_variant == "identifier_hash_mismatch":
            _add_identity(
                db,
                subject,
                identity_type="mobile",
                provider_key=SMS_PROVIDER,
                identifier=OTHER_MOBILE,
            )
        elif identity_variant in {"pending", "revoked"}:
            _add_identity(
                db,
                subject,
                identity_type="mobile",
                provider_key=SMS_PROVIDER,
                identifier=MOBILE,
                status=identity_variant,
            )
        elif identity_variant == "other_active_identity":
            _add_identity(
                db,
                subject,
                identity_type="wechat_unionid",
                provider_key=WECHAT_APP_ID,
                identifier="unionid-cannot-replace-mobile",
            )
        db.commit()

    requested = _request_sms(
        api_world,
        request_id=f"auth-api-inexact-request-{identity_variant}",
        idempotency_key=f"formal-inexact-{identity_variant}-0001",
    )
    assert requested.status_code == 200, requested.text
    assert requested.json() == {
        "ok": True,
        "message": auth.SMS_REQUEST_MESSAGE,
        "retry_after": auth.settings.sms_interval_seconds,
        "expires_in": auth.settings.sms_valid_seconds,
    }
    assert api_world.sms_provider.send_calls == []

    login = _login_sms_web(
        api_world,
        request_id=f"auth-api-inexact-login-{identity_variant}",
    )
    assert login.status_code == 401
    assert login.json()["detail"] == "验证码不正确或已失效"
    assert api_world.sms_provider.verify_calls == []

    with api_world.session_factory() as db:
        challenge = db.scalar(select(LoginChallenge))
        assert challenge is not None and challenge.status == "cancelled"
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0


def test_unknown_mobile_has_the_same_request_response_as_a_known_identity(
    api_world: ApiWorld,
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)

    known = _request_sms(
        api_world,
        request_id="auth-api-known-request-0001",
        idempotency_key="formal-known-request-key-0001",
    )
    unknown = _request_sms(
        api_world,
        mobile=OTHER_MOBILE,
        request_id="auth-api-unknown-request-0001",
        idempotency_key="formal-unknown-request-key-0001",
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert [call[0] for call in api_world.sms_provider.send_calls] == [MOBILE]
    with api_world.session_factory() as db:
        statuses = list(
            db.scalars(select(LoginChallenge.status).order_by(LoginChallenge.created_at))
        )
        assert statuses == ["pending", "cancelled"]
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_sms_request_idempotency_replay_does_not_send_or_append_twice(
    api_world: ApiWorld,
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)

    idempotency_key = "formal-idempotency-replay-key-0001"
    first = _request_sms(
        api_world,
        request_id="auth-api-idempotency-first-0001",
        idempotency_key=idempotency_key,
    )
    assert first.status_code == 200, first.text
    with api_world.session_factory() as db:
        first_counts = (
            db.scalar(select(func.count()).select_from(LoginChallenge)),
            db.scalar(select(func.count()).select_from(AuditEvent)),
            db.scalar(select(func.count()).select_from(StateTransitionEvent)),
        )

    replay = _request_sms(
        api_world,
        request_id="auth-api-idempotency-replay-0002",
        idempotency_key=idempotency_key,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert len(api_world.sms_provider.send_calls) == 1

    with api_world.session_factory() as db:
        assert (
            db.scalar(select(func.count()).select_from(LoginChallenge)),
            db.scalar(select(func.count()).select_from(AuditEvent)),
            db.scalar(select(func.count()).select_from(StateTransitionEvent)),
        ) == first_counts
        challenge = db.scalar(select(LoginChallenge))
        assert challenge is not None
        assert idempotency_key not in challenge.idempotency_key


def test_kms_preflight_failure_precedes_sms_and_all_persistent_side_effects(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _unavailable_cipher():
        raise AuthenticationEncryptionKeyUnavailable("test KMS unavailable")

    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        _unavailable_cipher,
    )

    response = _request_sms(
        api_world,
        request_id="auth-api-kms-preflight-failure-0001",
        idempotency_key="formal-kms-preflight-failure-key-0001",
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "authentication_idempotency_encryption_unavailable",
        "category": "service_unavailable",
        "message": "认证幂等加密服务不可用",
    }
    assert api_world.sms_provider.send_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_kms_preflight_failure_precedes_login_limits_codes_and_providers(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wechat_provider = FakeWechatProvider(openid="kms-must-precede-this-openid")
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: wechat_provider)

    def _unavailable_cipher():
        raise AuthenticationEncryptionKeyUnavailable("test KMS unavailable")

    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        _unavailable_cipher,
    )

    responses = [
        _login_sms_web(
            api_world,
            request_id="auth-api-kms-before-web-login-0001",
            idempotency_key="formal-kms-before-web-login-key-0001",
        ),
        _login_sms_miniprogram(
            api_world,
            request_id="auth-api-kms-before-mini-login-0001",
            idempotency_key="formal-kms-before-mini-login-key-0001",
        ),
        api_world.client.post(
            "/api/auth/miniprogram/wechat-login",
            json={
                "login_code": "kms-must-precede-this-login-code",
                "device_id": "kms-before-wechat-device-0001",
                "device_name": "KMS 前置验证设备",
            },
            headers=_formal_mutation_headers(
                request_id="auth-api-kms-before-wechat-login-0001",
                idempotency_key="formal-kms-before-wechat-login-key-0001",
            ),
        ),
    ]

    assert [response.status_code for response in responses] == [503, 503, 503]
    assert all(
        response.json()["detail"]["code"]
        == "authentication_idempotency_encryption_unavailable"
        for response in responses
    )
    assert api_world.sms_provider.verify_calls == []
    assert wechat_provider.login_calls == []
    assert wechat_provider.phone_calls == []
    with api_world.session_factory() as db:
        for model in (
            AuthLoginRateLimitBucket,
            AuthIdempotencyOperation,
            LoginChallenge,
            AuthSession,
            AuditEvent,
            StateTransitionEvent,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_login_preflight_cipher_is_reused_by_idempotency_begin(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-cipher-reuse-request-0001",
        idempotency_key="formal-cipher-reuse-request-key-0001",
    )
    assert requested.status_code == 200, requested.text

    factory_calls = 0

    def _new_cipher():
        nonlocal factory_calls
        factory_calls += 1
        return create_authentication_response_cipher(
            environment="test",
            key_provider=StaticAuthenticationKeyProvider(
                key=AUTH_IDEMPOTENCY_TEST_KEY,
                version=1,
            ),
        )

    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        _new_cipher,
    )
    logged_in = _login_sms_web(
        api_world,
        request_id="auth-api-cipher-reuse-login-0001",
        idempotency_key="formal-cipher-reuse-login-key-0001",
    )

    assert logged_in.status_code == 200, logged_in.text
    assert factory_calls == 1


def test_missing_authentication_chain_head_fails_before_sms_provider_send(
    api_world: ApiWorld,
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
        head = db.scalar(
            select(AuditChainHead).where(
                AuditChainHead.stream_key == "authentication"
            )
        )
        assert head is not None
        db.delete(head)
        db.commit()

    response = _request_sms(
        api_world,
        request_id="auth-api-missing-head-0001",
        idempotency_key="formal-missing-head-key-0001",
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "authentication_audit_unavailable",
        "category": "service_unavailable",
        "message": "认证服务暂时不可用",
    }
    assert api_world.sms_provider.send_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_wechat_login_requires_one_exact_formal_openid_identity(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    openid = "formal-openid-exact-0001"
    unionid = "formal-unionid-associated-0001"
    provider = FakeWechatProvider(openid=openid, unionid=unionid)
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: provider)
    with api_world.session_factory() as db:
        subject = _seed_subject(db)
        _add_identity(
            db,
            subject,
            identity_type="wechat_openid",
            provider_key=WECHAT_APP_ID,
            identifier=openid,
        )
        db.commit()

    response = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json={
            "login_code": "formal-wechat-login-code",
            "phone_code": "phone-code-must-not-be-used",
            "device_id": "formal-wechat-device-0001",
            "device_name": "微信正式认证测试设备",
        },
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-login-0001",
            idempotency_key="formal-wechat-login-key-0001",
        ),
    )

    assert response.status_code == 200, response.text
    _assert_response_has_no_legacy_identity(response.json())
    assert response.json()["user"]["person_id"] == str(subject.person_id)
    assert openid not in response.text and unionid not in response.text
    assert subject.user_id not in response.text
    assert provider.login_calls == ["formal-wechat-login-code"]
    assert provider.phone_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 1
        assert db.scalar(select(func.count()).select_from(WechatIdentity)) == 0
        actions = set(db.scalars(select(AuditEvent.action)))
        assert "authentication.session.created" in actions


def test_wechat_pre_provider_rate_rejection_writes_no_idempotency_or_audit(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    provider = FakeWechatProvider(openid="wechat-rate-rejected-openid")
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: provider)
    with api_world.session_factory() as db:
        bind = db.get_bind()
    consumed = consume_authentication_login_rate_limits(
        bind,
        operation_type="wechat_login",
        hmac_secret=AUTH_RATE_LIMIT_HMAC_SECRET,
        hash_version=1,
        window_seconds=60,
        global_limit=1,
        ip_address_value=CLIENT_IP,
        ip_limit=1,
    )
    assert consumed.allowed is True
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_global_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_ip_limit", 1)

    rejected = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json={
            "login_code": "wechat-rate-rejected-login-code",
            "device_id": "wechat-rate-rejected-device-0001",
            "device_name": "拒绝前不得调用 provider",
        },
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-rate-reject-0001",
            idempotency_key="formal-wechat-rate-reject-key-0001",
        ),
    )

    assert rejected.status_code == 429
    assert int(rejected.headers["Retry-After"]) >= 1
    assert provider.login_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthIdempotencyOperation)) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_wechat_login_same_key_replays_exact_tokens_without_provider_reuse(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    openid = "formal-openid-idempotent-0001"
    login_code = "formal-wechat-idempotent-login-code"
    device_id = "formal-wechat-idempotent-device-0001"
    raw_key = "formal-wechat-idempotent-login-key-0001"
    provider = FakeWechatProvider(
        openid=openid,
        unionid="formal-unionid-idempotent-0001",
    )
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: provider)
    with api_world.session_factory() as db:
        subject = _seed_subject(db)
        _add_identity(
            db,
            subject,
            identity_type="wechat_openid",
            provider_key=WECHAT_APP_ID,
            identifier=openid,
        )
        db.commit()

    payload = {
        "login_code": login_code,
        "phone_code": "phone-code-must-never-be-used",
        "device_id": device_id,
        "device_name": "微信幂等正式认证测试设备",
    }
    first = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-idempotent-first-0001",
            idempotency_key=raw_key,
        ),
    )
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_global_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_ip_limit", 1)
    monkeypatch.setattr(auth.settings, "auth_login_rate_limit_identity_limit", 1)
    replay = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-idempotent-replay-0002",
            idempotency_key=raw_key,
        ),
    )

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["access_token"] == first.json()["access_token"]
    assert replay.json()["refresh_token"] == first.json()["refresh_token"]
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert provider.login_calls == [login_code]
    assert provider.phone_calls == []
    random_key = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-idempotent-random-0003",
            idempotency_key="formal-wechat-idempotent-random-key-0003",
        ),
    )
    assert random_key.status_code == 429
    assert int(random_key.headers["Retry-After"]) >= 1
    assert provider.login_calls == [login_code]
    with api_world.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuthIdempotencyOperation)
                .where(
                    AuthIdempotencyOperation.operation_type == "wechat_login"
                )
            )
            == 1
        )


def test_wechat_provider_runs_only_after_committed_pending_owner(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openid = "formal-openid-owner-before-provider-0001"
    observed_statuses: list[list[str]] = []

    class OwnershipCheckingProvider(FakeWechatProvider):
        def exchange_login_code(self, code: str) -> WechatLoginIdentity:
            with api_world.session_factory() as db:
                observed_statuses.append(
                    list(
                        db.scalars(
                            select(AuthIdempotencyOperation.status).where(
                                AuthIdempotencyOperation.operation_type
                                == "wechat_login"
                            )
                        )
                    )
                )
            return super().exchange_login_code(code)

    provider = OwnershipCheckingProvider(openid=openid)
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: provider)
    with api_world.session_factory() as db:
        subject = _seed_subject(db)
        _add_identity(
            db,
            subject,
            identity_type="wechat_openid",
            provider_key=WECHAT_APP_ID,
            identifier=openid,
        )
        db.commit()

    response = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json={
            "login_code": "formal-wechat-owner-before-provider-code",
            "device_id": "formal-wechat-owner-device-0001",
        },
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-owner-before-provider-0001",
            idempotency_key="formal-wechat-owner-before-provider-key-0001",
        ),
    )

    assert response.status_code == 200, response.text
    assert observed_statuses == [["pending"]]
    assert provider.login_calls == ["formal-wechat-owner-before-provider-code"]


def test_wechat_provider_failure_is_one_replayable_terminal_operation_and_audit(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider(FakeWechatProvider):
        def exchange_login_code(self, code: str) -> WechatLoginIdentity:
            self.login_calls.append(code)
            raise WechatProviderError("sensitive provider diagnostic")

    provider = FailingProvider(openid="unused-provider-failure-openid")
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: provider)
    payload = {
        "login_code": "formal-wechat-provider-failure-code",
        "device_id": "formal-wechat-provider-failure-device-0001",
    }
    headers = _formal_mutation_headers(
        request_id="auth-api-wechat-provider-failure-first-0001",
        idempotency_key="formal-wechat-provider-failure-key-0001",
    )

    first = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json=payload,
        headers=headers,
    )
    replay = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-provider-failure-replay-0002",
            idempotency_key="formal-wechat-provider-failure-key-0001",
        ),
    )

    assert first.status_code == replay.status_code == 502
    assert first.json() == replay.json() == {"detail": "微信接口暂时不可用"}
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert provider.login_calls == ["formal-wechat-provider-failure-code"]
    assert "sensitive provider diagnostic" not in first.text
    with api_world.session_factory() as db:
        operation = db.scalar(
            select(AuthIdempotencyOperation).where(
                AuthIdempotencyOperation.operation_type == "wechat_login"
            )
        )
        assert operation is not None
        assert operation.status == "failed"
        assert operation.http_status == 502
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "authentication.wechat.login_failed"
                )
            )
        )
        assert len(events) == 1
        assert events[0].after_jsonb["reason_code"] == "provider_unavailable"
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_wechat_never_falls_back_to_unionid_legacy_binding_or_phone_code(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    openid = "legacy-openid-must-not-authenticate"
    unionid = "formal-unionid-must-not-replace-openid"
    provider = FakeWechatProvider(openid=openid, unionid=unionid)
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: provider)
    with api_world.session_factory() as db:
        subject = _seed_subject(db)
        _add_identity(
            db,
            subject,
            identity_type="wechat_unionid",
            provider_key=WECHAT_APP_ID,
            identifier=unionid,
        )
        legacy = WechatIdentity(
            user_id=subject.user_id,
            app_id=WECHAT_APP_ID,
            openid=openid,
            unionid=unionid,
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id

    response = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json={
            "login_code": "legacy-wechat-login-code",
            "phone_code": "legacy-phone-code",
            "device_id": "formal-wechat-device-0002",
        },
        headers=_formal_mutation_headers(
            request_id="auth-api-wechat-no-fallback-0001",
            idempotency_key="formal-wechat-no-fallback-key-0001",
        ),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "登录身份无效或账号不可用"
    assert provider.login_calls == ["legacy-wechat-login-code"]
    assert provider.phone_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0
        assert db.scalar(select(func.count()).select_from(AuthIdentity)) == 1
        assert db.get(WechatIdentity, legacy_id) is not None
        assert db.scalar(select(func.count()).select_from(WechatIdentity)) == 1


def test_refresh_same_key_replays_exact_rotation_but_different_key_revokes_family(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    device_id = "formal-idempotent-refresh-device-0001"
    refresh_key = "formal-refresh-idempotent-key-0001"
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-idempotent-refresh-sms-request-0001",
        idempotency_key="formal-idempotent-refresh-sms-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text
    login = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-idempotent-refresh-login-0001",
        idempotency_key="formal-idempotent-refresh-login-key-0001",
    )
    assert login.status_code == 200, login.text
    old_refresh_token = login.json()["refresh_token"]

    refresh_payload = {
        "refresh_token": old_refresh_token,
        "device_id": device_id,
    }
    first = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json=refresh_payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-idempotent-refresh-first-0001",
            idempotency_key=refresh_key,
        ),
    )
    replay = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json=refresh_payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-idempotent-refresh-replay-0002",
            idempotency_key=refresh_key,
        ),
    )

    assert first.status_code == replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.json()["access_token"] == first.json()["access_token"]
    assert replay.json()["refresh_token"] == first.json()["refresh_token"]
    assert replay.json()["refresh_token"] != old_refresh_token
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"

    with api_world.session_factory() as db:
        auth_session = db.scalar(select(AuthSession))
        assert auth_session is not None and auth_session.revoked_at is None
        histories = list(
            db.scalars(
                select(AuthRefreshToken).where(
                    AuthRefreshToken.session_id == auth_session.id
                )
            )
        )
        assert len(histories) == 2
        assert sum(row.consumed_at is not None for row in histories) == 1
        assert all(row.revoked_at is None for row in histories)
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action
                    == "authentication.session.refresh_replay"
                )
            )
            == 0
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "authentication.session.rotated")
            )
            == 1
        )

    kms_calls: list[tuple[str, int]] = []

    def fail_after_preflight(key_id: str, version: int) -> bytes:
        kms_calls.append((key_id, version))
        if len(kms_calls) > 1:
            raise RuntimeError("simulated KMS loss after replay preflight")
        return AUTH_IDEMPOTENCY_TEST_KEY

    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        lambda: create_configured_authentication_response_cipher(
            settings=auth.settings,
            kms_key_loader=fail_after_preflight,
        ),
    )
    attack = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json=refresh_payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-idempotent-refresh-new-key-0001",
            idempotency_key="formal-refresh-replay-attack-key-0001",
        ),
    )
    assert attack.status_code == 401, attack.text
    assert attack.json()["detail"] == "登录已失效"
    assert kms_calls == [("kms-formal-auth-api-test", 1)]

    with api_world.session_factory() as db:
        auth_session = db.scalar(select(AuthSession))
        assert auth_session is not None and auth_session.revoked_at is not None
        current_history = db.scalar(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash
                == hash_refresh_token(first.json()["refresh_token"])
            )
        )
        assert current_history is not None
        assert current_history.revoked_at is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action
                    == "authentication.session.refresh_replay"
                )
            )
            == 1
        )


def test_refresh_token_replay_revokes_the_family_and_current_access_immediately(
    api_world: ApiWorld,
):
    device_id = "formal-replay-device-0001"
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-replay-sms-request-0001",
        idempotency_key="formal-replay-sms-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text
    login = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-replay-login-0001",
    )
    assert login.status_code == 200, login.text
    old_refresh_token = login.json()["refresh_token"]

    rotated = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json={"refresh_token": old_refresh_token, "device_id": device_id},
        headers=_formal_mutation_headers(
            request_id="auth-api-refresh-rotate-0001",
            idempotency_key="formal-refresh-rotate-key-0001",
        ),
    )
    assert rotated.status_code == 200, rotated.text
    _assert_response_has_no_legacy_identity(rotated.json())
    current_access_token = rotated.json()["access_token"]
    current_refresh_token = rotated.json()["refresh_token"]
    assert current_refresh_token != old_refresh_token

    replay = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json={"refresh_token": old_refresh_token, "device_id": device_id},
        headers=_formal_mutation_headers(
            request_id="auth-api-refresh-replay-0001",
            idempotency_key="formal-refresh-replay-key-0001",
        ),
    )
    assert replay.status_code == 401
    assert replay.json()["detail"] == "登录已失效"

    current_access = api_world.client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {current_access_token}"},
    )
    assert current_access.status_code == 401
    assert current_access.json()["detail"] == "登录已失效"

    with api_world.session_factory() as db:
        auth_session = db.scalar(select(AuthSession))
        assert auth_session is not None and auth_session.revoked_at is not None
        _assert_hmac_session_ip(auth_session.ip_address)
        old_history = db.scalar(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash == hash_refresh_token(old_refresh_token)
            )
        )
        current_history = db.scalar(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash == hash_refresh_token(current_refresh_token)
            )
        )
        assert old_history is not None and old_history.consumed_at is not None
        assert current_history is not None and current_history.revoked_at is not None
        assert old_history.replaced_by_id == current_history.id
        replay_audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "authentication.session.refresh_replay"
            )
        )
        assert replay_audit is not None
        # A presented bearer token identifies the session, not the human who
        # presented it; never misattribute a possible replay to the account.
        assert replay_audit.actor_user_id is None
        assert any(
            row.aggregate_type == "auth_session" and row.to_status == "revoked"
            for row in db.scalars(select(StateTransitionEvent))
        )


def test_missing_web_refresh_token_is_rejected_and_audited(
    api_world: ApiWorld,
):
    request_id = "auth-api-refresh-missing-0001"

    response = api_world.client.post(
        "/api/auth/refresh",
        headers=_formal_mutation_headers(
            request_id=request_id,
            idempotency_key="formal-refresh-missing-key-0001",
        ),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "登录已失效"
    with api_world.session_factory() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "authentication.session.refresh_failed"
            )
        )
        assert audit is not None
        assert audit.actor_user_id is None
        assert audit.aggregate_type == "authentication_attempt"
        assert audit.after_jsonb == {
            "client_type": "web",
            "outcome": "rejected",
            "reason_code": "refresh_missing",
        }
        assert request_id not in json.dumps(audit.after_jsonb, ensure_ascii=False)


def test_unknown_miniprogram_refresh_token_is_rejected_and_audited(
    api_world: ApiWorld,
):
    request_id = "auth-api-refresh-rejected-0001"

    response = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json={
            "refresh_token": "unknown-refresh-token-material-00000000000000000001",
            "device_id": "formal-refresh-device-0001",
        },
        headers=_formal_mutation_headers(
            request_id=request_id,
            idempotency_key="formal-refresh-rejected-key-0001",
        ),
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "登录已失效"
    with api_world.session_factory() as db:
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "authentication.session.refresh_failed"
            )
        )
        assert audit is not None
        assert audit.actor_user_id is None
        assert audit.aggregate_type == "authentication_attempt"
        assert audit.after_jsonb == {
            "client_type": "miniprogram",
            "outcome": "rejected",
            "reason_code": "refresh_rejected",
        }
        serialized = json.dumps(
            {"before": audit.before_jsonb, "after": audit.after_jsonb},
            ensure_ascii=False,
        )
        assert "unknown-refresh-token" not in serialized
        assert request_id not in serialized


def test_failed_refresh_same_key_replays_exact_failure_without_duplicate_audit(
    api_world: ApiWorld,
):
    raw_token = "unknown-refresh-token-material-00000000000000000002"
    raw_key = "formal-refresh-failure-idempotent-key-0001"
    payload = {
        "refresh_token": raw_token,
        "device_id": "formal-refresh-failure-device-0001",
    }
    first = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-refresh-failure-first-0001",
            idempotency_key=raw_key,
        ),
    )
    replay = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-refresh-failure-replay-0002",
            idempotency_key=raw_key,
        ),
    )

    assert first.status_code == replay.status_code == 401
    assert first.json() == replay.json() == {"detail": "登录已失效"}
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action
                    == "authentication.session.refresh_failed"
                )
            )
            == 1
        )
        operations = list(
            db.scalars(
                select(AuthIdempotencyOperation).where(
                    AuthIdempotencyOperation.operation_type == "session_refresh"
                )
            )
        )
        assert len(operations) == 1
        assert operations[0].status == "failed"
        assert operations[0].http_status == 401
        assert operations[0].response_ciphertext is not None
        evidence = _idempotency_evidence_document(db)
        for plaintext in (raw_token, raw_key, payload["device_id"], CLIENT_IP):
            assert plaintext not in evidence


def test_logout_same_key_revokes_and_appends_evidence_only_once(
    api_world: ApiWorld,
):
    device_id = "formal-idempotent-logout-device-0001"
    logout_key = "formal-idempotent-logout-key-0001"
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-idempotent-logout-request-0001",
        idempotency_key="formal-idempotent-logout-request-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text
    login = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-idempotent-logout-login-0001",
        idempotency_key="formal-idempotent-logout-login-key-0001",
    )
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refresh_token"]
    payload = {"refresh_token": refresh_token}

    first = api_world.client.post(
        "/api/auth/miniprogram/logout",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-idempotent-logout-first-0001",
            idempotency_key=logout_key,
        ),
    )
    replay = api_world.client.post(
        "/api/auth/miniprogram/logout",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="auth-api-idempotent-logout-replay-0002",
            idempotency_key=logout_key,
        ),
    )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"ok": True}
    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.headers["Idempotency-Replayed"] == "true"
    with api_world.session_factory() as db:
        auth_session = db.scalar(select(AuthSession))
        assert auth_session is not None and auth_session.revoked_at is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "authentication.session.revoked")
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(StateTransitionEvent)
                .where(
                    StateTransitionEvent.aggregate_type == "auth_session",
                    StateTransitionEvent.to_status == "revoked",
                    StateTransitionEvent.reason == "user_logout",
                )
            )
            == 1
        )
        operations = list(
            db.scalars(
                select(AuthIdempotencyOperation).where(
                    AuthIdempotencyOperation.operation_type == "session_logout"
                )
            )
        )
        assert len(operations) == 1
        assert operations[0].status == "completed"


def test_authentication_idempotency_ledger_serialization_contains_no_raw_secrets(
    api_world: ApiWorld,
):
    device_id = "formal-ledger-secret-device-0001"
    raw_login_key = "formal-ledger-secret-login-key-0001"
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-ledger-secret-request-0001",
        idempotency_key="formal-ledger-secret-request-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text
    login = _login_sms_miniprogram(
        api_world,
        device_id=device_id,
        request_id="auth-api-ledger-secret-login-0001",
        idempotency_key=raw_login_key,
    )
    assert login.status_code == 200, login.text

    with api_world.session_factory() as db:
        operations = list(db.scalars(select(AuthIdempotencyOperation)))
        assert len(operations) == 1
        assert operations[0].response_ciphertext is not None
        assert operations[0].response_nonce is not None
        assert operations[0].response_sha256 is not None
        evidence = _idempotency_evidence_document(db)

    for plaintext in (
        MOBILE,
        SMS_CODE,
        device_id,
        CLIENT_IP,
        raw_login_key,
        login.json()["access_token"],
        login.json()["refresh_token"],
    ):
        assert plaintext not in evidence
    assert {
        "idempotency_key_hash",
        "scope_hash",
        "request_hmac",
        "response_ciphertext",
        "response_nonce",
        "response_sha256",
    }.issubset(AuthIdempotencyOperation.__table__.columns.keys())
    assert {
        "idempotency_key",
        "request_payload",
        "response_payload",
        "mobile",
        "code",
        "device_id",
        "ip_address",
        "refresh_token",
    }.isdisjoint(AuthIdempotencyOperation.__table__.columns.keys())


def test_kms_unavailable_fails_before_code_verification_or_session_mutation(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-kms-unavailable-request-0001",
        idempotency_key="formal-kms-unavailable-request-key-0001",
        client_type="miniprogram",
    )
    assert requested.status_code == 200, requested.text

    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        lambda: create_configured_authentication_response_cipher(
            settings=auth.settings,
            kms_key_loader=None,
        ),
    )
    response = _login_sms_miniprogram(
        api_world,
        device_id="formal-kms-unavailable-device-0001",
        request_id="auth-api-kms-unavailable-login-0001",
        idempotency_key="formal-kms-unavailable-login-key-0001",
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"]["code"]
        == "authentication_idempotency_encryption_unavailable"
    )
    assert api_world.sms_provider.verify_calls == []
    with api_world.session_factory() as db:
        challenge = db.scalar(select(LoginChallenge))
        assert challenge is not None
        assert challenge.status == "pending"
        assert challenge.attempts == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0
        assert (
            db.scalar(select(func.count()).select_from(AuthIdempotencyOperation))
            == 0
        )


def test_failed_login_has_no_committed_pending_gap_before_terminal_envelope(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    raw_key = "formal-failure-atomicity-key-0001"
    original_complete = auth._complete_formal_authentication_failure

    def crash_before_terminal_commit(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="simulated process stop")

    monkeypatch.setattr(
        auth,
        "_complete_formal_authentication_failure",
        crash_before_terminal_commit,
    )
    interrupted = _login_sms_miniprogram(
        api_world,
        mobile=OTHER_MOBILE,
        request_id="auth-api-failure-atomicity-interrupted-0001",
        idempotency_key=raw_key,
    )
    assert interrupted.status_code == 503
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert (
            db.scalar(select(func.count()).select_from(AuthIdempotencyOperation))
            == 0
        )

    monkeypatch.setattr(
        auth,
        "_complete_formal_authentication_failure",
        original_complete,
    )
    retried = _login_sms_miniprogram(
        api_world,
        mobile=OTHER_MOBILE,
        request_id="auth-api-failure-atomicity-retry-0002",
        idempotency_key=raw_key,
    )
    assert retried.status_code == 401
    assert retried.headers["Idempotency-Replayed"] == "false"
    with api_world.session_factory() as db:
        operation = db.scalar(select(AuthIdempotencyOperation))
        assert operation is not None and operation.status == "failed"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_web_refresh_failure_and_cached_replay_delete_both_session_cookies(
    api_world: ApiWorld,
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-web-cookie-request-0001",
        idempotency_key="formal-web-cookie-request-key-0001",
    )
    assert requested.status_code == 200, requested.text
    login = _login_sms_web(
        api_world,
        request_id="auth-api-web-cookie-login-0001",
        idempotency_key="formal-web-cookie-login-key-0001",
    )
    assert login.status_code == 200, login.text
    stale_access_token = api_world.client.cookies.get("access_token")
    assert stale_access_token

    raw_token = "unknown-web-refresh-token-material-00000000000000000001"
    raw_key = "formal-web-refresh-failure-key-0001"
    api_world.client.cookies.set(
        "refresh_token", raw_token, domain="testserver.local", path="/"
    )

    first = api_world.client.post(
        "/api/auth/refresh",
        headers=_formal_mutation_headers(
            request_id="auth-api-web-refresh-failure-first-0001",
            idempotency_key=raw_key,
        ),
    )
    assert first.status_code == 401
    assert first.headers["Idempotency-Replayed"] == "false"
    assert first.headers["X-Auth-Credentials-Cleared"] == "true"
    first_cookie_headers = first.headers.get_list("set-cookie")
    assert len(first_cookie_headers) == 2
    assert any("access_token=" in value for value in first_cookie_headers)
    assert any("refresh_token=" in value for value in first_cookie_headers)
    assert all("Max-Age=0" in value for value in first_cookie_headers)
    assert api_world.client.cookies.get("access_token") is None
    assert api_world.client.cookies.get("refresh_token") is None
    assert api_world.client.get("/api/auth/me").status_code == 401

    api_world.client.cookies.set(
        "access_token", stale_access_token, domain="testserver.local", path="/"
    )
    api_world.client.cookies.set(
        "refresh_token", raw_token, domain="testserver.local", path="/"
    )
    replay = api_world.client.post(
        "/api/auth/refresh",
        headers=_formal_mutation_headers(
            request_id="auth-api-web-refresh-failure-replay-0002",
            idempotency_key=raw_key,
        ),
    )
    assert replay.status_code == 401
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.headers["X-Auth-Credentials-Cleared"] == "true"
    replay_cookie_headers = replay.headers.get_list("set-cookie")
    assert len(replay_cookie_headers) == 2
    assert any("access_token=" in value for value in replay_cookie_headers)
    assert any("refresh_token=" in value for value in replay_cookie_headers)
    assert all("Max-Age=0" in value for value in replay_cookie_headers)
    assert api_world.client.cookies.get("access_token") is None
    assert api_world.client.cookies.get("refresh_token") is None
    assert api_world.client.get("/api/auth/me").status_code == 401
    with api_world.session_factory() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "authentication.session.refresh_failed")
            )
            == 1
        )


def test_web_logout_kms_failure_clears_local_session_but_does_not_claim_revoke(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    requested = _request_sms(
        api_world,
        request_id="auth-api-web-logout-kms-request-0001",
        idempotency_key="formal-web-logout-kms-request-key-0001",
    )
    assert requested.status_code == 200, requested.text
    login = _login_sms_web(
        api_world,
        request_id="auth-api-web-logout-kms-login-0001",
        idempotency_key="formal-web-logout-kms-login-key-0001",
    )
    assert login.status_code == 200, login.text
    with api_world.session_factory() as db:
        session_id = db.scalar(select(AuthSession.id))
    assert session_id is not None
    assert api_world.client.cookies.get("access_token")
    assert api_world.client.cookies.get("refresh_token")
    assert api_world.client.cookies.get("auth_device_id")

    monkeypatch.setattr(
        auth,
        "_configured_authentication_response_cipher",
        lambda: create_configured_authentication_response_cipher(
            settings=auth.settings,
            kms_key_loader=None,
        ),
    )
    failed = api_world.client.post(
        "/api/auth/logout",
        headers=_formal_mutation_headers(
            request_id="auth-api-web-logout-kms-failed-0001",
            idempotency_key="formal-web-logout-kms-failed-key-0001",
        ),
    )

    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "authentication_kms_required"
    assert failed.headers["X-Auth-Credentials-Cleared"] == "true"
    cookie_headers = failed.headers.get_list("set-cookie")
    assert len(cookie_headers) == 2
    assert any("access_token=" in value for value in cookie_headers)
    assert any("refresh_token=" in value for value in cookie_headers)
    assert all("Max-Age=0" in value for value in cookie_headers)
    assert api_world.client.cookies.get("access_token") is None
    assert api_world.client.cookies.get("refresh_token") is None
    assert api_world.client.cookies.get("auth_device_id")
    assert api_world.client.get("/api/auth/me").status_code == 401
    with api_world.session_factory() as db:
        session = db.get(AuthSession, session_id)
        assert session is not None and session.revoked_at is None


def test_production_authentication_mutations_reject_missing_trace_headers(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    wechat_provider = FakeWechatProvider(openid="header-test-openid")
    monkeypatch.setattr(auth, "get_wechat_provider", lambda: wechat_provider)

    no_request_id = api_world.client.post(
        "/api/auth/sms/request",
        json={"mobile": MOBILE},
    )
    assert no_request_id.status_code == 422
    assert no_request_id.json()["detail"] == "缺少有效的请求标识"

    no_idempotency_key = api_world.client.post(
        "/api/auth/sms/request",
        json={"mobile": MOBILE},
        headers={"X-Request-ID": "auth-api-header-test-0001"},
    )
    assert no_idempotency_key.status_code == 422
    assert no_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    no_login_request_id = api_world.client.post(
        "/api/auth/sms/login",
        json={"mobile": MOBILE, "code": SMS_CODE},
    )
    assert no_login_request_id.status_code == 422
    assert no_login_request_id.json()["detail"] == "缺少有效的请求标识"

    no_login_idempotency_key = api_world.client.post(
        "/api/auth/sms/login",
        json={"mobile": MOBILE, "code": SMS_CODE},
        headers={"X-Request-ID": "auth-api-header-login-idem-0001"},
    )
    assert no_login_idempotency_key.status_code == 422
    assert no_login_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    no_wechat_request_id = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json={
            "login_code": "header-test-login-code",
            "device_id": "header-test-device-0001",
        },
    )
    assert no_wechat_request_id.status_code == 422
    assert no_wechat_request_id.json()["detail"] == "缺少有效的请求标识"

    no_wechat_idempotency_key = api_world.client.post(
        "/api/auth/miniprogram/wechat-login",
        json={
            "login_code": "header-test-login-code",
            "device_id": "header-test-device-0001",
        },
        headers={"X-Request-ID": "auth-api-header-wechat-idem-0001"},
    )
    assert no_wechat_idempotency_key.status_code == 422
    assert no_wechat_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    no_refresh_request_id = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json={
            "refresh_token": "x" * 40,
            "device_id": "header-test-device-0001",
        },
    )
    assert no_refresh_request_id.status_code == 422
    assert no_refresh_request_id.json()["detail"] == "缺少有效的请求标识"

    no_refresh_idempotency_key = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json={
            "refresh_token": "x" * 40,
            "device_id": "header-test-device-0001",
        },
        headers={"X-Request-ID": "auth-api-header-refresh-idem-0001"},
    )
    assert no_refresh_idempotency_key.status_code == 422
    assert no_refresh_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    no_web_refresh_idempotency_key = api_world.client.post(
        "/api/auth/refresh",
        headers={"X-Request-ID": "auth-api-header-web-refresh-idem-0001"},
    )
    assert no_web_refresh_idempotency_key.status_code == 422
    assert no_web_refresh_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    no_logout_idempotency_key = api_world.client.post(
        "/api/auth/miniprogram/logout",
        json={"refresh_token": "x" * 40},
        headers={"X-Request-ID": "auth-api-header-logout-idem-0001"},
    )
    assert no_logout_idempotency_key.status_code == 422
    assert no_logout_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    no_web_logout_idempotency_key = api_world.client.post(
        "/api/auth/logout",
        headers={"X-Request-ID": "auth-api-header-web-logout-idem-0001"},
    )
    assert no_web_logout_idempotency_key.status_code == 422
    assert no_web_logout_idempotency_key.json()["detail"] == "缺少有效的幂等键"

    assert api_world.sms_provider.send_calls == []
    assert api_world.sms_provider.verify_calls == []
    assert wechat_provider.login_calls == []
    assert wechat_provider.phone_calls == []
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0


def test_production_refresh_rejects_missing_peer_ip_before_idempotency_write(
    api_world: ApiWorld,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth, "client_ip", lambda _request: "")

    def forbidden_idempotency_begin(*_args, **_kwargs):
        raise AssertionError("IP preflight must run before idempotency mutation")

    monkeypatch.setattr(
        auth,
        "_begin_formal_authentication_write",
        forbidden_idempotency_begin,
    )

    response = api_world.client.post(
        "/api/auth/miniprogram/refresh",
        json={
            "refresh_token": "x" * 40,
            "device_id": "missing-peer-ip-device",
        },
        headers=_formal_mutation_headers(
            request_id="auth-api-missing-peer-ip-0001",
            idempotency_key="auth-api-missing-peer-ip-key-0001",
        ),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "认证服务暂时不可用"
    with api_world.session_factory() as db:
        assert db.scalar(
            select(func.count()).select_from(AuthIdempotencyOperation)
        ) == 0
        assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0


def test_hq_national_admin_lists_only_redacted_formal_sessions(
    api_world: ApiWorld,
):
    world = _seed_session_admin_world(api_world)

    response = api_world.client.get(
        "/api/auth/sessions",
        headers=_admin_headers(world),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload) == 2
    _assert_session_response_is_redacted(payload)
    expected_fields = {
        "session_id",
        "person_id",
        "person_name",
        "employee_no",
        "organization_code",
        "organization_name",
        "account_status",
        "client_type",
        "device_name",
        "created_at",
        "last_seen_at",
        "expires_at",
        "is_current",
    }
    assert all(set(item) == expected_fields for item in payload)
    by_session = {item["session_id"]: item for item in payload}
    assert by_session[world.admin_session_id]["is_current"] is True
    assert by_session[world.target_session_id]["is_current"] is False
    assert by_session[world.target_session_id]["person_id"] == str(
        world.target.person_id
    )
    assert by_session[world.target_session_id]["person_name"] == "认证接口测试工程师"
    assert by_session[world.target_session_id]["employee_no"].startswith("EMP-AUTH-")
    for sensitive in (
        world.admin.user_id,
        world.target.user_id,
        ADMIN_MOBILE,
        MOBILE,
        world.old_target_refresh_token,
        world.current_target_refresh_token,
        CLIENT_IP,
    ):
        assert sensitive not in response.text


@pytest.mark.parametrize(
    "trace_headers",
    [
        {"Idempotency-Key": "session-admin-header-key-0001"},
        {"X-Request-ID": "session-admin-header-request-0001"},
        {
            "X-Request-ID": "1-invalid-request-id",
            "Idempotency-Key": "session-admin-header-key-0002",
        },
        {
            "X-Request-ID": "session-admin-header-request-0002",
            "Idempotency-Key": "too-short",
        },
    ],
)
def test_admin_session_revoke_rejects_missing_or_invalid_trace_headers(
    api_world: ApiWorld,
    trace_headers: dict[str, str],
):
    world = _seed_session_admin_world(api_world)
    headers = {
        "Authorization": f"Bearer {world.admin_access_token}",
        **trace_headers,
    }

    response = api_world.client.post(
        f"/api/auth/sessions/{world.target_session_id}/revoke",
        headers=headers,
    )

    assert response.status_code == 422, response.text
    with api_world.session_factory() as db:
        auth_session = db.get(AuthSession, world.target_session_id)
        assert auth_session is not None and auth_session.revoked_at is None
        current_history = db.scalar(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash
                == hash_refresh_token(world.current_target_refresh_token)
            )
        )
        assert current_history is not None and current_history.revoked_at is None
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_hq_admin_revoke_commits_family_audit_state_and_redacted_response(
    api_world: ApiWorld,
):
    world = _seed_session_admin_world(api_world)
    raw_key = "session-admin-force-revoke-key-0001"
    request_id = "session-admin-force-revoke-request-0001"

    response = api_world.client.post(
        f"/api/auth/sessions/{world.target_session_id}/revoke",
        headers=_admin_headers(
            world,
            request_id=request_id,
            idempotency_key=raw_key,
        ),
    )

    assert response.status_code == 200, response.text
    assert response.headers["Idempotency-Replayed"] == "false"
    assert set(response.json()) == {
        "session_id",
        "status",
        "revoked_at",
        "replayed",
        "audit_event_id",
        "state_transition_event_id",
    }
    assert response.json()["session_id"] == world.target_session_id
    assert response.json()["status"] == "revoked"
    response_revoked_at = datetime.fromisoformat(response.json()["revoked_at"])
    assert response_revoked_at.tzinfo is not None
    assert response_revoked_at.utcoffset() is not None
    assert response.json()["replayed"] is False
    _assert_session_response_is_redacted(response.json())
    for sensitive in (
        world.target.user_id,
        world.target.mobile,
        world.old_target_refresh_token,
        world.current_target_refresh_token,
        raw_key,
        CLIENT_IP,
    ):
        assert sensitive not in response.text

    with api_world.session_factory() as db:
        auth_session = db.get(AuthSession, world.target_session_id)
        assert auth_session is not None and auth_session.revoked_at is not None
        assert auth_session.revoked_by_id == world.admin.user_id
        stored_revoked_at = auth_session.revoked_at
        if stored_revoked_at.tzinfo is None:
            stored_revoked_at = stored_revoked_at.replace(tzinfo=timezone.utc)
        assert response_revoked_at.astimezone(
            timezone.utc
        ) == stored_revoked_at.astimezone(timezone.utc)
        histories = list(
            db.scalars(
                select(AuthRefreshToken).where(
                    AuthRefreshToken.session_id == world.target_session_id
                )
            )
        )
        assert len(histories) == 2
        assert all(
            row.revoked_at is not None
            for row in histories
            if row.consumed_at is None
        )
        assert any(
            row.consumed_at is not None and row.revoked_at is None
            for row in histories
        )

        audit = db.get(AuditEvent, uuid.UUID(response.json()["audit_event_id"]))
        transition = db.get(
            StateTransitionEvent,
            uuid.UUID(response.json()["state_transition_event_id"]),
        )
        assert audit is not None
        assert audit.actor_user_id == world.admin.user_id
        assert audit.action == "authentication.session.admin_revoked"
        assert audit.aggregate_id == world.target_session_id
        assert audit.before_jsonb == {"status": "active"}
        assert audit.after_jsonb == {
            "client_type": "miniprogram",
            "outcome": "revoked",
            "reason_code": "admin_force_logout",
            "status": "revoked",
        }
        assert audit.request_id.startswith("authreq-")
        assert request_id not in audit.request_id
        assert transition is not None
        assert transition.actor_id == world.admin.user_id
        assert (transition.from_status, transition.to_status) == (
            "active",
            "revoked",
        )
        assert transition.reason == "admin_force_logout"
        assert transition.idempotency_key == idempotency_storage_key(
            world.admin.user_id,
            raw_key,
        )
        assert raw_key not in json.dumps(
            transition.metadata_jsonb,
            ensure_ascii=False,
            sort_keys=True,
        )
        head = db.scalar(
            select(AuditChainHead).where(
                AuditChainHead.stream_key == "authentication"
            )
        )
        assert head is not None and head.version == 1

    target_access = api_world.client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {world.target_access_token}"},
    )
    assert target_access.status_code == 401
    sessions_after = api_world.client.get(
        "/api/auth/sessions",
        headers=_admin_headers(world),
    )
    assert sessions_after.status_code == 200
    assert [item["session_id"] for item in sessions_after.json()] == [
        world.admin_session_id
    ]


def test_admin_session_revoke_replays_same_key_and_new_key_rejects_revoked_target(
    api_world: ApiWorld,
):
    world = _seed_session_admin_world(api_world)
    raw_key = "session-admin-idempotent-revoke-key-0001"
    first = api_world.client.post(
        f"/api/auth/sessions/{world.target_session_id}/revoke",
        headers=_admin_headers(
            world,
            request_id="session-admin-idempotent-first-0001",
            idempotency_key=raw_key,
        ),
    )
    assert first.status_code == 200, first.text

    replay = api_world.client.post(
        f"/api/auth/sessions/{world.target_session_id}/revoke",
        headers=_admin_headers(
            world,
            request_id="session-admin-idempotent-replay-0002",
            idempotency_key=raw_key,
        ),
    )
    assert replay.status_code == 200, replay.text
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["replayed"] is True
    for field in (
        "session_id",
        "status",
        "audit_event_id",
        "state_transition_event_id",
    ):
        assert replay.json()[field] == first.json()[field]

    different_key = api_world.client.post(
        f"/api/auth/sessions/{world.target_session_id}/revoke",
        headers=_admin_headers(
            world,
            request_id="session-admin-new-key-retry-0001",
            idempotency_key="session-admin-new-key-retry-0001",
        ),
    )
    assert different_key.status_code == 412, different_key.text
    assert different_key.json()["detail"]["code"] == "session_already_revoked"
    with api_world.session_factory() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
        assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 1


def test_non_admin_is_denied_session_list_and_force_revoke(
    api_world: ApiWorld,
):
    world = _seed_session_admin_world(api_world)
    technician_headers = {
        "Authorization": f"Bearer {world.target_access_token}",
    }

    listed = api_world.client.get(
        "/api/auth/sessions",
        headers=technician_headers,
    )
    revoked = api_world.client.post(
        f"/api/auth/sessions/{world.admin_session_id}/revoke",
        headers={
            **technician_headers,
            "X-Request-ID": "session-admin-technician-denied-0001",
            "Idempotency-Key": "session-admin-technician-key-0001",
        },
    )

    assert listed.status_code == 403
    assert revoked.status_code == 403
    with api_world.session_factory() as db:
        admin_session = db.get(AuthSession, world.admin_session_id)
        assert admin_session is not None and admin_session.revoked_at is None
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_hq_admin_without_auth_session_manage_permission_is_denied(
    api_world: ApiWorld,
):
    world = _seed_session_admin_world(api_world)
    with api_world.session_factory() as db:
        permission_grant = db.scalar(
            select(RolePermission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .where(
                Permission.resource == "auth_session",
                Permission.action == "manage",
            )
        )
        assert permission_grant is not None
        db.delete(permission_grant)
        db.commit()

    listed = api_world.client.get(
        "/api/auth/sessions",
        headers=_admin_headers(world),
    )
    revoked = api_world.client.post(
        f"/api/auth/sessions/{world.target_session_id}/revoke",
        headers=_admin_headers(
            world,
            request_id="session-admin-permission-denied-0001",
            idempotency_key="session-admin-permission-denied-key-0001",
        ),
    )

    assert listed.status_code == 403
    assert revoked.status_code == 403
    with api_world.session_factory() as db:
        target_session = db.get(AuthSession, world.target_session_id)
        assert target_session is not None and target_session.revoked_at is None
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
        assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0
