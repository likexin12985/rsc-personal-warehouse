from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
from sqlalchemy.exc import OperationalError
from app.formal_services import material_request_return_compensation as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_formal_material_request_api import api_client, REQUEST_ID, REVISION_ID, LINE_ID

BASE = f'/api/v1/material-requests/{REQUEST_ID}/return-compensations'
HEADERS = {'Idempotency-Key': 'return-compensation-http-0001', 'X-Request-ID': 'return-compensation-http-trace'}
PAYLOAD = dict(expected_request_version=9, reason='退回已入账，不再补发', inbound_id=str(uuid4()),
    inbound_request_hash='a' * 64, inbound_plan_hash='b' * 64, cancelled_qty='1.000')


def result(replayed=False):
    return dict(schema_version='1.0', cancellation_scope='posted_return_compensation', compensation_id=str(uuid4()),
        inbound_id=PAYLOAD['inbound_id'], request_id=str(REQUEST_ID), revision_id=str(REVISION_ID),
        request_line_id=str(LINE_ID), request_version=9, cancelled_qty='1.000', request_hash='c' * 64,
        evidence_sha256='d' * 64, cancelled_at=datetime.now(timezone.utc).isoformat(), replayed=replayed)


def test_post_exact_source_commits_once(api_client, monkeypatch):
    client, db, principals, _, settings = api_client
    settings.material_request_writes_enabled = True
    action = Mock(return_value=result()); monkeypatch.setattr(service, 'cancel_returned_demand', action)
    response = client.post(BASE, headers=HEADERS, json=PAYLOAD)
    assert response.status_code == 201, response.text
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['actor'] is principals['value']
    assert action.call_args.kwargs['payload'].model_dump(mode='json') == PAYLOAD
    db.commit.assert_called_once()


@pytest.mark.parametrize('failure,status', [
    (MaterialRequestReadError('return_forbidden', 'forbidden', '无权限'), 403),
    (MaterialRequestReadError('source_changed', 'conflict', '来源已变'), 409),
    (OperationalError('commit', {}, RuntimeError('lost')), 503),
])
def test_failure_rolls_back_without_result(api_client, monkeypatch, failure, status):
    client, db, _, _, settings = api_client; settings.material_request_writes_enabled = True
    monkeypatch.setattr(service, 'cancel_returned_demand', Mock(side_effect=failure))
    response = client.post(BASE, headers=HEADERS, json=PAYLOAD)
    assert response.status_code == status, response.text
    db.rollback.assert_called_once(); db.commit.assert_not_called()


def test_closed_write_switch_and_extra_fields_never_submit(api_client, monkeypatch):
    client, db, _, _, settings = api_client
    action = Mock(); monkeypatch.setattr(service, 'cancel_returned_demand', action)
    settings.material_request_writes_enabled = False
    assert client.post(BASE, headers=HEADERS, json=PAYLOAD).status_code == 503
    settings.material_request_writes_enabled = True
    for payload in ({**PAYLOAD, 'force': True}, {**PAYLOAD, 'cancelled_qty': '0.000'},
                    {**PAYLOAD, 'expected_request_version': True}, {**PAYLOAD, 'expected_request_version': 0},
                    {**PAYLOAD, 'actor_id': str(uuid4())}):
        assert client.post(BASE, headers=HEADERS, json=payload).status_code == 422
    action.assert_not_called(); db.commit.assert_not_called()


