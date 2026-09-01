from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app import auth_sessions
from app.auth_sessions import (
    RefreshTokenReplayError,
    SessionError,
    create_session,
    hash_login_challenge_ip_address,
    protect_session_ip_address,
    revoke_by_refresh_token,
    revoke_session_family,
    rotate_session,
)
from app.database import Base
from app.foundation_models import (
    AuthIdentity,
    AuthRefreshToken,
    Organization,
    Person,
    Role,
    RoleAssignment,
)
from app.models import AuthSession, User
from app.security import hash_refresh_token


SESSION_IP = "198.51.100.80"
SESSION_IP_SECRET = "session-ip-protection-test-secret-at-least-32-characters"


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


@pytest.fixture(autouse=True)
def _session_ip_hash_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_secret",
        SESSION_IP_SECRET,
    )
    monkeypatch.setattr(auth_sessions.settings, "identity_hash_version", 1)


def _legacy_user(db: Session, *, mobile: str = "13800000801") -> User:
    user = User(
        mobile=mobile,
        name="会话兼容测试人员",
        password_hash="disabled",
        role="technician",
        province="江苏省",
        is_active=True,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    return user


def _formal_user(db: Session, *, suffix: str = "01") -> User:
    now = datetime.now(timezone.utc)
    organization = Organization(
        code=f"ORG-AUTH-REFRESH-{suffix}",
        name=f"会话测试区域{suffix}",
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    db.add(organization)
    db.flush()
    person = Person(
        organization_id=organization.id,
        employee_no=f"EMP-AUTH-REFRESH-{suffix}",
        name=f"会话测试工程师{suffix}",
        employment_status="active",
    )
    db.add(person)
    db.flush()
    user = User(
        person_id=person.id,
        account_status="active",
        mobile=f"138000009{suffix}",
        name=person.name,
        password_hash="disabled",
        role="technician",
        province="江苏省",
        is_active=True,
        require_password_change=False,
    )
    role = Role(
        code="technician",
        name="工程师",
        is_external=False,
        status="active",
    )
    db.add_all([user, role])
    db.flush()
    db.add_all(
        [
            AuthIdentity(
                user_id=user.id,
                identity_type="mobile",
                provider_key="aliyun",
                identifier_hash=(suffix * 64)[:64],
                hash_version=1,
                verified_at=now,
                status="active",
            ),
            RoleAssignment(
                user_id=user.id,
                role_id=role.id,
                scope_type="person",
                scope_id=str(person.id),
                valid_from=now - timedelta(minutes=1),
                status="active",
                assigned_by=user.id,
                reason="refresh lifecycle test",
            ),
        ]
    )
    db.flush()
    return user


def _legacy_session(db: Session, user: User, raw_token: str) -> AuthSession:
    now = datetime.now(timezone.utc)
    row = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(raw_token),
        client_type="miniprogram",
        device_id="legacy-device",
        device_name="旧会话",
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=1),
    )
    db.add(row)
    db.flush()
    return row


def test_new_session_records_refresh_history_in_callers_transaction(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "test")
    user = _legacy_user(db)
    db.commit()

    issued = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="formal-history-device",
    )
    history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
        )
    )

    assert history is not None
    assert history.session_id == issued.auth_session.id
    assert history.consumed_at is None
    assert history.revoked_at is None
    assert history.replaced_by_id is None

    db.rollback()
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
    assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0


def test_production_rotation_consumes_once_and_links_replacement(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db)
    issued = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="rotation-device",
        ip_address=SESSION_IP,
    )
    old_history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
        )
    )
    assert old_history is not None

    rotated_user, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="rotation-device",
        ip_address=SESSION_IP,
    )
    new_history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(rotated.refresh_token)
        )
    )

    assert rotated_user.id == user.id
    assert rotated.refresh_token != issued.refresh_token
    assert old_history.consumed_at is not None
    assert new_history is not None
    assert old_history.replaced_by_id == new_history.id
    assert new_history.consumed_at is None
    assert issued.auth_session.refresh_token_hash == new_history.token_hash


