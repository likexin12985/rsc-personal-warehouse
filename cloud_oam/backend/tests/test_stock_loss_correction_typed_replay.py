"""Exact duplicate correction commands recover; changed commands conflict."""
from uuid import uuid4
import pytest
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_correction_execution import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, inverse_ready, ready, command, snapshot, approval_request
from app.formal_services.stock_loss_corrections import correction_approval
from app.formal_services.stock_loss_corrections import correction_execution
from app.formal_services.stock_loss_corrections import correction_recovery
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)

def assert_duplicate(db, actor, request, execute, result):
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        execute(db, actor=actor, request=request)
    assert (caught.value.code, caught.value.status_code) == ('loss_correction_request_requires_recovery', 409)
    db.rollback()
    assert snapshot(db) == before
    recovered = correction_recovery.lookup(db, actor=actor, request=request)
    assert recovered['request_state'] == 'found' and recovered['result'] == result and (recovered['retry_allowed'] is False)
    for update in ({'reason': 'altered original command'}, {'idempotency_key': uuid4().hex}):
        changed = type(request).model_validate(request.model_copy(update=update).model_dump())
        with pytest.raises(InventoryReadError) as caught:
            execute(db, actor=actor, request=changed)
        assert (caught.value.code, caught.value.status_code) == ('stock_loss_correction_request_conflict', 409)
        db.rollback()
        assert snapshot(db) == before

def test_approval_duplicate_is_exact_recovery_not_inverse_collision(db, inverse_ready):
    w = inverse_ready
    request = approval_request(w)
    result = correction_approval.approve(db, actor=w.actor, request=request)
    db.commit()
    assert_duplicate(db, w.actor, request, correction_approval.approve, result)

def test_execution_duplicate_is_exact_recovery_not_inverse_collision(db, ready):
    w = ready.w
    request = command(db, ready)
    result = correction_execution.execute(db, actor=w.actor, request=request)
    db.commit()
    assert_duplicate(db, w.actor, request, correction_execution.execute, result)
