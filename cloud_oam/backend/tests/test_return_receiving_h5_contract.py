"""H5 receiving fixtures are synthetic service outputs, bound to current schemas."""
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.formal_services.stock_return_receipt_plan import intent
from app.formal_services.stock_return_inbound_commands import _request_hash
from app.formal_services.work_order_return_sources import _hash
from app.stock_return_receiving_schemas import StockReturnReceivingOut
from app.stock_return_receipt_schemas import (
    StockReturnReceiptHistoryOut, StockReturnReceiptSubmitIn,
    StockReturnReceiptOut, LossReturnReceiptOut, StockReturnReceiptPreviewOut, LossReturnReceiptPreviewOut,
)
from app.stock_return_inbound_schemas import (
    StockReturnInboundStateOut, StockReturnInboundPreviewOut, LossReturnInboundPreviewOut,
    StockReturnInboundOut, StockReturnInboundSubmitIn,
)

FIXTURES = Path(__file__).resolve().parents[2] / 'frontend/src/test-fixtures/return-receiving'


@pytest.mark.parametrize('origin', ['loss', 'work-order'])
@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
def test_h5_return_fixtures_match_current_backend_and_original_hashes(origin, tracking):
    data = json.loads((FIXTURES / f'{origin}-receiving-{tracking}.json').read_text())
    for key, schema in [('directory', StockReturnReceivingOut),
            ('before', StockReturnReceiptHistoryOut), ('after', StockReturnReceiptHistoryOut),
            ('preview', LossReturnReceiptPreviewOut if origin == 'loss' else StockReturnReceiptPreviewOut),
            ('receipt', LossReturnReceiptOut if origin == 'loss' else StockReturnReceiptOut)]:
        assert schema.model_validate(data[key]).model_dump(mode='json') == data[key]
    command = StockReturnReceiptSubmitIn.model_validate(data['command'])
    assert command.model_dump(mode='json') == data['command']
    receipt = data['receipt']
    assert _hash(intent(UUID(receipt['shipment_id']), command)) == receipt['request_hash']
    assert command.expected_plan_hash == receipt['plan_hash'] == data['preview']['plan_hash']
    assert receipt['request_hash'] == data['preview']['request_hash']
    assert str(command.operator_person_id) == receipt['operator_person_id'] == data['identity']['person_id']
    assert data['before']['receipts'] == [] and data['after']['receipts'] == [receipt]
    assert bool(data['after']['package']['lines'][0]['serials']) == (tracking == 'serial')
    v = data['inbound']
    for key, schema in [('before', StockReturnInboundStateOut), ('after', StockReturnInboundStateOut),
            ('preview', LossReturnInboundPreviewOut if origin == 'loss' else StockReturnInboundPreviewOut),
            ('posted', StockReturnInboundOut), ('command', StockReturnInboundSubmitIn)]:
        assert schema.model_validate(v[key]).model_dump(mode='json') == v[key]
    assert v['before']['status'] == 'not_posted' and v['before']['inbound'] is None
    assert v['after']['status'] == 'posted'
    assert v['posted']['posting_transaction_id'] == v['after']['inbound']['posting_transaction_id']
    assert v['preview']['receipt_plan_hash'] == receipt['plan_hash']
    assert v['command']['expected_plan_hash'] == v['preview']['plan_hash'] == v['posted']['plan_hash']
    assert v['posted']['request_hash'] == _request_hash(receipt_id=UUID(receipt['receipt_id']),
        request_id=v['command']['request_id'], plan_hash=v['command']['expected_plan_hash'])
