import pytest
from sqlalchemy import select
from app.stock_operation_models import StockLossDisposition
from app.formal_services.stock_scrap import execution as service
from test_stock_scrap_correction_plan import (
    world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, command,
)
from test_stock_scrap_execution import execute_command, verify_posted
from stock_scrap_writer_fixture import db

pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True),
    pytest.mark.parametrize('ready', ['scrap'], indirect=True)]


def test_correction_scrap_posts_without_rewriting_original_root(db, ready, monkeypatch):
    root = db.get(StockLossDisposition, ready.w.root.id)
    original_row = select(root.__table__).where(root.__table__.c.id == root.id)
    before = dict(db.execute(original_row).mappings().one())
    request, preparation = execute_command(db, ready.w.actor, command(db, ready, monkeypatch))
    result = verify_posted(db, ready.w.actor, request, preparation)
    db.refresh(root)
    assert dict(db.execute(original_row).mappings().one()) == before
    assert result['root_disposition_id'] == str(root.id) and result['correction_execution_id'] is not None
