from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

import app.database_security as database_security
from app.database_security import (
    DatabaseSecurityBoundaryError,
    EXPECTED_FORMAL_FILE_INDEXES,
    EXPECTED_FORMAL_FILE_TRIGGERS,
    EXPECTED_KMS_DATA_KEY_PIN_COLUMNS,
    EXPECTED_KMS_DATA_KEY_PIN_CONSTRAINTS,
    EXPECTED_KMS_DATA_KEY_PIN_INDEXES,
    EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS,
    EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS,
    EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK,
    EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN,
    EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES,
    EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS,
    EXPECTED_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX,
    EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS,
    EXPECTED_STOCKTAKE_START_COMPLETION_COLUMNS,
    EXPECTED_STOCKTAKE_START_COMPLETION_CONSTRAINTS,
    EXPECTED_STOCKTAKE_START_COMPLETION_INDEXES,
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
    EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS,
    EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS,
    EXPECTED_STOCKTAKE_SCOPE_TRIGGERS,
    EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS,
    POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019,
    POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037,
    POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019,
    STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031,
    STOCKTAKE_DIFFERENCE_COMPLETION_TRIGGER_0031,
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
    _NONOPENING_STOCKTAKE_START_TRIGGER_SQL,
    _NONOPENING_STOCKTAKE_CLOSE_TRIGGER_SQL,
    _MATERIAL_REQUEST_CANCELLATION_TRIGGER_SQL,
    _MATERIAL_REQUEST_APPROVAL_TRIGGER_SQL,
    _MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK_SQL,
    _MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_SQL,
    _MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX_SQL,
    _OPENING_TERMINAL_TRIGGER_SQL,
    _OPENING_TERMINAL_INDEX_SQL,
    _RECONCILIATION_CONSTRAINT_SQL,
    _RECONCILIATION_PARTIAL_INDEX_SQL,
    _RECONCILIATION_TRIGGER_SQL,
    _SMS_DISPATCH_ROLE_ACCESS_SQL,
    _STOCKTAKE_START_COMPLETION_CONSTRAINT_SQL,
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
    _assert_nonopening_stocktake_start_guards,
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
    _material_request_content_check_definition_matches,
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
STOCKTAKE_COUNT_OBSERVATIONS_MIGRATION_0011 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0011_opening_count_observations.py"
)
OPENING_OBSERVATION_DISPOSITIONS_MIGRATION_0016 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0016_opening_observation_dispositions.py"
)
STOCKTAKE_RECOUNT_CAUSALITY_MIGRATION_0018 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0018_stocktake_recount_causality.py"
)
STOCKTAKE_DIFFERENCE_EVALUATOR_MIGRATION_0031 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0031_stocktake_difference_evaluator.py"
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
MATERIAL_REQUEST_DRAFT_CONTENT_MIGRATION_0046 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0046_material_request_draft_content_causality.py"
)
NONOPENING_STOCKTAKE_START_MIGRATION_0047 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0047_nonopening_stocktake_start_causality.py"
)
STOCKTAKE_SCOPE_GUARD_SECURITY_MIGRATION_0048 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0048_stocktake_scope_guard_security.py"
)
STOCKTAKE_RECOUNT_GUARD_SECURITY_MIGRATION_0049 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0049_stocktake_recount_guard_security.py"
)
STOCKTAKE_OBSERVATION_SCOPE_MODE_MIGRATION_0050 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0050_stocktake_observation_scope_mode.py"
)
STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_MIGRATION_0051 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0051_stocktake_difference_authorization_hash.py"
)
OPENING_TERMINAL_GUARD_EXECUTION_MIGRATION_0052 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0052_opening_terminal_guard_execution.py"
)
OPENING_GRAPH_TABLE_DISPATCH_MIGRATION_0053 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260904_0053_opening_graph_table_dispatch.py"
)
OPENING_RECOUNT_SOURCE_HISTORY_MIGRATION_0054 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260904_0054_opening_recount_source_history.py"
)
NONOPENING_START_AUDIT_ORDER_MIGRATION_0055 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0055_nonopening_start_audit_order.py"
)
NONOPENING_COUNT_GUARD_COMPATIBILITY_MIGRATION_0056 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0056_nonopening_count_guard_compatibility.py"
)
NONOPENING_DIFFERENCE_REPLAY_LOCK_MIGRATION_0057 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0057_nonopening_difference_replay_lock.py"
)
NONOPENING_REVIEW_TERMINAL_STATUS_MIGRATION_0058 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0058_nonopening_review_terminal_status.py"
)
MATERIAL_REQUEST_SUPPLY_CAUSALITY_MIGRATION_0059 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0059_material_request_supply_task_causality.py"
)
MATERIAL_REQUEST_SUPPLY_SECURITY_MIGRATION_0060 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0060_material_request_supply_security_hardening.py"
)
MATERIAL_REQUEST_SUPPLY_EVENT_KEY_MIGRATION_0061 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0061_material_request_supply_event_key_expression.py"
)
STOCK_RESERVATIONS_MIGRATION_0069 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260909_0069_stock_reservations.py"
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
OPENING_OBSERVATION_POSTING_MIGRATION_0023 = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0023_opening_observation_posting.py"
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


def test_runtime_acl_verifier_matches_base_manifest_through_0047(
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
        "stocktake_posting_command_outcomes",
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
        "supply_tasks",
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
    stocktake_start_tables = {"stocktake_start_completions"}
    allocation_tables = {
        "outbound_postings", "outbound_posting_serials",
        "shipments", "shipment_lines", "shipment_serials",
        "logistics_events", "receipts", "receipt_lines", "receipt_serials", "receipt_exceptions", "oam_receipt_evidence", "inbound_orders", "inbound_postings",
        "outbound_orders", "outbound_lines", "stock_reservation_picks", "stock_reservation_pick_serials",
        "stock_allocations",
        "stock_allocation_serials",
        "stock_reservations",
        "stock_reservation_serials",
        "stock_reservation_releases",
        "stock_reservation_release_serials",
        "work_order_material_operations", "work_order_material_lines", "work_order_material_serials", "work_order_replacement_pairs",
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
        | stocktake_start_tables
        | allocation_tables
    )
    assert RUNTIME_INSERT_TABLES - set(values["API_INSERT_TABLES"]) == (
        safe_posting_tables
        | {"document_attachments", "files", "sms_challenge_dispatches"}
        | material_request_insert_tables
        | stocktake_close_insert_tables
        | stocktake_start_tables
        | (allocation_tables - {"oam_receipt_evidence"})
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
    base_update_columns["stocktake_tasks"].update(
        {
            "cutoff_ledger_cursor",
            "cutoff_at",
            "snapshot_manifest_sha256",
            "issued_at",
            "frozen_at",
        }
    )
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
            "allocation_status",
            "reservation_status",
            "outbound_status",
            "personal_inbound_status",
            "version",
            "updated_at",
        },
        "supply_tasks": {
            "reference_no",
            "expected_date",
            "status",
            "cancelled_by_user_id",
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
        import runpy
        receipt_files = runpy.run_path(str(FORMAL_FILE_MIGRATION_0036.with_name("20260926_0086_receipt_evidence_files.py")))
        for before, after in receipt_files["source_changes"]()[coordinate]:
            assert body == before
            body = after
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


def _load_nonopening_stocktake_start_migration_0047() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0047_security_manifest",
        NONOPENING_STOCKTAKE_START_MIGRATION_0047,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_scope_guard_security_migration_0048() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0048_scope_guard_security_manifest",
        STOCKTAKE_SCOPE_GUARD_SECURITY_MIGRATION_0048,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_recount_guard_security_migration_0049() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0049_recount_guard_security_manifest",
        STOCKTAKE_RECOUNT_GUARD_SECURITY_MIGRATION_0049,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_observation_scope_mode_migration_0050() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0050_observation_scope_mode_manifest",
        STOCKTAKE_OBSERVATION_SCOPE_MODE_MIGRATION_0050,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_difference_authorization_hash_migration_0051() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0051_difference_authorization_hash_manifest",
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_MIGRATION_0051,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_opening_terminal_guard_execution_migration_0052() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0052_opening_terminal_guard_execution_manifest",
        OPENING_TERMINAL_GUARD_EXECUTION_MIGRATION_0052,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_opening_graph_table_dispatch_migration_0053() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0053_opening_graph_table_dispatch_manifest",
        OPENING_GRAPH_TABLE_DISPATCH_MIGRATION_0053,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_opening_recount_source_history_migration_0054() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0054_opening_recount_source_history_manifest",
        OPENING_RECOUNT_SOURCE_HISTORY_MIGRATION_0054,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_nonopening_start_audit_order_migration_0055() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0055_nonopening_start_audit_order_manifest",
        NONOPENING_START_AUDIT_ORDER_MIGRATION_0055,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_nonopening_count_guard_compatibility_migration_0056() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0056_nonopening_count_guard_compatibility_manifest",
        NONOPENING_COUNT_GUARD_COMPATIBILITY_MIGRATION_0056,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_nonopening_difference_replay_lock_migration_0057() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0057_nonopening_difference_replay_lock_manifest",
        NONOPENING_DIFFERENCE_REPLAY_LOCK_MIGRATION_0057,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_nonopening_review_terminal_status_migration_0058() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0058_nonopening_review_terminal_status_manifest",
        NONOPENING_REVIEW_TERMINAL_STATUS_MIGRATION_0058,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_material_request_supply_causality_migration_0059() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0059_material_request_supply_causality_manifest",
        MATERIAL_REQUEST_SUPPLY_CAUSALITY_MIGRATION_0059,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_material_request_supply_security_migration_0060() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0060_material_request_supply_security_manifest",
        MATERIAL_REQUEST_SUPPLY_SECURITY_MIGRATION_0060,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_material_request_supply_event_key_migration_0061() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0061_material_request_supply_event_key_manifest",
        MATERIAL_REQUEST_SUPPLY_EVENT_KEY_MIGRATION_0061,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stock_reservations_migration_0069() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0069_stock_reservations_security_manifest",
        STOCK_RESERVATIONS_MIGRATION_0069,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _assert_0069_function_body_matches_runtime_manifest(
    *,
    coordinate: tuple[str, str],
    historical_body: str,
    historical_hash: str,
    current_hash: str,
    replacements: tuple[tuple[str, str], ...],
) -> None:
    assert hashlib.sha256(historical_body.encode()).hexdigest() == historical_hash
    current_body = historical_body
    for old, new in replacements:
        assert current_body.count(old) == 1
        assert new not in current_body
        current_body = current_body.replace(old, new)
    assert hashlib.sha256(current_body.encode()).hexdigest() == current_hash
    import runpy
    next_migration = runpy.run_path(str(STOCK_RESERVATIONS_MIGRATION_0069.with_name("20260910_0070_stock_reservation_releases.py")))
    latest_body = current_body
    for old, new in next_migration["source_changes"]().get(coordinate, ()):
        assert latest_body.count(old) == 1 and new not in latest_body
        latest_body = latest_body.replace(old, new)
    picking = runpy.run_path(str(STOCK_RESERVATIONS_MIGRATION_0069.with_name("20260911_0071_reservation_picking.py")))
    for old, new in picking["source_changes"]().get(coordinate, ()):
        assert latest_body.count(old) == 1 and new not in latest_body
        latest_body = latest_body.replace(old, new)
    outbound = runpy.run_path(str(STOCK_RESERVATIONS_MIGRATION_0069.with_name("20260912_0072_outbound_postings.py")))
    for old, new in outbound["source_changes"]().get(coordinate, ()):
        assert latest_body.count(old) == 1 and new not in latest_body
        latest_body = latest_body.replace(old, new)
    inbound = runpy.run_path(str(STOCK_RESERVATIONS_MIGRATION_0069.with_name("20260924_0084_personal_inbound_projection_boundary.py")))
    if coordinate in inbound["FUNCTION_HASHES"]:
        assert hashlib.sha256(latest_body.encode()).hexdigest() == inbound["FUNCTION_HASHES"][coordinate][0]
        for old, new in inbound["source_changes"]()[coordinate]:
            assert latest_body.count(old) == 1 and new not in latest_body
            latest_body = latest_body.replace(old, new)
        assert hashlib.sha256(latest_body.encode()).hexdigest() == inbound["FUNCTION_HASHES"][coordinate][1]
    if coordinate == ("rsc_validate_material_request_approval_projection_0045", "uuid"):
        fulfillment = runpy.run_path(str(STOCK_RESERVATIONS_MIGRATION_0069.with_name("20260925_0085_fulfillment_version_commands.py")))
        assert hashlib.sha256(latest_body.encode()).hexdigest() == fulfillment["APPROVAL_OLD_HASH"]
        for old, new in fulfillment["source_changes"]():
            assert latest_body.count(old) == 1 and new not in latest_body
            latest_body = latest_body.replace(old, new)
        assert hashlib.sha256(latest_body.encode()).hexdigest() == fulfillment["APPROVAL_NEW_HASH"]
        inbound_command = runpy.run_path(str(STOCK_RESERVATIONS_MIGRATION_0069.with_name("20260927_0087_inbound_fulfillment_boundary.py")))
        assert hashlib.sha256(latest_body.encode()).hexdigest() == inbound_command["APPROVAL_OLD_HASH"]
        for old, new in inbound_command["source_changes"]():
            assert latest_body.count(old) == 1 and new not in latest_body
            latest_body = latest_body.replace(old, new)
        assert hashlib.sha256(latest_body.encode()).hexdigest() == inbound_command["APPROVAL_NEW_HASH"]
    assert MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate] == hashlib.sha256(latest_body.encode()).hexdigest()
    assert current_hash != historical_hash
    for old, new in reversed(replacements):
        assert current_body.count(new) == 1
        assert old not in current_body
        current_body = current_body.replace(new, old)
    assert current_body == historical_body


def _load_stocktake_difference_evaluator_migration_0031() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0031_difference_evaluator_manifest",
        STOCKTAKE_DIFFERENCE_EVALUATOR_MIGRATION_0031,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_opening_terminal_runtime_migration_0022() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0022_opening_terminal_security_manifest",
        OPENING_TERMINAL_RUNTIME_MIGRATION_0022,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_opening_observation_posting_migration_0023() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0023_opening_observation_security_manifest",
        OPENING_OBSERVATION_POSTING_MIGRATION_0023,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_scope_region_owner_migration_0025() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0025_scope_region_owner_manifest",
        STOCKTAKE_SCOPE_REGION_OWNER_MIGRATION_0025,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _valid_nonopening_stocktake_start_trigger_rows() -> list[dict[str, object]]:
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
        ) in sorted(EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS.items())
    ]


def _valid_stocktake_start_completion_column_rows() -> list[dict[str, object]]:
    return [
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
            EXPECTED_STOCKTAKE_START_COMPLETION_COLUMNS, start=1
        )
    ]


