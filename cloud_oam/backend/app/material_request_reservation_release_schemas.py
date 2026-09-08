"""Strict release commands and independently recoverable results."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .demand_schemas import MaterialRequestStateAxesOut
from .material_request_reservation_schemas import _quantity, _uuid


class ReservationReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_request_version: StrictInt = Field(ge=0)
    reservation_id: UUID
    released_qty: Decimal
    reason: str = Field(min_length=1, max_length=500)
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)

    @field_validator("reservation_id")
    @classmethod
    def validate_id(cls, value):
        return _uuid(value, "reservation_id")

    @field_validator("released_qty", mode="before")
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
            raise ValueError("release reason must be trimmed printable text")
        return value


class ReservationReleaseOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    kind: Literal["reservation_release"]
    release_id: UUID
    release_no: str = Field(min_length=1, max_length=100)
    reservation_id: UUID
    allocation_id: UUID
    request_id: UUID
    request_no: str
    request_line_id: UUID
    revision_id: UUID
    revision_no: StrictInt = Field(ge=1)
    request_version: StrictInt = Field(ge=1)
    current_request_version: StrictInt = Field(ge=1)
    released_qty: str
    reason: str = Field(min_length=1, max_length=500)
    source_stock_account_id: UUID
    target_stock_account_id: UUID
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    release_transaction_id: UUID
    release_transaction_no: str = Field(min_length=1, max_length=100)
    serial_ids: tuple[UUID, ...] = Field(max_length=1000)
    state_axes: MaterialRequestStateAxesOut
    idempotency_replayed: bool

    @model_validator(mode="after")
    def validate_binding(self):
        quantity = _quantity(self.released_qty)
        if self.released_qty != format(quantity, ".3f"):
            raise ValueError("release quantity must use three decimal places")
        if self.current_request_version < self.request_version:
            raise ValueError("request version regressed")
        if self.source_stock_account_id == self.target_stock_account_id:
            raise ValueError("release accounts must differ")
        if len(set(self.serial_ids)) != len(self.serial_ids) or (self.serial_ids and quantity != len(self.serial_ids)):
            raise ValueError("SN release must bind exact quantity")
        if self.state_axes.reservation_status not in {"partially_released", "released"}:
            raise ValueError("release aggregate status invalid")
        return self


class ReservationReleaseStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["not_observed", "confirmed"]
    command: ReservationReleaseOut | None

    @model_validator(mode="after")
    def validate_lookup(self):
        if (self.lookup_status == "confirmed") != (self.command is not None):
            raise ValueError("release lookup status and command disagree")
        return self


class ReservationReleaseSerialOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial_id: UUID
    serial_no: str


class ReservationReleaseOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: UUID
    reservation_no: str
    allocation_id: UUID
    reserved_qty: str
    released_qty: str
    releasable_qty: str
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
    serials: tuple[ReservationReleaseSerialOptionOut, ...] = Field(max_length=1000)


class ReservationReleaseOptionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_line_id: UUID
    request_version: StrictInt = Field(ge=0)
    revision_id: UUID
    revision_no: StrictInt = Field(ge=1)
    state_axes: MaterialRequestStateAxesOut
    items: tuple[ReservationReleaseOptionOut, ...] = Field(max_length=100)
