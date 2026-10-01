from copy import deepcopy
from uuid import UUID

from pydantic import ValidationError
import pytest

from app.stock_scrap_schemas import (
    OriginalScrapSource, CorrectedScrapSource, ScrapPreview, ScrapExecute,
    ScrapRequestLookup, ScrapRequestSeal, validated_scrap_request,
)


def _command(kind='original'):
    source = dict(kind=kind, expected_submission_plan_hash='a'*64)
    if kind == 'original':
        source.update(headquarters_decision_id=str(UUID(int=1)), expected_headquarters_review_hash='b'*64)
    else:
        source.update(root_disposition_id=str(UUID(int=2)), expected_root_request_hash='b'*64,
            reversal_id=str(UUID(int=3)), expected_reversal_hash='c'*64,
            correction_decision_id=str(UUID(int=4)), expected_correction_decision_hash='d'*64)
    return dict(source=source, execution_reason='按已批准的准确明细办理报废',
        evidence_file_ids=[str(UUID(int=5))], request_id='scrap-request-01',
        idempotency_key='scrap-command-01', expected_plan_hash='e'*64)


@pytest.mark.parametrize('kind,origin_type', [('original', OriginalScrapSource), ('correction', CorrectedScrapSource)])
def test_both_approval_origins_keep_complete_coordinates_through_lookup_and_seal(kind, origin_type):
    command = ScrapExecute.model_validate(_command(kind))
    assert type(command.source) is origin_type
    for schema in (ScrapRequestLookup, ScrapRequestSeal):
        request = schema(operator_person_id=UUID(int=6), original=command)
        recovered = validated_scrap_request(schema.model_validate_json(request.model_dump_json()))
        assert recovered.original == command
        assert recovered.original.model_dump(mode='json') == command.model_dump(mode='json')
        assert recovered.operator_person_id == UUID(int=6)


@pytest.mark.parametrize('field,value', [
    ('quantity', '3.000'), ('serial_ids', [str(UUID(int=9))]),
    ('source_account_id', str(UUID(int=8))), ('target_account_id', str(UUID(int=9))),
    ('disposition', 'restore_available'), ('approved', True), ('actor_user_id', 'borrowed-user'),
])
def test_client_cannot_replace_approved_stock_or_claim_authority(field, value):
    command = _command(); command[field] = value
    with pytest.raises(ValidationError):
        ScrapExecute.model_validate(command)


@pytest.mark.parametrize('mutation', ['missing_kind', 'changed_kind', 'extra_origin', 'missing_reversal', 'missing_approval_hash'])
def test_ambiguous_or_incomplete_correction_origin_is_rejected(mutation):
    command = _command('correction'); source = command['source']
    if mutation == 'missing_kind': source.pop('kind')
    elif mutation == 'changed_kind': source['kind'] = 'original'
    elif mutation == 'extra_origin': source['headquarters_decision_id'] = str(UUID(int=7))
    elif mutation == 'missing_reversal': source.pop('reversal_id')
    else: source.pop('expected_correction_decision_hash')
    with pytest.raises(ValidationError):
        ScrapExecute.model_validate(command)


@pytest.mark.parametrize('evidence', [[], [str(UUID(int=0))], [str(UUID(int=5))]*2,
    [str(UUID(int=value)) for value in range(1, 22)]])
def test_evidence_must_be_nonempty_nonzero_unique_and_bounded(evidence):
    command = _command(); command['evidence_file_ids'] = evidence
    with pytest.raises(ValidationError):
        ScrapExecute.model_validate(command)


@pytest.mark.parametrize('field,value', [('request_id', 'a\nrequest'), ('idempotency_key', 'two keys'),
    ('expected_plan_hash', 'E'*64), ('execution_reason', '\x00')])
def test_request_binding_has_no_ambiguous_coordinates(field, value):
    command = _command(); command[field] = value
    with pytest.raises(ValidationError):
        ScrapExecute.model_validate(command)


def test_bypassed_nested_model_validation_is_rechecked_before_any_service_action():
    command = ScrapExecute.model_validate(_command('correction'))
    forged_origin = command.source.model_copy(update={'reversal_id': UUID(int=0)})
    forged = command.model_copy(update={'source': forged_origin})
    with pytest.raises(ValidationError):
        validated_scrap_request(forged)
    wrapper = ScrapRequestLookup.model_construct(operator_person_id=UUID(int=6), original=forged)
    with pytest.raises(ValidationError):
        validated_scrap_request(wrapper)


def test_execution_and_recovery_never_accept_preview_as_an_original_write():
    original = _command()
    preview = {key:value for key,value in original.items() if key not in
        ('request_id', 'idempotency_key', 'expected_plan_hash')}
    assert validated_scrap_request(ScrapPreview.model_validate(preview))
    with pytest.raises(ValidationError):
        ScrapRequestLookup.model_validate(dict(operator_person_id=str(UUID(int=6)), original=preview))
    with pytest.raises(ValueError):
        validated_scrap_request(deepcopy(original))
