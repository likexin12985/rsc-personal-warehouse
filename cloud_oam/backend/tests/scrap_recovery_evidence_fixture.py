"""Synthetic upload and exact Python event formats for native SQL proofs.

Only file/audit/event schemas are real here. This does not call OSS or imply
that the old stock/auth/file admission triggers have been installed.
"""
from datetime import timedelta
import hashlib
from uuid import uuid4, UUID
from sqlalchemy import select, text
from app.formal_services.stock_scrap import recovery_events
from app.formal_services.audit_chain import calculate_audit_event_hash
from app.formal_services.notification_events import target_manifest_hash
from formal_file_integrity import (FileObject, FileUploadIntentInput, StoredObjectHead,
    _prepare_upload, _upload_request_hash, _storage_key, _head_manifest_sha256,
    _canonical_hash, _validate_intent_metadata)

EVENT_TABLES = ('audit_chain_heads', 'audit_events', 'state_transition_events', 'outbox_events',
    'notification_events', 'notification_person_targets')


def extend(metadata, full):
    for name in ('files',):
        metadata.remove(metadata.tables[name])
        full.tables[name].to_metadata(metadata)
    for name in EVENT_TABLES:
        full.tables[name].to_metadata(metadata)
    # Only the fields used to prove approval does not post stock; other stock
    # columns remain the explicit minimal parent fixture, not a fake full DB.
    from sqlalchemy import Column
    for name in ('idempotency_key_hash', 'source_document_type', 'source_document_id'):
        c = full.tables['inventory_transactions'].c[name]
        metadata.tables['inventory_transactions'].append_column(Column(name, c.type, nullable=True))


def seed_file(db, metadata, *, identifier, user, person, at):
    prepared = _prepare_upload(FileUploadIntentInput(purpose='stock_loss_evidence',
        original_filename='找回凭证.png', mime_type='image/png', size_bytes=23,
        sha256=_canonical_hash(str(identifier))), maximum_size_bytes=120*1024*1024)
    key = _storage_key(prepared.purpose, identifier)
    head = StoredObjectHead(storage_key=key, size_bytes=prepared.size_bytes, mime_type=prepared.mime_type,
        metadata={'file-id': str(identifier), 'sha256': prepared.sha256, 'extra-provider-key': 'retained'}, etag='synthetic-etag')
    meta = dict(schema='cloud_oam.formal_file_upload_intent.v1', file_id=str(identifier), storage_key=key,
        purpose=prepared.purpose, provider='aliyun_oss_v2', uploader_user_id=user, uploader_person_id=str(person),
        authorization_version=1, idempotency_key_hash=_canonical_hash(uuid4().hex), request_sha256=_upload_request_hash(prepared),
        completion=dict(etag_sha256=hashlib.sha256(head.etag.encode()).hexdigest(),
            head_manifest_sha256=_head_manifest_sha256(head), verified_at=(at-timedelta(seconds=1)).isoformat()))
    row = dict(id=identifier, storage_key=key, sha256=prepared.sha256, size_bytes=prepared.size_bytes,
        mime_type=prepared.mime_type, original_filename=prepared.original_filename, uploaded_by=user, status='available',
        metadata_jsonb=meta, created_at=at-timedelta(seconds=2))
    _validate_intent_metadata(FileObject(**row), allow_completed=True)
    db.execute(metadata.tables['files'].insert(), row)
    payload = dict(mime_type=row['mime_type'], purpose='stock_loss_evidence',sha256=row['sha256'],size_bytes=row['size_bytes'])
    for action, when, before, after in [
            ('file.upload_intent.created',row['created_at'],None,payload | {'status':'pending'}),
            ('file.upload_completed',at-timedelta(seconds=1),{'status':'pending'},payload | {
                'status':'available','head_manifest_sha256':meta['completion']['head_manifest_sha256']})]:
        audit = dict(id=uuid4(), actor_user_id=user, action=action, aggregate_type='formal_file',aggregate_id=str(identifier),
            before_jsonb=before,after_jsonb=after,request_id=uuid4().hex,occurred_at=when,created_at=when)
        append_audit(db,metadata,audit)
    return row


