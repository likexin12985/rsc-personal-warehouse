from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.database_security import (
    DatabaseSecurityBoundaryError,
    EXPECTED_FORMAL_FILE_INDEXES,
    EXPECTED_FORMAL_FILE_TRIGGERS,
    EXPECTED_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINTS,
    EXPECTED_NONOPENING_STOCKTAKE_CLOSE_INDEXES,
    EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS,
    EXPECTED_AUDIT_HEAD_IDS,
    EXPECTED_AUDIT_TRIGGERS,
    EXPECTED_OPENING_TERMINAL_TRIGGERS,
    EXPECTED_OPENING_TERMINAL_INDEX,
    EXPECTED_RECONCILIATION_CONSTRAINTS,
    EXPECTED_RECONCILIATION_PARTIAL_INDEXES,
    EXPECTED_RECONCILIATION_TRIGGERS,
    EXPECTED_STOCKTAKE_RECOUNT_COLUMNS,
    EXPECTED_STOCKTAKE_RECOUNT_CONSTRAINTS,
    EXPECTED_STOCKTAKE_RECOUNT_INDEXES,
    EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS,
    EXPECTED_STOCKTAKE_SCOPE_TRIGGERS,
    EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS,
    POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019,
    POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019,
    OPENING_COMMIT_TRIGGER_NAMES,
    RUNTIME_DELETE_TABLES,
    RUNTIME_EXECUTE_FUNCTIONS,
    RUNTIME_FUNCTION_BODY_SHA256,
    RUNTIME_FUNCTION_SHAPES,
    FORMAL_FILE_INTERNAL_FUNCTIONS,
    FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256,
    FORMAL_FILE_INTERNAL_FUNCTION_SHAPES,
    RUNTIME_INSERT_TABLES,
    RUNTIME_READ_TABLES,
    RUNTIME_UPDATE_TABLES,
    RUNTIME_UPDATE_COLUMNS,
    _NONOPENING_STOCKTAKE_CLOSE_TRIGGER_SQL,
    _OPENING_TERMINAL_TRIGGER_SQL,
    _OPENING_TERMINAL_INDEX_SQL,
    _RECONCILIATION_CONSTRAINT_SQL,
    _RECONCILIATION_PARTIAL_INDEX_SQL,
    _RECONCILIATION_TRIGGER_SQL,
    _STOCKTAKE_RECOUNT_TRIGGER_SQL,
    _STOCKTAKE_SCOPE_TRIGGER_SQL,
    _STOCKTAKE_SENSITIVE_TRIGGER_SQL,
    _assert_production_database_evidence,
    _assert_audit_stream_schema,
    _assert_audit_trigger_guards,
    _assert_complete_audit_graph,
    _assert_fixed_audit_heads,
    _assert_formal_file_guards,
    _assert_nonopening_stocktake_close_guards,
    _assert_runtime_function_acl,
    _assert_runtime_column_acl,
    _assert_runtime_sequence_acl,
    _assert_runtime_table_acl,
    _assert_opening_terminal_triggers,
    _assert_opening_terminal_index,
    _assert_reconciliation_schema,
    _assert_reconciliation_triggers,
    _assert_stocktake_recount_schema,
    _assert_stocktake_scope_triggers,
)
from app.formal_services.audit_chain import calculate_audit_event_hash


ROOT = Path(__file__).resolve().parents[2]
ACL_MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0026_opening_control_reconciliation.py"
)
LOCK_GRAPH_MIGRATION_0027 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0027_postgresql_lock_graph.py"
)
LOCK_GRAPH_MIGRATION_0028 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0028_opening_terminal_reference_union_lock.py"
)
SAFE_POSTING_MIGRATION_0035 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0035_nonopening_stocktake_safe_posting.py"
)
FORMAL_FILE_MIGRATION_0036 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0036_formal_file_runtime_boundary.py"
)
NONOPENING_STOCKTAKE_CLOSE_MIGRATION_0038 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0038_nonopening_stocktake_close_reconciliation.py"
)
PERSONAL_LOCATION_MIGRATION_0019 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0019_technician_personal_stocktake_boundary.py"
)
PERSONAL_LOCATION_CONTINUITY_MIGRATION_0020 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0020_personal_stocktake_location_continuity.py"
)
ROUND_ASSIGNMENT_GUARDS_MIGRATION_0021 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0021_stocktake_round_assignment_guards.py"
)
STOCKTAKE_SCOPE_REGION_OWNER_MIGRATION_0025 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0025_stocktake_scope_region_owner_guard.py"
)
OPENING_TERMINAL_RUNTIME_MIGRATION_0022 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0022_opening_terminal_runtime_boundary.py"
)


def _valid_evidence() -> dict[str, object]:
    return {
        "role_name": "star_oam_api",
        "is_superuser": False,
        "can_create_database": False,
        "can_create_role": False,
        "can_replicate": False,
        "can_bypass_rls": False,
        "can_create_in_database": False,
        "can_create_temporary_tables": False,
        "has_database_grant_option": False,
        "database_owner": "star_oam_migrator",
        "can_use_schema": True,
        "can_create_in_schema": False,
        "has_schema_grant_option": False,
        "current_schema_name": "public",
        "current_schema_path": ["public"],
        "has_non_system_schema_control": False,
        "schema_owner": "star_oam_migrator",
        "audit_events_owner": "star_oam_migrator",
        "audit_heads_owner": "star_oam_migrator",
        "migration_role_exists": True,
        "has_any_role_membership": False,
        "has_any_role_members": False,
        "migration_role_is_superuser": False,
        "migration_role_can_create_database": False,
        "migration_role_can_create_role": False,
        "migration_role_can_replicate": False,
        "migration_role_can_bypass_rls": False,
        "migration_role_has_any_membership": False,
        "migration_role_has_any_members": False,
        "is_migration_role_member": False,
        "can_disable_replication_guards": False,
        "session_replication_role": "origin",
        "audit_can_select": True,
        "audit_can_insert": True,
        "audit_can_update": False,
        "audit_can_delete": False,
        "audit_can_truncate": False,
        "audit_can_control_trigger": False,
        "heads_can_select": True,
        "heads_can_update": False,
        "heads_can_insert": False,
        "heads_can_delete": False,
        "heads_can_truncate": False,
        "heads_can_control_trigger": False,
        "alembic_can_select": False,
        "alembic_can_insert": False,
        "alembic_can_update": False,
        "alembic_can_delete": False,
    }


def test_production_database_role_accepts_only_required_audit_privileges() -> None:
    _assert_production_database_evidence(
        _valid_evidence(),
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


def test_runtime_acl_verifier_matches_base_manifest_through_0038(
) -> None:
    tree = ast.parse(ACL_MIGRATION.read_text(encoding="utf-8"))
    values: dict[str, tuple[str, ...]] = {}
    update_columns: dict[str, tuple[str, ...]] | None = None
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "API_UPDATE_COLUMNS"
            and node.value is not None
        ):
            update_columns = ast.literal_eval(node.value)
            continue
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {
            "API_READ_TABLES",
            "API_INSERT_TABLES",
            "API_UPDATE_TABLES",
            "API_DELETE_TABLES",
        }:
            values[target.id] = ast.literal_eval(node.value)
    safe_posting_tables = {
        "stocktake_effective_approval_completions",
        "stocktake_effective_approval_items",
        "stocktake_effective_approval_scopes",
        "stocktake_posting_completion_items",
        "stocktake_posting_completions",
    }
    material_request_read_tables = {
        "approval_actions",
        "approval_external_registration_lines",
        "approval_external_registrations",
        "approval_instances",
        "approval_return_line_facts",
        "approval_route_step_defs",
        "approval_route_versions",
        "approval_step_candidates",
        "approval_step_line_decisions",
        "approval_steps",
        "material_request_cancellation_line_facts",
        "material_request_commands",
        "material_request_files",
        "material_request_lines",
        "material_request_revisions",
        "material_requests",
        "material_substitutions",
        "notification_events",
        "oam_work_orders",
        "substitution_decisions",
        "supply_tasks",
    }
    material_request_insert_tables = {
        "approval_actions",
        "approval_external_registration_lines",
        "approval_external_registrations",
        "approval_instances",
        "approval_return_line_facts",
        "approval_step_candidates",
        "approval_step_line_decisions",
        "approval_steps",
        "material_request_cancellation_line_facts",
        "material_request_commands",
        "material_request_files",
        "material_request_lines",
        "material_request_revisions",
        "material_requests",
    }
    stocktake_close_read_tables = {
        "stocktake_close_transition_acks",
        "stocktake_close_reconciliation_completions",
        "stocktake_close_reconciliation_accounts",
        "stocktake_close_reconciliation_serials",
        "stocktake_close_completions",
    }
    stocktake_close_insert_tables = stocktake_close_read_tables - {
        "stocktake_close_transition_acks"
    }
    assert RUNTIME_READ_TABLES - set(values["API_READ_TABLES"]) == (
        safe_posting_tables
        | {"document_attachments"}
        | material_request_read_tables
        | stocktake_close_read_tables
    )
    assert RUNTIME_INSERT_TABLES - set(values["API_INSERT_TABLES"]) == (
        safe_posting_tables
        | {"document_attachments", "files"}
        | material_request_insert_tables
        | stocktake_close_insert_tables
    )
    assert set(values["API_READ_TABLES"]) <= RUNTIME_READ_TABLES
    assert set(values["API_INSERT_TABLES"]) <= RUNTIME_INSERT_TABLES
    assert set(values["API_UPDATE_TABLES"]) == RUNTIME_UPDATE_TABLES
    assert RUNTIME_DELETE_TABLES - set(values["API_DELETE_TABLES"]) == {
        "material_request_files",
        "material_request_lines",
    }
    assert set(values["API_DELETE_TABLES"]) <= RUNTIME_DELETE_TABLES
    assert update_columns is not None
    base_update_columns = {
        table_name: set(column_names)
        for table_name, column_names in update_columns.items()
    }
    assert {
        table_name: set(column_names)
        for table_name, column_names in RUNTIME_UPDATE_COLUMNS.items()
        if table_name in base_update_columns
    } == base_update_columns
    assert {
        table_name: set(column_names)
        for table_name, column_names in RUNTIME_UPDATE_COLUMNS.items()
        if table_name not in base_update_columns
    } == {
        "files": {"status", "metadata_jsonb"},
        "approval_external_registrations": {
            "status",
            "verified_by_user_id",
            "verified_by_person_id",
            "verified_role_assignment_id",
            "verified_authorization_version",
            "verified_at",
            "verification_comment",
            "version",
            "updated_at",
        },
        "approval_instances": {
            "status",
            "current_step_no",
            "current_step_id",
            "completed_at",
            "version",
            "updated_at",
        },
        "approval_steps": {
            "status",
            "opened_at",
            "decided_at",
            "decision_manifest_sha256",
            "version",
            "updated_at",
        },
        "material_request_lines": {
            "status",
            "final_approved_qty",
            "cancelled_qty",
            "version",
            "updated_at",
        },
        "material_request_revisions": {
            "work_order_id",
            "purpose",
            "urgency",
            "expected_date",
            "address_snapshot_jsonb",
            "address_masked_jsonb",
            "contact_snapshot_jsonb",
            "contact_masked_jsonb",
            "note",
            "status",
            "content_manifest_sha256",
            "sealed_at",
            "sealed_by_user_id",
            "updated_at",
        },
        "material_requests": {
            "work_order_id",
            "purpose",
            "urgency",
            "expected_date",
            "address_snapshot_jsonb",
            "address_masked_jsonb",
            "contact_snapshot_jsonb",
            "contact_masked_jsonb",
            "note",
            "revision_no",
            "status",
            "submitted_at",
            "decided_at",
            "withdrawn_at",
            "cancelled_at",
            "version",
            "updated_at",
        },
    }


