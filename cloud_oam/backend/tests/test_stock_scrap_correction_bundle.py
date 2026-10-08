from uuid import UUID

from app.formal_services import stock_scrap_plan
from app.formal_services.stock_scrap.execution_bundle import build
from test_stock_scrap_execution_bundle import execution as make_execution, check_bundle
from test_stock_scrap_correction_plan import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, snapshot, command, pytestmark,
)


def test_corrected_bundle_retains_root_and_binds_only_the_new_execution(db, ready, monkeypatch):
    preview = command(db, ready, monkeypatch)
    prepared = stock_scrap_plan.prepare(db, actor=ready.w.actor, request=preview)
    before = snapshot(db)
    bundle = build(actor=ready.w.actor, request=make_execution(preview, prepared), preparation=prepared)
    rows = check_bundle(bundle, prepared=prepared, corrected=True)
    assert 'stock_loss_dispositions' not in rows, 'never rewrite the original root'
    line = rows['stock_scrap_lines'][0]
    correction = rows['stock_loss_correction_executions'][0]
    assert line['root_disposition_id'] == correction['root_disposition_id'] == ready.w.root.id
    assert line['correction_execution_id'] == correction['id']
    assert line['predecessor_reversal_id'] == correction['reversal_id'] == ready.w.inverse.id
    assert line['correction_decision_id'] == correction['correction_decision_id'] == ready.decision.id
    assert line['original_decision_id'] is None
    assert rows['stock_operation_orders'][0]['loss_correction_decision_id'] == ready.decision.id
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted
