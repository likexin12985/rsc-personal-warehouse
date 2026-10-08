"""Exact original execute/release input; private retention, never a lookup API.

The caller must prove the complete historical graph and current read scope.
Saved scan identity survives later master changes; this helper grants neither
current stock access nor permission to replay a missing request.
"""
from functools import lru_cache
from uuid import UUID
from sqlalchemy import select
from app.inventory_models import FormalMaterial, InventorySerial
from app.return_condition_settlement_requests import validate_settlement
from app.return_condition_settlement_input_schema import NAME, SCANS, build_schema
from app.formal_services import inventory_posting as posting, stock_loss_sources as sources
from app.formal_services.work_order_query import _aware


@lru_cache(maxsize=1)
def tables():
    return build_schema()[0].tables


def canonical(request):
    request = validate_settlement(request)
    return dict(schema_version='condition_settlement_input/1', action=request.action,
        case_id=str(request.case_id), expected_event_id=str(request.expected_event_id),
        expected_event_hash=request.expected_event_hash, reason=request.reason,
        request_id=request.request_id,
        idempotency_key_hash=posting._storage_hash('stock-condition:' + request.idempotency_key),
        serial_verifications=[s.model_dump(mode='json') for s in sorted(request.serial_verifications, key=lambda s: str(s.serial_id))],
        evidence_file_ids=sorted(str(v) for v in request.evidence_file_ids))


def invalid():
    sources._fail('return_condition_settlement_input_unknown',
        '原执行或释放输入缺失或不一致，不能判定结果或重发', 503)


def expected_document(*, row, case, event, previous, selected, scans, files):
    """Compare independent persisted relationships, without reading today's labels."""
    try:
        if (event['kind'] not in ('execute', 'release') or event['case_id'] != case['id']
                or previous['id'] != event['previous_event_id'] or previous['case_id'] != case['id']
                or event['decision_event_id'] != previous['id'] or event['decision_kind'] != previous['kind']
                or row['event_id'] != event['id'] or row['case_id'] != case['id'] or row['kind'] != event['kind']
                or row['previous_event_id'] != previous['id'] or row['previous_kind'] != previous['kind']
                or row['previous_request_hash'] != previous['request_hash']
                or _aware(row['created_at']) != _aware(event['created_at'])):
            invalid()
        for key in ('actor_user_id', 'actor_person_id', 'authorization_version', 'request_id', 'idempotency_key_hash'):
            if row[key] != event[key]: invalid()
        if tuple(s['serial_id'] for s in scans) != tuple(sorted(selected, key=str)):
            invalid()
        for scan in scans:
            if (scan['event_id'] != event['id'] or scan['case_id'] != case['id']
                    or scan['sku_code'] != row['material_sku_code']
                    or _aware(scan['created_at']) != _aware(event['created_at'])):
                invalid()
        return dict(schema_version='condition_settlement_input/1', action=event['kind'],
            case_id=str(case['id']), expected_event_id=str(previous['id']),
            expected_event_hash=previous['request_hash'], reason=event['reason'], request_id=event['request_id'],
            idempotency_key_hash=event['idempotency_key_hash'],
            serial_verifications=[dict(serial_id=str(s['serial_id']), sku_code=s['sku_code'],
                serial_no=s['serial_no'], qr_code=s['qr_code']) for s in scans],
            evidence_file_ids=sorted(str(v) for v in files))
    except (KeyError, TypeError, ValueError):
        invalid()


def verify(db, *, case, event):
    schema = tables(); registry = schema[NAME]; captured = schema[SCANS]
    row = db.execute(select(registry).where(registry.c.event_id == event['id'])).mappings().one_or_none()
    previous = db.execute(select(schema['stock_condition_events']).where(
        schema['stock_condition_events'].c.id == event['previous_event_id'])).mappings().one_or_none()
    if row is None or previous is None: invalid()
    scans = tuple(db.execute(select(captured).where(captured.c.event_id == event['id'])
        .order_by(captured.c.serial_id)).mappings())
    selected = tuple(db.scalars(select(schema['stock_condition_serials'].c.serial_id).where(
        schema['stock_condition_serials'].c.case_id == case['id'])))
    files = tuple(db.scalars(select(schema['stock_condition_files'].c.file_id).where(
        schema['stock_condition_files'].c.event_id == event['id'])))
    expected = expected_document(row=row, case=case, event=event, previous=previous,
        selected=selected, scans=scans, files=files)
    if row['input_jsonb'] != expected or row['input_hash'] != posting._canonical_hash(expected): invalid()
    return dict(row)


def match_original(db, *, request, case, event):
    row = verify(db, case=case, event=event)
    if canonical(request) != row['input_jsonb']:
        sources._fail('return_condition_settlement_input_conflict', '回查必须使用完整且一致的原执行或释放请求', 409)
    return row['input_hash']


def record(db, *, request, case, event):
    request = validate_settlement(request)
    material = db.get(FormalMaterial, UUID(case['source_jsonb']['material_id']), populate_existing=True)
    if material is None: invalid()
    document = canonical(request)
    previous = db.execute(select(tables()['stock_condition_events']).where(
        tables()['stock_condition_events'].c.id == request.expected_event_id)).mappings().one_or_none()
    if previous is None: invalid()
    scans = []
    for scan in request.serial_verifications:
        current = db.get(InventorySerial, scan.serial_id, populate_existing=True)
        if (current is None or current.material_id != material.id
                or (scan.sku_code, scan.serial_no, scan.qr_code) != (material.sku_code, current.serial_no, current.qr_code)):
            invalid()
        scans.append(dict(event_id=event['id'], case_id=case['id'], created_at=event['created_at'],
            serial_id=scan.serial_id, sku_code=scan.sku_code, serial_no=scan.serial_no, qr_code=scan.qr_code))
    db.execute(tables()[NAME].insert(), dict(event_id=event['id'], case_id=case['id'], kind=event['kind'],
        previous_event_id=previous['id'], previous_kind=previous['kind'], previous_request_hash=previous['request_hash'],
        created_at=event['created_at'], actor_user_id=event['actor_user_id'], actor_person_id=event['actor_person_id'],
        authorization_version=event['authorization_version'], request_id=event['request_id'],
        idempotency_key_hash=event['idempotency_key_hash'], material_sku_code=material.sku_code,
        input_jsonb=document, input_hash=posting._canonical_hash(document)))
    if scans: db.execute(tables()[SCANS].insert(), scans)
    return verify(db, case=case, event=event)
