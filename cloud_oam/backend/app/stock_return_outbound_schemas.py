"""Physical return departure, independent of carrier and receiving records."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from .stock_return_schemas import ReturnReason, StockReturnDestinationOut, StockReturnOut, StockReturnCancellationOut
from .work_order_material_schemas import StrictInput, SerialVerificationIn


class StockReturnOutboundLineIn(StrictInput):
    operation_line_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)


class StockReturnOutboundPreviewIn(ReturnReason):
    operator_person_id: UUID
    outbound_at: datetime
    lines: tuple[StockReturnOutboundLineIn, ...] = Field(min_length=1, max_length=100)

    @field_validator("outbound_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None: raise ValueError("实物发出时间必须包含时区")
        return value.astimezone(timezone.utc)


class StockReturnOutboundSubmitIn(StockReturnOutboundPreviewIn):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class StockReturnOutboundSerialOut(BaseModel):
    serial_id: UUID
    serial_no: str


class StockReturnOutboundLineOut(BaseModel):
    operation_line_id: UUID
    source_recovery_line_id: UUID
    source_stock_account_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: str
    lot_id: UUID | None
    lot_no: str | None
    return_quantity: str
    departed_quantity: str
    remaining_quantity: str
    held_quantity: str
    selected_quantity: str
    selected_serials: tuple[StockReturnOutboundSerialOut, ...]


class StockReturnOutboundPreviewOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["preview_only"] = "preview_only"
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    operator_person_id: UUID
    authorization_version: int
    outbound_at: datetime
    reason: str
    ledger_cursor: int
    checked_at: datetime
    destination: StockReturnDestinationOut
    request_hash: str
    plan_hash: str
    lines: tuple[StockReturnOutboundLineOut, ...]


class StockReturnOutboundOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["outbound"] = "outbound"
    outbound_id: UUID
    outbound_no: str
    operation_id: UUID
    work_order_id: UUID
    operator_person_id: UUID
    outbound_at: datetime
    recorded_at: datetime
    reason: str
    request_id: str
    request_hash: str
    plan_hash: str
    posting_transaction_id: UUID
    destination: StockReturnDestinationOut
    lines: tuple[StockReturnOutboundLineOut, ...]


class StockReturnOutboundOptionLineOut(BaseModel):
    operation_line_id: UUID
    source_recovery_line_id: UUID
    source_stock_account_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: str
    lot_id: UUID | None
    lot_no: str | None
    tracking_mode: Literal["none", "lot", "serial", "lot_and_serial"]
    quantity_scale: int
    allow_fraction: bool
    return_quantity: str
    departed_quantity: str
    remaining_quantity: str
    held_quantity: str
    selectable_quantity: str
    serials: tuple[StockReturnOutboundSerialOut, ...]


class StockReturnOutboundOptionsOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    person_id: UUID
    authorization_version: int
    ledger_cursor: int
    queried_at: datetime
    destination: StockReturnDestinationOut
    lines: tuple[StockReturnOutboundOptionLineOut, ...]


class StockReturnOutboundHistoryOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: UUID
    work_order_id: UUID
    person_id: UUID
    authorization_version: int
    queried_at: datetime
    outbound_status: Literal["not_outbound", "partially_outbound", "outbound"]
    original: StockReturnOut
    cancellation: StockReturnCancellationOut | None
    items: tuple[StockReturnOutboundOut, ...]
