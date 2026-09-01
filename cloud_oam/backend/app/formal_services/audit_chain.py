"""Append-only, hash-chained audit event writer for the V1.0 platform.

The caller owns the surrounding database transaction.  This module never
commits and never creates a chain head at runtime: every stream must be seeded
by a reviewed migration before it can accept events.  Keeping that boundary
fail-closed prevents two first writers from racing to create divergent genesis
events.

Payload redaction is deliberately a caller responsibility.  The writer stores
the supplied JSON document without removing or renaming fields, so callers
must exclude credentials, tokens, plaintext mobile numbers, and other secrets
before invoking it.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..foundation_models import AuditChainHead, AuditEvent


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TEXT_LIMITS = {
    "stream_key": 160,
    "actor_user_id": 36,
    "action": 100,
    "aggregate_type": 80,
    "aggregate_id": 64,
    "request_id": 160,
}


class AuditChainError(RuntimeError):
    """Base error for a fail-closed audit-chain append."""


class AuditChainValidationError(AuditChainError, ValueError):
    """The proposed audit event cannot be represented safely."""


class AuditChainHeadNotFound(AuditChainError):
    """The reviewed migration did not seed the requested audit stream."""


class AuditChainStateError(AuditChainError):
    """The persisted chain head is internally inconsistent."""


_PRELOCKED_AUDIT_STREAM_PROOF_SEAL = object()


@dataclass(frozen=True, slots=True)
class _PrelockedAuditStreamProof:
    """Unforgeable transaction-bound evidence that one stream head is held.

    This proof is intentionally private.  It can only be issued by the helper
    that takes the matching ``FOR UPDATE`` lock, so internal post-audit replay
    code no longer has to trust a caller-supplied boolean.
    """

    session: Session
    transaction: object
    stream_key: str
    seal: object


def append_audit_event(
    db: Session,
    *,
    stream_key: str,
    actor_user_id: str | None,
    action: str,
    aggregate_type: str,
    aggregate_id: str,
    before_jsonb: dict[str, Any] | None,
    after_jsonb: dict[str, Any] | None,
    request_id: str,
    occurred_at: datetime,
) -> AuditEvent:
    """Append one event and advance its pre-seeded chain head atomically.

    The matching ``AuditChainHead`` row is locked with ``FOR UPDATE`` before
    the previous hash is read.  The event insert and chain-head update are
    flushed into the caller's current transaction, but are never committed by
    this function.  Any flush error leaves that transaction failed, requiring
    the caller to roll it back rather than committing a business mutation
    without its audit evidence.
    """

    checked_stream_key = _require_text("stream_key", stream_key)
    checked_actor_user_id = _optional_text("actor_user_id", actor_user_id)
    checked_action = _require_text("action", action)
    checked_aggregate_type = _require_text("aggregate_type", aggregate_type)
    checked_aggregate_id = _require_text("aggregate_id", aggregate_id)
    checked_request_id = _require_text("request_id", request_id)
    checked_occurred_at = _require_aware_datetime(occurred_at)
    before_snapshot = _snapshot_json_document("before_jsonb", before_jsonb)
    after_snapshot = _snapshot_json_document("after_jsonb", after_jsonb)

    head = lock_audit_chain_head(db, stream_key=checked_stream_key)

    event_id = uuid.uuid4()
    stream_version = head.version + 1
    event_hash = calculate_audit_event_hash(
        stream_key=checked_stream_key,
        event_id=event_id,
        actor_user_id=checked_actor_user_id,
        action=checked_action,
        aggregate_type=checked_aggregate_type,
        aggregate_id=checked_aggregate_id,
        before_jsonb=before_snapshot,
        after_jsonb=after_snapshot,
        request_id=checked_request_id,
        previous_hash=head.last_hash,
        occurred_at=checked_occurred_at,
    )
    event = AuditEvent(
        id=event_id,
        stream_key=checked_stream_key,
        stream_version=stream_version,
        actor_user_id=checked_actor_user_id,
        action=checked_action,
        aggregate_type=checked_aggregate_type,
        aggregate_id=checked_aggregate_id,
        before_jsonb=before_snapshot,
        after_jsonb=after_snapshot,
        request_id=checked_request_id,
        previous_hash=head.last_hash,
        event_hash=event_hash,
        occurred_at=checked_occurred_at,
    )
    db.add(event)

    # Flush the immutable event before advancing the head.  If the insert
    # fails (for example, because the actor FK is invalid), the session cannot
    # be committed until its owner rolls back the entire business transaction.
    db.flush()
    head.last_event_id = event.id
    head.last_hash = event.event_hash
    head.version = stream_version
    db.flush()
    if (
        event.stream_key != head.stream_key
        or event.stream_version != head.version
        or event.id != head.last_event_id
        or event.event_hash != head.last_hash
    ):
        raise AuditChainStateError(
            "audit event was not bound to the exact stream head"
        )
    return event


def lock_audit_chain_head(
    db: Session,
    *,
    stream_key: str,
) -> AuditChainHead:
    """Lock and validate one pre-seeded audit stream head.

    Business services may call this before their final database-clock sample.
    A later :func:`append_audit_event` in the same transaction then cannot
    wait behind another audit writer after authorization has been finalized.
    """

    checked_stream_key = _require_text("stream_key", stream_key)
    head = db.scalar(
        select(AuditChainHead)
        .where(AuditChainHead.stream_key == checked_stream_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if head is None:
        raise AuditChainHeadNotFound(
            f"audit chain head is not provisioned: {checked_stream_key}"
        )
    _validate_chain_head(head)
    return head


def _lock_audit_chain_head_with_proof(
    db: Session,
    *,
    stream_key: str,
) -> tuple[AuditChainHead, _PrelockedAuditStreamProof]:
    """Lock one audit head and issue its private transaction-bound proof."""

    head = lock_audit_chain_head(db, stream_key=stream_key)
    transaction = db.get_transaction()
    if transaction is None:
        raise AuditChainStateError(
            "prelocked audit proof requires an active transaction"
        )
    proof = _PrelockedAuditStreamProof(
        session=db,
        transaction=transaction,
        stream_key=head.stream_key,
        seal=_PRELOCKED_AUDIT_STREAM_PROOF_SEAL,
    )
    return head, proof


def _require_prelocked_audit_stream_proof(
    db: Session,
    *,
    proof: object,
    stream_key: str,
) -> AuditChainHead:
    """Validate a proof and plainly refresh its already-held stream head."""

    checked_stream_key = _require_text("stream_key", stream_key)
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedAuditStreamProof)
        or proof.seal is not _PRELOCKED_AUDIT_STREAM_PROOF_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.stream_key != checked_stream_key
    ):
        raise AuditChainStateError(
            "prelocked audit proof does not belong to this transaction and stream"
        )
    head = db.scalar(
        select(AuditChainHead)
        .where(AuditChainHead.stream_key == checked_stream_key)
        .execution_options(populate_existing=True)
    )
    if head is None:
        raise AuditChainHeadNotFound(
            f"audit chain head is not provisioned: {checked_stream_key}"
        )
    _validate_chain_head(head)
    return head


def _verify_audit_event_with_prelocked_proof(
    db: Session,
    *,
    proof: object,
    stream_key: str,
    event_id: uuid.UUID,
) -> AuditEvent:
    """Pure hash walk guarded by an unforgeable prelocked-stream proof."""

    checked_stream_key = _require_text("stream_key", stream_key)
    if not isinstance(event_id, uuid.UUID) or event_id.int == 0:
        raise AuditChainValidationError("event_id must be a non-zero UUID")
    head = _require_prelocked_audit_stream_proof(
        db,
        proof=proof,
        stream_key=checked_stream_key,
    )
    return _verify_audit_event_from_head(
        db,
        checked_stream_key=checked_stream_key,
        event_id=event_id,
        head=head,
    )


def verify_audit_event_in_stream(
    db: Session,
    *,
    stream_key: str,
    event_id: uuid.UUID,
) -> AuditEvent:
    """Verify one event is reachable from the exact, locked stream head.

    Persisted stream coordinates make membership indexable, but they are not
    accepted on trust.  The complete hash path from the locked stream head is
    still walked to genesis, and every hop must carry the exact stream key and
    descending sequence number implied by that head.

    The matching mutable head is locked for the caller's current transaction.
    ``audit_events`` is an immutable, SELECT-only evidence table for the API
    role, so the hash walk deliberately uses ordinary reads after that head
    lock instead of attempting an unauthorized ``FOR UPDATE``.  This helper
    never commits or rolls back.
    """

    checked_stream_key = _require_text("stream_key", stream_key)
    if not isinstance(event_id, uuid.UUID) or event_id.int == 0:
        raise AuditChainValidationError("event_id must be a non-zero UUID")

    head = lock_audit_chain_head(db, stream_key=checked_stream_key)
    return _verify_audit_event_from_head(
        db,
        checked_stream_key=checked_stream_key,
        event_id=event_id,
        head=head,
    )


def _verify_audit_event_in_prelocked_stream(
    db: Session,
    *,
    stream_key: str,
    event_id: uuid.UUID,
) -> AuditEvent:
    """Pure hash walk for an internal caller that already locked the head.

    This entrypoint never issues ``FOR UPDATE``.  Its caller must already hold
    the exact stream-head row for the current transaction; the ordinary reread
    below refreshes that held row before the complete genesis walk.
    """

    checked_stream_key = _require_text("stream_key", stream_key)
    if not isinstance(event_id, uuid.UUID) or event_id.int == 0:
        raise AuditChainValidationError("event_id must be a non-zero UUID")
    head = db.scalar(
        select(AuditChainHead)
        .where(AuditChainHead.stream_key == checked_stream_key)
        .execution_options(populate_existing=True)
    )
    if head is None:
        raise AuditChainHeadNotFound(
            f"audit chain head is not provisioned: {checked_stream_key}"
        )
    _validate_chain_head(head)
    return _verify_audit_event_from_head(
        db,
        checked_stream_key=checked_stream_key,
        event_id=event_id,
        head=head,
    )


def _verify_audit_event_from_head(
    db: Session,
    *,
    checked_stream_key: str,
    event_id: uuid.UUID,
    head: AuditChainHead,
) -> AuditEvent:
    if head.version == 0:
        raise AuditChainStateError("empty audit chain cannot contain an event")
    total_event_count = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    if head.version > total_event_count:
        raise AuditChainStateError(
            "audit chain head version exceeds persisted event count"
        )

    expected_hash = head.last_hash
    expected_head_id = head.last_event_id
    target: AuditEvent | None = None
    visited: set[uuid.UUID] = set()
    for position in range(head.version):
        rows = tuple(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_hash == expected_hash)
            ).all()
        )
        if len(rows) != 1:
            raise AuditChainStateError(
                "audit chain hash does not resolve to exactly one event"
            )
        row = rows[0]
        if position == 0 and row.id != expected_head_id:
            raise AuditChainStateError(
                "audit chain head event and hash reference different rows"
            )
        if row.id in visited:
            raise AuditChainStateError("audit chain contains a cycle")
        if (
            row.stream_key != checked_stream_key
            or row.stream_version != head.version - position
        ):
            raise AuditChainStateError(
                "audit chain event stream coordinate is invalid"
            )
        visited.add(row.id)
        calculated = calculate_audit_event_hash(
            stream_key=checked_stream_key,
            event_id=row.id,
            actor_user_id=row.actor_user_id,
            action=row.action,
            aggregate_type=row.aggregate_type,
            aggregate_id=row.aggregate_id,
            before_jsonb=row.before_jsonb,
            after_jsonb=row.after_jsonb,
            request_id=row.request_id,
            previous_hash=row.previous_hash,
            occurred_at=row.occurred_at,
        )
        if row.event_hash != expected_hash or calculated != row.event_hash:
            raise AuditChainStateError("audit chain event hash is invalid")
        if row.id == event_id:
            target = row
        expected_hash = row.previous_hash

    if expected_hash is not None:
        raise AuditChainStateError(
            "audit chain head version does not reach genesis"
        )
    if target is None:
        raise AuditChainStateError(
            "audit event is not reachable from the requested stream head"
        )
    return target


def calculate_audit_event_hash(
    *,
    stream_key: str,
    event_id: uuid.UUID,
    actor_user_id: str | None,
    action: str,
    aggregate_type: str,
    aggregate_id: str,
    before_jsonb: dict[str, Any] | None,
    after_jsonb: dict[str, Any] | None,
    request_id: str,
    previous_hash: str | None,
    occurred_at: datetime,
) -> str:
    """Return the SHA-256 digest for one canonical audit-event document."""

    canonical_document = {
        "action": action,
        "actor_user_id": actor_user_id,
        "after_jsonb": after_jsonb,
        "aggregate_id": aggregate_id,
        "aggregate_type": aggregate_type,
        "before_jsonb": before_jsonb,
        "event_id": str(event_id),
        "occurred_at": _canonical_timestamp(occurred_at),
        "previous_hash": previous_hash,
        "request_id": request_id,
        "stream_key": stream_key,
    }
    try:
        canonical_json = json.dumps(
            canonical_document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise AuditChainValidationError(
            "audit event contains a value that is not canonical JSON"
        ) from exc
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _require_text(field: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditChainValidationError(f"{field} must be a non-empty string")
    limit = _TEXT_LIMITS[field]
    if len(value) > limit:
        raise AuditChainValidationError(
            f"{field} exceeds its {limit}-character database limit"
        )
    return value


def _optional_text(field: str, value: str | None) -> str | None:
    if value is None:
        return None
    return _require_text(field, value)


def _require_aware_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise AuditChainValidationError("occurred_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditChainValidationError("occurred_at must include a timezone")
    return value.astimezone(timezone.utc)


def _canonical_timestamp(value: datetime) -> str:
    # SQLite drops the timezone marker when a timestamptz value is reloaded.
    # Treat such persisted values as UTC so verification remains portable;
    # append_audit_event itself still rejects naive input at the boundary.
    if value.tzinfo is None or value.utcoffset() is None:
        normalized = value.replace(tzinfo=timezone.utc)
    else:
        normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_json_document(
    field: str,
    value: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AuditChainValidationError(f"{field} must be a JSON object or null")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AuditChainValidationError(
            f"{field} contains a value that is not canonical JSON"
        ) from exc
    return snapshot


def _validate_chain_head(head: AuditChainHead) -> None:
    has_event = head.last_event_id is not None
    has_hash = head.last_hash is not None
    if head.version < 0:
        raise AuditChainStateError("audit chain head version cannot be negative")
    if has_event != has_hash:
        raise AuditChainStateError(
            "audit chain head event/hash pair is inconsistent"
        )
    if head.version == 0 and has_event:
        raise AuditChainStateError(
            "empty audit chain head cannot reference a previous event"
        )
    if head.version > 0 and not has_event:
        raise AuditChainStateError(
            "non-empty audit chain head is missing its previous event"
        )
    if head.last_hash is not None and not _SHA256_PATTERN.fullmatch(head.last_hash):
        raise AuditChainStateError("audit chain head hash is not a SHA-256 digest")
