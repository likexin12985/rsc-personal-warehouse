"""New account admission uses the real command and rolls back with its facts."""
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.inventory_models import StockAccount
from app.formal_services import inventory_posting as posting
from app.formal_services import stock_return_inbound_commands as commands
from test_stock_return_inbound import (db, world, stock, recovered, destination,
    prepared, parcel, incoming, acceptance, execute, submit, snapshot)


def complete_snapshot(db):
    return snapshot(db), tuple(db.execute(text('SELECT * FROM stock_accounts ORDER BY id')))


def test_posting_failure_rolls_back_new_account_and_all_facts(db, acceptance, monkeypatch):
    receipt = execute(db, acceptance, submit(db, acceptance)); db.commit()
    before = complete_snapshot(db)
    checked = commands.preview_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id)
    target_id = UUID(checked['lines'][0]['target_account_id'])
    assert db.get(StockAccount, target_id) is None
    observed = []

    def fail_after_account(*args, **kwargs):
        observed.append(db.get(StockAccount, target_id) is not None)
        raise RuntimeError('synthetic posting failure after account creation')

    monkeypatch.setattr(posting, 'post_inventory_transaction', fail_after_account)
    with pytest.raises(RuntimeError, match='synthetic posting failure'), db.begin_nested():
        commands.execute_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id,
            expected_plan_hash=checked['plan_hash'], request_id=uuid4().hex, idempotency_key=uuid4().hex)
    db.expire_all()
    assert observed == [True]
    assert complete_snapshot(db) == before


def test_stale_preview_does_not_create_an_account(db, acceptance):
    receipt = execute(db, acceptance, submit(db, acceptance)); db.commit()
    before = complete_snapshot(db)
    from app.formal_services.inventory_query import InventoryReadError
    with pytest.raises(InventoryReadError) as failure:
        commands.execute_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id,
            expected_plan_hash='0' * 64, request_id=uuid4().hex, idempotency_key=uuid4().hex)
    assert failure.value.code == 'stock_return_inbound_plan_changed'
    assert not db.new and complete_snapshot(db) == before
