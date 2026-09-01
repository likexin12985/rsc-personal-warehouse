from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_services.authentication_challenge import (
    AuthenticationChallengeError,
    DeferredChallengeAuditEvent,
    begin_verify,
    consume_verified,
    finish_verify,
    flush_deferred_audit_events,
    mark_send_failed,
    mark_sent,
    prepare,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    LoginChallenge,
    Organization,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from app.models import User


NOW = datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc)
MOBILE_HASH = "a" * 64
OTHER_MOBILE_HASH = "c" * 64
IP_HASH = "b" * 64
OTHER_IP_HASH = "d" * 64
DEFAULTS = {
    "provider": "aliyun",
    "client_type": "miniprogram",
    "hash_version": 1,
    "mobile_hour_limit": 5,
    "ip_hour_limit": 20,
    "send_interval_seconds": 60,
    "ttl_seconds": 300,
    "max_attempts": 2,
}


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


def _formal_user(db: Session) -> User:
    organization = Organization(
        code="ORG-AUTH-CHALLENGE",
        name="认证挑战测试组织",
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    db.add(organization)
    db.flush()
    person = Person(
        organization_id=organization.id,
        employee_no="EMP-AUTH-CHALLENGE",
        name="认证挑战测试工程师",
        employment_status="active",
    )
    db.add(person)
    db.flush()
    user = User(
        person_id=person.id,
        account_status="active",
        mobile="13900000701",
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
                identifier_hash=MOBILE_HASH,
                hash_version=1,
                verified_at=NOW - timedelta(days=1),
                status="active",
            ),
            RoleAssignment(
                user_id=user.id,
                role_id=role.id,
                scope_type="person",
                scope_id=str(person.id),
                valid_from=NOW - timedelta(days=1),
                status="active",
                assigned_by=user.id,
                reason="formal authentication challenge test",
            ),
        ]
    )
    db.flush()
    return user


def _prepare(
    db: Session,
    *,
    user: User | None,
    mobile_hash: str = MOBILE_HASH,
    requested_ip_hash: str = IP_HASH,
    idempotency_key: str = "challenge-idempotency-0001",
    request_id: str = "challenge-request-0001",
    now: datetime = NOW,
    **overrides,
):
    arguments = dict(DEFAULTS)
    arguments.update(overrides)
    return prepare(
        db,
        mobile_hash=mobile_hash,
        requested_ip_hash=requested_ip_hash,
        user=user,
        idempotency_key=idempotency_key,
        request_id=request_id,
        now=now,
        **arguments,
    )


def _sent_challenge(db: Session, user: User):
    prepared = _prepare(db, user=user)
    mark_sent(
        db,
        challenge_id=prepared.challenge_id,
        provider_reference="BIZ-REFERENCE-0001",
        request_id="challenge-request-sent-0001",
        now=NOW + timedelta(seconds=1),
    )
    return prepared


def test_prepare_known_and_unknown_have_same_public_response_and_no_plaintext(
    db: Session,
):
    user = _formal_user(db)
    db.commit()

    known = _prepare(db, user=user)
    unknown = _prepare(
        db,
        user=None,
        mobile_hash=OTHER_MOBILE_HASH,
        requested_ip_hash=OTHER_IP_HASH,
        idempotency_key="challenge-idempotency-unknown-0001",
        request_id="challenge-request-unknown-0001",
    )

    assert known.status == "pending" and known.dispatch_required is True
    assert unknown.status == "cancelled" and unknown.dispatch_required is False
    assert (
        known.public_message,
        known.retry_after,
        known.expires_in,
    ) == (
        unknown.public_message,
        unknown.retry_after,
        unknown.expires_in,
    )
    rows = list(db.scalars(select(LoginChallenge).order_by(LoginChallenge.created_at)))
    assert all(row.code_hash is None for row in rows)
    assert all(row.verification_mode == "provider_managed" for row in rows)
    assert all("challenge-idempotency" not in row.idempotency_key for row in rows)

    persisted = json.dumps(
        {
            "challenges": [
                {
                    "mobile_hash": row.mobile_hash,
                    "requested_ip_hash": row.requested_ip_hash,
                    "idempotency_key": row.idempotency_key,
                    "provider_reference": row.provider_reference,
                }
                for row in rows
            ],
            "audits": [
                {"before": row.before_jsonb, "after": row.after_jsonb}
                for row in db.scalars(select(AuditEvent))
            ],
            "states": [
                {"reason": row.reason, "metadata": row.metadata_jsonb}
                for row in db.scalars(select(StateTransitionEvent))
            ],
        },
        ensure_ascii=False,
    )
    for secret in (
        "13800000000",
        "246810",
        "10.0.0.8",
        "challenge-idempotency-0001",
    ):
        assert secret not in persisted