def test_0035_safe_posting_runtime_manifest_is_append_only_and_exact() -> None:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0035_security_manifest",
        SAFE_POSTING_MIGRATION_0035,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    expected_tables = {
        "stocktake_effective_approval_completions",
        "stocktake_effective_approval_scopes",
        "stocktake_effective_approval_items",
        "stocktake_posting_completions",
        "stocktake_posting_completion_items",
    }
    assert set(migration.NEW_TABLES) == expected_tables
    assert expected_tables <= RUNTIME_READ_TABLES
    assert expected_tables <= RUNTIME_INSERT_TABLES
    assert expected_tables.isdisjoint(RUNTIME_UPDATE_TABLES)
    assert expected_tables.isdisjoint(RUNTIME_DELETE_TABLES)

    coordinate = (
        "rsc_lock_nonopening_stocktake_posting_graph_0035",
        "uuid",
    )
    assert RUNTIME_EXECUTE_FUNCTIONS[coordinate] == (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    )
    assert RUNTIME_FUNCTION_SHAPES[coordinate] == ("f", "void", False)
    function_sql = migration._postgresql_lock_function_sql()
    function_body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(function_body.encode("utf-8")).hexdigest() == (
        RUNTIME_FUNCTION_BODY_SHA256[coordinate]
    )
    source = SAFE_POSTING_MIGRATION_0035.read_text(encoding="utf-8")
    assert "GRANT SELECT, INSERT ON TABLE" in source
    assert "GRANT UPDATE ON TABLE" not in source
    assert "GRANT DELETE ON TABLE" not in source


def test_0036_formal_file_runtime_manifest_and_function_bodies_are_exact() -> None:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0036_security_manifest",
        FORMAL_FILE_MIGRATION_0036,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "20260901_0035"
    assert migration.PG_TRIGGERS[
        "trg_material_request_files_formal_guard_0036"
    ] == ("material_request_files", "insert")
    assert not any(
        "material_request_files" in name and "truncate" in name
        for name in migration.PG_TRIGGERS
    )
    assert not any(
        "material_request_files_formal_update" in name
        or "material_request_files_formal_delete" in name
        for name in migration._sqlite_trigger_names()
    )
    assert {"files", "document_attachments"} <= RUNTIME_READ_TABLES
    assert {"files", "document_attachments"} <= RUNTIME_INSERT_TABLES
    assert "files" not in RUNTIME_UPDATE_TABLES
    assert RUNTIME_UPDATE_COLUMNS["files"] == {"status", "metadata_jsonb"}
    assert "document_attachments" not in RUNTIME_UPDATE_TABLES
    assert "document_attachments" not in RUNTIME_DELETE_TABLES
    for function_name, function_sql in (
        (migration.PG_FILE_FUNCTION, migration._postgresql_file_function_sql()),
        (
            migration.PG_BINDING_FUNCTION,
            migration._postgresql_binding_function_sql(),
        ),
    ):
        coordinate = (function_name, "")
        body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
        )
        assert FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate] == (
            "v",
            True,
            "plpgsql",
            ("search_path=pg_catalog, public",),
        )
        assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == (
            "f",
            "trigger",
            False,
        )
    source = FORMAL_FILE_MIGRATION_0036.read_text(encoding="utf-8")
    assert "GRANT UPDATE (status, metadata_jsonb) ON TABLE public.files" in source
    assert "GRANT SELECT, INSERT ON TABLE public.document_attachments" in source
    assert "GRANT SELECT, INSERT ON TABLE public.material_request_files" not in source
    assert "GRANT UPDATE ON TABLE public.files" not in source
    assert "GRANT DELETE" not in source
    static_external_guard = migration._postgresql_binding_file_sql(
        file_expression="NEW.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="NEW.registered_by_user_id",
        person_expression="NEW.registered_by_person_id",
        bound_at_expression="NEW.registered_at",
        require_current_identity=False,
    )
    dynamic_external_guard = migration._postgresql_binding_file_sql(
        file_expression="NEW.evidence_file_id",
        purpose="external_approval_evidence",
        user_expression="NEW.registered_by_user_id",
        person_expression="NEW.registered_by_person_id",
        bound_at_expression="NEW.registered_at",
        require_current_identity=True,
    )
    assert "uploader.account_status" not in static_external_guard
    assert "uploader.authorization_version" not in static_external_guard
    assert "uploader.account_status = 'active'" in dynamic_external_guard
    assert "uploader.authorization_version::text" in dynamic_external_guard


def _load_nonopening_stocktake_close_migration_0038() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0038_security_manifest",
        NONOPENING_STOCKTAKE_CLOSE_MIGRATION_0038,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _valid_nonopening_stocktake_close_trigger_rows() -> list[dict[str, object]]:
    return [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": enabled,
            "trigger_type": trigger_type,
            "is_constraint_trigger": is_constraint,
            "is_deferrable": is_deferrable,
            "is_initially_deferred": is_initially_deferred,
            "has_when_clause": False,
            "has_column_filter": has_column_filter,
        }
        for name, (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
            has_column_filter,
        ) in sorted(EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS.items())
    ]


def _valid_nonopening_stocktake_close_index_rows() -> list[dict[str, object]]:
    return [
        {
            "index_name": name,
            "table_name": expected["table"],
            "access_method": "btree",
            "is_unique": expected["unique"],
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "key_columns": list(expected["columns"]),
            "predicate": None,
        }
        for name, expected in sorted(
            EXPECTED_NONOPENING_STOCKTAKE_CLOSE_INDEXES.items()
        )
    ]


def _valid_nonopening_stocktake_close_constraint_rows(
) -> list[dict[str, object]]:
    return [
        {
            "constraint_name": name,
            "table_name": expected["table"],
            "constraint_type": "f",
            "is_validated": True,
            "is_deferrable": True,
            "is_initially_deferred": True,
            "constrained_columns": list(expected["columns"]),
            "referenced_table": expected["referenced_table"],
            "referenced_columns": list(expected["referenced_columns"]),
            "update_action": "a",
            "delete_action": "r",
        }
        for name, expected in sorted(
            EXPECTED_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINTS.items()
        )
    ]