def append_audit(db, metadata, row):
    head = metadata.tables['audit_chain_heads']
    current = db.execute(select(head).where(head.c.stream_key=='inventory').with_for_update()).mappings().one()
    row.update(stream_key='inventory',stream_version=current['version']+1,previous_hash=current['last_hash'])
    row['event_hash']=calculate_audit_event_hash(event_id=row['id'], **{
        k:v for k,v in row.items() if k not in ('id','created_at','stream_version')})
    db.execute(metadata.tables['audit_events'].insert(),row)
    db.execute(head.update().where(head.c.id==current['id']).values(version=row['stream_version'],
        last_event_id=row['id'],last_hash=row['event_hash']))


def seed_head(db, metadata):
    db.execute(metadata.tables['audit_chain_heads'].insert(), dict(id=uuid4(), stream_key='inventory', version=0,
        last_hash=None,last_event_id=None))


def insert_events(db, metadata, stage, row, *, recipient=None, omit=None, mutate=None):
    # Match the real service's inventory -> audit lock order. Head updates below
    # belong only to this synthetic gate; production retains 0017 guards.
    db.execute(text('SELECT id FROM inventory_ledger_heads FOR UPDATE')).all()
    body = recovery_events.payload(row, stage)
    aggregate, kind, key = recovery_events.coordinates(row, stage)
    before, after = recovery_events.state(row, stage)
    at, identifier = row['created_at'], str(row['id'])
    if recipient is None:
        if stage == 'apply': recipient = row['actor_person_id']
        else:
            app = metadata.tables['stock_scrap_recovery_requests']
            recipient = db.scalar(select(app.c.actor_person_id).where(app.c.id == row['recovery_request_id']))
    head = metadata.tables['audit_chain_heads']
    current = db.execute(select(head).where(head.c.stream_key == 'inventory').with_for_update()).mappings().one()
    audit = dict(id=uuid4(), stream_key='inventory', stream_version=current['version']+1,
        actor_user_id=row['actor_user_id'], action=kind, aggregate_type=aggregate, aggregate_id=identifier,
        before_jsonb={}, after_jsonb=body, request_id=key, previous_hash=current['last_hash'], occurred_at=at,created_at=at)
    audit['event_hash'] = calculate_audit_event_hash(event_id=audit['id'], **{
        k:v for k,v in audit.items() if k not in ('id','created_at','stream_version')})
    state = dict(id=uuid4(), aggregate_type=aggregate, aggregate_id=identifier, from_status=before,
        to_status=after, actor_id=row['actor_user_id'], reason=kind,idempotency_key=key,
        occurred_at=at,metadata_jsonb=body,created_at=at)
    outbox = dict(id=uuid4(), event_type=kind,aggregate_type=aggregate,aggregate_id=identifier,
        payload_jsonb=body,idempotency_key=key,available_at=at,created_at=at,updated_at=at,status='pending',attempts=0)
    note = dict(id=uuid4(),event_type=kind,business_type=aggregate,business_id=identifier,dedup_key=key,
        payload_jsonb=body,status='pending',occurred_at=at,created_at=at,target_manifest_sha256=target_manifest_hash((recipient,)))
    target = dict(id=uuid4(),event_id=note['id'],person_id=recipient,created_at=at)
    rows = dict(audit_events=audit,state_transition_events=state,outbox_events=outbox,
        notification_events=note,notification_person_targets=target)
    if mutate: mutate(rows)
    for name, value in rows.items():
        if name == omit or isinstance(omit,tuple) and name in omit: continue
        db.execute(metadata.tables[name].insert(), value)
        if name == 'audit_events':
            db.execute(head.update().where(head.c.id == current['id']).values(version=audit['stream_version'],
                last_hash=audit['event_hash'],last_event_id=audit['id']))
    return rows
