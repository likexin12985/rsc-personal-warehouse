"""Typed approval/execution closure evidence; never closes a request on read.

Candidate only until the forward native migration and key registry cover both
tables. Service checks cannot substitute for database commit exclusion.
"""
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.stock_loss_correction_models import (
    StockLossCorrectionApprovalSeal as ApprovalSeal,
    StockLossCorrectionExecutionSeal as ExecutionSeal,
    StockLossDispositionReversal as Inverse,
    StockLossCorrectionDecision as Decision,
)
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services import stock_loss_facts as facts
from app.formal_services.work_order_query import _aware
from .request_contracts import CorrectionApprove, CorrectionExecute, require_original_row


MODELS = {CorrectionApprove: ApprovalSeal, CorrectionExecute: ExecutionSeal}
KINDS = {
    ApprovalSeal: ('stock_loss_correction_approval_seal', 'stock_loss.correction_approval_request_sealed'),
    ExecutionSeal: ('stock_loss_correction_execution_seal', 'stock_loss.correction_execution_request_sealed'),
}


def _unknown():
    sources._fail('loss_correction_seal_evidence_invalid', '原纠正封存证据不完整，不能重发或补写', 503)


def _conflict():
    sources._fail('loss_correction_seal_conflict', '原请求坐标已绑定其他纠正封存，请核验完整原请求', 409)


def keys(request):
    return tuple(posting._storage_hash('stock-loss:' + action + ':' + request.idempotency_key)
                 for action in ('reverse_loss', 'approve_loss_correction', 'correct_loss'))


def _condition(model, actor, request):
    hashes = keys(request)
    return or_(model.reversal_key_hash.in_(hashes), model.approval_key_hash.in_(hashes),
               model.correction_key_hash.in_(hashes),
               (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id))


def payload(row):
    result = dict(root_disposition_id=str(row.root_disposition_id),
        reversal_id=str(row.reversal_id), expected_reversal_hash=row.expected_reversal_hash,
        disposition=row.disposition, actor_user_id=row.actor_user_id,
        actor_person_id=str(row.actor_person_id), authorization_version=row.authorization_version,
        request_id=row.request_id, request_reference=row.request_reference,
        reversal_key_hash=row.reversal_key_hash, approval_key_hash=row.approval_key_hash,
        correction_key_hash=row.correction_key_hash, request_hash=row.request_hash,
        reason=row.reason, command=row.command_jsonb, stock_effect='none')
    if type(row) is ExecutionSeal:
        result.update(correction_decision_id=str(row.correction_decision_id),
            expected_correction_decision_hash=row.expected_correction_decision_hash, plan_hash=row.plan_hash)
    return result


def find(db, *, actor, request):
    rows = []
    for model in KINDS:
        rows.extend(db.scalars(select(model).where(_condition(model, actor, request)).limit(3)
                              .execution_options(populate_existing=True)))
    if len(rows) > 1:
        _conflict()
    row = rows[0] if rows else None
    if row is not None:
        if type(row) is not MODELS.get(type(request)):
            _conflict()
        if row.actor_user_id != actor.user_id or row.actor_person_id != actor.person_id:
            sources._fail('loss_correction_not_found', '本人原纠正封存不存在', 404)
        require_original_row(row=row, actor=actor, request=request)
        if ((row.reversal_key_hash, row.approval_key_hash, row.correction_key_hash) != keys(request)
                or row.request_reference != posting._request_reference(request.request_id)
                or row.reversal_id != request.reversal_id
                or row.expected_reversal_hash != request.expected_reversal_hash):
            _conflict()
        if type(request) is CorrectionApprove:
            if row.disposition != request.disposition:
                _conflict()
        elif (row.correction_decision_id != request.correction_decision_id
                or row.expected_correction_decision_hash != request.expected_correction_decision_hash
                or row.plan_hash != request.expected_plan_hash):
            _conflict()
    # Orphan evidence must not turn into a clean miss and a new write.
    body = AuditEvent.after_jsonb
    reference = posting._request_reference(request.request_id)
    audits = tuple(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id).where(
        AuditEvent.aggregate_type.in_(tuple(v[0] for v in KINDS.values())),
        or_((AuditEvent.actor_user_id == actor.user_id) & or_(
                body['request_id'].as_string() == request.request_id,
                body['request_reference'].as_string() == reference),
            *(body[name].as_string().in_(keys(request)) for name in
              ('reversal_key_hash', 'approval_key_hash', 'correction_key_hash')))).limit(3)))
    expected = ((KINDS[type(row)][0], str(row.id)),) if row is not None else ()
    if audits != expected:
        _unknown()
    return row


def require_unsealed(db, *, actor, request):
    if find(db, actor=actor, request=request) is not None:
        sources._fail('loss_correction_request_sealed', '原纠正请求已永久封存，不能再次执行', 409)


def verify(db, *, actor, request, row, root):
    inverse = db.get(Inverse, row.reversal_id, populate_existing=True)
    if (inverse is None or inverse.root_disposition_id != root.id
            or inverse.request_hash != row.expected_reversal_hash
            or type(row.authorization_version) is not int or row.authorization_version <= 0
            or not _aware(root.created_at) <= _aware(inverse.created_at) <= _aware(row.created_at)
            or _aware(row.created_at) > datetime.now(timezone.utc)):
        _unknown()
    if type(row) is ExecutionSeal:
        decision = db.get(Decision, row.correction_decision_id, populate_existing=True)
        if (decision is None or decision.root_disposition_id != root.id
                or decision.reversal_id != inverse.id or decision.disposition != row.disposition
                or decision.request_hash != row.expected_correction_decision_hash
                or _aware(decision.created_at) > _aware(row.created_at)):
            _unknown()
    aggregate, kind = KINDS[type(row)]
    audit = facts.single(db, AuditEvent, aggregate_type=aggregate, aggregate_id=str(row.id))
    facts.audit(db, actor=actor, stream='inventory', aggregate_type=aggregate, identifier=row.id,
        action=kind, request_id='loss-correction-seal:' + str(row.id), before={}, after=payload(row))
    if (sources._hash(audit.after_jsonb) != sources._hash(payload(row))
            or _aware(audit.created_at) != _aware(row.created_at)
            or _aware(audit.occurred_at) != _aware(row.created_at)):
        _unknown()
    for model, typ, identifier in (
        (OutboxEvent, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
        (StateTransitionEvent, StateTransitionEvent.aggregate_type, StateTransitionEvent.aggregate_id),
        (NotificationEvent, NotificationEvent.business_type, NotificationEvent.business_id),
    ):
        if db.scalar(select(model.id).where(typ == aggregate, identifier == str(row.id)).limit(1)) is not None:
            _unknown()
    return dict(request_state='sealed', retry_allowed=False, result_scope='closed_original_request',
        request_id=request.request_id, request_hash=row.request_hash, result=None,
        seal=dict(seal_id=str(row.id), root_disposition_id=str(root.id),
            sealed_at=_aware(row.created_at).isoformat(), stock_effect='none'))
