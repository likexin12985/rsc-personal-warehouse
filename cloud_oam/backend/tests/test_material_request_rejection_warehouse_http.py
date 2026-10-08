from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy.exc import OperationalError
from app.routers import formal_rejection_warehouse as routes
from test_formal_material_request_api import api_client

RETURN = str(uuid4()); RECEIPT = str(uuid4()); REQUEST = str(uuid4()); LOCATION = str(uuid4()); PERSON = str(uuid4())
BASE = '/api/v1/rejection-returns/' + RETURN
HEADERS = {'Idempotency-Key': 'rejection-warehouse-http-key-0001', 'X-Request-ID': 'rejection-warehouse-http-trace'}
NOW = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
ACCEPT = dict(expected_request_version=9, reason='仓库验收', registration_request_hash='a' * 64, handover_id=str(uuid4()),
    handover_request_hash='b' * 64, custody_assignment_id=str(uuid4()), received_at=NOW, observed_sku_code='SKU-1',
    amounts={'accepted_qty': '1.000', 'rejected_qty': '0.000', 'shortage_qty': '0.000', 'damaged_qty': '0.000',
        'accepted_serial_verifications': [], 'damaged_serial_ids': [], 'rejected_serial_ids': [], 'shortage_serial_ids': [], 'exceptions': []})
INBOUND = dict(expected_request_version=9, reason='仓库独立入账', receipt_request_hash='c' * 64, expected_plan_hash='d' * 64)


def receipt():
    return dict(receipt_id=RECEIPT, return_id=RETURN, request_id=REQUEST, request_version=9, receiver_person_id=PERSON,
        target_location_id=LOCATION, custody_assignment_id=ACCEPT['custody_assignment_id'], handover_id=ACCEPT['handover_id'],
        received_at=NOW, recorded_at=NOW, amounts=ACCEPT['amounts'], reason=ACCEPT['reason'], observed_sku_code='SKU-1', request_hash='c' * 64, replayed=False)


def posted():
    return dict(inbound_id=str(uuid4()), receipt_id=RECEIPT, return_id=RETURN, request_id=REQUEST, inventory_transaction_id=str(uuid4()),
        target_location_id=LOCATION, request_hash='e' * 64, plan_hash=INBOUND['expected_plan_hash'], replayed=False)


@pytest.fixture
def client(api_client):
    client, db, principals, _, settings = api_client
    client.app.include_router(routes.router, prefix='/api')
    principals['value'].permissions.add(('stock_operation', 'read', ''))
    return client, db, settings


@pytest.fixture(params=['receipt', 'inbound'])
def operation(request):
    if request.param == 'receipt': return BASE + '/warehouse-receipts', ACCEPT, routes.acceptance, 'record_rejection_receipt', 'rejection_receipt_command_status', receipt
    return BASE + '/warehouse-receipts/' + RECEIPT + '/inbounds', INBOUND, routes.inbound, 'post', 'command_status', posted


def test_new_post_validates_before_one_commit(client, operation, monkeypatch):
    client, db, settings = client; settings.material_request_writes_enabled = True
    path, payload, service, name, _, output = operation
    action = Mock(return_value=output()); monkeypatch.setattr(service, name, action)
    response = client.post(path, json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['payload'].model_dump(mode='json') == payload
    db.commit.assert_called_once()


@pytest.mark.parametrize('kind,status', [('denied', 403), ('changed', 409), ('database', 503), ('invalid_projection', 503)])
def test_failed_commands_roll_back(client, operation, monkeypatch, kind, status):
    client, db, settings = client; settings.material_request_writes_enabled = True
    path, payload, service, name, _, _ = operation
    error = routes.MaterialRequestReadError('denied', 'forbidden' if kind == 'denied' else 'conflict', '拒绝')
    if kind == 'database': error = OperationalError('commit', {}, RuntimeError('lost'))
    monkeypatch.setattr(service, name, Mock(return_value={'unexpected': True}) if kind == 'invalid_projection' else Mock(side_effect=error))
    response = client.post(path, json=payload, headers=HEADERS)
    assert response.status_code == status, response.text
    db.rollback.assert_called_once(); db.commit.assert_not_called()


def test_write_kill_switch_and_strict_input(client, operation, monkeypatch):
    client, db, settings = client
    path, payload, service, name, _, _ = operation; action = Mock(); monkeypatch.setattr(service, name, action)
    settings.material_request_writes_enabled = False
    assert client.post(path, json=payload, headers=HEADERS).status_code == 503
    settings.material_request_writes_enabled = True
    assert client.post(path, json={**payload, 'force': True}, headers=HEADERS).status_code == 422
    action.assert_not_called(); db.commit.assert_not_called()


@pytest.mark.parametrize('by_trace', [False, True])
@pytest.mark.parametrize('found', [False, True])
def test_recovery_with_writes_closed_is_exact_and_readonly(client, operation, monkeypatch, by_trace, found):
    client, db, settings = client; settings.material_request_writes_enabled = False
    path, _, service, _, name, output = operation
    action = Mock(return_value={**output(), 'replayed': True} if found else None); monkeypatch.setattr(service, name, action)
    headers = {'X-Request-Fingerprint': 'f' * 64, ('X-Original-Request-ID' if by_trace else 'Idempotency-Key'): HEADERS['X-Request-ID' if by_trace else 'Idempotency-Key']}
    response = client.get(path + '/command-status', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['lookup_status'] == ('confirmed' if found else 'not_observed')
    assert action.call_args.kwargs['request_fingerprint'] == 'f' * 64
    assert (action.call_args.kwargs['secret'] is None) == by_trace
    assert 'no-store' in response.headers['cache-control']; db.commit.assert_not_called()


def test_no_cache_on_framework_failures():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        for path in ('/api/v1/rejection-returns/my-warehouse?limit=21', BASE + '/warehouse', BASE + '/unknown'):
            response = client.get(path)
            assert response.status_code >= 400 and 'no-store' in response.headers['cache-control']