def test_0038_nonopening_close_runtime_manifest_and_function_bodies_are_exact(
) -> None:
    migration = _load_nonopening_stocktake_close_migration_0038()
    assert migration.down_revision == "20260901_0037"
    fact_tables = {
        "stocktake_close_transition_acks",
        "stocktake_close_reconciliation_completions",
        "stocktake_close_reconciliation_accounts",
        "stocktake_close_reconciliation_serials",
        "stocktake_close_completions",
    }
    assert set(migration.NEW_TABLES) == fact_tables
    assert fact_tables <= RUNTIME_READ_TABLES
    assert fact_tables - {"stocktake_close_transition_acks"} <= (
        RUNTIME_INSERT_TABLES
    )
    assert "stocktake_close_transition_acks" not in RUNTIME_INSERT_TABLES
    assert fact_tables.isdisjoint(RUNTIME_UPDATE_TABLES)
    assert fact_tables.isdisjoint(RUNTIME_DELETE_TABLES)

    function_sql = {
        (migration.PG_LOCK_FUNCTION, "uuid"):
            migration._postgresql_lock_function_sql(),
        (migration.PG_GRAPH_FUNCTION, ""):
            migration._postgresql_graph_function_sql(),
        (migration.PG_IMMUTABLE_FUNCTION, ""):
            migration._postgresql_immutable_function_sql(),
        (migration.PG_ACK_FUNCTION, ""):
            migration._postgresql_ack_function_sql(),
        (migration.PG_ACK_GUARD_FUNCTION, ""):
            migration._postgresql_ack_guard_function_sql(),
        (migration.PG_EVENT_GUARD_FUNCTION, ""):
            migration._postgresql_event_guard_function_sql(),
    }
    for coordinate, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        expected_hashes = (
            RUNTIME_FUNCTION_BODY_SHA256
            if coordinate in RUNTIME_FUNCTION_BODY_SHA256
            else FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256
        )
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            expected_hashes[coordinate]
        )
    assert RUNTIME_EXECUTE_FUNCTIONS[(migration.PG_LOCK_FUNCTION, "uuid")] == (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    )
    for internal_name in (
        migration.PG_GRAPH_FUNCTION,
        migration.PG_IMMUTABLE_FUNCTION,
        migration.PG_ACK_FUNCTION,
        migration.PG_ACK_GUARD_FUNCTION,
        migration.PG_EVENT_GUARD_FUNCTION,
    ):
        coordinate = (internal_name, "")
        assert coordinate in FORMAL_FILE_INTERNAL_FUNCTIONS
        assert coordinate not in RUNTIME_EXECUTE_FUNCTIONS
        assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == (
            "f",
            "trigger",
            False,
        )

    source = NONOPENING_STOCKTAKE_CLOSE_MIGRATION_0038.read_text(
        encoding="utf-8"
    )
    assert "GRANT SELECT ON TABLE public.{TRANSITION_ACK}" in source
    assert "GRANT SELECT, INSERT ON TABLE {command_tables}" in source
    assert "GRANT SELECT, INSERT ON TABLE public.{TRANSITION_ACK}" not in source
    assert "REVOKE EXECUTE ON FUNCTION {ack_signature}" in source
    assert "REVOKE EXECUTE ON FUNCTION {graph_signature}" in source
    assert "REVOKE EXECUTE ON FUNCTION {event_guard_signature}" in source


def test_0038_nonopening_close_catalog_guard_is_exact_and_rejects_drift(
) -> None:
    triggers = _valid_nonopening_stocktake_close_trigger_rows()
    indexes = _valid_nonopening_stocktake_close_index_rows()
    constraints = _valid_nonopening_stocktake_close_constraint_rows()
    _assert_nonopening_stocktake_close_guards(
        triggers=triggers,
        indexes=indexes,
        constraints=constraints,
    )

    task_ack = next(
        row
        for row in triggers
        if row["trigger_name"] == "trg_stocktake_tasks_close_ack_0038"
    )
    assert task_ack["has_column_filter"] is True
    for name, expected in EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS.items():
        if name.endswith("_graph_0038"):
            assert expected[4:7] == (True, True, True)
    trigger_query = str(_NONOPENING_STOCKTAKE_CLOSE_TRIGGER_SQL)
    assert "table_row.relname IN" in trigger_query
    assert "trigger_row.tgname LIKE '%0038'" in trigger_query

    drifted_triggers = [dict(row) for row in triggers]
    next(
        row
        for row in drifted_triggers
        if str(row["trigger_name"]).endswith("_graph_0038")
    )["is_initially_deferred"] = False
    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake close"):
        _assert_nonopening_stocktake_close_guards(
            triggers=drifted_triggers,
            indexes=indexes,
            constraints=constraints,
        )

    drifted_indexes = [dict(row) for row in indexes]
    drifted_indexes[0]["key_columns"] = ["occurred_at", "transition_kind"]
    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake close"):
        _assert_nonopening_stocktake_close_guards(
            triggers=triggers,
            indexes=drifted_indexes,
            constraints=constraints,
        )

    drifted_constraints = [dict(row) for row in constraints]
    drifted_constraints[0]["is_initially_deferred"] = False
    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake close"):
        _assert_nonopening_stocktake_close_guards(
            triggers=triggers,
            indexes=indexes,
            constraints=drifted_constraints,
        )

    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake close"):
        _assert_nonopening_stocktake_close_guards(
            triggers=triggers[:-1],
            indexes=indexes,
            constraints=constraints,
        )

    extra = dict(triggers[0])
    extra["trigger_name"] = "trg_unapproved_fact_rewrite"
    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake close"):
        _assert_nonopening_stocktake_close_guards(
            triggers=[*triggers, extra],
            indexes=indexes,
            constraints=constraints,
        )


def test_formal_file_catalog_guard_is_exact_and_rejects_drift() -> None:
    triggers = [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": enabled,
            "trigger_type": trigger_type,
            "is_constraint_trigger": False,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, (table_name, function_name, enabled, trigger_type) in sorted(
            EXPECTED_FORMAL_FILE_TRIGGERS.items()
        )
    ]
    indexes = [
        {
            "index_name": name,
            "table_name": expected["table"],
            "access_method": "btree",
            "is_unique": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "key_columns": list(expected["columns"]),
            "predicate": (
                None
                if expected["predicate"] is None
                else "document_type = 'stocktake_scope_count_completion' "
                "AND attachment_type = 'stocktake_evidence'"
            ),
        }
        for name, expected in sorted(EXPECTED_FORMAL_FILE_INDEXES.items())
    ]
    _assert_formal_file_guards(triggers=triggers, indexes=indexes)

    drifted = [dict(row) for row in indexes]
    row = next(
        item
        for item in drifted
        if item["table_name"] == "document_attachments"
    )
    row["predicate"] += " AND status = 'active'"
    with pytest.raises(DatabaseSecurityBoundaryError, match="formal file"):
        _assert_formal_file_guards(triggers=triggers, indexes=drifted)


def test_mounted_opening_workflow_has_complete_minimum_runtime_acl() -> None:
    # Static call-chain coverage for:
    # formal_opening_stocktake -> start/count/disposition/review/recount/
    # finalize -> inventory_posting and every replay validator they call.
    required_reads = {
        "audit_chain_heads",
        "audit_events",
        "external_objects",
        "external_object_versions",
        "inventory_freezes",
        "inventory_ledger_heads",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_opening_establishments",
        "inventory_serials",
        "inventory_transactions",
        "material_inventory_policies",
        "materials",
        "organizations",
        "outbox_events",
        "people",
        "qr_codes",
        "role_assignments",
        "role_permissions",
        "roles",
        "serial_current_positions",
        "source_systems",
        "state_transition_events",
        "stock_accounts",
        "stock_balances",
        "stock_locations",
        "stocktake_control_snapshot_lines",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_count_serials",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_observation_dispositions",
        "stocktake_posting_items",
        "stocktake_postings",
        "stocktake_recount_cases",
        "stocktake_recount_scope_assignments",
        "stocktake_review_items",
        "stocktake_reviews",
        "stocktake_round_submissions",
        "stocktake_rounds",
        "stocktake_scope_count_completions",
        "stocktake_scopes",
        "stocktake_snapshot_lines",
        "stocktake_tasks",
        "sync_batches",
        "sync_inbox_events",
        "sync_runs",
        "users",
    }
    assert required_reads <= RUNTIME_READ_TABLES
    required_inserts = {
        "audit_events",
        "inventory_freezes",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_opening_establishments",
        "inventory_transactions",
        "outbox_events",
        "serial_current_positions",
        "state_transition_events",
        "stock_accounts",
        "stock_balances",
        "stocktake_control_snapshot_lines",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_count_serials",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_observation_dispositions",
        "stocktake_posting_items",
        "stocktake_postings",
        "stocktake_recount_cases",
        "stocktake_recount_scope_assignments",
        "stocktake_review_items",
        "stocktake_reviews",
        "stocktake_round_submissions",
        "stocktake_rounds",
        "stocktake_scope_count_completions",
        "stocktake_scopes",
        "stocktake_snapshot_lines",
        "stocktake_tasks",
    }
    assert required_inserts <= RUNTIME_INSERT_TABLES
    assert RUNTIME_UPDATE_COLUMNS["stocktake_tasks"] == {
        "status",
        "submitted_at",
        "current_round_no",
        "posted_at",
        "closed_at",
        "version",
        "updated_at",
    }
    assert RUNTIME_UPDATE_COLUMNS["stocktake_rounds"] == {
        "status",
        "submitted_by_user_id",
        "submitted_at",
        "count_manifest_sha256",
        "updated_at",
    }
    assert RUNTIME_UPDATE_COLUMNS["inventory_freezes"] == {
        "status",
        "valid_to",
        "released_by_user_id",
        "release_reason",
        "version",
        "updated_at",
    }
    assert "stocktake_tasks" not in RUNTIME_UPDATE_TABLES
    assert "stocktake_rounds" not in RUNTIME_UPDATE_TABLES
    assert "inventory_freezes" not in RUNTIME_UPDATE_TABLES
    assert "qr_codes" not in RUNTIME_INSERT_TABLES
    assert "qr_codes" not in RUNTIME_UPDATE_TABLES
    assert "qr_codes" not in RUNTIME_DELETE_TABLES
    for table_name in required_inserts:
        if table_name not in {
            "stock_balances",
            "stock_accounts",
        }:
            assert table_name not in RUNTIME_DELETE_TABLES
    assert "stock_accounts" in RUNTIME_INSERT_TABLES
    assert "stock_accounts" not in RUNTIME_UPDATE_TABLES
    assert "stock_accounts" not in RUNTIME_DELETE_TABLES
    assert "stock_accounts" not in RUNTIME_UPDATE_COLUMNS


