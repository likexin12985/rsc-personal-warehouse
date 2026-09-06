from __future__ import annotations

from datetime import datetime, timezone
import inspect
from types import SimpleNamespace
import uuid

import app.formal_services.opening_observation_disposition as disposition_service
import app.formal_services.opening_stocktake as opening_service
import app.formal_services.opening_stocktake_count as count_service
import app.formal_services.opening_stocktake_finalize as finalize_service
import app.formal_services.opening_stocktake_recount as recount_service
import app.formal_services.opening_stocktake_review as review_service
import app.formal_services.stocktake_posting as posting_service
from app.formal_services.postgresql_lock_graph import (
    lock_inventory_reference_graph,
    lock_inventory_serial_graph,
    lock_material_request_work_order,
    lock_nonopening_stocktake_difference_replay_graph,
    lock_opening_control_import,
    lock_opening_stocktake_start_reference,
    lock_opening_stocktake_task_evidence,
    lock_opening_terminal_reference_union,
)


class _RecordingSession:
    def __init__(self, dialect_name: str) -> None:
        self._bind = SimpleNamespace(
            dialect=SimpleNamespace(name=dialect_name)
        )
        self.executed: list[tuple[str, dict[str, object]]] = []

    def get_bind(self) -> object:
        return self._bind

    def execute(
        self, statement: object, parameters: dict[str, object]
    ) -> None:
        self.executed.append((str(statement), parameters))


def test_sqlite_lock_graph_entrypoints_are_explicit_noops() -> None:
    db = _RecordingSession("sqlite")
    first = uuid.UUID("10000000-0000-4000-8000-000000000001")
    second = uuid.UUID("10000000-0000-4000-8000-000000000002")
    effective_at = datetime(2026, 8, 31, tzinfo=timezone.utc)

    lock_opening_control_import(db, first, second)
    lock_opening_stocktake_start_reference(
        db, first, (first,), (second,), (first,), effective_at
    )
    lock_opening_stocktake_task_evidence(db, first, second)
    lock_inventory_reference_graph(db, (first,), effective_at)
    lock_inventory_serial_graph(db, (second,))
    lock_opening_terminal_reference_union(
        db,
        (first,),
        (first,),
        (second,),
        (first,),
        (second,),
    )
    lock_material_request_work_order(db, first)

    assert db.executed == []


