"""Public scrap facts: approval, stock posting and request closure stay separate."""
from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import Field, model_validator

from .stock_loss_correction_http_schemas import Output, Quantity, InverseFact, Missing, Recovery
from .formal_services.stock_loss_corrections.request_contracts import Digest, FactId


class ScrapFact(Output):
    scrap_operation_id: FactId
    scrap_line_id: FactId
    source_kind: Literal['original', 'correction']
    root_disposition_id: FactId
    correction_execution_id: FactId | None
    posting_transaction_id: FactId
    posting_movement_id: FactId
    quantity: Quantity
    source_account_id: FactId
    target_account_id: None
    status: Literal['posted']
    stock_effect: Literal['removed_from_managed_assets']
    request_id: str
    request_hash: Digest
    plan_hash: Digest

    @model_validator(mode='after')
    def source_binding(self):
        if (self.source_kind == 'correction') != (self.correction_execution_id is not None):
            raise ValueError('scrap source and correction fact disagree')
        return self


class RecoveryReviewFact(Output):
    fact_id: FactId
    recovery_request_id: FactId
    scrap_line_id: FactId
    stage: Literal['apply', 'regional', 'headquarters']
    status: Literal['awaiting_regional', 'awaiting_headquarters', 'needs_evidence', 'approved_pending_execution']
    stock_effect: Literal['none']
    actor_person_id: FactId
    authorization_version: int = Field(ge=1)
    request_id: str
    request_hash: Digest
    reason: str
    decision: Literal['verified', 'needs_evidence', 'approve', 'request_regional_review'] | None
    regional_review_id: FactId | None

    @model_validator(mode='after')
    def exact_stage(self):
        states = {('apply', None): 'awaiting_regional',
            ('regional', 'verified'): 'awaiting_headquarters',
            ('regional', 'needs_evidence'): 'needs_evidence',
            ('headquarters', 'approve'): 'approved_pending_execution',
            ('headquarters', 'request_regional_review'): 'awaiting_regional'}
        if (states.get((self.stage, self.decision)) != self.status
                or (self.stage == 'headquarters') != (self.regional_review_id is not None)
                or (self.stage == 'apply' and self.fact_id != self.recovery_request_id)):
            raise ValueError('review stage, decision or ancestor mismatch')
        return self


class RecoveryApplyFact(RecoveryReviewFact):
    stage: Literal['apply']


class RecoveryRegionalFact(RecoveryReviewFact):
    stage: Literal['regional']


class RecoveryHeadquartersFact(RecoveryReviewFact):
    stage: Literal['headquarters']


class RecoveryPostingFact(InverseFact):
    # Found stock comes back across the external boundary; it cannot be
    # presented as an ordinary internal-account inverse or available stock.
    source_account_id: None

    @model_validator(mode='after')
    def exact_inverse_source(self):
        if self.original_execution_id != (self.reversed_correction_id or self.root_disposition_id):
            raise ValueError('inverse does not reference its exact original execution')
        return self


class SealFact(Output):
    seal_id: FactId
    kind: Literal['original', 'correction', 'apply', 'regional', 'headquarters', 'execute']
    loss_operation_id: FactId
    loss_line_id: FactId
    root_disposition_id: FactId | None
    sealed_at: datetime
    stock_effect: Literal['none']

    @model_validator(mode='after')
    def root_binding(self):
        if self.root_disposition_id is None and self.kind != 'original':
            raise ValueError('only an original request may be sealed before a root exists')
        return self


class Sealed(Recovery):
    request_state: Literal['sealed']
    result_scope: Literal['closed_original_request']
    result: None
    seal: SealFact


Result = TypeVar('Result')


class Found(Recovery, Generic[Result]):
    request_state: Literal['found']
    result_scope: Literal['historical_original_outcome']
    result: Result


ScrapLookup = Annotated[Found[ScrapFact] | Missing | Sealed, Field(discriminator='request_state')]
ApplyLookup = Annotated[Found[RecoveryApplyFact] | Missing | Sealed, Field(discriminator='request_state')]
RegionalLookup = Annotated[Found[RecoveryRegionalFact] | Missing | Sealed, Field(discriminator='request_state')]
HeadquartersLookup = Annotated[Found[RecoveryHeadquartersFact] | Missing | Sealed, Field(discriminator='request_state')]
RecoveryLookup = Annotated[Found[RecoveryPostingFact] | Missing | Sealed, Field(discriminator='request_state')]


class ScrapPreviewOut(Output):
    planning_status: Literal['preview_only'] = 'preview_only'
    stock_effect: Literal['none']
    operation_id: FactId
    line_id: FactId
    decision_id: FactId
    predecessor_reversal_id: FactId | None
    source_account_id: FactId
    target_account_id: None
    source_condition: str
    quantity: Quantity
    serial_ids: list[FactId]
    plan_hash: Digest
    checked_at: datetime


class RecoveryPreviewOut(Output):
    planning_status: Literal['preview_only'] = 'preview_only'
    stock_effect: Literal['none']
    root_disposition_id: FactId
    original_execution_id: FactId
    original_transaction_id: FactId
    original_movement_id: FactId
    source_account_id: None
    target_account_id: FactId
    target_condition: str
    quantity: Quantity
    serial_ids: list[FactId]
    plan_hash: Digest
    checked_at: datetime


def public_preview(prepared, schema):
    values = {name: prepared.document[name] for name in schema.model_fields if name in prepared.document}
    return schema.model_validate(dict(values, plan_hash=prepared.plan_hash, checked_at=prepared.checked_at))
