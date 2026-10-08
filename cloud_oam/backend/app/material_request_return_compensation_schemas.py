"""Cancel demand against one independently posted warehouse return, not stock."""
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, AwareDatetime, model_validator

from .material_request_closure_schemas import MaterialRequestCloseIn
from .material_request_remainder_schemas import Quantity, RemainderLineOut, RemainderAssessmentOut, BUCKETS


class ReturnCompensationIn(MaterialRequestCloseIn):
    inbound_id: UUID
    inbound_request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    inbound_plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    cancelled_qty: Quantity

    @model_validator(mode='after')
    def positive(self):
        if self.inbound_id.int == 0 or Decimal(self.cancelled_qty) <= 0:
            raise ValueError('return compensation requires an exact positive posted inbound')
        return self


class ReturnCompensationOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    cancellation_scope: Literal['posted_return_compensation'] = 'posted_return_compensation'
    compensation_id: UUID
    inbound_id: UUID
    request_id: UUID
    revision_id: UUID
    request_line_id: UUID
    request_version: StrictInt = Field(gt=0)
    cancelled_qty: Quantity
    request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    cancelled_at: AwareDatetime
    replayed: StrictBool = False

    @model_validator(mode='after')
    def identities(self):
        if any(v.int == 0 for v in (self.compensation_id, self.inbound_id, self.request_id,
                                   self.revision_id, self.request_line_id)) or Decimal(self.cancelled_qty) <= 0:
            raise ValueError('compensation requires exact identities and a positive quantity')
        return self


class ReturnedRemainderLineOut(RemainderLineOut):
    """Version 2 partition; version 1 remains unchanged for original 0171 seals."""
    unfulfilled_cancelled_qty: Quantity
    return_compensated_qty: Quantity
    returned_pending_compensation_qty: Quantity

    @model_validator(mode='after')
    def conserve(self):
        # Override the original seven-bucket validator, not its stored contract.
        if (self.request_line_id.int == 0
                or Decimal(self.cancelled_qty) != Decimal(self.unfulfilled_cancelled_qty) + Decimal(self.return_compensated_qty)
                or Decimal(self.approved_qty) != sum((Decimal(getattr(self, k)) for k in
                    ('cancelled_qty', 'posted_qty', *BUCKETS, 'returned_pending_compensation_qty')), Decimal(0))):
            raise ValueError('returned fulfillment quantities must conserve each approved line')
        return self


class ReturnedRemainderAssessmentOut(RemainderAssessmentOut):
    schema_version: Literal['2.0'] = '2.0'
    lines: tuple[ReturnedRemainderLineOut, ...] = Field(min_length=1, max_length=200)


def require_settled_returned_remainder(assessment, requested):
    """Cancel unfulfilled demand only after every returned item was compensated."""
    assessment = ReturnedRemainderAssessmentOut.model_validate(assessment)
    if assessment.open_supply_tasks or assessment.pending_substitutions:
        raise ValueError('开放供给任务或替代料建议尚未处理')
    if any(Decimal(getattr(row, key)) for row in assessment.lines
           for key in (*BUCKETS[1:], 'returned_pending_compensation_qty')):
        raise ValueError('占用、在途、待入账或退回补偿尚未完成')
    if any(Decimal(row.unfulfilled_cancelled_qty) for row in assessment.lines):
        raise ValueError('已存在未履约取消，请先核验原取消记录')
    if not any(Decimal(row.return_compensated_qty) for row in assessment.lines):
        raise ValueError('无退回补偿时应保留原七分区取消证据')
    expected = {row.request_line_id: row.unreserved_qty for row in assessment.lines if Decimal(row.unreserved_qty)>0}
    if not expected or expected != {row.request_line_id: row.cancelled_qty for row in requested}:
        raise ValueError('提交明细必须准确覆盖全部剩余未履约数量')
    return assessment


class ReturnCompensationStatusOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lookup_status: Literal['confirmed', 'not_observed']
    command: ReturnCompensationOut | None

    @model_validator(mode='after')
    def coherent(self):
        if (self.lookup_status == 'confirmed') != (self.command is not None):
            raise ValueError('compensation lookup must agree with its original command')
        return self


class ReturnCompensationCandidateOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inbound_id: UUID
    request_line_id: UUID
    inbound_request_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    inbound_plan_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    quantity: Quantity
    sku_code: str = Field(min_length=1)
    material_name: str = Field(min_length=1)
    posted_at: AwareDatetime
    compensate_permitted: StrictBool
    compensation: ReturnCompensationOut | None

    @model_validator(mode='after')
    def coherent(self):
        if self.inbound_id.int == 0 or self.request_line_id.int == 0 or Decimal(self.quantity) <= 0:
            raise ValueError('posted return candidate requires exact identities and quantity')
        if self.compensation is not None and (self.compensate_permitted
                or self.compensation.inbound_id != self.inbound_id
                or self.compensation.request_line_id != self.request_line_id
                or self.compensation.cancelled_qty != self.quantity):
            raise ValueError('candidate must match its compensation')
        return self


class ReturnCompensationCandidatesOut(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['1.0'] = '1.0'
    request_id: UUID
    request_version: StrictInt = Field(ge=0)
    items: tuple[ReturnCompensationCandidateOut, ...] = Field(max_length=1000)

    @model_validator(mode='after')
    def coherent(self):
        if self.request_version == 0 and self.items:
            raise ValueError('a draft cannot have posted return candidates')
        if self.request_id.int == 0 or len({r.inbound_id for r in self.items}) != len(self.items):
            raise ValueError('return candidates must be unique within one request')
        if any(r.compensation and (r.compensation.request_id != self.request_id
                or r.compensation.request_version > self.request_version) for r in self.items):
            raise ValueError('candidate compensation belongs to another request or future version')
        return self
