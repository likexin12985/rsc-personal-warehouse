"""Checked-in H5 fixtures stay bound to the public schemas and command digest."""
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.formal_services.stock_loss_sources import _hash
from app.formal_services.stock_loss_disposition_plan import intent as disposition_intent
from app.formal_services.stock_loss_return_plan import intent as return_intent
from app.stock_loss_schemas import StockLossDispositionExecuteIn
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.stock_loss_execution_source_schemas import ExecutionSourcesOut
from app.stock_loss_execution_schemas import DispositionPreviewOut, DerivedReturnPreviewOut
from app.stock_loss_execution_recovery_schemas import DispositionRecoveryOut, ReturnRecoveryOut

FIXTURES = Path(__file__).resolve().parents[2] / 'frontend/src/test-fixtures/loss-execution'


@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
@pytest.mark.parametrize('kind', ['restore_available', 'convert_used', 'convert_damaged', 'return_to_region'])
def test_browser_execution_contract_matches_current_backend(tracking, kind):
    data = json.loads((FIXTURES / f'{tracking}-{kind}.json').read_text())
    is_return = kind == 'return_to_region'
    command_type = StockLossReturnExecuteIn if is_return else StockLossDispositionExecuteIn
    preview_type = DerivedReturnPreviewOut if is_return else DispositionPreviewOut
    recovery = TypeAdapter(ReturnRecoveryOut if is_return else DispositionRecoveryOut)
    intent = return_intent if is_return else disposition_intent
    for name in ('source', 'after'):
        assert ExecutionSourcesOut.model_validate(data[name]).model_dump(mode='json') == data[name]
    assert preview_type.model_validate(data['preview']).model_dump(mode='json') == data['preview']
    for name in ('missing', 'sealed', 'found'):
        assert recovery.validate_python(data[name]).model_dump(mode='json') == data[name]
        assert data[name]['retry_permitted'] is False
        assert data[name]['result_scope'] == 'original_command'
    for field, result_field in [('original', 'disposition'), ('sealed_command', 'seal')]:
        command = command_type.model_validate(data[field])
        assert command.model_dump(mode='json') == data[field]
        document = dict(intent=intent(command), request_id=command.request_id,
                        expected_plan_hash=command.expected_plan_hash)
        result = data['found' if field == 'original' else 'sealed'][result_field]
        assert _hash(document) == result['request_hash']
        assert result['plan_hash'] == command.expected_plan_hash == data['preview']['plan_hash']
        assert result['headquarters_decision_id'] == str(command.headquarters_decision_id)
        assert result['executor_person_id'] == data['source']['person_id']
    source = data['source']
    decision = source['decisions'][0]
    assert decision['disposition'] == kind and decision['original_posting'] is None
    assert decision['preview_reference']['expected_headquarters_review_hash'] == source['report']['headquarters_review']['request_hash']
    posted = data['found']['disposition']
    assert posted['operation_id'] == source['report']['operation_id']
    assert posted['line_id'] == decision['line_id'] == source['report']['lines'][0]['line_id']
    assert posted['quantity'] == data['preview']['quantity'] == source['report']['lines'][0]['quantity']
    assert bool(data['preview']['serial_ids']) == (tracking == 'serial')
    assert set(data['preview']['serial_ids']) == {s['serial_id'] for s in source['report']['lines'][0]['serials']}
    original = data['after']['decisions'][0]['original_posting']
    assert original['disposition_id'] == posted['disposition_id']
    assert original['result_scope'] == 'original_posting'
    if is_return:
        assert posted['stock_effect'] == 'frozen_to_return_pending' and posted['return_fulfillment_required'] is True
        assert posted['return_operation_id'] == data['preview']['derived_return_operation_id'] == original['return_operation_id']
    else:
        assert original['return_operation_id'] is None
