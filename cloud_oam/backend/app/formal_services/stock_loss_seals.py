"""Permanently close an exact unresolved request, never replay inventory.

The caller owns commit/rollback; the formal router acknowledges after commit.
PostgreSQL independently proves authority, audit and late-command exclusion.
"""
from datetime import datetime, timezone
import re
from uuid import uuid4

from sqlalchemy import or_, select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent
from ..stock_operation_models import StockLossRequestSeal as Seal
from ..stock_loss_schemas import StockLossSealIn, StockLossRequestLookupIn, StockLossRequestSealedOut, StockLossSealOut
from . import inventory_posting as posting, stock_loss_sources as sources, stock_loss_facts as facts
from .audit_chain import append_audit_event, AuditChainError
from .work_order_query import _aware

AGGREGATE='stock_loss_request_seal'
KIND='stock_loss.request_sealed'


def payload(row):
    return dict(operator_person_id=str(row.operator_person_id),source_location_id=str(row.source_location_id),
        authorization_version=row.authorization_version,request_id=row.request_id,
        idempotency_key_hash=row.idempotency_key_hash,request_hash=row.request_hash,plan_hash=row.plan_hash)


def require_unsealed(db, *, actor, request_id, key=None):
    match=(Seal.actor_user_id==actor.user_id)&(Seal.request_id==request_id)
    if key is not None:match=or_(match,Seal.idempotency_key_hash==key)
    if db.scalar(select(Seal.id).where(match).limit(1)) is not None:
        sources._fail('stock_loss_request_sealed','原请求已永久封存，不能再次执行；请先核验封存记录')


def find(db, *, actor, request, key):
    rows=tuple(db.scalars(select(Seal).where(or_(Seal.idempotency_key_hash==key,
        (Seal.actor_user_id==actor.user_id)&(Seal.request_id==request.request_id)))
        .limit(3).execution_options(populate_existing=True)))
    if len(rows)>1:sources._fail('stock_loss_request_conflict','原请求已绑定其他封存记录')
    row=rows[0] if rows else None
    if row is not None:
        if row.actor_user_id!=actor.user_id or row.operator_person_id!=actor.person_id:
            sources._fail('stock_loss_not_found','本人原报损记录不存在',404)
        if (row.request_id!=request.request_id or row.idempotency_key_hash!=key
                or row.request_hash!=request.request_hash or row.plan_hash!=request.expected_plan_hash):
            sources._fail('stock_loss_request_conflict','原请求已按其他内容封存，请保留原请求核验')
    return row


def verified(db, *, actor, row):
    try:
        if (row.actor_user_id!=actor.user_id or row.operator_person_id!=actor.person_id
                or row.authorization_version<1 or row.source_location_id is None
                or not re.fullmatch(r'[A-Za-z0-9._:-]{8,160}',row.request_id)
                or any(not re.fullmatch(r'[a-f0-9]{64}',getattr(row,k))
                    for k in ('request_hash','plan_hash','idempotency_key_hash'))
                or row.request_reference!=posting._request_reference(row.request_id)):
            facts.invalid()
        facts.audit(db,actor=actor,stream='inventory',aggregate_type=AGGREGATE,identifier=row.id,
            action=KIND,request_id='stock-loss-seal:'+str(row.id),before={},after=payload(row))
        event=facts.single(db,AuditEvent,stream_key='inventory',aggregate_type=AGGREGATE,aggregate_id=str(row.id))
        if _aware(event.occurred_at)!=_aware(row.created_at) or _aware(event.created_at)!=_aware(row.created_at):facts.invalid()
        return StockLossRequestSealedOut(seal=StockLossSealOut(seal_id=row.id,operator_person_id=row.operator_person_id,
            source_location_id=row.source_location_id,request_id=row.request_id,request_hash=row.request_hash,
            plan_hash=row.plan_hash,sealed_at=_aware(row.created_at)))
    except (ValueError,TypeError,AttributeError,AuditChainError):
        facts.invalid()


def seal_loss_request(db, *, actor, request):
    from .stock_loss_recovery import lookup_loss_request
    request=StockLossSealIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db,(actor.user_id,))
    current=sources.authorize(db,actor)
    original=lookup_loss_request(db,actor=current,
        request=StockLossRequestLookupIn(**request.model_dump(exclude={'source_location_id'})))
    if original.lookup_status!='not_found':
        location=original.submission.source_location_id if original.lookup_status=='found' else original.seal.source_location_id
        if location!=request.source_location_id:
            sources._fail('stock_loss_request_conflict','原请求来源位置与当前坐标不一致')
        return original
    # No available quantity is required for a tombstone. The current personal
    # warehouse and custody still must be real and established.
    warehouse=sources._warehouse(db,current)
    if warehouse.location_id!=request.source_location_id:
        sources._fail('stock_loss_source_invalid','封存请求必须绑定本人准确个人仓')
    at=datetime.now(timezone.utc)
    row=Seal(id=uuid4(),actor_user_id=current.user_id,operator_person_id=current.person_id,
        source_location_id=request.source_location_id,authorization_version=current.authorization_version,
        request_id=request.request_id,request_reference=posting._request_reference(request.request_id),
        idempotency_key_hash=posting._storage_hash(request.idempotency_key),request_hash=request.request_hash,
        plan_hash=request.expected_plan_hash,created_at=at)
    db.add(row);db.flush()
    append_audit_event(db,stream_key='inventory',actor_user_id=current.user_id,action=KIND,
        aggregate_type=AGGREGATE,aggregate_id=str(row.id),before_jsonb={},after_jsonb=payload(row),
        request_id='stock-loss-seal:'+str(row.id),occurred_at=at,created_at=at)
    db.flush();sources.authorize(db,current)
    return verified(db,actor=current,row=row)
