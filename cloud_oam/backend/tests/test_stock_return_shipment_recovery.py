"""Parcel HTTP coordinates use the existing no-replay recovery protocol."""
import pytest
from test_stock_return_shipment import db, world, stock, recovered, destination, prepared, parcel, submission, execute, preview, snapshot
from test_formal_stock_return_routes import (
    client, path,
    test_committed_response_lost_recovers_by_original_get_without_replaying,
    test_absent_http_request_seal_blocks_late_post_and_changed_header,
    test_http_command_rejects_changed_original_coordinates_before_writing,
)


@pytest.fixture
def command(db, stock, parcel):
    value=submission(db,stock,parcel)
    coordinates=dict(actor=stock.actor,work_order_id=stock.orders[0].id,operation_id=parcel.operation_id,
        operation_type='ship_return',request_id=value.request_id)
    return value,coordinates,preview(db,stock,parcel).request_hash,lambda:execute(db,stock,parcel,value)


def test_parcel_preview_and_foreign_work_order_are_private_and_stock_neutral(db, stock, command, client):
    value,coordinates,digest,_=command;url=path(coordinates);before=snapshot(db)
    response=client.post(url+'/preview',json=value.model_dump(mode='json',exclude={'expected_plan_hash','request_id','idempotency_key'}))
    assert response.status_code==200 and 'no-store' in response.headers['cache-control']
    assert response.json()['request_hash']==digest and response.json()['plan_hash']==value.expected_plan_hash
    assert snapshot(db)==before
    wrong=path({**coordinates,'work_order_id':stock.orders[1].id})
    assert client.post(wrong,json=value.model_dump(mode='json')).status_code==404 and snapshot(db)==before
