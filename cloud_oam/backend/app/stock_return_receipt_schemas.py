"""Exact parcel acceptance. Shortages remain unconfirmed; damage is accepted stock."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .stock_return_schemas import ReturnReason
from .stock_return_receiving_schemas import StockReturnReceivingPackageOut
from .stock_return_outbound_schemas import StockReturnOutboundSerialOut
from .work_order_material_schemas import SerialVerificationIn, StrictInput

ExceptionType = Literal["shortage", "damaged", "wrong_material", "wrong_serial", "rejected"]


class StockReturnReceiptExceptionIn(StrictInput):
    exception_type: ExceptionType
    description: str = Field(min_length=1, max_length=1000)
    evidence_file_id: UUID

    @field_validator("description")
    @classmethod
    def description_text(cls, value):
        value = value.strip()
        if not value: raise ValueError("异常说明不能为空")
        value.encode("utf-8")
        return value


class StockReturnReceiptLineIn(StrictInput):
    shipment_line_id: UUID
    accepted_qty: Decimal = Decimal(0)
    rejected_qty: Decimal = Decimal(0)
    damaged_qty: Decimal = Decimal(0)
    shortage_qty: Decimal = Decimal(0)
    accepted_serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)
    damaged_serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    rejected_serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    shortage_serial_ids: tuple[UUID, ...] = Field(default=(), max_length=1000)
    exceptions: tuple[StockReturnReceiptExceptionIn, ...] = Field(default=(), max_length=5)

    @field_validator("accepted_qty", "rejected_qty", "damaged_qty", "shortage_qty", mode="before")
    @classmethod
    def quantity(cls, value):
        if isinstance(value, (float, bool)): raise ValueError("验收数量必须为精确十进制")
        try: result = Decimal(str(value))
        except (ValueError, TypeError, InvalidOperation) as exc: raise ValueError("验收数量无效") from exc
        if not result.is_finite() or result < 0 or result >= Decimal("1000000000000000") or result.as_tuple().exponent < -3:
            raise ValueError("验收数量超出 numeric(18,3) 范围")
        return result.quantize(Decimal(".001"))

    @model_validator(mode="after")
    def coherent(self):
        accepted = tuple(row.serial_id for row in self.accepted_serial_verifications)
        ids = accepted + self.rejected_serial_ids + self.shortage_serial_ids
        types = [row.exception_type for row in self.exceptions]
        if len(ids) != len(set(ids)) or len(types) != len(set(types)):
            raise ValueError("验收 SN 或异常类型重复")
        if (len(set(self.damaged_serial_ids)) != len(self.damaged_serial_ids)
                or not set(self.damaged_serial_ids) <= set(accepted)):
            raise ValueError("破损 SN 必须属于本次已接受的准确 SN")
        if self.damaged_qty > self.accepted_qty or self.accepted_qty + self.rejected_qty + self.shortage_qty <= 0:
            raise ValueError("验收数量或破损数量不一致")
        if (bool(self.shortage_qty) != ("shortage" in types) or bool(self.damaged_qty) != ("damaged" in types)
                or bool(self.rejected_qty) != bool(set(types) & {"rejected", "wrong_material", "wrong_serial"})):
            raise ValueError("短少、破损及拒收数量必须各有对应的异常说明和证据")
        return self


class StockReturnReceiptPreviewIn(ReturnReason):
    operator_person_id: UUID
    received_at: datetime
    lines: tuple[StockReturnReceiptLineIn, ...] = Field(min_length=1, max_length=100)

    @field_validator("received_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None: raise ValueError("验收时刻必须带时区")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def unique_lines(self):
        ids = [row.shipment_line_id for row in self.lines]
        serials = [identifier for row in self.lines for identifier in (
            tuple(proof.serial_id for proof in row.accepted_serial_verifications) + row.rejected_serial_ids + row.shortage_serial_ids)]
        if len(ids) != len(set(ids)) or len(serials) != len(set(serials)): raise ValueError("整组验收明细或 SN 重复")
        return self


class StockReturnReceiptSubmitIn(StockReturnReceiptPreviewIn):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    request_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    idempotency_key: str = Field(min_length=8, max_length=200, pattern=r"^[A-Za-z0-9._:-]+$")


class ReceiptOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class StockReturnReceiptLineOut(ReceiptOutput):
    shipment_line_id: UUID
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal["used", "damaged"]
    lot_id: UUID | None
    lot_no: str | None
    shipped_qty: str
    previously_accepted_qty: str
    previously_rejected_qty: str
    unconfirmed_qty: str
    accepted_qty: str
    rejected_qty: str
    damaged_qty: str
    shortage_qty: str
    accepted_serials: tuple[StockReturnOutboundSerialOut, ...]
    damaged_serial_ids: tuple[UUID, ...]
    rejected_serials: tuple[StockReturnOutboundSerialOut, ...]
    shortage_serials: tuple[StockReturnOutboundSerialOut, ...]
    exceptions: tuple[StockReturnReceiptExceptionIn, ...]


class StockReturnReceiptPreviewOut(ReceiptOutput):
    schema_version: Literal["1.0"] = "1.0"
    planning_status: Literal["preview_only"] = "preview_only"
    shipment_id: UUID
    operation_id: UUID
    work_order_id: UUID
    operator_person_id: UUID
    authorization_version: int
    received_at: datetime
    reason: str
    checked_at: datetime
    ledger_cursor: int
    package: StockReturnReceivingPackageOut
    request_hash: str
    plan_hash: str
    lines: tuple[StockReturnReceiptLineOut, ...]


class StockReturnReceiptOut(ReceiptOutput):
    schema_version: Literal["1.0"] = "1.0"
    receipt_id: UUID
    receipt_no: str
    shipment_id: UUID
    operation_id: UUID
    work_order_id: UUID
    operator_person_id: UUID
    status: Literal["accepted", "exception"]
    received_at: datetime
    recorded_at: datetime
    reason: str
    request_id: str
    request_hash: str
    plan_hash: str
    target_location_id: UUID
    target_custody_assignment_id: UUID
    lines: tuple[StockReturnReceiptLineOut, ...]


class StockReturnReceiptSealOut(ReceiptOutput):
    seal_id: UUID
    operation_type: Literal['receive_return'] = 'receive_return'
    operator_person_id: UUID
    work_order_id: UUID
    operation_id: UUID
    shipment_id: UUID
    request_id: str
    request_hash: str
    sealed_at: datetime


class StockReturnReceiptSealedOut(ReceiptOutput):
    schema_version: Literal['1.0'] = '1.0'
    lookup_status: Literal['sealed'] = 'sealed'
    seal: StockReturnReceiptSealOut


class StockReturnReceiptProgressOut(ReceiptOutput):
    shipment_line_id: UUID
    shipped_qty: str
    accepted_qty: str
    rejected_qty: str
    damaged_qty: str
    unconfirmed_qty: str
    unconfirmed_serials: tuple[StockReturnOutboundSerialOut, ...]


class StockReturnReceiptHistoryOut(ReceiptOutput):
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: int
    ledger_cursor: int
    queried_at: datetime
    package: StockReturnReceivingPackageOut
    receipts: tuple[StockReturnReceiptOut, ...]
    lines: tuple[StockReturnReceiptProgressOut, ...]
