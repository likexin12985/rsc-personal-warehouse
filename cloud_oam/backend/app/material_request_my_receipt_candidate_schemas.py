"""Exact recipient acceptance candidates; no source inventory dimensions."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .material_request_my_receiving_schemas import ReceivingLineOut


class CandidateSerialOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serial_id: UUID
    serial_no: str
    qr_code: str


class CandidateLineOut(ReceivingLineOut):
    lot_no: str | None
    tracking_mode: Literal["none", "lot", "serial", "lot_and_serial"]
    quantity_scale: int = Field(ge=0, le=3)
    allow_fraction: bool
    remaining_serials: tuple[CandidateSerialOut, ...]


class MyReceiptCandidateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_no: str
    request_version: int
    person_id: UUID
    shipment_id: UUID
    shipment_no: str
    shipped_at: datetime
    target_location_name: str
    checked_at: datetime
    can_receive: bool
    blocked_reason: Literal["permission_required", "request_not_approved", "pending_handover", "complete"] | None
    lines: tuple[CandidateLineOut, ...]
