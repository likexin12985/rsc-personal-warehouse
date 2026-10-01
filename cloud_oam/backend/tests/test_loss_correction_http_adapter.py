"""HTTP adapter contract only; native DB service acceptance is a separate gate."""
from pathlib import Path
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4
import importlib.util,runpy,sys
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.routers import formal_loss_corrections as router
from app.dependencies import get_formal_principal
from app.database import get_db
from app.formal_services.stock_loss_corrections import bound_commands,bound_recovery,reversal_stock,correction_stock


def identifier():return str(uuid4())

def request(flow):
    base=dict(root_disposition_id=identifier(),expected_root_request_hash='1'*64,
              expected_submission_plan_hash='2'*64,reason='Synthetic correction reason',
              request_id='synthetic-request-1',idempotency_key='PRIVATE-original-key-1')
    if flow=='inverses':base.update(reversed_correction_id=None,expected_execution_request_hash='3'*64,expected_plan_hash='4'*64)
    else:
        base.update(reversal_id=identifier(),expected_reversal_hash='5'*64)
        if flow=='approvals':base['disposition']='restore_available'
        else:base.update(correction_decision_id=identifier(),expected_correction_decision_hash='6'*64,expected_plan_hash='4'*64)
    return base


def fact(flow,body):
    result=dict(root_disposition_id=body['root_disposition_id'],operation_id=identifier(),line_id=identifier(),
        original_headquarters_decision_id=identifier(),requester_person_id=identifier(),actor_user_id='synthetic-actor',
        actor_person_id=identifier(),authorization_version=1,reason=body['reason'],request_id=body['request_id'],request_hash='7'*64)
    if flow=='approvals':return dict(result,correction_decision_id=identifier(),reversal_id=body['reversal_id'],
        expected_reversal_hash=body['expected_reversal_hash'],disposition='restore_available',approval_stage='approved',stock_effect='none')
    result.update(posting_transaction_id=identifier(),posting_movement_id=identifier(),source_account_id=identifier(),target_account_id=identifier(),quantity='1.000',plan_hash='4'*64,status='posted')
    if flow=='inverses':return dict(result,reversal_id=identifier(),original_execution_id=body['root_disposition_id'],reversed_correction_id=None,original_transaction_id=identifier(),original_movement_id=identifier(),stock_effect='restores_original_frozen_share')
    return dict(result,correction_execution_id=identifier(),correction_decision_id=body['correction_decision_id'],reversal_id=body['reversal_id'],disposition='restore_available',return_operation_id=None,return_fulfillment_required=False,stock_effect='frozen_to_available')

ACTIONS={'inverses':'reverse_loss','approvals':'approve_loss_correction','executions':'correct_loss'}
METHODS={'inverses':'inverse','approvals':'approve','executions':'correct'}
SCOPES={'inverses':'historical_original_posting','approvals':'historical_approval','executions':'historical_correction_posting'}

@pytest.fixture
def harness(monkeypatch):
    app=FastAPI();app.include_router(router.router)
    calls=[]
    class DB:
        commits=0
        rollbacks=0
        failure=None
        def commit(self):
            self.commits+=1;calls.append('commit')
            if self.failure:raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-DB'))
        def rollback(self):self.rollbacks+=1;calls.append('rollback')
    db=DB();permissions=set(ACTIONS.values())|{'read'}
    def allows(database,resource,action,**kwargs):
        assert database is db and resource=='stock_operation';calls.append('permission:'+action);return action in permissions
    principal=SimpleNamespace(user_id='synthetic-actor',allows=allows)
    app.dependency_overrides={get_db:lambda:db,get_formal_principal:lambda:principal}
    value=SimpleNamespace(client=TestClient(app,raise_server_exceptions=False),db=db,calls=calls,permissions=permissions,principal=principal)
    yield value
    value.client.close()


def safe(reply,status):
    assert reply.status_code==status,reply.text
    assert reply.headers['cache-control']=='private, no-store'
    assert 'PRIVATE-' not in reply.text and 'command_jsonb' not in reply.text and 'key_hash' not in reply.text
    return reply.json()

