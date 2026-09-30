"""Seal contracts bind the actual original approval, not a caller's hash alone."""
from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.stock_loss_schemas import StockLossRegionalReviewIn, StockLossHeadquartersReviewIn
from app.stock_loss_review_seal_schemas import StockLossRegionalReviewSealIn, StockLossHeadquartersReviewSealIn
from app.formal_services import stock_loss_sources as sources
from app.formal_services import stock_loss_regional_reviews as regional
from app.formal_services import stock_loss_headquarters_reviews as headquarters


def build(stage):
    command = dict(operation_id=str(UUID(int=1)), expected_submission_plan_hash='a'*64,
        comment='核验备件照片后提交\n保留原请求。', request_id='original-review-request',
        idempotency_key='original-review-key')
    schema, seal, service = StockLossRegionalReviewIn, StockLossRegionalReviewSealIn, regional
    if stage == 'headquarters':
        command.update(regional_review_id=str(UUID(int=2)), expected_regional_review_hash='b'*64,
            decisions=[dict(line_id=str(UUID(int=i)), disposition='return_to_region', reason='送回区域核验')
                for i in (4, 3)])
        schema, seal, service = StockLossHeadquartersReviewIn, StockLossHeadquartersReviewSealIn, headquarters
    parsed = schema.model_validate(command)
    body = dict(stage=stage, operator_person_id=str(UUID(int=5)), original=command,
        request_hash=sources._hash(service.intent(parsed)))
    return stage, seal, service, parsed, body


@pytest.fixture(params=['regional', 'headquarters'])
def original(request):
    return build(request.param)


def test_seal_retains_original_coordinates_and_matches_existing_command_hash(original):
    stage, schema, service, command, body = original
    value = schema.model_validate(body)
    assert value.stage == stage and value.original == command
    assert value.command_intent() == service.intent(command)
    assert value.original.request_id == body['original']['request_id']
    assert value.original.idempotency_key == body['original']['idempotency_key']
    assert 'idempotency_key' not in value.command_intent()
    assert 'request_id' not in value.command_intent()
    assert schema.model_validate_json(value.model_dump_json()) == value
    intent = value.command_intent(); intent['comment'] = 'changed'
    assert value.command_intent()['comment'] == command.comment


@pytest.mark.parametrize('field,value', [
    ('operation_id', str(UUID(int=99))), ('expected_submission_plan_hash', 'c'*64),
    ('comment', '另一份审批意见'),
])
def test_modified_original_content_cannot_keep_old_hash(original, field, value):
    _, schema, _, _, body = original
    body['original'][field] = value
    with pytest.raises(ValidationError, match='原审批请求摘要不一致'):
        schema.model_validate(body)


@pytest.mark.parametrize('fault', ['stage', 'operator', 'hash', 'extra', 'nested_extra', 'empty_comment', 'missing_original'])
def test_wrong_stage_or_incomplete_coordinates_fail_before_persistence(original, fault):
    stage, schema, _, _, body = original
    if fault == 'stage':body['stage'] = 'headquarters' if stage == 'regional' else 'regional'
    elif fault == 'operator':body['operator_person_id'] = 'not-a-person'
    elif fault == 'hash':body['request_hash'] = 'f'*64
    elif fault == 'extra':body['stock_effect'] = 'none'
    elif fault == 'nested_extra':body['original']['approval_stage'] = 'approved'
    elif fault == 'empty_comment':body['original']['comment'] = ' '
    else:del body['original']
    with pytest.raises(ValidationError):schema.model_validate(body)


@pytest.mark.parametrize('fault', ['regional_id', 'regional_hash', 'decision', 'line', 'reason', 'duplicate', 'empty'])
def test_headquarters_seal_binds_exact_regional_proof_and_all_line_decisions(fault):
    _, schema, _, _, body = build('headquarters')
    command = body['original']
    if fault == 'regional_id':command['regional_review_id'] = str(UUID(int=99))
    elif fault == 'regional_hash':command['expected_regional_review_hash'] = 'd'*64
    elif fault == 'decision':command['decisions'][0]['disposition'] = 'scrap'
    elif fault == 'line':command['decisions'][0]['line_id'] = str(UUID(int=99))
    elif fault == 'reason':command['decisions'][0]['reason'] = '其他原因'
    elif fault == 'duplicate':command['decisions'].append(deepcopy(command['decisions'][0]))
    else:command['decisions'] = []
    with pytest.raises(ValidationError):schema.model_validate(body)


def test_stage_contracts_cannot_silently_drop_headquarters_decisions(original):
    stage, _, _, _, body = original
    if stage == 'headquarters':
        body['stage'] = 'regional'
        with pytest.raises(ValidationError):StockLossRegionalReviewSealIn.model_validate(body)
    else:
        body['stage'] = 'headquarters'
        with pytest.raises(ValidationError):StockLossHeadquartersReviewSealIn.model_validate(body)


@pytest.mark.parametrize('comment', ['\u3000leading', 'trailing\u00a0', '\vleading'])
def test_original_comment_rejects_noncanonical_unicode_whitespace(original, comment):
    _, schema, _, _, body = original
    body['original']['comment'] = comment
    if 'decisions' in body['original']:
        body['original']['decisions'].sort(key=lambda item:item['line_id'])
    body['request_hash'] = sources._hash({k:v for k,v in body['original'].items()
        if k not in ('request_id', 'idempotency_key')})
    with pytest.raises(ValidationError):schema.model_validate(body)


@pytest.mark.parametrize('reason', ['before\rafter', '\u3000leading', 'trailing\u00a0'])
def test_original_headquarters_reason_rejects_controls_and_unicode_whitespace(reason):
    _, schema, _, _, body = build('headquarters')
    body['original']['decisions'][0]['reason'] = reason
    body['original']['decisions'].sort(key=lambda item:item['line_id'])
    body['request_hash'] = sources._hash({k:v for k,v in body['original'].items()
        if k not in ('request_id', 'idempotency_key')})
    with pytest.raises(ValidationError):schema.model_validate(body)


def test_original_headquarters_reason_keeps_valid_multiline_unicode_text():
    _, schema, service, _, body = build('headquarters')
    body['original']['decisions'][0]['reason'] = '照片核对\n型号\t一致，退回区域。'
    command = StockLossHeadquartersReviewIn.model_validate(body['original'])
    body['request_hash'] = sources._hash(service.intent(command))
    assert schema.model_validate(body).original == command