def test_postgresql_lock_graph_uses_only_fixed_migration_entrypoints() -> None:
    db = _RecordingSession("postgresql")
    first = uuid.UUID("10000000-0000-4000-8000-000000000001")
    second = uuid.UUID("10000000-0000-4000-8000-000000000002")
    owner = uuid.UUID("10000000-0000-4000-8000-000000000003")
    location = uuid.UUID("10000000-0000-4000-8000-000000000004")
    material = uuid.UUID("10000000-0000-4000-8000-000000000005")
    effective_at = datetime(2026, 8, 31, 8, 30, tzinfo=timezone.utc)

    lock_opening_control_import(db, first, second)
    lock_opening_stocktake_task_evidence(db, first, second)
    second_location = uuid.UUID("10000000-0000-4000-8000-000000000006")
    lock_opening_stocktake_start_reference(
        db,
        first,
        (owner, second, owner, owner),
        (location, first, location, second_location),
        (material, second, material),
        effective_at,
    )
    lock_inventory_reference_graph(
        db, (second, first, second), effective_at
    )
    lock_inventory_serial_graph(db, (second, first, second))
    lock_opening_terminal_reference_union(
        db,
        (second, first, second),
        (owner, second, owner, owner),
        (location, first, location, second_location),
        (material, second, material),
        (second, first, second),
    )
    lock_material_request_work_order(db, first)

    assert len(db.executed) == 7
    sql = "\n".join(statement for statement, _ in db.executed)
    assert "public.rsc_lock_opening_control_import_0027" in sql
    assert "public.rsc_lock_opening_stocktake_task_evidence_0027" in sql
    assert "public.rsc_lock_opening_stocktake_start_reference_0027" in sql
    assert "public.rsc_lock_inventory_reference_graph_0027" in sql
    assert "public.rsc_lock_inventory_serial_graph_0027" in sql
    assert "public.rsc_lock_opening_terminal_reference_union_0028" in sql
    assert "public.rsc_lock_material_request_work_order_reference_0042" in sql
    assert "pg_advisory" not in sql

    start_parameters = db.executed[2][1]
    reference_parameters = db.executed[3][1]
    serial_parameters = db.executed[4][1]
    terminal_union_parameters = db.executed[5][1]
    work_order_parameters = db.executed[6][1]
    assert start_parameters["region_org_id"] == str(first)
    assert start_parameters["owner_org_ids"] == [
        str(second),
        str(owner),
        str(owner),
    ]
    assert start_parameters["location_ids"] == [
        str(first),
        str(location),
        str(second_location),
    ]
    assert start_parameters["material_ids"] == [str(second), str(material)]
    assert start_parameters["effective_at"] is effective_at
    start_sql = db.executed[2][0]
    assert (
        "CAST(:region_org_id AS uuid), CAST(:owner_org_ids AS uuid[]), "
        "CAST(:location_ids AS uuid[]), CAST(:material_ids AS uuid[]), "
        "CAST(:effective_at AS timestamptz)"
    ) in start_sql
    expected_ids = [str(first), str(second)]
    assert reference_parameters["account_ids"] == expected_ids
    assert reference_parameters["effective_at"] is effective_at
    assert serial_parameters["serial_ids"] == expected_ids
    assert terminal_union_parameters == {
        "task_ids": expected_ids,
        "owner_org_ids": [str(second), str(owner), str(owner)],
        "location_ids": [str(first), str(location), str(second_location)],
        "material_ids": [str(second), str(material)],
        "account_ids": expected_ids,
    }
    assert (
        "CAST(:task_ids AS uuid[]), CAST(:owner_org_ids AS uuid[]), "
        "CAST(:location_ids AS uuid[]), CAST(:material_ids AS uuid[]), "
        "CAST(:account_ids AS uuid[])"
    ) in db.executed[5][0]
    assert work_order_parameters == {"work_order_id": str(first)}
    assert "CAST(:work_order_id AS uuid)" in db.executed[6][0]


def test_difference_replay_lock_is_postgresql_only_and_sqlite_noop() -> None:
    db = _RecordingSession("sqlite")

    lock_nonopening_stocktake_difference_replay_graph(
        db,
        uuid.UUID("10000000-0000-4000-8000-000000000001"),
        uuid.UUID("10000000-0000-4000-8000-000000000002"),
        "engineer-0057",
    )

    assert db.executed == []


def test_difference_replay_lock_forwards_exact_postgresql_signature() -> None:
    db = _RecordingSession("postgresql")
    task_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    round_id = uuid.UUID("10000000-0000-4000-8000-000000000002")
    actor_user_id = "engineer-0057"

    lock_nonopening_stocktake_difference_replay_graph(
        db,
        task_id,
        round_id,
        actor_user_id,
    )

    assert db.executed == [
        (
            "SELECT "
            "public.rsc_lock_nonopening_stocktake_difference_replay_graph_0057("
            "CAST(:task_id AS uuid), CAST(:round_id AS uuid), "
            "CAST(:actor_user_id AS text))",
            {
                "task_id": str(task_id),
                "round_id": str(round_id),
                "actor_user_id": actor_user_id,
            },
        )
    ]


def test_postgresql_start_reference_rejects_unpaired_scope_coordinates() -> None:
    db = _RecordingSession("postgresql")
    first = uuid.UUID("10000000-0000-4000-8000-000000000001")
    effective_at = datetime(2026, 8, 31, tzinfo=timezone.utc)

    try:
        lock_opening_stocktake_start_reference(
            db,
            first,
            (first,),
            (first, first),
            (),
            effective_at,
        )
    except ValueError as exc:
        assert "parallel pairs" in str(exc)
    else:
        raise AssertionError("unpaired scope arrays must fail closed")

    assert db.executed == []


