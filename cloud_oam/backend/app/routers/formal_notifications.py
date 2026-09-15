"""Formal user-scoped notification inbox."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import notification_inbox
from ..notification_schemas import NotificationItemOut, NotificationPageOut, NotificationReadOut


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
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
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
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    return NotificationReadOut(item=_item_out(item))


__all__ = ["router"]
