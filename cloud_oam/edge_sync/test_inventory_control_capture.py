"""Actual producer, shared transport contract and receiver wire; synthetic only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import ModuleType
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'cloud_oam/backend'))
from cloud_oam.edge_sync.test_oam_edge_sync import oam_edge_sync as edge
from cloud_oam.edge_sync import inventory_control_capture as capture
from app.inventory_control_evidence import validate_inventory_control_evidence

NOW = datetime(2026,9,20,0,0,tzinfo=timezone.utc)


def expected():
    return dict(schema_version=capture.SCHEMA,binding=dict(source_system='starcharge_oam',source_instance='synthetic-edge',
        company_id='synthetic-company',org_code='synthetic-org',scope_key='all'),catalog_revision='synthetic-v1',target_region_code='region-A',
        warehouses=[dict(warehouse_code='W-A',warehouse_type='supplyWarehouse',warehouse_attribute='good',
            positions=[dict(position_code='P-A',region_code='region-A'),dict(position_code='P-ZERO',region_code='region-A')]),
            dict(warehouse_code='W-B',warehouse_type='serviceWarehouse',warehouse_attribute='old',
            positions=[dict(position_code='P-B',region_code='region-B')])])


def row(key='1', **changes):
    return dict(id=key,companyId='synthetic-company',orgCode='synthetic-org',warehouseCode='W-A',
        warehouseType='supplyWarehouse',warehouseAttribute='good',positionCode='P-A',materialCode='SKU-1',
        qtyStock='2.000',qtyLock='0.000',unitName='piece',**changes)


class Source:
    def __init__(self, records=None):
        self.records=deepcopy(records if records is not None else [row('1'),row('2'),row('3')]);self.calls=[]
        self.warehouses=[dict(code=r['warehouse_code'],warehouseType=r['warehouse_type'],warehouseAttribute=r['warehouse_attribute'],
            companyId='synthetic-company',orgCode='synthetic-org') for r in expected()['warehouses']]
    def __call__(self,path,query):
        self.calls.append((path,deepcopy(query)))
        rows=self.warehouses if path=='/warehouse/list' else [r for r in self.records if r['warehouseCode']==query['warehouseCode']]
        start=(query['page']-1)*query['size']
        return dict(success=True,model=dict(amount=len(rows),result=deepcopy(rows[start:start+query['size']])))


def collect(source, catalog=None, now=NOW):
    return capture.collect(catalog or expected(),read_page=source,normalize_records=edge.inventory_records,
        bind_scope=edge.bind_inventory_scope,page_size=2,clock=lambda:now)


def prepare(collected,state=None,base=None,now=NOW,force=False):
    state = deepcopy(state) if state is not None else {'version':2,'sourceInstance':'synthetic-edge','scopes':{}}
    outbox=edge.build_outbox(source_instance='synthetic-edge',scope_key='all',warehouse_filter=None,
        company_id='synthetic-company',org_code='synthetic-org',snapshot_at=(now+timedelta(seconds=1)).isoformat(),
        snapshots={'inventory':collected['records']},state=state,force_full=force,batch_size=2)
    bundle=capture.build_bundle(expected(),collected,outbox,manifest=edge.snapshot_manifest(outbox),
        batches=list(edge.snapshot_batches(outbox)),base=base)
    outbox['controlAttestation']=capture.attestation_payload(bundle,key_id='synthetic-key-v1')
    report=validate_inventory_control_evidence(expected_json=capture.canonical(bundle['expected']).decode(),
        evidence_json=capture.canonical(bundle['evidence']).decode(),checked_at=now+timedelta(seconds=2))
    return outbox,bundle,report


def reply(kwargs,outbox):
    result=dict(ok=True,duplicate=False,mode='staging_only',snapshot_id=outbox['snapshotId'])
    if kwargs['endpoint'].endswith('/batches'):
        return dict(result,batch_id=kwargs['batch_id'],accepted_records=len(kwargs['payload']['records']))
    if kwargs['endpoint'].endswith('/captures'):
        return dict(result,attestation_id=str(uuid4()),source_instance=outbox['sourceInstance'],key_id='synthetic-key-v1',
            payload_sha256=capture.digest(kwargs['payload']),capture_attested=True,projection_published=False,start_ready=False)
    return dict(result,status='complete',personnel=None,entities={name:dict(records=e['finalRecordCount'],sha256=e['finalSha256'],deltaRecords=e['deltaRecordCount']) for name,e in outbox['entities'].items()})


def test_real_producer_matches_backend_coverage_and_preserves_explicit_zero():
    source=Source();collected=collect(source);outbox,bundle,report=prepare(collected)
    assert report.target_record_count==3 and report.target_position_count==2
    assert bundle['evidence']['snapshots'][0]['warehouses'][1]['pages']==[dict(response_status='success',page=1,size=2,
        source_total=0,record_keys=[],records_sha256=capture.digest([]))]
    assert len([c for c in source.calls if c[0]=='/warehouse/list'])==2
    assert all(bundle[key] is False for key in ('source_authenticated','catalog_authenticated','capture_attested','projection_published','start_ready'))
    assert bundle['evidence']['snapshots'][0]['manifest']==edge.snapshot_manifest(outbox)
    assert bundle['evidence']['snapshots'][0]['batches']==list(edge.snapshot_batches(outbox))


def test_all_zero_full_and_incremental_delete_chain_passes_actual_backend_validator(tmp_path):
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}}
    first,bundle,_=prepare(collect(Source()))
    first['controlEvidence']=capture.archive(tmp_path/'evidence',bundle)
    edge.persist_completed_state(tmp_path/'state.json',state,first)
    base=capture.load_base(tmp_path/'evidence',state,expected(),force_full=False)
    second,deleted,report=prepare(collect(Source([]),now=NOW+timedelta(minutes=1)),state,base,NOW+timedelta(minutes=1))
    assert report.sync_mode=='incremental' and report.target_record_count==0
    assert len(deleted['evidence']['snapshots'])==2
    assert all(r['operation']=='delete' for r in second['entities']['inventory']['deltaRecords'])
    zero,_,zero_report=prepare(collect(Source([])))
    assert zero['entities']['inventory']['batchCount']==0 and zero_report.target_record_count==0


@pytest.mark.parametrize('kind',['missing_total','bool_total','float_total','missing_result','missing_success','truthy_success',
    'short_page','changed_total','duplicate','cross_scope','unmapped_position','read_failure','catalog_removed','catalog_added','catalog_type'])
def test_unproven_or_changed_capture_never_becomes_an_empty_success(kind):
    source=Source();reads=[]
    def changed(path,query):
        result=source(path,query);reads.append(path)
        if kind=='read_failure': raise RuntimeError('private response with synthetic token')
        if path=='/material_stock/list' and query['warehouseCode']=='W-A':
            if kind=='missing_total': del result['model']['amount']
            if kind=='bool_total': result['model']['amount']=True
            if kind=='float_total': result['model']['amount']=3.0
            if kind=='missing_result': del result['model']['result']
            if kind=='missing_success': del result['success']
            if kind=='truthy_success': result['success']='true'
            if kind=='short_page': result['model']['result']=[]
            if kind=='changed_total' and query['page']==2: result['model']['amount']=4
            if kind=='duplicate' and query['page']==2: result['model']['result'][0]['id']='1'
            if kind=='cross_scope': result['model']['result'][0]['companyId']='other'
            if kind=='unmapped_position': result['model']['result'][0]['positionCode']='unknown'
        if path=='/warehouse/list' and kind.startswith('catalog_'):
            if kind=='catalog_removed': result['model']=dict(amount=1,result=source.warehouses[:1])
            if kind=='catalog_added': result['model']['result'][1]['code']='unexpected'
            if kind=='catalog_type': result['model']['result'][0]['warehouseType']='other'
        return result
    with pytest.raises(capture.ControlCaptureError) as error: collect(changed)
    assert 'private' not in str(error.value)
    assert reads.count('/material_stock/list')<=2


def test_rechecks_live_warehouse_catalog_after_inventory_reads():
    source=Source();count=0
    def changed(path,query):
        nonlocal count
        result=source(path,query)
        if path=='/warehouse/list':
            count+=1
            if count==2: result['model']['result'][0]['warehouseAttribute']='changed'
        return result
    with pytest.raises(capture.ControlCaptureError,match='live_catalog_changed'): collect(changed)
    assert count==2


def test_private_archive_cannot_overwrite_and_bad_base_requires_explicit_full(tmp_path):
    outbox,bundle,_=prepare(collect(Source()));pointer=capture.archive(tmp_path,bundle)
    assert os.stat(tmp_path/pointer['file']).st_mode & 0o777==0o600
    assert os.stat(tmp_path).st_mode & 0o777==0o700
    with pytest.raises(capture.ControlCaptureError,match='archive_unconfirmed'): capture.archive(tmp_path,bundle)
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}}
    edge.persist_completed_state(tmp_path/'state.json',state,outbox)
    with pytest.raises(capture.ControlCaptureError,match='base_evidence_required_force_full'):
        capture.load_base(tmp_path,state,expected(),force_full=False)
    assert capture.load_base(tmp_path,state,expected(),force_full=True) is None
    state['scopes']['all']['controlEvidence']=pointer
    (tmp_path/pointer['file']).write_text('{}')
    with pytest.raises(capture.ControlCaptureError,match='base_evidence_changed_force_full'):
        capture.load_base(tmp_path,state,expected(),force_full=False)


@pytest.mark.parametrize('kind',['batch_identity','batch_count','complete_identity','complete_hash','timeout'])
def test_wrong_or_unknown_upload_ack_does_not_advance_state_or_lose_capture(tmp_path,monkeypatch,kind):
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}};saved=deepcopy(state)
    outbox,bundle,_=prepare(collect(Source()));pointer=capture.archive(tmp_path/'evidence',bundle);outbox['controlEvidence']=pointer
    calls=[]
    def send(**kwargs):
        calls.append(kwargs);result=reply(kwargs,outbox)
        if kind=='timeout': raise edge.EdgeSyncError('unknown')
        if kwargs['endpoint'].endswith('/batches'):
            if kind=='batch_identity':result['snapshot_id']='other'
            if kind=='batch_count':result['accepted_records']=True
        else:
            if kind=='complete_identity':result['snapshot_id']='other'
            if kind=='complete_hash':result['entities']['inventory']['sha256']='f'*64
        return result
    monkeypatch.setattr(edge,'send_signed_json',send)
    with pytest.raises(edge.EdgeSyncError): edge.upload_outbox(outbox=outbox,api_base='https://invalid.test/api',secret='s'*32,
        state_file=tmp_path/'state.json',state=state)
    assert state==saved and not (tmp_path/'state.json').exists()
    assert (tmp_path/'evidence'/pointer['file']).exists()
    assert len(calls)==(3 if kind.startswith('complete') else 1)


def test_shared_edge_is_the_only_upload_transport_and_does_not_echo_errors(monkeypatch):
    calls=[]
    def shared(*args,**kwargs): calls.append((args,kwargs));return 200,b'{"ok":true}',{}
    monkeypatch.setattr(edge,'shared_edge_request',shared)
    values=dict(api_base='https://invalid.test/api',endpoint='integrations/oam/edge/snapshots/complete',
        secret='s'*32,source_instance='synthetic-edge',batch_id='snapshot-complete',payload={'synthetic':True})
    assert edge.send_signed_json(**values)=={'ok':True}
    assert calls[0][0]==('POST','https://invalid.test/api/integrations/oam/edge/snapshots/complete')
    assert calls[0][1]['body']==edge.canonical_json(values['payload'])
    monkeypatch.setattr(edge,'shared_edge_request',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('private token')))
    with pytest.raises(edge.EdgeSyncError) as caught:edge.send_signed_json(**values)
    assert 'private token' not in str(caught.value) and len(calls)==1


def test_cli_dry_run_collects_and_validates_without_upload_or_archive(tmp_path,monkeypatch,capsys):
    catalog=tmp_path/'catalog.json';catalog.write_bytes(capture.canonical(expected()))
    source=Source();health=[]
    monkeypatch.setattr(edge,'read_control_page',source)
    monkeypatch.setattr(edge,'run_oam_health',lambda **kw:health.append(kw))
    monkeypatch.setattr(edge,'upload_outbox',lambda **kw:pytest.fail('dry run upload'))
    monkeypatch.setattr(sys,'argv',['edge','--entity','inventory','--dry-run','--control-catalog-file',str(catalog),
        '--source-id','synthetic-edge','--company-id','synthetic-company','--org-code','synthetic-org',
        '--state-file',str(tmp_path/'state.json'),'--outbox-dir',str(tmp_path/'outbox'),'--lock-file',str(tmp_path/'sync.lock')])
    assert edge.main()==0
    summary=json.loads(capsys.readouterr().out)
    assert summary['controlCapture']['status']=='collected' and summary['controlCapture']['captureAttested'] is False
    assert health==[{'scheduled':False}] and not (tmp_path/'outbox').exists() and not (tmp_path/'state.json').exists()


@pytest.mark.parametrize('absent',[None,''])
def test_absent_display_bindings_inherit_only_the_verified_query(absent):
    source=Source([row()])
    def omitted(path,query):
        result=source(path,query)
        if path=='/material_stock/list':
            for value in result['model']['result']:
                for key in ('companyId','orgCode','warehouseCode','warehouseType','warehouseAttribute'): value[key]=absent
                value['unapprovedCredentialField']='synthetic-private-value'
        return result
    collected=collect(omitted);_,bundle,report=prepare(collected)
    assert report.target_record_count==1
    assert collected['records'][0]['data']['warehouseType']=='supplyWarehouse'
    assert 'synthetic-private-value' not in capture.canonical(bundle).decode()


def cli_args(tmp_path):
    catalog_file=tmp_path/'catalog.json';catalog_file.write_bytes(capture.canonical(expected()))
    return ['edge','--entity','inventory','--control-catalog-file',str(catalog_file),
        '--control-attestation-key-id','synthetic-key-v1',
        '--source-id','synthetic-edge','--company-id','synthetic-company','--org-code','synthetic-org',
        '--api-base','https://invalid.test/api','--state-file',str(tmp_path/'state.json'),
        '--outbox-dir',str(tmp_path/'outbox'),'--lock-file',str(tmp_path/'sync.lock')]


def test_cli_lost_ack_keeps_evidence_and_requeries_instead_of_replaying(tmp_path,monkeypatch,capsys):
    source=Source();calls=[];lose_ack=False;health=[]
    monkeypatch.setattr(sys,'argv',cli_args(tmp_path))
    monkeypatch.setenv('RSC_EDGE_SYNC_SECRET','synthetic-secret-for-isolated-tests-only')
    monkeypatch.setattr(edge,'read_control_page',source)
    monkeypatch.setattr(edge,'run_oam_health',lambda **kw:health.append(kw))
    def send(**kwargs):
        calls.append(kwargs)
        outbox_path=next((tmp_path/'outbox').glob('*.json'))
        outbox=json.loads(outbox_path.read_text())
        pointer=outbox['controlEvidence']
        assert capture.digest(capture.read_document(tmp_path/'outbox/evidence'/pointer['file']))==pointer['sha256']
        if lose_ack and kwargs['endpoint'].endswith('/complete'): raise edge.EdgeSyncError('unknown')
        return reply(kwargs,outbox)
    monkeypatch.setattr(edge,'send_signed_json',send)
    assert edge.main()==0
    first=json.loads(capsys.readouterr().out);saved=(tmp_path/'state.json').read_bytes()
    assert not list((tmp_path/'outbox').glob('*.json'))
    source.records=[];lose_ack=True
    with pytest.raises(edge.EdgeSyncError,match='unknown'):edge.main()
    failed=next((tmp_path/'outbox').glob('*.json'))
    failed_id=json.loads(failed.read_text())['snapshotId']
    assert (tmp_path/'state.json').read_bytes()==saved
    assert len(list((tmp_path/'outbox/evidence').glob('*.json')))==2
    previous_reads=len(source.calls);previous_calls=len(calls);lose_ack=False
    assert edge.main()==0
    recovered=json.loads(capsys.readouterr().out)
    assert len(source.calls)>previous_reads and recovered['snapshotId'] not in (failed_id,first['snapshotId'])
    assert recovered['quarantinedOutboxes']==[failed.name]
    assert (tmp_path/'outbox/quarantine'/failed.name).exists()
    assert all(c['payload']['snapshot_id']==recovered['snapshotId'] for c in calls[previous_calls:])
    state=json.loads((tmp_path/'state.json').read_text())
    base=capture.load_base(tmp_path/'outbox/evidence',state,expected(),force_full=False)
    assert [s['manifest']['snapshot_id'] for s in base['snapshots']]==[first['snapshotId'],recovered['snapshotId']]
    assert recovered['syncMode']=='incremental' and state['scopes']['all']['entities']['inventory']['records']==0
    assert len(list((tmp_path/'outbox/evidence').glob('*.json')))==3 and len(health)==3


@pytest.mark.parametrize('after_replace',[False,True])
def test_state_write_unknown_never_advances_ram_or_discards_capture(tmp_path,monkeypatch,after_replace):
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}};saved=deepcopy(state)
    outbox,bundle,_=prepare(collect(Source()));outbox['controlEvidence']=capture.archive(tmp_path/'evidence',bundle)
    path=tmp_path/'state.json';edge.write_private_json(path,state);old=path.read_bytes();calls=[]
    real_replace=edge.os.replace;real_fsync=edge.os.fsync
    def replace(source,target):
        if target==path and not after_replace: raise OSError('synthetic disk failure')
        return real_replace(source,target)
    def fsync(fd):
        if after_replace and stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('synthetic directory sync failure')
        return real_fsync(fd)
    def send(**kwargs):calls.append(kwargs);return reply(kwargs,outbox)
    monkeypatch.setattr(edge.os,'replace',replace);monkeypatch.setattr(edge.os,'fsync',fsync)
    monkeypatch.setattr(edge,'send_signed_json',send)
    with pytest.raises(edge.EdgeSyncError):edge.upload_outbox(outbox=outbox,api_base='https://invalid.test/api',secret='s'*32,
        state_file=path,state=state)
    assert state==saved and len(calls)==4
    assert (tmp_path/'evidence'/outbox['controlEvidence']['file']).exists()
    if after_replace:assert json.loads(path.read_text())['scopes']['all']['snapshotId']==outbox['snapshotId']
    else:assert path.read_bytes()==old


def test_invalid_catalog_binding_fails_before_health_or_source_read(tmp_path,monkeypatch):
    args=cli_args(tmp_path);document=expected();document['binding']['company_id']='other'
    (tmp_path/'catalog.json').write_bytes(capture.canonical(document));args.append('--dry-run')
    monkeypatch.setattr(sys,'argv',args)
    monkeypatch.setattr(edge,'run_oam_health',lambda **kw:pytest.fail('invalid preflight queried health'))
    monkeypatch.setattr(edge,'read_control_page',lambda *a:pytest.fail('invalid preflight queried source'))
    with pytest.raises(capture.ControlCaptureError,match='binding_mismatch'):edge.main()
    assert not (tmp_path/'outbox').exists()


def test_control_reader_uses_shared_client_without_retry_and_rejects_other_routes(monkeypatch):
    client=ModuleType('inventory_query_portal.oam_read_client');calls=[]
    def post(*args,**kwargs):calls.append((args,kwargs));return {'synthetic':True}
    client.post_json=post;monkeypatch.setitem(sys.modules,'inventory_query_portal.oam_read_client',client)
    assert edge.read_control_page('/material_stock/list',{'page':1})=={'synthetic':True}
    assert calls==[(('/material_stock/list',{'page':1}),{'retries':0})]
    with pytest.raises(edge.EdgeSyncError):edge.read_control_page('/material/apply/save',{})
    assert len(calls)==1


def test_packaged_runtime_can_import_capture_without_project_or_session(tmp_path):
    # Execute only the installer copy function against synthetic local sources.
    # Never call session migration, launchctl, health checks or a real transport.
    script=ROOT/'cloud_oam/edge_sync/install_launchd_agent.sh'
    function=script.read_text().split('install_runtime() {',1)[1].split('\ninstall_agent()',1)[0]
    project=tmp_path/'synthetic-project';work=project/'work';runtime=tmp_path/'runtime'
    for name in ('oam_shared_session.py','global_business_session_health.py','inventory_query_portal/oam_read_client.py',
            'inventory_query_portal/query_oam_flows.py','inventory_query_portal/query_oam_work_orders.py','rsc_ningbo_group_bot/oam_gateway.py'):
        path=work/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('# synthetic import fixture\n')
    (work/'inventory_query_portal/oam_read_client.py').write_text('def get_paged(*a, **k): raise AssertionError("no network")\n')
    (work/'inventory_query_portal/query_oam_work_orders.py').write_text(
        'def epoch_ms(*a): raise AssertionError("no network")\nget_paged_parallel = normalized_list_row = epoch_ms\n')
    env={**os.environ,'SCRIPT_DIR':str(script.parent),'PROJECT_ROOT':str(project),
        'RUNTIME_EDGE_DIR':str(runtime/'cloud_oam/edge_sync'),'RUNTIME_WORK_DIR':str(runtime/'work')}
    subprocess.run(['bash','-e','-c','install_runtime() {'+function+'\ninstall_runtime'],env=env,check=True,capture_output=True)
    installed=runtime/'cloud_oam/edge_sync'
    assert (installed/'inventory_control_capture.py').read_bytes()==(script.parent/'inventory_control_capture.py').read_bytes()
    assert (installed/'inventory_control_capture.py').stat().st_mode & 0o777==0o600
    result=subprocess.run([sys.executable,str(installed/'oam_edge_sync.py'),'--help'],cwd=runtime,capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert '--control-catalog-file' in result.stdout
    assert not list(runtime.rglob('*session.json'))


@pytest.mark.parametrize('kind',['index','count','hash','snapshot','source','catalog','scope_type','bundle_type'])
def test_changed_incremental_base_fails_before_any_new_capture(tmp_path,kind):
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}}
    outbox,bundle,_=prepare(collect(Source()));outbox['controlEvidence']=capture.archive(tmp_path,bundle)
    edge.persist_completed_state(tmp_path/'state.json',state,outbox);catalog=expected();scope=state['scopes']['all']
    if kind=='index':scope['entities']['inventory']['index']={}
    if kind=='count':scope['entities']['inventory']['records']=True
    if kind=='hash':scope['entities']['inventory']['sha256']='a'*64
    if kind=='snapshot':scope['snapshotId']='other'
    if kind=='source':state['sourceInstance']='other'
    if kind=='catalog':catalog['catalog_revision']='changed'
    if kind=='scope_type':state['scopes']['all']=[]
    if kind=='bundle_type':
        pointer=scope['controlEvidence'];(tmp_path/pointer['file']).write_text('[]');pointer['sha256']=capture.digest([])
    with pytest.raises(capture.ControlCaptureError):capture.load_base(tmp_path,state,catalog,force_full=False)
    assert capture.load_base(tmp_path,state,catalog,force_full=True) is None


@pytest.mark.parametrize('kind',['timeout','digest','key','identity','published','missing_proof'])
def test_capture_ack_is_required_before_advancing_local_state(tmp_path,monkeypatch,kind):
    outbox,bundle,_=prepare(collect(Source()));outbox['controlEvidence']=capture.archive(tmp_path/'evidence',bundle)
    state={'version':2,'sourceInstance':'synthetic-edge','scopes':{}};saved=deepcopy(state);calls=[]
    if kind=='missing_proof':outbox.pop('controlAttestation')
    def send(**kw):
        calls.append(kw);result=reply(kw,outbox)
        if kw['endpoint'].endswith('/captures'):
            if kind=='timeout':raise edge.EdgeSyncError('unknown')
            if kind=='digest':result['payload_sha256']='a'*64
            if kind=='key':result['key_id']='other'
            if kind=='identity':result['attestation_id']='not-a-uuid'
            if kind=='published':result['projection_published']=True
        return result
    monkeypatch.setattr(edge,'send_signed_json',send)
    with pytest.raises(edge.EdgeSyncError):edge.upload_outbox(outbox=outbox,api_base='https://synthetic.invalid/api',secret='s'*32,
        state_file=tmp_path/'state.json',state=state)
    assert state==saved and not (tmp_path/'state.json').exists()
    assert (tmp_path/'evidence'/outbox['controlEvidence']['file']).exists()
    assert len(calls)==(0 if kind=='missing_proof' else 4)


def test_attestation_matches_backend_canonical_hashes_without_inventory_payload():
    from app.inventory_control_publication import prepare_inventory_control_publication
    outbox,bundle,_=prepare(collect(Source()))
    facts=prepare_inventory_control_publication(expected_json=capture.canonical(bundle['expected']).decode(),
        evidence_json=capture.canonical(bundle['evidence']).decode(),checked_at=NOW+timedelta(seconds=2)).as_dict()
    claim=outbox['controlAttestation']
    assert len(claim)==18 and all(type(value) is str for value in claim.values())
    for key in ('source_binding_sha256','catalog_sha256','capture_chain_sha256'):assert claim[key]==facts[key]
    assert 'SKU-1' not in capture.canonical(claim).decode()
