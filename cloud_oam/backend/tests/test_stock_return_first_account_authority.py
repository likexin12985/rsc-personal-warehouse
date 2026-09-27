"""Authorization changes between preview and first receipt posting fail closed."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.inventory_models import CustodyAssignment, StockAccount
from app.formal_services import inventory_posting as posting
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services.inventory_query import InventoryReadError
from test_stock_return_inbound_account_admission import (
    db, world, stock, recovered, destination, prepared, parcel, incoming,
    acceptance, execute, submit, complete_snapshot,
)


@pytest.mark.parametrize('change', ['read_only', 'out_of_scope', 'custody'])
def test_first_account_rechecks_authority_before_materialization(db, stock, acceptance, change):
    receipt = execute(db, acceptance, submit(db, acceptance)); db.commit()
    checked = commands.preview_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id)
    target_id = UUID(checked['lines'][0]['target_account_id'])
    assert db.get(StockAccount, target_id) is None
    actor = acceptance.actor
    if change == 'read_only':
        actor = replace(actor, entitlements=tuple(row for row in actor.entitlements if row.action != 'receive_return'))
    elif change == 'out_of_scope':
        actor = replace(actor, entitlements=tuple(replace(row, scope_type='organization', scope_id=str(uuid4()))
            for row in actor.entitlements))
    else:
        db.get(CustodyAssignment, UUID(checked['target_custody_assignment_id'])).valid_to = datetime.now(timezone.utc)
        db.commit()
    stock.world.current_principal = actor
    before = complete_snapshot(db)
    with pytest.raises((InventoryReadError, posting.InventoryPostingError)):
        commands.execute_return_inbound(db, actor=actor, receipt_id=receipt.receipt_id,
            expected_plan_hash=checked['plan_hash'], request_id=uuid4().hex, idempotency_key=uuid4().hex)
    assert not db.new and db.get(StockAccount, target_id) is None
    assert complete_snapshot(db) == before
