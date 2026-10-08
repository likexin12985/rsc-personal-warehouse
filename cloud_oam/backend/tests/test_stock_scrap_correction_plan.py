"""Scrap planning after real original execution, inverse and new approval."""
import pytest
from sqlalchemy import text

from app.stock_scrap_schemas import ScrapPreview
from app.formal_services import stock_scrap_plan as plan
from app.formal_services.inventory_query import InventoryReadError
from test_stock_scrap_plan import upload
from test_stock_loss_correction_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, snapshot,
)

pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True),
    pytest.mark.parametrize('ready', ['scrap'], indirect=True)]


def command(db, ready, monkeypatch):
    file = upload(db, ready.w.actor, monkeypatch)
    source = ready.preview.model_dump(exclude={'reason'})
    return ScrapPreview(source=dict(kind='correction', **source),
        execution_reason='按独立纠正批准执行报废', evidence_file_ids=(file.id,))


def test_correction_scrap_uses_exact_restored_share_without_new_posting(db, ready, monkeypatch):
    request = command(db, ready, monkeypatch)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    first = plan.prepare(db, actor=ready.w.actor, request=request)
    assert plan.prepare(db, actor=ready.w.actor, request=request).plan_hash == first.plan_hash
    value = first.document
    assert value['source_account_id'] == str(ready.w.root.source_account_id)
    assert value['decision_id'] == str(ready.decision.id)
    assert value['predecessor_reversal_id'] == str(ready.w.inverse.id)
    assert value['quantity'] == format(ready.w.root.quantity, '.3f')
    assert value['target_account_id'] is None and value['stock_effect'] == 'none'
    assert value['intent']['source']['kind'] == 'correction'
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('field', ['expected_root_request_hash', 'expected_reversal_hash',
    'expected_correction_decision_hash', 'expected_submission_plan_hash'])
def test_correction_cannot_substitute_any_ancestor(db, ready, monkeypatch, field):
    request = command(db, ready, monkeypatch)
    request = request.model_copy(update={'source': request.source.model_copy(update={field: 'f' * 64})})
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        plan.prepare(db, actor=ready.w.actor, request=request)
    assert snapshot(db) == before
