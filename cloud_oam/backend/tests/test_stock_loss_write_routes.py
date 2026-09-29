"""Real loss services through HTTP, including both sides of an unknown COMMIT.

The current principal and file store are synthetic. PostgreSQL COMMIT/ACL
proofs are exercised separately by the owned-cluster release gate.
"""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.formal_services import stock_loss_commands as commands
from app.stock_operation_models import StockLossRequestSeal
from test_stock_loss_recovery import db, world, stock, allowed, readable, evidence, submission, lookup, snapshot
from test_stock_loss_seals import seal_input, stock_facts
from test_stock_loss_source_routes import client, private, PATH


def post(client, value, suffix=''):
    return client.post(PATH + suffix, json=value.model_dump(mode='json'), headers={
        'X-Request-ID': value.request_id, 'Idempotency-Key': value.idempotency_key})


def unavailable():
    return OperationalError('PRIVATE-SQL', {'key': 'PRIVATE-PARAMETER'}, Exception('PRIVATE-DB'))


def test_http_submission_commits_once_and_seal_returns_original(db, readable, evidence, client):
    value = submission(db, readable, evidence)
    db.commit()
    response = post(client, value)
    assert response.status_code == 200, response.text
    private(response)
    result = response.json()
    assert result['status'] == 'submitted' and result['posting_transaction_id']
    assert value.idempotency_key not in response.text and 'storage_key' not in response.text
    committed = snapshot(db)
    assert post(client, value).json() == result
    assert snapshot(db) == committed
    found = post(client, lookup(value), '/request-lookup')
    assert found.json()['submission'] == result and found.json()['retry_permitted'] is False
    sealed = post(client, seal_input(readable, value), '/request-seal')
    assert sealed.json() == found.json()
    private(sealed)
    assert snapshot(db) == committed
    assert not tuple(db.scalars(select(StockLossRequestSeal)))


def test_http_permanent_seal_never_posts_and_rejects_late_submission(db, readable, evidence, client):
    value = submission(db, readable, evidence)
    db.commit()
    before = stock_facts(db)
    response = post(client, seal_input(readable, value), '/request-seal')
    assert response.status_code == 200, response.text
    private(response)
    assert response.json()['lookup_status'] == 'sealed'
    assert response.json()['retry_permitted'] is False
    assert stock_facts(db) == before
    for change in ({}, {'request_id': uuid4().hex}, {'idempotency_key': uuid4().hex}):
        late = post(client, value.model_copy(update=change))
        assert late.status_code == 409, late.text
        assert late.json()['detail']['code'] == 'stock_loss_request_sealed'
    assert post(client, lookup(value), '/request-lookup').json() == response.json()
    assert post(client, seal_input(readable, value), '/request-seal').json() == response.json()
    assert stock_facts(db) == before
    assert len(tuple(db.scalars(select(StockLossRequestSeal)))) == 1


@pytest.mark.parametrize('action', ['submit', 'seal'])
@pytest.mark.parametrize('committed', [False, True])
def test_commit_error_is_unknown_and_exact_lookup_resolves_without_replaying(
    db, readable, evidence, client, monkeypatch, action, committed,
):
    value = submission(db, readable, evidence)
    db.commit()
    before = snapshot(db)
    commit = db.commit

    def lost_response():
        if committed:
            commit()
        raise unavailable()

    with monkeypatch.context() as patch:
        patch.setattr(db, 'commit', lost_response)
        response = post(client, value) if action == 'submit' else post(client, seal_input(readable, value), '/request-seal')
    assert response.status_code == 503, response.text
    private(response)
    assert response.json()['detail']['code'] == 'stock_loss_write_unconfirmed'
    assert 'PRIVATE-' not in response.text
    after = snapshot(db)
    found = post(client, lookup(value), '/request-lookup')
    assert found.status_code == 200, found.text
    assert found.json()['retry_permitted'] is False
    expected = ('found' if action == 'submit' else 'sealed') if committed else 'not_found'
    assert found.json()['lookup_status'] == expected
    assert snapshot(db) == after
    if not committed:
        assert after == before
        assert not tuple(db.scalars(select(StockLossRequestSeal)))


@pytest.mark.parametrize('action', ['submit', 'seal'])
@pytest.mark.parametrize('fault,status', [('operator', 403), ('permission', 403), ('trace', 400), ('key', 400)])
def test_write_boundary_refuses_wrong_identity_permissions_or_headers(
    db, readable, evidence, client, action, fault, status,
):
    value = submission(db, readable, evidence)
    payload = value if action == 'submit' else seal_input(readable, value)
    headers = {'X-Request-ID': value.request_id, 'Idempotency-Key': value.idempotency_key}
    if fault == 'operator':
        payload = payload.model_copy(update={'operator_person_id': uuid4()})
    elif fault == 'permission':
        readable.world.current_principal = replace(readable.actor, entitlements=tuple(
            p for p in readable.actor.entitlements if p.action != 'submit_loss'))
    else:
        headers['X-Request-ID' if fault == 'trace' else 'Idempotency-Key'] = uuid4().hex
    db.commit()
    before = snapshot(db)
    response = client.post(PATH + ('' if action == 'submit' else '/request-seal'),
        json=payload.model_dump(mode='json'), headers=headers)
    assert response.status_code == status, response.text
    private(response)
    assert snapshot(db) == before
    assert not tuple(db.scalars(select(StockLossRequestSeal)))


def test_http_failure_after_freeze_rolls_back_stock_files_and_notifications(db, readable, evidence, client, monkeypatch):
    value = submission(db, readable, evidence)
    db.commit()
    before = snapshot(db)
    record = commands._record

    def fail_after_facts(*args, **kwargs):
        record(*args, **kwargs)
        raise unavailable()

    monkeypatch.setattr(commands, '_record', fail_after_facts)
    response = post(client, value)
    assert response.status_code == 503, response.text
    assert 'PRIVATE-' not in response.text
    private(response)
    assert snapshot(db) == before
