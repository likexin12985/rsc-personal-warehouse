"""Exact, query-only recovery of an original loss inverse command.

Internal candidate only. A clean miss never allows replay; sealing a missing
request and PostgreSQL commit-time authority are separate release requirements.
The returned result describes the original posting, not current stock state.
"""
from . import correction_seal_facts

from sqlalchemy import or_, select

from app.foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction, StockLocation
from app.models import User
from app import stock_operation_models as stock_models
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from .correction_models import StockLossDispositionReversal as Inverse
from .correction_models import StockLossCorrectionDecision, StockLossCorrectionExecution
from .request_contracts import ReversalExecute, validate, original_request, require_original_row
from .historical_original import _bound, verify_historical_original
from .history_chain import verify_inverse as verify_original_inverse, verify_chain
from . import business_events
from .request_evidence_scope import authentication_state


def _conflict():
    sources._fail('loss_inverse_request_conflict', '原冲销请求已绑定不同内容，请保留完整原请求核验', 409)


def _unknown():
    sources._fail('loss_inverse_request_outcome_unknown', '原请求证据不完整或读取期间变化，不能判定未执行或重发', 503)


def _authorize(db, actor, order):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    assignments = {g.assignment_id for g in current.assignments
        if g.role_code == 'admin' and g.scope_type == 'national' and g.scope_id == '*'}
    if (user is None or not user.is_active or location is None or order.operation_type != 'loss_report'
            or not assignments or not current.allows(db, 'stock_operation', 'read',
                target_scope_type='organization', target_scope_id=str(location.owner_org_id))
            or not any(g.assignment_id in assignments and g.resource == 'stock_operation'
                and g.action == 'read' and g.field_code == '' and g.effect == 'allow'
                and g.role_code == 'admin' and g.scope_type == 'national' and g.scope_id == '*'
                for g in current.entitlements)):
        sources._fail('loss_inverse_read_forbidden', '没有该报损冲销记录的当前总部查看权限', 403)
    return current


def _coordinate_rows(db, *, actor, request, keys):
    correction_seal_facts.require_unsealed(db, actor=actor, request=request)
    matches = []
    for model in (Inverse, StockLossCorrectionDecision, StockLossCorrectionExecution):
        rows = tuple(db.scalars(select(model).where(or_(model.idempotency_key_hash.in_(keys),
            (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id)))
            .limit(3).execution_options(populate_existing=True)))
        matches.extend(rows)
    # Any other stock-operation request at these coordinates is a collision,
    # including an empty sealed request with no corresponding stock posting.
    for name in ('StockOperationOrder', 'StockLossDisposition', 'StockLossRegionalReview',
            'StockLossHeadquartersReview', 'StockLossRequestSeal', 'StockLossReviewRequestSeal',
            'StockLossDispositionRequestSeal', 'StockOperationCancellation', 'StockOperationCommandSeal',
            'StockOperationOutbound', 'StockOperationShipment', 'StockOperationReceipt',
            'StockOperationReturnInbound', 'StockOperationReturnInboundSeal'):
        model = getattr(stock_models, name)
        coordinate = (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id)
        if hasattr(model, 'idempotency_key_hash'):
            coordinate = or_(coordinate, model.idempotency_key_hash.in_(keys))
        if db.scalar(select(model.id).where(coordinate).limit(1)) is not None:
            _conflict()
    if len(matches) > 1 or (matches and type(matches[0]) is not Inverse):
        _conflict()
    return matches[0] if matches else None


