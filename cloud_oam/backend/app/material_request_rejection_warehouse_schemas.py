"""Source-custodian public views, keeping acceptance and posting independent."""
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import Field, StrictInt, model_validator
from .material_request_my_receipt_schemas import StrictModel
from .material_request_remainder_schemas import Quantity
from .material_request_rejection_receiving_schemas import RejectionReceivingOut, RejectionReceivingSerialOut
from .material_request_rejection_receipt_schemas import RejectionReceiptOut
from .material_request_rejection_inbound_schemas import RejectionInboundOut


class RejectionWarehouseSourceOut(RejectionReceivingOut):
    status: Literal['handed_over'] = 'handed_over'
    tracking_mode: Literal['none', 'lot', 'serial', 'lot_and_serial']
    quantity_scale: StrictInt = Field(ge=0, le=3)
    allow_fraction: bool


class RejectionWarehouseReceiptOut(StrictModel):
    receipt: RejectionReceiptOut
    inbound: RejectionInboundOut | None
    post_permitted: bool


class RejectionWarehouseDetailOut(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    source: RejectionWarehouseSourceOut
    accepted_qty: Quantity
    rejected_qty: Quantity
    damaged_qty: Quantity
    unconfirmed_qty: Quantity
    posted_qty: Quantity
    pending_inbound_qty: Quantity
    unconfirmed_serials: tuple[RejectionReceivingSerialOut, ...]
    receive_permitted: bool
    receipts: tuple[RejectionWarehouseReceiptOut, ...] = Field(max_length=1000)

    @model_validator(mode='after')
    def coherent(self):
        accepted = sum((r.receipt.amounts.accepted_qty for r in self.receipts), Decimal(0))
        rejected = sum((r.receipt.amounts.rejected_qty for r in self.receipts), Decimal(0))
        damaged = sum((r.receipt.amounts.damaged_qty for r in self.receipts), Decimal(0))
        posted = sum((r.receipt.amounts.accepted_qty for r in self.receipts if r.inbound), Decimal(0))
        ids = [r.receipt.receipt_id for r in self.receipts]
        used = [sid for r in self.receipts for sid in (
            tuple(s.serial_id for s in r.receipt.amounts.accepted_serial_verifications) + r.receipt.amounts.rejected_serial_ids)]
        if (len(set(ids)) != len(ids) or len(set(used)) != len(used)
                or accepted != Decimal(self.accepted_qty) or rejected != Decimal(self.rejected_qty)
                or damaged != Decimal(self.damaged_qty) or posted != Decimal(self.posted_qty)
                or accepted + rejected + Decimal(self.unconfirmed_qty) != Decimal(self.source.quantity)
                or accepted - posted != Decimal(self.pending_inbound_qty)
                or (self.receive_permitted and Decimal(self.unconfirmed_qty) <= 0)):
            raise ValueError('warehouse acceptance and posting quantities mismatch')
        if self.unconfirmed_serials != tuple(s for s in self.source.serials if s.serial_id not in used):
            raise ValueError('unconfirmed serials mismatch')
        for row in self.receipts:
            r, i = row.receipt, row.inbound
            if (r.return_id != self.source.return_id or r.request_id != self.source.request_id
                    or r.request_version > self.source.request_version
                    or (row.post_permitted and (i is not None or r.amounts.accepted_qty <= 0))
                    or (i is not None and (i.return_id != r.return_id or i.receipt_id != r.receipt_id
                        or i.request_id != r.request_id or i.target_location_id != r.target_location_id))):
                raise ValueError('warehouse history binding mismatch')
        return self


class RejectionWarehouseInboxItemOut(StrictModel):
    return_id: UUID
    verification_status: Literal['verified', 'blocked']
    message: str
    detail: RejectionWarehouseDetailOut | None

    @model_validator(mode='after')
    def coherent(self):
        if (self.verification_status == 'verified') != (self.detail is not None):
            raise ValueError('verified warehouse source required')
        if self.detail is not None and self.detail.source.return_id != self.return_id:
            raise ValueError('warehouse source binding mismatch')
        return self


class RejectionWarehouseInboxOut(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    person_id: UUID
    authorization_version: StrictInt = Field(gt=0)
    items: tuple[RejectionWarehouseInboxItemOut, ...] = Field(max_length=20)
    next_after_id: UUID | None


class RejectionWarehousePartOut(StrictModel):
    condition_code: Literal['new', 'used', 'damaged']
    quantity: Quantity
    serials: tuple[RejectionReceivingSerialOut, ...]


class RejectionWarehousePreviewOut(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    return_id: UUID
    receipt_id: UUID
    request_id: UUID
    request_version: StrictInt = Field(gt=0)
    person_id: UUID
    authorization_version: StrictInt = Field(gt=0)
    receipt_request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    plan_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    target_location_id: UUID
    target_location_name: str
    sku_code: str
    material_name: str
    parts: tuple[RejectionWarehousePartOut, ...] = Field(min_length=1, max_length=2)


class RejectionWarehouseReceiptStatusOut(StrictModel):
    lookup_status: Literal['confirmed', 'not_observed']
    command: RejectionReceiptOut | None

    @model_validator(mode='after')
    def coherent(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('lookup mismatch')
        return self


class RejectionWarehouseInboundStatusOut(StrictModel):
    lookup_status: Literal['confirmed', 'not_observed']
    command: RejectionInboundOut | None

    @model_validator(mode='after')
    def coherent(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('lookup mismatch')
        return self
