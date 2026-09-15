from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_material_request_approval_service import approval_db
from test_material_request_draft_service import make_world

from app.foundation_models import NotificationDelivery, NotificationEvent, NotificationRecipient
from app.formal_services.notification_inbox import (
    NotificationInboxError,
    list_notification_inbox,
    mark_notification_read,
)


NOW = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)


def _delivery(
    db,
    *,
    user_id: str,
    status: str = "sent",
    created_at: datetime = NOW,
) -> NotificationDelivery:
    event = NotificationEvent(
        event_type="shipment_handover_registered",
        business_type="shipment",
        business_id=str(uuid4()),
        dedup_key=f"inbox-event:{uuid4()}",
        payload_jsonb={"title": "包裹已发运"},
        status="expanded",
        occurred_at=created_at,
        created_at=created_at,
    )
    db.add(event)
    db.flush()
    recipient = NotificationRecipient(
        event_id=event.id,
        user_id=user_id,
        channel="wechat",
        recipient_key=f"user:{user_id}:{uuid4()}",
        status="active",
        created_at=created_at,
    )
    db.add(recipient)
    db.flush()
    delivery = NotificationDelivery(
        recipient_id=recipient.id,
        delivery_key=f"inbox-delivery:{uuid4()}",
        status=status,
        attempts=1,
        created_at=created_at,
        updated_at=created_at,
        sent_at=created_at if status in {"sent", "delivered"} else None,
    )
    db.add(delivery)
    db.flush()
    return delivery


def test_inbox_is_user_scoped_and_counts_only_unread_sent_or_delivered(approval_db):
    world = make_world(approval_db)
    own = _delivery(approval_db, user_id=world.actor_user.id)
    _delivery(approval_db, user_id=world.admin_users[0].id)

    page = list_notification_inbox(approval_db, user_id=world.actor_user.id)

    assert [row.delivery_id for row in page.items] == [own.id]
    assert page.unread_count == 1


def test_inbox_orders_newest_first_and_cursor_returns_older_rows(approval_db):
    world = make_world(approval_db)
    older = _delivery(
        approval_db,
        user_id=world.actor_user.id,
        created_at=NOW - timedelta(minutes=1),
    )
    newer = _delivery(approval_db, user_id=world.actor_user.id, created_at=NOW)

    first = list_notification_inbox(approval_db, user_id=world.actor_user.id, limit=1)
    second = list_notification_inbox(
        approval_db,
        user_id=world.actor_user.id,
        limit=1,
        after_id=first.next_after_id,
    )

    assert [item.delivery_id for item in first.items] == [newer.id]
    assert first.next_after_id == newer.id
    assert [item.delivery_id for item in second.items] == [older.id]
    assert second.next_after_id is None


def test_mark_read_is_monotonic_and_does_not_cross_user_boundary(approval_db):
    world = make_world(approval_db)
    delivery = _delivery(approval_db, user_id=world.actor_user.id, status="delivered")

    result = mark_notification_read(
        approval_db,
        user_id=world.actor_user.id,
        delivery_id=delivery.id,
        now=NOW,
    )
    assert result.status == "read"
    assert result.read_at == NOW
    assert mark_notification_read(
        approval_db,
        user_id=world.actor_user.id,
        delivery_id=delivery.id,
        now=NOW,
    ).status == "read"
    with pytest.raises(NotificationInboxError) as exc:
        mark_notification_read(
            approval_db,
            user_id=world.admin_users[0].id,
            delivery_id=delivery.id,
        )
    assert exc.value.http_status_code == 404


def test_queued_notification_cannot_be_marked_read(approval_db):
    world = make_world(approval_db)
    delivery = _delivery(approval_db, user_id=world.actor_user.id, status="queued")

    with pytest.raises(NotificationInboxError, match="has not been sent"):
        mark_notification_read(
            approval_db,
            user_id=world.actor_user.id,
            delivery_id=delivery.id,
        )