def test_postgresql_terminal_union_rejects_unpaired_scope_coordinates() -> None:
    db = _RecordingSession("postgresql")
    first = uuid.UUID("10000000-0000-4000-8000-000000000001")

    try:
        lock_opening_terminal_reference_union(
            db,
            (first,),
            (first,),
            (first, first),
            (),
            (),
        )
    except ValueError as exc:
        assert "parallel pairs" in str(exc)
    else:
        raise AssertionError("unpaired terminal scope arrays must fail closed")

    assert db.executed == []


def test_terminal_batch_plan_takes_reference_union_before_serial_owner() -> None:
    plan_source = inspect.getsource(
        finalize_service._plan_opening_terminal_task_batch_graph
    )
    batch_source = inspect.getsource(
        finalize_service._lock_opening_terminal_task_batch_graph
    )

    assert plan_source.count("lock_opening_terminal_reference_union") == 1
    assert plan_source.index("lock_opening_stocktake_task_evidence") < (
        plan_source.index("lock_opening_terminal_reference_union")
    )
    assert plan_source.index("lock_opening_terminal_reference_union") < (
        plan_source.index("_capture_opening_reference_master_signatures")
    )
    assert batch_source.index("_plan_opening_terminal_task_batch_graph") < (
        batch_source.index("_lock_opening_serial_union_graph")
    )


def test_opening_start_keeps_import_and_reference_locks_before_audit_head() -> None:
    source = inspect.getsource(opening_service._start_opening_stocktake_impl)

    import_lock = source.index("lock_opening_control_import")
    ledger_lock = source.index("select(InventoryLedgerHead)")
    reference_lock = source.index("lock_opening_stocktake_start_reference")
    scope_read = source.index("_prepare_scopes")
    audit_lock = source.index("lock_audit_chain_head")
    first_freeze_guard = source.index(
        "_require_no_existing_scope_fact_or_freeze"
    )
    final_freeze_reread = source.index("lock_freezes=False")

    assert import_lock < ledger_lock < reference_lock < scope_read < audit_lock
    assert first_freeze_guard < audit_lock
    assert audit_lock < final_freeze_reread

    freeze_guard_source = inspect.getsource(
        opening_service._require_no_existing_scope_fact_or_freeze
    )
    assert "if lock_freezes:" in freeze_guard_source
    assert freeze_guard_source.count(".with_for_update(") == 1
    assert "of=InventoryFreeze" in freeze_guard_source


def test_opening_start_uses_serial_before_balance_and_plain_post_audit_rereads() -> None:
    snapshot_source = inspect.getsource(opening_service._prepare_snapshots)
    serial_helper = snapshot_source.index("lock_inventory_serial_graph")
    balance_lock = snapshot_source.index("balance_statement.with_for_update")
    assert serial_helper < balance_lock
    assert "expected_references" in snapshot_source
    assert "reference_signature != expected_references" in snapshot_source

    start_source = inspect.getsource(opening_service._start_opening_stocktake_impl)
    new_fact_path = start_source[
        start_source.index("# New inventory facts use one global row-lock order") :
    ]
    assert new_fact_path.index("lock_opening_control_import") < new_fact_path.index(
        "select(InventoryLedgerHead)"
    )
    assert new_fact_path.index("select(InventoryLedgerHead)") < new_fact_path.index(
        "lock_formal_principal_graph"
    )
    assert new_fact_path.index("lock_formal_principal_graph") < new_fact_path.index(
        "lock_opening_stocktake_start_reference"
    )
    post_audit = start_source[start_source.index("lock_audit_chain_head") :]
    assert ".with_for_update(" not in post_audit
    assert "lock_rows=False" in post_audit
    assert "expected_references=locked_snapshot_references" in post_audit
    assert "refreshed_control_manifest != control_manifest" in post_audit
    assert "scope_manifest != locked_scope_manifest" in post_audit


