"""Permanent HQ execution seals. Caller owns commit/rollback.

SQLite service tests cannot prove late-write exclusion; production activation
requires the new immutable table and bidirectional PG16 COMMIT guards.
"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import or_, select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent
from ..inventory_models import StockLocation
from . import inventory_posting as posting, stock_loss_sources as sources
from . import stock_loss_disposition_plan as plan, stock_loss_disposition_facts as facts
from . import stock_loss_facts as original
from .audit_chain import append_audit_event
from .work_order_query import _aware
from ..stock_operation_models import StockLossDispositionRequestSeal as Seal
from ..stock_loss_disposition_seal_schemas import (
    command_document, StockLossDispositionSealIn, StockLossDerivedReturnSealIn,
)

AGGREGATE = 'stock_loss_disposition_request_seal'
KIND = 'stock_loss.disposition_request_sealed'


def payload(row):
    return dict(flow=row.flow,operation_id=str(row.operation_id),line_id=str(row.line_id),
        headquarters_decision_id=str(row.headquarters_decision_id),owner_org_id=str(row.owner_org_id),
        executor_person_id=str(row.executor_person_id),authorization_version=row.authorization_version,
        request_id=row.request_id,request_reference=row.request_reference,
        disposition_key_hash=row.disposition_key_hash,return_key_hash=row.return_key_hash,
        request_hash=row.request_hash,plan_hash=row.plan_hash,command=row.command_jsonb)


def matches(actor, request, proof):
    keys=(proof['disposition_key_hash'],proof['return_key_hash'])
    return or_(Seal.disposition_key_hash.in_(keys),Seal.return_key_hash.in_(keys),
        (Seal.actor_user_id==actor.user_id)&(Seal.request_id==request.request_id))


def require_unsealed(db, *, actor, request, flow):
    proof=command_document(request,flow)
    if db.scalar(select(Seal.id).where(matches(actor,request,proof)).limit(1)) is not None:
        sources._fail('stock_loss_disposition_request_sealed','原处置请求已永久封存，不能再次执行')


def find(db, *, actor, request, flow):
    proof=command_document(request,flow)
    rows=tuple(db.scalars(select(Seal).where(matches(actor,request,proof)).limit(3)
        .execution_options(populate_existing=True)))
    if len(rows)>1:
        sources._fail('stock_loss_disposition_request_conflict','原处置请求绑定多个封存记录')
    row=rows[0] if rows else None
    if row is not None:
        if row.actor_user_id!=actor.user_id or row.executor_person_id!=actor.person_id:
            sources._fail('stock_loss_disposition_not_found','本人原处置封存不存在',404)
        if (row.flow!=flow or row.request_id!=request.request_id
                or row.headquarters_decision_id!=request.headquarters_decision_id
                or row.plan_hash!=request.expected_plan_hash
                or any(getattr(row,k)!=proof[k] for k in
                    ('request_reference','request_hash','disposition_key_hash','return_key_hash','command_jsonb'))):
            sources._fail('stock_loss_disposition_request_conflict','原处置命令与封存记录不一致')
    observed=tuple(db.scalars(select(AuditEvent.aggregate_id).where(AuditEvent.stream_key=='inventory',
        AuditEvent.aggregate_type==AGGREGATE,AuditEvent.actor_user_id==actor.user_id,
        AuditEvent.after_jsonb['request_id'].as_string()==request.request_id).limit(3)))
    if observed!=((str(row.id),) if row else ()):
        facts.invalid()
    return row


def verified(db, *, actor, row, order, line, decision, review):
    owner=db.get(StockLocation,order.source_location_id,populate_existing=True).owner_org_id
    if (row.operation_id!=order.id or row.operation_type!='loss_report' or row.line_id!=line.id
            or row.headquarters_decision_id!=decision.id or row.owner_org_id!=owner
            or row.actor_user_id!=actor.user_id or row.executor_person_id!=actor.person_id
            or row.authorization_version<1 or _aware(row.created_at)<_aware(review.created_at)
            or _aware(row.created_at)>datetime.now(timezone.utc)):
        facts.invalid()
    original.audit(db,actor=actor,stream='inventory',aggregate_type=AGGREGATE,identifier=row.id,
        action=KIND,request_id='stock-loss-disposition-seal:'+str(row.id),before={},after=payload(row))
    audit=original.single(db,AuditEvent,stream_key='inventory',aggregate_type=AGGREGATE,aggregate_id=str(row.id))
    if _aware(audit.created_at)!=_aware(row.created_at) or _aware(audit.occurred_at)!=_aware(row.created_at):
        facts.invalid()
    for model,kind,identifier in ((OutboxEvent,OutboxEvent.aggregate_type,OutboxEvent.aggregate_id),
            (StateTransitionEvent,StateTransitionEvent.aggregate_type,StateTransitionEvent.aggregate_id),
            (NotificationEvent,NotificationEvent.business_type,NotificationEvent.business_id)):
        if db.scalar(select(model.id).where(kind==AGGREGATE,identifier==str(row.id)).limit(1)) is not None:
            facts.invalid()
    return dict(lookup_status='sealed',retry_permitted=False,seal=dict(seal_id=str(row.id),flow=row.flow,
        operation_id=str(row.operation_id),line_id=str(row.line_id),headquarters_decision_id=str(row.headquarters_decision_id),
        executor_person_id=str(row.executor_person_id),request_id=row.request_id,request_hash=row.request_hash,
        plan_hash=row.plan_hash,sealed_at=_aware(row.created_at).isoformat(),stock_effect='none'))


def seal_execution_request(db, *, actor, request, flow):
    from .stock_loss_disposition_recovery import lookup_disposition_request
    if flow not in ('disposition','return'):raise ValueError('explicit execution flow required')
    schema=StockLossDispositionSealIn if flow=='disposition' else StockLossDerivedReturnSealIn
    request=schema.model_validate(request.model_dump())
    proof=request.proof();command=proof['command']
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db,(actor.user_id,))
    current,order,line,decision=plan.approved_line(db,actor=actor,request=command)
    if request.operator_person_id!=current.person_id:
        sources._fail('operator_mismatch','操作人必须为当前登录人员',403)
    answer=lookup_disposition_request(db,actor=current,request=command,flow=flow)
    if answer['lookup_status']!='not_found':return answer
    at=datetime.now(timezone.utc)
    row=Seal(id=uuid4(),flow=flow,operation_id=order.id,operation_type='loss_report',line_id=line.id,
        headquarters_decision_id=decision.id,owner_org_id=db.get(StockLocation,order.source_location_id).owner_org_id,
        actor_user_id=current.user_id,executor_person_id=current.person_id,authorization_version=current.authorization_version,
        request_id=command.request_id,request_reference=proof['request_reference'],
        disposition_key_hash=proof['disposition_key_hash'],return_key_hash=proof['return_key_hash'],
        request_hash=proof['request_hash'],plan_hash=command.expected_plan_hash,command_jsonb=proof['command_jsonb'],created_at=at)
    db.add(row);db.flush()
    append_audit_event(db,stream_key='inventory',actor_user_id=current.user_id,action=KIND,
        aggregate_type=AGGREGATE,aggregate_id=str(row.id),before_jsonb={},after_jsonb=payload(row),
        request_id='stock-loss-disposition-seal:'+str(row.id),occurred_at=at,created_at=at)
    db.flush();plan.authorize(db,current,order)
    return lookup_disposition_request(db,actor=current,request=command,flow=flow)
