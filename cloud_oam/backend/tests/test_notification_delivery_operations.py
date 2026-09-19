from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_material_request_approval_service import approval_db
from test_material_request_draft_service import make_world

from app.formal_access import load_formal_principal
from app.formal_services.notification_delivery import (
    claim_notification_deliveries,
    record_notification_delivery_result,
    retry_failed_notification_delivery,
)
from app.formal_services.notification_delivery_operations import (
    NotificationDeliveryOperationsError,
    list_notification_delivery_records,
    retry_notification_delivery,
)
from app.foundation_models import (
    AuditEvent,
    NotificationDelivery,
    NotificationAttempt,
    NotificationEvent,
    NotificationRecipient,
    Permission,
    RolePermission,
)


NOW = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)


def _operator(db):
    world = make_world(db)
    permission = Permission(
        resource="notification_delivery",
        action="read",
        field_code="",
        description="test",
    )
    retry_permission = Permission(
        resource="notification_delivery",
        action="retry",
        field_code="",
        description="test",
    )
    db.add_all((permission, retry_permission))
    db.flush()
    admin_role = world.roles["admin"]
    db.add_all(
        (
            RolePermission(role_id=admin_role.id, permission_id=permission.id, effect="allow"),
            RolePermission(role_id=admin_role.id, permission_id=retry_permission.id, effect="allow"),
        )
    )
    db.flush()
    return world, load_formal_principal(db, world.admin_users[0].id, now=NOW)


def _failed(db, *, response_code: str | None = "503") -> NotificationDelivery:
    event = NotificationEvent(
        event_type="shipment_handover_registered",
        business_type="shipment",
        business_id=str(uuid4()),
        dedup_key=f"ops-event:{uuid4()}",
        payload_jsonb={"title": "已发运"},
        status="expanded",
        occurred_at=NOW,
        created_at=NOW,
    )
    db.add(event)
    db.flush()
    recipient = NotificationRecipient(
        event_id=event.id,
        user_id=None,
        channel="wechat",
        recipient_key=f"openid:{uuid4()}",
        status="active",
        created_at=NOW,
    )
    db.add(recipient)
    db.flush()
    delivery = NotificationDelivery(
        recipient_id=recipient.id,
        delivery_key=f"ops-delivery:{uuid4()}",
        status="queued",
        attempts=0,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(delivery)
    db.flush()
    claim_notification_deliveries(db, worker_id="ops-test", now=NOW)
    record_notification_delivery_result(
        db,
        delivery_id=delivery.id,
        worker_id="ops-test",
        request_hash=sha256(b"ops-request").hexdigest(),
        response_code=response_code,
        response_json={"safe": True} if response_code else None,
        error="provider rejected" if response_code else "provider outcome unknown",
        now=NOW,
    )
    return delivery


def test_list_redacts_recipient_key_and_marks_only_definitive_failures_retryable(approval_db):
    _world, operator = _operator(approval_db)
    delivery = _failed(approval_db)

    page = list_notification_delivery_records(approval_db, actor=operator)

    assert [row.delivery_id for row in page.items] == [delivery.id]
    row = page.items[0]
    assert row.retryable is True
    assert row.latest_response_code == "503"
    assert not hasattr(row, "recipient_key")
    assert not hasattr(row, "response_json")


def test_retry_is_audited_and_same_command_replays_without_second_queue(approval_db):
    _world, operator = _operator(approval_db)
    delivery = _failed(approval_db)
    command = dict(
        actor=operator,
        delivery_id=delivery.id,
        expected_attempt_no=1,
        reason="provider returned 503",
        idempotency_key="notification-retry-0001",
        request_id="trace-notification-1",
        now=NOW,
    )

    first = retry_notification_delivery(approval_db, **command)
    approval_db.commit()
    replay = retry_notification_delivery(approval_db, **command)

    assert first.status == "queued"
    assert first.retry_attempt_no == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.retry_attempt_no == first.retry_attempt_no
    assert approval_db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.id == delivery.id)
    ).status == "queued"
    audits = tuple(
        approval_db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "notification_delivery.retry",
                AuditEvent.aggregate_id == str(delivery.id),
            )
        ).all()
    )
    assert len(audits) == 1
    assert "idempotency_key_sha256" in audits[0].after_jsonb
    assert "notification-retry-0001" not in str(audits[0].after_jsonb)


