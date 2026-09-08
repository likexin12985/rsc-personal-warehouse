"""Strict pick commands and independently recoverable results."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .demand_schemas import MaterialRequestStateAxesOut
from .material_request_reservation_schemas import _quantity, _uuid


class OutboundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_request_version: StrictInt = Field(ge=0)
    pick_id: UUID
    target_stock_account_id: UUID
    outbound_qty: Decimal
    reason: str = Field(min_length=1, max_length=500)
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)

    @field_validator("pick_id", "target_stock_account_id")
    @classmethod
    def validate_id(cls, value):
        return _uuid(value, "outbound_binding")

    @field_validator("outbound_qty", mode="before")
    @classmethod
    def validate_quantity(cls, value):
        return _quantity(value)

    @field_validator("serial_ids")
    @classmethod
    def validate_serials(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate serial")
        return tuple(_uuid(item, "serial_id") for item in value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value):
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("outbound reason must be trimmed printable text")
        return value


class OutboundOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    kind: Literal["outbound"]
    outbound_line_id: UUID
    pick_id: UUID
    posting_id: UUID
    posting_no: str = Field(min_length=1, max_length=100)
    reservation_id: UUID
    allocation_id: UUID
    request_id: UUID
    request_no: str
    request_line_id: UUID
    revision_id: UUID
    revision_no: StrictInt = Field(ge=1)
    request_version: StrictInt = Field(ge=1)
    current_request_version: StrictInt = Field(ge=1)
    outbound_qty: str
    reason: str = Field(min_length=1, max_length=500)
    source_stock_account_id: UUID
    target_stock_account_id: UUID
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    outbound_transaction_id: UUID
    outbound_transaction_no: str = Field(min_length=1, max_length=100)
    serial_ids: tuple[UUID, ...] = Field(max_length=1000)
    state_axes: MaterialRequestStateAxesOut
    idempotency_replayed: bool

    @model_validator(mode="after")
    def validate_binding(self):
        quantity = _quantity(self.outbound_qty)
        if self.outbound_qty != format(quantity, ".3f"):
            raise ValueError("outbound quantity must use three decimal places")
        if self.current_request_version < self.request_version:
            raise ValueError("request version regressed")
        if self.source_stock_account_id == self.target_stock_account_id:
            raise ValueError("outbound accounts must differ")
        if len(set(self.serial_ids)) != len(self.serial_ids) or (self.serial_ids and quantity != len(self.serial_ids)):
            raise ValueError("SN outbound must bind exact quantity")
        if self.state_axes.outbound_status not in {"pending_pick", "picked", "outbound"}:
            raise ValueError("outbound aggregate status invalid")
        return self


class OutboundStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["not_observed", "confirmed"]
    command: OutboundOut | None

    @model_validator(mode="after")
    def validate_lookup(self):
        if (self.lookup_status == "confirmed") != (self.command is not None):
            raise ValueError("outbound lookup status and command disagree")
        return self


class OutboundSerialOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial_id: UUID
    serial_no: str


class OutboundOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pick_id: UUID
    pick_no: str
    outbound_line_id: UUID
    outbound_id: UUID
    outbound_no: str
    allocation_id: UUID
    reservation_id: UUID
    picked_qty: str
    outbound_qty: str
    outboundable_qty: str
    last_outbound_at: str | None
    condition_code: Literal["new", "used", "damaged", "scrapped"]
    lot_no: str | None
    material_name: str
    sku_code: str
    location_name: str
    tracking_mode: Literal["none", "lot", "serial", "lot_and_serial"]
    quantity_scale: StrictInt = Field(ge=0, le=3)
    allow_fraction: bool
    source_stock_account_id: UUID
    target_stock_account_id: UUID
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    serials: tuple[OutboundSerialOptionOut, ...] = Field(max_length=1000)


class OutboundOptionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_line_id: UUID
    request_version: StrictInt = Field(ge=0)
    revision_id: UUID
    revision_no: StrictInt = Field(ge=1)
    state_axes: MaterialRequestStateAxesOut
    items: tuple[OutboundOptionOut, ...] = Field(max_length=100)
