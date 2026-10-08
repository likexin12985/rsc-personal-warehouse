"""Exact seal evidence using SELECT only, never current write/custody proof."""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID
from sqlalchemy import or_, select
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent
from app.stock_scrap_seal_schema import ANCHORS, ALIASES, NAME
from app.stock_scrap_schemas import ScrapExecute
from app.formal_services import stock_loss_sources as sources, stock_loss_facts as original
from app.formal_services.work_order_query import _aware
from .lookup_coordinates import keys, unknown
from .tables import tables

AGGREGATE = 'stock_scrap_request_seal'


def table():
    return tables()[NAME]


def candidates(db, *, actor, command):
    seal = table()
    hashes = keys(command)
    token = sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest()
    where = [seal.c.key_token == token,
        (seal.c.actor_user_id == actor.user_id) & (seal.c.request_id == command.request_id)]
    where.extend(seal.c[name].in_(hashes) for name in ALIASES)
    return [dict(row) for row in db.execute(select(seal).where(or_(*where)).limit(3)).mappings()]


def find(db, *, actor, command, kind, document, conflict):
    rows = candidates(db, actor=actor, command=command)
    if len(rows)>1:
        unknown()
    row = rows[0] if rows else None
    if row is not None:
        hashes = keys(command)
        token = sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest()
        expected = dict(actor_user_id=actor.user_id, actor_person_id=actor.person_id,
            request_id=command.request_id, command_jsonb=document, request_hash=sources._hash(document),
            kind=kind, key_token=token, idempotency_key_hash=hashes[4 if type(command) is ScrapExecute else 3],
            plan_hash=getattr(command,'expected_plan_hash',None), **dict(zip(ALIASES,hashes)))
        if any(row[name]!=value for name,value in expected.items()):
            conflict()
    return row


def payload(row):
    # Matches the database-owned canonical UTC payload; no raw key or aliases.
    result = {k: str(v) if isinstance(v,UUID) else v for k,v in row.items()
        if k not in ('key_token','idempotency_key_hash',*ALIASES)}
    result['created_at'] = _aware(row['created_at']).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    return result | dict(request_state='sealed', retry_allowed=False, stock_effect='none')


def verified(db, *, actor, command, row, source_fields, source_time):
    expected = {name: None for name in ANCHORS}
    expected.update(plan_hash=getattr(command,'expected_plan_hash',None), scrap_disposition='scrap',
        reason=command.execution_reason if type(command) is ScrapExecute else command.reason)
    expected.update(source_fields)
    if (any(row[name]!=value for name,value in expected.items())
            or type(row['authorization_version']) is not int or row['authorization_version']<1
            or not isinstance(row['id'],UUID) or row['id'].int==0
            or not _aware(source_time)<=_aware(row['created_at'])<=datetime.now(timezone.utc)):
        unknown()
    identifier = str(row['id'])
    audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_id==identifier).limit(2)))
    if len(audits)!=1:
        unknown()
    original.audit(db,actor=actor,stream='inventory',aggregate_type=AGGREGATE,identifier=row['id'],
        action='seal_scrap_request',request_id='scrap-seal:'+identifier,before={},after=payload(row))
    if (_aware(audits[0].created_at)!=_aware(row['created_at'])
            or _aware(audits[0].occurred_at)!=_aware(row['created_at'])):
        unknown()
    for model,field in ((OutboxEvent,OutboxEvent.aggregate_id),(StateTransitionEvent,StateTransitionEvent.aggregate_id),
                        (NotificationEvent,NotificationEvent.business_id)):
        if db.scalar(select(model.id).where(field==identifier).limit(1)) is not None:
            unknown()
    return dict(request_state='sealed',retry_allowed=False,result_scope='closed_original_request',
        request_id=command.request_id,request_hash=row['request_hash'],result=None,
        seal=dict(seal_id=identifier,kind=row['kind'],loss_operation_id=str(row['loss_operation_id']),
            loss_line_id=str(row['loss_line_id']),root_disposition_id=str(row['root_disposition_id']) if row['root_disposition_id'] else None,
            sealed_at=payload(row)['created_at'],stock_effect='none'))
