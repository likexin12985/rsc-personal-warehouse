"""Recipient commands never accept a caller-selected receiver or stock account."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class MyReceiptLineIn(StrictModel):
    shipment_line_id: UUID
    accepted_qty: Decimal
    rejected_qty: Decimal
    condition: Literal["normal", "shortage", "damaged", "wrong_material", "wrong_serial", "rejected"]
    accepted_serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    rejected_serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    exception_evidence_file_id: UUID | None = None

    @field_validator("accepted_qty", "rejected_qty", mode="before")
    @classmethod
    def quantity(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("数量必须使用精确十进制")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("数量必须使用精确十进制") from exc
        if not number.is_finite() or number < 0 or number >= Decimal("1000000000000000") or number.as_tuple().exponent < -3:
            raise ValueError("数量超出 numeric(18,3) 范围")
        return number.quantize(Decimal("0.001"))

    @model_validator(mode="after")
    def coherent(self):
        serials = self.accepted_serial_ids + self.rejected_serial_ids
        if len(set(serials)) != len(serials) or self.accepted_qty + self.rejected_qty <= 0:
            raise ValueError("验收数量或 SN 重复")
        if self.condition == "normal" and self.rejected_qty:
            raise ValueError("拒收必须注明异常类型")
        if self.condition in {"damaged", "wrong_material", "wrong_serial", "rejected"} and self.accepted_qty:
            raise ValueError("破损、错料、错 SN 或拒收不能登记为合格数量")
        if self.condition != "normal" and self.exception_evidence_file_id is None:
            raise ValueError("异常验收必须提供证据文件")
        return self


class MyReceiptIn(StrictModel):
    expected_request_version: StrictInt = Field(ge=1)
    shipment_id: UUID
    received_at: str
    lines: tuple[MyReceiptLineIn, ...] = Field(min_length=1, max_length=100)

    @field_validator("received_at")
    @classmethod
    def aware_time(cls, value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("验收时间必须带时区")
        return value

    @model_validator(mode="after")
    def unique_lines(self):
        ids = [line.shipment_line_id for line in self.lines]
        serials = [s for line in self.lines for s in line.accepted_serial_ids + line.rejected_serial_ids]
        if len(ids) != len(set(ids)) or len(serials) != len(set(serials)):
            raise ValueError("验收明细或 SN 重复")
        return self


class MyReceiptLineOut(MyReceiptLineIn):
    receipt_line_id: UUID


class MyReceiptOut(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    person_id: UUID
    receipt_id: UUID
    receipt_no: str
    shipment_id: UUID
    received_at: datetime
    status: Literal["accepted", "exception"]
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    lines: tuple[MyReceiptLineOut, ...]
    idempotency_replayed: bool


class MyReceiptCommandStatusOut(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    lookup_status: Literal["confirmed", "not_observed"]
    command: MyReceiptOut | None = None
