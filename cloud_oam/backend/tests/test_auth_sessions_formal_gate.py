from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import auth_sessions
from app.auth_sessions import (
    SessionError,
    create_session,
    rotate_session,
    validate_active_production_session_ip_evidence,
)
from app.database import Base
from app.formal_access import load_formal_principal
from app.foundation_models import (
    AuthIdentity,
    Organization,
    Person,
    Role,
    RoleAssignment,
)
from app.models import AuthSession, User


GENERIC_FORMAL_SESSION_ERROR = "账号当前不可建立正式会话"
SESSION_IP = "198.51.100.80"
SESSION_IP_SECRET = "formal-session-ip-gate-secret-at-least-32-characters"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def _session_ip_hash_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        auth_sessions.settings,
        "identity_hash_secret",
        SESSION_IP_SECRET,
    )
    monkeypatch.setattr(auth_sessions.settings, "identity_hash_version", 1)


def _legacy_user(db: Session, *, mobile: str = "13800000001") -> User:
    user = User(
        mobile=mobile,
        name="兼容账号",
        password_hash="disabled",
        role="technician",
        province="江苏省",
        is_active=True,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    return user


def _formal_technician(
    db: Session,
) -> tuple[User, Person, AuthIdentity, RoleAssignment]:
    now = datetime.now(timezone.utc)
    organization = Organization(
        code="ORG-JS-SESSION",
        name="江苏区域",
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    db.add(organization)
    db.flush()

    person = Person(
        organization_id=organization.id,
        employee_no="EMP-SESSION-001",
        name="会话测试工程师",
        employment_status="active",
    )
    db.add(person)
    db.flush()

    user = User(
        person_id=person.id,
        account_status="active",
        mobile="13800000002",
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

    identity = AuthIdentity(
        user_id=user.id,
        identity_type="mobile",
        provider_key="aliyun",
        identifier_hash="a" * 64,
        hash_version=1,
        verified_at=now,
        status="active",
    )
    assignment = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        scope_type="person",
        scope_id=str(person.id),
        valid_from=now - timedelta(minutes=1),
        status="active",
        assigned_by=user.id,
        reason="test fixture",
    )
    db.add_all([identity, assignment])
    db.flush()
    return user, person, identity, assignment


def test_production_create_fails_before_token_for_legacy_only_account(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user = _legacy_user(db)

    def forbidden_token_generation() -> str:
        raise AssertionError("formal principal gate must run before token generation")

    monkeypatch.setattr(
        auth_sessions,
        "create_refresh_token",
        forbidden_token_generation,
    )

    with pytest.raises(SessionError) as error:
        create_session(
            db,
            user,
            client_type="web",
            device_id="legacy-device",
            ip_address=SESSION_IP,
        )

    assert str(error.value) == GENERIC_FORMAL_SESSION_ERROR
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
    assert user.last_login_at is None


def test_production_create_accepts_complete_formal_principal(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, _, _, _ = _formal_technician(db)

    tokens = create_session(
        db,
        user,
        client_type="miniprogram",
        device_id="formal-device",
        ip_address=SESSION_IP,
    )

    assert tokens.auth_session.user_id == user.id
    assert tokens.refresh_token
    assert tokens.access_token
    assert user.last_login_at is not None


def test_create_acquires_device_family_guard_before_generating_token(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, _, _, _ = _formal_technician(db)
    guarded: list[tuple[str, str, str]] = []
    original_token_factory = auth_sessions.create_refresh_token

    def record_guard(_db, *, user_id: str, client_type: str, device_id: str):
        guarded.append((user_id, client_type, device_id))

    def guarded_token() -> str:
        assert guarded == [(user.id, "web", "guarded-device")]
        return original_token_factory()

    monkeypatch.setattr(auth_sessions, "_acquire_device_session_lock", record_guard)
    monkeypatch.setattr(auth_sessions, "create_refresh_token", guarded_token)

    tokens = create_session(
        db,
        user,
        client_type="web",
        device_id="guarded-device",
        ip_address=SESSION_IP,
    )

    assert tokens.auth_session.device_id == "guarded-device"


def test_postgresql_device_family_guard_uses_only_a_stable_signed_lock_key():
    class PostgreSQLBind:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class RecordingSession:
        def __init__(self):
            self.calls: list[tuple[str, dict[str, int]]] = []

        def get_bind(self):
            return PostgreSQLBind()

        def execute(self, statement, parameters):
            self.calls.append((str(statement), parameters))

    first = RecordingSession()
    second = RecordingSession()
    auth_sessions._acquire_device_session_lock(
        first,  # type: ignore[arg-type]
        user_id="user-guard-1",
        client_type="web",
        device_id="device-guard-1",
    )
    auth_sessions._acquire_device_session_lock(
        second,  # type: ignore[arg-type]
        user_id="user-guard-1",
        client_type="web",
        device_id="device-guard-1",
    )

    assert first.calls == second.calls
    assert first.calls[0][0] == "SELECT pg_advisory_xact_lock(:lock_key)"
    assert set(first.calls[0][1]) == {"lock_key"}
    lock_key = first.calls[0][1]["lock_key"]
    assert -(2**63) <= lock_key < 2**63


def test_production_refresh_reloads_and_rejects_revoked_role_assignment(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, _, _, assignment = _formal_technician(db)
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="role-device",
        ip_address=SESSION_IP,
    )
    original_hash = issued.auth_session.refresh_token_hash

    assignment.status = "revoked"
    assignment.revoked_at = datetime.now(timezone.utc)
    assignment.revoked_by = user.id
    db.flush()

    def forbidden_token_generation() -> str:
        raise AssertionError("revoked authorization must be checked before rotation")

    monkeypatch.setattr(
        auth_sessions,
        "create_refresh_token",
        forbidden_token_generation,
    )

    with pytest.raises(SessionError) as error:
        rotate_session(
            db,
            issued.refresh_token,
            device_id="role-device",
            ip_address=SESSION_IP,
        )

    assert str(error.value) == GENERIC_FORMAL_SESSION_ERROR
    assert issued.auth_session.refresh_token_hash == original_hash


def test_production_refresh_rejects_removed_person_binding(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, _, _, _ = _formal_technician(db)
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="person-device",
        ip_address=SESSION_IP,
    )
    original_hash = issued.auth_session.refresh_token_hash

    user.person_id = None
    db.flush()

    with pytest.raises(SessionError) as error:
        rotate_session(
            db,
            issued.refresh_token,
            device_id="person-device",
            ip_address=SESSION_IP,
        )

    assert str(error.value) == GENERIC_FORMAL_SESSION_ERROR
    assert issued.auth_session.refresh_token_hash == original_hash


def test_left_person_keeps_only_restricted_handover_session(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, person, _, _ = _formal_technician(db)
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="handover-device",
        ip_address=SESSION_IP,
    )

    person.employment_status = "left"
    user.account_status = "restricted_handover"
    # The V1 formal statuses supersede this v0.9 compatibility flag in
    # production.  It must not remove the explicitly retained handover login.
    user.is_active = False
    db.flush()

    _, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="handover-device",
        ip_address=SESSION_IP,
    )

    principal = load_formal_principal(db, user.id)
    assert rotated.refresh_token != issued.refresh_token
    assert principal.access_mode == "restricted_handover"


def test_startup_ip_evidence_preflight_rejects_active_plaintext_but_keeps_revoked_history(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, _, _, _ = _formal_technician(db)
    issued = create_session(
        db,
        user,
        client_type="web",
        device_id="startup-ip-evidence-device",
        ip_address=SESSION_IP,
    )
    issued.auth_session.ip_address = "198.51.100.99"
    db.flush()

    with pytest.raises(SessionError, match="不兼容的活跃正式会话证据"):
        validate_active_production_session_ip_evidence(
            db,
            hash_version=1,
        )

    assert issued.auth_session.ip_address == "198.51.100.99"
    assert issued.auth_session.revoked_at is None

    issued.auth_session.revoked_at = datetime.now(timezone.utc)
    db.flush()
    validate_active_production_session_ip_evidence(db, hash_version=1)
    assert issued.auth_session.ip_address == "198.51.100.99"


@pytest.mark.parametrize("account_status", ["suspended", "disabled"])
def test_production_rejects_suspended_or_disabled_formal_account(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    account_status: str,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "production")
    user, _, _, _ = _formal_technician(db)
    user.account_status = account_status
    db.flush()

    with pytest.raises(SessionError) as error:
        create_session(
            db,
            user,
            client_type="web",
            device_id="blocked-device",
            ip_address=SESSION_IP,
        )

    assert str(error.value) == GENERIC_FORMAL_SESSION_ERROR
    assert db.scalar(select(func.count()).select_from(AuthSession)) == 0


def test_nonproduction_keeps_legacy_session_compatibility(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(auth_sessions.settings, "environment", "test")
    user = _legacy_user(db, mobile="13800000003")

    issued = create_session(db, user, client_type="web", device_id="legacy-device")
    rotated_user, rotated = rotate_session(
        db,
        issued.refresh_token,
        device_id="legacy-device",
    )

    assert rotated_user.id == user.id
    assert rotated.refresh_token != issued.refresh_token
    assert user.last_login_at is None