def test_unknown_provider_outcome_is_a_hard_stop(approval_db):
    _world, operator = _operator(approval_db)
    delivery = _failed(approval_db, response_code=None)
    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        retry_notification_delivery(
            approval_db,
            actor=operator,
            delivery_id=delivery.id,
            expected_attempt_no=1,
            reason="retry check",
            idempotency_key="notification-retry-0002",
            request_id="trace-notification-2",
            now=NOW,
        )
    assert exc.value.code == "notification_retry_outcome_unknown"
    assert delivery.status == "failed"


def test_formal_principal_without_notification_permission_is_rejected(approval_db):
    world = make_world(approval_db)
    operator = load_formal_principal(approval_db, world.admin_users[0].id, now=NOW)
    delivery = _failed(approval_db)

    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        list_notification_delivery_records(approval_db, actor=operator)

    assert exc.value.code == "notification_delivery_permission_denied"
    assert exc.value.http_status_code == 403
    with pytest.raises(NotificationDeliveryOperationsError) as retry_exc:
        retry_notification_delivery(
            approval_db,
            actor=operator,
            delivery_id=delivery.id,
            expected_attempt_no=1,
            reason="permission check",
            idempotency_key="notification-retry-no-permission",
            request_id="trace-notification-no-permission",
            now=NOW,
        )
    assert retry_exc.value.code == "notification_delivery_permission_denied"
    assert retry_exc.value.http_status_code == 403
    assert delivery.status == "failed"
    assert approval_db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == "notification_delivery.retry",
            AuditEvent.aggregate_id == str(delivery.id),
        )
    ) is None


def test_regional_formal_principal_with_notification_permission_is_not_national(
    approval_db,
):
    world = make_world(approval_db)
    for action in ("read", "retry"):
        permission = Permission(
            resource="notification_delivery",
            action=action,
            field_code="",
            description="test regional scope",
        )
        approval_db.add(permission)
        approval_db.flush()
        approval_db.add(
            RolePermission(
                role_id=world.roles["provincial_manager"].id,
                permission_id=permission.id,
                effect="allow",
            )
        )
    approval_db.flush()
    regional_operator = load_formal_principal(
        approval_db, world.manager_users[0].id, now=NOW
    )
    delivery = _failed(approval_db)

    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        list_notification_delivery_records(approval_db, actor=regional_operator)

    assert exc.value.code == "notification_delivery_national_scope_required"
    assert exc.value.http_status_code == 403
    with pytest.raises(NotificationDeliveryOperationsError) as retry_exc:
        retry_notification_delivery(
            approval_db,
            actor=regional_operator,
            delivery_id=delivery.id,
            expected_attempt_no=1,
            reason="regional scope check",
            idempotency_key="notification-retry-regional-scope",
            request_id="trace-notification-regional-scope",
            now=NOW,
        )
    assert retry_exc.value.code == "notification_delivery_national_scope_required"
    assert retry_exc.value.http_status_code == 403
    assert delivery.status == "failed"
    assert approval_db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == "notification_delivery.retry",
            AuditEvent.aggregate_id == str(delivery.id),
        )
    ) is None


def test_retry_rejects_idempotency_key_reuse_for_another_delivery(approval_db):
    _world, operator = _operator(approval_db)
    first = _failed(approval_db)
    second = _failed(approval_db)
    command = dict(
        actor=operator,
        expected_attempt_no=1,
        reason="provider returned 503",
        idempotency_key="notification-retry-cross-object",
        request_id="trace-notification-cross-object",
        now=NOW,
    )

    retry_notification_delivery(approval_db, delivery_id=first.id, **command)
    approval_db.commit()
    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        retry_notification_delivery(
            approval_db,
            delivery_id=second.id,
            **{**command, "request_id": "trace-notification-cross-object-2"},
        )

    assert exc.value.code == "notification_retry_idempotency_conflict"
    approval_db.refresh(second)
    assert second.status == "failed"
    assert second.attempts == 1
    assert approval_db.scalar(
        select(NotificationDelivery).where(NotificationDelivery.id == first.id)
    ).status == "queued"
    assert approval_db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == "notification_delivery.retry",
            AuditEvent.aggregate_id == str(second.id),
        )
    ) is None


