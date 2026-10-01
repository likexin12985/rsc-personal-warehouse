"""Inventory recovery must not consume authentic login correlations."""
from datetime import datetime, timezone
import hashlib
from uuid import uuid4
import pytest
from app.foundation_models import StateTransitionEvent
from app.formal_services.authentication_audit import add_authentication_state_transition, AUTHENTICATION_AGGREGATE_TYPES
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_correction_sealed_inverse import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, seal, lookup, stock_facts
from app.formal_services.stock_loss_corrections import inverse_recovery
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)

def test_authentication_digest_collision_does_not_invalidate_permanent_inventory_seal(db, recoverable):
    w = recoverable
    raw = uuid4().hex
    w.command = w.command.model_copy(update={'request_id': 'authreq-' + hashlib.sha256(raw.encode()).hexdigest()})
    before = stock_facts(db)
    for aggregate in sorted(AUTHENTICATION_AGGREGATE_TYPES):
        add_authentication_state_transition(db, aggregate_type=aggregate, aggregate_id=str(uuid4()), actor_user_id=w.actor.user_id, from_status=None, to_status='active', reason_code='synthetic_scope', request_id=raw, occurred_at=datetime.now(timezone.utc))
    db.commit()
    assert inverse_recovery.lookup_original_inverse(db, actor=w.actor, request=w.command)['request_state'] == 'not_found'
    answer = seal(db, w)
    db.commit()
    add_authentication_state_transition(db, aggregate_type='auth_session', aggregate_id=str(uuid4()), actor_user_id=w.actor.user_id, from_status=None, to_status='active', reason_code='synthetic_late_scope', request_id=raw, occurred_at=datetime.now(timezone.utc))
    db.commit()
    assert lookup(db, w) == answer and stock_facts(db) == before

@pytest.mark.parametrize('mode', ['wrong_operation', 'inventory_reference', 'wrong_aggregate'])
def test_claimed_authentication_cannot_hide_possible_inventory_write(db, recoverable, mode):
    w = recoverable
    w.command = w.command.model_copy(update={'request_id': 'authreq-' + 'a' * 64})
    body = dict(operation='formal_authentication_state_transition', request_id=w.command.request_id)
    aggregate = 'auth_session'
    if mode == 'wrong_operation':
        body['operation'] = 'unknown'
    elif mode == 'inventory_reference':
        body['request_reference'] = 'unexpected-inventory-reference'
    else:
        aggregate = 'stock_loss_disposition_reversal'
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(uuid4()), from_status=None, to_status='active', actor_id=w.actor.user_id, reason='synthetic scope', idempotency_key=uuid4().hex, occurred_at=datetime.now(timezone.utc), metadata_jsonb=body))
    db.commit()
    before = stock_facts(db)
    with pytest.raises(InventoryReadError) as caught:
        seal(db, w)
    assert caught.value.code == 'loss_inverse_request_outcome_unknown' and stock_facts(db) == before
