"""Strict command transport, private error responses and bounded worker slots."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings,Settings
from app.routers import formal_daily_reconciliation as routes
from app.daily_reconciliation import review_entry

@pytest.fixture
def http():
    command=dict(operation='open',cutoff_id=str(uuid4()),expected_cutoff_sha256='a'*64,
        expected_comparison_sha256='b'*64,expected_version=0,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    executor=Mock(return_value={'outcome':'observed','receipt':{'recorded':False,'stock_written':False}})
    old=dict(app.dependency_overrides)
    app.dependency_overrides[get_settings]=lambda:Settings(environment='test',database_url='postgresql+psycopg://synthetic/local',app_origin='https://rscwz.cn')
    app.dependency_overrides[routes.get_review_command_executor]=lambda:executor
    # No lifespan: real production startup is verified independently on native PG.
    client=TestClient(app)
    try:yield SimpleNamespace(client=client,executor=executor,command=command,
        body=dict(expected_authorization_version=1,command=command),
        headers={'Authorization':'Bearer synthetic-not-a-real-token','Idempotency-Key':command['idempotency_key'],'X-Request-Id':command['request_id']},
        url='/api/v1/reconciliations/daily/'+command['cutoff_id'])
    finally:client.close();app.dependency_overrides.clear();app.dependency_overrides.update(old)

def private(response):
    assert 'private' in response.headers['cache-control'] and 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy']=='no-referrer'

@pytest.mark.parametrize('suffix,recover',[('/commands',False),('/command-status',True)])
def test_fixed_worker_gets_only_valid_bound_commands(http,suffix,recover):
    r=http.client.post(http.url+suffix,json=http.body,headers=http.headers)
    assert r.status_code==200 and r.json()['receipt']['stock_written'] is False;private(r)
    args=http.executor.call_args.kwargs
    assert args['recover'] is recover and args['command']==http.command
    assert args['database_url']=='postgresql+psycopg://synthetic/local'

@pytest.mark.parametrize('fault',['missing_auth','duplicate_auth','cookie_only','wrong_scheme','missing_key','duplicate_key','key_mismatch','path_mismatch','query','extra_dsn','extra_actor','bool_version','negative_version','unknown_operation'])
def test_transport_rejects_before_any_worker(http,fault):
    headers=dict(http.headers);body={**http.body,'command':dict(http.command)};url=http.url+'/commands'
    if fault in ('missing_auth','cookie_only'):headers.pop('Authorization')
    elif fault=='wrong_scheme':headers['Authorization']='Basic synthetic'
    elif fault=='missing_key':headers.pop('Idempotency-Key')
    elif fault=='key_mismatch':headers['Idempotency-Key']=uuid4().hex
    elif fault=='path_mismatch':url='/api/v1/reconciliations/daily/'+str(uuid4())+'/commands'
    elif fault=='query':url+='?database_url=secret'
    elif fault=='extra_dsn':body['database_url']='postgresql://untrusted'
    elif fault=='extra_actor':body['command']['actor_user_id']='forged'
    elif fault=='bool_version':body['expected_authorization_version']=True
    elif fault=='negative_version':body['command']['expected_version']=-1
    elif fault=='unknown_operation':body['command']['operation']='write_stock'
    if fault.startswith('duplicate_'):headers=list(headers.items())+[(('Authorization' if fault.endswith('auth') else 'Idempotency-Key'),'duplicate')]
    if fault=='cookie_only':headers['Cookie']='access_token=synthetic'
    r=http.client.post(url,json=body,headers=headers)
    assert r.status_code==(403 if fault=='cookie_only' else 401 if fault in ('missing_auth','duplicate_auth','wrong_scheme') else 422);private(r)
    assert not http.executor.called

@pytest.mark.parametrize('suffix,recover',[('/commands',False),('/command-status',True)])
def test_same_origin_cookie_enters_the_same_supervised_worker(http,suffix,recover):
    headers={k:v for k,v in http.headers.items() if k!='Authorization'}
    headers.update(Cookie='other=ok; access_token=synthetic-cookie-token',Origin='https://rscwz.cn')
    r=http.client.post(http.url+suffix,json=http.body,headers=headers)
    assert r.status_code==200;private(r)
    assert http.executor.call_args.kwargs['access_token']=='synthetic-cookie-token'
    assert http.executor.call_args.kwargs['recover'] is recover

@pytest.mark.parametrize('fault,status',[
    ('missing_origin',403),('foreign_origin',403),('subdomain_origin',403),('null_origin',403),
    ('duplicate_origin',403),('cross_site',403),('same_site',403),('duplicate_site',403),
    ('duplicate_cookie',401),('separate_cookie_headers',401),('empty_cookie',401),
    ('quoted_cookie',401),('long_cookie',401),('malformed_authorization',401),
    ('unconfigured_origin',503),('path_in_origin',503),('userinfo_origin',503),('bad_port',503),
    ('insecure_production_origin',503),
])
def test_cookie_origin_and_credential_ambiguity_fail_before_worker(http,fault,status):
    headers={k:v for k,v in http.headers.items() if k!='Authorization'}
    headers.update(Cookie='access_token=synthetic',Origin='https://rscwz.cn',**{'Sec-Fetch-Site':'same-origin'})
    if fault=='missing_origin':headers.pop('Origin')
    if fault=='foreign_origin':headers['Origin']='https://attacker.invalid'
    if fault=='subdomain_origin':headers['Origin']='https://other.rscwz.cn'
    if fault=='null_origin':headers['Origin']='null'
    if fault in ('cross_site','same_site'):headers['Sec-Fetch-Site']=fault.replace('_','-')
    if fault=='duplicate_cookie':headers['Cookie']+='; access_token=another'
    if fault=='empty_cookie':headers['Cookie']='access_token='
    if fault=='quoted_cookie':headers['Cookie']='access_token="synthetic"'
    if fault=='long_cookie':headers['Cookie']='access_token='+'x'*8193
    if fault=='malformed_authorization':headers['Authorization']='Basic synthetic'
    extra={'duplicate_origin':('Origin','https://rscwz.cn'),'duplicate_site':('Sec-Fetch-Site','same-origin'),
           'separate_cookie_headers':('Cookie','access_token=another')}
    if fault in extra:headers=list(headers.items())+[extra[fault]]
    origins={'unconfigured_origin':'','path_in_origin':'https://rscwz.cn/xx','userinfo_origin':'https://user@rscwz.cn',
             'bad_port':'https://rscwz.cn:bad','insecure_production_origin':'http://rscwz.cn'}
    if fault in origins:
        settings=Settings(environment='test',database_url='postgresql+psycopg://synthetic/local',app_origin=origins[fault])
        if fault=='insecure_production_origin':settings=settings.model_copy(update={'environment':'production'})
        app.dependency_overrides[get_settings]=lambda:settings
    r=http.client.post(http.url+'/commands',json=http.body,headers=headers)
    assert r.status_code==status;private(r)
    assert not http.executor.called

def test_explicit_bearer_remains_independent_of_ambient_cookie(http):
    headers={**http.headers,'Cookie':'access_token=another','Origin':'https://external-client.invalid'}
    r=http.client.post(http.url+'/commands',json=http.body,headers=headers)
    assert r.status_code==200
    assert http.executor.call_args.kwargs['access_token']=='synthetic-not-a-real-token'

@pytest.mark.parametrize('error,code',[(review_entry.ReviewEntryBusy,'capacity_busy'),(review_entry.ProcessOutcomeUnknown,'outcome_unknown_use_exact_recovery'),(review_entry.ProcessEntryError,'outcome_unknown_use_exact_recovery')])
def test_unknown_outcome_never_retries_or_leaks_secrets(http,error,code):
    http.executor.side_effect=error('SECRET token database URL')
    r=http.client.post(http.url+'/commands',json=http.body,headers=http.headers)
    assert r.status_code==503 and r.json()['detail']['code']=='daily_review_'+code;private(r)
    assert 'SECRET' not in r.text and http.executor.call_count==1

@pytest.mark.parametrize('result,status',[({'outcome':'rejected','status':403,'code':'daily_review_forbidden'},403),({'outcome':'unknown'},503),({'outcome':'observed','receipt':{'bad':'SECRET'}},503)])
def test_worker_rejections_and_invalid_receipts(http,result,status):
    http.executor.return_value=result
    r=http.client.post(http.url+'/commands',json=http.body,headers=http.headers)
    assert r.status_code==status and 'SECRET' not in r.text;private(r)

def test_capacity_bounds_workers_and_keeps_unconfirmed_slot(monkeypatch):
    from threading import BoundedSemaphore
    capacity=BoundedSemaphore(1);monkeypatch.setattr(review_entry,'_CAPACITY',capacity)
    run=Mock(side_effect=review_entry.ProcessOutcomeUnknown('unknown'));monkeypatch.setattr(review_entry,'run_owned_job',run)
    args=dict(database_url='unused',command={},access_token='unused',expected_authorization_version=1)
    for _ in range(2):
        with pytest.raises(review_entry.ProcessOutcomeUnknown):review_entry.execute(**args)
    assert run.call_count==2
    run.side_effect=review_entry.ProcessEntryError('worker_cleanup_unconfirmed_pid_123')
    with pytest.raises(review_entry.ProcessEntryError):review_entry.execute(**args)
    with pytest.raises(review_entry.ReviewEntryBusy):review_entry.execute(**args)
    assert run.call_count==3

@pytest.mark.parametrize('method,path',[('get','/not-a-uuid'),('get','/not-a-uuid/history'),('delete','/not-a-uuid'),('post','/not-a-uuid/commands')])
def test_framework_errors_are_private(http,method,path):
    r=getattr(http.client,method)('/api/v1/reconciliations/daily'+path)
    assert r.status_code in (401,405,422);private(r)
