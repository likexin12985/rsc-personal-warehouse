"""Formal user-scoped notification inbox."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import notification_inbox
from ..formal_services import notification_delivery_operations
from ..formal_services import inventory_notification_operations
from ..formal_services import notification_target_operations
from ..notification_schemas import (
    InventoryNotificationSourcePageOut,
    InventoryNotificationSourceRecheckIn,
    InventoryNotificationSourceRecheckOut,
    NotificationDeliveryPageOut,
    NotificationDeliveryRecordOut,
    NotificationDeliveryRetryIn,
    NotificationDeliveryRetryOut,
    NotificationItemOut,
    NotificationPageOut,
    NotificationReadOut,
    NotificationTargetPageOut,
    NotificationLegacyEventPageOut,
    NotificationTargetRecoveryIn,
    NotificationTargetRecoveryOut,
)


router = APIRouter(prefix="/v1/notifications", tags=["formal-notifications"])


def _item_out(item: notification_inbox.NotificationInboxItem) -> NotificationItemOut:
    return NotificationItemOut(
        delivery_id=item.delivery_id,
        event_id=item.event_id,
        event_type=item.event_type,
        business_type=item.business_type,
        business_id=item.business_id,
        channel=item.channel,
        status=item.status,
        payload=item.payload,
        occurred_at=item.occurred_at,
        created_at=item.created_at,
        sent_at=item.sent_at,
        delivered_at=item.delivered_at,
        read_at=item.read_at,
    )


def _raise(exc: notification_inbox.NotificationInboxError) -> None:
    raise HTTPException(status_code=exc.http_status_code, detail=str(exc)) from None


def _raise_delivery(
    exc: notification_delivery_operations.NotificationDeliveryOperationsError,
) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None


def _private_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _delivery_record_out(
    item: notification_delivery_operations.NotificationDeliveryRecord,
) -> NotificationDeliveryRecordOut:
    return NotificationDeliveryRecordOut(
        delivery_id=item.delivery_id,
        event_id=item.event_id,
        event_type=item.event_type,
        business_type=item.business_type,
        business_id=item.business_id,
        recipient_user_id=item.recipient_user_id,
        channel=item.channel,
        status=item.status,
        attempts=item.attempts,
        provider_message_id=item.provider_message_id,
        last_error=item.last_error,
        created_at=item.created_at,
        updated_at=item.updated_at,
        sent_at=item.sent_at,
        delivered_at=item.delivered_at,
        read_at=item.read_at,
        latest_attempt_no=item.latest_attempt_no,
        latest_response_code=item.latest_response_code,
        latest_error=item.latest_error,
        latest_attempted_at=item.latest_attempted_at,
        retryable=item.retryable,
    )


@router.get("/deliveries", response_model=NotificationDeliveryPageOut)
def list_notification_deliveries(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    delivery_status: Annotated[
        Literal[
            "queued",
            "sending",
            "sent",
            "delivered",
            "read",
            "failed",
            "cancelled",
        ]
        | None,
        Query(alias="status"),
    ] = None,
    channel: Annotated[Literal["wechat", "sms", "feishu"] | None, Query()] = None,
    principal: FormalPrincipal = Depends(
        require_permission("notification_delivery", "read")
    ),
    db: Session = Depends(get_db),
):
    try:
        page = notification_delivery_operations.list_notification_delivery_records(
            db,
            actor=principal,
            limit=limit,
            after_id=after_id,
            status=delivery_status,
            channel=channel,
        )
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        _raise_delivery(exc)
    _private_headers(response)
    return NotificationDeliveryPageOut(
        items=tuple(_delivery_record_out(item) for item in page.items),
        next_after_id=page.next_after_id,
    )


@router.post(
    "/deliveries/{delivery_id}/retry",
    response_model=NotificationDeliveryRetryOut,
)
def retry_notification_delivery(
    delivery_id: UUID,
    payload: NotificationDeliveryRetryIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("notification_delivery", "retry")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    # The service repeats the exact validation so non-HTTP callers cannot
    # accidentally issue an untracked command.  Keeping missing headers here
    # produces the same structured 400 as the other formal write routes.
    if idempotency_key is None or request_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "notification_retry_headers_required",
                "category": "invalid_request",
                "message": "Idempotency-Key和X-Request-ID为必填项",
            },
        )
    try:
        result = notification_delivery_operations.retry_notification_delivery(
            db,
            actor=principal,
            delivery_id=delivery_id,
            expected_attempt_no=payload.expected_attempt_no,
            reason=payload.reason,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        db.commit()
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        db.rollback()
        _raise_delivery(exc)
    except Exception:
        db.rollback()
        raise
    _private_headers(response)
    response.headers["Idempotency-Replayed"] = "true" if result.replayed else "false"
    return NotificationDeliveryRetryOut(
        delivery_id=result.delivery_id,
        retry_attempt_no=result.retry_attempt_no,
        status=result.status,
        queued_at=result.queued_at,
        replayed=result.replayed,
    )


@router.get("/inventory-sources", response_model=InventoryNotificationSourcePageOut)
def list_inventory_notification_sources(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("notification_delivery", "read")),
    db: Session = Depends(get_db),
):
    try:
        page = inventory_notification_operations.list_inventory_notification_sources(
            db, actor=principal, limit=limit, after_id=after_id)
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        _raise_delivery(exc)
    _private_headers(response)
    return InventoryNotificationSourcePageOut(**asdict(page))


@router.post("/inventory-sources/{outbox_id}/recheck", response_model=InventoryNotificationSourceRecheckOut)
def recheck_inventory_notification_source(
    outbox_id: UUID,
    payload: InventoryNotificationSourceRecheckIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("notification_delivery", "retry")),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    try:
        result = inventory_notification_operations.recheck_inventory_notification_source(
            db, actor=principal, outbox_id=outbox_id, **payload.model_dump(),
            idempotency_key=idempotency_key, request_id=request_id)
        db.commit()
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        db.rollback()
        _raise_delivery(exc)
    except Exception:
        db.rollback()
        raise
    _private_headers(response)
    response.headers["Idempotency-Replayed"] = "true" if result.replayed else "false"
    return InventoryNotificationSourceRecheckOut(**asdict(result))


@router.get("/person-targets", response_model=NotificationTargetPageOut)
def list_notification_targets(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    unbound_only: bool = True,
    principal: FormalPrincipal = Depends(require_permission("notification_delivery", "read")),
    policy: notification_target_operations.IdentityPolicy = Depends(notification_target_operations.identity_policy),
    db: Session = Depends(get_db),
):
    try:
        page = notification_target_operations.list_notification_targets(db, actor=principal,
            policy=policy, limit=limit, after_id=after_id, unbound_only=unbound_only)
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        _raise_delivery(exc)
    _private_headers(response)
    return NotificationTargetPageOut(**asdict(page))


@router.get("/person-targets/legacy-events", response_model=NotificationLegacyEventPageOut)
def list_legacy_notification_events(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("notification_delivery", "read")),
    db: Session = Depends(get_db),
):
    try:
        page = notification_target_operations.list_legacy_notification_events(db,
            actor=principal, limit=limit, after_id=after_id)
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        _raise_delivery(exc)
    _private_headers(response)
    return NotificationLegacyEventPageOut(**asdict(page))


@router.post("/person-targets/{target_id}/recover", response_model=NotificationTargetRecoveryOut)
def recover_notification_target(
    target_id: UUID,
    payload: NotificationTargetRecoveryIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("notification_delivery", "retry")),
    policy: notification_target_operations.IdentityPolicy = Depends(notification_target_operations.identity_policy),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    try:
        result = notification_target_operations.recover_notification_target(db,
            actor=principal, policy=policy, target_id=target_id, **payload.model_dump(),
            idempotency_key=idempotency_key, request_id=request_id)
        db.commit()
    except notification_delivery_operations.NotificationDeliveryOperationsError as exc:
        db.rollback()
        _raise_delivery(exc)
    except Exception:
        db.rollback()
        raise
    _private_headers(response)
    response.headers["Idempotency-Replayed"] = "true" if result.replayed else "false"
    return NotificationTargetRecoveryOut(**asdict(result))


@router.get("", response_model=NotificationPageOut)
def list_notifications(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(require_permission("access_context", "read")),
    db: Session = Depends(get_db),
):
    try:
        page = notification_inbox.list_notification_inbox(
            db,
            user_id=principal.user_id,
            limit=limit,
            after_id=after_id,
        )
    except notification_inbox.NotificationInboxError as exc:
        _raise(exc)
    _private_headers(response)
    return NotificationPageOut(
        items=tuple(_item_out(item) for item in page.items),
        next_after_id=page.next_after_id,
        unread_count=page.unread_count,
    )


@router.post("/{delivery_id}/read", response_model=NotificationReadOut)
def read_notification(
    delivery_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("access_context", "read")),
    db: Session = Depends(get_db),
):
    try:
        item = notification_inbox.mark_notification_read(
            db,
            user_id=principal.user_id,
            delivery_id=delivery_id,
        )
        db.commit()
    except notification_inbox.NotificationInboxError as exc:
        db.rollback()
        _raise(exc)
    _private_headers(response)
    return NotificationReadOut(item=_item_out(item))


__all__ = ["router"]