@pytest.mark.parametrize('flow',ACTIONS)
@pytest.mark.parametrize('fault',['none','commit','invalid_output','database','unexpected'])
def test_validate_then_single_commit_or_rollback(harness,monkeypatch,flow,fault):
    body=request(flow);answer=fact(flow,body)
    def call(db,*,actor,request):
        assert db is harness.db and actor is harness.principal
        assert request.model_dump(mode='json')==body;harness.calls.append('service')
        if fault=='invalid_output':return dict(answer,command_jsonb={'idempotency_key':'PRIVATE-leak'})
        if fault=='database':raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-DB'))
        if fault=='unexpected':raise RuntimeError('PRIVATE-internal')
        return answer
    monkeypatch.setattr(bound_commands,METHODS[flow],call)
    if fault=='commit':harness.db.failure=True
    reply=harness.client.post('/corrections/'+flow,json=body)
    if fault=='unexpected':
        assert reply.status_code==500 and 'PRIVATE-' not in reply.text
    else:
        result=safe(reply,200 if fault=='none' else 503)
        if fault=='none':assert result==answer
    assert harness.calls[:2]==['permission:'+ACTIONS[flow],'service']
    assert harness.db.commits==(1 if fault in ('none','commit') else 0)
    assert harness.db.rollbacks==(0 if fault=='none' else 1)

@pytest.mark.parametrize('flow',ACTIONS)
@pytest.mark.parametrize('state',['not_found','found','sealed','database'])
def test_read_permission_only_recovery_never_commits_or_falls_back(harness,monkeypatch,flow,state):
    body=request(flow);harness.permissions.clear();harness.permissions.add('read')
    answer=dict(request_state=state,retry_allowed=False,request_id=body['request_id'],request_hash='7'*64,
                result_scope='unconfirmed_request',result=None)
    if state=='found':answer.update(result_scope=SCOPES[flow],result=fact(flow,body))
    if state=='sealed':answer.update(result_scope='closed_original_request',seal=dict(seal_id=identifier(),root_disposition_id=body['root_disposition_id'],sealed_at=datetime.now(timezone.utc).isoformat(),stock_effect='none'))
    def forbidden(*args,**kwargs):raise AssertionError('read invoked mutation or preview')
    for method in ('inverse','approve','correct','seal'):monkeypatch.setattr(bound_commands,method,forbidden)
    monkeypatch.setattr(reversal_stock,'prepare',forbidden);monkeypatch.setattr(correction_stock,'prepare',forbidden)
    def lookup(db,*,actor,request):
        assert request.model_dump(mode='json')==body;harness.calls.append('lookup')
        if state=='database':raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-DB'))
        return answer
    monkeypatch.setattr(bound_recovery,'lookup',lookup)
    result=safe(harness.client.post('/corrections/'+flow+'/request-lookup',json=body),503 if state=='database' else 200)
    if state!='database':assert result['request_state']==state and result['retry_allowed'] is False
    assert harness.calls==['permission:read','lookup']
    assert harness.db.commits==harness.db.rollbacks==0

@pytest.mark.parametrize('flow',ACTIONS)
@pytest.mark.parametrize('suffix',['','/request-seal','/request-lookup'])
def test_header_mismatch_refuses_before_service(harness,monkeypatch,flow,suffix):
    def forbidden(*args,**kwargs):raise AssertionError('coordinate mismatch reached service')
    for method in ('inverse','approve','correct','seal'):monkeypatch.setattr(bound_commands,method,forbidden)
    monkeypatch.setattr(bound_recovery,'lookup',forbidden)
    safe(harness.client.post('/corrections/'+flow+suffix,json=request(flow),headers={'Idempotency-Key':'wrong-key'}),400)
    assert harness.db.commits==harness.db.rollbacks==0

