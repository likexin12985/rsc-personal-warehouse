from datetime import datetime,timedelta,timezone
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,text
from app.inventory_models import StockAccount,StockBalance,InventorySerial,SerialCurrentPosition,Shipment,Receipt
from app.foundation_models import OutboxEvent
from app.stock_operation_models import StockOperationSerial,StockOperationOutbound,StockLossDisposition
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn,StockReturnOutboundSubmitIn
from app.formal_services import stock_return_outbound_plan as plan,stock_return_outbound_commands as commands,stock_return_outbound_facts as facts
from app.formal_services import stock_loss_disposition_facts as original
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.work_order_return_sources import _hash
from test_stock_return_loss_origin import db,world,stock,allowed,evidence,regional,headquarters,approved,route,derived
from test_inventory_posting import establish_account_for_posting,NOW
from test_stock_loss_dispositions import snapshot as loss_snapshot
from test_stock_return_outbound import snapshot as outbound_snapshot


def snapshot(db):return loss_snapshot(db),outbound_snapshot(db)

@pytest.fixture
def ready(db,allowed,derived,route):
    target,transit=route
    transit.created_at=transit.updated_at=NOW-timedelta(days=2)
    seed=StockAccount(id=uuid4(),owner_org_id=target.owner_org_id,location_id=transit.id,custodian_person_id=None,
        material_id=allowed.account.material_id,condition_code='new',availability_bucket='available',lot_id=None,
        created_at=NOW-timedelta(days=1),updated_at=NOW-timedelta(days=1))
    db.add(seed);db.flush();establish_account_for_posting(db,allowed.world,seed);db.commit()
    allowed.world.current_principal=derived.actor
    ids=tuple(db.scalars(select(StockOperationSerial.serial_id).where(StockOperationSerial.line_id==derived.line.id)))
    proofs=[]
    for identifier in ids:
        serial=db.get(InventorySerial,identifier)
        proofs.append(dict(serial_id=identifier,serial_no=serial.serial_no,qr_code=serial.qr_code,sku_code=allowed.world.material.sku_code))
    request=StockReturnOutboundPreviewIn(operator_person_id=derived.actor.person_id,outbound_at=datetime.now(timezone.utc),
        reason='Synthetic physical loss return departure',lines=(dict(operation_line_id=derived.line.id,quantity='1',serial_verifications=proofs),))
    return SimpleNamespace(derived=derived,request=request,transit=transit)


def preview(db,ready,request=None):
    return plan.preview_outbound(db,actor=ready.derived.actor,work_order_id=None,operation_id=ready.derived.order.id,request=request or ready.request)[0]


def submit(db,ready,request=None):
    request=request or ready.request;checked=preview(db,ready,request)
    value=StockReturnOutboundSubmitIn(**request.model_dump(),expected_plan_hash=checked.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex)
    result=commands.execute_outbound(db,actor=ready.derived.actor,work_order_id=None,operation_id=ready.derived.order.id,request=value)
    db.commit();return result,value


def test_loss_preview_is_readonly_and_has_exact_hq_origin_without_work_order(db,ready,approved):
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    value=preview(db,ready);wire=value.model_dump(mode='json')
    assert value.origin.requester_id==ready.derived.actor.person_id
    assert value.origin.submitted_by_user_id==approved.actor.user_id!=ready.derived.actor.user_id
    assert value.lines[0].condition_code=='new' and value.lines[0].source_loss_line_id==ready.derived.line.source_loss_line_id
    assert 'work_order_id' not in wire and 'source_recovery_line_id' not in wire['lines'][0]
    assert 'qr_code' not in value.model_dump_json() and snapshot(db)==before and not db.new and not db.dirty


