"""Post-recovery corruption and immutable original-cursor reconstruction."""
from copy import deepcopy
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.foundation_models import OutboxEvent
from app.stock_loss_correction_models import StockLossDispositionReversal
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import recovery_history
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_recovery_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found, ready_to_restore,
    service, snapshot,
)


@pytest.mark.parametrize('attack', ['approval_binding', 'missing_child_event', 'rehashed_balance'])
def test_recovery_history_rejects_corrupt_approval_or_rehashed_stock_plan(db, ready_to_restore, monkeypatch, attack):
    w = ready_to_restore
    result = service.execute(db, actor=w.actor, request=w.command)
    db.commit()
    inverse_id = UUID(result['reversal_id'])
    table = tables()['stock_scrap_recovery_executions']
    recovery = db.execute(select(table).where(table.c.reversal_id == inverse_id)).mappings().one()
    if attack == 'approval_binding':
        db.execute(table.update().where(table.c.id == recovery['id']).values(expected_headquarters_hash='f'*64))
    elif attack == 'missing_child_event':
        db.execute(OutboxEvent.__table__.delete().where(OutboxEvent.aggregate_type == 'stock_scrap_recovery_execution',
            OutboxEvent.aggregate_id == str(recovery['id'])))
    else:
        plan = deepcopy(recovery['plan_jsonb']); command = deepcopy(recovery['command_jsonb'])
        plan['target_balance_quantity'] = '999.000'
        command['expected_plan_hash'] = sources._hash(plan)
        common = dict(plan_jsonb=plan, plan_hash=command['expected_plan_hash'], command_jsonb=command, request_hash=sources._hash(command))
        db.execute(table.update().where(table.c.id == recovery['id']).values(**common))
        inv = tables()['stock_loss_disposition_reversals']
        db.execute(inv.update().where(inv.c.id == inverse_id).values(**common))
    db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    if attack == 'rehashed_balance':
        # Isolate full historical stock-plan proof from the independent event
        # mismatch caused by this deliberately corrupted SQLite fixture.
        monkeypatch.setattr(recovery_history, 'verify_execution', lambda *a, **k: None)
        inverse = db.get(StockLossDispositionReversal, inverse_id, populate_existing=True)
        with pytest.raises(ValueError, match='stock_scrap_recovery_evidence_invalid'):
            recovery_history.verify_plan(db, inverse=inverse,
                proved_execution_ids=frozenset({UUID(result['original_execution_id'])}))
    else:
        with pytest.raises((ValueError, InventoryReadError)):
            verify_chain(db, root_disposition_id=UUID(result['root_disposition_id']))
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
