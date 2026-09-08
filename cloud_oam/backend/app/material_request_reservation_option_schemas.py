"""Read-only reservation candidates, anchored to actual allocation facts."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr, model_validator

from .material_request_allocation_option_schemas import MaterialRequestAllocationOptionOut


class MaterialRequestReservationSerialOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial_id: UUID
    serial_no: StrictStr = Field(min_length=1, max_length=200)
    qr_code: StrictStr = Field(min_length=1, max_length=250)
    lot_id: UUID | None


class MaterialRequestReservationOptionOut(MaterialRequestAllocationOptionOut):
    allocation_id: UUID
    allocation_no: StrictStr = Field(min_length=1, max_length=100)
    allocated_qty: StrictStr = Field(min_length=1, max_length=32)
    reserved_qty: StrictStr = Field(min_length=1, max_length=32)
    remaining_qty: StrictStr = Field(min_length=1, max_length=32)
    reservable_qty: StrictStr = Field(min_length=1, max_length=32)
    tracking_mode: Literal["none", "lot", "serial", "lot_and_serial"]
    serial_options: tuple[MaterialRequestReservationSerialOptionOut, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def validate_quantities(self):
        values = {}
        for name in ("quantity", "allocated_qty", "reserved_qty", "remaining_qty", "reservable_qty"):
            value = getattr(self, name)
            pattern = r"(?:0|[1-9]\d{0,14})" + (rf"\.\d{{{self.quantity_scale}}}" if self.quantity_scale else "")
            if re.fullmatch(pattern, value, flags=re.ASCII) is None:
                raise ValueError("candidate quantities must use the material's fixed scale")
            values[name] = Decimal(value)
        if values["allocated_qty"] <= 0 or values["remaining_qty"] != values["allocated_qty"] - values["reserved_qty"]:
            raise ValueError("allocation remainder is inconsistent")
        if not 0 < values["reservable_qty"] <= min(values["remaining_qty"], values["quantity"]):
            raise ValueError("reservation capacity is inconsistent")
        serial_ids = [item.serial_id for item in self.serial_options]
        if len(serial_ids) != len(set(serial_ids)):
            raise ValueError("candidate serials must be unique")
        if self.tracking_mode in {"serial", "lot_and_serial"}:
            if values["reservable_qty"] > len(serial_ids):
                raise ValueError("reservation capacity exceeds serial candidates")
        elif serial_ids:
            raise ValueError("non-serial materials cannot have serial candidates")
        return self


class MaterialRequestReservationOptionPageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_line_id: UUID
    request_version: int = Field(ge=0)
    current_revision_id: UUID
    current_revision_no: int = Field(ge=1)
    material_id: UUID
    projection_status: Literal["ready"]
    opening_balance_status: Literal["established"]
    projected_at: AwareDatetime | None
    ledger_cursor: int = Field(ge=0)
    items: tuple[MaterialRequestReservationOptionOut, ...] = Field(max_length=100)


__all__ = [
    "MaterialRequestReservationOptionOut",
    "MaterialRequestReservationOptionPageOut",
    "MaterialRequestReservationSerialOptionOut",
]
