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
    EXPECTED_KMS_DATA_KEY_PIN_COLUMNS,
    EXPECTED_KMS_DATA_KEY_PIN_CONSTRAINTS,
    EXPECTED_KMS_DATA_KEY_PIN_INDEXES,
    EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS,
    EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS,
    EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES,
    EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS,
    EXPECTED_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX,
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
    EXPECTED_SMS_DISPATCH_COLUMNS,
    EXPECTED_SMS_DISPATCH_CONSTRAINTS,
    EXPECTED_SMS_DISPATCH_NONINHERIT_CONSTRAINTS,
    EXPECTED_SMS_DISPATCH_INDEXES,
    EXPECTED_SMS_DISPATCH_TRIGGERS,
    EXPECTED_STOCKTAKE_RECOUNT_COLUMNS,
    EXPECTED_STOCKTAKE_RECOUNT_CONSTRAINTS,
    EXPECTED_STOCKTAKE_RECOUNT_INDEXES,
    EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS,
    EXPECTED_STOCKTAKE_SCOPE_TRIGGERS,
    EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS,
    POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019,
    POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037,
    POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019,
    OPENING_COMMIT_TRIGGER_NAMES,
    OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256,
    OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS,
    OAM_SYNC_RUNTIME_FUNCTION_SHAPES,
    OAM_SYNC_RUNTIME_FUNCTIONS,
    MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256,
    MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS,
    MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS,
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
    _AUDIT_TRIGGER_SQL,
    _FUNCTION_ACL_SQL,
    _NONOPENING_STOCKTAKE_CLOSE_TRIGGER_SQL,
    _MATERIAL_REQUEST_CANCELLATION_TRIGGER_SQL,
    _MATERIAL_REQUEST_APPROVAL_TRIGGER_SQL,
    _MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX_SQL,
    _OPENING_TERMINAL_TRIGGER_SQL,
    _OPENING_TERMINAL_INDEX_SQL,
    _RECONCILIATION_CONSTRAINT_SQL,
    _RECONCILIATION_PARTIAL_INDEX_SQL,
    _RECONCILIATION_TRIGGER_SQL,
    _SMS_DISPATCH_ROLE_ACCESS_SQL,
    _STOCKTAKE_RECOUNT_TRIGGER_SQL,
    _STOCKTAKE_SCOPE_TRIGGER_SQL,
    _STOCKTAKE_SENSITIVE_TRIGGER_SQL,
    _assert_production_database_evidence,
    _assert_audit_stream_schema,
    _assert_audit_trigger_guards,
    _assert_complete_audit_graph,
    _assert_fixed_audit_heads,
    _assert_formal_file_guards,
    _assert_kms_data_key_pin_guards,
    _assert_material_request_approval_guards,
    _assert_material_request_cancellation_guards,
    _assert_material_request_command_recovery_index,
    _assert_nonopening_stocktake_close_guards,
    _assert_runtime_function_acl,
    _assert_runtime_column_acl,
    _assert_runtime_sequence_acl,
    _assert_runtime_table_acl,
    _assert_opening_terminal_triggers,
    _assert_opening_terminal_index,
    _assert_reconciliation_schema,
    _assert_reconciliation_triggers,
    _assert_sms_dispatch_guards,
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
NONOPENING_STOCKTAKE_REVIEW_MIGRATION_0032 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0032_nonopening_stocktake_review_recount.py"
)
STOCKTAKE_COUNT_LEDGER_MIGRATION_0033 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0033_stocktake_count_ledger_boundary.py"
)
STOCKTAKE_RECOUNT_SCOPE_MIGRATION_0034 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0034_stocktake_recount_selected_scope_submission.py"
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
MATERIAL_REQUEST_CANCELLATION_MIGRATION_0037 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0037_material_request_cancellation_boundary.py"
)
NONOPENING_STOCKTAKE_CLOSE_MIGRATION_0038 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0038_nonopening_stocktake_close_reconciliation.py"
)
SMS_DISPATCH_MIGRATION_0041 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260902_0041_sms_dispatch_ownership.py"
)
MATERIAL_REQUEST_WORK_ORDER_LOCK_MIGRATION_0042 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260902_0042_material_request_work_order_lock.py"
)
MATERIAL_REQUEST_APPROVAL_ACTIVATION_MIGRATION_0045 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0045_material_request_approval_activation.py"
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
        "session_role_name": "star_oam_api",
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
        | {
            "document_attachments",
            "kms_data_key_pins",
            "sms_challenge_dispatches",
        }
        | material_request_read_tables
        | stocktake_close_read_tables
    )
    assert RUNTIME_INSERT_TABLES - set(values["API_INSERT_TABLES"]) == (
        safe_posting_tables
        | {"document_attachments", "files", "sms_challenge_dispatches"}
        | material_request_insert_tables
        | stocktake_close_insert_tables
    )
    assert set(values["API_READ_TABLES"]) <= RUNTIME_READ_TABLES
    assert set(values["API_INSERT_TABLES"]) <= RUNTIME_INSERT_TABLES
    assert set(values["API_UPDATE_TABLES"]) - {"login_challenges"} == (
        RUNTIME_UPDATE_TABLES
    )
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
        "login_challenges": {
            "attempts",
            "status",
            "provider_reference",
            "verified_at",
            "consumed_at",
        },
        "sms_challenge_dispatches": {
            "status",
            "owner_token_hash",
            "provider_reference",
            "claimed_at",
            "lease_expires_at",
            "accepted_at",
            "uncertain_at",
            "expired_at",
        },
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


def test_0032_review_lock_runtime_manifest_and_function_body_are_exact() -> None:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0032_security_manifest",
        NONOPENING_STOCKTAKE_REVIEW_MIGRATION_0032,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    coordinate = (migration.PG_LOCK_FUNCTION, "uuid, uuid")
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


def test_0039_material_request_command_recovery_index_is_exact_and_rejects_drift(
) -> None:
    expected = EXPECTED_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX
    row: dict[str, object] = {
        "index_name": expected["name"],
        "table_name": expected["table"],
        "access_method": "btree",
        "is_unique": True,
        "is_valid": True,
        "is_ready": True,
        "is_live": True,
        "key_columns": list(expected["columns"]),
        "predicate": (
            "stream_key = 'material_request'::character varying AND "
            "action = ANY (ARRAY['material_request.withdraw'::character varying, "
            "'material_request.cancel'::character varying])"
        ),
    }
    _assert_material_request_command_recovery_index([row])
    assert str(expected["name"]) in str(
        _MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX_SQL
    )

    for field, value in (
        ("is_unique", False),
        ("is_valid", False),
        ("key_columns", ["stream_key", "request_id"]),
        (
            "predicate",
            "stream_key = 'material_request' AND "
            "action IN ('material_request.withdraw', "
            "'material_request.cancel', 'material_request.submit')",
        ),
        (
            "predicate",
            "stream_key = 'material_request' AND "
            "(action IN ('material_request.withdraw', "
            "'material_request.cancel') OR request_id IS NOT NULL)",
        ),
        (
            "predicate",
            "stream_key = 'material_request' AND "
            "action IN ('material_request.withdraw', "
            "'material_request.cancel') AND length(request_id) > 0",
        ),
        (
            "predicate",
            "stream_key = 'material_request.withdraw' AND "
            "action IN ('material_request', 'material_request.cancel')",
        ),
        (
            "predicate",
            "stream_key = 'material_request' AND action = ANY "
            "((ARRAY['material_request.withdraw', "
            "'material_request.cancel'])[1:1])",
        ),
        (
            "predicate",
            "stream_key = 'material_request::text'::text AND "
            "action IN ('material_request.withdraw'::text, "
            "'material_request.cancel'::text)",
        ),
        (
            "predicate",
            "stream_key = 'material_request'::text AND "
            "action IN ('material_request.withdraw::varchar'::text, "
            "'material_request.cancel'::text)",
        ),
        (
            "predicate",
            "stream_key = 'material\"_request' AND "
            "action IN ('material_request.withdraw', "
            "'material_request.cancel')",
        ),
        (
            "predicate",
            "stream_key = 'MATERIAL_REQUEST' AND "
            "action IN ('material_request.withdraw', "
            "'material_request.cancel')",
        ),
        (
            "predicate",
            "stream_key = 'material_request' AND "
            "action IN ('MATERIAL_REQUEST.WITHDRAW', "
            "'MATERIAL_REQUEST.CANCEL')",
        ),
    ):
        drifted = dict(row)
        drifted[field] = value
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="command recovery index",
        ):
            _assert_material_request_command_recovery_index([drifted])

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="command recovery index",
    ):
        _assert_material_request_command_recovery_index([])


