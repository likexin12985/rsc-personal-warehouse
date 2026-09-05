"""Static harness checks, not substitutes for the isolated PostgreSQL gate."""
from __future__ import annotations

import ast
from copy import deepcopy
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


def _synthetic_0058_catalog(*, fixed=True, history_oid=4062):
    """Catalog-shaped test inputs only; no DB connection or fabricated PG pass."""
    migration = gate._load_nonopening_review_terminal_status_migration_0058()
    history = gate._load_nonopening_count_history_owner_migration_0062()
    revision = gate.HEAD_REVISION if fixed else migration.down_revision
    migrator_acl = "{star_oam_migrator=X/star_oam_migrator}"
    api_acl = "{star_oam_migrator=X/star_oam_migrator,star_oam_api=X/star_oam_migrator}"
    validator = (
        3032, "public", migration.REVIEW_GRAPH_FUNCTION, "", None, "trigger", "f",
        "plpgsql", "v", False, False, "u", False, migration.MIGRATION_ROLE,
        [migration.FIXED_SEARCH_PATH], migrator_acl,
        migration.FIXED_BODY_SHA256 if fixed else migration.LEGACY_BODY_SHA256,
        migration.FIXED_SOURCE_FRAGMENT if fixed else migration.LEGACY_SOURCE_FRAGMENT,
        3, False,
    )
    replay = (
        4057, "public", migration.DIFFERENCE_REPLAY_LOCK_FUNCTION, "uuid, uuid, text",
        "requested_task_id,requested_round_id,requested_actor_user_id", "void", "f",
        "plpgsql", "v", False, False, "u", True, migration.MIGRATION_ROLE,
        [migration.FIXED_SEARCH_PATH], api_acl, migration.DIFFERENCE_REPLAY_LOCK_BODY_SHA256,
        migration.UPSTREAM_REVIEW_LOCK_FUNCTION, 0, True,
    )
    readiness = (
        4044, "public", migration.RUNTIME_READY_FUNCTION, "", None, "boolean", "f",
        "sql", "s", False, False, "u", True, migration.MIGRATION_ROLE,
        ["search_path=pg_catalog"], migrator_acl,
        gate._head_runtime_ready_hash() if fixed else migration.RUNTIME_READY_BODY_SHA256_0057,
        revision, 0, True,
    )
    functions = {migration.REVIEW_GRAPH_SIGNATURE: validator,
                 migration.DIFFERENCE_REPLAY_LOCK_SIGNATURE: replay,
                 migration.RUNTIME_READY_SIGNATURE: readiness}
    if fixed:
        functions[history.LOCK_SIGNATURE] = (
            history_oid, "public", history.LOCK_FUNCTION, *replay[3:16],
            history.LOCK_BODY_SHA256, migration.UPSTREAM_REVIEW_LOCK_FUNCTION, 0, True,
        )
    callers = tuple((functions[f"{namespace}.{name}({arguments})"][0], namespace, name, arguments, count)
                    for namespace, name, arguments, count in gate._expected_0058_upstream_review_callers(revision))
    triggers = tuple((5000 + index, validator[0], "public", table, name, "A", 29,
                      6000 + index, True, True, 0, 0, 0, True, True, True, 0, "")
                     for index, (table, name) in enumerate(migration.REVIEW_GRAPH_TRIGGERS))
    return {"revision": revision, "functions": functions, "triggers": triggers,
            "upstream_callers": callers}


@pytest.mark.parametrize("revision", (
    gate.NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION,
    gate.NONOPENING_REVIEW_TERMINAL_STATUS_REVISION,
    gate.SUPPLY_TASK_CAUSALITY_REVISION, gate.SUPPLY_TASK_SECURITY_REVISION,
    gate.SUPPLY_TASK_EVENT_KEY_REVISION,
))
def test_0058_caller_matrix_before_0062_keeps_exact_old_signature(revision):
    assert gate._expected_0058_upstream_review_callers(revision) == ((
        "public", "rsc_lock_nonopening_stocktake_difference_replay_graph_0057", "uuid, uuid, text", 1,
    ),)


@pytest.mark.parametrize("fixed", (True, False))
def test_0058_catalog_accepts_only_revision_specific_legitimate_callers(fixed):
    state = _synthetic_0058_catalog(fixed=fixed)
    gate._assert_0058_review_terminal_catalog_state(state, fixed=fixed)
    assert len(state["upstream_callers"]) == (2 if fixed else 1)


@pytest.mark.parametrize("mutation", ("missing", "duplicate", "namespace", "overload", "count", "oid"))
def test_0058_catalog_rejects_missing_extra_or_changed_caller(mutation):
    state = _synthetic_0058_catalog()
    callers = list(state["upstream_callers"])
    if mutation == "missing":
        callers.pop(0)
    elif mutation == "duplicate":
        callers.append(callers[0])
    else:
        row = list(callers[0])
        index, value = {"namespace": (1, "other"), "overload": (3, "uuid, text"),
                        "count": (4, 2), "oid": (0, 99999)}[mutation]
        row[index] = value
        callers[0] = tuple(row)
    state["upstream_callers"] = tuple(callers)
    with pytest.raises(AssertionError):
        gate._assert_0058_review_terminal_catalog_state(state, fixed=True)


@pytest.mark.parametrize("index,value", (
    (12, False), (13, "star_oam_api"), (14, ["search_path=public"]),
    (15, "{=X/star_oam_migrator}"), (16, "0" * 64), (17, "no upstream call"),
))
def test_0058_new_caller_keeps_full_security_and_body_checks(index, value):
    state = _synthetic_0058_catalog()
    signature = gate._load_nonopening_count_history_owner_migration_0062().LOCK_SIGNATURE
    row = list(state["functions"][signature])
    row[index] = value
    state["functions"][signature] = tuple(row)
    with pytest.raises(AssertionError):
        gate._assert_0058_review_terminal_catalog_state(state, fixed=True)


def test_0058_roundtrip_accepts_only_new_0062_oid_and_preserves_old_oids():
    before, after = _synthetic_0058_catalog(), _synthetic_0058_catalog(history_oid=5062)
    gate._assert_0058_roundtrip_preserves_catalog_identity(before, after)
    with pytest.raises(AssertionError):
        gate._assert_0058_roundtrip_preserves_catalog_identity(before, deepcopy(before))
    for signature in before["functions"]:
        if signature == gate._load_nonopening_count_history_owner_migration_0062().LOCK_SIGNATURE:
            continue
        changed = deepcopy(after)
        old_row = changed["functions"][signature]
        changed["functions"][signature] = (old_row[0] + 999, *old_row[1:])
        # Even a coherently updated caller map cannot hide an old OID change.
        changed["upstream_callers"] = tuple(
            (row[0] + 999, *row[1:]) if row[0] == old_row[0] else row
            for row in changed["upstream_callers"])
        with pytest.raises(AssertionError):
            gate._assert_0058_roundtrip_preserves_catalog_identity(before, changed)


def test_0062_adds_no_caller_to_0054_0055_or_0056_legacy_dependency_sets():
    owner = gate._load_nonopening_count_history_owner_migration_0062()._postgresql_lock_function_sql()
    for forbidden in (
        gate._load_opening_recount_source_history_migration_0054().ROUND_SUBMISSION_FUNCTION,
        gate._load_nonopening_start_audit_order_migration_0055().VALIDATOR_FUNCTION,
        gate._load_nonopening_count_guard_compatibility_migration_0056().HELPER_FUNCTION,
    ):
        assert forbidden not in owner
