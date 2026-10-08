"""Immutable business effects, separate from inventory and notification delivery."""
from types import SimpleNamespace

from sqlalchemy import and_, or_, select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from app.formal_services import stock_loss_facts as facts, stock_loss_sources as sources
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.notification_events import record_business_notification, target_manifest_hash
from app.formal_services.work_order_query import _aware


def coordinates(event):
    kind = 'stock_condition.' + event['kind']
    return 'stock_condition_event', kind, kind + ':' + str(event['id'])


def payload(case, event):
    return dict(schema_version='condition_result/1',case_id=str(case['id']),event_id=str(event['id']),
        inbound_line_id=str(case['inbound_line_id']),action=event['kind'],status=event['to_state'],
        quantity=format(case['quantity'],'.3f'),actor_user_id=event['actor_user_id'],
        actor_person_id=str(event['actor_person_id']),authorization_version=event['authorization_version'],
        request_id=event['request_id'],request_hash=event['request_hash'],plan_hash=event['plan_hash'],
        posting_transaction_id=str(event['posting_transaction_id']) if event['posting_transaction_id'] else None,
        posting_movement_id=str(event['posting_movement_id']) if event['posting_movement_id'] else None,
        stock_effect=event['movement_type'] or 'none',reason=event['reason'])


def record(db, *, case, event, recipient):
    aggregate,kind,key=coordinates(event); body=payload(case,event); at=event['created_at']
    append_audit_event(db,stream_key='inventory',actor_user_id=event['actor_user_id'],action=kind,
        aggregate_type=aggregate,aggregate_id=str(event['id']),before_jsonb={},after_jsonb=body,
        request_id=key,occurred_at=at,created_at=at)
    db.add(OutboxEvent(event_type=kind,aggregate_type=aggregate,aggregate_id=str(event['id']),
        payload_jsonb=body,idempotency_key=key,available_at=at,created_at=at,updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=aggregate,aggregate_id=str(event['id']),
        from_status=event['from_state'],to_status=event['to_state'],actor_id=event['actor_user_id'],
        reason=kind,idempotency_key=key,occurred_at=at,metadata_jsonb=body,created_at=at))
    record_business_notification(db,event_type=kind,business_type=aggregate,business_id=event['id'],
        dedup_key=key,payload=body,recipient_person_id=recipient,occurred_at=at,now=at)
    db.flush()



def _effect(db, model, aggregate, identifier, key):
    rows=tuple(db.scalars(select(model).where(or_(
        and_(model.aggregate_type==aggregate,model.aggregate_id==str(identifier)),
        model.idempotency_key==key)).limit(2).execution_options(populate_existing=True)))
    if len(rows)!=1:
        facts.invalid()
    row=rows[0]
    if (row.aggregate_type!=aggregate or row.aggregate_id!=str(identifier) or row.idempotency_key!=key):
        facts.invalid()
    return row


def verify(db, *, case, event, recipient):
    aggregate,kind,key=coordinates(event); body=payload(case,event); at=_aware(event['created_at'])
    audit= facts.single(db,AuditEvent,aggregate_type=aggregate,aggregate_id=str(event['id']))
    facts.audit(db,actor=SimpleNamespace(user_id=event['actor_user_id']),stream='inventory',
        aggregate_type=aggregate,identifier=event['id'],action=kind,request_id=key,before={},after=body)
    outbox=_effect(db,OutboxEvent,aggregate,event['id'],key)
    transition=_effect(db,StateTransitionEvent,aggregate,event['id'],key)
    if (outbox.event_type!=kind or outbox.payload_jsonb!=body
            or transition.from_status!=event['from_state'] or transition.to_status!=event['to_state']
            or transition.actor_id!=event['actor_user_id'] or transition.reason!=kind
            or transition.metadata_jsonb!=body):
        facts.invalid()
    note=facts.single(db,NotificationEvent,business_type=aggregate,business_id=str(event['id']))
    targets=tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id==note.id)))
    if (any(_aware(row.created_at)!=at for row in (audit,outbox,transition,note))
            or any(_aware(row.occurred_at)!=at for row in (audit,transition,note))
            or note.event_type!=kind or note.dedup_key!=key or sources._hash(note.payload_jsonb)!=sources._hash(body)
            or targets!=(recipient,) or note.target_manifest_sha256!=target_manifest_hash(targets)):
        facts.invalid()
    return body
