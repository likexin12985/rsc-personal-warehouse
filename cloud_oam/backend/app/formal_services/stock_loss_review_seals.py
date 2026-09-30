"""Permanent approval request seals, independent of the original loss seal.

Caller commits. PostgreSQL must enforce the same exclusion and complete proof;
this service alone is not a late-write or production acceptance guarantee.
"""
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import or_, select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent
from ..stock_operation_models import StockOperationOrder as Order, StockLossReviewRequestSeal as Seal, StockOperationLine as Line
from ..stock_loss_schemas import (StockLossReviewRequestLookupIn, StockLossReviewSealOut,
    StockLossReviewRequestSealedOut, StockLossRegionalReviewIn, StockLossHeadquartersReviewIn)
from ..stock_loss_review_seal_schemas import StockLossRegionalReviewSealIn, StockLossHeadquartersReviewSealIn
from . import inventory_posting as posting, stock_loss_sources as sources, stock_loss_facts as facts
from .audit_chain import append_audit_event
from .work_order_query import _aware

AGGREGATE = 'stock_loss_review_request_seal'
KIND = 'stock_loss.review_request_sealed'


def payload(row):
    return dict(stage=row.stage, operation_id=str(row.operation_id), owner_org_id=str(row.owner_org_id),
        reviewer_person_id=str(row.reviewer_person_id), authorization_version=row.authorization_version,
        request_id=row.request_id, idempotency_key_hash=row.idempotency_key_hash, request_hash=row.request_hash,
        submission_plan_hash=row.submission_plan_hash, command_intent=row.command_intent_jsonb)


def require_unsealed(db, *, actor, stage, request_id, key):
    if db.scalar(select(Seal.id).where(Seal.stage == stage, or_(Seal.idempotency_key_hash == key,
            (Seal.actor_user_id == actor.user_id) & (Seal.request_id == request_id))).limit(1)) is not None:
        sources._fail('stock_loss_review_request_sealed', '原审批请求已永久封存，不能再次执行')


def find(db, *, actor, stage, request, key):
    rows = tuple(db.scalars(select(Seal).where(Seal.stage == stage, or_(Seal.idempotency_key_hash == key,
        (Seal.actor_user_id == actor.user_id) & (Seal.request_id == request.request_id)))
        .limit(3).execution_options(populate_existing=True)))
    if len(rows) > 1:
        sources._fail('stock_loss_review_request_conflict', '原审批请求绑定多个封存记录')
    row = rows[0] if rows else None
    if row is not None:
        if row.actor_user_id != actor.user_id or row.reviewer_person_id != actor.person_id:
            sources._fail('stock_loss_review_not_found', '本人原审批记录不存在', 404)
        if (row.operation_id != request.operation_id or row.request_id != request.request_id
                or row.idempotency_key_hash != key or row.request_hash != request.request_hash
                or row.submission_plan_hash != request.expected_submission_plan_hash):
            sources._fail('stock_loss_review_request_conflict', '原审批请求坐标与封存记录不一致')
    expected = (str(row.id),) if row is not None else ()
    observed = tuple(db.scalars(select(AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == 'inventory', AuditEvent.aggregate_type == AGGREGATE,
        AuditEvent.actor_user_id == actor.user_id, AuditEvent.after_jsonb['stage'].as_string() == stage,
        AuditEvent.after_jsonb['request_id'].as_string() == request.request_id).limit(3)))
    if observed != expected:
        facts.invalid()
    return row


