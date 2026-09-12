"""Explicit recipient allowlist for acceptance-to-inbound confirmation."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from .material_request_my_receipt_schemas import StrictModel


class InboundSerialOut(StrictModel):
    serial_id: UUID
    serial_no: str


class InboundCandidateLineOut(StrictModel):
    receipt_line_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    accepted_qty: str
    rejected_qty: str
    accepted_serials: tuple[InboundSerialOut, ...]


class InboundCandidateDetailOut(StrictModel):
    receipt_no: str
    receipt_request_hash: str
    shipment_id: UUID
    shipment_no: str
    target_location_name: str
    received_at: datetime
    lines: tuple[InboundCandidateLineOut, ...]
    inbound_no: str | None
    inventory_transaction_id: UUID | None
    posted_at: datetime | None


class InboundCandidateOut(StrictModel):
    receipt_id: UUID
    status: Literal['pending', 'posted', 'no_accepted', 'blocked']
    message: str
    detail: InboundCandidateDetailOut | None


class MyInboundCandidatesOut(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    request_id: UUID
    request_no: str
    request_version: int
    person_id: UUID
    can_post: bool
    items: tuple[InboundCandidateOut, ...]
    next_after_id: UUID | None
