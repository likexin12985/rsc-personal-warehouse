"""Lease and result boundaries for notification provider workers.

This module owns database facts only.  Provider adapters call ``claim`` before
their one external request and ``record_result`` exactly once afterwards.  A
transport timeout is recorded as a failed/uncertain result by the caller and
is never automatically replayed by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..foundation_models import (
    NotificationAttempt,
    NotificationDelivery,
    NotificationEvent,
    NotificationRecipient,
)


class NotificationDeliveryError(RuntimeError):
    """A delivery cannot advance without exact ownership or evidence."""


@dataclass(frozen=True)
class NotificationDeliveryClaim:
    delivery_id: UUID
    event_id: UUID
    recipient_id: UUID
    channel: str
    recipient_key: str
    payload: dict[str, Any]
    attempt_no: int
    worker_id: str


def _utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_worker(worker_id: str) -> str:
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 160:
        raise NotificationDeliveryError("notification worker identity is invalid")
    return worker_id.strip()


def claim_notification_deliveries(
    db: Session,
    *,
    worker_id: str,
    limit: int = 50,
    now: datetime | None = None,
) -> tuple[NotificationDeliveryClaim, ...]:
    """Claim queued deliveries for one worker.

    Only ``queued`` rows are claimable.  Existing ``sending`` rows, including
    stale leases, remain untouched because their provider outcome is unknown.
    """

    worker = _require_worker(worker_id)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise NotificationDeliveryError("notification delivery limit is invalid")
    effective_at = _utc(now)
    rows = tuple(
        db.scalars(
            select(NotificationDelivery)
            .where(NotificationDelivery.status == "queued")
            .order_by(NotificationDelivery.created_at, NotificationDelivery.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
    )
    claims: list[NotificationDeliveryClaim] = []
    for delivery in rows:
        recipient = db.get(NotificationRecipient, delivery.recipient_id)
        if recipient is None or recipient.status != "active":
            delivery.status = "cancelled"
            delivery.last_error = "recipient is no longer active"
            delivery.updated_at = effective_at
            continue
        event = db.get(NotificationEvent, recipient.event_id)
        if event is None or event.status == "cancelled":
            delivery.status = "cancelled"
            delivery.last_error = "notification event is cancelled or missing"
            delivery.updated_at = effective_at
            continue
        delivery.status = "sending"
        delivery.attempts += 1
        delivery.locked_at = effective_at
        delivery.locked_by = worker
        delivery.updated_at = effective_at
        claims.append(
            NotificationDeliveryClaim(
                delivery_id=delivery.id,
                event_id=event.id,
                recipient_id=recipient.id,
                channel=recipient.channel,
                recipient_key=recipient.recipient_key,
                payload=dict(event.payload_jsonb),
                attempt_no=delivery.attempts,
                worker_id=worker,
            )
        )
    db.flush()
    return tuple(claims)


def record_notification_delivery_result(
    db: Session,
    *,
    delivery_id: UUID,
    worker_id: str,
    request_hash: str,
    response_code: str | None,
    response_json: dict[str, Any] | None,
    provider_message_id: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> NotificationDelivery:
    """Record one provider result for the exact active worker lease."""

    worker = _require_worker(worker_id)
    if not isinstance(request_hash, str) or len(request_hash) != 64:
        raise NotificationDeliveryError("notification request hash is invalid")
    if response_json is not None and not isinstance(response_json, dict):
        raise NotificationDeliveryError("notification response evidence is invalid")
    if provider_message_id is not None and not provider_message_id.strip():
        raise NotificationDeliveryError("provider message id is invalid")
    effective_at = _utc(now)
    delivery = db.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update()
    )
    if delivery is None:
        raise NotificationDeliveryError("notification delivery does not exist")
    if delivery.status != "sending" or delivery.locked_by != worker:
        raise NotificationDeliveryError("notification delivery lease is not owned")
    if delivery.attempts < 1:
        raise NotificationDeliveryError("notification delivery attempt is missing")
    succeeded = provider_message_id is not None and error is None
    if succeeded and response_code is None:
        raise NotificationDeliveryError("successful delivery needs a response code")
    if not succeeded and not error:
        raise NotificationDeliveryError("failed delivery needs an error")
    db.add(
        NotificationAttempt(
            delivery_id=delivery.id,
            attempt_no=delivery.attempts,
            request_hash=request_hash,
            response_code=response_code,
            response_jsonb=response_json,
            error=error,
            attempted_at=effective_at,
            created_at=effective_at,
        )
    )
    if succeeded:
        delivery.status = "sent"
        delivery.provider_message_id = provider_message_id
        delivery.sent_at = effective_at
        delivery.last_error = None
    else:
        delivery.status = "failed"
        delivery.last_error = error
    delivery.locked_at = None
    delivery.locked_by = None
    delivery.updated_at = effective_at
    db.flush()
    return delivery


def record_notification_provider_status(
    db: Session,
    *,
    channel: str,
    provider_message_id: str,
    status: str,
    occurred_at: datetime,
) -> NotificationDelivery:
    """Record an exact provider delivery/read acknowledgement monotonically.

    Provider adapters must resolve their authenticated callback to one channel
    and one provider message identifier before entering this boundary.  A read
    acknowledgement proves delivery as well, but a late delivered callback can
    never regress an already-read row.
    """

    if channel not in {"wechat", "sms", "feishu"}:
        raise NotificationDeliveryError("notification provider channel is invalid")
    if (
        not isinstance(provider_message_id, str)
        or not provider_message_id.strip()
        or len(provider_message_id) > 250
    ):
        raise NotificationDeliveryError("provider message id is invalid")
    if status not in {"delivered", "read"}:
        raise NotificationDeliveryError("notification provider status is invalid")
    if not isinstance(occurred_at, datetime):
        raise NotificationDeliveryError("notification provider timestamp is invalid")
    effective_at = _utc(occurred_at)
    rows = tuple(
        db.scalars(
            select(NotificationDelivery)
            .join(
                NotificationRecipient,
                NotificationRecipient.id == NotificationDelivery.recipient_id,
            )
            .where(
                NotificationDelivery.provider_message_id == provider_message_id.strip(),
                NotificationRecipient.channel == channel,
            )
            .limit(2)
            .with_for_update()
        ).all()
    )
    if not rows:
        raise NotificationDeliveryError("notification provider message does not exist")
    if len(rows) != 1:
        raise NotificationDeliveryError("notification provider message is ambiguous")
    delivery = rows[0]
    if delivery.status not in {"sent", "delivered", "read"} or delivery.sent_at is None:
        raise NotificationDeliveryError("notification provider acknowledgement is out of sequence")
    sent_at = _utc(delivery.sent_at)
    if effective_at < sent_at:
        raise NotificationDeliveryError("notification provider timestamp predates send")

    if status == "delivered":
        if delivery.status == "sent":
            delivery.status = "delivered"
            delivery.delivered_at = effective_at
            delivery.updated_at = effective_at
    elif delivery.status != "read":
        if delivery.delivered_at is not None and effective_at < _utc(delivery.delivered_at):
            raise NotificationDeliveryError("notification read timestamp predates delivery")
        delivery.status = "read"
        delivery.delivered_at = delivery.delivered_at or effective_at
        delivery.read_at = effective_at
        delivery.updated_at = effective_at
    db.flush()
    return delivery


def retry_failed_notification_delivery(
    db: Session,
    *,
    delivery_id: UUID,
    expected_attempt_no: int,
    now: datetime | None = None,
    max_attempts: int = 5,
) -> NotificationDelivery:
    """Explicitly requeue one definitively retryable provider failure.

    The latest attempt must contain an HTTP-style 429 or 5xx response code.
    Missing response evidence means the external outcome is unknown and is a
    hard stop; this function never turns a timeout into a duplicate send.
    """

    if (
        not isinstance(expected_attempt_no, int)
        or isinstance(expected_attempt_no, bool)
        or expected_attempt_no < 1
    ):
        raise NotificationDeliveryError("notification attempt number is invalid")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise NotificationDeliveryError("notification retry limit is invalid")
    delivery = db.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update()
    )
    if delivery is None:
        raise NotificationDeliveryError("notification delivery does not exist")
    if delivery.status != "failed":
        raise NotificationDeliveryError("notification delivery is not a failed attempt")
    if delivery.attempts != expected_attempt_no:
        raise NotificationDeliveryError("notification attempt has changed")
    attempt = db.scalar(
        select(NotificationAttempt)
        .where(
            NotificationAttempt.delivery_id == delivery.id,
            NotificationAttempt.attempt_no == expected_attempt_no,
        )
    )
    if attempt is None or not attempt.response_code:
        raise NotificationDeliveryError("notification failure outcome is unknown")
    response_code = attempt.response_code.strip()
    try:
        numeric_code = int(response_code)
    except ValueError:
        numeric_code = 0
    if numeric_code != 429 and not 500 <= numeric_code <= 599:
        raise NotificationDeliveryError("notification failure is not retryable")
    if delivery.attempts >= max_attempts:
        raise NotificationDeliveryError("notification retry limit reached")
    effective_at = _utc(now)
    delivery.status = "queued"
    delivery.locked_at = None
    delivery.locked_by = None
    delivery.updated_at = effective_at
    db.flush()
    return delivery


__all__ = [
    "NotificationDeliveryClaim",
    "NotificationDeliveryError",
    "claim_notification_deliveries",
    "record_notification_delivery_result",
    "record_notification_provider_status",
    "retry_failed_notification_delivery",
]
