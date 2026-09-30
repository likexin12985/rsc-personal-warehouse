"""Loss sender candidates: actual source/options/history under query-only DB."""
from dataclasses import replace
from uuid import uuid4
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_return_outbound_queries import outbound_options, outbound_history
from app.formal_services.stock_return_shipment_queries import shipment_options, shipment_history
from app.stock_return_outbound_schemas import StockReturnOutboundOptionsOut, StockReturnOutboundHistoryOut
from app.stock_return_shipment_schemas import StockReturnShipmentOptionsOut, StockReturnShipmentHistoryOut
from test_stock_loss_return_shipment import db, world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready, parcel, snapshot, execute


def coordinates(parcel):
    return dict(actor=parcel.actor,work_order_id=None,operation_id=parcel.order.id)


def test_loss_sender_choices_and_history_have_exclusive_origin_and_exact_budgets(db,parcel,approved):
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    outbound=outbound_options(db,**coordinates(parcel))
    departures=outbound_history(db,**coordinates(parcel))
    choices=shipment_options(db,**coordinates(parcel))
    parcels=shipment_history(db,**coordinates(parcel))
    assert outbound.origin==departures.original==choices.origin==parcels.origin
    assert choices.origin.requester_id==parcel.actor.person_id
    assert choices.origin.submitted_by_user_id==approved.actor.user_id!=parcel.actor.user_id
    assert outbound.lines[0].remaining_quantity=='0.000'
    assert outbound.lines[0].selectable_quantity=='0.000'
    assert choices.lines[0].selectable_quantity=='1.000'
    assert choices.lines[0].unshipped_quantity=='1.000'
    assert choices.lines[0].source_loss_line_id==choices.origin.loss_line_id
    assert departures.outbound_status=='outbound' and len(departures.items)==1
    assert parcels.shipment_status=='not_shipped' and parcels.items==()
    # The nested history is a fresh read; every business field must still match.
    assert parcels.departures.model_dump(exclude={'queried_at'})==departures.model_dump(exclude={'queried_at'})
    assert departures.queried_at.tzinfo is not None
    assert parcels.departures.queried_at.tzinfo is not None
    assert parcels.departures.queried_at>=departures.queried_at
    for value,ordinary in [(outbound,StockReturnOutboundOptionsOut),(departures,StockReturnOutboundHistoryOut),
                            (choices,StockReturnShipmentOptionsOut),(parcels,StockReturnShipmentHistoryOut)]:
        wire=value.model_dump(mode='json')
        assert 'work_order_id' not in wire and 'source_recovery_line_id' not in value.model_dump_json()
        assert 'qr_code' not in value.model_dump_json()
        with pytest.raises(ValidationError):ordinary.model_validate(wire)
    assert snapshot(db)==before and not db.new and not db.dirty


def test_loss_sender_history_survives_write_revocation_but_choices_do_not(db,parcel,allowed):
    result,_=execute(db,parcel)
    readonly=replace(parcel.actor,entitlements=tuple(e for e in parcel.actor.entitlements if e.action not in ('outbound_return','ship_return')))
    allowed.world.current_principal=readonly
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    coordinates=dict(actor=readonly,work_order_id=None,operation_id=parcel.order.id)
    assert outbound_history(db,**coordinates).outbound_status=='outbound'
    history=shipment_history(db,**coordinates)
    assert history.shipment_status=='shipped' and history.items==(result,)
    for query in (outbound_options,shipment_options):
        with pytest.raises(InventoryReadError):query(db,**coordinates)
    assert snapshot(db)==before and not db.new and not db.dirty


def test_loss_sender_queries_reject_foreign_operation_and_wrong_work_order(db,parcel):
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    for query in (outbound_options,outbound_history,shipment_options,shipment_history):
        for change in ({'operation_id':uuid4()},{'work_order_id':uuid4()}):
            with pytest.raises(InventoryReadError):query(db,**{**coordinates(parcel),**change})
    assert snapshot(db)==before and not db.new and not db.dirty


def test_hq_deriver_cannot_read_engineers_physical_sender_surface(db,parcel,allowed,approved):
    allowed.world.current_principal=approved.actor
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    for query in (outbound_options,outbound_history,shipment_options,shipment_history):
        with pytest.raises(InventoryReadError):query(db,**{**coordinates(parcel),'actor':approved.actor})
    assert snapshot(db)==before and not db.new and not db.dirty