def _valid_stocktake_start_completion_constraint_rows() -> list[dict[str, object]]:
    check_definitions = {
        "ck_stocktake_start_completions_versions_0047":
            "CHECK (expected_task_version >= 0 AND started_task_version = expected_task_version + 1)",
        "ck_stocktake_start_completions_counts_0047":
            "CHECK (cutoff_ledger_cursor >= 0 AND scope_count > 0 AND snapshot_line_count >= 0 AND active_freeze_count = scope_count)",
        "ck_stocktake_start_completions_authorization_0047":
            "CHECK (authorization_version > 0 AND role_code IN ('admin', 'provincial_manager', 'technician') AND scope_type <> '' AND scope_id_snapshot <> '')",
        "ck_stocktake_start_completions_hashes_0047":
            "CHECK (length(scope_manifest_sha256) = 64 AND length(snapshot_manifest_sha256) = 64 AND length(request_sha256) = 64 AND length(idempotency_key_hash) = 64 AND length(authorization_sha256) = 64 AND length(graph_manifest_sha256) = 64)",
        "ck_stocktake_start_completions_chronology_0047":
            "CHECK (cutoff_at <= started_at AND created_at = started_at)",
    }
    return [
        {
            "constraint_name": name,
            "constraint_type": constraint_type,
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": constraint_type != "c",
            "is_local": True,
            "inheritance_count": 0,
            "parent_constraint_id": 0,
            "definition": check_definitions.get(name, constraint_type),
            "constrained_columns": list(columns),
            "referenced_table": referenced_table,
            "referenced_columns": list(referenced_columns),
            "delete_action": "r" if constraint_type == "f" else " ",
        }
        for name, (
            constraint_type,
            columns,
            referenced_table,
            referenced_columns,
        ) in sorted(EXPECTED_STOCKTAKE_START_COMPLETION_CONSTRAINTS.items())
    ]


def _valid_stocktake_start_completion_index_rows() -> list[dict[str, object]]:
    return [
        {
            "index_name": name,
            "owner_name": "star_oam_migrator",
            "access_method": "btree",
            "is_unique": is_unique,
            "is_primary": is_primary,
            "is_exclusion": False,
            "is_immediate": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "nulls_not_distinct": False,
            "key_attribute_count": len(columns),
            "total_attribute_count": len(columns),
            "has_expressions": False,
            "key_columns": list(columns),
            "predicate": None,
        }
        for name, (columns, is_unique, is_primary) in sorted(
            EXPECTED_STOCKTAKE_START_COMPLETION_INDEXES.items()
        )
    ]


def _valid_nonopening_stocktake_start_guard_kwargs() -> dict[str, object]:
    return {
        "triggers": _valid_nonopening_stocktake_start_trigger_rows(),
        "columns": _valid_stocktake_start_completion_column_rows(),
        "constraints": _valid_stocktake_start_completion_constraint_rows(),
        "indexes": _valid_stocktake_start_completion_index_rows(),
        "expected_migration_role": "star_oam_migrator",
    }


def test_0047_nonopening_start_runtime_manifest_and_function_bodies_are_exact(
) -> None:
    migration = _load_nonopening_stocktake_start_migration_0047()
    repair = _load_nonopening_start_audit_order_migration_0055()
    assert migration.down_revision == "20260903_0046"
    assert repair.revision == "20260905_0055"
    assert repair.down_revision == "20260904_0054"
    assert migration.COMPLETION_TABLE == "stocktake_start_completions"
    assert tuple(migration.DEFERRED_TABLES) == (
        "stocktake_tasks",
        "stocktake_scopes",
        "inventory_freezes",
        "stocktake_snapshot_lines",
        "stocktake_rounds",
        "stocktake_start_completions",
        "state_transition_events",
        "audit_events",
    )
    assert tuple(migration.TASK_START_UPDATE_COLUMNS) == (
        "cutoff_ledger_cursor",
        "cutoff_at",
        "snapshot_manifest_sha256",
        "issued_at",
        "frozen_at",
    )

    assert migration.COMPLETION_TABLE in RUNTIME_READ_TABLES
    assert migration.COMPLETION_TABLE in RUNTIME_INSERT_TABLES
    assert migration.COMPLETION_TABLE not in RUNTIME_UPDATE_TABLES
    assert migration.COMPLETION_TABLE not in RUNTIME_DELETE_TABLES
    assert migration.COMPLETION_TABLE not in RUNTIME_UPDATE_COLUMNS
    assert set(migration.TASK_START_UPDATE_COLUMNS) <= (
        RUNTIME_UPDATE_COLUMNS["stocktake_tasks"]
    )

    function_sql = {
        (migration.PG_GUARD_FUNCTION, ""):
            migration._postgresql_guard_function_sql(),
        (migration.PG_VALIDATE_FUNCTION, "uuid"):
            migration._postgresql_validator_function_sql(),
        (migration.PG_DISPATCH_FUNCTION, ""):
            migration._postgresql_dispatch_function_sql(),
    }
    guard_sql = function_sql[(migration.PG_GUARD_FUNCTION, "")]
    validator_sql = function_sql[(migration.PG_VALIDATE_FUNCTION, "uuid")]
    assert "public.auth_identities AS identity" in guard_sql
    assert "assignment.status IN ('scheduled', 'active')" in guard_sql
    assert "public.role_assignments AS assignment" in guard_sql
    assert "IF scope_row.material_id IS NOT NULL" in guard_sql
    assert "material.status = 'active'" in guard_sql
    assert "policy.effective_from <= NEW.cutoff_at" in guard_sql
    assert "policy.effective_to > NEW.cutoff_at" in guard_sql
    assert "scope_row.created_at > NEW.cutoff_at" in guard_sql
    assert "target_person.id = current_custodian_id" in guard_sql
    assert "personal_location.custodian_person_id" in guard_sql
    assert "NEW.started_by_person_id" in guard_sql
    assert "deny_assignment.valid_from <= NEW.started_at" in guard_sql
    assert "deny_assignment.revoked_at > NEW.started_at" in guard_sql
    assert "deny_assignment.valid_from <= pg_catalog.clock_timestamp()" not in guard_sql
    assert "existing_scope.task_id <> candidate_scope.task_id" in guard_sql
    assert "existing_freeze.status = 'active'" in guard_sql
    assert "FOR UPDATE OF existing_freeze" in guard_sql
    assert "FROM public.stocktake_rounds AS round_row" in guard_sql
    assert "round_row.task_id = NEW.task_id) <> 1" in guard_sql
    assert "public.role_assignments" not in validator_sql
    assert "public.auth_identities" not in validator_sql
    assert "public.roles" not in validator_sql
    assert "actor_user.account_status" not in validator_sql
    assert "assignment.status" not in validator_sql
    assert set(function_sql) <= set(FORMAL_FILE_INTERNAL_FUNCTIONS)
    assert set(function_sql).isdisjoint(RUNTIME_EXECUTE_FUNCTIONS)
    repaired_hashes = {
        (migration.PG_GUARD_FUNCTION, ""): (
            repair.GUARD_BODY_SHA256_0054,
            repair.GUARD_BODY_SHA256_0055,
        ),
        (migration.PG_VALIDATE_FUNCTION, "uuid"): (
            repair.VALIDATOR_BODY_SHA256_0054,
            repair.VALIDATOR_BODY_SHA256_0055,
        ),
    }
    for coordinate, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if coordinate in repaired_hashes:
            legacy_hash, fixed_hash = repaired_hashes[coordinate]
            assert body_hash == legacy_hash
            assert body.count(repair.LEGACY_AUDIT_ORDER) == 1
            assert repair.FIXED_AUDIT_ORDER not in body
            fixed_body = body.replace(
                repair.LEGACY_AUDIT_ORDER,
                repair.FIXED_AUDIT_ORDER,
            )
            assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
                fixed_hash
            )
            assert fixed_body.count(repair.FIXED_AUDIT_ORDER) == 1
            assert repair.LEGACY_AUDIT_ORDER not in fixed_body
            assert fixed_body.replace(
                repair.FIXED_AUDIT_ORDER,
                repair.LEGACY_AUDIT_ORDER,
            ) == body
            assert (
                FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
                == fixed_hash
            )
        else:
            assert coordinate == (migration.PG_DISPATCH_FUNCTION, "")
            assert body_hash == repair.DISPATCH_BODY_SHA256
            assert (
                FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
                == body_hash
            )
        assert FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate] == (
            "v",
            True,
            "plpgsql",
            ("search_path=pg_catalog, public",),
        )
        assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == (
            "f",
            "void" if coordinate[1] == "uuid" else "trigger",
            False,
        )

    source = NONOPENING_STOCKTAKE_START_MIGRATION_0047.read_text(
        encoding="utf-8"
    )
    assert (
        "REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, "
        "{PRODUCTION_API_ROLE}"
    ) in source
    assert (
        "GRANT SELECT, INSERT ON TABLE public.{COMPLETION_TABLE}"
    ) in source
    assert (
        "GRANT UPDATE ({columns}) ON TABLE public.stocktake_tasks"
    ) in source
    assert "GRANT UPDATE ON TABLE public.stocktake_tasks" not in source


