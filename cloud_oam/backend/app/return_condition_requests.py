"""Internal correction commands; no endpoint or production activation."""
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from app.work_order_material_schemas import SerialVerificationIn
from app.formal_services.stock_loss_corrections.request_contracts import Digest, FactId, Reason, RequestCoordinates


class ConditionSubmit(RequestCoordinates):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')
    action: Literal['submit_return_condition']
    inbound_line_id: FactId
    expected_source_hash: Digest
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=3, allow_inf_nan=False)
    serial_verifications: tuple[SerialVerificationIn, ...] = Field(default=(), max_length=1000)
    evidence_file_ids: tuple[FactId, ...] = Field(min_length=1, max_length=20)
    reason: Reason

    @field_validator('quantity', mode='before')
    @classmethod
    def exact_decimal(cls, value):
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError('数量必须使用准确十进制字符串或整数')
        return value

    @field_validator('evidence_file_ids', 'serial_verifications')
    @classmethod
    def unique_references(cls, values):
        ids = [getattr(item, 'serial_id', item) for item in values]
        if len(ids) != len(set(ids)):
            raise ValueError('同一附件或序列号只能选择一次')
        return values


def validate_submit(request):
    if type(request) is not ConditionSubmit:
        raise ValueError('an exact condition submission command is required')
    return ConditionSubmit.model_validate(request.model_dump(mode='python'))
