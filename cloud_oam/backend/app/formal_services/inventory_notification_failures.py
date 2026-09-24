"""Durable, source-scoped quarantine facts for inventory notifications.

The existing append-only operator audit stream owns these facts, not the
inventory stream or another consumer's Outbox status. No provider is called.
A blocked source is never automatically retried merely because a worker
restarts; an explicit operator recheck must reprove the original source.
"""
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from uuid import UUID

from sqlalchemy import String, cast, func, select, text
from sqlalchemy.orm import Session

from ..foundation_models import AuditEvent, OutboxEvent
from .audit_chain import append_audit_event


STREAM = "material_request"
AGGREGATE = "inventory_notification_source"
BLOCKED_ACTION = "inventory_notification.source_blocked"
SOURCE_INVALID = "inventory_notification_source_invalid"
EVENT_CONFLICT = "inventory_notification_event_conflict"


@dataclass(frozen=True)
class InventoryNotificationFailure:
    outbox_id: UUID
    audit_id: UUID
    code: str
    created: bool


def blocked_source_exists():
    # UUID.hex is this consumer's stable object key in the audit namespace.
    # Normalize the source UUID expression, leaving the indexed audit column
    # untouched on both PostgreSQL's uuid and SQLite's CHAR(32) representation.
    return select(AuditEvent.id).where(
        AuditEvent.stream_key == STREAM,
        AuditEvent.aggregate_type == AGGREGATE,
        AuditEvent.aggregate_id == func.replace(cast(OutboxEvent.id, String), "-", ""),
        AuditEvent.action == BLOCKED_ACTION,
    ).exists()


def try_source_lock(db: Session, outbox_id: UUID) -> bool:
    if db.get_bind().dialect.name == "sqlite":
        return True  # Concurrency is proved separately with real PostgreSQL.
    if db.get_bind().dialect.name != "postgresql":
        raise RuntimeError("unsupported inventory notification database")
    key = int.from_bytes(hashlib.sha256(f"inventory-notification-source:{outbox_id}".encode()).digest()[:8], "big", signed=True)
    return bool(db.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"), {"key": key}))


def source_failure(db: Session, outbox_id: UUID) -> AuditEvent | None:
    return db.scalars(select(AuditEvent).where(
        AuditEvent.stream_key == STREAM, AuditEvent.aggregate_type == AGGREGATE,
        AuditEvent.aggregate_id == outbox_id.hex, AuditEvent.action == BLOCKED_ACTION,
    )).one_or_none()


def source_fingerprint(db: Session, outbox_id: UUID) -> str:
    source = db.get(OutboxEvent, outbox_id, populate_existing=True)
    document = None if source is None else {
        "outbox_id": str(source.id), "event_type": source.event_type,
        "aggregate_type": source.aggregate_type, "aggregate_id": source.aggregate_id,
        "payload": source.payload_jsonb,
    }
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def ensure_outer_transaction(db: Session) -> None:
    """Keep SQLite savepoints inside the caller's real transaction."""
    connection = db.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def record_source_failure(
    db: Session, *, outbox_id: UUID, code: str, now: datetime,
) -> InventoryNotificationFailure:
    """Append once while the caller holds the source lock; never commit here."""
    if not isinstance(outbox_id, UUID) or code not in {SOURCE_INVALID, EVENT_CONFLICT}:
        raise ValueError("invalid inventory notification failure coordinates")
    previous = source_failure(db, outbox_id)
    if previous is not None:
        return InventoryNotificationFailure(outbox_id, previous.id, previous.after_jsonb["code"], False)
    fingerprint = source_fingerprint(db, outbox_id)
    event = append_audit_event(
        db, stream_key=STREAM, actor_user_id=None, action=BLOCKED_ACTION,
        aggregate_type=AGGREGATE, aggregate_id=outbox_id.hex,
        before_jsonb={}, after_jsonb={"schema": "inventory-notification-source-failure.v1",
            "outbox_id": str(outbox_id), "code": code, "source_sha256": fingerprint},
        request_id=f"inventory-notification-blocked:{outbox_id.hex}",
        occurred_at=now, created_at=now,
    )
    return InventoryNotificationFailure(outbox_id, event.id, code, True)
