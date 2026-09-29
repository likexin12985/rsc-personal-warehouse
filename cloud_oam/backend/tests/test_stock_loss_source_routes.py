"""Production router and privacy boundary with a synthetic current principal."""
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import stock_loss_sources
from app.main import app
from test_stock_loss_sources import db, world, stock, allowed, request
from test_work_order_removed_registration import counts, inventory

PATH = '/api/v1/stock-operations/loss-reports'


@pytest.fixture
def client(db, allowed, monkeypatch):
    monkeypatch.setattr(app, 'dependency_overrides', {
        get_db: lambda: db, get_formal_principal: lambda: allowed.world.current_principal,
    })
    value = TestClient(app, raise_server_exceptions=False)
    yield value
    value.close()


def private(response):
    assert 'private' in response.headers['cache-control']
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_sources_and_preview_are_read_only_through_registered_routes(db, allowed, client):
    before = counts(db), inventory(db)
    db.execute(text('PRAGMA query_only=ON'))
    sources = client.get(PATH + '/sources')
    assert sources.status_code == 200
    assert sources.json()['items'][0]['stock_account_id'] == str(allowed.account.id)
    preview = client.post(PATH + '/source-preview', json=request(allowed).model_dump(mode='json'))
    assert preview.status_code == 200
    assert preview.json()['planning_status'] == 'source_selection_only'
    assert 'qr_code' not in preview.text
    private(sources)
    private(preview)
    assert (counts(db), inventory(db)) == before


@pytest.mark.parametrize('fault,status', [('auth', 401), ('permission', 403), ('operator', 403),
    ('quantity', 422), ('method', 405), ('route', 404), ('database', 503)])
def test_failures_have_private_responses_without_database_details(db, allowed, client, monkeypatch, fault, status):
    path, method, payload = PATH + '/source-preview', 'POST', request(allowed).model_dump(mode='json')
    if fault == 'auth':
        def denied():
            raise HTTPException(status_code=401, detail='需要登录')
        app.dependency_overrides[get_formal_principal] = denied
    elif fault == 'permission':
        from dataclasses import replace
        allowed.world.current_principal = replace(allowed.actor, entitlements=allowed.actor.entitlements[:-1])
    elif fault == 'operator':
        payload['operator_person_id'] = str(uuid4())
    elif fault == 'quantity':
        payload['lines'][0]['quantity'] = 0
    elif fault == 'method':
        method = 'DELETE'
    elif fault == 'route':
        path += '/missing'
    else:
        def unavailable(*args, **kwargs):
            raise OperationalError('PRIVATE-SQL', {'secret': 'PRIVATE-PARAMETER'}, Exception('PRIVATE-DB'))
        monkeypatch.setattr(stock_loss_sources, 'preview_selection', unavailable)
    response = client.request(method, path, json=payload)
    assert response.status_code == status, response.text
    private(response)
    assert 'PRIVATE-' not in response.text


def test_source_selection_is_not_a_complete_submission(db, allowed, client):
    before = counts(db), inventory(db)
    response = client.post(PATH, json=request(allowed).model_dump(mode='json'))
    assert response.status_code == 422
    private(response)
    assert (counts(db), inventory(db)) == before
