from dataclasses import replace
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,text
from app.inventory_models import CustodyAssignment,StockLocation
from app.stock_operation_models import StockOperationOrder,StockOperationLine,StockLossDisposition
from app.formal_services import stock_return_origins as origins
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_loss_return_commands as commands,stock_loss_disposition_plan as hq
from test_stock_loss_derived_returns import db,world,stock,allowed,evidence,regional,headquarters,approved,route,execute_request
from test_stock_loss_dispositions import snapshot

@pytest.fixture
def derived(db,allowed,approved,route):
    request,_=execute_request(db,approved,route)
    result=commands.execute_loss_return(db,actor=approved.actor,request=request);db.commit()
    actor=replace(allowed.actor,entitlements=allowed.actor.entitlements+tuple(
        replace(allowed.actor.entitlements[0],resource='stock_operation',action=action)
        for action in ('outbound_return','ship_return')))
    allowed.world.current_principal=actor
    order=db.get(StockOperationOrder,UUID(result['return_operation_id']))
    line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==order.id)).one()
    return SimpleNamespace(actor=actor,order=order,line=line,result=result)

@pytest.mark.parametrize('action',('outbound_return','ship_return'))
def test_exact_engineer_origin_read_only_without_current_hq_authority(db,derived,approved,monkeypatch,action):
    def forbidden(*a,**kw):raise AssertionError('physical executor must not require a new HQ execution')
    monkeypatch.setattr(hq,'authorize',forbidden)
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    actor,order,origin=origins.authorize_return_fulfillment(db,actor=derived.actor,operation_id=derived.order.id,action=action)
    assert actor.person_id==origin.requester_id!=approved.actor.person_id
    assert origin.submitted_by_user_id==approved.actor.user_id!=actor.user_id
    assert origin.origin_kind=='loss_report' and origin.loss_line_id==derived.line.source_loss_line_id
    assert origins.line_origin(origin,derived.line)=={'source_loss_line_id':str(derived.line.source_loss_line_id)}
    event=origins.event_origin(origin)
    assert event['loss_disposition_id']==derived.result['disposition_id'] and event['headquarters_decision_id']==derived.result['headquarters_decision_id']
    assert 'work_order_id' not in event and 'work_order_id' not in origin.model_dump()
    assert derived.line.target_condition=='new' and snapshot(db)==before and not db.new and not db.dirty


def test_hq_cannot_claim_engineers_physical_action(db,derived,approved,allowed):
    actor=replace(approved.actor,entitlements=approved.actor.entitlements+(replace(derived.actor.entitlements[-1],action='outbound_return',scope_type='person',scope_id=str(approved.actor.person_id)),))
    assert actor.allows(db,'stock_operation','outbound_return',target_scope_type='person',target_scope_id=str(actor.person_id))
    allowed.world.current_principal=actor;before=snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        origins.authorize_return_fulfillment(db,actor=actor,operation_id=derived.order.id,action='outbound_return')
    assert error.value.code=='stock_return_not_found' and snapshot(db)==before


def test_current_physical_permission_revocation_is_not_bypassed_by_history(db,derived,allowed):
    allowed.world.current_principal=replace(derived.actor,entitlements=tuple(e for e in derived.actor.entitlements if e.action!='outbound_return'))
    before=snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        origins.authorize_return_fulfillment(db,actor=derived.actor,operation_id=derived.order.id,action='outbound_return')
    assert error.value.code=='stock_return_forbidden' and snapshot(db)==before

@pytest.mark.parametrize('damage',('expired','overlap_other_person','location_person','inactive'))
def test_changed_current_custody_blocks_execution_but_preserves_original_history(db,derived,approved,damage):
    at=datetime.now(timezone.utc);location=db.get(StockLocation,derived.order.source_location_id)
    assignment=db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id==location.id)).one()
    if damage=='expired':assignment.valid_to=at-timedelta(microseconds=1)
    elif damage=='overlap_other_person':db.add(CustodyAssignment(location_id=location.id,custodian_person_id=approved.actor.person_id,valid_from=at-timedelta(seconds=1),valid_to=at+timedelta(minutes=1)))
    elif damage=='location_person':location.custodian_person_id=approved.actor.person_id
    else:location.status='inactive'
    db.commit();before=snapshot(db)
    original=origins.verify_return_origin(db,actor=derived.actor,order=derived.order)
    assert original.operation_id==derived.order.id
    with pytest.raises(InventoryReadError) as error:
        origins.authorize_return_fulfillment(db,actor=derived.actor,operation_id=derived.order.id,action='outbound_return')
    assert error.value.code=='stock_return_source_custody_changed' and snapshot(db)==before

@pytest.mark.parametrize('damage',('root_link','reason','quantity','foreign_line'))
def test_invalid_original_evidence_never_becomes_a_fulfillment_source(db,derived,damage):
    # Only corrupt this local fixture; release PG constraints remain unchanged.
    if damage=='root_link':
        row=db.get(StockLossDisposition,UUID(derived.result['disposition_id']));db.delete(row)
    elif damage=='reason':derived.order.reason+=' changed'
    elif damage=='quantity':derived.line.quantity+=1
    else:
        origin=origins.verify_return_origin(db,actor=derived.actor,order=derived.order)
        with pytest.raises(InventoryReadError):origins.line_origin(origin,SimpleNamespace(operation_id=uuid4()))
        return
    db.flush();before=snapshot(db)
    with pytest.raises(InventoryReadError):origins.verify_return_origin(db,actor=derived.actor,order=derived.order)
    assert snapshot(db)==before
