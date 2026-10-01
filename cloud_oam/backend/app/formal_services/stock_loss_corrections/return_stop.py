"""Atomic stop evidence for a fully unshipped original loss return.

    This candidate has no route or migration activation. Its native immutable
    constraints and stop/outbound concurrency fence are required before use.
"""
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.stock_loss_return_stop_models import StockLossReturnStop as Stop
from app.formal_services import stock_loss_facts as original, stock_loss_sources as sources
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.work_order_query import _aware
from . import return_history, return_posting_facts, return_dependencies, return_boundary
from .historical_original import _bound
from .return_progress import project_lines

AGGREGATE = 'stock_loss_return_stop'
KIND = 'stock_loss.return_stopped'


def _need(value):
    if not value:
        sources._fail('loss_return_stop_evidence_invalid', '退回停止与准确原退回或反向流水不一致', 503)


def historical_boundary(db, root):
    """Exact original graph, valid after the inverse; not current permission."""
    before = _bound(db)
    _need(root.disposition == 'return_to_region' and root.return_operation_id is not None)
    return_posting_facts._verify_at_posting(db, root)
    groups, fingerprint = return_history._capture(db, root)
    _need(len(groups['orders']) == len(groups['lines']) == 1)
    _need(all(not rows for name, rows in groups.items()
        if name not in {'root', 'orders', 'lines', 'serials'}))
    lines = project_lines(groups)
    deps = return_dependencies.read(db, disposition_id=root.id)
    _need(not deps.has_downstream_facts)
    result = return_boundary.document(root, groups['orders'][0], lines)
    _need(return_history._capture(db, root)[1] == fingerprint and _bound(db) == before)
    return result


def payload(stop, inverse):
    return dict(stop_id=str(stop.id), root_disposition_id=str(stop.root_disposition_id),
        return_operation_id=str(stop.return_operation_id), return_line_id=str(stop.return_line_id),
        reversal_id=str(inverse.id), inverse_request_hash=inverse.request_hash,
        actor_user_id=inverse.actor_user_id, evidence_fingerprint=stop.evidence_fingerprint,
        posting_transaction_id=str(inverse.posting_transaction_id), status='stopped',
        stock_effect='none', stop_scope='whole_unshipped_return')


def record(db, *, root, inverse):
    """Private transaction component; caller owns stock posting and rollback."""
    boundary = historical_boundary(db, root)
    _need(inverse.reversed_correction_id is None and inverse.root_disposition_id == root.id)
    _need(sources._hash(boundary) == sources._hash(inverse.plan_jsonb.get('return_boundary')))
    from uuid import UUID
    stop = Stop(id=uuid4(), root_disposition_id=root.id, reversal_id=inverse.id,
        return_operation_id=root.return_operation_id,
        return_line_id=UUID(boundary['lines'][0]['operation_line_id']),
        evidence_fingerprint=boundary['evidence_fingerprint'], created_at=inverse.created_at)
    db.add(stop); db.flush()
    body = payload(stop, inverse); key = KIND + ':' + str(stop.id); at = _aware(stop.created_at)
    append_audit_event(db, stream_key='inventory', actor_user_id=inverse.actor_user_id,
        action=KIND, aggregate_type=AGGREGATE, aggregate_id=str(stop.id),
        before_jsonb={}, after_jsonb=body, request_id='loss-return-stop:' + str(stop.id),
        occurred_at=at, created_at=at)
    db.add(OutboxEvent(event_type=KIND, aggregate_type=AGGREGATE, aggregate_id=str(stop.id),
        payload_jsonb=body, idempotency_key=key, available_at=at, created_at=at, updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=AGGREGATE, aggregate_id=str(stop.id),
        from_status='fulfillable', to_status='stopped', actor_id=inverse.actor_user_id,
        reason=KIND, idempotency_key=key, metadata_jsonb=body, occurred_at=at, created_at=at))
    db.flush()
    return stop


def verify(db, *, root, inverse):
    with db.no_autoflush:
        before = _bound(db)
        stops = tuple(db.scalars(select(Stop).where(Stop.root_disposition_id == root.id)
            .execution_options(populate_existing=True)))
        _need(len(stops) == 1)
        stop = stops[0]; boundary = historical_boundary(db, root)
        _need(stop.reversal_id == inverse.id and inverse.reversed_correction_id is None
            and stop.root_disposition_id == inverse.root_disposition_id == root.id
            and stop.return_operation_id == root.return_operation_id
            and str(stop.return_line_id) == boundary['lines'][0]['operation_line_id']
            and stop.evidence_fingerprint == boundary['evidence_fingerprint']
            and _aware(stop.created_at) == _aware(inverse.created_at)
            and sources._hash(boundary) == sources._hash(inverse.plan_jsonb.get('return_boundary')))
        body = payload(stop, inverse); key = KIND + ':' + str(stop.id)
        audit = original.single(db, AuditEvent, aggregate_type=AGGREGATE, aggregate_id=str(stop.id))
        original.audit(db, actor=SimpleNamespace(user_id=inverse.actor_user_id), stream='inventory',
            aggregate_type=AGGREGATE, identifier=stop.id, action=KIND,
            request_id='loss-return-stop:' + str(stop.id), before={}, after=body)
        for model in (OutboxEvent, StateTransitionEvent):
            original.single(db, model, aggregate_type=AGGREGATE, aggregate_id=str(stop.id))
        outbox = original.single(db, OutboxEvent, event_type=KIND, aggregate_type=AGGREGATE,
            aggregate_id=str(stop.id), idempotency_key=key, payload_jsonb=body)
        state = original.single(db, StateTransitionEvent, aggregate_type=AGGREGATE,
            aggregate_id=str(stop.id), from_status='fulfillable', to_status='stopped',
            actor_id=inverse.actor_user_id, reason=KIND, idempotency_key=key, metadata_jsonb=body)
        _need(all(sources._hash(v) == sources._hash(body) for v in
            (audit.after_jsonb, outbox.payload_jsonb, state.metadata_jsonb)))
        _need(all(_aware(v.created_at) == _aware(stop.created_at) for v in (audit, outbox, state))
            and _aware(audit.occurred_at) == _aware(state.occurred_at) == _aware(stop.created_at))
        _need(_bound(db) == before)
        return boundary


def require_open(db, *, operation_id):
    if db.scalar(select(Stop.id).where(Stop.return_operation_id == operation_id).limit(1)):
        sources._fail('loss_return_stopped', '该报损退回已停止履约，请回查原退回和冲销事实', 412)
