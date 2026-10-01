"""Internal inverse write/recovery/closure composition, caller owns transaction.

Production use remains closed until native bidirectional commit fences and
full correction/return/scrap constraints are installed. This is not a route.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import or_, select

from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.formal_access import lock_formal_principal_graph
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services import stock_loss_facts as original
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.work_order_query import _aware
from .historical_original import _bound
from .request_contracts import ReversalExecute, validate, original_request, require_original_row
from .seal_model import StockLossInverseRequestSeal as Seal
from . import inverse_posting as inverse_posting
from . import inverse_recovery as inverse_recovery
from . import request_authority


AGGREGATE = 'stock_loss_inverse_request_seal'
KIND = 'stock_loss.inverse_request_sealed'


def _command(request):
    request = validate(request)
    if type(request) is not ReversalExecute:
        raise ValueError('complete original inverse command required')
    return request


def _keys(request):
    return tuple(posting._storage_hash('stock-loss:' + action + ':' + request.idempotency_key)
        for action in ('reverse_loss', 'approve_loss_correction', 'correct_loss'))


def _condition(actor, request):
    keys = _keys(request)
    return or_(Seal.reversal_key_hash.in_(keys), Seal.approval_key_hash.in_(keys),
        Seal.correction_key_hash.in_(keys),
        (Seal.actor_user_id == actor.user_id) & (Seal.request_id == request.request_id))


def _unknown():
    sources._fail('loss_inverse_seal_evidence_invalid', '原冲销封存证据不完整，不能重发或补写', 503)


def payload(row):
    return dict(root_disposition_id=str(row.root_disposition_id), actor_user_id=row.actor_user_id,
        actor_person_id=str(row.actor_person_id), authorization_version=row.authorization_version,
        request_id=row.request_id, request_reference=row.request_reference,
        reversal_key_hash=row.reversal_key_hash, approval_key_hash=row.approval_key_hash,
        correction_key_hash=row.correction_key_hash, request_hash=row.request_hash,
        plan_hash=row.plan_hash, reason=row.reason, command=row.command_jsonb, stock_effect='none')


def find(db, *, actor, request):
    rows = tuple(db.scalars(select(Seal).where(_condition(actor, request)).limit(3)
        .execution_options(populate_existing=True)))
    if len(rows) > 1: inverse_recovery._conflict()
    row = rows[0] if rows else None
    if row is not None:
        if row.actor_user_id != actor.user_id or row.actor_person_id != actor.person_id:
            sources._fail('loss_inverse_not_found', '本人原冲销封存不存在', 404)
        require_original_row(row=row, actor=actor, request=request)
        if ((row.reversal_key_hash, row.approval_key_hash, row.correction_key_hash) != _keys(request)
                or row.plan_hash != request.expected_plan_hash
                or row.request_reference != posting._request_reference(request.request_id)):
            inverse_recovery._conflict()
    # A detached seal audit must never become a clean missing request.
    body = AuditEvent.after_jsonb
    identifiers = tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.aggregate_type == AGGREGATE,
        or_((AuditEvent.actor_user_id == actor.user_id) &
                or_(body['request_id'].as_string() == request.request_id,
                    body['request_reference'].as_string() == posting._request_reference(request.request_id)),
            *(body[key].as_string().in_(_keys(request)) for key in
                ('reversal_key_hash', 'approval_key_hash', 'correction_key_hash')))).limit(3)))
    if identifiers != ((str(row.id),) if row else ()): _unknown()
    return row


def _verify_seal_evidence(db, *, actor, request, row):
    # The caller already proved the exact missing original request. This
    # component verifies only the seal evidence, not a second history graph.
    from app.stock_operation_models import StockLossDisposition
    root = db.get(StockLossDisposition, row.root_disposition_id, populate_existing=True)
    if (root is None or row.authorization_version < 1
            or not _aware(root.created_at) <= _aware(row.created_at) <= datetime.now(timezone.utc)):
        _unknown()
    audit = original.single(db, AuditEvent, aggregate_type=AGGREGATE, aggregate_id=str(row.id))
    original.audit(db, actor=actor, stream='inventory', aggregate_type=AGGREGATE, identifier=row.id,
        action=KIND, request_id='loss-inverse-seal:' + str(row.id), before={}, after=payload(row))
    if (sources._hash(audit.after_jsonb) != sources._hash(payload(row))
            or _aware(audit.created_at) != _aware(row.created_at)
            or _aware(audit.occurred_at) != _aware(row.created_at)): _unknown()
    for model, kind, identifier in ((OutboxEvent, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
            (StateTransitionEvent, StateTransitionEvent.aggregate_type, StateTransitionEvent.aggregate_id),
            (NotificationEvent, NotificationEvent.business_type, NotificationEvent.business_id)):
        if db.scalar(select(model.id).where(kind == AGGREGATE, identifier == str(row.id)).limit(1)) is not None:
            _unknown()
    return dict(request_state='sealed', retry_allowed=False, result_scope='closed_original_request',
        request_id=request.request_id, request_hash=row.request_hash, result=None,
        seal=dict(seal_id=str(row.id), root_disposition_id=str(row.root_disposition_id),
            sealed_at=_aware(row.created_at).isoformat(), stock_effect='none'))


def lookup(db, *, actor, request):
    return _lookup(db, actor=actor, request=request, stopped_return=False)


def lookup_unshipped_return(db, *, actor, request):
    """Exact query-only recovery, including permanent closure, for original returns."""
    return _lookup(db, actor=actor, request=request, stopped_return=True)


def _lookup(db, *, actor, request, stopped_return):
    request = _command(request)
    with db.no_autoflush:
        before = _bound(db)
        # Authenticate/authorize before revealing whether a seal exists.
        recover = (inverse_recovery.lookup_unshipped_return_inverse if stopped_return
            else inverse_recovery.lookup_original_inverse)
        answer = recover(db, actor=actor, request=request)
        row = find(db, actor=actor, request=request)
        if row is not None:
            if answer['request_state'] != 'not_found': _unknown()
            answer = _verify_seal_evidence(db, actor=actor, request=request, row=row)
            # Keep a fresh permission check after reading seal evidence. No
            # actor or history proof survives beyond this one lookup.
            from app.stock_operation_models import StockLossDisposition, StockOperationOrder
            root = db.get(StockLossDisposition, row.root_disposition_id, populate_existing=True)
            order = db.get(StockOperationOrder, root.operation_id, populate_existing=True) if root else None
            if order is None: _unknown()
            inverse_recovery._authorize(db, actor, order)
        if _bound(db) != before: _unknown()
        return answer


def require_unsealed(db, *, actor, request):
    if db.scalar(select(Seal.id).where(_condition(actor, request)).limit(1)) is not None:
        sources._fail('loss_inverse_request_sealed', '原冲销请求已永久封存，不能再次执行', 409)
    if find(db, actor=actor, request=request) is not None: _unknown()


def execute(db, *, actor, request):
    request = _command(request)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    refs = request_authority.references(db, actor=actor, request=request)
    require_unsealed(db, actor=refs.actor, request=request)
    return inverse_posting.execute_account_inverse(db, actor=refs.actor, request=request)


def execute_unshipped_return(db, *, actor, request):
    return inverse_posting.execute_unshipped_return_inverse(db, actor=actor, request=_command(request))


def seal(db, *, actor, request):
    return _seal(db, actor=actor, request=request, stopped_return=False)


def seal_unshipped_return(db, *, actor, request):
    return _seal(db, actor=actor, request=request, stopped_return=True)


def _seal(db, *, actor, request, stopped_return):
    request = _command(request)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    refs = request_authority.references(db, actor=actor, request=request)
    current = refs.actor
    answer = _lookup(db, actor=current, request=request, stopped_return=stopped_return)
    if answer['request_state'] != 'not_found': return answer
    binding = original_request(actor=current, request=request)
    keys = _keys(request); at = datetime.now(timezone.utc)
    row = Seal(id=uuid4(), root_disposition_id=refs.root.id, actor_user_id=current.user_id,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        request_id=request.request_id, idempotency_key_hash=binding.key_hash,
        reversal_key_hash=keys[0], approval_key_hash=keys[1], correction_key_hash=keys[2],
        request_reference=posting._request_reference(request.request_id), request_hash=binding.request_hash,
        plan_hash=request.expected_plan_hash, reason=request.reason, command_jsonb=binding.document, created_at=at)
    db.add(row); db.flush()
    append_audit_event(db, stream_key='inventory', actor_user_id=current.user_id, action=KIND,
        aggregate_type=AGGREGATE, aggregate_id=str(row.id), before_jsonb={}, after_jsonb=payload(row),
        request_id='loss-inverse-seal:' + str(row.id), occurred_at=at, created_at=at)
    db.flush()
    request_authority.authorize(db, actor=current, order=refs.order, action='reverse_loss')
    return _lookup(db, actor=current, request=request, stopped_return=stopped_return)
