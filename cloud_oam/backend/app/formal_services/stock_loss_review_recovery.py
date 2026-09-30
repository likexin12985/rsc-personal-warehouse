"""Recover an exact review without replaying approval or requiring write access.

The original reviewer needs current scoped read access. Immutable submission,
review, audit, state, outbox and notification proofs must agree; a moving audit
cursor invalidates the read. An absent result never authorizes a new write.
"""
from sqlalchemy import or_, select

from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent
from ..inventory_models import StockLocation
from ..models import User
from ..stock_operation_models import (
    StockOperationOrder as Order, StockLossRegionalReview, StockLossHeadquartersReview,
)
from ..stock_loss_schemas import (
    StockLossReviewRequestLookupIn, StockLossRegionalReviewFoundOut,
    StockLossHeadquartersReviewFoundOut, StockLossRequestMissingOut,
)
from . import inventory_posting as posting, stock_loss_facts as facts, stock_loss_sources as sources
from . import stock_loss_regional_reviews as regional, stock_loss_headquarters_reviews as headquarters
from .stock_loss_recovery import _cursor


def _stage(stage):
    if stage == 'regional':
        return regional, StockLossRegionalReview, StockLossRegionalReviewFoundOut
    if stage == 'headquarters':
        return headquarters, StockLossHeadquartersReview, StockLossHeadquartersReviewFoundOut
    raise ValueError('an explicit regional or headquarters review stage is required')


def _authorize(db, actor, order, stage):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    if user is None or not user.is_active:
        sources._fail('stock_loss_review_read_forbidden', '当前核实账号已停用', 403)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    if location is None or order.operation_type != 'loss_report':
        sources._fail('stock_loss_not_found', '报损单不存在', 404)
    owner = location.owner_org_id
    role, scope, identifier = (('provincial_manager', 'organization', str(owner))
        if stage == 'regional' else ('admin', 'national', '*'))
    if (not any(g.role_code == role and g.scope_type == scope and g.scope_id == identifier
                for g in current.assignments)
            or not current.allows(db, 'stock_operation', 'read',
                target_scope_type='organization', target_scope_id=str(owner))
            or not any(g.role_code == role and g.scope_type == scope and g.scope_id == identifier
                and g.resource == 'stock_operation' and g.action == 'read'
                and g.field_code == '' and g.effect == 'allow' for g in current.entitlements)):
        sources._fail('stock_loss_review_read_forbidden', '没有该区域报损审批记录的当前查看权限', 403)
    if current.user_id == order.actor_user_id or current.person_id == order.requester_id:
        sources._fail('stock_loss_self_review_forbidden', '申请人不能恢复自己的审批请求', 403)
    return current, owner


def _conflict():
    sources._fail('stock_loss_review_request_conflict', '原审批请求坐标或内容不一致，请保留原请求核验')


def _request_events(db, actor, request, service, row):
    """Orphan or duplicate evidence is an unknown outcome, never a clean miss."""
    expected = (str(row.id),) if row is not None else ()
    audits = tuple(db.scalars(select(AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == 'inventory', AuditEvent.aggregate_type == service.AGGREGATE,
        AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.after_jsonb['request_id'].as_string() == request.request_id).limit(3)))
    if audits != expected:
        facts.invalid()
    for model, body, kind, identifier in (
        (OutboxEvent, OutboxEvent.payload_jsonb, OutboxEvent.aggregate_type, OutboxEvent.aggregate_id),
        (StateTransitionEvent, StateTransitionEvent.metadata_jsonb,
            StateTransitionEvent.aggregate_type, StateTransitionEvent.aggregate_id),
        (NotificationEvent, NotificationEvent.payload_jsonb,
            NotificationEvent.business_type, NotificationEvent.business_id),
    ):
        observed = tuple(db.scalars(select(identifier).where(kind == service.AGGREGATE,
            body['reviewer_person_id'].as_string() == str(actor.person_id),
            body['request_id'].as_string() == request.request_id).limit(3)))
        if observed != expected:
            facts.invalid()


def lookup_review_request(db, *, actor, request, stage):
    from . import stock_loss_review_seals as seals
    service, model, output = _stage(stage)
    request = StockLossReviewRequestLookupIn.model_validate(request.model_dump())
    with db.no_autoflush:
        before = _cursor(db)
        order = db.get(Order, request.operation_id, populate_existing=True)
        if order is None:
            sources._fail('stock_loss_not_found', '报损单不存在', 404)
        current, owner = _authorize(db, actor, order, stage)
        if request.operator_person_id != current.person_id:
            sources._fail('operator_mismatch', '操作人必须是当前登录人员', 403)
        if request.expected_submission_plan_hash != order.plan_hash:
            _conflict()
        facts.submission_evidence(db, order=order)
        key = posting._storage_hash('stock-loss-'+stage+'-review:'
            + posting._require_idempotency_key(request.idempotency_key))
        seal = seals.find(db, actor=current, stage=stage, request=request, key=key)
        rows = tuple(db.scalars(select(model).where(or_(model.operation_id == order.id if seal is None else False,
            model.idempotency_key_hash == key,
            (model.actor_user_id == current.user_id) & (model.request_id == request.request_id)))
            .limit(3).execution_options(populate_existing=True)))
        if len(rows) > 1:
            _conflict()
        row = rows[0] if rows else None
        if row is not None:
            if row.actor_user_id != current.user_id or row.reviewer_person_id != current.person_id:
                sources._fail('stock_loss_review_not_found', '本人原审批记录不存在', 404)
            if (row.operation_id != order.id or row.owner_org_id != owner
                    or row.request_id != request.request_id or row.idempotency_key_hash != key
                    or row.request_hash != request.request_hash
                    or row.submission_plan_hash != request.expected_submission_plan_hash):
                _conflict()
        if seal is not None and (row is not None or seal.owner_org_id != owner):
            facts.invalid()
        _request_events(db, current, request, service, row)
        if seal is not None:
            result = seals.verified(db, actor=current, row=seal, order=order, stage=stage)
        else:
            result = output(review=service.verified(db, row=row, order=order)) if row else StockLossRequestMissingOut()
        if _cursor(db) != before:
            sources._fail('stock_loss_review_lookup_changed', '审批记录在回查期间变化，请保留原请求重新查询')
        _authorize(db, current, order, stage)
        return result