def test_opening_reconciliation_has_exact_runtime_acl() -> None:
    assert {
        "files",
        "opening_control_reconciliation_command_consumptions",
        "opening_control_reconciliation_items",
        "opening_control_reconciliation_runs",
        "reconciliation_commands",
        "reconciliation_items",
        "reconciliation_runs",
    } <= RUNTIME_READ_TABLES
    assert {
        "opening_control_reconciliation_command_consumptions",
        "opening_control_reconciliation_items",
        "opening_control_reconciliation_runs",
        "reconciliation_commands",
        "reconciliation_items",
        "reconciliation_runs",
    } <= RUNTIME_INSERT_TABLES
    assert "files" in RUNTIME_INSERT_TABLES
    assert RUNTIME_UPDATE_COLUMNS["files"] == {"status", "metadata_jsonb"}
    assert "reconciliation_commands" not in RUNTIME_UPDATE_TABLES
    assert "reconciliation_commands" not in RUNTIME_DELETE_TABLES
    assert (
        "opening_control_reconciliation_command_consumptions"
        not in RUNTIME_UPDATE_TABLES
    )
    assert (
        "opening_control_reconciliation_command_consumptions"
        not in RUNTIME_DELETE_TABLES
    )
    assert RUNTIME_UPDATE_COLUMNS["reconciliation_runs"] == {
        "status",
        "updated_at",
    }
    assert RUNTIME_UPDATE_COLUMNS["reconciliation_items"] == {
        "status",
        "explanation",
        "evidence_file_id",
        "updated_at",
    }
    assert RUNTIME_UPDATE_COLUMNS["opening_control_reconciliation_items"] == {
        "version",
        "evidence_reference",
        "evidence_file_sha256",
        "evidence_file_size_bytes",
        "evidence_file_mime_type",
        "explained_by_user_id",
        "explained_by_person_id",
        "explained_role_assignment_id",
        "explanation_authorization_version",
        "explained_at",
        "updated_at",
    }


def test_opening_observation_account_acl_is_insert_only_master_creation() -> None:
    account = next(
        row
        for row in _valid_table_acl()
        if row["table_name"] == "stock_accounts"
    )
    assert account["can_select"] is True
    assert account["can_insert"] is True
    for field in (
        "can_update",
        "can_delete",
        "can_truncate",
        "can_reference",
        "can_trigger",
        "has_runtime_grant_option",
        "has_public_table_acl",
        "has_explicit_runtime_column_acl",
    ):
        assert account[field] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role_name", "star_oam_migrator"),
        ("is_superuser", True),
        ("can_create_database", True),
        ("can_create_role", True),
        ("can_replicate", True),
        ("can_bypass_rls", True),
        ("can_create_in_database", True),
        ("can_create_temporary_tables", True),
        ("has_database_grant_option", True),
        ("database_owner", "star_oam_api"),
        ("can_use_schema", False),
        ("can_create_in_schema", True),
        ("has_schema_grant_option", True),
        ("current_schema_name", "attacker_schema"),
        ("current_schema_path", ["attacker_schema", "public"]),
        ("has_non_system_schema_control", True),
        ("schema_owner", "star_oam_api"),
        ("audit_events_owner", "star_oam_api"),
        ("audit_heads_owner", "star_oam_api"),
        ("migration_role_exists", False),
        ("has_any_role_membership", True),
        ("has_any_role_members", True),
        ("migration_role_is_superuser", True),
        ("migration_role_can_create_database", True),
        ("migration_role_can_create_role", True),
        ("migration_role_can_replicate", True),
        ("migration_role_can_bypass_rls", True),
        ("migration_role_has_any_membership", True),
        ("migration_role_has_any_members", True),
        ("is_migration_role_member", True),
        ("can_disable_replication_guards", True),
        ("session_replication_role", "replica"),
        ("audit_can_select", False),
        ("audit_can_insert", False),
        ("audit_can_update", True),
        ("audit_can_delete", True),
        ("audit_can_truncate", True),
        ("audit_can_control_trigger", True),
        ("heads_can_select", False),
        ("heads_can_update", True),
        ("heads_can_insert", True),
        ("heads_can_delete", True),
        ("heads_can_truncate", True),
        ("heads_can_control_trigger", True),
        ("alembic_can_select", True),
        ("alembic_can_insert", True),
        ("alembic_can_update", True),
        ("alembic_can_delete", True),
    ],
)
def test_production_database_role_fails_closed_on_each_forbidden_capability(
    field: str,
    value: object,
) -> None:
    evidence = _valid_evidence()
    evidence[field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match=field):
        _assert_production_database_evidence(
            evidence,
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def _table_acl_row(table_name: str) -> dict[str, object]:
    return {
        "table_name": table_name,
        "owner_name": "star_oam_migrator",
        "can_select": table_name in RUNTIME_READ_TABLES,
        "can_insert": table_name in RUNTIME_INSERT_TABLES,
        "can_update": table_name in RUNTIME_UPDATE_TABLES,
        "can_delete": table_name in RUNTIME_DELETE_TABLES,
        "can_truncate": False,
        "can_reference": False,
        "can_trigger": False,
        "has_explicit_runtime_column_acl": table_name in RUNTIME_UPDATE_COLUMNS,
        "has_public_table_acl": False,
        "has_runtime_grant_option": False,
    }


def _valid_table_acl() -> list[dict[str, object]]:
    names = (
        set(RUNTIME_READ_TABLES)
        | set(RUNTIME_INSERT_TABLES)
        | set(RUNTIME_UPDATE_TABLES)
        | set(RUNTIME_DELETE_TABLES)
        | set(RUNTIME_UPDATE_COLUMNS)
        | {
        "alembic_version",
        "legacy_v09_materials",
        }
    )
    return [_table_acl_row(name) for name in sorted(names)]


def test_runtime_table_acl_is_an_exact_allowlist() -> None:
    _assert_runtime_table_acl(
        _valid_table_acl(),
        expected_migration_role="star_oam_migrator",
    )


def _valid_column_acl() -> list[dict[str, object]]:
    return [
        {
            "table_name": table_name,
            "column_name": column_name,
            "grantee_name": "star_oam_api",
            "privilege_type": "UPDATE",
            "is_grantable": False,
        }
        for table_name, column_names in sorted(RUNTIME_UPDATE_COLUMNS.items())
        for column_name in sorted(column_names)
    ]


def test_runtime_column_acl_is_an_exact_allowlist() -> None:
    rows = _valid_column_acl()
    _assert_runtime_column_acl(rows, expected_runtime_role="star_oam_api")

    missing = [row.copy() for row in rows[:-1]]
    with pytest.raises(DatabaseSecurityBoundaryError, match="missing"):
        _assert_runtime_column_acl(
            missing,
            expected_runtime_role="star_oam_api",
        )

    excess = [row.copy() for row in rows]
    excess.append(
        {
            "table_name": "stocktake_tasks",
            "column_name": "note",
            "grantee_name": "star_oam_api",
            "privilege_type": "UPDATE",
            "is_grantable": False,
        }
    )
    with pytest.raises(DatabaseSecurityBoundaryError, match="note"):
        _assert_runtime_column_acl(
            excess,
            expected_runtime_role="star_oam_api",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("grantee_name", "PUBLIC"),
        ("privilege_type", "SELECT"),
        ("is_grantable", True),
    ],
)
def test_runtime_column_acl_rejects_public_wrong_or_grantable_privilege(
    field: str,
    value: object,
) -> None:
    rows = _valid_column_acl()
    rows[0][field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match="column ACL"):
        _assert_runtime_column_acl(rows, expected_runtime_role="star_oam_api")


@pytest.mark.parametrize(
    ("table_name", "field", "value"),
    [
        ("roles", "can_update", True),
        ("inventory_ledger_heads", "can_insert", True),
        ("audit_events", "can_delete", True),
        ("legacy_v09_materials", "can_select", True),
        ("auth_sessions", "can_truncate", True),
        ("users", "owner_name", "star_oam_bootstrap"),
        ("users", "has_explicit_runtime_column_acl", True),
        ("roles", "has_public_table_acl", True),
        ("audit_events", "has_runtime_grant_option", True),
        ("stocktake_recount_cases", "can_insert", False),
        ("stocktake_recount_scope_assignments", "can_insert", False),
        ("stock_accounts", "can_insert", False),
    ],
)
def test_runtime_table_acl_rejects_every_unlisted_or_excess_privilege(
    table_name: str,
    field: str,
    value: object,
) -> None:
    rows = _valid_table_acl()
    row = next(item for item in rows if item["table_name"] == table_name)
    row[field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match=table_name):
        _assert_runtime_table_acl(
            rows,
            expected_migration_role="star_oam_migrator",
        )


def test_runtime_sequence_acl_rejects_privilege_or_wrong_owner() -> None:
    _assert_runtime_sequence_acl(
        [],
        expected_migration_role="star_oam_migrator",
    )
    with pytest.raises(DatabaseSecurityBoundaryError, match="unsafe_sequence"):
        _assert_runtime_sequence_acl(
            [
                {
                    "sequence_name": "unsafe_sequence",
                    "owner_name": "star_oam_migrator",
                    "can_use": True,
                    "can_select": False,
                    "can_update": False,
                }
            ],
            expected_migration_role="star_oam_migrator",
        )


