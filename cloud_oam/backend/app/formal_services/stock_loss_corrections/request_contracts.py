"""Strict candidate commands and complete original-request fingerprints.

No route, stock posting or retry policy is installed by this module. Recovery
must receive the full original command; a missing result never grants a replay.
Quantities, accounts, serials and actors are derived from persisted facts.
"""
from dataclasses import dataclass
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, ConfigDict, Field, field_validator

from app.work_order_material_schemas import StrictInput
from app.formal_services import stock_loss_sources as sources, inventory_posting as posting


def _identifier(value):
    if value.int == 0:
        raise ValueError('a nonzero fact identifier is required')
    return value


def _reason(value):
    value = value.strip()
    if not 1 <= len(value) <= 500 or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError('a meaningful reason without control characters is required')
    value.encode('utf-8')
    return value


FactId = Annotated[UUID, AfterValidator(_identifier)]
Digest = Annotated[str, Field(strict=True, min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')]
Reason = Annotated[str, Field(strict=True, max_length=500), AfterValidator(_reason)]
Disposition = Literal['restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap']


class RootBinding(StrictInput):
    model_config = ConfigDict(extra='forbid', frozen=True, revalidate_instances='always')
    root_disposition_id: FactId
    expected_root_request_hash: Digest
    expected_submission_plan_hash: Digest
    reason: Reason


class ReversalPreview(RootBinding):
    # Explicit null means reverse the original root, never "choose the latest".
    reversed_correction_id: FactId | None
    expected_execution_request_hash: Digest


class RequestCoordinates(StrictInput):
    request_id: str = Field(strict=True, min_length=8, max_length=160)
    idempotency_key: str = Field(strict=True, min_length=8, max_length=200)

    @field_validator('request_id', 'idempotency_key')
    @classmethod
    def safe_coordinate(cls, value):
        if re.fullmatch(r'[A-Za-z0-9._:-]+', value) is None:
            raise ValueError('request coordinates must be exact safe strings')
        return value


class ReversalExecute(ReversalPreview, RequestCoordinates):
    expected_plan_hash: Digest


class InverseBinding(RootBinding):
    reversal_id: FactId
    expected_reversal_hash: Digest


class CorrectionApprove(InverseBinding, RequestCoordinates):
    disposition: Disposition


class CorrectionPreview(InverseBinding):
    correction_decision_id: FactId
    expected_correction_decision_hash: Digest


class CorrectionExecute(CorrectionPreview, RequestCoordinates):
    expected_plan_hash: Digest


ACTIONS = {
    ReversalPreview: 'reverse_loss', ReversalExecute: 'reverse_loss',
    CorrectionApprove: 'approve_loss_correction',
    CorrectionPreview: 'correct_loss', CorrectionExecute: 'correct_loss',
}
COMMANDS = (ReversalExecute, CorrectionApprove, CorrectionExecute)


def validate(request):
    # model_copy(update=...) bypasses Pydantic validation. Every service entry
    # parses again, including already-constructed candidate models.
    if type(request) not in ACTIONS:
        raise ValueError('an explicit correction request type is required')
    return type(request).model_validate(request.model_dump(mode='python'))


@dataclass(frozen=True)
class OriginalRequest:
    action: str
    actor_user_id: str
    actor_person_id: UUID
    request_id: str
    key_hash: str
    request_hash: str
    document_json: str

    @property
    def document(self):
        return json.loads(self.document_json)


def original_request(*, actor, request):
    request = validate(request)
    if type(request) not in COMMANDS:
        raise ValueError('a preview has no original write request')
    body = request.model_dump(mode='json')
    key = body.pop('idempotency_key')
    request_id = body.pop('request_id')
    plan_hash = body.pop('expected_plan_hash', None)
    action = ACTIONS[type(request)]
    document = dict(schema_version=1, action=action, intent=body, request_id=request_id)
    if plan_hash is not None:
        document['expected_plan_hash'] = plan_hash
    return OriginalRequest(action, actor.user_id, actor.person_id, request_id,
        posting._storage_hash('stock-loss:' + action + ':' + key), sources._hash(document),
        json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


def require_original_row(*, row, actor, request):
    """Match every original coordinate; never turn a collision into a replay.

    This is request identity only, not historical business proof or current
    read authorization. Later recovery composes all three independently.
    """
    request = validate(request)
    binding = original_request(actor=actor, request=request)
    if (row.actor_user_id != binding.actor_user_id or row.actor_person_id != binding.actor_person_id
            or row.request_id != binding.request_id or row.idempotency_key_hash != binding.key_hash
            or row.request_hash != binding.request_hash or row.command_jsonb != binding.document
            or sources._hash(row.command_jsonb) != binding.request_hash
            or row.root_disposition_id != request.root_disposition_id
            or row.reason != request.reason):
        sources._fail('stock_loss_correction_request_conflict', '原请求已绑定其他内容，请回查完整原请求')
    return binding
