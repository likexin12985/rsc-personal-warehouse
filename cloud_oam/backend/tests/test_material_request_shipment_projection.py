"""Split-package projection and immutable recovery across later packages."""
from decimal import Decimal
from uuid import UUID, uuid4
from types import SimpleNamespace

from sqlalchemy import select
from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent
from app.inventory_models import StockBalance, Shipment
from app.formal_services import material_request_shipment as shipment
from app.formal_services.material_request_fulfillment_command import verify_fulfillment_command
from test_material_request_shipment import outbound_world, pick_world, release_world, approval_db
from test_material_request_outbound import _create, _input
from test_material_request_draft_service import SECRET


def test_partial_packages_keep_original_command_and_independent_axes(outbound_world):
    db, actor, request, pick, serials, calls, _ = outbound_world
    outbound = _create(outbound_world, _input(outbound_world, str(pick.picked_qty), serials))
    initial_balances = tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity,
        StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id)))
    target = uuid4()
    quantity = Decimal(1) if serials else pick.picked_qty / 2
    path = f'/api/v1/material-requests/{request.id}/shipments'
    def send(key, qty, identifiers):
        return shipment.create_shipment(db, actor=actor, request_id=request.id,
            expected_version=request.version, target_location_id=target, target_person_id=actor.person_id,
            carrier='人工承运', tracking_no=key, shipped_at='2026-09-09T10:00:00+08:00',
            lines=(SimpleNamespace(outbound_posting_id=UUID(outbound['posting_id']),
                shipped_qty=qty, serial_ids=tuple(identifiers)),),
            idempotency_key=key, secret=SECRET, trace_request_id='trace-'+key)
    first = send('shipment-split-first-0001', quantity, serials[:1])
    first_version = request.version
    command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.request_id==request.id,
        MaterialRequestCommand.target_version==first_version))
    immutable = (command.request_jsonb.copy(), command.result_jsonb.copy(), command.request_hash, command.result_hash)
    assert request.shipment_status=='shipped' and request.personal_inbound_status=='pending_acceptance'
    second = send('shipment-split-second-0002', pick.picked_qty-quantity, serials[1:])
    assert second['shipment_id'] != first['shipment_id'] and request.version==first_version+1
    assert request.shipment_status=='shipped' and request.personal_inbound_status=='pending_acceptance'
    assert request.logistics_signature_status=='not_signed'
    assert request.oam_receipt_status=='not_occurred' and request.notification_status=='not_started'
    before = tuple((x.id,x.result_hash) for x in db.scalars(select(MaterialRequestCommand).order_by(MaterialRequestCommand.id)))
    audits = tuple(x.id for x in db.scalars(select(AuditEvent).order_by(AuditEvent.id)))
    recovered = shipment.shipment_command_status(db, actor=actor, request_id=request.id,
        idempotency_key='shipment-split-first-0001', secret=SECRET)
    assert recovered['command']['shipment_id']==first['shipment_id']
    verified = verify_fulfillment_command(db, request=request, actor=actor, operation='shipment',
        fact=db.get(Shipment,first['shipment_id']),request_reference=path,expected_version=first_version)
    assert verified.id==command.id
    db.refresh(command)
    assert (command.request_jsonb,command.result_jsonb,command.request_hash,command.result_hash)==immutable
    assert tuple((x.id,x.result_hash) for x in db.scalars(select(MaterialRequestCommand).order_by(MaterialRequestCommand.id)))==before
    assert tuple(x.id for x in db.scalars(select(AuditEvent).order_by(AuditEvent.id)))==audits
    assert tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity,
        StockBalance.version,StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id)))==initial_balances