@pytest.mark.parametrize('by_trace', [False, True])
@pytest.mark.parametrize('found', [False, True])
def test_recovery_readonly_with_write_switch_closed(api_client, monkeypatch, by_trace, found):
    client, db, _, _, settings = api_client; settings.material_request_writes_enabled = False
    action = Mock(return_value=result(True) if found else None); monkeypatch.setattr(service, 'command_status', action)
    headers = {'X-Request-Fingerprint': 'e' * 64}
    headers['X-Original-Request-ID' if by_trace else 'Idempotency-Key'] = HEADERS['X-Request-ID' if by_trace else 'Idempotency-Key']
    response = client.get(BASE + '/command-status', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['lookup_status'] == ('confirmed' if found else 'not_observed')
    assert 'no-store' in response.headers['cache-control']
    assert action.call_args.kwargs['request_fingerprint'] == 'e' * 64
    assert (action.call_args.kwargs['secret'] is None) == by_trace
    db.commit.assert_not_called()


def test_ambiguous_recovery_and_missing_fingerprint_refused(api_client, monkeypatch):
    client, db, *_ = api_client
    action = Mock(); monkeypatch.setattr(service, 'command_status', action)
    for headers, status in (({}, 422), ({'Idempotency-Key': HEADERS['Idempotency-Key']}, 400),
                    ({**HEADERS, 'X-Original-Request-ID': HEADERS['X-Request-ID'], 'X-Request-Fingerprint': 'a' * 64}, 422)):
        assert client.get(BASE + '/command-status', headers=headers).status_code == status
    action.assert_not_called(); db.commit.assert_not_called()


def test_candidate_failure_not_returned_as_empty_sources(api_client, monkeypatch):
    client, db, *_ = api_client
    monkeypatch.setattr(service, 'candidates', Mock(side_effect=MaterialRequestReadError('history_invalid', 'service_unavailable', '来源不完整')))
    response = client.get(BASE + '/candidates')
    assert response.status_code == 503 and 'items' not in response.json()
    assert 'no-store' in response.headers['cache-control']; db.commit.assert_not_called()


def test_technician_draft_candidates_are_hidden_before_history_lookup(tmp_path):
    """The trial MVP hides return compensation behind the operator boundary."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session
    from app.database import Base, get_db
    from app.demand_models import MaterialRequest
    from app.dependencies import get_formal_principal
    from app.routers import formal_material_requests
    from test_material_request_draft_service import _create, _create_id
    from test_material_request_query_service import _read_world

    engine = create_engine(f'sqlite+pysqlite:///{tmp_path / "draft-candidates.sqlite"}',
                           connect_args={'check_same_thread': False})
    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            world = _read_world(db)
            request_id = _create_id(world, 'draft-return-candidates')
            _create(db, world, request_id, key='draft-return-candidates')
            request = db.get(MaterialRequest, request_id)
            assert request.version == 0 and request.status == 'draft'
            db.commit()
            api = FastAPI()
            api.include_router(formal_material_requests.router, prefix='/api')
            api.dependency_overrides[get_db] = lambda: db
            api.dependency_overrides[get_formal_principal] = lambda: world.actor
            statements = []
            def capture(_conn, _cursor, sql, *_):
                statements.append(sql)
            event.listen(engine, 'before_cursor_execute', capture)
            try:
                with TestClient(api) as client:
                    response = client.get(f'/api/v1/material-requests/{request_id}/return-compensations/candidates')
            finally:
                event.remove(engine, 'before_cursor_execute', capture)
            assert response.status_code == 403, response.text
            assert response.json()['detail']['code'] == 'fulfillment_forbidden'
            assert 'no-store' in response.headers['cache-control']
            assert statements == []
    finally:
        engine.dispose()


def test_draft_candidate_schema_refuses_nonempty_or_invalid_version():
    from pydantic import ValidationError
    from app.material_request_return_compensation_schemas import ReturnCompensationCandidatesOut
    page = dict(request_id=REQUEST_ID, request_version=0, items=[])
    for version in (-1, True, '0', 0.5):
        with pytest.raises(ValidationError):
            ReturnCompensationCandidatesOut.model_validate({**page, 'request_version': version})
    candidate = dict(inbound_id=PAYLOAD['inbound_id'], request_line_id=LINE_ID,
        inbound_request_hash='a' * 64, inbound_plan_hash='b' * 64, quantity='1.000',
        sku_code='SYNTHETIC-SKU', material_name='synthetic return', posted_at=datetime.now(timezone.utc),
        compensate_permitted=False, compensation=None)
    with pytest.raises(ValidationError, match='draft cannot have posted'):
        ReturnCompensationCandidatesOut.model_validate({**page, 'items': [candidate]})
