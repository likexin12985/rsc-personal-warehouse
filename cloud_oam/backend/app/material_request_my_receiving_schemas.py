"""Recipient-only package progress. No source accounts or writable commands."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ReceivingLineOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shipment_line_id: UUID
    request_line_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    shipped_qty: str
    accepted_qty: str
    rejected_qty: str
    unconfirmed_qty: str
    has_exception: bool


class ReceivingPackageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shipment_id: UUID
    shipment_no: str
    shipment_status: Literal["pending_handover", "shipped", "in_transit", "exception"]
    carrier: str
    tracking_no: str
    shipped_at: datetime
    target_location_id: UUID
    target_location_name: str
    lines: tuple[ReceivingLineOut, ...]


class MyReceivingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_no: str
    request_version: int
    person_id: UUID
    packages: tuple[ReceivingPackageOut, ...]
    next_after_id: UUID | None
