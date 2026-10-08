"""Original-recipient rejection returns, independent of physical return and inbound."""
from decimal import Decimal
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from .material_request_closure_schemas import MaterialRequestCloseIn
from .material_request_remainder_schemas import Quantity
from .material_request_my_receipt_schemas import StrictModel


class RejectionReturnIn(MaterialRequestCloseIn):
    receipt_id: UUID
    receipt_line_id: UUID
    receipt_request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    quantity: Quantity
    serial_ids: tuple[UUID, ...] = Field(max_length=1000)

    @model_validator(mode='after')
    def identities(self):
        if (not self.receipt_id.int or not self.receipt_line_id.int or Decimal(self.quantity) <= 0
                or any(not sid.int for sid in self.serial_ids) or len(set(self.serial_ids)) != len(self.serial_ids)):
            raise ValueError('return requires original receipt identities, positive quantity and distinct serials')
        self.serial_ids = tuple(sorted(self.serial_ids, key=str))
        return self


class RejectionReturnOut(StrictModel):
    schema_version: Literal['1.0'] = '1.0'
    return_id: UUID
    return_no: str
    request_id: UUID
    request_version: int
    receipt_id: UUID
    receipt_line_id: UUID
    receipt_request_hash: str
    quantity: Quantity
    serial_ids: tuple[UUID, ...]
    status: Literal['registered'] = 'registered'
    registered_at: AwareDatetime
    request_hash: str
    replayed: bool
