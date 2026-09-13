"""Exact parcel choices and history retain original departure/stock boundaries."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from app.formal_services import stock_return_shipment_queries as query
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_return_commands import cancel_return
from app.formal_services.inventory_query import InventoryReadError
from app.stock_operation_models import StockOperationOutboundLine
from app.stock_return_schemas import StockReturnCancelIn
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn
from test_stock_return_shipment import db, world, stock, recovered, destination, prepared, parcel, submission, execute, snapshot
from test_stock_return_outbound import submit as depart
from test_formal_stock_return_routes import client


def coordinates(stock, prepared):
    return dict(actor=stock.actor, work_order_id=stock.orders[0].id, operation_id=prepared.order.operation_id)


def url(stock, prepared):
    return f'/api/v1/work-orders/{stock.orders[0].id}/returns/{prepared.order.operation_id}/shipments'


def grant(stock):
    stock.actor = replace(stock.actor, entitlements=stock.actor.entitlements + (
        replace(stock.actor.entitlements[0], resource='stock_operation', action='ship_return'),))
    stock.world.current_principal = stock.actor


def test_undeparted_return_has_no_parcel_choices_or_shipping_fact(db, stock, prepared, client, monkeypatch):
    grant(stock)
    guard = posting._require_no_active_hard_freezes
    def scoped_freezes(db, accounts, **kwargs):
        assert accounts, 'an empty parcel directory must not inspect unrelated freeze scopes'
        return guard(db, accounts, **kwargs)
    monkeypatch.setattr(posting, '_require_no_active_hard_freezes', scoped_freezes)
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    options = client.get(url(stock, prepared) + '/options')
    assert options.status_code == 200, options.text
    assert options.json()['lines'] == []
    history = client.get(url(stock, prepared))
    assert history.status_code == 200, history.text
    assert history.json()['shipment_status'] == 'not_shipped'
    assert history.json()['departures']['outbound_status'] == 'not_outbound'
    assert history.json()['departures']['original']['status'] == 'submitted'
    assert history.json()['items'] == []
    assert snapshot(db) == before


def test_choices_bind_exact_departure_and_reads_do_not_change_stock(db, stock, prepared, parcel, client):
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    response = client.get(url(stock, prepared) + '/options')
    assert response.status_code == 200, response.text
    row = response.json()['lines'][0]
    assert row['outbound_line_id'] == str(parcel.line.id)
    assert row['outbound_id'] == str(parcel.outbound.outbound_id)
    assert row['operation_line_id'] == str(prepared.line.id)
    assert row['outbound_quantity'] == row['unshipped_quantity'] == row['unassigned_quantity'] == row['selectable_quantity'] == '1.000'
    assert row['shipped_quantity'] == '0.000'
    assert len(row['serials']) == (1 if stock.tracked else 0)
    assert 'qr_code' not in response.text and 'serial_verifications' not in response.text
    history = client.get(url(stock, prepared))
    assert history.status_code == 200 and history.json()['shipment_status'] == 'not_shipped'
    assert history.json()['departures']['outbound_status'] == 'outbound'
    assert all('no-store' in item.headers['cache-control'] for item in (response, history))
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_partial_parcels_expose_remaining_quantity_without_new_stock_posting(db, stock, prepared, parcel):
    first = parcel.request.model_copy(update={'lines': (parcel.request.lines[0].model_copy(update={'quantity': Decimal('.375')}),)})
    one = execute(db, stock, parcel, submission(db, stock, parcel, first)); db.commit()
    options = query.shipment_options(db, **coordinates(stock, prepared))
    row = options.lines[0]
    assert row.shipped_quantity == '0.375' and row.unshipped_quantity == row.selectable_quantity == row.unassigned_quantity == '0.625'
    assert row.in_transit_quantity == '1.000'
    history = query.shipment_history(db, **coordinates(stock, prepared))
    assert history.shipment_status == 'partially_shipped' and history.items == (one,)
    rest = first.model_copy(update={'tracking_no': 'SYNTHETIC-PARCEL-REST', 'lines': (first.lines[0].model_copy(update={'quantity': Decimal('.625')}),)})
    two = execute(db, stock, parcel, submission(db, stock, parcel, rest)); db.commit()
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    assert query.shipment_options(db, **coordinates(stock, prepared)).lines[0].selectable_quantity == '0.000'
    final = query.shipment_history(db, **coordinates(stock, prepared))
    assert final.shipment_status == 'shipped' and final.items == (one, two)
    assert final.departures == history.departures.model_copy(update={'queried_at': final.departures.queried_at})
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_all_current_departures_shipped_does_not_complete_the_whole_return(db, stock, prepared):
    grant(stock)
    selected = prepared.request.model_copy(update={'lines': (prepared.request.lines[0].model_copy(update={'quantity': Decimal('.375')}),)})
    first, _ = depart(db, stock, prepared, selected)
    line = db.scalar(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id == first.outbound_id))
    request = StockReturnShipmentPreviewIn(operator_person_id=stock.actor.person_id, carrier='Synthetic carrier',
        tracking_no='SYNTHETIC-FIRST-BATCH', shipped_at=datetime.now(timezone.utc), reason='Synthetic first batch parcel',
        lines=[dict(outbound_line_id=line.id, quantity='.375')])
    current = SimpleNamespace(request=request, operation_id=first.operation_id)
    execute(db, stock, current, submission(db, stock, current)); db.commit()
    history = query.shipment_history(db, **coordinates(stock, prepared))
    assert history.shipment_status == 'partially_shipped'
    assert history.departures.outbound_status == 'partially_outbound'
    assert query.shipment_options(db, **coordinates(stock, prepared)).lines[0].selectable_quantity == '0.000'
    rest = selected.model_copy(update={'lines': (selected.lines[0].model_copy(update={'quantity': Decimal('.625')}),)})
    second, _ = depart(db, stock, prepared, rest)
    options = query.shipment_options(db, **coordinates(stock, prepared))
    by_departure = {row.outbound_id: row for row in options.lines}
    assert by_departure[first.outbound_id].selectable_quantity == '0.000'
    assert by_departure[second.outbound_id].selectable_quantity == '0.625'
    assert {row.unassigned_quantity for row in options.lines} == {'0.625'}
    assert query.shipment_history(db, **coordinates(stock, prepared)).shipment_status == 'partially_shipped'


@pytest.mark.parametrize('stock', ['serial'], indirect=True)
def test_assigned_serial_is_removed_from_choices_and_retained_in_history(db, stock, prepared, parcel):
    original = query.shipment_options(db, **coordinates(stock, prepared)).lines[0].serials
    result = execute(db, stock, parcel, submission(db, stock, parcel)); db.commit()
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    row = query.shipment_options(db, **coordinates(stock, prepared)).lines[0]
    assert row.serials == () and row.selectable_quantity == '0.000'
    history = query.shipment_history(db, **coordinates(stock, prepared))
    assert history.shipment_status == 'shipped' and history.items == (result,)
    assert history.items[0].lines[0].selected_serials == original
    assert 'qr_code' not in history.model_dump_json() and snapshot(db) == before


def test_cancellation_stays_separate_and_prevents_choices(db, stock, prepared):
    grant(stock)
    cancelled = cancel_return(db, actor=stock.actor, operation_id=prepared.order.operation_id,
        request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason='Synthetic cancellation before shipping',
            request_id=uuid4().hex, idempotency_key=uuid4().hex)); db.commit()
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    history = query.shipment_history(db, **coordinates(stock, prepared))
    assert history.shipment_status == 'not_shipped' and history.items == ()
    assert history.departures.cancellation == cancelled and history.departures.original.status == 'submitted'
    with pytest.raises(InventoryReadError) as exc: query.shipment_options(db, **coordinates(stock, prepared))
    assert exc.value.code == 'stock_return_already_cancelled' and snapshot(db) == before


def test_read_permission_is_independent_from_new_parcel_permission(db, stock, prepared, parcel):
    result = execute(db, stock, parcel, submission(db, stock, parcel)); db.commit()
    stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements if row.action != 'ship_return'))
    assert query.shipment_history(db, **coordinates(stock, prepared)).items == (result,)
    with pytest.raises(InventoryReadError) as exc: query.shipment_options(db, **coordinates(stock, prepared))
    assert exc.value.code == 'stock_return_forbidden'
    stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements if not (row.resource == 'stock_operation' and row.action == 'read')))
    with pytest.raises(InventoryReadError) as exc: query.shipment_history(db, **coordinates(stock, prepared))
    assert exc.value.code == 'stock_return_forbidden'


def test_wrong_work_order_and_person_cannot_read_parcel_choices_or_history(db, stock, prepared, parcel, client):
    before = snapshot(db)
    wrong = f'/api/v1/work-orders/{stock.orders[1].id}/returns/{prepared.order.operation_id}/shipments'
    for suffix in ('', '/options'):
        response = client.get(wrong + suffix)
        assert response.status_code == 404 and 'no-store' in response.headers['cache-control']
    stock.world.current_principal = replace(stock.actor, person_id=stock.world.headquarters_reviewer_person.id)
    for method in (query.shipment_options, query.shipment_history):
        with pytest.raises(InventoryReadError):
            method(db, **{**coordinates(stock, prepared), 'actor': stock.world.current_principal})
    assert snapshot(db) == before


def test_route_change_during_options_read_rejects_stale_choices(db, stock, prepared, parcel, monkeypatch):
    original = query._options_basis; calls = 0
    def changing(*args):
        nonlocal calls
        result = original(*args); calls += 1
        if calls == 1:
            prepared.transit.status = 'inactive'; db.commit()
        return result
    monkeypatch.setattr(query, '_options_basis', changing)
    with pytest.raises(InventoryReadError): query.shipment_options(db, **coordinates(stock, prepared))
    assert calls == 1


@pytest.mark.parametrize('method', ['shipment_options', 'shipment_history'])
@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_audit_change_during_read_is_not_published(db, stock, prepared, parcel, monkeypatch, method):
    real = query.material_audit_cursor; calls = 0
    def changing(db):
        nonlocal calls
        result = real(db); calls += 1
        return result if calls == 1 else ()
    monkeypatch.setattr(query, 'material_audit_cursor', changing)
    with pytest.raises(InventoryReadError) as exc: getattr(query, method)(db, **coordinates(stock, prepared))
    assert exc.value.code in {'stock_return_shipment_options_changed', 'stock_return_shipment_history_changed'}


@pytest.mark.parametrize('method', ['shipment_options', 'shipment_history'])
@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_permission_revoked_during_read_is_rechecked(db, stock, prepared, parcel, monkeypatch, method):
    real = query.authorize
    action = 'ship_return' if method == 'shipment_options' else 'read'
    def changing(db, actor, requested_action):
        result = real(db, actor, requested_action)
        stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements
            if not (row.resource == 'stock_operation' and row.action == action)))
        return result
    monkeypatch.setattr(query, 'authorize', changing)
    with pytest.raises(InventoryReadError) as exc: getattr(query, method)(db, **coordinates(stock, prepared))
    assert exc.value.code == 'stock_return_forbidden'
