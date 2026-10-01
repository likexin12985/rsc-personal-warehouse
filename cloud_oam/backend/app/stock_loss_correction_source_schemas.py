"""Verified correction references never constitute stock preview or write authority."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import Field
from .stock_loss_correction_http_schemas import Output,Quantity
from .formal_services.stock_loss_corrections.request_contracts import FactId,Digest,Disposition

class RootReference(Output):
    root_disposition_id: FactId
    expected_root_request_hash: Digest
    expected_submission_plan_hash: Digest

class InverseReference(RootReference):
    reversed_correction_id: FactId | None
    expected_execution_request_hash: Digest

class ApprovalReference(RootReference):
    reversal_id: FactId
    expected_reversal_hash: Digest

class CorrectionReference(ApprovalReference):
    correction_decision_id: FactId
    expected_correction_decision_hash: Digest

class ApprovalChoice(Output):
    correction_decision_id: FactId
    disposition: Disposition
    reason: str
    execution_mode: Literal['preview_required','dedicated_flow_required']
    preview_reference: CorrectionReference | None

class HistoryFact(Output):
    kind: Literal['original_execution','inverse','approval','correction_execution']
    fact_id: FactId
    request_hash: Digest
    created_at: datetime
    posting_transaction_id: FactId | None
    disposition: Disposition | None
    quantity: Quantity | None

class CorrectionSources(Output):
    schema_version: Literal['1.0']='1.0'
    result_scope: Literal['verified_loss_history_references']='verified_loss_history_references'
    write_authorization_provided: Literal[False]=False
    stock_effect: Literal['none']='none'
    person_id: FactId
    authorization_version: int=Field(ge=1)
    queried_at: datetime
    observed_ledger_cursor: int=Field(ge=1)
    root_disposition_id: FactId
    operation_id: FactId
    line_id: FactId
    quantity: Quantity
    serial_ids: tuple[FactId,...]
    frozen_share_in_verified_history: Decimal=Field(ge=0,max_digits=18,decimal_places=3)
    chain_state: Literal['active_execution','awaiting_approval','awaiting_execution','dedicated_compensation_required']
    inverse_preview_reference: InverseReference | None
    approval_reference: ApprovalReference | None
    approval_choices: tuple[ApprovalChoice,...]
    history: tuple[HistoryFact,...]
