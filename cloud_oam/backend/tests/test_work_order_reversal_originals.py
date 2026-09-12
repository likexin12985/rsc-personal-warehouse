"""Original selection groups paired stock facts and verifies completed inverses."""
from sqlalchemy import text, select
import pytest

from app.formal_services.work_order_reversal_originals import list_originals
from app.formal_services.work_order_reversal_write import execute_reversal
from app.formal_services import work_order_material as material
from app.demand_models import WorkOrderMaterialLine
from test_work_order_reversal_write import db, world, stock, operation, submission


def test_originals_read_only_then_mark_complete_reversal(db, stock):
    source = operation(db, stock)
    args = dict(actor=stock.actor, work_order_id=stock.orders[0].id)
    result = list_originals(db, **args)
    selected = next(row for row in result.items if row.original_operation_id == source.id)
    assert selected.original_type == 'consume' and selected.operation_count == 1 and selected.line_count == 1
    assert selected.reversal_id is None and selected.material_names == (stock.world.material.name,)
    posted = execute_reversal(db, **args, request=submission(db, stock, original=source)); db.commit()
    db.execute(text('PRAGMA query_only=ON'))
    rows = list_originals(db, **args).items
    assert next(row for row in rows if row.original_operation_id == source.id).reversal_id == posted.reversal_id
    assert all(row.original_type != 'reverse' for row in rows)
    assert not db.new and not db.dirty and not db.deleted


def test_pair_is_one_selectable_whole_and_corrupt_evidence_is_rejected(db, stock):
    from uuid import uuid4
    from app.formal_services.work_order_replacements import execute_replacement
    from test_work_order_replacement_preview import inputs
    args = dict(actor=stock.actor, work_order_id=stock.orders[0].id)
    parent = execute_replacement(db, **args, **inputs(stock), idempotency_key=uuid4().hex, request_id=uuid4().hex); db.commit()
    rows = list_originals(db, **args).items
    selected = [row for row in rows if row.original_replacement_id == parent.id]
    assert len(selected) == 1 and selected[0].operation_count == 2 and selected[0].line_count == 2
    assert not any(row.original_operation_id in (parent.consume_operation_id, parent.recover_operation_id) for row in rows)
    db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.consume_operation_id)).quantity += 1
    db.commit()
    with pytest.raises(material.InventoryPostingError): list_originals(db, **args)
