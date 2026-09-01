from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_access import FormalPrincipal, load_formal_principal
from app.formal_services import authentication_session_admin
from app.formal_services.authentication_session_admin import (
    AuthenticationSessionAdminError,
    AuthenticationSessionRevocationResult,
    force_revoke_session,
    idempotency_storage_key,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    AuthRefreshToken,
    Organization,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
)
from app.models import AuthSession, User


NOW = datetime.now(timezone.utc)
RAW_IDEMPOTENCY_KEY = "raw-session-admin-idempotency-0001"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            AuditChainHead(
                stream_key="authentication",
                last_event_id=None,
                last_hash=None,
                version=0,
            )
        )
        session.commit()
        yield session
    engine.dispose()


@dataclass
class World:
    admin_user: User
    actor: FormalPrincipal
    permission_grant: RolePermission
    target_user: User
    auth_session: AuthSession
    consumed_token: AuthRefreshToken
    active_token: AuthRefreshToken


def _world(db: Session) -> World:
    headquarters = Organization(
        code="HQ-SESSION-ADMIN",
        name="会话管理测试总部",
        org_type="headquarters",
        status="active",
    )
    db.add(headquarters)
    db.flush()
    admin_person = Person(
        organization_id=headquarters.id,
        employee_no="EMP-SESSION-ADMIN",
        name="会话管理测试管理员",
        employment_status="active",
    )
    db.add(admin_person)
    db.flush()
    admin_user = User(
        person_id=admin_person.id,
        account_status="active",
        mobile="13900000601",
        name=admin_person.name,
        password_hash="disabled",
        role="admin",
        province=None,
        is_active=True,
        require_password_change=False,
    )
    admin_role = Role(
        code="admin",
        name="蔚来总部管理员",
        is_external=False,
        status="active",
    )
    permission = Permission(
        resource="auth_session",
        action="manage",
        field_code="",
        description="正式会话强制下线",
    )
    db.add_all([admin_user, admin_role, permission])
    db.flush()
    permission_grant = RolePermission(
        role_id=admin_role.id,
        permission_id=permission.id,
        effect="allow",
    )
    db.add_all(
        [
            AuthIdentity(
                user_id=admin_user.id,
                identity_type="mobile",
                provider_key="aliyun",
                identifier_hash="a" * 64,
                hash_version=1,
                verified_at=NOW - timedelta(days=1),
                status="active",
            ),
            RoleAssignment(
                user_id=admin_user.id,
                role_id=admin_role.id,
                scope_type="national",
                scope_id="*",
                valid_from=NOW - timedelta(days=1),
                status="active",
                assigned_by=admin_user.id,
                reason="session administration test",
            ),
            permission_grant,
        ]
    )
    target_user = User(
        mobile="13800000602",
        name="被强制下线人员",
        password_hash="disabled",
        role="technician",
        province="江苏省",
        is_active=True,
        require_password_change=False,
    )
    db.add(target_user)
    db.flush()
    auth_session = _session(db, target_user)
    consumed_token = AuthRefreshToken(
        session_id=auth_session.id,
        token_hash="1" * 64,
        issued_at=NOW - timedelta(hours=2),
        consumed_at=NOW - timedelta(hours=1),
    )
    active_token = AuthRefreshToken(
        session_id=auth_session.id,
        token_hash="2" * 64,
        issued_at=NOW - timedelta(hours=1),
    )
    db.add_all([consumed_token, active_token])
    db.flush()
    consumed_token.replaced_by_id = active_token.id
    db.flush()
    actor = load_formal_principal(db, admin_user.id, now=NOW)
    return World(
        admin_user=admin_user,
        actor=actor,
        permission_grant=permission_grant,
        target_user=target_user,
        auth_session=auth_session,
        consumed_token=consumed_token,
        active_token=active_token,
    )


def _session(
    db: Session,
    user: User,
    *,
    expires_at: datetime | None = None,
) -> AuthSession:
    row = AuthSession(
        user_id=user.id,
        refresh_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        client_type="web",
        device_id=f"device-{uuid.uuid4().hex}",
        device_name="会话管理测试设备",
        ip_address="10.20.30.40",
        user_agent="session-admin-test",
        created_at=NOW - timedelta(hours=2),
        last_seen_at=NOW - timedelta(minutes=2),
        expires_at=expires_at or NOW + timedelta(days=1),
    )
    db.add(row)
    db.flush()
    return row


