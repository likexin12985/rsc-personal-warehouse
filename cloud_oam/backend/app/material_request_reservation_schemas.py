"""Strict HTTP contracts for the first material-request reservation slice.

The public command only creates a ``reserved`` fact.  Release, picking,
outbound and shipment each have their own reviewed command boundary and are
therefore intentionally absent from this DTO.
"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .demand_schemas import MaterialRequestStateAxesOut


_DECIMAL = re.compile(r"^(0|[1-9]\d*)(?:\.\d{1,3})?$")
_ZERO_UUID = UUID(int=0)


def _uuid(value: UUID, name: str) -> UUID:
    if value == _ZERO_UUID:
        raise ValueError(f"{name} cannot be the zero UUID")
    return value


def _quantity(value: object) -> Decimal:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ValueError("reserved_qty must be a canonical decimal string")
    parsed = Decimal(value)
    if parsed <= 0 or parsed >= Decimal("1000000000000000"):
        raise ValueError("reserved_qty must be positive and within range")
    return parsed


class ReservationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_request_version: StrictInt = Field(ge=0)
    allocation_id: UUID
    request_line_id: UUID
    reserved_qty: Annotated[Decimal, Field(gt=0)]
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)

    @field_validator("allocation_id", "request_line_id")
    @classmethod
    def validate_ids(cls, value: UUID, info) -> UUID:
        return _uuid(value, info.field_name)

    @field_validator("reserved_qty", mode="before")
    @classmethod
    def validate_quantity(cls, value: object) -> Decimal:
        return _quantity(value)

    @field_validator("serial_ids")
    @classmethod
    def validate_serials(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        checked = tuple(_uuid(item, "serial_id") for item in value)
        if len(set(checked)) != len(checked):
            raise ValueError("serial_ids cannot contain duplicates")
        return checked


class ReservationMutationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    reservation_id: UUID
    reservation_no: str = Field(min_length=1, max_length=100)
    request_version: int = Field(ge=0)
    current_request_version: int | None = Field(default=None, ge=0)
    revision_id: UUID
    revision_no: int = Field(ge=1)
    request_line_id: UUID
    allocation_id: UUID
    source_stock_account_id: UUID
    stock_account_id: UUID
    reserve_transaction_id: UUID
    reserve_transaction_no: str = Field(min_length=1, max_length=100)
    reserved_qty: str = Field(min_length=1, max_length=32)
    reservation_status: Literal["reserved"]
    request_status: str = Field(min_length=1, max_length=32)
    state_axes: MaterialRequestStateAxesOut
    source_balance_version: StrictInt = Field(ge=0)
    source_ledger_cursor: StrictInt = Field(ge=0)
    serial_ids: tuple[UUID, ...] = Field(max_length=1000)
    idempotency_replayed: bool = False

    @field_validator("serial_ids")
    @classmethod
    def validate_serials(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        checked = tuple(_uuid(item, "serial_id") for item in value)
        if len(set(checked)) != len(checked):
            raise ValueError("serial_ids cannot contain duplicates")
        return checked

    @field_validator("reserved_qty")
    @classmethod
    def validate_output_quantity(cls, value: str) -> str:
        if re.fullmatch(r"(?:0|[1-9]\d{0,14})\.\d{3}", value) is None or Decimal(value) <= 0:
            raise ValueError("reserved_qty must be a fixed-scale positive decimal")
        return value

    @model_validator(mode="after")
    def validate_state_anchor(self):
        if self.serial_ids and Decimal(self.reserved_qty) != Decimal(len(self.serial_ids)):
            raise ValueError("serial_ids must cover the exact reserved quantity")
        if self.request_status != self.state_axes.request_status:
            raise ValueError("request_status must match state_axes.request_status")
        # One reservation fact can cover only part of the approved demand.
        # The fact itself is always ``reserved``; the aggregate axis remains
        # ``pending`` until every current approved line is covered.
        if self.state_axes.reservation_status not in {"pending", "reserved"}:
            raise ValueError("reservation state axis must be pending or reserved")
        return self


class ReservationCommandStatusOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["not_observed", "confirmed"]
    command: ReservationMutationOut | None

    @model_validator(mode="after")
    def validate_lookup(self):
        if (self.lookup_status == "confirmed") != (self.command is not None):
            raise ValueError("reservation lookup status and command disagree")
        return self


__all__ = [
    "ReservationCommandStatusOut",
    "ReservationCreateIn",
    "ReservationMutationOut",
]
