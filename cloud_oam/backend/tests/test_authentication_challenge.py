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
    authorize_dispatch_provider_call,
    begin_verify,
    claim_dispatch,
    consume_verified,
    finish_verify,
    flush_deferred_audit_events,
    mark_send_uncertain,
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
    SmsChallengeDispatch,
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
    "dispatch_request_profile_sha256": "e" * 64,
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
    claimed = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-request-claim-0001",
        lease_seconds=30,
        now=NOW + timedelta(milliseconds=500),
    )
    assert claimed.owner_token is not None
    mark_sent(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=claimed.owner_token,
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
    assert known.dispatch_status == "prepared"
    assert unknown.status == "cancelled" and unknown.dispatch_required is False
    assert unknown.dispatch_status is None
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
    dispatches = list(db.scalars(select(SmsChallengeDispatch)))
    assert len(dispatches) == 1
    assert dispatches[0].mobile_hash == MOBILE_HASH
    assert dispatches[0].request_sha256 != DEFAULTS[
        "dispatch_request_profile_sha256"
    ]
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
    assert db.scalar(select(func.count()).select_from(SmsChallengeDispatch)) == 1

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
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 3
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 3

    with pytest.raises(AuthenticationChallengeError) as different_key:
        _prepare(
            db,
            user=user,
            idempotency_key="challenge-idempotency-rate-0003",
            request_id="challenge-request-rate-different-key",
            now=NOW + timedelta(seconds=2),
            mobile_hour_limit=1,
        )
    assert different_key.value.code == "mobile_hour_limit"
    assert different_key.value.mutation_persisted is False
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 2
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 3
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 3

    with pytest.raises(AuthenticationChallengeError) as replayed:
        _prepare(
            db,
            user=user,
            idempotency_key="challenge-idempotency-rate-0002",
            request_id="challenge-request-rate-retry",
            now=NOW + timedelta(seconds=3),
            mobile_hour_limit=1,
        )
    assert replayed.value.code == "mobile_hour_limit"
    assert replayed.value.mutation_persisted is False
    assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 2
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 3
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 3


def test_rate_denial_evidence_is_refreshed_after_its_active_window(db: Session):
    user = _formal_user(db)
    db.commit()
    _prepare(
        db,
        user=user,
        mobile_hour_limit=1,
        idempotency_key="challenge-rate-window-first-key",
        request_id="challenge-rate-window-first-request",
    )
    with pytest.raises(AuthenticationChallengeError):
        _prepare(
            db,
            user=user,
            mobile_hour_limit=1,
            idempotency_key="challenge-rate-window-old-denial-key",
            request_id="challenge-rate-window-old-denial-request",
            now=NOW + timedelta(seconds=1),
        )
    db.commit()

    _prepare(
        db,
        user=user,
        mobile_hour_limit=1,
        idempotency_key="challenge-rate-window-new-key",
        request_id="challenge-rate-window-new-request",
        now=NOW + timedelta(hours=1, seconds=2),
    )
    db.commit()
    before_current_denial = (
        db.scalar(select(func.count()).select_from(LoginChallenge)),
        db.scalar(select(func.count()).select_from(StateTransitionEvent)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    )

    with pytest.raises(AuthenticationChallengeError) as current_denial:
        _prepare(
            db,
            user=user,
            mobile_hour_limit=1,
            idempotency_key="challenge-rate-window-current-denial-key",
            request_id="challenge-rate-window-current-denial-request",
            now=NOW + timedelta(hours=1, seconds=3),
        )
    assert current_denial.value.mutation_persisted is True
    after_current_denial = (
        db.scalar(select(func.count()).select_from(LoginChallenge)),
        db.scalar(select(func.count()).select_from(StateTransitionEvent)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    )
    assert after_current_denial == tuple(value + 1 for value in before_current_denial)

    with pytest.raises(AuthenticationChallengeError) as bounded_replay:
        _prepare(
            db,
            user=user,
            mobile_hour_limit=1,
            idempotency_key="challenge-rate-window-bounded-replay-key",
            request_id="challenge-rate-window-bounded-replay-request",
            now=NOW + timedelta(hours=1, seconds=4),
        )
    assert bounded_replay.value.mutation_persisted is False
    assert (
        db.scalar(select(func.count()).select_from(LoginChallenge)),
        db.scalar(select(func.count()).select_from(StateTransitionEvent)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    ) == after_current_denial


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
    assert [
        (item.aggregate_type, item.from_status, item.to_status)
        for item in transitions
    ] == [
        ("login_challenge", None, "pending"),
        ("sms_dispatch", None, "prepared"),
        ("sms_dispatch", "prepared", "sending"),
        ("sms_dispatch", "sending", "accepted"),
        ("login_challenge", "pending", "verified"),
        ("login_challenge", "verified", "consumed"),
    ]
    actions = list(db.scalars(select(AuditEvent.action).order_by(AuditEvent.occurred_at)))
    assert actions == [
        "authentication.sms.challenge_prepared",
        "authentication.sms.dispatch_prepared",
        "authentication.sms.dispatch_claimed",
        "authentication.sms.dispatch_accepted",
        "authentication.sms.verification_started",
        "authentication.sms.verification_succeeded",
        "authentication.sms.challenge_consumed",
    ]


def test_verification_lifecycle_keeps_immediate_audit_by_default(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _sent_challenge(db, user)
    initial_audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
    assert initial_audit_count == 4

    attempt = begin_verify(
        db,
        mobile_hash=MOBILE_HASH,
        provider="aliyun",
        client_type="miniprogram",
        request_id="challenge-request-immediate-begin",
        now=NOW + timedelta(seconds=2),
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 5

    finish_verify(
        db,
        challenge_id=attempt.challenge_id,
        verified=True,
        request_id="challenge-request-immediate-finish",
        now=NOW + timedelta(seconds=3),
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 6

    consume_verified(
        db,
        challenge_id=prepared.challenge_id,
        actor_user_id=user.id,
        request_id="challenge-request-immediate-consume",
        now=NOW + timedelta(seconds=4),
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 7


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
    assert initial_audit_count == 4
    assert initial_transition_count == 4
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
        "authentication.sms.dispatch_prepared",
        "authentication.sms.dispatch_claimed",
        "authentication.sms.dispatch_accepted",
        "authentication.sms.verification_started",
        "authentication.sms.verification_succeeded",
        "authentication.sms.challenge_consumed",
    ]

    flush_deferred_audit_events(db, deferred_events=deferred_events)
    assert deferred_events == []
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 7


def test_expired_challenge_is_transitioned_before_error(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user, ttl_seconds=30)
    claimed = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-request-claim-expiry",
        lease_seconds=30,
        now=NOW + timedelta(milliseconds=500),
    )
    assert claimed.owner_token is not None
    mark_sent(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=claimed.owner_token,
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


def test_uncertain_send_is_independent_state_and_idempotent(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)

    claimed = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-request-send-claim",
        lease_seconds=30,
        now=NOW + timedelta(milliseconds=500),
    )
    assert claimed.owner_token is not None
    failed = mark_send_uncertain(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=claimed.owner_token,
        request_id="challenge-request-send-uncertain",
        now=NOW + timedelta(seconds=1),
    )
    replay = mark_send_uncertain(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=claimed.owner_token,
        request_id="challenge-request-send-uncertain-retry",
        now=NOW + timedelta(seconds=2),
    )
    assert failed.dispatch_status == "uncertain"
    assert replay.replayed is True
    challenge = db.get(LoginChallenge, prepared.challenge_id)
    dispatch = db.get(SmsChallengeDispatch, prepared.challenge_id)
    assert challenge is not None and challenge.status == "pending"
    assert dispatch is not None and dispatch.status == "uncertain"
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == 4
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 4


def test_dispatch_claim_has_one_owner_and_stale_replay_never_reclaims(
    db: Session,
):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)

    owner = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-dispatch-owner-first",
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )
    concurrent_replay = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-dispatch-owner-concurrent",
        lease_seconds=30,
        now=NOW + timedelta(seconds=2),
    )
    stale_replay = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-dispatch-owner-stale",
        lease_seconds=30,
        now=NOW + timedelta(seconds=32),
    )

    assert owner.acquired is True and owner.owner_token is not None
    assert owner.owner_token not in repr(owner)
    assert concurrent_replay.owner_token is None
    assert concurrent_replay.dispatch_status == "sending"
    assert stale_replay.owner_token is None
    assert stale_replay.dispatch_status == "uncertain"

    accepted = mark_sent(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=owner.owner_token,
        provider_reference="BIZ-REFERENCE-LATE-ACCEPT",
        request_id="challenge-dispatch-owner-late-accept",
        now=NOW + timedelta(seconds=33),
    )
    audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
    replay = mark_sent(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=owner.owner_token,
        provider_reference="BIZ-REFERENCE-LATE-ACCEPT",
        request_id="challenge-dispatch-owner-late-replay",
        now=NOW + timedelta(seconds=34),
    )
    assert accepted.dispatch_status == "accepted"
    assert replay.replayed is True
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == audit_count


def test_provider_call_authorization_holds_owner_and_rejects_late_start(
    db: Session,
):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)
    owner = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-provider-gate-owner",
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )
    assert owner.owner_token is not None

    assert authorize_dispatch_provider_call(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=owner.owner_token,
        request_id="challenge-provider-gate-authorized",
        minimum_remaining_seconds=15,
        now=NOW + timedelta(seconds=2),
    ) is True
    assert db.get(SmsChallengeDispatch, prepared.challenge_id).status == "sending"

    assert authorize_dispatch_provider_call(
        db,
        challenge_id=prepared.challenge_id,
        owner_token=owner.owner_token,
        request_id="challenge-provider-gate-too-late",
        minimum_remaining_seconds=15,
        now=NOW + timedelta(seconds=20),
    ) is False
    dispatch = db.get(SmsChallengeDispatch, prepared.challenge_id)
    assert dispatch is not None and dispatch.status == "uncertain"
    assert dispatch.uncertain_at is not None
    assert dispatch.uncertain_at.replace(tzinfo=timezone.utc) == (
        NOW + timedelta(seconds=20)
    )


