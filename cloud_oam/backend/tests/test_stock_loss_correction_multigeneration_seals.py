from uuid import uuid4
import pytest
from sqlalchemy import select, text
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import sealed_inverse as service
from test_stock_loss_correction_sealed_inverse import stock_facts, snapshot
from test_stock_loss_correction_multigeneration import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, active, Correction, InventoryTransaction,
    ReversalPreview, ReversalExecute, reversal_stock,
    test_three_rounds_reverse_exact_predecessor_and_recover_all_original_requests as _build,
)
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)


def test_later_inverse_seal_is_permanent_stock_neutral_and_preserves_original_result(db, active, execution):
    _build(db, active, execution)
    db.execute(text('PRAGMA query_only=OFF'))
    w = active
    last = db.scalars(select(Correction).join(InventoryTransaction,
        InventoryTransaction.id == Correction.posting_transaction_id)
        .order_by(InventoryTransaction.ledger_cursor.desc())).first()
    preview = ReversalPreview(root_disposition_id=w.root.id,
        expected_root_request_hash=w.root.request_hash,
        expected_submission_plan_hash=w.order.plan_hash, reversed_correction_id=last.id,
        expected_execution_request_hash=last.request_hash, reason='核验后永久关闭结果未知的后继请求')
    checked = reversal_stock.prepare(db, actor=w.actor, request=preview)
    command = ReversalExecute(**preview.model_dump(), expected_plan_hash=checked.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    before_stock = stock_facts(db)
    assert service.lookup(db, actor=w.actor, request=command)['request_state'] == 'not_found'
    sealed = service.seal(db, actor=w.actor, request=command); db.commit()
    assert sealed['request_state'] == 'sealed' and sealed['retry_allowed'] is False
    assert sealed['seal']['stock_effect'] == 'none' and stock_facts(db) == before_stock
    assert command.idempotency_key not in str(sealed)
    before = snapshot(db)
    assert service.seal(db, actor=w.actor, request=command) == sealed
    db.commit(); assert snapshot(db) == before
    for changed in ({}, {'request_id': uuid4().hex}, {'idempotency_key': uuid4().hex}):
        with pytest.raises(InventoryReadError) as caught:
            service.execute(db, actor=w.actor, request=command.model_copy(update=changed))
        assert caught.value.code == 'loss_inverse_request_sealed'
        db.rollback(); assert snapshot(db) == before
    # New explicit coordinates can post while the previous request stays sealed.
    fresh = command.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex})
    posted = service.execute(db, actor=w.actor, request=fresh); db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert service.lookup(db, actor=w.actor, request=command) == sealed
    assert service.lookup(db, actor=w.actor, request=fresh)['result'] == posted
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
