from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_material_request_approval_service import approval_db

from app.foundation_models import (
    NotificationAttempt,
    NotificationDelivery,
    NotificationEvent,
    NotificationRecipient,
)
from app.formal_services.notification_delivery import (
    NotificationDeliveryError,
    claim_notification_deliveries,
    record_notification_delivery_result,
    record_notification_provider_status,
)


NOW = datetime(2026, 9, 15, 1, 30, tzinfo=timezone.utc)
REQUEST_HASH = sha256(b"notification-test-request").hexdigest()


def _queued_delivery(db):
    event = NotificationEvent(
        event_type="shipment_handover_registered",
        business_type="shipment",
        business_id=str(uuid4()),
        dedup_key=f"delivery-event:{uuid4()}",
        payload_jsonb={"tracking_no": "TEST-002", "quantity": "1.000"},
        status="expanded",
        occurred_at=NOW,
        created_at=NOW,
    )
    db.add(event)
    db.flush()
    recipient = NotificationRecipient(
        event_id=event.id,
        user_id=None,
        channel="feishu",
        recipient_key=f"chat:{uuid4()}",
        status="active",
        created_at=NOW,
    )
    db.add(recipient)
    db.flush()
    delivery = NotificationDelivery(
        recipient_id=recipient.id,
        delivery_key=f"delivery:{uuid4()}",
        status="queued",
        attempts=0,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(delivery)
    db.flush()
    return delivery


def test_claim_is_single_owner_and_carries_exact_event_payload(approval_db):
    delivery = _queued_delivery(approval_db)

    claims = claim_notification_deliveries(
        approval_db, worker_id="worker-a", limit=10, now=NOW
    )

    assert len(claims) == 1
    assert claims[0].delivery_id == delivery.id
    assert claims[0].channel == "feishu"
    assert claims[0].payload == {"tracking_no": "TEST-002", "quantity": "1.000"}
    assert claims[0].attempt_no == 1
    assert delivery.status == "sending"
    assert delivery.locked_by == "worker-a"
    assert claim_notification_deliveries(
        approval_db, worker_id="worker-b", now=NOW
    ) == ()


def test_result_requires_the_active_worker_and_records_success_attempt(approval_db):
    delivery = _queued_delivery(approval_db)
    claim_notification_deliveries(approval_db, worker_id="worker-a", now=NOW)

    with pytest.raises(NotificationDeliveryError):
        record_notification_delivery_result(
            approval_db,
            delivery_id=delivery.id,
            worker_id="worker-b",
            request_hash=REQUEST_HASH,
            response_code="200",
            response_json={"ok": True},
            provider_message_id="provider-1",
            now=NOW,
        )

    result = record_notification_delivery_result(
        approval_db,
        delivery_id=delivery.id,
        worker_id="worker-a",
        request_hash=REQUEST_HASH,
        response_code="200",
        response_json={"ok": True},
        provider_message_id="provider-1",
        now=NOW,
    )
    assert result.status == "sent"
    assert result.locked_by is None
    assert result.provider_message_id == "provider-1"
    attempt = approval_db.scalar(
        select(NotificationAttempt).where(NotificationAttempt.delivery_id == delivery.id)
    )
    assert attempt is not None
    assert attempt.attempt_no == 1


def test_failed_result_is_recorded_without_automatic_replay(approval_db):
    delivery = _queued_delivery(approval_db)
    claim_notification_deliveries(approval_db, worker_id="worker-a", now=NOW)

    result = record_notification_delivery_result(
        approval_db,
        delivery_id=delivery.id,
        worker_id="worker-a",
        request_hash=REQUEST_HASH,
        response_code=None,
        response_json=None,
        error="provider rejected request",
        now=NOW,
    )

    assert result.status == "failed"
    assert result.last_error == "provider rejected request"
    assert claim_notification_deliveries(approval_db, worker_id="worker-a", now=NOW) == ()


def _sent_delivery(db):
    delivery = _queued_delivery(db)
    claim_notification_deliveries(db, worker_id="worker-a", now=NOW)
    return record_notification_delivery_result(
        db,
        delivery_id=delivery.id,
        worker_id="worker-a",
        request_hash=REQUEST_HASH,
        response_code="200",
        response_json={"ok": True},
        provider_message_id=f"provider-{uuid4()}",
        now=NOW,
    )


def test_provider_acknowledgement_advances_sent_delivered_read_monotonically(approval_db):
    delivery = _sent_delivery(approval_db)
    delivered_at = NOW.replace(minute=31)
    read_at = NOW.replace(minute=32)

    delivered = record_notification_provider_status(
        approval_db,
        channel="feishu",
        provider_message_id=delivery.provider_message_id,
        status="delivered",
        occurred_at=delivered_at,
    )
    assert delivered.status == "delivered"
    assert delivered.delivered_at == delivered_at

    read = record_notification_provider_status(
        approval_db,
        channel="feishu",
        provider_message_id=delivery.provider_message_id,
        status="read",
        occurred_at=read_at,
    )
    assert read.status == "read"
    assert read.read_at == read_at

    late_delivery = record_notification_provider_status(
        approval_db,
        channel="feishu",
        provider_message_id=delivery.provider_message_id,
        status="delivered",
        occurred_at=read_at,
    )
    assert late_delivery.status == "read"
    assert late_delivery.read_at == read_at


def test_provider_read_implies_delivery_but_rejects_wrong_channel_or_time(approval_db):
    delivery = _sent_delivery(approval_db)
    read_at = NOW.replace(minute=33)

    direct_read = record_notification_provider_status(
        approval_db,
        channel="feishu",
        provider_message_id=delivery.provider_message_id,
        status="read",
        occurred_at=read_at,
    )
    assert direct_read.delivered_at == read_at
    assert direct_read.read_at == read_at

    with pytest.raises(NotificationDeliveryError, match="does not exist"):
        record_notification_provider_status(
            approval_db,
            channel="wechat",
            provider_message_id=delivery.provider_message_id,
            status="delivered",
            occurred_at=read_at,
        )

    other = _sent_delivery(approval_db)
    with pytest.raises(NotificationDeliveryError, match="predates send"):
        record_notification_provider_status(
            approval_db,
            channel="feishu",
            provider_message_id=other.provider_message_id,
            status="delivered",
            occurred_at=NOW.replace(minute=29),
        )
