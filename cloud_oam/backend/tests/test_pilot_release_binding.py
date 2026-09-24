"""Adversarial lifecycle evidence; synthetic Docker never contacts a daemon."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from test_pilot_deploy import Deployment, mutations
from scripts.pilot_release import Release, atomic_receipt, decoded_config, tree_digest


def test_successful_receipt_cannot_be_overwritten(tmp_path):
    deploy=Deployment(tmp_path);deploy.prepare();original=deploy.receipt.read_bytes()
    result,calls=deploy.run('prepare')
    assert result.returncode==2 and 'successful_prepare_receipt_exists' in result.stderr
    assert calls==[] and deploy.receipt.read_bytes()==original
    with pytest.raises(FileExistsError): atomic_receipt(deploy.receipt, {'replacement':True})
    assert deploy.receipt.read_bytes()==original
    assert not list(deploy.receipt.parent.glob('.receipt-*'))


@pytest.mark.parametrize('overrides', [
    {'FAKE_IMAGE_ARCH': 'unsupported-architecture'},
    {'FAKE_IMAGE_OS': 'windows'},
])
def test_prepare_rejects_wrong_image_platform_before_database(tmp_path, overrides):
    deploy = Deployment(tmp_path)
    result, calls = deploy.run('prepare', **overrides)
    assert result.returncode == 2 and 'image_platform_mismatch' in result.stderr
    assert mutations(calls) == ['build']
    assert not deploy.receipt.exists()


def test_start_rejects_image_platform_drift_before_pin_or_application(tmp_path):
    deploy = Deployment(tmp_path)
    deploy.prepare()
    receipt = deploy.receipt.read_bytes()
    result, calls = deploy.run('start', FAKE_IMAGE_ARCH='unsupported-architecture')
    assert result.returncode == 2 and 'image_platform_mismatch' in result.stderr
    assert mutations(calls) == []
    assert deploy.receipt.read_bytes() == receipt


@pytest.mark.parametrize('kind',['env','source','config','registry','tag','database','head','frozen','receipt_mode'])
def test_start_rejects_prepared_binding_drift_before_pin_or_app(tmp_path,kind):
    deploy=Deployment(tmp_path);deploy.prepare()
    if kind=='env': (deploy.root/'.env').write_text('# legitimate different env\n')
    elif kind=='source': (deploy.root/'new-release.py').write_text('change=True\n')
    elif kind=='config': deploy.mutate_config(lambda d:d['services']['api']['environment'].update(OAM_SMS_SCHEME_NAME='valid-new-scheme'))
    elif kind=='registry':
        p=tmp_path/'rsc-kms-data-keys.json';p.write_text(p.read_text()+'\n')
    elif kind=='tag': deploy.mutate_state(lambda d:d['images'].update({'rsc-pilot-api:test-candidate':'sha256:'+'a'*64}))
    elif kind=='database': deploy.mutate_state(lambda d:d['containers']['db'].update(Id='a'*64))
    elif kind=='head': deploy.mutate_state(lambda d:d.update(head='0133'))
    elif kind=='frozen':
        snapshot=next(iter(json.loads(deploy.receipt.read_text())['static_mounts'].values()))
        Path(snapshot['snapshot']).write_text('changed snapshot')
    else: deploy.receipt.chmod(0o644)
    original=deploy.receipt.read_bytes()
    result,calls=deploy.run('start')
    assert result.returncode==2,result.stdout
    assert mutations(calls)==[] and deploy.receipt.read_bytes()==original
    assert 'completed' not in result.stdout


@pytest.mark.parametrize('stage',['build','database','migration'])
@pytest.mark.parametrize('kind',['env','source','config','registry','tag'])
def test_prepare_drift_at_each_stage_never_creates_successful_receipt(tmp_path,stage,kind):
    deploy=Deployment(tmp_path)
    if stage=='build' and kind=='tag':
        deploy.mutate_state(lambda d:d['images'].update({
            'rsc-pilot-'+name+':test-candidate':'sha256:'+'0'*64 for name in ('db','api','web')}))
    result,calls=deploy.run('prepare',FAKE_DRIFT_STAGE=stage,FAKE_DRIFT_KIND=kind)
    # Build finishes before the first immutable image IDs exist. Its resulting
    # IDs are the candidate, whereas a moved tag after DB startup is a mismatch.
    if stage=='build' and kind=='tag':
        assert result.returncode==0,result.stderr
        receipt=json.loads(deploy.receipt.read_text())
        expected={name:'sha256:'+hashlib.sha256(name.encode()).hexdigest() for name in ('db','api','web')}
        changed=next(c['images'] for c in calls if c['stage']=='retagged_public_images')
        assert len(changed)==3 and all(value not in expected.values() for value in changed.values())
        assert receipt['images']==expected
        inspected=[c['args'][-1] for c in calls if c['stage']=='image_inspect']
        assert len(inspected)>=3 and all(':build-' in reference for reference in inspected[:3])
        return
    assert result.returncode==2,result.stdout
    assert not deploy.receipt.exists()
    assert not any(c['stage'] in ('kms_pin_gate','application') for c in calls)


@pytest.mark.parametrize('stage,kind',[('kms_pin_gate','env'),('kms_pin_gate','source'),
    ('kms_pin_gate','registry'),('kms_pin_gate','database'),('application','config'),
    ('application','actual_image')])
def test_start_drift_stops_without_claiming_verified(tmp_path,stage,kind):
    deploy=Deployment(tmp_path);deploy.prepare()
    original=deploy.receipt.read_bytes()
    result,calls=deploy.run('start',FAKE_DRIFT_STAGE=stage,FAKE_DRIFT_KIND=kind)
    assert result.returncode==2 and deploy.receipt.read_bytes()==original
    assert not any(c['stage']=='smoke' for c in calls)
    if stage=='kms_pin_gate': assert 'application' not in mutations(calls)


def test_tag_moves_during_gate_but_start_still_uses_reviewed_image_ids(tmp_path):
    deploy=Deployment(tmp_path);deploy.prepare()
    receipt=json.loads(deploy.receipt.read_text())
    result,calls=deploy.run('start',FAKE_DRIFT_STAGE='kms_pin_gate',FAKE_DRIFT_KIND='tag')
    assert result.returncode==0,result.stderr
    application=next(c for c in calls if c['stage']=='application')
    assert application['images']['api']==receipt['images']['api']
    assert application['images']['web']==receipt['images']['web']
    state=json.loads(deploy.state.read_text())
    assert state['containers']['api']['Image']==receipt['images']['api']
    assert state['images']['rsc-pilot-api:test-candidate']!=receipt['images']['api']


def test_compose_roundtrip_mismatch_is_caught_before_database_start(tmp_path):
    deploy=Deployment(tmp_path)
    result,calls=deploy.run('prepare',FAKE_ROUNDTRIP_BAD='1')
    assert result.returncode==2 and 'compose_snapshot_roundtrip_changed' in result.stderr
    assert mutations(calls)==[] and not deploy.receipt.exists()


@pytest.mark.parametrize('change',[
    lambda d:d['services']['api']['build'].update(context='/outside/candidate'),
    lambda d:d['services']['api']['build'].update(additional_contexts={'external':'https://invalid'}),
    lambda d:d['services']['api']['build'].update(dockerfile_inline='FROM remote'),
    lambda d:d['services']['db']['build'].update(dockerfile='other.Dockerfile'),
    lambda d:d['services']['web']['build']['args'].update(RELEASE_PROFILE='production'),
])
def test_external_or_unreviewed_build_inputs_block_before_build(tmp_path,change):
    deploy=Deployment(tmp_path);deploy.mutate_config(change)
    result,calls=deploy.run('prepare')
    assert result.returncode==2 and 'build_topology_invalid' in result.stderr
    assert mutations(calls)==[]


def test_project_lock_refuses_second_invocation_before_docker(tmp_path):
    deploy=Deployment(tmp_path)
    directory=tmp_path/'private-state/rsc-pilot-test';directory.mkdir(mode=0o700,parents=True)
    (tmp_path/'private-state').chmod(0o700)
    descriptor=os.open(directory/'deployment.lock',os.O_WRONLY|os.O_CREAT,0o600)
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        result,calls=deploy.run('prepare')
        assert result.returncode==2 and 'project_deployment_busy' in result.stderr
        assert calls==[]
    finally: os.close(descriptor)


def test_sigterm_stops_child_and_releases_lock_without_receipt(tmp_path):
    deploy=Deployment(tmp_path)
    process=subprocess.Popen(deploy.command('prepare'),env={**deploy.env,'FAKE_SLEEP':'build'},
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    ready=tmp_path/'child-ready'
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline: time.sleep(.02)
        assert ready.exists(),'synthetic child did not start'
        child=int(ready.read_text())
        process.send_signal(signal.SIGTERM)
        stdout,stderr=process.communicate(timeout=8)
        assert process.returncode==2 and 'code=interrupted' in stderr and 'prepared;' not in stdout
        assert not deploy.receipt.exists()
        with pytest.raises(ProcessLookupError): os.kill(child,0)
        # The interrupted attempt leaves retained snapshots for diagnosis, but
        # the lock is released and no success marker blocks a reviewed retry.
        deploy.prepare()
    finally:
        if process.poll() is None: process.kill();process.communicate()


def test_candidate_hash_ignores_runtime_db_but_binds_new_sources_and_image_inputs(tmp_path):
    deploy=Deployment(tmp_path)
    initial=tree_digest(deploy.root,candidate=True)
    (deploy.root/'local.db').write_bytes(b'runtime')
    assert tree_digest(deploy.root,candidate=True)==initial
    app=deploy.root/'backend/app/runtime';app.mkdir(parents=True)
    (app/'value.db').write_bytes(b'part of COPY app')
    changed=tree_digest(deploy.root,candidate=True)
    assert changed!=initial
    (app/'value.db').write_bytes(b'changed image input')
    assert tree_digest(deploy.root,candidate=True)!=changed


def test_canonical_config_decodes_exactly_one_layer_for_semantic_validation():
    assert decoded_config({'password':'a$$b$$$${literal}$$$$'})=={'password':'a$b$${literal}$$'}


@pytest.mark.parametrize('relative',['backend/app/.env','frontend/public/.test_payload.js','backend/app/runtime/data.db'])
def test_docker_copy_inputs_are_never_hidden_by_runtime_ignore(tmp_path,relative):
    deploy=Deployment(tmp_path)
    file=deploy.root/relative;file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text('before')
    previous=tree_digest(deploy.root,candidate=True)
    file.write_text('after')
    assert tree_digest(deploy.root,candidate=True)!=previous


def test_resolve_target_is_bound_to_prepare_receipt(tmp_path):
    deploy=Deployment(tmp_path);deploy.prepare()
    result,calls=deploy.run('start',SMOKE_RESOLVE_HOST='rscwz.cn',SMOKE_RESOLVE_IP='192.0.2.10')
    assert result.returncode==2 and 'prepared_inputs_changed' in result.stderr
    assert mutations(calls)==[]

@pytest.mark.parametrize('values',[
    {'SMOKE_RESOLVE_HOST':'rscwz.cn'}, {'SMOKE_RESOLVE_IP':'192.0.2.10'},
    {'SMOKE_RESOLVE_HOST':'other.invalid','SMOKE_RESOLVE_IP':'192.0.2.10'},
    {'SMOKE_RESOLVE_HOST':'rscwz.cn','SMOKE_RESOLVE_IP':'another.invalid'},
])
def test_invalid_resolve_binding_fails_before_docker(tmp_path,values):
    deploy=Deployment(tmp_path);result,calls=deploy.run('prepare',**values)
    assert result.returncode==2 and 'smoke_resolution_invalid' in result.stderr
    assert calls==[]


@pytest.mark.parametrize('change',[
    lambda d:d.update(secrets={'extra':{'file':'/outside/secret'}}),
    lambda d:d.update(configs={'extra':{'file':'/outside/config'}}),
    lambda d:d['services']['api'].update(env_file=['/outside/env']),
    lambda d:d['services']['api'].update(secrets=['external']),
])
def test_unsupported_external_configuration_sources_fail_closed(tmp_path,change):
    deploy=Deployment(tmp_path);deploy.mutate_config(change)
    result,calls=deploy.run('prepare')
    assert result.returncode==2 and 'unbound_configuration_source' in result.stderr
    assert mutations(calls)==[]

@pytest.mark.parametrize('damage',['truncated','symlink','wrong_mode'])
def test_invalid_receipt_never_starts_and_is_preserved(tmp_path,damage):
    deploy=Deployment(tmp_path);deploy.prepare()
    if damage=='truncated': deploy.receipt.write_text('{"schema":')
    elif damage=='symlink':
        alternate=tmp_path/'alternate.json';alternate.write_bytes(deploy.receipt.read_bytes())
        deploy.receipt.unlink();deploy.receipt.symlink_to(alternate)
    else: deploy.receipt.chmod(0o644)
    original=deploy.receipt.read_bytes()
    result,calls=deploy.run('start')
    assert result.returncode==2 and mutations(calls)==[]
    assert deploy.receipt.read_bytes()==original


def test_broken_receipt_symlink_cannot_be_overwritten_by_prepare(tmp_path):
    deploy=Deployment(tmp_path)
    deploy.receipt.parent.mkdir(mode=0o700,parents=True)
    for directory in [deploy.receipt.parent.parent,deploy.receipt.parent.parent.parent]: directory.chmod(0o700)
    deploy.receipt.symlink_to(tmp_path/'absent.json')
    result,calls=deploy.run('prepare')
    assert result.returncode==2 and 'successful_prepare_receipt_exists' in result.stderr
    assert calls==[] and deploy.receipt.is_symlink()


def test_head_query_discards_remote_libpq_environment_and_rc_files(tmp_path):
    binary=tmp_path/'psql'
    capture=tmp_path/'psql-input.json'
    binary.write_text(f'#!{sys.executable}\n'+
        'import json,os,sys\n'+
        'from pathlib import Path\n'+
        'Path(os.environ["CAPTURE"]).write_text(json.dumps({"args":sys.argv[1:],"env":dict(os.environ)}))\n'+
        'print("0134")\n')
    binary.chmod(0o700)
    observed=[]
    release=Release.__new__(Release);release.head='0134'
    def run(args,**kwargs):
        observed.append(args)
        environment={'PATH':str(tmp_path)+':/usr/bin:/bin','CAPTURE':str(capture),
            'PGHOSTADDR':'192.0.2.22','PGSERVICE':'unreviewed','PGSERVICEFILE':'/outside/service',
            'PGHOST':'remote.invalid','PGPORT':'8888','POSTGRES_USER':'owner','POSTGRES_DB':'pilot',
            'POSTGRES_PASSWORD':'synthetic-only'}
        result=subprocess.run(['sh','-c',args[-1]],env=environment,capture_output=True,text=True,check=True)
        return result.stdout
    release.run=run
    release.database_head({'container_id':'c'*64})
    data=json.loads(capture.read_text())
    assert observed[0][:5]==['docker','exec','c'*64,'sh','-c']
    assert not {'PGHOSTADDR','PGSERVICE','PGSERVICEFILE'}&set(data['env'])
    assert data['env']['PGHOST']=='/var/run/postgresql' and data['env']['PGPORT']=='5432'
    assert data['env']['PGOPTIONS']=='-c default_transaction_read_only=on -c statement_timeout=5000'
    assert data['args']==['-X','-U','owner','-d','pilot','-Atqc','SELECT version_num FROM public.alembic_version']


@pytest.mark.parametrize('database',['host=192.0.2.20','postgresql://remote.invalid/db','x'*64])
def test_database_name_cannot_be_interpreted_as_psql_connection_string(tmp_path,database):
    deploy=Deployment(tmp_path)
    deploy.mutate_config(lambda d:d['services']['db']['environment'].update(POSTGRES_DB=database))
    result,calls=deploy.run('prepare')
    assert result.returncode==2 and 'database_name_invalid' in result.stderr
    assert mutations(calls)==[]