def _evidence(db, *, actor, request, keys, row):
    expected_tx = [str(row.posting_transaction_id)] if row is not None else []
    keyed = [str(v) for v in db.scalars(select(InventoryTransaction.id).where(
        InventoryTransaction.idempotency_key_hash.in_(keys)).limit(3))]
    if keyed != expected_tx:
        _unknown()
    reference = posting._request_reference(request.request_id)
    expected_audits = []
    expected_states = []
    expected_domain = []
    if row is not None:
        aggregate, kind, _, _ = business_events.KINDS[Inverse]
        expected_audits = [('inventory', aggregate, str(row.id), kind, request.request_id),
            ('inventory', 'inventory_transaction', str(row.posting_transaction_id),
                'inventory.transaction.reversed', reference)]
        expected_states = [(aggregate, str(row.id)), ('inventory_transaction', str(row.posting_transaction_id))]
        expected_domain = [(aggregate, str(row.id))]
    audits = [tuple(v) for v in db.execute(select(AuditEvent.stream_key, AuditEvent.aggregate_type,
        AuditEvent.aggregate_id, AuditEvent.action, AuditEvent.request_id).where(
            AuditEvent.actor_user_id == actor.user_id, AuditEvent.stream_key.in_(('inventory', 'material_request')),
            AuditEvent.request_id.in_((request.request_id, reference))).limit(4))]
    states = [tuple(v) for v in db.execute(select(StateTransitionEvent.aggregate_type,
        StateTransitionEvent.aggregate_id).where(StateTransitionEvent.actor_id == actor.user_id,
            ~authentication_state(),
            or_(StateTransitionEvent.metadata_jsonb['request_id'].as_string() == request.request_id,
                StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == reference)).limit(4))]
    if sorted(audits) != sorted(expected_audits) or sorted(states) != sorted(expected_states):
        _unknown()
    for model, body, kind, identifier in (
        (OutboxEvent, OutboxEvent.payload_jsonb, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
        (NotificationEvent, NotificationEvent.payload_jsonb, NotificationEvent.business_type, NotificationEvent.business_id),
    ):
        rows = [tuple(v) for v in db.execute(select(kind, identifier).where(
            body['actor_user_id'].as_string() == actor.user_id,
            body['request_id'].as_string() == request.request_id).limit(3))]
        if sorted(rows) != sorted(expected_domain):
            _unknown()


def lookup_original_inverse(db, *, actor, request):
    return _lookup(db, actor=actor, request=request, stopped_return=False)


def lookup_unshipped_return_inverse(db, *, actor, request):
    """Dedicated candidate recovery; never falls back to a write."""
    return _lookup(db, actor=actor, request=request, stopped_return=True)


def _lookup(db, *, actor, request, stopped_return):
    request = validate(request)
    if type(request) is not ReversalExecute:
        raise ValueError('the complete original inverse request is required')
    with db.no_autoflush:
        start = _bound(db)
        root = db.get(stock_models.StockLossDisposition, request.root_disposition_id, populate_existing=True)
        order = db.get(stock_models.StockOperationOrder, root.operation_id, populate_existing=True) if root else None
        if root is None or order is None:
            sources._fail('loss_inverse_not_found', '准确原处置不存在', 404)
        current = _authorize(db, actor, order)
        execution = db.get(StockLossCorrectionExecution, request.reversed_correction_id, populate_existing=True) if request.reversed_correction_id else root
        if execution is None or (execution is not root and execution.root_disposition_id != root.id):
            _conflict()
        if (request.expected_root_request_hash != root.request_hash
                or request.expected_execution_request_hash != execution.request_hash
                or request.expected_submission_plan_hash != order.plan_hash):
            _conflict()
        if stopped_return:
            if execution is not root or execution.disposition != 'return_to_region':
                sources._fail('loss_return_stop_requires_original_return', '须选择准确原报损退回', 412)
        elif execution.disposition not in {'restore_available', 'convert_used', 'convert_damaged'}:
            sources._fail('loss_inverse_dedicated_compensation_required', '须使用对应退回或报废补偿的原请求恢复', 412)
        binding = original_request(actor=current, request=request)
        keys = tuple(posting._storage_hash('stock-loss:' + action + ':' + request.idempotency_key)
            for action in ('reverse_loss', 'approve_loss_correction', 'correct_loss'))
        row = _coordinate_rows(db, actor=current, request=request, keys=keys)
        if row is not None:
            if row.actor_user_id != current.user_id or row.actor_person_id != current.person_id:
                sources._fail('loss_inverse_not_found', '本人原冲销请求不存在', 404)
            require_original_row(row=row, actor=current, request=request)
            verify_original_inverse(db, reversal_id=row.id)
        else:
            verify_chain(db, root_disposition_id=root.id)
        _evidence(db, actor=current, request=request, keys=keys, row=row)
        result = dict(request_state='found' if row is not None else 'not_found', retry_allowed=False,
            request_id=request.request_id, request_hash=binding.request_hash,
            result_scope=('historical_original_posting' if request.reversed_correction_id is None
                else 'historical_correction_inverse_posting') if row is not None else 'unconfirmed_request',
            result=business_events.payload(row, root=root, order=order) if row is not None else None)
        later = _coordinate_rows(db, actor=current, request=request, keys=keys)
        if (later.id if later is not None else None) != (row.id if row is not None else None):
            _unknown()
        if later is not None:
            require_original_row(row=later, actor=current, request=request)
        _evidence(db, actor=current, request=request, keys=keys, row=later)
        _authorize(db, current, order)
        if _bound(db) != start:
            _unknown()
        return result