def _valid_kms_data_key_pin_catalog():
    columns = [
        {
            "relation_kind": "r",
            "persistence": "p",
            "row_security": False,
            "force_row_security": False,
            "ordinal_position": ordinal,
            "column_name": name,
            "data_type": data_type,
            "is_not_null": True,
            "identity_kind": "",
            "generated_kind": "",
            "default_expression": None,
        }
        for ordinal, (name, data_type) in enumerate(
            EXPECTED_KMS_DATA_KEY_PIN_COLUMNS,
            start=1,
        )
    ]
    definitions = {
        "ck_kms_data_key_pins_purpose_0040": (
            "CHECK (purpose IN ('authentication_idempotency', "
            "'material_request_contact'))"
        ),
        "ck_kms_data_key_pins_version_0040": (
            "CHECK (application_key_version BETWEEN 1 AND 2147483647)"
        ),
        "ck_kms_data_key_pins_coordinates_0040": (
            "CHECK (kms_key_id = trim(kms_key_id) AND "
            "kms_key_version_id = trim(kms_key_version_id) AND "
            "length(kms_key_id) BETWEEN 3 AND 256 AND "
            "length(kms_key_version_id) BETWEEN 8 AND 128)"
        ),
    }
    hash_expression = "ciphertext_sha256"
    for character in "0123456789abcdef":
        hash_expression = f"replace({hash_expression}, '{character}', '')"
    definitions["ck_kms_data_key_pins_sha256_0040"] = (
        "CHECK (length(ciphertext_sha256) = 64 AND "
        f"length({hash_expression}) = 0)"
    )
    constraints = [
        {
            "constraint_name": name,
            "constraint_type": expected["type"],
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": expected["no_inherit"],
            "is_local": True,
            "inheritance_count": 0,
            "parent_constraint_id": 0,
            "definition": definitions.get(name, "PRIMARY OR UNIQUE"),
            "constrained_columns": list(expected["columns"]),
            "backing_index_name": expected["backing_index"],
        }
        for name, expected in sorted(
            EXPECTED_KMS_DATA_KEY_PIN_CONSTRAINTS.items()
        )
    ]
    indexes = [
        {
            "index_name": name,
            "owner_name": "star_oam_migrator",
            "constraint_name": expected["constraint"],
            "access_method": "btree",
            "is_unique": True,
            "is_primary": expected["primary"],
            "is_exclusion": False,
            "is_immediate": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "nulls_not_distinct": False,
            "key_attribute_count": len(expected["columns"]),
            "total_attribute_count": len(expected["columns"]),
            "has_expressions": False,
            "key_columns": list(expected["columns"]),
            "predicate": None,
        }
        for name, expected in sorted(EXPECTED_KMS_DATA_KEY_PIN_INDEXES.items())
    ]
    triggers = [
        {
            "trigger_name": name,
            "table_name": expected[0],
            "function_name": expected[1],
            "function_schema": "public",
            "enabled": expected[2],
            "trigger_type": expected[3],
            "is_constraint_trigger": False,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, expected in sorted(EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS.items())
    ]
    acl_common = {
        "owner_name": "star_oam_migrator",
        "backup_role_exists": True,
        "edge_role_exists": True,
        "is_grantable": False,
    }
    table_acl = [
        {
            **acl_common,
            "grantee_name": "star_oam_api",
            "privilege_type": "SELECT",
        },
        {
            **acl_common,
            "grantee_name": "star_oam_backup",
            "privilege_type": "SELECT",
        },
    ]
    function_acl = [
        {
            **acl_common,
            "grantee_name": None,
            "privilege_type": None,
            "is_grantable": None,
        }
    ]
    return triggers, columns, constraints, indexes, table_acl, function_acl


def test_0040_kms_data_key_pin_catalog_guard_is_exact_and_rejects_drift() -> None:
    (
        triggers,
        columns,
        constraints,
        indexes,
        table_acl,
        function_acl,
    ) = _valid_kms_data_key_pin_catalog()
    _assert_kms_data_key_pin_guards(
        triggers=triggers,
        columns=columns,
        constraints=constraints,
        indexes=indexes,
        table_acl=table_acl,
        function_acl=function_acl,
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )

    sha_constraint_index = next(
        index
        for index, row in enumerate(constraints)
        if row["constraint_name"] == "ck_kms_data_key_pins_sha256_0040"
    )
    sha_literal_drift = str(
        constraints[sha_constraint_index]["definition"]
    ).replace("'0'", "'0::text'::text", 1)
    mutations = (
        ("triggers", 0, "enabled", "O"),
        ("columns", 0, "row_security", True),
        ("columns", 5, "default_expression", "now()"),
        (
            "constraints",
            next(
                index
                for index, row in enumerate(constraints)
                if row["constraint_name"]
                == "ck_kms_data_key_pins_purpose_0040"
            ),
            "definition",
            "CHECK (purpose IN ('authentication_idempotency', "
            "'material_request_contact') OR TRUE)",
        ),
        (
            "constraints",
            next(
                index
                for index, row in enumerate(constraints)
                if row["constraint_name"]
                == "ck_kms_data_key_pins_purpose_0040"
            ),
            "definition",
            "CHECK (purpose IN ('authentication_idempotency::text'::text, "
            "'material_request_contact'::text))",
        ),
        (
            "constraints",
            sha_constraint_index,
            "definition",
            sha_literal_drift,
        ),
        (
            "constraints",
            next(
                index
                for index, row in enumerate(constraints)
                if row["constraint_name"]
                == "ck_kms_data_key_pins_purpose_0040"
            ),
            "definition",
            "CHECK (purpose IN ('authentication_(idempotency)'::text, "
            "'material_request_contact'::text))",
        ),
        ("indexes", 0, "owner_name", "star_oam_api"),
        ("indexes", 0, "total_attribute_count", 4),
        ("table_acl", 0, "privilege_type", "INSERT"),
        ("function_acl", 0, "grantee_name", "star_oam_edge"),
    )
    for collection_name, row_index, field, value in mutations:
        current = _valid_kms_data_key_pin_catalog()
        collections = dict(
            zip(
                (
                    "triggers",
                    "columns",
                    "constraints",
                    "indexes",
                    "table_acl",
                    "function_acl",
                ),
                current,
            )
        )
        collections[collection_name][row_index][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="KMS data-key pin"):
            _assert_kms_data_key_pin_guards(
                triggers=collections["triggers"],
                columns=collections["columns"],
                constraints=collections["constraints"],
                indexes=collections["indexes"],
                table_acl=collections["table_acl"],
                function_acl=collections["function_acl"],
                expected_runtime_role="star_oam_api",
                expected_migration_role="star_oam_migrator",
            )


def test_0040_kms_guard_accepts_postgresql16_trim_deparse() -> None:
    (
        triggers,
        columns,
        constraints,
        indexes,
        table_acl,
        function_acl,
    ) = _valid_kms_data_key_pin_catalog()
    coordinate = next(
        row
        for row in constraints
        if row["constraint_name"]
        == "ck_kms_data_key_pins_coordinates_0040"
    )
    coordinate["definition"] = (
        "CHECK (((kms_key_id)::text = TRIM(BOTH FROM "
        "(kms_key_id)::text)) AND ((kms_key_version_id)::text = "
        "TRIM(BOTH FROM (kms_key_version_id)::text)) AND "
        "(length((kms_key_id)::text) >= 3) AND "
        "(length((kms_key_id)::text) <= 256) AND "
        "(length((kms_key_version_id)::text) >= 8) AND "
        "(length((kms_key_version_id)::text) <= 128))"
    )

    _assert_kms_data_key_pin_guards(
        triggers=triggers,
        columns=columns,
        constraints=constraints,
        indexes=indexes,
        table_acl=table_acl,
        function_acl=function_acl,
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


def test_0040_kms_guard_reports_exact_index_field() -> None:
    (
        triggers,
        columns,
        constraints,
        indexes,
        table_acl,
        function_acl,
    ) = _valid_kms_data_key_pin_catalog()
    primary = next(
        row
        for row in indexes
        if row["index_name"] == "pk_kms_data_key_pins_coordinate_0040"
    )
    primary["is_immediate"] = False

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="pk_kms_data_key_pins_coordinate_0040\\.is_immediate",
    ):
        _assert_kms_data_key_pin_guards(
            triggers=triggers,
            columns=columns,
            constraints=constraints,
            indexes=indexes,
            table_acl=table_acl,
            function_acl=function_acl,
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def test_0040_kms_guard_reports_exact_constraint_field() -> None:
    (
        triggers,
        columns,
        constraints,
        indexes,
        table_acl,
        function_acl,
    ) = _valid_kms_data_key_pin_catalog()
    primary = next(
        row
        for row in constraints
        if row["constraint_name"]
        == "pk_kms_data_key_pins_coordinate_0040"
    )
    primary["is_no_inherit"] = False

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="pk_kms_data_key_pins_coordinate_0040\\.is_no_inherit",
    ):
        _assert_kms_data_key_pin_guards(
            triggers=triggers,
            columns=columns,
            constraints=constraints,
            indexes=indexes,
            table_acl=table_acl,
            function_acl=function_acl,
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def _load_sms_dispatch_migration_0041() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0041_sms_dispatch_security_manifest",
        SMS_DISPATCH_MIGRATION_0041,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _valid_sms_dispatch_catalog() -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    triggers = [
        {
            "trigger_name": name,
            "table_name": expected[0],
            "function_name": expected[1],
            "function_schema": "public",
            "enabled": expected[2],
            "trigger_type": expected[3],
            "is_constraint_trigger": False,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "has_when_clause": False,
            "has_column_filter": False,
        }
        for name, expected in sorted(EXPECTED_SMS_DISPATCH_TRIGGERS.items())
    ]
    columns = [
        {
            "relation_kind": "r",
            "persistence": "p",
            "row_security": False,
            "force_row_security": False,
            "ordinal_position": ordinal,
            "column_name": name,
            "data_type": data_type,
            "is_not_null": is_not_null,
            "identity_kind": "",
            "generated_kind": "",
            "default_expression": None,
        }
        for ordinal, (name, data_type, is_not_null) in enumerate(
            EXPECTED_SMS_DISPATCH_COLUMNS,
            start=1,
        )
    ]
    definitions = {
        "ck_sms_challenge_dispatches_status": (
            "CHECK (status IN ('prepared', 'sending', 'accepted', "
            "'uncertain', 'expired'))"
        ),
        "ck_sms_challenge_dispatches_request_sha256": (
            "CHECK (length(request_sha256) = 64)"
        ),
        "ck_sms_challenge_dispatches_mobile_hash": (
            "CHECK (length(mobile_hash) = 64)"
        ),
        "ck_sms_challenge_dispatches_owner_hash": (
            "CHECK (owner_token_hash IS NULL OR length(owner_token_hash) = 64)"
        ),
        "ck_sms_challenge_dispatches_state_evidence": (
            "CHECK ((status = 'prepared' AND owner_token_hash IS NULL "
            "AND claimed_at IS NULL AND lease_expires_at IS NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NULL "
            "AND expired_at IS NULL AND provider_reference IS NULL) OR "
            "(status = 'sending' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NULL "
            "AND expired_at IS NULL AND provider_reference IS NULL) OR "
            "(status = 'uncertain' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NOT NULL "
            "AND expired_at IS NULL AND provider_reference IS NULL) OR "
            "(status = 'accepted' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NOT NULL "
            "AND provider_reference IS NOT NULL) OR "
            "(status = 'expired' AND owner_token_hash IS NOT NULL "
            "AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND accepted_at IS NULL AND uncertain_at IS NOT NULL "
            "AND expired_at IS NOT NULL AND provider_reference IS NULL))"
        ),
        "ck_sms_challenge_dispatches_lease_order": (
            "CHECK (lease_expires_at IS NULL OR lease_expires_at >= claimed_at)"
        ),
        "ck_sms_challenge_dispatches_accepted_order": (
            "CHECK (accepted_at IS NULL OR accepted_at >= claimed_at)"
        ),
        "ck_sms_challenge_dispatches_uncertain_order": (
            "CHECK (uncertain_at IS NULL OR uncertain_at >= claimed_at)"
        ),
        "ck_sms_challenge_dispatches_expired_order": (
            "CHECK (expired_at IS NULL OR expired_at >= claimed_at)"
        ),
        "pk_sms_challenge_dispatches_0041": (
            "PRIMARY KEY (challenge_id)"
        ),
        "fk_sms_challenge_dispatches_challenge_0041": (
            "FOREIGN KEY (challenge_id) REFERENCES login_challenges(id) "
            "ON DELETE RESTRICT"
        ),
    }
    constraints = [
        {
            "constraint_name": name,
            "constraint_type": constraint_type,
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": (
                name in EXPECTED_SMS_DISPATCH_NONINHERIT_CONSTRAINTS
            ),
            "is_local": True,
            "inheritance_count": 0,
            "parent_constraint_id": 0,
            "definition": definitions[name],
            "constrained_columns": (
                ["challenge_id"] if constraint_type in {"p", "f"} else []
            ),
            "referenced_table": (
                "login_challenges" if constraint_type == "f" else None
            ),
            "referenced_columns": (
                ["id"] if constraint_type == "f" else []
            ),
            "delete_action": "r" if constraint_type == "f" else " ",
        }
        for name, constraint_type in sorted(
            EXPECTED_SMS_DISPATCH_CONSTRAINTS.items()
        )
    ]
    indexes = [
        {
            "index_name": name,
            "owner_name": "star_oam_migrator",
            "access_method": "btree",
            "is_unique": expected["unique"],
            "is_primary": expected["primary"],
            "is_exclusion": False,
            "is_immediate": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "nulls_not_distinct": False,
            "key_attribute_count": len(expected["columns"]),
            "total_attribute_count": len(expected["columns"]),
            "has_expressions": False,
            "key_columns": list(expected["columns"]),
            "predicate": (
                "status IN ('sending', 'uncertain')"
                if expected.get("predicate_literals") is not None
                else expected.get("predicate")
            ),
        }
        for name, expected in sorted(EXPECTED_SMS_DISPATCH_INDEXES.items())
    ]
    acl_common = {
        "owner_name": "star_oam_migrator",
        "backup_role_exists": True,
        "edge_role_exists": True,
        "is_grantable": False,
    }
    table_acl = [
        {
            **acl_common,
            "grantee_name": "star_oam_api",
            "privilege_type": "INSERT",
        },
        {
            **acl_common,
            "grantee_name": "star_oam_api",
            "privilege_type": "SELECT",
        },
        {
            **acl_common,
            "grantee_name": "star_oam_backup",
            "privilege_type": "SELECT",
        },
    ]
    column_acl = [
        {
            **acl_common,
            "column_name": column_name,
            "grantee_name": "star_oam_api",
            "privilege_type": "UPDATE",
        }
        for column_name in sorted(
            RUNTIME_UPDATE_COLUMNS["sms_challenge_dispatches"]
        )
    ]
    role_access_common = {
        "role_inherits": True,
        "can_select": False,
        "can_write": False,
        "is_member_of_any_role": False,
        "has_any_nonsuper_member": False,
        "can_set_select_role": False,
        "can_set_write_role": False,
        "can_admin_select_role": False,
        "can_admin_write_role": False,
        "inherited_by_other_role": False,
        "settable_by_other_role": False,
        "administered_by_other_role": False,
        "capability_role_has_admin_member": False,
    }
    role_access = [
        {
            **role_access_common,
            "role_label": "backup",
            "role_name": "star_oam_backup",
            "role_exists": True,
            "can_select": True,
        },
        {
            **role_access_common,
            "role_label": "edge",
            "role_name": "star_oam_edge",
            "role_exists": True,
        },
        {
            **role_access_common,
            "role_label": "migration",
            "role_name": "star_oam_migrator",
            "role_exists": True,
            "can_select": True,
            "can_write": True,
        },
        {
            **role_access_common,
            "role_label": "runtime",
            "role_name": "star_oam_api",
            "role_exists": True,
            "can_select": True,
            "can_write": True,
        },
    ]
    return (
        triggers,
        columns,
        constraints,
        indexes,
        table_acl,
        column_acl,
        role_access,
    )


def test_0041_sms_dispatch_manifest_guard_body_and_acl_are_exact() -> None:
    migration = _load_sms_dispatch_migration_0041()
    assert migration.down_revision == "20260901_0040"
    coordinate = (migration.PG_GUARD_FUNCTION, "")
    function_sql = migration._postgresql_guard_function_sql()
    function_body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    expected_hash = "42201b13bb8998ea8522b190bfed67bbc7faab4c7bc355b4a7f5c2c13cd59993"
    assert hashlib.sha256(function_body.encode("utf-8")).hexdigest() == (
        expected_hash
    )
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == expected_hash
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
    assert coordinate not in RUNTIME_EXECUTE_FUNCTIONS

    assert migration.TABLE_NAME in RUNTIME_READ_TABLES
    assert migration.TABLE_NAME in RUNTIME_INSERT_TABLES
    assert migration.TABLE_NAME not in RUNTIME_UPDATE_TABLES
    assert migration.TABLE_NAME not in RUNTIME_DELETE_TABLES
    assert RUNTIME_UPDATE_COLUMNS[migration.TABLE_NAME] == {
        "status",
        "owner_token_hash",
        "provider_reference",
        "claimed_at",
        "lease_expires_at",
        "accepted_at",
        "uncertain_at",
        "expired_at",
    }
    source = SMS_DISPATCH_MIGRATION_0041.read_text(encoding="utf-8")
    assert source.count("REVOKE EXECUTE ON FUNCTION") == 4
    assert "GRANT EXECUTE ON FUNCTION" not in source
    assert (
        "GRANT SELECT, INSERT ON TABLE public.{TABLE_NAME} "
        "TO {PRODUCTION_API_ROLE}"
    ) in source
    assert (
        "GRANT UPDATE ({dispatch_update_columns}) ON TABLE "
        "public.{TABLE_NAME} TO {PRODUCTION_API_ROLE}"
    ) in source
    assert EXPECTED_SMS_DISPATCH_NONINHERIT_CONSTRAINTS == {
        "pk_sms_challenge_dispatches_0041",
        "fk_sms_challenge_dispatches_challenge_0041",
    }


def test_0041_sms_dispatch_catalog_guard_rejects_each_drift() -> None:
    (
        triggers,
        columns,
        constraints,
        indexes,
        table_acl,
        column_acl,
        role_access,
    ) = _valid_sms_dispatch_catalog()
    _assert_sms_dispatch_guards(
        triggers=triggers,
        columns=columns,
        constraints=constraints,
        indexes=indexes,
        table_acl=table_acl,
        column_acl=column_acl,
        role_access=role_access,
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )

    mutations = (
        ("triggers", 0, "enabled", "O"),
        ("triggers", 1, "function_schema", "attacker"),
        ("columns", 0, "ordinal_position", 2),
        ("columns", 4, "data_type", "text"),
        ("columns", 5, "is_not_null", True),
        (
            "constraints",
            next(
                index
                for index, row in enumerate(constraints)
                if row["constraint_name"]
                == "ck_sms_challenge_dispatches_status"
            ),
            "definition",
            "CHECK (status = 'prepared')",
        ),
        (
            "constraints",
            next(
                index
                for index, row in enumerate(constraints)
                if row["constraint_name"]
                == "fk_sms_challenge_dispatches_challenge_0041"
            ),
            "delete_action",
            "c",
        ),
        ("indexes", 0, "owner_name", "star_oam_api"),
        (
            "indexes",
            next(
                index
                for index, row in enumerate(indexes)
                if row["index_name"]
                == "uq_sms_challenge_dispatches_provider_reference"
            ),
            "predicate",
            "provider_reference IS NULL",
        ),
        (
            "indexes",
            next(
                index
                for index, row in enumerate(indexes)
                if row["index_name"]
                == "uq_sms_challenge_dispatches_unresolved_mobile"
            ),
            "predicate",
            "status IN ('sending', 'uncertain', 'prepared')",
        ),
        ("table_acl", 0, "owner_name", "star_oam_api"),
        ("table_acl", 0, "grantee_name", "star_oam_edge"),
        ("table_acl", 0, "is_grantable", True),
        ("column_acl", 0, "column_name", "request_sha256"),
        ("column_acl", 0, "grantee_name", "PUBLIC"),
        ("column_acl", 0, "privilege_type", "SELECT"),
        ("column_acl", 0, "is_grantable", True),
        ("role_access", 0, "role_name", "attacker"),
        ("role_access", 0, "can_write", True),
    )
    collection_names = (
        "triggers",
        "columns",
        "constraints",
        "indexes",
        "table_acl",
        "column_acl",
        "role_access",
    )
    for collection_name, row_index, field, value in mutations:
        current = _valid_sms_dispatch_catalog()
        collections = dict(zip(collection_names, current))
        collections[collection_name][row_index][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
            _assert_sms_dispatch_guards(
                triggers=collections["triggers"],
                columns=collections["columns"],
                constraints=collections["constraints"],
                indexes=collections["indexes"],
                table_acl=collections["table_acl"],
                column_acl=collections["column_acl"],
                role_access=collections["role_access"],
                expected_runtime_role="star_oam_api",
                expected_migration_role="star_oam_migrator",
            )


@pytest.mark.parametrize(
    ("constraint_name", "drifted_no_inherit"),
    [
        ("pk_sms_challenge_dispatches_0041", False),
        ("fk_sms_challenge_dispatches_challenge_0041", False),
        ("ck_sms_challenge_dispatches_status", True),
    ],
)
def test_0041_sms_dispatch_constraint_inheritance_drift_is_rejected(
    constraint_name: str,
    drifted_no_inherit: bool,
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    constraint = next(
        row
        for row in catalog[2]
        if row["constraint_name"] == constraint_name
    )
    constraint["is_no_inherit"] = drifted_no_inherit

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match=rf"{constraint_name}\.is_no_inherit",
    ):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


@pytest.mark.parametrize(
    "predicate",
    [
        "status NOT IN ('sending', 'uncertain')",
        "status IN ('sending', 'uncertain') OR TRUE",
        "status IN ('uncertain', 'sending')",
        "ANY (ARRAY['sending', 'uncertain']) = status",
        "'sending' = status OR status = 'uncertain'",
        "status IN ('sending', 'uncertain', 'prepared')",
        (
            "status IN ('sending', 'uncertain') "
            "AND lease_expires_at > now()"
        ),
    ],
)
def test_0041_sms_dispatch_unresolved_predicate_rejects_semantic_drift(
    predicate: str,
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    unresolved = next(
        row
        for row in catalog[3]
        if row["index_name"]
        == "uq_sms_challenge_dispatches_unresolved_mobile"
    )
    unresolved["predicate"] = predicate
    with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def test_0041_sms_dispatch_unresolved_predicate_accepts_postgresql_any_form(
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    unresolved = next(
        row
        for row in catalog[3]
        if row["index_name"]
        == "uq_sms_challenge_dispatches_unresolved_mobile"
    )
    unresolved["predicate"] = (
        "(status)::text = ANY ((ARRAY['sending'::character varying, "
        "'uncertain'::character varying])::text[])"
    )
    _assert_sms_dispatch_guards(
        triggers=catalog[0],
        columns=catalog[1],
        constraints=catalog[2],
        indexes=catalog[3],
        table_acl=catalog[4],
        column_acl=catalog[5],
        role_access=catalog[6],
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


def test_0041_sms_dispatch_acl_rejects_missing_and_excess_grants() -> None:
    for collection_index in (4, 5):
        catalog = _valid_sms_dispatch_catalog()
        catalog[collection_index].pop()
        with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
            _assert_sms_dispatch_guards(
                triggers=catalog[0],
                columns=catalog[1],
                constraints=catalog[2],
                indexes=catalog[3],
                table_acl=catalog[4],
                column_acl=catalog[5],
                role_access=catalog[6],
                expected_runtime_role="star_oam_api",
                expected_migration_role="star_oam_migrator",
            )

    catalog = _valid_sms_dispatch_catalog()
    catalog[4].append(
        {
            **catalog[4][0],
            "grantee_name": "star_oam_edge",
            "privilege_type": "SELECT",
        }
    )
    with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )

    catalog = _valid_sms_dispatch_catalog()
    catalog[5].append(
        {
            **catalog[5][0],
            "grantee_name": "star_oam_backup",
        }
    )
    with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def test_0041_sms_dispatch_acl_allows_absent_optional_roles() -> None:
    catalog = _valid_sms_dispatch_catalog()
    catalog[4][:] = [
        row for row in catalog[4] if row["grantee_name"] != "star_oam_backup"
    ]
    for row in [*catalog[4], *catalog[5]]:
        row["backup_role_exists"] = False
        row["edge_role_exists"] = False
    for row in catalog[6]:
        if row["role_label"] in {"backup", "edge"}:
            row["role_exists"] = False
            row["role_inherits"] = False
            row["can_select"] = False
    _assert_sms_dispatch_guards(
        triggers=catalog[0],
        columns=catalog[1],
        constraints=catalog[2],
        indexes=catalog[3],
        table_acl=catalog[4],
        column_acl=catalog[5],
        role_access=catalog[6],
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


@pytest.mark.parametrize(
    ("role_label", "field"),
    [
        ("backup", "can_write"),
        ("backup", "is_member_of_any_role"),
        ("backup", "has_any_nonsuper_member"),
        ("backup", "can_set_select_role"),
        ("backup", "can_set_write_role"),
        ("backup", "can_admin_select_role"),
        ("backup", "can_admin_write_role"),
        ("backup", "inherited_by_other_role"),
        ("backup", "settable_by_other_role"),
        ("backup", "administered_by_other_role"),
        ("edge", "can_select"),
        ("edge", "can_write"),
        ("edge", "is_member_of_any_role"),
        ("edge", "can_set_select_role"),
        ("edge", "can_set_write_role"),
        ("edge", "can_admin_select_role"),
        ("edge", "can_admin_write_role"),
        ("runtime", "inherited_by_other_role"),
        ("runtime", "is_member_of_any_role"),
        ("runtime", "has_any_nonsuper_member"),
        ("runtime", "settable_by_other_role"),
        ("runtime", "can_set_select_role"),
        ("runtime", "can_set_write_role"),
        ("runtime", "can_admin_select_role"),
        ("runtime", "can_admin_write_role"),
        ("runtime", "administered_by_other_role"),
        ("migration", "inherited_by_other_role"),
        ("migration", "is_member_of_any_role"),
        ("migration", "has_any_nonsuper_member"),
        ("migration", "settable_by_other_role"),
        ("migration", "can_set_select_role"),
        ("migration", "can_set_write_role"),
        ("migration", "can_admin_select_role"),
        ("migration", "can_admin_write_role"),
        ("migration", "administered_by_other_role"),
        ("edge", "capability_role_has_admin_member"),
    ],
)
def test_0041_sms_dispatch_effective_role_access_rejects_closure_drift(
    role_label: str,
    field: str,
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    role_row = next(
        row for row in catalog[6] if row["role_label"] == role_label
    )
    role_row[field] = True
    with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def test_0041_sms_dispatch_effective_role_access_allows_safe_noinherit(
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    for row in catalog[6]:
        if row["role_label"] in {"backup", "edge"}:
            row["role_inherits"] = False
    _assert_sms_dispatch_guards(
        triggers=catalog[0],
        columns=catalog[1],
        constraints=catalog[2],
        indexes=catalog[3],
        table_acl=catalog[4],
        column_acl=catalog[5],
        role_access=catalog[6],
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


def test_0041_sms_dispatch_effective_role_access_rejects_admin_only_path(
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    edge = next(
        row for row in catalog[6] if row["role_label"] == "edge"
    )
    backup = next(
        row for row in catalog[6] if row["role_label"] == "backup"
    )
    edge["role_inherits"] = False
    edge["can_set_select_role"] = False
    edge["can_set_write_role"] = False
    edge["is_member_of_any_role"] = True
    edge["can_admin_select_role"] = True
    backup["has_any_nonsuper_member"] = True
    with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
    )


def test_0041_sms_dispatch_effective_role_access_rejects_mixed_member_path(
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    edge = next(
        row for row in catalog[6] if row["role_label"] == "edge"
    )
    backup = next(
        row for row in catalog[6] if row["role_label"] == "backup"
    )
    edge["role_inherits"] = False
    edge["is_member_of_any_role"] = True
    backup["has_any_nonsuper_member"] = True
    with pytest.raises(DatabaseSecurityBoundaryError, match="SMS dispatch"):
        _assert_sms_dispatch_guards(
            triggers=catalog[0],
            columns=catalog[1],
            constraints=catalog[2],
            indexes=catalog[3],
            table_acl=catalog[4],
            column_acl=catalog[5],
            role_access=catalog[6],
            expected_runtime_role="star_oam_api",
            expected_migration_role="star_oam_migrator",
        )


def test_0041_sms_dispatch_effective_role_access_allows_edge_members(
) -> None:
    catalog = _valid_sms_dispatch_catalog()
    edge = next(
        row for row in catalog[6] if row["role_label"] == "edge"
    )
    edge["has_any_nonsuper_member"] = True
    edge["administered_by_other_role"] = True
    _assert_sms_dispatch_guards(
        triggers=catalog[0],
        columns=catalog[1],
        constraints=catalog[2],
        indexes=catalog[3],
        table_acl=catalog[4],
        column_acl=catalog[5],
        role_access=catalog[6],
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


def test_0041_sms_dispatch_effective_role_query_uses_cycle_safe_closure(
) -> None:
    query = str(_SMS_DISPATCH_ROLE_ACCESS_SQL)
    assert "pg_has_role" in query
    assert "'USAGE'" in query
    assert "'SET'" in query
    assert query.count("'MEMBER'") == 2
    assert query.count("'MEMBER WITH ADMIN OPTION'") == 4
    assert "FROM pg_roles AS candidate_role" in query
    assert "FROM role_capabilities AS capability_role" in query
    assert "NOT candidate_role.rolsuper" in query
    assert "current_database_owner AS" in query
    assert "target_role.rolname = 'pg_database_owner'" in query
    assert "database_row.datname = current_database()" in query
    assert "pg_auth_members" not in query
    assert "WITH RECURSIVE" not in query


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
        ("session_role_name", "star_oam_bootstrap"),
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
    for coordinate in OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256:
        monkeypatch.setitem(
            OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256,
            coordinate,
            fixture_source_hash,
        )
    function_definitions = {
        **RUNTIME_EXECUTE_FUNCTIONS,
        **FORMAL_FILE_INTERNAL_FUNCTIONS,
        **OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS,
    }
    function_shapes = {
        **RUNTIME_FUNCTION_SHAPES,
        **FORMAL_FILE_INTERNAL_FUNCTION_SHAPES,
        **OAM_SYNC_RUNTIME_FUNCTION_SHAPES,
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
            "parallel_safety": "u",
            "is_leakproof": False,
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
            "edge_receiver_can_execute": (
                (function_name, argument_types)
                in OAM_SYNC_RUNTIME_FUNCTIONS
            ),
            "projector_can_execute": (
                (function_name, argument_types)
                in OAM_SYNC_RUNTIME_FUNCTIONS
            ),
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
        ("edge_receiver_can_execute", True),
        ("projector_can_execute", True),
    ):
        drifted = [row.copy() for row in allowed_rows]
        drifted[0][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="function"):
            _assert_runtime_function_acl(
                drifted,
                expected_migration_role="star_oam_migrator",
            )

    sync_index = next(
        index
        for index, row in enumerate(allowed_rows)
        if (row["function_name"], row["argument_types"])
        in OAM_SYNC_RUNTIME_FUNCTIONS
    )
    for field, value in (
        ("can_execute", True),
        ("edge_receiver_can_execute", False),
        ("projector_can_execute", False),
        ("unexpected_execute_grantee_count", 1),
        ("parallel_safety", "s"),
        ("is_leakproof", True),
    ):
        drifted = [row.copy() for row in allowed_rows]
        drifted[sync_index][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="function"):
            _assert_runtime_function_acl(
                drifted,
                expected_migration_role="star_oam_migrator",
            )


def test_0044_api_function_acl_exception_is_exact_and_non_grantable() -> None:
    sql = " ".join(str(_FUNCTION_ACL_SQL).split())

    for required in (
        "rsc_oam_rls_check_0044",
        "rsc_oam_runtime_binding_ready_0044",
        "edge_inbox",
        "star_oam_projector",
        "function_acl.is_grantable IS FALSE",
        "edge_receiver_can_execute",
        "projector_can_execute",
    ):
        assert required in sql
    assert "star_oam_api" not in sql


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


def test_0042_work_order_lock_is_in_exact_runtime_manifest() -> None:
    source = MATERIAL_REQUEST_WORK_ORDER_LOCK_MIGRATION_0042.read_text(
        encoding="utf-8"
    )
    coordinate = (
        "rsc_lock_material_request_work_order_reference_0042",
        "uuid",
    )
    assert RUNTIME_EXECUTE_FUNCTIONS[coordinate] == (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    )
    assert RUNTIME_FUNCTION_SHAPES[coordinate] == ("f", "void", False)
    assert RUNTIME_FUNCTION_BODY_SHA256[coordinate] == (
        "d889b397912e98e1b9c2ec1de03ada750f42df01803c87239b9d04a624221982"
    )
    assert set(RUNTIME_FUNCTION_SHAPES) == set(RUNTIME_EXECUTE_FUNCTIONS)
    assert set(RUNTIME_FUNCTION_BODY_SHA256) == set(RUNTIME_EXECUTE_FUNCTIONS)

    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0042_security_manifest",
        MATERIAL_REQUEST_WORK_ORDER_LOCK_MIGRATION_0042,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    rendered: list[str] = []
    migration.op = SimpleNamespace(execute=rendered.append)
    migration._create_postgresql_function()
    function_sql = rendered[0]
    function_body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(function_body.encode("utf-8")).hexdigest() == (
        RUNTIME_FUNCTION_BODY_SHA256[coordinate]
    )
    assert "FOR SHARE OF work_order" in function_sql
    assert "GET DIAGNOSTICS locked_count = ROW_COUNT" in function_sql
    assert "public.oam_work_orders" in function_sql
    assert "EXECUTE format" not in function_sql
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
    migration_source = ACL_MIGRATION.read_text(encoding="utf-8")
    file_snapshot_constraint = (
        "ck_opening_control_reconciliation_items_file_snapshot"
    )
    assert f'name="{file_snapshot_constraint}"' in migration_source
    assert file_snapshot_constraint in EXPECTED_RECONCILIATION_CONSTRAINTS
    assert (
        "ck_opening_control_reconciliation_items_evidence_snapshot"
        not in EXPECTED_RECONCILIATION_CONSTRAINTS
    )
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

    unexpected = _valid_audit_trigger_rows()[0].copy()
    unexpected["trigger_name"] = "trg_unapproved_audit_probe"
    with pytest.raises(DatabaseSecurityBoundaryError, match="trigger_set"):
        _assert_audit_trigger_guards(
            [*_valid_audit_trigger_rows(), unexpected]
        )


def test_audit_trigger_inventory_covers_cross_domain_manifests() -> None:
    cross_domain: dict[str, tuple[object, ...]] = {}

    opening_name = "trg_audit_events_opening_commit_0022"
    table_name, function_name, enabled, trigger_type = (
        EXPECTED_OPENING_TERMINAL_TRIGGERS[opening_name]
    )
    assert enabled == "A"
    cross_domain[opening_name] = (
        table_name,
        function_name,
        trigger_type,
        True,
        True,
        True,
    )

    for name, (table_name, function_name, enabled, trigger_type) in (
        EXPECTED_RECONCILIATION_TRIGGERS.items()
    ):
        if table_name != "audit_events":
            continue
        assert enabled == "A"
        cross_domain[name] = (
            table_name,
            function_name,
            trigger_type,
            False,
            False,
            False,
        )

    cancellation_name = "trg_audit_events_cancellation_graph_0037"
    table_name, function_name, enabled, trigger_type = (
        EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS[cancellation_name]
    )
    assert enabled == "A"
    cross_domain[cancellation_name] = (
        table_name,
        function_name,
        trigger_type,
        True,
        True,
        True,
    )

    nonopening_name = (
        "trg_audit_events_nonopening_stocktake_close_guard_0038"
    )
    (
        table_name,
        function_name,
        enabled,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
        has_when_clause,
    ) = EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS[nonopening_name]
    assert enabled == "A"
    assert has_when_clause is False
    cross_domain[nonopening_name] = (
        table_name,
        function_name,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    )

    approval_name = "trg_audit_events_approval_projection_0045"
    (
        table_name,
        function_name,
        enabled,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    ) = EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[approval_name]
    assert enabled == "A"
    cross_domain[approval_name] = (
        table_name,
        function_name,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    )

    assert set(cross_domain) == {
        "trg_audit_events_opening_commit_0022",
        "trg_reconciliation_audit_effect_guard_0026",
        "trg_reconciliation_audit_effect_no_truncate_0026",
        "trg_audit_events_cancellation_graph_0037",
        "trg_audit_events_nonopening_stocktake_close_guard_0038",
        "trg_audit_events_approval_projection_0045",
    }
    for name, expected in cross_domain.items():
        assert EXPECTED_AUDIT_TRIGGERS[name] == expected

    query = str(_AUDIT_TRIGGER_SQL)
    assert (
        "table_row.relname IN ('audit_events', 'audit_chain_heads')" in query
    )
    assert "trigger_row.tgname IN" not in query


def test_material_request_cancellation_catalog_uses_postgresql_identifier(
) -> None:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0037_cancellation_trigger_catalog",
        MATERIAL_REQUEST_CANCELLATION_MIGRATION_0037,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    source_name = f"trg_{migration.FACT_TABLE}_cancellation_graph_0037"
    assert len(source_name.encode("utf-8")) > 63
    assert (
        source_name.encode("utf-8")[:63].decode("utf-8")
        == POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037
    )
    assert (
        migration.PG_FACT_GRAPH_TRIGGER
        == POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037
    )
    assert all(
        len(name.encode("utf-8")) <= 63
        for name in migration._postgresql_triggers()
    )
    assert source_name not in EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS
    assert (
        POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037
        in EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS
    )

    triggers = []
    for name, (table_name, function_name, enabled, trigger_type) in (
        EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS.items()
    ):
        is_deferred = function_name == migration.PG_DISPATCH_FUNCTION
        triggers.append(
            {
                "trigger_name": name,
                "table_name": table_name,
                "function_name": function_name,
                "function_schema": "public",
                "enabled": enabled,
                "trigger_type": trigger_type,
                "is_constraint_trigger": is_deferred,
                "is_deferrable": is_deferred,
                "is_initially_deferred": is_deferred,
                "has_when_clause": False,
                "has_column_filter": False,
            }
        )
    indexes = [
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
        for name, expected in (
            EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES.items()
        )
    ]
    _assert_material_request_cancellation_guards(
        triggers=triggers,
        indexes=indexes,
    )

    query = str(_MATERIAL_REQUEST_CANCELLATION_TRIGGER_SQL)
    assert "trigger_row.tgname IN" not in query
    assert "function_row.proname IN" in query
    for function_name in {
        expected[1]
        for expected in (
            EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS.values()
        )
    }:
        assert f"'{function_name}'" in query
    assert f"'{source_name}'" not in query

    for drifted in (
        triggers[1:],
        [
            *triggers,
            {
                **triggers[0],
                "trigger_name": "trg_unapproved_cancellation_probe",
            },
        ],
    ):
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="cancellation guard",
        ):
            _assert_material_request_cancellation_guards(
                triggers=drifted,
                indexes=indexes,
            )


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
    ):
        trigger_name = migration_0021[constant_name]
        assert len(trigger_name.encode("utf-8")) <= 63
        assert trigger_name in EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS
    assert (
        migration_0021["CASE_TRIGGER"]
        not in EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS
    )

    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0032_recount_trigger_catalog",
        NONOPENING_STOCKTAKE_REVIEW_MIGRATION_0032,
    )
    assert spec is not None and spec.loader is not None
    migration_0032 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_0032)
    current_0032_triggers = {
        migration_0032.CASE_TRIGGER,
        migration_0032.TASK_TRIGGER,
        migration_0032.ROUND_TRIGGER,
        *migration_0032.RECOUNT_GRAPH_TRIGGERS.values(),
    }
    retired_0032_triggers = {
        migration_0032.OLD_CASE_TRIGGER,
        migration_0032.OLD_TASK_TRIGGER,
        migration_0032.OLD_ROUND_TRIGGER,
        *migration_0032.OLD_GRAPH_TRIGGERS.values(),
    }
    assert current_0032_triggers <= set(
        EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS
    )
    assert retired_0032_triggers.isdisjoint(
        EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS
    )
    expected_0032_shapes = {
        migration_0032.CASE_TRIGGER: (
            "stocktake_recount_cases",
            migration_0032.PG_CASE_FUNCTION,
            "A", 7, False, False, False,
        ),
        migration_0032.TASK_TRIGGER: (
            "stocktake_tasks",
            migration_0032.PG_TASK_FUNCTION,
            "A", 19, False, False, False,
        ),
        migration_0032.ROUND_TRIGGER: (
            "stocktake_rounds",
            migration_0032.PG_ROUND_FUNCTION,
            "A", 23, False, False, False,
        ),
        **{
            trigger_name: (
                table_name,
                migration_0032.PG_RECOUNT_GRAPH_FUNCTION,
                "A", 29, True, True, True,
            )
            for table_name, trigger_name in (
                migration_0032.RECOUNT_GRAPH_TRIGGERS.items()
            )
        },
    }
    for trigger_name, expected in expected_0032_shapes.items():
        assert EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS[trigger_name] == expected

    migration_0033 = constants(STOCKTAKE_COUNT_LEDGER_MIGRATION_0033)
    assert EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS[
        migration_0033["PG_TRIGGER"]
    ] == (
        migration_0033["TABLE"],
        migration_0033["PG_FUNCTION"],
        "A", 7, False, False, False,
    )
    migration_0034 = constants(STOCKTAKE_RECOUNT_SCOPE_MIGRATION_0034)
    assert EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS[
        migration_0034["PG_COMPLETION_TRIGGER"]
    ] == (
        "stocktake_scope_count_completions",
        migration_0034["PG_COMPLETION_FUNCTION"],
        "A", 7, False, False, False,
    )

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
        "trg_stocktake_recount_graph_assignment_0032",
        "trg_stocktake_scope_count_ledger_boundary_0033",
        "trg_stocktake_recount_completion_scope_0034",
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
        "trg_stocktake_recount_graph_assignment_0032": "A",
        "trg_stocktake_scope_count_ledger_boundary_0033": "A",
        "trg_stocktake_recount_completion_scope_0034": "A",
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


def _load_material_request_approval_activation_migration_0045() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0045_material_request_approval_security_manifest",
        MATERIAL_REQUEST_APPROVAL_ACTIVATION_MIGRATION_0045,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _valid_material_request_approval_trigger_rows(
) -> list[dict[str, object]]:
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
            "has_column_filter": False,
        }
        for name, (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
        ) in sorted(EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.items())
    ]


def _valid_material_request_approval_function_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for function_id, coordinate in enumerate(
        sorted(MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256),
        start=1,
    ):
        function_name, argument_types = coordinate
        source_body = f"catalog fixture for {function_name}({argument_types})"
        monkeypatch.setitem(
            MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256,
            coordinate,
            hashlib.sha256(source_body.encode("utf-8")).hexdigest(),
        )
        rows.append(
            {
                "function_id": function_id,
                "function_name": function_name,
                "argument_types": argument_types,
                "function_kind": "f",
                "result_type": (
                    "void"
                    if coordinate in MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
                    else "trigger"
                ),
                "argument_modes": None,
                "argument_default_count": 0,
                "is_strict": False,
                "source_body": source_body,
                "volatility": "v",
                "parallel_safety": "u",
                "is_leakproof": False,
                "is_security_definer": (
                    coordinate
                    in MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
                ),
                "language_name": "plpgsql",
                "configuration": ["search_path=pg_catalog, public"],
                "owner_name": "star_oam_migrator",
                "can_execute": False,
                "api_execute_is_grantable": False,
                "unexpected_execute_grantee_count": 0,
                "public_can_execute": False,
                "edge_can_execute": False,
                "backup_can_execute": False,
                "edge_receiver_can_execute": False,
                "projector_can_execute": False,
            }
        )
    return rows


def _assert_valid_material_request_approval_catalog(
    monkeypatch: pytest.MonkeyPatch,
    *,
    triggers: list[dict[str, object]] | None = None,
    functions: list[dict[str, object]] | None = None,
) -> None:
    _assert_material_request_approval_guards(
        triggers=(
            _valid_material_request_approval_trigger_rows()
            if triggers is None
            else triggers
        ),
        functions=(
            _valid_material_request_approval_function_rows(monkeypatch)
            if functions is None
            else functions
        ),
        expected_migration_role="star_oam_migrator",
    )


def test_0045_material_request_approval_catalog_accepts_exact_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    triggers = _valid_material_request_approval_trigger_rows()
    functions = _valid_material_request_approval_function_rows(monkeypatch)

    assert len(triggers) == 61
    assert len(functions) == 28
    _assert_material_request_approval_guards(
        triggers=triggers,
        functions=functions,
        expected_migration_role="star_oam_migrator",
    )


def test_0045_material_request_approval_trigger_catalog_rejects_set_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    triggers = _valid_material_request_approval_trigger_rows()
    for drifted in (
        triggers[1:],
        [
            *triggers,
            {
                **triggers[0],
                "trigger_name": "trg_unapproved_material_request_probe_0045",
            },
        ],
    ):
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="material-request approval guard",
        ):
            _assert_valid_material_request_approval_catalog(
                monkeypatch,
                triggers=drifted,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("table_name", "approval_actions"),
        ("function_name", "rsc_unapproved_approval_rewrite_0045"),
        ("function_schema", "attacker"),
        ("enabled", "O"),
        ("trigger_type", 31),
        ("is_constraint_trigger", False),
        ("is_deferrable", False),
        ("is_initially_deferred", False),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ],
)
def test_0045_material_request_approval_trigger_catalog_rejects_shape_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    triggers = _valid_material_request_approval_trigger_rows()
    target = next(
        row
        for row in triggers
        if row["trigger_name"]
        == "trg_material_requests_approval_projection_0045"
    )
    target[field] = value

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match=rf"trg_material_requests_approval_projection_0045\.{field}",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            triggers=triggers,
        )


def test_0045_material_request_approval_trigger_names_fit_postgresql() -> None:
    assert all(
        len(name.encode("utf-8")) <= 63
        for name in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS
    )


def test_0045_material_request_approval_function_catalog_rejects_set_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = _valid_material_request_approval_function_rows(monkeypatch)
    for drifted in (
        functions[1:],
        [
            *functions,
            {
                **functions[0],
                "function_id": len(functions) + 1,
                "function_name": "rsc_unapproved_approval_helper_0045",
            },
        ],
    ):
        with pytest.raises(
            DatabaseSecurityBoundaryError,
            match="material-request approval guard",
        ):
            _assert_material_request_approval_guards(
                triggers=_valid_material_request_approval_trigger_rows(),
                functions=drifted,
                expected_migration_role="star_oam_migrator",
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_body", "tampered approval helper body"),
        ("owner_name", "star_oam_api"),
        ("can_execute", True),
        ("api_execute_is_grantable", True),
        ("unexpected_execute_grantee_count", 1),
        ("public_can_execute", True),
        ("edge_can_execute", True),
        ("backup_can_execute", True),
        ("edge_receiver_can_execute", True),
        ("projector_can_execute", True),
        ("is_security_definer", True),
        ("result_type", "record"),
        ("configuration", ["search_path=public"]),
    ],
)
def test_0045_material_request_approval_function_catalog_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    functions = _valid_material_request_approval_function_rows(monkeypatch)
    target = next(
        row
        for row in functions
        if row["function_name"]
        == "rsc_guard_material_request_status_transition_0045"
    )
    target[field] = value

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="rsc_guard_material_request_status_transition_0045",
    ):
        _assert_material_request_approval_guards(
            triggers=_valid_material_request_approval_trigger_rows(),
            functions=functions,
            expected_migration_role="star_oam_migrator",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_security_definer", False),
        ("result_type", "trigger"),
    ],
)
def test_0045_material_request_approval_privileged_validator_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    functions = _valid_material_request_approval_function_rows(monkeypatch)
    target = next(
        row
        for row in functions
        if (
            row["function_name"],
            row["argument_types"],
        )
        == (
            "rsc_validate_material_request_approval_projection_0045",
            "uuid",
        )
    )
    target[field] = value

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="rsc_validate_material_request_approval_projection_0045",
    ):
        _assert_material_request_approval_guards(
            triggers=_valid_material_request_approval_trigger_rows(),
            functions=functions,
            expected_migration_role="star_oam_migrator",
        )


