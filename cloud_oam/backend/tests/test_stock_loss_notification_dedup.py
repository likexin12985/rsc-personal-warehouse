"""Real loss facts must retain a single intended notification per engineer."""
from uuid import UUID
import pytest
from sqlalchemy import select
from app.foundation_models import NotificationEvent, NotificationPersonTarget, OutboxEvent
from app.stock_operation_models import StockOperationOrder
from app.formal_services import inventory_notifications as notifications
from app.formal_services import stock_loss_facts as loss, stock_loss_disposition_facts as disposition
from test_stock_loss_dispositions import db, world, stock, allowed, evidence, regional, headquarters, approved, prepare, execute_request, plan, commands
from test_work_order_removed_registration import inventory


def check_projection(db, allowed, transaction_id, kind, business, business_id):
    original = db.scalar(select(NotificationEvent).where(NotificationEvent.event_type == kind,
        NotificationEvent.business_type == business, NotificationEvent.business_id == str(business_id)))
    assert original is not None and original.target_manifest_sha256 is not None
    target = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == original.id)))
    assert target == (allowed.actor.person_id,)
    source = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'inventory_transaction',
        OutboxEvent.aggregate_id == str(transaction_id), OutboxEvent.event_type == 'inventory.transaction.posted'))
    before = inventory(db)
    result = notifications.project_inventory_notification(db, outbox_id=source.id)
    db.commit()
    assert inventory(db) == before
    assert result.affected_person_count == 1
    assert result.already_notified_person_count == 1, 'dedicated loss target is not recognized'
    assert not tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == result.event_id)))
    replay = notifications.project_inventory_notification(db, outbox_id=source.id)
    assert not replay.created and replay.event_id == result.event_id
    assert replay.already_notified_person_count == 1 and replay.recipient_count == 0
    assert inventory(db) == before


def test_loss_freeze_dedicated_notification_owns_original_target(db, allowed, regional):
    order = db.get(StockOperationOrder, regional.submission.operation_id)
    check_projection(db, allowed, order.posting_transaction_id, loss.KIND, loss.AGGREGATE, order.id)


@pytest.mark.parametrize('kind', ['restore_available','convert_used','convert_damaged'])
def test_loss_disposition_dedicated_notification_owns_original_target(db, allowed, approved, kind):
    request = prepare(db, approved, kind)
    preview = plan.preview_disposition(db, actor=approved.actor, request=request)
    result = commands.execute_disposition(db, actor=approved.actor, request=execute_request(preview, request))
    db.commit()
    check_projection(db, allowed, UUID(result['posting_transaction_id']), disposition.KIND,
        disposition.AGGREGATE, UUID(result['disposition_id']))
