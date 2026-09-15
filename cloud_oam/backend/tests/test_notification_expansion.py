from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_material_request_approval_service import approval_db

from app.foundation_models import (
    NotificationDelivery,
    NotificationEvent,
    NotificationRecipient,
)
from app.formal_services.notification_expansion import (
    NotificationExpansionError,
    expand_notification_event,
    expand_pending_notification_events,
)


NOW = datetime(2026, 9, 15, 1, 20, tzinfo=timezone.utc)


def _event(db, *, status: str = "pending") -> NotificationEvent:
    event = NotificationEvent(
        event_type="shipment_handover_registered",
        business_type="shipment",
        business_id=str(uuid4()),
        dedup_key=f"event:{uuid4()}",
        payload_jsonb={"tracking_no": "TEST-001"},
        status=status,
        occurred_at=NOW,
        created_at=NOW,
    )
    db.add(event)
    db.flush()
    return event


def _recipient(db, event: NotificationEvent, *, status: str = "active"):
    recipient = NotificationRecipient(
        event_id=event.id,
        user_id=None,
        channel="wechat",
        recipient_key=f"recipient:{uuid4()}",
        status=status,
        created_at=NOW,
    )
    db.add(recipient)
    db.flush()
    return recipient


def test_expansion_creates_one_queued_delivery_per_active_recipient(approval_db):
    event = _event(approval_db)
    first = _recipient(approval_db, event)
    second = _recipient(approval_db, event)
    _recipient(approval_db, event, status="suppressed")

    result = expand_notification_event(approval_db, event_id=event.id, now=NOW)

    assert result.event_status == "expanded"
    assert result.active_recipient_count == 2
    assert result.created_delivery_count == 2
    deliveries = tuple(
        approval_db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.recipient_id.in_((first.id, second.id)))
            .order_by(NotificationDelivery.delivery_key)
        ).all()
    )
    assert [delivery.status for delivery in deliveries] == ["queued", "queued"]
    assert all(delivery.attempts == 0 for delivery in deliveries)


def test_expansion_is_idempotent_after_event_is_already_expanded(approval_db):
    event = _event(approval_db)
    _recipient(approval_db, event)

    first = expand_notification_event(approval_db, event_id=event.id, now=NOW)
    second = expand_notification_event(approval_db, event_id=event.id, now=NOW)

    assert first.created_delivery_count == 1
    assert second.created_delivery_count == 0
    assert approval_db.scalar(
        select(NotificationDelivery).join(NotificationRecipient).where(
            NotificationRecipient.event_id == event.id
        )
    ) is not None
    assert approval_db.scalar(
        select(NotificationDelivery.recipient_id)
        .join(NotificationRecipient)
        .where(NotificationRecipient.event_id == event.id)
    ) is not None
    assert len(
        tuple(
            approval_db.scalars(
                select(NotificationDelivery)
                .join(NotificationRecipient)
                .where(NotificationRecipient.event_id == event.id)
            ).all()
        )
    ) == 1


def test_cancelled_event_never_creates_delivery(approval_db):
    event = _event(approval_db, status="cancelled")
    _recipient(approval_db, event)

    result = expand_notification_event(approval_db, event_id=event.id, now=NOW)

    assert result.event_status == "cancelled"
    assert result.created_delivery_count == 0
    assert approval_db.scalar(
        select(NotificationDelivery.id).join(NotificationRecipient).where(
            NotificationRecipient.event_id == event.id
        )
    ) is None


def test_missing_event_fails_closed(approval_db):
    with pytest.raises(NotificationExpansionError):
        expand_notification_event(approval_db, event_id=uuid4(), now=NOW)


def test_pending_batch_is_bounded_and_expands_only_pending_events(approval_db):
    first = _event(approval_db)
    _recipient(approval_db, first)
    second = _event(approval_db)
    _recipient(approval_db, second)
    cancelled = _event(approval_db, status="cancelled")
    _recipient(approval_db, cancelled)

    results = expand_pending_notification_events(approval_db, limit=1, now=NOW)

    assert len(results) == 1
    assert results[0].event_status == "expanded"
    assert sum(
        1
        for event in (first, second)
        if event.status == "expanded"
    ) == 1
    with pytest.raises(NotificationExpansionError):
        expand_pending_notification_events(approval_db, limit=0, now=NOW)
