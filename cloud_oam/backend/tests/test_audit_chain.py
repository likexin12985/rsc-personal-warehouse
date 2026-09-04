from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event as sqlalchemy_event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_services.audit_chain import (
    AuditChainHeadNotFound,
    AuditChainStateError,
    AuditChainValidationError,
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_from_head,
    append_audit_event,
    calculate_audit_event_hash,
    _verify_audit_event_in_prelocked_stream,
    _verify_audit_event_with_prelocked_proof,
    verify_audit_event_in_stream,
)
from app.foundation_models import AuditChainHead, AuditEvent
from app.models import User


NOW = datetime(2026, 8, 30, 8, 9, 10, 123456, tzinfo=timezone.utc)
STREAM_KEY = "authorization"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @sqlalchemy_event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_actor(db: Session) -> User:
    actor = User(
        id=str(uuid.uuid4()),
        mobile=f"1{uuid.uuid4().int % 10**19:019d}",
        name="审计操作人",
        password_hash="formal-password-login-disabled",
        role="admin",
        province=None,
        account_status="active",
        authorization_version=1,
        is_active=True,
        require_password_change=False,
    )
    db.add(actor)
    db.flush()
    return actor


def seed_head(db: Session, stream_key: str = STREAM_KEY) -> AuditChainHead:
    head = AuditChainHead(stream_key=stream_key, version=0)
    db.add(head)
    db.flush()
    return head


def append_kwargs(actor: User) -> dict[str, object]:
    return {
        "stream_key": STREAM_KEY,
        "actor_user_id": actor.id,
        "action": "role_assignment.created",
        "aggregate_type": "role_assignment",
        "aggregate_id": "assignment-001",
        "before_jsonb": {"roles": [], "meta": {"b": 2, "a": "原值"}},
        "after_jsonb": {
            "roles": ["technician"],
            "scope": {"type": "person", "id": "person-001"},
        },
        "request_id": "request-001",
        "occurred_at": NOW,
    }


def test_payload_is_snapshotted_and_hash_is_independently_recomputable(
    db: Session,
):
    actor = make_actor(db)
    head = seed_head(db)
    db.commit()
    kwargs = append_kwargs(actor)
    before = kwargs["before_jsonb"]
    after = kwargs["after_jsonb"]

    audit_event = append_audit_event(db, **kwargs)

    assert audit_event.before_jsonb == before
    assert audit_event.after_jsonb == after
    assert audit_event.stream_key == STREAM_KEY
    assert audit_event.stream_version == 1
    assert audit_event.before_jsonb is not before
    assert audit_event.after_jsonb is not after
    expected_document = {
        "action": audit_event.action,
        "actor_user_id": audit_event.actor_user_id,
        "after_jsonb": audit_event.after_jsonb,
        "aggregate_id": audit_event.aggregate_id,
        "aggregate_type": audit_event.aggregate_type,
        "before_jsonb": audit_event.before_jsonb,
        "event_id": str(audit_event.id),
        "occurred_at": "2026-08-30T08:09:10.123456Z",
        "previous_hash": None,
        "request_id": audit_event.request_id,
        "stream_key": STREAM_KEY,
    }
    expected_canonical_json = json.dumps(
        expected_document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_hash = hashlib.sha256(
        expected_canonical_json.encode("utf-8")
    ).hexdigest()
    assert audit_event.event_hash == expected_hash
    assert calculate_audit_event_hash(
        stream_key=STREAM_KEY,
        event_id=audit_event.id,
        actor_user_id=audit_event.actor_user_id,
        action=audit_event.action,
        aggregate_type=audit_event.aggregate_type,
        aggregate_id=audit_event.aggregate_id,
        before_jsonb=audit_event.before_jsonb,
        after_jsonb=audit_event.after_jsonb,
        request_id=audit_event.request_id,
        previous_hash=audit_event.previous_hash,
        occurred_at=audit_event.occurred_at,
    ) == audit_event.event_hash
    assert head.last_event_id == audit_event.id
    assert head.last_hash == audit_event.event_hash
    assert head.version == 1

    before["roles"].append("admin")
    after["scope"]["id"] = "mutated-by-caller"
    assert audit_event.before_jsonb["roles"] == []
    assert audit_event.after_jsonb["scope"]["id"] == "person-001"


def test_two_events_form_one_continuous_chain(db: Session):
    actor = make_actor(db)
    head = seed_head(db)
    db.commit()

    first = append_audit_event(db, **append_kwargs(actor))
    second_kwargs = append_kwargs(actor)
    second_kwargs.update(
        {
            "action": "role_assignment.revoked",
            "aggregate_id": "assignment-002",
            "before_jsonb": {"status": "active"},
            "after_jsonb": {"status": "revoked"},
            "request_id": "request-002",
            "occurred_at": datetime(
                2026, 8, 30, 16, 10, 11, 654321,
                tzinfo=timezone.utc,
            ),
        }
    )
    second = append_audit_event(db, **second_kwargs)

    assert first.previous_hash is None
    assert first.stream_key == STREAM_KEY
    assert first.stream_version == 1
    assert second.previous_hash == first.event_hash
    assert second.stream_key == STREAM_KEY
    assert second.stream_version == 2
    assert second.event_hash != first.event_hash
    assert head.last_event_id == second.id
    assert head.last_hash == second.event_hash
    assert head.version == 2
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2
    assert (
        verify_audit_event_in_stream(
            db,
            stream_key=STREAM_KEY,
            event_id=first.id,
        ).id
        == first.id
    )
    assert (
        verify_audit_event_in_stream(
            db,
            stream_key=STREAM_KEY,
            event_id=second.id,
        ).id
        == second.id
    )


def test_stream_verification_locks_only_the_mutable_head() -> None:
    source = inspect.getsource(verify_audit_event_in_stream)
    walk_source = inspect.getsource(_verify_audit_event_from_head)
    prelocked_source = inspect.getsource(_verify_audit_event_in_prelocked_stream)

    assert "lock_audit_chain_head" in source
    assert "select(AuditEvent)" in walk_source
    assert ".with_for_update()" not in source
    assert ".with_for_update()" not in walk_source
    assert ".with_for_update()" not in prelocked_source


def test_transaction_bound_prelocked_proof_allows_only_same_transaction(
    db: Session,
) -> None:
    actor = make_actor(db)
    seed_head(db)
    db.commit()

    _head, proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=STREAM_KEY,
    )
    original_transaction = db.get_transaction()
    assert original_transaction is not None
    assert proof.session is db
    assert proof.transaction is original_transaction
    audit_event = append_audit_event(db, **append_kwargs(actor))
    assert (
        _verify_audit_event_with_prelocked_proof(
            db,
            proof=proof,
            stream_key=STREAM_KEY,
            event_id=audit_event.id,
        ).id
        == audit_event.id
    )

    db.commit()
    db.scalar(select(AuditChainHead).limit(1))
    assert db.get_transaction() is not proof.transaction
    with pytest.raises(AuditChainStateError, match="does not belong"):
        _verify_audit_event_with_prelocked_proof(
            db,
            proof=proof,
            stream_key=STREAM_KEY,
            event_id=audit_event.id,
        )