def test_runtime_function_acl_rejects_execute_or_wrong_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_source_body = "test fixture body"
    fixture_source_hash = hashlib.sha256(
        fixture_source_body.encode("utf-8")
    ).hexdigest()
    for coordinate in RUNTIME_FUNCTION_BODY_SHA256:
        monkeypatch.setitem(
            RUNTIME_FUNCTION_BODY_SHA256,
            coordinate,
            fixture_source_hash,
        )
    for coordinate in FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256:
        monkeypatch.setitem(
            FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256,
            coordinate,
            fixture_source_hash,
        )
    function_definitions = {
        **RUNTIME_EXECUTE_FUNCTIONS,
        **FORMAL_FILE_INTERNAL_FUNCTIONS,
    }
    function_shapes = {
        **RUNTIME_FUNCTION_SHAPES,
        **FORMAL_FILE_INTERNAL_FUNCTION_SHAPES,
    }
    allowed_rows = [
        {
            "function_id": index,
            "function_name": function_name,
            "argument_types": argument_types,
            "function_kind": function_shapes[(function_name, argument_types)][0],
            "result_type": function_shapes[(function_name, argument_types)][1],
            "argument_modes": None,
            "argument_default_count": 0,
            "is_strict": function_shapes[(function_name, argument_types)][2],
            "source_body": fixture_source_body,
            "volatility": volatility,
            "is_security_definer": is_security_definer,
            "language_name": language_name,
            "configuration": list(configuration),
            "owner_name": "star_oam_migrator",
            "can_execute": (
                (function_name, argument_types) in RUNTIME_EXECUTE_FUNCTIONS
            ),
            "api_execute_is_grantable": False,
            "unexpected_execute_grantee_count": 0,
            "public_can_execute": False,
            "edge_can_execute": False,
            "backup_can_execute": False,
        }
        for index, (
            (function_name, argument_types),
            (
                volatility,
                is_security_definer,
                language_name,
                configuration,
            ),
        ) in enumerate(sorted(function_definitions.items()), start=1)
    ]
    _assert_runtime_function_acl(
        allowed_rows,
        expected_migration_role="star_oam_migrator",
    )
    for row in (
        {
            "function_id": 1,
            "function_name": "unsafe_function",
            "owner_name": "star_oam_migrator",
            "can_execute": True,
        },
        {
            "function_id": 2,
            "function_name": "wrong_owner_function",
            "owner_name": "star_oam_bootstrap",
            "can_execute": False,
        },
    ):
        with pytest.raises(DatabaseSecurityBoundaryError, match="function"):
            _assert_runtime_function_acl(
                [row],
                expected_migration_role="star_oam_migrator",
            )

    for field, value in (
        ("can_execute", False),
        ("api_execute_is_grantable", True),
        ("unexpected_execute_grantee_count", 1),
        ("volatility", "v"),
        ("is_security_definer", True),
        ("argument_types", "text"),
        ("function_kind", "p"),
        ("result_type", "record"),
        ("argument_modes", ["i", "o"]),
        ("argument_default_count", 1),
        ("is_strict", None),
        ("source_body", "drifted helper body"),
        ("language_name", "internal"),
        ("configuration", ["search_path=public"]),
        ("public_can_execute", True),
        ("edge_can_execute", True),
        ("backup_can_execute", True),
    ):
        drifted = [row.copy() for row in allowed_rows]
        drifted[0][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="function"):
            _assert_runtime_function_acl(
                drifted,
                expected_migration_role="star_oam_migrator",
            )


def test_0027_runtime_function_manifest_is_exact_and_does_not_expand_updates(
) -> None:
    source = LOCK_GRAPH_MIGRATION_0027.read_text(encoding="utf-8")
    expected_coordinates = {
        (
            "rsc_lock_opening_control_import_0027",
            "uuid, uuid",
        ),
        (
            "rsc_lock_opening_stocktake_start_reference_0027",
            "uuid, uuid[], uuid[], uuid[], timestamp with time zone",
        ),
        (
            "rsc_lock_opening_stocktake_task_evidence_0027",
            "uuid, uuid",
        ),
        (
            "rsc_lock_inventory_reference_graph_0027",
            "uuid[], timestamp with time zone",
        ),
        ("rsc_lock_inventory_serial_graph_0027", "uuid[]"),
    }
    assert expected_coordinates <= set(RUNTIME_EXECUTE_FUNCTIONS)
    assert set(RUNTIME_FUNCTION_SHAPES) == set(RUNTIME_EXECUTE_FUNCTIONS)
    assert set(RUNTIME_FUNCTION_BODY_SHA256) == set(RUNTIME_EXECUTE_FUNCTIONS)
    for coordinate in expected_coordinates:
        assert RUNTIME_EXECUTE_FUNCTIONS[coordinate] == (
            "v",
            True,
            "plpgsql",
            ("search_path=pg_catalog, public",),
        )
        assert RUNTIME_FUNCTION_SHAPES[coordinate] == ("f", "void", False)
    assert "GRANT UPDATE" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT DELETE" not in source
    assert "GRANT EXECUTE ON FUNCTION" in source
    assert 'f"PUBLIC, {PRODUCTION_API_ROLE}"' in source
    assert "'star_oam_backup', 'star_oam_edge'" in source


def test_0028_terminal_union_helper_is_in_exact_runtime_manifest() -> None:
    source = LOCK_GRAPH_MIGRATION_0028.read_text(encoding="utf-8")
    coordinate = (
        "rsc_lock_opening_terminal_reference_union_0028",
        "uuid[], uuid[], uuid[], uuid[], uuid[]",
    )
    assert coordinate in RUNTIME_EXECUTE_FUNCTIONS
    assert set(RUNTIME_FUNCTION_SHAPES) == set(RUNTIME_EXECUTE_FUNCTIONS)
    assert set(RUNTIME_FUNCTION_BODY_SHA256) == set(RUNTIME_EXECUTE_FUNCTIONS)
    assert RUNTIME_EXECUTE_FUNCTIONS[coordinate] == (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    )
    assert RUNTIME_FUNCTION_SHAPES[coordinate] == ("f", "void", False)
    assert RUNTIME_FUNCTION_BODY_SHA256[coordinate] == (
        "a5445f4651364a179223695d28ba7ce9434f0f20307098a9291067b64b07d066"
    )
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0028_security_manifest",
        LOCK_GRAPH_MIGRATION_0028,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    rendered: list[str] = []
    migration.op = SimpleNamespace(execute=rendered.append)
    migration._create_postgresql_union_lock_function()
    function_sql = rendered[0]
    function_body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(function_body.encode("utf-8")).hexdigest() == (
        RUNTIME_FUNCTION_BODY_SHA256[coordinate]
    )
    assert "GRANT UPDATE" not in source
    assert "GRANT INSERT" not in source
    assert "GRANT DELETE" not in source
    assert "GRANT EXECUTE ON FUNCTION" in source


def _valid_audit_trigger_rows() -> list[dict[str, object]]:
    return [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": "A",
            "trigger_type": trigger_type,
            "is_constraint_trigger": is_constraint_trigger,
            "is_deferrable": is_deferrable,
            "is_initially_deferred": is_initially_deferred,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, (
            table_name,
            function_name,
            trigger_type,
            is_constraint_trigger,
            is_deferrable,
            is_initially_deferred,
        ) in sorted(
            EXPECTED_AUDIT_TRIGGERS.items()
        )
    ]


def _valid_opening_terminal_trigger_rows() -> list[dict[str, object]]:
    return [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": enabled,
            "trigger_type": trigger_type,
            "is_constraint_trigger": name in OPENING_COMMIT_TRIGGER_NAMES,
            "is_deferrable": name in OPENING_COMMIT_TRIGGER_NAMES,
            "is_initially_deferred": name in OPENING_COMMIT_TRIGGER_NAMES,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, (
            table_name,
            function_name,
            enabled,
            trigger_type,
        ) in sorted(EXPECTED_OPENING_TERMINAL_TRIGGERS.items())
    ]


def test_opening_terminal_trigger_guard_requires_exact_always_bindings() -> None:
    rows = _valid_opening_terminal_trigger_rows()
    _assert_opening_terminal_triggers(rows)
    query = str(_OPENING_TERMINAL_TRIGGER_SQL)
    for trigger_name in EXPECTED_OPENING_TERMINAL_TRIGGERS:
        assert f"'{trigger_name}'" in query

    for field, value in (
        ("enabled", "D"),
        ("function_schema", "attacker"),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ):
        drifted = [row.copy() for row in rows]
        drifted[0][field] = value
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="opening terminal trigger",
        ):
            _assert_opening_terminal_triggers(drifted)

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="opening terminal trigger",
    ):
        _assert_opening_terminal_triggers(rows[:-1])


def _valid_reconciliation_trigger_rows() -> list[dict[str, object]]:
    return [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": enabled,
            "trigger_type": trigger_type,
            "is_constraint_trigger": False,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, (
            table_name,
            function_name,
            enabled,
            trigger_type,
        ) in sorted(EXPECTED_RECONCILIATION_TRIGGERS.items())
    ]


def test_reconciliation_trigger_guard_requires_exact_always_bindings() -> None:
    rows = _valid_reconciliation_trigger_rows()
    _assert_reconciliation_triggers(rows)
    query = str(_RECONCILIATION_TRIGGER_SQL)
    assert "tgname LIKE '%reconciliation%0026'" in query
    assert "tgname IN" not in query
    for field, value in (
        ("enabled", "D"),
        ("function_schema", "attacker"),
        ("trigger_type", 0),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ):
        drifted = [row.copy() for row in rows]
        drifted[0][field] = value
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="reconciliation trigger",
        ):
            _assert_reconciliation_triggers(drifted)
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="reconciliation trigger",
    ):
        _assert_reconciliation_triggers(rows[:-1])


def _valid_reconciliation_constraint_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, expected in EXPECTED_RECONCILIATION_CONSTRAINTS.items():
        constraint_type = str(expected["type"])
        rows.append(
            {
                "constraint_name": name,
                "table_name": expected["table"],
                "constraint_type": constraint_type,
                "is_validated": True,
                "is_deferrable": bool(expected.get("deferred", False)),
                "is_initially_deferred": bool(
                    expected.get("deferred", False)
                ),
                "definition": (
                    "CHECK ("
                    + " AND ".join(expected.get("tokens", ()))
                    + ")"
                    if constraint_type == "c"
                    else ""
                ),
                "constrained_columns": list(expected.get("columns", ())),
                "referenced_table": expected.get("referenced_table"),
                "referenced_columns": list(
                    expected.get("referenced_columns", ())
                ),
                "update_action": "a" if constraint_type == "f" else None,
                "delete_action": "a" if constraint_type == "f" else None,
            }
        )
    return rows