def test_production_session_never_persists_plaintext_ip(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_secret",
        "session-ip-protection-test-secret-at-least-32-characters",
    )
    monkeypatch.setattr(auth_sessions.settings, "identity_hash_version", 7)
    user = _formal_user(db, suffix="31")
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="protected-ip-device",
        ip_address="198.51.100.44",
    )

    canonical = json.dumps(
        [7, "auth_session_ip", "198.51.100.44"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    expected_digest = hmac.new(
        auth_sessions.settings.identity_hash_secret.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    assert issued.auth_session.ip_address == f"hmac:7:{expected_digest}"
    assert "198.51.100.44" not in issued.auth_session.ip_address

    _, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="protected-ip-device",
        ip_address="203.0.113.8",
    )
    assert rotated.auth_session.ip_address.startswith("hmac:7:")
    assert "203.0.113.8" not in rotated.auth_session.ip_address


def test_production_session_ip_hash_is_canonical_and_version_bounded(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_secret",
        "session-ip-protection-test-secret-at-least-32-characters",
    )
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_version",
        2_147_483_647,
    )

    expanded = protect_session_ip_address("2001:0db8:0:0:0:0:0:1")
    canonical = protect_session_ip_address("2001:db8::1")

    assert expanded == canonical
    assert expanded.startswith("hmac:2147483647:")
    assert len(expanded) == 80

    with pytest.raises(SessionError, match="会话来源地址无效"):
        protect_session_ip_address("hmac:2147483647:" + "a" * 64)


def test_production_session_and_login_challenge_ip_domains_are_independent(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_secret",
        "session-ip-protection-test-secret-at-least-32-characters",
    )
    monkeypatch.setattr(auth_sessions.settings, "identity_hash_version", 7)

    session_evidence = protect_session_ip_address("198.51.100.44")
    challenge_evidence = hash_login_challenge_ip_address("198.51.100.44")

    assert len(challenge_evidence) == 64
    assert session_evidence.rsplit(":", 1)[1] != challenge_evidence


def test_production_invalid_session_ip_hash_config_fails_before_mutation(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_secret",
        "session-ip-protection-test-secret-at-least-32-characters",
    )
    monkeypatch.setattr(auth_sessions.settings, "identity_hash_version", 1)
    user = _formal_user(db, suffix="32")
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="protected-ip-fail-closed-device",
        ip_address="198.51.100.44",
    )
    db.flush()
    history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
        )
    )
    assert history is not None and history.consumed_at is None
    original_last_seen_at = issued.auth_session.last_seen_at

    monkeypatch.setattr(auth_sessions.settings, "identity_hash_secret", "too-short")
    with pytest.raises(SessionError, match="会话来源证据配置无效"):
        create_session(
            db,
            user,
            client_type="web",
            device_id="protected-ip-fail-closed-device",
            ip_address="203.0.113.8",
        )
    with pytest.raises(SessionError, match="会话来源证据配置无效"):
        rotate_session(
            db,
            issued.refresh_token,
            device_id="protected-ip-fail-closed-device",
            ip_address="203.0.113.8",
        )

    assert db.scalar(select(func.count()).select_from(AuthSession)) == 1
    assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 1
    assert issued.auth_session.revoked_at is None
    assert issued.auth_session.last_seen_at == original_last_seen_at
    assert history.consumed_at is None


@pytest.mark.parametrize(
    "invalid_ip",
    ("", "not-an-ip", f"hmac:1:{'a' * 64}"),
)
def test_production_rotation_rejects_invalid_ip_before_token_mutation(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    invalid_ip: str,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db, suffix="33")
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="invalid-refresh-ip-device",
        ip_address=SESSION_IP,
    )
    history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(issued.refresh_token)
        )
    )
    assert history is not None
    original_hash = issued.auth_session.refresh_token_hash
    original_last_seen_at = issued.auth_session.last_seen_at

    with pytest.raises(SessionError, match="会话来源地址无效"):
        rotate_session(
            db,
            issued.refresh_token,
            device_id="invalid-refresh-ip-device",
            ip_address=invalid_ip,
        )

    assert history.consumed_at is None
    assert history.replaced_by_id is None
    assert issued.auth_session.refresh_token_hash == original_hash
    assert issued.auth_session.last_seen_at == original_last_seen_at
    assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 1


def test_nonproduction_session_ip_keeps_legacy_plaintext_compatibility(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "test")
    monkeypatch.setattr(auth_sessions.settings, "identity_hash_secret", "")

    assert protect_session_ip_address(" 198.51.100.44 ") == "198.51.100.44"


