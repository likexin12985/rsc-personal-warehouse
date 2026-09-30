"""Historical derived-return contents, independent from available stock."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from app.loss_return_sender_schemas import LossReturnSenderItemOut
from app.work_order_query_schemas import WorkOrderSerialOptionOut


class LossReturnSenderLineOut(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    operation_line_id: UUID
    source_loss_line_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal['new','used','damaged']
    lot_id: UUID | None
    lot_no: str | None
    return_quantity: str
    selected_serials: tuple[WorkOrderSerialOptionOut,...]


class LossReturnSenderDetailOut(LossReturnSenderItemOut):
    schema_version: Literal['1.0']='1.0'
    person_id: UUID
    authorization_version: int
    ledger_cursor: int
    queried_at: datetime
    loss_operation_no: str
    loss_submitted_at: datetime
    line: LossReturnSenderLineOut
