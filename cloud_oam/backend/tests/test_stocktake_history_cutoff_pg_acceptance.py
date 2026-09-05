"""Bounded regression checks for the next PostgreSQL 16 history sample.

These checks intentionally do not fabricate an opening establishment or call
the disposable PostgreSQL runner locally.  The production gate invokes the
same owner-contract assertion after applying all migrations; the semantic
cutoff-replay cases remain covered by the isolated service world tests.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.formal_services import stocktake_count_command_status, stocktake_difference
from app.formal_services import stocktake_recount_count
from app.formal_services import stocktake_count


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0062_nonopening_count_history_owner_graph.py"
)


def test_cutoff_replay_two_scope_contract_seals_each_scope_cursor():
    source = inspect.getsource(stocktake_difference._replay_scope_expected_states)
    tree = ast.parse(source)
    assert "cutoff_cursor" in source
    assert "completion_by_scope" in source
    assert "count_cursors" in source
    assert "for scope in scopes" in source
    assert "completion_by_scope[scope.id].count_ledger_cursor" in source
    # A single task-wide cursor must never replace the per-scope seal.
    assert not any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "count_cursor" for target in node.targets)
        for node in ast.walk(tree)
    )


def test_cutoff_replay_accepts_movement_on_either_account_side():
    source = MIGRATION.read_text(encoding="utf-8")
    # The owner graph must include a replay movement when the scoped account
    # is either its source or destination, then lock both account rows.
    assert "movement.from_account_id = ANY(scoped_account_ids)" in source
    assert "movement.to_account_id = ANY(scoped_account_ids)" in source
    assert "UNION SELECT from_account_id FROM public.inventory_movements" in source
    assert "UNION SELECT to_account_id FROM public.inventory_movements" in source
    assert "FOR UPDATE OF account" in source


def test_cutoff_replay_includes_new_accounts_from_snapshot_and_count_evidence():
    source = MIGRATION.read_text(encoding="utf-8")
    # A post-cutoff account may be absent from the initial scope seed.  The
    # snapshot/count/difference/reconciliation unions must expand the owner
    # graph before any historical GET is allowed to return.
    for fragment in (
        "stocktake_snapshot_lines",
        "stocktake_count_lines",
        "stocktake_differences",
        "stocktake_close_reconciliation_accounts",
        "stocktake_close_reconciliation_serials",
    ):
        assert fragment in source


def test_count_and_recount_services_capture_server_sealed_cursor():
    initial = inspect.getsource(stocktake_count)
    recount = inspect.getsource(stocktake_recount_count)
    for source in (initial, recount):
        assert "_lock_current_ledger_cursor" in source
        assert "count_ledger_cursor=count_ledger_cursor" in source
        assert "count_ledger_cursor < task.cutoff_ledger_cursor" in source


def test_history_get_is_non_mutating_but_not_a_read_only_shortcut():
    source = inspect.getsource(stocktake_count_command_status.stocktake_count_command_status)
    assert "lock_nonopening_stocktake_count_history_graph" in source
    assert "_lock_current_ledger_cursor" in source
    assert "task.cutoff_ledger_cursor" in source
    assert "session.commit" not in source
    assert "READ ONLY" not in source.upper()


def test_pg_gate_reuses_the_same_cutoff_owner_contract():
    # Keep the disposable gate's bounded preflight and this focused suite in
    # lockstep; pytest's local settings fixture supplies an isolated URL.
    import test_postgresql16_release_gate as gate

    gate._assert_pg16_cutoff_replay_multiscope_owner_contract()
