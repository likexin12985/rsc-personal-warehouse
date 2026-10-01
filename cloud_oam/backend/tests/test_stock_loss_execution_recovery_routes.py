"""Registered recovery routes preserve original facts and cannot replay writes."""
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.dependencies import get_formal_principal
from app.formal_services import stock_loss_disposition_recovery as recovery
from app.formal_services import stock_loss_disposition_commands, stock_loss_return_commands
from app.formal_services.audit_chain import AuditChainStateError
from app.main import app
from test_stock_loss_disposition_recovery import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution,
)
from test_stock_loss_disposition_seals import seal, snapshot
from test_stock_loss_source_routes import client, private, PATH


def path(w):
    return PATH + ('/dispositions' if w.flow == 'disposition' else '/derived-returns') + '/request-lookup'


def assert_original(response, w, state):
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['lookup_status'] == state
    assert answer['retry_permitted'] is False
    assert answer['result_scope'] == 'original_command'
    assert w.command.idempotency_key not in response.text
    assert 'key_hash' not in response.text and 'command_jsonb' not in response.text
    private(response)
    return answer


def test_registered_read_handles_missing_then_committed_without_write_grant(
        db, allowed, execution, client, monkeypatch):
    w = execution
    db.commit()
    body = w.command.model_dump(mode='json')
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert_original(client.post(path(w), json=body), w, 'not_found')
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    posted = w.commit()
    allowed.world.current_principal = replace(w.actor,
        entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))

    def forbidden(*args, **kwargs):
        raise AssertionError('Recovery must not submit or commit a write')

    monkeypatch.setattr(stock_loss_disposition_commands, 'execute_disposition', forbidden)
    monkeypatch.setattr(stock_loss_return_commands, 'execute_loss_return', forbidden)
    monkeypatch.setattr(db, 'commit', forbidden)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    answer = assert_original(client.post(path(w), json=body), w, 'found')
    assert answer['disposition'] == posted
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted


def test_registered_read_preserves_sealed_outcome_without_inventory_effect(
        db, allowed, execution, client):
    w = execution
    expected = seal(db, w)
    db.commit()
    allowed.world.current_principal = replace(w.actor,
        entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    answer = assert_original(client.post(path(w), json=w.command.model_dump(mode='json')), w, 'sealed')
    assert {k: v for k, v in answer['seal'].items() if k != 'sealed_at'} == {
        k: v for k, v in expected['seal'].items() if k != 'sealed_at'}
    assert datetime.fromisoformat(answer['seal']['sealed_at'].replace('Z', '+00:00')) == (
        datetime.fromisoformat(expected['seal']['sealed_at']))
    assert answer['seal']['stock_effect'] == 'none'
    assert snapshot(db) == before


@pytest.mark.parametrize('execution', ['convert_used', 'return_to_region'], indirect=True)
@pytest.mark.parametrize('fault,status', [
    ('auth', 401), ('permission', 403), ('schema', 422),
    ('trace', 400), ('key', 400), ('database', 503), ('audit', 503),
])
def test_errors_preserve_privacy_and_do_not_mutate(
        db, allowed, execution, client, monkeypatch, fault, status):
    w = execution
    db.commit()
    body, headers = w.command.model_dump(mode='json'), {}
    if fault == 'auth':
        def denied():
            raise HTTPException(status_code=401, detail='需要登录')
        app.dependency_overrides[get_formal_principal] = denied
    elif fault == 'permission':
        allowed.world.current_principal = replace(w.actor, entitlements=())
    elif fault == 'schema':
        body['quantity'] = '999.000'
    elif fault in ('trace', 'key'):
        headers['X-Request-ID' if fault == 'trace' else 'Idempotency-Key'] = uuid4().hex
    else:
        def unavailable(*args, **kwargs):
            if fault == 'audit':
                raise AuditChainStateError('PRIVATE-AUDIT')
            raise OperationalError('PRIVATE-SQL', {'secret': 'PRIVATE-PARAMETER'}, Exception('PRIVATE-DB'))
        monkeypatch.setattr(recovery, 'lookup_disposition_request', unavailable)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    response = client.post(path(w), json=body, headers=headers)
    assert response.status_code == status, response.text
    assert 'PRIVATE-' not in response.text and w.command.idempotency_key not in response.text
    private(response)
    assert snapshot(db) == before


@pytest.mark.parametrize('execution', ['convert_used', 'return_to_region'], indirect=True)
@pytest.mark.parametrize('coordinate', ['request_id', 'idempotency_key'])
def test_conflicting_original_request_never_discloses_success(
        db, execution, client, coordinate):
    w = execution
    posted = w.commit()
    body = w.command.model_dump(mode='json')
    body[coordinate] = uuid4().hex
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    response = client.post(path(w), json=body)
    assert response.status_code == 409, response.text
    assert str(posted['disposition_id']) not in response.text
    assert w.command.idempotency_key not in response.text
    private(response)
    assert snapshot(db) == before