def _revoke(
    db: Session,
    world: World,
    *,
    session_id: str | None = None,
    idempotency_key: str = RAW_IDEMPOTENCY_KEY,
    request_id: str = "session-admin-request-0001",
):
    return force_revoke_session(
        db,
        actor=world.actor,
        session_id=session_id or world.auth_session.id,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


def test_hq_admin_revokes_session_family_with_separate_audit_and_state(db: Session):
    world = _world(db)
    db.commit()

    result = _revoke(db, world)

    assert result.session_id == world.auth_session.id
    assert result.status == "revoked"
    assert result.replayed is False
    db.refresh(world.auth_session)
    db.refresh(world.consumed_token)
    db.refresh(world.active_token)
    assert world.auth_session.revoked_at is not None
    assert world.auth_session.revoked_by_id == world.admin_user.id
    assert world.active_token.revoked_at is not None
    assert world.consumed_token.revoked_at is None

    audit = db.get(AuditEvent, result.audit_event_id)
    transition = db.get(StateTransitionEvent, result.state_transition_event_id)
    assert audit is not None
    assert audit.action == "authentication.session.admin_revoked"
    assert audit.before_jsonb == {"status": "active"}
    assert audit.after_jsonb == {
        "client_type": "web",
        "outcome": "revoked",
        "reason_code": "admin_force_logout",
        "status": "revoked",
    }
    assert transition is not None
    assert (transition.from_status, transition.to_status) == ("active", "revoked")
    assert transition.idempotency_key == idempotency_storage_key(
        world.admin_user.id,
        RAW_IDEMPOTENCY_KEY,
    )


def test_permission_and_supplied_principal_privilege_escalation_fail_closed(db: Session):
    world = _world(db)
    db.commit()

    forged = replace(world.actor, assignments=())
    with pytest.raises(AuthenticationSessionAdminError) as caught:
        force_revoke_session(
            db,
            actor=forged,
            session_id=world.auth_session.id,
            idempotency_key="forged-session-admin-key-0001",
            request_id="session-admin-forged-0001",
        )
    assert caught.value.code == "actor_admin_required"

    db.delete(world.permission_grant)
    db.commit()
    with pytest.raises(AuthenticationSessionAdminError) as caught:
        _revoke(db, world, idempotency_key="missing-permission-key-0001")
    assert caught.value.code == "actor_permission_required"
    db.refresh(world.auth_session)
    assert world.auth_session.revoked_at is None


def test_same_request_replays_but_different_request_conflicts(db: Session):
    world = _world(db)
    second_session = _session(db, world.target_user)
    db.commit()

    first = _revoke(db, world)
    db.commit()
    replay = _revoke(db, world, request_id="session-admin-request-retry")
    assert replay == replace(first, replayed=True)
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1

    with pytest.raises(AuthenticationSessionAdminError) as caught:
        _revoke(db, world, session_id=second_session.id)
    assert caught.value.code == "idempotency_key_conflict"
    db.refresh(second_session)
    assert second_session.revoked_at is None


def test_same_key_is_rechecked_after_waiting_for_target_session_lock(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    world = _world(db)
    world.auth_session.revoked_at = NOW
    world.auth_session.revoked_by_id = world.admin_user.id
    db.commit()
    committed_peer_result = AuthenticationSessionRevocationResult(
        session_id=world.auth_session.id,
        status="revoked",
        revoked_at=NOW,
        audit_event_id=uuid.uuid4(),
        state_transition_event_id=uuid.uuid4(),
    )
    calls = 0

    def load_after_peer_commit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return committed_peer_result if calls == 2 else None

    monkeypatch.setattr(
        authentication_session_admin,
        "_load_idempotent_result",
        load_after_peer_commit,
    )
    replay = _revoke(db, world)

    assert calls == 2
    assert replay == replace(committed_peer_result, replayed=True)


def test_new_key_rejects_revoked_and_expired_sessions(db: Session):
    world = _world(db)
    expired_session = _session(
        db,
        world.target_user,
        expires_at=NOW - timedelta(seconds=1),
    )
    db.commit()
    _revoke(db, world)
    db.commit()

    with pytest.raises(AuthenticationSessionAdminError) as revoked:
        _revoke(
            db,
            world,
            idempotency_key="new-key-for-revoked-session-0001",
            request_id="session-admin-revoked-0001",
        )
    assert revoked.value.code == "session_already_revoked"

    with pytest.raises(AuthenticationSessionAdminError) as expired:
        _revoke(
            db,
            world,
            session_id=expired_session.id,
            idempotency_key="new-key-for-expired-session-0001",
            request_id="session-admin-expired-0001",
        )
    assert expired.value.code == "session_expired"


def test_missing_authentication_audit_rolls_back_session_and_token(db: Session):
    world = _world(db)
    db.commit()
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    assert head is not None
    db.delete(head)
    db.commit()

    with pytest.raises(AuthenticationSessionAdminError) as caught:
        _revoke(db, world)
    assert caught.value.code == "authentication_audit_unavailable"
    db.expire_all()
    assert db.get(AuthSession, world.auth_session.id).revoked_at is None
    assert db.get(AuthRefreshToken, world.active_token.id).revoked_at is None
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0


def test_caller_rollback_owns_all_revocation_writes(db: Session):
    world = _world(db)
    db.commit()
    result = _revoke(db, world)
    assert db.get(AuthSession, result.session_id).revoked_at is not None

    db.rollback()
    assert db.get(AuthSession, result.session_id).revoked_at is None
    assert db.get(AuthRefreshToken, world.active_token.id).revoked_at is None
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    assert head is not None and head.version == 0


def test_public_result_and_new_evidence_exclude_sensitive_values(db: Session):
    world = _world(db)
    db.commit()
    result = _revoke(db, world)
    transition = db.get(StateTransitionEvent, result.state_transition_event_id)
    audit = db.get(AuditEvent, result.audit_event_id)
    assert transition is not None and audit is not None

    assert {field.name for field in fields(AuthenticationSessionRevocationResult)} == {
        "session_id",
        "status",
        "revoked_at",
        "audit_event_id",
        "state_transition_event_id",
        "replayed",
    }
    evidence = json.dumps(
        {
            "result": asdict(result),
            "state_key": transition.idempotency_key,
            "state_metadata": transition.metadata_jsonb,
            "audit_before": audit.before_jsonb,
            "audit_after": audit.after_jsonb,
        },
        default=str,
        ensure_ascii=False,
    )
    for sensitive in (
        RAW_IDEMPOTENCY_KEY,
        world.target_user.id,
        world.target_user.mobile,
        world.auth_session.ip_address,
        world.auth_session.refresh_token_hash,
        world.active_token.token_hash,
    ):
        assert sensitive not in evidence