def test_opening_writers_use_plain_role_reproof_after_audit_head() -> None:
    count_source = inspect.getsource(
        count_service._submit_opening_stocktake_scope_count
    )
    count_post_audit = count_source[
        count_source.index("lock_audit_chain_head") :
    ]
    assert "_lock_current_assignment(" in count_post_audit
    assert "lock_rows=False" in count_post_audit
    assert "if lock_rows:" in inspect.getsource(
        count_service._lock_current_assignment
    )

    disposition_source = inspect.getsource(
        disposition_service._record_disposition
    )
    disposition_pre_audit, disposition_post_audit = disposition_source.split(
        "lock_audit_chain_head", maxsplit=1
    )
    assert "_authorize_disposition(" in disposition_pre_audit
    assert "lock_rows=False" not in disposition_pre_audit
    assert "_authorize_disposition(" in disposition_post_audit
    assert "_validate_scope_dimensions(" in disposition_post_audit
    assert disposition_post_audit.count("lock_rows=False") >= 2
    assert "if lock_rows:" in inspect.getsource(
        disposition_service._authorize_disposition
    )
    dimension_source = inspect.getsource(
        disposition_service._validate_scope_dimensions
    )
    assert "lock_rows and" in dimension_source
    assert "location_stmt.execution_options(populate_existing=True)" in (
        dimension_source
    )

    review_source = inspect.getsource(review_service._submit_review)
    review_pre_audit, review_post_audit = review_source.split(
        "lock_audit_chain_head", maxsplit=1
    )
    assert "_authorize_reviewer(" in review_pre_audit
    assert "lock_rows=False" not in review_pre_audit
    assert "_authorize_reviewer(" in review_post_audit
    assert "lock_rows=False" in review_post_audit
    assert "if lock_rows:" in inspect.getsource(
        review_service._authorize_reviewer
    )

    recount_source = inspect.getsource(recount_service._open_recount)
    recount_pre_audit, recount_post_audit = recount_source.split(
        "lock_audit_chain_head", maxsplit=1
    )
    assert "lock_formal_principal_graph" in recount_pre_audit
    assert "_authorize_opener(" in recount_post_audit
    assert "_authorize_scope_assignee(" in recount_post_audit
    assert recount_post_audit.count("lock_rows=False") >= 2
    selected_source = inspect.getsource(
        recount_service._lock_selected_assignment
    )
    assert "if lock_rows:" in selected_source
    assert "lock_rows=lock_rows" in inspect.getsource(
        recount_service._authorize_opener
    )
    assert "lock_rows=lock_rows" in inspect.getsource(
        recount_service._authorize_scope_assignee
    )

    close_source = inspect.getsource(
        finalize_service._close_posted_opening_stocktake
    )
    close_pre_audit, close_post_audit = close_source.split(
        "lock_audit_chain_head", maxsplit=1
    )
    assert "_authorize_finalizer(" in close_pre_audit
    assert "lock_rows=False" not in close_pre_audit
    assert "_authorize_finalizer(" in close_post_audit
    assert "lock_rows=False" in close_post_audit
    assert "if lock_rows:" in inspect.getsource(
        finalize_service._authorize_finalizer
    )


def test_nonopening_posting_reproves_authorization_after_batch_audit() -> None:
    source = inspect.getsource(posting_service._post_approved_stocktake_differences)
    batch_anchor = source.index("_post_prelocked_stocktake_inventory_batch(")
    audit_anchor = source.index("_verify_source_audits(", batch_anchor)
    tail_anchor = source.index("_reprove_posting_authorization(", audit_anchor)
    completion_anchor = source.index("_persist_posting_completion(", tail_anchor)
    assert audit_anchor < tail_anchor < completion_anchor

    helper_source = inspect.getsource(posting_service._reprove_posting_authorization)
    assert "_require_current_stocktake_difference_finalizer" in helper_source
    assert "lock_current_stocktake_finalizer_organization" in helper_source
    assert "_current_admin_assignment" in helper_source
    assert "stocktake_posting_authorization_changed" in helper_source
