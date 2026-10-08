"""Exact settlement intent; quantity and target come only from retained facts."""
from typing import Literal
from pydantic import ConfigDict, Field, field_validator
from app.work_order_material_schemas import SerialVerificationIn
from app.formal_services.stock_loss_corrections.request_contracts import Digest, FactId, Reason, RequestCoordinates


class ConditionSettlement(RequestCoordinates):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')
    action: Literal['execute','release']
    case_id: FactId
    expected_event_id: FactId
    expected_event_hash: Digest
    reason: Reason
    serial_verifications: tuple[SerialVerificationIn,...] = Field(default=(),max_length=1000)
    evidence_file_ids: tuple[FactId,...] = Field(default=(),max_length=20)

    @field_validator('serial_verifications','evidence_file_ids')
    @classmethod
    def exact_references(cls, values):
        identifiers=[getattr(item,'serial_id',item) for item in values]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError('同一序列号或附件只能选择一次')
        return values


def validate_settlement(request):
    if type(request) is not ConditionSettlement:
        raise ValueError('an exact condition settlement command is required')
    return ConditionSettlement.model_validate(request.model_dump(mode='python'))
