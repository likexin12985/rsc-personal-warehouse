"""Persist and compare exact original submit input, without replay authority.

These internal helpers are not request recovery or read authorization. The
caller must prove current read scope and the complete historical graph before
returning any business result. A missing input is unknown, never permission to
repeat a write. Historical comparison uses saved SKU text, not today's label.
"""
from functools import lru_cache
from uuid import UUID
from sqlalchemy import select
from app.inventory_models import FormalMaterial
from app.return_condition_request_schema import build_schema, NAME
from app.return_condition_requests import validate_submit
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.work_order_query import _aware
from .return_condition_identity import _quantity


@lru_cache(maxsize=1)
def table():
    return build_schema()[0].tables[NAME]


def canonical(request):
    request=validate_submit(request)
    return dict(schema_version='condition_input/1',action=request.action,
        inbound_line_id=str(request.inbound_line_id),expected_source_hash=request.expected_source_hash,
        quantity=_quantity(request.quantity),
        serial_verifications=[s.model_dump(mode='json') for s in sorted(request.serial_verifications,key=lambda s:str(s.serial_id))],
        evidence_file_ids=sorted(str(v) for v in request.evidence_file_ids),reason=request.reason,
        request_id=request.request_id,idempotency_key_hash=posting._storage_hash('stock-condition:'+request.idempotency_key))


def invalid():
    sources._fail('return_condition_original_input_unknown','原纠正请求记录不完整或不一致，不能判定结果或重发',503)


def record(db, *, request, case, event):
    material=db.get(FormalMaterial,UUID(case['source_jsonb']['material_id']),populate_existing=True)
    if material is None:
        invalid()
    document=canonical(request)
    db.execute(table().insert(),dict(event_id=event['id'],created_at=event['created_at'],
        actor_user_id=event['actor_user_id'],actor_person_id=event['actor_person_id'],
        authorization_version=event['authorization_version'],request_id=event['request_id'],
        idempotency_key_hash=event['idempotency_key_hash'],material_sku_code=material.sku_code,
        input_jsonb=document,input_hash=posting._canonical_hash(document)))
    return verify(db,case=case,event=event)


def verify(db, *, case, event):
    """Verify immutable input-to-event bindings; no current actor reauthorization."""
    schema=table().metadata.tables
    row=db.execute(select(table()).where(table().c.event_id==event['id'])).mappings().one_or_none()
    if row is None or event['kind']!='submit' or case['submit_event_id']!=event['id']:
        invalid()
    for key in ('actor_user_id','actor_person_id','authorization_version','request_id','idempotency_key_hash'):
        if row[key]!=event[key]: invalid()
    if _aware(row['created_at'])!=_aware(event['created_at']): invalid()
    serial_table=schema['stock_condition_serials']; file_table=schema['stock_condition_files']
    serial_ids=tuple(db.scalars(select(serial_table.c.serial_id).where(serial_table.c.case_id==case['id']).order_by(serial_table.c.serial_id)))
    files=tuple(db.scalars(select(file_table.c.file_id).where(file_table.c.event_id==event['id']).order_by(file_table.c.file_id)))
    try:
        originals={UUID(s['serial_id']):s for s in case['source_jsonb']['serials']}
        scans=[dict(serial_id=str(s),sku_code=row['material_sku_code'],serial_no=originals[s]['serial_no'],
            qr_code=originals[s]['qr_code']) for s in serial_ids]
        expected=dict(schema_version='condition_input/1',action='submit_return_condition',
            inbound_line_id=str(case['inbound_line_id']),expected_source_hash=case['source_hash'],
            quantity=_quantity(case['quantity']),serial_verifications=scans,
            evidence_file_ids=[str(f) for f in files],reason=event['reason'],request_id=event['request_id'],
            idempotency_key_hash=event['idempotency_key_hash'])
        if row['input_jsonb']!=expected or row['input_hash']!=posting._canonical_hash(expected): invalid()
    except (KeyError,TypeError,ValueError):
        invalid()
    return dict(row)


def match_original(db, *, request, case, event):
    row=verify(db,case=case,event=event)
    if canonical(request)!=row['input_jsonb']:
        sources._fail('return_condition_original_input_conflict','回查必须使用完整且一致的原纠正请求',409)
    return row['input_hash']