def _valid_reconciliation_partial_index_rows() -> list[dict[str, object]]:
    return [
        {
            "index_name": name,
            "table_name": "reconciliation_commands",
            "access_method": "btree",
            "is_unique": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "key_columns": ["run_id"],
            "predicate": f"operation = '{operation}'::character varying",
        }
        for name, operation in sorted(
            EXPECTED_RECONCILIATION_PARTIAL_INDEXES.items()
        )
    ]


def test_reconciliation_schema_guard_requires_exact_deferred_closure() -> None:
    constraints = _valid_reconciliation_constraint_rows()
    partial_indexes = _valid_reconciliation_partial_index_rows()
    _assert_reconciliation_schema(
        constraints=constraints,
        partial_indexes=partial_indexes,
    )
    constraint_query = str(_RECONCILIATION_CONSTRAINT_SQL)
    index_query = str(_RECONCILIATION_PARTIAL_INDEX_SQL)
    for name in EXPECTED_RECONCILIATION_CONSTRAINTS:
        assert f"'{name}'" in constraint_query
    assert "indpred IS NOT NULL" in index_query

    for field, value in (
        ("is_initially_deferred", False),
        ("delete_action", "r"),
        ("referenced_columns", ["command_id", "run_id"]),
    ):
        drifted = [row.copy() for row in constraints]
        target = next(
            row for row in drifted if row["constraint_type"] == "f"
        )
        target[field] = value
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="reconciliation schema",
        ):
            _assert_reconciliation_schema(
                constraints=drifted,
                partial_indexes=partial_indexes,
            )

    drifted_check = [row.copy() for row in constraints]
    target_check = next(
        row
        for row in drifted_check
        if row["constraint_name"]
        == "ck_reconciliation_commands_target_version"
    )
    target_check["definition"] = "CHECK (target_version IS NOT NULL)"
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="reconciliation schema",
    ):
        _assert_reconciliation_schema(
            constraints=drifted_check,
            partial_indexes=partial_indexes,
        )

    drifted_indexes = [row.copy() for row in partial_indexes]
    drifted_indexes[0]["predicate"] = "operation = 'explain_opening'"
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="reconciliation schema",
    ):
        _assert_reconciliation_schema(
            constraints=constraints,
            partial_indexes=drifted_indexes,
        )


def test_opening_terminal_unique_index_is_exact_startup_proof() -> None:
    row = {
        "index_name": EXPECTED_OPENING_TERMINAL_INDEX,
        "table_name": "stocktake_postings",
        "access_method": "btree",
        "is_unique": True,
        "is_valid": True,
        "is_ready": True,
        "is_live": True,
        "key_columns": ["task_id"],
        "predicate": "posting_kind = 'opening'::character varying",
    }
    _assert_opening_terminal_index([row])
    assert EXPECTED_OPENING_TERMINAL_INDEX in str(_OPENING_TERMINAL_INDEX_SQL)
    for field, value in (
        ("is_unique", False),
        ("key_columns", ["round_id"]),
        ("predicate", "posting_kind = 'difference_adjustment'"),
    ):
        drifted = row.copy()
        drifted[field] = value
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="unique index",
        ):
            _assert_opening_terminal_index([drifted])


def test_audit_trigger_guard_requires_exact_enabled_bindings() -> None:
    _assert_audit_trigger_guards(_valid_audit_trigger_rows())
    for field, value in (
        ("enabled", "D"),
        ("function_name", "unsafe_function"),
        ("trigger_type", 0),
        ("is_constraint_trigger", True),
        ("is_deferrable", True),
        ("is_initially_deferred", True),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ):
        rows = _valid_audit_trigger_rows()
        rows[0][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="trigger"):
            _assert_audit_trigger_guards(rows)
    with pytest.raises(DatabaseSecurityBoundaryError, match="trigger_set"):
        _assert_audit_trigger_guards(_valid_audit_trigger_rows()[1:])


def _valid_audit_stream_columns() -> list[dict[str, object]]:
    return [
        {
            "column_name": "stream_key",
            "is_not_null": True,
            "data_type": "character varying(160)",
        },
        {
            "column_name": "stream_version",
            "is_not_null": True,
            "data_type": "bigint",
        },
    ]


def _valid_audit_stream_constraints() -> list[dict[str, object]]:
    common = {
        "is_validated": True,
        "is_deferrable": False,
        "is_initially_deferred": False,
        "referenced_table": None,
        "referenced_columns": [],
        "update_action": " ",
        "delete_action": " ",
    }
    return [
        {
            **common,
            "constraint_name": "ck_audit_events_stream_key_0017",
            "constraint_type": "c",
            "definition": "CHECK (stream_key IN ('authorization', "
            "'authentication', 'inventory', 'material_request'))",
            "constrained_columns": ["stream_key"],
        },
        {
            **common,
            "constraint_name": "ck_audit_events_stream_version_0017",
            "constraint_type": "c",
            "definition": "CHECK (stream_version > 0)",
            "constrained_columns": ["stream_version"],
        },
        {
            **common,
            "constraint_name": "uq_audit_events_stream_version_0017",
            "constraint_type": "u",
            "definition": "UNIQUE (stream_key, stream_version)",
            "constrained_columns": ["stream_key", "stream_version"],
        },
        {
            **common,
            "constraint_name": "fk_audit_events_stream_key_0017",
            "constraint_type": "f",
            "definition": "FOREIGN KEY (stream_key) REFERENCES "
            "audit_chain_heads(stream_key) ON UPDATE RESTRICT ON DELETE RESTRICT",
            "constrained_columns": ["stream_key"],
            "referenced_table": "audit_chain_heads",
            "referenced_columns": ["stream_key"],
            "update_action": "r",
            "delete_action": "r",
        },
    ]


def test_audit_stream_schema_guard_requires_exact_validated_constraints() -> None:
    _assert_audit_stream_schema(
        columns=_valid_audit_stream_columns(),
        constraints=_valid_audit_stream_constraints(),
    )
    for collection, row_index, field, value in (
        ("columns", 0, "is_not_null", False),
        ("columns", 1, "data_type", "integer"),
        ("constraints", 0, "definition", "CHECK (stream_key IS NOT NULL)"),
        (
            "constraints",
            0,
            "definition",
            "CHECK (stream_key IN ('authorization', 'authentication', "
            "'inventory', 'evil'))",
        ),
        ("constraints", 1, "is_validated", False),
        ("constraints", 2, "constrained_columns", ["stream_key"]),
        ("constraints", 3, "delete_action", "a"),
    ):
        columns = _valid_audit_stream_columns()
        constraints = _valid_audit_stream_constraints()
        rows = columns if collection == "columns" else constraints
        rows[row_index][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="stream schema"):
            _assert_audit_stream_schema(
                columns=columns,
                constraints=constraints,
            )


def _valid_stocktake_recount_columns() -> list[dict[str, object]]:
    return [
        {
            "table_name": table_name,
            "table_kind": "r",
            "column_name": column_name,
            "is_not_null": is_not_null,
            "data_type": data_type,
        }
        for (table_name, column_name), (
            is_not_null,
            data_type,
        ) in sorted(EXPECTED_STOCKTAKE_RECOUNT_COLUMNS.items())
    ]


def _valid_stocktake_recount_constraints() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, expected in sorted(
        EXPECTED_STOCKTAKE_RECOUNT_CONSTRAINTS.items()
    ):
        is_foreign_key = expected["type"] == "f"
        if is_foreign_key:
            definition = "FOREIGN KEY (...) REFERENCES ..."
        elif expected["type"] == "p":
            definition = "PRIMARY KEY (...)"
        elif name == "ck_stocktake_recount_cases_round_0018":
            definition = "CHECK (next_round_no > 1 AND scope_count > 0)"
        else:
            definition = (
                "CHECK ("
                + " AND ".join(
                    f"length({token}) = 64"
                    for token in expected["definition_tokens"]
                )
                + ")"
            )
        rows.append(
            {
                "constraint_name": name,
                "table_name": expected["table"],
                "constraint_type": expected["type"],
                "is_validated": True,
                "is_deferrable": False,
                "is_initially_deferred": False,
                "definition": definition,
                "constrained_columns": list(expected.get("columns", ())),
                "referenced_table": expected.get("referenced_table"),
                "referenced_columns": list(
                    expected.get("referenced_columns", ())
                ),
                "update_action": "a" if is_foreign_key else " ",
                "delete_action": "r" if is_foreign_key else " ",
            }
        )
    return rows


def _valid_stocktake_recount_indexes() -> list[dict[str, object]]:
    predicates = {
        None: None,
        "not_null_recount_case": "recount_case_id IS NOT NULL",
        "counting": "status = 'counting'",
    }
    return [
        {
            "index_name": name,
            "table_name": expected["table"],
            "access_method": "btree",
            "is_unique": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "key_columns": list(expected["columns"]),
            "predicate": predicates[expected["predicate"]],
        }
        for name, expected in sorted(
            EXPECTED_STOCKTAKE_RECOUNT_INDEXES.items()
        )
    ]


def _valid_stocktake_recount_triggers() -> list[dict[str, object]]:
    return [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": enabled,
            "trigger_type": trigger_type,
            "is_constraint_trigger": is_constraint_trigger,
            "is_deferrable": is_deferrable,
            "is_initially_deferred": is_initially_deferred,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint_trigger,
            is_deferrable,
            is_initially_deferred,
        ) in sorted(EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS.items())
    ]


