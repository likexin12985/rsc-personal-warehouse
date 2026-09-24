"""Expand one notification event into durable, idempotent deliveries.

The expansion step deliberately stops before any provider call.  It turns the
business notification fact into one delivery fact per active recipient and
leaves sending, provider acknowledgement, retry, and read state to a later
worker.  Keeping those steps separate prevents a queued notification from
being mistaken for a delivered message.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..foundation_models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationRecipient,
)


class NotificationExpansionError(RuntimeError):
    """A notification event cannot be expanded safely."""


@dataclass(frozen=True)
class NotificationExpansionResult:
    event_id: UUID
    event_status: str
    created_delivery_count: int
    active_recipient_count: int


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def expand_notification_event(
    db: Session,
    *,
    event_id: UUID,
    now: datetime | None = None,
) -> NotificationExpansionResult:
    """Create queued deliveries for every active recipient of ``event_id``.

    The event row is locked for the duration of the expansion.  This makes
    concurrent workers serialize on one event, while the unique recipient and
    delivery keys keep a repeated invocation idempotent even after a retry.
    A cancelled event is never expanded and is returned as-is.
    """

    event = db.scalar(
        select(NotificationEvent)
        .where(NotificationEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        raise NotificationExpansionError("notification event does not exist")

    if event.status == "cancelled":
        return NotificationExpansionResult(
            event_id=event.id,
            event_status=event.status,
            created_delivery_count=0,
            active_recipient_count=0,
        )

    occurred_at = _utc(now or event.occurred_at)
    recipients = tuple(
        db.scalars(
            select(NotificationRecipient)
            .where(
                NotificationRecipient.event_id == event.id,
                NotificationRecipient.status == "active",
            )
            .order_by(NotificationRecipient.id)
            # Recipient coordinates are append-only for the API role. The
            # parent event FOR UPDATE lock serializes expansion and the FK
            # binding; no recipient UPDATE privilege is needed for this read.
        ).all()
    )

    created = 0
    for recipient in recipients:
        existing = db.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.recipient_id == recipient.id
            )
        )
        if existing is not None:
            continue
        db.add(
            NotificationDelivery(
                recipient_id=recipient.id,
                delivery_key=f"notification:{event.id}:{recipient.id}",
                status="queued",
                attempts=0,
                created_at=occurred_at,
                updated_at=occurred_at,
            )
        )
        created += 1

    event.status = "expanded"
    db.flush()
    return NotificationExpansionResult(
        event_id=event.id,
        event_status=event.status,
        created_delivery_count=created,
        active_recipient_count=len(recipients),
    )


def expand_pending_notification_events(
    db: Session,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> tuple[NotificationExpansionResult, ...]:
    """Expand a bounded batch without claiming provider delivery.

    ``skip_locked`` lets multiple workers divide pending events on
    PostgreSQL.  The event-level lock in :func:`expand_notification_event`
    remains the authoritative idempotency boundary for retries.
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
        raise NotificationExpansionError("notification expansion limit is invalid")
    event_ids = tuple(
        db.scalars(
            select(NotificationEvent.id)
            .where(NotificationEvent.status == "pending")
            .order_by(NotificationEvent.created_at, NotificationEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    return tuple(
        expand_notification_event(db, event_id=event_id, now=now)
        for event_id in event_ids
    )


__all__ = [
    "NotificationExpansionError",
    "NotificationExpansionResult",
    "expand_notification_event",
    "expand_pending_notification_events",
]
