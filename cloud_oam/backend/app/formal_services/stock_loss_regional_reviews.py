"""Append regional verification of an existing loss; no disposal or stock write.

The caller owns commit/rollback. Public activation waits for the complete loss
workflow; this service does not seed regional production permissions.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import or_, select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from ..inventory_models import StockLocation
from ..models import User
from ..stock_operation_models import StockOperationOrder as Order, StockLossRegionalReview as Review
from ..stock_loss_schemas import StockLossRegionalReviewIn, StockLossRegionalReviewOut
from . import inventory_posting as posting, stock_loss_facts as facts, stock_loss_sources as sources
from .audit_chain import append_audit_event
from .notification_events import record_business_notification, target_manifest_hash
from .work_order_query import _aware

AGGREGATE = 'stock_loss_regional_review'
KIND = 'stock_loss.regionally_verified'
ACTION = 'review_loss_regional'


def intent(request):
    return dict(operation_id=str(request.operation_id),
        expected_submission_plan_hash=request.expected_submission_plan_hash, comment=request.comment)


def payload(row):
    return dict(review_id=str(row.id), operation_id=str(row.operation_id), owner_org_id=str(row.owner_org_id),
        reviewer_person_id=str(row.reviewer_person_id), authorization_version=row.authorization_version,
        decision=row.decision, comment=row.comment, request_id=row.request_id, request_hash=row.request_hash,
        submission_plan_hash=row.submission_plan_hash, approval_stage='awaiting_headquarters', stock_effect='none')


def authorize(db, actor, order):
    current = posting._require_current_actor(db, posting._validate_supplied_actor(actor))
    user = db.get(User, current.user_id, populate_existing=True)
    if user is None or not user.is_active:
        sources._fail('stock_loss_regional_forbidden', '当前核实账号已停用', 403)
    location = db.get(StockLocation, order.source_location_id, populate_existing=True)
    if location is None or order.operation_type != 'loss_report':
        sources._fail('stock_loss_not_found', '报损单不存在', 404)
    owner = location.owner_org_id
    if not any(grant.role_code == 'provincial_manager' and grant.scope_type == 'organization'
               and grant.scope_id == str(owner) for grant in current.assignments):
        sources._fail('stock_loss_regional_forbidden', '没有该报损所属区域的核实权限', 403)
    if not current.allows(db, 'stock_operation', ACTION,
            target_scope_type='organization', target_scope_id=str(owner)) or not any(
                item.role_code == 'provincial_manager' and item.scope_type == 'organization'
                and item.scope_id == str(owner) and item.resource == 'stock_operation'
                and item.action == ACTION and item.field_code == '' and item.effect == 'allow'
                for item in current.entitlements):
        sources._fail('stock_loss_regional_forbidden', '没有当前区域报损核实权限', 403)
    if current.user_id == order.actor_user_id or current.person_id == order.requester_id:
        sources._fail('stock_loss_self_review_forbidden', '申请人不能核实自己的报损', 403)
    return current, owner


def verified(db, *, row, order):
    """Internal durable result proof; authorization belongs to the caller."""
    body = payload(row)
    if (row.operation_id != order.id or row.operation_type != 'loss_report' or row.decision != 'verified'
            or row.submission_plan_hash != order.plan_hash or row.authorization_version < 1
            or row.actor_user_id == order.actor_user_id or row.reviewer_person_id == order.requester_id
            or _aware(row.created_at) < _aware(order.created_at)
            or row.request_hash != sources._hash(dict(operation_id=str(order.id),
                expected_submission_plan_hash=order.plan_hash, comment=row.comment))):
        facts.invalid()
    event = facts.single(db, AuditEvent, stream_key='inventory', aggregate_type=AGGREGATE, aggregate_id=str(row.id))
    facts.audit(db, actor=SimpleNamespace(user_id=row.actor_user_id), stream='inventory', aggregate_type=AGGREGATE, identifier=row.id,
        action=KIND, request_id='stock-loss-regional-review:'+str(row.id), before={}, after=body)
    if (event.actor_user_id != row.actor_user_id or _aware(event.created_at) != _aware(row.created_at)
            or _aware(event.occurred_at) != _aware(row.created_at)):
        facts.invalid()
    facts.single(db, OutboxEvent, event_type=KIND, aggregate_type=AGGREGATE, aggregate_id=str(row.id),
        idempotency_key=KIND+':'+str(row.id), payload_jsonb=body)
    facts.single(db, StateTransitionEvent, aggregate_type=AGGREGATE, aggregate_id=str(row.id),
        from_status='awaiting_regional', to_status='awaiting_headquarters', actor_id=row.actor_user_id,
        reason=KIND, idempotency_key=KIND+':'+str(row.id), metadata_jsonb=body)
    notification = facts.single(db, NotificationEvent, event_type=KIND, business_type=AGGREGATE,
        business_id=str(row.id), dedup_key=KIND+':'+str(row.id), payload_jsonb=body)
    targets = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(
        NotificationPersonTarget.event_id == notification.id)))
    if targets != (order.requester_id,) or notification.target_manifest_sha256 != target_manifest_hash(targets):
        facts.invalid()
    return StockLossRegionalReviewOut(review_id=row.id, operation_id=row.operation_id, owner_org_id=row.owner_org_id,
        reviewer_person_id=row.reviewer_person_id, comment=row.comment, request_id=row.request_id,
        request_hash=row.request_hash, submission_plan_hash=row.submission_plan_hash, reviewed_at=_aware(row.created_at))


def _record(db, *, row, order):
    body = payload(row); at = row.created_at
    append_audit_event(db, stream_key='inventory', actor_user_id=row.actor_user_id, action=KIND,
        aggregate_type=AGGREGATE, aggregate_id=str(row.id), before_jsonb={}, after_jsonb=body,
        request_id='stock-loss-regional-review:'+str(row.id), occurred_at=at, created_at=at)
    db.add(OutboxEvent(event_type=KIND, aggregate_type=AGGREGATE, aggregate_id=str(row.id), payload_jsonb=body,
        idempotency_key=KIND+':'+str(row.id), available_at=at, created_at=at, updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=AGGREGATE, aggregate_id=str(row.id),
        from_status='awaiting_regional', to_status='awaiting_headquarters', actor_id=row.actor_user_id,
        reason=KIND, idempotency_key=KIND+':'+str(row.id), occurred_at=at, metadata_jsonb=body, created_at=at))
    record_business_notification(db, event_type=KIND, business_type=AGGREGATE, business_id=row.id,
        dedup_key=KIND+':'+str(row.id), payload=body, recipient_person_id=order.requester_id, occurred_at=at, now=at)
    db.flush()


def verify_regional_loss(db, *, actor, request):
    request = StockLossRegionalReviewIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    order = db.get(Order, request.operation_id, populate_existing=True)
    if order is None:
        sources._fail('stock_loss_not_found', '报损单不存在', 404)
    current, owner = authorize(db, actor, order)
    if order.plan_hash != request.expected_submission_plan_hash:
        sources._fail('stock_loss_submission_changed', '待核实的原报损内容不一致，请重新查看')
    facts.submission_evidence(db, order=order)
    # Review idempotency is independent from stock and submission keys.
    key = posting._storage_hash('stock-loss-regional-review:'+posting._require_idempotency_key(request.idempotency_key))
    from .stock_loss_review_seals import require_unsealed
    require_unsealed(db, actor=current, stage='regional', request_id=request.request_id, key=key)
    rows = tuple(db.scalars(select(Review).where(or_(Review.operation_id == order.id,
        Review.idempotency_key_hash == key,
        (Review.actor_user_id == current.user_id) & (Review.request_id == request.request_id)))
        .limit(3).execution_options(populate_existing=True)))
    digest = sources._hash(intent(request))
    if rows:
        row = rows[0]
        if (len(rows) != 1 or row.operation_id != order.id or row.actor_user_id != current.user_id
                or row.reviewer_person_id != current.person_id or row.request_id != request.request_id
                or row.idempotency_key_hash != key or row.request_hash != digest):
            sources._fail('stock_loss_regional_review_conflict', '报损已核实或原请求绑定其他内容，请回查原结果')
        return verified(db, row=row, order=order)
    row = Review(id=uuid4(), operation_id=order.id, operation_type='loss_report', owner_org_id=owner,
        actor_user_id=current.user_id, reviewer_person_id=current.person_id,
        authorization_version=current.authorization_version, decision='verified', comment=request.comment,
        request_id=request.request_id, idempotency_key_hash=key, request_hash=digest,
        submission_plan_hash=order.plan_hash, created_at=datetime.now(timezone.utc))
    db.add(row); db.flush()
    _record(db, row=row, order=order)
    authorize(db, current, order)
    return verified(db, row=row, order=order)
