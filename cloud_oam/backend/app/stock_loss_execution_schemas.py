"""Public execution previews; internal proof documents stay on the server."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from .stock_loss_execution_recovery_schemas import RecoveryOutput


class ExecutionPreview(RecoveryOutput):
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    checked_at: datetime
    planning_status: Literal['preview_only'] = 'preview_only'
    stock_effect: Literal['none'] = 'none'
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    material_id: UUID
    source_account_id: UUID
    serial_ids: list[UUID]
    reason: str


class DispositionPreviewOut(ExecutionPreview):
    operation_id: UUID
    line_id: UUID
    disposition: Literal['restore_available', 'convert_used', 'convert_damaged']
    target_account_id: UUID
    source_condition: str
    target_condition: str


class DerivedReturnPreviewOut(ExecutionPreview):
    loss_operation_id: UUID
    loss_line_id: UUID
    headquarters_decision_id: UUID
    derived_return_operation_id: UUID
    pending_account_id: UUID
    condition_code: str
    return_fulfillment_required: Literal[True]


def public_preview(schema, prepared):
    # The digest covers the full server proof, even though that proof is not
    # returned as an editable document. Missing required fields still fail.
    return schema.model_validate({name: prepared[name]
        for name in schema.model_fields if name in prepared})
