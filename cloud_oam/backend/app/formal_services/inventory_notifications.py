"""Project immutable inventory Outbox facts into notifications, without sending.

This consumer owns no stock, audit or Outbox status. A notification dedup key
is its durable checkpoint; a transaction-scoped advisory lock serializes only
consumers of the same transaction. Existing business notifications suppress
duplicate recipients, including failed or unknown deliveries: those must be
handled through their original delivery records, never by this consumer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from uuid import UUID

from sqlalchemy import and_, literal, or_, select, text
from sqlalchemy.orm import Session

from ..foundation_models import AuditEvent, NotificationEvent, NotificationPersonTarget, NotificationRecipient, OutboxEvent
from ..inventory_models import InventoryMovement, InventoryTransaction, StockAccount
from ..models import User
from .notification_events import NotificationEventError, record_business_notification
from .inventory_notification_failures import (
    EVENT_CONFLICT, SOURCE_INVALID, InventoryNotificationFailure,
    blocked_source_exists, ensure_outer_transaction, record_source_failure, source_failure, try_source_lock,
)


INVENTORY_EVENT_TYPES = frozenset({
    "inventory.transaction.posted", "inventory.transaction.reversed",
    "inventory.transaction.stocktake_difference_posted",
})
EVENT_TYPE = "inventory_transaction_changed"
# Exact existing event/business contracts; matching a free-form JSON field in
# any notification is not sufficient proof that a custodian was covered.
DEDICATED_EVENTS = (
    ("personal_inbound_posted", "inbound_order", "inventory_transaction_id"),
    ("work_order_material_operation_posted", "work_order_material_operation", "posting_transaction_id"),
    ("stock_return_submitted", "stock_operation_order", "posting_transaction_id"),
    ("stock_return_cancelled", "stock_operation_cancellation", "posting_transaction_id"),
    ("stock_return_outbound", "stock_operation_outbound", "posting_transaction_id"),
    ("stock_return_inbound_posted", "stock_operation_return_inbound", "inventory_transaction_id"),
)


class InventoryNotificationError(RuntimeError):
    """The source fact cannot safely become a notification."""


@dataclass(frozen=True)
class InventoryNotificationResult:
    transaction_id: UUID
    event_id: UUID
    created: bool
    affected_person_count: int
    already_notified_person_count: int
    recipient_count: int


@dataclass(frozen=True)
class InventoryNotificationBatchResult:
    projected: tuple[InventoryNotificationResult, ...] = ()
    blocked: tuple[InventoryNotificationFailure, ...] = ()
    deferred: int = 0


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryNotificationError(message)


def _try_transaction_lock(db: Session, transaction_id: UUID) -> bool:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        return True  # Unit tests do not claim PostgreSQL concurrency semantics.
    _require(dialect == "postgresql", "unsupported notification database")
    key = int.from_bytes(hashlib.sha256(f"inventory-notification:{transaction_id}".encode()).digest()[:8], "big", signed=True)
    return bool(db.scalar(text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"), {"key": key}))


def project_inventory_notification(
    db: Session, *, outbox_id: UUID, now: datetime | None = None,
) -> InventoryNotificationResult | None:
    """Record one inventory notification, or defer an already-owned object.

    Only the caller commits. Failure must roll back this consumer transaction;
    the previously committed inventory transaction is never modified.
    """
    _require(isinstance(outbox_id, UUID), "invalid inventory Outbox identity")
    source = db.get(OutboxEvent, outbox_id, populate_existing=True)
    _require(source is not None and source.aggregate_type == "inventory_transaction"
             and source.event_type in INVENTORY_EVENT_TYPES, "unsupported inventory Outbox event")
    try:
        transaction_id = UUID(source.aggregate_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise InventoryNotificationError("invalid inventory transaction identity") from exc
    _require(str(transaction_id) == source.aggregate_id, "noncanonical inventory transaction identity")
    if not _try_transaction_lock(db, transaction_id):
        return None
    transaction = db.get(InventoryTransaction, transaction_id, populate_existing=True)
    _require(transaction is not None and transaction.status == "posted", "inventory fact is missing")
    effective_now = _utc(now or datetime.now(timezone.utc))
    _require(_utc(source.available_at) <= effective_now, "inventory event is not available yet")
    expected_type = (
        "inventory.transaction.reversed" if transaction.reversed_transaction_id is not None
        else "inventory.transaction.stocktake_difference_posted" if transaction.source_document_type == "stocktake_difference"
        else "inventory.transaction.posted"
    )
    _require(source.event_type == expected_type, "inventory event kind does not match its transaction")
    siblings = tuple(db.scalars(select(OutboxEvent.id).where(
        OutboxEvent.aggregate_type == "inventory_transaction", OutboxEvent.aggregate_id == str(transaction_id),
        OutboxEvent.event_type.in_(INVENTORY_EVENT_TYPES),
    )))
    _require(siblings == (source.id,), "inventory Outbox source is ambiguous")
    expected_payload = {
        "transaction_id": str(transaction.id), "transaction_no": transaction.transaction_no,
        "movement_type": transaction.movement_type, "ledger_cursor": transaction.ledger_cursor,
        "reversed_transaction_id": str(transaction.reversed_transaction_id) if transaction.reversed_transaction_id else None,
    }
    _require(isinstance(source.payload_jsonb, dict) and type(source.payload_jsonb.get("ledger_cursor")) is int
             and source.payload_jsonb == expected_payload, "inventory Outbox does not match its immutable transaction")
    movements = tuple(db.scalars(select(InventoryMovement).where(
        InventoryMovement.transaction_id == transaction_id).order_by(InventoryMovement.line_no)))
    _require(bool(movements) and [row.line_no for row in movements] == list(range(1, len(movements) + 1)),
             "inventory movement facts are missing or incomplete")
    _require(all(isinstance(row.quantity, Decimal) and row.quantity > 0 for row in movements), "invalid inventory quantities")
    audits = tuple(db.scalars(select(AuditEvent).where(
        AuditEvent.stream_key == "inventory", AuditEvent.action == source.event_type,
        AuditEvent.aggregate_type == "inventory_transaction", AuditEvent.aggregate_id == str(transaction_id),
    )))
    _require(len(audits) == 1, "inventory audit evidence is missing or ambiguous")
    audit = audits[0].after_jsonb
    _require(isinstance(audit, dict) and type(audit.get("ledger_cursor")) is int
             and type(audit.get("movement_count")) is int and all(audit.get(key) == value for key, value in {
        "status": "posted", "ledger_cursor": transaction.ledger_cursor,
        "movement_type": transaction.movement_type, "movement_count": len(movements),
        "posting_key": transaction.posting_key,
    }.items()), "inventory audit does not match its transaction")
    account_ids = {account_id for row in movements for account_id in (row.from_account_id, row.to_account_id) if account_id}
    accounts = tuple(db.scalars(select(StockAccount).where(StockAccount.id.in_(account_ids))))
    _require({row.id for row in accounts} == account_ids, "inventory custodian account is missing")
    affected = {row.custodian_person_id for row in accounts if row.custodian_person_id is not None}
    dedicated = or_(*(and_(NotificationEvent.event_type == kind, NotificationEvent.business_type == business,
                          NotificationEvent.payload_jsonb[key].as_string() == str(transaction_id))
                      for kind, business, key in DEDICATED_EVENTS))
    # A new dedicated event already owns its intended person even when no
    # channel is bound yet. Recovery belongs to that original target, not a
    # second generic notification. Keep the legacy recipient fallback only
    # for events whose original audience was never recorded.
    covered = set(db.scalars(select(NotificationPersonTarget.person_id)
        .join(NotificationEvent, NotificationEvent.id == NotificationPersonTarget.event_id)
        .where(dedicated, NotificationEvent.target_manifest_sha256.is_not(None))))
    covered.update(db.scalars(
        select(User.person_id).join(NotificationRecipient, NotificationRecipient.user_id == User.id)
        .join(NotificationEvent, NotificationEvent.id == NotificationRecipient.event_id)
        .where(dedicated, NotificationEvent.target_manifest_sha256.is_(None))))
    covered &= affected
    dedup_key = f"inventory-change:{transaction_id}"
    existing = db.scalar(select(NotificationEvent).where(NotificationEvent.dedup_key == dedup_key))
    intended = affected - covered
    if existing is not None and existing.target_manifest_sha256 is not None:
        # Later identity mapping or a later dedicated event cannot reinterpret
        # the first committed generic audience. The recorder verifies its seal.
        intended = set(db.scalars(select(NotificationPersonTarget.person_id)
            .where(NotificationPersonTarget.event_id == existing.id)))
        _require(intended <= affected, "notification target does not match its inventory custodian")
        covered = affected - intended
    # Shared event payload contains no accounts, other custodians, recipient
    # coordinates or aggregate quantities. Authorized stock queries provide
    # the per-person detail; this event is only evidence that a change occurred.
    result = record_business_notification(
        db, event_type=EVENT_TYPE, business_type="inventory_transaction", business_id=transaction_id,
        dedup_key=dedup_key, payload=expected_payload,
        recipient_person_id=None, recipient_person_ids=tuple(intended),
        occurred_at=transaction.posted_at, now=effective_now,
    )
    return InventoryNotificationResult(transaction_id, result.event.id, existing is None,
                                       len(affected), len(covered), result.recipient_count)


def project_pending_inventory_notifications(
    db: Session, *, limit: int = 100, now: datetime | None = None,
) -> InventoryNotificationBatchResult:
    """Consume a bounded inventory batch without mutating another consumer's Outbox status."""
    _require(type(limit) is int and 1 <= limit <= 500, "invalid inventory notification batch limit")
    effective_now = _utc(now or datetime.now(timezone.utc))
    # sqlite3's legacy transaction mode otherwise lets RELEASE SAVEPOINT
    # commit a batch before the caller commits. PostgreSQL already has a real
    # outer transaction. Match the explicit boundary used by other services.
    ensure_outer_transaction(db)
    completed = select(NotificationEvent.id).where(
        NotificationEvent.dedup_key == literal("inventory-change:") + OutboxEvent.aggregate_id,
    ).exists()
    ids = tuple(db.scalars(select(OutboxEvent.id).where(
        OutboxEvent.aggregate_type == "inventory_transaction", OutboxEvent.event_type.in_(INVENTORY_EVENT_TYPES),
        OutboxEvent.available_at <= effective_now, ~completed, ~blocked_source_exists(),
    ).order_by(OutboxEvent.created_at, OutboxEvent.id).limit(limit)))
    results = []
    failures = []
    deferred = 0
    for outbox_id in ids:
        # Keep this ownership outside the savepoint: rolling back a rejected
        # source must not release ownership before its failure fact is durable.
        if not try_source_lock(db, outbox_id):
            deferred += 1
            continue
        if source_failure(db, outbox_id) is not None:
            continue  # Another committed consumer isolated it after selection.
        try:
            with db.begin_nested():
                result = project_inventory_notification(db, outbox_id=outbox_id, now=effective_now)
        except (InventoryNotificationError, NotificationEventError) as exc:
            failures.append(record_source_failure(db, outbox_id=outbox_id,
                code=EVENT_CONFLICT if isinstance(exc, NotificationEventError) else SOURCE_INVALID,
                now=effective_now))
            continue
        if result is not None:
            results.append(result)
        else:
            deferred += 1
    return InventoryNotificationBatchResult(tuple(results), tuple(failures), deferred)
