import pytest
from app.formal_services.stock_scrap.execution import execute
from test_stock_scrap_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, request, execute_command,
)
from scrap_history_assertions import exercise


@pytest.mark.parametrize('attack', ['none', 'missing_child_event', 'rehashed_balance', 'rehashed_ancestor'])
def test_original_scrap_history_requires_full_cursor_bound_proof(db, approved, monkeypatch, attack):
    command, _ = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    result = execute(db, actor=approved.actor, request=command)
    db.commit()
    exercise(db, command=command, result=result, attack=attack)
