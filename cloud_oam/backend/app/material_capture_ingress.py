"""Authenticated material staging; no authority to project SKU or change stock."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
from uuid import UUID

from sqlalchemy import select, text

from .foundation_models import SourceSystem
from .material_capture_models import MaterialCaptureBinding as Binding, MaterialCaptureReceipt as Receipt
from .material_master_capture_evidence import (
    MAX_BYTES, FRESHNESS, MaterialMasterCaptureError, canonical, digest, fail, source_binding, validate_capture,
)

REQUEST_SCHEMA = 'rsc.oam_material_capture_request.v1'
KEY_PATTERN = re.compile(r'[A-Za-z0-9._:-]{1,128}')
HASH_PATTERN = re.compile(r'[a-f0-9]{64}')
MAX_REQUEST_BYTES = MAX_BYTES + 1024


def key_fingerprint(secret, source_instance):
    return hmac.new(secret.encode(), b'rsc.material-master-capture-key.v1\n' + source_instance.encode(), hashlib.sha256).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def clock(db):
    return aware(db.scalar(text('SELECT clock_timestamp()'))) if db.get_bind().dialect.name == 'postgresql' else datetime.now(timezone.utc)


def _parse(verified, operation, now):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: fail('duplicate_json_key')
            result[key] = value
        return result
    try:
        if len(verified.body) > MAX_REQUEST_BYTES: fail('document_too_large')
        payload = json.loads(verified.body, object_pairs_hook=unique, parse_constant=lambda _: fail('invalid_number'))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fail('invalid_document')
    keys = {'schema_version', 'operation', 'key_id', 'capture' if operation == 'receive' else 'capture_id'}
    if type(payload) is not dict or set(payload) != keys or payload['schema_version'] != REQUEST_SCHEMA \
            or payload['operation'] != operation or type(payload['key_id']) is not str \
            or not KEY_PATTERN.fullmatch(payload['key_id']):
        fail('invalid_request')
    if operation == 'receive':
        report = validate_capture(payload['capture'], expected_source_instance=verified.source_instance, now=now)
        capture_id = report.capture_id
    else:
        capture_id = payload['capture_id']
        try:
            if type(capture_id) is not str or str(UUID(capture_id)) != capture_id: fail('invalid_capture_id')
        except (ValueError, TypeError): fail('invalid_capture_id')
    source_binding(verified.source_instance)
    if any(not isinstance(value,datetime) or value.utcoffset() is None
           for value in (verified.authenticated_at,verified.signed_at)):
        fail('authentication_mismatch')
    if verified.batch_id != 'material-' + capture_id + '-' + operation \
            or verified.authentication_key_id != payload['key_id'] \
            or not HASH_PATTERN.fullmatch(verified.authentication_key_fingerprint or '') \
            or verified.authenticated_at is None or verified.signed_at is None \
            or not timedelta(0) <= now-aware(verified.authenticated_at) <= timedelta(minutes=5) \
            or abs(now-aware(verified.signed_at)) > timedelta(minutes=5) \
            or canonical(payload) != verified.body or digest(payload) != verified.body_sha256:
        fail('authentication_mismatch')
    return payload, UUID(capture_id)


def _begin(db):
    if db.new or db.dirty or db.deleted: fail('requires_clean_session')
    dialect = db.get_bind().dialect.name
    if dialect == 'postgresql':
        if db.execute(text('SELECT current_user, session_user')).one() != ('edge_inbox', 'edge_inbox'):
            fail('requires_edge_identity')
        if db.scalar(text('SHOW transaction_isolation')) != 'read committed': fail('requires_read_committed')
    elif dialect == 'sqlite':
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction: connection.exec_driver_sql('BEGIN')
    else: fail('unsupported_database')


def _binding(db, verified):
    if db.get_bind().dialect.name == 'postgresql':
        value = db.scalar(text('SELECT public.rsc_oam_material_capture_binding_0116(:source,:key,:fingerprint)'),
            dict(source=verified.source_instance, key=verified.authentication_key_id, fingerprint=verified.authentication_key_fingerprint))
        if type(value) is not dict: fail('binding_unavailable')
        return dict(id=UUID(value['id']), valid_from=datetime.fromisoformat(value['valid_from']),
                    valid_to=datetime.fromisoformat(value['valid_to']))
    row = db.scalar(select(Binding).where(Binding.source_instance == verified.source_instance,
        Binding.key_id == verified.authentication_key_id).execution_options(populate_existing=True))
    source = db.get(SourceSystem, row.source_system_id, populate_existing=True) if row else None
    now = clock(db)
    if row is None or row.revoked_at is not None or row.key_fingerprint != verified.authentication_key_fingerprint \
            or not aware(row.valid_from) <= now < aware(row.valid_to) \
            or source is None or source.code != 'oam' or source.mode != 'read_only' or not source.enabled:
        fail('binding_unavailable')
    return dict(id=row.id, valid_from=aware(row.valid_from), valid_to=aware(row.valid_to))


def _prove(row):
    payload = row.payload_jsonb
    if type(payload) is not dict or set(payload) != {'schema_version','operation','key_id','capture'} \
            or payload['schema_version'] != REQUEST_SCHEMA or payload['operation'] != 'receive': fail('receipt_changed')
    report = validate_capture(payload['capture'], expected_source_instance=row.source_instance, now=aware(row.created_at))
    if str(row.capture_id) != report.capture_id or row.key_id != payload['key_id'] \
            or row.request_id != 'material-' + report.capture_id + '-receive' \
            or row.capture_sha256 != report.capture_sha256 or row.records_sha256 != report.records_sha256 \
            or row.observed_count != report.observed_count or row.body_sha256 != digest(payload) \
            or aware(row.capture_started_at) != datetime.fromisoformat(payload['capture']['started_at']) \
            or aware(row.capture_completed_at) != datetime.fromisoformat(payload['capture']['completed_at']) \
            or abs(aware(row.signed_at)-aware(row.created_at)) > timedelta(minutes=5) \
            or not HASH_PATTERN.fullmatch(row.key_fingerprint):
        fail('receipt_changed')
    return report


def _reply(row, *, duplicate, now):
    report = _prove(row)
    return dict(ok=True, mode='staging_only', duplicate=duplicate, receipt_id=str(row.id), capture_id=report.capture_id,
        source_instance=row.source_instance, key_id=row.key_id, capture_sha256=row.capture_sha256,
        records_sha256=row.records_sha256, observed_count=row.observed_count, received_at=aware(row.created_at).isoformat(),
        channel_attested=True, within_freshness_target=timedelta(0) <= now-aware(row.capture_started_at) <= FRESHNESS,
        full_catalog_verified=False, source_authorized=False, master_source_evidence_verified=False,
        projection_published=False, start_ready=False)


def handle_material_capture(db, *, verified, operation):
    """Caller commits; status requires current channel credentials but permits old captures."""
    if operation not in ('receive', 'status'): fail('invalid_request')
    _begin(db)
    payload, capture_id = _parse(verified, operation, clock(db))
    with db.begin_nested():
        if db.get_bind().dialect.name == 'postgresql':
            db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                {'key':'rsc.material-capture:' + verified.source_instance + ':' + str(capture_id)})
        binding = _binding(db, verified)
        # Locks can consume the validity window; never cache the first clock observation.
        now = clock(db)
        _parse(verified, operation, now)
        if not binding['valid_from'] <= now < binding['valid_to']: fail('binding_expired_while_waiting')
        row = db.scalar(select(Receipt).where(Receipt.source_instance == verified.source_instance,
            Receipt.capture_id == capture_id).execution_options(populate_existing=True))
        if operation == 'status':
            if row is None:
                return dict(ok=True, mode='staging_only', status='not_found', source_instance=verified.source_instance,
                            capture_id=str(capture_id), retry_allowed=False)
            return dict(_reply(row, duplicate=True, now=now), status='received')
        if row is not None:
            if row.body_sha256 != verified.body_sha256 or canonical(row.payload_jsonb) != verified.body \
                    or row.key_fingerprint != verified.authentication_key_fingerprint or row.binding_id != binding['id']:
                fail('replay_conflict')
            return _reply(row, duplicate=True, now=now)
        capture = payload['capture']
        started, completed = (datetime.fromisoformat(capture[key]) for key in ('started_at', 'completed_at'))
        if not binding['valid_from'] <= started <= completed <= now < binding['valid_to']:
            fail('capture_outside_binding_window')
        row = Receipt(binding_id=binding['id'], source_instance=verified.source_instance, capture_id=capture_id,
            request_id=verified.batch_id, key_id=verified.authentication_key_id, key_fingerprint=verified.authentication_key_fingerprint,
            signed_at=verified.signed_at, capture_started_at=started, capture_completed_at=completed,
            observed_count=capture['observed_count'], records_sha256=capture['records_sha256'], capture_sha256=digest(capture),
            body_sha256=verified.body_sha256, payload_jsonb=payload, created_at=clock(db))
        if not row.created_at < binding['valid_to'] or row.created_at-started > FRESHNESS \
                or row.created_at-aware(verified.authenticated_at) > timedelta(minutes=5): fail('expired_while_waiting')
        db.add(row)
        db.flush()
        return _reply(row, duplicate=False, now=clock(db))