def _valid_stocktake_scope_triggers() -> list[dict[str, object]]:
    return [
        {
            "trigger_name": name,
            "table_name": table_name,
            "function_name": function_name,
            "function_schema": "public",
            "enabled": enabled,
            "trigger_type": trigger_type,
            "is_constraint_trigger": is_constraint_trigger,
            "is_deferrable": is_deferrable,
            "is_initially_deferred": is_initially_deferred,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint_trigger,
            is_deferrable,
            is_initially_deferred,
        ) in sorted(EXPECTED_STOCKTAKE_SCOPE_TRIGGERS.items())
    ]


def _assert_valid_stocktake_recount_schema() -> None:
    _assert_stocktake_recount_schema(
        columns=_valid_stocktake_recount_columns(),
        constraints=_valid_stocktake_recount_constraints(),
        indexes=_valid_stocktake_recount_indexes(),
        triggers=_valid_stocktake_recount_triggers(),
    )


def test_stocktake_recount_schema_guard_accepts_insert_only_runtime_facts() -> None:
    _assert_valid_stocktake_recount_schema()
    rows = _valid_table_acl()
    for table_name in (
        "stocktake_recount_cases",
        "stocktake_recount_scope_assignments",
    ):
        row = next(item for item in rows if item["table_name"] == table_name)
        assert row["can_select"] is True
        assert row["can_insert"] is True
        assert not any(
            row[field]
            for field in (
                "can_update",
                "can_delete",
                "can_truncate",
                "can_reference",
                "can_trigger",
            )
        )


def test_stocktake_recount_schema_guard_rejects_missing_or_drifted_columns() -> None:
    columns = _valid_stocktake_recount_columns()[1:]
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=columns,
            constraints=_valid_stocktake_recount_constraints(),
            indexes=_valid_stocktake_recount_indexes(),
            triggers=_valid_stocktake_recount_triggers(),
        )

    columns = _valid_stocktake_recount_columns()
    columns[0]["is_not_null"] = not columns[0]["is_not_null"]
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=columns,
            constraints=_valid_stocktake_recount_constraints(),
            indexes=_valid_stocktake_recount_indexes(),
            triggers=_valid_stocktake_recount_triggers(),
        )


def test_stocktake_recount_schema_guard_rejects_constraint_drift() -> None:
    constraints = _valid_stocktake_recount_constraints()[1:]
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=constraints,
            indexes=_valid_stocktake_recount_indexes(),
            triggers=_valid_stocktake_recount_triggers(),
        )

    constraints = _valid_stocktake_recount_constraints()
    foreign_key = next(
        row for row in constraints if row["constraint_type"] == "f"
    )
    foreign_key["delete_action"] = "a"
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=constraints,
            indexes=_valid_stocktake_recount_indexes(),
            triggers=_valid_stocktake_recount_triggers(),
        )

    constraints = _valid_stocktake_recount_constraints()
    check = next(row for row in constraints if row["constraint_type"] == "c")
    check["definition"] = "CHECK (TRUE)"
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=constraints,
            indexes=_valid_stocktake_recount_indexes(),
            triggers=_valid_stocktake_recount_triggers(),
        )


def test_stocktake_recount_schema_guard_rejects_index_drift() -> None:
    indexes = _valid_stocktake_recount_indexes()[1:]
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=_valid_stocktake_recount_constraints(),
            indexes=indexes,
            triggers=_valid_stocktake_recount_triggers(),
        )

    indexes = _valid_stocktake_recount_indexes()
    counting = next(
        row
        for row in indexes
        if row["index_name"] == "uq_stocktake_rounds_one_counting_0018"
    )
    counting["predicate"] = "status = 'counting' OR TRUE"
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=_valid_stocktake_recount_constraints(),
            indexes=indexes,
            triggers=_valid_stocktake_recount_triggers(),
        )


def test_stocktake_recount_schema_guard_rejects_trigger_drift() -> None:
    triggers = _valid_stocktake_recount_triggers()[1:]
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=_valid_stocktake_recount_constraints(),
            indexes=_valid_stocktake_recount_indexes(),
            triggers=triggers,
        )

    for field, value in (
        ("enabled", "D"),
        ("function_name", "unsafe_function"),
        ("is_deferrable", False),
        ("is_initially_deferred", False),
    ):
        triggers = _valid_stocktake_recount_triggers()
        graph_trigger = next(
            row for row in triggers if row["is_constraint_trigger"] is True
        )
        graph_trigger[field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
            _assert_stocktake_recount_schema(
                columns=_valid_stocktake_recount_columns(),
                constraints=_valid_stocktake_recount_constraints(),
                indexes=_valid_stocktake_recount_indexes(),
                triggers=triggers,
            )


def test_stocktake_scope_trigger_guard_is_complete_exact_startup_proof() -> None:
    query = str(_STOCKTAKE_SCOPE_TRIGGER_SQL)
    assert "table_row.relname = 'stocktake_scopes'" in query
    assert "NOT trigger_row.tgisinternal" in query
    assert "trigger_row.tgname IN" not in query
    for trigger_name in EXPECTED_STOCKTAKE_SCOPE_TRIGGERS:
        assert f"'{trigger_name}'" not in query

    rows = _valid_stocktake_scope_triggers()
    _assert_stocktake_scope_triggers(rows)
    assert set(EXPECTED_STOCKTAKE_SCOPE_TRIGGERS) == {
        "trg_stocktake_scopes_immutable_0010",
        "trg_stocktake_scopes_sealed_insert_0010",
        "trg_stocktake_scopes_region_owner_0025",
    }
    assert EXPECTED_STOCKTAKE_SCOPE_TRIGGERS[
        "trg_stocktake_scopes_region_owner_0025"
    ] == (
        "stocktake_scopes",
        "rsc_validate_stocktake_scope_region_owner_0025",
        "A",
        7,
        False,
        False,
        False,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("table_name", "stocktake_tasks"),
        ("function_name", "unsafe_scope_guard"),
        ("function_schema", "attacker_schema"),
        ("enabled", "D"),
        ("trigger_type", 0),
        ("is_constraint_trigger", True),
        ("is_deferrable", True),
        ("is_initially_deferred", True),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ],
)
def test_stocktake_scope_trigger_guard_rejects_catalog_drift(
    field: str,
    value: object,
) -> None:
    rows = _valid_stocktake_scope_triggers()
    target = next(
        row
        for row in rows
        if row["trigger_name"]
        == "trg_stocktake_scopes_region_owner_0025"
    )
    target[field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match="scope trigger"):
        _assert_stocktake_scope_triggers(rows)


def test_stocktake_scope_trigger_guard_rejects_missing_extra_or_duplicate(
) -> None:
    valid = _valid_stocktake_scope_triggers()
    extra = {
        "trigger_name": "trg_stocktake_scopes_unapproved_extra",
        "table_name": "stocktake_scopes",
        "function_name": "rsc_unapproved_scope_guard",
        "function_schema": "public",
        "enabled": "A",
        "trigger_type": 7,
        "is_constraint_trigger": False,
        "is_deferrable": False,
        "is_initially_deferred": False,
        "has_when_clause": False,
        "has_column_filter": False,
    }
    for rows in (
        valid[1:],
        [*valid, extra],
        [*valid, dict(valid[0])],
    ):
        with pytest.raises(DatabaseSecurityBoundaryError, match="scope trigger"):
            _assert_stocktake_scope_triggers(rows)


def test_postgresql_stocktake_guard_names_match_identifier_limit() -> None:
    def constants(path: Path) -> dict[str, str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }

    migration_0019 = constants(PERSONAL_LOCATION_MIGRATION_0019)
    completion_ddl_name = migration_0019["COMPLETION_TRIGGER"]
    assignment_ddl_name = migration_0019["RECOUNT_ASSIGNMENT_TRIGGER"]
    assert len(completion_ddl_name.encode("utf-8")) > 63
    assert len(assignment_ddl_name.encode("utf-8")) > 63
    assert (
        completion_ddl_name.encode("utf-8")[:63].decode("utf-8")
        == POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019
    )
    assert (
        assignment_ddl_name.encode("utf-8")[:63].decode("utf-8")
        == POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019
    )

    migration_0020 = constants(PERSONAL_LOCATION_CONTINUITY_MIGRATION_0020)
    continuity_name = migration_0020["LOCATION_TRIGGER"]
    assert len(continuity_name.encode("utf-8")) <= 63
    assert continuity_name in EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS

    migration_0021 = constants(ROUND_ASSIGNMENT_GUARDS_MIGRATION_0021)
    for constant_name in (
        "COUNT_LINE_TRIGGER",
        "OBSERVATION_TRIGGER",
        "COMPLETION_TRIGGER",
        "CASE_TRIGGER",
    ):
        trigger_name = migration_0021[constant_name]
        assert len(trigger_name.encode("utf-8")) <= 63
        assert trigger_name in EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS

    migration_0025 = constants(STOCKTAKE_SCOPE_REGION_OWNER_MIGRATION_0025)
    scope_trigger_name = migration_0025["SCOPE_TRIGGER"]
    assert len(scope_trigger_name.encode("utf-8")) <= 63
    assert scope_trigger_name in EXPECTED_STOCKTAKE_SCOPE_TRIGGERS


