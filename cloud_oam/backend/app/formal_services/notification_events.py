"""Create durable notification facts in a caller-owned database transaction.

This module only records the notification event and the recipient coordinates.
It never calls a provider and never changes the business fact that triggered
it.  Provider delivery, acknowledgement, retry, and read state remain owned
by the notification delivery boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..foundation_models import (
    NotificationEvent,
    NotificationRecipient,
    NotificationPersonTarget,
    NotificationTargetBinding,
)
from . import notification_identities


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


def target_manifest_hash(people: tuple[UUID, ...]) -> str:
    canonical = "notification-person-targets.v1\n" + ",".join(sorted({str(person) for person in people}))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _add_recipient(
    db: Session,
    *,
    event_id: UUID,
    target_id: UUID,
    user_id: str,
    channel: str,
    recipient_key: str,
    now: datetime,
) -> bool:
    if not recipient_key.strip():
        return False
    recipient = NotificationRecipient(
            id=uuid4(),
            event_id=event_id,
            user_id=user_id,
            channel=channel,
            recipient_key=recipient_key.strip(),
            status="active",
            created_at=now,
        )
    db.add(recipient)
    db.add(NotificationTargetBinding(target_id=target_id, recipient_id=recipient.id, created_at=now))
    return True


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
    recipient_person_ids: tuple[UUID, ...] | None = None,
) -> BusinessNotificationResult:
    """Record one durable business notification and its active channels.

    Each channel must match a current, verified formal AuthIdentity in the
    configured provider/app domain. Legacy mobile/OpenID fields alone cannot
    create recipients. Intended people are independent of channel resolution.
    Missing accounts/channels leave an unbound person target, even after the
    event is expanded; no guessed recipient or provider request is created.
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
    if recipient_person_ids is not None and recipient_person_id is not None:
        raise NotificationEventError("notification recipient forms cannot be mixed")
    people = recipient_person_ids if recipient_person_ids is not None else (
        (recipient_person_id,) if recipient_person_id is not None else ()
    )
    if not isinstance(people, tuple) or any(not isinstance(person, UUID) for person in people):
        raise NotificationEventError("notification recipient identities are invalid")
    people = tuple(sorted(set(people), key=str))
    effective_now = _utc(now or datetime.now(timezone.utc))
    # Deduplication must not flush unrelated facts that the outer business
    # command is still assembling for its atomic checkpoint.
    with db.no_autoflush:
        existing = db.scalar(
            select(NotificationEvent).where(NotificationEvent.dedup_key == dedup_key)
        )
    if existing is not None:
        if (existing.event_type != event_type.strip() or existing.business_type != business_type.strip()
                or existing.business_id != str(business_id) or existing.payload_jsonb != payload
                or _utc(existing.occurred_at) != _utc(occurred_at)):
            raise NotificationEventError("notification deduplication key is bound to different facts")
        with db.no_autoflush:
            targets = set(db.scalars(select(NotificationPersonTarget.person_id).where(
                NotificationPersonTarget.event_id == existing.id)))
            targets.update(row.person_id for row in db.new if isinstance(row, NotificationPersonTarget) and row.event_id == existing.id)
            # Never reinterpret a historical target set using a new caller's
            # inputs, including when that old event has zero targets.
            if existing.target_manifest_sha256 is not None and (
                    targets != set(people) or existing.target_manifest_sha256 != target_manifest_hash(tuple(targets))):
                raise NotificationEventError("notification deduplication key is bound to different target people")
            recipient_count = int(
                db.scalar(
                    select(func.count(NotificationRecipient.id))
                    .where(NotificationRecipient.event_id == existing.id)
                ) or 0
            )
            recipient_count += sum(isinstance(row, NotificationRecipient) and row.event_id == existing.id for row in db.new)
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
        target_manifest_sha256=target_manifest_hash(people),
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
        policy = notification_identities.identity_policy()
        for person_id in people:
            target = NotificationPersonTarget(id=uuid4(), event_id=event.id, person_id=person_id, created_at=effective_now)
            db.add(target)
            for channel in notification_identities.resolve_verified_channels(db,person_id=person_id,policy=policy,now=effective_now):
                recipient_count += int(_add_recipient(
                    db,
                    event_id=event.id,
                    target_id=target.id,
                    user_id=channel.user_id,
                    channel=channel.channel,
                    recipient_key=channel.recipient_key,
                    now=effective_now,
                ))

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
