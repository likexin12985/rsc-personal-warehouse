"""Server-proved return provenance; never inferred from missing work-order IDs."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class ReturnOriginBase(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    operation_id: UUID
    requester_id: UUID
    submitted_by_user_id: str
    request_hash: str
    plan_hash: str
    posting_transaction_id: UUID
    submitted_at: datetime


class WorkOrderReturnOrigin(ReturnOriginBase):
    origin_kind: Literal['work_order_recovery'] = 'work_order_recovery'
    work_order_id: UUID


class LossReturnOrigin(ReturnOriginBase):
    origin_kind: Literal['loss_report'] = 'loss_report'
    loss_operation_id: UUID
    loss_line_id: UUID
    headquarters_decision_id: UUID
    disposition_id: UUID
