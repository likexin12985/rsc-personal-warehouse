"""Strict found-stock approval commands, separate from request-result lookup.

No command accepts quantity, SN, accounts, ownership or a claimed permission.
Services derive them from the exact scrap and verify the current principal.
These types install no endpoint and do not authorize a physical recovery.
"""
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.stock_scrap_schemas import ScrapInput
from app.formal_services.stock_loss_corrections.request_contracts import (
    Digest, FactId, Reason, RequestCoordinates,
)


class ScrapRecoverySource(ScrapInput):
    scrap_line_id: FactId
    expected_scrap_request_hash: Digest


class ScrapRecoveryApply(ScrapInput, RequestCoordinates):
    action: Literal['apply_scrap_recovery']
    source: ScrapRecoverySource
    reason: Reason
    evidence_file_ids: tuple[FactId, ...] = Field(min_length=1, max_length=20)

    @field_validator('evidence_file_ids')
    @classmethod
    def unique_evidence(cls, identifiers):
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('每份找回实物证据只能绑定一次')
        return identifiers


class ScrapRecoveryRequestBinding(ScrapInput):
    source: ScrapRecoverySource
    recovery_request_id: FactId
    expected_request_hash: Digest


class ScrapRecoveryRegionalReview(ScrapRecoveryRequestBinding, RequestCoordinates):
    action: Literal['review_scrap_recovery_region']
    decision: Literal['verified', 'needs_evidence']
    reason: Reason


class ScrapRecoveryHeadquartersReview(ScrapRecoveryRequestBinding, RequestCoordinates):
    action: Literal['review_scrap_recovery_headquarters']
    regional_review_id: FactId
    expected_regional_hash: Digest
    decision: Literal['approve', 'request_regional_review']
    reason: Reason


class ScrapRecoveryPreview(ScrapRecoveryRequestBinding):
    headquarters_review_id: FactId
    expected_headquarters_hash: Digest
    reason: Reason


class ScrapRecoveryExecute(ScrapRecoveryPreview, RequestCoordinates):
    action: Literal['execute_scrap_recovery']
    expected_plan_hash: Digest


RecoveryCommand = Annotated[
    ScrapRecoveryApply | ScrapRecoveryRegionalReview |
    ScrapRecoveryHeadquartersReview | ScrapRecoveryExecute,
    Field(discriminator='action'),
]


class ScrapRecoveryRequestLookup(ScrapInput):
    operator_person_id: FactId
    original: RecoveryCommand


class ScrapRecoveryRequestSeal(ScrapRecoveryRequestLookup):
    pass


def validated_recovery_request(request):
    if type(request) not in (
        ScrapRecoveryApply, ScrapRecoveryRegionalReview,
        ScrapRecoveryHeadquartersReview, ScrapRecoveryPreview,
        ScrapRecoveryExecute, ScrapRecoveryRequestLookup, ScrapRecoveryRequestSeal,
    ):
        raise ValueError('an explicit scrap recovery request contract is required')
    return type(request).model_validate(request.model_dump(mode='python'))