@pytest.mark.parametrize('flow',ACTIONS)
@pytest.mark.parametrize('suffix',['','/request-seal','/request-lookup'])
def test_exact_permission_is_required(harness,flow,suffix):
    required='read' if suffix=='/request-lookup' else ACTIONS[flow]
    harness.permissions.remove(required)
    reply=harness.client.post('/corrections/'+flow+suffix,json=request(flow))
    assert reply.status_code==403 and harness.calls==['permission:'+required]
    assert harness.db.commits==harness.db.rollbacks==0

@pytest.mark.parametrize('flow',ACTIONS)
@pytest.mark.parametrize('state',['found','sealed','not_found'])
def test_seal_validates_result_and_commits_once(harness,monkeypatch,flow,state):
    body=request(flow)
    answer=dict(request_state=state,retry_allowed=False,request_id=body['request_id'],request_hash='7'*64,result=None,result_scope='unconfirmed_request')
    if state=='found':answer.update(result=fact(flow,body),result_scope=SCOPES[flow])
    if state=='sealed':answer.update(result_scope='closed_original_request',seal=dict(seal_id=identifier(),root_disposition_id=body['root_disposition_id'],sealed_at=datetime.now(timezone.utc).isoformat(),stock_effect='none'))
    monkeypatch.setattr(bound_commands,'seal',lambda *args,**kwargs:answer)
    reply=harness.client.post('/corrections/'+flow+'/request-seal',json=body)
    safe(reply,503 if state=='not_found' else 200)
    assert harness.db.commits==(0 if state=='not_found' else 1)
    assert harness.db.rollbacks==(1 if state=='not_found' else 0)

@pytest.mark.parametrize('flow',['inverses','executions'])
def test_preview_projection_hides_internal_proof_and_never_commits(harness,monkeypatch,flow):
    body=request(flow);body.pop('request_id');body.pop('idempotency_key');body.pop('expected_plan_hash')
    document=dict(root_disposition_id=body['root_disposition_id'],source_account_id=identifier(),target_account_id=identifier(),
        source_condition='new',target_condition='new',quantity='1.000',serial_ids=[],intent=body,
        frozen_holds_before={'PRIVATE-proof':'PRIVATE-value'},actor_user_id='PRIVATE-actor',authorization_version=1)
    if flow=='inverses':document.update(original_execution_id=body['root_disposition_id'],original_transaction_id=identifier(),original_movement_id=identifier())
    else:document.update(reversal_id=body['reversal_id'],correction_decision_id=body['correction_decision_id'],disposition='restore_available',target_requires_creation=False)
    module=reversal_stock if flow=='inverses' else correction_stock
    monkeypatch.setattr(module,'prepare',lambda *args,**kwargs:SimpleNamespace(document=document,plan_hash='4'*64,checked_at=datetime.now(timezone.utc)))
    result=safe(harness.client.post('/corrections/'+flow+'/preview',json=body),200)
    assert result['stock_effect']=='none' and result['planning_status']=='preview_only'
    assert 'intent' not in result and 'frozen_holds_before' not in result
    assert harness.db.commits==harness.db.rollbacks==0


def test_complete_application_registers_exact_correction_and_return_routes():
    from app.main import app
    prefix='/api/v1/stock-operations/loss-reports/corrections'
    actual={(route.path,tuple(sorted(route.methods))) for route in app.routes
            if hasattr(route,'methods') and route.path.startswith(prefix)}
    expected={(prefix+'/'+flow+suffix,('POST',)) for flow in ACTIONS
              for suffix in ('','/request-lookup','/request-seal')}
    expected|={(prefix+'/'+flow+'/preview',('POST',)) for flow in ('inverses','executions')}
    expected.add((prefix+'/sources/{root_disposition_id}',('GET',)))
    expected.add((prefix+'/return-history/{root_disposition_id}',('GET',)))
    expected.add((prefix+'/return-stops/sources/{root_disposition_id}',('GET',)))
    expected|={(prefix+'/return-stops'+suffix,('POST',))
               for suffix in ('','/preview','/request-lookup','/request-seal')}
    assert actual==expected
