"""Source warehouse acceptance of one original refusal-return registration."""
from datetime import timezone
from uuid import UUID
from pydantic import AwareDatetime, Field, field_validator, model_validator
from .material_request_closure_schemas import MaterialRequestCloseIn
from .material_request_my_receipt_schemas import StrictModel
from .stock_return_receipt_schemas import ReceiptAmountsIn


class RejectionReceiptIn(MaterialRequestCloseIn):
    registration_request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    handover_id: UUID
    handover_request_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    custody_assignment_id: UUID
    received_at: AwareDatetime
    amounts: ReceiptAmountsIn
    observed_sku_code: str | None = Field(default=None,min_length=1,max_length=80)

    @field_validator('received_at', mode='before')
    @classmethod
    def explicit_time(cls, value):
        if isinstance(value, (int, float, bool)):
            raise ValueError('验收时间必须是带时区的明确时间')
        return value

    @field_validator('received_at')
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode='after')
    def canonical(self):
        if not self.handover_id.int or not self.custody_assignment_id.int:
            raise ValueError('必须绑定准确交运及保管责任')
        # Revalidate nested instances too; model_copy/model_construct cannot
        # bypass the established quantity/SN/exception contract.
        self.amounts = ReceiptAmountsIn.model_validate(self.amounts.model_dump())
        if bool(self.amounts.accepted_qty) != (self.observed_sku_code is not None):
            raise ValueError('接受实物必须明确记录核对到的SKU；无接受数量不能填写')
        if self.observed_sku_code is not None and self.observed_sku_code != self.observed_sku_code.strip():
            raise ValueError('实物SKU必须为准确编码')
        self.amounts.accepted_serial_verifications = tuple(sorted(self.amounts.accepted_serial_verifications, key=lambda x: str(x.serial_id)))
        for name in ('damaged_serial_ids', 'rejected_serial_ids', 'shortage_serial_ids'):
            setattr(self.amounts, name, tuple(sorted(getattr(self.amounts, name), key=str)))
        self.amounts.exceptions = tuple(sorted(self.amounts.exceptions, key=lambda x: x.exception_type))
        return self


class RejectionReceiptOut(StrictModel):
    receipt_id: UUID
    return_id: UUID
    request_id: UUID
    request_version: int
    receiver_person_id: UUID
    target_location_id: UUID
    custody_assignment_id: UUID
    handover_id: UUID
    received_at: AwareDatetime
    recorded_at: AwareDatetime
    amounts: ReceiptAmountsIn
    reason: str
    observed_sku_code: str | None
    request_hash: str
    replayed: bool
