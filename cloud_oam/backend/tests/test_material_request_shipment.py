from decimal import Decimal
from dataclasses import replace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, func
from app.formal_services import material_request_shipment as shipment
from app.formal_services.material_request_query import MaterialRequestReadError
from app.inventory_models import Shipment, ShipmentLine, ShipmentSerial, InventoryTransaction, StockBalance
from test_material_request_outbound import outbound_world, _create as create_outbound, _input as outbound_input
from test_material_request_picking import pick_world
from test_material_request_reservation_release import release_world
from test_material_request_fulfillment_preparation import approval_db
from test_material_request_draft_service import SECRET


def test_registers_package_against_posting_without_second_inventory_movement(outbound_world):
    db, actor, request, fact, serials, calls, target_id = outbound_world
    first=create_outbound(outbound_world)
    db.flush(); before=tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity,StockBalance.version,StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))); tx_before=db.scalar(select(func.count()).select_from(InventoryTransaction))
    result=shipment.create_shipment(db,actor=actor,request_id=request.id,expected_version=request.version,target_location_id=uuid4(),target_person_id=actor.person_id,carrier='人工承运',tracking_no='TEST-001',shipped_at='2026-09-09T10:00:00+08:00',lines=(type('L',(),{'outbound_posting_id':UUID(first['posting_id']),'shipped_qty':Decimal(first['outbound_qty']),'serial_ids':tuple(serials[:1])})(),),idempotency_key='shipment-command-0001',secret=SECRET,trace_request_id='trace-shipment-command-0001')
    db.flush()
    assert result['status']=='shipped' and len(result['lines'])==1
    after=tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity,StockBalance.version,StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id)))
    assert before==after
    assert db.scalar(select(func.count()).select_from(InventoryTransaction))==tx_before
    assert db.scalar(select(func.count()).select_from(Shipment))==1


def test_cannot_ship_more_than_posted(outbound_world):
    db, actor, request, fact, serials, calls, target_id = outbound_world
    first=create_outbound(outbound_world)
    with pytest.raises(shipment.ShipmentError):
        shipment.create_shipment(db,actor=actor,request_id=request.id,expected_version=request.version,target_location_id=uuid4(),target_person_id=actor.person_id,carrier='人工承运',tracking_no='TEST-002',shipped_at='2026-09-09T10:00:00+08:00',lines=(type('L',(),{'outbound_posting_id':UUID(first['posting_id']),'shipped_qty':Decimal(first['outbound_qty'])+Decimal('0.001'),'serial_ids':tuple(serials[:1])})(),),idempotency_key='shipment-command-0002',secret=SECRET,trace_request_id='trace-shipment-command-0002')


def test_shipment_command_status_recovers_exact_idempotent_result(outbound_world):
    db, actor, request, fact, serials, calls, target_id = outbound_world
    first = create_outbound(outbound_world)
    key = 'shipment-command-recovery-001'
    result = shipment.create_shipment(
        db, actor=actor, request_id=request.id, expected_version=request.version,
        target_location_id=uuid4(), target_person_id=actor.person_id,
        carrier='人工承运', tracking_no='RECOVER-001',
        shipped_at='2026-09-09T10:00:00+08:00',
        lines=(type('L', (), {'outbound_posting_id': UUID(first['posting_id']),
                              'shipped_qty': Decimal(first['outbound_qty']),
                              'serial_ids': tuple(serials[:1])})(),),
        idempotency_key=key, secret=SECRET,
        trace_request_id='trace-shipment-recovery-001',
    )
    db.flush()
    recovered = shipment.shipment_command_status(
        db, actor=actor, request_id=request.id, idempotency_key=key, secret=SECRET,
    )
    assert recovered is not None
    assert recovered['request_hash']
    assert recovered['command']['shipment_id'] == result['shipment_id']
    assert recovered['command']['idempotency_replayed'] is True
    assert shipment.shipment_command_status(
        db, actor=actor, request_id=request.id,
        idempotency_key='shipment-command-recovery-missing', secret=SECRET,
    ) is None
    shipment_row = db.get(Shipment, result['shipment_id'])
    assert shipment_row is not None
    shipment_row.request_hash = 'tampered'
    with pytest.raises(shipment.ShipmentError) as forged:
        shipment.shipment_command_status(
            db, actor=actor, request_id=request.id, idempotency_key=key, secret=SECRET,
        )
    assert forged.value.code == 'history_invalid'
    with pytest.raises(MaterialRequestReadError) as changed:
        shipment.shipment_command_status(
            db, actor=replace(actor, authorization_version=actor.authorization_version + 1),
            request_id=request.id, idempotency_key=key, secret=SECRET,
        )
    assert changed.value.code == 'material_request_actor_principal_stale'
