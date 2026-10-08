"""Full application registration, authentication and private error middleware.

No lifespan/database bootstrap: native PG startup and business transactions
have separate gates. Authentication failures must happen before database use.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.main import app
from test_stock_scrap_http_adapter import FLOWS, request, url

BASE = '/api/v1/stock-operations/loss-reports'
PATHS = [url(kind, mode) for kind in FLOWS for mode in ('write','lookup','seal')]
PATHS += [url(kind)+'/preview' for kind in ('original','correction','execute')]


@pytest.fixture
def client():
    before = dict(app.dependency_overrides)
    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError('unauthorized request accessed database: '+name)
    app.dependency_overrides[get_db] = lambda: NoDatabase()
    # No context manager: this suite checks mounted routes and middleware,
    # without running the independent startup/bootstrap path.
    client = TestClient(app, raise_server_exceptions=True)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(before)


def private(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'private, no-store, max-age=0'
    assert response.headers['pragma'] == 'no-cache'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert 'PRIVATE-' not in response.text


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('headers', [{}, {'Authorization':'Bearer PRIVATE-invalid-token'}])
def test_every_mounted_route_requires_authentication_before_database(client, path, headers):
    private(client.post(BASE+path,headers=headers,json={'idempotency_key':'PRIVATE-original-key'}),401)


def test_exact_mounted_methods_without_duplicate_shadow_routes():
    actual = [(r.path,tuple(sorted(r.methods))) for r in app.routes if r.path.startswith(BASE+'/scraps')]
    expected = [(BASE+path,('POST',)) for path in PATHS]
    expected += [(BASE+'/scraps/recovery/sources', ('GET',)),
        (BASE+'/scraps/recovery/sources/{scrap_line_id}', ('GET',))]
    assert len(actual)==23 and sorted(actual)==sorted(expected)


@pytest.mark.parametrize('kind', FLOWS)
def test_authenticated_private_validation_uses_route_handler_not_global_echo(client, kind):
    app.dependency_overrides[get_formal_principal] = lambda: SimpleNamespace(
        person_id=uuid4(),allows=lambda *a,**kw: True)
    body=request(kind)
    body['unexpected_private_evidence']='PRIVATE-evidence'
    response=client.post(BASE+url(kind),json=body)
    private(response,422)
    assert response.json()['detail']['code']=='stock_scrap_request_invalid'


@pytest.mark.parametrize('kind', FLOWS)
def test_authenticated_without_grant_is_refused_before_service(client, kind):
    app.dependency_overrides[get_formal_principal] = lambda: SimpleNamespace(
        person_id=uuid4(),allows=lambda *a,**kw: False)
    private(client.post(BASE+url(kind),json=request(kind)),403)


def test_unmatched_route_and_method_are_private(client):
    private(client.get(BASE+url('original')),405)
    private(client.post(BASE+'/scraps/unknown'),404)
