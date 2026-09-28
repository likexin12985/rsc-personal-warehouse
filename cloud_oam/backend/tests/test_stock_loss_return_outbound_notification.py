"""Historical proof must bind the durable notification, not its delivery status."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.foundation_models import NotificationEvent, NotificationPersonTarget
from app.stock_operation_models import StockOperationOutbound
from app.formal_services import stock_return_outbound_facts as facts
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.notification_events import target_manifest_hash
from test_stock_loss_return_outbound import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    derived, ready, submit,
)


@pytest.mark.parametrize('damage', ('missing_target', 'wrong_target', 'manifest', 'payload', 'time', 'duplicate_event'))
def test_loss_departure_history_rejects_incomplete_notification(db, ready, damage):
    result, _ = submit(db, ready)
    fact = db.get(StockOperationOutbound, result.outbound_id)
    event = db.scalars(select(NotificationEvent).where(
        NotificationEvent.business_type == 'stock_operation_outbound',
        NotificationEvent.business_id == str(result.outbound_id),
    )).one()
    target = db.scalars(select(NotificationPersonTarget).where(
        NotificationPersonTarget.event_id == event.id,
    )).one()
    if damage == 'missing_target':
        db.delete(target)
    elif damage == 'wrong_target':
        target.person_id = ready.derived.actor.person_id
        event.target_manifest_sha256 = target_manifest_hash((target.person_id,))
    elif damage == 'manifest':
        event.target_manifest_sha256 = '0' * 64
    elif damage == 'payload':
        event.payload_jsonb = {**event.payload_jsonb, 'origin_kind': 'work_order_recovery'}
    elif damage == 'time':
        event.occurred_at += timedelta(seconds=1)
    else:
        db.add(NotificationEvent(
            event_type=event.event_type, business_type=event.business_type,
            business_id=event.business_id, dedup_key=event.dedup_key + ':extra',
            payload_jsonb=event.payload_jsonb, status='pending',
            occurred_at=event.occurred_at, created_at=event.created_at,
            target_manifest_sha256=event.target_manifest_sha256,
        ))
    db.flush()
    with pytest.raises(InventoryReadError) as error:
        facts.outbound_result(db, actor=ready.derived.actor, fact=fact)
    assert error.value.code == 'stock_return_outbound_evidence_invalid'


def test_notification_expansion_is_independent_from_departure_history(db, ready):
    result, _ = submit(db, ready)
    event = db.scalars(select(NotificationEvent).where(
        NotificationEvent.business_type == 'stock_operation_outbound',
        NotificationEvent.business_id == str(result.outbound_id),
    )).one()
    event.status = 'expanded'
    db.flush()
    assert facts.outbound_result(db, actor=ready.derived.actor,
        fact=db.get(StockOperationOutbound, result.outbound_id)) == result
