"""Explicit approved-scrap requests, including correction-origin execution.

These contracts do not install routes, grant permission or post stock. The
service must derive quantities/accounts/SN from the exact persisted approval,
verify available evidence and current authority, and prove the complete chain
at COMMIT. Recovery carries the entire original command, never a new intent.
"""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator

from .formal_services.stock_loss_corrections.request_contracts import (
    Digest, FactId, Reason, RequestCoordinates,
)
from .work_order_material_schemas import StrictInput


class ScrapInput(StrictInput):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')


class OriginalScrapSource(ScrapInput):
    kind: Literal['original']
    headquarters_decision_id: FactId
    expected_headquarters_review_hash: Digest
    expected_submission_plan_hash: Digest


class CorrectedScrapSource(ScrapInput):
    kind: Literal['correction']
    root_disposition_id: FactId
    expected_root_request_hash: Digest
    expected_submission_plan_hash: Digest
    reversal_id: FactId
    expected_reversal_hash: Digest
    correction_decision_id: FactId
    expected_correction_decision_hash: Digest


ScrapSource = Annotated[OriginalScrapSource | CorrectedScrapSource, Field(discriminator='kind')]


class ScrapPreview(ScrapInput):
    source: ScrapSource
    execution_reason: Reason
    evidence_file_ids: tuple[FactId, ...] = Field(min_length=1, max_length=20)

    @field_validator('evidence_file_ids')
    @classmethod
    def unique_evidence(cls, identifiers):
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('每份报废执行证据只能绑定一次')
        return identifiers


class ScrapExecute(ScrapPreview, RequestCoordinates):
    expected_plan_hash: Digest


class ScrapRequestLookup(ScrapInput):
    operator_person_id: FactId
    original: ScrapExecute


class ScrapRequestSeal(ScrapRequestLookup):
    pass


def validated_scrap_request(request):
    """Reparse model_copy/model_construct candidates at every service boundary."""
    if type(request) not in (ScrapPreview, ScrapExecute, ScrapRequestLookup, ScrapRequestSeal):
        raise ValueError('an explicit scrap request contract is required')
    return type(request).model_validate(request.model_dump(mode='python'))
