"""Collector -> actual HMAC verification -> staging -> immutable preparation.

No external call: only the final shared Edge transport is replaced by an in-
process receiver. Business response/manifest validation and commits are real.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import text

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from cloud_oam.edge_sync.test_inventory_control_capture import Source, expected, collect, prepare, edge, capture
from app import inventory_control_preparation as preparation
from app.inventory_control_attestation import CaptureAttestationIn, observe_inventory_control_attestation
from app.foundation_models import SourceSystem, Organization
from app.routers import integrations
from app.schemas import EdgeSyncSnapshotBatchIn, EdgeSyncSnapshotCompleteIn
from test_inventory_control_preparation import db
from test_edge_sync_safety import request


@pytest.mark.parametrize('mode',['full','incremental','zero','tampered_signature'])
def test_collector_actual_hmac_staging_and_preparation(db,tmp_path,monkeypatch,mode):
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret='synthetic-ingress-secret-at-least-32-characters',
        edge_sync_allowed_sources='synthetic-edge',edge_sync_legacy_personnel_projection_enabled=False,
        edge_control_capture_enabled=True,edge_control_capture_key_id='synthetic-key-v1'))
    monkeypatch.setattr(integrations,'settings',settings)
    source=SourceSystem(code='oam',name='Synthetic control',mode='read_only',enabled=True)
    region=Organization(code='region-A',name='Synthetic region',org_type='region_company',status='active')
    db.add_all([source,region]);db.commit()
    calls=[]
    def transport(method,url,*,headers,body,timeout):
        calls.append(url);assert method=='POST' and timeout==60
        req=request();req._body=body
        proof=asyncio.run(integrations.verify_edge_request(req,source_instance=headers['X-RSC-Edge-Source'],
            timestamp_value=headers['X-RSC-Edge-Timestamp'],batch_id=headers['X-RSC-Edge-Batch'],
            signature='0'*64 if mode=='tampered_signature' else headers['X-RSC-Edge-Signature']))
        if url.endswith('/batches'):
            result=integrations.receive_snapshot_batch(EdgeSyncSnapshotBatchIn.model_validate_json(body),req,proof,db)
        elif url.endswith('/captures'):
            result=integrations.receive_inventory_control_capture(CaptureAttestationIn.model_validate_json(body),req,proof,db)
        else:
            result=integrations.complete_snapshot(EdgeSyncSnapshotCompleteIn.model_validate_json(body),req,proof,db)
        return 200,json.dumps(result).encode(),{}
    monkeypatch.setattr(edge,'shared_edge_request',transport)
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}};base=None
    now=datetime.now(timezone.utc)-timedelta(minutes=5)
    for step in range(2 if mode=='incremental' else 1):
        moment=now+timedelta(minutes=step)
        collected=collect(Source([] if mode=='zero' or step else None),now=moment)
        outbox,bundle,_=prepare(collected,state,base,moment)
        outbox['controlEvidence']=capture.archive(tmp_path/'evidence',bundle)
        kwargs=dict(outbox=outbox,api_base='https://synthetic.invalid/api',secret=settings.edge_sync_secret,
                    state_file=tmp_path/'state.json',state=state)
        if mode=='tampered_signature':
            with pytest.raises(edge.EdgeSyncError,match='响应未知'):edge.upload_outbox(**kwargs)
            assert len(calls)==1 and state['scopes']=={}
            assert db.scalar(text('SELECT count(*) FROM external_sync_snapshots'))==0
            return
        assert edge.upload_outbox(**kwargs)['completion']['status']=='complete'
        base=capture.load_base(tmp_path/'evidence',state,expected(),force_full=False)
        result=preparation.record_inventory_control_preparation(db,source_system_id=source.id,region_org_id=region.id,
            expected_json=capture.canonical(bundle['expected']).decode(),evidence_json=capture.canonical(base).decode(),
            checked_at=moment+timedelta(seconds=2))
        db.commit()
        assert preparation.read_inventory_control_preparation(db,preparation_id=result['preparation_id'])==result
        observation=observe_inventory_control_attestation(db,preparation_id=result['preparation_id'])
        assert observation['source_authenticated'] is True and observation['capture_attested'] is True
        assert observation['projection_published'] is False and observation['start_ready'] is False
        assert all(result[key] is False for key in ('source_authenticated','catalog_authenticated','capture_attested','projection_published','start_ready'))
    assert db.scalar(text('SELECT count(*) FROM inventory_control_capture_snapshots'))==(3 if mode=='incremental' else 1)
    for table in ('inventory_transactions','stock_balances','stocktake_tasks','sync_runs'):
        assert db.scalar(text(f'SELECT count(*) FROM {table}'))==0