def test_0048_scope_guard_execution_boundary_is_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_0025 = _load_stocktake_scope_region_owner_migration_0025()
    migration_0048 = _load_stocktake_scope_guard_security_migration_0048()
    executed: list[str] = []
    monkeypatch.setattr(
        migration_0025.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration_0025._create_postgresql_guard()
    function_sql = next(
        statement
        for statement in executed
        if statement.lstrip().startswith(
            "CREATE FUNCTION public."
            "rsc_validate_stocktake_scope_region_owner_0025()"
        )
    )
    function_body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    coordinate = (migration_0048.SCOPE_GUARD_FUNCTION, "")
    actual_hash = hashlib.sha256(function_body.encode("utf-8")).hexdigest()

    assert migration_0048.revision == "20260903_0048"
    assert migration_0048.down_revision == "20260903_0047"
    assert migration_0048.PREVIOUS_SCHEMA_REVISION == migration_0048.down_revision
    assert migration_0048.SCOPE_GUARD_FUNCTION == migration_0025.PG_FUNCTION
    assert migration_0048.SCOPE_GUARD_TRIGGER == migration_0025.SCOPE_TRIGGER
    assert actual_hash == migration_0048.EXPECTED_FUNCTION_BODY_SHA256
    assert actual_hash == FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
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
    assert migration_0048.LEGACY_SEARCH_PATH == "search_path=pg_catalog, public"
    assert migration_0048.HARDENED_SEARCH_PATH == (
        "search_path=pg_catalog, public"
    )

    executed.clear()
    migration_0048._lock_scope_graph()
    assert executed == [
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    ]

    executed.clear()
    migration_0048._verify_scope_guard_catalog(
        security_definer=True,
        expected_search_path=migration_0048.HARDENED_SEARCH_PATH,
        phase="test hardened catalog",
    )
    assert len(executed) == 1
    catalog_sql = executed[0]
    for required in (
        "current_user <> 'star_oam_migrator'",
        "session_user <> 'star_oam_migrator'",
        "function_row.prokind = 'f'",
        "function_row.pronargs = 0",
        "function_row.prorettype = 'trigger'::pg_catalog.regtype",
        "language_row.lanname = 'plpgsql'",
        "function_row.provolatile = 'v'",
        "NOT function_row.proisstrict",
        "NOT function_row.proleakproof",
        "function_row.proparallel = 'u'",
        "function_row.prosecdef IS TRUE",
        "function_row.proowner = migrator_oid",
        "function_row.proconfig IS NOT DISTINCT FROM",
        migration_0048.EXPECTED_FUNCTION_BODY_SHA256,
        "trigger_row.tgenabled = 'A'",
        "trigger_row.tgtype = 7",
        "trigger_row.tgconstraint = 0",
        "NOT trigger_row.tgdeferrable",
        "NOT trigger_row.tginitdeferred",
        "trigger_row.tgqual IS NULL",
        "trigger_row.tgnargs = 0",
        "function_acl.grantee = 0",
        "pg_catalog.has_function_privilege",
    ):
        assert required in catalog_sql

    executed.clear()
    migration_0048._set_scope_guard_security(
        security_definer=True,
        search_path="pg_catalog, public",
    )
    signature = "public.rsc_validate_stocktake_scope_region_owner_0025()"
    assert executed == [
        f"ALTER FUNCTION {signature} SECURITY DEFINER",
        f"ALTER FUNCTION {signature} SET search_path = pg_catalog, public",
        f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator",
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, star_oam_api",
    ]

    executed.clear()
    migration_0048._set_scope_guard_security(
        security_definer=False,
        search_path="pg_catalog, public",
    )
    assert executed[0] == f"ALTER FUNCTION {signature} SECURITY INVOKER"
    assert not any(statement.startswith("GRANT ") for statement in executed)


def test_0049_recount_guard_function_bodies_and_security_manifest_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def load(path: Path, module_name: str) -> object:
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    migration_0011 = load(
        STOCKTAKE_COUNT_OBSERVATIONS_MIGRATION_0011,
        "rsc_migration_0011_recount_guard_body",
    )
    migration_0016 = load(
        OPENING_OBSERVATION_DISPOSITIONS_MIGRATION_0016,
        "rsc_migration_0016_recount_guard_body",
    )
    migration_0018 = load(
        STOCKTAKE_RECOUNT_CAUSALITY_MIGRATION_0018,
        "rsc_migration_0018_recount_guard_body",
    )
    migration_0021 = load(
        ROUND_ASSIGNMENT_GUARDS_MIGRATION_0021,
        "rsc_migration_0021_recount_guard_body",
    )
    migration_0032 = load(
        NONOPENING_STOCKTAKE_REVIEW_MIGRATION_0032,
        "rsc_migration_0032_recount_guard_body",
    )
    migration_0049 = _load_stocktake_recount_guard_security_migration_0049()
    migration_0050 = _load_stocktake_observation_scope_mode_migration_0050()
    migration_0052 = _load_opening_terminal_guard_execution_migration_0052()
    migration_0056 = (
        _load_nonopening_count_guard_compatibility_migration_0056()
    )
    migration_0058 = (
        _load_nonopening_review_terminal_status_migration_0058()
    )

    executed_0011: list[str] = []
    with monkeypatch.context() as patcher:
        patcher.setattr(
            migration_0011.op,
            "execute",
            lambda statement: executed_0011.append(str(statement)),
        )
        migration_0011._create_postgresql_count_contract_triggers()
    actor_assignment_sql = next(
        statement
        for statement in executed_0011
        if statement.lstrip().startswith(
            "CREATE FUNCTION rsc_stocktake_actor_assignment_valid_0011("
        )
    )

    function_sql = {
        migration_0049.ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE:
            actor_assignment_sql,
        migration_0049.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE:
            migration_0016._postgresql_disposition_function_sql(),
        migration_0049.RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE:
            migration_0018._postgresql_assignment_validate_sql(),
        migration_0049.ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE:
            migration_0021._postgresql_actor_function_sql(),
        migration_0049.COUNT_LINE_CALLER_0021_SIGNATURE:
            migration_0021._postgresql_count_line_function_sql(),
        migration_0049.OBSERVATION_CALLER_0021_SIGNATURE:
            migration_0021._postgresql_observation_function_sql(
                round_aware=True
            ),
        migration_0049.SCOPE_COMPLETION_CALLER_0021_SIGNATURE:
            migration_0021._postgresql_completion_function_sql(
                round_aware=True
            ),
        migration_0049.REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE:
            migration_0032._postgresql_review_graph_function_sql(),
        migration_0049.RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE:
            migration_0032._postgresql_scope_graph_function_sql(),
        migration_0049.RECOUNT_CASE_CALLER_0032_SIGNATURE:
            migration_0032._postgresql_case_function_sql(),
        migration_0049.RECOUNT_TASK_CALLER_0032_SIGNATURE:
            migration_0032._postgresql_task_function_sql(),
        migration_0049.RECOUNT_ROUND_CALLER_0032_SIGNATURE:
            migration_0032._postgresql_round_function_sql(),
        migration_0049.RECOUNT_GRAPH_CALLER_0032_SIGNATURE:
            migration_0032._postgresql_recount_graph_function_sql(),
    }
    coordinates = {
        migration_0049.ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE: (
            migration_0049.ACTOR_ASSIGNMENT_HELPER_0011,
            "text, uuid, uuid, bigint, timestamp with time zone, text, "
            "text, text",
        ),
        migration_0049.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE: (
            migration_0049.OBSERVATION_DISPOSITION_CALLER_0016,
            "",
        ),
        migration_0049.RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE: (
            migration_0049.RECOUNT_ASSIGNMENT_CALLER_0018,
            "",
        ),
        migration_0049.ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE: (
            migration_0049.ROUND_ASSIGNMENT_HELPER_0021,
            "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, text, "
            "timestamp with time zone, boolean",
        ),
        migration_0049.COUNT_LINE_CALLER_0021_SIGNATURE: (
            migration_0049.COUNT_LINE_CALLER_0021,
            "",
        ),
        migration_0049.OBSERVATION_CALLER_0021_SIGNATURE: (
            migration_0049.OBSERVATION_CALLER_0021,
            "",
        ),
        migration_0049.SCOPE_COMPLETION_CALLER_0021_SIGNATURE: (
            migration_0049.SCOPE_COMPLETION_CALLER_0021,
            "",
        ),
        migration_0049.REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE: (
            migration_0049.REVIEW_GRAPH_VALIDATOR_0032,
            "",
        ),
        migration_0049.RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE: (
            migration_0049.RECOUNT_SCOPE_GRAPH_HELPER_0032,
            "uuid",
        ),
        migration_0049.RECOUNT_CASE_CALLER_0032_SIGNATURE: (
            migration_0049.RECOUNT_CASE_CALLER_0032,
            "",
        ),
        migration_0049.RECOUNT_TASK_CALLER_0032_SIGNATURE: (
            migration_0049.RECOUNT_TASK_CALLER_0032,
            "",
        ),
        migration_0049.RECOUNT_ROUND_CALLER_0032_SIGNATURE: (
            migration_0049.RECOUNT_ROUND_CALLER_0032,
            "",
        ),
        migration_0049.RECOUNT_GRAPH_CALLER_0032_SIGNATURE: (
            migration_0049.RECOUNT_GRAPH_CALLER_0032,
            "",
        ),
    }
    catalog = {row[0]: row for row in migration_0049.FUNCTION_CATALOG}

    assert migration_0049.revision == "20260903_0049"
    assert migration_0049.down_revision == "20260903_0048"
    assert migration_0049.PREVIOUS_SCHEMA_REVISION == migration_0049.down_revision
    assert len(migration_0049.CALLER_SIGNATURES) == 9
    assert len(migration_0049.INVOKER_SIGNATURES) == 4
    assert set(migration_0049.CALLER_SIGNATURES).isdisjoint(
        migration_0049.INVOKER_SIGNATURES
    )
    assert set(migration_0049.ALL_FUNCTION_SIGNATURES) == (
        set(migration_0049.CALLER_SIGNATURES)
        | set(migration_0049.INVOKER_SIGNATURES)
    )
    assert set(function_sql) == set(coordinates) == set(catalog) == set(
        migration_0049.EXPECTED_FUNCTION_BODY_SHA256
    )
    assert migration_0049.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE in (
        migration_0049.CALLER_SIGNATURES
    )
    assert migration_0049.REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE in (
        migration_0049.INVOKER_SIGNATURES
    )

    for signature, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        coordinate = coordinates[signature]
        (
            _,
            _,
            return_type,
            language,
            volatility,
            _,
        ) = catalog[signature]
        expected_configuration = (
            ()
            if signature
            == migration_0049.ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE
            else (migration_0049.FIXED_SEARCH_PATH,)
        )
        assert body_hash == migration_0049.EXPECTED_FUNCTION_BODY_SHA256[
            signature
        ]
        if signature == migration_0050.FUNCTION_SIGNATURE:
            expected_runtime_hash = migration_0050.QUALIFIED_BODY_SHA256
        elif signature == migration_0056.HELPER_SIGNATURE:
            expected_runtime_hash = migration_0056.FIXED_BODY_SHA256
        elif signature == migration_0058.REVIEW_GRAPH_SIGNATURE:
            expected_runtime_hash = migration_0058.FIXED_BODY_SHA256
        elif signature == migration_0052.SCOPE_COMPLETION_GUARD_SIGNATURE_0021:
            expected_runtime_hash = (
                migration_0052.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
            )
        else:
            expected_runtime_hash = body_hash
        assert (
            FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
            == expected_runtime_hash
        )
        assert FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate] == (
            volatility,
            signature in migration_0049.CALLER_SIGNATURES,
            language,
            expected_configuration,
        )
        assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == (
            "f",
            return_type,
            False,
        )
        assert coordinate not in RUNTIME_EXECUTE_FUNCTIONS

    legacy_observation_sql = function_sql[migration_0050.FUNCTION_SIGNATURE]
    legacy_observation_body = legacy_observation_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    assert legacy_observation_body.count(
        migration_0050.LEGACY_SOURCE_FRAGMENT
    ) == 1
    assert migration_0050.QUALIFIED_SOURCE_FRAGMENT not in legacy_observation_body
    qualified_observation_body = legacy_observation_body.replace(
        migration_0050.LEGACY_SOURCE_FRAGMENT,
        migration_0050.QUALIFIED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(
        qualified_observation_body.encode("utf-8")
    ).hexdigest() == migration_0050.QUALIFIED_BODY_SHA256
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[
        (migration_0050.FUNCTION_NAME, "")
    ] == migration_0050.QUALIFIED_BODY_SHA256

    legacy_helper_sql = function_sql[migration_0056.HELPER_SIGNATURE]
    legacy_helper_body = legacy_helper_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    assert legacy_helper_body.count(migration_0056.LEGACY_SOURCE_FRAGMENT) == 1
    assert migration_0056.FIXED_SOURCE_FRAGMENT not in legacy_helper_body
    fixed_helper_body = legacy_helper_body.replace(
        migration_0056.LEGACY_SOURCE_FRAGMENT,
        migration_0056.FIXED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(fixed_helper_body.encode("utf-8")).hexdigest() == (
        migration_0056.FIXED_BODY_SHA256
    )
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[
        (
            migration_0056.HELPER_FUNCTION,
            "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, text, "
            "timestamp with time zone, boolean",
        )
    ] == migration_0056.FIXED_BODY_SHA256

    legacy_review_sql = function_sql[migration_0058.REVIEW_GRAPH_SIGNATURE]
    legacy_review_body = legacy_review_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    assert legacy_review_body.count(migration_0058.LEGACY_SOURCE_FRAGMENT) == 1
    assert migration_0058.FIXED_SOURCE_FRAGMENT not in legacy_review_body
    fixed_review_body = legacy_review_body.replace(
        migration_0058.LEGACY_SOURCE_FRAGMENT,
        migration_0058.FIXED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(fixed_review_body.encode("utf-8")).hexdigest() == (
        migration_0058.FIXED_BODY_SHA256
    )
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[
        (migration_0058.REVIEW_GRAPH_FUNCTION, "")
    ] == migration_0058.FIXED_BODY_SHA256


def test_0049_recount_guard_security_mutations_and_catalog_are_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_stocktake_recount_guard_security_migration_0049()
    executed: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed.append(str(statement)),
    )

    migration._lock_trigger_tables()
    assert executed == [
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in migration.TRIGGER_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    ]

    executed.clear()
    migration._set_caller_security(security_definer=True)
    expected_hardened: list[str] = []
    for signature in migration.CALLER_SIGNATURES:
        expected_hardened.extend(
            (
                f"ALTER FUNCTION {signature} SECURITY DEFINER",
                f"ALTER FUNCTION {signature} SET search_path = "
                "pg_catalog, public",
            )
        )
    for signature in migration.ALL_FUNCTION_SIGNATURES:
        expected_hardened.extend(
            (
                f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator",
                f"REVOKE ALL ON FUNCTION {signature} "
                "FROM PUBLIC, star_oam_api",
            )
        )
    assert executed == expected_hardened
    assert not any(
        signature in statement and "SECURITY DEFINER" in statement
        for signature in migration.INVOKER_SIGNATURES
        for statement in executed
    )

    executed.clear()
    migration._set_caller_security(security_definer=False)
    expected_legacy: list[str] = []
    reset_signatures = {
        migration.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
        migration.RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
    }
    for signature in migration.CALLER_SIGNATURES:
        expected_legacy.append(f"ALTER FUNCTION {signature} SECURITY INVOKER")
        if signature in reset_signatures:
            expected_legacy.append(f"ALTER FUNCTION {signature} RESET search_path")
        else:
            expected_legacy.append(
                f"ALTER FUNCTION {signature} SET search_path = "
                "pg_catalog, public"
            )
    for signature in migration.ALL_FUNCTION_SIGNATURES:
        expected_legacy.extend(
            (
                f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator",
                f"REVOKE ALL ON FUNCTION {signature} "
                "FROM PUBLIC, star_oam_api",
            )
        )
    assert executed == expected_legacy

    executed.clear()
    migration._verify_catalog(
        callers_security_definer=True,
        phase="test hardened recount guard catalog",
    )
    assert len(executed) == 1
    catalog_sql = executed[0]
    for signature, expected_hash in (
        migration.EXPECTED_FUNCTION_BODY_SHA256.items()
    ):
        assert signature in catalog_sql
        assert expected_hash in catalog_sql
    for _, trigger_name, _, _, _, _, _ in migration.TRIGGER_CATALOG:
        assert trigger_name in catalog_sql
    for required in (
        "current_user <> 'star_oam_migrator'",
        "session_user <> 'star_oam_migrator'",
        "function_row.proowner = migrator_oid",
        "function_row.prokind = 'f'",
        "NOT function_row.proisstrict",
        "NOT function_row.proleakproof",
        "function_row.proparallel = 'u'",
        "function_row.prosecdef IS NOT DISTINCT FROM",
        "function_row.proconfig IS NOT DISTINCT FROM",
        "pg_catalog.has_function_privilege",
        "trigger_row.tgenabled = 'A'",
        "trigger_row.tgqual IS NULL",
        "trigger_row.tgnargs = 0",
        "trigger_row.tgattr = ''::pg_catalog.int2vector",
        f") <> {len(migration.TRIGGER_CATALOG)} THEN",
    ):
        assert required in catalog_sql


def test_0049_recount_guard_trigger_roster_is_exact_startup_proof() -> None:
    migration = _load_stocktake_recount_guard_security_migration_0049()
    function_names = {
        signature: function_name
        for signature, function_name, _, _, _, _ in migration.FUNCTION_CATALOG
    }
    expected = {
        trigger_name: (
            table_name,
            function_names[function_signature],
            "A",
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
        )
        for (
            table_name,
            trigger_name,
            function_signature,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
        ) in migration.TRIGGER_CATALOG
    }
    trigger_function_signatures = {
        function_signature
        for _, _, function_signature, _, _, _, _ in migration.TRIGGER_CATALOG
    }

    assert len(migration.TRIGGER_CATALOG) == len(expected) == 15
    assert trigger_function_signatures == set(
        migration.TRIGGER_FUNCTION_SIGNATURES
    )
    assert len(migration.TRIGGER_FUNCTION_SIGNATURES) == 10
    assert (
        set(migration.INVOKER_SIGNATURES) - trigger_function_signatures
    ) == {
        migration.ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE,
        migration.ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE,
        migration.RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE,
    }
    for trigger_name, shape in expected.items():
        assert EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS[trigger_name] == shape

    newly_guarded = {
        "trg_stocktake_observation_dispositions_validate_0016",
        "trg_nonopening_review_graph_task_0032",
        "trg_nonopening_review_graph_review_0032",
        "trg_nonopening_review_graph_item_0032",
    }
    assert newly_guarded <= set(expected)
    startup_query = str(_STOCKTAKE_RECOUNT_TRIGGER_SQL)
    for trigger_name in newly_guarded:
        assert f"'{trigger_name}'" in startup_query
    assert "OR (\n          function_row.proname IN (" in startup_query
    for function_name in {
        shape[1]
        for shape in EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS.values()
    } | {
        "rsc_validate_stocktake_recount_scope_assignment_0018",
        "rsc_validate_stocktake_count_line_insert_0021",
        "rsc_validate_stocktake_observation_insert_0021",
        "rsc_validate_stocktake_scope_completion_insert_0021",
    }:
        assert f"'{function_name}'" in startup_query
    for sensitive_table in (
        "stock_locations",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_scope_count_completions",
        "stocktake_recount_scope_assignments",
    ):
        assert f"'{sensitive_table}'" in startup_query

    extra_alias = dict(_valid_stocktake_recount_triggers()[0])
    extra_alias["trigger_name"] = "trg_unapproved_recount_guard_alias"
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=_valid_stocktake_recount_constraints(),
            indexes=_valid_stocktake_recount_indexes(),
            triggers=[*_valid_stocktake_recount_triggers(), extra_alias],
        )


@pytest.mark.parametrize(
    "trigger_name",
    (
        "trg_stocktake_observation_dispositions_validate_0016",
        "trg_nonopening_review_graph_task_0032",
        "trg_nonopening_review_graph_review_0032",
        "trg_nonopening_review_graph_item_0032",
    ),
)
def test_0049_new_startup_trigger_guards_reject_catalog_drift(
    trigger_name: str,
) -> None:
    valid = _valid_stocktake_recount_triggers()
    for rows in (
        [row for row in valid if row["trigger_name"] != trigger_name],
        [dict(row) for row in valid],
    ):
        if len(rows) == len(valid):
            target = next(
                row for row in rows if row["trigger_name"] == trigger_name
            )
            target["function_name"] = "rsc_unapproved_stocktake_guard"
        with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
            _assert_stocktake_recount_schema(
                columns=_valid_stocktake_recount_columns(),
                constraints=_valid_stocktake_recount_constraints(),
                indexes=_valid_stocktake_recount_indexes(),
                triggers=rows,
            )

    if trigger_name.startswith("trg_nonopening_review_graph_"):
        rows = [dict(row) for row in valid]
        target = next(
            row for row in rows if row["trigger_name"] == trigger_name
        )
        target["is_deferrable"] = False
        with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
            _assert_stocktake_recount_schema(
                columns=_valid_stocktake_recount_columns(),
                constraints=_valid_stocktake_recount_constraints(),
                indexes=_valid_stocktake_recount_indexes(),
                triggers=rows,
            )


def test_0051_difference_completion_function_manifest_and_body_are_exact(
) -> None:
    migration_0031 = _load_stocktake_difference_evaluator_migration_0031()
    migration_0051 = (
        _load_stocktake_difference_authorization_hash_migration_0051()
    )
    coordinate = (STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031, "")
    legacy_sql = migration_0031._postgresql_0031_function_sql()
    legacy_body = legacy_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    assert migration_0051.revision == "20260903_0051"
    assert migration_0051.down_revision == "20260903_0050"
    assert migration_0051.PREVIOUS_SCHEMA_REVISION == (
        migration_0051.down_revision
    )
    assert migration_0051.FUNCTION_NAME == coordinate[0]
    assert migration_0051.FUNCTION_SIGNATURE == (
        f"public.{coordinate[0]}()"
    )
    assert migration_0051.FIXED_SEARCH_PATH == (
        "search_path=pg_catalog, public"
    )
    assert migration_0051.LEGACY_BODY_SHA256 == (
        "6eca64aa504472b4b3ce8e164c3d314e1092e5319797bcf9ea2ad415b51c8ff0"
    )
    assert migration_0051.FIXED_BODY_SHA256 == (
        "ead5a0a72c25cd326a1d036dddd384bb583b66141512f568b8929ab31b4773a9"
    )
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        migration_0051.LEGACY_BODY_SHA256
    )
    assert legacy_body.count(migration_0051.LEGACY_SOURCE_FRAGMENT) == 1
    assert migration_0051.FIXED_SOURCE_FRAGMENT not in legacy_body
    fixed_body = legacy_body.replace(
        migration_0051.LEGACY_SOURCE_FRAGMENT,
        migration_0051.FIXED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
        migration_0051.FIXED_BODY_SHA256
    )

    assert FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate] == (
        "v",
        False,
        "plpgsql",
        (migration_0051.FIXED_SEARCH_PATH,),
    )
    assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == (
        "f",
        "trigger",
        False,
    )
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == (
        migration_0051.FIXED_BODY_SHA256
    )
    assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] != (
        migration_0051.LEGACY_BODY_SHA256
    )
    assert coordinate not in RUNTIME_EXECUTE_FUNCTIONS
    assert set(FORMAL_FILE_INTERNAL_FUNCTION_SHAPES) == set(
        FORMAL_FILE_INTERNAL_FUNCTIONS
    )
    assert set(FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256) == set(
        FORMAL_FILE_INTERNAL_FUNCTIONS
    )


