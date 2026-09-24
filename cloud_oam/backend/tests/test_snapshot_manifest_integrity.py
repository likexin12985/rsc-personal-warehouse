"""The signed HTTP body and stored completion seal must be the same bytes."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import ExternalSyncSnapshot
from app.routers import integrations
from app.schemas import EdgeSyncSnapshotCompleteIn
from test_edge_sync_safety import request, verified

SECRET='synthetic-manifest-secret-at-least-thirty-two-characters'
SOURCE='synthetic-manifest'
URL='/api/integrations/oam/edge/snapshots/complete'


@pytest.fixture
def db():
    engine=create_engine('sqlite+pysqlite:///:memory:',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:yield db
    engine.dispose()


@pytest.fixture
def client(db,monkeypatch):
    monkeypatch.setattr(integrations,'settings',integrations.settings.model_copy(update=dict(
        edge_sync_enabled=True,edge_sync_secret=SECRET,edge_sync_allowed_sources=SOURCE,
        edge_sync_legacy_personnel_projection_enabled=False)))
    app=FastAPI();app.include_router(integrations.ingress_router,prefix='/api')
    def database():yield db
    app.dependency_overrides[get_db]=database
    with TestClient(app) as client:yield client


def manifest():
    return dict(source_system='starcharge_oam',snapshot_id='manifest-'+uuid4().hex,scope_key='all',sync_mode='full',
        company_id='synthetic-company',org_code='synthetic-org',snapshot_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
        entities=[dict(entity_type='inventory',final_record_count=0,final_sha256=hashlib.sha256(b'[]').hexdigest(),
            delta_record_count=0,delta_sha256=hashlib.sha256(b'[]').hexdigest(),batch_count=0)])


def send(client,raw):
    stamp=str(int(datetime.now(timezone.utc).timestamp()));batch='manifest-'+uuid4().hex
    signature=hmac.new(SECRET.encode(),integrations._signing_message(stamp,SOURCE,batch,raw),hashlib.sha256).hexdigest()
    return client.post(URL,content=raw,headers={'Content-Type':'application/json','X-RSC-Edge-Source':SOURCE,
        'X-RSC-Edge-Timestamp':stamp,'X-RSC-Edge-Batch':batch,'X-RSC-Edge-Signature':signature})


@pytest.mark.parametrize('form',['offset','z','whitespace','reordered'])
def test_real_hmac_body_is_preserved_and_only_exact_replay_is_duplicate(db,client,form):
    value=manifest()
    if form=='z':value['snapshot_at']=value['snapshot_at'].replace('+00:00','Z')
    raw=(json.dumps(value,indent=2)+'\n').encode() if form=='whitespace' else json.dumps(value,sort_keys=form!='reordered',separators=(',',':')).encode()
    first=send(client,raw)
    assert first.status_code==200 and not first.json()['duplicate']
    snapshot=db.scalar(select(ExternalSyncSnapshot))
    assert snapshot.manifest_json.encode()==raw and snapshot.manifest_sha256==hashlib.sha256(raw).hexdigest()
    assert send(client,raw).json()['duplicate']
    assert send(client,b' '+raw).status_code==409
    assert db.scalar(select(func.count()).select_from(ExternalSyncSnapshot))==1
    db.refresh(snapshot)
    assert snapshot.manifest_json.encode()==raw


@pytest.mark.parametrize('fault',['body','hash','invalid_json','invalid_utf8'])
def test_internal_payload_cannot_substitute_for_verified_body_before_any_write(db,fault):
    value=manifest();payload=EdgeSyncSnapshotCompleteIn.model_validate(value)
    body=json.dumps(dict(value,company_id='other-company') if fault=='body' else value).encode()
    if fault=='invalid_json':body=b'not-json'
    elif fault=='invalid_utf8':body=b'\xff'
    proof=verified(SOURCE,'synthetic-complete',body)
    if fault=='hash':proof=integrations.replace(proof,body_sha256='a'*64)
    with pytest.raises(HTTPException,match='snapshot_manifest_authentication_mismatch'):
        integrations.complete_snapshot(payload,request(),proof,db)
    assert db.scalar(select(func.count()).select_from(ExternalSyncSnapshot))==0
