"""Warehouse receipt input basis. No inventory posting or public route yet."""
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime
from .material_request_my_receipt_schemas import StrictModel


class RejectionReceivingSerialOut(StrictModel):
    serial_id: UUID
    serial_no: str


class RejectionReceivingOut(StrictModel):
    return_id: UUID
    return_no: str
    request_id: UUID
    request_version: int
    registration_request_hash: str
    handover_id: UUID
    handover_request_hash: str
    handed_over_at: AwareDatetime
    carrier: str
    tracking_no: str
    quantity: str
    material_id: UUID
    sku_code: str
    material_name: str
    base_unit: str
    lot_id: UUID | None
    source_condition: Literal['new', 'used', 'damaged']
    original_exception: Literal['shortage', 'damaged', 'wrong_material', 'wrong_serial', 'rejected']
    serials: tuple[RejectionReceivingSerialOut, ...]
    target_location_id: UUID
    target_location_name: str
    custody_assignment_id: UUID
    receiver_person_id: UUID
    authorization_version: int
    status: Literal['awaiting_warehouse_acceptance'] = 'awaiting_warehouse_acceptance'
