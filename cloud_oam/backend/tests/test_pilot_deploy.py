"""Exercise real deployment coordination against stateful synthetic Docker only."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from test_pilot_preflight import deployment_document
from scripts.pilot_network_preflight import check_networks

ROOT = Path(__file__).resolve().parents[2]

FAKE_DOCKER = r'''
import hashlib, json, os, platform, sys, time
from pathlib import Path
args = sys.argv[1:]
state_path = Path(os.environ['FAKE_STATE'])
state = json.loads(state_path.read_text())
config_path = Path(os.environ['FAKE_CONFIG'])
config = json.loads(config_path.read_text())
execution = config
if '-f' in args and args[args.index('-f')+1].endswith('resolved.json'):
    execution = json.loads(Path(args[args.index('-f')+1]).read_text())
if args[:2] == ['network', 'ls']: stage = 'network_list'
elif args[:2] == ['network', 'inspect']: stage = 'network_inspect'
elif args[:2] == ['image', 'inspect']: stage = 'image_inspect'
elif args[:2] == ['image', 'tag']: stage = 'image_tag'
elif args[0] == 'inspect': stage = 'container_inspect'
elif args[0] == 'exec': stage = 'head'
elif args[0] == 'ps': stage = 'ports'
elif 'config' in args: stage = 'config'
elif 'build' in args: stage = 'build'
elif 'ps' in args: stage = 'prepared_database' if args[-1]=='db' else 'container_lookup'
elif args[-1] == 'db': stage = 'database'
elif args[-1] == 'migrate': stage = 'migration'
elif args[-1] == 'kms-pin-gate': stage = 'kms_pin_gate'
else: stage = 'application'
with open(os.environ['FAKE_LOG'], 'a') as output:
    output.write(json.dumps({'stage':stage,'args':args,'images':{
        k:v.get('image') for k,v in execution['services'].items()},'pid':os.getpid()})+'\n')
if stage == os.environ.get('FAKE_FAIL'):
    print('synthetic-secret-SHOULD-NOT-LEAK', file=sys.stderr)
    sys.exit(44)
if stage == os.environ.get('FAKE_SLEEP'):
    Path(os.environ['FAKE_READY']).write_text(str(os.getpid()))
    time.sleep(30)
def image_id(name): return 'sha256:'+hashlib.sha256(name.encode()).hexdigest()
def create(name, running):
    identity = hashlib.sha256(('container-'+name).encode()).hexdigest()
    image = execution['services'][name]['image']
    assert image.startswith('sha256:'), 'mutation did not use immutable image'
    state['containers'][name] = {'Id':identity,'Image':image,
        'Config':{'Labels':{'com.docker.compose.project':config['name'],'com.docker.compose.service':name}},
        'State':{'Running':running,'Status':'running' if running else 'exited','ExitCode':0},
        'Mounts':[{'Type':'volume','Destination':'/var/lib/postgresql/data',
            'Name':config['volumes']['postgres_data']['name'],
            'Source':'/var/lib/docker/volumes/synthetic/_data'}] if name=='db' else []}
if stage=='config':
    if os.environ.get('FAKE_ROUNDTRIP_BAD') and execution is not config:
        execution['services']['api']['environment']['OAM_SMS_SCHEME_NAME']='changed-by-reparse'
    print(json.dumps(execution))
elif stage=='network_list': print('existing-network-id')
elif stage=='network_inspect': print(os.environ['FAKE_NETWORKS'])
elif stage=='ports': print(os.environ.get('FAKE_PORTS',''))
elif stage=='build':
    state['images'].update({execution['services'][name]['image']:image_id(name) for name in ('db','api','web')})
elif stage=='image_tag': state['images'][args[-1]]=args[-2]
elif stage=='image_inspect':
    value=state['images'].get(args[-1])
    architecture={'x86_64':'amd64','aarch64':'arm64','arm64':'arm64'}.get(platform.machine().lower())
    print(json.dumps([{'Id':value,'Os':os.environ.get('FAKE_IMAGE_OS','linux'),
        'Architecture':os.environ.get('FAKE_IMAGE_ARCH',architecture)}] if value else []))
elif stage=='database': create('db',True)
elif stage=='migration': create('migrate',False);state['head']='0134'
elif stage=='kms_pin_gate': create('kms-pin-gate',False)
elif stage=='application': create('api',True);create('web',True)
elif stage in ('prepared_database','container_lookup'):
    row=state['containers'].get(args[-1]);print(row['Id'] if row else '')
elif stage=='container_inspect':
    print(json.dumps([row for row in state['containers'].values() if row['Id']==args[-1]]))
elif stage=='head': print(state['head'])
if stage == os.environ.get('FAKE_DRIFT_STAGE'):
    kind=os.environ.get('FAKE_DRIFT_KIND','config')
    if kind=='config':
        config['services']['api']['environment']['OAM_SMS_SCHEME_NAME']='changed-valid-scheme'
        config_path.write_text(json.dumps(config))
    elif kind=='env':
        with open(os.environ['PILOT_ENV_FILE'],'a') as output: output.write('# changed\n')
    elif kind=='source':
        Path(os.environ['FAKE_ROOT'],'new-release-source.py').write_text('changed=True\n')
    elif kind=='registry':
        registry=Path(config['services']['api']['volumes'][0]['source'].replace('$$','$'))
        registry.write_text(registry.read_text()+'\n')
    elif kind=='tag':
        for key in list(state['images']):
            if ':build-' not in key: state['images'][key]=image_id('changed-'+key)
        with open(os.environ['FAKE_LOG'],'a') as output:
            output.write(json.dumps({'stage':'retagged_public_images','images':{key:value for key,value in state['images'].items() if ':build-' not in key}})+'\n')
    elif kind=='database': state['containers']['db']['Id']='a'*64
    elif kind=='actual_image': state['containers']['api']['Image']=image_id('unexpected-image')
state_path.write_text(json.dumps(state))
'''


class Deployment:
    def __init__(self, tmp_path):
        self.path = tmp_path
        self.root = tmp_path/'candidate'
        scripts = self.root/'scripts';scripts.mkdir(parents=True)
        for name in ('deploy_pilot.sh','pilot_release.py','pilot_preflight.py','pilot_network_preflight.py'):
            shutil.copyfile(ROOT/'scripts'/name,scripts/name)
        (scripts/'smoke_test.sh').write_text('#!/bin/sh\nprintf \'%s\\n\' smoke >> "$FAKE_LOG"\n[ "$FAKE_FAIL" != smoke ]\n')
        versions=self.root/'backend/alembic/versions';versions.mkdir(parents=True)
        (versions/'0134.py').write_text("revision='0134'\ndown_revision=None\n")
        (self.root/'.env').write_text('# synthetic only\n')
        (self.root/'docker-compose.yml').write_text('# synthetic resolved fixture\n')
        document,_=deployment_document(tmp_path)
        for name in ('db','api','web'):
            document['services'][name]['build']={'context':str(self.root if name=='db' else self.root/('frontend' if name=='web' else 'backend')), 'dockerfile':'deployment/backup/Postgres.Dockerfile' if name=='db' else 'Dockerfile'}
        document['services']['web']['build']['args']={'RELEASE_PROFILE':'pilot'}
        self.config=tmp_path/'config.json';self.config.write_text(json.dumps(document))
        self.state=tmp_path/'docker-state.json';self.state.write_text(json.dumps({'images':{},'containers':{},'head':''}))
        binary=tmp_path/'bin';binary.mkdir()
        (binary/'python3').symlink_to(sys.executable)
        (binary/'docker').write_text(f'#!{sys.executable}\n'+FAKE_DOCKER)
        (binary/'docker').chmod(0o700)
        (binary/'ss').write_text(f'#!{sys.executable}\nimport os,sys\n'
            'assert sys.argv[1:] == ["-H", "-ltn"]\n'
            'sys.stdout.write(os.environ.get("FAKE_SS", ""))\n')
        (binary/'ss').chmod(0o700)
        self.log=tmp_path/'commands.jsonl'
        self.env={'PATH':str(binary)+os.pathsep+'/usr/bin:/bin',
            'PILOT_COMPOSE_PROJECT':'rsc-pilot-test','SMOKE_BASE_URL':'https://rscwz.cn',
            'PILOT_IMAGE_TAG':'test-candidate','PILOT_ENV_FILE':str(self.root/'.env'),
            'PILOT_STATE_DIR':str(tmp_path/'private-state'),'FAKE_CONFIG':str(self.config),
            'FAKE_STATE':str(self.state),'FAKE_LOG':str(self.log),'FAKE_ROOT':str(self.root),
            'FAKE_FAIL':'','FAKE_PORTS':'','FAKE_SS':'','FAKE_READY':str(tmp_path/'child-ready'),
            'FAKE_NETWORKS':json.dumps([{'Id':'existing-network-id','Name':'bridge','IPAM':{'Config':[]}}])}
        self.receipt=tmp_path/'private-state/rsc-pilot-test/receipts/test-candidate.json'

    def command(self, action): return ['sh',str(self.root/'scripts/deploy_pilot.sh'),action]

    def run(self, action, **overrides):
        self.log.unlink(missing_ok=True)
        result=subprocess.run(self.command(action),env={**self.env,**overrides},capture_output=True,text=True,timeout=30)
        calls=self.calls()
        assert 'synthetic-secret-SHOULD-NOT-LEAK' not in result.stdout+result.stderr
        assert 'jwt-' not in result.stdout+result.stderr
        return result,calls

    def calls(self):
        return [json.loads(line) if line.startswith('{') else {'stage':line}
                for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def prepare(self):
        result,_=self.run('prepare')
        assert result.returncode==0,result.stderr

    def mutate_config(self, change):
        document=json.loads(self.config.read_text());change(document)
        self.config.write_text(json.dumps(document))

    def mutate_state(self, change):
        state=json.loads(self.state.read_text());change(state)
        self.state.write_text(json.dumps(state))


def run_deploy(tmp_path, *, action='start', fail='', ports='', overrides=None, networks=None):
    deployment=Deployment(tmp_path)
    if action=='start': deployment.prepare()
    environment={'FAKE_FAIL':fail,'FAKE_PORTS':ports,**(overrides or {})}
    if networks is not None: environment['FAKE_NETWORKS']=json.dumps(networks)
    return deployment.run(action,**environment)


def mutations(calls):
    return [c['stage'] for c in calls if c['stage'] in {'build','database','migration','kms_pin_gate','application','smoke'}]


def test_start_requires_prepare_before_any_docker_call(tmp_path):
    deploy=Deployment(tmp_path);result,calls=deploy.run('start')
    assert result.returncode==2 and 'prepare_receipt_required' in result.stderr
    assert calls==[]


def test_isolated_start_runs_immutable_pin_gate_before_application_and_smoke(tmp_path):
    result,calls=run_deploy(tmp_path)
    assert result.returncode==0,result.stderr
    assert mutations(calls)==['kms_pin_gate','application','smoke']
    for call in calls:
        if call['stage'] in {'kms_pin_gate','application'}:
            assert '--no-deps' in call['args'] and '--no-build' in call['args']
            assert all(value.startswith('sha256:') for value in call['images'].values())
        if call['args'][0:1]==['compose'] if 'args' in call else False:
            assert call['args'][call['args'].index('--project-name')+1]=='rsc-pilot-test'
    head=next(c for c in calls if c['stage']=='head')
    assert 'PGHOST=/var/run/postgresql' in head['args'][-1]
    assert 'default_transaction_read_only=on' in head['args'][-1]
    assert 'unset PGHOSTADDR PGSERVICE PGSERVICEFILE;' in head['args'][-1]
    assert 'psql -X -U' in head['args'][-1]


def test_prepare_builds_and_migrates_then_writes_private_nosecret_receipt(tmp_path):
    deploy=Deployment(tmp_path);result,calls=deploy.run('prepare',FAKE_PORTS='0.0.0.0:443->443/tcp')
    assert result.returncode==0,result.stderr
    assert mutations(calls)==['build','database','migration']
    assert 'application is NOT started' in result.stdout
    receipt=json.loads(deploy.receipt.read_text())
    assert receipt['head']=='0134'
    assert set(receipt['images'])=={'api','web','db'}
    assert 'api-cccc' not in deploy.receipt.read_text() and 'environment' not in receipt
    assert deploy.receipt.stat().st_mode&0o777==0o600
    assert deploy.receipt.parent.stat().st_mode&0o777==0o700
    assert not list(deploy.receipt.parent.parent.glob('.compose-*'))
    assert all(Path(row['snapshot']).exists() for row in receipt['static_mounts'].values())


@pytest.mark.parametrize('ports',['0.0.0.0:80->80/tcp','[::]:443->8443/tcp','127.0.0.1:80->8080/tcp','0.0.0.0:443->8443/udp'])
def test_occupied_ports_stop_before_start_mutation(tmp_path,ports):
    result,calls=run_deploy(tmp_path,ports=ports,overrides={'PILOT_ALLOW_PORT_CUTOVER':'true'})
    assert result.returncode==2 and 'stage=port_check' in result.stderr
    assert mutations(calls)==[]


@pytest.mark.parametrize('listener', [
    'LISTEN 0 4096 0.0.0.0:80 0.0.0.0:*',
    'LISTEN 0 4096 [::]:443 [::]:*',
    'LISTEN 0 128 127.0.0.1:443 0.0.0.0:*',
])
def test_host_listener_stops_before_pin_gate_or_application(tmp_path, listener):
    result,calls=run_deploy(tmp_path,overrides={'FAKE_SS':listener})
    assert result.returncode==2 and 'stage=port_check code=public_ports_occupied' in result.stderr
    assert mutations(calls)==[]


def test_unparseable_host_socket_output_fails_closed(tmp_path):
    result,calls=run_deploy(tmp_path,overrides={'FAKE_SS':'LISTEN truncated'})
    assert result.returncode==2 and 'stage=port_check code=host_socket_inspection_invalid' in result.stderr
    assert mutations(calls)==[]


@pytest.mark.parametrize('action,fail,stage',[
    ('start','config','preflight'),('start','ports','port_check'),
    ('prepare','build','build'),('prepare','database','database'),('prepare','migration','migration'),
    ('start','prepared_database','receipt_check'),('start','kms_pin_gate','kms_pin_gate'),
    ('start','application','application'),('prepare','network_list','network_check'),
    ('prepare','network_inspect','network_check'),('prepare','head','migration')])
def test_failure_stops_without_destructive_cleanup(tmp_path,action,fail,stage):
    result,calls=run_deploy(tmp_path,action=action,fail=fail)
    assert result.returncode==2 and 'stage='+stage in result.stderr
    assert calls[-1]['stage']==fail
    assert not any('down' in c.get('args',[]) or 'rm' in c.get('args',[]) for c in calls)
    if action=='prepare': assert not (tmp_path/'private-state/rsc-pilot-test/receipts/test-candidate.json').exists()


@pytest.mark.parametrize('overrides',[{'PILOT_COMPOSE_PROJECT':''},{'PILOT_COMPOSE_PROJECT':'star-oam'},
    {'SMOKE_BASE_URL':'https://another.invalid'},{'SMOKE_PRIVATE_PATH':'/'},
    {'PILOT_STATE_DIR':'relative-state'}])
def test_wrong_project_target_or_state_path_never_mutates(tmp_path,overrides):
    deploy=Deployment(tmp_path);result,calls=deploy.run('prepare',**overrides)
    assert result.returncode==2 and mutations(calls)==[]


def test_failed_smoke_bounded_and_partial_deployment_never_claims_verified(tmp_path):
    result,calls=run_deploy(tmp_path,fail='smoke')
    assert result.returncode==2 and 'stage=smoke' in result.stderr
    assert sum(c['stage']=='smoke' for c in calls)==3
    assert 'completed' not in result.stdout


@pytest.mark.parametrize('networks,error',[
    ([], 'network_inspection_incomplete'),([{'Id':'different-id'}],'network_inspection_incomplete'),
    ([{'Id':'existing-network-id','Name':'old_backend','IPAM':{'Config':[{'Subnet':'172.18.0.0/16'}]}}],'network_preflight_refused')])
def test_invalid_network_inspection_blocks_prepare(tmp_path,networks,error):
    result,calls=run_deploy(tmp_path,action='prepare',networks=networks)
    assert result.returncode==2 and error in result.stderr and mutations(calls)==[]
@pytest.mark.parametrize("subnet", ["172.18.0.0/16", "172.18.4.0/24", "172.16.0.0/12"])
def test_other_network_subnet_overlap_is_rejected_even_if_no_containers(tmp_path, subnet):
    document, _ = deployment_document(tmp_path)
    existing = [{"Name": "old_backend", "IPAM": {"Config": [{"Subnet": subnet}]}, "Containers": {}}]
    assert check_networks(document, existing, "rsc-pilot-test") == ["docker_subnet_overlap"]


def test_disjoint_networks_and_exact_owned_network_can_be_reused(tmp_path):
    document, _ = deployment_document(tmp_path)
    existing = [
        {"Name": "old_backend", "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]}},
        {"Name": "rsc-pilot-test_backend", "IPAM": {"Config": [{"Subnet": "172.18.0.0/16"}]},
         "Labels": {"com.docker.compose.project": "rsc-pilot-test", "com.docker.compose.network": "backend"}},
    ]
    assert check_networks(document, existing, "rsc-pilot-test") == []


@pytest.mark.parametrize("change", [
    lambda n: n.update(Labels={}),
    lambda n: n["IPAM"].update(Config=[{"Subnet": "172.30.0.0/24"}]),
])
def test_same_network_name_does_not_hide_wrong_owner_or_allocation(tmp_path, change):
    document, _ = deployment_document(tmp_path)
    network = {"Name": "rsc-pilot-test_backend", "IPAM": {"Config": [{"Subnet": "172.18.0.0/16"}]},
               "Labels": {"com.docker.compose.project": "rsc-pilot-test", "com.docker.compose.network": "backend"}}
    change(network)
    assert check_networks(document, [network], "rsc-pilot-test") == ["existing_pilot_network_mismatch"]