def test_consumed_token_replay_revokes_session_and_unfinished_family(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db)
    issued = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="replay-device",
        ip_address=SESSION_IP,
    )
    _, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="replay-device",
        ip_address=SESSION_IP,
    )
    db.commit()

    with pytest.raises(RefreshTokenReplayError) as error:
        rotate_session(
            db,
            issued.refresh_token,
            device_id="replay-device",
            ip_address=SESSION_IP,
        )
    assert error.value.session_id == issued.auth_session.id

    # The replay response is intentionally part of the still-healthy caller
    # transaction so the router can commit it before returning 401.
    db.commit()
    db.refresh(issued.auth_session)
    current_history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(rotated.refresh_token)
        )
    )
    assert issued.auth_session.revoked_at is not None
    assert current_history is not None
    assert current_history.consumed_at is None
    assert current_history.revoked_at is not None


def test_revoked_token_replay_raises_distinct_error(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db)
    issued = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="revoked-replay-device",
        ip_address=SESSION_IP,
    )
    revoked = revoke_by_refresh_token(db, issued.refresh_token)
    assert revoked is not None
    db.commit()

    with pytest.raises(RefreshTokenReplayError) as error:
        rotate_session(
            db,
            issued.refresh_token,
            device_id="revoked-replay-device",
            ip_address=SESSION_IP,
        )

    assert error.value.session_id == issued.auth_session.id
    db.commit()
    db.refresh(issued.auth_session)
    assert issued.auth_session.revoked_at is not None


def test_production_missing_refresh_history_fails_closed(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db)
    raw_token = "legacy-token-with-no-formal-history"
    auth_session = _legacy_session(db, user, raw_token)
    original_hash = auth_session.refresh_token_hash

    with pytest.raises(SessionError, match="登录已失效"):
        rotate_session(
            db,
            raw_token,
            device_id="legacy-device",
            ip_address=SESSION_IP,
        )

    assert auth_session.revoked_at is None
    assert auth_session.refresh_token_hash == original_hash
    assert db.scalar(select(func.count()).select_from(AuthRefreshToken)) == 0


def test_nonproduction_legacy_rotation_upgrades_to_formal_history(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "test")
    user = _legacy_user(db)
    raw_token = "legacy-token-retained-for-test-compatibility"
    auth_session = _legacy_session(db, user, raw_token)

    rotated_user, rotated = rotate_session(
        db,
        raw_token,
        device_id="legacy-device",
    )
    history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(rotated.refresh_token)
        )
    )

    assert rotated_user.id == user.id
    assert history is not None
    assert history.session_id == auth_session.id
    assert auth_session.refresh_token_hash == history.token_hash


def test_logout_with_rotated_old_token_revokes_entire_family(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db)
    issued = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="logout-family-device",
        ip_address=SESSION_IP,
    )
    _, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="logout-family-device",
        ip_address=SESSION_IP,
    )
    db.commit()

    revoked = revoke_by_refresh_token(
        db,
        issued.refresh_token,
        revoked_by_id=user.id,
    )
    db.commit()

    assert revoked is not None
    assert revoked.revoked_at is not None
    assert revoked.revoked_by_id == user.id
    current_history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(rotated.refresh_token)
        )
    )
    assert current_history is not None
    assert current_history.revoked_at is not None


def test_administrator_force_revoke_terminates_current_refresh_token(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _formal_user(db)
    issued = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="administrator-revoke-device",
        ip_address=SESSION_IP,
    )
    _, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="administrator-revoke-device",
        ip_address=SESSION_IP,
    )
    db.commit()

    revoked = revoke_session_family(
        db,
        issued.auth_session,
        revoked_by_id=user.id,
    )
    db.commit()

    current_history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(rotated.refresh_token)
        )
    )
    assert revoked.revoked_at is not None
    assert revoked.revoked_by_id == user.id
    assert current_history is not None
    assert current_history.consumed_at is None
    assert current_history.revoked_at is not None


def test_same_device_relogin_revokes_previous_unfinished_token(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "test")
    user = _legacy_user(db)
    first = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="same-device",
    )
    first_history = db.scalar(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == hash_refresh_token(first.refresh_token)
        )
    )
    assert first_history is not None

    second = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="same-device",
    )

    assert first.auth_session.revoked_at is not None
    assert first_history.revoked_at is not None
    assert second.auth_session.revoked_at is None
