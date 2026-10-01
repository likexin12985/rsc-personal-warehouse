"""Real posting through command HTTP, including uncertain COMMIT recovery."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.formal_services import stock_loss_disposition_commands, stock_loss_return_commands
from app.stock_loss_schemas import StockLossDispositionPreviewIn
from app.stock_loss_return_schemas import StockLossReturnPreviewIn
from test_stock_loss_disposition_recovery import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution,
)
from test_stock_loss_disposition_seals import snapshot
from test_stock_loss_source_routes import client, private, PATH


def path(w):
    return PATH + ('/dispositions' if w.flow == 'disposition' else '/derived-returns')


def body(w):
    return w.command.model_dump(mode='json')


def sealed_body(w):
    return dict(operator_person_id=str(w.actor.person_id), original=body(w))


def safe(response, w, status):
    assert response.status_code == status, response.text
    private(response)
    assert w.command.idempotency_key not in response.text
    assert 'command_jsonb' not in response.text and 'key_hash' not in response.text
    assert 'PRIVATE-' not in response.text
    return response.json()


def test_preview_execute_replay_recover_and_seal_existing_fact(db, execution, client):
    w = execution
    db.commit()
    preview_schema = StockLossDispositionPreviewIn if w.flow == 'disposition' else StockLossReturnPreviewIn
    request = {k: body(w)[k] for k in preview_schema.model_fields}
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    prepared = safe(client.post(path(w) + '/preview', json=request), w, 200)
    assert prepared['plan_hash'] == w.command.expected_plan_hash
    assert prepared['planning_status'] == 'preview_only' and prepared['stock_effect'] == 'none'
    assert 'holds' not in prepared and 'authorization_version' not in prepared
    assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    posted = safe(client.post(path(w), json=body(w)), w, 200)
    after = snapshot(db)
    assert after != before
    assert safe(client.post(path(w), json=body(w)), w, 200) == posted
    assert snapshot(db) == after
    found = safe(client.post(path(w) + '/request-lookup', json=body(w)), w, 200)
    assert found['disposition'] == posted and found['retry_permitted'] is False
    retained = safe(client.post(path(w) + '/request-seal', json=sealed_body(w)), w, 200)
    assert retained == found and snapshot(db) == after
    if w.flow == 'return':
        assert posted['return_fulfillment_required'] is True
        assert posted['stock_effect'] == 'frozen_to_return_pending'


def test_permanent_seal_rejects_late_http_write_without_stock_change(db, execution, client):
    w = execution
    db.commit()
    sealed = safe(client.post(path(w) + '/request-seal', json=sealed_body(w)), w, 200)
    assert sealed['lookup_status'] == 'sealed' and sealed['seal']['stock_effect'] == 'none'
    after = snapshot(db)
    assert safe(client.post(path(w) + '/request-seal', json=sealed_body(w)), w, 200) == sealed
    safe(client.post(path(w), json=body(w)), w, 409)
    assert snapshot(db) == after
    assert safe(client.post(path(w) + '/request-lookup', json=body(w)), w, 200) == sealed


@pytest.mark.parametrize('execution', ['convert_used', 'return_to_region'], indirect=True)
@pytest.mark.parametrize('failure', ['before_commit', 'after_commit', 'invalid_output'])
def test_commit_failure_and_invalid_response_never_acknowledge_success(
        db, execution, client, monkeypatch, failure):
    w = execution
    db.commit()
    before = snapshot(db)
    commit = db.commit
    service = stock_loss_disposition_commands if w.flow == 'disposition' else stock_loss_return_commands
    method = 'execute_disposition' if w.flow == 'disposition' else 'execute_loss_return'
    original = getattr(service, method)
    attempts = []
    def execute(*args, **kwargs):
        attempts.append(1)
        answer = original(*args, **kwargs)
        if failure == 'invalid_output':
            return dict(answer, quantity='-1.000')
        return answer
    def interrupted_commit():
        if failure == 'after_commit':
            commit()
        raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-COMMIT'))
    with monkeypatch.context() as patch:
        patch.setattr(service, method, execute)
        if failure != 'invalid_output':
            patch.setattr(db, 'commit', interrupted_commit)
        response = client.post(path(w), json=body(w))
    assert safe(response, w, 503)['detail']['code'] == 'stock_loss_execution_unconfirmed'
    assert len(attempts) == 1
    after = snapshot(db)
    answer = safe(client.post(path(w) + '/request-lookup', json=body(w)), w, 200)
    assert answer['lookup_status'] == ('found' if failure == 'after_commit' else 'not_found')
    assert answer['retry_permitted'] is False
    assert (after != before) == (failure == 'after_commit')
    assert snapshot(db) == after


@pytest.mark.parametrize('execution', ['convert_used', 'return_to_region'], indirect=True)
@pytest.mark.parametrize('fault,status', [('permission', 403), ('header', 400), ('schema', 422), ('plan', 409)])
def test_rejected_command_cannot_mutate_stock(db, allowed, execution, client, fault, status):
    w = execution
    db.commit()
    request, headers = body(w), {}
    if fault == 'permission':
        allowed.world.current_principal = replace(w.actor,
            entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))
    elif fault == 'header':
        headers['Idempotency-Key'] = uuid4().hex
    elif fault == 'schema':
        request['quantity'] = '999.000'
    else:
        request['expected_plan_hash'] = 'f' * 64
    before = snapshot(db)
    safe(client.post(path(w), json=request, headers=headers), w, status)
    assert snapshot(db) == before