def test_0045_request_file_guard_must_remain_security_definer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    functions = _valid_material_request_approval_function_rows(monkeypatch)
    target = next(
        row
        for row in functions
        if (
            row["function_name"],
            row["argument_types"],
        )
        == ("rsc_guard_material_request_file_0029", "")
    )
    target["is_security_definer"] = False

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="rsc_guard_material_request_file_0029",
    ):
        _assert_material_request_approval_guards(
            triggers=_valid_material_request_approval_trigger_rows(),
            functions=functions,
            expected_migration_role="star_oam_migrator",
        )


def test_0045_material_request_approval_trigger_query_captures_complete_scope(
) -> None:
    query = " ".join(str(_MATERIAL_REQUEST_APPROVAL_TRIGGER_SQL).split())

    assert "trigger_row.tgname ~ '_(0029|0030|0045)$'" in query
    assert "function_row.proname IN" in query
    assert "AND NOT trigger_row.tgisinternal" in query
    assert "trigger_row.tgname IN" not in query
    for required_field in (
        "trigger_row.tgenabled",
        "trigger_row.tgtype",
        "trigger_row.tgconstraint",
        "trigger_row.tgdeferrable",
        "trigger_row.tginitdeferred",
        "trigger_row.tgqual",
        "trigger_row.tgattr",
    ):
        assert required_field in query
    for function_name in {
        expected[1]
        for expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.values()
    }:
        assert f"'{function_name}'" in query
    assert {
        coordinate[0].rsplit("_", 1)[-1]
        for coordinate in MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
    } == {"0029", "0030", "0045"}


