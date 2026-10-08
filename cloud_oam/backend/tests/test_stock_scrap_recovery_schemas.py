from copy import deepcopy
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.stock_scrap_recovery_schemas import (
    ScrapRecoveryApply, ScrapRecoveryRegionalReview,
    ScrapRecoveryHeadquartersReview, ScrapRecoveryPreview,
    ScrapRecoveryExecute, ScrapRecoveryRequestLookup, ScrapRecoveryRequestSeal,
    validated_recovery_request,
)


def commands():
    source = dict(scrap_line_id=str(UUID(int=1)), expected_scrap_request_hash='a' * 64)
    coords = dict(request_id='original-request-001', idempotency_key='original-key-001')
    binding = dict(source=source, recovery_request_id=str(UUID(int=2)), expected_request_hash='b' * 64)
    return (
        (ScrapRecoveryApply, dict(source=source, **coords, action='apply_scrap_recovery',
            reason='找回原已报废实物', evidence_file_ids=[str(UUID(int=3))])),
        (ScrapRecoveryRegionalReview, dict(**binding, **coords, action='review_scrap_recovery_region',
            decision='verified', reason='区域独立核实实物与原报废一致')),
        (ScrapRecoveryHeadquartersReview, dict(**binding, **coords, action='review_scrap_recovery_headquarters',
            regional_review_id=str(UUID(int=4)), expected_regional_hash='c' * 64,
            decision='approve', reason='总部批准恢复原冻结库存')),
        (ScrapRecoveryExecute, dict(**binding, **coords, action='execute_scrap_recovery',
            headquarters_review_id=str(UUID(int=5)), expected_headquarters_hash='d' * 64,
            expected_plan_hash='e' * 64, reason='依批准办理准确反向')),
    )


@pytest.mark.parametrize('schema,body', commands())
def test_each_stage_retains_exact_command_across_result_lookup_and_seal(schema, body):
    command = schema.model_validate(body)
    for wrapper in (ScrapRecoveryRequestLookup, ScrapRecoveryRequestSeal):
        request = wrapper(operator_person_id=UUID(int=6), original=command)
        roundtrip = validated_recovery_request(wrapper.model_validate_json(request.model_dump_json()))
        assert type(roundtrip.original) is schema
        assert roundtrip.original.model_dump(mode='json') == command.model_dump(mode='json')
        assert roundtrip.original.idempotency_key == body['idempotency_key']
        with pytest.raises(ValidationError):
            roundtrip.original.request_id = 'silently-replaced-request'


@pytest.mark.parametrize('field,value', [
    ('quantity', '2.000'), ('serial_ids', [str(UUID(int=7))]),
    ('target_account_id', str(UUID(int=8))), ('availability_bucket', 'available'),
    ('actor_user_id', 'borrowed'), ('authorization_version', 123), ('approved', True),
])
def test_no_stage_accepts_client_stock_destination_or_authority(field, value):
    for schema, body in commands():
        with pytest.raises(ValidationError):
            schema.model_validate(body | {field: value})


@pytest.mark.parametrize('schema,body', commands())
def test_nested_source_model_copy_cannot_bypass_service_revalidation(schema, body):
    command = schema.model_validate(body)
    forged = command.model_copy(update={'source': command.source.model_copy(update={'scrap_line_id': UUID(int=0)})})
    with pytest.raises(ValidationError):
        validated_recovery_request(forged)
    with pytest.raises(ValidationError):
        validated_recovery_request(ScrapRecoveryRequestLookup.model_construct(operator_person_id=UUID(int=6), original=forged))


@pytest.mark.parametrize('schema,body', commands())
def test_lookup_cannot_relabel_one_stage_as_another(schema, body):
    for _, other in commands():
        if other['action'] == body['action']:
            continue
        forged = deepcopy(body) | {'action': other['action']}
        with pytest.raises(ValidationError):
            ScrapRecoveryRequestLookup.model_validate(dict(operator_person_id=str(UUID(int=6)), original=forged))


@pytest.mark.parametrize('field', ['headquarters_review_id', 'expected_headquarters_hash',
    'recovery_request_id', 'expected_request_hash', 'expected_plan_hash'])
def test_execution_requires_specific_approval_source_and_server_plan(field):
    schema, body = commands()[-1]
    body.pop(field)
    with pytest.raises(ValidationError):
        schema.model_validate(body)


@pytest.mark.parametrize('evidence', [[], [str(UUID(int=0))], [str(UUID(int=3))] * 2,
    [str(UUID(int=i)) for i in range(1, 22)]])
def test_finding_requires_bounded_distinct_real_evidence_identifiers(evidence):
    schema, body = commands()[0]
    with pytest.raises(ValidationError):
        schema.model_validate(body | {'evidence_file_ids': evidence})


def test_preview_is_never_a_recoverable_original_write():
    _, body = commands()[-1]
    preview = ScrapRecoveryPreview.model_validate({key: value for key, value in body.items()
        if key not in ('action', 'request_id', 'idempotency_key', 'expected_plan_hash')})
    assert validated_recovery_request(preview) == preview
    with pytest.raises(ValidationError):
        ScrapRecoveryRequestLookup(operator_person_id=UUID(int=6), original=preview)
    with pytest.raises(ValueError):
        validated_recovery_request(body)