def test_wrong_dispatch_owner_cannot_complete_or_change_evidence(db: Session):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)
    owner = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-dispatch-owner-valid",
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )
    assert owner.owner_token is not None

    with pytest.raises(AuthenticationChallengeError) as caught:
        mark_send_uncertain(
            db,
            challenge_id=prepared.challenge_id,
            owner_token="0" * 32,
            request_id="challenge-dispatch-owner-invalid",
            now=NOW + timedelta(seconds=2),
        )
    assert caught.value.code == "dispatch_owner_conflict"
    dispatch = db.get(SmsChallengeDispatch, prepared.challenge_id)
    assert dispatch is not None and dispatch.status == "sending"
    assert dispatch.uncertain_at is None


def test_unresolved_dispatch_blocks_new_send_until_challenge_expiry(db: Session):
    user = _formal_user(db)
    db.commit()
    first = _prepare(db, user=user)
    owner = claim_dispatch(
        db,
        challenge_id=first.challenge_id,
        request_id="challenge-unresolved-owner",
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )
    assert owner.owner_token is not None
    mark_send_uncertain(
        db,
        challenge_id=first.challenge_id,
        owner_token=owner.owner_token,
        request_id="challenge-unresolved-unknown",
        now=NOW + timedelta(seconds=2),
    )

    blocked = _prepare(
        db,
        user=user,
        idempotency_key="challenge-unresolved-blocked-key",
        request_id="challenge-unresolved-blocked-request",
        now=NOW + timedelta(seconds=61),
        # Provider ownership is mobile-scoped, not client-surface-scoped.
        # A miniprogram uncertainty must also block a web resend.
        client_type="web",
    )
    assert blocked.challenge_id == first.challenge_id
    assert blocked.status == "pending"
    assert blocked.dispatch_status == "uncertain"
    assert blocked.dispatch_required is False
    assert blocked.replayed is True

    replacement = _prepare(
        db,
        user=user,
        idempotency_key="challenge-unresolved-replacement-key",
        request_id="challenge-unresolved-replacement-request",
        now=NOW + timedelta(seconds=301),
    )
    assert replacement.status == "pending"
    assert replacement.dispatch_status == "prepared"
    old_dispatch = db.get(SmsChallengeDispatch, first.challenge_id)
    assert old_dispatch is not None and old_dispatch.status == "expired"
    assert old_dispatch.uncertain_at is not None
    assert old_dispatch.expired_at is not None
    old_challenge = db.get(LoginChallenge, first.challenge_id)
    assert old_challenge is not None and old_challenge.status == "expired"