def test_0045_material_request_approval_migration_bindings_match_manifest(
) -> None:
    migration = _load_material_request_approval_activation_migration_0045()
    manifest_bindings = {
        name: expected[0]
        for name, expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.items()
    }
    migration_bindings = {
        trigger_name: table_name
        for table_name, trigger_name in migration.TRIGGER_BINDINGS
    }

    assert migration.down_revision == "20260902_0044"
    assert len(migration.LEGACY_TRIGGER_BINDINGS) == 45
    assert len(migration.NEW_TRIGGER_BINDINGS) == 16
    assert len(migration.TRIGGER_BINDINGS) == 61
    assert len(migration_bindings) == len(migration.TRIGGER_BINDINGS)
    assert migration_bindings == manifest_bindings
    assert migration.PROJECTION_TRIGGER_TABLES == (
        "material_requests",
        "material_request_revisions",
        "material_request_lines",
        "material_request_commands",
        "approval_instances",
        "approval_steps",
        "approval_actions",
        "approval_external_registrations",
        "approval_external_registration_lines",
        "approval_step_line_decisions",
        "approval_return_line_facts",
        "state_transition_events",
        "audit_events",
    )
    assert {
        trigger_name
        for _, trigger_name in migration.LEGACY_TRIGGER_BINDINGS
    } == {
        name
        for name in manifest_bindings
        if name.endswith(("_0029", "_0030"))
    }
    assert {
        trigger_name
        for _, trigger_name in migration.NEW_TRIGGER_BINDINGS
    } == {
        name for name in manifest_bindings if name.endswith("_0045")
    }
    assert all(
        expected[2] == "A"
        for expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.values()
    )


