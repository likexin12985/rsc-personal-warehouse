"""Database-owned key registration and immutable historical binding checks."""
from functools import lru_cache
from hashlib import sha256
import re

from sqlalchemy import or_, select, text
from app.return_condition_key_schema import NAME, ALIASES, KEY_DOMAINS, build_schema
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.work_order_query import _aware


@lru_cache(maxsize=1)
def table():
    return build_schema()[0].tables[NAME]


def invalid():
    sources._fail('return_condition_request_binding_unknown',
        '原纠正请求的持久键登记缺失或不一致，不能确认结果或重发',503)


def aliases(raw_key):
    return dict(key_token=sha256(('cloud_oam.loss.correction.key.v1\0'+raw_key).encode()).hexdigest(),
        **{name:posting._storage_hash(prefix+raw_key) for name,prefix in KEY_DOMAINS})


def verify(db, *, event):
    t=table()
    rows=tuple(db.execute(select(t).where(or_(t.c.event_id==event['id'],
        (t.c.actor_user_id==event['actor_user_id'])&(t.c.request_id==event['request_id']),
        t.c.condition_key_hash==event['idempotency_key_hash'])).limit(2)).mappings())
    if len(rows)!=1:
        invalid()
    row=rows[0]
    expected=dict(event_id=event['id'],case_id=event['case_id'],actor_user_id=event['actor_user_id'],
        actor_person_id=event['actor_person_id'],request_id=event['request_id'],request_hash=event['request_hash'],
        condition_key_hash=event['idempotency_key_hash'])
    if (any(row[k]!=v for k,v in expected.items()) or _aware(row['created_at'])!=_aware(event['created_at'])
            or any(not re.fullmatch('[a-f0-9]{64}',row[k]) for k in ('key_token',*ALIASES))
            or len({row[k] for k in ALIASES})!=len(ALIASES)):
        invalid()
    return dict(row)


def match_original(db, *, event, request):
    row=verify(db,event=event)
    if any(row[k]!=v for k,v in aliases(request.idempotency_key).items()):
        invalid()
    return row


def record(db, *, event, request):
    if db.get_bind().dialect.name!='postgresql':
        raise RuntimeError('condition durable key registrar requires PostgreSQL')
    db.execute(text('SELECT public.rsc_register_condition_request_key(:event,:key)'),
        {'event':event['id'],'key':request.idempotency_key})
    return match_original(db,event=event,request=request)