@pytest.mark.parametrize(
    ("retry_offsets", "replacement_offset", "limit_overrides"),
    [
        ((61, 122, 183, 244), 305, {}),
        ((250,), 301, {"mobile_hour_limit": 30}),
    ],
    ids=["mobile-hour", "send-interval"],
)
def test_dispatch_unresolved_retries_reuse_evidence_without_writes_or_quota(
    db: Session,
    retry_offsets: tuple[int, ...],
    replacement_offset: int,
    limit_overrides: dict[str, int],
) -> None:
    user = _formal_user(db)
    db.commit()
    first = _prepare(db, user=user, **limit_overrides)
    db.commit()
    owner = claim_dispatch(
        db,
        challenge_id=first.challenge_id,
        request_id="challenge-quota-unresolved-owner",
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )
    assert owner.owner_token is not None
    db.commit()
    mark_send_uncertain(
        db,
        challenge_id=first.challenge_id,
        owner_token=owner.owner_token,
        request_id="challenge-quota-unresolved-unknown",
        now=NOW + timedelta(seconds=2),
    )
    db.commit()
    frozen_counts = (
        db.scalar(select(func.count()).select_from(LoginChallenge)),
        db.scalar(select(func.count()).select_from(SmsChallengeDispatch)),
        db.scalar(select(func.count()).select_from(StateTransitionEvent)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    )

    for index, offset in enumerate(retry_offsets, start=1):
        blocked = _prepare(
            db,
            user=user,
            idempotency_key=f"challenge-quota-blocked-key-{index:04d}",
            request_id=f"challenge-quota-blocked-request-{index:04d}",
            now=NOW + timedelta(seconds=offset),
            **limit_overrides,
        )
        assert blocked.challenge_id == first.challenge_id
        assert blocked.status == "pending"
        assert blocked.dispatch_status == "uncertain"
        assert blocked.dispatch_required is False
        assert blocked.replayed is True
        assert (
            db.scalar(select(func.count()).select_from(LoginChallenge)),
            db.scalar(select(func.count()).select_from(SmsChallengeDispatch)),
            db.scalar(select(func.count()).select_from(StateTransitionEvent)),
            db.scalar(select(func.count()).select_from(AuditEvent)),
        ) == frozen_counts
        db.commit()

    replacement = _prepare(
        db,
        user=user,
        idempotency_key="challenge-quota-replacement-key",
        request_id="challenge-quota-replacement-request",
        now=NOW + timedelta(seconds=replacement_offset),
        **limit_overrides,
    )
    assert replacement.status == "pending"
    assert replacement.dispatch_status == "prepared"
    assert replacement.dispatch_required is True


def test_verification_can_recover_only_after_sending_lease_becomes_uncertain(
    db: Session,
):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)
    claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-verification-stale-owner",
        lease_seconds=30,
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(AuthenticationChallengeError) as in_flight:
        begin_verify(
            db,
            mobile_hash=MOBILE_HASH,
            provider="aliyun",
            client_type="miniprogram",
            request_id="challenge-verification-in-flight",
            now=NOW + timedelta(seconds=2),
        )
    assert in_flight.value.code == "challenge_not_sent"

    attempt = begin_verify(
        db,
        mobile_hash=MOBILE_HASH,
        provider="aliyun",
        client_type="miniprogram",
        request_id="challenge-verification-stale-recovery",
        now=NOW + timedelta(seconds=32),
    )
    assert attempt.challenge_id == prepared.challenge_id
    assert attempt.attempt_no == 1
    dispatch = db.get(SmsChallengeDispatch, prepared.challenge_id)
    assert dispatch is not None and dispatch.status == "uncertain"


@pytest.mark.parametrize("unsafe_reference", ["13800000000", "24681012", "10.0.0.8"])
def test_mark_sent_rejects_plaintext_auth_values_as_provider_reference(
    db: Session,
    unsafe_reference: str,
):
    user = _formal_user(db)
    db.commit()
    prepared = _prepare(db, user=user)
    claimed = claim_dispatch(
        db,
        challenge_id=prepared.challenge_id,
        request_id="challenge-request-unsafe-claim",
        lease_seconds=30,
        now=NOW + timedelta(milliseconds=500),
    )
    assert claimed.owner_token is not None

    with pytest.raises(AuthenticationChallengeError) as caught:
        mark_sent(
            db,
            challenge_id=prepared.challenge_id,
            owner_token=claimed.owner_token,
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
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2

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
    assert persisted_head is not None and persisted_head.version == 2
    row = db.get(LoginChallenge, prepared.challenge_id)
    assert row is not None and row.status == "pending"
    transitions = list(db.scalars(select(StateTransitionEvent)))
    assert [
        (item.aggregate_type, item.from_status, item.to_status)
        for item in transitions
    ] == [
        ("login_challenge", None, "pending"),
        ("sms_dispatch", None, "prepared"),
    ]
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2