def test_stocktake_technician_personal_location_guards_are_exact_startup_proof(
) -> None:
    sensitive_query = str(_STOCKTAKE_SENSITIVE_TRIGGER_SQL)
    for table_name in (
        "stock_locations",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_scope_count_completions",
        "stocktake_recount_scope_assignments",
    ):
        assert f"'{table_name}'" in sensitive_query
    assert "NOT trigger_row.tgisinternal" in sensitive_query
    assert "trigger_row.tgname IN" not in sensitive_query
    assert "trigger_row.tgname =" not in sensitive_query
    assert "RIGHT(trigger_row.tgname" not in sensitive_query
    for trigger_name in EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS:
        assert f"'{trigger_name}'" not in sensitive_query

    recount_query = str(_STOCKTAKE_RECOUNT_TRIGGER_SQL)
    for trigger_name in (
        set(EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS)
        - set(EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS)
    ):
        assert f"'{trigger_name}'" in recount_query

    for trigger_name in (
        "trg_stock_locations_stocktake_personal_continuity_0020",
        POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019,
        POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019,
    ):
        triggers = _valid_stocktake_recount_triggers()
        target = next(
            row for row in triggers if row["trigger_name"] == trigger_name
        )
        target["has_when_clause"] = True
        with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
            _assert_stocktake_recount_schema(
                columns=_valid_stocktake_recount_columns(),
                constraints=_valid_stocktake_recount_constraints(),
                indexes=_valid_stocktake_recount_indexes(),
                triggers=triggers,
            )

    for function_id, function_name in enumerate(
        (
            "rsc_validate_stocktake_technician_personal_location_0019",
            "rsc_preserve_stocktake_personal_location_0020",
            "rsc_stocktake_round_assignment_valid_0021",
            "rsc_validate_stocktake_count_line_insert_0021",
            "rsc_validate_stocktake_observation_insert_0021",
            "rsc_validate_stocktake_scope_completion_insert_0021",
            "rsc_validate_stocktake_recount_case_0021",
            "rsc_validate_stocktake_scope_region_owner_0025",
        ),
        start=19,
    ):
        for field, value in (
            ("owner_name", "star_oam_bootstrap"),
            ("can_execute", True),
        ):
            row = {
                "function_id": function_id,
                "function_name": function_name,
                "owner_name": "star_oam_migrator",
                "can_execute": False,
            }
            row[field] = value
            with pytest.raises(DatabaseSecurityBoundaryError, match=function_name):
                _assert_runtime_function_acl(
                    [row],
                    expected_migration_role="star_oam_migrator",
                )


def test_sensitive_stocktake_trigger_allowlist_matches_migration_catalog(
) -> None:
    assert set(EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS) == {
        "trg_stock_locations_stocktake_personal_continuity_0020",
        "trg_stocktake_count_lines_submitted_immutable_0010",
        "trg_stocktake_count_lines_immutable_0011",
        "trg_stocktake_count_lines_assignment_0021",
        "trg_stocktake_count_observations_immutable_0011",
        "trg_stocktake_count_observations_assignment_0021",
        "trg_stocktake_scope_count_completions_immutable_0011",
        "trg_stocktake_scope_completions_assignment_0021",
        POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019,
        "trg_stocktake_recount_scope_assignments_validate_0018",
        "trg_stocktake_recount_scope_assignments_immutable_0018",
        "trg_stocktake_recount_scope_assignments_immutable_truncate_0018",
        "trg_stocktake_recount_graph_assignment_0018",
        POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019,
    }
    assert {
        name: expected[2]
        for name, expected in EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS.items()
    } == {
        "trg_stock_locations_stocktake_personal_continuity_0020": "A",
        "trg_stocktake_count_lines_submitted_immutable_0010": "O",
        "trg_stocktake_count_lines_immutable_0011": "O",
        "trg_stocktake_count_lines_assignment_0021": "A",
        "trg_stocktake_count_observations_immutable_0011": "O",
        "trg_stocktake_count_observations_assignment_0021": "A",
        "trg_stocktake_scope_count_completions_immutable_0011": "O",
        "trg_stocktake_scope_completions_assignment_0021": "A",
        POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019: "A",
        "trg_stocktake_recount_scope_assignments_validate_0018": "A",
        "trg_stocktake_recount_scope_assignments_immutable_0018": "A",
        "trg_stocktake_recount_scope_assignments_immutable_truncate_0018": "A",
        "trg_stocktake_recount_graph_assignment_0018": "A",
        POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019: "A",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("table_name", "stocktake_tasks"),
        ("function_name", "unsafe_trigger_function"),
        ("function_schema", "attacker_schema"),
        ("enabled", "D"),
        ("trigger_type", 0),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ],
)
def test_sensitive_stocktake_trigger_allowlist_rejects_catalog_drift(
    field: str,
    value: object,
) -> None:
    triggers = _valid_stocktake_recount_triggers()
    target = next(
        row
        for row in triggers
        if row["trigger_name"]
        == "trg_stock_locations_stocktake_personal_continuity_0020"
    )
    target[field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=_valid_stocktake_recount_constraints(),
            indexes=_valid_stocktake_recount_indexes(),
            triggers=triggers,
        )


def test_sensitive_stocktake_trigger_allowlist_rejects_extra_or_duplicate(
) -> None:
    extra = {
        "trigger_name": "trg_stock_locations_unapproved_extra",
        "table_name": "stock_locations",
        "function_name": "rsc_unapproved_stock_location_rewrite",
        "function_schema": "public",
        "enabled": "A",
        "trigger_type": 19,
        "is_constraint_trigger": False,
        "is_deferrable": False,
        "is_initially_deferred": False,
        "has_when_clause": False,
        "has_column_filter": False,
    }
    for unexpected in (
        extra,
        dict(_valid_stocktake_recount_triggers()[0]),
    ):
        triggers = _valid_stocktake_recount_triggers()
        triggers.append(unexpected)
        with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
            _assert_stocktake_recount_schema(
                columns=_valid_stocktake_recount_columns(),
                constraints=_valid_stocktake_recount_constraints(),
                indexes=_valid_stocktake_recount_indexes(),
                triggers=triggers,
            )


def _valid_audit_head_rows() -> list[dict[str, object]]:
    return [
        {
            "id": uuid.UUID(head_id),
            "stream_key": stream_key,
            "version": 0,
            "last_event_id": None,
            "last_hash": None,
            "bound_event_id": None,
            "bound_event_hash": None,
        }
        for stream_key, head_id in sorted(EXPECTED_AUDIT_HEAD_IDS.items())
    ]


def test_audit_head_guard_requires_fixed_set_and_bound_last_event() -> None:
    _assert_fixed_audit_heads(_valid_audit_head_rows())

    rows = _valid_audit_head_rows()
    rows[0]["id"] = uuid.uuid4()
    with pytest.raises(DatabaseSecurityBoundaryError, match=r"\.id"):
        _assert_fixed_audit_heads(rows)

    rows = _valid_audit_head_rows()
    event_id = uuid.uuid4()
    rows[0].update(
        version=1,
        last_event_id=event_id,
        last_hash="a" * 64,
        bound_event_id=None,
        bound_event_hash=None,
    )
    with pytest.raises(DatabaseSecurityBoundaryError, match="event_binding"):
        _assert_fixed_audit_heads(rows)


def _one_event_audit_graph() -> tuple[
    list[dict[str, object]], list[dict[str, object]]
]:
    heads = _valid_audit_head_rows()
    event_id = uuid.uuid4()
    occurred_at = datetime(2026, 8, 31, 8, 30, tzinfo=timezone.utc)
    event: dict[str, object] = {
        "id": event_id,
        "stream_key": "authorization",
        "stream_version": 1,
        "actor_user_id": None,
        "action": "role.assignment.created",
        "aggregate_type": "role_assignment",
        "aggregate_id": "assignment-1",
        "before_jsonb": None,
        "after_jsonb": {"status": "active"},
        "request_id": "audit-graph-test-request",
        "previous_hash": None,
        "occurred_at": occurred_at,
    }
    event_hash = calculate_audit_event_hash(
        stream_key="authorization",
        event_id=event_id,
        actor_user_id=None,
        action=str(event["action"]),
        aggregate_type=str(event["aggregate_type"]),
        aggregate_id=str(event["aggregate_id"]),
        before_jsonb=None,
        after_jsonb={"status": "active"},
        request_id=str(event["request_id"]),
        previous_hash=None,
        occurred_at=occurred_at,
    )
    event["event_hash"] = event_hash
    authorization_head = next(
        row for row in heads if row["stream_key"] == "authorization"
    )
    authorization_head.update(
        version=1,
        last_event_id=event_id,
        last_hash=event_hash,
        bound_event_id=event_id,
        bound_event_hash=event_hash,
    )
    return heads, [event]


def test_complete_audit_graph_accepts_every_event_owned_by_one_fixed_stream() -> None:
    heads, events = _one_event_audit_graph()
    _assert_complete_audit_graph(heads=heads, events=events)


def test_complete_audit_graph_rejects_an_orphan_even_when_heads_are_valid() -> None:
    heads, events = _one_event_audit_graph()
    orphan = dict(events[0])
    orphan["id"] = uuid.uuid4()
    orphan["event_hash"] = "b" * 64
    events.append(orphan)
    with pytest.raises(DatabaseSecurityBoundaryError, match="audit graph"):
        _assert_complete_audit_graph(heads=heads, events=events)


def test_complete_audit_graph_rejects_canonical_payload_tampering() -> None:
    heads, events = _one_event_audit_graph()
    events[0]["after_jsonb"] = {"status": "revoked"}
    with pytest.raises(DatabaseSecurityBoundaryError, match="audit graph"):
        _assert_complete_audit_graph(heads=heads, events=events)


def test_complete_audit_graph_rejects_missing_predecessor() -> None:
    heads, events = _one_event_audit_graph()
    authorization_head = next(
        row for row in heads if row["stream_key"] == "authorization"
    )
    authorization_head["version"] = 2
    with pytest.raises(DatabaseSecurityBoundaryError, match="audit graph"):
        _assert_complete_audit_graph(heads=heads, events=events)


@pytest.mark.parametrize(
    ("field", "value"),
    [("stream_key", "inventory"), ("stream_version", 2)],
)
def test_complete_audit_graph_rejects_persisted_coordinate_mismatch(
    field: str,
    value: object,
) -> None:
    heads, events = _one_event_audit_graph()
    events[0][field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match="audit graph"):
        _assert_complete_audit_graph(heads=heads, events=events)