@pytest.mark.parametrize(
    "tamper",
    ["head_version", "head_event", "event_hash", "event_coordinate"],
)
def test_stream_membership_fails_closed_on_chain_tamper(
    db: Session,
    tamper: str,
):
    actor = make_actor(db)
    head = seed_head(db)
    db.commit()
    first = append_audit_event(db, **append_kwargs(actor))
    second_kwargs = append_kwargs(actor)
    second_kwargs.update(
        {
            "action": "role_assignment.revoked",
            "aggregate_id": "assignment-002",
            "request_id": "request-002",
        }
    )
    second = append_audit_event(db, **second_kwargs)
    if tamper == "head_version":
        head.version = 1
    elif tamper == "head_event":
        head.last_event_id = first.id
    elif tamper == "event_coordinate":
        second.stream_version = 3
    else:
        second.after_jsonb = {"tampered": True}
    db.flush()

    with pytest.raises(AuditChainStateError):
        verify_audit_event_in_stream(
            db,
            stream_key=STREAM_KEY,
            event_id=first.id,
        )


def test_event_from_another_stream_is_not_a_member(db: Session):
    actor = make_actor(db)
    seed_head(db)
    seed_head(db, "inventory")
    db.commit()
    authorization_event = append_audit_event(db, **append_kwargs(actor))
    inventory_kwargs = append_kwargs(actor)
    inventory_kwargs["stream_key"] = "inventory"
    inventory_kwargs["request_id"] = "inventory-request-001"
    inventory_event = append_audit_event(db, **inventory_kwargs)

    with pytest.raises(AuditChainStateError, match="not reachable"):
        verify_audit_event_in_stream(
            db,
            stream_key="inventory",
            event_id=authorization_event.id,
        )
    assert (
        verify_audit_event_in_stream(
            db,
            stream_key="inventory",
            event_id=inventory_event.id,
        ).id
        == inventory_event.id
    )


def test_persisted_cross_stream_coordinate_fails_full_chain_replay(db: Session):
    actor = make_actor(db)
    seed_head(db)
    seed_head(db, "inventory")
    db.commit()
    authorization_event = append_audit_event(db, **append_kwargs(actor))
    inventory_kwargs = append_kwargs(actor)
    inventory_kwargs["stream_key"] = "inventory"
    inventory_kwargs["request_id"] = "inventory-request-coordinate"
    append_audit_event(db, **inventory_kwargs)

    authorization_event.stream_key = "inventory"
    authorization_event.stream_version = 2
    db.flush()

    with pytest.raises(AuditChainStateError, match="coordinate"):
        verify_audit_event_in_stream(
            db,
            stream_key=STREAM_KEY,
            event_id=authorization_event.id,
        )


