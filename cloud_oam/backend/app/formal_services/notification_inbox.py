"""User-scoped notification inbox and monotonic read-state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..foundation_models import NotificationDelivery, NotificationEvent, NotificationRecipient


class NotificationInboxError(RuntimeError):
    def __init__(self, message: str, *, http_status_code: int = 409) -> None:
        super().__init__(message)
        self.http_status_code = http_status_code


@dataclass(frozen=True)
class NotificationInboxItem:
    delivery_id: UUID
    event_id: UUID
    event_type: str
    business_type: str
    business_id: str
    channel: str
    status: str
    payload: dict[str, Any]
    occurred_at: datetime
    created_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None


@dataclass(frozen=True)
class NotificationInboxPage:
    items: tuple[NotificationInboxItem, ...]
    next_after_id: UUID | None
    unread_count: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _item(delivery: NotificationDelivery, recipient: NotificationRecipient, event: NotificationEvent) -> NotificationInboxItem:
    return NotificationInboxItem(
        delivery_id=delivery.id,
        event_id=event.id,
        event_type=event.event_type,
        business_type=event.business_type,
        business_id=event.business_id,
        channel=recipient.channel,
        status=delivery.status,
        payload=dict(event.payload_jsonb),
        occurred_at=_utc(event.occurred_at),
        created_at=_utc(delivery.created_at),
        sent_at=_utc(delivery.sent_at) if delivery.sent_at else None,
        delivered_at=_utc(delivery.delivered_at) if delivery.delivered_at else None,
        read_at=_utc(delivery.read_at) if delivery.read_at else None,
    )


def _base_query(*, user_id: str):
    return (
        select(NotificationDelivery, NotificationRecipient, NotificationEvent)
        .join(NotificationRecipient, NotificationRecipient.id == NotificationDelivery.recipient_id)
        .join(NotificationEvent, NotificationEvent.id == NotificationRecipient.event_id)
        .where(
            NotificationRecipient.user_id == user_id,
            NotificationRecipient.status == "active",
        )
    )


def list_notification_inbox(
    db: Session,
    *,
    user_id: str,
    limit: int = 50,
    after_id: UUID | None = None,
) -> NotificationInboxPage:
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 36:
        raise NotificationInboxError("notification user identity is invalid", http_status_code=403)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise NotificationInboxError("notification limit is invalid", http_status_code=422)

    cursor: NotificationDelivery | None = None
    if after_id is not None:
        cursor = db.scalar(
            select(NotificationDelivery)
            .join(NotificationRecipient, NotificationRecipient.id == NotificationDelivery.recipient_id)
            .where(
                NotificationDelivery.id == after_id,
                NotificationRecipient.user_id == user_id,
                NotificationRecipient.status == "active",
            )
        )
        if cursor is None:
            raise NotificationInboxError("notification cursor does not belong to current user", http_status_code=404)

    query = _base_query(user_id=user_id)
    if cursor is not None:
        query = query.where(
            or_(
                NotificationDelivery.created_at > cursor.created_at,
                and_(
                    NotificationDelivery.created_at == cursor.created_at,
                    NotificationDelivery.id > cursor.id,
                ),
            )
        )
    rows = tuple(
        db.execute(
            query.order_by(NotificationDelivery.created_at, NotificationDelivery.id).limit(limit + 1)
        ).all()
    )
    has_next = len(rows) > limit
    page_rows = rows[:limit]
    items = tuple(_item(delivery, recipient, event) for delivery, recipient, event in page_rows)
    unread_count = int(
        db.scalar(
            select(func.count(NotificationDelivery.id))
            .join(NotificationRecipient, NotificationRecipient.id == NotificationDelivery.recipient_id)
            .where(
                NotificationRecipient.user_id == user_id,
                NotificationRecipient.status == "active",
                NotificationDelivery.status.in_(("sent", "delivered")),
            )
        )
        or 0
    )
    return NotificationInboxPage(
        items=items,
        next_after_id=items[-1].delivery_id if has_next and items else None,
        unread_count=unread_count,
    )


def mark_notification_read(
    db: Session,
    *,
    user_id: str,
    delivery_id: UUID,
    now: datetime | None = None,
) -> NotificationInboxItem:
    row = db.execute(
        _base_query(user_id=user_id)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update()
    ).first()
    if row is None:
        raise NotificationInboxError("notification does not belong to current user", http_status_code=404)
    delivery, recipient, event = row
    if delivery.status in {"queued", "sending"}:
        raise NotificationInboxError("notification has not been sent")
    if delivery.status in {"failed", "cancelled"}:
        raise NotificationInboxError("notification is not readable")
    if delivery.status != "read":
        effective_at = _utc(now or datetime.now(timezone.utc))
        delivery.status = "read"
        delivery.read_at = effective_at
        delivery.updated_at = effective_at
        db.flush()
    return _item(delivery, recipient, event)


__all__ = [
    "NotificationInboxError",
    "NotificationInboxItem",
    "NotificationInboxPage",
    "list_notification_inbox",
    "mark_notification_read",
]