def test_0051_difference_completion_function_guard_rejects_legacy_body_and_acl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration_0031 = _load_stocktake_difference_evaluator_migration_0031()
    migration_0051 = (
        _load_stocktake_difference_authorization_hash_migration_0051()
    )
    coordinate = (migration_0051.FUNCTION_NAME, "")
    legacy_body = migration_0031._postgresql_0031_function_sql().split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    fixed_body = legacy_body.replace(
        migration_0051.LEGACY_SOURCE_FRAGMENT,
        migration_0051.FIXED_SOURCE_FRAGMENT,
    )
    row = {
        "function_id": 1,
        "function_name": coordinate[0],
        "argument_types": coordinate[1],
        "function_kind": "f",
        "result_type": "trigger",
        "returns_set": False,
        "variadic_type": 0,
        "argument_modes": None,
        "argument_default_count": 0,
        "is_strict": False,
        "source_body": fixed_body,
        "volatility": "v",
        "parallel_safety": "u",
        "is_leakproof": False,
        "is_security_definer": False,
        "language_name": "plpgsql",
        "configuration": [migration_0051.FIXED_SEARCH_PATH],
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

    with monkeypatch.context() as patcher:
        patcher.setattr(database_security, "RUNTIME_EXECUTE_FUNCTIONS", {})
        patcher.setattr(database_security, "RUNTIME_FUNCTION_SHAPES", {})
        patcher.setattr(database_security, "RUNTIME_FUNCTION_BODY_SHA256", {})
        patcher.setattr(
            database_security,
            "FORMAL_FILE_INTERNAL_FUNCTIONS",
            {coordinate: ("v", False, "plpgsql", (migration_0051.FIXED_SEARCH_PATH,))},
        )
        patcher.setattr(
            database_security,
            "FORMAL_FILE_INTERNAL_FUNCTION_SHAPES",
            {coordinate: ("f", "trigger", False)},
        )
        patcher.setattr(
            database_security,
            "FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256",
            {coordinate: migration_0051.FIXED_BODY_SHA256},
        )
        patcher.setattr(database_security, "OAM_SYNC_RUNTIME_FUNCTIONS", set())
        patcher.setattr(
            database_security, "OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS", {}
        )
        patcher.setattr(
            database_security, "OAM_SYNC_RUNTIME_FUNCTION_SHAPES", {}
        )
        patcher.setattr(
            database_security, "OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256", {}
        )

        _assert_runtime_function_acl(
            [row], expected_migration_role="star_oam_migrator"
        )

        for field, value in (
            ("source_body", legacy_body),
            ("is_security_definer", True),
            ("can_execute", True),
            ("public_can_execute", True),
            ("unexpected_execute_grantee_count", 1),
        ):
            drifted = dict(row)
            drifted[field] = value
            with pytest.raises(
                DatabaseSecurityBoundaryError,
                match="function ACL",
            ):
                _assert_runtime_function_acl(
                    [drifted], expected_migration_role="star_oam_migrator"
                )


def test_0051_difference_completion_trigger_is_one_exact_always_binding(
) -> None:
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    expected = (
        migration.TRIGGER_TABLE,
        migration.FUNCTION_NAME,
        migration.FIXED_TRIGGER_ENABLED,
        7,
        False,
        False,
        False,
    )
    assert migration.TRIGGER_NAME == (
        STOCKTAKE_DIFFERENCE_COMPLETION_TRIGGER_0031
    )
    assert EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS[
        STOCKTAKE_DIFFERENCE_COMPLETION_TRIGGER_0031
    ] == expected

    startup_query = str(_STOCKTAKE_RECOUNT_TRIGGER_SQL)
    assert f"'{migration.TRIGGER_NAME}'" in startup_query
    assert f"'{migration.FUNCTION_NAME}'" in startup_query
    assert "trigger_row.tgnargs AS argument_count" in startup_query
    assert (
        "function_row.proname =\n"
        f"                  '{migration.FUNCTION_NAME}'"
    ) in startup_query

    _assert_valid_stocktake_recount_schema()
    valid = _valid_stocktake_recount_triggers()
    target = next(
        row
        for row in valid
        if row["trigger_name"] == migration.TRIGGER_NAME
    )
    for field, value in (
        ("table_name", "stocktake_tasks"),
        ("function_name", "rsc_unapproved_stocktake_guard"),
        ("function_schema", "attacker_schema"),
        ("enabled", migration.LEGACY_TRIGGER_ENABLED),
        ("trigger_type", 0),
        ("is_constraint_trigger", True),
        ("is_deferrable", True),
        ("is_initially_deferred", True),
        ("has_when_clause", True),
        ("has_column_filter", True),
        ("argument_count", 1),
    ):
        drifted = [dict(row) for row in valid]
        drifted_target = next(
            row
            for row in drifted
            if row["trigger_name"] == migration.TRIGGER_NAME
        )
        drifted_target[field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
            _assert_stocktake_recount_schema(
                columns=_valid_stocktake_recount_columns(),
                constraints=_valid_stocktake_recount_constraints(),
                indexes=_valid_stocktake_recount_indexes(),
                triggers=drifted,
            )

    alias = dict(target)
    alias["trigger_name"] = "trg_stocktake_difference_completion_alias"
    alias["table_name"] = "stocktake_count_lines"
    with pytest.raises(DatabaseSecurityBoundaryError, match="recount schema"):
        _assert_stocktake_recount_schema(
            columns=_valid_stocktake_recount_columns(),
            constraints=_valid_stocktake_recount_constraints(),
            indexes=_valid_stocktake_recount_indexes(),
            triggers=[*valid, alias],
        )


def _opening_terminal_0052_function_bodies() -> dict[tuple[str, str], str]:
    def load(path: Path, module_name: str) -> object:
        spec = importlib.util.spec_from_file_location(module_name, path)
        assert spec is not None and spec.loader is not None
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    migration_0011 = load(
        STOCKTAKE_COUNT_OBSERVATIONS_MIGRATION_0011,
        "rsc_migration_0011_opening_terminal_0052_manifest",
    )
    migration_0016 = load(
        OPENING_OBSERVATION_DISPOSITIONS_MIGRATION_0016,
        "rsc_migration_0016_opening_terminal_0052_manifest",
    )
    migration_0021 = load(
        ROUND_ASSIGNMENT_GUARDS_MIGRATION_0021,
        "rsc_migration_0021_opening_terminal_0052_manifest",
    )
    migration_0022 = _load_opening_terminal_runtime_migration_0022()
    migration_0023 = _load_opening_observation_posting_migration_0023()
    migration_0026 = load(
        ACL_MIGRATION,
        "rsc_migration_0026_opening_terminal_0052_manifest",
    )
    migration_0052 = _load_opening_terminal_guard_execution_migration_0052()
    migration_0053 = _load_opening_graph_table_dispatch_migration_0053()
    migration_0054 = _load_opening_recount_source_history_migration_0054()
    assert migration_0052.GRAPH_CLOSURE_BODY.count(
        migration_0053.GRAPH_ROUND_DISPATCH_0052
    ) == 1
    head_graph_closure_body = migration_0052.GRAPH_CLOSURE_BODY.replace(
        migration_0053.GRAPH_ROUND_DISPATCH_0052,
        migration_0053.GRAPH_ROUND_DISPATCH_0053,
    )
    assert hashlib.sha256(
        head_graph_closure_body.encode("utf-8")
    ).hexdigest() == migration_0053.GRAPH_CLOSURE_BODY_SHA256_0053
    assert migration_0052.ROUND_SUBMISSION_BODY.count(
        migration_0054.INITIAL_ASSIGNMENT_REJECTION_0053
    ) == 2
    head_round_submission_body = migration_0052.ROUND_SUBMISSION_BODY.replace(
        migration_0054.INITIAL_ASSIGNMENT_REJECTION_0053,
        migration_0054.INITIAL_ASSIGNMENT_REJECTION_0054,
    )
    assert hashlib.sha256(
        head_round_submission_body.encode("utf-8")
    ).hexdigest() == migration_0054.ROUND_SUBMISSION_BODY_SHA256_0054

    executed_0011: list[str] = []
    migration_0011.op = SimpleNamespace(execute=executed_0011.append)
    migration_0011._create_postgresql_count_contract_triggers()
    actor_sql = next(
        statement
        for statement in executed_0011
        if statement.lstrip().startswith(
            "CREATE FUNCTION rsc_stocktake_actor_assignment_valid_0011("
        )
    )
    executed_0016: list[str] = []
    migration_0016.op = SimpleNamespace(execute=executed_0016.append)
    migration_0016._create_postgresql_guards()
    review_immutable_sql = next(
        statement
        for statement in executed_0016
        if statement.lstrip().startswith(
            "CREATE FUNCTION rsc_block_stocktake_review_fact_mutation_0016()"
        )
    )
    review_completion_sql = migration_0016._postgresql_review_function_sql()
    executed_0026: list[str] = []
    migration_0026.op = SimpleNamespace(execute=executed_0026.append)
    migration_0026._create_postgresql_guards()
    reconciliation_effect_sql = next(
        statement
        for statement in executed_0026
        if statement.lstrip().startswith(
            "CREATE FUNCTION public.rsc_guard_reconciliation_effect_0026()"
        )
    )

    graph_sql = migration_0023._postgresql_graph_function_sql(current=True)
    commit_sql = migration_0022._postgresql_commit_function_sql()
    legacy_account_sql = migration_0023._postgresql_account_function_sql()
    legacy_scope_completion_sql = (
        migration_0021._postgresql_completion_function_sql(round_aware=True)
    )
    actor_body = actor_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    review_completion_body = review_completion_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    review_immutable_body = review_immutable_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    reconciliation_effect_body = reconciliation_effect_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    graph_body = graph_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    legacy_commit_body = commit_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    legacy_account_body = legacy_account_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    legacy_scope_completion_body = legacy_scope_completion_sql.split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(graph_body.encode("utf-8")).hexdigest() == (
        migration_0052.GRAPH_BODY_SHA256
    )
    assert hashlib.sha256(legacy_commit_body.encode("utf-8")).hexdigest() == (
        migration_0052.LEGACY_COMMIT_BODY_SHA256
    )
    assert legacy_commit_body.count(migration_0052.LEGACY_TASK_BRANCH) == 1
    assert migration_0052.FIXED_TASK_BRANCH not in legacy_commit_body
    hardened_commit_body = legacy_commit_body.replace(
        migration_0052.LEGACY_TASK_BRANCH,
        migration_0052.FIXED_TASK_BRANCH,
    )
    assert hashlib.sha256(legacy_account_body.encode("utf-8")).hexdigest() == (
        migration_0052.LEGACY_ACCOUNT_BODY_SHA256
    )
    assert legacy_account_body.count(
        migration_0052.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT
    ) == 1
    assert (
        migration_0052.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT
        not in legacy_account_body
    )
    hardened_account_body = legacy_account_body.replace(
        migration_0052.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        migration_0052.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
    )
    assert legacy_scope_completion_body.count(
        migration_0052.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
    ) == 1
    assert (
        migration_0052.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        not in legacy_scope_completion_body
    )
    hardened_scope_completion_body = legacy_scope_completion_body.replace(
        migration_0052.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
        migration_0052.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
    )
    bodies = {
        (
            migration_0052.ACTOR_ASSIGNMENT_FUNCTION,
            (
                "text, uuid, uuid, bigint, timestamp with time zone, "
                "text, text, text"
            ),
        ): actor_body,
        (migration_0052.REVIEW_COMPLETION_FUNCTION, ""):
            review_completion_body,
        (migration_0052.REVIEW_IMMUTABLE_FUNCTION, ""):
            review_immutable_body,
        (migration_0052.SCOPE_COMPLETION_GUARD_FUNCTION_0021, ""):
            hardened_scope_completion_body,
        (migration_0022.PG_GRAPH_CHECK_FUNCTION, "uuid, uuid"): graph_body,
        (migration_0022.PG_COMMIT_FUNCTION, ""): hardened_commit_body,
        (migration_0023.PG_ACCOUNT_FUNCTION, ""): hardened_account_body,
        (migration_0026.PG_EFFECT_FUNCTION, ""): reconciliation_effect_body,
    }
    head_bodies = {
        migration_0052.START_GRAPH_FUNCTION: migration_0052.START_GRAPH_BODY,
        migration_0052.ROUND_SUBMISSION_FUNCTION:
            head_round_submission_body,
        migration_0052.SCOPE_COMPLETION_FUNCTION:
            migration_0052.SCOPE_COMPLETION_BODY,
        migration_0052.REVIEW_GRAPH_FUNCTION:
            migration_0052.REVIEW_GRAPH_BODY,
        migration_0052.RECOUNT_GRAPH_FUNCTION:
            migration_0052.RECOUNT_GRAPH_BODY,
        migration_0052.DISPOSITION_GRAPH_FUNCTION:
            migration_0052.DISPOSITION_GRAPH_BODY,
        migration_0052.TERMINAL_GRAPH_FUNCTION:
            migration_0052.TERMINAL_GRAPH_BODY,
        migration_0052.INSERT_GUARD_FUNCTION:
            migration_0052.INSERT_GUARD_BODY,
        migration_0052.COUNT_WRITE_FUNCTION: migration_0052.COUNT_WRITE_BODY,
        migration_0052.GRAPH_CLOSURE_FUNCTION:
            head_graph_closure_body,
    }
    for row in migration_0052.HEAD_ONLY_FUNCTION_CATALOG:
        coordinate = (row[1], ", ".join(row[5]))
        bodies[coordinate] = head_bodies[row[1]]
    return bodies


def test_0052_opening_terminal_internal_function_manifest_is_exact() -> None:
    bodies = _opening_terminal_0052_function_bodies()
    migration_0052 = _load_opening_terminal_guard_execution_migration_0052()
    migration_0053 = _load_opening_graph_table_dispatch_migration_0053()
    migration_0054 = _load_opening_recount_source_history_migration_0054()
    actor_assignment_coordinate = (
        "rsc_stocktake_actor_assignment_valid_0011",
        (
            "text, uuid, uuid, bigint, timestamp with time zone, "
            "text, text, text"
        ),
    )
    review_completion_coordinate = (
        "rsc_require_stocktake_difference_completion_0016",
        "",
    )
    review_immutable_coordinate = (
        "rsc_block_stocktake_review_fact_mutation_0016",
        "",
    )
    scope_completion_guard_coordinate = (
        "rsc_validate_stocktake_scope_completion_insert_0021",
        "",
    )
    graph_coordinate = (
        "rsc_opening_terminal_graph_complete_0022",
        "uuid, uuid",
    )
    commit_coordinate = ("rsc_require_opening_terminal_graph_0022", "")
    account_coordinate = (
        "rsc_require_opening_observation_account_0023",
        "",
    )
    reconciliation_effect_coordinate = (
        "rsc_guard_reconciliation_effect_0026",
        "",
    )
    round_submission_coordinate = (
        migration_0052.ROUND_SUBMISSION_FUNCTION,
        "uuid, uuid, boolean",
    )
    scope_completion_coordinate = (
        migration_0052.SCOPE_COMPLETION_FUNCTION,
        "uuid, uuid, uuid, boolean",
    )
    expected_definitions = {
        actor_assignment_coordinate: ("s", False, "sql", ()),
        review_completion_coordinate: (
            "v",
            False,
            "plpgsql",
            (migration_0052.FIXED_SEARCH_PATH,),
        ),
        review_immutable_coordinate: ("v", False, "plpgsql", ()),
        scope_completion_guard_coordinate: (
            "v",
            True,
            "plpgsql",
            (migration_0052.FIXED_SEARCH_PATH,),
        ),
        graph_coordinate: (
            "s",
            False,
            "sql",
            ("search_path=pg_catalog, public",),
        ),
        commit_coordinate: (
            "v",
            True,
            "plpgsql",
            ("search_path=pg_catalog, public",),
        ),
        account_coordinate: (
            "v",
            True,
            "plpgsql",
            ("search_path=pg_catalog, public",),
        ),
        reconciliation_effect_coordinate: (
            "v",
            False,
            "plpgsql",
            (migration_0052.FIXED_SEARCH_PATH,),
        ),
    }
    expected_shapes = {
        actor_assignment_coordinate: ("f", "boolean", False),
        review_completion_coordinate: ("f", "trigger", False),
        review_immutable_coordinate: ("f", "trigger", False),
        scope_completion_guard_coordinate: ("f", "trigger", False),
        graph_coordinate: ("f", "boolean", False),
        commit_coordinate: ("f", "trigger", False),
        account_coordinate: ("f", "trigger", False),
        reconciliation_effect_coordinate: ("f", "trigger", False),
    }
    expected_hashes = {
        actor_assignment_coordinate: migration_0052.ACTOR_ASSIGNMENT_BODY_SHA256,
        review_completion_coordinate:
            migration_0052.REVIEW_COMPLETION_BODY_SHA256,
        review_immutable_coordinate:
            migration_0052.REVIEW_IMMUTABLE_BODY_SHA256,
        scope_completion_guard_coordinate: (
            migration_0052.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
        ),
        graph_coordinate: migration_0052.GRAPH_BODY_SHA256,
        commit_coordinate: migration_0052.FIXED_COMMIT_BODY_SHA256,
        account_coordinate: migration_0052.FIXED_ACCOUNT_BODY_SHA256,
        reconciliation_effect_coordinate:
            migration_0052.INHERITED_RECONCILIATION_FUNCTION_CATALOG[2][10],
    }
    for row in migration_0052.HEAD_ONLY_FUNCTION_CATALOG:
        coordinate = (row[1], ", ".join(row[5]))
        expected_definitions[coordinate] = (row[4], row[7], row[3], row[8])
        expected_shapes[coordinate] = ("f", row[2], False)
        expected_hashes[coordinate] = (
            migration_0053.GRAPH_CLOSURE_BODY_SHA256_0053
            if row[1] == migration_0052.GRAPH_CLOSURE_FUNCTION
            else migration_0054.ROUND_SUBMISSION_BODY_SHA256_0054
            if row[1] == migration_0052.ROUND_SUBMISSION_FUNCTION
            else row[9]
        )

    assert migration_0052.revision == "20260903_0052"
    assert migration_0052.down_revision == "20260903_0051"
    assert migration_0052.PREVIOUS_SCHEMA_REVISION == (
        migration_0052.down_revision
    )
    assert len(migration_0052.PERSISTENT_FUNCTION_SIGNATURES) == 7
    assert len(migration_0052.HEAD_ONLY_FUNCTION_SIGNATURES) == 10
    assert migration_0052.ALL_FUNCTION_SIGNATURES == (
        *migration_0052.PERSISTENT_FUNCTION_SIGNATURES,
        *migration_0052.HEAD_ONLY_FUNCTION_SIGNATURES,
    )
    assert migration_0052.HEAD_ONLY_FUNCTION_SIGNATURES == tuple(
        row[0] for row in migration_0052.HEAD_ONLY_FUNCTION_CATALOG
    )
    helper_catalog = migration_0052.HEAD_ONLY_FUNCTION_CATALOG[:7]
    guard_catalog = migration_0052.HEAD_ONLY_FUNCTION_CATALOG[7:]
    assert all(
        row[2] == "boolean"
        and row[3] == "sql"
        and row[4] == "v"
        and row[7] is False
        and row[8] == (migration_0052.FIXED_SEARCH_PATH,)
        for row in helper_catalog
    )
    assert all(
        row[2] == "trigger"
        and row[3] == "plpgsql"
        and row[4] == "v"
        and row[5] == ()
        and row[7] is True
        and row[8] == (migration_0052.FIXED_SEARCH_PATH,)
        for row in guard_catalog
    )
    assert migration_0052.CALLER_SIGNATURES == (
        migration_0052.COMMIT_SIGNATURE,
        migration_0052.ACCOUNT_SIGNATURE,
    )
    assert migration_0052.ACTOR_ASSIGNMENT_BODY_SHA256 == (
        FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[
            actor_assignment_coordinate
        ]
    )
    assert FORMAL_FILE_INTERNAL_FUNCTIONS[actor_assignment_coordinate] == (
        "s",
        False,
        "sql",
        (),
    )
    assert {
        row[1] for row in migration_0052.INHERITED_RECONCILIATION_FUNCTION_CATALOG
    } == {
        "rsc_canonical_reconciliation_json_0026",
        "rsc_reconciliation_event_key_0026",
        "rsc_guard_reconciliation_effect_0026",
    }
    assert set(bodies) == set(expected_definitions)
    for coordinate, body in bodies.items():
        assert FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate] == (
            expected_definitions[coordinate]
        )
        assert FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == (
            expected_shapes[coordinate]
        )
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert body_hash == expected_hashes[coordinate]
        assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == (
            body_hash
        )
        assert coordinate not in RUNTIME_EXECUTE_FUNCTIONS

    assert "session_user::text <> 'star_oam_api'" in (
        bodies[account_coordinate]
    )
    assert "current_user" not in bodies[account_coordinate]
    assert migration_0052.FIXED_TASK_BRANCH in bodies[commit_coordinate]
    assert migration_0052.LEGACY_TASK_BRANCH not in bodies[commit_coordinate]
    assert (
        migration_0052.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        in bodies[scope_completion_guard_coordinate]
    )
    assert (
        migration_0052.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        not in bodies[scope_completion_guard_coordinate]
    )
    for coordinate in (
        round_submission_coordinate,
        scope_completion_coordinate,
    ):
        assert "request_resolution_jsonb" in bodies[coordinate]
        assert (
            "cloud_oam.opening_stocktake.scope_count_request_resolution.v1"
            in bodies[coordinate]
        )
        assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == (
            hashlib.sha256(bodies[coordinate].encode("utf-8")).hexdigest()
        )
    assert set(FORMAL_FILE_INTERNAL_FUNCTION_SHAPES) == set(
        FORMAL_FILE_INTERNAL_FUNCTIONS
    )
    assert set(FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256) == set(
        FORMAL_FILE_INTERNAL_FUNCTIONS
    )


def test_0052_opening_trigger_catalogs_are_pinned_by_startup_guards() -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()

    def function_name(signature: str) -> str:
        return signature.split("(", 1)[0].rsplit(".", 1)[1]

    assert len(migration.TRIGGER_CATALOG) == 12
    assert len(migration.REVIEW_GUARD_TRIGGER_CATALOG) == 6
    assert len(migration.OPENING_0052_TRIGGER_CATALOG) == 20
    assert len(migration.INHERITED_RECONCILIATION_TRIGGER_CATALOG) == 6
    assert len(EXPECTED_OPENING_TERMINAL_TRIGGERS) == 53
    assert len(OPENING_COMMIT_TRIGGER_NAMES) == 28
    assert all(
        len(trigger_name.encode("utf-8")) <= 63
        for trigger_name in EXPECTED_OPENING_TERMINAL_TRIGGERS
    )
    assert (
        "trg_stocktake_difference_set_completions_immutable_truncate_001"
        in EXPECTED_OPENING_TERMINAL_TRIGGERS
    )
    assert (
        "trg_stocktake_difference_set_completions_immutable_truncate_0016"
        not in EXPECTED_OPENING_TERMINAL_TRIGGERS
    )

    for table_name, trigger_name, signature, trigger_type in (
        migration.TRIGGER_CATALOG
    ):
        assert EXPECTED_OPENING_TERMINAL_TRIGGERS[trigger_name] == (
            table_name,
            function_name(signature),
            "A",
            trigger_type,
        )
        assert trigger_name in OPENING_COMMIT_TRIGGER_NAMES

    for table_name, trigger_name, signature, trigger_type in (
        migration.REVIEW_GUARD_TRIGGER_CATALOG
    ):
        expected_trigger = EXPECTED_OPENING_TERMINAL_TRIGGERS.get(
            trigger_name
        )
        if expected_trigger is None:
            expected_trigger = EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS[
                trigger_name
            ][:4]
        assert expected_trigger == (
            table_name,
            function_name(signature),
            "A",
            trigger_type,
        )
        assert trigger_name not in OPENING_COMMIT_TRIGGER_NAMES

    for (
        table_name,
        trigger_name,
        signature,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
        enabled,
    ) in migration.OPENING_0052_TRIGGER_CATALOG:
        caller = function_name(signature)
        assert EXPECTED_OPENING_TERMINAL_TRIGGERS[trigger_name] == (
            table_name,
            caller,
            enabled,
            trigger_type,
        )
        assert (trigger_name in OPENING_COMMIT_TRIGGER_NAMES) is (
            is_constraint_trigger and is_deferrable and is_initially_deferred
        )

    sensitive_0052 = {
        trigger_name
        for (
            table_name,
            trigger_name,
            _,
            _,
            _,
            _,
            _,
            _,
        ) in migration.OPENING_0052_TRIGGER_CATALOG
        if table_name in {
            "stocktake_count_lines",
            "stocktake_count_observations",
            "stocktake_scope_count_completions",
        }
    }
    assert len(sensitive_0052) == 6
    for trigger_name in sensitive_0052:
        table_name, caller, enabled, trigger_type = (
            EXPECTED_OPENING_TERMINAL_TRIGGERS[trigger_name]
        )
        is_deferred = trigger_name in OPENING_COMMIT_TRIGGER_NAMES
        assert EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS[trigger_name] == (
            table_name,
            caller,
            enabled,
            trigger_type,
            is_deferred,
            is_deferred,
            is_deferred,
        )

    audit_trigger = "trg_audit_events_opening_graph_0052"
    assert EXPECTED_AUDIT_TRIGGERS[audit_trigger] == (
        "audit_events",
        "rsc_require_opening_live_graph_0052",
        5,
        True,
        True,
        True,
    )
    for table_name, trigger_name, signature, trigger_type in (
        migration.INHERITED_RECONCILIATION_TRIGGER_CATALOG
    ):
        assert EXPECTED_RECONCILIATION_TRIGGERS[trigger_name] == (
            table_name,
            function_name(signature),
            "A",
            trigger_type,
        )


def test_0052_opening_terminal_startup_rejects_function_security_body_and_acl_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies = _opening_terminal_0052_function_bodies()
    definitions = {
        coordinate: FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate]
        for coordinate in bodies
    }
    shapes = {
        coordinate: FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate]
        for coordinate in bodies
    }
    hashes = {
        coordinate: FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
        for coordinate in bodies
    }
    rows: list[dict[str, object]] = []
    for function_id, coordinate in enumerate(sorted(bodies), start=1):
        function_name, argument_types = coordinate
        volatility, is_security_definer, language_name, configuration = (
            definitions[coordinate]
        )
        function_kind, result_type, is_strict = shapes[coordinate]
        rows.append(
            {
                "function_id": function_id,
                "function_name": function_name,
                "argument_types": argument_types,
                "function_kind": function_kind,
                "result_type": result_type,
                "returns_set": False,
                "variadic_type": 0,
                "argument_modes": None,
                "argument_default_count": 0,
                "is_strict": is_strict,
                "source_body": bodies[coordinate],
                "volatility": volatility,
                "parallel_safety": "u",
                "is_leakproof": False,
                "is_security_definer": is_security_definer,
                "language_name": language_name,
                "configuration": list(configuration),
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

    with monkeypatch.context() as patcher:
        patcher.setattr(database_security, "RUNTIME_EXECUTE_FUNCTIONS", {})
        patcher.setattr(database_security, "RUNTIME_FUNCTION_SHAPES", {})
        patcher.setattr(database_security, "RUNTIME_FUNCTION_BODY_SHA256", {})
        patcher.setattr(
            database_security,
            "FORMAL_FILE_INTERNAL_FUNCTIONS",
            definitions,
        )
        patcher.setattr(
            database_security,
            "FORMAL_FILE_INTERNAL_FUNCTION_SHAPES",
            shapes,
        )
        patcher.setattr(
            database_security,
            "FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256",
            hashes,
        )
        patcher.setattr(database_security, "OAM_SYNC_RUNTIME_FUNCTIONS", set())
        patcher.setattr(
            database_security, "OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS", {}
        )
        patcher.setattr(
            database_security, "OAM_SYNC_RUNTIME_FUNCTION_SHAPES", {}
        )
        patcher.setattr(
            database_security, "OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256", {}
        )

        _assert_runtime_function_acl(
            rows, expected_migration_role="star_oam_migrator"
        )

        for target_index, target_row in enumerate(rows):
            drift_cases = (
                ("source_body", "tampered opening terminal function body"),
                (
                    "is_security_definer",
                    not bool(target_row["is_security_definer"]),
                ),
                ("configuration", ["search_path=public"]),
                ("owner_name", "star_oam_api"),
                ("can_execute", True),
                ("api_execute_is_grantable", True),
                ("unexpected_execute_grantee_count", 1),
                ("public_can_execute", True),
                ("edge_can_execute", True),
                ("backup_can_execute", True),
                ("edge_receiver_can_execute", True),
                ("projector_can_execute", True),
            )
            for field, value in drift_cases:
                drifted = [dict(row) for row in rows]
                drifted[target_index][field] = value
                with pytest.raises(
                    DatabaseSecurityBoundaryError,
                    match=str(target_row["function_name"]),
                ):
                    _assert_runtime_function_acl(
                        drifted,
                        expected_migration_role="star_oam_migrator",
                    )


def test_0047_nonopening_start_catalog_guard_is_exact_and_rejects_drift(
) -> None:
    guard_kwargs = _valid_nonopening_stocktake_start_guard_kwargs()
    triggers = guard_kwargs["triggers"]
    assert isinstance(triggers, list)
    _assert_nonopening_stocktake_start_guards(**guard_kwargs)

    constraint_query = " ".join(
        str(_STOCKTAKE_START_COMPLETION_CONSTRAINT_SQL).split()
    )
    assert "constraint_row.contype IN ('c', 'f', 'p', 'u')" in constraint_query

    immediate_name = "trg_stocktake_start_completions_guard_0047"
    assert EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS[immediate_name][3:] == (
        31,
        False,
        False,
        False,
    )
    deferred = {
        name: expected
        for name, expected in EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS.items()
        if name.endswith("_causality_0047")
    }
    assert len(deferred) == 8
    assert {expected[0] for expected in deferred.values()} == {
        "stocktake_tasks",
        "stocktake_scopes",
        "inventory_freezes",
        "stocktake_snapshot_lines",
        "stocktake_rounds",
        "stocktake_start_completions",
        "state_transition_events",
        "audit_events",
    }
    assert {expected[3:] for expected in deferred.values()} == {
        (29, True, True, True)
    }
    sealed = {
        name: expected
        for name, expected in EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS.items()
        if name.endswith("_sealed_0047")
    }
    assert len(sealed) == 7
    assert {expected[3:] for expected in sealed.values()} == {
        (31, False, False, False)
    }

    trigger_query = " ".join(
        str(_NONOPENING_STOCKTAKE_START_TRIGGER_SQL).split()
    )
    for required in (
        "stocktake_start_completions",
        "rsc_guard_stocktake_start_completion_0047",
        "rsc_validate_nonopening_stocktake_start_causality_0047",
        "rsc_dispatch_nonopening_stocktake_start_causality_0047",
        "trigger_row.tgname LIKE '%0047'",
        "NOT trigger_row.tgisinternal",
    ):
        assert required in trigger_query

    for field, value in (
        ("function_schema", "attacker"),
        ("enabled", "D"),
        ("trigger_type", 0),
        ("is_constraint_trigger", False),
        ("is_deferrable", False),
        ("is_initially_deferred", False),
        ("has_when_clause", True),
        ("has_column_filter", True),
    ):
        drifted = [dict(row) for row in triggers]
        target = next(
            row
            for row in drifted
            if row["trigger_name"] != immediate_name
        )
        target[field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake start"):
            _assert_nonopening_stocktake_start_guards(
                **{**guard_kwargs, "triggers": drifted}
            )

    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake start"):
        _assert_nonopening_stocktake_start_guards(
            **{**guard_kwargs, "triggers": triggers[:-1]}
        )
    extra = dict(triggers[0])
    extra["trigger_name"] = "trg_unapproved_stocktake_start_0047"
    with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake start"):
        _assert_nonopening_stocktake_start_guards(
            **{**guard_kwargs, "triggers": [*triggers, extra]}
        )

    for collection_name, field, value in (
        ("columns", "default_expression", "'forged'::text"),
        ("columns", "is_not_null", False),
        ("constraints", "constraint_type", "x"),
        ("constraints", "definition", "CHECK (true)"),
        ("indexes", "is_unique", True),
        ("indexes", "owner_name", "attacker"),
    ):
        drifted_rows = [dict(row) for row in guard_kwargs[collection_name]]
        drifted_rows[0][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError, match="stocktake start"):
            _assert_nonopening_stocktake_start_guards(
                **{**guard_kwargs, collection_name: drifted_rows}
            )


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
        "stocktake_start_completions",
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
        "stocktake_start_completions",
        "stocktake_tasks",
    }
    assert required_inserts <= RUNTIME_INSERT_TABLES
    assert RUNTIME_UPDATE_COLUMNS["stocktake_tasks"] == {
        "cutoff_ledger_cursor",
        "cutoff_at",
        "snapshot_manifest_sha256",
        "issued_at",
        "frozen_at",
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
            "returns_set": False,
            "variadic_type": 0,
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
                and function_name != "rsc_oam_receipt_rls_check_0082"
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

    drift_target = allowed_rows[0]
    for field, value in (
        ("can_execute", not bool(drift_target["can_execute"])),
        ("api_execute_is_grantable", True),
        ("unexpected_execute_grantee_count", 1),
        (
            "volatility",
            "i" if drift_target["volatility"] != "i" else "v",
        ),
        (
            "is_security_definer",
            not bool(drift_target["is_security_definer"]),
        ),
        ("argument_types", "text"),
        ("function_kind", "p"),
        ("result_type", "record"),
        ("returns_set", True),
        ("variadic_type", 25),
        ("argument_modes", ["i", "o"]),
        ("argument_default_count", 1),
        ("is_strict", None),
        ("source_body", "drifted helper body"),
        ("language_name", "internal"),
        ("configuration", ["search_path=public"]),
        ("parallel_safety", "s"),
        ("is_leakproof", True),
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
        and row["function_name"] != "rsc_oam_receipt_rls_check_0082"
    )
    receipt_index = next(index for index, row in enumerate(allowed_rows)
                         if row["function_name"] == "rsc_oam_receipt_rls_check_0082")
    for field, value in (("can_execute", True), ("edge_receiver_can_execute", True),
                         ("projector_can_execute", False), ("source_body", "tampered")):
        drifted = [row.copy() for row in allowed_rows]
        drifted[receipt_index][field] = value
        with pytest.raises(DatabaseSecurityBoundaryError):
            _assert_runtime_function_acl(drifted, expected_migration_role="star_oam_migrator")
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


def test_0057_difference_replay_runtime_and_guard_manifests_are_exact() -> None:
    migration = _load_nonopening_difference_replay_lock_migration_0057()
    source = NONOPENING_DIFFERENCE_REPLAY_LOCK_MIGRATION_0057.read_text(
        encoding="utf-8"
    )
    lock_coordinate = (
        migration.LOCK_FUNCTION,
        "uuid, uuid, text",
    )
    assert RUNTIME_EXECUTE_FUNCTIONS[lock_coordinate] == (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    )
    assert RUNTIME_FUNCTION_SHAPES[lock_coordinate] == ("f", "void", False)
    lock_sql = migration._postgresql_lock_function_sql()
    lock_body = lock_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(lock_body.encode("utf-8")).hexdigest() == (
        migration.LOCK_BODY_SHA256
    )
    assert RUNTIME_FUNCTION_BODY_SHA256[lock_coordinate] == (
        migration.LOCK_BODY_SHA256
    )

    internal_functions = {
        (migration.SEAL_FUNCTION, ""): (
            migration._postgresql_seal_function_sql(),
            migration.SEAL_BODY_SHA256,
        ),
        (migration.CONTROL_GUARD_FUNCTION, ""): (
            migration._postgresql_control_guard_function_sql(),
            migration.CONTROL_GUARD_BODY_SHA256,
        ),
    }
    for coordinate, (function_sql, expected_hash) in internal_functions.items():
        body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == expected_hash
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
        assert FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == (
            expected_hash
        )
        assert coordinate not in RUNTIME_EXECUTE_FUNCTIONS

    assert EXPECTED_FORMAL_FILE_TRIGGERS[migration.SEAL_TRIGGER] == (
        "document_attachments",
        migration.SEAL_FUNCTION,
        "A",
        7,
    )
    assert EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS[
        migration.CONTROL_GUARD_TRIGGER
    ] == (
        "stocktake_control_snapshot_lines",
        migration.CONTROL_GUARD_FUNCTION,
        "A",
        7,
        False,
        False,
        False,
    )
    assert set(RUNTIME_FUNCTION_SHAPES) == set(RUNTIME_EXECUTE_FUNCTIONS)
    assert set(RUNTIME_FUNCTION_BODY_SHA256) == set(RUNTIME_EXECUTE_FUNCTIONS)
    assert set(FORMAL_FILE_INTERNAL_FUNCTION_SHAPES) == set(
        FORMAL_FILE_INTERNAL_FUNCTIONS
    )
    assert set(FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256) == set(
        FORMAL_FILE_INTERNAL_FUNCTIONS
    )
    assert (
        "GRANT EXECUTE ON FUNCTION {LOCK_SIGNATURE} TO {PRODUCTION_API_ROLE}"
        in source
    )
    assert "GRANT INSERT ON TABLE" not in source


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
            "table_schema": "public",
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
    assert "function_row.proname IN" in query
    exact_callers = {
        expected[1]
        for expected in EXPECTED_OPENING_TERMINAL_TRIGGERS.values()
    }
    for caller in exact_callers:
        assert f"'{caller}'" in query
    assert "oidvectortypes(function_row.proargtypes) = ''" in query

    for field, value in (
        ("table_name", "stocktake_scopes"),
        ("function_name", "rsc_unapproved_opening_guard"),
        ("enabled", "D"),
        ("table_schema", "attacker"),
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

    representative_names = (
        "trg_stocktake_reviews_difference_completion_0016",
        "trg_stocktake_count_lines_current_0052",
        "trg_stocktake_count_lines_graph_0052",
    )
    for trigger_name in representative_names:
        target_index = next(
            index
            for index, row in enumerate(rows)
            if row["trigger_name"] == trigger_name
        )
        target = rows[target_index]
        for field, value in (
            ("trigger_type", 0),
            (
                "is_constraint_trigger",
                not bool(target["is_constraint_trigger"]),
            ),
            ("is_deferrable", not bool(target["is_deferrable"])),
            (
                "is_initially_deferred",
                not bool(target["is_initially_deferred"]),
            ),
        ):
            drifted = [row.copy() for row in rows]
            drifted[target_index][field] = value
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

    extra_alias = rows[0].copy()
    extra_alias["trigger_name"] = "trg_unapproved_opening_caller_alias"
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="opening terminal trigger",
    ):
        _assert_opening_terminal_triggers([*rows, extra_alias])


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
    missing_rows = _valid_audit_trigger_rows()
    missing_name = missing_rows[0]["trigger_name"]
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match=rf"trigger_set\.missing=.*{missing_name}",
    ):
        _assert_audit_trigger_guards(missing_rows[1:])

    unexpected = _valid_audit_trigger_rows()[0].copy()
    unexpected["trigger_name"] = "trg_unapproved_audit_probe"
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match=r"trigger_set\.unexpected=.*trg_unapproved_audit_probe",
    ):
        _assert_audit_trigger_guards(
            [*_valid_audit_trigger_rows(), unexpected]
        )


def test_audit_trigger_inventory_covers_cross_domain_manifests() -> None:
    cross_domain: dict[str, tuple[object, ...]] = {}

    for opening_name in (
        "trg_audit_events_opening_commit_0022",
        "trg_audit_events_opening_graph_0052",
    ):
        table_name, function_name, enabled, trigger_type = (
            EXPECTED_OPENING_TERMINAL_TRIGGERS[opening_name]
        )
        assert enabled == "A"
        assert opening_name in OPENING_COMMIT_TRIGGER_NAMES
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

    start_name = "trg_audit_events_stocktake_start_causality_0047"
    (
        table_name,
        function_name,
        enabled,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    ) = EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS[start_name]
    assert enabled == "A"
    cross_domain[start_name] = (
        table_name,
        function_name,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    )

    supply_name = "trg_audit_events_supply_causality_0059"
    (
        table_name,
        function_name,
        enabled,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    ) = EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[supply_name]
    assert enabled == "A"
    cross_domain[supply_name] = (
        table_name,
        function_name,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    )

    assert set(cross_domain) == {
        "trg_audit_events_opening_commit_0022",
        "trg_audit_events_opening_graph_0052",
        "trg_reconciliation_audit_effect_guard_0026",
        "trg_reconciliation_audit_effect_no_truncate_0026",
        "trg_audit_events_cancellation_graph_0037",
        "trg_audit_events_nonopening_stocktake_close_guard_0038",
        "trg_audit_events_approval_projection_0045",
        "trg_audit_events_supply_causality_0059",
        "trg_audit_events_stocktake_start_causality_0047",
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
            "argument_count": 0,
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
        "trg_stocktake_scopes_stocktake_start_sealed_0047",
        "trg_stocktake_scopes_stocktake_start_causality_0047",
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
    assert EXPECTED_STOCKTAKE_SCOPE_TRIGGERS[
        "trg_stocktake_scopes_stocktake_start_sealed_0047"
    ] == (
        "stocktake_scopes",
        "rsc_guard_stocktake_start_completion_0047",
        "A",
        31,
        False,
        False,
        False,
    )
    assert EXPECTED_STOCKTAKE_SCOPE_TRIGGERS[
        "trg_stocktake_scopes_stocktake_start_causality_0047"
    ] == (
        "stocktake_scopes",
        "rsc_dispatch_nonopening_stocktake_start_causality_0047",
        "A",
        29,
        True,
        True,
        True,
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
        "stocktake_control_snapshot_lines",
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
        "trg_stocktake_control_snapshot_00_nonopening_0057",
        "trg_stocktake_control_snapshot_lines_immutable_0010",
        "trg_stocktake_control_snapshot_lines_sealed_insert_0010",
        "trg_stock_locations_stocktake_personal_continuity_0020",
        "trg_stocktake_count_lines_submitted_immutable_0010",
        "trg_stocktake_count_lines_immutable_0011",
        "trg_stocktake_count_lines_assignment_0021",
        "trg_stocktake_count_lines_current_0052",
        "trg_stocktake_count_lines_graph_0052",
        "trg_stocktake_count_observations_immutable_0011",
        "trg_stocktake_count_observations_assignment_0021",
        "trg_stocktake_count_observations_current_0052",
        "trg_stocktake_count_observations_graph_0052",
        "trg_stocktake_scope_count_completions_immutable_0011",
        "trg_stocktake_scope_completions_assignment_0021",
        "trg_stocktake_scope_count_completions_current_0052",
        "trg_stocktake_scope_count_completions_graph_0052",
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
        "trg_stocktake_control_snapshot_00_nonopening_0057": "A",
        "trg_stocktake_control_snapshot_lines_immutable_0010": "O",
        "trg_stocktake_control_snapshot_lines_sealed_insert_0010": "O",
        "trg_stock_locations_stocktake_personal_continuity_0020": "A",
        "trg_stocktake_count_lines_submitted_immutable_0010": "O",
        "trg_stocktake_count_lines_immutable_0011": "O",
        "trg_stocktake_count_lines_assignment_0021": "A",
        "trg_stocktake_count_lines_current_0052": "O",
        "trg_stocktake_count_lines_graph_0052": "A",
        "trg_stocktake_count_observations_immutable_0011": "O",
        "trg_stocktake_count_observations_assignment_0021": "A",
        "trg_stocktake_count_observations_current_0052": "O",
        "trg_stocktake_count_observations_graph_0052": "A",
        "trg_stocktake_scope_count_completions_immutable_0011": "O",
        "trg_stocktake_scope_completions_assignment_0021": "A",
        "trg_stocktake_scope_count_completions_current_0052": "O",
        "trg_stocktake_scope_count_completions_graph_0052": "A",
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


def _load_material_request_draft_content_migration_0046() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0046_material_request_content_security_manifest",
        MATERIAL_REQUEST_DRAFT_CONTENT_MIGRATION_0046,
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
                    "text" if coordinate in {("rsc_material_request_reservation_state_0070", "uuid, bigint"), ("rsc_material_request_picking_state_0071", "uuid, bigint"), ("rsc_material_request_outbound_state_0072", "uuid, bigint")} else
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


def test_runtime_approval_function_selector_covers_every_manifest_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.database_security import _select_material_request_approval_functions

    functions = _valid_material_request_approval_function_rows(monkeypatch)
    unrelated = {"function_name": "rsc_non_supply_function_0015"}
    selected = _select_material_request_approval_functions([
        *functions, unrelated, {"function_name": None},
    ])
    assert selected == functions
    assert {
        (row["function_name"], row["argument_types"]) for row in selected
    } == set(MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256)
    _assert_valid_material_request_approval_catalog(monkeypatch, functions=selected)


@pytest.mark.parametrize("suffix", ["0059", "0060", "0069"])
def test_runtime_approval_function_selector_does_not_hide_unknown_supply_function(
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    from app.database_security import _select_material_request_approval_functions

    functions = _valid_material_request_approval_function_rows(monkeypatch)
    unexpected = {**functions[0], "function_name": f"rsc_unreviewed_supply_{suffix}"}
    selected = _select_material_request_approval_functions([*functions, unexpected])
    assert unexpected in selected
    with pytest.raises(DatabaseSecurityBoundaryError, match="function_set"):
        _assert_valid_material_request_approval_catalog(monkeypatch, functions=selected)


def _valid_material_request_content_manifest_columns(
) -> list[dict[str, object]]:
    return [dict(EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN)]


def _valid_material_request_content_manifest_checks(
) -> list[dict[str, object]]:
    return [
        {
            **EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK,
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": False,
            "is_local": True,
            "inheritance_count": 0,
            "parent_constraint_id": 0,
            "definition": (
                "CHECK ((operation IN ('create', 'update_draft', 'submit') "
                "AND projection_manifest_sha256 IS NOT NULL AND "
                "projection_manifest_sha256 ~ '^[0-9a-f]{64}$') OR "
                "(operation NOT IN ('create', 'update_draft', 'submit') "
                "AND projection_manifest_sha256 IS NULL))"
            ),
            "backing_index_name": None,
        }
    ]


def _assert_valid_material_request_approval_catalog(
    monkeypatch: pytest.MonkeyPatch,
    *,
    triggers: list[dict[str, object]] | None = None,
    functions: list[dict[str, object]] | None = None,
    manifest_columns: list[dict[str, object]] | None = None,
    manifest_checks: list[dict[str, object]] | None = None,
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
        manifest_columns=(
            _valid_material_request_content_manifest_columns()
            if manifest_columns is None
            else manifest_columns
        ),
        manifest_checks=(
            _valid_material_request_content_manifest_checks()
            if manifest_checks is None
            else manifest_checks
        ),
        expected_migration_role="star_oam_migrator",
    )


def test_0046_material_request_guard_catalog_accepts_exact_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    triggers = _valid_material_request_approval_trigger_rows()
    functions = _valid_material_request_approval_function_rows(monkeypatch)

    assert len(triggers) == 127
    assert len(functions) == 51
    _assert_material_request_approval_guards(
        triggers=triggers,
        functions=functions,
        manifest_columns=_valid_material_request_content_manifest_columns(),
        manifest_checks=_valid_material_request_content_manifest_checks(),
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
                manifest_columns=(
                    _valid_material_request_content_manifest_columns()
                ),
                manifest_checks=_valid_material_request_content_manifest_checks(),
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
            manifest_columns=_valid_material_request_content_manifest_columns(),
            manifest_checks=_valid_material_request_content_manifest_checks(),
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
            manifest_columns=_valid_material_request_content_manifest_columns(),
            manifest_checks=_valid_material_request_content_manifest_checks(),
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
            manifest_columns=_valid_material_request_content_manifest_columns(),
            manifest_checks=_valid_material_request_content_manifest_checks(),
            expected_migration_role="star_oam_migrator",
        )


def test_0046_material_request_guard_trigger_query_captures_complete_scope(
) -> None:
    query = " ".join(str(_MATERIAL_REQUEST_APPROVAL_TRIGGER_SQL).split())

    assert "trigger_row.tgname ~ '_(0029|0030|0045|0046|0059|0060|0069|0070|0071|0072)$'" in query
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
    } == {"0029", "0030", "0045", "0046", "0059", "0060", "0069", "0070", "0071", "0072", "0087"}


def test_0069_reservation_guard_bodies_match_runtime_manifest(monkeypatch):
    migration = _load_stock_reservations_migration_0069()
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)
    migration._create_postgresql_reservation_guards()
    functions = {}
    for sql in statements:
        if "CREATE FUNCTION public." in sql:
            name = sql.split("CREATE FUNCTION public.", 1)[1].split("(", 1)[0]
            body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
            functions[(name, "")] = hashlib.sha256(body.encode()).hexdigest()
    assert functions == {
        coordinate: body_hash
        for coordinate, body_hash in MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256.items()
        if coordinate[0].endswith("_0069")
    }
    assert len(functions) == 2
    assert set(functions) <= MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS


@pytest.mark.parametrize("name", [
    "rsc_guard_stock_reservation_0069",
    "rsc_guard_stock_reservation_serials_binding_0069",
])
@pytest.mark.parametrize(("field", "value"), [
    ("source_body", "tampered reservation guard"),
    ("is_security_definer", False),
    ("owner_name", "star_oam_api"),
    ("can_execute", True),
    ("public_can_execute", True),
    ("configuration", ["search_path=public"]),
])
def test_0069_reservation_catalog_rejects_drift(monkeypatch, name, field, value):
    functions = _valid_material_request_approval_function_rows(monkeypatch)
    next(row for row in functions if row["function_name"] == name)[field] = value
    with pytest.raises(DatabaseSecurityBoundaryError, match=name):
        _assert_valid_material_request_approval_catalog(monkeypatch, functions=functions)


def test_0045_material_request_approval_migration_bindings_match_manifest(
) -> None:
    migration = _load_material_request_approval_activation_migration_0045()
    manifest_bindings = {
        name: expected[0]
        for name, expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.items()
        if name.endswith(("_0029", "_0030", "_0045"))
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
    migration_0059 = _load_material_request_supply_causality_migration_0059()
    function_sql = {
        (
            migration.PG_DECISION_FUNCTION_0029,
            "",
        ): migration._decision_guard_sql(repaired=True),
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

    assert len(MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256) == 51
    assert set(function_sql) == {
        coordinate
        for coordinate in MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
        if coordinate[0].endswith("_0045")
    } | {(migration.PG_DECISION_FUNCTION_0029, "")}
    for coordinate, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        actual_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if coordinate == (
            migration.PG_TERMINAL_VALIDATE_FUNCTION,
            "uuid, uuid, uuid, bigint",
        ):
            assert actual_hash == migration_0059.TERMINAL_BODY_SHA256_0045
        elif coordinate == (migration.PG_PROJECTION_VALIDATE_FUNCTION, "uuid"):
            assert actual_hash == migration_0059.PROJECTION_BODY_SHA256_0045
        else:
            assert actual_hash == (
                MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]
            )
    assert {
        coordinate
        for coordinate in MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        if coordinate[0].endswith(("_0029", "_0030", "_0045"))
    } == {
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
    assert {
        coordinate
        for coordinate in MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
        if coordinate[0].endswith(("_0029", "_0030", "_0045"))
    } == {
        (migration.PG_APPROVAL_VALIDATE_FUNCTION_0030, "uuid"),
        (
            migration.PG_TERMINAL_VALIDATE_FUNCTION,
            "uuid, uuid, uuid, bigint",
        ),
        (migration.PG_RETURN_VALIDATE_FUNCTION, "uuid, uuid"),
        (migration.PG_EXTERNAL_VALIDATE_FUNCTION, "uuid"),
        (migration.PG_PROJECTION_VALIDATE_FUNCTION, "uuid"),
    }


def test_0046_material_request_content_bindings_match_manifest() -> None:
    migration = _load_material_request_draft_content_migration_0046()
    manifest_bindings = {
        name: expected[0]
        for name, expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.items()
        if name.endswith("_0046")
    }
    migration_bindings = {
        trigger_name: table_name
        for table_name, trigger_name in migration.TRIGGER_BINDINGS
    }

    assert migration.down_revision == "20260903_0045"
    assert len(migration.IMMEDIATE_TRIGGER_BINDINGS) == 4
    assert len(migration.DEFERRED_TRIGGER_BINDINGS) == 4
    assert len(migration.TRIGGER_BINDINGS) == 8
    assert migration_bindings == manifest_bindings
    for table_name, trigger_name in migration.IMMEDIATE_TRIGGER_BINDINGS:
        expected = EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[trigger_name]
        assert expected == (
            table_name,
            migration.PG_CONTENT_GUARD_FUNCTION,
            "A",
            7 if table_name == "material_request_commands" else 31,
            False,
            False,
            False,
        )
    for table_name, trigger_name in migration.DEFERRED_TRIGGER_BINDINGS:
        assert EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[trigger_name] == (
            table_name,
            migration.PG_CONTENT_DISPATCH_FUNCTION,
            "A",
            29,
            True,
            True,
            True,
        )


def test_0046_material_request_content_function_bodies_match_manifest() -> None:
    migration = _load_material_request_draft_content_migration_0046()
    function_sql = {
        (migration.PG_CONTENT_GUARD_FUNCTION, ""): migration._content_guard_sql(),
        (
            migration.PG_CONTENT_VALIDATE_FUNCTION,
            "uuid",
        ): migration._content_validator_sql(),
        (
            migration.PG_CONTENT_DISPATCH_FUNCTION,
            "",
        ): migration._content_dispatcher_sql(),
    }

    assert set(function_sql) == {
        coordinate
        for coordinate in MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
        if coordinate[0].endswith("_0046")
    }
    for coordinate, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]
        )
        assert "SECURITY DEFINER" in sql
        assert "SET search_path = pg_catalog, public" in sql
    assert set(function_sql) <= MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS & set(function_sql) == {
        (migration.PG_CONTENT_VALIDATE_FUNCTION, "uuid")
    }


def test_0059_supply_guard_bodies_triggers_and_runtime_acl_match_manifest() -> None:
    migration_0045 = _load_material_request_approval_activation_migration_0045()
    migration = _load_material_request_supply_causality_migration_0059()
    migration_0069 = _load_stock_reservations_migration_0069()

    function_sql = {
        (migration.OWNER_GUARD_FUNCTION, ""): migration._owner_guard_sql(),
        (
            migration.SUPPLY_VALIDATE_FUNCTION,
            "uuid, bigint",
        ): migration._supply_validator_sql(),
        (migration.SUPPLY_DISPATCH_FUNCTION, ""): migration._supply_dispatcher_sql(),
    }
    historical_hashes = {
        (migration.OWNER_GUARD_FUNCTION, ""):
            "913d606ff9f47fd05feda92d75ef76477daf6823b71ecdb9c47cabf5355a5398",
        (migration.SUPPLY_VALIDATE_FUNCTION, "uuid, bigint"):
            "ce370ea355224013645f399b2176aceedf92e51c01f8159866a822bb33120f46",
        (migration.SUPPLY_DISPATCH_FUNCTION, ""):
            "efab0c6eee9c8fbaccb1e334b0dc28d10fc85a4fb097a508b422c30b0cb034ed",
    }
    for coordinate, sql in function_sql.items():
        body = sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            historical_hashes[coordinate]
        )
        assert coordinate in MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert (migration.SUPPLY_VALIDATE_FUNCTION, "uuid, bigint") in (
        MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
    )

    terminal_sql = migration_0045._terminal_validator_sql().replace(
        migration.TERMINAL_LEGACY_FRAGMENT,
        migration.TERMINAL_FIXED_FRAGMENT,
    )
    terminal_body = terminal_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(terminal_body.encode("utf-8")).hexdigest() == (
        MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[
            (migration.TERMINAL_FUNCTION, "uuid, uuid, uuid, bigint")
        ]
    )

    projection_sql = migration_0045._projection_validator_sql()
    for legacy, fixed in (
        (migration.PROJECTION_DECLARATION_LEGACY, migration.PROJECTION_DECLARATION_FIXED),
        (
            migration.PROJECTION_TERMINAL_CALL_LEGACY,
            migration.PROJECTION_TERMINAL_CALL_FIXED,
        ),
        (
            migration.PROJECTION_APPROVED_RETURN_LEGACY,
            migration.PROJECTION_APPROVED_RETURN_FIXED,
        ),
        (
            migration.PROJECTION_CANCEL_TERMINAL_CALL_LEGACY,
            migration.PROJECTION_CANCEL_TERMINAL_CALL_FIXED,
        ),
    ):
        projection_sql = projection_sql.replace(legacy, fixed)
    projection_body = projection_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(projection_body.encode("utf-8")).hexdigest() == (
        migration.PROJECTION_BODY_SHA256_0059
    )
    _assert_0069_function_body_matches_runtime_manifest(
        coordinate=(migration.PROJECTION_FUNCTION, "uuid"),
        historical_body=projection_body,
        historical_hash=migration_0069.APPROVAL_PROJECTION_BODY_SHA256_0059,
        current_hash=migration_0069.APPROVAL_PROJECTION_BODY_SHA256_0069,
        replacements=((
            migration_0069.APPROVAL_PROJECTION_APPROVED_OLD,
            migration_0069.APPROVAL_PROJECTION_APPROVED_NEW,
        ),),
    )

    expected_bindings = {
        migration.OWNER_GUARD_TRIGGER: (
            "supply_tasks",
            migration.OWNER_GUARD_FUNCTION,
            "A",
            31,
            False,
            False,
            False,
        ),
        **{
            trigger_name: (
                table_name,
                migration.SUPPLY_DISPATCH_FUNCTION,
                "A",
                29,
                True,
                True,
                True,
            )
            for table_name, trigger_name in migration.SUPPLY_TRIGGER_BINDINGS
        },
    }
    assert {
        name: EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]
        for name in expected_bindings
    } == expected_bindings
    assert "supply_tasks" in RUNTIME_INSERT_TABLES
    assert "supply_tasks" not in RUNTIME_UPDATE_TABLES
    assert RUNTIME_UPDATE_COLUMNS["supply_tasks"] == {
        "reference_no",
        "expected_date",
        "status",
        "cancelled_by_user_id",
        "cancelled_at",
        "version",
        "updated_at",
    }


