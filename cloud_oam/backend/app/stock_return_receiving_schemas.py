"""Recipient field allowlist; viewing a parcel is not acceptance or posting."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .stock_return_outbound_schemas import StockReturnOutboundSerialOut


class ReceivingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StockReturnReceivingLineOut(ReceivingModel):
    shipment_line_id: UUID
    outbound_no: str
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    condition_code: Literal["used", "damaged"]
    lot_id: UUID | None
    lot_no: str | None
    shipped_quantity: str
    serials: tuple[StockReturnOutboundSerialOut, ...]


class StockReturnReceivingPackageOut(ReceivingModel):
    verification_status: Literal["verified"] = "verified"
    shipment_id: UUID
    shipment_no: str
    operation_id: UUID
    operation_no: str
    work_order_id: UUID
    sender_person_id: UUID
    receiver_person_id: UUID
    target_location_id: UUID
    target_location_name: str
    custody_assignment_id: UUID
    carrier: str
    tracking_no: str
    shipped_at: datetime
    recorded_at: datetime
    lines: tuple[StockReturnReceivingLineOut, ...]


class StockReturnReceivingBlockedOut(ReceivingModel):
    verification_status: Literal["unavailable"] = "unavailable"
    shipment_id: UUID
    code: Literal["stock_return_receiving_verification_required"] = "stock_return_receiving_verification_required"
    message: str = "此包裹的原单据或接收责任未通过核验，请保留原记录处理。"


class StockReturnReceivingOut(ReceivingModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int
    ledger_cursor: int
    queried_at: datetime
    items: tuple[Annotated[StockReturnReceivingPackageOut | StockReturnReceivingBlockedOut,
        Field(discriminator="verification_status")], ...]
    next_after_id: UUID | None = None


class StockReturnReceivingDetailOut(ReceivingModel):
    schema_version: Literal["1.0"] = "1.0"
    person_id: UUID
    authorization_version: int
    ledger_cursor: int
    queried_at: datetime
    package: StockReturnReceivingPackageOut