def test_retry_rejects_same_key_with_changed_command(approval_db):
    _world, operator = _operator(approval_db)
    delivery = _failed(approval_db)
    first = dict(
        actor=operator,
        delivery_id=delivery.id,
        expected_attempt_no=1,
        reason="provider returned 503",
        idempotency_key="notification-retry-command-conflict",
        request_id="trace-notification-command-conflict",
        now=NOW,
    )

    retry_notification_delivery(approval_db, **first)
    approval_db.commit()
    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        retry_notification_delivery(
            approval_db,
            **{**first, "reason": "operator changed the retry reason"},
        )

    assert exc.value.code == "notification_retry_idempotency_conflict"
    approval_db.refresh(delivery)
    assert delivery.status == "queued"
    assert delivery.attempts == 1
    assert approval_db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == "notification_delivery.retry",
            AuditEvent.aggregate_id == str(delivery.id),
        )
    ) is not None
    assert len(
        approval_db.scalars(
            select(AuditEvent.id).where(
                AuditEvent.action == "notification_delivery.retry",
                AuditEvent.aggregate_id == str(delivery.id),
            )
        ).all()
    ) == 1


def test_retry_rejects_request_id_reuse_for_another_delivery(approval_db):
    _world, operator = _operator(approval_db)
    first = _failed(approval_db)
    second = _failed(approval_db)
    first_command = dict(
        actor=operator,
        delivery_id=first.id,
        expected_attempt_no=1,
        reason="provider returned 503",
        idempotency_key="notification-retry-request-id-1",
        request_id="trace-notification-request-id-reuse",
        now=NOW,
    )

    retry_notification_delivery(approval_db, **first_command)
    approval_db.commit()
    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        retry_notification_delivery(
            approval_db,
            actor=operator,
            delivery_id=second.id,
            expected_attempt_no=1,
            reason="provider returned 503",
            idempotency_key="notification-retry-request-id-2",
            request_id="trace-notification-request-id-reuse",
            now=NOW,
        )

    assert exc.value.code == "notification_retry_idempotency_conflict"
    approval_db.refresh(second)
    assert second.status == "failed"
    assert approval_db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == "notification_delivery.retry",
            AuditEvent.aggregate_id == str(second.id),
        )
    ) is None


def test_retry_rejects_old_attempt_without_audit_or_queue_side_effect(approval_db):
    _world, operator = _operator(approval_db)
    delivery = _failed(approval_db)
    # Produce a real second provider attempt after the caller could have read
    # attempt 1.  The stale command must stop before touching the queue.
    retry_failed_notification_delivery(
        approval_db,
        delivery_id=delivery.id,
        expected_attempt_no=1,
        now=NOW,
    )
    claims = claim_notification_deliveries(
        approval_db, worker_id="ops-test-stale-attempt", now=NOW
    )
    assert tuple(claim.delivery_id for claim in claims) == (delivery.id,)
    record_notification_delivery_result(
        approval_db,
        delivery_id=delivery.id,
        worker_id="ops-test-stale-attempt",
        request_hash=sha256(b"ops-request-attempt-2").hexdigest(),
        response_code="503",
        response_json={"safe": True},
        error="provider rejected",
        now=NOW,
    )
    approval_db.refresh(delivery)
    assert delivery.attempts == 2
    attempt_ids_before = tuple(
        approval_db.scalars(
            select(NotificationAttempt.id).where(
                NotificationAttempt.delivery_id == delivery.id
            )
        ).all()
    )
    audit_ids_before = tuple(
        approval_db.scalars(
            select(AuditEvent.id).where(
                AuditEvent.action == "notification_delivery.retry",
                AuditEvent.aggregate_id == str(delivery.id),
            )
        ).all()
    )

    with pytest.raises(NotificationDeliveryOperationsError) as exc:
        retry_notification_delivery(
            approval_db,
            actor=operator,
            delivery_id=delivery.id,
            expected_attempt_no=1,
            reason="stale attempt check",
            idempotency_key="notification-retry-old-attempt",
            request_id="trace-notification-old-attempt",
            now=NOW,
        )

    assert exc.value.code == "notification_retry_precondition_failed"
    approval_db.refresh(delivery)
    assert delivery.status == "failed"
    assert delivery.attempts == 2
    assert delivery.locked_by is None
    assert tuple(
        approval_db.scalars(
            select(NotificationAttempt.id).where(
                NotificationAttempt.delivery_id == delivery.id
            )
        ).all()
    ) == attempt_ids_before
    assert tuple(
        approval_db.scalars(
            select(AuditEvent.id).where(
                AuditEvent.action == "notification_delivery.retry",
                AuditEvent.aggregate_id == str(delivery.id),
            )
        ).all()
    ) == audit_ids_before