def test_0060_supply_security_hashes_and_triggers_match_historical_body() -> None:
    old = _load_material_request_supply_causality_migration_0059()
    migration = _load_material_request_supply_security_migration_0060()
    migration_0069 = _load_stock_reservations_migration_0069()

    guard_body = migration._write_guard_sql().split("AS $$", 1)[1].rsplit(
        "$$", 1
    )[0]
    assert hashlib.sha256(guard_body.encode()).hexdigest() == (
        MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[
            (migration.WRITE_GUARD_FUNCTION, "")
        ]
    )

    validator = old._supply_validator_sql().split("AS $$", 1)[1].rsplit(
        "$$", 1
    )[0]
    for legacy, fixed in (
        (migration.VALIDATOR_DECLARATION_0059, migration.VALIDATOR_DECLARATION_0060),
        (migration.VALIDATOR_ACTOR_0059, migration.VALIDATOR_ACTOR_0060),
        (migration.VALIDATOR_TASK_COUNT_0059, migration.VALIDATOR_TASK_COUNT_0060),
        (migration.VALIDATOR_TASK_IF_0059, migration.VALIDATOR_TASK_IF_0060),
        (migration.VALIDATOR_ORDERED_0059, migration.VALIDATOR_ORDERED_0060),
        (migration.VALIDATOR_SEQUENCE_0059, migration.VALIDATOR_SEQUENCE_0060),
    ):
        validator = validator.replace(legacy, fixed)
    assert hashlib.sha256(validator.encode()).hexdigest() == (
        migration.VALIDATOR_BODY_SHA256_0060
    )

    dispatcher = old._supply_dispatcher_sql().split("AS $$", 1)[1].rsplit(
        "$$", 1
    )[0].replace(
        migration._dispatcher_body_0059(), migration._dispatcher_body_0060()
    )
    assert hashlib.sha256(dispatcher.encode()).hexdigest() == (
        migration.DISPATCHER_BODY_SHA256_0060
    )
    _assert_0069_function_body_matches_runtime_manifest(
        coordinate=(migration.DISPATCHER_FUNCTION, ""),
        historical_body=dispatcher,
        historical_hash=migration_0069.SUPPLY_DISPATCH_BODY_SHA256_0060,
        current_hash=migration_0069.SUPPLY_DISPATCH_BODY_SHA256_0069,
        replacements=((
            migration_0069._dispatcher_body_0060(),
            migration_0069._dispatcher_body_0069(),
        ),),
    )
    assert (migration.WRITE_GUARD_FUNCTION, "") in (
        MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    )
    assert EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[
        migration.COMMAND_GUARD_TRIGGER
    ][:4] == (
        "material_request_commands", migration.WRITE_GUARD_FUNCTION, "A", 7
    )
    assert EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[
        migration.TASK_GUARD_TRIGGER
    ][:4] == (
        "supply_tasks", migration.WRITE_GUARD_FUNCTION, "A", 7
    )


