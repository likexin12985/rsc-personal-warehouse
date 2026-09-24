"""Real HMAC/HTTP parsing and SQL-created material receipts; synthetic OAM only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import runpy
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.foundation_models import SourceSystem
from app import material_capture_ingress as service
from app import material_master_capture_evidence as evidence
from app.material_capture_models import MaterialCaptureBinding as Binding, MaterialCaptureReceipt as Receipt
from app.routers import integrations
from test_inventory_control_admission import facts
from test_edge_sync_safety import request

PATH = Path(__file__).parents[1] / 'alembic/versions/20261026_0116_material_capture_ingress.py'
SPEC = importlib.util.spec_from_file_location('material_capture_service_test_client',
    Path(__file__).parents[2] / 'edge_sync/oam_material_master_capture.py')
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)
SECRET = 'synthetic-material-capture-key-at-least-32-characters'
SOURCE = 'synthetic-material-edge'
KEY = 'synthetic-material-v1'


@pytest.fixture
def db():
    engine=sa.create_engine('sqlite+pysqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @sa.event.listens_for(engine,'connect')
    def foreign_keys(connection,_):connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine,tables=[table for table in Base.metadata.tables.values()
        if table.name not in (Receipt.__tablename__,Binding.__tablename__)])
    with engine.begin() as connection,Operations.context(MigrationContext.configure(connection)):
        runpy.run_path(str(PATH))['upgrade']()
    with Session(engine) as db:yield db
    engine.dispose()


def bundle(rows=None, *, now=None):
    rows = [dict(materialCode='SKU-1', materialName='Synthetic', unitCode='EA', unitName='piece', materialStatus=0)] if rows is None else rows
    def source(path, query):
        assert path == evidence.ENDPOINT and query == {'page':1, 'size':1000}
        return dict(success=True, model=dict(amount=len(rows), result=deepcopy(rows)))
    now = now or datetime.now(timezone.utc)-timedelta(seconds=1)
    return capture.collect(source_instance=SOURCE, read_page=source, clock=lambda:now)


def packet(value, operation='receive', key=KEY):
    return dict(schema_version=service.REQUEST_SCHEMA, operation=operation, key_id=key,
        **({'capture':value} if operation=='receive' else {'capture_id':value['capture_id']}))


def headers(payload, *, source=SOURCE, raw=None, secret=SECRET):
    raw = evidence.canonical(payload) if raw is None else raw
    stamp = str(int(datetime.now(timezone.utc).timestamp()))
    capture_id = payload.get('capture_id') or payload['capture']['capture_id']
    request_id = 'material-' + capture_id + '-' + payload['operation']
    signature = hmac.new(secret.encode(),integrations._signing_message(stamp,source,request_id,raw),hashlib.sha256).hexdigest()
    return {'X-RSC-Edge-Source':source,'X-RSC-Edge-Timestamp':stamp,'X-RSC-Edge-Batch':request_id,
            'X-RSC-Edge-Signature':signature,'Content-Type':'application/json'}


def proof(payload, **kwargs):
    values = headers(payload,**kwargs)
    req = request()
    req._body = kwargs.get('raw', evidence.canonical(payload))
    verified = asyncio.run(integrations.verify_edge_request(req,source_instance=values['X-RSC-Edge-Source'],
        timestamp_value=values['X-RSC-Edge-Timestamp'], batch_id=values['X-RSC-Edge-Batch'],signature=values['X-RSC-Edge-Signature']))
    return asyncio.run(integrations.verify_material_capture_request(verified))


@pytest.fixture
def world(db, monkeypatch):
    settings = integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=SECRET,
        edge_sync_allowed_sources=SOURCE,edge_material_capture_enabled=True,edge_material_capture_key_id=KEY,
        edge_sync_legacy_personnel_projection_enabled=False))
    monkeypatch.setattr(integrations,'settings',settings)
    source = SourceSystem(code='oam',name='Synthetic material source',mode='read_only',enabled=True)
    db.add(source)
    db.flush()
    now = datetime.now(timezone.utc)
    binding = Binding(source_system_id=source.id,source_instance=SOURCE,key_id=KEY,
        key_fingerprint=service.key_fingerprint(SECRET,SOURCE),created_at=now-timedelta(seconds=10),
        valid_from=now-timedelta(seconds=10),valid_to=now+timedelta(hours=1))
    db.add(binding)
    db.commit()
    return SimpleNamespace(binding=binding,source=source,settings=settings,capture=bundle())


def receive(db, value):
    return integrations.receive_material_master_capture(proof(packet(value)),db)


def status(db, value):
    return integrations.read_material_master_capture_status(proof(packet(value,'status')),db)


@pytest.mark.parametrize('updates', [
    {'edge_material_capture_key_id': ''},
    {'edge_material_capture_key_id': 'invalid key'},
    {'edge_sync_allowed_sources': ''},
    {'edge_sync_secret': ''},
])
def test_production_material_capture_requires_complete_configuration(world, updates):
    settings = world.settings.model_copy(update={
        'environment': 'production', 'database_schema_mode': 'alembic',
        'database_url': 'postgresql+psycopg://edge_inbox:synthetic@localhost/oam',
        'database_expected_edge_role': 'edge_inbox',
        'edge_sync_legacy_batches_enabled': False,
    })
    settings.validate_edge_receiver_startup()
    with pytest.raises(ValueError, match='material capture authentication is incomplete'):
        settings.model_copy(update=updates).validate_edge_receiver_startup()


@pytest.mark.parametrize('empty',[False,True])
def test_authenticated_receive_duplicate_and_signed_status_only_add_one_receipt(db,world,empty):
    value = bundle([]) if empty else world.capture
    before = facts(db)
    first = receive(db,value)
    assert first['channel_attested'] and first['within_freshness_target'] and not first['duplicate']
    assert first['observed_count'] == (0 if empty else 1)
    assert not any(first[key] for key in ('full_catalog_verified','source_authorized','master_source_evidence_verified','projection_published','start_ready'))
    assert receive(db,value) == dict(first,duplicate=True)
    assert status(db,value) == dict(first,duplicate=True,status='received')
    after = facts(db)
    changed = [table for table in before if before[table]!=after[table]]
    assert changed == [Receipt.__tablename__]
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt)) == 1
    saved = db.scalar(sa.select(Receipt))
    assert SECRET not in json.dumps(saved.payload_jsonb)
    assert service._prove(saved).capture_sha256 == first['capture_sha256']


def test_same_capture_id_with_different_signed_content_does_not_replace_history(db,world):
    receive(db,world.capture)
    before = facts(db)
    changed = bundle([])
    changed['capture_id'] = world.capture['capture_id']
    with pytest.raises(HTTPException,match='replay_conflict'):receive(db,changed)
    assert facts(db)==before


@pytest.mark.parametrize('kind',['missing','revoked','expired','future','key_fingerprint','source_disabled','wrong_source_code','capture_before_registration'])
def test_exact_transport_registration_is_required_and_failure_preserves_state(db,world,kind):
    # Immutable registration cannot be edited to manufacture another historical
    # key. Replace the fixture with another explicit row where necessary.
    if kind=='revoked':world.binding.revoked_at=datetime.now(timezone.utc)
    elif kind=='source_disabled':world.source.enabled=False
    elif kind=='wrong_source_code':world.source.code='starcharge_oam'
    else:
        world.settings.edge_material_capture_key_id='synthetic-second-key'
        now=datetime.now(timezone.utc)
        if kind!='missing':
            db.add(Binding(source_system_id=world.source.id,source_instance=SOURCE,key_id=world.settings.edge_material_capture_key_id,
                key_fingerprint='f'*64 if kind=='key_fingerprint' else service.key_fingerprint(SECRET,SOURCE),
                created_at=now-timedelta(hours=2),valid_from=now+timedelta(minutes=1) if kind=='future' else now if kind=='capture_before_registration' else now-timedelta(hours=2),
                valid_to=now-timedelta(minutes=1) if kind=='expired' else now+timedelta(hours=1)))
    db.commit()
    before=facts(db)
    payload=packet(world.capture,key=world.settings.edge_material_capture_key_id)
    with pytest.raises(HTTPException):integrations.receive_material_master_capture(proof(payload),db)
    assert facts(db)==before


@pytest.mark.parametrize('kind',['body','wrong_key','missing_proof','expired','future','old_signature','wrong_request','wrong_fingerprint'])
def test_authentication_metadata_cannot_replace_verified_exact_bytes(db,world,kind):
    payload=packet(world.capture); verified=proof(payload)
    if kind=='body':verified=replace(verified,body=b'{}')
    if kind=='wrong_key':verified=replace(verified,authentication_key_id='other')
    if kind=='missing_proof':verified=integrations.VerifiedEdgeRequest(verified.source_instance,verified.batch_id,verified.body,verified.body_sha256)
    if kind=='expired':verified=replace(verified,authenticated_at=datetime.now(timezone.utc)-timedelta(minutes=6))
    if kind=='future':verified=replace(verified,authenticated_at=datetime.now(timezone.utc)+timedelta(minutes=1))
    if kind=='old_signature':verified=replace(verified,signed_at=datetime.now(timezone.utc)-timedelta(minutes=6))
    if kind=='wrong_request':verified=replace(verified,batch_id='other')
    if kind=='wrong_fingerprint':verified=replace(verified,authentication_key_fingerprint='f'*64)
    before=facts(db)
    with pytest.raises(evidence.MaterialMasterCaptureError):service.handle_material_capture(db,verified=verified,operation='receive')
    db.rollback()
    assert facts(db)==before


def test_valid_signature_does_not_bypass_capture_shape_page_hash_or_canonical_bytes(db,world):
    for kind in ('hash','extra','duplicate_json','whitespace','wrong_operation'):
        value=deepcopy(world.capture)
        if kind=='hash':value['scans'][0]['pages'][0]['accepted_rows_sha256']='f'*64
        if kind=='extra':value['source_authenticated']=True
        payload=packet(value)
        if kind=='wrong_operation':payload=packet(value,'status')
        raw=evidence.canonical(payload)
        if kind=='duplicate_json':raw=raw.replace(b'"key_id":', b'"key_id":"discarded","key_id":')
        if kind=='whitespace':raw=b' '+raw
        before=facts(db)
        with pytest.raises(HTTPException):integrations.receive_material_master_capture(proof(payload,raw=raw),db)
        assert facts(db)==before


def test_expiry_after_binding_lock_cannot_commit(db,world,monkeypatch):
    real_binding=service._binding
    def delayed(db,verified):
        result=real_binding(db,verified)
        monkeypatch.setattr(service,'clock',lambda db:datetime.now(timezone.utc)+timedelta(minutes=46))
        return result
    monkeypatch.setattr(service,'_binding',delayed)
    before=facts(db)
    with pytest.raises(HTTPException):receive(db,world.capture)
    assert facts(db)==before


def test_signed_status_of_expired_capture_is_history_not_freshness_or_retry_permission(db,world,monkeypatch):
    receive(db,world.capture)
    # Current authentication remains current, but the previously received
    # capture can be older than the read request. Only the reply clock advances.
    real_reply=service._reply
    monkeypatch.setattr(service,'_reply',lambda row,**kw:real_reply(row,**{**kw,'now':kw['now']+timedelta(hours=1)}))
    result=status(db,world.capture)
    assert result['status']=='received' and result['within_freshness_target'] is False
    missing=deepcopy(world.capture);missing['capture_id']=str(uuid4())
    assert status(db,missing)['retry_allowed'] is False


def test_committed_receipt_can_be_recovered_after_lost_response(db,world,monkeypatch):
    real=db.commit
    def lost():
        real()
        raise RuntimeError('synthetic-private-response')
    with monkeypatch.context() as patch:
        patch.setattr(db,'commit',lost)
        with pytest.raises(HTTPException,match='material_capture_result_unknown'):receive(db,world.capture)
    assert status(db,world.capture)['status']=='received'
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==1


def test_failed_commit_rolls_back_the_entire_receipt(db,world,monkeypatch):
    before=facts(db)
    with monkeypatch.context() as patch:
        patch.setattr(db,'commit',lambda:(_ for _ in ()).throw(RuntimeError('synthetic-private')))
        with pytest.raises(HTTPException,match='material_capture_result_unknown'):receive(db,world.capture)
    assert facts(db)==before


def test_http_route_authenticates_raw_body_and_honors_disabled_mode(db,world):
    app=FastAPI();app.include_router(integrations.ingress_router,prefix='/api')
    app.dependency_overrides[get_db]=lambda:db
    payload=packet(world.capture);raw=evidence.canonical(payload)
    with TestClient(app,raise_server_exceptions=False) as client:
        path='/api/integrations/oam/edge/material-master/captures'
        bad=client.post(path,content=raw,headers=headers(payload,secret='wrong'))
        assert bad.status_code==401
        received=client.post(path,content=raw,headers=headers(payload))
        assert received.status_code==200 and received.json()['channel_attested']
        lookup=packet(world.capture,'status')
        reread=client.post(path+'/status',content=evidence.canonical(lookup),headers=headers(lookup))
        assert reread.status_code==200 and reread.json()['receipt_id']==received.json()['receipt_id']
        world.settings.edge_material_capture_enabled=False
        assert client.post(path,content=raw,headers=headers(payload)).status_code==503


@pytest.mark.parametrize('statement',['UPDATE oam_material_capture_receipts SET key_id=key_id',
    'DELETE FROM oam_material_capture_receipts','UPDATE oam_material_capture_bindings SET key_id=key_id',
    'DELETE FROM oam_material_capture_bindings'])
def test_sql_facts_are_immutable_except_explicit_one_way_binding_revocation(db,world,statement):
    receive(db,world.capture);before=facts(db)
    with pytest.raises(sa.exc.IntegrityError):
        with db.begin_nested():db.execute(sa.text(statement))
    assert facts(db)==before


@pytest.fixture
def transport(db,world,monkeypatch):
    app=FastAPI();app.include_router(integrations.ingress_router,prefix='/api')
    app.dependency_overrides[get_db]=lambda:db
    calls=[]
    with TestClient(app,raise_server_exceptions=False) as client:
        def send(method,url,*,headers,body,timeout):
            assert method=='POST' and timeout==60
            calls.append(url)
            response=client.post(urlsplit(url).path,content=body,headers=headers)
            return response.status_code,response.content,{}
        monkeypatch.setenv('RSC_EDGE_SYNC_SECRET',SECRET)
        monkeypatch.setattr(capture,'_shared_request',send)
        yield SimpleNamespace(send=send,calls=calls)


def cli_args(tmp_path):
    return ['--capture','--upload','--source-instance',SOURCE,'--key-id',KEY,
            '--api-base','https://synthetic.invalid/api','--archive-dir',str(tmp_path)]


def test_real_cli_archive_signed_upload_and_status_use_receiver_http(db,world,transport,tmp_path,monkeypatch,capsys):
    tmp_path.chmod(0o700)
    raw=world.capture['records'][0]['data'];checks=[]
    monkeypatch.setattr(capture,'_local_transport',lambda:lambda path,query:dict(success=True,model=dict(amount=1,result=[raw])))
    monkeypatch.setattr(capture,'_edge_preflight',lambda:checks.append('edge'))
    monkeypatch.setattr(capture,'_health',lambda:checks.append('oam'))
    assert capture.main(cli_args(tmp_path))==0
    result=json.loads(capsys.readouterr().out)
    assert result['receipt']['channel_attested'] and result['receipt']['projection_published'] is False
    assert checks==['edge','oam'] and len(transport.calls)==1
    path=tmp_path/result['archive']['file']
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(capture,'_health',lambda:pytest.fail('status does not read OAM'))
    monkeypatch.setattr(capture,'_local_transport',lambda:pytest.fail('status does not read OAM'))
    assert capture.main(['--status-file',str(path),'--source-instance',SOURCE,'--key-id',KEY,
                         '--api-base','https://synthetic.invalid/api'])==0
    readback=json.loads(capsys.readouterr().out)
    assert readback['receipt']['receipt_id']==result['receipt']['receipt_id']
    assert readback['report']['historical_only'] and transport.calls[-1].endswith('/status')
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==1


def test_lost_upload_ack_is_not_replayed_and_signed_status_recovers_committed_receipt(db,world,transport,tmp_path,monkeypatch,capsys):
    tmp_path.chmod(0o700)
    raw=world.capture['records'][0]['data']
    monkeypatch.setattr(capture,'_local_transport',lambda:lambda *a:dict(success=True,model=dict(amount=1,result=[raw])))
    monkeypatch.setattr(capture,'_edge_preflight',lambda:None)
    monkeypatch.setattr(capture,'_health',lambda:None)
    def lost(*args,**kwargs):
        transport.send(*args,**kwargs)
        raise TimeoutError('private response value')
    monkeypatch.setattr(capture,'_shared_request',lost)
    assert capture.main(cli_args(tmp_path))==1
    error=capsys.readouterr().err
    assert 'upload_result_unknown' in error and 'private response value' not in error
    assert len(transport.calls)==1 and db.scalar(sa.select(sa.func.count()).select_from(Receipt))==1
    archived=list(tmp_path.glob('material-master-*.json'))
    assert len(archived)==1
    monkeypatch.setattr(capture,'_shared_request',transport.send)
    assert capture.main(['--status-file',str(archived[0]),'--source-instance',SOURCE,'--key-id',KEY,
                         '--api-base','https://synthetic.invalid/api'])==0
    assert json.loads(capsys.readouterr().out)['receipt']['status']=='received'
    assert len(transport.calls)==2 and transport.calls[-1].endswith('/status')


@pytest.mark.parametrize('kind',['count_bool','capture_hash','records_hash','capture_id','source','publishes','extra','time','status'])
def test_inexact_upload_ack_cannot_be_reported_as_received(db,world,transport,monkeypatch,kind):
    def wrong(*args,**kwargs):
        code,body,headers=transport.send(*args,**kwargs)
        value=json.loads(body)
        if kind=='count_bool':value['observed_count']=True
        if kind=='capture_hash':value['capture_sha256']='f'*64
        if kind=='records_hash':value['records_sha256']='f'*64
        if kind=='capture_id':value['capture_id']=str(uuid4())
        if kind=='source':value['source_instance']='other'
        if kind=='publishes':value['projection_published']=True
        if kind=='extra':value['secret']='private'
        if kind=='time':value['received_at']='invalid'
        if kind=='status':value['status']='received'
        return code,json.dumps(value).encode(),headers
    monkeypatch.setattr(capture,'_shared_request',wrong)
    with pytest.raises(evidence.MaterialMasterCaptureError,match='acknowledgement_mismatch'):
        capture.transmit(world.capture,source_instance=SOURCE,api_base='https://synthetic.invalid/api',key_id=KEY,operation='receive')
    assert len(transport.calls)==1


def test_existing_files_cannot_be_blindly_reuploaded_and_bad_status_file_stops_before_edge(tmp_path,monkeypatch,capsys):
    monkeypatch.setenv('RSC_EDGE_SYNC_SECRET',SECRET)
    monkeypatch.setattr(capture,'_edge_preflight',lambda:pytest.fail('invalid file must stop before Edge'))
    path=tmp_path/'bad.json';path.write_text('{}')
    assert capture.main(['--inspect-file',str(path),'--upload','--source-instance',SOURCE])==1
    assert 'upload_requires_fresh_capture' in capsys.readouterr().err
    assert capture.main(['--status-file',str(path),'--source-instance',SOURCE,'--key-id',KEY,
                         '--api-base','https://synthetic.invalid/api'])==1
    assert 'invalid_document' in capsys.readouterr().err


def test_oversized_stream_stops_reading_before_authentication_or_database_access(world):
    world.settings.edge_sync_max_body_bytes=1024
    calls=[]
    class Stream:
        async def stream(self):
            calls.append('first');yield b'x'*1025
            pytest.fail('oversized stream must stop without draining untrusted data')
    with pytest.raises(HTTPException) as caught:
        asyncio.run(integrations.verify_edge_request(Stream(),source_instance=SOURCE,
            timestamp_value=str(int(datetime.now(timezone.utc).timestamp())),batch_id='synthetic-stream',signature='a'*64))
    assert caught.value.status_code==413 and calls==['first']
