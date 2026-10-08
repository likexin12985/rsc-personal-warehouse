"""Public condition facts and exact-request recovery; no replay authority."""
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .return_condition_requests import ConditionSubmit
from .return_condition_decision_requests import ConditionDecision
from .return_condition_settlement_requests import ConditionSettlement
from .return_condition_schema import TRANSITIONS
from .stock_loss_correction_http_schemas import Output, Quantity
from .formal_services.stock_loss_corrections.request_contracts import Digest, FactId, Reason

ConditionCommand = Annotated[
    ConditionSubmit | ConditionDecision | ConditionSettlement, Field(discriminator='action')]
ConditionAction = Literal['submit', 'supplement', 'withdraw', 'verify_region',
    'return_evidence', 'reject_region', 'return_region', 'reject_hq', 'approve_hq',
    'cancel_approved', 'execute', 'release']
ConditionStatus = Literal['awaiting_regional', 'awaiting_headquarters', 'needs_evidence',
    'rejected_pending_release', 'cancelled_pending_release', 'approved',
    'released_rejected', 'released_cancelled', 'executed']


class ConditionRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')
    operator_person_id: FactId
    original: ConditionCommand


class ConditionFact(Output):
    schema_version: Literal['condition_result/1']
    case_id: FactId
    event_id: FactId
    inbound_line_id: FactId
    action: ConditionAction
    status: ConditionStatus
    quantity: Quantity
    actor_user_id: str = Field(strict=True, min_length=1)
    actor_person_id: FactId
    authorization_version: int = Field(strict=True, ge=1)
    request_id: str = Field(strict=True, min_length=8, max_length=160)
    request_hash: Digest
    plan_hash: Digest
    posting_transaction_id: FactId | None
    posting_movement_id: FactId | None
    stock_effect: Literal['freeze', 'none', 'status_change', 'unfreeze']
    reason: Reason

    @model_validator(mode='after')
    def consistent_effect(self):
        effect = {'submit': 'freeze', 'execute': 'status_change', 'release': 'unfreeze'}.get(self.action, 'none')
        posted = effect != 'none'
        if (not any(kind == self.action and after == self.status for kind, _, after in TRANSITIONS)
                or self.stock_effect != effect
                or (self.posting_transaction_id is not None) != posted
                or (self.posting_movement_id is not None) != posted):
            raise ValueError('condition action, state and stock facts disagree')
        return self


class RecoveryBase(Output):
    request_id: str = Field(strict=True, min_length=8, max_length=160)
    retry_allowed: Literal[False]
    current_stock_verified: Literal[False]
    observed_ledger_cursor: int = Field(strict=True, ge=0)


class ConditionUnknown(RecoveryBase):
    request_state: Literal['unknown']
    result_scope: Literal['historical_original_outcome']
    result: None
    absence_sealed: Literal[False]


class ConditionFound(RecoveryBase):
    request_state: Literal['found']
    result_scope: Literal['historical_original_outcome']
    result: ConditionFact
    absence_sealed: Literal[False]
    current_case_status: ConditionStatus
    original_input_hash: Digest

    @model_validator(mode='after')
    def same_request(self):
        if self.request_id != self.result.request_id:
            raise ValueError('historical result belongs to another request')
        return self


class ConditionSealFact(Output):
    seal_id: FactId
    kind: ConditionAction
    inbound_line_id: FactId
    case_id: FactId | None
    expected_event_id: FactId | None
    sealed_at: AwareDatetime
    stock_effect: Literal['none']

    @model_validator(mode='after')
    def same_stage(self):
        subsequent = self.kind != 'submit'
        if ((self.case_id is not None) != subsequent
                or (self.expected_event_id is not None) != subsequent):
            raise ValueError('closure stage and original references disagree')
        return self


class ConditionSealed(RecoveryBase):
    request_state: Literal['sealed']
    result_scope: Literal['closed_original_request']
    result: None
    seal: ConditionSealFact
    original_input_hash: Digest
    absence_sealed: Literal[True]
    original_preflight_verified: Literal[False]
    stock_effect: Literal['none']


ConditionLookup = Annotated[ConditionUnknown | ConditionFound | ConditionSealed,
    Field(discriminator='request_state')]
ConditionClosure = Annotated[ConditionFound | ConditionSealed, Field(discriminator='request_state')]

NonnegativeQuantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=3)]


class ConditionSourceSerial(Output):
    serial_id: FactId
    serial_no: str
    qr_code: str
    claimable_for_correction: bool = Field(strict=True)


class ConditionSourcePreview(Output):
    stage: Literal['source_evidence_only']
    inbound_line_id: FactId
    inbound_id: FactId
    root_disposition_id: FactId
    source_account_id: FactId
    material_id: FactId
    lot_id: FactId | None
    location_id: FactId
    custodian_person_id: FactId
    recorded_condition: Literal['new', 'used']
    required_condition: Literal['damaged']
    source_status: Literal['recorded_stock_retained', 'verified_condition_history', 'later_activity_requires_reconciliation']
    historical_damaged_quantity: Quantity
    account_balance_quantity: NonnegativeQuantity
    claimable_quantity: NonnegativeQuantity | None
    tracking_mode: Literal['none', 'lot', 'serial', 'lot_and_serial']
    serials: list[ConditionSourceSerial]
    observed_ledger_cursor: int = Field(strict=True, ge=1)
    expected_source_hash: Digest
    checked_at: AwareDatetime
    physical_verification_required: Literal[True]
    posting_allowed: Literal[False]

    @model_validator(mode='after')
    def bounded_source(self):
        unresolved = self.source_status == 'later_activity_requires_reconciliation'
        if unresolved != (self.claimable_quantity is None):
            raise ValueError('unresolved source cannot claim a available quantity')
        if self.claimable_quantity is not None and self.claimable_quantity > min(
                self.historical_damaged_quantity, self.account_balance_quantity):
            raise ValueError('claimable amount exceeds historical share or account stock')
        if len({s.serial_id for s in self.serials}) != len(self.serials):
            raise ValueError('duplicate source serial')
        tracked = self.tracking_mode in ('serial', 'lot_and_serial')
        if (tracked and len(self.serials) != self.historical_damaged_quantity) or (not tracked and self.serials):
            raise ValueError('source serials and quantity disagree')
        if tracked and self.claimable_quantity is not None and sum(
                s.claimable_for_correction for s in self.serials) != self.claimable_quantity:
            raise ValueError('claimable serials and quantity disagree')
        return self
