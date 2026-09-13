"""Receiver visibility is independent of sender login and never releases stock."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from app.database import get_db
from app.inventory_models import CustodyAssignment, Shipment
from app.stock_operation_models import StockOperationShipment
from app.formal_services import stock_return_receiving as receiving
from app.formal_services import stock_return_shipment_facts as facts
from app.formal_services import inventory_posting as posting
from app.formal_services.inventory_query import InventoryReadError
from app.routers import formal_stock_return_receiving as router
from test_stock_return_shipment import db, world, stock, recovered, destination, prepared, parcel, submission, execute, snapshot


def receiver(stock, *, scope_type="national", scope_id="*"):
    actor = stock.actor
    result = replace(actor, user_id=stock.world.headquarters_reviewer_user.id,
        person_id=stock.world.headquarters_reviewer_person.id,
        assignments=tuple(replace(row, role_code="admin" if scope_type=="national" else "provincial_manager",
            scope_type=scope_type, scope_id=scope_id) for row in actor.assignments),
        entitlements=tuple(replace(actor.entitlements[0], resource=resource, action="read",
            scope_type=scope_type, scope_id=scope_id) for resource in ("stock_operation", "inventory")))
    stock.world.current_principal = result
    return result


@pytest.fixture
def incoming(db, stock, parcel):
    result = execute(db, stock, parcel, submission(db, stock, parcel)); db.commit()
    return result, receiver(stock)


@pytest.fixture
def client(db, stock):
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: stock.world.current_principal
    with TestClient(app) as value: yield value


def test_receiver_gets_exact_parcel_without_sender_login_stock_or_private_fields(db, stock, incoming, client, monkeypatch):
    result, actor = incoming
    original = posting.load_formal_principal
    def current_receiver(db, user_id):
        assert user_id == actor.user_id, 'a historical sender is not an authorized reader'
        return original(db, user_id)
    monkeypatch.setattr(posting, 'load_formal_principal', current_receiver)
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    response = client.get('/api/v1/stock-returns/my-receiving')
    assert response.status_code == 200, response.text
    value = response.json(); assert value['person_id'] == str(actor.person_id)
    assert len(value['items']) == 1 and value['items'][0]['verification_status'] == 'verified'
    package = value['items'][0]
    assert package['shipment_id'] == str(result.shipment_id) and package['operation_id'] == str(result.operation_id)
    assert package['sender_person_id'] == str(stock.actor.person_id) and package['receiver_person_id'] == str(actor.person_id)
    assert package['lines'][0]['shipped_quantity'] == '1.000'
    assert len(package['lines'][0]['serials']) == (1 if stock.tracked else 0)
    detail = client.get('/api/v1/stock-returns/my-receiving/' + str(result.shipment_id))
    assert detail.status_code == 200 and detail.json()['package'] == package
    for private in ('stock_account_id', 'ledger_cursor_before', 'request_hash', 'plan_hash', 'request_id', 'qr_code', 'source_recovery_line_id', 'in_transit_quantity'):
        assert private not in response.text and private not in detail.text
    assert all('no-store' in row.headers['cache-control'] for row in (response, detail))
    assert 'receipt_id' not in response.text and 'posting_transaction_id' not in response.text
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('change', ['sender', 'other_receiver', 'out_of_region', 'no_inventory_read', 'no_return_read'])
def test_sender_other_receiver_and_missing_scope_never_see_the_package(db, stock, incoming, change):
    result, actor = incoming
    if change == 'sender': actor = stock.actor
    elif change == 'other_receiver': actor = replace(actor, person_id=stock.actor.person_id)
    elif change == 'out_of_region':
        actor = receiver(stock, scope_type='organization', scope_id=str(stock.world.headquarters_organization.id))
        # This exact unrelated scope must not cover the target region.
        actor = replace(actor, entitlements=tuple(replace(row, scope_id=str(uuid4())) for row in actor.entitlements))
    else: actor = replace(actor, entitlements=tuple(row for row in actor.entitlements
        if row.resource != ('inventory' if change == 'no_inventory_read' else 'stock_operation')))
    stock.world.current_principal = actor; before = snapshot(db)
    try:
        assert receiving.list_my_return_receiving(db, actor=actor).items == ()
    except (InventoryReadError, posting.InventoryPostingError) as error:
        assert error.code in {'stock_return_receiving_forbidden', 'actor_principal_stale'}
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)):
        receiving.my_return_receiving_detail(db, actor=actor, shipment_id=result.shipment_id)
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_changed_custody_blocks_exact_package_without_exposing_other_details(db, stock, incoming):
    result, actor = incoming
    fact = db.get(StockOperationShipment, result.shipment_id)
    original = db.get(CustodyAssignment, fact.target_custody_assignment_id)
    original.valid_to = datetime.now(timezone.utc) - timedelta(microseconds=1)
    db.add(CustodyAssignment(location_id=original.location_id, custodian_person_id=actor.person_id,
        valid_from=datetime.now(timezone.utc))); db.commit()
    before = snapshot(db)
    item = receiving.list_my_return_receiving(db, actor=actor).items[0]
    assert set(item.model_dump()) == {'verification_status', 'shipment_id', 'code', 'message'}
    assert item.verification_status == 'unavailable'
    with pytest.raises(InventoryReadError) as error:
        receiving.my_return_receiving_detail(db, actor=actor, shipment_id=result.shipment_id)
    assert error.value.code == 'stock_return_receiving_custody_changed' and snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_partial_parcels_page_independently_and_one_invalid_fact_does_not_hide_another(db, stock, parcel):
    value = parcel.request.model_copy(update={'lines': (parcel.request.lines[0].model_copy(update={'quantity': Decimal('.5')}),)})
    a = execute(db, stock, parcel, submission(db, stock, parcel, value)); db.commit()
    b = execute(db, stock, parcel, submission(db, stock, parcel, value.model_copy(update={'tracking_no': 'SYNTHETIC-SECOND'}))); db.commit()
    actor = receiver(stock)
    first = receiving.list_my_return_receiving(db, actor=actor, limit=1)
    assert len(first.items) == 1 and first.next_after_id == first.items[0].shipment_id
    second = receiving.list_my_return_receiving(db, actor=actor, limit=1, after_id=first.next_after_id)
    assert len(second.items) == 1 and second.next_after_id is None
    assert {first.items[0].shipment_id, second.items[0].shipment_id} == {a.shipment_id, b.shipment_id}
    db.get(Shipment, a.shipment_id).tracking_no = 'SYNTHETIC-TAMPER'; db.commit()
    before = snapshot(db); listing = receiving.list_my_return_receiving(db, actor=actor)
    assert {row.shipment_id: row.verification_status for row in listing.items} == {a.shipment_id: 'unavailable', b.shipment_id: 'verified'}
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
@pytest.mark.parametrize('change', ['permission', 'custodian', 'assignment', 'audit'])
def test_read_rechecks_live_authority_location_and_audit(db, stock, incoming, destination, monkeypatch, change):
    result, actor = incoming
    original = receiving._package
    def changing(*args):
        value = original(*args)
        if change == 'permission': stock.world.current_principal = replace(actor, entitlements=())
        elif change == 'custodian': destination[0].custodian_person_id = stock.actor.person_id; db.commit()
        elif change == 'assignment':
            fact = db.get(StockOperationShipment, result.shipment_id)
            db.get(CustodyAssignment, fact.target_custody_assignment_id).valid_to = datetime.now(timezone.utc)
            db.commit()
        else: monkeypatch.setattr(receiving, 'material_audit_cursor', lambda _db: ())
        return value
    monkeypatch.setattr(receiving, '_package', changing)
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)):
        receiving.list_my_return_receiving(db, actor=actor)


def test_internal_history_proof_has_no_authority_and_cannot_open_sender_recovery(db, stock, incoming):
    result, actor = incoming; before = snapshot(db)
    fact = db.get(StockOperationShipment, result.shipment_id)
    assert facts.verified_shipment_history(db, fact=fact) == result
    with pytest.raises(InventoryReadError) as error: facts.shipment_result(db, actor=actor, fact=fact)
    assert error.value.code == 'stock_return_shipment_not_found'
    assert snapshot(db) == before


@pytest.mark.parametrize('stock', ['quantity'], indirect=True)
def test_unproven_original_posting_is_isolated_and_cannot_be_read_as_verified(db, stock, incoming, monkeypatch):
    result, actor = incoming; before = snapshot(db)
    def unproven(*_args, **_kwargs):
        posting._fail('synthetic_original_posting_invalid', 'service_unavailable', 'Synthetic invalid original posting')
    monkeypatch.setattr(facts, 'verified_shipment_history', unproven)
    item = receiving.list_my_return_receiving(db, actor=actor).items[0]
    assert item.shipment_id == result.shipment_id and item.verification_status == 'unavailable'
    with pytest.raises(posting.InventoryPostingError):
        receiving.my_return_receiving_detail(db, actor=actor, shipment_id=result.shipment_id)
    assert snapshot(db) == before


@pytest.mark.parametrize('failure', ['auth', 'path', 'query', 'method', 'route'])
def test_receiving_production_framework_failures_are_private(monkeypatch, failure):
    from app.main import app
    from fastapi import HTTPException
    overrides = {get_db: lambda: None}
    def principal():
        if failure == 'auth': raise HTTPException(status_code=401, detail='需要登录')
        return None
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == 'principal': overrides[dependency.call] = principal
    monkeypatch.setattr(app, 'dependency_overrides', overrides)
    path = '/api/v1/stock-returns/my-receiving'
    if failure == 'path': path += '/invalid'
    elif failure == 'query': path += '?limit=99'
    elif failure == 'route': path += '/unknown/route'
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.request('POST' if failure == 'method' else 'GET', path)
    finally:
        client.close()
    assert response.status_code == {'auth': 401, 'path': 422, 'query': 422, 'method': 405, 'route': 404}[failure]
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy'] == 'no-referrer'
