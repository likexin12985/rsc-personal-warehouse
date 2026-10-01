"""Explicit whole-return stop references; original fulfillment remains historical."""
from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from .stock_loss_correction_http_schemas import Output, Quantity, InversePreview
from .stock_loss_correction_source_schemas import InverseReference
from .formal_services.stock_loss_corrections.request_contracts import FactId,Digest


class ReturnStopFact(Output):
    stop_id: FactId
    reversal_id: FactId
    posting_transaction_id: FactId
    stopped_at: datetime
    reason: str=Field(min_length=1,max_length=500)
    quantity: Quantity
    serial_ids: tuple[FactId,...]
    evidence_fingerprint: Digest
    stop_scope: Literal['whole_unshipped_return']='whole_unshipped_return'
    historical_stock_effect: Literal['return_pending_to_original_frozen']='return_pending_to_original_frozen'


class ReturnStopSources(Output):
    schema_version: Literal['1.0']='1.0'
    result_scope: Literal['verified_loss_return_stop_references']='verified_loss_return_stop_references'
    stock_effect: Literal['none']='none'
    current_stock_verified: Literal[False]=False
    write_authorization_provided: Literal[False]=False
    person_id: FactId
    authorization_version: int=Field(ge=1)
    queried_at: datetime
    observed_ledger_cursor: int=Field(ge=1)
    root_disposition_id: FactId
    report_operation_id: FactId
    report_line_id: FactId
    return_operation_id: FactId
    return_line_id: FactId
    quantity: Quantity
    serial_ids: tuple[FactId,...]
    state: Literal['preview_required','stopped','downstream_compensation_required']
    preview_reference: InverseReference|None
    stop: ReturnStopFact|None

    @model_validator(mode='after')
    def exact_state(self):
        if len(set(self.serial_ids))!=len(self.serial_ids) or (self.serial_ids and self.quantity!=len(self.serial_ids)):
            raise ValueError('exact serial quantity required')
        if self.state=='preview_required':
            ref=self.preview_reference
            if self.stop is not None or ref is None or ref.root_disposition_id!=self.root_disposition_id or ref.reversed_correction_id is not None or ref.expected_execution_request_hash!=ref.expected_root_request_hash:
                raise ValueError('exact original return preview reference required')
        elif self.preview_reference is not None:
            raise ValueError('completed or downstream return is not a stop preview')
        if self.state=='stopped':
            if self.stop is None or self.stop.quantity!=self.quantity or self.stop.serial_ids!=self.serial_ids:
                raise ValueError('exact stopped share required')
        elif self.stop is not None:raise ValueError('unexpected stop fact')
        return self


class ReturnStopPreview(InversePreview):
    return_operation_id: FactId
    return_line_id: FactId
    stop_scope: Literal['whole_unshipped_return']='whole_unshipped_return'
    planned_stock_effect: Literal['return_pending_to_original_frozen']='return_pending_to_original_frozen'
