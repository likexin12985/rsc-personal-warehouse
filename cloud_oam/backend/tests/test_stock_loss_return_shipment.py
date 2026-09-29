"""Loss-origin shipment service contracts; PostgreSQL guarantees have separate gates."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from app.foundation_models import NotificationEvent, NotificationPersonTarget
from app.inventory_models import Receipt, Shipment, StockBalance
from app.stock_operation_models import StockOperationOutboundLine, StockOperationShipment
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentSubmitIn
from app.formal_services import stock_return_shipment_plan as plan, stock_return_shipment_commands as commands, stock_return_shipment_facts as facts
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_return_outbound import db, world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready, submit as depart
from test_stock_return_shipment import inventory, snapshot


@pytest.fixture
def parcel(db, ready, allowed):
    departure, _ = depart(db, ready)
    # Existing shipment history requires the independent current read grant.
    # This synthetic engineer fixture previously only granted ship/outbound.
    ready.derived.actor = replace(ready.derived.actor, entitlements=ready.derived.actor.entitlements +
        (replace(ready.derived.actor.entitlements[-1], resource='stock_operation', action='read'),))
    allowed.world.current_principal = ready.derived.actor
    line = db.scalars(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id == departure.outbound_id)).one()
    request = StockReturnShipmentPreviewIn(operator_person_id=ready.derived.actor.person_id, carrier='Synthetic carrier',
        tracking_no='SYNTHETIC-'+uuid4().hex, shipped_at=datetime.now(timezone.utc), reason='Physical handover of an approved loss return',
        lines=(dict(outbound_line_id=line.id, quantity='1.000', serial_ids=tuple(s.serial_id for s in departure.lines[0].selected_serials)),))
    return SimpleNamespace(actor=ready.derived.actor, order=ready.derived.order, line=line, request=request)


def preview(db, parcel, request=None):
    return plan.preview_shipment(db, actor=parcel.actor, work_order_id=None, operation_id=parcel.order.id, request=request or parcel.request)[0]


def execute(db, parcel, request=None):
    request = request or parcel.request
    checked = preview(db, parcel, request)
    value = StockReturnShipmentSubmitIn(**request.model_dump(), expected_plan_hash=checked.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    result = commands.execute_shipment(db, actor=parcel.actor, work_order_id=None, operation_id=parcel.order.id, request=value)
    db.commit()
    return result, value


def test_loss_shipment_preview_is_readonly_and_preserves_origin_and_new_condition(db, parcel, approved):
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = preview(db, parcel)
    wire = result.model_dump(mode='json')
    assert result.origin.submitted_by_user_id == approved.actor.user_id != parcel.actor.user_id
    assert result.operator_person_id == result.origin.requester_id == parcel.actor.person_id
    assert result.lines[0].condition_code == 'new' and 'source_loss_line_id' in wire['lines'][0]
    assert 'work_order_id' not in wire and 'source_recovery_line_id' not in wire['lines'][0]
    assert 'qr_code' not in result.model_dump_json()
    assert snapshot(db) == before and not db.new and not db.dirty


def test_loss_shipment_and_replay_are_stock_neutral_and_are_not_receipt(db, parcel):
    before = inventory(db)
    result, value = execute(db, parcel)
    assert result.status == 'shipped' and result.lines[0].condition_code == 'new'
    assert result.origin.origin_kind == 'loss_report' and inventory(db) == before
    assert not tuple(db.scalars(select(Receipt.id)))
    assert db.get(StockBalance, parcel.line.transit_stock_account_id).quantity == 1
    after = snapshot(db)
    replay = commands.execute_shipment(db, actor=parcel.actor, work_order_id=None, operation_id=parcel.order.id, request=value)
    db.commit()
    assert replay == result and snapshot(db) == after
    fact = db.get(StockOperationShipment, result.shipment_id)
    db.execute(text('PRAGMA query_only=ON'))
    assert facts.verified_shipment_history(db, fact=fact) == result
    assert snapshot(db) == after


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_partial_loss_parcels_bind_exact_departure_budget_without_reposting(db, parcel):
    before = inventory(db)
    first = parcel.request.model_copy(update={'lines':(parcel.request.lines[0].model_copy(update={'quantity':Decimal('.375')}),)})
    one, _ = execute(db, parcel, first)
    with pytest.raises(InventoryReadError) as error:
        preview(db, parcel)
    assert error.value.code == 'stock_return_shipment_quantity_exceeded'
    rest = first.model_copy(update={'tracking_no':'SECOND-'+uuid4().hex,'lines':(first.lines[0].model_copy(update={'quantity':Decimal('.625')}),)})
    two, _ = execute(db, parcel, rest)
    a, b = (db.get(StockOperationShipment, item.shipment_id) for item in (one,two))
    assert a.audit_version < b.audit_version and a.plan_jsonb['ledger_cursor'] == b.plan_jsonb['ledger_cursor']
    assert facts.verified_shipment_history(db, fact=a) == one
    assert facts.verified_shipment_history(db, fact=b) == two
    assert inventory(db) == before


def test_revoked_shipping_authority_rejects_a_new_handover_without_rewriting_history(db, parcel, allowed):
    result, _ = execute(db, parcel)
    before = snapshot(db)
    allowed.world.current_principal = replace(parcel.actor, entitlements=tuple(e for e in parcel.actor.entitlements if e.action != 'ship_return'))
    with pytest.raises(InventoryReadError):
        preview(db, parcel)
    assert facts.verified_shipment_history(db, fact=db.get(StockOperationShipment,result.shipment_id)) == result
    assert snapshot(db) == before


@pytest.mark.parametrize('damage', ('origin','line_source','notification_payload','missing_target','manifest','event_time'))
def test_loss_parcel_history_rejects_forged_origin_or_notification_intent(db, parcel, damage):
    result, _ = execute(db, parcel)
    fact = db.get(StockOperationShipment, result.shipment_id)
    event = db.scalars(select(NotificationEvent).where(NotificationEvent.business_type=='stock_operation_shipment',
        NotificationEvent.business_id==str(fact.id))).one()
    if damage == 'origin':
        fact.plan_jsonb = {**fact.plan_jsonb, 'origin':{**fact.plan_jsonb['origin'], 'loss_line_id':str(uuid4())}}
    elif damage == 'line_source':
        fact.plan_jsonb = {**fact.plan_jsonb, 'lines':[{**fact.plan_jsonb['lines'][0], 'source_loss_line_id':str(uuid4())}]}
    elif damage == 'notification_payload':
        event.payload_jsonb = {**event.payload_jsonb, 'origin_kind':'work_order_recovery'}
    elif damage == 'missing_target':
        db.delete(db.scalars(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id==event.id)).one())
    elif damage == 'manifest':
        event.target_manifest_sha256 = '0'*64
    else:
        from datetime import timedelta
        event.occurred_at += timedelta(seconds=1)
    # Rehashing cannot make a semantically forged snapshot valid.
    from app.formal_services.work_order_return_sources import _hash
    fact.plan_hash = _hash(fact.plan_jsonb)
    db.flush()
    with pytest.raises(InventoryReadError) as error:
        facts.verified_shipment_history(db, fact=fact)
    assert error.value.code == 'stock_return_shipment_evidence_invalid'


def test_notification_expansion_is_independent_of_loss_shipment_history(db, parcel):
    result, _ = execute(db, parcel)
    event = db.scalars(select(NotificationEvent).where(NotificationEvent.business_type=='stock_operation_shipment',
        NotificationEvent.business_id==str(result.shipment_id))).one()
    event.status = 'expanded'
    db.flush()
    assert facts.verified_shipment_history(db, fact=db.get(StockOperationShipment,result.shipment_id)) == result
