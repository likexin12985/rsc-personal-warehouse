"""Loss parcels must not break the existing recipient list before acceptance support."""
from sqlalchemy import text
from app.formal_services import stock_return_receiving as receiving
from app.formal_services.inventory_query import InventoryReadError
import pytest
from test_stock_loss_return_shipment import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, execute, snapshot,
)
from test_stock_return_receiving import receiver, client


def test_loss_parcel_is_private_unavailable_not_a_failed_recipient_list(db, stock, parcel, client):
    shipment, _ = execute(db, parcel)
    actor = receiver(stock)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    response = client.get('/api/v1/stock-returns/my-receiving')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['person_id'] == str(actor.person_id)
    assert value['items'] == [{
        'verification_status': 'unavailable',
        'shipment_id': str(shipment.shipment_id),
        'code': 'stock_return_receiving_verification_required',
        'message': '此包裹的原单据或接收责任未通过核验，请保留原记录处理。',
    }]
    assert 'no-store' in response.headers['cache-control']
    detail = client.get('/api/v1/stock-returns/my-receiving/' + str(shipment.shipment_id))
    assert detail.status_code == 409
    assert detail.json()['detail']['code'] == 'stock_return_receiving_verification_required'
    assert 'no-store' in detail.headers['cache-control']
    for private in ('request_hash', 'plan_hash', 'source_loss_line_id', 'work_order_id', 'qr_code', 'posting_transaction_id'):
        assert private not in response.text and private not in detail.text
    assert snapshot(db) == before
