"""Public correction facts and previews, distinct from present stock or delivery."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .formal_services.stock_loss_corrections.request_contracts import Digest, FactId, Disposition

Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)]

class Output(BaseModel):
    model_config = ConfigDict(extra='forbid')

class CorrectionFact(Output):
    root_disposition_id: FactId
    operation_id: FactId
    line_id: FactId
    original_headquarters_decision_id: FactId
    requester_person_id: FactId
    actor_user_id: str
    actor_person_id: FactId
    authorization_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)
    request_id: str
    request_hash: Digest

class ApprovalFact(CorrectionFact):
    correction_decision_id: FactId
    reversal_id: FactId
    expected_reversal_hash: Digest
    disposition: Disposition
    approval_stage: Literal['approved']
    stock_effect: Literal['none']

class PostingFact(CorrectionFact):
    posting_transaction_id: FactId
    posting_movement_id: FactId
    source_account_id: FactId
    target_account_id: FactId
    quantity: Quantity
    plan_hash: Digest
    status: Literal['posted']

class InverseFact(PostingFact):
    reversal_id: FactId
    original_execution_id: FactId
    reversed_correction_id: FactId | None
    original_transaction_id: FactId
    original_movement_id: FactId
    stock_effect: Literal['restores_original_frozen_share']

class ExecutionFact(PostingFact):
    correction_execution_id: FactId
    correction_decision_id: FactId
    reversal_id: FactId
    disposition: Literal['restore_available', 'convert_used', 'convert_damaged']
    return_operation_id: None
    return_fulfillment_required: Literal[False]
    stock_effect: Literal['frozen_to_available', 'frozen_to_used', 'frozen_to_damaged']

    @model_validator(mode='after')
    def exact_effect(self):
        effects = {'restore_available':'frozen_to_available', 'convert_used':'frozen_to_used',
                   'convert_damaged':'frozen_to_damaged'}
        if self.stock_effect != effects[self.disposition]:
            raise ValueError('correction disposition and stock effect disagree')
        return self

class Recovery(Output):
    retry_allowed: Literal[False]
    request_id: str
    request_hash: Digest

class Missing(Recovery):
    request_state: Literal['not_found']
    result_scope: Literal['unconfirmed_request']
    result: None

class SealFact(Output):
    seal_id: FactId
    root_disposition_id: FactId
    sealed_at: datetime
    stock_effect: Literal['none']

class Sealed(Recovery):
    request_state: Literal['sealed']
    result_scope: Literal['closed_original_request']
    result: None
    seal: SealFact

class InverseFound(Recovery):
    request_state: Literal['found']
    result_scope: Literal['historical_original_posting', 'historical_correction_inverse_posting']
    result: InverseFact

class ApprovalFound(Recovery):
    request_state: Literal['found']
    result_scope: Literal['historical_approval']
    result: ApprovalFact

class ExecutionFound(Recovery):
    request_state: Literal['found']
    result_scope: Literal['historical_correction_posting']
    result: ExecutionFact

InverseRecovery = Annotated[InverseFound | Missing | Sealed, Field(discriminator='request_state')]
ApprovalRecovery = Annotated[ApprovalFound | Missing | Sealed, Field(discriminator='request_state')]
ExecutionRecovery = Annotated[ExecutionFound | Missing | Sealed, Field(discriminator='request_state')]

class StockPreview(Output):
    planning_status: Literal['preview_only'] = 'preview_only'
    stock_effect: Literal['none'] = 'none'
    root_disposition_id: FactId
    source_account_id: FactId
    target_account_id: FactId
    source_condition: str
    target_condition: str
    quantity: Quantity
    serial_ids: list[UUID]
    plan_hash: Digest
    checked_at: datetime

class InversePreview(StockPreview):
    original_execution_id: FactId
    original_transaction_id: FactId
    original_movement_id: FactId

class ExecutionPreview(StockPreview):
    reversal_id: FactId
    correction_decision_id: FactId
    disposition: Literal['restore_available', 'convert_used', 'convert_damaged']
    target_requires_creation: bool


def public_preview(prepared, schema):
    # Internal frozen holds, authority snapshots and full intent remain private.
    document = prepared.document
    fields = {name: document[name] for name in schema.model_fields if name in document}
    fields.update(plan_hash=prepared.plan_hash, checked_at=prepared.checked_at)
    return schema.model_validate(fields)
