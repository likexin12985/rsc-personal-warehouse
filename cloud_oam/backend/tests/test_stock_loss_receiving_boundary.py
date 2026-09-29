"""Loss recipient projection exposes only the verified public origin."""
from sqlalchemy import text
from app.formal_services import stock_return_receiving as receiving
from app.formal_services.inventory_query import InventoryReadError
import pytest
from test_stock_loss_return_shipment import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    route, derived, ready, parcel, execute, snapshot,
)
from test_stock_return_receiving import receiver, client


def test_loss_parcel_has_exact_private_recipient_projection(db, stock, parcel, client):
    shipment, _ = execute(db, parcel)
    actor = receiver(stock)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    response = client.get('/api/v1/stock-returns/my-receiving')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['person_id'] == str(actor.person_id)
    assert len(value['items']) == 1
    package = value['items'][0]
    assert package['verification_status'] == 'verified'
    assert package['shipment_id'] == str(shipment.shipment_id)
    assert package['origin'] == {key: shipment.origin.model_dump(mode='json')[key] for key in
        ('origin_kind', 'loss_operation_id', 'loss_line_id', 'headquarters_decision_id', 'disposition_id')}
    assert package['lines'][0]['condition_code'] == 'new'
    detail = client.get('/api/v1/stock-returns/my-receiving/' + str(shipment.shipment_id))
    assert detail.status_code == 200, detail.text
    assert detail.json()['package'] == package
    assert all('no-store' in row.headers['cache-control'] for row in (response, detail))
    for private in ('request_hash', 'plan_hash', 'source_loss_line_id', 'work_order_id', 'qr_code', 'posting_transaction_id', 'submitted_by_user_id'):
        assert private not in response.text and private not in detail.text
    assert snapshot(db) == before