def test_prepare_is_idempotent_and_same_key_different_request_fails_closed(db: Session):
    user = _formal_user(db)
    db.commit()

    first = _prepare(db, user=user)
    replay = _prepare(
        db,
        user=user,
        request_id="challenge-request-retry-0001",
    )
    assert replay.challenge_id == first.challenge_id
    assert replay.replayed is True
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 1

    with pytest.raises(AuthenticationChallengeError) as caught:
        _prepare(
            db,
            user=user,
            mobile_hash=OTHER_MOBILE_HASH,
            request_id="challenge-request-conflict-0001",
        )
    assert caught.value.code == "challenge_idempotency_conflict"
    assert caught.value.category == "conflict"
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 1


def test_prepare_treats_non_exact_user_candidate_as_unknown(db: Session):
    user = _formal_user(db)
    db.commit()

    result = _prepare(db, user=user, mobile_hash=OTHER_MOBILE_HASH)

    assert result.status == "cancelled"
    assert result.dispatch_required is False
    row = db.get(LoginChallenge, result.challenge_id)
    assert row is not None and row.status == "cancelled"


def test_prepare_requires_exact_identity_hash_version_and_fingerprints_it(db: Session):
    user = _formal_user(db)
    db.commit()

    mismatch = _prepare(
        db,
        user=user,
        hash_version=2,
        idempotency_key="challenge-idempotency-version-0001",
        request_id="challenge-request-version-0001",
    )
    assert mismatch.status == "cancelled"
    assert mismatch.dispatch_required is False

    first_unknown = _prepare(
        db,
        user=None,
        mobile_hash=OTHER_MOBILE_HASH,
        requested_ip_hash=OTHER_IP_HASH,
        hash_version=1,
        idempotency_key="challenge-idempotency-version-0002",
        request_id="challenge-request-version-0002",
    )
    assert first_unknown.status == "cancelled"
    with pytest.raises(AuthenticationChallengeError) as caught:
        _prepare(
            db,
            user=None,
            mobile_hash=OTHER_MOBILE_HASH,
            requested_ip_hash=OTHER_IP_HASH,
            hash_version=2,
            idempotency_key="challenge-idempotency-version-0002",
            request_id="challenge-request-version-0002-retry",
        )
    assert caught.value.code == "challenge_idempotency_conflict"


@pytest.mark.parametrize("invalid_version", [0, -1, True, "1", 2_147_483_648])
def test_prepare_rejects_invalid_hash_version(db: Session, invalid_version):
    with pytest.raises(AuthenticationChallengeError) as caught:
        _prepare(db, user=None, hash_version=invalid_version)
    assert caught.value.code == "invalid_hash_version"
    assert caught.value.category == "invalid_request"
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0


