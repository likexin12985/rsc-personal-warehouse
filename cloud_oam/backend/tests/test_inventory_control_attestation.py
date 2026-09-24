"""Actual HMAC and SQL-created immutable receipts, synthetic sources only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
import pytest
import sqlalchemy as sa

from app import inventory_control_attestation as service
from app import inventory_control_preparation as preparation
from app.database import Base
from app.foundation_models import SourceSystem, Organization
from app.routers import integrations
from app.schemas import EdgeSyncSnapshotBatchIn, EdgeSyncSnapshotCompleteIn
from app.inventory_control_attestation_models import InventoryControlCaptureAttestation as Receipt
from test_inventory_control_preparation import db as preparation_db
from test_inventory_control_capture_ingress import Source, expected, collect, prepare, edge, capture
from test_edge_sync_safety import request

PATH=Path(__file__).parents[1]/'alembic/versions/20261024_0114_control_capture_attestations.py'
SECRET='synthetic-attestation-integration-secret-at-least-32-characters'


@pytest.fixture
def db(preparation_db):
    db=preparation_db
    with Operations.context(MigrationContext.configure(db.connection())):
        Base.metadata.tables[Receipt.__tablename__].drop(db.connection())
        runpy.run_path(str(PATH))['upgrade']()
    db.commit()
    return db


def signed(payload, *, signing_time=None, source=None, batch=None, secret=SECRET, body=None):
    body=capture.canonical(payload) if body is None else body
    stamp=str(int((signing_time or datetime.now(timezone.utc)).timestamp()))
    source=source or payload['source_instance'];batch=batch or payload['snapshot_id']+'-capture'
    # Exercise the verifier with the exact signing contract used by the client.
    import hmac
    signature=hmac.new(secret.encode(),integrations._signing_message(stamp,source,batch,body),hashlib.sha256).hexdigest()
    req=request();req._body=body
    return asyncio.run(integrations.verify_edge_request(req,source_instance=source,timestamp_value=stamp,batch_id=batch,signature=signature))


@pytest.fixture
def world(db,monkeypatch,tmp_path):
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources='synthetic-edge',edge_control_capture_enabled=True,edge_control_capture_key_id='synthetic-key-v1',
        edge_sync_legacy_personnel_projection_enabled=False))
    monkeypatch.setattr(integrations,'settings',settings)
    source=SourceSystem(code='oam',name='Synthetic',mode='read_only',enabled=True)
    region=Organization(code='attestation-region',name='Synthetic',org_type='region_company',status='active')
    db.add_all([source,region]);db.commit()
    calls=[]
    def transport(method,url,*,headers,body,timeout):
        calls.append(url);req=request();req._body=body
        proof=asyncio.run(integrations.verify_edge_request(req,source_instance=headers['X-RSC-Edge-Source'],
            timestamp_value=headers['X-RSC-Edge-Timestamp'],batch_id=headers['X-RSC-Edge-Batch'],signature=headers['X-RSC-Edge-Signature']))
        if url.endswith('/batches'):result=integrations.receive_snapshot_batch(EdgeSyncSnapshotBatchIn.model_validate_json(body),req,proof,db)
        elif url.endswith('/complete'):result=integrations.complete_snapshot(EdgeSyncSnapshotCompleteIn.model_validate_json(body),req,proof,db)
        else:result=integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate_json(body),req,proof,db)
        return 200,json.dumps(result).encode(),{}
    monkeypatch.setattr(edge,'shared_edge_request',transport)
    now=datetime.now(timezone.utc)-timedelta(seconds=5)
    outbox,bundle,_=prepare(collect(Source(),now=now),now=now)
    def stage(*,claim=True):
        packet=deepcopy(outbox)
        if not claim:packet.pop('controlAttestation')
        return edge.upload_outbox(outbox=packet,api_base='https://synthetic.invalid/api',secret=SECRET,
            state_file=tmp_path/'state.json',state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}})
    def record():
        value=preparation.record_inventory_control_preparation(db,source_system_id=source.id,region_org_id=region.id,
            expected_json=capture.canonical(bundle['expected']).decode(),evidence_json=capture.canonical(bundle['evidence']).decode(),
            checked_at=datetime.now(timezone.utc))
        db.commit();return value
    return dict(outbox=outbox,bundle=bundle,stage=stage,record=record,calls=calls,settings=settings)


def test_exact_hmac_receipt_replay_and_read_only_preparation_observation(db,world):
    result=world['stage']();payload=world['outbox']['controlAttestation']
    first=result['controlAttestation'];assert first['capture_attested'] and not first['projection_published']
    replay=integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload),db)
    assert replay==dict(first,duplicate=True)
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==1
    prepared=world['record']()
    result=service.observe_inventory_control_attestation(db,preparation_id=prepared['preparation_id'])
    assert result['source_authenticated'] and result['capture_attested'] and not result['projection_published'] and not result['start_ready']
    assert prepared['source_authenticated'] is False and prepared['capture_attested'] is False
    assert SECRET not in json.dumps(db.scalar(sa.select(Receipt)).payload_jsonb)


@pytest.mark.parametrize('field,value',[('source_instance','wrong'),('company_id','other'),('org_code','other'),
    ('scope_key','warehouse:other'),('snapshot_id','s-other-snapshot'),('source_binding_sha256','a'*64),('sync_mode','incremental')])
def test_signed_wrong_snapshot_coordinates_cannot_create_a_receipt(db,world,field,value):
    world['stage'](claim=False);payload=deepcopy(world['outbox']['controlAttestation']);payload[field]=value
    with pytest.raises((HTTPException,service.CaptureAttestationError)):
        proof=signed(payload)
        integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),proof,db)
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0


@pytest.mark.parametrize('field',['catalog_sha256','capture_chain_sha256','catalog_revision','target_region_code'])
def test_authenticated_claim_is_not_equivalent_to_preparation_consistency(db,world,field):
    world['stage'](claim=False);payload=deepcopy(world['outbox']['controlAttestation'])
    payload[field]='b'*64 if field.endswith('sha256') else 'other'
    integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload),db)
    prepared=world['record']()
    with pytest.raises(service.CaptureAttestationError,match='claim_mismatch'):
        service.observe_inventory_control_attestation(db,preparation_id=prepared['preparation_id'])
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==1


def test_valid_signature_cannot_replace_an_existing_claim(db,world):
    world['stage']();saved=db.scalar(sa.select(Receipt)).payload_sha256
    payload=deepcopy(world['outbox']['controlAttestation']);payload['catalog_sha256']='b'*64
    with pytest.raises(HTTPException,match='replay_conflict'):
        integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload),db)
    assert db.scalar(sa.select(Receipt)).payload_sha256==saved


@pytest.mark.parametrize('kind',['no_proof','different_body','wrong_key','expired_verification','future_verification','old_signature','wrong_request'])
def test_proof_metadata_cannot_be_substituted_for_actual_verification(db,world,kind):
    world['stage'](claim=False);payload=world['outbox']['controlAttestation'];proof=signed(payload)
    if kind=='no_proof':proof=integrations.VerifiedEdgeRequest(proof.source_instance,proof.batch_id,proof.body,proof.body_sha256)
    if kind=='different_body':proof=replace(proof,body=b'{}')
    if kind=='wrong_key':proof=replace(proof,authentication_key_id='other')
    if kind=='expired_verification':proof=replace(proof,authenticated_at=datetime.now(timezone.utc)-timedelta(minutes=6))
    if kind=='future_verification':proof=replace(proof,authenticated_at=datetime.now(timezone.utc)+timedelta(minutes=1))
    if kind=='old_signature':proof=replace(proof,signed_at=datetime.now(timezone.utc)-timedelta(minutes=6))
    if kind=='wrong_request':proof=replace(proof,batch_id=proof.batch_id+'-other')
    with pytest.raises(service.CaptureAttestationError,match='authentication_mismatch'):
        service.accept_inventory_control_attestation(db,payload=service.CaptureAttestationIn.model_validate(payload),verified=proof)
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0


@pytest.mark.parametrize('kind',['missing','stale'])
def test_missing_or_stale_capture_does_not_authenticate_preparation(db,world,monkeypatch,kind):
    world['stage'](claim=kind!='missing');prepared=world['record']()
    if kind=='stale':monkeypatch.setattr(service,'_clock',lambda db:datetime.now(timezone.utc)+timedelta(hours=1))
    with pytest.raises(service.CaptureAttestationError,match='missing_capture_receipt' if kind=='missing' else 'capture_stale'):
        service.observe_inventory_control_attestation(db,preparation_id=prepared['preparation_id'])


@pytest.mark.parametrize('operation',['UPDATE inventory_control_capture_attestations SET key_id=key_id',
    'DELETE FROM inventory_control_capture_attestations'])
def test_sql_created_receipts_are_immutable_and_retained(db,world,operation):
    world['stage']()
    with pytest.raises(sa.exc.IntegrityError,match='append-only'):db.execute(sa.text(operation))
    db.rollback()
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='capture receipts must be retained'):runpy.run_path(str(PATH))['downgrade']()
    db.rollback();assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==1


def test_caller_rollback_removes_receipt_and_unknown_commit_preserves_original(db,world,monkeypatch):
    world['stage'](claim=False);payload=world['outbox']['controlAttestation']
    result=service.accept_inventory_control_attestation(db,payload=service.CaptureAttestationIn.model_validate(payload),verified=signed(payload))
    db.rollback();assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0
    commit=db.commit
    def lost():commit();raise OSError('synthetic acknowledgement loss')
    monkeypatch.setattr(db,'commit',lost)
    with pytest.raises(HTTPException,match='result_unknown'):
        integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload),db)
    monkeypatch.setattr(db,'commit',commit)
    preserved=db.scalar(sa.select(Receipt));assert preserved.id and str(preserved.id)!=result['attestation_id']
    replay=integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload),db)
    assert replay['duplicate'] and replay['attestation_id']==str(preserved.id)


@pytest.mark.parametrize('change',[dict(edge_control_capture_enabled=False),dict(edge_control_capture_key_id=''),
    dict(edge_sync_allowed_sources=''),dict(edge_control_capture_key_id='other-key')])
def test_disabled_or_mismatched_key_configuration_is_closed(db,world,monkeypatch,change):
    world['stage'](claim=False);payload=world['outbox']['controlAttestation'];proof=signed(payload)
    monkeypatch.setattr(integrations,'settings',world['settings'].model_copy(update=change))
    with pytest.raises(HTTPException):integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),proof,db)
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0


@pytest.mark.parametrize('change',[dict(capture_started_at='2000-01-01T00:00:00Z'),
    dict(capture_completed_at='2099-01-01T00:00:00Z'),dict(snapshot_at='2099-01-01T00:00:00Z')])
def test_signed_stale_or_impossible_capture_intervals_are_rejected(db,world,change):
    world['stage'](claim=False);payload={**world['outbox']['controlAttestation'],**change}
    with pytest.raises(HTTPException):integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload),db)
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0


def test_signature_does_not_accept_ambiguous_noncanonical_json(db,world):
    world['stage'](claim=False);payload=world['outbox']['controlAttestation']
    proof=signed(payload,body=b' '+capture.canonical(payload))
    with pytest.raises(service.CaptureAttestationError):
        service.accept_inventory_control_attestation(db,payload=service.CaptureAttestationIn.model_validate(payload),verified=proof)


def test_pending_business_edits_are_preserved_by_observation_refusal(db,world):
    world['stage']();prepared=world['record']();source=db.scalar(sa.select(SourceSystem));source.name='pending edit'
    with pytest.raises(service.CaptureAttestationError,match='requires_clean_session'):
        service.observe_inventory_control_attestation(db,preparation_id=prepared['preparation_id'])
    assert source.name=='pending edit' and source in db.dirty


@pytest.mark.parametrize('stamp',['not-a-time','2026-09-20T00:00:00','2026-13-20T00:00:00Z'])
def test_malformed_or_naive_timestamp_is_a_validation_error(world,stamp):
    from pydantic import ValidationError
    payload={**world['outbox']['controlAttestation'],'capture_started_at':stamp}
    with pytest.raises(ValidationError,match='control_attestation_invalid_time'):
        service.CaptureAttestationIn.model_validate(payload)


def test_rotating_secret_without_key_version_cannot_rebind_existing_receipt(db,world,monkeypatch):
    world['stage']();original=db.scalar(sa.select(Receipt));fingerprint=original.key_fingerprint
    rotated='another-synthetic-capture-secret-at-least-32-characters'
    monkeypatch.setattr(integrations,'settings',world['settings'].model_copy(update=dict(edge_sync_secret=rotated)))
    payload=world['outbox']['controlAttestation']
    with pytest.raises(HTTPException,match='replay_conflict'):
        integrations.receive_inventory_control_capture(service.CaptureAttestationIn.model_validate(payload),request(),signed(payload,secret=rotated),db)
    assert db.scalar(sa.select(Receipt)).key_fingerprint==fingerprint
