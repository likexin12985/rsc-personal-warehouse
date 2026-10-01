"""HTTP transaction and privacy contract; native business is tested separately."""
from types import SimpleNamespace
from datetime import datetime,timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
import pytest
from app.routers import formal_loss_return_stops as router
from app.dependencies import get_formal_principal
from app.database import get_db
from app.formal_services.stock_loss_corrections import bound_commands,bound_recovery
from test_loss_correction_http_adapter import request,fact,safe,identifier

BASE='/corrections/return-stops'
@pytest.fixture
def harness(monkeypatch):
    app=FastAPI();app.include_router(router.router);calls=[];permissions={'read','reverse_loss'}
    class DB:
        commits=0;rollbacks=0;fail_commit=False
        def commit(self):
            self.commits+=1;calls.append('commit')
            if self.fail_commit:raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-DB'))
        def rollback(self):self.rollbacks+=1;calls.append('rollback')
    db=DB()
    def allows(database,resource,action,**kwargs):
        assert database is db and resource=='stock_operation';calls.append('permission:'+action);return action in permissions
    actor=SimpleNamespace(user_id='synthetic-actor',allows=allows)
    app.dependency_overrides={get_db:lambda:db,get_formal_principal:lambda:actor}
    client=TestClient(app,raise_server_exceptions=False)
    yield SimpleNamespace(client=client,db=db,calls=calls,permissions=permissions,actor=actor)
    client.close()

@pytest.mark.parametrize('fault',['none','commit','invalid_output','database'])
def test_dedicated_execute_validates_before_one_commit_or_rollback(harness,monkeypatch,fault):
    body=request('inverses');answer=fact('inverses',body)
    def generic(*args,**kwargs):raise AssertionError('generic inverse used')
    monkeypatch.setattr(bound_commands,'inverse',generic)
    def execute(db,*,actor,request):
        assert db is harness.db and actor is harness.actor
        assert request.model_dump(mode='json')==body;harness.calls.append('stop')
        if fault=='database':raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-DB'))
        return dict(answer,command_jsonb={}) if fault=='invalid_output' else answer
    monkeypatch.setattr(bound_commands,'return_inverse',execute)
    harness.db.fail_commit=fault=='commit'
    result=safe(harness.client.post(BASE,json=body),200 if fault=='none' else 503)
    if fault=='none':assert result==answer
    assert harness.calls[:2]==['permission:reverse_loss','stop']
    assert harness.db.commits==int(fault in ('none','commit'))
    assert harness.db.rollbacks==int(fault!='none')

@pytest.mark.parametrize('state',['not_found','found','sealed','database'])
def test_dedicated_recovery_requires_only_read_and_never_writes(harness,monkeypatch,state):
    body=request('inverses');harness.permissions.clear();harness.permissions.add('read')
    answer=dict(request_state=state,retry_allowed=False,request_id=body['request_id'],request_hash='7'*64,result_scope='unconfirmed_request',result=None)
    if state=='found':answer.update(result_scope='historical_original_posting',result=fact('inverses',body))
    if state=='sealed':answer.update(result_scope='closed_original_request',seal=dict(seal_id=identifier(),root_disposition_id=body['root_disposition_id'],sealed_at=datetime.now(timezone.utc).isoformat(),stock_effect='none'))
    def forbidden(*args,**kwargs):raise AssertionError('query routed through generic or write')
    for method in ('return_inverse','seal_return_inverse'):monkeypatch.setattr(bound_commands,method,forbidden)
    monkeypatch.setattr(bound_recovery,'lookup',forbidden)
    def lookup(db,*,actor,request):
        harness.calls.append('bound-stop-lookup');assert request.model_dump(mode='json')==body
        if state=='database':raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-DB'))
        return answer
    monkeypatch.setattr(bound_recovery,'lookup_unshipped_return',lookup)
    result=safe(harness.client.post(BASE+'/request-lookup',json=body),503 if state=='database' else 200)
    if state!='database':assert result['request_state']==state and result['retry_allowed'] is False
    assert harness.calls==['permission:read','bound-stop-lookup']
    assert harness.db.commits==harness.db.rollbacks==0

@pytest.mark.parametrize('suffix',['','/request-seal','/request-lookup'])
@pytest.mark.parametrize('header',['X-Request-ID','Idempotency-Key'])
def test_mismatched_header_cannot_enter_any_service(harness,monkeypatch,suffix,header):
    def forbidden(*args,**kwargs):raise AssertionError('mismatch reached service')
    monkeypatch.setattr(bound_commands,'return_inverse',forbidden);monkeypatch.setattr(bound_commands,'seal_return_inverse',forbidden);monkeypatch.setattr(bound_recovery,'lookup_unshipped_return',forbidden)
    safe(harness.client.post(BASE+suffix,json=request('inverses'),headers={header:'different'}),400)
    assert harness.db.commits==harness.db.rollbacks==0


def test_application_registers_each_dedicated_route():
    from app.main import app
    routes={(r.path,method) for r in app.routes for method in getattr(r,'methods',())}
    base='/api/v1/stock-operations/loss-reports'+BASE
    assert (base+'/sources/{root_disposition_id}','GET') in routes
    for suffix in ('','/preview','/request-seal','/request-lookup'):assert (base+suffix,'POST') in routes