def test_prepare_records_rate_limit_denial_for_caller_to_commit(db: Session):
    user = _formal_user(db)
    db.commit()
    _prepare(db, user=user, mobile_hour_limit=1)

    with pytest.raises(AuthenticationChallengeError) as caught:
        _prepare(
            db,
            user=user,
            idempotency_key="challenge-idempotency-rate-0002",
            request_id="challenge-request-rate-0002",
            now=NOW + timedelta(seconds=1),
            mobile_hour_limit=1,
        )
    error = caught.value
    assert error.code == "mobile_hour_limit"
    assert error.category == "rate_limited"
    assert error.retry_after is not None and error.retry_after > 0
    assert error.mutation_persisted is True
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 2
    denied = db.scalar(
        select(LoginChallenge).order_by(LoginChallenge.created_at.desc()).limit(1)
    )
    assert denied is not None and denied.status == "cancelled"
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 2
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2

    with pytest.raises(AuthenticationChallengeError) as replayed:
        _prepare(
            db,
            user=user,
            idempotency_key="challenge-idempotency-rate-0002",
            request_id="challenge-request-rate-retry",
            now=NOW + timedelta(seconds=2),
            mobile_hour_limit=1,
        )
    assert replayed.value.code == "mobile_hour_limit"
    assert replayed.value.mutation_persisted is False
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 2
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 2
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_provider_managed_success_lifecycle_is_independently_audited(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _sent_challenge(db, user)

    attempt = begin_verify(
        db,
        mobile_hash=MOBILE_HASH,
        provider="aliyun",
        client_type="miniprogram",
        request_id="challenge-request-verify-0001",
        now=NOW + timedelta(seconds=2),
    )
    assert attempt.challenge_id == prepared.challenge_id
    assert attempt.attempt_no == 1
    assert attempt.verification_mode == "provider_managed"
    assert attempt.provider_reference == "BIZ-REFERENCE-0001"

    verified = finish_verify(
        db,
        challenge_id=attempt.challenge_id,
        verified=True,
        request_id="challenge-request-finish-0001",
        now=NOW + timedelta(seconds=3),
    )
    assert verified.status == "verified"
    row = db.get(LoginChallenge, attempt.challenge_id)
    assert row is not None and row.code_hash is None and row.verified_at is not None

    consumed = consume_verified(
        db,
        challenge_id=attempt.challenge_id,
        actor_user_id=user.id,
        request_id="challenge-request-consume-0001",
        now=NOW + timedelta(seconds=4),
    )
    assert consumed.status == "consumed"
    assert row.consumed_at is not None

    transitions = list(
        db.scalars(
            select(StateTransitionEvent).order_by(StateTransitionEvent.occurred_at)
        )
    )
    assert [(item.from_status, item.to_status) for item in transitions] == [
        (None, "pending"),
        ("pending", "verified"),
        ("verified", "consumed"),
    ]
    actions = list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.occurred_at)))
    assert actions == [
        "authentication.sms.challenge_prepared",
        "authentication.sms.challenge_sent",
        "authentication.sms.verification_started",
        "authentication.sms.verification_succeeded",
        "authentication.sms.challenge_consumed",
    ]


def test_verification_lifecycle_keeps_immediate_audit_by_default(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _sent_challenge(db, user)
    initial_audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
    assert initial_audit_count == 2

    attempt = begin_verify(
        db,
        mobile_hash=MOBILE_HASH,
        provider="aliyun",
        client_type="miniprogram",
        request_id="challenge-request-immediate-begin",
        now=NOW + timedelta(seconds=2),
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 3

    finish_verify(
        db,
        challenge_id=attempt.challenge_id,
        verified=True,
        request_id="challenge-request-immediate-finish",
        now=NOW + timedelta(seconds=3),
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 4

    consume_verified(
        db,
        challenge_id=prepared.challenge_id,
        actor_user_id=user.id,
        request_id="challenge-request-immediate-consume",
        now=NOW + timedelta(seconds=4),
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 5


def test_verification_lifecycle_defers_only_audit_chain_until_explicit_flush(
    db: Session,
):
    user = _formal_user(db)
    db.commit()
    prepared = _sent_challenge(db, user)
    initial_audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
    initial_transition_count = db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    )
    assert initial_audit_count == 2
    assert initial_transition_count == 1
    deferred_events: list[DeferredChallengeAuditEvent] = []

    attempt = begin_verify(
        db,
        mobile_hash=MOBILE_HASH,
        provider="aliyun",
        client_type="miniprogram",
        request_id="challenge-request-deferred-begin",
        now=NOW + timedelta(seconds=2),
        deferred_events=deferred_events,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == initial_audit_count
    assert db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    ) == initial_transition_count

    finish_verify(
        db,
        challenge_id=attempt.challenge_id,
        verified=True,
        request_id="challenge-request-deferred-finish",
        now=NOW + timedelta(seconds=3),
        deferred_events=deferred_events,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == initial_audit_count
    assert db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    ) == initial_transition_count + 1

    consume_verified(
        db,
        challenge_id=prepared.challenge_id,
        actor_user_id=user.id,
        request_id="challenge-request-deferred-consume",
        now=NOW + timedelta(seconds=4),
        deferred_events=deferred_events,
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == initial_audit_count
    assert db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    ) == initial_transition_count + 2
    assert len(deferred_events) == 3

    flush_deferred_audit_events(db, deferred_events=deferred_events)

    assert deferred_events == []
    actions = list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.occurred_at)))
    assert actions == [
        "authentication.sms.challenge_prepared",
        "authentication.sms.challenge_sent",
        "authentication.sms.verification_started",
        "authentication.sms.verification_succeeded",
        "authentication.sms.challenge_consumed",
    ]

    flush_deferred_audit_events(db, deferred_events=deferred_events)
    assert deferred_events == []
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 5


