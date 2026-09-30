"""Own loss-derived return directory; no inferred shipment or inventory state."""
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from app.stock_return_origin_schemas import LossReturnOrigin
from app.stock_return_schemas import StockReturnDestinationOut


class LossReturnSenderItemOut(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    operation_no: str
    origin: LossReturnOrigin
    reason: str
    destination: StockReturnDestinationOut


class LossReturnSenderDirectoryOut(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: int
    ledger_cursor: int
    snapshot_hash: str
    queried_at: datetime
    items: tuple[LossReturnSenderItemOut, ...]
    next_after_id: UUID | None
