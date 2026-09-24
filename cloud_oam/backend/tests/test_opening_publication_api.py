from uuid import uuid4
from unittest.mock import Mock
import pytest
from app.formal_services import opening_publication_admission as admission,opening_start_recovery as recovery
from app.formal_services.opening_stocktake import OpeningStocktakeError
from test_formal_opening_stocktake_write_api import write_api_client,_headers,_start_body

PREFIX='/api/v1/stocktakes/opening'

def body():
    return {key:value for key,value in _start_body().items() if key not in {'control_source_system_id','control_sync_run_id','control_sync_scope_key','control_lines'}}|{'publication_id':str(uuid4())}

def test_write_headers_and_permissions_precede_selection(write_api_client,monkeypatch):
    client,db,principal=write_api_client;probe=Mock();monkeypatch.setattr(admission,'selection',probe)
    assert client.post(PREFIX+'/from-publication',json=body()).status_code==400
    principal.allowed_actions.clear()
    assert client.post(PREFIX+'/from-publication',json=body(),headers=_headers()).status_code==403
    assert not probe.called and not db.commit.called

@pytest.mark.parametrize('extra',[{'control_lines':[]},{'control_sync_run_id':str(uuid4())},{'publication_sha256':'a'*64}])
def test_client_cannot_inject_server_control_input(write_api_client,monkeypatch,extra):
    client,db,_=write_api_client;probe=Mock();monkeypatch.setattr(admission,'selection',probe)
    assert client.post(PREFIX+'/from-publication',json=body()|extra,headers=_headers()).status_code==422
    assert not probe.called and not db.commit.called

def test_selection_failure_rolls_back_before_command(write_api_client,monkeypatch):
    client,db,_=write_api_client
    monkeypatch.setattr(admission,'selection',Mock(side_effect=OpeningStocktakeError('control_publication_not_admissible','precondition_failed','批次不可用')))
    response=client.post(PREFIX+'/from-publication',json=body(),headers=_headers())
    assert response.status_code==412 and db.rollback.call_count==1 and not db.commit.called

def test_recovery_query_is_strict_and_no_store(write_api_client,monkeypatch):
    client,db,_=write_api_client
    params=dict(region_org_id=str(uuid4()),publication_id=str(uuid4()),trace_request_id=uuid4().hex)
    result=dict(schema_version='rsc.opening_start_recovery.v1',actor_person_id=str(uuid4()),authorization_version=1,
        region_org_id=params['region_org_id'],publication_id=params['publication_id'],outcome='not_observed',automatic_retry_allowed=False,result=None)
    probe=Mock(return_value=result);monkeypatch.setattr(recovery,'recover_start',probe)
    response=client.get(PREFIX+'/start-result',params=params)
    assert response.status_code==200 and response.headers['cache-control']=='no-store' and not response.json()['automatic_retry_allowed']
    assert client.get(PREFIX+'/start-result',params=params|{'extra':'x'}).status_code==422
    assert client.get(PREFIX+'/start-result',params=list(params.items())+[('publication_id',params['publication_id'])]).status_code==422
    assert probe.call_count==1 and not db.commit.called
    probe.side_effect=OpeningStocktakeError('opening_recovery_unavailable','service_unavailable','证据不可用')
    response=client.get(PREFIX+'/start-result',params=params)
    assert response.status_code==503 and response.headers['cache-control']=='no-store'

@pytest.mark.parametrize('known',[True,False])
def test_commit_denial_requires_named_database_evidence(write_api_client,monkeypatch,known):
    from types import SimpleNamespace
    from sqlalchemy.exc import DBAPIError
    from app.formal_services import opening_stocktake as service
    from test_formal_opening_stocktake_write_api import _configure_evidence,_start_result
    client,db,_=write_api_client
    _configure_evidence(db)
    monkeypatch.setattr(service,'start_opening_stocktake',Mock(return_value=_start_result()))
    original=RuntimeError('synthetic driver details must not reach the response')
    original.sqlstate='42501'
    original.diag=SimpleNamespace(constraint_name='opening_actor_admission_0126' if known else 'unrelated')
    failure=DBAPIError('synthetic SQL',{},original)
    db.commit.side_effect=failure
    if known:
        response=client.post(PREFIX,json=_start_body(),headers=_headers())
        assert response.status_code==403 and response.headers['cache-control']=='no-store'
        assert response.json()['detail']['code']=='opening_authorization_changed'
        assert 'driver' not in response.text and 'synthetic SQL' not in response.text
    else:
        with pytest.raises(DBAPIError):client.post(PREFIX,json=_start_body(),headers=_headers())
    assert db.commit.call_count==1 and db.rollback.call_count==1
