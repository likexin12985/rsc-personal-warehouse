"""Create durable notification facts from already committed business facts.

This module only records the notification event and the recipient coordinates.
It never calls a provider and never changes the business fact that triggered
it.  Provider delivery, acknowledgement, retry, and read state remain owned
by the notification delivery boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..foundation_models import (
    NotificationEvent,
    NotificationRecipient,
    Person,
)
from ..models import User, WechatIdentity


class NotificationEventError(RuntimeError):
    """A notification event cannot be recorded safely."""


@dataclass(frozen=True)
class ShipmentNotificationResult:
    event: NotificationEvent
    recipient_count: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _active_target_user(db: Session, *, person_id: UUID | None) -> User | None:
    if person_id is None:
        return None
    return db.scalar(
        select(User)
        .join(Person, Person.id == User.person_id)
        .where(
            User.person_id == person_id,
            User.account_status == "active",
            User.is_active.is_(True),
            Person.employment_status == "active",
        )
        .order_by(User.id)
        .limit(1)
    )


def _add_recipient(
    db: Session,
    *,
    event_id: UUID,
    user: User,
    channel: str,
    recipient_key: str,
    now: datetime,
) -> None:
    if not recipient_key.strip():
        return
    db.add(
        NotificationRecipient(
            event_id=event_id,
            user_id=user.id,
            channel=channel,
            recipient_key=recipient_key.strip(),
            status="active",
            created_at=now,
        )
    )


def record_shipment_handover_notification(
    db: Session,
    *,
    shipment: Any,
    request_id: UUID,
    now: datetime | None = None,
) -> ShipmentNotificationResult:
    """Record one target-engineer notification for a shipment handover.

    The target person is resolved through the formal ``users.person_id``
    mapping and must still be active.  We create only channels with a current
    identity: WeChat uses the bound openid and SMS uses the current mobile
    number.  Missing identity mappings leave the event durable and pending;
    they never cause a guessed recipient or a provider request.
    """

    shipment_id = getattr(shipment, "id", None)
    if not isinstance(shipment_id, UUID):
        raise NotificationEventError("shipment identity is invalid")
    if not isinstance(request_id, UUID):
        raise NotificationEventError("request identity is invalid")
    effective_now = _utc(now or datetime.now(timezone.utc))
    dedup_key = f"shipment-handover:{shipment_id}"
    existing = db.scalar(
        select(NotificationEvent).where(NotificationEvent.dedup_key == dedup_key)
    )
    if existing is not None:
        recipient_count = int(
            db.scalar(
                select(func.count(NotificationRecipient.id))
                .where(NotificationRecipient.event_id == existing.id)
            )
            or 0
        )
        return ShipmentNotificationResult(existing, recipient_count)

    event = NotificationEvent(
        event_type="shipment_handover_registered",
        business_type="shipment",
        business_id=str(shipment_id),
        dedup_key=dedup_key,
        payload_jsonb={
            "request_id": str(request_id),
            "shipment_id": str(shipment_id),
            "shipment_no": str(getattr(shipment, "shipment_no", "")),
            "carrier": str(getattr(shipment, "carrier", "")),
            "tracking_no": str(getattr(shipment, "tracking_no", "")),
            "target_location_id": str(getattr(shipment, "target_location_id", "")),
            "target_person_id": (
                str(getattr(shipment, "target_person_id"))
                if getattr(shipment, "target_person_id", None) is not None
                else None
            ),
        },
        status="pending",
        occurred_at=_utc(getattr(shipment, "shipped_at", effective_now)),
        created_at=effective_now,
    )
    db.add(event)
    db.flush()

    user = _active_target_user(
        db, person_id=getattr(shipment, "target_person_id", None)
    )
    recipient_count = 0
    if user is not None:
        identity = db.scalar(
            select(WechatIdentity)
            .where(WechatIdentity.user_id == user.id)
            .order_by(WechatIdentity.last_login_at.desc(), WechatIdentity.id)
            .limit(1)
        )
        if identity is not None:
            _add_recipient(
                db,
                event_id=event.id,
                user=user,
                channel="wechat",
                recipient_key=identity.openid,
                now=effective_now,
            )
            recipient_count += 1
        if user.mobile:
            _add_recipient(
                db,
                event_id=event.id,
                user=user,
                channel="sms",
                recipient_key=user.mobile,
                now=effective_now,
            )
            recipient_count += 1

    db.flush()
    return ShipmentNotificationResult(event, recipient_count)


__all__ = [
    "NotificationEventError",
    "ShipmentNotificationResult",
    "record_shipment_handover_notification",
]
