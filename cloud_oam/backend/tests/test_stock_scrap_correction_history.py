import pytest
from app.formal_services.stock_scrap.execution import execute
from test_stock_scrap_correction_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, command, execute_command,
)
from scrap_history_assertions import exercise

pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True),
    pytest.mark.parametrize('ready', ['scrap'], indirect=True)]


@pytest.mark.parametrize('attack', ['none', 'missing_child_event', 'rehashed_balance', 'rehashed_ancestor'])
def test_corrected_scrap_history_requires_full_cursor_bound_proof(db, ready, monkeypatch, attack):
    value, _ = execute_command(db, ready.w.actor, command(db, ready, monkeypatch))
    result = execute(db, actor=ready.w.actor, request=value)
    db.commit()
    exercise(db, command=value, result=result, attack=attack)
