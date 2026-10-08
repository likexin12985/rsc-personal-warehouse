from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy.exc import OperationalError
from app.formal_services import material_request_rejection_return as registration
from app.formal_services import material_request_rejection_progress as progress
from app.formal_services import material_request_rejection_candidates as sources
from app.formal_services.material_request_query import MaterialRequestReadError
from test_formal_material_request_api import api_client, REQUEST_ID

BASE = f'/api/v1/material-requests/{REQUEST_ID}/rejection-returns'
RETURN_ID = str(uuid4())
HEADERS = {'Idempotency-Key': 'rejection-http-original-0001', 'X-Request-ID': 'rejection-http-trace-0001'}
NOW = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
REGISTRATION = dict(expected_request_version=9, reason='拒收后退回', receipt_id=str(uuid4()), receipt_line_id=str(uuid4()),
    receipt_request_hash='a' * 64, quantity='1.000', serial_ids=[])
PROGRESS = dict(expected_request_version=9, reason='实物已交出', action='depart', registration_request_hash='b' * 64,
    previous_event_id=None, previous_request_hash=None, physical_at=NOW, carrier=None, tracking_no=None)


def output(kind, replayed=False):
    common = dict(request_id=str(REQUEST_ID), request_version=9, return_id=RETURN_ID, request_hash='c' * 64, replayed=replayed)
    if kind == 'registration':
        return dict(common, schema_version='1.0', status='registered', return_no='RJR-HTTP', registered_at=NOW,
            **{k: REGISTRATION[k] for k in ('receipt_id', 'receipt_line_id', 'receipt_request_hash', 'quantity', 'serial_ids')})
    return dict(common, event_id=str(uuid4()), recorded_at=NOW,
        **{k: v for k, v in PROGRESS.items() if k != 'expected_request_version'})


@pytest.fixture(params=['registration', 'progress'])
def command(request):
    if request.param == 'registration':
        return request.param, BASE, registration, 'register_rejection_return', 'rejection_return_command_status', REGISTRATION
    return request.param, BASE + '/' + RETURN_ID + '/progress', progress, 'record_rejection_progress', 'rejection_progress_command_status', PROGRESS


def test_post_exact_input_once(api_client, monkeypatch, command):
    kind, path, service, action_name, _, payload = command
    client, db, principals, _, settings = api_client; settings.material_request_writes_enabled = True
    action = Mock(return_value=output(kind)); monkeypatch.setattr(service, action_name, action)
    response = client.post(path, headers=HEADERS, json=payload)
    assert response.status_code == 201, response.text
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['actor'] is principals['value']
    assert action.call_args.kwargs['payload'].model_dump(mode='json') == payload
    if kind == 'progress': assert str(action.call_args.kwargs['return_id']) == RETURN_ID
    db.commit.assert_called_once()


@pytest.mark.parametrize('error,status', [
    (MaterialRequestReadError('denied', 'forbidden', '无权限'), 403),
    (MaterialRequestReadError('changed', 'conflict', '来源变化'), 409),
    (OperationalError('commit', {}, RuntimeError('lost')), 503),
])
def test_post_failure_rolls_back(api_client, monkeypatch, command, error, status):
    _, path, service, name, _, payload = command
    client, db, _, _, settings = api_client; settings.material_request_writes_enabled = True
    monkeypatch.setattr(service, name, Mock(side_effect=error))
    response = client.post(path, headers=HEADERS, json=payload)
    assert response.status_code == status, response.text
    db.rollback.assert_called_once(); db.commit.assert_not_called()


def test_no_write_on_kill_switch_or_bad_input(api_client, monkeypatch, command):
    _, path, service, name, _, payload = command
    client, db, _, _, settings = api_client
    action = Mock(); monkeypatch.setattr(service, name, action)
    settings.material_request_writes_enabled = False
    assert client.post(path, headers=HEADERS, json=payload).status_code == 503
    settings.material_request_writes_enabled = True
    assert client.post(path, headers=HEADERS, json={**payload, 'force': True}).status_code == 422
    assert client.post(path, headers=HEADERS, json={**payload, 'expected_request_version': True}).status_code == 422
    assert client.post(path, json=payload).status_code == 400
    action.assert_not_called(); db.commit.assert_not_called()


@pytest.mark.parametrize('found', [False, True])
@pytest.mark.parametrize('by_trace', [False, True])
def test_read_recovery_no_write_permission_needed(api_client, monkeypatch, command, found, by_trace):
    kind, path, service, _, name, _ = command
    client, db, _, _, settings = api_client; settings.material_request_writes_enabled = False
    action = Mock(return_value=output(kind, True) if found else None); monkeypatch.setattr(service, name, action)
    headers = {'X-Request-Fingerprint': 'e' * 64,
        ('X-Original-Request-ID' if by_trace else 'Idempotency-Key'): HEADERS['X-Request-ID' if by_trace else 'Idempotency-Key']}
    response = client.get(path + '/command-status', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['lookup_status'] == ('confirmed' if found else 'not_observed')
    assert action.call_args.kwargs['request_fingerprint'] == 'e' * 64
    assert (action.call_args.kwargs['secret'] is None) == by_trace
    assert 'no-store' in response.headers['cache-control']; db.commit.assert_not_called()


def test_recovery_requires_one_coordinate_and_fingerprint(api_client, monkeypatch, command):
    _, path, service, _, name, _ = command
    client, db, *_ = api_client; action = Mock(); monkeypatch.setattr(service, name, action)
    for headers, status in (({}, 422), ({'Idempotency-Key': HEADERS['Idempotency-Key']}, 400),
        ({**HEADERS, 'X-Original-Request-ID': HEADERS['X-Request-ID'], 'X-Request-Fingerprint': 'a' * 64}, 422)):
        assert client.get(path + '/command-status', headers=headers).status_code == status
    action.assert_not_called(); db.commit.assert_not_called()


def test_source_failure_and_pagination_are_explicit(api_client, monkeypatch):
    client, db, *_ = api_client
    action = Mock(side_effect=MaterialRequestReadError('invalid', 'service_unavailable', '历史不完整'))
    monkeypatch.setattr(sources, 'candidates', action)
    response = client.get(BASE + '/candidates', params={'limit': 2, 'after_id': RETURN_ID})
    assert response.status_code == 503 and 'items' not in response.json()
    assert action.call_args.kwargs['limit'] == 2 and str(action.call_args.kwargs['after_id']) == RETURN_ID
    assert client.get(BASE + '/candidates?limit=21').status_code == 422
    db.commit.assert_not_called()
