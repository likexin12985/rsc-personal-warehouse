from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

RECEIPT_CONDITIONS = frozenset({"normal", "shortage", "damaged", "wrong_material", "wrong_serial", "rejected"})

class ReceiptLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shipment_line_id: UUID
    accepted_qty: Decimal = Field(ge=0)
    rejected_qty: Decimal = Field(ge=0)
    condition: str = Field(min_length=1, max_length=32)
    serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    exception_evidence_file_id: UUID | None = None
    @field_validator("condition")
    @classmethod
    def condition_code(cls, value):
        if value not in RECEIPT_CONDITIONS:
            raise ValueError("收货条件无效")
        return value
    @field_validator("accepted_qty", "rejected_qty", mode="before")
    @classmethod
    def quantity(cls, value):
        value = Decimal(str(value))
        if value.as_tuple().exponent < -3 or value < 0: raise ValueError("数量无效")
        return value

class ReceiptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_request_version: StrictInt = Field(ge=1)
    receiver_person_id: UUID
    received_at: str
    lines: tuple[ReceiptLineIn, ...] = Field(min_length=1, max_length=100)

class ReceiptOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    receipt_id: UUID
    receipt_no: str
    shipment_id: UUID
    status: str
    lines: tuple[dict, ...]
    exceptions: tuple[dict, ...] = ()
    idempotency_replayed: bool = False
