"""Static harness checks, not substitutes for the isolated PostgreSQL gate."""
from __future__ import annotations

import ast
import inspect

import pytest

import test_postgresql16_release_gate as gate


def _history_source():
    return inspect.getsource(gate._assert_nonopening_multiround_count_status)


@pytest.mark.parametrize("service", (
    "recount_difference.generate_stocktake_recount_differences",
    "review.submit_stocktake_region_review",
    "review.submit_stocktake_headquarters_review",
    "posting.post_approved_stocktake_differences",
    "close.reconcile_posted_stocktake_for_close",
    "close.close_reconciled_stocktake",
))
def test_pg_history_terminal_chain_uses_formal_service_in_committed_write(service):
    tree = ast.parse(_history_source())
    services = {
        ast.unparse(node.args[0]) for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "write" and node.args
    }
    assert service in services
    write = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name == "write")
    assert "session.commit()" in ast.unparse(write)
    assert '"star_oam_api"' in ast.unparse(write) or "'star_oam_api'" in ast.unparse(write)
    assert not any(isinstance(node, ast.Assign) and any(
        isinstance(target, ast.Attribute) and target.attr in {"status", "released_at", "closed_at", "posted_at"}
        for target in node.targets) for node in ast.walk(tree))


def test_pg_history_rechecks_every_round_through_each_independent_terminal_stage():
    tree = ast.parse(_history_source())
    stages = [keyword.value.value for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "assert_all_history"
              for keyword in node.keywords if keyword.arg == "task_status"]
    assert stages == ["approved", "posted", "posted", "closed"]
    helper = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "assert_all_history")
    assert "in history" in ast.unparse(helper)
    assert "not_observed" in ast.unparse(helper)
    assert "assert durable_snapshot() == before" in _history_source()
    assert "assert inventory_snapshot(session) == inventory_after_post" in _history_source()


@pytest.mark.parametrize("model", (
    "StocktakeEffectiveApprovalCompletion", "StocktakeEffectiveApprovalScope",
    "StocktakeEffectiveApprovalItem", "StocktakePostingCompletion",
    "StocktakePostingCompletionItem", "StocktakeCloseReconciliationCompletion",
    "StocktakeCloseReconciliationAccount", "StocktakeCloseReconciliationSerial",
    "StocktakeCloseCompletion", "StocktakeCloseTransitionAck",
    "DocumentAttachment", "FileObject",
))
def test_pg_history_durable_snapshot_covers_terminal_and_attachment_full_rows(model):
    tree = ast.parse(_history_source())
    snapshot = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "durable_snapshot")
    assert model in {node.id for node in ast.walk(snapshot) if isinstance(node, ast.Name)}
    assert "_ordered_durable_model_rows" in ast.unparse(snapshot)


def test_pg_history_file_purpose_default_stays_request_and_stocktake_is_explicit():
    assert inspect.signature(gate._create_0046_available_request_file).parameters["purpose"].default == "request_attachment"
    preparation = inspect.getsource(gate._create_0046_available_request_file)
    assert 'f"formal-files/v1/{purpose}/"' in preparation
    assert "purpose=purpose" in preparation
    assert '"purpose": purpose' in preparation
    source = _history_source()
    assert 'purpose="stocktake_evidence"' in source
    assert "evidence_files[2 - round_no]" in source
    assert "_assert_0062_history_owner_boundary(" in source
    assert "assert durable_snapshot() == owner_before" in source


def test_pg_history_owner_probe_covers_real_acl_wait_and_ancestor_union():
    source = inspect.getsource(gate._assert_0062_history_owner_boundary)
    for required in (
        "psycopg.errors.InsufficientPrivilege", "pg_blocking_pids",
        "_wait_for_backend_lock", "FOR UPDATE NOWAIT", "psycopg.errors.LockNotAvailable",
        "inventory_ledger_heads", "stocktake_rounds", "stocktake_recount_cases",
        "stocktake_scope_count_completions", "stocktake_posting_completions",
        "stocktake_close_completions", '"files"',
    ):
        assert required in source
    driver = inspect.getsource(gate.test_postgresql16_migration_acl_concurrency_and_kill_gate)
    assert "_assert_0062_empty_history_owner_downgrade_and_reupgrade()" in driver
