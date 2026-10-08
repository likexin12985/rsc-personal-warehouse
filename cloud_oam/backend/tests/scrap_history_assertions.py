"""Read-only history checks, including well-hashed but false stock plans."""
from copy import deepcopy
from uuid import UUID
import pytest
from sqlalchemy import select, text
from app.foundation_models import OutboxEvent
from app.stock_operation_models import StockLossDisposition
from app.stock_loss_correction_models import StockLossCorrectionExecution
from app.formal_services.stock_loss_corrections.history_chain import verify_chain
from app.formal_services.stock_loss_corrections.chain_projection import InvalidChain
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_loss_sources as sources
from app.formal_services.stock_scrap import historical_facts
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_plan import snapshot


def history_snapshot(db):
    return snapshot(db), tuple((name, tuple(db.execute(text('SELECT * FROM ' + name + ' ORDER BY 1'))))
        for name in sorted(tables()) if name.startswith('stock_scrap_'))


def exercise(db, *, command, result, attack):
    root_id = UUID(result['root_disposition_id'])
    corrected = command.source.kind == 'correction'
    fact_id = UUID(result['correction_execution_id']) if corrected else root_id
    model = StockLossCorrectionExecution if corrected else StockLossDisposition
    fact = db.get(model, fact_id, populate_existing=True)
    if attack == 'missing_child_event':
        db.execute(OutboxEvent.__table__.delete().where(OutboxEvent.aggregate_type == 'stock_operation_scrap',
            OutboxEvent.aggregate_id == result['scrap_operation_id']))
        db.commit()
    elif attack in ('rehashed_balance', 'rehashed_ancestor'):
        document = deepcopy(fact.command_jsonb); plan = deepcopy(fact.plan_jsonb)
        if attack == 'rehashed_balance':
            plan['source_balance_quantity'] = '999.000'
        else:
            document['intent']['source']['expected_submission_plan_hash'] = 'f' * 64
            plan['intent'] = document['intent']
        plan_hash = sources._hash(plan)
        document['expected_plan_hash'] = plan_hash
        request_hash = sources._hash(document)
        common = dict(command_jsonb=document, request_hash=request_hash, plan_jsonb=plan, plan_hash=plan_hash)
        for name, identifier in ((model.__tablename__, fact_id), ('stock_operation_orders', UUID(result['scrap_operation_id']))):
            table = tables()[name]
            db.execute(table.update().where(table.c.id == identifier).values(**common))
        child = tables()['stock_scrap_lines']
        db.execute(child.update().where(child.c.id == UUID(result['scrap_line_id'])).values(plan_jsonb=plan, plan_hash=plan_hash))
        db.commit()
        fact = db.get(model, fact_id, populate_existing=True)
    before = history_snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    if attack == 'none':
        proof = verify_chain(db, root_disposition_id=root_id)
        assert root_id in proof.proved_roots
        if corrected:
            assert fact_id in {p.correction_execution_id for p in proof.correction_proofs}
    elif attack == 'missing_child_event':
        with pytest.raises((InvalidChain, InventoryReadError, ValueError)):
            verify_chain(db, root_disposition_id=root_id)
    else:
        # Isolate the plan/request proof from later event-hash checks. Native
        # tests must separately prohibit the deliberate UPDATE itself.
        inverses = frozenset({command.source.reversal_id}) if corrected else None
        with pytest.raises(InvalidChain) as error:
            historical_facts.verify(db, fact=fact, proved_inverse_ids=inverses)
        expected = 'stock_scrap_historical_request_invalid' if attack == 'rehashed_ancestor' else 'stock_scrap_historical_facts_invalid'
        assert str(error.value) == expected
    assert history_snapshot(db) == before and not db.new and not db.dirty and not db.deleted