def test_0061_supply_event_key_history_and_0069_runtime_manifest() -> None:
    migration_0059 = _load_material_request_supply_causality_migration_0059()
    migration_0060 = _load_material_request_supply_security_migration_0060()
    migration = _load_material_request_supply_event_key_migration_0061()
    migration_0069 = _load_stock_reservations_migration_0069()

    validator = migration_0059._supply_validator_sql().split(
        "AS $$", 1
    )[1].rsplit("$$", 1)[0]
    for legacy, fixed in (
        (
            migration_0060.VALIDATOR_DECLARATION_0059,
            migration_0060.VALIDATOR_DECLARATION_0060,
        ),
        (
            migration_0060.VALIDATOR_ACTOR_0059,
            migration_0060.VALIDATOR_ACTOR_0060,
        ),
        (
            migration_0060.VALIDATOR_TASK_COUNT_0059,
            migration_0060.VALIDATOR_TASK_COUNT_0060,
        ),
        (
            migration_0060.VALIDATOR_TASK_IF_0059,
            migration_0060.VALIDATOR_TASK_IF_0060,
        ),
        (
            migration_0060.VALIDATOR_ORDERED_0059,
            migration_0060.VALIDATOR_ORDERED_0060,
        ),
        (
            migration_0060.VALIDATOR_SEQUENCE_0059,
            migration_0060.VALIDATOR_SEQUENCE_0060,
        ),
    ):
        validator = validator.replace(legacy, fixed)
    assert hashlib.sha256(validator.encode()).hexdigest() == (
        migration.PRIOR_VALIDATOR_BODY_SHA256
    )
    validator = validator.replace(
        migration.LEGACY_EVENT_KEY_EXPRESSION,
        migration.FIXED_EVENT_KEY_EXPRESSION,
    )
    assert hashlib.sha256(validator.encode()).hexdigest() == (
        migration.FIXED_VALIDATOR_BODY_SHA256
    )
    assert migration.FIXED_VALIDATOR_BODY_SHA256 == (
        migration_0069.SUPPLY_VALIDATE_BODY_SHA256_0061
    )
    _assert_0069_function_body_matches_runtime_manifest(
        coordinate=("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"),
        historical_body=validator,
        historical_hash=migration_0069.SUPPLY_VALIDATE_BODY_SHA256_0061,
        current_hash=migration_0069.SUPPLY_VALIDATE_BODY_SHA256_0069,
        replacements=(
            (migration_0069.SUPPLY_VALIDATE_CEILING_OLD, migration_0069.SUPPLY_VALIDATE_CEILING_NEW),
            (migration_0069.SUPPLY_VALIDATE_NEUTRAL_OLD, migration_0069.SUPPLY_VALIDATE_NEUTRAL_NEW),
            (migration_0069.SUPPLY_VALIDATE_STATE_AXES_OLD, migration_0069.SUPPLY_VALIDATE_STATE_AXES_NEW),
        ),
    )
    from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061

    assert migration.RUNTIME_READY_BODY_SHA256_0061 == (
        OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0061[
            "rsc_oam_runtime_binding_ready_0044()"
        ][6]
    )