def test_expired_challenge_is_transitioned_before_error(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user, ttl_seconds=30)
    mark_sent(
        db,
        challenge_id=prepared.challenge_id,
        provider_reference="BIZ-REFERENCE-EXPIRY",
        request_id="challenge-request-sent-expiry",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(AuthenticationChallengeError) as caught:
        begin_verify(
            db,
            mobile_hash=MOBILE_HASH,
            provider="aliyun",
            client_type="miniprogram",
            request_id="challenge-request-expiry",
            now=NOW + timedelta(seconds=31),
        )
    assert caught.value.code == "challenge_expired"
    assert caught.value.mutation_persisted is True
    row = db.get(LoginChallenge, prepared.challenge_id)
    assert row is not None and row.status == "expired"


def test_attempt_limit_locks_challenge_and_each_failure_is_audited(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _sent_challenge(db, user)

    for attempt_no, expected_code in ((1, "verification_failed"), (2, "attempts_exhausted")):
        attempt = begin_verify(
            db,
            mobile_hash=MOBILE_HASH,
            provider="aliyun",
            client_type="miniprogram",
            request_id=f"challenge-request-begin-{attempt_no:04d}",
            now=NOW + timedelta(seconds=attempt_no * 2),
        )
        assert attempt.attempt_no == attempt_no
        with pytest.raises(AuthenticationChallengeError) as caught:
            finish_verify(
                db,
                challenge_id=prepared.challenge_id,
                verified=False,
                request_id=f"challenge-request-fail-{attempt_no:04d}",
                now=NOW + timedelta(seconds=attempt_no * 2 + 1),
            )
        assert caught.value.code == expected_code
        assert caught.value.mutation_persisted is True

    row = db.get(LoginChallenge, prepared.challenge_id)
    assert row is not None and row.status == "locked" and row.attempts == 2
    with pytest.raises(AuthenticationChallengeError) as caught:
        begin_verify(
            db,
            mobile_hash=MOBILE_HASH,
            provider="aliyun",
            client_type="miniprogram",
            request_id="challenge-request-after-lock",
            now=NOW + timedelta(seconds=8),
        )
    assert caught.value.code == "challenge_unavailable"


def test_send_failure_is_state_transition_and_idempotent(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)

    failed = mark_send_failed(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-request-send-failed",
        now=NOW + timedelta(seconds=1),
    )
    replay = mark_send_failed(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-request-send-failed-retry",
        now=NOW + timedelta(seconds=2),
    )
    assert failed.status == "cancelled"
    assert replay.replayed is True
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 2
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2


@pytest.mark.parametrize("unsafe_reference", ["13800000000", "24681012", "10.0.0.8"])
def test_mark_sent_rejects_plaintext_auth_values_as_provider_reference(
    db: Session,
    unsafe_reference: str,
):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)

    with pytest.raises(AuthenticationChallengeError) as caught:
        mark_sent(
            db,
            challenge_id=prepared.challenge_id,
            provider_reference=unsafe_reference,
            request_id="challenge-request-unsafe-reference",
            now=NOW + timedelta(seconds=1),
        )
    assert caught.value.code == "invalid_provider_reference"
    row = db.get(LoginChallenge, prepared.challenge_id)
    assert row is not None and row.provider_reference is None


def test_caller_rollback_removes_challenge_state_and_audit(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)
    assert db.get(LoginChallenge, prepared.challenge_id) is not None
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1

    db.rollback()
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    assert head is not None and head.version == 0


def test_missing_authentication_audit_head_fails_whole_operation(db: Session):
    user = _formal_user(db)
    db.commit()
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    assert head is not None
    db.delete(head)
    db.commit()

    with pytest.raises(AuthenticationChallengeError) as caught:
        _prepare(db, user=user)
    assert caught.value.code == "authentication_audit_unavailable"
    assert caught.value.category == "service_unavailable"
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 0
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 0
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_nonempty_audit_head_cannot_be_deleted_and_lifecycle_state_is_preserved(
    db: Session,
):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)
    db.commit()
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    assert head is not None
    db.delete(head)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    persisted_head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    assert persisted_head is not None and persisted_head.version == 1
    row = db.get(LoginChallenge, prepared.challenge_id)
    assert row is not None and row.status == "pending"
    transitions = list(db.scalars(select(StateTransitionEvent)))
    assert [(item.from_status, item.to_status) for item in transitions] == [
        (None, "pending")
    ]
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