def verified(db, *, actor, row, order, stage):
    schema = StockLossRegionalReviewIn if stage == 'regional' else StockLossHeadquartersReviewIn
    try:
        command = schema.model_validate(dict(row.command_intent_jsonb,
            request_id=row.request_id, idempotency_key='sealed-key-not-disclosed'))
        normalized = command.model_dump(mode='json', exclude={'request_id', 'idempotency_key'})
    except (ValidationError, ValueError, TypeError):
        facts.invalid()
    if (row.stage != stage or row.operation_id != order.id or row.operation_type != 'loss_report'
            or row.actor_user_id != actor.user_id or row.reviewer_person_id != actor.person_id
            or row.authorization_version < 1 or row.submission_plan_hash != order.plan_hash
            or command.operation_id != order.id or command.expected_submission_plan_hash != order.plan_hash
            or normalized != row.command_intent_jsonb or sources._hash(normalized) != row.request_hash
            or _aware(row.created_at) < _aware(order.created_at)):
        facts.invalid()
    if stage == 'headquarters':
        from .stock_loss_headquarters_reviews import regional_evidence
        regional = regional_evidence(db, order=order, identifier=command.regional_review_id,
            digest=command.expected_regional_review_hash)
        expected = set(db.scalars(select(Line.id).where(Line.operation_id == order.id)))
        if {d.line_id for d in command.decisions} != expected or _aware(row.created_at) < _aware(regional.created_at):
            facts.invalid()
    facts.audit(db, actor=actor, stream='inventory', aggregate_type=AGGREGATE, identifier=row.id,
        action=KIND, request_id='stock-loss-review-seal:'+str(row.id), before={}, after=payload(row))
    event = facts.single(db, AuditEvent, stream_key='inventory', aggregate_type=AGGREGATE, aggregate_id=str(row.id))
    if _aware(event.occurred_at) != _aware(row.created_at) or _aware(event.created_at) != _aware(row.created_at):
        facts.invalid()
    return StockLossReviewRequestSealedOut(seal=StockLossReviewSealOut(seal_id=row.id, stage=row.stage,
        operation_id=row.operation_id, owner_org_id=row.owner_org_id, reviewer_person_id=row.reviewer_person_id,
        request_id=row.request_id, request_hash=row.request_hash, submission_plan_hash=row.submission_plan_hash,
        sealed_at=_aware(row.created_at)))


def seal_review_request(db, *, actor, request, stage):
    from .stock_loss_review_recovery import _stage, lookup_review_request
    service, _, _ = _stage(stage)
    schema = StockLossRegionalReviewSealIn if stage == 'regional' else StockLossHeadquartersReviewSealIn
    request = schema.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    command = request.original
    order = db.get(Order, command.operation_id, populate_existing=True)
    if order is None:
        sources._fail('stock_loss_not_found', '报损单不存在', 404)
    current, owner = service.authorize(db, actor, order)
    lookup = StockLossReviewRequestLookupIn(operation_id=command.operation_id,
        operator_person_id=request.operator_person_id, expected_submission_plan_hash=command.expected_submission_plan_hash,
        request_id=command.request_id, idempotency_key=command.idempotency_key, request_hash=request.request_hash)
    original = lookup_review_request(db, actor=current, request=lookup, stage=stage)
    if original.lookup_status != 'not_found':
        return original
    if stage == 'headquarters':
        service.regional_evidence(db, order=order, identifier=command.regional_review_id,
            digest=command.expected_regional_review_hash)
        if {d.line_id for d in command.decisions} != set(db.scalars(select(Line.id).where(Line.operation_id == order.id))):
            sources._fail('stock_loss_headquarters_line_coverage', '必须为原报损的每条明细选择准确的一项处置')
    at = datetime.now(timezone.utc)
    row = Seal(id=uuid4(), stage=stage, operation_id=order.id, operation_type='loss_report', owner_org_id=owner,
        actor_user_id=current.user_id, reviewer_person_id=current.person_id,
        authorization_version=current.authorization_version, request_id=command.request_id,
        idempotency_key_hash=posting._storage_hash('stock-loss-'+stage+'-review:'
            + posting._require_idempotency_key(command.idempotency_key)),
        request_hash=request.request_hash, submission_plan_hash=order.plan_hash,
        command_intent_jsonb=request.command_intent(), created_at=at)
    db.add(row); db.flush()
    append_audit_event(db, stream_key='inventory', actor_user_id=current.user_id, action=KIND,
        aggregate_type=AGGREGATE, aggregate_id=str(row.id), before_jsonb={}, after_jsonb=payload(row),
        request_id='stock-loss-review-seal:'+str(row.id), occurred_at=at, created_at=at)
    db.flush(); service.authorize(db, current, order)
    return verified(db, actor=current, row=row, order=order, stage=stage)
