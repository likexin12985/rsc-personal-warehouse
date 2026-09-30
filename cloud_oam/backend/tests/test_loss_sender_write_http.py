"""Synthetic candidate HTTP lifecycle; not a production migration acceptance."""
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.inventory_models import Shipment, Receipt
from app.stock_operation_models import StockOperationCommandSeal
from test_stock_loss_return_shipment import db, world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready, parcel, snapshot
from test_loss_return_sender_http import http_client, private, BASE


@pytest.mark.parametrize('kind', ['outbound_return', 'ship_return'])
def test_actual_candidate_preview_submit_lookup_and_seal_keep_original_request(db, ready, allowed, request, monkeypatch, kind):
    if kind == 'outbound_return':
        actor = ready.derived.actor
        ready.derived.actor = replace(actor, entitlements=actor.entitlements + (
            replace(actor.entitlements[-1], resource='stock_operation', action='read'),))
        actor, order, preview = ready.derived.actor, ready.derived.order, ready.request
        path = 'outbounds'
    else:
        fixture = request.getfixturevalue('parcel')
        actor, order, preview = fixture.actor, fixture.order, fixture.request
        path = 'shipments'
    allowed.world.current_principal = actor
    client = http_client(monkeypatch, db, lambda: allowed.world.current_principal)
    url = BASE+'/'+str(order.id)+'/'+path
    try:
        before = snapshot(db)
        response = client.post(url+'/preview', json=preview.model_dump(mode='json')); private(response)
        assert response.status_code == 200, response.text
        body = {**preview.model_dump(mode='json'), 'expected_plan_hash': response.json()['plan_hash'],
            'request_id': uuid4().hex, 'idempotency_key': uuid4().hex}
        assert snapshot(db) == before
        response = client.post(url+'/requests/lookup', json=body); private(response)
        assert response.status_code == 200 and response.json() == {'lookup_status':'not_observed','retry_allowed':False}, response.text
        assert snapshot(db) == before
        wrong = client.post(url, json=body, headers={'X-Request-ID':'different-request'}); private(wrong)
        assert wrong.status_code == 400 and snapshot(db) == before
        response = client.post(url, json=body, headers={'X-Request-ID':body['request_id'],'Idempotency-Key':body['idempotency_key']})
        private(response); assert response.status_code == 200, response.text
        result = response.json(); after = snapshot(db)
        assert result['origin']['origin_kind'] == 'loss_report'
        assert result['origin']['requester_id'] == str(actor.person_id)
        assert 'work_order_id' not in result and db.scalar(select(Receipt.id)) is None
        if kind == 'outbound_return': assert db.scalar(select(Shipment.id)) is None
        # Simulate a lost response by recovering solely from the persisted
        # original command, without relying on the just-returned identifier.
        response = client.post(url+'/requests/lookup', json=body); private(response)
        assert response.status_code == 200 and response.json()['result'] == result, response.text
        assert snapshot(db) == after
        changed = {**body,'reason':'Changed request content'}
        response = client.post(url+'/requests/lookup', json=changed); private(response)
        assert response.status_code == 409 and snapshot(db) == after
        # Seal a separate never-observed request; do not pretend it was sent.
        missing = {**body,'request_id':uuid4().hex,'idempotency_key':uuid4().hex}
        response = client.post(url+'/requests/seal', json=missing,
            headers={'X-Request-ID':missing['request_id']}); private(response)
        assert response.status_code == 200 and response.json()['lookup_status'] == 'sealed', response.text
        seal = response.json(); sealed_snapshot = snapshot(db)
        assert len(tuple(db.scalars(select(StockOperationCommandSeal.id)))) == 1
        response = client.post(url, json=missing); private(response)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'stock_return_request_sealed', response.text
        assert snapshot(db) == sealed_snapshot
        allowed.world.current_principal = replace(actor, entitlements=tuple(e for e in actor.entitlements
            if e.action not in ('outbound_return','ship_return')))
        response = client.post(url+'/requests/lookup', json=missing); private(response)
        assert response.status_code == 200 and response.json() == seal, response.text
        response = client.post(url+'/requests/lookup', json=body); private(response)
        assert response.status_code == 200 and response.json()['result'] == result, response.text
        response = client.post(url+'/requests/seal', json=missing); private(response)
        assert response.status_code == 403, response.text
        assert snapshot(db) == sealed_snapshot
    finally:
        client.close()
