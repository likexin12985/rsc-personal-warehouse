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
class BusinessNotificationResult:
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


def record_business_notification(
    db: Session,
    *,
    event_type: str,
    business_type: str,
    business_id: UUID,
    dedup_key: str,
    payload: dict[str, Any],
    recipient_person_id: UUID | None,
    occurred_at: datetime,
    now: datetime | None = None,
) -> BusinessNotificationResult:
    """Record one durable business notification and its active channels.

    The target person is resolved through the formal ``users.person_id``
    mapping and must still be active.  We create only channels with a current
    identity: WeChat uses the bound openid and SMS uses the current mobile
    number.  Missing identity mappings leave the event durable and pending;
    they never cause a guessed recipient or a provider request.
    """

    if not isinstance(event_type, str) or not event_type.strip() or len(event_type) > 100:
        raise NotificationEventError("notification event type is invalid")
    if not isinstance(business_type, str) or not business_type.strip() or len(business_type) > 80:
        raise NotificationEventError("notification business type is invalid")
    if not isinstance(business_id, UUID):
        raise NotificationEventError("notification business identity is invalid")
    if not isinstance(dedup_key, str) or not dedup_key.strip() or len(dedup_key) > 200:
        raise NotificationEventError("notification deduplication key is invalid")
    if not isinstance(payload, dict):
        raise NotificationEventError("notification payload is invalid")
    if not isinstance(occurred_at, datetime):
        raise NotificationEventError("notification event time is invalid")
    effective_now = _utc(now or datetime.now(timezone.utc))
    # Deduplication must not flush unrelated facts that the outer business
    # command is still assembling for its atomic checkpoint.
    with db.no_autoflush:
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
        return BusinessNotificationResult(existing, recipient_count)

    event = NotificationEvent(
        event_type=event_type.strip(),
        business_type=business_type.strip(),
        business_id=str(business_id),
        dedup_key=dedup_key,
        payload_jsonb=dict(payload),
        status="pending",
        occurred_at=_utc(occurred_at),
        created_at=effective_now,
    )
    db.add(event)
    recipient_count = 0
    # Only flush the event itself to obtain its primary key.  The caller may
    # already have staged an outbox row that must remain observable as part of
    # the same atomic business boundary.
    db.flush([event])
    # User and identity lookups would otherwise trigger an autoflush of the
    # caller's pending business/outbox facts.  Keep all recipient rows pending
    # until the outer command commits or explicitly checkpoints them.
    with db.no_autoflush:
        user = _active_target_user(
            db, person_id=recipient_person_id
        )
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

    # Leave recipients pending in the caller's transaction.  The business
    # command may have already staged an outbox row whose database constraint
    # deliberately observes the complete atomic fact set at the caller's
    # checkpoint.  Flushing every pending object here would move that outbox
    # row to the database before the caller can validate the same boundary.
    # The event itself was flushed above to obtain its primary key; normal
    # commit/autoflush persists the recipients together with the business
    # fact and outbox event.
    return BusinessNotificationResult(event, recipient_count)


def record_shipment_handover_notification(
    db: Session,
    *,
    shipment: Any,
    request_id: UUID,
    now: datetime | None = None,
) -> BusinessNotificationResult:
    """Record one target-engineer notification for a shipment handover."""

    shipment_id = getattr(shipment, "id", None)
    if not isinstance(shipment_id, UUID):
        raise NotificationEventError("shipment identity is invalid")
    if not isinstance(request_id, UUID):
        raise NotificationEventError("request identity is invalid")
    effective_now = _utc(now or datetime.now(timezone.utc))
    return record_business_notification(
        db,
        event_type="shipment_handover_registered",
        business_type="shipment",
        business_id=shipment_id,
        dedup_key=f"shipment-handover:{shipment_id}",
        payload={
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
        recipient_person_id=getattr(shipment, "target_person_id", None),
        occurred_at=_utc(getattr(shipment, "shipped_at", effective_now)),
        now=effective_now,
    )


def record_stock_return_notification(
    db: Session,
    *,
    event_type: str,
    business_type: str,
    business_id: UUID,
    payload: dict[str, Any],
    recipient_person_id: UUID | None,
    occurred_at: datetime,
    now: datetime | None = None,
) -> BusinessNotificationResult:
    """Record a return-lifecycle notification for the bound custodian.

    Return submission, cancellation, physical departure, acceptance, and
    inbound posting are separate business facts.  This helper only gives each
    fact one durable notification identity and resolves the current recipient;
    it never collapses those state axes or invokes a provider.
    """

    if not isinstance(event_type, str) or not event_type.strip():
        raise NotificationEventError("stock return notification event type is invalid")
    if not isinstance(business_type, str) or not business_type.strip():
        raise NotificationEventError("stock return notification business type is invalid")
    return record_business_notification(
        db,
        event_type=event_type,
        business_type=business_type,
        business_id=business_id,
        dedup_key=f"stock-return-notification:{event_type}:{business_id}",
        payload=dict(payload),
        recipient_person_id=recipient_person_id,
        occurred_at=occurred_at,
        now=now,
    )


__all__ = [
    "NotificationEventError",
    "BusinessNotificationResult",
    "record_business_notification",
    "record_shipment_handover_notification",
    "record_stock_return_notification",
]
