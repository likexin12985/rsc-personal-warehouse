"""Exact subsequent non-posting commands; internal until the release gates pass."""
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from app.formal_services.stock_loss_corrections.request_contracts import (
    Digest, FactId, Reason, RequestCoordinates,
)


class ConditionDecision(RequestCoordinates):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')
    action: Literal['supplement', 'withdraw', 'verify_region', 'return_evidence',
        'reject_region', 'return_region', 'reject_hq', 'approve_hq', 'cancel_approved']
    case_id: FactId
    expected_event_id: FactId
    expected_event_hash: Digest
    reason: Reason
    evidence_file_ids: tuple[FactId, ...] = Field(default=(), max_length=20)

    @model_validator(mode='after')
    def exact_evidence(self):
        if len(set(self.evidence_file_ids)) != len(self.evidence_file_ids):
            raise ValueError('同一附件只能选择一次')
        if self.action in ('supplement', 'verify_region') and not self.evidence_file_ids:
            raise ValueError('补充证据或区域实物核实必须提供本次附件')
        return self


def validate_decision(request):
    if type(request) is not ConditionDecision:
        raise ValueError('an exact non-posting condition decision is required')
    return ConditionDecision.model_validate(request.model_dump(mode='python'))
