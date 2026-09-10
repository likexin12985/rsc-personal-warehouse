from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

class ShipmentLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outbound_posting_id: UUID
    shipped_qty: Decimal = Field(gt=0)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)

    @field_validator("shipped_qty", mode="before")
    @classmethod
    def quantity(cls, value):
        value = Decimal(str(value))
        if value <= 0 or value.as_tuple().exponent < -3:
            raise ValueError("shipped quantity must be a positive number with at most 3 decimals")
        return value

    @field_validator("serial_ids")
    @classmethod
    def unique_serials(cls, value):
        if len(set(value)) != len(value): raise ValueError("duplicate serial")
        return value

class ShipmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_request_version: StrictInt = Field(ge=1)
    target_location_id: UUID
    target_person_id: UUID | None = None
    carrier: str = Field(min_length=1, max_length=100)
    tracking_no: str = Field(min_length=1, max_length=100)
    shipped_at: str
    lines: tuple[ShipmentLineIn, ...] = Field(min_length=1, max_length=100)

class ShipmentLineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shipment_line_id: UUID
    outbound_posting_id: UUID
    shipped_qty: str
    serial_ids: tuple[UUID, ...]

class ShipmentOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    shipment_id: UUID
    shipment_no: str
    request_id: UUID
    status: str
    target_location_id: UUID
    target_person_id: UUID | None
    carrier: str
    tracking_no: str
    shipped_at: str
    lines: tuple[ShipmentLineOut, ...]
    idempotency_replayed: bool = False

class ShipmentOptionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    request_version: int
    items: tuple[dict, ...]
