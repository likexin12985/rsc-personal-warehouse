"""Approval seals never approve, move stock, or permit replay of old requests."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.stock_operation_models import StockLossReviewRequestSeal as Seal
from app.stock_loss_review_seal_schemas import StockLossRegionalReviewSealIn, StockLossHeadquartersReviewSealIn
from app.formal_services import stock_loss_review_seals as seals
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_review_recovery import (db, world, stock, allowed, evidence, regional,
    headquarters, state, review_world, read)


@pytest.fixture
def candidate(request, review_world):
    w = review_world
    command = request.getfixturevalue(w.stage).request
    schema = StockLossRegionalReviewSealIn if w.stage == 'regional' else StockLossHeadquartersReviewSealIn
    return schema(operator_person_id=w.actor.person_id, original=command, request_hash=w.request.request_hash)


def snapshot(db):
    return state(db), tuple(db.execute(text('SELECT * FROM stock_loss_review_request_seals ORDER BY id')))


def create(db, w, candidate):
    return seals.seal_review_request(db, actor=w.actor, stage=w.stage, request=candidate)


def test_seal_repeat_read_and_late_approval_variants_are_stock_neutral(db, review_world, candidate):
    w = review_world
    from test_stock_loss_seals import stock_facts
    stock_before = stock_facts(db)
    result = create(db, w, candidate); db.commit()
    assert stock_facts(db) == stock_before
    assert result.lookup_status == 'sealed' and not result.retry_permitted and result.seal.stock_effect == 'none'
    before = snapshot(db)
    assert create(db, w, candidate) == result
    db.commit(); assert snapshot(db) == before
    command = w.service.verify_regional_loss if w.stage == 'regional' else w.service.approve_headquarters_loss
    for changed in ({}, {'request_id':uuid4().hex}, {'idempotency_key':uuid4().hex}):
        with pytest.raises(InventoryReadError) as error:
            command(db, actor=w.actor, request=candidate.original.model_copy(update=changed))
        assert error.value.code == 'stock_loss_review_request_sealed'
        db.rollback(); assert snapshot(db) == before
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, w) == result and snapshot(db) == before
    assert candidate.original.idempotency_key not in result.model_dump_json()


def test_committed_approval_is_recovered_instead_of_sealed(db, review_world, candidate):
    w = review_world; result = w.commit(); before = snapshot(db)
    assert create(db, w, candidate).review == result
    db.commit(); assert snapshot(db) == before
    assert not tuple(db.scalars(select(Seal)))


def test_write_revocation_prevents_seal_but_not_read_of_existing_seal(db, allowed, review_world, candidate):
    w = review_world; result = create(db, w, candidate); db.commit()
    allowed.world.current_principal = replace(w.actor, entitlements=tuple(g for g in w.actor.entitlements if g.action != w.service.ACTION))
    before = snapshot(db)
    assert read(db, w) == result
    with pytest.raises(InventoryReadError):create(db, w, candidate)
    db.rollback(); assert snapshot(db) == before


def test_failure_after_audit_rolls_back_seal_and_history(db, allowed, review_world, candidate, monkeypatch):
    w = review_world; db.commit(); before = snapshot(db)
    original = seals.append_audit_event
    def revoke(*args, **kwargs):
        original(*args, **kwargs)
        allowed.world.current_principal = replace(w.actor, entitlements=())
    monkeypatch.setattr(seals, 'append_audit_event', revoke)
    with pytest.raises(InventoryReadError):create(db, w, candidate)
    db.rollback(); assert snapshot(db) == before


def test_new_explicit_request_after_seal_does_not_destroy_old_recovery(db, review_world, candidate):
    w = review_world; result = create(db, w, candidate); db.commit()
    command = w.service.verify_regional_loss if w.stage == 'regional' else w.service.approve_headquarters_loss
    changed = candidate.original.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    new = command(db, actor=w.actor, request=changed); db.commit()
    assert new.request_id != result.seal.request_id
    before = snapshot(db)
    assert read(db, w) == result
    assert snapshot(db) == before
