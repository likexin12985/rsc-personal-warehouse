"""Internal exact-command recovery for HQ execution, with no write fallback.

Current scoped read permission is independent of the historical write grant.
Public recovery is read-only. Execution, correction actions and client
acceptance remain separate requirements before the full workflow can open.
"""
from sqlalchemy import or_, select

from ..foundation_models import AuditEvent, NotificationEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryTransaction, StockLocation
from ..models import User
from ..stock_operation_models import (
    StockLossDisposition as Disposition, StockLossHeadquartersDecision as Decision,
    StockLossHeadquartersReview as Review, StockOperationLine as Line, StockOperationOrder as Order,
    StockOperationCancellation, StockOperationOutbound, StockOperationShipment, StockOperationReceipt,
    StockOperationReturnInbound, StockOperationCommandSeal, StockOperationReturnInboundSeal,
    StockLossRequestSeal, StockLossReviewRequestSeal, StockLossRegionalReview,
)
from ..stock_loss_schemas import StockLossDispositionExecuteIn
from ..stock_loss_return_schemas import StockLossReturnExecuteIn
from . import inventory_posting as posting, stock_loss_sources as sources
from . import stock_loss_facts as original, stock_loss_headquarters_reviews as headquarters
from . import stock_loss_disposition_plan as disposition_plan, stock_loss_disposition_facts as facts
from . import stock_loss_return_plan as return_plan, stock_loss_return_facts as return_facts
from .stock_loss_recovery import _cursor
from .stock_loss_corrections import original_recovery as historical_recovery


def _conflict():
    sources._fail('stock_loss_disposition_request_conflict', '原处置请求坐标或完整内容不一致，请保留原请求核验')


def _authorize(db, actor, order):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    if user is None or not user.is_active or location is None or order.operation_type != 'loss_report':
        sources._fail('stock_loss_disposition_read_forbidden', '当前处置记录查看身份或原单无效', 403)
    if (not any(g.role_code == 'admin' and g.scope_type == 'national' and g.scope_id == '*'
                for g in current.assignments)
            or not current.allows(db, 'stock_operation', 'read',
                target_scope_type='organization', target_scope_id=str(location.owner_org_id))
            or not any(g.role_code == 'admin' and g.scope_type == 'national' and g.scope_id == '*'
                and g.resource == 'stock_operation' and g.action == 'read'
                and g.field_code == '' and g.effect == 'allow' for g in current.entitlements)):
        sources._fail('stock_loss_disposition_read_forbidden', '没有该报损处置记录的当前总部查看权限', 403)
    return current


def _related_requests(db, actor, request, keys, row):
    # The derived child deliberately shares its parent's request and posting.
    # It is one command, whereas any other order at these coordinates conflicts.
    children = tuple(db.scalars(select(Order.id).where(or_(Order.idempotency_key_hash.in_(keys),
        (Order.actor_user_id == actor.user_id) & (Order.request_id == request.request_id)))
        .limit(3)))
    expected = (row.return_operation_id,) if row is not None and row.return_operation_id else ()
    if children != expected:
        _conflict()
    for model in (StockOperationCancellation, StockOperationOutbound, StockOperationShipment,
            StockOperationReceipt, StockOperationReturnInbound, StockOperationCommandSeal,
            StockOperationReturnInboundSeal, StockLossRequestSeal, StockLossReviewRequestSeal,
            StockLossRegionalReview, Review):
        coordinate = (model.actor_user_id == actor.user_id) & (model.request_id == request.request_id)
        if hasattr(model, 'idempotency_key_hash'):
            coordinate = or_(coordinate, model.idempotency_key_hash.in_(keys))
        if db.scalar(select(model.id).where(coordinate).limit(1)) is not None:
            _conflict()


