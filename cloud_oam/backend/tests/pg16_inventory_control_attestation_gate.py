"""Actual scoped edge receipt/owner observation in the protected PG16 gate only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import inventory_control_attestation as attest
from app import inventory_control_preparation as preparation
from app.foundation_models import Organization, SourceSystem
from app.inventory_control_attestation_models import InventoryControlCaptureAttestation as Receipt
from app.routers import integrations
from app.schemas import EdgeSyncSnapshotBatchIn, EdgeSyncSnapshotCompleteIn
from test_inventory_control_capture_ingress import Source, expected, edge, capture
from test_edge_sync_safety import request
from pg16_inventory_control_preparation_gate import _formal_stock, _rejected


def snapshot(engine):
    with engine.connect() as connection:
        return tuple(connection.scalars(text('SELECT to_jsonb(t) FROM public.inventory_control_capture_attestations t ORDER BY id')))


def assert_inventory_control_attestation_gate(owner_engine, edge_engine, api_engine, projector_engine, backup_engine,
                                              validate_runtime_security, verify_edge_boundary):
    catalog=expected();catalog['binding']['source_instance']='pg16-capture-attestation'
    source_instance=catalog['binding']['source_instance'];secret='synthetic-pg16-capture-authentication-secret-only'
    with Session(owner_engine) as db:
        source=db.scalars(select(SourceSystem).where(func.lower(func.btrim(SourceSystem.code))=='oam')).one()
        source_id=source.id
        region=Organization(code='pg16-attestation-region',name='Synthetic capture region',org_type='region_company',status='active')
        db.add(region);db.flush();region_id=region.id
        db.execute(text('''INSERT INTO public.oam_sync_scope_bindings
            (id,principal_name,capability,source_system,source_instance,scope_key,company_id,org_code,entity_type)
            VALUES (:id,'edge_inbox','edge_ingress',:source_system,:source_instance,:scope_key,:company_id,:org_code,'inventory')'''),
            {'id':uuid4(),**catalog['binding']})
        db.commit()
    stock_before=_formal_stock(owner_engine)
    settings=integrations.settings.model_copy(update=dict(edge_sync_enabled=True,edge_sync_secret=secret,
        edge_sync_allowed_sources=source_instance,edge_control_capture_enabled=True,edge_control_capture_key_id='pg16-capture-key-v1',
        edge_sync_legacy_personnel_projection_enabled=False))
    def transport(method,url,*,headers,body,timeout):
        assert method=='POST';req=request();req._body=body
        proof=asyncio.run(integrations.verify_edge_request(req,source_instance=headers['X-RSC-Edge-Source'],
            timestamp_value=headers['X-RSC-Edge-Timestamp'],batch_id=headers['X-RSC-Edge-Batch'],signature=headers['X-RSC-Edge-Signature']))
        with Session(edge_engine) as db:
            assert db.execute(text('SELECT current_user,session_user')).one()==('edge_inbox','edge_inbox')
            if url.endswith('/batches'):result=integrations.receive_snapshot_batch(EdgeSyncSnapshotBatchIn.model_validate_json(body),req,proof,db)
            elif url.endswith('/complete'):result=integrations.complete_snapshot(EdgeSyncSnapshotCompleteIn.model_validate_json(body),req,proof,db)
            else:result=integrations.receive_inventory_control_capture(attest.CaptureAttestationIn.model_validate_json(body),req,proof,db)
        return 200,json.dumps(result).encode(),{}
    state={'version':2,'sourceInstance':source_instance,'scopes':{}};base=None;latest=None
    with pytest.MonkeyPatch.context() as patch,TemporaryDirectory(prefix='pg16-capture-') as folder:
        folder=Path(folder);patch.setattr(integrations,'settings',settings);patch.setattr(edge,'shared_edge_request',transport)
        for number in range(3):
            now=datetime.now(timezone.utc)-timedelta(seconds=10)
            collected=capture.collect(catalog,read_page=Source([] if number else None),normalize_records=edge.inventory_records,
                bind_scope=edge.bind_inventory_scope,page_size=2,clock=lambda:now)
            box=edge.build_outbox(source_instance=source_instance,scope_key='all',warehouse_filter=None,
                company_id=catalog['binding']['company_id'],org_code=catalog['binding']['org_code'],snapshot_at=(now+timedelta(seconds=1)).isoformat(),
                snapshots={'inventory':collected['records']},state=state,force_full=number==2,batch_size=2)
            bundle=capture.build_bundle(catalog,collected,box,manifest=edge.snapshot_manifest(box),batches=list(edge.snapshot_batches(box)),base=None if number==2 else base)
            claim=capture.attestation_payload(bundle,key_id='pg16-capture-key-v1')
            pointer=capture.archive(folder/'evidence',bundle)
            if number==0:
                # Stage a fresh snapshot, then race its first two authenticated
                # receipts. This setup does not claim legacy staging is attested.
                edge.upload_outbox(outbox=box,api_base='https://synthetic.invalid/api',secret=secret,
                    state_file=folder/'staging.json',state=deepcopy(state))
                def submit():return edge.send_signed_json(api_base='https://synthetic.invalid/api',
                    endpoint='integrations/oam/edge/inventory-control/captures',secret=secret,source_instance=source_instance,
                    batch_id=box['snapshotId']+'-capture',payload=claim)
                with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(lambda _:submit(),range(2)))
                assert {r['duplicate'] for r in results}=={True,False}
                assert len({r['attestation_id'] for r in results})==1
                box['controlEvidence']=pointer;edge.persist_completed_state(folder/'state.json',state,box)
            else:
                box['controlEvidence']=pointer;box['controlAttestation']=claim
                result=edge.upload_outbox(outbox=box,api_base='https://synthetic.invalid/api',secret=secret,state_file=folder/'state.json',state=state)
                assert result['controlAttestation']['capture_attested']
            base=capture.load_base(folder/'evidence',state,catalog,force_full=False)
            with Session(owner_engine) as db:
                prepared=preparation.record_inventory_control_preparation(db,source_system_id=source_id,region_org_id=region_id,
                    expected_json=capture.canonical(catalog).decode(),evidence_json=capture.canonical(base).decode(),checked_at=datetime.now(timezone.utc))
                db.commit();latest=prepared['preparation_id']
            with Session(owner_engine) as db:
                db.execute(text('SET TRANSACTION READ ONLY'))
                observed=attest.observe_inventory_control_attestation(db,preparation_id=latest)
                assert observed['source_authenticated'] and observed['capture_attested']
                assert not observed['projection_published'] and not observed['start_ready']
        # Reusing the original ID for other signed content conflicts, not overwrite.
        bad={**claim,'catalog_sha256':'a'*64}
        with pytest.raises(edge.EdgeSyncError):edge.send_signed_json(api_base='https://synthetic.invalid/api',
            endpoint='integrations/oam/edge/inventory-control/captures',secret=secret,source_instance=source_instance,
            batch_id=box['snapshotId']+'-capture',payload=bad)
    saved=snapshot(owner_engine);assert len(saved)==3 and snapshot(backup_engine)==saved
    assert _formal_stock(owner_engine)==stock_before
    for engine in (api_engine,projector_engine):
        _rejected(engine,'SELECT * FROM public.inventory_control_capture_attestations',state='42501')
        _rejected(engine,'INSERT INTO public.inventory_control_capture_attestations DEFAULT VALUES',state='42501')
    _rejected(backup_engine,'INSERT INTO public.inventory_control_capture_attestations DEFAULT VALUES',state='42501')
    for command in ('UPDATE public.inventory_control_capture_attestations SET key_id=key_id',
                    'DELETE FROM public.inventory_control_capture_attestations','TRUNCATE public.inventory_control_capture_attestations'):
        _rejected(owner_engine,command,state='23514',message='append-only')
        _rejected(edge_engine,command,state='42501')
    # Current exact inventory binding controls receipt visibility, including
    # explicit-zero snapshots; a generic binding or caller GUC cannot replace it.
    try:
        with owner_engine.begin() as c:c.execute(text('UPDATE public.oam_sync_scope_bindings SET enabled=false WHERE source_instance=:source'),{'source':source_instance})
        with edge_engine.connect() as c:assert c.scalar(text('SELECT count(*) FROM public.inventory_control_capture_attestations'))==0
    finally:
        with owner_engine.begin() as c:c.execute(text('UPDATE public.oam_sync_scope_bindings SET enabled=true WHERE source_instance=:source'),{'source':source_instance})
    verify_edge_boundary(edge_engine);validate_runtime_security(api_engine)
    assert snapshot(owner_engine)==saved and _formal_stock(owner_engine)==stock_before
    print('PG16 capture attestation: actual HMAC, edge RLS, concurrent first receipt, full/delta/zero, immutable retention and owner observation PASS',flush=True)
    from pg16_inventory_control_admission_gate import assert_inventory_control_admission_gate
    assert_inventory_control_admission_gate(owner_engine, edge_engine, api_engine, projector_engine, backup_engine,
                                            catalog, settings, transport)
