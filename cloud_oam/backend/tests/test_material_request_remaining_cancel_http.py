from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError
from app.formal_services import material_request_remaining_cancel as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_remaining_cancel_schemas import MaterialRequestRemainingCancellationOut
from test_formal_material_request_api import api_client, REQUEST_ID, REVISION_ID, LINE_ID

HEADERS = {'Idempotency-Key': 'remaining-http-request-0001', 'X-Request-ID': 'remaining-http-trace-0001'}
PAYLOAD = {'expected_request_version': 9, 'reason': '剩余未履约数量不再需要',
           'lines': [{'request_line_id': str(LINE_ID), 'cancelled_qty': '1.000'}]}
BASE = f'/api/v1/material-requests/{REQUEST_ID}'


def result(*, replayed=False):
    return MaterialRequestRemainingCancellationOut(cancellation_id=uuid4(), request_id=REQUEST_ID,
        revision_id=REVISION_ID, request_version=9, cancelled_at=datetime.now(timezone.utc),
        evidence_sha256='a'*64, replayed=replayed, lines=PAYLOAD['lines'])


def test_cancel_commits_once_with_independent_fact(api_client, monkeypatch):
    client, db, principal, _, settings = api_client
    settings.material_request_writes_enabled = True
    action = Mock(return_value=result())
    monkeypatch.setattr(service, 'cancel_remaining_demand', action)
    response = client.post(BASE+'/cancel-remaining', json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 201, response.text
    assert response.json()['cancellation_scope'] == 'all_remaining_unfulfilled'
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['actor'] is principal['value']
    assert action.call_args.kwargs['_include_returns'] is True
    assert action.call_args.kwargs['payload'].model_dump(mode='json') == PAYLOAD
    db.commit.assert_called_once()


@pytest.mark.parametrize('failure,status', [
    (MaterialRequestReadError('remaining_forbidden','forbidden','无权限'),403),
    (MaterialRequestReadError('remaining_unsettled','precondition_failed','仍有在途'),412),
    (OperationalError('commit',{},RuntimeError('connection lost')),503),
])
def test_failure_rolls_back_and_never_claims_success(api_client, monkeypatch, failure, status):
    client, db, _, _, settings = api_client
    settings.material_request_writes_enabled = True
    monkeypatch.setattr(service,'cancel_remaining_demand',Mock(side_effect=failure))
    response=client.post(BASE+'/cancel-remaining',json=PAYLOAD,headers=HEADERS)
    assert response.status_code==status,response.text
    db.rollback.assert_called_once();db.commit.assert_not_called()


def test_disabled_write_and_invalid_input_never_call_service(api_client, monkeypatch):
    client, db, _, _, settings = api_client
    action=Mock(return_value=result());monkeypatch.setattr(service,'cancel_remaining_demand',action)
    settings.material_request_writes_enabled=False
    assert client.post(BASE+'/cancel-remaining',json=PAYLOAD,headers=HEADERS).status_code==503
    settings.material_request_writes_enabled=True
    for payload in ({**PAYLOAD,'expected_request_version':True}, {**PAYLOAD,'lines':[]},
                    {**PAYLOAD,'force':True}, {**PAYLOAD,'reason':' '}):
        assert client.post(BASE+'/cancel-remaining',json=payload,headers=HEADERS).status_code==422
    action.assert_not_called();db.commit.assert_not_called()


@pytest.mark.parametrize('found',[True,False])
def test_recovery_remains_read_only_with_writes_disabled(api_client, monkeypatch, found):
    client, db, _, _, settings=api_client
    settings.material_request_writes_enabled=False
    action=Mock(return_value=result(replayed=True) if found else None)
    monkeypatch.setattr(service,'remaining_cancellation_command_status',action)
    response=client.get(BASE+'/cancel-remaining-command-status',headers={**HEADERS,'X-Request-Fingerprint':'a'*64})
    assert response.status_code==200,response.text
    assert response.json()['lookup_status']==('confirmed' if found else 'not_observed')
    assert 'no-store' in response.headers['cache-control']
    db.commit.assert_not_called()
    action.reset_mock()
    assert client.get(BASE+'/cancel-remaining-command-status',headers=HEADERS).status_code==400
    action.assert_not_called()


def test_commit_failure_is_unknown_and_does_not_return_success(api_client, monkeypatch):
    client, db, _, _, settings=api_client
    settings.material_request_writes_enabled=True
    monkeypatch.setattr(service,'cancel_remaining_demand',Mock(return_value=result()))
    db.commit.side_effect=OperationalError('commit',{},RuntimeError('connection lost'))
    response=client.post(BASE+'/cancel-remaining',json=PAYLOAD,headers=HEADERS)
    assert response.status_code==503 and 'cancellation_id' not in response.json()
    db.rollback.assert_called_once()


def test_state_read_does_not_claim_cancellation_when_absent(api_client, monkeypatch):
    client, db, *_=api_client
    monkeypatch.setattr(service,'read_remaining_cancellation',Mock(return_value=dict(request_id=REQUEST_ID,
        request_version=9,cancel_permitted=False,cancellation=None)))
    response=client.get(BASE+'/remaining-cancellation')
    assert response.status_code==200 and response.json()['cancellation'] is None
    assert 'no-store' in response.headers['cache-control']
    db.commit.assert_not_called()