def test_0069_request_guard_body_matches_runtime_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "rsc_migration_0029_request_guard_security_manifest",
        STOCK_RESERVATIONS_MIGRATION_0069.parent
        / "20260831_0029_material_request_approval_domain.py",
    )
    assert spec is not None and spec.loader is not None
    migration_0029 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration_0029)
    migration_0069 = _load_stock_reservations_migration_0069()
    statements: list[str] = []
    monkeypatch.setattr(migration_0029.op, "execute", statements.append)
    migration_0029._create_postgresql_guards()
    request_guard_sql = next(
        sql for sql in statements
        if f"CREATE FUNCTION {migration_0069.REQUEST_GUARD_SIGNATURE}" in sql
    )
    _assert_0069_function_body_matches_runtime_manifest(
        coordinate=(migration_0069.REQUEST_GUARD_FUNCTION, ""),
        historical_body=request_guard_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0],
        historical_hash=migration_0069.REQUEST_GUARD_BODY_SHA256_0029,
        current_hash=migration_0069.REQUEST_GUARD_BODY_SHA256_0069,
        replacements=((
            migration_0069.REQUEST_GUARD_AXIS_OLD,
            migration_0069.REQUEST_GUARD_AXIS_NEW,
        ),),
    )


def test_0046_content_guard_seals_approval_attempt_coordinate() -> None:
    migration = _load_material_request_draft_content_migration_0046()
    guard_sql = " ".join(migration._content_guard_sql().split())
    manifest_sql = migration._projection_manifest_expression("guard_revision.id")

    assert (
        "NEW.operation IN ('create', 'update_draft') AND "
        "NEW.request_jsonb->'approval_attempt_no' IS DISTINCT FROM "
        "'null'::jsonb"
    ) in guard_sql
    for document in ("request", "result"):
        assert (
            f"jsonb_typeof(NEW.{document}_jsonb->'approval_attempt_no') "
            "IS DISTINCT FROM 'number'"
        ) in guard_sql
        assert (
            f"NEW.{document}_jsonb->>'approval_attempt_no' "
            "!~ '^[1-9][0-9]*$'"
        ) in guard_sql
    assert (
        "NEW.request_jsonb->>'approval_attempt_no' IS DISTINCT FROM "
        "NEW.result_jsonb->>'approval_attempt_no'"
    ) in guard_sql
    assert (
        "IF NEW.operation = 'submit' AND NOT EXISTS ( SELECT 1 FROM "
        "public.approval_instances AS approval_instance WHERE "
        "approval_instance.id::text = "
        "NEW.result_jsonb->>'approval_instance_id' AND "
        "approval_instance.request_id = guard_request.id AND "
        "approval_instance.request_revision_id = guard_revision.id AND "
        "approval_instance.revision_no = guard_revision.revision_no AND "
        "approval_instance.attempt_no::text = "
        "NEW.request_jsonb->>'approval_attempt_no' AND "
        "approval_instance.created_at = NEW.occurred_at ) THEN"
    ) in guard_sql
    for approval_coordinate in (
        "approval_attempt_no",
        "approval_instance_id",
        "approval_instances",
        "attempt_no",
        "current_step_no",
    ):
        assert approval_coordinate not in manifest_sql


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_name", "star_oam_api"),
        ("can_execute", True),
        ("api_execute_is_grantable", True),
        ("unexpected_execute_grantee_count", 1),
        ("public_can_execute", True),
        ("is_security_definer", False),
        ("result_type", "trigger"),
        ("configuration", ["search_path=public"]),
    ],
)
def test_0046_content_validator_privilege_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    functions = _valid_material_request_approval_function_rows(monkeypatch)
    target = next(
        row
        for row in functions
        if (row["function_name"], row["argument_types"])
        == ("rsc_validate_material_request_content_causality_0046", "uuid")
    )
    target[field] = value

    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="rsc_validate_material_request_content_causality_0046",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            functions=functions,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("table_name", "material_request_lines"),
        ("column_name", "projection_manifest"),
        ("data_type", "text"),
        ("is_not_null", True),
        ("identity_kind", "a"),
        ("generated_kind", "s"),
        ("default_expression", "repeat('0', 64)"),
        ("comment", "untrusted manifest"),
    ],
)
def test_0046_projection_manifest_column_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    columns = _valid_material_request_content_manifest_columns()
    columns[0][field] = value
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match=rf"projection_manifest_sha256\.{field}",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            manifest_columns=columns,
        )


