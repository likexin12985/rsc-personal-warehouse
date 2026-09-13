"""Carrier handover parcels bind actual return departures, never allocations."""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from .stock_return_schemas import ReturnReason, StockReturnDestinationOut
from .stock_return_outbound_schemas import StockReturnOutboundSerialOut
from .work_order_material_schemas import StrictInput


class StockReturnShipmentLineIn(StrictInput):
    outbound_line_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)


class StockReturnShipmentPreviewIn(ReturnReason):
    operator_person_id: UUID
    carrier: str = Field(min_length=1, max_length=100)
    tracking_no: str = Field(min_length=1, max_length=100)
    shipped_at: datetime
    lines: tuple[StockReturnShipmentLineIn, ...] = Field(min_length=1, max_length=100)

    @field_validator("carrier", "tracking_no")
    @classmethod
    def parcel_text(cls, value):
        value=value.strip()
        if not value or any(ord(char)<32 or ord(char)==127 for char in value):
            raise ValueError("承运商和运单号不能为空或包含控制字符")
        value.encode("utf-8")
        return value

    @field_validator("shipped_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None: raise ValueError("实际交运时间必须包含时区")
        return value.astimezone(timezone.utc)


class StockReturnShipmentSubmitIn(StockReturnShipmentPreviewIn):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")


class StockReturnShipmentLineOut(BaseModel):
    outbound_id: UUID
    outbound_no: str
    outbound_line_id: UUID
    operation_line_id: UUID
    source_recovery_line_id: UUID
    transit_stock_account_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: str
    lot_id: UUID | None
    lot_no: str | None
    outbound_quantity: str
    shipped_quantity: str
    unshipped_quantity: str
    in_transit_quantity: str
    unassigned_quantity: str
    selected_quantity: str
    selected_serials: tuple[StockReturnOutboundSerialOut, ...]


class StockReturnShipmentPreviewOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["preview_only"] = "preview_only"
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    operator_person_id: UUID
    authorization_version: int
    shipped_at: datetime
    carrier: str
    tracking_no: str
    reason: str
    ledger_cursor: int
    checked_at: datetime
    destination: StockReturnDestinationOut
    request_hash: str
    plan_hash: str
    lines: tuple[StockReturnShipmentLineOut, ...]


class StockReturnShipmentOut(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["shipped"] = "shipped"
    shipment_id: UUID
    shipment_no: str
    operation_id: UUID
    work_order_id: UUID
    operator_person_id: UUID
    shipped_at: datetime
    recorded_at: datetime
    carrier: str
    tracking_no: str
    reason: str
    request_id: str
    request_hash: str
    plan_hash: str
    destination: StockReturnDestinationOut
    lines: tuple[StockReturnShipmentLineOut, ...]