def test_0045_material_request_approval_function_bodies_match_manifest(
) -> None:
    migration = _load_material_request_approval_activation_migration_0045()
    function_sql = {
        (migration.PG_STATUS_GUARD_FUNCTION, ""): migration._status_guard_sql(),
        (migration.PG_LINE_GUARD_FUNCTION, ""): migration._line_guard_sql(),
        (
            migration.PG_COMMAND_PARENT_LOCK_FUNCTION,
            "",
        ): migration._command_parent_lock_sql(),
        (
            migration.PG_TERMINAL_VALIDATE_FUNCTION,
            "uuid, uuid, uuid, bigint",
        ): migration._terminal_validator_sql(),
        (
            migration.PG_RETURN_VALIDATE_FUNCTION,
            "uuid, uuid",
        ): migration._return_validator_sql(),
        (
            migration.PG_EXTERNAL_VALIDATE_FUNCTION,
            "uuid",
        ): migration._external_validator_sql(),
        (
            migration.PG_PROJECTION_VALIDATE_FUNCTION,
            "uuid",
        ): migration._projection_validator_sql(),
        (
            migration.PG_PROJECTION_DISPATCH_FUNCTION,
            "",
        ): migration._projection_dispatcher_sql(),
    }

    assert len(MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256) == 28
    assert set(function_sql) == {
        coordinate
        for coordinate in MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
        if coordinate[0].endswith("_0045")
    }
    for coordinate, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]
        )
    assert MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS == {
        (migration.PG_REQUEST_FILE_FUNCTION_0029, ""),
        (migration.PG_APPROVAL_DISPATCH_FUNCTION_0030, ""),
        (
            migration.PG_TERMINAL_VALIDATE_FUNCTION,
            "uuid, uuid, uuid, bigint",
        ),
        (migration.PG_RETURN_VALIDATE_FUNCTION, "uuid, uuid"),
        (migration.PG_EXTERNAL_VALIDATE_FUNCTION, "uuid"),
        (migration.PG_PROJECTION_VALIDATE_FUNCTION, "uuid"),
        (migration.PG_PROJECTION_DISPATCH_FUNCTION, ""),
    }
    assert MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS == {
        (migration.PG_APPROVAL_VALIDATE_FUNCTION_0030, "uuid"),
        (
            migration.PG_TERMINAL_VALIDATE_FUNCTION,
            "uuid, uuid, uuid, bigint",
        ),
        (migration.PG_RETURN_VALIDATE_FUNCTION, "uuid, uuid"),
        (migration.PG_EXTERNAL_VALIDATE_FUNCTION, "uuid"),
        (migration.PG_PROJECTION_VALIDATE_FUNCTION, "uuid"),
    }