def test_departure_uses_original_engineer_and_condition_without_claiming_shipment_or_receipt(db,ready,allowed):
    result,value=submit(db,ready)
    assert result.status=='outbound' and result.operator_person_id==ready.derived.actor.person_id
    target=db.scalars(select(StockAccount).where(StockAccount.location_id==ready.transit.id,StockAccount.availability_bucket=='in_transit')).one()
    assert target.custodian_person_id==ready.derived.actor.person_id and target.condition_code=='new'
    assert db.get(StockBalance,target.id).quantity==1 and db.get(StockBalance,ready.derived.line.reserved_account_id).quantity==0
    if allowed.tracked:
        for proof in ready.request.lines[0].serial_verifications:assert db.get(SerialCurrentPosition,proof.serial_id).stock_account_id==target.id
    assert not tuple(db.scalars(select(Shipment.id))) and not tuple(db.scalars(select(Receipt.id)))
    assert original.verified(db,row=db.get(StockLossDisposition,UUID(ready.derived.result['disposition_id'])))==ready.derived.result
    event=db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_type=='stock_operation_outbound',OutboxEvent.aggregate_id==str(result.outbound_id))).one()
    assert event.payload_jsonb['origin_kind']=='loss_report' and 'work_order_id' not in event.payload_jsonb
    before=snapshot(db)
    assert commands.execute_outbound(db,actor=ready.derived.actor,work_order_id=None,operation_id=ready.derived.order.id,request=value)==result
    db.commit();assert snapshot(db)==before
    db.execute(text('PRAGMA query_only=ON'))
    assert facts.outbound_result(db,actor=ready.derived.actor,fact=db.get(StockOperationOutbound,result.outbound_id))==result


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_exact_partial_loss_departures_preserve_origin_and_remainder(db,ready):
    first=ready.request.model_copy(update={'lines':(ready.request.lines[0].model_copy(update={'quantity':Decimal('.375')}),)})
    result,_=submit(db,ready,first)
    rest=ready.request.model_copy(update={'lines':(ready.request.lines[0].model_copy(update={'quantity':Decimal('.625')}),)})
    view=preview(db,ready,rest)
    assert view.lines[0].departed_quantity=='0.375' and view.lines[0].remaining_quantity=='0.625'
    other,_=submit(db,ready,rest);assert result.posting_transaction_id!=other.posting_transaction_id
    assert db.get(StockBalance,ready.derived.line.reserved_account_id).quantity==0
    before=snapshot(db)
    with pytest.raises(InventoryReadError):preview(db,ready,rest)
    assert snapshot(db)==before


@pytest.mark.parametrize('damage',('foreign_line','excess','duplicate','future','permission','hq_operator'))
def test_invalid_loss_departure_does_not_change_any_fact(db,ready,allowed,approved,damage):
    request=ready.request
    if damage=='foreign_line':request=request.model_copy(update={'lines':(request.lines[0].model_copy(update={'operation_line_id':uuid4()}),)})
    elif damage=='excess':request=request.model_copy(update={'lines':(request.lines[0].model_copy(update={'quantity':Decimal(2)}),)})
    elif damage=='duplicate':request=request.model_copy(update={'lines':request.lines*2})
    elif damage=='future':request=request.model_copy(update={'outbound_at':datetime.now(timezone.utc)+timedelta(days=1)})
    elif damage=='hq_operator':request=request.model_copy(update={'operator_person_id':approved.actor.person_id})
    else:allowed.world.current_principal=replace(ready.derived.actor,entitlements=tuple(e for e in ready.derived.actor.entitlements if e.action!='outbound_return'))
    before=snapshot(db)
    with pytest.raises(InventoryReadError):preview(db,ready,request)
    assert snapshot(db)==before


@pytest.mark.parametrize('stage',('domain_event','final_proof'))
def test_late_failure_rolls_back_transit_account_posting_and_departure(db,ready,monkeypatch,stage):
    view=preview(db,ready)
    request=StockReturnOutboundSubmitIn(**ready.request.model_dump(),expected_plan_hash=view.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex)
    before=snapshot(db)
    def failure(*a,**kw):raise RuntimeError('synthetic loss departure failure')
    monkeypatch.setattr(commands if stage=='domain_event' else facts,'_record' if stage=='domain_event' else 'outbound_result',failure)
    with pytest.raises(RuntimeError,match='synthetic loss departure failure'):
        commands.execute_outbound(db,actor=ready.derived.actor,work_order_id=None,operation_id=ready.derived.order.id,request=request)
    db.rollback();assert snapshot(db)==before


@pytest.mark.parametrize('damage',('origin','line_source'))
def test_forged_loss_provenance_fails_even_with_rehashed_plan(db,ready,damage):
    result,_=submit(db,ready);row=db.get(StockOperationOutbound,result.outbound_id)
    if damage=='origin':row.plan_jsonb={**row.plan_jsonb,'origin':{**row.plan_jsonb['origin'],'loss_operation_id':str(uuid4())}}
    else:row.plan_jsonb={**row.plan_jsonb,'lines':[{**row.plan_jsonb['lines'][0],'source_loss_line_id':str(uuid4())}]}
    row.plan_hash=_hash(row.plan_jsonb);db.flush()
    with pytest.raises(InventoryReadError):facts.outbound_result(db,actor=ready.derived.actor,fact=row)


def test_revocation_blocks_replay_without_erasing_departure_history(db,ready,allowed):
    result,request=submit(db,ready)
    allowed.world.current_principal=replace(ready.derived.actor,entitlements=tuple(e for e in ready.derived.actor.entitlements if e.action!='outbound_return'))
    before=snapshot(db)
    with pytest.raises(InventoryReadError):commands.execute_outbound(db,actor=ready.derived.actor,work_order_id=None,operation_id=ready.derived.order.id,request=request)
    assert facts.outbound_result(db,actor=ready.derived.actor,fact=db.get(StockOperationOutbound,result.outbound_id))==result
    assert snapshot(db)==before
