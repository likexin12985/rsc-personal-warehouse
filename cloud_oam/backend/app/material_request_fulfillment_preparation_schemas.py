"""Read-only, original-reservation-bound fulfillment preparation contracts."""
from datetime import datetime
from decimal import Decimal
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from .demand_schemas import MaterialRequestStateAxesOut
from .material_request_reservation_schemas import _uuid


class PreparationSerialOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial_id: UUID
    serial_no: str = Field(min_length=1, max_length=160)


class FulfillmentPreparationItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: UUID
    reservation_no: str = Field(min_length=1, max_length=100)
    allocation_id: UUID
    source_stock_account_id: UUID
    location_name: str = Field(min_length=1, max_length=200)
    sku_code: str = Field(min_length=1, max_length=100)
    material_name: str = Field(min_length=1, max_length=240)
    reserved_qty: str
    released_qty: str
    remaining_reserved_qty: str
    verified_held_qty: str
    tracking_mode: Literal["none", "lot", "serial", "lot_and_serial"]
    quantity_scale: StrictInt = Field(ge=0, le=3)
    allow_fraction: StrictBool
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    preparation_status: Literal["ready_for_review", "released", "blocked"]
    blockers: tuple[Literal["source_inactive", "pool_shortfall", "serial_mismatch"], ...]
    serials: tuple[PreparationSerialOut, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_conservation(self):
        values = []
        for field in ("reserved_qty", "released_qty", "remaining_reserved_qty", "verified_held_qty"):
            raw = getattr(self, field)
            if not re.fullmatch(r"(?:0|[1-9]\d{0,14})\.\d{3}", raw):
                raise ValueError("quantity must be canonical nonnegative numeric(18,3)")
            value = Decimal(raw)
            values.append(value)
        reserved, released, remaining, held = values
        if reserved <= 0 or released + remaining != reserved or held > remaining:
            raise ValueError("original reservation quantities do not conserve")
        if len(set(self.blockers)) != len(self.blockers):
            raise ValueError("duplicate blocker")
        if self.preparation_status == "ready_for_review":
            valid = remaining > 0 and held == remaining and not self.blockers
        elif self.preparation_status == "released":
            valid = remaining == held == 0 and not self.blockers
        else:
            valid = remaining > 0 and held == 0 and bool(self.blockers)
        if not valid:
            raise ValueError("preparation status and evidence disagree")
        if len({s.serial_id for s in self.serials}) != len(self.serials):
            raise ValueError("duplicate original SN")
        if self.tracking_mode in {"serial", "lot_and_serial"}:
            if held != len(self.serials) or any(value != value.to_integral_value() for value in values):
                raise ValueError("SN quantity must match original bound serials")
        elif self.serials:
            raise ValueError("untracked material cannot expose SN candidates")
        for value in values:
            if value != value.quantize(Decimal(1).scaleb(-self.quantity_scale)) or (not self.allow_fraction and value != value.to_integral_value()):
                raise ValueError("quantity disagrees with current tracking policy")
        for field in ("reservation_id", "allocation_id", "source_stock_account_id"):
            _uuid(getattr(self, field), field)
        for serial in self.serials:
            _uuid(serial.serial_id, "serial_id")
        return self


class FulfillmentPreparationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    scope: Literal["authorized_sources"] = "authorized_sources"
    request_id: UUID
    request_line_id: UUID
    material_id: UUID
    request_version: StrictInt = Field(ge=1)
    revision_id: UUID
    revision_no: StrictInt = Field(ge=1)
    state_axes: MaterialRequestStateAxesOut
    ledger_cursor: StrictInt = Field(ge=0)
    projected_at: datetime
    items: tuple[FulfillmentPreparationItemOut, ...] = Field(max_length=100)

    @field_validator("projected_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("projection time must be timezone aware")
        return value

    @model_validator(mode="after")
    def validate_page(self):
        for field in ("request_id", "request_line_id", "material_id", "revision_id"):
            _uuid(getattr(self, field), field)
        if self.state_axes.request_status not in {"approved", "partially_approved"} or self.state_axes.outbound_status != "not_started":
            raise ValueError("preparation requires final approval and an unstarted outbound")
        if len({item.reservation_id for item in self.items}) != len(self.items):
            raise ValueError("duplicate reservation")
        serial_ids = [s.serial_id for item in self.items for s in item.serials]
        if len(serial_ids) > 1000 or len(set(serial_ids)) != len(serial_ids):
            raise ValueError("SN cannot appear in multiple remaining reservations")
        if any(item.source_ledger_cursor > self.ledger_cursor for item in self.items):
            raise ValueError("account projection is newer than the read snapshot")
        return self
