from uuid import uuid4
import pytest
from pydantic import ValidationError

from app.stock_loss_disposition_seal_schemas import (
    StockLossDispositionSealIn, StockLossDerivedReturnSealIn, command_document,
)


def original(flow):
    value=dict(headquarters_decision_id=str(uuid4()), expected_headquarters_review_hash='a'*64,
        expected_submission_plan_hash='b'*64, expected_plan_hash='c'*64,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    if flow=='return':value.update(target_location_id=str(uuid4()), transit_location_id=str(uuid4()))
    return value


@pytest.mark.parametrize('flow',['disposition','return'])
def test_original_coordinates_and_namespaced_hashes_are_preserved(flow):
    raw=original(flow)
    schema=StockLossDispositionSealIn if flow=='disposition' else StockLossDerivedReturnSealIn
    request=schema(operator_person_id=uuid4(),original=raw)
    first=request.proof()
    assert first['command'].model_dump(mode='json')==raw
    assert first['disposition_key_hash']!=first['return_key_hash']
    assert first['command_jsonb']['request_id']==raw['request_id']
    assert 'idempotency_key' not in str(first['command_jsonb'])
    assert command_document(request.original,flow)==first
    for field in type(request.original).model_fields:
        changed=uuid4() if field.endswith('_id') and field!='request_id' else (
            uuid4().hex if field in ('request_id','idempotency_key') else 'f'*64)
        second=command_document(request.original.model_copy(update={field:changed}),flow)
        if field=='idempotency_key':
            assert second['request_hash']==first['request_hash']
            assert second['disposition_key_hash']!=first['disposition_key_hash']
            assert second['return_key_hash']!=first['return_key_hash']
        else:
            assert second['request_hash']!=first['request_hash']
        if field=='request_id':
            assert second['request_reference']!=first['request_reference']
        else:
            assert second['request_reference']==first['request_reference']


@pytest.mark.parametrize('flow',['disposition','return'])
@pytest.mark.parametrize('field',['request_id','idempotency_key','request_hash','plan_hash','quantity','status','flow'])
def test_envelope_cannot_override_original_command(flow,field):
    schema=StockLossDispositionSealIn if flow=='disposition' else StockLossDerivedReturnSealIn
    with pytest.raises(ValidationError):
        schema.model_validate(dict(operator_person_id=uuid4(),original=original(flow),**{field:'invented'}))


def test_flow_schemas_are_not_interchangeable():
    with pytest.raises(ValidationError):
        StockLossDispositionSealIn(operator_person_id=uuid4(),original=original('return'))
    with pytest.raises(ValidationError):
        StockLossDerivedReturnSealIn(operator_person_id=uuid4(),original=original('disposition'))


@pytest.mark.parametrize('flow',['disposition','return'])
@pytest.mark.parametrize('field',['quantity','source_account_id','disposition','serial_ids','operator_person_id'])
def test_original_command_cannot_gain_client_inventory_dimensions(flow,field):
    schema=StockLossDispositionSealIn if flow=='disposition' else StockLossDerivedReturnSealIn
    with pytest.raises(ValidationError):
        schema(operator_person_id=uuid4(),original={**original(flow),field:'invented'})