@pytest.mark.parametrize("drift", ["missing", "extra"])
def test_0046_projection_manifest_column_set_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    columns = _valid_material_request_content_manifest_columns()
    if drift == "missing":
        columns.clear()
    else:
        columns.append({**columns[0], "column_name": "attacker_manifest"})
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="projection_manifest_sha256.column_set",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            manifest_columns=columns,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("constraint_name", "ck_attacker_projection_manifest"),
        ("table_name", "material_request_lines"),
        ("constraint_type", "u"),
        ("constrained_columns", ["projection_manifest_sha256"]),
        ("is_validated", False),
        ("is_deferrable", True),
        ("is_initially_deferred", True),
        ("is_no_inherit", True),
        ("is_local", False),
        ("inheritance_count", 1),
        ("parent_constraint_id", 42),
        ("backing_index_name", "attacker_index"),
    ],
)
def test_0046_projection_manifest_check_shape_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    checks = _valid_material_request_content_manifest_checks()
    checks[0][field] = value
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match=rf"projection_manifest_0046\.{field}",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            manifest_checks=checks,
        )


@pytest.mark.parametrize("drift", ["missing", "extra"])
def test_0046_projection_manifest_check_set_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    checks = _valid_material_request_content_manifest_checks()
    if drift == "missing":
        checks.clear()
    else:
        checks.append(
            {**checks[0], "constraint_name": "ck_attacker_projection_manifest"}
        )
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="projection_manifest_0046.constraint_set",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            manifest_checks=checks,
        )


@pytest.mark.parametrize(
    "definition",
    [
        "CHECK (TRUE)",
        (
            "CHECK ((operation IN ('create', 'update_draft', 'submit') AND "
            "projection_manifest_sha256 ~ '^[0-9a-f]{64}$') OR TRUE)"
        ),
        (
            "CHECK ((operation IN ('create', 'update_draft', 'submit') AND "
            "projection_manifest_sha256 ~ '^[0-9a-f]{64}$') OR "
            "(operation NOT IN ('create', 'update_draft', 'submit') AND "
            "projection_manifest_sha256 IS NULL))"
        ),
        (
            "CHECK ((operation IN ('create', 'update_draft', 'submit') AND "
            "projection_manifest_sha256 IS NOT NULL AND "
            "projection_manifest_sha256 ~ '^[0-9A-Fa-f]{64}$') OR "
            "(operation NOT IN ('create', 'update_draft', 'submit') AND "
            "projection_manifest_sha256 IS NULL))"
        ),
        (
            "CHECK ((operation IN ('create', 'update_draft') AND "
            "projection_manifest_sha256 IS NOT NULL AND "
            "projection_manifest_sha256 ~ '^[0-9a-f]{64}$') OR "
            "(operation NOT IN ('create', 'update_draft') AND "
            "projection_manifest_sha256 IS NULL))"
        ),
    ],
)
def test_0046_projection_manifest_check_semantic_drift_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    definition: str,
) -> None:
    checks = _valid_material_request_content_manifest_checks()
    checks[0]["definition"] = definition
    with pytest.raises(
        DatabaseSecurityBoundaryError,
        match="projection_manifest_0046.definition",
    ):
        _assert_valid_material_request_approval_catalog(
            monkeypatch,
            manifest_checks=checks,
        )


def test_0046_projection_manifest_check_accepts_postgresql_any_all_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = (
        "CHECK ((((operation)::text = ANY ((ARRAY['create'::character varying, "
        "'update_draft'::character varying, 'submit'::character varying])::text[])) "
        "AND (projection_manifest_sha256 IS NOT NULL) AND "
        "((projection_manifest_sha256)::text ~ '^[0-9a-f]{64}$'::text)) OR "
        "(((operation)::text <> ALL ((ARRAY['create'::character varying, "
        "'update_draft'::character varying, 'submit'::character varying])::text[])) "
        "AND (projection_manifest_sha256 IS NULL))))"
    )
    assert _material_request_content_check_definition_matches(definition)
    checks = _valid_material_request_content_manifest_checks()
    checks[0]["definition"] = definition
    _assert_valid_material_request_approval_catalog(
        monkeypatch,
        manifest_checks=checks,
    )


def test_0046_projection_manifest_catalog_queries_are_exact() -> None:
    column_query = " ".join(
        str(_MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_SQL).split()
    )
    check_query = " ".join(
        str(_MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK_SQL).split()
    )

    assert "table_row.relname = 'material_request_commands'" in column_query
    assert "attribute_row.attname = 'projection_manifest_sha256'" in column_query
    assert "attribute_row.attisdropped" in column_query
    for required in (
        "format_type",
        "attnotnull",
        "attidentity",
        "attgenerated",
        "pg_get_expr",
        "col_description",
    ):
        assert required in column_query
    assert "table_row.relname = 'material_request_commands'" in check_query
    assert (
        "'ck_material_request_commands_projection_manifest_0046'"
        in check_query
    )
    for required in (
        "convalidated",
        "condeferrable",
        "condeferred",
        "connoinherit",
        "conislocal",
        "coninhcount",
        "conparentid",
        "pg_get_constraintdef",
        "constraint_row.conkey",
        "constraint_row.conindid",
    ):
        assert required in check_query