def test_impossible_head_version_fails_before_an_unbounded_chain_walk(db: Session):
    actor = make_actor(db)
    head = seed_head(db)
    db.commit()
    audit_event = append_audit_event(db, **append_kwargs(actor))
    head.version = 10**9
    db.flush()

    with pytest.raises(AuditChainStateError, match="exceeds persisted event count"):
        verify_audit_event_in_stream(
            db,
            stream_key=STREAM_KEY,
            event_id=audit_event.id,
        )


def test_missing_chain_head_fails_closed_without_writing(db: Session):
    actor = make_actor(db)
    db.commit()

    with pytest.raises(AuditChainHeadNotFound, match="not provisioned"):
        append_audit_event(db, **append_kwargs(actor))

    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("stream_key", ""),
        ("stream_key", "   "),
        ("action", ""),
        ("aggregate_type", ""),
        ("aggregate_id", ""),
        ("request_id", ""),
    ],
)
def test_required_event_coordinates_reject_empty_values(
    db: Session,
    field: str,
    value: str,
):
    actor = make_actor(db)
    head = seed_head(db)
    db.commit()
    kwargs = append_kwargs(actor)
    kwargs[field] = value

    with pytest.raises(AuditChainValidationError):
        append_audit_event(db, **kwargs)

    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    db.refresh(head)
    assert head.version == 0
    assert head.last_event_id is None
    assert head.last_hash is None


def test_naive_timestamp_and_non_json_payload_are_rejected(db: Session):
    actor = make_actor(db)
    seed_head(db)
    db.commit()
    kwargs = append_kwargs(actor)
    kwargs["occurred_at"] = datetime(2026, 8, 30, 8, 9, 10)

    with pytest.raises(AuditChainValidationError, match="timezone"):
        append_audit_event(db, **kwargs)

    kwargs = append_kwargs(actor)
    kwargs["after_jsonb"] = {"invalid": float("nan")}
    with pytest.raises(AuditChainValidationError, match="canonical JSON"):
        append_audit_event(db, **kwargs)
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_explicit_created_at_is_normalized_and_cannot_precede_event(
    db: Session,
):
    actor = make_actor(db)
    head = seed_head(db)
    db.commit()
    kwargs = append_kwargs(actor)

    kwargs["created_at"] = datetime(2026, 8, 30, 16, 9, 11)
    with pytest.raises(AuditChainValidationError, match="created_at.*timezone"):
        append_audit_event(db, **kwargs)

    kwargs["created_at"] = datetime(
        2026,
        8,
        30,
        16,
        9,
        9,
        123456,
        tzinfo=timezone(timedelta(hours=8)),
    )
    with pytest.raises(AuditChainValidationError, match="earlier than occurred_at"):
        append_audit_event(db, **kwargs)

    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    db.refresh(head)
    assert head.version == 0

    kwargs["created_at"] = datetime(
        2026,
        8,
        30,
        16,
        9,
        10,
        123456,
        tzinfo=timezone(timedelta(hours=8)),
    )
    audit_event = append_audit_event(db, **kwargs)

    assert audit_event.occurred_at == NOW
    assert audit_event.created_at == datetime(
        2026,
        8,
        30,
        8,
        9,
        10,
        123456,
        tzinfo=timezone.utc,
    )


def test_database_failure_requires_rollback_and_leaves_chain_unchanged(
    db: Session,
):
    actor = make_actor(db)
    seed_head(db)
    db.commit()
    kwargs = append_kwargs(actor)
    kwargs["actor_user_id"] = str(uuid.uuid4())

    with pytest.raises(IntegrityError):
        append_audit_event(db, **kwargs)

    db.rollback()
    head = db.scalar(
        select(AuditChainHead).where(
            AuditChainHead.stream_key == STREAM_KEY
        )
    )
    assert head is not None
    assert head.version == 0
    assert head.last_event_id is None
    assert head.last_hash is None
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_successful_append_can_be_rolled_back_by_transaction_owner(db: Session):
    actor = make_actor(db)
    seed_head(db)
    db.commit()

    append_audit_event(db, **append_kwargs(actor))
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
    db.rollback()

    head = db.scalar(
        select(AuditChainHead).where(
            AuditChainHead.stream_key == STREAM_KEY
        )
    )
    assert head is not None
    assert head.version == 0
    assert head.last_event_id is None
    assert head.last_hash is None
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
