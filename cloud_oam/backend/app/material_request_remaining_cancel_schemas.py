"""Explicit cancellation of all remaining, settled demand; not inventory release."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, AwareDatetime, model_validator

from .material_request_closure_schemas import MaterialRequestCloseIn
from .material_request_remainder_schemas import Quantity, RemainderAssessmentOut, BUCKETS


class RemainingCancelLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_line_id: UUID
    cancelled_qty: Quantity

    @model_validator(mode='after')
    def positive(self):
        if self.request_line_id.int == 0 or Decimal(self.cancelled_qty) <= 0:
            raise ValueError('remaining cancellation requires a positive quantity and line identity')
        return self


class MaterialRequestCancelRemainingIn(MaterialRequestCloseIn):
    lines: tuple[RemainingCancelLine, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def unique_lines(self):
        if len({row.request_line_id for row in self.lines}) != len(self.lines):
            raise ValueError('a cancellation line may appear only once')
        return self


def require_settled_remainder(assessment, requested):
    """The caller must first prove these quantities from immutable history."""
    assessment = RemainderAssessmentOut.model_validate(assessment)
    if assessment.open_supply_tasks or assessment.pending_substitutions:
        raise ValueError('开放供给任务或替代料建议尚未处理')
    if any(Decimal(getattr(row, key)) for row in assessment.lines for key in BUCKETS[1:]):
        raise ValueError('占用、在途、待入账或拒收数量尚未完成释放或补偿')
    if any(Decimal(row.cancelled_qty) for row in assessment.lines):
        raise ValueError('已存在取消数量，请先核验原取消记录')
    remaining = {row.request_line_id: row.unreserved_qty for row in assessment.lines
                 if Decimal(row.unreserved_qty) > 0}
    if not remaining or {row.request_line_id: row.cancelled_qty for row in requested} != remaining:
        raise ValueError('提交明细必须准确覆盖全部剩余未履约数量')
    return assessment


class MaterialRequestRemainingCancellationOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    cancellation_id: UUID
    request_id: UUID
    revision_id: UUID
    request_version: StrictInt = Field(gt=0)
    cancellation_scope: Literal['all_remaining_unfulfilled'] = 'all_remaining_unfulfilled'
    cancelled_at: AwareDatetime
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    lines: tuple[RemainingCancelLine, ...] = Field(min_length=1, max_length=200)
    replayed: StrictBool = False

    @model_validator(mode='after')
    def identities(self):
        if any(value.int == 0 for value in (self.cancellation_id, self.request_id, self.revision_id)) \
                or len({row.request_line_id for row in self.lines}) != len(self.lines):
            raise ValueError('remaining cancellation requires unique, nonempty identities')
        return self


class MaterialRequestRemainingCancellationStatusOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['confirmed', 'not_observed']
    command: MaterialRequestRemainingCancellationOut | None

    @model_validator(mode='after')
    def consistent(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('cancellation recovery requires an exact command')
        if self.command is not None and not self.command.replayed:
            raise ValueError('recovered cancellation must be an existing fact')
        return self


class MaterialRequestRemainingCancellationStateOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: UUID
    request_version: StrictInt = Field(ge=0)
    cancel_permitted: StrictBool
    cancellation: MaterialRequestRemainingCancellationOut | None

    @model_validator(mode='after')
    def consistent(self):
        if self.request_id.int == 0:
            raise ValueError('request identity required')
        if self.cancellation is not None and (self.cancel_permitted
                or self.cancellation.request_id != self.request_id
                or self.cancellation.request_version != self.request_version):
            raise ValueError('cancellation state must match the request')
        return self
