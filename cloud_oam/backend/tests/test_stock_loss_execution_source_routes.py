"""Clients receive exact proven decision IDs and explicit current routes."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.stock_operation_models import StockLossHeadquartersDecision, StockLossHeadquartersReview
from app.formal_services import stock_loss_execution_sources as sources
from test_stock_loss_disposition_recovery import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution,
)
from test_stock_loss_disposition_seals import snapshot
from test_stock_loss_source_routes import client, private, PATH


def url(db, w):
    decision = db.get(StockLossHeadquartersDecision, w.command.headquarters_decision_id)
    review = db.get(StockLossHeadquartersReview, decision.review_id)
    return PATH + '/execution-sources/' + str(review.operation_id)


def read(client, path, status=200):
    response = client.get(path)
    assert response.status_code == status, response.text
    private(response)
    assert 'idempotency_key' not in response.text and 'command_jsonb' not in response.text
    assert 'key_hash' not in response.text
    return response.json()


def test_exact_reference_then_original_posting_without_write_authority(
        db, allowed, execution, client):
    w = execution
    path = url(db, w)
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = read(client, path)
    assert result['person_id'] == str(w.actor.person_id)
    assert len(result['decisions']) == 1
    item = result['decisions'][0]
    assert item['headquarters_decision_id'] == str(w.command.headquarters_decision_id)
    assert item['preview_reference'] == {k: w.command.model_dump(mode='json')[k] for k in (
        'headquarters_decision_id', 'expected_headquarters_review_hash', 'expected_submission_plan_hash')}
    assert item['original_posting'] is None and snapshot(db) == before
    if w.flow == 'return':
        assert result['return_routes_status'] == 'available' and len(result['return_routes']) == 1
        r = result['return_routes'][0]
        assert r['target_location_id'] == str(w.command.target_location_id)
        assert r['transit_location_id'] == str(w.command.transit_location_id)
    else:
        assert result['return_routes_status'] == 'not_required' and not result['return_routes']
    db.execute(text('PRAGMA query_only=OFF'))
    posted = w.commit()
    allowed.world.current_principal = replace(w.actor,
        entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    item = read(client, path)['decisions'][0]
    assert item['original_posting'] == dict(result_scope='original_posting',
        **{k: posted[k] for k in ('disposition_id', 'executor_person_id', 'posting_transaction_id', 'quantity')},
        return_operation_id=posted.get('return_operation_id'))
    assert snapshot(db) == before and not db.new and not db.dirty


@pytest.mark.parametrize('execution', ['convert_used', 'return_to_region'], indirect=True)
@pytest.mark.parametrize('fault,status', [('read', 403), ('borrowed', 403), ('decision', 503), ('revoked_during_read', 403)])
def test_unproven_or_unauthorized_references_are_never_exposed(
        db, allowed, execution, client, monkeypatch, fault, status):
    w = execution
    path = url(db, w)
    db.commit()
    if fault == 'read':
        allowed.world.current_principal = replace(w.actor, entitlements=())
    elif fault == 'borrowed':
        allowed.world.current_principal = replace(w.actor, entitlements=tuple(
            replace(g, assignment_id=uuid4()) if g.action == 'read' else g for g in w.actor.entitlements))
    elif fault == 'decision':
        db.get(StockLossHeadquartersDecision, w.command.headquarters_decision_id).reason += ' forged'
        db.commit()
    else:
        original = sources._routes
        def revoke(*args, **kwargs):
            result = original(*args, **kwargs)
            allowed.world.current_principal = replace(w.actor, entitlements=())
            return result
        monkeypatch.setattr(sources, '_routes', revoke)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = read(client, path, status)
    assert 'decisions' not in result and snapshot(db) == before


@pytest.mark.parametrize('execution', ['return_to_region'], indirect=True)
@pytest.mark.parametrize('fault', ['transit_parent', 'receiver'])
def test_invalid_routes_stay_unavailable_without_guessed_substitutes(db, execution, route, client, fault):
    path = url(db, execution)
    if fault == 'transit_parent':
        route[1].parent_id = None
    else:
        route[0].custodian_person_id = None
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = read(client, path)
    assert result['return_routes_status'] == 'unavailable' and result['return_routes'] == []
    assert result['return_routes_reason'] and snapshot(db) == before
