from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from test_material_request_approval_service import approval_db
from test_material_request_draft_service import make_world

from app.foundation_models import NotificationDelivery, NotificationEvent, NotificationRecipient
from app.database import get_db
from app.dependencies import get_formal_principal
from app.main import block_legacy_prototype_writes
from app.routers import formal_notifications
from app.formal_services.notification_inbox import (
    NotificationInboxError,
    list_notification_inbox,
    mark_notification_read,
)


NOW = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)


class _ApiPrincipal:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    def allows(self, _db, resource: str, action: str, **_kwargs) -> bool:
        return (resource, action) == ("access_context", "read")


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


def test_formal_api_returns_only_current_user_and_keeps_failures_private(approval_db, monkeypatch):
    world = make_world(approval_db)
    own = _delivery(approval_db, user_id=world.actor_user.id)
    foreign = _delivery(approval_db, user_id=world.admin_users[0].id)
    page = list_notification_inbox(approval_db, user_id=world.actor_user.id)

    class _Db:
        def commit(self): pass
        def rollback(self): pass

    monkeypatch.setattr(
        formal_notifications.notification_inbox,
        "list_notification_inbox",
        lambda _db, *, user_id, limit, after_id: page
        if user_id == world.actor_user.id
        else pytest.fail("router crossed current-user boundary"),
    )

    def reject_foreign(_db, *, user_id, delivery_id):
        assert user_id == world.actor_user.id
        assert delivery_id == foreign.id
        raise NotificationInboxError(
            "notification does not belong to current user",
            http_status_code=404,
        )

    monkeypatch.setattr(
        formal_notifications.notification_inbox,
        "mark_notification_read",
        reject_foreign,
    )
    api = FastAPI()
    api.middleware("http")(block_legacy_prototype_writes)
    api.include_router(formal_notifications.router, prefix="/api")
    api.dependency_overrides[get_db] = _Db
    api.dependency_overrides[get_formal_principal] = lambda: _ApiPrincipal(world.actor_user.id)

    with TestClient(api) as client:
        listed = client.get("/api/v1/notifications")
        assert listed.status_code == 200
        assert [row["delivery_id"] for row in listed.json()["items"]] == [str(own.id)]
        assert listed.headers["Cache-Control"] == "private, no-store, max-age=0"

        rejected = client.post(f"/api/v1/notifications/{foreign.id}/read", json={})
        assert rejected.status_code == 404
        assert rejected.headers["Cache-Control"] == "private, no-store, max-age=0"
