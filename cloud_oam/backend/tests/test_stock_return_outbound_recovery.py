"""Exercise the same uncertain-result protocol for physical departures."""
from uuid import uuid4
import pytest
from app.stock_return_outbound_schemas import StockReturnOutboundSubmitIn
from app.formal_services.stock_return_recovery import lookup_return_request, seal_return_request
from app.formal_services.stock_return_commands import cancel_return
from app.formal_services.inventory_query import InventoryReadError
from app.stock_return_schemas import StockReturnCancelIn
from test_stock_return_outbound import db, world, stock, recovered, destination, prepared, preview, snapshot, commands, plan
from test_stock_return_recovery import (
    test_missing_request_can_be_sealed_once_and_late_keys_cannot_post,
    test_lost_success_response_reads_original_and_cannot_be_sealed,
    test_current_read_and_seal_permissions_are_independent,
    test_orphaned_request_evidence_is_never_treated_as_absence,
)
from test_formal_stock_return_routes import (
    client, path,
    test_committed_response_lost_recovers_by_original_get_without_replaying,
    test_absent_http_request_seal_blocks_late_post_and_changed_header,
)


@pytest.fixture
def command(db, stock, prepared):
    checked = preview(db, stock, prepared)
    value = StockReturnOutboundSubmitIn(**prepared.request.model_dump(), expected_plan_hash=checked.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    coordinates = dict(actor=stock.actor, work_order_id=stock.orders[0].id, operation_id=prepared.order.operation_id,
        operation_type='outbound_return', request_id=value.request_id)
    def execute(item=value):
        return commands.execute_outbound(db, actor=stock.actor, work_order_id=stock.orders[0].id,
            operation_id=prepared.order.operation_id, request=item)
    return value, coordinates, checked.request_hash, execute


def test_outbound_http_preview_and_coordinates_are_private(db, stock, command, client):
    value, coordinates, digest, _ = command
    url = path(coordinates)
    before = snapshot(db)
    body = value.model_dump(mode='json', exclude={'expected_plan_hash','request_id','idempotency_key'})
    response = client.post(url+'/preview', json=body)
    assert response.status_code==200 and 'no-store' in response.headers['cache-control']
    assert response.json()['request_hash']==digest and response.json()['plan_hash']==value.expected_plan_hash
    assert snapshot(db)==before and 'qr_code' not in response.text
    wrong = path({**coordinates,'work_order_id':stock.orders[1].id})
    assert client.post(wrong,json=value.model_dump(mode='json')).status_code==404
    assert snapshot(db)==before


def test_outbound_seal_owns_actor_request_namespace_across_return_kinds(db, stock, command):
    value, coordinates, digest, execute = command
    seal_return_request(db, **coordinates, request_hash=digest); db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as exc:
        lookup_return_request(db, **{**coordinates, 'operation_type':'cancel_return'})
    assert exc.value.code=='stock_return_request_conflict'
    with pytest.raises(InventoryReadError) as exc:
        cancel_return(db, actor=stock.actor, operation_id=coordinates['operation_id'],
            request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason='same request cannot switch operation',
                request_id=value.request_id, idempotency_key=uuid4().hex))
    assert exc.value.code=='stock_return_request_sealed'
    assert snapshot(db)==before
