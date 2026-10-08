from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.formal_services import material_request_closure as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_closure_schemas import MaterialRequestClosureOut
from test_formal_material_request_api import api_client, REQUEST_ID, REVISION_ID, LINE_ID

HEADERS = {'Idempotency-Key': 'closure-http-request-0001', 'X-Request-ID': 'closure-http-trace-0001'}
PAYLOAD = {'expected_request_version': 9, 'reason': '已核验入账'}


def result(*, replayed=False):
    return MaterialRequestClosureOut(closure_id=uuid4(), request_id=REQUEST_ID, revision_id=REVISION_ID,
        request_version=9, closed_at=datetime.now(timezone.utc), evidence_sha256='a'*64, replayed=replayed,
        lines=[dict(request_line_id=LINE_ID, approved_qty='1.000', posted_qty='1.000', cancelled_qty='0.000', remaining_qty='0.000')])


def test_close_commits_once_and_returns_independent_fact(api_client, monkeypatch):
    client, db, principal, _, settings = api_client
    settings.material_request_writes_enabled = True
    action = Mock(return_value=result())
    monkeypatch.setattr(service, 'close_material_request', action)
    response = client.post(f'/api/v1/material-requests/{REQUEST_ID}/close', json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 201, response.text
    assert response.json()['business_status'] == 'closed'
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['actor'] is principal['value']
    assert action.call_args.kwargs['_include_returns'] is True
    assert action.call_args.kwargs['payload'].model_dump() == PAYLOAD
    db.commit.assert_called_once()


@pytest.mark.parametrize('failure,status', [
    (MaterialRequestReadError('closure_forbidden', 'forbidden', '无关闭权限'),403),
    (MaterialRequestReadError('closure_incomplete', 'precondition_failed', '未完成入账'),412),
    (OperationalError('commit', {}, RuntimeError('connection lost')),503),
])
def test_close_failure_rolls_back_without_claiming_result(api_client, monkeypatch, failure, status):
    client, db, _, _, settings = api_client
    settings.material_request_writes_enabled = True
    monkeypatch.setattr(service, 'close_material_request', Mock(side_effect=failure))
    response = client.post(f'/api/v1/material-requests/{REQUEST_ID}/close', json=PAYLOAD, headers=HEADERS)
    assert response.status_code == status, response.text
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_close_kill_switch_and_invalid_body_never_call_service(api_client, monkeypatch):
    client, db, _, _, settings = api_client
    settings.material_request_writes_enabled = False
    action = Mock(return_value=result())
    monkeypatch.setattr(service, 'close_material_request', action)
    response = client.post(f'/api/v1/material-requests/{REQUEST_ID}/close', json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 503
    settings.material_request_writes_enabled = True
    for payload in ({**PAYLOAD, 'expected_request_version': True}, {**PAYLOAD, 'reason':'  '}, {**PAYLOAD, 'force':True}):
        response=client.post(f'/api/v1/material-requests/{REQUEST_ID}/close', json=payload, headers=HEADERS)
        assert response.status_code==422
    action.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize('found', [True, False])
def test_recovery_is_read_only_with_writes_disabled_and_requires_fingerprint(api_client, monkeypatch, found):
    client, db, _, _, settings = api_client
    settings.material_request_writes_enabled = False
    action = Mock(return_value=result(replayed=True) if found else None)
    monkeypatch.setattr(service, 'closure_command_status', action)
    url=f'/api/v1/material-requests/{REQUEST_ID}/close-command-status'
    response=client.get(url,headers={**HEADERS,'X-Request-Fingerprint':'a'*64})
    assert response.status_code==200,response.text
    assert response.json()['lookup_status']==('confirmed' if found else 'not_observed')
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['request_fingerprint']=='a'*64
    db.commit.assert_not_called()
    action.reset_mock()
    assert client.get(url,headers=HEADERS).status_code==400
    action.assert_not_called()


def test_state_read_does_not_report_missing_or_broken_as_closed(api_client, monkeypatch):
    client, db, *_ = api_client
    monkeypatch.setattr(service,'read_closure',Mock(return_value=dict(request_id=REQUEST_ID,request_version=9,
        business_status='open',close_permitted=False,closure=None)))
    response=client.get(f'/api/v1/material-requests/{REQUEST_ID}/closure')
    assert response.status_code==200 and response.json()['business_status']=='open'
    assert 'no-store' in response.headers['cache-control']
    db.commit.assert_not_called()


def test_commit_failure_does_not_return_closure_success(api_client, monkeypatch):
    client,db,_,_,settings=api_client
    settings.material_request_writes_enabled=True
    monkeypatch.setattr(service,'close_material_request',Mock(return_value=result()))
    db.commit.side_effect=OperationalError('commit',{},RuntimeError('lost connection'))
    response=client.post(f'/api/v1/material-requests/{REQUEST_ID}/close',json=PAYLOAD,headers=HEADERS)
    assert response.status_code==503
    assert 'closure_id' not in response.json()
    db.rollback.assert_called_once()


def test_recovery_missing_secret_fails_closed(api_client, monkeypatch):
    client,db,_,_,settings=api_client
    settings.material_request_idempotency_hmac_secret=''
    action=Mock();monkeypatch.setattr(service,'closure_command_status',action)
    response=client.get(f'/api/v1/material-requests/{REQUEST_ID}/close-command-status',
        headers={**HEADERS,'X-Request-Fingerprint':'a'*64})
    assert response.status_code==503,response.text
    action.assert_not_called()
    db.commit.assert_not_called()
