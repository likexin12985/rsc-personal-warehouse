from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_overview_schemas import DIMENSIONS, MaterialRequestOverviewOut
from app.routers import formal_material_request_overview as route


@pytest.fixture
def harness(monkeypatch):
    app = FastAPI()
    app.include_router(route.router, prefix='/api')
    db, actor = Mock(), object()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_formal_principal] = lambda: actor
    output = MaterialRequestOverviewOut(observed_at=datetime(2026, 10, 6, tzinfo=timezone.utc),
        created_from=None, created_before=None, organization_id=None, matched_requests=0,
        counts={name: dict.fromkeys(values, 0) for name, values in DIMENSIONS.items()})
    service = Mock(return_value=output)
    monkeypatch.setattr(route, 'material_request_overview', service)
    with TestClient(app) as client:
        yield client, db, actor, service


def test_read_contract_and_transaction_release(harness):
    client, db, actor, service = harness
    response = client.get('/api/v1/reports/material-requests')
    assert response.status_code == 200
    assert response.json()['matched_requests'] == 0
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy'] == 'no-referrer'
    service.assert_called_once_with(db, actor=actor, organization_id=None, created_from=None, created_before=None)
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


@pytest.mark.parametrize('query', ['?created_from=2026-10-06T00:00:00', '?organization_id=bad'])
def test_bad_query_never_reaches_service(harness, query):
    client, _, _, service = harness
    assert client.get('/api/v1/reports/material-requests'+query).status_code == 422
    service.assert_not_called()


@pytest.mark.parametrize('error,code', [
    (MaterialRequestReadError('scope_changed', 'precondition_failed', '权限变化'), 412),
    (SQLAlchemyError('SECRET_DATABASE_DETAIL'), 503),
])
def test_service_failures_are_private_and_release_transaction(harness, error, code):
    client, db, _, service = harness
    service.side_effect = error
    response = client.get('/api/v1/reports/material-requests')
    assert response.status_code == code
    assert 'SECRET_DATABASE_DETAIL' not in response.text
    assert 'no-store' in response.headers['cache-control']
    db.rollback.assert_called_once()
    db.commit.assert_not_called()


def test_technician_report_access_is_rejected_before_service(harness):
    client, _, _, service = harness
    # The route fixture intentionally omits role_codes for legacy admin-like
    # cases; an explicit technician role must hit the capability fence first.
    from app.dependencies import get_formal_principal

    app = client.app
    technician = Mock(role_codes=('technician',))
    app.dependency_overrides[get_formal_principal] = lambda: technician
    service.reset_mock()
    response = client.get('/api/v1/reports/material-requests')
    assert response.status_code == 403
    assert response.json()['detail']['code'] == 'report_forbidden'
    assert response.headers['cache-control'] == 'private, no-store, max-age=0'
    service.assert_not_called()


@pytest.mark.parametrize('method,path,authenticated,status', [
    ('GET', '/api/v1/reports/material-requests', False, 401),
    ('GET', '/api/v1/reports/material-requests/missing', False, 404),
    ('POST', '/api/v1/reports/material-requests', False, 405),
    ('GET', '/api/v1/reports/material-requests?organization_id=invalid', True, 422),
    ('GET', '/api/v1/reports/material-requests?created_from=2026-10-06', True, 422),
])
def test_main_app_framework_failures_remain_private(monkeypatch, method, path, authenticated, status):
    from app.main import app

    service = Mock(side_effect=AssertionError('invalid request reached report service'))
    monkeypatch.setattr(route, 'material_request_overview', service)
    monkeypatch.setattr(app, 'dependency_overrides', {
        **app.dependency_overrides,
        get_db: lambda: Mock(),
        **({get_formal_principal: lambda: object()} if authenticated else {}),
    })
    with TestClient(app) as client:
        response = client.request(method, path)
    assert response.status_code == status
    assert response.headers['cache-control'] == 'private, no-store, max-age=0'
    assert response.headers['pragma'] == 'no-cache'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    service.assert_not_called()
