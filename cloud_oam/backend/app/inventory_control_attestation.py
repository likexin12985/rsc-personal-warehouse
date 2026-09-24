"""Bind authenticated collector claims to immutable staging and preparation.

HMAC proves the configured channel, not truth inside OAM or catalogue approval.
An attestation never publishes inventory or grants opening readiness.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator
from sqlalchemy import select, text

from .inventory_control_attestation_models import InventoryControlCaptureAttestation as Receipt
from .inventory_control_models import InventoryControlPreparation as Preparation, InventoryControlCaptureChain as Chain, InventoryControlCatalogVersion as Catalog
from .inventory_control_preparation import _aware, _begin_outer, _canonical, _sha, _json, read_inventory_control_preparation
from .models import ExternalSyncSnapshot

SCHEMA = 'rsc.inventory_control_attestation.v1'
CONTRACT = 'rsc.inventory_control_capture.v1'
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._:-]+$')]
Coordinate = Annotated[str, StringConstraints(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]


class CaptureAttestationError(RuntimeError):
    pass


def fail(code):
    raise CaptureAttestationError('control_attestation_' + code)


def instant(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.utcoffset() is None: fail('invalid_time')
        return stamp.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError): fail('invalid_time')


class CaptureAttestationIn(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['rsc.inventory_control_attestation.v1']
    collector_contract: Literal['rsc.inventory_control_capture.v1']
    key_id: Identifier
    source_system: Literal['starcharge_oam']
    source_instance: Identifier
    company_id: Coordinate
    org_code: Coordinate
    scope_key: Coordinate
    snapshot_id: Identifier
    snapshot_at: str
    sync_mode: Literal['full', 'incremental']
    catalog_revision: Coordinate
    target_region_code: Coordinate
    source_binding_sha256: Digest
    catalog_sha256: Digest
    capture_chain_sha256: Digest
    capture_started_at: str
    capture_completed_at: str

    @field_validator('snapshot_at', 'capture_started_at', 'capture_completed_at')
    @classmethod
    def utc_time(cls, value):
        try:
            return instant(value).isoformat().replace('+00:00', 'Z')
        except CaptureAttestationError as exc:
            raise ValueError('control_attestation_invalid_time') from exc


def _clock(db):
    return _aware(db.scalar(text('SELECT clock_timestamp()'))) if db.get_bind().dialect.name == 'postgresql' else datetime.now(timezone.utc)


def _snapshot_matches(row, payload):
    return row is not None and row.status == 'complete' and row.completed_at is not None and all(
        getattr(row, field) == payload[field] for field in ('source_system','source_instance','company_id','org_code','scope_key','snapshot_id','sync_mode')) \
        and _aware(row.snapshot_at) == instant(payload['snapshot_at']) \
        and {e['entity_type'] for e in _json(row.manifest_json)['entities']} == {'inventory'} \
        and payload['source_binding_sha256'] == _sha({key:payload[key] for key in ('source_system','source_instance','company_id','org_code','scope_key')})


def _receipt(row, snapshot, *, duplicate):
    return dict(ok=True, duplicate=duplicate, mode='staging_only', attestation_id=str(row.id),
        snapshot_id=snapshot.snapshot_id, source_instance=row.source_instance, key_id=row.key_id,
        payload_sha256=row.payload_sha256, capture_attested=True, projection_published=False, start_ready=False)


def accept_inventory_control_attestation(db, *, payload, verified):
    """Caller commits; exact scoped lock, immutable receipt, no secret persistence."""
    if db.new or db.dirty or db.deleted: fail('requires_clean_session')
    if db.get_bind().dialect.name == 'postgresql':
        if db.execute(text('SELECT current_user, session_user')).one() != ('edge_inbox','edge_inbox'): fail('requires_edge_identity')
    elif db.get_bind().dialect.name != 'sqlite': fail('unsupported_database')
    body = payload.model_dump(mode='json')
    digest = _sha(body)
    now = _clock(db)
    if verified.source_instance != body['source_instance'] or verified.batch_id != body['snapshot_id'] + '-capture' \
            or verified.authentication_key_id != body['key_id'] or not re.fullmatch('[a-f0-9]{64}', verified.authentication_key_fingerprint or '') \
            or verified.authenticated_at is None or verified.signed_at is None \
            or not timedelta(0) <= now - verified.authenticated_at <= timedelta(minutes=5) \
            or abs(now - verified.signed_at) > timedelta(minutes=5) \
            or verified.body != _canonical(body).encode() or verified.body_sha256 != digest \
            or hashlib.sha256(verified.body).hexdigest() != digest:
        fail('authentication_mismatch')
    started, completed, snapshot_at = (instant(body[k]) for k in ('capture_started_at','capture_completed_at','snapshot_at'))
    if not started <= completed <= snapshot_at <= now or now - started > timedelta(minutes=45): fail('capture_time_invalid')
    _begin_outer(db)
    with db.begin_nested():
        snapshot = db.scalar(select(ExternalSyncSnapshot).where(ExternalSyncSnapshot.source_instance == body['source_instance'],
            ExternalSyncSnapshot.snapshot_id == body['snapshot_id']).with_for_update().execution_options(populate_existing=True))
        if not _snapshot_matches(snapshot, body): fail('snapshot_mismatch')
        existing = db.scalar(select(Receipt).where(Receipt.snapshot_ref_id == snapshot.id).execution_options(populate_existing=True))
        if existing is not None:
            if existing.payload_sha256 != digest or existing.payload_jsonb != body \
                    or existing.source_instance != verified.source_instance or existing.request_id != verified.batch_id \
                    or existing.key_fingerprint != verified.authentication_key_fingerprint or existing.body_sha256 != digest \
                    or existing.key_id != body['key_id'] or existing.entity_type != 'inventory' \
                    or not snapshot_at <= _aware(existing.created_at) <= now \
                    or abs(_aware(existing.created_at)-_aware(existing.signed_at)) > timedelta(minutes=5): fail('replay_conflict')
            return _receipt(existing, snapshot, duplicate=True)
        occupied = db.scalar(select(Receipt.id).where(Receipt.source_instance == body['source_instance'], Receipt.request_id == verified.batch_id))
        if occupied is not None: fail('request_conflict')
        row = Receipt(snapshot_ref_id=snapshot.id, source_instance=body['source_instance'], request_id=verified.batch_id,
            key_id=body['key_id'], key_fingerprint=verified.authentication_key_fingerprint, signed_at=verified.signed_at,
            payload_jsonb=body, payload_sha256=digest, body_sha256=verified.body_sha256, created_at=_clock(db))
        if row.created_at - verified.authenticated_at > timedelta(minutes=5) or row.created_at - started > timedelta(minutes=45): fail('expired_while_waiting')
        db.add(row); db.flush()
        return _receipt(row, snapshot, duplicate=False)


def observe_inventory_control_attestation(db, *, preparation_id):
    """Reprove every chain prefix and current freshness; authorization stays separate."""
    if db.new or db.dirty or db.deleted: fail('requires_clean_session')
    with db.no_autoflush:
        prepared = read_inventory_control_preparation(db, preparation_id=preparation_id)
        root = db.get(Preparation, preparation_id)
        chain = db.get(Chain, root.capture_chain_id, populate_existing=True)
        catalog = db.get(Catalog, root.catalog_id, populate_existing=True)
        snapshots = chain.evidence_jsonb['snapshots']; receipts=[]; now=_clock(db)
        for number, evidence in enumerate(snapshots,1):
            manifest = evidence['manifest']
            snapshot = db.scalar(select(ExternalSyncSnapshot).where(ExternalSyncSnapshot.source_instance == evidence['source_instance'],
                ExternalSyncSnapshot.snapshot_id == manifest['snapshot_id']).execution_options(populate_existing=True))
            if snapshot is None: fail('snapshot_mismatch')
            row = db.scalar(select(Receipt).where(Receipt.snapshot_ref_id == snapshot.id).execution_options(populate_existing=True))
            if row is None: fail('missing_capture_receipt')
            try:
                body = CaptureAttestationIn.model_validate(row.payload_jsonb).model_dump(mode='json')
            except ValidationError:
                fail('receipt_changed')
            if not _snapshot_matches(snapshot, body) or row.source_instance != body['source_instance'] or row.key_id != body['key_id'] or row.entity_type != 'inventory' \
                    or row.request_id != body['snapshot_id'] + '-capture' or row.payload_sha256 != _sha(body) or row.body_sha256 != _sha(body) \
                    or not re.fullmatch('[a-f0-9]{64}',row.key_fingerprint) \
                    or abs(_aware(row.created_at)-_aware(row.signed_at)) > timedelta(minutes=5): fail('receipt_changed')
            started = min(instant(w['started_at']) for w in evidence['warehouses'])
            completed = max(instant(w['completed_at']) for w in evidence['warehouses'])
            if body['source_binding_sha256'] != prepared['source_binding_sha256'] or body['catalog_sha256'] != prepared['catalog_sha256'] \
                    or body['catalog_revision'] != catalog.catalog_revision or body['target_region_code'] != catalog.catalog_jsonb['target_region_code'] \
                    or body['capture_chain_sha256'] != _sha(dict(schema_version=chain.evidence_jsonb['schema_version'],snapshots=snapshots[:number])) \
                    or instant(body['capture_started_at']) != started or instant(body['capture_completed_at']) != completed \
                    or not completed <= _aware(snapshot.snapshot_at) <= _aware(row.created_at) \
                    or _aware(row.created_at)-started > timedelta(minutes=45): fail('claim_mismatch')
            receipts.append(dict(attestation_id=str(row.id),payload_sha256=row.payload_sha256,key_id=row.key_id,key_fingerprint=row.key_fingerprint))
        latest_started = min(instant(w['started_at']) for w in snapshots[-1]['warehouses'])
        if not timedelta(0) <= now-latest_started <= timedelta(minutes=45): fail('capture_stale')
        return dict(preparation_id=preparation_id, source_authenticated=True, capture_attested=True,
            receipts=receipts, attestation_cursor_sha256=_sha(receipts), checked_at=now,
            projection_published=False, start_ready=False)