def _request_evidence(db, actor, request, keys, row, command_hash):
    """Reject orphan postings/events rather than turning them into a clean miss."""
    expected_tx = (str(row.posting_transaction_id),) if row is not None else ()
    keyed = tuple(str(v) for v in db.scalars(select(InventoryTransaction.id)
        .where(InventoryTransaction.idempotency_key_hash.in_(keys)).limit(3)))
    reference = posting._request_reference(request.request_id)
    audited_tx = tuple(db.scalars(select(AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == 'inventory', AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.request_id == reference, AuditEvent.aggregate_type == 'inventory_transaction').limit(3)))
    stated_tx = tuple(db.scalars(select(StateTransitionEvent.aggregate_id).where(
        StateTransitionEvent.actor_id == actor.user_id,
        StateTransitionEvent.aggregate_type == 'inventory_transaction',
        StateTransitionEvent.metadata_jsonb['request_reference'].as_string() == reference).limit(3)))
    if keyed != expected_tx or audited_tx != expected_tx or stated_tx != expected_tx:
        facts.invalid()
    expected_audits = []
    if row is not None:
        expected_audits.append(('inventory', facts.AGGREGATE, str(row.id), facts.KIND))
        if row.return_operation_id:
            expected_audits.append(('material_request', 'stock_operation_order',
                str(row.return_operation_id), return_facts.CHILD_KIND))
    audits = tuple(db.execute(select(AuditEvent.stream_key, AuditEvent.aggregate_type,
        AuditEvent.aggregate_id, AuditEvent.action).where(AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.request_id == request.request_id,
        AuditEvent.stream_key.in_(('inventory', 'material_request'))).limit(4)))
    if sorted(tuple(v) for v in audits) != sorted(expected_audits):
        facts.invalid()
    expected_parent = (str(row.id),) if row is not None else ()
    for model, body, kind, identifier in (
        (OutboxEvent, OutboxEvent.payload_jsonb, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
        (StateTransitionEvent, StateTransitionEvent.metadata_jsonb,
            StateTransitionEvent.aggregate_type, StateTransitionEvent.aggregate_id),
        (NotificationEvent, NotificationEvent.payload_jsonb,
            NotificationEvent.business_type, NotificationEvent.business_id),
    ):
        observed = tuple(db.scalars(select(identifier).where(kind == facts.AGGREGATE,
            body['executor_person_id'].as_string() == str(actor.person_id),
            body['request_id'].as_string() == request.request_id).limit(3)))
        if observed != expected_parent:
            facts.invalid()
        if model is not NotificationEvent:
            children = tuple(db.scalars(select(identifier).where(kind == 'stock_operation_order',
                body['executor_person_id'].as_string() == str(actor.person_id),
                body['request_hash'].as_string() == command_hash).limit(3)))
            expected_child = (str(row.return_operation_id),) if row is not None and row.return_operation_id else ()
            if children != expected_child:
                facts.invalid()


def lookup_disposition_request(db, *, actor, request, flow):
    if flow not in ('disposition', 'return'):
        raise ValueError('explicit disposition or return flow required')
    model, service = ((StockLossDispositionExecuteIn, disposition_plan) if flow == 'disposition'
        else (StockLossReturnExecuteIn, return_plan))
    request = model.model_validate(request.model_dump())
    document = dict(intent=service.intent(request), request_id=request.request_id,
        expected_plan_hash=request.expected_plan_hash)
    command_hash = sources._hash(document)
    raw_key = posting._require_idempotency_key(request.idempotency_key)
    keys = tuple(posting._storage_hash(prefix + raw_key)
        for prefix in ('stock-loss-disposition:', 'stock-loss-return:'))
    key = keys[0 if flow == 'disposition' else 1]
    with db.no_autoflush:
        before = _cursor(db)
        decision = db.get(Decision, request.headquarters_decision_id, populate_existing=True)
        review = db.get(Review, decision.review_id, populate_existing=True) if decision else None
        line = db.get(Line, decision.line_id, populate_existing=True) if decision else None
        order = db.get(Order, line.operation_id, populate_existing=True) if line else None
        if decision is None or review is None or line is None or order is None:
            sources._fail('stock_loss_disposition_not_found', '准确报损终审明细不存在', 404)
        current = _authorize(db, actor, order)
        if (review.operation_id != order.id or request.expected_headquarters_review_hash != review.request_hash
                or request.expected_submission_plan_hash != order.plan_hash
                or (flow == 'return' and decision.disposition != 'return_to_region')
                or (flow == 'disposition' and decision.disposition not in disposition_plan.SUPPORTED)):
            _conflict()
        original.submission_evidence(db, order=order)
        headquarters.verified(db, row=review, order=order)
        from . import stock_loss_disposition_seals as seals
        seal = seals.find(db, actor=current, request=request, flow=flow)
        rows = tuple(db.scalars(select(Disposition).where(or_(Disposition.line_id == line.id if seal is None else False,
            Disposition.headquarters_decision_id == decision.id if seal is None else False, Disposition.idempotency_key_hash.in_(keys),
            (Disposition.actor_user_id == current.user_id) & (Disposition.request_id == request.request_id)))
            .limit(3).execution_options(populate_existing=True)))
        if len(rows) > 1:
            _conflict()
        row = rows[0] if rows else None
        if row is not None:
            if row.actor_user_id != current.user_id or row.executor_person_id != current.person_id:
                sources._fail('stock_loss_disposition_not_found', '本人原处置记录不存在', 404)
            if (row.operation_id != order.id or row.line_id != line.id
                    or row.headquarters_decision_id != decision.id or row.disposition != decision.disposition
                    or row.request_id != request.request_id or row.idempotency_key_hash != key
                    or row.command_jsonb != document or row.request_hash != command_hash
                    or row.plan_hash != request.expected_plan_hash):
                _conflict()
        if seal is not None and row is not None:
            facts.invalid()
        _related_requests(db, current, request, keys, row)
        _request_evidence(db, current, request, keys, row, command_hash)
        result = dict(lookup_status='not_found', retry_permitted=False)
        if seal is not None:
            result = seals.verified(db, actor=current, row=seal, order=order, line=line, decision=decision, review=review)
        elif row is not None:
            result = dict(lookup_status='found', retry_permitted=False, disposition=historical_recovery.verified(db, row=row))
        if _cursor(db) != before:
            sources._fail('stock_loss_disposition_lookup_changed', '处置记录在回查期间变化，请保留原请求重新查询')
        _authorize(db, current, order)
        return result
