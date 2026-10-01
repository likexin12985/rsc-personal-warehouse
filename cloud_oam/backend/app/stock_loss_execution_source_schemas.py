"""Approved decision references and routing choices for the HQ workbench."""
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from .stock_loss_execution_recovery_schemas import RecoveryOutput
from .stock_loss_review_query_schemas import LossReviewDetailOut
from .stock_loss_schemas import StockLossDispositionPreviewIn
from .stock_return_schemas import StockReturnDestinationOut


class OriginalPostingOut(RecoveryOutput):
    result_scope: Literal['original_posting'] = 'original_posting'
    disposition_id: UUID
    executor_person_id: UUID
    posting_transaction_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    return_operation_id: UUID | None = None


class ApprovedExecutionLineOut(RecoveryOutput):
    line_id: UUID
    headquarters_decision_id: UUID
    disposition: Literal['restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap']
    reason: str
    preview_reference: StockLossDispositionPreviewIn
    original_posting: OriginalPostingOut | None


class ExecutionSourcesOut(RecoveryOutput):
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: int = Field(ge=1)
    queried_at: datetime
    report: LossReviewDetailOut
    decisions: tuple[ApprovedExecutionLineOut, ...]
    return_routes_status: Literal['not_required', 'available', 'unavailable']
    return_routes: tuple[StockReturnDestinationOut, ...]
    return_routes_reason: str | None
