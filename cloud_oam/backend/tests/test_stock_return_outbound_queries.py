"""Departure choices and history reflect actual stock without exposing QR proof."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import text
from app.formal_services import stock_return_outbound_queries as query
from app.formal_services.stock_return_commands import cancel_return
from app.formal_services.inventory_query import InventoryReadError
from app.stock_return_schemas import StockReturnCancelIn
from test_stock_return_outbound import db,world,stock,recovered,destination,prepared,submit,snapshot
from test_formal_stock_return_routes import client


def coordinates(stock,prepared):
    return dict(actor=stock.actor,work_order_id=stock.orders[0].id,operation_id=prepared.order.operation_id)


def url(stock,prepared):return f'/api/v1/work-orders/{stock.orders[0].id}/returns/{prepared.order.operation_id}/outbounds'


def test_options_and_empty_history_are_read_only_and_do_not_supply_scan_proof(db,stock,prepared,client):
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    response=client.get(url(stock,prepared)+'/options')
    assert response.status_code==200 and 'no-store' in response.headers['cache-control'],response.text
    row=response.json()['lines'][0]
    assert row['return_quantity']==row['remaining_quantity']==row['held_quantity']==row['selectable_quantity']=='1.000'
    assert row['departed_quantity']=='0.000' and len(row['serials'])==(1 if stock.tracked else 0)
    assert 'qr_code' not in response.text and 'serial_verifications' not in response.text
    history=client.get(url(stock,prepared))
    assert history.status_code==200 and 'no-store' in history.headers['cache-control']
    assert history.json()['outbound_status']=='not_outbound' and history.json()['items']==[]
    assert history.json()['original']['status']=='submitted' and history.json()['cancellation'] is None
    assert snapshot(db)==before


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_partial_and_complete_history_follow_each_original_line(db,stock,prepared):
    selected=prepared.request.model_copy(update={'lines':(prepared.request.lines[0].model_copy(update={'quantity':Decimal('.375')}),)})
    first,_=submit(db,stock,prepared,selected)
    options=query.outbound_options(db,**coordinates(stock,prepared))
    assert options.lines[0].departed_quantity=='0.375'
    assert options.lines[0].remaining_quantity==options.lines[0].selectable_quantity=='0.625'
    history=query.outbound_history(db,**coordinates(stock,prepared))
    assert history.outbound_status=='partially_outbound' and history.items==(first,)
    assert history.original.status=='submitted' and history.cancellation is None
    rest=selected.model_copy(update={'lines':(selected.lines[0].model_copy(update={'quantity':Decimal('.625')}),)})
    second,_=submit(db,stock,prepared,rest)
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    assert query.outbound_options(db,**coordinates(stock,prepared)).lines[0].selectable_quantity=='0.000'
    final=query.outbound_history(db,**coordinates(stock,prepared))
    assert final.outbound_status=='outbound' and final.items==(first,second) and final.original==history.original
    assert snapshot(db)==before


@pytest.mark.parametrize('stock',['serial'],indirect=True)
def test_departed_serial_is_removed_from_candidates_but_retained_in_history(db,stock,prepared):
    before=query.outbound_options(db,**coordinates(stock,prepared)).lines[0].serials
    result,_=submit(db,stock,prepared)
    options=query.outbound_options(db,**coordinates(stock,prepared))
    assert options.lines[0].serials==() and options.lines[0].remaining_quantity=='0.000'
    history=query.outbound_history(db,**coordinates(stock,prepared))
    assert history.outbound_status=='outbound' and history.items==(result,)
    assert history.items[0].lines[0].selected_serials==before
    assert 'qr_code' not in history.model_dump_json()


def test_cancellation_is_independent_and_no_longer_offered_for_departure(db,stock,prepared):
    cancelled=cancel_return(db,actor=stock.actor,operation_id=prepared.order.operation_id,
        request=StockReturnCancelIn(operator_person_id=stock.actor.person_id,reason='Return cancelled before departure',request_id=uuid4().hex,idempotency_key=uuid4().hex));db.commit()
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    result=query.outbound_history(db,**coordinates(stock,prepared))
    assert result.original.status=='submitted' and result.outbound_status=='not_outbound'
    assert result.cancellation==cancelled and result.items==()
    with pytest.raises(InventoryReadError) as exc:query.outbound_options(db,**coordinates(stock,prepared))
    assert exc.value.code=='stock_return_already_cancelled' and snapshot(db)==before


def test_current_history_and_departure_permissions_are_separate(db,stock,prepared):
    stock.world.current_principal=replace(stock.actor,entitlements=tuple(row for row in stock.actor.entitlements if row.action!='outbound_return'))
    assert query.outbound_history(db,**coordinates(stock,prepared)).outbound_status=='not_outbound'
    with pytest.raises(InventoryReadError) as exc:query.outbound_options(db,**coordinates(stock,prepared))
    assert exc.value.code=='stock_return_forbidden'
    stock.world.current_principal=replace(stock.actor,entitlements=tuple(row for row in stock.actor.entitlements if not(row.resource=='stock_operation' and row.action=='read')))
    with pytest.raises(InventoryReadError) as exc:query.outbound_history(db,**coordinates(stock,prepared))
    assert exc.value.code=='stock_return_forbidden'


def test_wrong_work_order_cannot_read_another_return(db,stock,prepared,client):
    before=snapshot(db)
    wrong=f'/api/v1/work-orders/{stock.orders[1].id}/returns/{prepared.order.operation_id}/outbounds'
    for suffix in ('','/options'):
        response=client.get(wrong+suffix)
        assert response.status_code==404 and 'no-store' in response.headers['cache-control']
    assert snapshot(db)==before


def test_route_change_during_directory_read_does_not_publish_stale_choices(db,stock,prepared,monkeypatch):
    original=query._options_basis;calls=0
    def changing(*args):
        nonlocal calls
        value=original(*args);calls+=1
        if calls==1:
            prepared.transit.status='inactive';db.commit()
        return value
    monkeypatch.setattr(query,'_options_basis',changing)
    with pytest.raises(InventoryReadError):query.outbound_options(db,**coordinates(stock,prepared))
    assert calls==1
