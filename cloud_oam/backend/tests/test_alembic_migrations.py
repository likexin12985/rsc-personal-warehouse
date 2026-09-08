from __future__ import annotations

import ast
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
INITIAL_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0001_v09_initial_schema.py"
)
FOUNDATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0002_v1_foundation_schema.py"
)
ACTIVATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0003_formal_identity_rbac_activation.py"
)
ROLE_ADMINISTRATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0004_role_assignment_administration.py"
)
AUTHENTICATION_RUNTIME_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0005_formal_authentication_runtime.py"
)
AUTHENTICATION_IDEMPOTENCY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0006_authentication_idempotency_operations.py"
)
ACTIVE_AUTH_SESSION_DEVICE_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0007_active_auth_session_device_family.py"
)
AUTHENTICATION_LOGIN_RATE_LIMIT_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0008_authentication_login_rate_limits.py"
)
INVENTORY_LEDGER_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0009_inventory_ledger_foundation.py"
)
OPENING_STOCKTAKE_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0010_opening_stocktake_foundation.py"
)
OPENING_COUNT_OBSERVATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260830_0011_opening_count_observations.py"
)
OPENING_RESOLVED_SERIAL_UNIQUENESS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0012_unique_observation_resolved_serial.py"
)
OPENING_SERIAL_IDENTIFIER_VALIDATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0013_observation_serial_identifier_validation.py"
)
OPENING_EXPLICIT_SEALING_COMPLETION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0014_explicit_round_sealing_completion.py"
)
IMMUTABLE_AUDIT_EVENTS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0015_immutable_audit_events.py"
)
OPENING_REVIEW_EVIDENCE_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0016_opening_observation_dispositions.py"
)
AUDIT_STREAM_BINDING_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0017_audit_stream_binding.py"
)
STOCKTAKE_RECOUNT_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0018_stocktake_recount_causality.py"
)
TECHNICIAN_PERSONAL_STOCKTAKE_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0019_technician_personal_stocktake_boundary.py"
)
PERSONAL_STOCKTAKE_LOCATION_CONTINUITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0020_personal_stocktake_location_continuity.py"
)
STOCKTAKE_ROUND_ASSIGNMENT_GUARDS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0021_stocktake_round_assignment_guards.py"
)
OPENING_TERMINAL_RUNTIME_BOUNDARY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0022_opening_terminal_runtime_boundary.py"
)
OPENING_OBSERVATION_POSTING_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0023_opening_observation_posting.py"
)
FORMAL_OPENING_RUNTIME_ACL_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0024_formal_opening_runtime_acl.py"
)
STOCKTAKE_SCOPE_REGION_OWNER_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0025_stocktake_scope_region_owner_guard.py"
)
OPENING_CONTROL_RECONCILIATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0026_opening_control_reconciliation.py"
)
POSTGRESQL_LOCK_GRAPH_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0027_postgresql_lock_graph.py"
)
OPENING_TERMINAL_REFERENCE_UNION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0028_opening_terminal_reference_union_lock.py"
)
MATERIAL_REQUEST_APPROVAL_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260831_0029_material_request_approval_domain.py"
)
APPROVAL_CAUSALITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0030_approval_causality_guards.py"
)
STOCKTAKE_DIFFERENCE_EVALUATOR_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0031_stocktake_difference_evaluator.py"
)
NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0032_nonopening_stocktake_review_recount.py"
)
STOCKTAKE_COUNT_LEDGER_BOUNDARY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0033_stocktake_count_ledger_boundary.py"
)
STOCKTAKE_RECOUNT_SELECTED_SCOPE_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0034_stocktake_recount_selected_scope_submission.py"
)
NONOPENING_STOCKTAKE_SAFE_POSTING_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0035_nonopening_stocktake_safe_posting.py"
)
FORMAL_FILE_RUNTIME_BOUNDARY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0036_formal_file_runtime_boundary.py"
)
MATERIAL_REQUEST_CANCELLATION_BOUNDARY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0037_material_request_cancellation_boundary.py"
)
NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0038_nonopening_stocktake_close_reconciliation.py"
)
MATERIAL_REQUEST_COMMAND_STATUS_LOOKUP_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0039_material_request_command_status_lookup.py"
)
KMS_DATA_KEY_PINS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0040_kms_data_key_pins.py"
)
SMS_DISPATCH_OWNERSHIP_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260902_0041_sms_dispatch_ownership.py"
)
MATERIAL_REQUEST_WORK_ORDER_LOCK_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260902_0042_material_request_work_order_lock.py"
)
OAM_WORK_ORDER_PROJECTOR_BOUNDARY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260902_0043_oam_work_order_projector_boundary.py"
)
OAM_SYNC_SCOPE_FORCE_RLS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260902_0044_oam_sync_scope_force_rls.py"
)
MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0045_material_request_approval_activation.py"
)
MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0046_material_request_draft_content_causality.py"
)
NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0047_nonopening_stocktake_start_causality.py"
)
STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0048_stocktake_scope_guard_security.py"
)
STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0049_stocktake_recount_guard_security.py"
)
STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0050_stocktake_observation_scope_mode.py"
)
STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0051_stocktake_difference_authorization_hash.py"
)
OPENING_TERMINAL_GUARD_EXECUTION_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0052_opening_terminal_guard_execution.py"
)
OPENING_GRAPH_TABLE_DISPATCH_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260904_0053_opening_graph_table_dispatch.py"
)
OPENING_RECOUNT_SOURCE_HISTORY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260904_0054_opening_recount_source_history.py"
)
NONOPENING_START_AUDIT_ORDER_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0055_nonopening_start_audit_order.py"
)
NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0056_nonopening_count_guard_compatibility.py"
)
NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0057_nonopening_difference_replay_lock.py"
)
NONOPENING_REVIEW_TERMINAL_STATUS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0058_nonopening_review_terminal_status.py"
)
MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0059_material_request_supply_task_causality.py"
)
MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0060_material_request_supply_security_hardening.py"
)
MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260905_0061_material_request_supply_event_key_expression.py"
)
REVIEW_COMMAND_STATUS_REVISION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260906_0063_review_command_status.py"
)
HEAD_REVISION = "20260915_0075"
NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION_ID = "20260901_0032"
STOCKTAKE_COUNT_LEDGER_BOUNDARY_REVISION_ID = "20260901_0033"
STOCKTAKE_RECOUNT_SELECTED_SCOPE_REVISION_ID = "20260901_0034"
NONOPENING_STOCKTAKE_SAFE_POSTING_REVISION_ID = "20260901_0035"
FORMAL_FILE_RUNTIME_BOUNDARY_REVISION_ID = "20260901_0036"
MATERIAL_REQUEST_CANCELLATION_BOUNDARY_REVISION_ID = "20260901_0037"
NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_REVISION_ID = "20260901_0038"
MATERIAL_REQUEST_COMMAND_STATUS_LOOKUP_REVISION_ID = "20260901_0039"
KMS_DATA_KEY_PINS_REVISION_ID = "20260901_0040"
SMS_DISPATCH_OWNERSHIP_REVISION_ID = "20260902_0041"
MATERIAL_REQUEST_WORK_ORDER_LOCK_REVISION_ID = "20260902_0042"
OAM_WORK_ORDER_PROJECTOR_BOUNDARY_REVISION_ID = "20260902_0043"
OAM_SYNC_SCOPE_FORCE_RLS_REVISION_ID = "20260902_0044"
MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION_ID = "20260903_0045"
MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION_ID = "20260903_0046"
NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION_ID = "20260903_0047"
STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION_ID = "20260903_0048"
STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION_ID = "20260903_0049"
STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION_ID = "20260903_0050"
STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID = "20260903_0051"
OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID = "20260903_0052"
OPENING_GRAPH_TABLE_DISPATCH_REVISION_ID = "20260904_0053"
OPENING_RECOUNT_SOURCE_HISTORY_REVISION_ID = "20260904_0054"
NONOPENING_START_AUDIT_ORDER_REVISION_ID = "20260905_0055"
NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION_ID = "20260905_0056"
NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION_ID = "20260905_0057"
NONOPENING_REVIEW_TERMINAL_STATUS_REVISION_ID = "20260905_0058"
MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID = "20260905_0059"
MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID = "20260905_0060"
MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID = "20260905_0061"
PRE_MATERIAL_REQUEST_SUPPLY_EVENT_KEY_HEAD_REVISION = "20260905_0060"
PRE_MATERIAL_REQUEST_SUPPLY_SECURITY_HEAD_REVISION = "20260905_0059"
PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION = "20260905_0058"
PRE_NONOPENING_REVIEW_TERMINAL_STATUS_HEAD_REVISION = "20260905_0057"
PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION = "20260905_0056"
PRE_NONOPENING_COUNT_GUARD_COMPATIBILITY_HEAD_REVISION = "20260905_0055"
PRE_NONOPENING_START_AUDIT_ORDER_HEAD_REVISION = "20260904_0054"
PRE_OPENING_RECOUNT_SOURCE_HISTORY_HEAD_REVISION = "20260904_0053"
PRE_OPENING_GRAPH_TABLE_DISPATCH_HEAD_REVISION = "20260903_0052"
PRE_OPENING_TERMINAL_GUARD_EXECUTION_HEAD_REVISION = "20260903_0051"
PRE_STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_HEAD_REVISION = "20260903_0050"
PRE_STOCKTAKE_OBSERVATION_SCOPE_MODE_HEAD_REVISION = "20260903_0049"
PRE_STOCKTAKE_RECOUNT_GUARD_SECURITY_HEAD_REVISION = "20260903_0048"
PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION = "20260903_0047"
PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION = "20260903_0046"
PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION = "20260903_0045"
PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION = "20260902_0044"
PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION = "20260902_0043"
PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION = "20260902_0042"
PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION = "20260902_0041"
PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION = "20260901_0040"
PRE_KMS_DATA_KEY_PINS_HEAD_REVISION = "20260901_0039"
PRE_MATERIAL_REQUEST_COMMAND_STATUS_LOOKUP_HEAD_REVISION = "20260901_0038"
PRE_NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_HEAD_REVISION = "20260901_0037"
PRE_MATERIAL_REQUEST_CANCELLATION_BOUNDARY_HEAD_REVISION = "20260901_0036"
PRE_FORMAL_FILE_RUNTIME_BOUNDARY_HEAD_REVISION = "20260901_0035"
PRE_NONOPENING_STOCKTAKE_SAFE_POSTING_HEAD_REVISION = "20260901_0034"
PRE_STOCKTAKE_RECOUNT_SELECTED_SCOPE_HEAD_REVISION = "20260901_0033"
PRE_STOCKTAKE_COUNT_LEDGER_BOUNDARY_HEAD_REVISION = "20260901_0032"
PRE_STOCKTAKE_DIFFERENCE_HEAD_REVISION = "20260901_0030"
PRE_APPROVAL_CAUSALITY_HEAD_REVISION = "20260831_0029"
PRE_DEMAND_HEAD_REVISION = "20260831_0028"
OPENING_TERMINAL_SECURITY_INDEX = (
    "uq_stocktake_postings_one_opening_task_0022"
)
V09_TABLES = {
    "audit_logs",
    "auth_sessions",
    "external_sync_batches",
    "external_sync_current_records",
    "external_sync_records",
    "external_sync_snapshot_batches",
    "external_sync_snapshot_records",
    "external_sync_snapshots",
    "inventory_balances",
    "materials",
    "media_attachments",
    "oam_personnel_bindings",
    "sms_login_challenges",
    "stocktake_items",
    "stocktake_tasks",
    "transfer_items",
    "transfers",
    "users",
    "warehouses",
    "wechat_identities",
    "work_order_materials",
}
FOUNDATION_TABLES = {
    "approval_delegations",
    "audit_events",
    "auth_identities",
    "document_attachments",
    "external_object_mappings",
    "external_object_versions",
    "external_objects",
    "file_jobs",
    "files",
    "login_challenges",
    "migration_batches",
    "migration_errors",
    "notification_attempts",
    "notification_deliveries",
    "notification_events",
    "notification_recipients",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
    "reconciliation_items",
    "reconciliation_runs",
    "robot_inbound_events",
    "role_assignments",
    "role_permissions",
    "roles",
    "source_systems",
    "state_transition_events",
    "sync_batches",
    "sync_conflicts",
    "sync_inbox_events",
    "sync_runs",
    "system_parameters",
}
ACTIVATION_TABLES = {
    "audit_chain_heads",
    "auth_refresh_tokens",
}
AUTHENTICATION_IDEMPOTENCY_TABLES = {"auth_idempotency_operations"}
AUTHENTICATION_LOGIN_RATE_LIMIT_TABLES = {"auth_login_rate_limit_buckets"}
KMS_DATA_KEY_PIN_TABLES = {"kms_data_key_pins"}
SMS_DISPATCH_OWNERSHIP_TABLES = {"sms_challenge_dispatches"}
V09_TABLE_RENAMES_AT_HEAD = {
    "materials": "legacy_v09_materials",
    "stocktake_tasks": "legacy_v09_stocktake_tasks",
    "stocktake_items": "legacy_v09_stocktake_items",
}
V09_TABLES_AT_HEAD = (V09_TABLES - set(V09_TABLE_RENAMES_AT_HEAD)) | set(
    V09_TABLE_RENAMES_AT_HEAD.values()
)
LEGACY_MATERIAL_DEPENDENT_TABLES = {
    "inventory_balances",
    "stocktake_items",
    "transfer_items",
    "work_order_materials",
}
INVENTORY_LEDGER_TABLES = {
    "custody_assignments",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_serials",
    "inventory_transactions",
    "material_inventory_policies",
    "materials",
    "qr_codes",
    "serial_current_positions",
    "stock_accounts",
    "stock_balances",
    "stock_locations",
}
OPENING_STOCKTAKE_TABLES = {
    "inventory_freezes",
    "inventory_opening_establishments",
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_serials",
    "stocktake_differences",
    "stocktake_posting_items",
    "stocktake_postings",
    "stocktake_review_items",
    "stocktake_reviews",
    "stocktake_rounds",
    "stocktake_scopes",
    "stocktake_snapshot_lines",
    "stocktake_tasks",
}
OPENING_COUNT_OBSERVATION_TABLES = {
    "stocktake_count_observations",
    "stocktake_difference_set_completions",
    "stocktake_observation_dispositions",
    "stocktake_scope_count_completions",
    "stocktake_round_submissions",
}
STOCKTAKE_RECOUNT_TABLES = {
    "stocktake_recount_cases",
    "stocktake_recount_scope_assignments",
}
NONOPENING_STOCKTAKE_SAFE_POSTING_TABLES = {
    "stocktake_effective_approval_completions",
    "stocktake_effective_approval_scopes",
    "stocktake_effective_approval_items",
    "stocktake_posting_completions",
    "stocktake_posting_completion_items",
}
STOCKTAKE_POSTING_COMMAND_OUTCOME_TABLES = {
    "stocktake_posting_command_outcomes",
}
NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_TABLES = {
    "stocktake_close_transition_acks",
    "stocktake_close_reconciliation_completions",
    "stocktake_close_reconciliation_accounts",
    "stocktake_close_reconciliation_serials",
    "stocktake_close_completions",
}
NONOPENING_STOCKTAKE_START_CAUSALITY_TABLES = {
    "stocktake_start_completions",
}
OPENING_CONTROL_RECONCILIATION_TABLES = {
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_items",
    "opening_control_reconciliation_runs",
    "reconciliation_commands",
}
MATERIAL_REQUEST_APPROVAL_TABLES = {
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
    "material_request_commands",
    "material_request_cancellation_line_facts",
    "material_request_files",
    "material_request_lines",
    "material_request_revisions",
    "material_requests",
    "material_substitutions",
    "oam_work_orders",
    "substitution_decisions",
    "supply_tasks",
}
EXPECTED_TABLES = (
    V09_TABLES_AT_HEAD
    | FOUNDATION_TABLES
    | ACTIVATION_TABLES
    | AUTHENTICATION_IDEMPOTENCY_TABLES
    | AUTHENTICATION_LOGIN_RATE_LIMIT_TABLES
    | KMS_DATA_KEY_PIN_TABLES
    | SMS_DISPATCH_OWNERSHIP_TABLES
    | INVENTORY_LEDGER_TABLES
    | OPENING_STOCKTAKE_TABLES
    | OPENING_COUNT_OBSERVATION_TABLES
    | STOCKTAKE_RECOUNT_TABLES
    | NONOPENING_STOCKTAKE_START_CAUSALITY_TABLES
    | NONOPENING_STOCKTAKE_SAFE_POSTING_TABLES
    | STOCKTAKE_POSTING_COMMAND_OUTCOME_TABLES
    | NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_TABLES
    | OPENING_CONTROL_RECONCILIATION_TABLES
    | MATERIAL_REQUEST_APPROVAL_TABLES
    | {
        "stock_allocations",
        "stock_allocation_serials",
        "stock_reservations",
        "stock_reservation_serials",
        "outbound_orders", "outbound_lines", "stock_reservation_picks", "stock_reservation_pick_serials",
        "outbound_postings", "outbound_posting_serials",
        "shipments", "shipment_lines", "shipment_serials",
        "logistics_events", "receipts", "receipt_lines", "receipt_serials", "receipt_exceptions", "inbound_orders",
        "inbound_postings",
        "stock_reservation_releases",
        "stock_reservation_release_serials",
    }
)

EXPECTED_PERMISSIONS = [
    ("access_context", "read"),
    ("account", "read_self"),
    ("audit", "read"),
    ("auth_session", "manage"),
    ("dashboard", "read"),
    ("inventory", "read"),
    ("inventory_transaction", "post"),
    ("inventory_transaction", "reverse"),
    ("legacy_transfer_history", "read"),
    ("material_request", "approve_headquarters"),
    ("material_request", "approve_region"),
    ("material_request", "approve_star"),
    ("material_request", "cancel"),
    ("material_request", "confirm_substitution"),
    ("material_request", "create"),
    ("material_request", "propose_substitution"),
    ("material_request", "read"),
    ("material_request", "read_star_approval"),
    ("material_request", "register_external"),
    ("material_request", "submit"),
    ("material_request", "update_draft"),
    ("material_request", "verify_external"),
    ("material_request", "withdraw"),
    ("material_request_approval", "decide_level_1"),
    ("material_request_approval", "decide_level_2"),
    ("material_request_approval", "decide_level_3"),
    ("oam_data", "read"),
    ("people", "read_minimal"),
    ("reconciliation", "approve_opening"),
    ("reconciliation", "create_opening"),
    ("reconciliation", "explain_opening"),
    ("reconciliation", "read"),
    ("role_assignment", "manage_provincial"),
    ("stocktake", "close"),
    ("stocktake", "count"),
    ("stocktake", "manage"),
    ("stocktake", "post_difference"),
    ("stocktake", "post_opening"),
    ("stocktake", "read"),
    ("stocktake", "reconcile"),
    ("stocktake", "review_headquarters"),
    ("stocktake", "review_region"),
    ("supply_task", "manage"),
    ("supply_task", "read"),
    ("system_settings", "read"),
]

EXPECTED_ROLE_PERMISSIONS = {
    "admin": {
        ("account", "read_self"),
        ("access_context", "read"),
        ("dashboard", "read"),
        ("oam_data", "read"),
        ("inventory", "read"),
        ("inventory_transaction", "post"),
        ("inventory_transaction", "reverse"),
        ("legacy_transfer_history", "read"),
        ("audit", "read"),
        ("system_settings", "read"),
        ("auth_session", "manage"),
        ("material_request", "approve_headquarters"),
        ("material_request", "propose_substitution"),
        ("material_request", "read"),
        ("material_request", "register_external"),
        ("material_request", "verify_external"),
        ("material_request_approval", "decide_level_2"),
        ("people", "read_minimal"),
        ("reconciliation", "read"),
        ("reconciliation", "create_opening"),
        ("reconciliation", "approve_opening"),
        ("role_assignment", "manage_provincial"),
        ("stocktake", "read"),
        ("stocktake", "count"),
        ("stocktake", "close"),
        ("stocktake", "manage"),
        ("stocktake", "post_difference"),
        ("stocktake", "reconcile"),
        ("stocktake", "review_headquarters"),
        ("stocktake", "post_opening"),
        ("supply_task", "manage"),
        ("supply_task", "read"),
    },
    "provincial_manager": {
        ("account", "read_self"),
        ("access_context", "read"),
        ("dashboard", "read"),
        ("oam_data", "read"),
        ("inventory", "read"),
        ("inventory_transaction", "post"),
        ("legacy_transfer_history", "read"),
        ("material_request", "approve_region"),
        ("material_request", "propose_substitution"),
        ("material_request", "read"),
        ("material_request_approval", "decide_level_1"),
        ("reconciliation", "read"),
        ("reconciliation", "explain_opening"),
        ("stocktake", "read"),
        ("stocktake", "count"),
        ("stocktake", "manage"),
        ("stocktake", "review_region"),
        ("supply_task", "read"),
    },
    "technician": {
        ("account", "read_self"),
        ("access_context", "read"),
        ("dashboard", "read"),
        ("oam_data", "read"),
        ("inventory", "read"),
        ("legacy_transfer_history", "read"),
        ("material_request", "cancel"),
        ("material_request", "confirm_substitution"),
        ("material_request", "create"),
        ("material_request", "read"),
        ("material_request", "submit"),
        ("material_request", "update_draft"),
        ("material_request", "withdraw"),
        ("stocktake", "read"),
        ("stocktake", "count"),
    },
    "star_headquarters_approver": {
        ("account", "read_self"),
        ("access_context", "read"),
        ("material_request", "approve_star"),
        ("material_request", "read_star_approval"),
        ("material_request_approval", "decide_level_3"),
    },
}


def _config(database_url: str, *, output_buffer=None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output_buffer)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _canonical_audit_hash(
    *,
    stream_key: str,
    event_id: str,
    action: str,
    aggregate_type: str,
    aggregate_id: str,
    after_jsonb: dict[str, object] | None,
    request_id: str,
    occurred_at: str,
    previous_hash: str | None = None,
) -> str:
    parsed_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        parsed_at = parsed_at.replace(tzinfo=timezone.utc)
    document = {
        "action": action,
        "actor_user_id": None,
        "after_jsonb": after_jsonb,
        "aggregate_id": aggregate_id,
        "aggregate_type": aggregate_type,
        "before_jsonb": None,
        "event_id": str(uuid.UUID(event_id)),
        "occurred_at": parsed_at.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "previous_hash": previous_hash,
        "request_id": request_id,
        "stream_key": stream_key,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _insert_valid_audit_chain_event(
    connection: sa.Connection,
    *,
    event_id: str = "91000000000040008000000000000011",
    stream_key: str = "authorization",
) -> str:
    occurred_at = "2026-08-31 00:00:00+00:00"
    action = "role_assignment.created"
    aggregate_type = "role_assignment"
    aggregate_id = "assignment-preflight-sentinel"
    request_id = "request-preflight-sentinel"
    after_jsonb = {"status": "active", "说明": "链完整"}
    head_version, previous_hash = connection.exec_driver_sql(
        "SELECT version, last_hash FROM audit_chain_heads WHERE stream_key = ?",
        (stream_key,),
    ).one()
    event_hash = _canonical_audit_hash(
        stream_key=stream_key,
        event_id=event_id,
        action=action,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        after_jsonb=after_jsonb,
        request_id=request_id,
        occurred_at=occurred_at,
        previous_hash=previous_hash,
    )
    has_stream_binding = "stream_key" in {
        row[1]
        for row in connection.exec_driver_sql(
            "PRAGMA table_info(audit_events)"
        ).all()
    }
    if has_stream_binding:
        connection.exec_driver_sql(
            "INSERT INTO audit_events "
            "(id, stream_key, stream_version, actor_user_id, action, "
            "aggregate_type, aggregate_id, before_jsonb, after_jsonb, "
            "request_id, previous_hash, event_hash, occurred_at, created_at) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                stream_key,
                head_version + 1,
                action,
                aggregate_type,
                aggregate_id,
                json.dumps(after_jsonb, ensure_ascii=False),
                request_id,
                previous_hash,
                event_hash,
                occurred_at,
                occurred_at,
            ),
        )
    else:
        connection.exec_driver_sql(
            "INSERT INTO audit_events "
            "(id, actor_user_id, action, aggregate_type, aggregate_id, "
            "before_jsonb, after_jsonb, request_id, previous_hash, event_hash, "
            "occurred_at, created_at) VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                action,
                aggregate_type,
                aggregate_id,
                json.dumps(after_jsonb, ensure_ascii=False),
                request_id,
                previous_hash,
                event_hash,
                occurred_at,
                occurred_at,
            ),
        )
    connection.exec_driver_sql(
        "UPDATE audit_chain_heads SET last_event_id = ?, last_hash = ?, "
        "version = ? WHERE stream_key = ?",
        (event_id, event_hash, head_version + 1, stream_key),
    )
    return event_hash


def _insert_0041_login_challenge(
    connection: sa.Connection,
    *,
    challenge_id: str,
    mobile_hash: str,
    provider_reference: str | None = None,
    expires_at: str = "2020-01-01 00:05:00+00:00",
    status: str = "pending",
    created_at: str = "2020-01-01 00:00:00+00:00",
    client_type: str = "web",
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO login_challenges "
        "(id, mobile_hash, code_hash, verification_mode, provider, "
        "provider_reference, client_type, purpose, attempts, max_attempts, "
        "expires_at, status, idempotency_key, requested_ip_hash, verified_at, "
        "consumed_at, created_at) "
        "VALUES (?, ?, NULL, 'provider_managed', 'aliyun_pnvs', ?, ?, "
        "'login', 0, 5, ?, ?, ?, ?, NULL, NULL, ?)",
        (
            challenge_id,
            mobile_hash,
            provider_reference,
            client_type,
            expires_at,
            status,
            f"sms-0041-{challenge_id}",
            "9" * 64,
            created_at,
        ),
    )


def _insert_0041_acceptance_audit(
    connection: sa.Connection,
    *,
    challenge_id: str,
    event_id: str,
    occurred_at: str,
    client_type: str = "web",
    outcome: str = "accepted",
    reason_code: str = "provider_accepted",
    status: str = "pending",
) -> str:
    stream_key = "authentication"
    action = "authentication.sms.challenge_sent"
    aggregate_type = "login_challenge"
    aggregate_id = str(uuid.UUID(challenge_id))
    request_id = f"authreq-{hashlib.sha256(event_id.encode()).hexdigest()}"
    after_jsonb = {
        "client_type": client_type,
        "outcome": outcome,
        "reason_code": reason_code,
        "status": status,
    }
    head_version, previous_hash = connection.exec_driver_sql(
        "SELECT version, last_hash FROM audit_chain_heads WHERE stream_key = ?",
        (stream_key,),
    ).one()
    event_hash = _canonical_audit_hash(
        stream_key=stream_key,
        event_id=event_id,
        action=action,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        after_jsonb=after_jsonb,
        request_id=request_id,
        occurred_at=occurred_at,
        previous_hash=previous_hash,
    )
    connection.exec_driver_sql(
        "INSERT INTO audit_events "
        "(id, stream_key, stream_version, actor_user_id, action, "
        "aggregate_type, aggregate_id, before_jsonb, after_jsonb, request_id, "
        "previous_hash, event_hash, occurred_at, created_at) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            stream_key,
            head_version + 1,
            action,
            aggregate_type,
            aggregate_id,
            json.dumps(after_jsonb, ensure_ascii=False),
            request_id,
            previous_hash,
            event_hash,
            occurred_at,
            occurred_at,
        ),
    )
    connection.exec_driver_sql(
        "UPDATE audit_chain_heads SET last_event_id = ?, last_hash = ?, "
        "version = ? WHERE stream_key = ?",
        (event_id, event_hash, head_version + 1, stream_key),
    )
    return event_hash


def _insert_0041_no_dispatch_transition(
    connection: sa.Connection,
    *,
    challenge_id: str,
    event_id: str,
    reason: str,
) -> None:
    occurred_at = "2020-01-01 00:00:01+00:00"
    connection.exec_driver_sql(
        "INSERT INTO state_transition_events "
        "(id, aggregate_type, aggregate_id, from_status, to_status, reason, "
        "actor_id, idempotency_key, occurred_at, metadata_jsonb, created_at) "
        "VALUES (?, 'login_challenge', ?, NULL, 'cancelled', ?, NULL, ?, ?, "
        "'{}', ?)",
        (
            event_id,
            str(uuid.UUID(challenge_id)),
            reason,
            f"sms-0041-transition-{event_id}",
            occurred_at,
            occurred_at,
        ),
    )


def _insert_0041_prepared_dispatch(
    connection: sa.Connection,
    *,
    challenge_id: str,
    mobile_hash: str,
    request_sha256: str = "a" * 64,
    created_at: str = "2026-09-02 00:00:00+00:00",
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO sms_challenge_dispatches "
        "(challenge_id, provider, mobile_hash, status, request_sha256, "
        "owner_token_hash, provider_reference, claimed_at, lease_expires_at, "
        "accepted_at, uncertain_at, expired_at, created_at) "
        "VALUES (?, 'aliyun_pnvs', ?, 'prepared', ?, NULL, NULL, NULL, NULL, "
        "NULL, NULL, NULL, ?)",
        (challenge_id, mobile_hash, request_sha256, created_at),
    )


def _advance_0041_dispatch(
    connection: sa.Connection,
    *,
    challenge_id: str,
    target_status: str,
) -> None:
    if target_status == "prepared":
        return
    claimed_at = "2026-09-02 00:01:00+00:00"
    lease_expires_at = "2026-09-02 00:06:00+00:00"
    connection.exec_driver_sql(
        "UPDATE sms_challenge_dispatches SET status = 'sending', "
        "owner_token_hash = ?, claimed_at = ?, lease_expires_at = ? "
        "WHERE challenge_id = ?",
        ("b" * 64, claimed_at, lease_expires_at, challenge_id),
    )
    if target_status == "sending":
        return
    if target_status == "accepted":
        connection.exec_driver_sql(
            "UPDATE sms_challenge_dispatches SET status = 'accepted', "
            "provider_reference = ?, accepted_at = ? WHERE challenge_id = ?",
            (
                f"biz-{challenge_id[-12:]}",
                "2026-09-02 00:02:00+00:00",
                challenge_id,
            ),
        )
        return
    if target_status == "uncertain":
        connection.exec_driver_sql(
            "UPDATE sms_challenge_dispatches SET status = 'uncertain', "
            "uncertain_at = ? WHERE challenge_id = ?",
            ("2026-09-02 00:02:00+00:00", challenge_id),
        )
        return
    if target_status == "expired":
        connection.exec_driver_sql(
            "UPDATE sms_challenge_dispatches SET status = 'expired', "
            "uncertain_at = ?, expired_at = ? WHERE challenge_id = ?",
            (
                "2026-09-02 00:02:00+00:00",
                "2026-09-02 00:03:00+00:00",
                challenge_id,
            ),
        )
        return
    raise AssertionError(f"unsupported 0041 dispatch target: {target_status}")


def _normalized_sql(value: str | None) -> str:
    return " ".join((value or "").replace("(", " ").replace(")", " ").split()).lower()


def _index_predicate(index: dict[str, object]) -> str:
    dialect_options = index.get("dialect_options") or {}
    assert isinstance(dialect_options, dict)
    for key in ("sqlite_where", "postgresql_where"):
        if key in dialect_options:
            return _normalized_sql(str(dialect_options[key]))
    return ""


def _sqlite_foreign_key_delete_actions(
    connection: sa.Connection,
    table_name: str,
) -> dict[tuple[tuple[str, ...], str, tuple[str, ...]], str | None]:
    """Recover SQLite FK actions that SQLAlchemy may omit after ADD COLUMN."""

    if connection.dialect.name != "sqlite":
        return {}
    quoted_table = connection.dialect.identifier_preparer.quote(table_name)
    rows = connection.exec_driver_sql(
        f"PRAGMA foreign_key_list({quoted_table})"
    ).mappings().all()
    grouped: dict[int, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(int(row["id"]), []).append(dict(row))
    actions: dict[
        tuple[tuple[str, ...], str, tuple[str, ...]], str | None
    ] = {}
    for parts in grouped.values():
        ordered = sorted(parts, key=lambda item: int(item["seq"]))
        on_delete = str(ordered[0]["on_delete"]).upper()
        actions[
            (
                tuple(str(item["from"]) for item in ordered),
                str(ordered[0]["table"]),
                tuple(str(item["to"]) for item in ordered),
            )
        ] = None if on_delete == "NO ACTION" else on_delete
    return actions


def _schema_snapshot(database_url: str) -> dict[str, dict[str, object]]:
    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        snapshot: dict[str, dict[str, object]] = {}
        with engine.connect() as connection:
            for table_name in sorted(
                set(inspector.get_table_names()) - {"alembic_version"}
            ):
                sqlite_delete_actions = _sqlite_foreign_key_delete_actions(
                    connection,
                    table_name,
                )
                foreign_keys = inspector.get_foreign_keys(table_name)
                snapshot[table_name] = {
                    "columns": sorted(
                        (
                            column["name"],
                            str(column["type"]).upper(),
                            bool(column["nullable"]),
                            bool(column.get("primary_key")),
                            column.get("default"),
                        )
                        for column in inspector.get_columns(table_name)
                    ),
                    "primary_key": tuple(
                        inspector.get_pk_constraint(table_name)[
                            "constrained_columns"
                        ]
                    ),
                    "foreign_keys": sorted(
                        (
                            tuple(foreign_key["constrained_columns"]),
                            foreign_key["referred_table"],
                            tuple(foreign_key["referred_columns"]),
                            (foreign_key.get("options") or {}).get("ondelete")
                            or sqlite_delete_actions.get(
                                (
                                    tuple(foreign_key["constrained_columns"]),
                                    foreign_key["referred_table"],
                                    tuple(foreign_key["referred_columns"]),
                                )
                            ),
                        )
                        for foreign_key in foreign_keys
                    ),
                    "unique_constraints": sorted(
                        (
                            tuple(constraint["column_names"]),
                            constraint.get("name"),
                        )
                        for constraint in inspector.get_unique_constraints(
                            table_name
                        )
                    ),
                    "indexes": sorted(
                        (
                            index["name"],
                            tuple(index["column_names"]),
                            bool(index["unique"]),
                            _index_predicate(index),
                        )
                        for index in inspector.get_indexes(table_name)
                    ),
                    "checks": sorted(
                        (
                            constraint.get("name"),
                            _normalized_sql(constraint.get("sqltext")),
                        )
                        for constraint in inspector.get_check_constraints(
                            table_name
                        )
                    ),
                }
        return snapshot
    finally:
        engine.dispose()


def _create_model_schema_in_subprocess(database_url: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(ROOT / "backend"),
            "OAM_DATABASE_URL": database_url,
            "OAM_JWT_SECRET": "test-secret-with-at-least-thirty-two-characters",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.models import Base; "
            "from app.database import engine; "
            "Base.metadata.create_all(engine)",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def _insert_auth_idempotency_operation(
    engine: sa.Engine,
    operation_id: str,
    **overrides,
) -> None:
    values = {
        "id": operation_id,
        "operation_type": "sms_login",
        "client_type": "web",
        "idempotency_key_hash": hashlib.sha256(
            operation_id.encode("utf-8")
        ).hexdigest(),
        "scope_hash": "a" * 64,
        "request_hmac": "b" * 64,
        "status": "pending",
        "response_ciphertext": None,
        "response_nonce": None,
        "response_sha256": None,
        "encryption_key_version": None,
        "http_status": None,
        "auth_session_id": None,
        "input_refresh_token_id": None,
        "output_refresh_token_id": None,
        "expires_at": "2026-09-01 00:00:00+00:00",
        "completed_at": None,
        "updated_at": "2026-08-30 00:00:00+00:00",
        "created_at": "2026-08-30 00:00:00+00:00",
    }
    values.update(overrides)
    columns = tuple(values)
    placeholders = ", ".join("?" for _ in columns)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"INSERT INTO auth_idempotency_operations "
            f"({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )


def _insert_auth_session(
    engine: sa.Engine,
    *,
    session_id: str,
    user_id: str,
    refresh_token_hash: str,
    client_type: str = "web",
    device_id: str = "device-1",
    revoked_at: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO auth_sessions "
            "(id, user_id, refresh_token_hash, client_type, device_id, "
            "device_name, ip_address, user_agent, created_at, last_seen_at, "
            "expires_at, revoked_at, revoked_by_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                user_id,
                refresh_token_hash,
                client_type,
                device_id,
                "migration sentinel",
                "",
                "migration-test",
                "2026-08-30 00:00:00+00:00",
                "2026-08-30 00:00:00+00:00",
                "2026-09-30 00:00:00+00:00",
                revoked_at,
                None,
            ),
        )


def _insert_legacy_user(
    engine: sa.Engine,
    *,
    user_id: str,
    mobile: str,
) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users "
            "(id, mobile, name, password_hash, role, province, is_active, "
            "require_password_change, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                mobile,
                "session migration sentinel",
                "not-a-real-password-hash",
                "technician",
                None,
                True,
                False,
                "2026-08-30 00:00:00+00:00",
                "2026-08-30 00:00:00+00:00",
            ),
        )


def test_initial_revision_is_explicit_and_model_independent() -> None:
    for revision_path in (
        INITIAL_REVISION,
        FOUNDATION_REVISION,
        ACTIVATION_REVISION,
        ROLE_ADMINISTRATION_REVISION,
        AUTHENTICATION_RUNTIME_REVISION,
        AUTHENTICATION_IDEMPOTENCY_REVISION,
        ACTIVE_AUTH_SESSION_DEVICE_REVISION,
        AUTHENTICATION_LOGIN_RATE_LIMIT_REVISION,
        INVENTORY_LEDGER_REVISION,
        OPENING_STOCKTAKE_REVISION,
        OPENING_COUNT_OBSERVATION_REVISION,
        OPENING_RESOLVED_SERIAL_UNIQUENESS_REVISION,
        OPENING_SERIAL_IDENTIFIER_VALIDATION_REVISION,
        OPENING_EXPLICIT_SEALING_COMPLETION_REVISION,
        IMMUTABLE_AUDIT_EVENTS_REVISION,
        OPENING_REVIEW_EVIDENCE_REVISION,
        AUDIT_STREAM_BINDING_REVISION,
        STOCKTAKE_RECOUNT_REVISION,
        TECHNICIAN_PERSONAL_STOCKTAKE_REVISION,
        PERSONAL_STOCKTAKE_LOCATION_CONTINUITY_REVISION,
        STOCKTAKE_ROUND_ASSIGNMENT_GUARDS_REVISION,
        OPENING_TERMINAL_RUNTIME_BOUNDARY_REVISION,
        OPENING_OBSERVATION_POSTING_REVISION,
        FORMAL_OPENING_RUNTIME_ACL_REVISION,
        STOCKTAKE_SCOPE_REGION_OWNER_REVISION,
        OPENING_CONTROL_RECONCILIATION_REVISION,
        POSTGRESQL_LOCK_GRAPH_REVISION,
        OPENING_TERMINAL_REFERENCE_UNION_REVISION,
        MATERIAL_REQUEST_APPROVAL_REVISION,
        APPROVAL_CAUSALITY_REVISION,
        STOCKTAKE_DIFFERENCE_EVALUATOR_REVISION,
        NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION,
        STOCKTAKE_COUNT_LEDGER_BOUNDARY_REVISION,
        STOCKTAKE_RECOUNT_SELECTED_SCOPE_REVISION,
        NONOPENING_STOCKTAKE_SAFE_POSTING_REVISION,
        FORMAL_FILE_RUNTIME_BOUNDARY_REVISION,
        MATERIAL_REQUEST_CANCELLATION_BOUNDARY_REVISION,
    ):
        tree = ast.parse(revision_path.read_text(encoding="utf-8"))
        imported_modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported_from_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        assert not any(
            module == "app" or module.startswith("app.")
            for module in imported_modules | imported_from_modules
        )
        assert "create_all" not in called_attributes
        assert "drop_all" not in called_attributes

    foundation_tree = ast.parse(FOUNDATION_REVISION.read_text(encoding="utf-8"))
    foundation_upgrade = next(
        node
        for node in foundation_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    upgrade_calls = {
        node.func.attr
        for node in ast.walk(foundation_upgrade)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "create_table" in upgrade_calls
    assert not upgrade_calls & {
        "alter_column",
        "batch_alter_table",
        "drop_column",
        "drop_constraint",
        "drop_index",
        "drop_table",
    }


def test_pg16_gate_head_matches_alembic_graph() -> None:
    """Detect a stale dynamic-gate target without requiring a PostgreSQL run."""
    gate = ROOT / "backend" / "tests" / "test_postgresql16_release_gate.py"
    constants: dict[str, str] = {}
    for node in ast.parse(gate.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            constants[target.id] = node.value.value
        elif isinstance(node.value, ast.Name) and node.value.id in constants:
            constants[target.id] = constants[node.value.id]
    script = ScriptDirectory.from_config(_config("sqlite+pysqlite:///:memory:"))
    assert constants["HEAD_REVISION"] == script.get_current_head()


def test_revision_history_has_single_integrity_hardening_head() -> None:
    script = ScriptDirectory.from_config(_config("sqlite+pysqlite:///:memory:"))
    assert script.get_heads() == [HEAD_REVISION]
    head = script.get_revision(HEAD_REVISION)
    assert head is not None
    assert head.down_revision == "20260914_0074"
    assert REVIEW_COMMAND_STATUS_REVISION.exists()
    supply_event_key_head = script.get_revision(
        MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID
    )
    assert supply_event_key_head is not None
    assert (
        supply_event_key_head.down_revision
        == PRE_MATERIAL_REQUEST_SUPPLY_EVENT_KEY_HEAD_REVISION
    )
    previous_supply_event_key_head = script.get_revision(
        PRE_MATERIAL_REQUEST_SUPPLY_EVENT_KEY_HEAD_REVISION
    )
    assert previous_supply_event_key_head is not None
    assert (
        previous_supply_event_key_head.down_revision
        == PRE_MATERIAL_REQUEST_SUPPLY_SECURITY_HEAD_REVISION
    )
    previous_supply_security_head = script.get_revision(
        PRE_MATERIAL_REQUEST_SUPPLY_SECURITY_HEAD_REVISION
    )
    assert previous_supply_security_head is not None
    assert (
        previous_supply_security_head.down_revision
        == PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION
    )
    previous_supply_causality_head = script.get_revision(
        PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION
    )
    assert previous_supply_causality_head is not None
    assert (
        previous_supply_causality_head.down_revision
        == PRE_NONOPENING_REVIEW_TERMINAL_STATUS_HEAD_REVISION
    )
    previous_nonopening_review_terminal_head = script.get_revision(
        PRE_NONOPENING_REVIEW_TERMINAL_STATUS_HEAD_REVISION
    )
    assert previous_nonopening_review_terminal_head is not None
    assert (
        previous_nonopening_review_terminal_head.down_revision
        == PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION
    )
    previous_nonopening_difference_replay_head = script.get_revision(
        PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION
    )
    assert previous_nonopening_difference_replay_head is not None
    assert (
        previous_nonopening_difference_replay_head.down_revision
        == PRE_NONOPENING_COUNT_GUARD_COMPATIBILITY_HEAD_REVISION
    )
    previous_nonopening_count_guard_head = script.get_revision(
        PRE_NONOPENING_COUNT_GUARD_COMPATIBILITY_HEAD_REVISION
    )
    assert previous_nonopening_count_guard_head is not None
    assert (
        previous_nonopening_count_guard_head.down_revision
        == PRE_NONOPENING_START_AUDIT_ORDER_HEAD_REVISION
    )
    previous_nonopening_start_audit_order_head = script.get_revision(
        PRE_NONOPENING_START_AUDIT_ORDER_HEAD_REVISION
    )
    assert previous_nonopening_start_audit_order_head is not None
    assert (
        previous_nonopening_start_audit_order_head.down_revision
        == PRE_OPENING_RECOUNT_SOURCE_HISTORY_HEAD_REVISION
    )
    previous_opening_recount_source_history_head = script.get_revision(
        PRE_OPENING_RECOUNT_SOURCE_HISTORY_HEAD_REVISION
    )
    assert previous_opening_recount_source_history_head is not None
    assert (
        previous_opening_recount_source_history_head.down_revision
        == PRE_OPENING_GRAPH_TABLE_DISPATCH_HEAD_REVISION
    )
    previous_opening_graph_table_dispatch_head = script.get_revision(
        PRE_OPENING_GRAPH_TABLE_DISPATCH_HEAD_REVISION
    )
    assert previous_opening_graph_table_dispatch_head is not None
    assert (
        previous_opening_graph_table_dispatch_head.down_revision
        == PRE_OPENING_TERMINAL_GUARD_EXECUTION_HEAD_REVISION
    )
    previous_opening_terminal_guard_execution_head = script.get_revision(
        PRE_OPENING_TERMINAL_GUARD_EXECUTION_HEAD_REVISION
    )
    assert previous_opening_terminal_guard_execution_head is not None
    assert (
        previous_opening_terminal_guard_execution_head.down_revision
        == PRE_STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_HEAD_REVISION
    )
    previous_difference_authorization_hash_head = script.get_revision(
        PRE_STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_HEAD_REVISION
    )
    assert previous_difference_authorization_hash_head is not None
    assert (
        previous_difference_authorization_hash_head.down_revision
        == PRE_STOCKTAKE_OBSERVATION_SCOPE_MODE_HEAD_REVISION
    )
    previous_observation_scope_mode_head = script.get_revision(
        PRE_STOCKTAKE_OBSERVATION_SCOPE_MODE_HEAD_REVISION
    )
    assert previous_observation_scope_mode_head is not None
    assert (
        previous_observation_scope_mode_head.down_revision
        == PRE_STOCKTAKE_RECOUNT_GUARD_SECURITY_HEAD_REVISION
    )
    previous_recount_guard_security_head = script.get_revision(
        PRE_STOCKTAKE_RECOUNT_GUARD_SECURITY_HEAD_REVISION
    )
    assert previous_recount_guard_security_head is not None
    assert (
        previous_recount_guard_security_head.down_revision
        == PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION
    )
    previous_scope_guard_security_head = script.get_revision(
        PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION
    )
    assert previous_scope_guard_security_head is not None
    assert (
        previous_scope_guard_security_head.down_revision
        == PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION
    )
    previous_stocktake_start_head = script.get_revision(
        PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION
    )
    assert previous_stocktake_start_head is not None
    assert (
        previous_stocktake_start_head.down_revision
        == PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION
    )
    previous_head = script.get_revision(
        PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION
    )
    assert previous_head is not None
    assert (
        previous_head.down_revision
        == PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION
    )
    previous_approval_head = script.get_revision(
        PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION
    )
    assert previous_approval_head is not None
    assert (
        previous_approval_head.down_revision
        == PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION
    )
    previous_sync_head = script.get_revision(
        PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION
    )
    assert previous_sync_head is not None
    assert (
        previous_sync_head.down_revision
        == PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION
    )


def _load_0041_migration_module():
    spec = importlib.util.spec_from_file_location(
        "sms_dispatch_ownership_migration_0041",
        SMS_DISPATCH_OWNERSHIP_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0042_postgresql_offline_sql_is_function_only_and_exact(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    upgrade_output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=upgrade_output,
    )
    command.upgrade(
        config,
        f"{PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION}:"
        f"{MATERIAL_REQUEST_WORK_ORDER_LOCK_REVISION_ID}",
        sql=True,
    )
    sql = upgrade_output.getvalue()
    function = "rsc_lock_material_request_work_order_reference_0042"
    assert "-- Running upgrade 20260902_0041 -> 20260902_0042" in sql
    assert f"CREATE FUNCTION public.{function}(requested_work_order_id uuid)" in sql
    assert "RETURNS void" in sql
    assert "VOLATILE" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "FROM public.oam_work_orders AS work_order" in sql
    assert "WHERE work_order.id = requested_work_order_id" in sql
    assert "FOR SHARE OF work_order" in sql
    assert "GET DIAGNOSTICS locked_count = ROW_COUNT" in sql
    assert f"ALTER FUNCTION public.{function}(uuid) OWNER TO star_oam_migrator" in sql
    assert (
        f"REVOKE ALL ON FUNCTION public.{function}(uuid) FROM "
        "PUBLIC, star_oam_api"
    ) in sql
    assert f"GRANT EXECUTE ON FUNCTION public.{function}(uuid) TO star_oam_api" in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT DELETE" not in sql
    assert "ALTER TABLE public.oam_work_orders" not in sql

    downgrade_output = io.StringIO()
    downgrade_config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=downgrade_output,
    )
    command.downgrade(
        downgrade_config,
        f"{MATERIAL_REQUEST_WORK_ORDER_LOCK_REVISION_ID}:"
        f"{PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION}",
        sql=True,
    )
    downgrade_sql = downgrade_output.getvalue()
    assert f"DROP FUNCTION public.{function}(uuid)" in downgrade_sql
    assert "DROP TABLE" not in downgrade_sql
    assert "ALTER TABLE" not in downgrade_sql


def test_0042_sqlite_is_schema_noop_and_only_moves_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'work-order-lock-noop.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION)
    before_engine = sa.create_engine(database_url)
    try:
        before_tables = set(inspect(before_engine).get_table_names())
        with before_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION
    finally:
        before_engine.dispose()

    command.upgrade(config, MATERIAL_REQUEST_WORK_ORDER_LOCK_REVISION_ID)
    after_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(after_engine).get_table_names()) == before_tables
        with after_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == MATERIAL_REQUEST_WORK_ORDER_LOCK_REVISION_ID
    finally:
        after_engine.dispose()

    command.downgrade(config, PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION)
    downgraded_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(downgraded_engine).get_table_names()) == before_tables
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION
    finally:
        downgraded_engine.dispose()


def test_0043_postgresql_offline_sql_is_exact_projector_acl_only(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    upgrade_output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=upgrade_output,
    )
    command.upgrade(
        config,
        f"{PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION}:"
        f"{OAM_WORK_ORDER_PROJECTOR_BOUNDARY_REVISION_ID}",
        sql=True,
    )
    sql = upgrade_output.getvalue()
    read_tables = (
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "source_systems",
        "sync_runs",
        "sync_batches",
        "sync_inbox_events",
        "external_objects",
        "external_object_versions",
        "external_object_mappings",
        "sync_conflicts",
        "organizations",
        "people",
        "oam_work_orders",
    )
    qualified_read_tables = ", ".join(
        f"public.{table_name}" for table_name in read_tables
    )

    assert "-- Running upgrade 20260902_0042 -> 20260902_0043" in sql
    assert "rolname = 'star_oam_migrator'" in sql
    assert "rolname = 'star_oam_projector'" in sql
    assert "0043 projector boundary missing tables" in sql
    assert "REVOKE ALL ON SCHEMA public FROM star_oam_projector" in sql
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in sql
    assert "GRANT USAGE ON SCHEMA public TO star_oam_projector" in sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        "FROM star_oam_projector"
    ) in sql
    assert (
        f"GRANT SELECT ON TABLE {qualified_read_tables} TO star_oam_projector"
    ) in sql
    for table_name, columns in {
        "sync_runs": (
            "id, source_system_id, run_key, scope_key, mode, watermark_from, "
            "watermark_to, status, manifest_sha256, started_at, completed_at, "
            "failure_code, failure_detail, created_at, updated_at"
        ),
        "sync_batches": (
            "id, run_id, entity_type, sequence, record_count, body_sha256, "
            "status, received_at, validated_at, created_at"
        ),
        "sync_inbox_events": (
            "id, batch_id, source_system_id, external_event_id, entity_type, "
            "external_id, source_version, source_updated_at, payload_jsonb, "
            "payload_sha256, status, error_code, error_detail, processed_at, "
            "created_at"
        ),
        "external_objects": (
            "id, source_system_id, entity_type, external_id, "
            "current_version_id, deleted_at, created_at, updated_at"
        ),
        "external_object_versions": (
            "id, external_object_id, source_version, source_updated_at, "
            "valid_from, valid_to, payload_jsonb, payload_sha256, is_current, "
            "created_at"
        ),
        "sync_conflicts": (
            "id, run_id, inbox_event_id, external_object_id, dedup_key, "
            "conflict_type, external_value_jsonb, local_value_jsonb, status, "
            "resolution_jsonb, resolved_by, resolved_at, created_at, updated_at"
        ),
        "oam_work_orders": (
            "id, external_object_id, work_order_no, organization_id, "
            "engineer_person_id, status, source_updated_at, created_at, updated_at"
        ),
    }.items():
        assert (
            f"GRANT INSERT ({columns}) ON TABLE public.{table_name} "
            "TO star_oam_projector"
        ) in sql
    assert "GRANT INSERT ON TABLE" not in sql
    for table_name, columns in {
        "sync_runs": (
            "status, manifest_sha256, completed_at, failure_code, "
            "failure_detail, updated_at"
        ),
        "sync_batches": "status, validated_at",
        "sync_inbox_events": "status, error_code, error_detail, processed_at",
        "external_objects": "current_version_id, updated_at",
        "external_object_versions": "is_current, valid_to",
        "sync_conflicts": (
            "status, resolution_jsonb, resolved_by, resolved_at, updated_at"
        ),
        "oam_work_orders": (
            "work_order_no, organization_id, engineer_person_id, status, "
            "source_updated_at, updated_at"
        ),
    }.items():
        assert (
            f"GRANT UPDATE ({columns}) ON TABLE public.{table_name} "
            "TO star_oam_projector"
        ) in sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        "FROM star_oam_projector"
    ) in sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public "
        "FROM star_oam_projector"
    ) in sql
    assert "pg_catalog.aclexplode(attribute_row.attacl)" in sql
    assert "pg_catalog.pg_largeobject_metadata" in sql
    assert "pg_catalog.pg_parameter_acl" in sql
    assert "REVOKE ALL PRIVILEGES ON LARGE OBJECT" in sql
    assert "REVOKE ALL PRIVILEGES ON PARAMETER" in sql
    assert "0043 PUBLIC ACL boundary is not closed" in sql
    assert "0043 large-object or parameter ACL boundary is not closed" in sql
    assert "REVOKE ALL ON DATABASE %I FROM star_oam_projector" in sql
    assert "REVOKE ALL ON DATABASE %I FROM PUBLIC" in sql
    assert "GRANT CONNECT ON DATABASE %I TO star_oam_projector" in sql
    assert "0043 projector cross-schema boundary is not closed" in sql
    assert "0043 projector cross-database CONNECT boundary is not closed" in sql
    assert "pg_catalog.pg_auth_members" in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT TRUNCATE" not in sql
    assert "GRANT REFERENCES" not in sql
    assert "GRANT TRIGGER" not in sql
    assert "GRANT EXECUTE" not in sql
    assert "GRANT CREATE" not in sql
    assert "CREATE TABLE" not in sql
    assert "CREATE FUNCTION" not in sql

    downgrade_output = io.StringIO()
    downgrade_config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=downgrade_output,
    )
    command.downgrade(
        downgrade_config,
        f"{OAM_WORK_ORDER_PROJECTOR_BOUNDARY_REVISION_ID}:"
        f"{PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION}",
        sql=True,
    )
    downgrade_sql = downgrade_output.getvalue()
    assert "-- Running downgrade 20260902_0043 -> 20260902_0042" in downgrade_sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        "FROM star_oam_projector"
    ) in downgrade_sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        "FROM star_oam_projector"
    ) in downgrade_sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public "
        "FROM star_oam_projector"
    ) in downgrade_sql
    assert "REVOKE ALL ON SCHEMA public FROM star_oam_projector" in downgrade_sql
    assert "REVOKE ALL PRIVILEGES ON LARGE OBJECT" in downgrade_sql
    assert "REVOKE ALL PRIVILEGES ON PARAMETER" in downgrade_sql
    assert "DROP TABLE" not in downgrade_sql
    assert "DROP FUNCTION" not in downgrade_sql


def test_0043_sqlite_is_schema_noop_and_only_moves_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'projector-boundary-noop.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION)
    before_engine = sa.create_engine(database_url)
    try:
        before_tables = set(inspect(before_engine).get_table_names())
        with before_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION
    finally:
        before_engine.dispose()

    command.upgrade(config, OAM_WORK_ORDER_PROJECTOR_BOUNDARY_REVISION_ID)
    after_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(after_engine).get_table_names()) == before_tables
        with after_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == OAM_WORK_ORDER_PROJECTOR_BOUNDARY_REVISION_ID
    finally:
        after_engine.dispose()

    command.downgrade(config, PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION)
    downgraded_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(downgraded_engine).get_table_names()) == before_tables
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_OAM_WORK_ORDER_PROJECTOR_BOUNDARY_HEAD_REVISION
    finally:
        downgraded_engine.dispose()


def test_0044_postgresql_offline_sql_forces_exact_scope_rls(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    revision_spec = importlib.util.spec_from_file_location(
        "migration_0044_policy_roster",
        OAM_SYNC_SCOPE_FORCE_RLS_REVISION,
    )
    assert revision_spec is not None and revision_spec.loader is not None
    revision_module = importlib.util.module_from_spec(revision_spec)
    revision_spec.loader.exec_module(revision_module)
    assert len(revision_module.EXPECTED_POLICY_ROSTER) == 84
    assert len(
        {
            (policy[0], policy[1])
            for policy in revision_module.EXPECTED_POLICY_ROSTER
        }
    ) == 84
    upgrade_output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=upgrade_output,
    )
    command.upgrade(
        config,
        f"{PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION}:"
        f"{OAM_SYNC_SCOPE_FORCE_RLS_REVISION_ID}",
        sql=True,
    )
    sql = upgrade_output.getvalue()

    assert "-- Running upgrade 20260902_0043 -> 20260902_0044" in sql
    assert sql.count("CREATE POLICY ") == 84
    assert "CREATE TABLE public.oam_sync_scope_bindings" in sql
    assert (
        "LOCK TABLE public.external_sync_snapshots, "
        "public.external_sync_snapshot_batches, "
        "public.external_sync_snapshot_records, "
        "public.external_sync_current_records, public.sync_runs, "
        "public.sync_batches, public.sync_inbox_events, public.external_objects, "
        "public.external_object_versions, public.external_object_mappings, "
        "public.sync_conflicts, public.oam_work_orders IN SHARE ROW "
        "EXCLUSIVE MODE"
    ) in sql
    assert (
        "0044 requires an empty OAM sync graph; resynchronize from source "
        "after upgrade: %"
        in sql
    )
    assert sql.index("LOCK TABLE public.external_sync_snapshots") < sql.index(
        "CREATE TABLE public.oam_sync_scope_bindings"
    )
    for sync_graph_table in (
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "sync_runs",
        "sync_batches",
        "sync_inbox_events",
        "external_objects",
        "external_object_versions",
        "external_object_mappings",
        "sync_conflicts",
        "oam_work_orders",
    ):
        assert (
            f"SELECT 1 FROM public.{sync_graph_table}"
            in sql
        )
    assert "source_instance ||" not in sql
    assert "pg_catalog.decode('00', 'hex')" in sql
    assert "pg_catalog.sha256" in sql
    assert "formal_scope_key text GENERATED ALWAYS AS" in sql
    assert "session_user::text" in sql
    assert "current_setting" not in sql
    assert "set_config" not in sql
    normalized_sql = " ".join(sql.split())
    assert (
        "entity_type = 'work_order' OR ( principal_name = "
        "'star_oam_projector' AND capability = 'projector_read' AND "
        "entity_type = 'employee' ) ) AND scope_key ~ "
        "'^work-orders:recent-"
        in normalized_sql
    )
    external_helper = normalized_sql[
        normalized_sql.index(
            "CREATE FUNCTION public.rsc_oam_external_object_allowed_0044"
        ) : normalized_sql.index(
            "CREATE FUNCTION public.rsc_oam_version_allowed_0044"
        )
    ]
    version_helper = normalized_sql[
        normalized_sql.index(
            "CREATE FUNCTION public.rsc_oam_version_allowed_0044"
        ) : normalized_sql.index(
            "CREATE FUNCTION public.rsc_oam_person_allowed_0044"
        )
    ]
    work_order_helper = normalized_sql[
        normalized_sql.index(
            "CREATE FUNCTION public.rsc_oam_work_order_allowed_0044"
        ) : normalized_sql.index(
            "CREATE FUNCTION public.rsc_oam_snapshot_transition_guard_0044"
        )
    ]
    assert "p_operation_name IN ('select', 'update_old')" in external_helper
    assert "WHEN 'update_old' THEN p_is_current" in version_helper
    assert "WHEN 'update_new' THEN NOT p_is_current" in version_helper
    assert "p_operation_name IN ('select', 'update_old')" in work_order_helper
    assert (
        "p_operation_name IN ('insert', 'update_new') AND version.id = "
        "external.current_version_id"
        in work_order_helper
    )
    assert (
        "CREATE FUNCTION public.rsc_oam_snapshot_transition_guard_0044()"
        in normalized_sql
    )
    assert (
        "CREATE FUNCTION public.rsc_oam_projection_chain_guard_0044()"
        in normalized_sql
    )
    assert "0044 RLS helper ACL closure is invalid" in normalized_sql
    assert "0044 RLS policy closure is invalid" in normalized_sql
    assert "FULL JOIN actual_policy AS actual" in normalized_sql
    assert (
        "actual.using_expression IS DISTINCT FROM "
        "expected.using_expression"
        in normalized_sql
    )
    assert (
        "CREATE POLICY external_object_versions_projector_update_0044 "
        "ON public.external_object_versions AS PERMISSIVE FOR UPDATE TO "
        "star_oam_projector USING "
        "(public.rsc_oam_rls_check_0044('external_object_versions', "
        "'update_old'"
        in normalized_sql
    )
    assert (
        "WITH CHECK "
        "(public.rsc_oam_rls_check_0044('external_object_versions', "
        "'update_new'"
        in normalized_sql
    )
    assert (
        "CREATE TRIGGER trg_external_sync_snapshot_transition_0044 "
        "BEFORE UPDATE ON public.external_sync_snapshots FOR EACH ROW "
        "EXECUTE FUNCTION public.rsc_oam_snapshot_transition_guard_0044()"
        in normalized_sql
    )
    for table_name, trigger_name in (
        ("external_objects", "trg_external_objects_chain_0044"),
        (
            "external_object_versions",
            "trg_external_object_versions_chain_0044",
        ),
    ):
        assert (
            f"CREATE CONSTRAINT TRIGGER {trigger_name} "
            f"AFTER INSERT OR UPDATE ON public.{table_name} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION public.rsc_oam_projection_chain_guard_0044()"
            in normalized_sql
        )
    for snapshot_seal_condition in (
        "IF OLD.status <> 'receiving'",
        "IF NEW.status = 'complete'",
        "NEW.manifest_sha256 !~ '^[0-9a-f]{64}$'",
        "NEW.manifest_sha256 <> pg_catalog.encode(",
        "NEW.completed_at < NEW.received_at",
        "NEW.completed_at < NEW.snapshot_at",
        "NEW.completed_at > pg_catalog.statement_timestamp()",
        "ELSIF NEW.status = 'rejected_stale'",
        "NEW.manifest_json IS DISTINCT FROM OLD.manifest_json",
    ):
        assert snapshot_seal_condition in sql
    for projection_chain_condition in (
        "checked_deleted_at IS NOT NULL OR checked_pointer_id IS NULL",
        "current_version_count <> 1",
        "current_version_id IS DISTINCT FROM checked_pointer_id",
        "version.payload_jsonb = pg_catalog.jsonb_build_object(",
        "version.id = checked_pointer_id",
        "version.external_object_id = checked_object_id",
        "version.is_current",
        "version.valid_to IS NULL",
        "version.valid_from = version.created_at",
        "version.valid_from <= work_order.updated_at",
        "successor.valid_from = version.valid_to",
        "successor.source_updated_at < version.source_updated_at",
        "predecessor.valid_to = version.valid_from",
        "count(DISTINCT version.valid_from)",
        "0044 work-order version history is invalid",
    ):
        assert projection_chain_condition in normalized_sql
    for trigger_closure_condition in (
        "FROM pg_catalog.pg_trigger AS trigger_row",
        "trigger_row.tgtype = 19",
        "trigger_row.tgconstraint = 0",
        "NOT trigger_row.tgdeferrable",
        "NOT trigger_row.tginitdeferred",
        "trigger_row.tgtype = 21",
        "trigger_row.tgconstraint <> 0",
        "trigger_row.tgdeferrable",
        "trigger_row.tginitdeferred",
        "0044 projection lifecycle triggers are invalid",
    ):
        assert trigger_closure_condition in sql
    rls_body = sql[
        sql.index("CREATE FUNCTION public.rsc_oam_rls_check_0044") :
        sql.index("CREATE FUNCTION public.rsc_oam_runtime_binding_ready_0044")
    ]
    assert "EXECUTE" not in rls_body
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog" in sql
    assert "REVOKE ALL ON TABLE public.oam_sync_scope_bindings" in sql
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "public.rsc_oam_rls_check_0044(text,text,jsonb) "
        "TO star_oam_projector, edge_inbox"
    ) in sql
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "public.rsc_oam_runtime_binding_ready_0044() "
        "TO star_oam_projector, edge_inbox"
    ) in sql
    for table_name in (
        "oam_sync_scope_bindings",
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "audit_logs",
        "source_systems",
        "sync_runs",
        "sync_batches",
        "sync_inbox_events",
        "external_objects",
        "external_object_versions",
        "external_object_mappings",
        "sync_conflicts",
        "organizations",
        "people",
        "oam_work_orders",
    ):
        assert (
            f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY"
        ) in sql
        assert (
            f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY"
        ) in sql
        assert f"{table_name}_migrator_0044" in sql
        assert f"{table_name}_backup_select_0044" in sql
    assert "external_sync_snapshots_edge_insert_0044" in sql
    assert "external_sync_current_records_edge_delete_0044" in sql
    assert "audit_logs_edge_insert_0044" in sql
    assert "organizations_projector_select_0044" in sql
    assert "people_projector_select_0044" in sql
    assert "oam_work_orders_projector_update_0044" in sql
    assert "oam_sync_scope_bindings_projector_select_0044" not in sql
    assert "oam_sync_scope_bindings_edge_select_0044" not in sql

    downgrade_output = io.StringIO()
    downgrade_config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=downgrade_output,
    )
    command.downgrade(
        downgrade_config,
        f"{OAM_SYNC_SCOPE_FORCE_RLS_REVISION_ID}:"
        f"{PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION}",
        sql=True,
    )
    downgrade_sql = downgrade_output.getvalue()
    downgrade_lock_offset = downgrade_sql.index(
        "LOCK TABLE public.external_sync_snapshots"
    )
    revoke_offset = downgrade_sql.index(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public"
    )
    disable_offset = downgrade_sql.index(
        "ALTER TABLE public.oam_sync_scope_bindings NO FORCE ROW LEVEL SECURITY"
    )
    drop_offset = downgrade_sql.index(
        "DROP TABLE public.oam_sync_scope_bindings"
    )
    trigger_drop_offsets = (
        downgrade_sql.index(
            "DROP TRIGGER IF EXISTS trg_external_sync_snapshot_transition_0044"
        ),
        downgrade_sql.index(
            "DROP TRIGGER IF EXISTS trg_external_objects_chain_0044"
        ),
        downgrade_sql.index(
            "DROP TRIGGER IF EXISTS trg_external_object_versions_chain_0044"
        ),
    )
    first_policy_drop_offset = downgrade_sql.index(
        "DROP POLICY IF EXISTS oam_sync_scope_bindings_migrator_0044"
    )
    guard_function_drop_offsets = (
        downgrade_sql.index(
            "DROP FUNCTION IF EXISTS "
            "public.rsc_oam_snapshot_transition_guard_0044()"
        ),
        downgrade_sql.index(
            "DROP FUNCTION IF EXISTS "
            "public.rsc_oam_projection_chain_guard_0044()"
        ),
    )
    assert downgrade_lock_offset < revoke_offset < disable_offset < drop_offset
    assert revoke_offset < min(trigger_drop_offsets)
    assert max(trigger_drop_offsets) < first_policy_drop_offset
    assert max(trigger_drop_offsets) < min(guard_function_drop_offsets)
    assert "DROP FUNCTION IF EXISTS public.rsc_oam_rls_check_0044" in downgrade_sql
    assert "DROP POLICY IF EXISTS oam_work_orders_projector_update_0044" in downgrade_sql


def test_0044_sqlite_is_schema_noop_and_only_moves_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'oam-force-rls-noop.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION)
    before_engine = sa.create_engine(database_url)
    try:
        before_tables = set(inspect(before_engine).get_table_names())
    finally:
        before_engine.dispose()

    command.upgrade(config, OAM_SYNC_SCOPE_FORCE_RLS_REVISION_ID)
    after_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(after_engine).get_table_names()) == before_tables
        with after_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == OAM_SYNC_SCOPE_FORCE_RLS_REVISION_ID
    finally:
        after_engine.dispose()

    command.downgrade(config, PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION)
    downgraded_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(downgraded_engine).get_table_names()) == before_tables
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_OAM_SYNC_SCOPE_FORCE_RLS_HEAD_REVISION
    finally:
        downgraded_engine.dispose()


def _load_0045_migration_module():
    spec = importlib.util.spec_from_file_location(
        "material_request_approval_activation_migration_0045",
        MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0045_postgresql_functions_parse_as_sql_and_plpgsql() -> None:
    parser = pytest.importorskip("pglast.parser")
    module = _load_0045_migration_module()
    function_sql = (
        module._oam_runtime_ready_function_sql(module.revision),
        module._decision_guard_sql(repaired=True),
        module._decision_guard_sql(repaired=False),
        module._status_guard_sql(),
        module._line_guard_sql(),
        module._command_parent_lock_sql(),
        module._terminal_validator_sql(),
        module._return_validator_sql(),
        module._external_validator_sql(),
        module._projection_validator_sql(),
        module._projection_dispatcher_sql(),
    )

    for statement in function_sql:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    previous_decision_body = module._decision_guard_sql(
        repaired=False
    ).split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(previous_decision_body.encode("utf-8")).hexdigest() == (
        module.PREVIOUS_DECISION_GUARD_BODY_SHA256
    )

    from app.oam_sync_scope_security import (
        OAM_SYNC_FUNCTION_MANIFEST_0044,
        OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0045,
    )

    ready_signature = "rsc_oam_runtime_binding_ready_0044()"
    current_ready_sql = module._oam_runtime_ready_function_sql(module.revision)
    current_ready_body = current_ready_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    previous_ready_sql = module._oam_runtime_ready_function_sql(
        module.PREVIOUS_SCHEMA_REVISION
    )
    previous_ready_body = previous_ready_sql.split("AS $$", 1)[1].rsplit(
        "$$", 1
    )[0]
    assert hashlib.sha256(current_ready_body.encode("utf-8")).hexdigest() == (
        OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0045[ready_signature][6]
    )
    assert hashlib.sha256(previous_ready_body.encode("utf-8")).hexdigest() == (
        OAM_SYNC_FUNCTION_MANIFEST_0044[ready_signature][6]
    )


def test_0045_postgresql_offline_sql_closes_exact_approval_boundary(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0045_migration_module()
    assert len(module.LEGACY_TRIGGER_BINDINGS) == 45
    assert len(module.NEW_TRIGGER_BINDINGS) == 16
    assert len(module.TRIGGER_BINDINGS) == 61
    assert len(set(module.TRIGGER_BINDINGS)) == 61
    assert len(module.PROJECTION_TRIGGER_TABLES) == 13
    assert all(
        len(trigger_name.encode("utf-8")) <= 63
        for _, trigger_name in module.TRIGGER_BINDINGS
    )

    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION}:"
        f"{MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    assert "-- Running upgrade 20260902_0044 -> 20260903_0045" in sql
    assert sql.count(
        "CREATE OR REPLACE FUNCTION public.rsc_oam_runtime_binding_ready_0044()"
    ) == 1
    assert "pg_catalog.min(version_num) = '20260903_0045'" in sql
    lock_offset = sql.index("LOCK TABLE public.approval_step_candidates")
    preflight_offset = sql.index(module.UPGRADE_BLOCKER)
    request_file_security_sql = (
        "ALTER FUNCTION public.rsc_guard_material_request_file_0029() "
        "SECURITY DEFINER"
    )
    request_file_repair_offset = sql.index(request_file_security_sql)
    decision_guard_repair_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_guard_material_request_decision_quantity_0029()"
    )
    decision_guard_repair_offset = sql.index(decision_guard_repair_sql)
    dispatcher_repair_offset = sql.index(
        "ALTER FUNCTION public.rsc_dispatch_approval_causality_0030() "
        "SECURITY DEFINER"
    )
    first_enable_offset = sql.index(" ENABLE ALWAYS TRIGGER ")
    assert (
        lock_offset
        < preflight_offset
        < request_file_repair_offset
        < decision_guard_repair_offset
        < dispatcher_repair_offset
        < first_enable_offset
    )
    assert sql.count(request_file_security_sql) == 1
    assert sql.count(
        "ALTER FUNCTION public.rsc_guard_material_request_file_0029() "
        "OWNER TO star_oam_migrator"
    ) == 1
    assert sql.count(
        "REVOKE ALL ON FUNCTION "
        "public.rsc_guard_material_request_file_0029() "
        "FROM PUBLIC, star_oam_api"
    ) == 1
    assert sql.count(decision_guard_repair_sql) == 1
    assert "#variable_conflict error" in sql
    assert "line.request_id = guard_request_id" in sql
    assert "line.revision_id = guard_request_revision_id" in sql
    assert "line.request_id = request_id" not in (
        module._decision_guard_sql(repaired=True)
    )
    assert sql.count(
        "ALTER FUNCTION public."
        "rsc_guard_material_request_decision_quantity_0029() "
        "OWNER TO star_oam_migrator"
    ) == 1
    assert sql.count(
        "REVOKE ALL ON FUNCTION public."
        "rsc_guard_material_request_decision_quantity_0029() "
        "FROM PUBLIC, star_oam_api"
    ) == 1
    assert not any(
        statement.lstrip().upper().startswith("GRANT ")
        and "approval_delegations" in statement
        for statement in sql.split(";")
    )
    for table_name in module.APPROVAL_FACT_TABLES:
        assert f"EXISTS (SELECT 1 FROM public.{table_name})" in sql
    for table_name, trigger_name in module.TRIGGER_BINDINGS:
        statement = (
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
            f"{trigger_name}"
        )
        assert sql.count(statement) == 1
    assert sql.count("CREATE CONSTRAINT TRIGGER ") == 13
    assert (
        "CREATE TRIGGER trg_material_requests_status_transition_0045 "
        "BEFORE INSERT OR UPDATE"
    ) in sql
    assert (
        "CREATE TRIGGER trg_material_request_lines_projection_write_0045 "
        "BEFORE UPDATE"
    ) in sql
    assert (
        "CREATE TRIGGER trg_material_request_commands_parent_lock_0045 "
        "BEFORE INSERT"
    ) in sql
    for table_name, trigger_name in module.PROJECTION_TRIGGER_BINDINGS:
        assert sql.count(
            f"CREATE CONSTRAINT TRIGGER {trigger_name}"
        ) == 1

    parent_lock_sql = module._command_parent_lock_sql()
    terminal_sql = module._terminal_validator_sql()
    return_sql = module._return_validator_sql()
    external_sql = module._external_validator_sql()
    projection_sql = module._projection_validator_sql()
    dispatcher_sql = module._projection_dispatcher_sql()
    status_sql = module._status_guard_sql()
    assert "WHERE id = NEW.request_id" in parent_lock_sql
    assert "FOR UPDATE" in parent_lock_sql
    assert "NEW.operation <> 'cancel'" not in parent_lock_sql
    assert "SECURITY DEFINER" in terminal_sql
    assert "SECURITY DEFINER" in return_sql
    assert "SECURITY DEFINER" in external_sql
    assert "SECURITY DEFINER" in projection_sql
    assert "SECURITY DEFINER" in dispatcher_sql
    assert (
        "PERFORM public.rsc_validate_approval_instance_causality_0030"
        in projection_sql
    )
    assert "command.operation = 'region_decide'" in terminal_sql
    assert "command.operation = 'headquarters_decide'" in terminal_sql
    assert "command.operation = 'verify_external'" in terminal_sql
    assert "action.action = 'verify_external_accept'" in terminal_sql
    assert "event.reason = CASE" in terminal_sql
    assert "material_request.external_evidence.verify_accept" in terminal_sql
    assert "material_request.approval.reject" in terminal_sql
    assert "step.step_no = 1" in return_sql
    assert "command.operation = 'region_decide'" in return_sql
    assert "command.operation <> 'update_draft'" in return_sql
    assert "command.operation = 'cancel'" in return_sql
    assert "regional_approval_returned_to_requester" in return_sql
    assert "material_request.approval.return" in return_sql
    assert "command.operation = 'register_external'" in external_sql
    assert "command.operation = 'verify_external'" in external_sql
    assert "action.action <> 'register_external_evidence'" in external_sql
    assert "'verify_external_accept', 'verify_external_reject'" in external_sql
    assert (
        "registration.id::text =\n"
        "                              command.request_jsonb->>'registration_id'"
        in external_sql
    )
    assert (
        "registration.id::text =\n"
        "                              command.result_jsonb->>'registration_id'"
        in external_sql
    )
    assert "registration_manifest_sha256" in external_sql
    assert "approval_external_registration_lines" in dispatcher_sql
    for table_name in module.PROJECTION_TRIGGER_TABLES:
        assert f"'{table_name}'" in dispatcher_sql
    assert "checked_aggregate_type = 'material_request'" in dispatcher_sql
    assert "checked_aggregate_type = 'approval_step'" in dispatcher_sql
    assert "FROM public.approval_instances AS instance" in dispatcher_sql
    assert "FROM public.approval_steps AS step" in dispatcher_sql
    assert "command.operation = 'withdraw'" in projection_sql
    assert "command_count <> request_row.version + 1" in projection_sql
    assert "minimum_target_version <> 0" in projection_sql
    assert "maximum_target_version <> request_row.version" in projection_sql
    assert "command.target_version = request_row.version" in projection_sql
    assert "action.action = 'withdraw'" in projection_sql
    assert "event.action = 'material_request.withdraw'" in projection_sql
    assert "event.to_status = 'withdrawn'" in projection_sql
    assert (
        "request_row.withdrawn_at IS DISTINCT FROM "
        "latest_instance.completed_at"
    ) in projection_sql
    assert (
        "request_row.decided_at IS DISTINCT FROM "
        "latest_instance.completed_at"
    ) in projection_sql
    assert "NEW.status = 'cancellation_pending'" not in status_sql
    assert "request_row.status IN ('approved', 'partially_approved', 'rejected')" in (
        projection_sql
    )
    assert (
        "PERFORM public.rsc_validate_material_request_terminal_causality_0045"
        in projection_sql
    )
    assert (
        "PERFORM public.rsc_validate_material_request_return_causality_0045"
        in projection_sql
    )
    assert "FROM PUBLIC, star_oam_api" in sql


def test_0045_postgresql_offline_downgrade_requires_online_empty_graph_check(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    with pytest.raises(RuntimeError, match="requires an online connection"):
        command.downgrade(
            config,
            f"{MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION_ID}:"
            f"{PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION}",
            sql=True,
        )


def test_0045_sqlite_is_schema_noop_and_only_moves_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'approval-activation-noop.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION)
    before_engine = sa.create_engine(database_url)
    try:
        before_tables = set(inspect(before_engine).get_table_names())
    finally:
        before_engine.dispose()

    command.upgrade(config, MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION_ID)
    after_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(after_engine).get_table_names()) == before_tables
        with after_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == MATERIAL_REQUEST_APPROVAL_ACTIVATION_REVISION_ID
    finally:
        after_engine.dispose()

    command.downgrade(config, PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION)
    downgraded_engine = sa.create_engine(database_url)
    try:
        assert set(inspect(downgraded_engine).get_table_names()) == before_tables
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_MATERIAL_REQUEST_APPROVAL_ACTIVATION_HEAD_REVISION
    finally:
        downgraded_engine.dispose()


def _load_0046_migration_module():
    spec = importlib.util.spec_from_file_location(
        "material_request_draft_content_causality_migration_0046",
        MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0046_postgresql_functions_parse_as_sql_and_plpgsql() -> None:
    parser = pytest.importorskip("pglast.parser")
    module = _load_0046_migration_module()
    function_sql = (
        module._content_guard_sql(),
        module._content_validator_sql(),
        module._content_dispatcher_sql(),
        module._oam_runtime_ready_function_sql(module.revision),
        module._oam_runtime_ready_function_sql(module.PREVIOUS_SCHEMA_REVISION),
    )

    for statement in function_sql:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    manifest_sql = module._projection_manifest_expression("guard_revision.id")
    assert manifest_sql in function_sql[0]
    assert manifest_sql in function_sql[1]
    assert manifest_sql.count("pg_catalog.timezone('UTC'") == 3
    assert manifest_sql.count("'YYYY-MM-DD'") == 2
    assert re.search(
        r"pg_catalog\.to_char\(\s*manifest_revision\.expected_date,\s*"
        r"'YYYY-MM-DD'\s*\)",
        manifest_sql,
    )
    assert re.search(
        r"pg_catalog\.to_char\(\s*manifest_line\.required_date,\s*"
        r"'YYYY-MM-DD'\s*\)",
        manifest_sql,
    )
    assert "manifest_revision.expected_date::text" not in manifest_sql
    assert "manifest_line.required_date::text" not in manifest_sql
    assert "ORDER BY manifest_line.line_no, manifest_line.id" in manifest_sql
    assert "ORDER BY manifest_binding.id" in manifest_sql
    assert manifest_sql.count("'[]'::jsonb") == 2
    assert "manifest_line.status" not in manifest_sql
    assert "manifest_line.updated_at" not in manifest_sql
    assert "manifest_revision.updated_at" not in manifest_sql
    assert "manifest_revision.content_manifest_sha256" not in manifest_sql
    for approval_coordinate in (
        "approval_attempt_no",
        "approval_instance_id",
        "approval_instances",
        "attempt_no",
        "current_step_no",
    ):
        assert approval_coordinate not in manifest_sql


def test_0046_postgresql_offline_sql_seals_exact_draft_content_boundary(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0046_migration_module()
    assert module.revision == MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION_ID
    assert module.down_revision == (
        PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION
    )
    assert len(module.IMMEDIATE_TRIGGER_BINDINGS) == 4
    assert len(module.DEFERRED_TRIGGER_BINDINGS) == 4
    assert len(module.TRIGGER_BINDINGS) == 8
    assert len(set(module.TRIGGER_BINDINGS)) == 8
    assert all(
        len(trigger_name.encode("utf-8")) <= 63
        for _, trigger_name in module.TRIGGER_BINDINGS
    )

    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION}:"
        f"{MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    assert "-- Running upgrade 20260903_0045 -> 20260903_0046" in sql
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.CONTENT_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert sql.count(lock_sql) == 1
    for table_name in module.CONTENT_TABLES:
        assert f"EXISTS (SELECT 1 FROM public.{table_name})" in sql

    add_column_sql = (
        "ALTER TABLE public.material_request_commands ADD COLUMN "
        "projection_manifest_sha256 VARCHAR(64)"
    )
    assert sql.count(add_column_sql) == 1
    assert sql.count(module.MANIFEST_CONSTRAINT) == 1
    assert "operation IN ('create', 'update_draft', 'submit')" in sql
    assert (
        "operation IN ('create', 'update_draft', 'submit') AND "
        "projection_manifest_sha256 IS NOT NULL AND "
        "projection_manifest_sha256 ~ '^[0-9a-f]{64}$'"
    ) in sql
    assert (
        "operation NOT IN ('create', 'update_draft', 'submit') AND "
        "projection_manifest_sha256 IS NULL"
    ) in sql

    function_coordinates = (
        (module.PG_CONTENT_GUARD_FUNCTION, ""),
        (module.PG_CONTENT_VALIDATE_FUNCTION, "uuid"),
        (module.PG_CONTENT_DISPATCH_FUNCTION, ""),
    )
    for function_name, argument_types in function_coordinates:
        signature = f"public.{function_name}({argument_types})"
        assert sql.count(f"CREATE FUNCTION public.{function_name}") == 1
        assert sql.count(
            f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator"
        ) == 1
        assert sql.count(
            f"REVOKE ALL ON FUNCTION {signature} "
            "FROM PUBLIC, star_oam_api"
        ) == 1

    for table_name, trigger_name in module.IMMEDIATE_TRIGGER_BINDINGS:
        operations = (
            "INSERT"
            if table_name == "material_request_commands"
            else "INSERT OR UPDATE OR DELETE"
        )
        assert sql.count(
            f"CREATE TRIGGER {trigger_name} BEFORE {operations} "
            f"ON public.{table_name}"
        ) == 1
    for table_name, trigger_name in module.DEFERRED_TRIGGER_BINDINGS:
        assert sql.count(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} "
            f"AFTER INSERT OR UPDATE OR DELETE ON public.{table_name} "
            "DEFERRABLE INITIALLY DEFERRED"
        ) == 1
    assert sql.count("CREATE CONSTRAINT TRIGGER ") == 4
    for table_name, trigger_name in module.TRIGGER_BINDINGS:
        assert sql.count(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
            f"{trigger_name}"
        ) == 1
    assert sql.count(" ENABLE ALWAYS TRIGGER ") == 8

    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0046'" in sql
    lock_offset = sql.index(lock_sql)
    preflight_offset = sql.index(module.UPGRADE_BLOCKER)
    column_offset = sql.index(add_column_sql)
    constraint_offset = sql.index(module.MANIFEST_CONSTRAINT)
    guard_offset = sql.index(
        f"CREATE FUNCTION public.{module.PG_CONTENT_GUARD_FUNCTION}()"
    )
    trigger_offset = sql.index(
        f"CREATE TRIGGER {module.IMMEDIATE_TRIGGER_BINDINGS[0][1]}"
    )
    enable_offset = sql.index(" ENABLE ALWAYS TRIGGER ")
    ready_offset = sql.index(ready_sql)
    assert (
        lock_offset
        < preflight_offset
        < column_offset
        < constraint_offset
        < guard_offset
        < trigger_offset
        < enable_offset
        < ready_offset
    )

    guard_sql = module._content_guard_sql()
    validator_sql = module._content_validator_sql()
    dispatcher_sql = module._content_dispatcher_sql()
    assert "NEW.projection_manifest_sha256 IS NOT NULL" in guard_sql
    assert "NEW.actor_user_id <> guard_request.requester_user_id" in guard_sql
    assert "NEW.actor_person_id <> guard_request.requester_person_id" in guard_sql
    assert "guard_origin_manifest IS DISTINCT FROM guard_computed_manifest" in (
        guard_sql
    )
    for json_value, expected_value in (
        ("NEW.request_jsonb->>'schema'", "'rsc.material_request_command.v1'"),
        ("NEW.request_jsonb->>'operation'", "NEW.operation"),
        ("NEW.request_jsonb->>'request_id'", "guard_request.id::text"),
        ("NEW.request_jsonb->>'target_version'", "NEW.target_version::text"),
        ("NEW.request_jsonb->>'payload_sha256'", "NEW.request_hash"),
        ("NEW.request_jsonb->>'sensitive_fields'", "'excluded'"),
        ("NEW.result_jsonb->>'request_id'", "guard_request.id::text"),
        ("NEW.result_jsonb->>'revision_id'", "guard_revision.id::text"),
        (
            "NEW.result_jsonb->>'revision_no'",
            "guard_revision.revision_no::text",
        ),
        ("NEW.result_jsonb->>'kind'", "guard_result_kind"),
    ):
        assert re.search(
            rf"{re.escape(json_value)}\s+IS DISTINCT FROM\s+"
            rf"{re.escape(expected_value)}",
            guard_sql,
        )
    compact_guard_sql = " ".join(guard_sql.split())
    assert (
        "NEW.operation IN ('create', 'update_draft') AND "
        "NEW.request_jsonb->'approval_attempt_no' IS DISTINCT FROM "
        "'null'::jsonb"
    ) in compact_guard_sql
    assert (
        "jsonb_typeof(NEW.request_jsonb->'approval_attempt_no') "
        "IS DISTINCT FROM 'number'"
    ) in compact_guard_sql
    assert (
        "NEW.request_jsonb->>'approval_attempt_no' !~ '^[1-9][0-9]*$'"
    ) in compact_guard_sql
    assert (
        "jsonb_typeof(NEW.result_jsonb->'approval_attempt_no') "
        "IS DISTINCT FROM 'number'"
    ) in compact_guard_sql
    assert (
        "NEW.result_jsonb->>'approval_attempt_no' !~ '^[1-9][0-9]*$'"
    ) in compact_guard_sql
    assert (
        "NEW.request_jsonb->>'approval_attempt_no' IS DISTINCT FROM "
        "NEW.result_jsonb->>'approval_attempt_no'"
    ) in compact_guard_sql
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
    ) in compact_guard_sql
    assert "FOR guard_revision IN" in validator_sql
    assert "guard_submit.target_version <>" in validator_sql
    assert "guard_origin.target_version + 1" in validator_sql
    assert "WHEN 'cancelled' THEN guard_request.version - 1" in validator_sql
    assert "guard_origin.result_jsonb->'line_ids'" in validator_sql
    assert "guard_old_request_id" in dispatcher_sql
    assert "guard_new_request_id" in dispatcher_sql
    assert "FROM PUBLIC, star_oam_api" in sql
    assert not any(
        statement.lstrip().upper().startswith("GRANT ")
        for statement in sql.split(";")
    )


def test_0046_postgresql_offline_downgrade_requires_online_empty_graph_check(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    with pytest.raises(RuntimeError, match="requires an online connection"):
        command.downgrade(
            config,
            f"{MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION_ID}:"
            f"{PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION}",
            sql=True,
        )


def test_0046_sqlite_adds_and_drops_nullable_projection_manifest_column(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'draft-content-causality.db'}"
    config = _config(database_url)
    command.upgrade(
        config, PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION
    )
    before_engine = sa.create_engine(database_url)
    try:
        before_columns = {
            column["name"]
            for column in inspect(before_engine).get_columns(
                "material_request_commands"
            )
        }
        assert "projection_manifest_sha256" not in before_columns
    finally:
        before_engine.dispose()

    command.upgrade(config, MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION_ID)
    upgraded_engine = sa.create_engine(database_url)
    try:
        columns = {
            column["name"]: column
            for column in inspect(upgraded_engine).get_columns(
                "material_request_commands"
            )
        }
        manifest_column = columns["projection_manifest_sha256"]
        assert manifest_column["nullable"] is True
        assert isinstance(manifest_column["type"], sa.String)
        assert manifest_column["type"].length == 64
        assert manifest_column["default"] is None
        with upgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_REVISION_ID
    finally:
        upgraded_engine.dispose()

    command.downgrade(
        config, PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION
    )
    downgraded_engine = sa.create_engine(database_url)
    try:
        downgraded_columns = {
            column["name"]
            for column in inspect(downgraded_engine).get_columns(
                "material_request_commands"
            )
        }
        assert "projection_manifest_sha256" not in downgraded_columns
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_MATERIAL_REQUEST_DRAFT_CONTENT_CAUSALITY_HEAD_REVISION
    finally:
        downgraded_engine.dispose()


def _load_0047_migration_module():
    spec = importlib.util.spec_from_file_location(
        "nonopening_stocktake_start_causality_migration_0047",
        NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0048_migration_module():
    spec = importlib.util.spec_from_file_location(
        "stocktake_scope_guard_security_migration_0048",
        STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0049_migration_module():
    spec = importlib.util.spec_from_file_location(
        "stocktake_recount_guard_security_migration_0049",
        STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0050_migration_module():
    spec = importlib.util.spec_from_file_location(
        "stocktake_observation_scope_mode_migration_0050",
        STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0051_migration_module():
    spec = importlib.util.spec_from_file_location(
        "stocktake_difference_authorization_hash_migration_0051",
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0052_migration_module():
    spec = importlib.util.spec_from_file_location(
        "opening_terminal_guard_execution_migration_0052",
        OPENING_TERMINAL_GUARD_EXECUTION_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0032_migration_module():
    spec = importlib.util.spec_from_file_location(
        "nonopening_stocktake_review_recount_migration_0032",
        NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0053_migration_module():
    spec = importlib.util.spec_from_file_location(
        "opening_graph_table_dispatch_migration_0053",
        OPENING_GRAPH_TABLE_DISPATCH_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0054_migration_module():
    spec = importlib.util.spec_from_file_location(
        "opening_recount_source_history_migration_0054",
        OPENING_RECOUNT_SOURCE_HISTORY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0055_migration_module():
    spec = importlib.util.spec_from_file_location(
        "nonopening_start_audit_order_migration_0055",
        NONOPENING_START_AUDIT_ORDER_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0056_migration_module():
    spec = importlib.util.spec_from_file_location(
        "nonopening_count_guard_compatibility_migration_0056",
        NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0057_migration_module():
    spec = importlib.util.spec_from_file_location(
        "nonopening_difference_replay_lock_migration_0057",
        NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0058_migration_module():
    spec = importlib.util.spec_from_file_location(
        "nonopening_review_terminal_status_migration_0058",
        NONOPENING_REVIEW_TERMINAL_STATUS_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0059_migration_module():
    spec = importlib.util.spec_from_file_location(
        "material_request_supply_causality_migration_0059",
        MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0060_migration_module():
    spec = importlib.util.spec_from_file_location(
        "material_request_supply_security_migration_0060",
        MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_0061_migration_module():
    spec = importlib.util.spec_from_file_location(
        "material_request_supply_event_key_migration_0061",
        MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0047_postgresql_functions_parse_as_sql_and_plpgsql() -> None:
    parser = pytest.importorskip("pglast.parser")
    module = _load_0047_migration_module()
    function_sql = (
        module._postgresql_guard_function_sql(),
        module._postgresql_validator_function_sql(),
        module._postgresql_dispatch_function_sql(),
        module._oam_runtime_ready_function_sql(module.revision),
        module._oam_runtime_ready_function_sql(module.PREVIOUS_SCHEMA_REVISION),
    )

    for statement in function_sql:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    downgrade_probe = sa.text(
        "SELECT 1 WHERE "
        f"{module._legacy_start_exists_sql('public.')} LIMIT 1"
    )
    assert not downgrade_probe._bindparams

    authorization_sql = module._authorization_document_expression("completion")
    assert tuple(
        authorization_sql.index(f'"{key}"')
        for key in (
            "assignment_id",
            "authorization_version",
            "person_id",
            "role_code",
            "schema",
            "scope_id",
            "scope_type",
            "started_at",
            "user_id",
        )
    ) == tuple(
        sorted(
            authorization_sql.index(f'"{key}"')
            for key in (
                "assignment_id",
                "authorization_version",
                "person_id",
                "role_code",
                "schema",
                "scope_id",
                "scope_type",
                "started_at",
                "user_id",
            )
        )
    )
    assert "YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"" in authorization_sql
    graph_sql = module._graph_manifest_expression("completion")
    assert "pg_catalog.encode" in graph_sql
    assert "pg_catalog.sha256" in graph_sql
    assert "'hex'" in graph_sql
    assert "ORDER BY scope.scope_no, scope.id" in graph_sql
    assert "ORDER BY snapshot.scope_id, snapshot.stock_account_id" in graph_sql
    assert "ORDER BY event.occurred_at, event.id" in graph_sql
    assert "ORDER BY audit.sequence_no, audit.id" in graph_sql
    for forbidden_axis in (
        "allocation_status",
        "reservation_status",
        "outbound_status",
        "shipment_status",
        "logistics_signature_status",
        "oam_receipt_status",
        "personal_inbound_status",
        "notification_status",
        "reconciliation_status",
    ):
        assert forbidden_axis not in graph_sql


def test_0047_postgresql_offline_sql_seals_start_before_granting_acl(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0047_migration_module()
    assert module.revision == NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION_ID
    assert module.down_revision == (
        PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION
    )
    assert len(module.DEFERRED_TABLES) == 8
    assert set(module.PG_DEFERRED_TRIGGERS) == set(module.DEFERRED_TABLES)
    assert len(set(module.PG_DEFERRED_TRIGGERS.values())) == 8
    assert all(
        len(trigger_name.encode("utf-8")) <= 63
        for trigger_name in module.PG_DEFERRED_TRIGGERS.values()
    )

    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION}:"
        f"{NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    assert "-- Running upgrade 20260903_0046 -> 20260903_0047" in sql
    locked_tables = tuple(
        table_name
        for table_name in module.DEFERRED_TABLES
        if table_name != module.COMPLETION_TABLE
    )
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in locked_tables)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert sql.count(lock_sql) == 1
    assert module.UPGRADE_BLOCKER in sql
    create_table_sql = f"CREATE TABLE public.{module.COMPLETION_TABLE}"
    assert sql.count(create_table_sql) == 1
    assert "graph_manifest_sha256 VARCHAR(64) NOT NULL" in sql

    function_coordinates = (
        (module.PG_GUARD_FUNCTION, ""),
        (module.PG_VALIDATE_FUNCTION, "uuid"),
        (module.PG_DISPATCH_FUNCTION, ""),
    )
    for function_name, argument_types in function_coordinates:
        signature = f"public.{function_name}({argument_types})"
        assert sql.count(f"CREATE FUNCTION public.{function_name}") == 1
        assert sql.count(
            f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator"
        ) == 1
        assert sql.count(
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, star_oam_api"
        ) == 1

    assert sql.count(
        f"CREATE TRIGGER {module.PG_GUARD_TRIGGER} "
        f"BEFORE INSERT OR UPDATE OR DELETE ON public.{module.COMPLETION_TABLE}"
    ) == 1
    for table_name in module.DEFERRED_TABLES:
        trigger_name = module.PG_DEFERRED_TRIGGERS[table_name]
        assert sql.count(
            f"CREATE CONSTRAINT TRIGGER {trigger_name} "
            f"AFTER INSERT OR UPDATE OR DELETE ON public.{table_name} "
            "DEFERRABLE INITIALLY DEFERRED"
        ) == 1
        assert sql.count(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
            f"{trigger_name}"
        ) == 1
    assert sql.count("CREATE CONSTRAINT TRIGGER ") == 8
    assert (
        f"GRANT SELECT, INSERT ON TABLE public.{module.COMPLETION_TABLE} "
        "TO star_oam_api"
    ) in sql
    assert (
        "GRANT UPDATE (cutoff_ledger_cursor, cutoff_at, "
        "snapshot_manifest_sha256, issued_at, frozen_at) "
        "ON TABLE public.stocktake_tasks TO star_oam_api"
    ) in sql
    assert "GRANT UPDATE ON TABLE" not in sql
    assert "GRANT DELETE" not in sql
    assert "GRANT TRIGGER" not in sql

    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0047'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index(module.UPGRADE_BLOCKER)
        < sql.index(create_table_sql)
        < sql.index(f"CREATE FUNCTION public.{module.PG_GUARD_FUNCTION}()")
        < sql.index(f"CREATE TRIGGER {module.PG_GUARD_TRIGGER}")
        < sql.index("GRANT SELECT, INSERT ON TABLE")
        < sql.index(ready_sql)
    )


def test_0047_postgresql_offline_downgrade_requires_online_empty_graph_check(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    with pytest.raises(RuntimeError, match="requires an online evidence check"):
        command.downgrade(
            config,
            f"{NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION_ID}:"
            f"{PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION}",
            sql=True,
        )


def test_0047_sqlite_schema_and_triggers_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0047_migration_module()
    database_url = f"sqlite+pysqlite:///{tmp_path / 'stocktake-start-0047.db'}"
    config = _config(database_url)
    command.upgrade(
        config, PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION
    )
    before_engine = sa.create_engine(database_url)
    try:
        assert module.COMPLETION_TABLE not in inspect(before_engine).get_table_names()
    finally:
        before_engine.dispose()

    command.upgrade(config, NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION_ID)
    upgraded_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(upgraded_engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns(module.COMPLETION_TABLE)
        }
        assert tuple(columns) == (
            "id",
            "task_id",
            "initial_round_id",
            "expected_task_version",
            "started_task_version",
            "cutoff_ledger_cursor",
            "cutoff_at",
            "scope_count",
            "snapshot_line_count",
            "active_freeze_count",
            "scope_manifest_sha256",
            "snapshot_manifest_sha256",
            "request_sha256",
            "idempotency_key_hash",
            "started_by_user_id",
            "started_by_person_id",
            "started_role_assignment_id",
            "authorization_version",
            "role_code",
            "scope_type",
            "scope_id_snapshot",
            "authorization_sha256",
            "graph_manifest_sha256",
            "started_at",
            "created_at",
        )
        assert columns["graph_manifest_sha256"]["nullable"] is True
        assert columns["graph_manifest_sha256"]["default"] is None
        assert {row["name"] for row in inspector.get_indexes(module.COMPLETION_TABLE)} == {
            "ix_stocktake_start_completions_actor_0047"
        }
        with upgraded_engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' AND sql LIKE '%0047%'"
                )
            }
            assert triggers == {
                module.SQLITE_VALIDATE_TRIGGER,
                module.SQLITE_SEAL_TRIGGER,
                module.SQLITE_GUARD_UPDATE_TRIGGER,
                module.SQLITE_GUARD_DELETE_TRIGGER,
                module.SQLITE_TASK_SEAL_TRIGGER,
                module.SQLITE_SNAPSHOT_UPDATE_TRIGGER,
                module.SQLITE_SNAPSHOT_DELETE_TRIGGER,
                module.SQLITE_ROUND_SEAL_TRIGGER,
                *module.SQLITE_ADDITIONAL_SEAL_TRIGGERS,
            }
            validation_trigger_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name = ?",
                (module.SQLITE_VALIDATE_TRIGGER,),
            ).scalar_one()
            assert "scope.created_at IS NULL" in validation_trigger_sql
            assert "scope.created_at > NEW.cutoff_at" in validation_trigger_sql
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == NONOPENING_STOCKTAKE_START_CAUSALITY_REVISION_ID
    finally:
        upgraded_engine.dispose()

    command.downgrade(
        config, PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION
    )
    downgraded_engine = sa.create_engine(database_url)
    try:
        assert module.COMPLETION_TABLE not in inspect(downgraded_engine).get_table_names()
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_NONOPENING_STOCKTAKE_START_CAUSALITY_HEAD_REVISION
    finally:
        downgraded_engine.dispose()


def test_0048_pins_exact_0025_guard_body_and_runtime_ready_sql(
    monkeypatch,
) -> None:
    module = _load_0048_migration_module()
    assert module.revision == STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION_ID
    assert module.down_revision == (
        PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION
    )
    assert module.SCOPE_GUARD_FUNCTION == (
        "rsc_validate_stocktake_scope_region_owner_0025"
    )
    assert module.SCOPE_GUARD_TRIGGER == (
        "trg_stocktake_scopes_region_owner_0025"
    )

    spec = importlib.util.spec_from_file_location(
        "stocktake_scope_region_owner_migration_0025_for_0048",
        STOCKTAKE_SCOPE_REGION_OWNER_REVISION,
    )
    assert spec is not None and spec.loader is not None
    legacy_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy_module)
    statements: list[str] = []
    monkeypatch.setattr(legacy_module.op, "execute", statements.append)
    legacy_module._create_postgresql_guard()
    body_match = re.search(
        r"AS \$\$(?P<body>.*?)\$\$",
        statements[0],
        flags=re.DOTALL,
    )
    assert body_match is not None
    assert hashlib.sha256(
        body_match.group("body").encode("utf-8")
    ).hexdigest() == module.EXPECTED_FUNCTION_BODY_SHA256

    parser = pytest.importorskip("pglast.parser")
    module._verify_scope_guard_catalog(
        security_definer=False,
        expected_search_path=module.LEGACY_SEARCH_PATH,
        phase="parse probe",
    )
    catalog_sql = statements[-1]
    assert not sa.text(catalog_sql)._bindparams
    parser.parse_sql(catalog_sql)
    parser.parse_plpgsql_json(catalog_sql)
    for expected_revision in (module.PREVIOUS_SCHEMA_REVISION, module.revision):
        ready_sql = module._oam_runtime_ready_function_sql(expected_revision)
        assert not sa.text(ready_sql)._bindparams
        parser.parse_sql(ready_sql)
        parser.parse_plpgsql_json(ready_sql)


def test_0048_postgresql_offline_upgrade_hardens_exact_scope_guard(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0048_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION}:"
        f"{STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    signature = f"public.{module.SCOPE_GUARD_FUNCTION}()"
    lock_sql = (
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260903_0047 -> 20260903_0048" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 12
    assert sql.count(module.EXPECTED_FUNCTION_BODY_SHA256) == 2
    assert "legacy upgrade preflight" in sql
    assert "hardened upgrade postflight" in sql
    assert "function_row.prosecdef IS FALSE" in sql
    assert "function_row.prosecdef IS TRUE" in sql
    assert sql.count("ARRAY['search_path=pg_catalog, public']::text[]") == 2
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.prorettype = 'trigger'::pg_catalog.regtype" in sql
    assert "language_row.lanname = 'plpgsql'" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgtype = 7" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    assert "trigger_row.tgnargs = 0" in sql
    assert "trigger_row.tgattr = ''::pg_catalog.int2vector" in sql
    assert "function_acl.grantee = 0" in sql
    assert "pg_catalog.has_function_privilege" in sql
    assert f"ALTER FUNCTION {signature} SECURITY DEFINER" in sql
    assert (
        f"ALTER FUNCTION {signature} SET search_path = pg_catalog, public"
        in sql
    )
    assert (
        f"ALTER FUNCTION {signature} OWNER TO {module.MIGRATION_ROLE}" in sql
    )
    assert (
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, "
        f"{module.PRODUCTION_API_ROLE}" in sql
    )
    assert f"CREATE OR REPLACE FUNCTION {signature}" not in sql
    assert "GRANT " not in sql

    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0048'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("legacy upgrade preflight")
        < sql.index(f"ALTER FUNCTION {signature} SECURITY DEFINER")
        < sql.index("hardened upgrade postflight")
        < sql.index(ready_sql)
    )


def test_0048_postgresql_offline_downgrade_restores_exact_invoker_guard(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0048_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION_ID}:"
        f"{PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()

    signature = f"public.{module.SCOPE_GUARD_FUNCTION}()"
    lock_sql = (
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260903_0048 -> 20260903_0047" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 12
    assert sql.count(module.EXPECTED_FUNCTION_BODY_SHA256) == 2
    assert "hardened downgrade preflight" in sql
    assert "legacy downgrade postflight" in sql
    assert f"ALTER FUNCTION {signature} SECURITY INVOKER" in sql
    assert (
        f"ALTER FUNCTION {signature} SET search_path = pg_catalog, public"
        in sql
    )
    assert (
        f"ALTER FUNCTION {signature} OWNER TO {module.MIGRATION_ROLE}" in sql
    )
    assert (
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, "
        f"{module.PRODUCTION_API_ROLE}" in sql
    )
    assert f"CREATE OR REPLACE FUNCTION {signature}" not in sql
    assert "GRANT " not in sql
    assert "pg_catalog.min(version_num) = '20260903_0047'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("hardened downgrade preflight")
        < sql.index(f"ALTER FUNCTION {signature} SECURITY INVOKER")
        < sql.index("legacy downgrade postflight")
        < sql.index(
            "CREATE OR REPLACE FUNCTION public."
            "rsc_oam_runtime_binding_ready_0044()"
        )
    )


def test_0048_through_0051_sqlite_noops_then_0052_adds_request_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module_0048 = _load_0048_migration_module()
    module_0049 = _load_0049_migration_module()
    module_0050 = _load_0050_migration_module()
    module_0051 = _load_0051_migration_module()
    module_0052 = _load_0052_migration_module()
    for module in (
        module_0048,
        module_0049,
        module_0050,
        module_0051,
    ):
        assert "SQLite is an explicit schema no-op" in (module.__doc__ or "")
    assert "SQLite receives only the" in (module_0052.__doc__ or "")

    database_url = f"sqlite+pysqlite:///{tmp_path / 'security-noops.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION)

    def schema_snapshot() -> tuple[tuple[object, ...], ...]:
        engine = sa.create_engine(database_url)
        try:
            with engine.connect() as connection:
                return tuple(
                    connection.exec_driver_sql(
                        "SELECT type, name, tbl_name, sql FROM sqlite_master "
                        "ORDER BY type, name"
                    ).all()
                )
        finally:
            engine.dispose()

    def assert_revision(expected_revision: str) -> None:
        engine = sa.create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one() == expected_revision
        finally:
            engine.dispose()

    baseline_schema = schema_snapshot()
    assert_revision(PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION)
    for target_revision in (
        STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION_ID,
        STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION_ID,
        STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION_ID,
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID,
    ):
        command.upgrade(config, target_revision)
        assert schema_snapshot() == baseline_schema
        assert_revision(target_revision)

    command.upgrade(config, OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID)
    assert_revision(OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID)
    engine = sa.create_engine(database_url)
    try:
        columns = {
            column["name"]
            for column in sa.inspect(engine).get_columns(
                "stocktake_scope_count_completions"
            )
        }
    finally:
        engine.dispose()
    assert module_0052.COUNT_REQUEST_COLUMN in columns
    assert module_0052.COUNT_REQUEST_RESOLUTION_COLUMN in columns
    assert schema_snapshot() != baseline_schema

    command.downgrade(
        config,
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID,
    )
    assert schema_snapshot() == baseline_schema
    assert_revision(STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID)

    for target_revision in (
        STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION_ID,
        STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION_ID,
        STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION_ID,
        PRE_STOCKTAKE_SCOPE_GUARD_SECURITY_HEAD_REVISION,
    ):
        command.downgrade(config, target_revision)
        assert schema_snapshot() == baseline_schema
        assert_revision(target_revision)


def test_0052_sqlite_online_downgrade_blocks_either_request_evidence_column(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0052_migration_module()
    database_url = f"sqlite+pysqlite:///{tmp_path / '0052-evidence.db'}"
    config = _config(database_url)
    command.upgrade(config, OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID)

    row_id = uuid.uuid4().hex
    occurred_at = "2026-09-04 00:00:00.000000"
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            assert connection.exec_driver_sql(
                "PRAGMA foreign_keys"
            ).scalar_one() == 0
            trigger_names = tuple(
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'trigger' "
                    "AND tbl_name = 'stocktake_scope_count_completions'"
                ).scalars()
            )
            assert trigger_names
            for trigger_name in trigger_names:
                quoted_name = trigger_name.replace('"', '""')
                connection.exec_driver_sql(
                    f'DROP TRIGGER "{quoted_name}"'
                )
            connection.execute(
                sa.text(
                    "INSERT INTO stocktake_scope_count_completions ("
                    "id, task_id, round_id, scope_id, count_line_count, "
                    "observation_line_count, serial_count, total_counted_qty, "
                    "zero_confirmed, evidence_manifest_sha256, request_sha256, "
                    "idempotency_key_hash, completed_by_user_id, "
                    "completed_by_person_id, completed_role_assignment_id, "
                    "authorization_version, role_code, scope_type, "
                    "scope_id_snapshot, authorization_sha256, completed_at, "
                    "created_at, count_ledger_cursor, request_jsonb, "
                    "request_resolution_jsonb"
                    ") VALUES ("
                    ":id, :task_id, :round_id, :scope_id, 0, 0, 0, 0, 1, "
                    ":hash_a, :hash_b, :hash_c, :user_id, :person_id, "
                    ":assignment_id, 1, 'admin', 'national', '*', :hash_d, "
                    ":completed_at, :created_at, NULL, '{}', NULL"
                    ")"
                ),
                {
                    "id": row_id,
                    "task_id": uuid.uuid4().hex,
                    "round_id": uuid.uuid4().hex,
                    "scope_id": uuid.uuid4().hex,
                    "hash_a": "a" * 64,
                    "hash_b": "b" * 64,
                    "hash_c": "c" * 64,
                    "user_id": f"0052-{uuid.uuid4().hex[:20]}",
                    "person_id": uuid.uuid4().hex,
                    "assignment_id": uuid.uuid4().hex,
                    "hash_d": "d" * 64,
                    "completed_at": occurred_at,
                    "created_at": occurred_at,
                },
            )
            connection.commit()
    finally:
        engine.dispose()

    def assert_evidence_preserved() -> None:
        current_engine = sa.create_engine(database_url)
        try:
            with current_engine.connect() as connection:
                assert connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar_one() == OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID
                columns = {
                    column[1]
                    for column in connection.exec_driver_sql(
                        "PRAGMA table_info(stocktake_scope_count_completions)"
                    ).all()
                }
                assert module.COUNT_REQUEST_COLUMN in columns
                assert module.COUNT_REQUEST_RESOLUTION_COLUMN in columns
        finally:
            current_engine.dispose()

    with pytest.raises(RuntimeError, match=re.escape(module.DOWNGRADE_BLOCKER)):
        command.downgrade(
            config,
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID,
        )
    assert_evidence_preserved()

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "UPDATE stocktake_scope_count_completions "
                    "SET request_jsonb = NULL, request_resolution_jsonb = '{}' "
                    "WHERE id = :id"
                ),
                {"id": row_id},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match=re.escape(module.DOWNGRADE_BLOCKER)):
        command.downgrade(
            config,
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID,
        )
    assert_evidence_preserved()


def test_0049_pins_exact_nested_guard_bodies_catalog_and_runtime_ready_sql(
    monkeypatch,
) -> None:
    module = _load_0049_migration_module()
    assert module.revision == STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION_ID
    assert module.down_revision == (
        PRE_STOCKTAKE_RECOUNT_GUARD_SECURITY_HEAD_REVISION
    )
    assert len(module.FUNCTION_CATALOG) == 13
    assert len(module.CALLER_SIGNATURES) == 9
    assert len(module.INVOKER_SIGNATURES) == 4
    assert len(module.TRIGGER_CATALOG) == 15
    assert len(module.TRIGGER_FUNCTION_SIGNATURES) == 10
    assert module.CALLER_SIGNATURES == (
        "public.rsc_validate_stocktake_observation_disposition_0016()",
        "public.rsc_validate_stocktake_recount_scope_assignment_0018()",
        "public.rsc_validate_stocktake_count_line_insert_0021()",
        "public.rsc_validate_stocktake_observation_insert_0021()",
        "public.rsc_validate_stocktake_scope_completion_insert_0021()",
        "public.rsc_validate_stocktake_recount_case_0032()",
        "public.rsc_validate_stocktake_recount_task_advance_0032()",
        "public.rsc_validate_stocktake_recount_round_0032()",
        "public.rsc_require_stocktake_recount_graph_0032()",
    )
    assert module.INVOKER_SIGNATURES == (
        "public.rsc_stocktake_actor_assignment_valid_0011(text, uuid, uuid, "
        "bigint, timestamptz, text, text, text)",
        "public.rsc_stocktake_round_assignment_valid_0021(uuid, uuid, uuid, "
        "text, uuid, uuid, bigint, text, text, text, timestamptz, boolean)",
        "public.rsc_require_nonopening_stocktake_review_graph_0032()",
        "public.rsc_stocktake_recount_scope_graph_valid_0032(uuid)",
    )
    assert set(module.CALLER_SIGNATURES).isdisjoint(module.INVOKER_SIGNATURES)
    assert set(module.ALL_FUNCTION_SIGNATURES) == {
        row[0] for row in module.FUNCTION_CATALOG
    }
    assert set(module.EXPECTED_FUNCTION_BODY_SHA256) == set(
        module.ALL_FUNCTION_SIGNATURES
    )
    assert len({row[1] for row in module.TRIGGER_CATALOG}) == 15
    assert tuple(row[1] for row in module.TRIGGER_CATALOG) == (
        "trg_stocktake_observation_dispositions_validate_0016",
        "trg_stocktake_recount_scope_assignments_validate_0018",
        "trg_stocktake_count_lines_assignment_0021",
        "trg_stocktake_count_observations_assignment_0021",
        "trg_stocktake_scope_completions_assignment_0021",
        "trg_stocktake_recount_cases_review_path_0032",
        "trg_stocktake_tasks_recount_causality_0032",
        "trg_stocktake_rounds_recount_causality_0032",
        "trg_nonopening_review_graph_task_0032",
        "trg_nonopening_review_graph_review_0032",
        "trg_nonopening_review_graph_item_0032",
        "trg_stocktake_recount_graph_task_0032",
        "trg_stocktake_recount_graph_round_0032",
        "trg_stocktake_recount_graph_case_0032",
        "trg_stocktake_recount_graph_assignment_0032",
    )
    assert tuple(row[3:] for row in module.TRIGGER_CATALOG) == (
        *((7, False, False, False),) * 6,
        (19, False, False, False),
        (23, False, False, False),
        *((29, True, True, True),) * 7,
    )
    assert set(module.TRIGGER_FUNCTION_SIGNATURES) == {
        row[2] for row in module.TRIGGER_CATALOG
    }
    assert {
        row[0] for row in module.FUNCTION_CATALOG if row[5] is None
    } == {
        module.ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE,
        module.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
        module.RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
    }

    def load_legacy(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        legacy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_module)
        return legacy_module

    legacy_0011 = load_legacy(
        "opening_count_observations_migration_0011_for_0049",
        OPENING_COUNT_OBSERVATION_REVISION,
    )
    legacy_0016 = load_legacy(
        "opening_observation_dispositions_migration_0016_for_0049",
        OPENING_REVIEW_EVIDENCE_REVISION,
    )
    legacy_0018 = load_legacy(
        "stocktake_recount_causality_migration_0018_for_0049",
        STOCKTAKE_RECOUNT_REVISION,
    )
    legacy_0021 = load_legacy(
        "stocktake_round_assignment_guards_migration_0021_for_0049",
        STOCKTAKE_ROUND_ASSIGNMENT_GUARDS_REVISION,
    )
    legacy_0032 = load_legacy(
        "nonopening_stocktake_review_recount_migration_0032_for_0049",
        NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION,
    )
    statements: list[str] = []
    monkeypatch.setattr(legacy_0011.op, "execute", statements.append)
    legacy_0011._create_postgresql_count_contract_triggers()
    function_sql = {
        module.ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE: statements[0],
        module.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE:
            legacy_0016._postgresql_disposition_function_sql(),
        module.RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE:
            legacy_0018._postgresql_assignment_validate_sql(),
        module.ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE:
            legacy_0021._postgresql_actor_function_sql(),
        module.COUNT_LINE_CALLER_0021_SIGNATURE:
            legacy_0021._postgresql_count_line_function_sql(),
        module.OBSERVATION_CALLER_0021_SIGNATURE:
            legacy_0021._postgresql_observation_function_sql(round_aware=True),
        module.SCOPE_COMPLETION_CALLER_0021_SIGNATURE:
            legacy_0021._postgresql_completion_function_sql(round_aware=True),
        module.REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE:
            legacy_0032._postgresql_review_graph_function_sql(),
        module.RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE:
            legacy_0032._postgresql_scope_graph_function_sql(),
        module.RECOUNT_CASE_CALLER_0032_SIGNATURE:
            legacy_0032._postgresql_case_function_sql(),
        module.RECOUNT_TASK_CALLER_0032_SIGNATURE:
            legacy_0032._postgresql_task_function_sql(),
        module.RECOUNT_ROUND_CALLER_0032_SIGNATURE:
            legacy_0032._postgresql_round_function_sql(),
        module.RECOUNT_GRAPH_CALLER_0032_SIGNATURE:
            legacy_0032._postgresql_recount_graph_function_sql(),
    }
    assert set(function_sql) == set(module.ALL_FUNCTION_SIGNATURES)
    for signature, statement in function_sql.items():
        body_match = re.search(
            r"AS \$\$(?P<body>.*?)\$\$",
            statement,
            flags=re.DOTALL,
        )
        assert body_match is not None
        assert hashlib.sha256(
            body_match.group("body").encode("utf-8")
        ).hexdigest() == module.EXPECTED_FUNCTION_BODY_SHA256[signature]

    legacy_values = module._function_catalog_values(
        callers_security_definer=False
    )
    hardened_values = module._function_catalog_values(
        callers_security_definer=True
    )
    assert legacy_values.count("NULL::text") == 3
    assert hardened_values.count("NULL::text") == 1
    assert legacy_values.count(f"'{module.FIXED_SEARCH_PATH}'::text") == 10
    assert hardened_values.count(f"'{module.FIXED_SEARCH_PATH}'::text") == 12

    parser = pytest.importorskip("pglast.parser")
    statements.clear()
    module._verify_catalog(
        callers_security_definer=False,
        phase="legacy parse probe",
    )
    module._verify_catalog(
        callers_security_definer=True,
        phase="hardened parse probe",
    )
    for catalog_sql in statements:
        assert not sa.text(catalog_sql)._bindparams
        parser.parse_sql(catalog_sql)
        parser.parse_plpgsql_json(catalog_sql)
    for expected_revision in (module.PREVIOUS_SCHEMA_REVISION, module.revision):
        ready_sql = module._oam_runtime_ready_function_sql(expected_revision)
        assert not sa.text(ready_sql)._bindparams
        parser.parse_sql(ready_sql)
        parser.parse_plpgsql_json(ready_sql)


def test_0049_postgresql_offline_upgrade_hardens_exact_nested_guard_closure(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0049_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_STOCKTAKE_RECOUNT_GUARD_SECURITY_HEAD_REVISION}:"
        f"{STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.TRIGGER_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260903_0048 -> 20260903_0049" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 12
    assert "legacy upgrade preflight" in sql
    assert "hardened upgrade postflight" in sql
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.prokind = 'f'" in sql
    assert "NOT function_row.proretset" in sql
    assert "function_row.proargmodes IS NULL" in sql
    assert "function_row.pronargdefaults = 0" in sql
    assert "function_row.proargdefaults IS NULL" in sql
    assert "function_row.provariadic = 0" in sql
    assert "function_row.proconfig IS NOT DISTINCT FROM" in sql
    assert "function_row.proleakproof" in sql
    assert "function_row.proparallel = 'u'" in sql
    assert "function_acl.grantee = 0" in sql
    assert "function_acl.grantor <> migrator_oid" in sql
    assert "function_acl.is_grantable" in sql
    assert "pg_catalog.has_function_privilege" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    assert "trigger_row.tgnargs = 0" in sql
    assert "trigger_row.tgattr = ''::pg_catalog.int2vector" in sql
    for expected_hash in module.EXPECTED_FUNCTION_BODY_SHA256.values():
        assert sql.count(expected_hash) == 2
    for trigger_row in module.TRIGGER_CATALOG:
        assert sql.count(trigger_row[1]) == 2

    for signature in module.CALLER_SIGNATURES:
        assert sql.count(
            f"ALTER FUNCTION {signature} SECURITY DEFINER"
        ) == 1
        assert sql.count(
            f"ALTER FUNCTION {signature} "
            "SET search_path = pg_catalog, public"
        ) == 1
    for signature in module.INVOKER_SIGNATURES:
        assert f"ALTER FUNCTION {signature} SECURITY" not in sql
    for signature in module.ALL_FUNCTION_SIGNATURES:
        assert sql.count(
            f"ALTER FUNCTION {signature} OWNER TO {module.MIGRATION_ROLE}"
        ) == 1
        assert sql.count(
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, "
            f"{module.PRODUCTION_API_ROLE}"
        ) == 1
        assert f"CREATE OR REPLACE FUNCTION {signature}" not in sql
    assert "GRANT " not in sql

    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0049'" in sql
    first_caller = module.CALLER_SIGNATURES[0]
    assert (
        sql.index(lock_sql)
        < sql.index("legacy upgrade preflight")
        < sql.index(f"ALTER FUNCTION {first_caller} SECURITY DEFINER")
        < sql.index("hardened upgrade postflight")
        < sql.index(ready_sql)
    )


def test_0049_postgresql_offline_downgrade_restores_exact_invoker_closure(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0049_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION_ID}:"
        f"{PRE_STOCKTAKE_RECOUNT_GUARD_SECURITY_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()

    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.TRIGGER_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260903_0049 -> 20260903_0048" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 12
    assert "hardened downgrade preflight" in sql
    assert "legacy downgrade postflight" in sql
    for expected_hash in module.EXPECTED_FUNCTION_BODY_SHA256.values():
        assert sql.count(expected_hash) == 2
    legacy_unset_callers = {
        module.OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
        module.RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
    }
    for signature in module.CALLER_SIGNATURES:
        assert sql.count(
            f"ALTER FUNCTION {signature} SECURITY INVOKER"
        ) == 1
        if signature in legacy_unset_callers:
            assert sql.count(
                f"ALTER FUNCTION {signature} RESET search_path"
            ) == 1
        else:
            assert sql.count(
                f"ALTER FUNCTION {signature} "
                "SET search_path = pg_catalog, public"
            ) == 1
    for signature in module.INVOKER_SIGNATURES:
        assert f"ALTER FUNCTION {signature} SECURITY" not in sql
    for signature in module.ALL_FUNCTION_SIGNATURES:
        assert sql.count(
            f"ALTER FUNCTION {signature} OWNER TO {module.MIGRATION_ROLE}"
        ) == 1
        assert sql.count(
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, "
            f"{module.PRODUCTION_API_ROLE}"
        ) == 1
        assert f"CREATE OR REPLACE FUNCTION {signature}" not in sql
    assert "GRANT " not in sql
    assert "pg_catalog.min(version_num) = '20260903_0048'" in sql
    first_caller = module.CALLER_SIGNATURES[0]
    assert (
        sql.index(lock_sql)
        < sql.index("hardened downgrade preflight")
        < sql.index(f"ALTER FUNCTION {first_caller} SECURITY INVOKER")
        < sql.index("legacy downgrade postflight")
        < sql.index(
            "CREATE OR REPLACE FUNCTION public."
            "rsc_oam_runtime_binding_ready_0044()"
        )
    )


def test_0050_pins_exact_observation_body_replacement_and_catalog_sql(
    monkeypatch,
) -> None:
    module = _load_0050_migration_module()
    assert module.revision == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION_ID
    assert module.down_revision == (
        PRE_STOCKTAKE_OBSERVATION_SCOPE_MODE_HEAD_REVISION
    )
    assert module.FUNCTION_SIGNATURE == (
        "public.rsc_validate_stocktake_observation_insert_0021()"
    )
    assert module.TRIGGER_TABLE == "stocktake_count_observations"
    assert module.TRIGGER_NAME == (
        "trg_stocktake_count_observations_assignment_0021"
    )

    spec = importlib.util.spec_from_file_location(
        "stocktake_round_assignment_guards_migration_0021_for_0050",
        STOCKTAKE_ROUND_ASSIGNMENT_GUARDS_REVISION,
    )
    assert spec is not None and spec.loader is not None
    legacy_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy_module)
    legacy_sql = legacy_module._postgresql_observation_function_sql(
        round_aware=True
    )
    body_match = re.search(
        r"AS \$\$(?P<body>.*?)\$\$",
        legacy_sql,
        flags=re.DOTALL,
    )
    assert body_match is not None
    legacy_body = body_match.group("body")
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        module.LEGACY_BODY_SHA256
    )
    assert legacy_body.count(module.LEGACY_SOURCE_FRAGMENT) == 1
    assert module.QUALIFIED_SOURCE_FRAGMENT not in legacy_body

    qualified_body = legacy_body.replace(
        module.LEGACY_SOURCE_FRAGMENT,
        module.QUALIFIED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(qualified_body.encode("utf-8")).hexdigest() == (
        module.QUALIFIED_BODY_SHA256
    )
    assert qualified_body.count(module.QUALIFIED_SOURCE_FRAGMENT) == 1
    assert module.LEGACY_SOURCE_FRAGMENT not in qualified_body
    assert qualified_body.replace(
        module.QUALIFIED_SOURCE_FRAGMENT,
        module.LEGACY_SOURCE_FRAGMENT,
    ) == legacy_body

    parser = pytest.importorskip("pglast.parser")
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_catalog(
        expected_body_sha256=module.LEGACY_BODY_SHA256,
        expected_source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        forbidden_source_fragment=module.QUALIFIED_SOURCE_FRAGMENT,
        phase="legacy parse probe",
    )
    module._replace_body_fragment(
        expected_body_sha256=module.LEGACY_BODY_SHA256,
        source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=module.QUALIFIED_SOURCE_FRAGMENT,
        phase="upgrade parse probe",
    )
    module._verify_catalog(
        expected_body_sha256=module.QUALIFIED_BODY_SHA256,
        expected_source_fragment=module.QUALIFIED_SOURCE_FRAGMENT,
        forbidden_source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        phase="qualified parse probe",
    )
    module._replace_body_fragment(
        expected_body_sha256=module.QUALIFIED_BODY_SHA256,
        source_fragment=module.QUALIFIED_SOURCE_FRAGMENT,
        replacement_fragment=module.LEGACY_SOURCE_FRAGMENT,
        phase="downgrade parse probe",
    )
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    for expected_revision in (module.PREVIOUS_SCHEMA_REVISION, module.revision):
        ready_sql = module._oam_runtime_ready_function_sql(expected_revision)
        assert not sa.text(ready_sql)._bindparams
        parser.parse_sql(ready_sql)
        parser.parse_plpgsql_json(ready_sql)

    with pytest.raises(ValueError, match="unsupported stocktake observation"):
        module._verify_catalog(
            expected_body_sha256="0" * 64,
            expected_source_fragment=module.LEGACY_SOURCE_FRAGMENT,
            forbidden_source_fragment=module.QUALIFIED_SOURCE_FRAGMENT,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported stocktake observation"):
        module._replace_body_fragment(
            expected_body_sha256=module.LEGACY_BODY_SHA256,
            source_fragment=module.QUALIFIED_SOURCE_FRAGMENT,
            replacement_fragment=module.LEGACY_SOURCE_FRAGMENT,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported OAM runtime"):
        module._oam_runtime_ready_function_sql("20260903_9999")


def test_0050_postgresql_offline_upgrade_qualifies_only_scope_mode_source(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0050_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_STOCKTAKE_OBSERVATION_SCOPE_MODE_HEAD_REVISION}:"
        f"{STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    lock_sql = (
        "LOCK TABLE public.stocktake_count_observations "
        "IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260903_0049 -> 20260903_0050" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 14
    assert sql.count(module.REPLACEMENT_ERROR) == 3
    assert "legacy upgrade preflight" in sql
    assert "qualified upgrade postflight" in sql
    assert sql.count(module.LEGACY_BODY_SHA256) == 2
    assert sql.count(module.QUALIFIED_BODY_SHA256) == 1
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.prokind = 'f'" in sql
    assert "function_row.prorettype = pg_catalog.to_regtype('trigger')" in sql
    assert "function_row.pronargs = 0" in sql
    assert "function_row.proargtypes = ''::pg_catalog.oidvector" in sql
    assert "function_row.proallargtypes IS NULL" in sql
    assert "function_row.proargnames IS NULL" in sql
    assert "function_row.proargmodes IS NULL" in sql
    assert "function_row.provariadic = 0" in sql
    assert "function_row.prosecdef" in sql
    assert "function_row.proconfig = ARRAY['search_path=pg_catalog, public']" in sql
    assert "function_acl.grantor <> migrator_oid" in sql
    assert "function_acl.grantee = 0" in sql
    assert "pg_catalog.has_function_privilege" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgtype = 7" in sql
    assert "trigger_row.tgconstraint = 0" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    assert "trigger_row.tgnargs = 0" in sql
    assert "trigger_row.tgattr = ''::pg_catalog.int2vector" in sql
    assert "trigger_row.tgfoid = function_oid" in sql
    assert "pg_catalog.pg_get_functiondef(function_oid)" in sql
    assert "EXECUTE pg_catalog.replace" in sql
    assert f"ALTER FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert f"CREATE OR REPLACE FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert "GRANT " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql

    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0050'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("legacy upgrade preflight")
        < sql.index("0050 stocktake observation scope-mode replacement failed")
        < sql.index("qualified upgrade postflight")
        < sql.index(ready_sql)
    )


def test_0050_postgresql_offline_downgrade_restores_exact_legacy_body(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0050_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION_ID}:"
        f"{PRE_STOCKTAKE_OBSERVATION_SCOPE_MODE_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()

    lock_sql = (
        "LOCK TABLE public.stocktake_count_observations "
        "IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260903_0050 -> 20260903_0049" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 14
    assert sql.count(module.REPLACEMENT_ERROR) == 3
    assert "qualified downgrade preflight" in sql
    assert "legacy downgrade postflight" in sql
    assert sql.count(module.QUALIFIED_BODY_SHA256) == 2
    assert sql.count(module.LEGACY_BODY_SHA256) == 1
    assert "pg_catalog.pg_get_functiondef(function_oid)" in sql
    assert "EXECUTE pg_catalog.replace" in sql
    assert f"ALTER FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert f"CREATE OR REPLACE FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert "GRANT " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert "pg_catalog.min(version_num) = '20260903_0049'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("qualified downgrade preflight")
        < sql.index("0050 stocktake observation scope-mode replacement failed")
        < sql.index("legacy downgrade postflight")
        < sql.index(
            "CREATE OR REPLACE FUNCTION public."
            "rsc_oam_runtime_binding_ready_0044()"
        )
    )


def test_0051_pins_exact_authorization_hash_repair_and_catalog_sql(
    monkeypatch,
) -> None:
    module = _load_0051_migration_module()
    assert module.revision == (
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID
    )
    assert module.down_revision == (
        PRE_STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_HEAD_REVISION
    )
    assert module.FUNCTION_SIGNATURE == (
        "public.rsc_validate_stocktake_difference_set_completion_0031()"
    )
    assert module.TRIGGER_TABLE == "stocktake_difference_set_completions"
    assert module.TRIGGER_NAME == (
        "trg_stocktake_difference_set_completions_validate_0031"
    )
    assert module.LEGACY_TRIGGER_ENABLED == "O"
    assert module.FIXED_TRIGGER_ENABLED == "A"
    assert module.NONOPENING_AUTHORIZATION_SCHEMA == (
        "cloud_oam.stocktake.difference_authorization.v1"
    )
    assert module.NONOPENING_AUTHORIZATION_CANONICAL_KEYS == (
        "assignment_id",
        "authorization_version",
        "completed_at",
        "person_id",
        "role_code",
        "schema",
        "scope_id",
        "scope_type",
        "user_id",
    )
    assert module.NONOPENING_AUTHORIZATION_CANONICAL_KEYS == tuple(
        sorted(module.NONOPENING_AUTHORIZATION_CANONICAL_KEYS)
    )

    spec = importlib.util.spec_from_file_location(
        "stocktake_difference_evaluator_migration_0031_for_0051",
        STOCKTAKE_DIFFERENCE_EVALUATOR_REVISION,
    )
    assert spec is not None and spec.loader is not None
    legacy_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy_module)
    legacy_sql = legacy_module._postgresql_0031_function_sql()
    body_match = re.search(
        r"AS \$\$(?P<body>.*?)\$\$",
        legacy_sql,
        flags=re.DOTALL,
    )
    assert body_match is not None
    legacy_body = body_match.group("body")
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        module.LEGACY_BODY_SHA256
    )
    assert legacy_body.count(module.LEGACY_SOURCE_FRAGMENT) == 1
    assert module.FIXED_SOURCE_FRAGMENT not in legacy_body

    fixed_body = legacy_body.replace(
        module.LEGACY_SOURCE_FRAGMENT,
        module.FIXED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
        module.FIXED_BODY_SHA256
    )
    assert fixed_body.count(module.FIXED_SOURCE_FRAGMENT) == 1
    assert module.LEGACY_SOURCE_FRAGMENT not in fixed_body
    assert fixed_body.replace(
        module.FIXED_SOURCE_FRAGMENT,
        module.LEGACY_SOURCE_FRAGMENT,
    ) == legacy_body

    parser = pytest.importorskip("pglast.parser")
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_catalog(
        expected_body_sha256=module.LEGACY_BODY_SHA256,
        expected_source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        forbidden_source_fragment=module.FIXED_SOURCE_FRAGMENT,
        expected_trigger_enabled=module.LEGACY_TRIGGER_ENABLED,
        phase="legacy parse probe",
    )
    module._verify_existing_rows(phase="existing rows parse probe")
    module._replace_body_fragment(
        expected_body_sha256=module.LEGACY_BODY_SHA256,
        source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=module.FIXED_SOURCE_FRAGMENT,
        phase="upgrade parse probe",
    )
    module._verify_catalog(
        expected_body_sha256=module.FIXED_BODY_SHA256,
        expected_source_fragment=module.FIXED_SOURCE_FRAGMENT,
        forbidden_source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        expected_trigger_enabled=module.FIXED_TRIGGER_ENABLED,
        phase="fixed parse probe",
    )
    module._replace_body_fragment(
        expected_body_sha256=module.FIXED_BODY_SHA256,
        source_fragment=module.FIXED_SOURCE_FRAGMENT,
        replacement_fragment=module.LEGACY_SOURCE_FRAGMENT,
        phase="downgrade parse probe",
    )
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    existing_rows_sql = next(
        statement
        for statement in statements
        if module.EXISTING_ROWS_ERROR in statement
    )
    normalized_existing_rows_sql = " ".join(existing_rows_sql.split())
    for stable_historical_guard in (
        "FROM public.users AS app_user",
        "JOIN public.people AS person ON person.id = app_user.person_id",
        "JOIN public.role_assignments AS assignment ON assignment.id = "
        "completion.completed_role_assignment_id AND assignment.user_id = "
        "app_user.id",
        "JOIN public.roles AS role ON role.id = assignment.role_id",
        "app_user.id = completion.completed_by_user_id",
        "person.id = completion.completed_by_person_id",
        "role.code = completion.role_code",
        "NOT role.is_external",
        "assignment.scope_type = completion.scope_type",
        "assignment.scope_id = completion.scope_id_snapshot",
        "assignment.valid_from <= completion.completed_at",
        "completion.completed_at < assignment.valid_to",
        "completion.completed_at < assignment.revoked_at",
        "role.code = 'admin' AND assignment.scope_type = 'national' "
        "AND assignment.scope_id = '*'",
        "role.code = 'provincial_manager' AND assignment.scope_type = "
        "'organization' AND assignment.scope_id = task.region_org_id::text",
        "completion.authorization_sha256 IS DISTINCT FROM",
        "pg_catalog.sha256(",
        "pg_catalog.convert_to(",
        "pg_catalog.timezone('UTC', completion.completed_at)",
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'",
    ):
        assert stable_historical_guard in normalized_existing_rows_sql
    for forbidden_current_state_dependency in (
        "app_user.account_status",
        "app_user.is_active",
        "app_user.authorization_version",
        "person.employment_status",
        "assignment.status",
        "role.status",
        "role_permissions",
        "permissions AS permission",
    ):
        assert forbidden_current_state_dependency not in existing_rows_sql

    for expected_revision in (module.PREVIOUS_SCHEMA_REVISION, module.revision):
        ready_sql = module._oam_runtime_ready_function_sql(expected_revision)
        assert not sa.text(ready_sql)._bindparams
        parser.parse_sql(ready_sql)
        parser.parse_plpgsql_json(ready_sql)

    with pytest.raises(ValueError, match="unsupported stocktake difference"):
        module._verify_catalog(
            expected_body_sha256=module.LEGACY_BODY_SHA256,
            expected_source_fragment=module.LEGACY_SOURCE_FRAGMENT,
            forbidden_source_fragment=module.FIXED_SOURCE_FRAGMENT,
            expected_trigger_enabled=module.FIXED_TRIGGER_ENABLED,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported stocktake difference"):
        module._replace_body_fragment(
            expected_body_sha256=module.LEGACY_BODY_SHA256,
            source_fragment=module.FIXED_SOURCE_FRAGMENT,
            replacement_fragment=module.LEGACY_SOURCE_FRAGMENT,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported OAM runtime"):
        module._oam_runtime_ready_function_sql("20260903_9999")


def test_0051_nonopening_authorization_sha256_sql_pins_python_canonical_json(
) -> None:
    module = _load_0051_migration_module()
    service_source = (
        ROOT
        / "backend"
        / "app"
        / "formal_services"
        / "stocktake_difference.py"
    ).read_text(encoding="utf-8")
    service_tree = ast.parse(service_source)
    authorization_function = next(
        node
        for node in service_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_difference_authorization_sha256"
    )
    return_node = next(
        node
        for node in ast.walk(authorization_function)
        if isinstance(node, ast.Return)
    )
    assert isinstance(return_node.value, ast.Call)
    assert isinstance(return_node.value.func, ast.Name)
    assert return_node.value.func.id == "_sha256"
    assert len(return_node.value.args) == 1
    service_document = return_node.value.args[0]
    assert isinstance(service_document, ast.Dict)
    service_keys = tuple(
        key.value
        for key in service_document.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    )
    assert service_keys == module.NONOPENING_AUTHORIZATION_CANONICAL_KEYS
    service_schema_index = service_keys.index("schema")
    service_schema = service_document.values[service_schema_index]
    assert isinstance(service_schema, ast.Constant)
    assert service_schema.value == module.NONOPENING_AUTHORIZATION_SCHEMA

    hash_sql = module._NONOPENING_AUTHORIZATION_SHA256_SQL
    json_fragments = (
        '{"assignment_id":',
        ',"authorization_version":',
        ',"completed_at":',
        ',"person_id":',
        ',"role_code":',
        ',"schema":"cloud_oam.stocktake.difference_authorization.v1"',
        ',"scope_id":',
        ',"scope_type":',
        ',"user_id":',
        '}',
    )
    positions = tuple(hash_sql.index(fragment) for fragment in json_fragments)
    assert positions == tuple(sorted(positions))
    assert all(hash_sql.count(fragment) == 1 for fragment in json_fragments)
    assert "completion.authorization_version::text" in hash_sql
    assert hash_sql.count("pg_catalog.to_json(") == 7
    assert (
        "pg_catalog.timezone('UTC', completion.completed_at)" in hash_sql
    )
    assert "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'" in hash_sql
    assert "pg_catalog.convert_to(" in hash_sql
    assert "'UTF8'" in hash_sql
    assert "pg_catalog.sha256(" in hash_sql
    assert "pg_catalog.encode(" in hash_sql
    assert "'hex'" in hash_sql
    timestamp_function = next(
        node
        for node in service_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_timestamp"
    )
    normalized_timestamp_source = " ".join(
        ast.unparse(timestamp_function).split()
    )
    assert "isoformat(timespec='microseconds')" in normalized_timestamp_source
    assert ".replace('+00:00', 'Z')" in normalized_timestamp_source

    document = {
        "assignment_id": "11111111-1111-1111-1111-111111111111",
        "authorization_version": 7,
        "completed_at": "2026-09-03T12:34:56.123456Z",
        "person_id": "22222222-2222-2222-2222-222222222222",
        "role_code": "provincial_manager",
        "schema": module.NONOPENING_AUTHORIZATION_SCHEMA,
        "scope_id": "33333333-3333-3333-3333-333333333333",
        "scope_type": "organization",
        "user_id": "user-001",
    }
    canonical_json = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert canonical_json == (
        '{"assignment_id":"11111111-1111-1111-1111-111111111111",'
        '"authorization_version":7,'
        '"completed_at":"2026-09-03T12:34:56.123456Z",'
        '"person_id":"22222222-2222-2222-2222-222222222222",'
        '"role_code":"provincial_manager",'
        '"schema":"cloud_oam.stocktake.difference_authorization.v1",'
        '"scope_id":"33333333-3333-3333-3333-333333333333",'
        '"scope_type":"organization","user_id":"user-001"}'
    )
    assert hashlib.sha256(canonical_json.encode("utf-8")).hexdigest() == (
        "df73a093d060d5a0dd8474a44ce23c8ba0eab54b96abbac7d8e16cb3ee79a9ec"
    )


def test_0051_postgresql_offline_upgrade_repairs_hash_and_enables_always(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0051_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_HEAD_REVISION}:"
        f"{STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()

    lock_sql = (
        "LOCK TABLE public.stocktake_difference_set_completions "
        "IN ACCESS EXCLUSIVE MODE"
    )
    enable_sql = (
        "ALTER TABLE public.stocktake_difference_set_completions "
        "ENABLE ALWAYS TRIGGER "
        "trg_stocktake_difference_set_completions_validate_0031"
    )
    assert "-- Running upgrade 20260903_0050 -> 20260903_0051" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 14
    assert sql.count(module.REPLACEMENT_ERROR) == 3
    assert sql.count(module.EXISTING_ROWS_ERROR) == 1
    assert "legacy upgrade preflight" in sql
    assert "upgrade preflight" in sql
    assert "fixed upgrade postflight" in sql
    assert sql.count(module.LEGACY_BODY_SHA256) == 2
    assert sql.count(module.FIXED_BODY_SHA256) == 1
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.prokind = 'f'" in sql
    assert "function_row.prorettype = pg_catalog.to_regtype('trigger')" in sql
    assert "NOT function_row.proretset" in sql
    assert "function_row.pronargs = 0" in sql
    assert "function_row.proargtypes = ''::pg_catalog.oidvector" in sql
    assert "function_row.proallargtypes IS NULL" in sql
    assert "function_row.proargnames IS NULL" in sql
    assert "function_row.proargmodes IS NULL" in sql
    assert "function_row.pronargdefaults = 0" in sql
    assert "function_row.proargdefaults IS NULL" in sql
    assert "function_row.provariadic = 0" in sql
    assert "language_row.lanname = 'plpgsql'" in sql
    assert "function_row.provolatile = 'v'" in sql
    assert "NOT function_row.proisstrict" in sql
    assert "NOT function_row.proleakproof" in sql
    assert "function_row.proparallel = 'u'" in sql
    assert "NOT function_row.prosecdef" in sql
    assert "function_row.proconfig = ARRAY['search_path=pg_catalog, public']" in sql
    assert "function_acl.grantor <> migrator_oid" in sql
    assert "function_acl.grantee = 0" in sql
    assert "pg_catalog.has_function_privilege" in sql
    assert "trigger_row.tgenabled = 'O'" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgtype = 7" in sql
    assert "trigger_row.tgconstraint = 0" in sql
    assert "NOT trigger_row.tgdeferrable" in sql
    assert "NOT trigger_row.tginitdeferred" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    assert "trigger_row.tgnargs = 0" in sql
    assert "trigger_row.tgattr = ''::pg_catalog.int2vector" in sql
    assert "trigger_row.tgfoid = function_oid" in sql
    assert "completion.authorization_sha256 !~ '^[0-9a-f]{64}$'" in sql
    assert "completion.difference_count IS DISTINCT FROM" in sql
    assert "sealing.authorization_sha256 =" in sql
    assert "completion.authorization_sha256" in sql
    assert "completion.completed_at < submission.submitted_at" in sql
    assert "FROM public.users AS app_user" in sql
    assert "JOIN public.people AS person" in sql
    assert "JOIN public.role_assignments AS assignment" in sql
    assert "JOIN public.roles AS role" in sql
    assert "NOT role.is_external" in sql
    assert "assignment.valid_from <=" in sql
    assert "completion.completed_at <" in sql
    assert "assignment.valid_to" in sql
    assert "assignment.revoked_at" in sql
    assert module.NONOPENING_AUTHORIZATION_SCHEMA in sql
    assert "pg_catalog.timezone('UTC', completion.completed_at)" in sql
    assert "app_user.account_status" not in sql
    assert "app_user.is_active" not in sql
    assert "app_user.authorization_version" not in sql
    assert "person.employment_status" not in sql
    assert "assignment.status" not in sql
    assert "role.status" not in sql
    assert "role_permissions" not in sql
    assert sql.count(enable_sql) == 1
    assert "pg_catalog.pg_get_functiondef(function_oid)" in sql
    assert "EXECUTE pg_catalog.replace" in sql
    assert f"ALTER FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert f"CREATE OR REPLACE FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert "GRANT " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql

    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0051'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("legacy upgrade preflight")
        < sql.index(module.EXISTING_ROWS_ERROR)
        < sql.index(module.REPLACEMENT_ERROR)
        < sql.index(enable_sql)
        < sql.index("fixed upgrade postflight")
        < sql.index(ready_sql)
    )


def test_0051_postgresql_offline_downgrade_restores_legacy_hash_and_origin(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0051_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION_ID}:"
        f"{PRE_STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()

    lock_sql = (
        "LOCK TABLE public.stocktake_difference_set_completions "
        "IN ACCESS EXCLUSIVE MODE"
    )
    enable_sql = (
        "ALTER TABLE public.stocktake_difference_set_completions ENABLE "
        "TRIGGER trg_stocktake_difference_set_completions_validate_0031"
    )
    assert "-- Running downgrade 20260903_0051 -> 20260903_0050" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(module.CATALOG_ERROR) == 14
    assert sql.count(module.REPLACEMENT_ERROR) == 3
    assert sql.count(module.EXISTING_ROWS_ERROR) == 1
    assert "fixed downgrade preflight" in sql
    assert "downgrade preflight" in sql
    assert "legacy downgrade postflight" in sql
    assert sql.count(module.FIXED_BODY_SHA256) == 2
    assert sql.count(module.LEGACY_BODY_SHA256) == 1
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgenabled = 'O'" in sql
    assert "FROM public.users AS app_user" in sql
    assert module.NONOPENING_AUTHORIZATION_SCHEMA in sql
    assert "pg_catalog.timezone('UTC', completion.completed_at)" in sql
    assert "app_user.account_status" not in sql
    assert "app_user.authorization_version" not in sql
    assert "role_permissions" not in sql
    assert sql.count(enable_sql) == 1
    assert "pg_catalog.pg_get_functiondef(function_oid)" in sql
    assert "EXECUTE pg_catalog.replace" in sql
    assert f"ALTER FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert f"CREATE OR REPLACE FUNCTION {module.FUNCTION_SIGNATURE}" not in sql
    assert "GRANT " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert "pg_catalog.min(version_num) = '20260903_0050'" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("fixed downgrade preflight")
        < sql.index(module.EXISTING_ROWS_ERROR)
        < sql.index(module.REPLACEMENT_ERROR)
        < sql.index(enable_sql)
        < sql.index("legacy downgrade postflight")
        < sql.index(
            "CREATE OR REPLACE FUNCTION public."
            "rsc_oam_runtime_binding_ready_0044()"
        )
    )


def test_0052_pins_exact_opening_terminal_bodies_catalog_and_ready_sql(
    monkeypatch,
) -> None:
    module = _load_0052_migration_module()
    assert module.revision == OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID
    assert module.down_revision == (
        PRE_OPENING_TERMINAL_GUARD_EXECUTION_HEAD_REVISION
    )
    assert module.PERSISTENT_FUNCTION_SIGNATURES == (
        "public.rsc_stocktake_actor_assignment_valid_0011(text, uuid, uuid, "
        "bigint, timestamptz, text, text, text)",
        "public.rsc_require_stocktake_difference_completion_0016()",
        "public.rsc_block_stocktake_review_fact_mutation_0016()",
        "public.rsc_validate_stocktake_scope_completion_insert_0021()",
        "public.rsc_opening_terminal_graph_complete_0022(uuid, uuid)",
        "public.rsc_require_opening_terminal_graph_0022()",
        "public.rsc_require_opening_observation_account_0023()",
    )
    assert module.HEAD_ONLY_FUNCTION_SIGNATURES == tuple(
        row[0] for row in module.HEAD_ONLY_FUNCTION_CATALOG
    )
    assert module.ALL_FUNCTION_SIGNATURES == (
        *module.PERSISTENT_FUNCTION_SIGNATURES,
        *module.HEAD_ONLY_FUNCTION_SIGNATURES,
    )
    assert module.CALLER_SIGNATURES == (
        module.COMMIT_SIGNATURE,
        module.ACCOUNT_SIGNATURE,
    )
    assert len(module.LOCK_TABLES) == 57
    assert len(set(module.LOCK_TABLES)) == len(module.LOCK_TABLES)
    assert {
        "role_assignments",
        "roles",
        "inventory_serials",
        "opening_control_reconciliation_command_consumptions",
        "opening_control_reconciliation_runs",
        "qr_codes",
        "reconciliation_commands",
        "reconciliation_items",
        "stocktake_observation_dispositions",
        "users",
    } <= set(module.LOCK_TABLES)
    assert set(row[0] for row in module.TRIGGER_CATALOG) <= set(
        module.LOCK_TABLES
    )
    assert len(module.TRIGGER_CATALOG) == 12
    assert len({row[1] for row in module.TRIGGER_CATALOG}) == 12
    assert tuple(row[3] for row in module.TRIGGER_CATALOG) == (
        *((5,) * 6),
        17,
        17,
        5,
        5,
        5,
        5,
    )
    assert {row[2] for row in module.TRIGGER_CATALOG} == {
        module.COMMIT_SIGNATURE,
        module.ACCOUNT_SIGNATURE,
    }
    assert sum(
        row[2] == module.COMMIT_SIGNATURE
        for row in module.TRIGGER_CATALOG
    ) == 11
    assert sum(
        row[2] == module.ACCOUNT_SIGNATURE
        for row in module.TRIGGER_CATALOG
    ) == 1

    def load_legacy(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        legacy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy_module)
        return legacy_module

    legacy_0022 = load_legacy(
        "opening_terminal_runtime_migration_0022_for_0052",
        OPENING_TERMINAL_RUNTIME_BOUNDARY_REVISION,
    )
    current_0023 = load_legacy(
        "opening_observation_posting_migration_0023_for_0052",
        OPENING_OBSERVATION_POSTING_REVISION,
    )
    legacy_0011 = load_legacy(
        "opening_count_observations_migration_0011_for_0052",
        OPENING_COUNT_OBSERVATION_REVISION,
    )
    legacy_0021 = load_legacy(
        "stocktake_round_assignment_guards_migration_0021_for_0052",
        STOCKTAKE_ROUND_ASSIGNMENT_GUARDS_REVISION,
    )

    def body(statement: str) -> str:
        body_match = re.search(
            r"AS \$\$(?P<body>.*?)\$\$",
            statement,
            flags=re.DOTALL,
        )
        assert body_match is not None
        return body_match.group("body")

    graph_sql = current_0023._postgresql_graph_function_sql(current=True)
    legacy_commit_sql = legacy_0022._postgresql_commit_function_sql()
    legacy_account_sql = current_0023._postgresql_account_function_sql()
    legacy_scope_completion_sql = (
        legacy_0021._postgresql_completion_function_sql(round_aware=True)
    )
    actor_statements: list[str] = []
    monkeypatch.setattr(legacy_0011.op, "execute", actor_statements.append)
    legacy_0011._create_postgresql_count_contract_triggers()
    actor_sql = actor_statements[0]
    actor_body = body(actor_sql)
    graph_body = body(graph_sql)
    legacy_commit_body = body(legacy_commit_sql)
    legacy_account_body = body(legacy_account_sql)
    legacy_scope_completion_body = body(legacy_scope_completion_sql)
    assert hashlib.sha256(actor_body.encode("utf-8")).hexdigest() == (
        module.ACTOR_ASSIGNMENT_BODY_SHA256
    )
    assert hashlib.sha256(graph_body.encode("utf-8")).hexdigest() == (
        module.GRAPH_BODY_SHA256
    )
    assert hashlib.sha256(legacy_commit_body.encode("utf-8")).hexdigest() == (
        module.LEGACY_COMMIT_BODY_SHA256
    )
    assert hashlib.sha256(legacy_account_body.encode("utf-8")).hexdigest() == (
        module.LEGACY_ACCOUNT_BODY_SHA256
    )
    assert hashlib.sha256(
        legacy_scope_completion_body.encode("utf-8")
    ).hexdigest() == module.LEGACY_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
    assert legacy_commit_body.count(module.LEGACY_TASK_BRANCH) == 1
    assert module.FIXED_TASK_BRANCH not in legacy_commit_body
    assert legacy_account_body.count(
        module.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT
    ) == 1
    assert module.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT not in legacy_account_body
    assert legacy_scope_completion_body.count(
        module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
    ) == 1
    assert (
        module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        not in legacy_scope_completion_body
    )

    fixed_commit_body = legacy_commit_body.replace(
        module.LEGACY_TASK_BRANCH,
        module.FIXED_TASK_BRANCH,
    )
    fixed_account_body = legacy_account_body.replace(
        module.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        module.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
    )
    fixed_scope_completion_body = legacy_scope_completion_body.replace(
        module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
        module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
    )
    assert hashlib.sha256(fixed_commit_body.encode("utf-8")).hexdigest() == (
        module.FIXED_COMMIT_BODY_SHA256
    )
    assert hashlib.sha256(fixed_account_body.encode("utf-8")).hexdigest() == (
        module.FIXED_ACCOUNT_BODY_SHA256
    )
    assert hashlib.sha256(
        fixed_scope_completion_body.encode("utf-8")
    ).hexdigest() == module.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
    assert fixed_commit_body.replace(
        module.FIXED_TASK_BRANCH,
        module.LEGACY_TASK_BRANCH,
    ) == legacy_commit_body
    assert fixed_account_body.replace(
        module.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
        module.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
    ) == legacy_account_body
    assert fixed_scope_completion_body.replace(
        module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
        module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
    ) == legacy_scope_completion_body

    normalized_fixed_branch = " ".join(module.FIXED_TASK_BRANCH.split())
    for required_guard in (
        "OLD.status = 'recount_required' AND NEW.status = 'counting'",
        "NEW.current_round_no <> OLD.current_round_no + 1",
        "OLD.submitted_at IS DISTINCT FROM NEW.submitted_at",
        "FROM public.stocktake_rounds AS current_round JOIN public."
        "stocktake_recount_cases AS recount_case",
        "recount_case.id = current_round.recount_case_id",
        "recount_case.task_id = NEW.id",
        "recount_case.next_round_no = current_round.round_no",
        "recount_case.opened_at = NEW.updated_at",
        "source_round.id = recount_case.source_round_id",
        "source_round.round_no = OLD.current_round_no",
        "submission.id = recount_case.source_round_submission_id",
        "current_round.round_no = NEW.current_round_no",
        "current_round.round_type = 'recount'",
        "current_round.status = 'counting'",
        "current_round.started_at = NEW.updated_at",
        "current_round.created_at = NEW.updated_at",
        "current_round.updated_at = NEW.updated_at",
        "transition_event.from_status = 'recount_required'",
        "transition_event.to_status = 'counting'",
        "transition_event.reason = 'opening_recount_opened'",
        "OLD.status = 'counting' AND NEW.status = 'submitted'",
        "OLD.submitted_at IS NOT DISTINCT FROM NEW.submitted_at",
        "NEW.current_round_no IS DISTINCT FROM OLD.current_round_no",
        "OLD.submitted_at IS NOT NULL AND NEW.submitted_at <= "
        "OLD.submitted_at",
        "FROM public.stocktake_rounds AS round_row JOIN public."
        "stocktake_round_submissions AS submission",
        "submission.task_id = NEW.id",
        "submission.round_id = round_row.id",
        "submission.submitted_at = NEW.submitted_at",
        "submission.count_manifest_sha256 = round_row."
        "count_manifest_sha256",
        "sealing.id = submission.sealing_completion_id",
        "sealing.completed_by_user_id = submission.submitted_by_user_id",
        "sealing.completed_by_person_id = submission.submitted_by_person_id",
        "sealing.completed_role_assignment_id = submission."
        "submitted_role_assignment_id",
        "round_row.task_id = NEW.id",
        "round_row.round_no = NEW.current_round_no",
        "round_row.status = 'submitted'",
        "round_row.submitted_at = NEW.submitted_at",
        "round_row.count_manifest_sha256 ~ '^[0-9a-f]{64}$'",
        "OLD.status = 'submitted' AND NEW.status IN ('hq_review', "
        "'recount_required')",
        "review.review_stage = 'region'",
        "NEW.status = 'hq_review' AND review.decision = 'approve'",
        "NEW.status = 'recount_required' AND review.decision IN "
        "('recount', 'reject')",
        "transition_event.from_status = 'submitted'",
        "transition_event.to_status = NEW.status",
        "'opening_region_review_' || review.decision",
        "item.review_id = review.id",
        "item.difference_id = difference.id",
        "OLD.status = 'hq_review' AND NEW.status IN ('approved', "
        "'recount_required')",
        "region_review.review_stage = 'region'",
        "region_review.decision = 'approve'",
        "review.review_stage = 'headquarters'",
        "review.decision = CASE NEW.status WHEN 'approved' THEN 'approve' "
        "ELSE 'reject' END",
        "review.reviewed_at > region_review.reviewed_at",
        "transition_event.from_status = 'hq_review'",
        "'opening_headquarters_review_' || review.decision",
        "OLD.status = NEW.status",
        "OLD.version IS DISTINCT FROM NEW.version",
        "OLD.updated_at IS DISTINCT FROM NEW.updated_at",
        "opening task status transition is invalid",
        "OLD.posted_at IS DISTINCT FROM NEW.posted_at",
        "OLD.closed_at IS DISTINCT FROM NEW.closed_at",
        "NEW.current_round_no IS DISTINCT FROM OLD.current_round_no OR NEW."
        "submitted_at IS DISTINCT FROM OLD.submitted_at OR OLD.posted_at IS "
        "NOT NULL",
        "OLD.status = NEW.status THEN IF OLD.current_round_no IS DISTINCT "
        "FROM NEW.current_round_no OR OLD.submitted_at IS DISTINCT FROM NEW."
        "submitted_at",
    ):
        assert required_guard in normalized_fixed_branch
    assert module.FIXED_TASK_BRANCH.count(
        f"public.{module.ACTOR_ASSIGNMENT_FUNCTION}("
    ) == 0
    for actor_guard in (
        "review.reviewer_user_id <> region_review.reviewer_user_id",
        "review.reviewer_person_id <> region_review.reviewer_person_id",
        "review.reviewer_role_assignment_id <> region_review."
        "reviewer_role_assignment_id",
    ):
        assert actor_guard in normalized_fixed_branch
    for item_guard in (
        "decision_difference.difference_type = 'control_unassigned' THEN "
        "'pending_verification'",
        "decision_difference.observed_line_id IS NULL",
        "round_row.round_no > 1 AND round_row.round_type = 'recount'",
        "decision_observation.verification_status = 'verified'",
        "decision_disposition.disposition = 'pending_verification'",
        "decision_disposition.disposition IN ( 'requires_recount', "
        "'resolved_existing_master' )",
        "decision_item.decision = 'pending_verification' AND pg_catalog."
        "btrim(decision_item.comment) = ''",
    ):
        assert item_guard in normalized_fixed_branch
    assert module.FIXED_TASK_BRANCH.count(
        "JOIN public.stocktake_observation_dispositions"
    ) == 5
    for event_guard in (
        "submission_event.reason = CASE WHEN round_row.round_no = 1",
        "submission_event.metadata_jsonb = CASE",
        "transition_event.metadata_jsonb = pg_catalog.jsonb_build_object",
        "region_transition_event.metadata_jsonb = pg_catalog."
        "jsonb_build_object",
        "'assignment_manifest_sha256', recount_case."
        "assignment_manifest_sha256",
        "'next_round_id', current_round.id::text",
        "'recount_case_id', recount_case.id::text",
        "'source_round_id', recount_case.source_round_id::text",
    ):
        assert event_guard in normalized_fixed_branch
    assert (
        "OR OLD.current_round_no IS DISTINCT FROM NEW.current_round_no"
        not in module.FIXED_TASK_BRANCH
    )
    fixed_binding_prefix = module.FIXED_TASK_BRANCH.split(
        "        IF OLD.status = 'recount_required' AND NEW.status = 'counting'",
        maxsplit=1,
    )[0]
    assert "OLD.submitted_at IS DISTINCT FROM NEW.submitted_at" not in (
        fixed_binding_prefix
    )
    assert "OLD.cancelled_at IS DISTINCT FROM NEW.cancelled_at" in (
        module.FIXED_TASK_BRANCH
    )

    parser = pytest.importorskip("pglast.parser")
    fixed_commit_sql = legacy_commit_sql.replace(
        module.LEGACY_TASK_BRANCH,
        module.FIXED_TASK_BRANCH,
    )
    fixed_account_sql = legacy_account_sql.replace(
        module.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        module.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
    )
    fixed_scope_completion_sql = legacy_scope_completion_sql.replace(
        module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
        module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021,
    )
    for statement in (
        actor_sql,
        graph_sql,
        fixed_commit_sql,
        fixed_account_sql,
        fixed_scope_completion_sql,
    ):
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_catalog(hardened=False, phase="legacy parse probe")
    module._verify_catalog(hardened=True, phase="hardened parse probe")
    module._verify_existing_opening_rows()
    module._require_no_active_opening_task()
    module._replace_function_body(
        signature=module.COMMIT_SIGNATURE,
        expected_body_sha256=module.LEGACY_COMMIT_BODY_SHA256,
        source_fragment=module.LEGACY_TASK_BRANCH,
        replacement_fragment=module.FIXED_TASK_BRANCH,
        phase="commit parse probe",
    )
    module._replace_function_body(
        signature=module.ACCOUNT_SIGNATURE,
        expected_body_sha256=module.LEGACY_ACCOUNT_BODY_SHA256,
        source_fragment=module.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT,
        replacement_fragment=module.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT,
        phase="account parse probe",
    )
    module._replace_function_body(
        signature=module.SCOPE_COMPLETION_GUARD_SIGNATURE_0021,
        expected_body_sha256=(
            module.LEGACY_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
        ),
        source_fragment=(
            module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        ),
        replacement_fragment=(
            module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        ),
        phase="scope completion parse probe",
    )
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    existing_rows_sql = next(
        statement
        for statement in statements
        if module.EXISTING_ROWS_ERROR in statement
    )
    normalized_existing_rows_sql = " ".join(existing_rows_sql.split())
    for required_existing_guard in (
        "FROM public.stocktake_recount_cases AS task_recount_case WHERE "
        "task_recount_case.task_id = task.id ) IS DISTINCT FROM GREATEST( "
        "task.current_round_no - 1, 0 )",
        "task.status = 'counting' AND task.current_round_no = 1 AND "
        "task.submitted_at IS NULL",
        "task.status = 'counting' AND task.current_round_no > 1 AND "
        "task.submitted_at IS NOT NULL",
        "JOIN public.stocktake_recount_cases AS recount_case",
        "recount_case.id = current_round.recount_case_id",
        "recount_case.task_id = task.id",
        "recount_case.next_round_no = current_round.round_no",
        "submission.id = recount_case.source_round_submission_id",
        "source_round.round_no = current_round.round_no - 1",
        "source_round.status = 'submitted'",
        "source_round.submitted_at = task.submitted_at",
        "JOIN public.stocktake_scope_count_completions AS sealing",
        "sealing.id = submission.sealing_completion_id",
        "sealing.completed_by_user_id = submission.submitted_by_user_id",
        "sealing.completed_by_person_id = submission.submitted_by_person_id",
        "sealing.completed_role_assignment_id = submission."
        "submitted_role_assignment_id",
        "sealing.authorization_version = submission.authorization_version",
        "sealing.completed_at = submission.submitted_at",
        "task.status IN ( 'submitted', 'hq_review', 'approved', "
        "'recount_required', 'posted', 'closed' )",
        "current_round.status = 'submitted'",
        "current_round.submitted_at = task.submitted_at",
        "submission.count_manifest_sha256 = current_round."
        "count_manifest_sha256",
        "task.status = 'submitted' AND task.updated_at = task.submitted_at "
        "AND NOT EXISTS ( SELECT 1 FROM public.stocktake_reviews AS "
        "premature_review JOIN public.stocktake_rounds AS submitted_round",
        "submitted_round.round_no = task.current_round_no",
        "task.status IN ('approved', 'posted', 'closed')",
        "task.status = 'approved' AND hq_review.reviewed_at = task.updated_at",
        "task.status IN ('posted', 'closed') AND task.posted_at IS NOT NULL "
        "AND hq_review.reviewed_at <= task.posted_at",
        f"public.{module.GRAPH_FUNCTION}( task.id, posting."
        "inventory_transaction_id )",
        "historical_user.authorization_version >= region_review."
        "authorization_version",
        "historical_role.code = 'provincial_manager'",
        "historical_assignment.scope_type = 'organization'",
        "historical_assignment.scope_id = task.region_org_id::text",
        "historical_user.authorization_version >= hq_review."
        "authorization_version",
        "historical_role.code = 'admin'",
        "historical_assignment.scope_type = 'national'",
        "historical_assignment.scope_id = '*'",
        "hq_review.reviewer_user_id <> region_review.reviewer_user_id",
        "hq_review.reviewer_person_id <> region_review.reviewer_person_id",
        "hq_review.reviewer_role_assignment_id <> region_review."
        "reviewer_role_assignment_id",
        "FROM public.stocktake_recount_cases AS recount_case JOIN public."
        "stocktake_tasks AS recount_task",
        "trigger_review.id = recount_case.trigger_review_id",
        "trigger_review.review_stage = 'region' AND trigger_review.decision "
        "IN ( 'recount', 'reject' )",
        "trigger_review.review_stage = 'headquarters' AND trigger_review."
        "decision = 'reject'",
        "source_region_review.review_stage = 'region'",
        "source_region_review.decision = 'approve'",
        "source_region_review.reviewer_user_id <> trigger_review."
        "reviewer_user_id",
        "source_region_review.reviewer_person_id <> trigger_review."
        "reviewer_person_id",
        "source_region_review.reviewer_role_assignment_id <> trigger_review."
        "reviewer_role_assignment_id",
        "trigger_review_event.metadata_jsonb = pg_catalog."
        "jsonb_build_object",
        "source_region_review_event.metadata_jsonb = pg_catalog."
        "jsonb_build_object",
        "next_round.recount_case_id = recount_case.id",
        "recount_open_event.reason = 'opening_recount_opened'",
        "recount_open_event.metadata_jsonb = pg_catalog.jsonb_build_object",
    ):
        assert required_existing_guard in normalized_existing_rows_sql
    assert normalized_existing_rows_sql.count(
        "decision_difference.difference_type = 'control_unassigned'"
    ) >= 7
    assert normalized_existing_rows_sql.count(
        "JOIN public.stocktake_observation_dispositions"
    ) >= 7
    assert normalized_existing_rows_sql.count(
        "premature_review.round_id = current_round.id"
    ) == 2
    assert "historical_role.status" not in existing_rows_sql
    assert "pg_catalog.greatest(" not in existing_rows_sql.lower()
    assert "pg_catalog.least(" not in existing_rows_sql.lower()
    assert f"public.{module.ACTOR_ASSIGNMENT_FUNCTION}(" not in (
        existing_rows_sql
    )
    assert "'cancelled'" not in existing_rows_sql
    assert "'region_review'" not in existing_rows_sql

    legacy_values = module._function_catalog_values(hardened=False)
    hardened_values = module._function_catalog_values(hardened=True)
    assert legacy_values.count("FALSE") == 6
    assert hardened_values.count("FALSE") == 4
    assert legacy_values.count("TRUE") == 1
    assert hardened_values.count("TRUE") == 3
    assert "NULL::text" in legacy_values
    assert (
        f"'{module.ACTOR_ASSIGNMENT_SIGNATURE}', "
        f"'{module.ACTOR_ASSIGNMENT_FUNCTION}', 'boolean', 'sql', 's', 8"
    ) in legacy_values
    assert (
        "'p_user_id,p_person_id,p_assignment_id,p_authorization_version,"
        "p_occurred_at,p_role_code,p_scope_type,p_scope_id'::text, FALSE, "
        "NULL::text"
    ) in legacy_values
    for expected_hash in (
        module.ACTOR_ASSIGNMENT_BODY_SHA256,
        module.GRAPH_BODY_SHA256,
        module.LEGACY_COMMIT_BODY_SHA256,
        module.FIXED_COMMIT_BODY_SHA256,
        module.LEGACY_ACCOUNT_BODY_SHA256,
        module.FIXED_ACCOUNT_BODY_SHA256,
        module.LEGACY_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021,
        module.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021,
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", expected_hash)

    for expected_revision in (module.PREVIOUS_SCHEMA_REVISION, module.revision):
        ready_sql = module._oam_runtime_ready_function_sql(expected_revision)
        assert not sa.text(ready_sql)._bindparams
        parser.parse_sql(ready_sql)
        parser.parse_plpgsql_json(ready_sql)
    ready_body = body(module._oam_runtime_ready_function_sql(module.revision))
    assert hashlib.sha256(ready_body.encode("utf-8")).hexdigest() == (
        "7b87874563d011de3c6c02e598892700d4400dd0cccfee38c7f573068c319f12"
    )
    previous_ready_body = body(
        module._oam_runtime_ready_function_sql(module.PREVIOUS_SCHEMA_REVISION)
    )
    assert hashlib.sha256(previous_ready_body.encode("utf-8")).hexdigest() == (
        "8764e8b910dff314b5a3e0478043c1145e228cbbd9ec70a1715426cd8ad714c3"
    )

    with pytest.raises(ValueError, match="unsupported opening terminal"):
        module._replace_function_body(
            signature=module.GRAPH_SIGNATURE,
            expected_body_sha256=module.GRAPH_BODY_SHA256,
            source_fragment=module.LEGACY_TASK_BRANCH,
            replacement_fragment=module.FIXED_TASK_BRANCH,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported OAM runtime"):
        module._oam_runtime_ready_function_sql("20260903_9999")


def test_0052_exports_exact_head_and_inherited_dependency_catalogs() -> None:
    module = _load_0052_migration_module()

    trigger_catalogs = (
        module.TRIGGER_CATALOG,
        module.REVIEW_GUARD_TRIGGER_CATALOG,
        module.INHERITED_RECONCILIATION_TRIGGER_CATALOG,
        module.OPENING_0052_TRIGGER_CATALOG,
    )
    assert all(
        len(row[1].encode("utf-8")) <= 63
        for catalog in trigger_catalogs
        for row in catalog
    )
    assert module.REVIEW_GUARD_TRIGGER_CATALOG[2][1] == (
        "trg_stocktake_difference_set_completions_immutable_truncate_001"
    )

    assert module.HEAD_ONLY_FUNCTION_CATALOG_FIELDS == (
        "signature",
        "name",
        "return_type",
        "language",
        "volatility",
        "argument_types",
        "argument_names",
        "security_definer",
        "search_path",
        "body_sha256",
    )
    assert len(module.HEAD_ONLY_FUNCTION_CATALOG) == 10
    assert len({row[0] for row in module.HEAD_ONLY_FUNCTION_CATALOG}) == 10
    assert tuple(row[0] for row in module.HEAD_ONLY_FUNCTION_CATALOG) == (
        *module.NEW_HELPER_SIGNATURES,
        module.INSERT_GUARD_SIGNATURE,
        module.COUNT_WRITE_SIGNATURE,
        module.GRAPH_CLOSURE_SIGNATURE,
    )
    body_by_signature = {
        module.START_GRAPH_SIGNATURE: module.START_GRAPH_BODY,
        module.ROUND_SUBMISSION_SIGNATURE: module.ROUND_SUBMISSION_BODY,
        module.SCOPE_COMPLETION_SIGNATURE: module.SCOPE_COMPLETION_BODY,
        module.REVIEW_GRAPH_SIGNATURE: module.REVIEW_GRAPH_BODY,
        module.RECOUNT_GRAPH_SIGNATURE: module.RECOUNT_GRAPH_BODY,
        module.DISPOSITION_GRAPH_SIGNATURE: module.DISPOSITION_GRAPH_BODY,
        module.TERMINAL_GRAPH_SIGNATURE: module.TERMINAL_GRAPH_BODY,
        module.INSERT_GUARD_SIGNATURE: module.INSERT_GUARD_BODY,
        module.COUNT_WRITE_SIGNATURE: module.COUNT_WRITE_BODY,
        module.GRAPH_CLOSURE_SIGNATURE: module.GRAPH_CLOSURE_BODY,
    }
    for row in module.HEAD_ONLY_FUNCTION_CATALOG:
        (
            signature,
            _function_name,
            return_type,
            language,
            volatility,
            argument_types,
            argument_names,
            security_definer,
            search_path,
            body_sha256,
        ) = row
        assert volatility == "v"
        assert search_path == (module.FIXED_SEARCH_PATH,)
        assert re.fullmatch(r"[0-9a-f]{64}", body_sha256)
        assert hashlib.sha256(
            body_by_signature[signature].encode("utf-8")
        ).hexdigest() == body_sha256
        if signature in module.NEW_HELPER_SIGNATURES:
            assert return_type == "boolean"
            assert language == "sql"
            assert argument_types
            assert len(argument_names) == len(argument_types)
            assert not security_definer
        else:
            assert return_type == "trigger"
            assert language == "plpgsql"
            assert argument_types == ()
            assert argument_names == ()
            assert security_definer

    assert module.INHERITED_RECONCILIATION_FUNCTION_CATALOG_FIELDS == (
        "signature",
        "name",
        "return_type",
        "language",
        "volatility",
        "argument_types",
        "argument_names",
        "security_definer",
        "is_strict",
        "search_path",
        "body_sha256",
        "api_execute",
    )
    assert module.INHERITED_RECONCILIATION_FUNCTION_CATALOG == (
        (
            "public.rsc_canonical_reconciliation_json_0026(jsonb)",
            "rsc_canonical_reconciliation_json_0026",
            "text",
            "plpgsql",
            "i",
            ("jsonb",),
            ("document",),
            False,
            True,
            (module.FIXED_SEARCH_PATH,),
            "35a956052a13a94d1c6b57f252273f46fa806b2e9c596531205a148114b7dc53",
            True,
        ),
        (
            "public.rsc_reconciliation_event_key_0026(text, text, text)",
            "rsc_reconciliation_event_key_0026",
            "text",
            "sql",
            "i",
            ("text", "text", "text"),
            ("operation_name", "anchor", "suffix"),
            False,
            True,
            (module.FIXED_SEARCH_PATH,),
            "9ec2f326f040fa1cd223e570b83ae6d6ff3dad7eb81f56e55e3342b45d9e5446",
            True,
        ),
        (
            "public.rsc_guard_reconciliation_effect_0026()",
            "rsc_guard_reconciliation_effect_0026",
            "trigger",
            "plpgsql",
            "v",
            (),
            (),
            False,
            False,
            (module.FIXED_SEARCH_PATH,),
            "6e7e845ac518f378139b6f79218da0f08a55f9398b63429686af47f8f10024fe",
            False,
        ),
    )
    assert module.INHERITED_RECONCILIATION_TRIGGER_CATALOG == (
        (
            "state_transition_events",
            "trg_reconciliation_state_effect_guard_0026",
            module.RECONCILIATION_EFFECT_SIGNATURE,
            31,
        ),
        (
            "outbox_events",
            "trg_reconciliation_outbox_effect_guard_0026",
            module.RECONCILIATION_EFFECT_SIGNATURE,
            31,
        ),
        (
            "audit_events",
            "trg_reconciliation_audit_effect_guard_0026",
            module.RECONCILIATION_EFFECT_SIGNATURE,
            31,
        ),
        (
            "state_transition_events",
            "trg_reconciliation_state_effect_no_truncate_0026",
            module.RECONCILIATION_EFFECT_SIGNATURE,
            34,
        ),
        (
            "outbox_events",
            "trg_reconciliation_outbox_effect_no_truncate_0026",
            module.RECONCILIATION_EFFECT_SIGNATURE,
            34,
        ),
        (
            "audit_events",
            "trg_reconciliation_audit_effect_no_truncate_0026",
            module.RECONCILIATION_EFFECT_SIGNATURE,
            34,
        ),
    )

    assert module.COUNT_WRITE_TRIGGER_CATALOG == (
        ("stocktake_count_lines", "trg_stocktake_count_lines_current_0052"),
        ("stocktake_count_serials", "trg_stocktake_count_serials_current_0052"),
        (
            "stocktake_count_observations",
            "trg_stocktake_count_observations_current_0052",
        ),
        (
            "stocktake_scope_count_completions",
            "trg_stocktake_scope_count_completions_current_0052",
        ),
    )
    assert module.GRAPH_CLOSURE_TRIGGER_CATALOG == (
        ("stocktake_count_lines", "trg_stocktake_count_lines_graph_0052", "INSERT"),
        ("stocktake_count_serials", "trg_stocktake_count_serials_graph_0052", "INSERT"),
        (
            "stocktake_count_observations",
            "trg_stocktake_count_observations_graph_0052",
            "INSERT",
        ),
        (
            "stocktake_scope_count_completions",
            "trg_stocktake_scope_count_completions_graph_0052",
            "INSERT",
        ),
        (
            "stocktake_round_submissions",
            "trg_stocktake_round_submissions_graph_0052",
            "INSERT",
        ),
        ("stocktake_rounds", "trg_stocktake_rounds_graph_0052", "INSERT OR UPDATE"),
        ("stocktake_reviews", "trg_stocktake_reviews_graph_0052", "INSERT"),
        ("stocktake_review_items", "trg_stocktake_review_items_graph_0052", "INSERT"),
        ("stocktake_differences", "trg_stocktake_differences_graph_0052", "INSERT"),
        (
            "stocktake_difference_set_completions",
            "trg_stocktake_difference_set_completions_graph_0052",
            "INSERT",
        ),
        (
            "stocktake_observation_dispositions",
            "trg_stocktake_observation_dispositions_graph_0052",
            "INSERT",
        ),
        (
            "stocktake_postings",
            "trg_stocktake_postings_graph_0052",
            "INSERT",
        ),
        (
            "state_transition_events",
            "trg_state_transition_events_opening_graph_0052",
            "INSERT",
        ),
        ("outbox_events", "trg_outbox_events_opening_graph_0052", "INSERT"),
        ("audit_events", "trg_audit_events_opening_graph_0052", "INSERT"),
    )
    assert module.OPENING_0052_TRIGGER_CATALOG_FIELDS == (
        "table",
        "name",
        "function_signature",
        "tgtype",
        "constraint",
        "deferrable",
        "initially_deferred",
        "enabled",
    )
    assert len(module.OPENING_0052_TRIGGER_CATALOG) == 20
    assert len({row[1] for row in module.OPENING_0052_TRIGGER_CATALOG}) == 20
    assert sum(
        row[2] == module.INSERT_GUARD_SIGNATURE
        for row in module.OPENING_0052_TRIGGER_CATALOG
    ) == 1
    assert sum(
        row[2] == module.COUNT_WRITE_SIGNATURE
        for row in module.OPENING_0052_TRIGGER_CATALOG
    ) == 4
    assert sum(
        row[2] == module.GRAPH_CLOSURE_SIGNATURE
        for row in module.OPENING_0052_TRIGGER_CATALOG
    ) == 15
    assert sum(row[3] == 21 for row in module.OPENING_0052_TRIGGER_CATALOG) == 1
    assert sum(row[7] == "A" for row in module.OPENING_0052_TRIGGER_CATALOG) == 16


def test_0052_national_authorization_omits_unreachable_uuid_coverage() -> None:
    module = _load_0052_migration_module()
    national_authorization = module._current_authorization_sql(
        user_id_sql="review.reviewer_user_id",
        person_id_sql="review.reviewer_person_id",
        assignment_id_sql="review.reviewer_role_assignment_id",
        authorization_version_sql="review.authorization_version",
        occurred_at_sql="review.reviewed_at",
        role_code_sql="'admin'",
        scope_type_sql="'national'",
        scope_id_sql="'*'",
        permission_resource="stocktake",
        permission_action="review_headquarters",
        allow_scheduled=True,
        alias_suffix="national_compile_guard",
    )

    assert "('*')::uuid" not in national_authorization
    assert "organization_lineage_deny_national_compile_guard" not in (
        national_authorization
    )
    assert "AND (FALSE)" in national_authorization
    national_entitlement = module._current_entitlement_target_sql(
        user_id_sql="review.reviewer_user_id",
        assignment_id_sql="review.reviewer_role_assignment_id",
        permission_resource="stocktake",
        permission_action="review_headquarters",
        target_scope_type_sql="'national'",
        target_scope_id_sql="'*'",
        alias_suffix="national_entitlement_compile_guard",
    )
    assert "('*')::uuid" not in national_entitlement
    assert "organization_lineage_target_" not in national_entitlement
    assert national_entitlement.count("AND (FALSE)") == 2
    for body in (
        module.REVIEW_GRAPH_BODY,
        module.TERMINAL_GRAPH_BODY,
        module.FIXED_TASK_BRANCH,
    ):
        assert "('*')::uuid" not in body

    with pytest.raises(ValueError, match="national authorization"):
        module._current_authorization_sql(
            user_id_sql="review.reviewer_user_id",
            person_id_sql="review.reviewer_person_id",
            assignment_id_sql="review.reviewer_role_assignment_id",
            authorization_version_sql="review.authorization_version",
            occurred_at_sql="review.reviewed_at",
            role_code_sql="'admin'",
            scope_type_sql="'national'",
            scope_id_sql="'not-wildcard'",
            permission_resource="stocktake",
            permission_action="review_headquarters",
            allow_scheduled=True,
            alias_suffix="invalid_national_compile_guard",
        )
    with pytest.raises(ValueError, match="wildcard scope"):
        module._current_entitlement_target_sql(
            user_id_sql="review.reviewer_user_id",
            assignment_id_sql="review.reviewer_role_assignment_id",
            permission_resource="stocktake",
            permission_action="review_region",
            target_scope_type_sql="'organization'",
            target_scope_id_sql="'*'",
            alias_suffix="invalid_wildcard_compile_guard",
        )


def test_0052_parses_named_helpers_and_closes_event_ownership(
    monkeypatch,
) -> None:
    module = _load_0052_migration_module()
    parser = pytest.importorskip("pglast.parser")

    assert "snapshot_account.created_at IS NULL" in module.START_GRAPH_BODY
    assert (
        "snapshot_account.created_at > start_task.cutoff_at"
        in module.START_GRAPH_BODY
    )
    assert (
        "scoped_account.created_at <= start_task.cutoff_at"
        in module.START_GRAPH_BODY
    )
    normalized_start_graph = " ".join(module.START_GRAPH_BODY.split())
    assert module._jsonb_null_sql("audit_row.before_jsonb") == (
        "(audit_row.before_jsonb IS NULL OR "
        "audit_row.before_jsonb = 'null'::jsonb)"
    )
    audit_null_proofs = (
        (module.START_GRAPH_BODY, "start_audit"),
        (module.ROUND_SUBMISSION_BODY, "side_audit"),
        (module.ROUND_SUBMISSION_BODY, "round_audit"),
        (module.SCOPE_COMPLETION_BODY, "scope_audit"),
        (module.REVIEW_GRAPH_BODY, "review_audit"),
        (module.RECOUNT_GRAPH_BODY, "recount_audit"),
        (module.DISPOSITION_GRAPH_BODY, "disposition_audit"),
        (module.TERMINAL_GRAPH_BODY, "inventory_audit"),
    )
    for helper_body, audit_alias in audit_null_proofs:
        normalized_helper = " ".join(helper_body.split())
        assert (
            f"({audit_alias}.before_jsonb IS NULL OR "
            f"{audit_alias}.before_jsonb = 'null'::jsonb)"
            in normalized_helper
        )
        assert (
            f"AND {audit_alias}.before_jsonb IS NULL AND"
            not in normalized_helper
        )
    assert (
        "pg_catalog.lower(pg_catalog.btrim(control_source.code)) = 'oam'"
        in normalized_start_graph
    )
    assert "control_source.code = 'oam'" not in normalized_start_graph
    assert (
        "LEFT JOIN public.stock_locations AS snapshot_location ON "
        "snapshot_location.id = snapshot_scope.location_id"
        in normalized_start_graph
    )
    assert "OR snapshot_location.id IS NULL" in normalized_start_graph
    assert (
        "snapshot_location.location_type = 'personal' AND "
        "snapshot_account.custodian_person_id IS DISTINCT FROM "
        "snapshot_scope.custodian_person_id_snapshot"
        in normalized_start_graph
    )
    assert (
        "snapshot_scope.custodian_person_id_snapshot IS NOT NULL AND "
        "snapshot_account.custodian_person_id IS DISTINCT FROM "
        "snapshot_scope.custodian_person_id_snapshot"
        not in normalized_start_graph
    )
    normalized_scope_graph = " ".join(module.SCOPE_COMPLETION_BODY.split())
    assert (
        "current_location.location_type = 'region' AND "
        "current_location.custodian_person_id IS NOT NULL AND "
        "current_location.custodian_person_id IS DISTINCT FROM "
        "current_scope.custodian_person_id_snapshot"
        in normalized_scope_graph
    )
    assert (
        "current_location.location_type = 'region' AND "
        "current_location.custodian_person_id IS NOT NULL AND "
        "current_location.custodian_person_id <> "
        "current_scope.custodian_person_id_snapshot"
        not in normalized_scope_graph
    )
    assert module._fixed_scale_quantity_sql(
        "terminal_posting.total_quantity"
    ) == (
        "pg_catalog.to_char((terminal_posting.total_quantity)::numeric(18, 3), "
        "'FM999999999999990.000')"
    )
    normalized_terminal_graph = " ".join(module.TERMINAL_GRAPH_BODY.split())
    assert module._fixed_scale_quantity_sql(
        "terminal_posting.total_quantity"
    ) in module.TERMINAL_GRAPH_BODY
    assert (
        "pg_catalog.trim_scale((terminal_posting.total_quantity)::numeric)::text"
        not in normalized_terminal_graph
    )
    normalized_review_graph = " ".join(module.REVIEW_GRAPH_BODY.split())
    assert normalized_review_graph.count(
        "historical_assignment.status IN ( 'scheduled', 'active', "
        "'expired', 'revoked' )"
    ) == 2
    assert (
        "review_row.decision <> 'approve' OR "
        "sibling_review.review_stage <> 'headquarters' OR "
        "sibling_review.reviewed_at <= review_row.reviewed_at"
        in normalized_review_graph
    )
    assert "scope_completion.request_jsonb" in module.SCOPE_COMPLETION_BODY
    assert (
        "scope_completion.request_resolution_jsonb"
        in module.SCOPE_COMPLETION_BODY
    )
    assert module.CANONICAL_JSON_FUNCTION in module._scope_count_request_sha256_sql(
        "scope_completion"
    )
    request_proof = module._scope_count_request_document_proof_sql(
        "scope_task.id",
        "scope_round",
        "scope_row",
        "scope_completion",
    )
    for request_key in (
        "actor_person_id",
        "actor_user_id",
        "physical_observations",
        "round_id",
        "schema",
        "scope_id",
        "task_id",
        "zero_confirmed",
    ):
        assert f"'{request_key}'" in request_proof
    assert "WITH ORDINALITY" in request_proof
    assert "pg_catalog.convert_to(" in request_proof
    assert "ordered_request.prior_document" in request_proof
    assert "ordered_request.document" in request_proof
    assert module.PYTHON_STRIP_CHARACTERS_SQL in request_proof
    assert "request_item.value ->> 'counted_qty'" in request_proof
    assert "pg_catalog.jsonb_array_length(" in request_proof
    assert ") <= 10000" in request_proof
    current_resolution_proof = (
        module._scope_count_request_resolution_proof_sql(
            "scope_task.id",
            "scope_round",
            "scope_row",
            "scope_completion",
            historical=False,
        )
    )
    historical_resolution_proof = (
        module._scope_count_request_resolution_proof_sql(
            "scope_task.id",
            "scope_round",
            "scope_row",
            "scope_completion",
            historical=True,
        )
    )
    for resolution_key in (
        "items",
        "request_sha256",
        "round_id",
        "schema",
        "scope_id",
        "task_id",
    ):
        assert f"'{resolution_key}'" in current_resolution_proof
    for item_key in (
        "material_qr_mapping_id",
        "policy",
        "request_item_sha256",
        "request_ordinal",
        "resolved_lot_id",
        "resolved_material_id",
        "resolved_serial_id",
        "serial_alias_keys",
        "serial_qr_mapping_id",
        "target_id",
        "target_type",
    ):
        assert f"'{item_key}'" in current_resolution_proof
    for policy_key in (
        "allow_fraction",
        "effective_from",
        "id",
        "quantity_scale",
        "tracking_mode",
    ):
        assert f"'{policy_key}'" in current_resolution_proof
    assert (
        "cloud_oam.opening_stocktake.scope_count_request_resolution.v1"
        in current_resolution_proof
    )
    assert "request_item_sha256' <>" in current_resolution_proof
    assert "resolution_item.value -> 'request_ordinal'" in current_resolution_proof
    assert "'^[1-9][0-9]*$'" in current_resolution_proof
    assert "resolution_item.ordinal::text" in current_resolution_proof
    assert "policy' -> 'quantity_scale'" in current_resolution_proof
    assert "policy' ->> 'quantity_scale') ~ '^[0-3]$'" in current_resolution_proof
    assert module._serial_alias_key_sql("candidate.value") == (
        "(pg_catalog.translate(candidate.value, "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') COLLATE \"C\")"
    )
    for resolution_proof in (
        current_resolution_proof,
        historical_resolution_proof,
    ):
        assert "resolution_item.value -> 'serial_alias_keys'" in resolution_proof
        assert "pg_catalog.jsonb_typeof(serial_alias.value)" in resolution_proof
        assert "pg_catalog.char_length(((serial_alias.value #>> '{}')" in (
            resolution_proof
        )
        assert 'COLLATE "C"' in resolution_proof
        assert "ordered_serial_alias.prior_alias_key" in resolution_proof
        assert "pg_catalog.jsonb_array_length(" in resolution_proof
        assert ") <= 3" in resolution_proof
        assert "alias_completion.request_resolution_jsonb -> 'items'" in (
            resolution_proof
        )
        assert "DISTINCT round_serial_alias.occurrence_id" in resolution_proof
    assert "resolution_alias_master.serial_no" in current_resolution_proof
    assert "resolution_alias_candidate.id" in current_resolution_proof
    assert "AS current_serial_reference_identity" in current_resolution_proof
    assert "current_serial_candidate.id AS serial_id" in current_resolution_proof
    assert (
        "current_serial_mapping.object_id AS serial_id"
        in current_resolution_proof
    )
    assert "AS current_serial_reference_identity" not in historical_resolution_proof
    assert "resolution_alias_master.serial_no" not in historical_resolution_proof
    assert "resolution_alias_candidate.id" not in historical_resolution_proof
    assert "covered_line.counted_qty <>" in current_resolution_proof
    assert "), 0)::numeric(18, 3)" not in current_resolution_proof
    for resolution_proof in (
        current_resolution_proof,
        historical_resolution_proof,
    ):
        assert "AS round_count_serial" in resolution_proof
        assert "AS round_serial_observation" in resolution_proof
        assert (
            "round_serial_observation.serial_id =\n"
            "                   round_count_serial.serial_id"
        ) in resolution_proof
    assert "scope_full_set_zero" in current_resolution_proof
    assert "covered_serial.result = 'unexpected'" in current_resolution_proof
    assert "current_sku_material.status = 'active'" in current_resolution_proof
    assert "current_resolved_serial.lifecycle_status =" in current_resolution_proof
    assert "current_sku_material.status = 'active'" not in historical_resolution_proof
    assert "current_resolved_serial.lifecycle_status =" not in historical_resolution_proof
    assert "historical_material_mapping.object_id::text" in historical_resolution_proof
    assert "historical_material_mapping.code" not in historical_resolution_proof
    legacy_resolution = module._legacy_scope_count_request_resolution_document_sql(
        "completion.task_id",
        "round_row",
        "scope_row",
        "completion",
        "candidate_request",
    )
    for lossless_backfill_guard in (
        "legacy_resolved_material.sku_code =",
        "legacy_resolved_material.created_at <=",
        "legacy_resolved_material.updated_at <=",
        "legacy_material_mapping.mapping_id IS NOT NULL",
        "legacy_resolved_lot.lot_no =",
        "legacy_resolved_lot.created_at <=",
        "legacy_resolved_lot.updated_at <=",
        "legacy_serial_mapping.mapping_id IS NOT NULL",
        "legacy_policy.document IS NOT NULL",
        "legacy_mutable_policy.updated_at >",
    ):
        assert lossless_backfill_guard in legacy_resolution
    assert "'serial_alias_keys'" in legacy_resolution
    assert "legacy_alias_candidate.updated_at >" in legacy_resolution
    assert "AS legacy_serial_reference_identity" in legacy_resolution
    assert "legacy_identity_candidate.id AS serial_id" in legacy_resolution
    assert "legacy_identity_mapping.object_id AS serial_id" in legacy_resolution
    assert "legacy_mutable_identity_mapping.updated_at >" in legacy_resolution
    assert module.COUNT_WRITE_BODY.count(
        "pg_catalog.pg_advisory_xact_lock("
    ) == 2
    assert "rsc-opening-round-serial-0052" in module.COUNT_WRITE_BODY
    assert module.OPENING_ROUND_SERIAL_DUPLICATE_ERROR in module.COUNT_WRITE_BODY
    assert module.COUNT_WRITE_BODY.count(
        "pg_catalog.current_setting('transaction_isolation') <>"
    ) == 4
    assert module.COUNT_WRITE_BODY.count(
        module.OPENING_COUNT_ISOLATION_ERROR
    ) == 4
    assert "AS existing_round_serial_alias" in module.COUNT_WRITE_BODY
    assert "new_count_serial_alias.value" in module.COUNT_WRITE_BODY
    assert "new_observation_alias.alias_key" in module.COUNT_WRITE_BODY
    assert module.COUNT_WRITE_BODY.count(
        "ORDER BY pg_catalog.hashtext("
    ) == 2
    assert "ordered_count_alias.alias_key" in module.COUNT_WRITE_BODY
    for aggregate_proof in (
        module.ROUND_SUBMISSION_BODY,
        module.SCOPE_COMPLETION_BODY,
        module.REVIEW_GRAPH_BODY,
        module.RECOUNT_GRAPH_BODY,
        module.DISPOSITION_GRAPH_BODY,
        module.TERMINAL_GRAPH_BODY,
    ):
        assert "), 0)::numeric(18, 3)" not in aggregate_proof
        assert "))::numeric(18, 3)" not in aggregate_proof
        assert "sum(control.control_qty)::numeric(18, 3)" not in (
            aggregate_proof
        )
    assert "request_resolution_jsonb" in module.ROUND_SUBMISSION_BODY
    assert "request_jsonb IS NULL OR" not in module.SCOPE_COMPLETION_BODY
    assert "request_resolution_jsonb IS NULL OR" not in module.SCOPE_COMPLETION_BODY
    assert "opening_headquarters_review_recount" not in module.REVIEW_GRAPH_BODY
    assert "review_row.review_stage = 'headquarters'" in module.REVIEW_GRAPH_BODY
    assert "review_row.decision = 'recount'" in module.REVIEW_GRAPH_BODY
    assert module.FIXED_TASK_BRANCH.count(module.TERMINAL_GRAPH_FUNCTION) == 1
    assert module.LEGACY_TASK_BRANCH.count(module.TERMINAL_GRAPH_FUNCTION) == 0
    assert "__RSC_0052_" not in module.FIXED_TASK_BRANCH

    runtime_audit_binding = module._audit_event_chain_binding_sql(
        "audit_row",
        require_head=False,
    )
    full_chain_binding = module._audit_stream_full_chain_binding_sql(
        "audit_head"
    )
    assert "WITH RECURSIVE" not in runtime_audit_binding
    assert "audit_predecessor" in runtime_audit_binding
    assert "audit_successor" in runtime_audit_binding
    assert "audit_chain_heads" in runtime_audit_binding
    assert "WITH RECURSIVE" in full_chain_binding
    assert module.CANONICAL_JSON_FUNCTION in full_chain_binding
    for helper_body in (
        module.START_GRAPH_BODY,
        module.ROUND_SUBMISSION_BODY,
        module.SCOPE_COMPLETION_BODY,
        module.REVIEW_GRAPH_BODY,
        module.RECOUNT_GRAPH_BODY,
        module.DISPOSITION_GRAPH_BODY,
        module.TERMINAL_GRAPH_BODY,
    ):
        assert module.CANONICAL_JSON_FUNCTION in helper_body

    normalized_closure = " ".join(module.GRAPH_CLOSURE_BODY.split())
    assert module.GRAPH_CLOSURE_BODY.count("AS forbidden_owner") == 2
    assert "TG_TABLE_NAME = 'stocktake_postings'" in normalized_closure
    assert "NEW.posting_kind <> 'opening'" in normalized_closure
    assert (
        "NEW.reason = 'opening_' || review_row.review_stage || "
        "'_review_' || review_row.decision"
    ) in normalized_closure
    assert (
        "NEW.event_type = 'stocktake.opening.' || review_row.review_stage || "
        "'_reviewed'"
    ) in normalized_closure
    review_audit_dispatch = normalized_closure.split(
        "ELSIF NEW.aggregate_type = 'stocktake_review'",
        maxsplit=1,
    )[1].split(
        "ELSIF NEW.aggregate_type = 'stocktake_recount_case'",
        maxsplit=1,
    )[0]
    review_owner_select = review_audit_dispatch.split(
        "IF resolved_task_id IS NOT NULL",
        maxsplit=1,
    )[0]
    assert "NEW.action" not in review_owner_select
    assert "review_row.review_stage" in review_owner_select
    assert (
        "NEW.action <> 'stocktake.opening.' || resolved_review_stage || "
        "'_reviewed'"
    ) in review_audit_dispatch
    assert "terminal_task.status = CASE NEW.reason" in normalized_closure
    assert "terminal_task.status = CASE NEW.event_type" in normalized_closure
    assert "terminal_task.status = 'posted'" in normalized_closure
    state_reconciliation = normalized_closure.split(
        "ELSIF TG_TABLE_NAME = 'state_transition_events'",
        maxsplit=1,
    )[1].split("IF NEW.aggregate_type = 'stocktake_task'", maxsplit=1)[0]
    outbox_reconciliation = normalized_closure.split(
        "ELSIF TG_TABLE_NAME = 'outbox_events'",
        maxsplit=1,
    )[1].split("IF NEW.aggregate_type = 'stocktake_task'", maxsplit=1)[0]
    assert "reconciliation.opening.approve' ) AND" in state_reconciliation
    assert "reconciliation.opening.approve' ) AND" in outbox_reconciliation
    task_outbox = normalized_closure.split(
        "ELSIF TG_TABLE_NAME = 'outbox_events'",
        maxsplit=1,
    )[1].split(
        "ELSIF NEW.aggregate_type = 'stocktake_round'",
        maxsplit=1,
    )[0]
    assert "stocktake.opening.round_submitted" not in task_outbox

    helper_statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", helper_statements.append)
    module._create_opening_helpers()
    for catalog_row in module.HEAD_ONLY_FUNCTION_CATALOG[:7]:
        (
            signature,
            function_name,
            _return_type,
            _language,
            _volatility,
            argument_types,
            argument_names,
            *_rest,
        ) = catalog_row
        create_statement = next(
            statement
            for statement in helper_statements
            if f"CREATE FUNCTION public.{function_name}(" in statement
        )
        assert f"CREATE FUNCTION {signature}" not in create_statement
        for argument_name, argument_type in zip(
            argument_names,
            argument_types,
            strict=True,
        ):
            assert f"{argument_name} {argument_type}" in create_statement
        assert "VOLATILE" in create_statement
        parser.parse_sql(create_statement)

    guard_statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", guard_statements.append)
    module._create_opening_insert_guard()
    module._create_opening_count_write_guard()
    module._create_opening_graph_closure()
    assert sum("CREATE CONSTRAINT TRIGGER" in row for row in guard_statements) == 16
    assert sum("CREATE TRIGGER" in row for row in guard_statements) == 4
    assert sum("ENABLE ALWAYS TRIGGER" in row for row in guard_statements) == 16
    for statement in guard_statements:
        parser.parse_sql(statement)
        if "CREATE FUNCTION" in statement:
            parser.parse_plpgsql_json(statement)

    verification_statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", verification_statements.append)
    module._verify_helper_catalog(present=True, phase="parse probe")
    module._verify_insert_guard_catalog(present=True, phase="parse probe")
    module._verify_count_write_guard_catalog(present=True, phase="parse probe")
    module._verify_graph_closure_catalog(present=True, phase="parse probe")
    module._verify_canonical_json_catalog(phase="parse probe")
    module._verify_reconciliation_dependency_catalog(phase="parse probe")
    module._verify_existing_graph_closure_rows()
    module._require_no_active_opening_task()
    verification_sql = "\n".join(verification_statements)
    assert "has_function_privilege('public'" not in verification_sql.lower()
    assert module.RECONCILIATION_EVENT_KEY_BODY_SHA256 in verification_sql
    assert module.RECONCILIATION_EFFECT_BODY_SHA256 in verification_sql
    # STRICT is a reserved PL/pgSQL keyword.  It parses as a column alias but
    # cannot be dereferenced as an unquoted record field on PostgreSQL 16.
    assert "expected_function.is_strict" in verification_sql
    assert "expected_function.strict" not in verification_sql
    assert "trigger_row.tgenabled = 'A'" in verification_sql
    assert "audit_only_owner" in verification_sql
    assert "state_review.review_stage" in verification_sql
    assert "outbox_review.review_stage" in verification_sql
    assert "state_task.status = 'closed'" in verification_sql
    assert "outbox_task.status = 'closed'" in verification_sql
    for statement in verification_statements:
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)


def test_0052_postgresql_offline_upgrade_repairs_callers_and_task_guard(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0052_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_OPENING_TERMINAL_GUARD_EXECUTION_HEAD_REVISION}:"
        f"{OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    assert "pg_catalog.greatest(" not in sql.lower()
    assert "pg_catalog.least(" not in sql.lower()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.LOCK_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert "-- Running upgrade 20260903_0051 -> 20260903_0052" in sql
    assert sql.count(lock_sql) == 1
    assert "legacy upgrade preflight" in sql
    assert "hardened upgrade postflight" in sql
    assert sql.count(module.CATALOG_ERROR) == 14
    assert sql.count(module.REPLACEMENT_ERROR) == 9
    assert sql.count(module.EXISTING_ROWS_ERROR) == 4
    assert "JOIN public.stocktake_recount_cases AS recount_case" in sql
    assert "source_round.submitted_at = task.submitted_at" in sql
    assert "JOIN public.stocktake_scope_count_completions AS sealing" in sql
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.prokind = 'f'" in sql
    assert "NOT function_row.proretset" in sql
    assert "function_row.pronargs = expected_function.argument_count" in sql
    assert "pg_catalog.oidvectortypes(function_row.proargtypes)" in sql
    assert "function_row.proallargtypes IS NULL" in sql
    assert "function_row.proargmodes IS NULL" in sql
    assert "function_row.pronargdefaults = 0" in sql
    assert "function_row.proargdefaults IS NULL" in sql
    assert "function_row.provariadic = 0" in sql
    assert "function_row.proconfig IS NOT DISTINCT FROM CASE" in sql
    assert "WHEN expected_function.search_path IS NULL" in sql
    assert "THEN NULL::text[]" in sql
    assert "ELSE ARRAY[expected_function.search_path]::text[]" in sql
    assert "function_acl.grantor <> migrator_oid" in sql
    assert "function_acl.grantee = 0" in sql
    assert "pg_catalog.has_function_privilege" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgconstraint <> 0" in sql
    assert "trigger_row.tgdeferrable" in sql
    assert "trigger_row.tginitdeferred" in sql
    assert "trigger_row.tgconstrrelid = 0" in sql
    assert "trigger_row.tgconstrindid = 0" in sql
    assert "trigger_row.tgparentid = 0" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    assert "trigger_row.tgoldtable IS NULL" in sql
    assert "trigger_row.tgnewtable IS NULL" in sql
    assert "trigger_row.tgnargs = 0" in sql
    assert "trigger_row.tgattr = ''::pg_catalog.int2vector" in sql
    for signature in module.CALLER_SIGNATURES:
        assert sql.count(f"ALTER FUNCTION {signature} SECURITY DEFINER") == 1
        assert f"ALTER FUNCTION {signature} SECURITY INVOKER" not in sql
    assert f"ALTER FUNCTION {module.GRAPH_SIGNATURE} SECURITY" not in sql
    assert f"ALTER FUNCTION {module.ACTOR_ASSIGNMENT_SIGNATURE} SECURITY" not in sql
    for expected_hash in (
        module.ACTOR_ASSIGNMENT_BODY_SHA256,
        module.GRAPH_BODY_SHA256,
        module.LEGACY_COMMIT_BODY_SHA256,
        module.FIXED_COMMIT_BODY_SHA256,
        module.LEGACY_ACCOUNT_BODY_SHA256,
        module.FIXED_ACCOUNT_BODY_SHA256,
        module.LEGACY_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021,
        module.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021,
    ):
        assert expected_hash in sql
    assert "NEW.current_round_no <> OLD.current_round_no + 1" in sql
    assert "OLD.submitted_at IS DISTINCT FROM NEW.submitted_at" in sql
    assert "JOIN public.stocktake_round_submissions AS submission" in sql
    assert module.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT in sql
    assert module.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT in sql
    assert module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021 in sql
    assert module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021 in sql
    assert "pg_catalog.pg_get_functiondef(function_oid)" in sql
    assert sql.count("EXECUTE pg_catalog.replace") == 3
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0052'" in sql
    assert module.DOWNGRADE_BLOCKER not in sql
    assert "GRANT " not in sql
    assert "INSERT INTO public." not in sql
    assert sql.count("UPDATE public.") == 1
    assert (
        "UPDATE public.stocktake_scope_count_completions AS completion"
        in sql
    )
    assert module.REQUEST_EVIDENCE_ERROR in sql
    assert "ADD COLUMN request_jsonb JSONB" in sql
    assert "ADD COLUMN request_resolution_jsonb JSONB" in sql
    assert "request_item_sha256" in sql
    assert "target_type', 'observation'" in sql
    assert "mapping.updated_at <=" in sql
    assert "policy.updated_at <=" in sql
    assert (
        "DISABLE TRIGGER " + module.COUNT_COMPLETION_IMMUTABLE_TRIGGER
        in sql
    )
    assert (
        "ENABLE TRIGGER " + module.COUNT_COMPLETION_IMMUTABLE_TRIGGER
        in sql
    )
    assert "DELETE FROM public." not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("legacy upgrade preflight")
        < sql.index(module.EXISTING_ROWS_ERROR)
        < sql.index("commit upgrade")
        < sql.index("account upgrade")
        < sql.index("scope completion guard upgrade")
        < sql.index(f"ALTER FUNCTION {module.COMMIT_SIGNATURE} SECURITY DEFINER")
        < sql.index("hardened upgrade postflight")
        < sql.index(ready_sql)
    )


def test_0052_postgresql_offline_downgrade_blocks_active_opening_and_restores(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0052_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{OPENING_TERMINAL_GUARD_EXECUTION_REVISION_ID}:"
        f"{PRE_OPENING_TERMINAL_GUARD_EXECUTION_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.LOCK_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    ready_sql = (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_oam_runtime_binding_ready_0044()"
    )
    assert "-- Running downgrade 20260903_0052 -> 20260903_0051" in sql
    assert sql.count(lock_sql) == 1
    assert "hardened downgrade preflight" in sql
    assert "legacy downgrade postflight" in sql
    assert sql.count(module.CATALOG_ERROR) == 14
    assert sql.count(module.REPLACEMENT_ERROR) == 9
    assert sql.count(module.DOWNGRADE_BLOCKER) == 1
    assert "task.task_type = 'opening'" in sql
    assert "task.status <> 'closed'" not in sql
    assert "task.status NOT IN ('closed', 'cancelled')" not in sql
    assert "FROM public.inventory_opening_establishments" in sql
    assert "opening-reconciliation-" in sql
    for signature in module.CALLER_SIGNATURES:
        assert sql.count(f"ALTER FUNCTION {signature} SECURITY INVOKER") == 1
        assert f"ALTER FUNCTION {signature} SECURITY DEFINER" not in sql
    assert f"ALTER FUNCTION {module.GRAPH_SIGNATURE} SECURITY" not in sql
    assert f"ALTER FUNCTION {module.ACTOR_ASSIGNMENT_SIGNATURE} SECURITY" not in sql
    assert module.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021 in sql
    assert module.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021 in sql
    assert sql.count("EXECUTE pg_catalog.replace") == 3
    assert sql.count(ready_sql) == 1
    assert "pg_catalog.min(version_num) = '20260903_0051'" in sql
    assert "GRANT " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert "DROP COLUMN request_jsonb" in sql
    assert "DROP COLUMN request_resolution_jsonb" in sql
    assert sql.index("DROP COLUMN request_resolution_jsonb") < sql.index(
        "DROP COLUMN request_jsonb"
    )
    assert (
        sql.index(lock_sql)
        < sql.index("hardened downgrade preflight")
        < sql.index(module.DOWNGRADE_BLOCKER)
        < sql.index("account downgrade")
        < sql.index("commit downgrade")
        < sql.index("scope completion guard downgrade")
        < sql.index(f"ALTER FUNCTION {module.COMMIT_SIGNATURE} SECURITY INVOKER")
        < sql.index("legacy downgrade postflight")
        < sql.index(ready_sql)
    )


def test_0053_pins_nested_round_dispatch_and_exact_function_sources(
    monkeypatch,
) -> None:
    migration_0052 = _load_0052_migration_module()
    module = _load_0053_migration_module()
    assert module.revision == OPENING_GRAPH_TABLE_DISPATCH_REVISION_ID
    assert module.down_revision == PRE_OPENING_GRAPH_TABLE_DISPATCH_HEAD_REVISION
    assert module.PREVIOUS_SCHEMA_REVISION == module.down_revision
    assert len(module.GRAPH_CLOSURE_TRIGGER_CATALOG) == 15
    assert len({row[1] for row in module.GRAPH_CLOSURE_TRIGGER_CATALOG}) == 15
    assert module.GRAPH_CLOSURE_TRIGGER_CATALOG == tuple(
        (
            table_name,
            trigger_name,
            21 if operations == "INSERT OR UPDATE" else 5,
        )
        for table_name, trigger_name, operations
        in migration_0052.GRAPH_CLOSURE_TRIGGER_CATALOG
    )
    assert {row[0] for row in module.GRAPH_CLOSURE_TRIGGER_CATALOG} <= set(
        module.LOCK_TABLES
    )
    assert len(module.LOCK_TABLES) == 57
    assert len(set(module.LOCK_TABLES)) == len(module.LOCK_TABLES)
    assert module.LOCK_TABLES == migration_0052.LOCK_TABLES

    legacy_body = migration_0052.GRAPH_CLOSURE_BODY
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        module.GRAPH_CLOSURE_BODY_SHA256_0052
    )
    assert legacy_body.count(module.GRAPH_ROUND_DISPATCH_0052) == 1
    assert module.GRAPH_ROUND_DISPATCH_0053 not in legacy_body
    fixed_body = legacy_body.replace(
        module.GRAPH_ROUND_DISPATCH_0052,
        module.GRAPH_ROUND_DISPATCH_0053,
    )
    assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
        module.GRAPH_CLOSURE_BODY_SHA256_0053
    )
    assert fixed_body.count(module.GRAPH_ROUND_DISPATCH_0053) == 1
    assert module.GRAPH_ROUND_DISPATCH_0052 not in fixed_body
    assert fixed_body.replace(
        module.GRAPH_ROUND_DISPATCH_0053,
        module.GRAPH_ROUND_DISPATCH_0052,
    ) == legacy_body
    legacy_table_dispatch_conditions = re.findall(
        r"\bIF\s+(TG_TABLE_NAME\b.*?)\s+THEN\b",
        " ".join(legacy_body.split()),
        flags=re.IGNORECASE,
    )
    fixed_table_dispatch_conditions = re.findall(
        r"\bIF\s+(TG_TABLE_NAME\b.*?)\s+THEN\b",
        " ".join(fixed_body.split()),
        flags=re.IGNORECASE,
    )
    assert any(
        re.search(r"\b(?:AND|OR)\b", condition, flags=re.IGNORECASE)
        and re.search(r"\b(?:NEW|OLD)\.", condition, flags=re.IGNORECASE)
        for condition in legacy_table_dispatch_conditions
    )
    assert not any(
        re.search(r"\b(?:AND|OR)\b", condition, flags=re.IGNORECASE)
        and re.search(r"\b(?:NEW|OLD)\.", condition, flags=re.IGNORECASE)
        for condition in fixed_table_dispatch_conditions
    )
    assert "IF TG_TABLE_NAME = 'stocktake_rounds' THEN" in fixed_body
    assert "        IF NEW.status = 'counting' THEN" in fixed_body

    legacy_ready_sql = migration_0052._oam_runtime_ready_function_sql(
        module.PREVIOUS_SCHEMA_REVISION
    )
    legacy_ready_body = legacy_ready_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    assert hashlib.sha256(legacy_ready_body.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0052
    )
    assert legacy_ready_body.count(module.RUNTIME_READY_REVISION_0052) == 1
    fixed_ready_body = legacy_ready_body.replace(
        module.RUNTIME_READY_REVISION_0052,
        module.RUNTIME_READY_REVISION_0053,
    )
    assert hashlib.sha256(fixed_ready_body.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0053
    )

    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(
        "CREATE FUNCTION public.rsc_0053_parse_probe() RETURNS trigger "
        "LANGUAGE plpgsql AS $rsc_0053_parse$"
        + fixed_body
        + "$rsc_0053_parse$"
    )
    parser.parse_plpgsql_json(
        "CREATE FUNCTION public.rsc_0053_parse_probe() RETURNS trigger "
        "LANGUAGE plpgsql AS $rsc_0053_parse$"
        + fixed_body
        + "$rsc_0053_parse$"
    )
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_graph_catalog(
        expected_body_sha256=module.GRAPH_CLOSURE_BODY_SHA256_0052,
        phase="legacy parse probe",
    )
    module._verify_graph_catalog(
        expected_body_sha256=module.GRAPH_CLOSURE_BODY_SHA256_0053,
        phase="fixed parse probe",
    )
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0052,
        phase="legacy readiness parse probe",
    )
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0053,
        phase="fixed readiness parse probe",
    )
    module._replace_function_source(
        signature=module.GRAPH_CLOSURE_SIGNATURE,
        expected_body_sha256=module.GRAPH_CLOSURE_BODY_SHA256_0052,
        expected_replacement_body_sha256=(
            module.GRAPH_CLOSURE_BODY_SHA256_0053
        ),
        source_fragment=module.GRAPH_ROUND_DISPATCH_0052,
        replacement_fragment=module.GRAPH_ROUND_DISPATCH_0053,
        phase="graph parse probe",
    )
    module._replace_function_source(
        signature=module.RUNTIME_READY_SIGNATURE,
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0052,
        expected_replacement_body_sha256=(
            module.RUNTIME_READY_BODY_SHA256_0053
        ),
        source_fragment=module.RUNTIME_READY_REVISION_0052,
        replacement_fragment=module.RUNTIME_READY_REVISION_0053,
        phase="readiness parse probe",
    )
    module._require_no_opening_evidence()
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    with pytest.raises(ValueError, match="unsupported 0053 graph body hash"):
        module._verify_graph_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0053 readiness body hash"):
        module._verify_runtime_ready_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0053 function"):
        module._replace_function_source(
            signature=module.GRAPH_CLOSURE_SIGNATURE,
            expected_body_sha256=module.GRAPH_CLOSURE_BODY_SHA256_0052,
            expected_replacement_body_sha256=(
                module.GRAPH_CLOSURE_BODY_SHA256_0053
            ),
            source_fragment=module.GRAPH_ROUND_DISPATCH_0053,
            replacement_fragment=module.GRAPH_ROUND_DISPATCH_0052,
            phase="invalid",
        )


def test_0053_postgresql_offline_upgrade_is_source_only_and_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0053_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_OPENING_GRAPH_TABLE_DISPATCH_HEAD_REVISION}:"
        f"{OPENING_GRAPH_TABLE_DISPATCH_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.LOCK_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260903_0052 -> 20260904_0053" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    for failure_reason in (
        "migration role mismatch",
        "graph identity mismatch",
        "graph function mismatch",
        "graph ACL mismatch",
        "graph trigger mismatch",
        "readiness identity mismatch",
        "readiness function mismatch",
        "readiness ACL mismatch",
        "source mismatch",
        "definition mismatch",
        "replacement hash mismatch",
    ):
        assert failure_reason in sql
    assert module.GRAPH_CLOSURE_BODY_SHA256_0052 in sql
    assert module.GRAPH_CLOSURE_BODY_SHA256_0053 in sql
    assert module.RUNTIME_READY_BODY_SHA256_0052 in sql
    assert module.RUNTIME_READY_BODY_SHA256_0053 in sql
    assert module.GRAPH_ROUND_DISPATCH_0052 in sql
    assert module.GRAPH_ROUND_DISPATCH_0053 in sql
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.proconfig = ARRAY['search_path=pg_catalog, public']" in sql
    assert "function_row.proconfig = ARRAY['search_path=pg_catalog']" in sql
    assert "function_acl.grantee NOT IN" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgdeferrable" in sql
    assert "trigger_row.tginitdeferred" in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TRIGGER" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert module.DOWNGRADE_BLOCKER not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("upgrade preflight")
        < sql.index("graph upgrade")
        < sql.index("readiness upgrade")
        < sql.index("upgrade postflight")
    )


def test_0053_postgresql_offline_downgrade_requires_empty_opening_graph(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0053_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{OPENING_GRAPH_TABLE_DISPATCH_REVISION_ID}:"
        f"{PRE_OPENING_GRAPH_TABLE_DISPATCH_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.LOCK_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260904_0053 -> 20260903_0052" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    assert sql.count(module.DOWNGRADE_BLOCKER) == 1
    assert "task.task_type = 'opening'" in sql
    assert "FROM public.inventory_opening_establishments" in sql
    assert "opening-reconciliation-" in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TRIGGER" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("downgrade preflight")
        < sql.index(module.DOWNGRADE_BLOCKER)
        < sql.index("readiness downgrade")
        < sql.index("graph downgrade")
        < sql.index("downgrade postflight")
    )


def test_0054_pins_historical_recount_assignment_exception_and_catalog(
    monkeypatch,
) -> None:
    migration_0052 = _load_0052_migration_module()
    migration_0053 = _load_0053_migration_module()
    module = _load_0054_migration_module()
    assert module.revision == OPENING_RECOUNT_SOURCE_HISTORY_REVISION_ID
    assert module.down_revision == PRE_OPENING_RECOUNT_SOURCE_HISTORY_HEAD_REVISION
    assert module.PREVIOUS_SCHEMA_REVISION == module.down_revision
    assert len(module.LOCK_TABLES) == 57
    assert len(set(module.LOCK_TABLES)) == len(module.LOCK_TABLES)
    assert module.LOCK_TABLES == migration_0053.LOCK_TABLES
    assert len(module.ROUND_SUBMISSION_CALLER_CATALOG) == 6
    assert len(
        {row[0] for row in module.ROUND_SUBMISSION_CALLER_CATALOG}
    ) == 6
    assert sum(row[6] for row in module.ROUND_SUBMISSION_CALLER_CATALOG) == 14
    assert sum(row[7] for row in module.ROUND_SUBMISSION_CALLER_CATALOG) == 26
    assert module.ROUND_SUBMISSION_TRIGGER_CATALOG == (
        *tuple(
            (
                module.GRAPH_CLOSURE_SIGNATURE,
                table_name,
                trigger_name,
                trigger_type,
            )
            for table_name, trigger_name, trigger_type
            in migration_0053.GRAPH_CLOSURE_TRIGGER_CATALOG
        ),
        *tuple(
            (function_signature, table_name, trigger_name, trigger_type)
            for table_name, trigger_name, function_signature, trigger_type
            in migration_0052.TRIGGER_CATALOG
            if function_signature == module.TERMINAL_COMMIT_SIGNATURE
        ),
    )
    assert len(module.ROUND_SUBMISSION_TRIGGER_CATALOG) == 26
    assert len(
        {row[2] for row in module.ROUND_SUBMISSION_TRIGGER_CATALOG}
    ) == 26
    assert {
        row[1] for row in module.ROUND_SUBMISSION_TRIGGER_CATALOG
    }.issubset(set(module.LOCK_TABLES))
    caller_trigger_counts = {
        row[0]: row[7] for row in module.ROUND_SUBMISSION_CALLER_CATALOG
    }
    assert {
        signature: sum(
            1
            for trigger_row in module.ROUND_SUBMISSION_TRIGGER_CATALOG
            if trigger_row[0] == signature
        )
        for signature in caller_trigger_counts
        if caller_trigger_counts[signature]
    } == {
        signature: trigger_count
        for signature, trigger_count in caller_trigger_counts.items()
        if trigger_count
    }

    legacy_body = migration_0052.ROUND_SUBMISSION_BODY
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        module.ROUND_SUBMISSION_BODY_SHA256_0053
    )
    assert legacy_body.count(module.INITIAL_ASSIGNMENT_REJECTION_0053) == 2
    assert module.INITIAL_ASSIGNMENT_REJECTION_0054 not in legacy_body
    fixed_body = legacy_body.replace(
        module.INITIAL_ASSIGNMENT_REJECTION_0053,
        module.INITIAL_ASSIGNMENT_REJECTION_0054,
    )
    assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
        module.ROUND_SUBMISSION_BODY_SHA256_0054
    )
    assert fixed_body.count(module.INITIAL_ASSIGNMENT_REJECTION_0054) == 2
    assert module.INITIAL_ASSIGNMENT_REJECTION_0053 not in fixed_body
    assert fixed_body.replace(
        module.INITIAL_ASSIGNMENT_REJECTION_0054,
        module.INITIAL_ASSIGNMENT_REJECTION_0053,
    ) == legacy_body
    assert fixed_body.count("AND NOT p_historical") == (
        legacy_body.count("AND NOT p_historical") + 2
    )

    ready_0052_sql = migration_0052._oam_runtime_ready_function_sql(
        migration_0052.revision
    )
    ready_0052 = ready_0052_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
    ready_0053 = ready_0052.replace(
        migration_0053.RUNTIME_READY_REVISION_0052,
        migration_0053.RUNTIME_READY_REVISION_0053,
    )
    assert hashlib.sha256(ready_0053.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0053
    )
    assert ready_0053.count(module.RUNTIME_READY_REVISION_0053) == 1
    ready_0054 = ready_0053.replace(
        module.RUNTIME_READY_REVISION_0053,
        module.RUNTIME_READY_REVISION_0054,
    )
    assert hashlib.sha256(ready_0054.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0054
    )

    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(
        "CREATE FUNCTION public.rsc_0054_parse_probe("
        "p_task_id uuid, p_round_id uuid, p_historical boolean) "
        "RETURNS boolean LANGUAGE sql AS $rsc_0054_parse$"
        + fixed_body
        + "$rsc_0054_parse$"
    )
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_round_submission_catalog(
        expected_body_sha256=module.ROUND_SUBMISSION_BODY_SHA256_0053,
        phase="legacy helper parse probe",
    )
    module._verify_round_submission_catalog(
        expected_body_sha256=module.ROUND_SUBMISSION_BODY_SHA256_0054,
        phase="fixed helper parse probe",
    )
    module._verify_round_submission_callers(phase="caller parse probe")
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0053,
        phase="legacy readiness parse probe",
    )
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0054,
        phase="fixed readiness parse probe",
    )
    module._replace_function_source(
        signature=module.ROUND_SUBMISSION_SIGNATURE,
        expected_body_sha256=module.ROUND_SUBMISSION_BODY_SHA256_0053,
        expected_replacement_body_sha256=(
            module.ROUND_SUBMISSION_BODY_SHA256_0054
        ),
        source_fragment=module.INITIAL_ASSIGNMENT_REJECTION_0053,
        replacement_fragment=module.INITIAL_ASSIGNMENT_REJECTION_0054,
        expected_source_count=2,
        phase="helper parse probe",
    )
    module._replace_function_source(
        signature=module.RUNTIME_READY_SIGNATURE,
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0053,
        expected_replacement_body_sha256=module.RUNTIME_READY_BODY_SHA256_0054,
        source_fragment=module.RUNTIME_READY_REVISION_0053,
        replacement_fragment=module.RUNTIME_READY_REVISION_0054,
        expected_source_count=1,
        phase="readiness parse probe",
    )
    module._require_no_opening_evidence()
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    with pytest.raises(ValueError, match="unsupported 0054 round submission"):
        module._verify_round_submission_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0054 readiness"):
        module._verify_runtime_ready_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0054 function"):
        module._replace_function_source(
            signature=module.ROUND_SUBMISSION_SIGNATURE,
            expected_body_sha256=module.ROUND_SUBMISSION_BODY_SHA256_0053,
            expected_replacement_body_sha256=(
                module.ROUND_SUBMISSION_BODY_SHA256_0054
            ),
            source_fragment=module.INITIAL_ASSIGNMENT_REJECTION_0053,
            replacement_fragment=module.INITIAL_ASSIGNMENT_REJECTION_0054,
            expected_source_count=1,
            phase="invalid",
        )


def test_0054_postgresql_offline_upgrade_is_source_only_and_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0054_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_OPENING_RECOUNT_SOURCE_HISTORY_HEAD_REVISION}:"
        f"{OPENING_RECOUNT_SOURCE_HISTORY_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.LOCK_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260904_0053 -> 20260904_0054" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    for failure_reason in (
        "migration role mismatch",
        "helper identity mismatch",
        "helper function mismatch",
        "helper ACL mismatch",
        "caller role mismatch",
        "caller catalog mismatch",
        "caller trigger mismatch",
        "readiness identity mismatch",
        "readiness function mismatch",
        "readiness ACL mismatch",
        "source mismatch",
        "definition mismatch",
        "replacement mismatch",
    ):
        assert failure_reason in sql
    assert module.ROUND_SUBMISSION_BODY_SHA256_0053 in sql
    assert module.ROUND_SUBMISSION_BODY_SHA256_0054 in sql
    assert module.RUNTIME_READY_BODY_SHA256_0053 in sql
    assert module.RUNTIME_READY_BODY_SHA256_0054 in sql
    assert module.INITIAL_ASSIGNMENT_REJECTION_0053 in sql
    assert module.INITIAL_ASSIGNMENT_REJECTION_0054 in sql
    assert "function_row.proowner = migrator_oid" in sql
    assert "expected_caller.trigger_count" in sql
    assert "function_row.proallargtypes IS NULL" in sql
    assert "function_row.pronargdefaults = 0" in sql
    assert "named_function.proname" in sql
    assert "candidate_namespace" not in sql
    assert "expected_trigger.function_signature" in sql
    assert "namespace_row.nspname = 'public'" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgdeferrable" in sql
    assert "trigger_row.tginitdeferred" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    for _signature, table_name, trigger_name, trigger_type in (
        module.ROUND_SUBMISSION_TRIGGER_CATALOG
    ):
        assert f"'{table_name}'" in sql
        assert f"'{trigger_name}'" in sql
        assert f", {trigger_type})" in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TRIGGER" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert module.DOWNGRADE_BLOCKER not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("upgrade preflight")
        < sql.index("round submission upgrade")
        < sql.index("readiness upgrade")
        < sql.index("upgrade postflight")
    )


def test_0054_postgresql_offline_downgrade_requires_empty_opening_graph(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0054_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{OPENING_RECOUNT_SOURCE_HISTORY_REVISION_ID}:"
        f"{PRE_OPENING_RECOUNT_SOURCE_HISTORY_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(
            f"public.{table_name}" for table_name in module.LOCK_TABLES
        )
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260904_0054 -> 20260904_0053" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    assert sql.count(module.DOWNGRADE_BLOCKER) == 1
    assert "task.task_type = 'opening'" in sql
    assert "FROM public.inventory_opening_establishments" in sql
    assert "opening-reconciliation-" in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TRIGGER" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("downgrade preflight")
        < sql.index(module.DOWNGRADE_BLOCKER)
        < sql.index("readiness downgrade")
        < sql.index("round submission downgrade")
        < sql.index("downgrade postflight")
    )


def test_0055_pins_nonopening_start_audit_order_and_catalog(
    monkeypatch,
) -> None:
    migration_0047 = _load_0047_migration_module()
    module = _load_0055_migration_module()

    assert module.revision == NONOPENING_START_AUDIT_ORDER_REVISION_ID
    assert module.down_revision == PRE_NONOPENING_START_AUDIT_ORDER_HEAD_REVISION
    assert module.PREVIOUS_SCHEMA_REVISION == module.down_revision
    assert module.DEFERRED_TABLES == migration_0047.DEFERRED_TABLES
    assert len(module.LOCK_TABLES) == 30
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))
    assert set(module.DEFERRED_TABLES).issubset(module.LOCK_TABLES)
    assert set(module.LOCK_TABLES) == {
        "audit_events",
        "auth_identities",
        "custody_assignments",
        "inventory_freezes",
        "inventory_ledger_heads",
        "inventory_lots",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_serials",
        "inventory_transactions",
        "material_inventory_policies",
        "materials",
        "organizations",
        "people",
        "permissions",
        "role_assignments",
        "role_permissions",
        "roles",
        "serial_current_positions",
        "state_transition_events",
        "stock_accounts",
        "stock_balances",
        "stock_locations",
        "stocktake_postings",
        "stocktake_rounds",
        "stocktake_scopes",
        "stocktake_snapshot_lines",
        "stocktake_start_completions",
        "stocktake_tasks",
        "users",
    }

    expected_guard_triggers = {
        (
            migration_0047.COMPLETION_TABLE,
            migration_0047.PG_GUARD_TRIGGER,
            31,
        ),
        *{
            (
                table_name,
                migration_0047.PG_SEALED_GUARD_TRIGGERS[table_name],
                31,
            )
            for table_name in migration_0047.SEALED_GUARD_TABLES
        },
    }
    assert set(module.GUARD_TRIGGER_CATALOG) == expected_guard_triggers
    assert len(module.GUARD_TRIGGER_CATALOG) == 8
    assert len({row[1] for row in module.GUARD_TRIGGER_CATALOG}) == 8
    assert module.DISPATCH_TRIGGER_CATALOG == tuple(
        (
            table_name,
            migration_0047.PG_DEFERRED_TRIGGERS[table_name],
            29,
        )
        for table_name in migration_0047.DEFERRED_TABLES
    )
    assert len({row[1] for row in module.DISPATCH_TRIGGER_CATALOG}) == 8
    assert module.START_FUNCTION_CATALOG == (
        (
            module.GUARD_SIGNATURE,
            module.GUARD_FUNCTION,
            "trigger",
            (),
            (),
            module.GUARD_BODY_SHA256_0054,
            module.GUARD_BODY_SHA256_0055,
            8,
        ),
        (
            module.VALIDATOR_SIGNATURE,
            module.VALIDATOR_FUNCTION,
            "void",
            ("uuid",),
            ("checked_task_id",),
            module.VALIDATOR_BODY_SHA256_0054,
            module.VALIDATOR_BODY_SHA256_0055,
            0,
        ),
    )

    def function_body(statement: str) -> str:
        return statement.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    legacy_guard = function_body(migration_0047._postgresql_guard_function_sql())
    legacy_validator = function_body(
        migration_0047._postgresql_validator_function_sql()
    )
    dispatch_body = function_body(
        migration_0047._postgresql_dispatch_function_sql()
    )
    for legacy_body, legacy_hash, fixed_hash in (
        (
            legacy_guard,
            module.GUARD_BODY_SHA256_0054,
            module.GUARD_BODY_SHA256_0055,
        ),
        (
            legacy_validator,
            module.VALIDATOR_BODY_SHA256_0054,
            module.VALIDATOR_BODY_SHA256_0055,
        ),
    ):
        assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
            legacy_hash
        )
        assert legacy_body.count(module.LEGACY_AUDIT_ORDER) == 1
        assert module.FIXED_AUDIT_ORDER not in legacy_body
        fixed_body = legacy_body.replace(
            module.LEGACY_AUDIT_ORDER,
            module.FIXED_AUDIT_ORDER,
        )
        assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == fixed_hash
        assert fixed_body.count(module.FIXED_AUDIT_ORDER) == 1
        assert module.LEGACY_AUDIT_ORDER not in fixed_body
        assert fixed_body.replace(
            module.FIXED_AUDIT_ORDER,
            module.LEGACY_AUDIT_ORDER,
        ) == legacy_body
    assert hashlib.sha256(dispatch_body.encode("utf-8")).hexdigest() == (
        module.DISPATCH_BODY_SHA256
    )
    assert dispatch_body.count(module.VALIDATOR_FUNCTION) == 3
    assert module.LEGACY_AUDIT_ORDER not in dispatch_body
    assert module.FIXED_AUDIT_ORDER not in dispatch_body

    ready_0047 = function_body(
        migration_0047._oam_runtime_ready_function_sql(migration_0047.revision)
    )
    assert ready_0047.count(migration_0047.revision) == 1
    ready_0054 = ready_0047.replace(
        migration_0047.revision,
        module.RUNTIME_READY_REVISION_0054,
    )
    assert hashlib.sha256(ready_0054.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0054
    )
    assert ready_0054.count(module.RUNTIME_READY_REVISION_0054) == 1
    ready_0055 = ready_0054.replace(
        module.RUNTIME_READY_REVISION_0054,
        module.RUNTIME_READY_REVISION_0055,
    )
    assert hashlib.sha256(ready_0055.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0055
    )
    assert ready_0055.count(module.RUNTIME_READY_REVISION_0055) == 1

    parser = pytest.importorskip("pglast.parser")
    for fixed_body, function_name, arguments, result_type in (
        (
            legacy_guard.replace(
                module.LEGACY_AUDIT_ORDER,
                module.FIXED_AUDIT_ORDER,
            ),
            "rsc_0055_guard_parse_probe",
            "",
            "trigger",
        ),
        (
            legacy_validator.replace(
                module.LEGACY_AUDIT_ORDER,
                module.FIXED_AUDIT_ORDER,
            ),
            "rsc_0055_validator_parse_probe",
            "checked_task_id uuid",
            "void",
        ),
    ):
        parser.parse_sql(
            f"CREATE FUNCTION public.{function_name}({arguments}) "
            f"RETURNS {result_type} LANGUAGE plpgsql AS $rsc_0055_parse$"
            + fixed_body
            + "$rsc_0055_parse$"
        )

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_audit_sequence_catalog(phase="audit parse probe")
    module._verify_start_function_catalog(fixed=False, phase="legacy parse probe")
    module._verify_start_function_catalog(fixed=True, phase="fixed parse probe")
    module._verify_dispatch_catalog(phase="dispatch parse probe")
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0054,
        phase="legacy readiness parse probe",
    )
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0055,
        phase="fixed readiness parse probe",
    )
    module._replace_function_source(
        signature=module.GUARD_SIGNATURE,
        expected_body_sha256=module.GUARD_BODY_SHA256_0054,
        expected_replacement_body_sha256=module.GUARD_BODY_SHA256_0055,
        source_fragment=module.LEGACY_AUDIT_ORDER,
        replacement_fragment=module.FIXED_AUDIT_ORDER,
        phase="guard parse probe",
    )
    module._replace_function_source(
        signature=module.VALIDATOR_SIGNATURE,
        expected_body_sha256=module.VALIDATOR_BODY_SHA256_0054,
        expected_replacement_body_sha256=module.VALIDATOR_BODY_SHA256_0055,
        source_fragment=module.LEGACY_AUDIT_ORDER,
        replacement_fragment=module.FIXED_AUDIT_ORDER,
        phase="validator parse probe",
    )
    module._replace_function_source(
        signature=module.RUNTIME_READY_SIGNATURE,
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0054,
        expected_replacement_body_sha256=module.RUNTIME_READY_BODY_SHA256_0055,
        source_fragment=module.RUNTIME_READY_REVISION_0054,
        replacement_fragment=module.RUNTIME_READY_REVISION_0055,
        phase="readiness parse probe",
    )
    module._validate_existing_start_graphs(phase="graph parse probe")
    module._require_no_start_graph()
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    with pytest.raises(ValueError, match="unsupported 0055 start function"):
        module._verify_start_function_catalog(fixed=None, phase="invalid")
    with pytest.raises(ValueError, match="unsupported 0055 readiness"):
        module._verify_runtime_ready_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0055 function"):
        module._replace_function_source(
            signature=module.GUARD_SIGNATURE,
            expected_body_sha256=module.GUARD_BODY_SHA256_0054,
            expected_replacement_body_sha256=module.GUARD_BODY_SHA256_0055,
            source_fragment=module.FIXED_AUDIT_ORDER,
            replacement_fragment=module.LEGACY_AUDIT_ORDER,
            phase="invalid",
        )


def test_0055_postgresql_offline_upgrade_is_source_only_and_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0055_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_NONOPENING_START_AUDIT_ORDER_HEAD_REVISION}:"
        f"{NONOPENING_START_AUDIT_ORDER_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260904_0054 -> 20260905_0055" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 3
    for failure_reason in (
        "migration role mismatch",
        "audit identity mismatch",
        "audit sequence mismatch",
        "function identity mismatch",
        "function catalog mismatch",
        "guard trigger mismatch",
        "dispatcher identity mismatch",
        "dispatcher function mismatch",
        "dispatcher ACL mismatch",
        "dispatcher trigger mismatch",
        "readiness identity mismatch",
        "readiness function mismatch",
        "readiness ACL mismatch",
        "source mismatch",
        "definition mismatch",
        "replacement mismatch",
    ):
        assert failure_reason in sql
    for body_hash in (
        module.GUARD_BODY_SHA256_0054,
        module.GUARD_BODY_SHA256_0055,
        module.VALIDATOR_BODY_SHA256_0054,
        module.VALIDATOR_BODY_SHA256_0055,
        module.DISPATCH_BODY_SHA256,
        module.RUNTIME_READY_BODY_SHA256_0054,
        module.RUNTIME_READY_BODY_SHA256_0055,
    ):
        assert body_hash in sql
    assert module.LEGACY_AUDIT_ORDER in sql
    assert module.FIXED_AUDIT_ORDER in sql
    assert "attribute_row.atttypid = 'character varying'::pg_catalog.regtype" in sql
    assert "attribute_row.atttypmod = 164" in sql
    assert "attribute_row.attname = 'sequence_no'" in sql
    assert "uq_audit_events_stream_version_0017" in sql
    assert "FROM pg_catalog.pg_proc AS caller_row" in sql
    assert (
        "PERFORM public.rsc_validate_nonopening_stocktake_start_causality_0047"
        in sql
    )
    assert "function_row.proowner = migrator_oid" in sql
    assert "function_row.proallargtypes IS NULL" in sql
    assert "function_row.pronargdefaults = 0" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgdeferrable" in sql
    assert "trigger_row.tginitdeferred" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    for table_name, trigger_name, trigger_type in (
        *module.GUARD_TRIGGER_CATALOG,
        *module.DISPATCH_TRIGGER_CATALOG,
    ):
        assert f"'{table_name}'" in sql
        assert f"'{trigger_name}'" in sql
        assert f", {trigger_type})" in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TRIGGER" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert module.DOWNGRADE_BLOCKER not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("upgrade preflight")
        < sql.index("guard upgrade")
        < sql.index("validator upgrade")
        < sql.index("upgrade replacement")
        < sql.index("readiness upgrade")
        < sql.index("upgrade postflight")
    )


def test_0055_postgresql_offline_downgrade_requires_empty_start_graph(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0055_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{NONOPENING_START_AUDIT_ORDER_REVISION_ID}:"
        f"{PRE_NONOPENING_START_AUDIT_ORDER_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260905_0055 -> 20260904_0054" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 3
    assert sql.count(module.DOWNGRADE_BLOCKER) == 1
    assert "FROM public.stocktake_start_completions" in sql
    assert f"task.task_type IN {module.NONOPENING_SQL}" in sql
    assert "task.status <> 'draft'" in sql
    assert "stocktake_task_issued" in sql
    assert "stocktake.task.started" in sql
    assert module.COMMAND_SCHEMA in sql
    assert "CREATE TABLE" not in sql
    assert "ALTER TABLE" not in sql
    assert "DROP TRIGGER" not in sql
    assert "CREATE TRIGGER" not in sql
    assert "GRANT " not in sql
    assert "REVOKE " not in sql
    assert "INSERT INTO public." not in sql
    assert "UPDATE public." not in sql
    assert "DELETE FROM public." not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("downgrade preflight")
        < sql.index(module.DOWNGRADE_BLOCKER)
        < sql.index("readiness downgrade")
        < sql.index("validator downgrade")
        < sql.index("guard downgrade")
        < sql.index("downgrade postflight")
    )


def test_0056_pins_nonopening_count_helper_callers_and_readiness(
    monkeypatch,
) -> None:
    legacy_spec = importlib.util.spec_from_file_location(
        "stocktake_round_assignment_guards_migration_0021_for_0056",
        STOCKTAKE_ROUND_ASSIGNMENT_GUARDS_REVISION,
    )
    assert legacy_spec is not None and legacy_spec.loader is not None
    legacy = importlib.util.module_from_spec(legacy_spec)
    legacy_spec.loader.exec_module(legacy)
    migration_0047 = _load_0047_migration_module()
    module = _load_0056_migration_module()

    assert module.revision == NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION_ID
    assert (
        module.down_revision
        == PRE_NONOPENING_COUNT_GUARD_COMPATIBILITY_HEAD_REVISION
    )
    assert module.PREVIOUS_SCHEMA_REVISION == module.down_revision
    assert module.NONOPENING_TASK_TYPES == (
        "full",
        "sample",
        "ad_hoc",
        "personal",
        "termination",
    )
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))
    assert {
        "role_assignments",
        "roles",
        "stock_accounts",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_count_serials",
        "stocktake_recount_scope_assignments",
        "stocktake_round_submissions",
        "stocktake_rounds",
        "stocktake_scope_count_completions",
        "stocktake_scopes",
        "stocktake_snapshot_lines",
        "stocktake_tasks",
        "users",
    } <= set(module.LOCK_TABLES)
    assert module.CALLER_CATALOG == (
        (
            "public.rsc_validate_stocktake_count_line_insert_0021()",
            "rsc_validate_stocktake_count_line_insert_0021",
            "319b1804e6fa6af3c7d3510524755d4b4d74b556a90640f6adfa6efa17f19693",
            "stocktake_count_lines",
            "trg_stocktake_count_lines_assignment_0021",
        ),
        (
            "public.rsc_validate_stocktake_observation_insert_0021()",
            "rsc_validate_stocktake_observation_insert_0021",
            "06cf2fafa1d90f120fe4bba21cc1dc55dba70bd63f649671b4333a6159af06bb",
            "stocktake_count_observations",
            "trg_stocktake_count_observations_assignment_0021",
        ),
        (
            "public.rsc_validate_stocktake_scope_completion_insert_0021()",
            "rsc_validate_stocktake_scope_completion_insert_0021",
            "9fda4b71155e3bdb6802e2f09bf32b90284b0f0643f4d518ee3aa286bf9efdc9",
            "stocktake_scope_count_completions",
            "trg_stocktake_scope_completions_assignment_0021",
        ),
    )

    def function_body(statement: str) -> str:
        return statement.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    legacy_body = function_body(legacy._postgresql_actor_function_sql())
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        module.LEGACY_BODY_SHA256
    )
    assert legacy_body.count(module.LEGACY_SOURCE_FRAGMENT) == 1
    assert module.FIXED_SOURCE_FRAGMENT not in legacy_body
    fixed_body = legacy_body.replace(
        module.LEGACY_SOURCE_FRAGMENT,
        module.FIXED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
        module.FIXED_BODY_SHA256
    )
    assert fixed_body.count(module.FIXED_SOURCE_FRAGMENT) == 1
    assert module.LEGACY_SOURCE_FRAGMENT not in fixed_body
    assert fixed_body.replace(
        module.FIXED_SOURCE_FRAGMENT,
        module.LEGACY_SOURCE_FRAGMENT,
    ) == legacy_body

    ready_0047 = function_body(
        migration_0047._oam_runtime_ready_function_sql(migration_0047.revision)
    )
    ready_0055 = ready_0047.replace(
        migration_0047.revision,
        module.PREVIOUS_SCHEMA_REVISION,
    )
    ready_0056 = ready_0055.replace(
        module.PREVIOUS_SCHEMA_REVISION,
        module.revision,
    )
    assert hashlib.sha256(ready_0055.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0055
    )
    assert hashlib.sha256(ready_0056.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0056
    )

    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(
        "CREATE FUNCTION public.rsc_0056_helper_parse_probe("
        "p_task_id uuid, p_round_id uuid, p_scope_id uuid, p_user_id text, "
        "p_person_id uuid, p_assignment_id uuid, "
        "p_authorization_version bigint, p_role_code text, "
        "p_scope_type text, p_scope_id_snapshot text, "
        "p_occurred_at timestamptz, p_require_full_snapshot boolean) "
        "RETURNS boolean LANGUAGE sql AS $rsc_0056_parse$"
        + fixed_body
        + "$rsc_0056_parse$"
    )

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_guard_catalog(fixed=False, phase="legacy parse probe")
    module._verify_guard_catalog(fixed=True, phase="fixed parse probe")
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0055,
        phase="legacy readiness parse probe",
    )
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0056,
        phase="fixed readiness parse probe",
    )
    module._require_no_nonopening_count_facts(
        blocker=module.UPGRADE_BLOCKER,
        phase="upgrade fact parse probe",
    )
    module._require_no_nonopening_count_facts(
        blocker=module.DOWNGRADE_BLOCKER,
        phase="downgrade fact parse probe",
    )
    module._replace_function_source(
        signature=module.HELPER_SIGNATURE,
        expected_body_sha256=module.LEGACY_BODY_SHA256,
        expected_replacement_body_sha256=module.FIXED_BODY_SHA256,
        source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=module.FIXED_SOURCE_FRAGMENT,
        phase="helper parse probe",
    )
    module._replace_function_source(
        signature=module.RUNTIME_READY_SIGNATURE,
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0055,
        expected_replacement_body_sha256=module.RUNTIME_READY_BODY_SHA256_0056,
        source_fragment=module.PREVIOUS_SCHEMA_REVISION,
        replacement_fragment=module.revision,
        phase="readiness parse probe",
    )
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    with pytest.raises(ValueError, match="unsupported 0056 guard"):
        module._verify_guard_catalog(fixed=None, phase="invalid")
    with pytest.raises(ValueError, match="unsupported 0056 readiness"):
        module._verify_runtime_ready_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0056 non-opening"):
        module._require_no_nonopening_count_facts(
            blocker="invalid",
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0056 function"):
        module._replace_function_source(
            signature=module.HELPER_SIGNATURE,
            expected_body_sha256=module.LEGACY_BODY_SHA256,
            expected_replacement_body_sha256=module.FIXED_BODY_SHA256,
            source_fragment=module.FIXED_SOURCE_FRAGMENT,
            replacement_fragment=module.LEGACY_SOURCE_FRAGMENT,
            phase="invalid",
        )


def test_0056_postgresql_offline_upgrade_is_locked_and_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0056_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_NONOPENING_COUNT_GUARD_COMPATIBILITY_HEAD_REVISION}:"
        f"{NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260905_0055 -> 20260905_0056" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    assert sql.count(module.UPGRADE_BLOCKER) == 1
    assert module.DOWNGRADE_BLOCKER not in sql
    for body_hash in (
        module.LEGACY_BODY_SHA256,
        module.FIXED_BODY_SHA256,
        module.RUNTIME_READY_BODY_SHA256_0055,
        module.RUNTIME_READY_BODY_SHA256_0056,
        *(row[2] for row in module.CALLER_CATALOG),
    ):
        assert body_hash in sql
    assert module.LEGACY_SOURCE_FRAGMENT in sql
    assert module.FIXED_SOURCE_FRAGMENT in sql
    assert "function_row.prosecdef" in sql
    assert "NOT function_row.prosecdef" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgtype = 7" in sql
    assert "FROM pg_catalog.pg_proc AS caller_row" in sql
    for _, _, _, table_name, trigger_name in module.CALLER_CATALOG:
        assert f"'{table_name}'" in sql
        assert f"'{trigger_name}'" in sql
    for forbidden in (
        "CREATE TABLE",
        "ALTER TABLE",
        "DROP TRIGGER",
        "CREATE TRIGGER",
        "GRANT ",
        "REVOKE ",
        "INSERT INTO public.",
        "UPDATE public.",
        "DELETE FROM public.",
    ):
        assert forbidden not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("upgrade preflight")
        < sql.index(module.UPGRADE_BLOCKER)
        < sql.index("helper upgrade")
        < sql.index("upgrade replacement")
        < sql.index("readiness upgrade")
        < sql.index("upgrade postflight")
    )


def test_0056_postgresql_offline_downgrade_rejects_nonopening_facts(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0056_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION_ID}:"
        f"{PRE_NONOPENING_COUNT_GUARD_COMPATIBILITY_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260905_0056 -> 20260905_0055" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    assert sql.count(module.DOWNGRADE_BLOCKER) == 1
    assert module.UPGRADE_BLOCKER not in sql
    assert f"task.task_type IN {module.NONOPENING_SQL}" in sql
    for fact_table in (
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_scope_count_completions",
    ):
        assert f"FROM public.{fact_table} AS fact" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("downgrade preflight")
        < sql.index(module.DOWNGRADE_BLOCKER)
        < sql.index("readiness downgrade")
        < sql.index("helper downgrade")
        < sql.index("downgrade postflight")
    )


def test_0057_pins_difference_replay_functions_catalog_acl_and_readiness(
    monkeypatch,
) -> None:
    migration_0047 = _load_0047_migration_module()
    module = _load_0057_migration_module()

    assert module.revision == NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION_ID
    assert module.down_revision == (
        PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION
    )
    assert module.PREVIOUS_SCHEMA_REVISION == module.down_revision
    script = ScriptDirectory.from_config(_config("sqlite+pysqlite:///:memory:"))
    revision_row = script.get_revision(module.revision)
    successor_row = script.get_revision(NONOPENING_REVIEW_TERMINAL_STATUS_REVISION_ID)
    assert revision_row is not None
    assert revision_row.down_revision == module.down_revision
    assert successor_row is not None
    assert successor_row.down_revision == module.revision
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))
    assert module.MAXIMUM_LOCK_ROWS == 100_000
    assert module.LOCK_SIGNATURE == (
        "public.rsc_lock_nonopening_stocktake_difference_replay_graph_0057("
        "uuid, uuid, text)"
    )
    assert module.SEAL_SIGNATURE == (
        "public.rsc_guard_stocktake_evidence_seal_0057()"
    )
    assert module.CONTROL_GUARD_SIGNATURE == (
        "public.rsc_guard_nonopening_control_snapshot_0057()"
    )
    assert module.PRINCIPAL_HELPER_BODY_SHA256 == (
        "4b7d3e47541a1de999f33af65d95a697ffd44cfea8930f6fbeb265d4fd727aaf"
    )
    assert module.REVIEW_HELPER_BODY_SHA256 == (
        "ce7dda6f207c9a17bfde049749aa6e689e3f84d9e2c3892c66f17ac15c411ee3"
    )
    assert {
        "document_attachments",
        "files",
        "inventory_ledger_heads",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_transactions",
        "stock_accounts",
        "stocktake_control_snapshot_lines",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_postings",
        "stocktake_review_items",
        "stocktake_reviews",
        "stocktake_round_submissions",
        "stocktake_rounds",
        "stocktake_tasks",
    } <= set(module.LOCK_TABLES)

    def function_body(statement: str) -> str:
        return statement.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    function_catalog = (
        (
            module._postgresql_lock_function_sql(),
            module.LOCK_BODY_SHA256,
            "RETURNS void",
        ),
        (
            module._postgresql_seal_function_sql(),
            module.SEAL_BODY_SHA256,
            "RETURNS trigger",
        ),
        (
            module._postgresql_control_guard_function_sql(),
            module.CONTROL_GUARD_BODY_SHA256,
            "RETURNS trigger",
        ),
    )
    assert module.LOCK_BODY_SHA256 == (
        "7771bc7f9b59465fb47426eaabbff79deeb92967c0c77bbed79c0fe01585596c"
    )
    assert module.SEAL_BODY_SHA256 == (
        "7a5ae0c9fd3117cb784dce34c7882b8301759e846321ac7a6233235c6333222c"
    )
    assert module.CONTROL_GUARD_BODY_SHA256 == (
        "40d7a6223b6250316f7bed15c0dd4749238066865f7581156646c5b89b509249"
    )
    parser = pytest.importorskip("pglast.parser")
    for function_sql, expected_hash, return_shape in function_catalog:
        assert "pg_catalog.coalesce" not in function_sql.lower()
        assert function_sql.count("CREATE FUNCTION public.") == 1
        assert return_shape in function_sql
        assert "LANGUAGE plpgsql" in function_sql
        assert "VOLATILE" in function_sql
        assert "SECURITY DEFINER" in function_sql
        assert "SET search_path = pg_catalog, public" in function_sql
        assert hashlib.sha256(
            function_body(function_sql).encode("utf-8")
        ).hexdigest() == expected_hash
        parser.parse_sql(function_sql)
        parser.parse_plpgsql_json(function_sql)

    lock_body = function_body(module._postgresql_lock_function_sql())
    assert lock_body.index("FROM public.inventory_ledger_heads AS head") < (
        lock_body.index("FROM public.stocktake_tasks AS task")
    )
    assert lock_body.index("rsc_lock_formal_principal_graph_0026") < (
        lock_body.index("FROM public.stock_accounts AS account")
    )
    assert lock_body.index("FROM public.stock_accounts AS account") < (
        lock_body.index("rsc_lock_nonopening_stocktake_review_graph_0032")
    )
    assert lock_body.index("rsc_lock_nonopening_stocktake_review_graph_0032") < (
        lock_body.index(
            "PERFORM transaction_row.id FROM public.inventory_transactions "
            "AS transaction_row"
        )
    )
    assert "graph_count > 100000" in lock_body
    assert "pg_catalog.cardinality(principal_user_ids) NOT BETWEEN 1 AND 1000" in (
        lock_body
    )

    ready_0047 = function_body(
        migration_0047._oam_runtime_ready_function_sql(migration_0047.revision)
    )
    ready_0056 = ready_0047.replace(
        migration_0047.revision,
        module.PREVIOUS_SCHEMA_REVISION,
    )
    ready_0057 = ready_0056.replace(
        module.PREVIOUS_SCHEMA_REVISION,
        module.revision,
    )
    assert hashlib.sha256(ready_0056.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0056
    )
    assert hashlib.sha256(ready_0057.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0057
    )
    assert module.RUNTIME_READY_BODY_SHA256_0057 == (
        "9b97c355d0fcbb5ee4dfcf1e90fd76339b2eafd64f5f5b287a21aa3d364cfdc5"
    )

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_postgresql_prerequisites()
    prerequisite_sql = statements.pop()
    module._verify_postgresql_catalog(
        expected_ready_hash=module.RUNTIME_READY_BODY_SHA256_0057
    )
    catalog_sql = statements.pop()
    module._replace_runtime_ready(
        expected_hash=module.RUNTIME_READY_BODY_SHA256_0056,
        replacement_hash=module.RUNTIME_READY_BODY_SHA256_0057,
        old_revision=module.PREVIOUS_SCHEMA_REVISION,
        new_revision=module.revision,
    )
    readiness_sql = statements.pop()
    module._apply_postgresql_acl()
    acl_sql = tuple(statements)

    for statement in (prerequisite_sql, catalog_sql, readiness_sql, *acl_sql):
        assert "pg_catalog.coalesce" not in statement.lower()
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    for signature, body_hash, argtypes, pronargs in (
        (
            "public.rsc_lock_formal_principal_graph_0026(text[])",
            module.PRINCIPAL_HELPER_BODY_SHA256,
            "text[]",
            "row.pronargs = 1",
        ),
        (
            "public.rsc_lock_nonopening_stocktake_review_graph_0032(uuid, uuid)",
            module.REVIEW_HELPER_BODY_SHA256,
            "uuid, uuid",
            "row.pronargs = 2",
        ),
    ):
        assert signature in prerequisite_sql
        assert body_hash in prerequisite_sql
        assert f"pg_catalog.oidvectortypes(row.proargtypes) = '{argtypes}'" in (
            prerequisite_sql
        )
        assert pronargs in prerequisite_sql
    for prerequisite_fragment in (
        "row.proowner = migrator_oid",
        "row.prokind = 'f'",
        "row.prorettype = 'void'::pg_catalog.regtype",
        "NOT row.proretset",
        "row.proallargtypes IS NULL",
        "row.proargmodes IS NULL",
        "row.pronargdefaults = 0",
        "row.proargdefaults IS NULL",
        "row.provariadic = 0",
        "language_row.lanname = 'plpgsql'",
        "row.provolatile = 'v'",
        "row.prosecdef",
        "NOT row.proisstrict",
        "NOT row.proleakproof",
        "row.proparallel = 'u'",
        "row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]",
        "acl.grantor <> migrator_oid",
        "acl.grantee NOT IN (migrator_oid, api_oid)",
        "acl.is_grantable",
    ):
        assert prerequisite_fragment in prerequisite_sql
    assert prerequisite_sql.count("acl.grantor = migrator_oid") >= 2
    assert prerequisite_sql.count("AND NOT acl.is_grantable) <> 2") >= 2
    assert "trg_document_attachments_stocktake_evidence_guard_0036" in (
        prerequisite_sql
    )
    assert "trigger_row.tgenabled = 'A'" in prerequisite_sql

    for catalog_fragment in (
        "row.oid = lock_oid AND row.proowner = migrator_oid",
        "row.prorettype = 'void'::pg_catalog.regtype",
        "row.pronargs = 3",
        "pg_catalog.oidvectortypes(row.proargtypes) = 'uuid, uuid, text'",
        "'requested_actor_user_id'",
        "row.proallargtypes IS NULL AND row.proargmodes IS NULL",
        "row.pronargdefaults = 0 AND row.proargdefaults IS NULL",
        "row.provariadic = 0",
        "row.provolatile = 'v'",
        "row.prosecdef",
        "NOT row.proisstrict",
        "NOT row.proleakproof",
        "row.proparallel = 'u'",
        "row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]",
        "acl.grantor <> migrator_oid",
        "acl.grantee NOT IN (migrator_oid, api_oid)",
        "acl.grantee IN (migrator_oid, api_oid)",
        "row.prorettype = 'trigger'::pg_catalog.regtype",
        "pg_catalog.has_function_privilege(api_oid, expected.function_oid, 'EXECUTE')",
        "trigger_row.tgenabled = 'A'",
        "trigger_row.tgtype = 7",
        "trigger_row.tgconstraint = 0",
        "NOT trigger_row.tgdeferrable",
        "NOT trigger_row.tginitdeferred",
        "trigger_row.tgconstrrelid = 0",
        "trigger_row.tgconstrindid = 0",
        "trigger_row.tgparentid = 0",
        "trigger_row.tgqual IS NULL",
        "trigger_row.tgoldtable IS NULL",
        "trigger_row.tgnewtable IS NULL",
        "trigger_row.tgnargs = 0",
        "trigger_row.tgattr = ''::pg_catalog.int2vector",
        "row.prorettype = 'boolean'::pg_catalog.regtype",
        "row.proconfig = ARRAY['search_path=pg_catalog']::text[]",
    ):
        assert catalog_fragment in catalog_sql
    for body_hash in (
        module.LOCK_BODY_SHA256,
        module.SEAL_BODY_SHA256,
        module.CONTROL_GUARD_BODY_SHA256,
        module.RUNTIME_READY_BODY_SHA256_0057,
    ):
        assert body_hash in catalog_sql
    assert module.SEAL_TRIGGER in catalog_sql
    assert module.CONTROL_GUARD_TRIGGER in catalog_sql
    assert f"'{module.SEAL_TRIGGER}' >=" in catalog_sql
    assert catalog_sql.count("acl.grantor = migrator_oid") >= 1

    assert module.RUNTIME_READY_BODY_SHA256_0056 in readiness_sql
    assert module.RUNTIME_READY_BODY_SHA256_0057 in readiness_sql
    assert module.PREVIOUS_SCHEMA_REVISION in readiness_sql
    assert module.revision in readiness_sql
    assert "EXECUTE pg_catalog.replace(" in readiness_sql
    assert acl_sql == (
        f"REVOKE EXECUTE ON FUNCTION {module.LOCK_SIGNATURE} FROM PUBLIC",
        f"ALTER FUNCTION {module.LOCK_SIGNATURE} OWNER TO {module.MIGRATION_ROLE}",
        f"REVOKE EXECUTE ON FUNCTION {module.SEAL_SIGNATURE} FROM PUBLIC",
        f"ALTER FUNCTION {module.SEAL_SIGNATURE} OWNER TO {module.MIGRATION_ROLE}",
        f"REVOKE EXECUTE ON FUNCTION {module.CONTROL_GUARD_SIGNATURE} FROM PUBLIC",
        (
            f"ALTER FUNCTION {module.CONTROL_GUARD_SIGNATURE} OWNER TO "
            f"{module.MIGRATION_ROLE}"
        ),
        (
            f"GRANT EXECUTE ON FUNCTION {module.LOCK_SIGNATURE} TO "
            f"{module.PRODUCTION_API_ROLE}"
        ),
        (
            f"REVOKE EXECUTE ON FUNCTION {module.SEAL_SIGNATURE} FROM "
            f"{module.PRODUCTION_API_ROLE}"
        ),
        (
            f"REVOKE EXECUTE ON FUNCTION {module.CONTROL_GUARD_SIGNATURE} FROM "
            f"{module.PRODUCTION_API_ROLE}"
        ),
    )

    with pytest.raises(ValueError, match="unsupported 0057 catalog state"):
        module._verify_postgresql_catalog(expected_ready_hash="0" * 64)


def test_0057_postgresql_offline_upgrade_emits_pinned_catalog_sql(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0057_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION}:"
        f"{NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260905_0056 -> 20260905_0057" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count(f"CREATE FUNCTION public.{module.LOCK_FUNCTION}") == 1
    assert sql.count(f"CREATE FUNCTION public.{module.SEAL_FUNCTION}") == 1
    assert sql.count(f"CREATE FUNCTION public.{module.CONTROL_GUARD_FUNCTION}") == 1
    assert sql.count(f"CREATE TRIGGER {module.SEAL_TRIGGER}") == 1
    assert sql.count(f"CREATE TRIGGER {module.CONTROL_GUARD_TRIGGER}") == 1
    assert sql.count("ENABLE ALWAYS TRIGGER") == 2
    for body_hash in (
        module.PRINCIPAL_HELPER_BODY_SHA256,
        module.REVIEW_HELPER_BODY_SHA256,
        module.LOCK_BODY_SHA256,
        module.SEAL_BODY_SHA256,
        module.CONTROL_GUARD_BODY_SHA256,
        module.RUNTIME_READY_BODY_SHA256_0056,
        module.RUNTIME_READY_BODY_SHA256_0057,
    ):
        assert body_hash in sql
    assert "acl.grantor <> migrator_oid" in sql
    assert "acl.grantor = migrator_oid" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgtype = 7" in sql
    assert "trigger_row.tgnargs = 0" in sql
    assert "trigger_row.tgqual IS NULL" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert module.LOCK_SIGNATURE in sql
    assert sql.index(lock_sql) < sql.index("0057 prerequisite catalog mismatch")
    assert sql.index("0057 prerequisite catalog mismatch") < sql.index(
        f"CREATE FUNCTION public.{module.SEAL_FUNCTION}"
    )
    assert sql.index(f"CREATE FUNCTION public.{module.SEAL_FUNCTION}") < sql.index(
        f"CREATE FUNCTION public.{module.LOCK_FUNCTION}"
    )
    assert sql.index(f"CREATE FUNCTION public.{module.LOCK_FUNCTION}") < sql.index(
        "0057 runtime readiness source mismatch"
    )
    assert sql.index("0057 runtime readiness source mismatch") < sql.index(
        "0057 catalog identity mismatch"
    )
    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(sql)
    parser.parse_plpgsql_json(sql)


def test_0057_offline_modes_fail_closed_when_online_evidence_is_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    postgres_output = io.StringIO()
    postgres_config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=postgres_output,
    )
    with pytest.raises(
        RuntimeError,
        match="0057 downgrade requires an online evidence check",
    ):
        command.downgrade(
            postgres_config,
            f"{NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION_ID}:"
            f"{PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION}",
            sql=True,
        )

    sqlite_output = io.StringIO()
    sqlite_config = _config(
        f"sqlite+pysqlite:///{tmp_path / '0057-offline.db'}",
        output_buffer=sqlite_output,
    )
    with pytest.raises(
        RuntimeError,
        match="0057 SQLite upgrade requires an online connection",
    ):
        command.upgrade(
            sqlite_config,
            f"{PRE_NONOPENING_DIFFERENCE_REPLAY_LOCK_HEAD_REVISION}:"
            f"{NONOPENING_DIFFERENCE_REPLAY_LOCK_REVISION_ID}",
            sql=True,
        )


def test_0057_sqlite_triggers_parse_and_online_downgrade_blocks_sealed_facts(
    monkeypatch,
) -> None:
    module = _load_0057_migration_module()
    seal_sql = module._sqlite_seal_trigger_sql()
    control_sql = module._sqlite_control_guard_trigger_sql()
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE document_attachments ("
                "document_type text, attachment_type text, document_id text)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_control_snapshot_lines (task_id text)"
            )
            connection.exec_driver_sql(seal_sql)
            connection.exec_driver_sql(control_sql)
            triggers = dict(
                connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger' ORDER BY name"
                ).all()
            )
        assert set(triggers) == {
            module.SQLITE_SEAL_TRIGGER,
            module.CONTROL_GUARD_TRIGGER,
        }
        assert "BEFORE INSERT ON document_attachments" in triggers[
            module.SQLITE_SEAL_TRIGGER
        ]
        assert "stocktake_round_submissions" in triggers[
            module.SQLITE_SEAL_TRIGGER
        ]
        assert "sealed non-opening stocktake evidence is immutable" in triggers[
            module.SQLITE_SEAL_TRIGGER
        ]
        assert "BEFORE INSERT ON stocktake_control_snapshot_lines" in triggers[
            module.CONTROL_GUARD_TRIGGER
        ]
        assert module.NONOPENING_SQL in triggers[module.CONTROL_GUARD_TRIGGER]
    finally:
        engine.dispose()

    class _FakeResult:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

    class _FakeBind:
        def __init__(self, row):
            self.row = row
            self.queries: list[str] = []

        def exec_driver_sql(self, statement: str):
            self.queries.append(statement)
            return _FakeResult(self.row)

    blocked_bind = _FakeBind((1,))
    executed: list[str] = []
    monkeypatch.setattr(module.context, "is_offline_mode", lambda: False)
    monkeypatch.setattr(module, "_dialect_name", lambda: "sqlite")
    monkeypatch.setattr(module, "_ensure_sqlite_migration_transaction", lambda: None)
    monkeypatch.setattr(module.op, "get_bind", lambda: blocked_bind)
    monkeypatch.setattr(module.op, "execute", executed.append)
    with pytest.raises(RuntimeError, match=re.escape(module.DOWNGRADE_BLOCKER)):
        module.downgrade()
    assert executed == []
    assert len(blocked_bind.queries) == 1
    assert "stocktake_round_submissions" in blocked_bind.queries[0]
    assert "stocktake_difference_set_completions" in blocked_bind.queries[0]
    assert f"task.task_type IN {module.NONOPENING_SQL}" in blocked_bind.queries[0]

    clear_bind = _FakeBind(None)
    monkeypatch.setattr(module.op, "get_bind", lambda: clear_bind)
    module.downgrade()
    assert executed == [
        f"DROP TRIGGER IF EXISTS {module.SQLITE_SEAL_TRIGGER}",
        f"DROP TRIGGER IF EXISTS {module.CONTROL_GUARD_TRIGGER}",
    ]


def test_0058_pins_review_terminal_function_triggers_and_readiness(
    monkeypatch,
) -> None:
    migration_0032 = _load_0032_migration_module()
    migration_0047 = _load_0047_migration_module()
    migration_0057 = _load_0057_migration_module()
    module = _load_0058_migration_module()

    assert module.revision == NONOPENING_REVIEW_TERMINAL_STATUS_REVISION_ID
    assert module.down_revision == (
        PRE_NONOPENING_REVIEW_TERMINAL_STATUS_HEAD_REVISION
    )
    assert module.PREVIOUS_SCHEMA_REVISION == module.down_revision
    assert migration_0057.revision == module.down_revision
    assert migration_0057.down_revision == (
        NONOPENING_COUNT_GUARD_COMPATIBILITY_REVISION_ID
    )
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))
    assert set(module.LOCK_TABLES) == {
        "alembic_version",
        "role_assignments",
        "roles",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_review_items",
        "stocktake_reviews",
        "stocktake_rounds",
        "stocktake_tasks",
        "users",
    }
    assert module.REVIEW_GRAPH_TRIGGERS == (
        (
            "stocktake_review_items",
            "trg_nonopening_review_graph_item_0032",
        ),
        (
            "stocktake_reviews",
            "trg_nonopening_review_graph_review_0032",
        ),
        (
            "stocktake_tasks",
            "trg_nonopening_review_graph_task_0032",
        ),
    )

    def function_body(statement: str) -> str:
        return statement.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    legacy_sql = migration_0032._postgresql_review_graph_function_sql()
    legacy_body = function_body(legacy_sql)
    assert hashlib.sha256(legacy_body.encode("utf-8")).hexdigest() == (
        module.LEGACY_BODY_SHA256
    )
    assert legacy_body.count(module.LEGACY_SOURCE_FRAGMENT) == 1
    assert module.FIXED_SOURCE_FRAGMENT not in legacy_body
    fixed_body = legacy_body.replace(
        module.LEGACY_SOURCE_FRAGMENT,
        module.FIXED_SOURCE_FRAGMENT,
    )
    assert hashlib.sha256(fixed_body.encode("utf-8")).hexdigest() == (
        module.FIXED_BODY_SHA256
    )
    assert fixed_body.count(module.FIXED_SOURCE_FRAGMENT) == 1
    assert module.LEGACY_SOURCE_FRAGMENT not in fixed_body
    assert fixed_body.replace(
        module.FIXED_SOURCE_FRAGMENT,
        module.LEGACY_SOURCE_FRAGMENT,
    ) == legacy_body

    ready_0047 = function_body(
        migration_0047._oam_runtime_ready_function_sql(migration_0047.revision)
    )
    ready_0057 = ready_0047.replace(
        migration_0047.revision,
        module.PREVIOUS_SCHEMA_REVISION,
    )
    ready_0058 = ready_0057.replace(
        module.PREVIOUS_SCHEMA_REVISION,
        module.revision,
    )
    assert hashlib.sha256(ready_0057.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0057
    )
    assert hashlib.sha256(ready_0058.encode("utf-8")).hexdigest() == (
        module.RUNTIME_READY_BODY_SHA256_0058
    )

    parser = pytest.importorskip("pglast.parser")
    fixed_sql = legacy_sql.replace(
        module.LEGACY_SOURCE_FRAGMENT,
        module.FIXED_SOURCE_FRAGMENT,
    )
    parser.parse_sql(fixed_sql)
    parser.parse_plpgsql_json(fixed_sql)

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_review_catalog(fixed=False, phase="legacy parse probe")
    module._verify_review_catalog(fixed=True, phase="fixed parse probe")
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0057,
        phase="legacy readiness parse probe",
    )
    module._verify_runtime_ready_catalog(
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0058,
        phase="fixed readiness parse probe",
    )
    module._require_no_nonopening_terminal_facts(
        blocker=module.UPGRADE_BLOCKER,
        phase="upgrade fact parse probe",
    )
    module._require_no_nonopening_terminal_facts(
        blocker=module.DOWNGRADE_BLOCKER,
        phase="downgrade fact parse probe",
    )
    module._replace_function_source(
        signature=module.REVIEW_GRAPH_SIGNATURE,
        expected_body_sha256=module.LEGACY_BODY_SHA256,
        expected_replacement_body_sha256=module.FIXED_BODY_SHA256,
        source_fragment=module.LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=module.FIXED_SOURCE_FRAGMENT,
        phase="review parse probe",
    )
    module._replace_function_source(
        signature=module.RUNTIME_READY_SIGNATURE,
        expected_body_sha256=module.RUNTIME_READY_BODY_SHA256_0057,
        expected_replacement_body_sha256=module.RUNTIME_READY_BODY_SHA256_0058,
        source_fragment=module.PREVIOUS_SCHEMA_REVISION,
        replacement_fragment=module.revision,
        phase="readiness parse probe",
    )
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)

    with pytest.raises(ValueError, match="unsupported 0058 review"):
        module._verify_review_catalog(fixed=None, phase="invalid")
    with pytest.raises(ValueError, match="unsupported 0058 readiness"):
        module._verify_runtime_ready_catalog(
            expected_body_sha256="0" * 64,
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0058 non-opening"):
        module._require_no_nonopening_terminal_facts(
            blocker="invalid",
            phase="invalid",
        )
    with pytest.raises(ValueError, match="unsupported 0058 function"):
        module._replace_function_source(
            signature=module.REVIEW_GRAPH_SIGNATURE,
            expected_body_sha256=module.LEGACY_BODY_SHA256,
            expected_replacement_body_sha256=module.FIXED_BODY_SHA256,
            source_fragment=module.FIXED_SOURCE_FRAGMENT,
            replacement_fragment=module.LEGACY_SOURCE_FRAGMENT,
            phase="invalid",
        )


def test_0058_postgresql_offline_upgrade_is_locked_and_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0058_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_NONOPENING_REVIEW_TERMINAL_STATUS_HEAD_REVISION}:"
        f"{NONOPENING_REVIEW_TERMINAL_STATUS_REVISION_ID}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running upgrade 20260905_0057 -> 20260905_0058" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    assert sql.count(module.UPGRADE_BLOCKER) == 1
    assert module.DOWNGRADE_BLOCKER not in sql
    for body_hash in (
        module.LEGACY_BODY_SHA256,
        module.FIXED_BODY_SHA256,
        module.RUNTIME_READY_BODY_SHA256_0057,
        module.RUNTIME_READY_BODY_SHA256_0058,
    ):
        assert body_hash in sql
    assert module.LEGACY_SOURCE_FRAGMENT in sql
    assert module.FIXED_SOURCE_FRAGMENT in sql
    assert "task.status IN ('posted', 'closed')" in sql
    assert "NOT function_row.prosecdef" in sql
    assert "trigger_row.tgenabled = 'A'" in sql
    assert "trigger_row.tgtype = 29" in sql
    assert "trigger_row.tgdeferrable" in sql
    assert "trigger_row.tginitdeferred" in sql
    assert "array_agg(trigger_row.oid ORDER BY trigger_row.oid)" in sql
    for table_name, trigger_name in module.REVIEW_GRAPH_TRIGGERS:
        assert f"'{table_name}'" in sql
        assert f"'{trigger_name}'" in sql
    for forbidden in (
        "CREATE TABLE",
        "ALTER TABLE",
        "DROP TRIGGER",
        "CREATE TRIGGER",
        "GRANT ",
        "REVOKE ",
        "INSERT INTO public.",
        "UPDATE public.",
        "DELETE FROM public.",
    ):
        assert forbidden not in sql
    assert (
        sql.index(lock_sql)
        < sql.index("upgrade preflight")
        < sql.index(module.UPGRADE_BLOCKER)
        < sql.index("review validator upgrade")
        < sql.index("upgrade replacement")
        < sql.index("readiness upgrade")
        < sql.index("upgrade postflight")
    )


def test_0058_postgresql_offline_downgrade_rejects_terminal_facts(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0058_migration_module()
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        f"{NONOPENING_REVIEW_TERMINAL_STATUS_REVISION_ID}:"
        f"{PRE_NONOPENING_REVIEW_TERMINAL_STATUS_HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()
    lock_sql = (
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in module.LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )
    assert "-- Running downgrade 20260905_0058 -> 20260905_0057" in sql
    assert sql.count(lock_sql) == 1
    assert sql.count("EXECUTE replacement_definition") == 2
    assert sql.count(module.DOWNGRADE_BLOCKER) == 1
    assert module.UPGRADE_BLOCKER not in sql
    assert f"task.task_type IN {module.NONOPENING_SQL}" in sql
    assert "task.status IN ('posted', 'closed')" in sql
    assert (
        sql.index(lock_sql)
        < sql.index("downgrade preflight")
        < sql.index(module.DOWNGRADE_BLOCKER)
        < sql.index("readiness downgrade")
        < sql.index("review validator downgrade")
        < sql.index("downgrade postflight")
    )


def test_0059_pins_supply_causality_guards_acl_and_readiness(monkeypatch) -> None:
    migration_0045 = _load_0045_migration_module()
    migration_0058 = _load_0058_migration_module()
    module = _load_0059_migration_module()

    assert module.revision == MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID
    assert module.down_revision == PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION
    assert migration_0058.revision == module.down_revision
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))
    assert module.OWNER_GUARD_TRIGGER < "trg_supply_tasks_guard_0029"
    assert module.SUPPLY_TRIGGER_TABLES == tuple(
        sorted(module.SUPPLY_TRIGGER_TABLES)
    )

    def function_body(statement: str) -> str:
        return statement.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    terminal_sql = migration_0045._terminal_validator_sql()
    assert hashlib.sha256(function_body(terminal_sql).encode()).hexdigest() == (
        module.TERMINAL_BODY_SHA256_0045
    )
    assert terminal_sql.count(module.TERMINAL_LEGACY_FRAGMENT) == 1
    terminal_0059_sql = terminal_sql.replace(
        module.TERMINAL_LEGACY_FRAGMENT, module.TERMINAL_FIXED_FRAGMENT
    )
    assert hashlib.sha256(function_body(terminal_0059_sql).encode()).hexdigest() == (
        module.TERMINAL_BODY_SHA256_0059
    )
    assert "request_row.status <> 'cancelled' AND" in terminal_0059_sql

    projection_sql = migration_0045._projection_validator_sql()
    assert hashlib.sha256(function_body(projection_sql).encode()).hexdigest() == (
        module.PROJECTION_BODY_SHA256_0045
    )
    projection_replacements = (
        (module.PROJECTION_DECLARATION_LEGACY, module.PROJECTION_DECLARATION_FIXED),
        (
            module.PROJECTION_TERMINAL_CALL_LEGACY,
            module.PROJECTION_TERMINAL_CALL_FIXED,
        ),
        (
            module.PROJECTION_APPROVED_RETURN_LEGACY,
            module.PROJECTION_APPROVED_RETURN_FIXED,
        ),
        (
            module.PROJECTION_CANCEL_TERMINAL_CALL_LEGACY,
            module.PROJECTION_CANCEL_TERMINAL_CALL_FIXED,
        ),
    )
    for legacy, fixed in projection_replacements:
        assert projection_sql.count(legacy) == 1
        assert fixed not in projection_sql
        projection_sql = projection_sql.replace(legacy, fixed)
    assert hashlib.sha256(function_body(projection_sql).encode()).hexdigest() == (
        module.PROJECTION_BODY_SHA256_0059
    )
    assert projection_sql.count(
        "rsc_validate_material_request_supply_causality_0059"
    ) == 2

    expected_hashes = {
        "owner": "913d606ff9f47fd05feda92d75ef76477daf6823b71ecdb9c47cabf5355a5398",
        "validator": "ce370ea355224013645f399b2176aceedf92e51c01f8159866a822bb33120f46",
        "dispatcher": "efab0c6eee9c8fbaccb1e334b0dc28d10fc85a4fb097a508b422c30b0cb034ed",
    }
    function_sql = {
        "owner": module._owner_guard_sql(),
        "validator": module._supply_validator_sql(),
        "dispatcher": module._supply_dispatcher_sql(),
    }
    parser = pytest.importorskip("pglast.parser")
    for name, statement in function_sql.items():
        assert hashlib.sha256(function_body(statement).encode()).hexdigest() == (
            expected_hashes[name]
        )
        assert "SECURITY DEFINER" in statement
        assert "SET search_path = pg_catalog, public" in statement
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        parser.parse_plpgsql_json(statement)
    assert "FOR UPDATE" in function_sql["owner"]
    assert "task_count > 10000" in function_sql["owner"]
    assert "supply_ceiling_version := request_row.version - 1" in (
        function_sql["validator"]
    )
    assert "command.operation = 'cancel'" in function_sql["validator"]
    assert "NEW.reference_no IS DISTINCT FROM OLD.reference_no" in (
        function_sql["owner"]
    )
    assert "NEW.expected_date IS DISTINCT FROM OLD.expected_date" in (
        function_sql["owner"]
    )
    assert "command_row.operation = 'cancel_supply_task'" in (
        function_sql["validator"]
    )
    assert "previous_result->'reference_no'" in function_sql["validator"]
    assert "previous_result->'expected_date'" in function_sql["validator"]
    for neutral_value in (
        "not_allocated",
        "not_reserved",
        "not_started",
        "not_signed",
        "not_occurred",
    ):
        assert neutral_value in function_sql["validator"]

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._lock_execution_boundary()
    module._require_empty_supply_graph(module.UPGRADE_BLOCKER)
    module._replace_function_source(
        signature=module.TERMINAL_SIGNATURE,
        expected_hash=module.TERMINAL_BODY_SHA256_0045,
        replacement_hash=module.TERMINAL_BODY_SHA256_0059,
        replacements=((module.TERMINAL_LEGACY_FRAGMENT, module.TERMINAL_FIXED_FRAGMENT),),
    )
    module._create_postgresql_triggers()
    module._grant_runtime_supply_dml()
    module._replace_runtime_ready(
        expected_hash=module.RUNTIME_READY_BODY_SHA256_0058,
        replacement_hash=module.RUNTIME_READY_BODY_SHA256_0059,
        old_revision=module.PREVIOUS_SCHEMA_REVISION,
        new_revision=module.revision,
    )
    module._verify_postgresql_catalog()
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith(("DO ", "CREATE FUNCTION")):
            parser.parse_plpgsql_json(statement)
    catalog_sql = statements[-1]
    assert "acl.grantor <> migrator_oid" in catalog_sql
    assert "function_row.proargnames" in catalog_sql
    assert "trigger_row.tgparentid <> 0" in catalog_sql
    assert "trigger_row.tgdeferrable <> expected.is_constraint" in catalog_sql
    assert "has_table_privilege" in catalog_sql
    assert module.RUNTIME_READY_BODY_SHA256_0059 in catalog_sql

    with pytest.raises(ValueError, match="unsupported 0059 supply graph"):
        module._require_empty_supply_graph("invalid")


def test_0059_postgresql_offline_upgrade_and_downgrade_are_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0059_migration_module()
    upgrade_buffer = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=upgrade_buffer,
    )
    command.upgrade(
        config,
        f"{PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION}:"
        f"{MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID}",
        sql=True,
    )
    upgrade_sql = upgrade_buffer.getvalue()
    assert "-- Running upgrade 20260905_0058 -> 20260905_0059" in upgrade_sql
    assert module.UPGRADE_BLOCKER in upgrade_sql
    assert module.DOWNGRADE_BLOCKER not in upgrade_sql
    assert f"CREATE TRIGGER {module.OWNER_GUARD_TRIGGER}" in upgrade_sql
    assert "GRANT INSERT ON TABLE public.supply_tasks" in upgrade_sql
    assert module.RUNTIME_READY_BODY_SHA256_0059 in upgrade_sql

    downgrade_buffer = io.StringIO()
    downgrade_config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=downgrade_buffer,
    )
    command.downgrade(
        downgrade_config,
        f"{MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID}:"
        f"{PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION}",
        sql=True,
    )
    downgrade_sql = downgrade_buffer.getvalue()
    assert "-- Running downgrade 20260905_0059 -> 20260905_0058" in downgrade_sql
    assert module.DOWNGRADE_BLOCKER in downgrade_sql
    assert module.UPGRADE_BLOCKER not in downgrade_sql
    assert "REVOKE INSERT ON TABLE public.supply_tasks" in downgrade_sql
    assert f"DROP TRIGGER {module.OWNER_GUARD_TRIGGER}" in downgrade_sql
    assert module.RUNTIME_READY_BODY_SHA256_0058 in downgrade_sql


def test_0059_sqlite_guards_and_supply_fact_downgrade_blocker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'supply-0059.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION)
    command.upgrade(config, MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE '%0059'"
                ).all()
            }
        assert triggers == {
            "trg_supply_tasks_state_guard_0059",
            "trg_supply_tasks_transition_guard_0059",
        }
    finally:
        engine.dispose()
    command.downgrade(config, PRE_MATERIAL_REQUEST_SUPPLY_CAUSALITY_HEAD_REVISION)
    command.upgrade(config, MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID)

    module = _load_0059_migration_module()

    class _FakeResult:
        def __init__(self, value: int):
            self.value = value

        def scalar_one(self) -> int:
            return self.value

    class _FakeBind:
        def __init__(self, value: int):
            self.value = value
            self.statements: list[str] = []

        def execute(self, statement):
            self.statements.append(str(statement))
            return _FakeResult(self.value)

    blocked = _FakeBind(1)
    monkeypatch.setattr(module.op, "get_bind", lambda: blocked)
    with pytest.raises(RuntimeError, match=re.escape(module.DOWNGRADE_BLOCKER)):
        module._require_empty_sqlite_supply_graph(module.DOWNGRADE_BLOCKER)
    assert len(blocked.statements) == 1
    assert "audit_events" in blocked.statements[0]
    assert "material_request_commands" in blocked.statements[0]
    assert "state_transition_events" in blocked.statements[0]
    assert "supply_tasks" in blocked.statements[0]
    with pytest.raises(ValueError, match="unsupported 0059 supply graph"):
        module._require_empty_sqlite_supply_graph("invalid")


def test_0060_supply_security_forward_migration_is_exact_and_parseable(
    monkeypatch,
) -> None:
    old = _load_0059_migration_module()
    module = _load_0060_migration_module()
    assert module.revision == MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID
    assert module.down_revision == MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))
    assert module.TASK_GUARD_TRIGGER < old.OWNER_GUARD_TRIGGER

    def body(sql: str) -> str:
        return sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]

    validator = body(old._supply_validator_sql())
    replacements = (
        (module.VALIDATOR_DECLARATION_0059, module.VALIDATOR_DECLARATION_0060),
        (module.VALIDATOR_ACTOR_0059, module.VALIDATOR_ACTOR_0060),
        (module.VALIDATOR_TASK_COUNT_0059, module.VALIDATOR_TASK_COUNT_0060),
        (module.VALIDATOR_TASK_IF_0059, module.VALIDATOR_TASK_IF_0060),
        (module.VALIDATOR_ORDERED_0059, module.VALIDATOR_ORDERED_0060),
        (module.VALIDATOR_SEQUENCE_0059, module.VALIDATOR_SEQUENCE_0060),
    )
    for legacy, fixed in replacements:
        assert validator.count(legacy) == 1
        assert fixed not in validator
        validator = validator.replace(legacy, fixed)
    assert hashlib.sha256(validator.encode()).hexdigest() == (
        module.VALIDATOR_BODY_SHA256_0060
    )
    assert "count(DISTINCT" in validator
    assert "task_version <> expected_task_version" in validator
    assert "role_permission" not in module.VALIDATOR_ACTOR_0060

    dispatcher = body(old._supply_dispatcher_sql())
    assert dispatcher.count(module._dispatcher_body_0059()) == 1
    dispatcher = dispatcher.replace(
        module._dispatcher_body_0059(), module._dispatcher_body_0060()
    )
    assert hashlib.sha256(dispatcher.encode()).hexdigest() == (
        module.DISPATCHER_BODY_SHA256_0060
    )
    assert "supply_fact AND target_request_id IS NULL" in dispatcher
    assert "supply_fact AND NOT has_supply_command" in dispatcher
    assert "ERRCODE = '23514'" in dispatcher

    guard_sql = module._write_guard_sql()
    assert hashlib.sha256(body(guard_sql).encode()).hexdigest() == (
        module.WRITE_GUARD_BODY_SHA256_0060
    )
    for token in (
        "binding.effect = 'allow'",
        "denied_binding.effect = 'deny'",
        "actor_user.is_active",
        "actor_org.org_type = 'headquarters'",
        "NOT role.is_external",
        "identity.status = 'active'",
        "assignment.revoked_at IS NULL",
        "FOR UPDATE",
    ):
        assert token in guard_sql

    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(guard_sql)
    parser.parse_plpgsql_json(guard_sql)
    parser.parse_sql(
        "CREATE FUNCTION public.test_dispatch_0060() RETURNS trigger "
        "LANGUAGE plpgsql AS $$" + dispatcher + "$$"
    )

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._verify_prerequisite_hashes()
    module._create_postgresql_triggers()
    module._audit_existing_graph()
    module._verify_postgresql_catalog()
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith("DO "):
            parser.parse_plpgsql_json(statement)
    catalog = statements[-1]
    assert "acl.grantor <> migrator_oid" in catalog
    assert "trigger_row.tgenabled='A'" in catalog
    assert module.RUNTIME_READY_BODY_SHA256_0060 in catalog


def test_0060_postgresql_offline_upgrade_and_downgrade_are_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0060_migration_module()
    upgrade_buffer = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://offline:offline@localhost/offline",
            output_buffer=upgrade_buffer,
        ),
        f"{MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID}:"
        f"{MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID}",
        sql=True,
    )
    upgrade_sql = upgrade_buffer.getvalue()
    assert "-- Running upgrade 20260905_0059 -> 20260905_0060" in upgrade_sql
    assert f"CREATE TRIGGER {module.COMMAND_GUARD_TRIGGER}" in upgrade_sql
    assert f"CREATE TRIGGER {module.TASK_GUARD_TRIGGER}" in upgrade_sql
    assert module.RUNTIME_READY_BODY_SHA256_0060 in upgrade_sql
    assert module.DOWNGRADE_BLOCKER not in upgrade_sql

    downgrade_buffer = io.StringIO()
    command.downgrade(
        _config(
            "postgresql+psycopg://offline:offline@localhost/offline",
            output_buffer=downgrade_buffer,
        ),
        f"{MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID}:"
        f"{MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID}",
        sql=True,
    )
    downgrade_sql = downgrade_buffer.getvalue()
    assert "-- Running downgrade 20260905_0060 -> 20260905_0059" in downgrade_sql
    assert module.DOWNGRADE_BLOCKER in downgrade_sql
    assert f"DROP TRIGGER {module.COMMAND_GUARD_TRIGGER}" in downgrade_sql
    assert module.RUNTIME_READY_BODY_SHA256_0059 in downgrade_sql


def test_0060_sqlite_orphan_guards_and_downgrade_blocker(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'supply-0060.db'}"
    config = _config(database_url)
    command.upgrade(config, MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            triggers = {
                row[0] for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE '%0060'"
                ).all()
            }
        assert triggers == {
            "trg_supply_audit_owner_guard_0060",
            "trg_supply_state_owner_guard_0060",
        }
    finally:
        engine.dispose()
    command.downgrade(config, MATERIAL_REQUEST_SUPPLY_CAUSALITY_REVISION_ID)
    command.upgrade(config, MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID)

    module = _load_0060_migration_module()
    class _Result:
        def scalar_one(self) -> int:
            return 1
    class _Bind:
        def exec_driver_sql(self, _statement: str) -> _Result:
            return _Result()
    monkeypatch.setattr(module.op, "get_bind", _Bind)
    with pytest.raises(RuntimeError, match=re.escape(module.DOWNGRADE_BLOCKER)):
        module._require_empty_sqlite_supply_graph()


def test_0061_supply_event_key_forward_fix_is_exact_and_parseable(
    monkeypatch,
) -> None:
    migration_0059 = _load_0059_migration_module()
    migration_0060 = _load_0060_migration_module()
    module = _load_0061_migration_module()
    assert module.revision == MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID
    assert module.down_revision == MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID
    assert module.LOCK_TABLES == tuple(sorted(set(module.LOCK_TABLES)))

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
        assert validator.count(legacy) == 1
        validator = validator.replace(legacy, fixed)
    assert hashlib.sha256(validator.encode()).hexdigest() == (
        module.PRIOR_VALIDATOR_BODY_SHA256
    )
    assert validator.count(module.LEGACY_EVENT_KEY_EXPRESSION) == 1
    assert module.FIXED_EVENT_KEY_EXPRESSION not in validator
    fixed_validator = validator.replace(
        module.LEGACY_EVENT_KEY_EXPRESSION,
        module.FIXED_EVENT_KEY_EXPRESSION,
    )
    assert hashlib.sha256(fixed_validator.encode()).hexdigest() == (
        module.FIXED_VALIDATOR_BODY_SHA256
    )
    assert not re.search(
        r"\|\|\s*[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\s*->>",
        fixed_validator,
        re.IGNORECASE,
    )

    parser = pytest.importorskip("pglast.parser")
    parser.parse_sql(
        "CREATE FUNCTION public.test_supply_validator_0061(uuid, bigint) "
        "RETURNS void LANGUAGE plpgsql AS $$" + fixed_validator + "$$"
    )

    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)
    module._lock_execution_boundary()
    module._verify_postgresql_catalog(
        validator_hash=module.PRIOR_VALIDATOR_BODY_SHA256,
        readiness_hash=module.RUNTIME_READY_BODY_SHA256_0060,
    )
    module._replace_function_source(
        signature=module.VALIDATOR_SIGNATURE,
        expected_hash=module.PRIOR_VALIDATOR_BODY_SHA256,
        replacement_hash=module.FIXED_VALIDATOR_BODY_SHA256,
        old=module.LEGACY_EVENT_KEY_EXPRESSION,
        new=module.FIXED_EVENT_KEY_EXPRESSION,
    )
    module._audit_existing_supply_graph()
    module._require_empty_postgresql_supply_graph()
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith("DO "):
            parser.parse_plpgsql_json(statement)
    catalog = statements[1]
    for token in (
        "function_row.proowner <> migrator_oid",
        "function_row.prokind <> 'f'",
        "function_row.proretset",
        "function_row.pronargdefaults <> 0",
        "function_row.provariadic <> 0",
        "function_row.proargmodes IS NOT NULL",
        "acl.grantor <> migrator_oid",
        "trigger_row.tgenabled <> 'A'",
        "trigger_row.tgnargs <> 0",
        "trigger_row.tgqual IS NOT NULL",
        "trigger_row.tgparentid <> 0",
    ):
        assert token in catalog
    audit = statements[3]
    assert "orphan supply fact" in audit
    assert "UNION" in audit and "FROM public.supply_tasks AS task" in audit
    assert "SELECT task.request_id" not in audit
    assert "SELECT line.request_id" in audit
    assert "ON line.id = task.request_line_id" in audit
    assert "JOIN public.material_requests AS request" in audit
    assert "ON audit.aggregate_id = request.id::text" in audit


def test_0061_postgresql_offline_upgrade_and_downgrade_are_fail_closed(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    module = _load_0061_migration_module()
    upgrade_buffer = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://offline:offline@localhost/offline",
            output_buffer=upgrade_buffer,
        ),
        f"{MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID}:"
        f"{MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID}",
        sql=True,
    )
    upgrade_sql = upgrade_buffer.getvalue()
    assert "-- Running upgrade 20260905_0060 -> 20260905_0061" in upgrade_sql
    assert module.LEGACY_EVENT_KEY_EXPRESSION in upgrade_sql
    assert module.FIXED_EVENT_KEY_EXPRESSION in upgrade_sql
    assert module.PRIOR_VALIDATOR_BODY_SHA256 in upgrade_sql
    assert module.FIXED_VALIDATOR_BODY_SHA256 in upgrade_sql
    assert module.RUNTIME_READY_BODY_SHA256_0061 in upgrade_sql
    assert "orphan supply fact" in upgrade_sql
    assert module.DOWNGRADE_BLOCKER not in upgrade_sql

    downgrade_buffer = io.StringIO()
    command.downgrade(
        _config(
            "postgresql+psycopg://offline:offline@localhost/offline",
            output_buffer=downgrade_buffer,
        ),
        f"{MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID}:"
        f"{MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID}",
        sql=True,
    )
    downgrade_sql = downgrade_buffer.getvalue()
    assert "-- Running downgrade 20260905_0061 -> 20260905_0060" in downgrade_sql
    assert module.DOWNGRADE_BLOCKER in downgrade_sql
    assert module.RUNTIME_READY_BODY_SHA256_0060 in downgrade_sql
    assert module.FIXED_VALIDATOR_BODY_SHA256 in downgrade_sql
    assert module.PRIOR_VALIDATOR_BODY_SHA256 in downgrade_sql


def test_0061_sqlite_empty_roundtrip_and_supply_fact_downgrade_blocker(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'supply-0061.db'}"
    config = _config(database_url)
    command.upgrade(config, MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID
        command.downgrade(config, MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID)
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == MATERIAL_REQUEST_SUPPLY_SECURITY_REVISION_ID
        command.upgrade(config, MATERIAL_REQUEST_SUPPLY_EVENT_KEY_REVISION_ID)
    finally:
        engine.dispose()

    module = _load_0061_migration_module()

    class _Result:
        def first(self):
            return (1,)

    class _Bind:
        def execute(self, _statement):
            return _Result()

    monkeypatch.setattr(module.op, "get_bind", _Bind)
    with pytest.raises(RuntimeError, match=re.escape(module.DOWNGRADE_BLOCKER)):
        module._require_empty_sqlite_supply_graph()


def test_0041_sqlite_schema_indexes_and_evidence_triggers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'sms-dispatch-schema.db'}"
    command.upgrade(_config(database_url), HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert [
            column["name"]
            for column in inspector.get_columns("sms_challenge_dispatches")
        ] == [
            "challenge_id",
            "provider",
            "mobile_hash",
            "status",
            "request_sha256",
            "owner_token_hash",
            "provider_reference",
            "claimed_at",
            "lease_expires_at",
            "accepted_at",
            "uncertain_at",
            "expired_at",
            "created_at",
        ]
        assert inspector.get_pk_constraint("sms_challenge_dispatches") == {
            "constrained_columns": ["challenge_id"],
            "name": "pk_sms_challenge_dispatches_0041",
        }
        foreign_keys = inspector.get_foreign_keys("sms_challenge_dispatches")
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["name"] == (
            "fk_sms_challenge_dispatches_challenge_0041"
        )
        assert foreign_keys[0]["constrained_columns"] == ["challenge_id"]
        assert foreign_keys[0]["referred_table"] == "login_challenges"
        assert foreign_keys[0]["referred_columns"] == ["id"]
        assert foreign_keys[0].get("options", {}).get("ondelete") == "RESTRICT"
        assert {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "sms_challenge_dispatches"
            )
        } == {
            "ck_sms_challenge_dispatches_accepted_order",
            "ck_sms_challenge_dispatches_expired_order",
            "ck_sms_challenge_dispatches_lease_order",
            "ck_sms_challenge_dispatches_mobile_hash",
            "ck_sms_challenge_dispatches_owner_hash",
            "ck_sms_challenge_dispatches_request_sha256",
            "ck_sms_challenge_dispatches_state_evidence",
            "ck_sms_challenge_dispatches_status",
            "ck_sms_challenge_dispatches_uncertain_order",
        }

        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("sms_challenge_dispatches")
        }
        assert set(indexes) == {
            "ix_sms_challenge_dispatches_status",
            "ix_sms_challenge_dispatches_unresolved_lease",
            "uq_sms_challenge_dispatches_provider_reference",
            "uq_sms_challenge_dispatches_unresolved_mobile",
        }
        assert indexes["ix_sms_challenge_dispatches_status"]["column_names"] == [
            "status"
        ]
        assert indexes["ix_sms_challenge_dispatches_unresolved_lease"][
            "column_names"
        ] == ["status", "lease_expires_at"]
        assert indexes["uq_sms_challenge_dispatches_provider_reference"][
            "unique"
        ] == 1
        assert indexes["uq_sms_challenge_dispatches_provider_reference"][
            "column_names"
        ] == ["provider", "provider_reference"]
        assert _index_predicate(
            indexes["uq_sms_challenge_dispatches_provider_reference"]
        ) == "provider_reference is not null"
        assert indexes["uq_sms_challenge_dispatches_unresolved_mobile"][
            "unique"
        ] == 1
        assert indexes["uq_sms_challenge_dispatches_unresolved_mobile"][
            "column_names"
        ] == ["provider", "mobile_hash"]
        assert _index_predicate(
            indexes["uq_sms_challenge_dispatches_unresolved_mobile"]
        ) == "status in 'sending', 'uncertain'"

        with engine.connect() as connection:
            trigger_rows = connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'sms_challenge_dispatches' ORDER BY name"
            ).all()
        triggers = {name: _normalized_sql(sql) for name, sql in trigger_rows}
        assert set(triggers) == {
            "trg_sms_challenge_dispatches_delete_0041",
            "trg_sms_challenge_dispatches_insert_0041",
            "trg_sms_challenge_dispatches_update_0041",
        }
        assert "invalid prepared sms dispatch parent" in triggers[
            "trg_sms_challenge_dispatches_insert_0041"
        ]
        assert "invalid sms challenge dispatch mutation" in triggers[
            "trg_sms_challenge_dispatches_update_0041"
        ]
        assert "sms challenge dispatch evidence cannot be deleted" in triggers[
            "trg_sms_challenge_dispatches_delete_0041"
        ]
    finally:
        engine.dispose()


def test_0041_postgresql_offline_sql_covers_preflight_truncate_acl_and_pg_guards(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        f"{PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION}:{HEAD_REVISION}",
        sql=True,
    )
    sql = output.getvalue()

    assert (
        "-- Running upgrade 20260901_0040 -> 20260902_0041" in sql
    )
    assert (
        "LOCK TABLE public.login_challenges, public.audit_events, "
        "public.state_transition_events IN SHARE ROW EXCLUSIVE MODE"
    ) in sql
    for message in (
        "an unexpired provider-managed challenge without an accepted provider reference",
        "a verified or consumed provider-managed challenge has no accepted provider reference",
        "duplicate provider references exist",
        "every accepted legacy challenge must have exactly one well-ordered",
    ):
        assert message in sql
    assert "CREATE FUNCTION public.rsc_guard_sms_challenge_dispatch_0041()" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE" in sql
    assert "BEFORE TRUNCATE" in sql
    assert "SMS challenge dispatch evidence cannot be truncated" in sql
    assert "SMS challenge dispatch evidence cannot be deleted" in sql
    assert "ENABLE ALWAYS TRIGGER trg_sms_challenge_dispatches_guard_0041" in sql
    assert (
        "ENABLE ALWAYS TRIGGER trg_sms_challenge_dispatches_no_truncate_0041"
        in sql
    )
    assert "NEW.mobile_hash !~ '^[0-9a-f]{64}$'" in sql
    assert "NEW.request_sha256 !~ '^[0-9a-f]{64}$'" in sql
    assert "NEW.claimed_at < NEW.created_at" in sql
    assert "NEW.expired_at < NEW.uncertain_at" in sql
    assert "NEW.accepted_at < NEW.expired_at" in sql
    assert (
        "GRANT UPDATE (status, owner_token_hash, provider_reference, claimed_at, "
        "lease_expires_at, accepted_at, uncertain_at, expired_at)"
    ) in sql
    assert "REVOKE UPDATE ON TABLE public.login_challenges" in sql
    assert (
        "GRANT UPDATE (provider_reference, attempts, status, verified_at, "
        "consumed_at) ON TABLE public.login_challenges"
    ) in sql
    assert "GRANT SELECT ON TABLE public.sms_challenge_dispatches" in sql
    assert "REVOKE EXECUTE ON FUNCTION" in sql


def test_0041_offline_downgrade_requires_online_evidence_check(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=io.StringIO(),
    )
    with pytest.raises(RuntimeError, match="online evidence check"):
        command.downgrade(
            config,
            f"{SMS_DISPATCH_OWNERSHIP_REVISION_ID}:"
            f"{PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION}",
            sql=True,
        )


def test_0041_sqlite_enforces_parent_state_machine_immutability_and_no_delete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'sms-dispatch-guards.db'}"
    command.upgrade(_config(database_url), HEAD_REVISION)
    engine = sa.create_engine(database_url)

    @sa.event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    challenge_id = "81000000000040008000000000000001"
    recovery_id = "81000000000040008000000000000002"
    invalid_parent_id = "81000000000040008000000000000003"
    mobile_hash = "1" * 64
    try:
        with engine.begin() as connection:
            _insert_0041_login_challenge(
                connection,
                challenge_id=challenge_id,
                mobile_hash=mobile_hash,
                expires_at="2999-01-01 00:05:00+00:00",
                created_at="2026-09-02 00:00:00+00:00",
            )
            _insert_0041_login_challenge(
                connection,
                challenge_id=recovery_id,
                mobile_hash="2" * 64,
                expires_at="2999-01-01 00:05:00+00:00",
                created_at="2026-09-02 00:00:00+00:00",
            )
            _insert_0041_login_challenge(
                connection,
                challenge_id=invalid_parent_id,
                mobile_hash="3" * 64,
                expires_at="2999-01-01 00:05:00+00:00",
                created_at="2026-09-02 00:00:00+00:00",
            )
            _insert_0041_prepared_dispatch(
                connection,
                challenge_id=challenge_id,
                mobile_hash=mobile_hash,
            )
            _insert_0041_prepared_dispatch(
                connection,
                challenge_id=recovery_id,
                mobile_hash="2" * 64,
            )

        with pytest.raises(sa.exc.IntegrityError, match="invalid prepared"):
            with engine.begin() as connection:
                _insert_0041_prepared_dispatch(
                    connection,
                    challenge_id=invalid_parent_id,
                    mobile_hash="4" * 64,
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                _insert_0041_prepared_dispatch(
                    connection,
                    challenge_id=invalid_parent_id,
                    mobile_hash="3" * 64,
                    request_sha256="A" * 64,
                )
        with pytest.raises(sa.exc.IntegrityError, match="invalid SMS"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE sms_challenge_dispatches SET status = 'accepted', "
                    "owner_token_hash = ?, claimed_at = ?, lease_expires_at = ?, "
                    "accepted_at = ?, provider_reference = 'biz-illegal-direct' "
                    "WHERE challenge_id = ?",
                    (
                        "b" * 64,
                        "2026-09-02 00:01:00+00:00",
                        "2026-09-02 00:06:00+00:00",
                        "2026-09-02 00:02:00+00:00",
                        challenge_id,
                    ),
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE sms_challenge_dispatches SET status = 'sending', "
                    "owner_token_hash = ?, claimed_at = ?, lease_expires_at = ? "
                    "WHERE challenge_id = ?",
                    (
                        "b" * 64,
                        "2026-09-02 00:03:00+00:00",
                        "2026-09-02 00:02:00+00:00",
                        challenge_id,
                    ),
                )

        with engine.begin() as connection:
            _advance_0041_dispatch(
                connection,
                challenge_id=challenge_id,
                target_status="sending",
            )
        for statement, parameters in (
            (
                "UPDATE sms_challenge_dispatches SET owner_token_hash = ? "
                "WHERE challenge_id = ?",
                ("c" * 64, challenge_id),
            ),
            (
                "UPDATE sms_challenge_dispatches SET request_sha256 = ? "
                "WHERE challenge_id = ?",
                ("d" * 64, challenge_id),
            ),
            (
                "UPDATE sms_challenge_dispatches SET status = 'prepared', "
                "owner_token_hash = NULL, claimed_at = NULL, "
                "lease_expires_at = NULL WHERE challenge_id = ?",
                (challenge_id,),
            ),
        ):
            with pytest.raises(sa.exc.IntegrityError, match="invalid SMS"):
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement, parameters)

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE sms_challenge_dispatches SET status = 'accepted', "
                "provider_reference = 'biz-primary', accepted_at = ? "
                "WHERE challenge_id = ?",
                ("2026-09-02 00:02:00+00:00", challenge_id),
            )
        with pytest.raises(sa.exc.IntegrityError, match="invalid SMS"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE sms_challenge_dispatches SET status = 'accepted' "
                    "WHERE challenge_id = ?",
                    (challenge_id,),
                )
        with pytest.raises(sa.exc.IntegrityError, match="cannot be deleted"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM sms_challenge_dispatches WHERE challenge_id = ?",
                    (challenge_id,),
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM login_challenges WHERE id = ?",
                    (challenge_id,),
                )

        with engine.begin() as connection:
            _advance_0041_dispatch(
                connection,
                challenge_id=recovery_id,
                target_status="uncertain",
            )
            connection.exec_driver_sql(
                "UPDATE sms_challenge_dispatches SET status = 'expired', "
                "expired_at = ? WHERE challenge_id = ?",
                ("2026-09-02 00:03:00+00:00", recovery_id),
            )
            connection.exec_driver_sql(
                "UPDATE sms_challenge_dispatches SET status = 'accepted', "
                "provider_reference = 'biz-recovered', accepted_at = ? "
                "WHERE challenge_id = ?",
                ("2026-09-02 00:04:00+00:00", recovery_id),
            )
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT status, provider_reference, uncertain_at, expired_at, "
                "accepted_at FROM sms_challenge_dispatches "
                "WHERE challenge_id = ?",
                (recovery_id,),
            ).one() == (
                "accepted",
                "biz-recovered",
                "2026-09-02 00:02:00+00:00",
                "2026-09-02 00:03:00+00:00",
                "2026-09-02 00:04:00+00:00",
            )
    finally:
        engine.dispose()


def test_0041_sqlite_partial_unique_indexes_block_duplicate_live_send_and_biz_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'sms-dispatch-unique.db'}"
    command.upgrade(_config(database_url), HEAD_REVISION)
    engine = sa.create_engine(database_url)
    challenge_ids = (
        "82000000000040008000000000000001",
        "82000000000040008000000000000002",
        "82000000000040008000000000000003",
        "82000000000040008000000000000004",
    )
    try:
        with engine.begin() as connection:
            for index, challenge_id in enumerate(challenge_ids):
                mobile_hash = "5" * 64 if index < 2 else f"{index + 5:x}" * 64
                _insert_0041_login_challenge(
                    connection,
                    challenge_id=challenge_id,
                    mobile_hash=mobile_hash,
                    expires_at="2999-01-01 00:05:00+00:00",
                    created_at="2026-09-02 00:00:00+00:00",
                )
                _insert_0041_prepared_dispatch(
                    connection,
                    challenge_id=challenge_id,
                    mobile_hash=mobile_hash,
                )
            _advance_0041_dispatch(
                connection,
                challenge_id=challenge_ids[0],
                target_status="sending",
            )
        with pytest.raises(sa.exc.IntegrityError, match="UNIQUE constraint"):
            with engine.begin() as connection:
                _advance_0041_dispatch(
                    connection,
                    challenge_id=challenge_ids[1],
                    target_status="sending",
                )

        with engine.begin() as connection:
            _advance_0041_dispatch(
                connection,
                challenge_id=challenge_ids[2],
                target_status="sending",
            )
            connection.exec_driver_sql(
                "UPDATE sms_challenge_dispatches SET status = 'accepted', "
                "provider_reference = 'biz-duplicate-sentinel', accepted_at = ? "
                "WHERE challenge_id = ?",
                ("2026-09-02 00:02:00+00:00", challenge_ids[2]),
            )
            _advance_0041_dispatch(
                connection,
                challenge_id=challenge_ids[3],
                target_status="sending",
            )
        with pytest.raises(sa.exc.IntegrityError, match="UNIQUE constraint"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE sms_challenge_dispatches SET status = 'accepted', "
                    "provider_reference = 'biz-duplicate-sentinel', "
                    "accepted_at = ? WHERE challenge_id = ?",
                    ("2026-09-02 00:02:00+00:00", challenge_ids[3]),
                )
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT status FROM sms_challenge_dispatches "
                "WHERE challenge_id = ?",
                (challenge_ids[1],),
            ).scalar_one() == "prepared"
            assert connection.exec_driver_sql(
                "SELECT status, provider_reference FROM sms_challenge_dispatches "
                "WHERE challenge_id = ?",
                (challenge_ids[3],),
            ).one() == ("sending", None)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("case", "error_match"),
    [
        (
            "unexpired_without_reference",
            "unexpired provider-managed challenge",
        ),
        (
            "verified_without_reference",
            "verified or consumed provider-managed challenge",
        ),
        (
            "consumed_without_reference",
            "verified or consumed provider-managed challenge",
        ),
        ("duplicate_provider_reference", "duplicate provider references"),
        ("accepted_without_audit", "exactly one well-ordered"),
        ("accepted_audit_before_challenge", "exactly one well-ordered"),
        ("accepted_with_duplicate_audits", "exactly one well-ordered"),
        ("accepted_with_malformed_audit", "exactly one well-ordered"),
    ],
)
def test_0041_sqlite_upgrade_preflight_rejects_ambiguous_legacy_evidence(
    tmp_path: Path,
    monkeypatch,
    case: str,
    error_match: str,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / f'sms-preflight-{case}.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION)
    engine = sa.create_engine(database_url)
    challenge_id = "83000000000040008000000000000001"
    try:
        with engine.begin() as connection:
            if case == "unexpired_without_reference":
                _insert_0041_login_challenge(
                    connection,
                    challenge_id=challenge_id,
                    mobile_hash="a" * 64,
                    expires_at="2999-01-01 00:00:00+00:00",
                    created_at="2026-09-02 00:00:00+00:00",
                )
            elif case in {
                "verified_without_reference",
                "consumed_without_reference",
            }:
                _insert_0041_login_challenge(
                    connection,
                    challenge_id=challenge_id,
                    mobile_hash="a" * 64,
                    status=case.split("_", 1)[0],
                )
            elif case == "duplicate_provider_reference":
                for suffix, mobile_character in (("01", "a"), ("02", "b")):
                    _insert_0041_login_challenge(
                        connection,
                        challenge_id=f"830000000000400080000000000000{suffix}",
                        mobile_hash=mobile_character * 64,
                        provider_reference="biz-legacy-duplicate",
                    )
            else:
                _insert_0041_login_challenge(
                    connection,
                    challenge_id=challenge_id,
                    mobile_hash="a" * 64,
                    provider_reference="biz-legacy-accepted",
                    created_at="2026-09-02 00:00:00+00:00",
                )
                if case == "accepted_audit_before_challenge":
                    _insert_0041_acceptance_audit(
                        connection,
                        challenge_id=challenge_id,
                        event_id="93000000000040008000000000000001",
                        occurred_at="2026-09-01 23:59:59+00:00",
                    )
                elif case == "accepted_with_duplicate_audits":
                    _insert_0041_acceptance_audit(
                        connection,
                        challenge_id=challenge_id,
                        event_id="93000000000040008000000000000001",
                        occurred_at="2026-09-02 00:00:01+00:00",
                    )
                    _insert_0041_acceptance_audit(
                        connection,
                        challenge_id=challenge_id,
                        event_id="93000000000040008000000000000002",
                        occurred_at="2026-09-02 00:00:02+00:00",
                    )
                elif case == "accepted_with_malformed_audit":
                    _insert_0041_acceptance_audit(
                        connection,
                        challenge_id=challenge_id,
                        event_id="93000000000040008000000000000001",
                        occurred_at="2026-09-02 00:00:01+00:00",
                        outcome="unknown",
                    )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match=error_match):
        command.upgrade(config, HEAD_REVISION)
    verification = sa.create_engine(database_url)
    try:
        assert "sms_challenge_dispatches" not in inspect(
            verification
        ).get_table_names()
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION
    finally:
        verification.dispose()


def test_0041_sqlite_backfills_only_proven_legacy_facts_and_downgrades_safely(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'sms-legacy-backfill.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION)
    engine = sa.create_engine(database_url)
    accepted_id = "84000000000040008000000000000001"
    uncertain_id = "84000000000040008000000000000002"
    no_dispatch_id = "84000000000040008000000000000003"
    audit_id = "94000000000040008000000000000001"
    try:
        with engine.begin() as connection:
            _insert_0041_login_challenge(
                connection,
                challenge_id=accepted_id,
                mobile_hash="a" * 64,
                provider_reference="biz-proven-legacy",
                expires_at="2020-01-01 00:05:00+00:00",
                created_at="2020-01-01 00:00:00+00:00",
            )
            _insert_0041_acceptance_audit(
                connection,
                challenge_id=accepted_id,
                event_id=audit_id,
                occurred_at="2020-01-01 00:00:03+00:00",
            )
            _insert_0041_login_challenge(
                connection,
                challenge_id=uncertain_id,
                mobile_hash="b" * 64,
                expires_at="2020-01-01 00:05:00+00:00",
                created_at="2020-01-01 00:00:00+00:00",
            )
            _insert_0041_login_challenge(
                connection,
                challenge_id=no_dispatch_id,
                mobile_hash="c" * 64,
                expires_at="2020-01-01 00:05:00+00:00",
                status="cancelled",
                created_at="2020-01-01 00:00:00+00:00",
            )
            _insert_0041_no_dispatch_transition(
                connection,
                challenge_id=no_dispatch_id,
                event_id="95000000000040008000000000000001",
                reason="mobile_hour_limit",
            )
    finally:
        engine.dispose()

    command.upgrade(config, HEAD_REVISION)
    module = _load_0041_migration_module()
    migrated = sa.create_engine(database_url)
    try:
        with migrated.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT challenge_id, status, request_sha256, owner_token_hash, "
                "provider_reference, claimed_at, lease_expires_at, accepted_at, "
                "uncertain_at, expired_at, created_at "
                "FROM sms_challenge_dispatches ORDER BY challenge_id"
            ).all()
            assert rows == [
                (
                    accepted_id,
                    "accepted",
                    module.LEGACY_ACCEPTED_REQUEST_SHA256,
                    module.LEGACY_ACCEPTED_OWNER_SHA256,
                    "biz-proven-legacy",
                    "2020-01-01 00:00:00+00:00",
                    "2020-01-01 00:00:03+00:00",
                    "2020-01-01 00:00:03+00:00",
                    None,
                    None,
                    "2020-01-01 00:00:00+00:00",
                ),
                (
                    uncertain_id,
                    "expired",
                    module.LEGACY_UNCERTAIN_REQUEST_SHA256,
                    module.LEGACY_UNCERTAIN_OWNER_SHA256,
                    None,
                    "2020-01-01 00:00:00+00:00",
                    "2020-01-01 00:05:00+00:00",
                    None,
                    "2020-01-01 00:05:00+00:00",
                    "2020-01-01 00:05:00+00:00",
                    "2020-01-01 00:00:00+00:00",
                ),
            ]
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sms_challenge_dispatches "
                "WHERE challenge_id = ?",
                (no_dispatch_id,),
            ).scalar_one() == 0
    finally:
        migrated.dispose()

    command.downgrade(config, PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION)
    downgraded = sa.create_engine(database_url)
    try:
        assert "sms_challenge_dispatches" not in inspect(
            downgraded
        ).get_table_names()
        with downgraded.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM login_challenges"
            ).scalar_one() == 3
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_events WHERE id = ?",
                (audit_id,),
            ).scalar_one() == 1
    finally:
        downgraded.dispose()


@pytest.mark.parametrize(
    "dispatch_status",
    ["prepared", "sending", "accepted", "uncertain", "expired"],
)
def test_0041_sqlite_downgrade_blocks_every_nonlegacy_dispatch_state(
    tmp_path: Path,
    monkeypatch,
    dispatch_status: str,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = (
        f"sqlite+pysqlite:///{tmp_path / f'sms-downgrade-{dispatch_status}.db'}"
    )
    config = _config(database_url)
    command.upgrade(config, HEAD_REVISION)
    engine = sa.create_engine(database_url)
    challenge_id = "85000000000040008000000000000001"
    try:
        with engine.begin() as connection:
            _insert_0041_login_challenge(
                connection,
                challenge_id=challenge_id,
                mobile_hash="d" * 64,
                expires_at="2999-01-01 00:05:00+00:00",
                created_at="2026-09-02 00:00:00+00:00",
            )
            _insert_0041_prepared_dispatch(
                connection,
                challenge_id=challenge_id,
                mobile_hash="d" * 64,
            )
            _advance_0041_dispatch(
                connection,
                challenge_id=challenge_id,
                target_status=dispatch_status,
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="non-legacy SMS dispatch facts"):
        command.downgrade(config, PRE_SMS_DISPATCH_OWNERSHIP_HEAD_REVISION)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_MATERIAL_REQUEST_WORK_ORDER_LOCK_HEAD_REVISION
            assert connection.exec_driver_sql(
                "SELECT status FROM sms_challenge_dispatches "
                "WHERE challenge_id = ?",
                (challenge_id,),
            ).scalar_one() == dispatch_status
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'sms_challenge_dispatches'"
            ).scalar_one() == 3
    finally:
        verification.dispose()


def test_0040_kms_pin_table_is_constrained_immutable_and_blocks_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'kms-pins.db'}"
    config = _config(database_url)
    command.upgrade(config, HEAD_REVISION)
    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)

    assert [column["name"] for column in inspector.get_columns("kms_data_key_pins")] == [
        "purpose",
        "kms_key_id",
        "application_key_version",
        "kms_key_version_id",
        "ciphertext_sha256",
        "created_at",
    ]
    assert inspector.get_pk_constraint("kms_data_key_pins")["name"] == (
        "pk_kms_data_key_pins_coordinate_0040"
    )
    assert {item["name"] for item in inspector.get_unique_constraints(
        "kms_data_key_pins"
    )} == {
        "uq_kms_data_key_pins_ciphertext_0040",
        "uq_kms_data_key_pins_purpose_version_0040",
    }
    assert {item["name"] for item in inspector.get_check_constraints(
        "kms_data_key_pins"
    )} == {
        "ck_kms_data_key_pins_coordinates_0040",
        "ck_kms_data_key_pins_purpose_0040",
        "ck_kms_data_key_pins_sha256_0040",
        "ck_kms_data_key_pins_version_0040",
    }

    values = (
        "authentication_idempotency",
        "kms-reviewed-auth-key",
        1,
        "12345678-reviewed-kms-version",
        "a" * 64,
        "2026-09-01 00:00:00+00:00",
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO kms_data_key_pins "
            "(purpose, kms_key_id, application_key_version, "
            "kms_key_version_id, ciphertext_sha256, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            values,
        )
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO kms_data_key_pins "
                "(purpose, kms_key_id, application_key_version, "
                "kms_key_version_id, ciphertext_sha256, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "authentication_idempotency",
                    "kms-different-auth-key",
                    1,
                    "12345678-different-kms-version",
                    "b" * 64,
                    "2026-09-01 00:00:01+00:00",
                ),
            )
    for mutation in (
        "UPDATE kms_data_key_pins SET kms_key_version_id = "
        "'12345678-different-version'",
        "DELETE FROM kms_data_key_pins",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="immutable"):
            with engine.begin() as connection:
                connection.exec_driver_sql(mutation)

    with pytest.raises(RuntimeError, match="encrypted references"):
        command.downgrade(config, PRE_KMS_DATA_KEY_PINS_HEAD_REVISION)


def test_0040_empty_downgrade_succeeds_but_encrypted_auth_reference_blocks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    empty_url = f"sqlite+pysqlite:///{tmp_path / 'kms-empty.db'}"
    empty_config = _config(empty_url)
    command.upgrade(empty_config, HEAD_REVISION)
    command.downgrade(empty_config, PRE_KMS_DATA_KEY_PINS_HEAD_REVISION)
    assert "kms_data_key_pins" not in sa.inspect(
        sa.create_engine(empty_url)
    ).get_table_names()

    encrypted_url = f"sqlite+pysqlite:///{tmp_path / 'kms-auth-ref.db'}"
    encrypted_config = _config(encrypted_url)
    command.upgrade(encrypted_config, HEAD_REVISION)
    encrypted_engine = sa.create_engine(encrypted_url)
    _insert_auth_idempotency_operation(
        encrypted_engine,
        "10000000-0000-4000-8000-000000004000",
        status="completed",
        response_ciphertext=b"sealed-response",
        response_nonce=b"0123456789ab",
        response_sha256="c" * 64,
        encryption_key_version=1,
        http_status=200,
        completed_at="2026-08-30 00:00:01+00:00",
    )

    with pytest.raises(RuntimeError, match="encrypted references"):
        command.downgrade(
            encrypted_config,
            PRE_KMS_DATA_KEY_PINS_HEAD_REVISION,
        )


def test_0040_downgrade_sql_locks_every_reference_source() -> None:
    source = KMS_DATA_KEY_PINS_REVISION.read_text(encoding="utf-8")

    assert "LOCK TABLE public.auth_idempotency_operations" in source
    assert "public.kms_data_key_pins" in source
    assert "public.material_request_revisions" in source
    assert "public.material_requests IN ACCESS EXCLUSIVE MODE" in source
    assert "response_sha256 IS NOT NULL" in source
    assert "UPDATE {TABLE_NAME} SET purpose = purpose WHERE 0" in source


def test_missing_database_url_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    config = Config(str(ALEMBIC_INI))

    with pytest.raises(RuntimeError, match="refusing to guess"):
        command.current(config)


def test_production_migration_rejects_non_postgresql_target(monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    monkeypatch.setenv("OAM_ENVIRONMENT", "production")
    config = _config("sqlite+pysqlite:///:memory:")

    with pytest.raises(RuntimeError, match=r"postgresql\+psycopg database URL"):
        command.current(config)


def test_production_migration_rejects_runtime_or_shared_database_role(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    monkeypatch.setenv("OAM_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "OAM_DATABASE_EXPECTED_MIGRATION_ROLE", "star_oam_migrator"
    )
    monkeypatch.setenv("OAM_DATABASE_EXPECTED_RUNTIME_ROLE", "star_oam_api")
    config = _config(
        "postgresql+psycopg://star_oam_api:local-only@127.0.0.1:1/star_oam"
    )

    with pytest.raises(RuntimeError, match="must connect as the migration role"):
        command.current(config)


def test_production_migration_refuses_offline_sql(monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    monkeypatch.setenv("OAM_ENVIRONMENT", "production")
    monkeypatch.setenv(
        "OAM_DATABASE_EXPECTED_MIGRATION_ROLE", "star_oam_migrator"
    )
    monkeypatch.setenv("OAM_DATABASE_EXPECTED_RUNTIME_ROLE", "star_oam_api")
    config = _config(
        "postgresql+psycopg://star_oam_migrator:local-only@localhost/star_oam"
    )

    with pytest.raises(RuntimeError, match="online migration path"):
        command.upgrade(config, "head", sql=True)


def _seed_0037_posted_task_for_0038_preflight(
    connection,
    *,
    task_version: int = 7,
    posting_version: int = 7,
    task_posted_at: str = "2026-09-01 08:00:00+00:00",
    task_updated_at: str = "2026-09-01 08:00:00+00:00",
    posting_at: str = "2026-09-01 08:00:00+00:00",
    closed_at: str | None = None,
    with_posting: bool = True,
) -> str:
    task_id = "98000000000040008000000000000001"
    trigger_names = connection.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND tbl_name IN ('stocktake_tasks', 'stocktake_posting_completions')"
    ).scalars().all()
    for trigger_name in trigger_names:
        connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
    connection.exec_driver_sql(
        "INSERT INTO stocktake_tasks "
        "(id, task_no, task_type, region_org_id, status, blind_count, "
        "cutoff_ledger_cursor, cutoff_at, scope_manifest_sha256, "
        "snapshot_manifest_sha256, current_round_no, created_by_user_id, "
        "frozen_at, submitted_at, posted_at, closed_at, version, note, "
        "created_at, updated_at) VALUES "
        "(?, '0038-PREFLIGHT-POSTED', 'full', ?, 'posted', 0, 0, ?, ?, ?, "
        "1, ?, ?, ?, ?, ?, ?, '', ?, ?)",
        (
            task_id,
            "98000000000040008000000000000002",
            "2026-09-01 07:00:00+00:00",
            "a" * 64,
            "b" * 64,
            "98000000-0000-4000-8000-000000000003",
            "2026-09-01 07:30:00+00:00",
            "2026-09-01 07:45:00+00:00",
            task_posted_at,
            closed_at,
            task_version,
            "2026-09-01 07:00:00+00:00",
            task_updated_at,
        ),
    )
    if with_posting:
        connection.exec_driver_sql(
            "INSERT INTO stocktake_posting_completions "
            "(id, task_id, effective_approval_completion_id, terminal_round_id, "
            "expected_task_version, posted_task_version, scope_count, "
            "difference_count, accepted_difference_count, no_adjustment_count, "
            "transaction_count, movement_count, total_quantity, "
            "first_ledger_cursor, last_ledger_cursor, approval_manifest_sha256, "
            "posting_manifest_sha256, request_sha256, idempotency_key_hash, "
            "posted_by_user_id, posted_by_person_id, posted_role_assignment_id, "
            "authorization_version, role_code, scope_type, scope_id_snapshot, "
            "authorization_sha256, posted_at, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, 1, 0, 0, 0, 0, 0, 0, NULL, NULL, ?, ?, ?, ?, "
            "?, ?, ?, 1, 'admin', 'national', '*', ?, ?, ?)",
            (
                "98000000000040008000000000000004",
                task_id,
                "98000000000040008000000000000005",
                "98000000000040008000000000000006",
                posting_version - 1,
                posting_version,
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "98000000-0000-4000-8000-000000000003",
                "98000000000040008000000000000007",
                "98000000000040008000000000000008",
                "1" * 64,
                posting_at,
                posting_at,
            ),
        )
    return task_id


def test_0038_sqlite_upgrade_preflight_accepts_exact_posted_coordinate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0038-preflight-valid.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _seed_0037_posted_task_for_0038_preflight(connection)
    finally:
        engine.dispose()

    command.upgrade(config, NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_REVISION_ID)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_REVISION_ID
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM stocktake_close_transition_acks"
            ).scalar_one() == 0
    finally:
        verification.dispose()


def test_0038_postgresql_offline_upgrade_locks_and_guards_exact_event_coordinates(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        "20260901_0037:20260901_0038",
        sql=True,
    )
    sql = output.getvalue()
    assert (
        "public.inventory_ledger_heads, public.audit_events, "
        "public.state_transition_events"
    ) in sql
    assert "task.version = posting.posted_task_version" in sql
    assert "task.posted_at = posting.posted_at" in sql
    assert "task.updated_at = posting.posted_at" in sql
    assert "CREATE FUNCTION public.rsc_guard_nonopening_stocktake_close_event_0038()" in sql
    assert (
        "CREATE TRIGGER trg_audit_events_nonopening_stocktake_close_guard_0038 "
        "BEFORE INSERT ON public.audit_events"
    ) in sql
    assert (
        "CREATE TRIGGER trg_state_events_nonopening_stocktake_close_guard_0038 "
        "BEFORE INSERT ON public.state_transition_events"
    ) in sql
    assert (
        "ALTER TABLE public.audit_events ENABLE ALWAYS TRIGGER "
        "trg_audit_events_nonopening_stocktake_close_guard_0038"
    ) in sql
    assert "existing_coordinate_count <> 0" in sql
    assert "NEW.before_jsonb IS DISTINCT FROM" in sql
    assert "NEW.after_jsonb IS DISTINCT FROM" in sql
    assert "NEW.metadata_jsonb IS DISTINCT FROM" in sql
    assert "FOR UPDATE" in sql
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "public.rsc_guard_nonopening_stocktake_close_event_0038() "
        "FROM star_oam_api"
    ) in sql


@pytest.mark.parametrize(
    "drift",
    (
        "missing_posting",
        "version",
        "posted_at",
        "updated_at",
        "closed_at",
        "reserved_state_reason",
    ),
)
def test_0038_sqlite_upgrade_preflight_rejects_posted_coordinate_drift(
    tmp_path: Path, monkeypatch, drift: str
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = (
        f"sqlite+pysqlite:///{tmp_path / ('0038-preflight-' + drift + '.db')}"
    )
    config = _config(database_url)
    command.upgrade(config, PRE_NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            task_id = _seed_0037_posted_task_for_0038_preflight(
                connection,
                with_posting=drift != "missing_posting",
                posting_version=8 if drift == "version" else 7,
                task_posted_at=(
                    "2026-09-01 08:01:00+00:00"
                    if drift == "posted_at"
                    else "2026-09-01 08:00:00+00:00"
                ),
                task_updated_at=(
                    "2026-09-01 08:01:00+00:00"
                    if drift == "updated_at"
                    else "2026-09-01 08:00:00+00:00"
                ),
                closed_at=(
                    "2026-09-01 08:02:00+00:00"
                    if drift == "closed_at"
                    else None
                ),
            )
            if drift == "reserved_state_reason":
                state_triggers = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'state_transition_events'"
                ).scalars().all()
                for trigger_name in state_triggers:
                    connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
                connection.exec_driver_sql(
                    "INSERT INTO state_transition_events "
                    "(id, aggregate_type, aggregate_id, from_status, to_status, "
                    "reason, actor_id, idempotency_key, occurred_at, "
                    "metadata_jsonb, created_at) VALUES "
                    "(?, 'stocktake_task', ?, 'posted', 'approved', ?, ?, ?, ?, "
                    "'{}', ?)",
                    (
                        "98000000000040008000000000000009",
                        str(uuid.UUID(hex=task_id)),
                        "nonopening_stocktake_closed_after_internal_reconciliation",
                        "98000000-0000-4000-8000-000000000003",
                        "0038-preflight-reserved-state-reason",
                        "2026-09-01 08:01:00+00:00",
                        "2026-09-01 08:01:00+00:00",
                    ),
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="0038 preflight failed"):
        command.upgrade(
            config,
            NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_REVISION_ID,
        )
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_NONOPENING_STOCKTAKE_CLOSE_RECONCILIATION_HEAD_REVISION
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table' "
                "AND name = 'stocktake_close_transition_acks'"
            ).scalar_one() == 0
    finally:
        verification.dispose()


def test_upgrade_head_matches_current_orm_and_downgrades(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    migrated_url = f"sqlite+pysqlite:///{tmp_path / 'migrated.db'}"
    model_url = f"sqlite+pysqlite:///{tmp_path / 'model.db'}"
    config = _config(migrated_url)

    command.upgrade(config, "head")
    _create_model_schema_in_subprocess(model_url)

    migrated = _schema_snapshot(migrated_url)
    expected = _schema_snapshot(model_url)
    expected["stocktake_postings"]["indexes"].append(
        (
            OPENING_TERMINAL_SECURITY_INDEX,
            ("task_id",),
            True,
            "posting_kind = 'opening'",
        )
    )
    expected["stocktake_postings"]["indexes"].sort()
    assert set(migrated) == EXPECTED_TABLES
    assert migrated == expected

    engine = sa.create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == HEAD_REVISION
            roles = connection.exec_driver_sql(
                "SELECT code, is_external FROM roles ORDER BY code"
            ).all()
            assert roles == [
                ("admin", False),
                ("provincial_manager", False),
                ("star_headquarters_approver", True),
                ("technician", False),
            ]
            permissions = connection.exec_driver_sql(
                "SELECT resource, action FROM permissions "
                "ORDER BY resource, action"
            ).all()
            assert permissions == EXPECTED_PERMISSIONS
            role_permission_rows = connection.exec_driver_sql(
                "SELECT roles.code, permissions.resource, permissions.action, "
                "role_permissions.effect "
                "FROM role_permissions "
                "JOIN roles ON roles.id = role_permissions.role_id "
                "JOIN permissions ON permissions.id = role_permissions.permission_id"
            ).all()
            assert len(role_permission_rows) == 70
            assert {row[3] for row in role_permission_rows} == {"allow"}
            actual_role_permissions = {
                role_code: {
                    (resource, action)
                    for row_role, resource, action, _ in role_permission_rows
                    if row_role == role_code
                }
                for role_code in EXPECTED_ROLE_PERMISSIONS
            }
            assert actual_role_permissions == EXPECTED_ROLE_PERMISSIONS
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM users"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM people"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_identities"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM role_assignments"
            ).scalar_one() == 0
            authorization_head = connection.exec_driver_sql(
                "SELECT stream_key, last_event_id, last_hash, version "
                "FROM audit_chain_heads WHERE id = ?",
                ("30000000000040008000000000000001",),
            ).one()
            assert authorization_head == ("authorization", None, None, 0)
            authentication_head = connection.exec_driver_sql(
                "SELECT stream_key, last_event_id, last_hash, version "
                "FROM audit_chain_heads WHERE id = ?",
                ("30000000000040008000000000000002",),
            ).one()
            assert authentication_head == ("authentication", None, None, 0)
            inventory_audit_head = connection.exec_driver_sql(
                "SELECT stream_key, last_event_id, last_hash, version "
                "FROM audit_chain_heads WHERE id = ?",
                ("30000000000040008000000000000003",),
            ).one()
            assert inventory_audit_head == ("inventory", None, None, 0)
            inventory_ledger_head = connection.exec_driver_sql(
                "SELECT stream_key, next_cursor FROM inventory_ledger_heads "
                "WHERE id = ?",
                ("40000000000040008000000000000001",),
            ).one()
            assert inventory_ledger_head == ("inventory", 1)
            for empty_table in INVENTORY_LEDGER_TABLES - {
                "inventory_ledger_heads"
            }:
                assert connection.exec_driver_sql(
                    f"SELECT count(*) FROM {empty_table}"
                ).scalar_one() == 0
            for empty_table in OPENING_STOCKTAKE_TABLES:
                assert connection.exec_driver_sql(
                    f"SELECT count(*) FROM {empty_table}"
                ).scalar_one() == 0
            login_challenge_indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspect(engine).get_indexes("login_challenges")
            }
            assert login_challenge_indexes[
                "ix_login_challenges_requested_ip_created"
            ] == ("requested_ip_hash", "created_at")
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_idempotency_operations"
            ).scalar_one() == 0
            operation_indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspect(engine).get_indexes(
                    "auth_idempotency_operations"
                )
            }
            assert operation_indexes[
                "ix_auth_idempotency_operations_scope_status_expires"
            ] == ("scope_hash", "status", "expires_at")
            assert operation_indexes[
                "ix_auth_idempotency_operations_status_expires"
            ] == ("status", "expires_at")
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_login_rate_limit_buckets"
            ).scalar_one() == 0
            rate_limit_indexes = {
                index["name"]: tuple(index["column_names"])
                for index in inspect(engine).get_indexes(
                    "auth_login_rate_limit_buckets"
                )
            }
            assert rate_limit_indexes[
                "ix_auth_login_rate_limit_buckets_cleanup_after"
            ] == ("cleanup_after",)
            assert rate_limit_indexes[
                "ix_auth_login_rate_limit_buckets_lookup"
            ] == (
                "operation_type",
                "scope_type",
                "hash_version",
                "scope_hmac",
                "window_started_at",
            )
            session_indexes = {
                index["name"]: index
                for index in inspect(engine).get_indexes("auth_sessions")
            }
            active_device_index = session_indexes[
                "uq_auth_sessions_active_device_family"
            ]
            assert tuple(active_device_index["column_names"]) == (
                "user_id",
                "client_type",
                "device_id",
            )
            assert bool(active_device_index["unique"]) is True
            assert _index_predicate(active_device_index) == "revoked_at is null"
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    assert _schema_snapshot(migrated_url) == {}


def test_active_device_family_migration_is_repeatable_and_downgrade_is_data_safe(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'active-device-family.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0006")

    user_id = "00000000-0000-0000-0000-000000000071"
    engine = sa.create_engine(database_url)
    try:
        _insert_legacy_user(engine, user_id=user_id, mobile="13800000071")
        _insert_auth_session(
            engine,
            session_id="71000000000040008000000000000001",
            user_id=user_id,
            refresh_token_hash="1" * 64,
        )
        _insert_auth_session(
            engine,
            session_id="71000000000040008000000000000002",
            user_id=user_id,
            refresh_token_hash="2" * 64,
            revoked_at="2026-08-30 01:00:00+00:00",
        )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    verification_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(verification_engine)
        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("auth_sessions")
        }
        active_index = indexes["uq_auth_sessions_active_device_family"]
        assert tuple(active_index["column_names"]) == (
            "user_id",
            "client_type",
            "device_id",
        )
        assert bool(active_index["unique"]) is True
        assert _index_predicate(active_index) == "revoked_at is null"
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT id, revoked_at FROM auth_sessions ORDER BY id"
            ).all() == [
                ("71000000000040008000000000000001", None),
                (
                    "71000000000040008000000000000002",
                    "2026-08-30 01:00:00+00:00",
                ),
            ]
        with pytest.raises(sa.exc.IntegrityError):
            _insert_auth_session(
                verification_engine,
                session_id="71000000000040008000000000000003",
                user_id=user_id,
                refresh_token_hash="3" * 64,
            )
        _insert_auth_session(
            verification_engine,
            session_id="71000000000040008000000000000004",
            user_id=user_id,
            refresh_token_hash="4" * 64,
            client_type="miniprogram",
        )
    finally:
        verification_engine.dispose()

    command.downgrade(config, "20260830_0006")
    command.downgrade(config, "20260830_0006")

    downgraded_engine = sa.create_engine(database_url)
    try:
        assert "uq_auth_sessions_active_device_family" not in {
            index["name"]
            for index in inspect(downgraded_engine).get_indexes("auth_sessions")
        }
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0006"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_sessions"
            ).scalar_one() == 3
        _insert_auth_session(
            downgraded_engine,
            session_id="71000000000040008000000000000005",
            user_id=user_id,
            refresh_token_hash="5" * 64,
        )
    finally:
        downgraded_engine.dispose()


def test_active_device_family_upgrade_rejects_duplicate_unrevoked_sessions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'duplicate-device-family.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0006")

    user_id = "00000000-0000-0000-0000-000000000072"
    engine = sa.create_engine(database_url)
    try:
        _insert_legacy_user(engine, user_id=user_id, mobile="13800000072")
        for ordinal in (1, 2):
            _insert_auth_session(
                engine,
                session_id=f"7200000000004000800000000000000{ordinal}",
                user_id=user_id,
                refresh_token_hash=str(ordinal) * 64,
            )
    finally:
        engine.dispose()

    with pytest.raises(
        RuntimeError,
        match="duplicate active authentication device families",
    ):
        command.upgrade(config, "head")

    verification_engine = sa.create_engine(database_url)
    try:
        assert "uq_auth_sessions_active_device_family" not in {
            index["name"]
            for index in inspect(verification_engine).get_indexes("auth_sessions")
        }
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0006"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL"
            ).scalar_one() == 2
    finally:
        verification_engine.dispose()


def test_empty_active_device_family_upgrade_and_downgrade_are_repeatable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'empty-device-family.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0006")

    command.upgrade(config, "head")
    command.upgrade(config, "head")
    command.downgrade(config, "20260830_0006")
    command.downgrade(config, "20260830_0006")

    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0006"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_sessions"
            ).scalar_one() == 0
        assert "uq_auth_sessions_active_device_family" not in {
            index["name"] for index in inspect(engine).get_indexes("auth_sessions")
        }
    finally:
        engine.dispose()


def test_auth_idempotency_ledger_enforces_encrypted_evidence_shape(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'auth-idempotency.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("auth_idempotency_operations")
        }
        assert set(columns) == {
            "id",
            "operation_type",
            "client_type",
            "idempotency_key_hash",
            "scope_hash",
            "request_hmac",
            "status",
            "response_ciphertext",
            "response_nonce",
            "response_sha256",
            "encryption_key_version",
            "http_status",
            "auth_session_id",
            "input_refresh_token_id",
            "output_refresh_token_id",
            "expires_at",
            "completed_at",
            "updated_at",
            "created_at",
        }
        assert not {
            "request_json",
            "request_payload",
            "response_json",
            "response_plaintext",
        } & set(columns)
        assert columns["id"]["nullable"] is False
        assert columns["response_ciphertext"]["nullable"] is True
        assert columns["response_nonce"]["nullable"] is True
        assert columns["expires_at"]["nullable"] is False

        constraints = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "auth_idempotency_operations"
            )
        }
        assert {
            "ck_auth_idempotency_operations_type",
            "ck_auth_idempotency_operations_client_type",
            "ck_auth_idempotency_operations_status",
            "ck_auth_idempotency_operations_request_hashes",
            "ck_auth_idempotency_operations_response_evidence",
            "ck_auth_idempotency_operations_output_token_status",
            "ck_auth_idempotency_operations_time_order",
        } <= constraints
        assert {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(
                "auth_idempotency_operations"
            )
        } == {("idempotency_key_hash",)}

        _insert_auth_idempotency_operation(
            engine, "51000000000040008000000000000001"
        )
        _insert_auth_idempotency_operation(
            engine,
            "51000000000040008000000000000002",
            operation_type="session_refresh",
            status="completed",
            response_ciphertext=b"encrypted-success",
            response_nonce=b"n" * 12,
            response_sha256="c" * 64,
            encryption_key_version=1,
            http_status=200,
            completed_at="2026-08-30 00:00:01+00:00",
            output_refresh_token_id="51000000000040008000000000001002",
        )
        _insert_auth_idempotency_operation(
            engine,
            "51000000000040008000000000000003",
            operation_type="wechat_login",
            client_type="miniprogram",
            status="failed",
            response_ciphertext=b"encrypted-failure",
            response_nonce=b"n" * 12,
            response_sha256="d" * 64,
            encryption_key_version=2,
            http_status=401,
            completed_at="2026-08-30 00:00:01+00:00",
        )

        duplicate_hash = hashlib.sha256(
            b"51000000000040008000000000000001"
        ).hexdigest()
        invalid_rows = (
            {"operation_type": "password_login"},
            {"client_type": "legacy_unknown"},
            {"status": "expired"},
            {"idempotency_key_hash": "e" * 63},
            {"scope_hash": "a" * 63},
            {"request_hmac": "b" * 63},
            {"response_ciphertext": b"partial-pending-envelope"},
            {
                "status": "completed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": None,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 200,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "completed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 11,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 200,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "failed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 63,
                "encryption_key_version": 1,
                "http_status": 401,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "failed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 64,
                "encryption_key_version": 0,
                "http_status": 401,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "failed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 200,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "completed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 500,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "failed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 600,
                "completed_at": "2026-08-30 00:00:01+00:00",
            },
            {
                "status": "failed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 401,
                "completed_at": "2026-08-30 00:00:01+00:00",
                "output_refresh_token_id": "51000000000040008000000000001003",
            },
            {"expires_at": "2026-08-30 00:00:00+00:00"},
            {
                "status": "failed",
                "response_ciphertext": b"ciphertext",
                "response_nonce": b"n" * 12,
                "response_sha256": "f" * 64,
                "encryption_key_version": 1,
                "http_status": 401,
                "completed_at": "2026-08-29 23:59:59+00:00",
            },
            {"idempotency_key_hash": duplicate_hash},
        )
        for sequence, overrides in enumerate(invalid_rows, start=100):
            with pytest.raises(sa.exc.IntegrityError):
                _insert_auth_idempotency_operation(
                    engine,
                    f"{sequence:032x}",
                    **overrides,
                )
    finally:
        engine.dispose()


def test_empty_auth_idempotency_downgrade_removes_only_0006_table(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'empty-auth-idem.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "20260830_0005")

    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "auth_idempotency_operations" not in inspector.get_table_names()
        assert "ix_login_challenges_requested_ip_created" in {
            index["name"]
            for index in inspector.get_indexes("login_challenges")
        }
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0005"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_chain_heads "
                "WHERE stream_key = 'authentication'"
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_auth_idempotency_downgrade_fails_closed_when_ledger_is_nonempty(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'used-auth-idem.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    try:
        _insert_auth_idempotency_operation(
            engine, "52000000000040008000000000000001"
        )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="operation ledger is not empty"):
        command.downgrade(config, "20260830_0005")

    verification_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(verification_engine)
        assert "auth_idempotency_operations" in inspector.get_table_names()
        assert {
            "ix_auth_idempotency_operations_scope_status_expires",
            "ix_auth_idempotency_operations_status_expires",
        } <= {
            index["name"]
            for index in inspector.get_indexes(
                "auth_idempotency_operations"
            )
        }
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0006"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM auth_idempotency_operations"
            ).scalar_one() == 1
    finally:
        verification_engine.dispose()


def test_current_role_assignment_scope_is_unique_until_explicitly_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'role-assignment-guard.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")

    user_id = "00000000-0000-0000-0000-000000000041"
    first_assignment_id = "30000000000040008000000000000001"
    second_assignment_id = "30000000000040008000000000000002"
    historical_assignment_id = "30000000000040008000000000000003"
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users "
                "(id, mobile, name, password_hash, role, province, is_active, "
                "require_password_change, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    "13800000041",
                    "role guard sentinel",
                    "not-a-real-password-hash",
                    "technician",
                    None,
                    True,
                    False,
                    "2026-08-30 00:00:00+00:00",
                    "2026-08-30 00:00:00+00:00",
                ),
            )
            technician_role_id = connection.exec_driver_sql(
                "SELECT id FROM roles WHERE code = 'technician'"
            ).scalar_one()
            connection.exec_driver_sql(
                "INSERT INTO role_assignments "
                "(id, user_id, role_id, scope_type, scope_id, valid_from, "
                "valid_to, status, assigned_by, reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    first_assignment_id,
                    user_id,
                    technician_role_id,
                    "person",
                    "00000000-0000-4000-8000-000000000041",
                    "2026-01-01 00:00:00+00:00",
                    "2026-02-01 00:00:00+00:00",
                    "active",
                    user_id,
                    "stale active sentinel",
                    "2026-01-01 00:00:00+00:00",
                    "2026-01-01 00:00:00+00:00",
                ),
            )

        # valid_to is deliberately in the past.  Status is still active, so a
        # replacement must fail closed until an explicit lifecycle transition.
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO role_assignments "
                    "(id, user_id, role_id, scope_type, scope_id, valid_from, "
                    "valid_to, status, assigned_by, reason, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        second_assignment_id,
                        user_id,
                        technician_role_id,
                        "person",
                        "00000000-0000-4000-8000-000000000041",
                        "2026-08-30 00:00:00+00:00",
                        None,
                        "scheduled",
                        user_id,
                        "replacement sentinel",
                        "2026-08-30 00:00:00+00:00",
                        "2026-08-30 00:00:00+00:00",
                    ),
                )

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE role_assignments SET status = 'expired' WHERE id = ?",
                (first_assignment_id,),
            )
            connection.exec_driver_sql(
                "INSERT INTO role_assignments "
                "(id, user_id, role_id, scope_type, scope_id, valid_from, "
                "valid_to, status, assigned_by, reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    second_assignment_id,
                    user_id,
                    technician_role_id,
                    "person",
                    "00000000-0000-4000-8000-000000000041",
                    "2026-08-30 00:00:00+00:00",
                    None,
                    "scheduled",
                    user_id,
                    "replacement sentinel",
                    "2026-08-30 00:00:00+00:00",
                    "2026-08-30 00:00:00+00:00",
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO role_assignments "
                "(id, user_id, role_id, scope_type, scope_id, valid_from, "
                "valid_to, status, assigned_by, reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    historical_assignment_id,
                    user_id,
                    technician_role_id,
                    "person",
                    "00000000-0000-4000-8000-000000000041",
                    "2025-01-01 00:00:00+00:00",
                    "2025-02-01 00:00:00+00:00",
                    "expired",
                    user_id,
                    "older history sentinel",
                    "2025-01-01 00:00:00+00:00",
                    "2025-01-01 00:00:00+00:00",
                ),
            )
    finally:
        engine.dispose()

    command.downgrade(config, "20260830_0003")

    inspection_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(inspection_engine)
        assert "uq_role_assignments_current_scope" not in {
            index["name"] for index in inspector.get_indexes("role_assignments")
        }
    finally:
        inspection_engine.dispose()
    downgraded_engine = sa.create_engine(database_url)
    try:
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0003"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM permissions"
            ).scalar_one() == 12
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM role_permissions"
            ).scalar_one() == 26
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM role_assignments"
            ).scalar_one() == 3
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM permissions "
                "WHERE id IN (?, ?)",
                (
                    "20000000000040008000000000000013",
                    "20000000000040008000000000000014",
                ),
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_chain_heads "
                "WHERE id = ? OR stream_key = 'authorization'",
                ("30000000000040008000000000000001",),
            ).scalar_one() == 0
    finally:
        downgraded_engine.dispose()


def test_downgrade_refuses_to_remove_used_authorization_audit_chain(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'used-audit-chain.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "20260830_0004")

    event_id = "31000000000040008000000000000001"
    event_hash = "a" * 64
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO audit_events "
                "(id, actor_user_id, action, aggregate_type, aggregate_id, "
                "before_jsonb, after_jsonb, request_id, previous_hash, "
                "event_hash, occurred_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    None,
                    "role_assignment.created",
                    "role_assignment",
                    "assignment-sentinel",
                    None,
                    '{"status":"active"}',
                    "request-sentinel",
                    None,
                    event_hash,
                    "2026-08-30 00:00:00+00:00",
                    "2026-08-30 00:00:00+00:00",
                ),
            )
            connection.exec_driver_sql(
                "UPDATE audit_chain_heads "
                "SET last_event_id = ?, last_hash = ?, version = 1 "
                "WHERE stream_key = 'authorization'",
                (event_id, event_hash),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="authorization audit chain"):
        command.downgrade(config, "20260830_0003")

    verification_engine = sa.create_engine(database_url)
    try:
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0004"
            assert connection.exec_driver_sql(
                "SELECT version, last_event_id, last_hash "
                "FROM audit_chain_heads WHERE stream_key = 'authorization'"
            ).one() == (1, event_id, event_hash)
    finally:
        verification_engine.dispose()


def test_empty_authentication_runtime_downgrade_removes_only_0005_objects(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'empty-auth-runtime.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "20260830_0004")

    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        login_indexes = {
            index["name"] for index in inspector.get_indexes("login_challenges")
        }
        assert "ix_login_challenges_requested_ip_created" not in login_indexes
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0004"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_chain_heads "
                "WHERE stream_key = 'authentication'"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT stream_key, last_event_id, last_hash, version "
                "FROM audit_chain_heads WHERE stream_key = 'authorization'"
            ).one() == ("authorization", None, None, 0)
    finally:
        engine.dispose()


def test_authentication_runtime_backfills_modes_and_enforces_evidence_shape(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'auth-modes.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0004")

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            for challenge_id, code_hash, idempotency_key in (
                (
                    "41000000000040008000000000000001",
                    "c" * 64,
                    "legacy-local-hash-sentinel",
                ),
                (
                    "41000000000040008000000000000002",
                    "",
                    "legacy-empty-hash-sentinel",
                ),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO login_challenges "
                    "(id, mobile_hash, code_hash, provider, provider_reference, "
                    "client_type, purpose, attempts, max_attempts, expires_at, "
                    "status, idempotency_key, requested_ip_hash, verified_at, "
                    "consumed_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        challenge_id,
                        "d" * 64,
                        code_hash,
                        "legacy_unknown",
                        None,
                        "legacy_unknown",
                        "login",
                        0,
                        5,
                        "2026-08-30 01:00:00+00:00",
                        "pending",
                        idempotency_key,
                        "e" * 64,
                        None,
                        None,
                        "2026-08-30 00:00:00+00:00",
                    ),
                )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("login_challenges")
        }
        assert columns["code_hash"]["nullable"] is True
        assert columns["verification_mode"]["nullable"] is False
        assert columns["verification_mode"].get("default") is None
        assert "ck_login_challenges_verification_material" in {
            constraint["name"]
            for constraint in inspector.get_check_constraints("login_challenges")
        }
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT code_hash, verification_mode FROM login_challenges "
                "ORDER BY id"
            ).all() == [
                ("c" * 64, "local_hash"),
                ("", "legacy_unknown"),
            ]

        def insert_challenge(
            challenge_id: str,
            *,
            code_hash: str | None,
            verification_mode: str,
        ) -> None:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO login_challenges "
                    "(id, mobile_hash, code_hash, verification_mode, provider, "
                    "provider_reference, client_type, purpose, attempts, "
                    "max_attempts, expires_at, status, idempotency_key, "
                    "requested_ip_hash, verified_at, consumed_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        challenge_id,
                        "f" * 64,
                        code_hash,
                        verification_mode,
                        "aliyun_pnvs",
                        None,
                        "web",
                        "login",
                        0,
                        5,
                        "2026-08-30 02:00:00+00:00",
                        "pending",
                        f"mode-check-{challenge_id}",
                        "1" * 64,
                        None,
                        None,
                        "2026-08-30 00:30:00+00:00",
                    ),
                )

        insert_challenge(
            "41000000000040008000000000000003",
            code_hash=None,
            verification_mode="provider_managed",
        )
        with pytest.raises(sa.exc.IntegrityError):
            insert_challenge(
                "41000000000040008000000000000004",
                code_hash=None,
                verification_mode="local_hash",
            )
        with pytest.raises(sa.exc.IntegrityError):
            insert_challenge(
                "41000000000040008000000000000005",
                code_hash="2" * 64,
                verification_mode="provider_managed",
            )
        with pytest.raises(sa.exc.IntegrityError):
            insert_challenge(
                "41000000000040008000000000000006",
                code_hash=None,
                verification_mode="unsupported",
            )
    finally:
        engine.dispose()


def test_downgrade_refuses_null_code_hash_before_removing_0005_objects(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'null-code-hash.db'}"
    config = _config(database_url)
    command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO login_challenges "
                "(id, mobile_hash, code_hash, verification_mode, provider, "
                "provider_reference, client_type, purpose, attempts, "
                "max_attempts, expires_at, status, idempotency_key, "
                "requested_ip_hash, verified_at, consumed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "42000000000040008000000000000001",
                    "3" * 64,
                    None,
                    "provider_managed",
                    "aliyun_pnvs",
                    None,
                    "web",
                    "login",
                    0,
                    5,
                    "2026-08-30 02:00:00+00:00",
                    "pending",
                    "provider-managed-downgrade-sentinel",
                    "4" * 64,
                    None,
                    None,
                    "2026-08-30 01:00:00+00:00",
                ),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="null challenge code_hash"):
        command.downgrade(config, "20260830_0004")

    verification_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(verification_engine)
        assert "ix_login_challenges_requested_ip_created" in {
            index["name"] for index in inspector.get_indexes("login_challenges")
        }
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0005"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_chain_heads "
                "WHERE stream_key = 'authentication'"
            ).scalar_one() == 1
    finally:
        verification_engine.dispose()


def test_downgrade_refuses_used_authentication_chain_before_dropping_index(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'used-auth-runtime.db'}"
    config = _config(database_url)
    # Exercise the 0005 downgrade guard directly.  Newer 0015 correctly blocks
    # every downgrade as soon as any immutable audit event exists, which would
    # otherwise mask the older authentication-chain-specific contract here.
    command.upgrade(config, "20260830_0005")

    event_id = "31000000000040008000000000000002"
    event_hash = "b" * 64
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO audit_events "
                "(id, actor_user_id, action, aggregate_type, aggregate_id, "
                "before_jsonb, after_jsonb, request_id, previous_hash, "
                "event_hash, occurred_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    None,
                    "login_challenge.requested",
                    "login_challenge",
                    "challenge-sentinel",
                    None,
                    '{"status":"pending"}',
                    "authentication-request-sentinel",
                    None,
                    event_hash,
                    "2026-08-30 00:00:00+00:00",
                    "2026-08-30 00:00:00+00:00",
                ),
            )
            connection.exec_driver_sql(
                "UPDATE audit_chain_heads "
                "SET last_event_id = ?, last_hash = ?, version = 1 "
                "WHERE stream_key = 'authentication'",
                (event_id, event_hash),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="authentication audit chain"):
        command.downgrade(config, "20260830_0004")

    verification_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(verification_engine)
        assert "ix_login_challenges_requested_ip_created" in {
            index["name"] for index in inspector.get_indexes("login_challenges")
        }
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0005"
            assert connection.exec_driver_sql(
                "SELECT version, last_event_id, last_hash "
                "FROM audit_chain_heads WHERE stream_key = 'authentication'"
            ).one() == (1, event_id, event_hash)
    finally:
        verification_engine.dispose()


def test_upgrade_preflight_rejects_duplicate_current_role_assignments(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'duplicate-role-scope.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0003")

    user_id = "00000000-0000-0000-0000-000000000042"
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users "
                "(id, mobile, name, password_hash, role, province, is_active, "
                "require_password_change, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    "13800000042",
                    "duplicate role guard sentinel",
                    "not-a-real-password-hash",
                    "technician",
                    None,
                    True,
                    False,
                    "2026-08-30 00:00:00+00:00",
                    "2026-08-30 00:00:00+00:00",
                ),
            )
            role_id = connection.exec_driver_sql(
                "SELECT id FROM roles WHERE code = 'technician'"
            ).scalar_one()
            for assignment_id, valid_from, status in (
                (
                    "32000000000040008000000000000001",
                    "2026-08-29 00:00:00+00:00",
                    "active",
                ),
                (
                    "32000000000040008000000000000002",
                    "2026-08-30 00:00:00+00:00",
                    "scheduled",
                ),
            ):
                connection.exec_driver_sql(
                    "INSERT INTO role_assignments "
                    "(id, user_id, role_id, scope_type, scope_id, valid_from, "
                    "valid_to, status, assigned_by, reason, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        assignment_id,
                        user_id,
                        role_id,
                        "person",
                        "00000000-0000-4000-8000-000000000042",
                        valid_from,
                        None,
                        status,
                        user_id,
                        "duplicate migration preflight sentinel",
                        "2026-08-30 00:00:00+00:00",
                        "2026-08-30 00:00:00+00:00",
                    ),
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="duplicate current role assignments"):
        command.upgrade(config, "head")

    verification_engine = sa.create_engine(database_url)
    try:
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0003"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM permissions"
            ).scalar_one() == 12
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_chain_heads "
                "WHERE stream_key = 'authorization'"
            ).scalar_one() == 0
    finally:
        verification_engine.dispose()


def test_exact_preexisting_v09_schema_can_be_stamped_without_ddl(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'preexisting.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0001")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE alembic_version")
    finally:
        engine.dispose()
    before = _schema_snapshot(database_url)
    assert set(before) == V09_TABLES

    command.stamp(config, "20260830_0001")

    assert _schema_snapshot(database_url) == before
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260830_0001"
    finally:
        engine.dispose()


def test_stamped_v09_data_is_preserved_by_additive_foundation_upgrade(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'legacy-upgrade.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0001")

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users "
                "(id, mobile, name, password_hash, role, province, is_active, "
                "require_password_change, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "00000000-0000-0000-0000-000000000001",
                    "13800000000",
                    "legacy sentinel",
                    "not-a-real-password-hash",
                    "technician",
                    "江苏省",
                    True,
                    False,
                    "2026-08-30 00:00:00+00:00",
                    "2026-08-30 00:00:00+00:00",
                ),
            )
        before = _schema_snapshot(database_url)
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    after = _schema_snapshot(database_url)
    unchanged_v09_tables = V09_TABLES - {
        "users",
        "auth_sessions",
        "materials",
        "stocktake_tasks",
        "stocktake_items",
    } - LEGACY_MATERIAL_DEPENDENT_TABLES
    assert {name: after[name] for name in unchanged_v09_tables} == {
        name: before[name] for name in unchanged_v09_tables
    }
    assert {
        key: value
        for key, value in after["auth_sessions"].items()
        if key != "indexes"
    } == {
        key: value
        for key, value in before["auth_sessions"].items()
        if key != "indexes"
    }
    assert after["legacy_v09_materials"] == before["materials"]
    assert after["legacy_v09_stocktake_tasks"] == before["stocktake_tasks"]
    for table_name in LEGACY_MATERIAL_DEPENDENT_TABLES:
        after_table_name = V09_TABLE_RENAMES_AT_HEAD.get(table_name, table_name)
        assert {
            key: value
            for key, value in after[after_table_name].items()
            if key != "foreign_keys"
        } == {
            key: value
            for key, value in before[table_name].items()
            if key != "foreign_keys"
        }
        expected_foreign_keys = sorted(
            (
                constrained_columns,
                V09_TABLE_RENAMES_AT_HEAD.get(referred_table, referred_table),
                referred_columns,
                ondelete,
            )
            for (
                constrained_columns,
                referred_table,
                referred_columns,
                ondelete,
            ) in before[table_name]["foreign_keys"]
        )
        assert after[after_table_name]["foreign_keys"] == expected_foreign_keys
    before_session_indexes = set(before["auth_sessions"]["indexes"])
    after_session_indexes = set(after["auth_sessions"]["indexes"])
    assert after_session_indexes - before_session_indexes == {
        (
            "uq_auth_sessions_active_device_family",
            ("user_id", "client_type", "device_id"),
            True,
            "revoked_at is null",
        )
    }
    before_legacy_columns = {
        column[0]: column for column in before["users"]["columns"]
    }
    after_user_columns = {
        column[0]: column for column in after["users"]["columns"]
    }
    assert {
        name: after_user_columns[name] for name in before_legacy_columns
    } == before_legacy_columns
    assert FOUNDATION_TABLES <= set(after)
    assert ACTIVATION_TABLES <= set(after)
    assert AUTHENTICATION_IDEMPOTENCY_TABLES <= set(after)
    assert AUTHENTICATION_LOGIN_RATE_LIMIT_TABLES <= set(after)
    assert INVENTORY_LEDGER_TABLES <= set(after)
    assert OPENING_STOCKTAKE_TABLES <= set(after)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            sentinel = connection.exec_driver_sql(
                "SELECT name, mobile, role, province, person_id, account_status, "
                "last_login_at, authorization_version FROM users WHERE id = ?",
                ("00000000-0000-0000-0000-000000000001",),
            ).one()
            assert sentinel == (
                "legacy sentinel",
                "13800000000",
                "technician",
                "江苏省",
                None,
                "pending_identity",
                None,
                1,
            )
    finally:
        engine.dispose()


def test_0009_preserves_legacy_material_and_balance_without_formal_backfill(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'inventory-0009.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260830_0008")

    legacy_material_id = "81000000-0000-4000-8000-000000000001"
    legacy_warehouse_id = "82000000-0000-4000-8000-000000000001"
    legacy_balance_id = "83000000-0000-4000-8000-000000000001"
    seeded_at = "2026-08-30 00:00:00+00:00"
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO materials "
                "(id, code, name, specification, category, aliases, unit, "
                "is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy_material_id,
                    "LEGACY-SKU-1",
                    "legacy material sentinel",
                    "prototype-only",
                    "其他",
                    "",
                    "个",
                    True,
                    seeded_at,
                    seeded_at,
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO warehouses "
                "(id, code, name, province, city, warehouse_type, "
                "condition_scope, warehouse_level, ownership_type, "
                "position_scope, parent_warehouse_id, manager_id, is_active, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy_warehouse_id,
                    "LEGACY-WH-1",
                    "legacy warehouse sentinel",
                    "江苏省",
                    "南京市",
                    "service_backpack",
                    "good",
                    "network",
                    "regular",
                    "unrestricted",
                    None,
                    None,
                    True,
                    seeded_at,
                    seeded_at,
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO inventory_balances "
                "(id, warehouse_id, holder_user_id, holder_key, material_id, "
                "condition, quantity_on_hand, quantity_occupied, "
                "quantity_in_transit, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    legacy_balance_id,
                    legacy_warehouse_id,
                    None,
                    "WAREHOUSE",
                    legacy_material_id,
                    "good",
                    9,
                    2,
                    1,
                    4,
                    seeded_at,
                    seeded_at,
                ),
            )
    finally:
        engine.dispose()

    command.upgrade(config, "20260830_0009")

    verification_engine = sa.create_engine(database_url)
    try:
        inspector = inspect(verification_engine)
        with verification_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT id, code, name FROM legacy_v09_materials"
            ).one() == (
                legacy_material_id,
                "LEGACY-SKU-1",
                "legacy material sentinel",
            )
            assert connection.exec_driver_sql(
                "SELECT material_id, quantity_on_hand, quantity_occupied, "
                "quantity_in_transit, version FROM inventory_balances"
            ).one() == (legacy_material_id, 9, 2, 1, 4)
            for table_name in INVENTORY_LEDGER_TABLES - {
                "inventory_ledger_heads"
            }:
                assert connection.exec_driver_sql(
                    f"SELECT count(*) FROM {table_name}"
                ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT stream_key, next_cursor FROM inventory_ledger_heads"
            ).one() == ("inventory", 1)
        for table_name in (
            "inventory_balances",
            "transfer_items",
            "work_order_materials",
            "stocktake_items",
        ):
            material_fks = [
                fk
                for fk in inspector.get_foreign_keys(table_name)
                if tuple(fk["constrained_columns"]) == ("material_id",)
            ]
            assert len(material_fks) == 1
            assert material_fks[0]["referred_table"] == "legacy_v09_materials"
    finally:
        verification_engine.dispose()

    command.downgrade(config, "20260830_0008")
    downgraded_engine = sa.create_engine(database_url)
    try:
        with downgraded_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT id, code FROM materials"
            ).one() == (legacy_material_id, "LEGACY-SKU-1")
            assert connection.exec_driver_sql(
                "SELECT material_id, quantity_on_hand FROM inventory_balances"
            ).one() == (legacy_material_id, 9)
    finally:
        downgraded_engine.dispose()


def test_postgresql_offline_sql_preserves_type_boundary(monkeypatch) -> None:
    from app.database_security import RUNTIME_FUNCTION_BODY_SHA256

    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    marker_0023 = (
        "-- Running upgrade 20260831_0022 -> 20260831_0023"
    )
    assert marker_0023 in sql
    marker_0024 = (
        "-- Running upgrade 20260831_0023 -> 20260831_0024"
    )
    assert marker_0024 in sql
    marker_0025 = (
        "-- Running upgrade 20260831_0024 -> 20260831_0025"
    )
    assert marker_0025 in sql
    marker_0026 = (
        "-- Running upgrade 20260831_0025 -> 20260831_0026"
    )
    assert marker_0026 in sql
    marker_0027 = (
        "-- Running upgrade 20260831_0026 -> 20260831_0027"
    )
    assert marker_0027 in sql
    marker_0028 = (
        "-- Running upgrade 20260831_0027 -> 20260831_0028"
    )
    assert marker_0028 in sql
    marker_0029 = (
        "-- Running upgrade 20260831_0028 -> 20260831_0029"
    )
    assert marker_0029 in sql
    sql_0023, after_0023 = sql.split(marker_0023, 1)[1].split(
        marker_0024, 1
    )
    sql_0024, after_0024 = after_0023.split(marker_0025, 1)
    sql_0025, after_0025 = after_0024.split(marker_0026, 1)
    sql_0026, after_0026 = after_0025.split(marker_0027, 1)
    sql_0027, after_0027 = after_0026.split(marker_0028, 1)
    sql_0028, _sql_0029 = after_0027.split(marker_0029, 1)
    assert (
        "CREATE OR REPLACE FUNCTION public."
        "rsc_validate_stocktake_posting_item_0010()"
    ) in sql_0023
    assert (
        "ALTER TABLE public.stocktake_posting_items ENABLE ALWAYS TRIGGER "
        "trg_stocktake_posting_items_validate_insert_0010"
    ) in sql_0023
    assert "NEW.count_line_id IS NOT NULL" in sql_0023
    assert "NEW.difference_id IS NOT NULL" in sql_0023
    assert "difference.difference_type = 'excess'" in sql_0023
    assert "observation.verification_status = 'verified'" in sql_0023
    assert "round_row.round_no > 1" in sql_0023
    assert "round_row.round_type = 'recount'" in sql_0023
    assert "opening_unexpected_dimension" in sql_0023
    assert "region_item.decision = 'accept_for_posting'" in sql_0023
    assert "hq_item.decision = 'accept_for_posting'" in sql_0023
    assert (
        "account.owner_org_id = observation.owner_org_id" in sql_0023
    )
    assert (
        "account.custodian_person_id IS NOT DISTINCT FROM" in sql_0023
    )
    assert "account.location_id = observation.location_id" in sql_0023
    assert "account.material_id = observation.material_id" in sql_0023
    assert "account.condition_code = observation.condition_code" in sql_0023
    assert "account.availability_bucket =" in sql_0023
    assert "account.lot_id IS NOT DISTINCT FROM observation.lot_id" in sql_0023
    assert "movement_serial.serial_id" in sql_0023
    assert "SELECT count(*)" in sql_0023
    assert "difference.observed_line_id = observation.id" in sql_0023
    assert "position.stock_account_id = movement.to_account_id" in sql_0023
    assert "position.last_movement_id = movement.id" in sql_0023
    assert "balance.version = 1" in sql_0023
    assert "COALESCE(sum(account_movement.quantity), 0)" in sql_0023
    assert (
        "prior_tx.ledger_cursor < transaction_row.ledger_cursor" in sql_0023
    )
    assert "prior_tx.ledger_cursor <= transaction_row.ledger_cursor" not in sql_0023
    assert (
        "CREATE CONSTRAINT TRIGGER "
        "trg_stock_accounts_opening_observation_commit_0023"
    ) in sql_0023
    assert (
        "ALTER TABLE public.stock_accounts ENABLE ALWAYS TRIGGER "
        "trg_stock_accounts_opening_observation_commit_0023"
    ) in sql_0023
    assert (
        "REVOKE EXECUTE ON FUNCTION public."
        "rsc_require_opening_observation_account_0023() "
        "FROM PUBLIC, star_oam_api"
    ) in sql_0023
    assert (
        "API stock account insert must terminate a verified opening recount "
        "observation graph"
    ) in sql_0023
    account_guard_position = sql_0023.index(
        "trg_stock_accounts_opening_observation_commit_0023"
    )
    account_insert_grant_position = sql_0023.index(
        "GRANT INSERT ON TABLE public.stock_accounts TO star_oam_api"
    )
    assert account_guard_position < account_insert_grant_position
    assert (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        "FROM PUBLIC, star_oam_api"
    ) in sql_0024
    assert (
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        "FROM PUBLIC, star_oam_api"
    ) in sql_0024
    assert (
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public "
        "FROM PUBLIC, star_oam_api"
    ) in sql_0024
    insert_grant = next(
        statement
        for statement in sql_0024.split(";")
        if "GRANT INSERT ON TABLE" in statement
    )
    required_opening_inserts = {
        "inventory_freezes",
        "stocktake_control_snapshot_lines",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_count_serials",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_observation_dispositions",
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
    for table_name in required_opening_inserts:
        assert f"public.{table_name}" in insert_grant
    assert "public.qr_codes" not in insert_grant
    select_grant = next(
        statement
        for statement in sql_0024.split(";")
        if "GRANT SELECT ON TABLE" in statement
    )
    assert "public.qr_codes" in select_grant
    assert (
        "GRANT UPDATE (status, submitted_by_user_id, submitted_at, "
        "count_manifest_sha256, updated_at) ON TABLE "
        "public.stocktake_rounds TO star_oam_api"
    ) in sql_0024
    assert (
        "GRANT UPDATE (status, submitted_at, current_round_no, posted_at, "
        "closed_at, version, updated_at) ON TABLE "
        "public.stocktake_tasks TO star_oam_api"
    ) in sql_0024
    assert "GRANT UPDATE ON TABLE public.stocktake_rounds" not in sql_0024
    assert "GRANT UPDATE ON TABLE public.stocktake_tasks" not in sql_0024
    assert "GRANT EXECUTE" not in sql_0024
    assert "GRANT USAGE ON SEQUENCE" not in sql_0024
    assert (
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    ) in sql_0025
    assert (
        "0025 preflight failed: an existing stocktake scope asset or physical "
        "location is outside its active task region tree"
    ) in sql_0025
    assert (
        "CREATE FUNCTION public."
        "rsc_validate_stocktake_scope_region_owner_0025()"
    ) in sql_0025
    assert (
        "CREATE TRIGGER trg_stocktake_scopes_region_owner_0025 BEFORE INSERT "
        "ON public.stocktake_scopes"
    ) in sql_0025
    assert (
        "ALTER TABLE public.stocktake_scopes ENABLE ALWAYS TRIGGER "
        "trg_stocktake_scopes_region_owner_0025"
    ) in sql_0025
    assert (
        "REVOKE EXECUTE ON FUNCTION public."
        "rsc_validate_stocktake_scope_region_owner_0025() "
        "FROM PUBLIC, star_oam_api"
    ) in sql_0025
    assert "region.status <> 'active'" in sql_0025
    assert "region.org_type <> 'region_company'" in sql_0025
    assert "owner.status <> 'active'" in sql_0025
    assert "owner.org_type <> 'region_company'" in sql_0025
    assert "target_location.status <> 'active'" in sql_0025
    assert (
        "target_location.location_type NOT IN ('region', 'personal')"
        in sql_0025
    )
    assert "WITH RECURSIVE asset_owner_path" in sql_0025
    assert "location_path(" in sql_0025
    assert "location_owner_path(" in sql_0025
    assert "path.location_status <> 'active'" in sql_0025
    assert "path.parent_location_id IS NOT NULL" in sql_0025
    assert "path.depth > (SELECT count(*) FROM public.stock_locations)" in sql_0025
    assert "FROM public.stock_locations AS location" in sql_0025
    assert "current_location_type NOT IN ('region', 'personal')" in sql_0025
    assert "visited_location_ids" in sql_0025
    assert "path.depth > (SELECT count(*)" in sql_0025
    assert "GRANT " not in sql_0025
    assert "CREATE TABLE opening_control_reconciliation_runs" in sql_0026
    assert "CREATE TABLE opening_control_reconciliation_items" in sql_0026
    assert "CREATE TABLE reconciliation_commands" in sql_0026
    assert (
        "CREATE TABLE opening_control_reconciliation_command_consumptions"
        in sql_0026
    )
    assert (
        "CONSTRAINT uq_reconciliation_commands_id_run UNIQUE (id, run_id)"
        in sql_0026
    )
    assert (
        "CONSTRAINT uq_reconciliation_commands_target "
        "UNIQUE (run_id, target_version)" in sql_0026
    )
    assert (
        "FOREIGN KEY(id, run_id, operation, target_version) REFERENCES "
        "opening_control_reconciliation_command_consumptions "
        "(command_id, run_id, operation, target_version) ON DELETE NO ACTION "
        "DEFERRABLE INITIALLY DEFERRED" in sql_0026
    )
    assert (
        "FOREIGN KEY(run_id) REFERENCES reconciliation_runs (id) "
        "ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED"
    ) in sql_0026
    assert (
        "FOREIGN KEY(create_command_id, run_id) REFERENCES "
        "reconciliation_commands (id, run_id) ON DELETE NO ACTION "
        "DEFERRABLE INITIALLY DEFERRED"
    ) in sql_0026
    assert (
        "FOREIGN KEY(item_id) REFERENCES reconciliation_items (id) "
        "ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED"
    ) in sql_0026
    assert "evidence_file_sha256 VARCHAR(64)" in sql_0026
    assert "evidence_file_size_bytes BIGINT" in sql_0026
    assert "evidence_file_mime_type VARCHAR(160)" in sql_0026
    assert "uq_reconciliation_commands_explain_run" not in sql_0026
    assert "existing reconciliation projections cannot be promoted" in sql_0026
    for function_name in (
        "rsc_guard_reconciliation_command_0026",
        "rsc_guard_opening_reconciliation_command_consumption_0026",
        "rsc_guard_opening_reconciliation_run_0026",
        "rsc_guard_opening_reconciliation_item_0026",
        "rsc_guard_reconciliation_run_projection_0026",
        "rsc_guard_reconciliation_item_projection_0026",
        "rsc_guard_reconciliation_effect_0026",
        "rsc_guard_opening_reconciliation_task_close_0026",
    ):
        assert f"CREATE FUNCTION public.{function_name}()" in sql_0026
        assert (
            f"REVOKE ALL ON FUNCTION public.{function_name}() FROM "
            "PUBLIC, star_oam_api"
        ) in sql_0026
    assert (
        "ALTER TABLE public.reconciliation_runs ENABLE ALWAYS TRIGGER "
        "trg_reconciliation_runs_formal_guard_0026"
    ) in sql_0026
    assert (
        "ALTER TABLE public.reconciliation_runs ENABLE ALWAYS TRIGGER "
        "trg_reconciliation_runs_formal_insert_0026"
    ) in sql_0026
    assert (
        "ALTER TABLE public.reconciliation_items ENABLE ALWAYS TRIGGER "
        "trg_reconciliation_items_formal_insert_0026"
    ) in sql_0026
    assert "binding.create_command_id = NEW.id" in sql_0026
    assert "create_command.id = binding.create_command_id" in sql_0026
    assert (
        "ALTER TABLE public.reconciliation_commands ENABLE ALWAYS TRIGGER "
        "trg_reconciliation_commands_immutable_0026"
    ) in sql_0026
    assert "GRANT INSERT ON TABLE" in sql_0026
    assert "public.reconciliation_commands" in sql_0026
    assert "public.files" in sql_0026
    assert (
        "GRANT UPDATE (status, updated_at) ON TABLE "
        "public.reconciliation_runs TO star_oam_api"
    ) in sql_0026
    assert "GRANT UPDATE ON TABLE public.reconciliation_runs" not in sql_0026
    assert "GRANT DELETE ON TABLE public.reconciliation_runs" not in sql_0026
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "public.rsc_canonical_reconciliation_json_0026(jsonb), "
        "public.rsc_reconciliation_event_key_0026(text, text, text), "
        in sql_0026
    )
    for signature in (
        "public.rsc_lock_opening_reconciliation_source_0026(uuid)",
        "public.rsc_lock_opening_reconciliation_run_0026(uuid)",
        "public.rsc_lock_opening_reconciliation_files_0026(uuid, uuid[])",
        "public.rsc_lock_formal_principal_graph_0026(text[])",
    ):
        assert signature in sql_0026
    assert "TO star_oam_api" in sql_0026
    assert sql_0026.count("SECURITY DEFINER") >= 4
    assert "FOR UPDATE OF posting" in sql_0026
    assert "FOR UPDATE OF establishment" in sql_0026
    assert "FOR UPDATE OF difference" in sql_0026
    assert "FOR UPDATE OF control" in sql_0026
    assert "FOR UPDATE OF evidence" in sql_0026
    run_lock_source = sql_0026.split(
        "CREATE FUNCTION public.rsc_lock_opening_reconciliation_run_0026",
        maxsplit=1,
    )[1].split(
        "CREATE FUNCTION public.rsc_lock_opening_reconciliation_files_0026",
        maxsplit=1,
    )[0]
    assert "FROM public.files" not in run_lock_source
    files_lock_source = sql_0026.split(
        "CREATE FUNCTION public.rsc_lock_opening_reconciliation_files_0026",
        maxsplit=1,
    )[1].split(
        "CREATE FUNCTION public.rsc_lock_formal_principal_graph_0026",
        maxsplit=1,
    )[0]
    assert "SELECT DISTINCT item.evidence_file_id" in files_lock_source
    assert "UNION" in files_lock_source
    assert "unique_requested_file_count <> requested_file_count" in files_lock_source
    assert "INTO expected_file_count" in files_lock_source
    assert "INTO locked_file_count" in files_lock_source
    assert "AS locked_files" in files_lock_source
    assert "locked_file_count <> expected_file_count" in files_lock_source
    for locked_alias in (
        "actor",
        "person",
        "identity",
        "assignment",
        "role",
        "role_permission",
        "permission",
    ):
        assert f"FOR UPDATE OF {locked_alias}" in sql_0026
    assert "requested_count > 1000" in sql_0026
    assert "count(DISTINCT requested.value)" in sql_0026
    assert (
        "GRANT EXECUTE ON FUNCTION public."
        "rsc_guard_reconciliation_effect_0026()" not in sql_0026
    )
    assert "decode('00', 'hex')" in sql_0026
    assert "command_row.occurred_at AT TIME ZONE 'UTC'" in sql_0026
    assert "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'" in sql_0026
    assert (
        "ALTER TABLE public.stocktake_tasks ENABLE ALWAYS TRIGGER "
        "trg_stocktake_tasks_reconciliation_close_guard_0026" in sql_0026
    )
    lock_signatures = (
        "public.rsc_lock_opening_control_import_0027(uuid, uuid)",
        "public.rsc_lock_opening_stocktake_start_reference_0027"
        "(uuid, uuid[], uuid[], uuid[], timestamptz)",
        "public.rsc_lock_opening_stocktake_task_evidence_0027(uuid, uuid)",
        "public.rsc_lock_inventory_reference_graph_0027"
        "(uuid[], timestamptz)",
        "public.rsc_lock_inventory_serial_graph_0027(uuid[])",
    )
    for signature in lock_signatures:
        assert f"CREATE FUNCTION {signature.split('(', 1)[0]}(" in sql_0027
        assert f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator" in sql_0027
        assert (
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, star_oam_api"
            in sql_0027
        )
    for (function_name, _argument_types), expected_hash in (
        RUNTIME_FUNCTION_BODY_SHA256.items()
    ):
        function_start = sql.index(f"CREATE FUNCTION public.{function_name}(")
        body_start = sql.index("AS $$", function_start) + len("AS $$")
        body_end = sql.index("$$", body_start)
        assert (
            hashlib.sha256(sql[body_start:body_end].encode("utf-8")).hexdigest()
            == expected_hash
        )
    assert sql_0027.count("SECURITY DEFINER") == 5
    assert sql_0027.count("SET search_path = pg_catalog, public") == 5
    assert sql_0027.count("VOLATILE") == 5
    assert "GRANT UPDATE" not in sql_0027
    assert "GRANT INSERT" not in sql_0027
    assert "GRANT DELETE" not in sql_0027
    assert "requested_owner_org_ids uuid[]" in sql_0027
    assert "graph_count > 100000" in sql_0027
    assert "array_position(requested_owner_org_ids, NULL)" in sql_0027
    assert "count(DISTINCT value)" in sql_0027
    assert "organization_ancestors" in sql_0027
    assert "region_descendants" in sql_0027
    assert "location_graph" in sql_0027
    assert (
        "location.location_type IN ('region', 'personal')" in sql_0027
    )
    assert "HAVING count(*) > 1" in sql_0027
    assert "qr.object_type = 'material'" in sql_0027
    assert "qr.object_type = 'serial'" in sql_0027

    terminal_union_signature = (
        "public.rsc_lock_opening_terminal_reference_union_0028"
        "(uuid[], uuid[], uuid[], uuid[], uuid[])"
    )
    assert (
        "CREATE FUNCTION public."
        "rsc_lock_opening_terminal_reference_union_0028("
    ) in sql_0028
    assert (
        f"ALTER FUNCTION {terminal_union_signature} OWNER TO "
        "star_oam_migrator"
    ) in sql_0028
    assert (
        f"REVOKE ALL ON FUNCTION {terminal_union_signature} FROM "
        "PUBLIC, star_oam_api"
    ) in sql_0028
    assert (
        f"GRANT EXECUTE ON FUNCTION {terminal_union_signature} "
        "TO star_oam_api"
    ) in sql_0028
    assert sql_0028.count("SECURITY DEFINER") == 1
    assert sql_0028.count("SET search_path = pg_catalog, public") == 1
    assert sql_0028.count("VOLATILE") == 1
    assert "GRANT UPDATE" not in sql_0028
    assert "GRANT INSERT" not in sql_0028
    assert "GRANT DELETE" not in sql_0028
    for array_name in (
        "requested_task_ids",
        "requested_owner_org_ids",
        "requested_location_ids",
        "requested_material_ids",
        "requested_account_ids",
    ):
        assert f"array_position({array_name}, NULL)" in sql_0028
    assert sql_0028.count("EXCEPT") >= 6
    assert "task.status IN ('posted', 'closed')" in sql_0028
    assert "scope.task_id = ANY(requested_task_ids)" in sql_0028
    assert "control.material_id IS NOT NULL" in sql_0028
    assert "snapshot.task_id = ANY(requested_task_ids)" in sql_0028
    assert "count_line.task_id = ANY(requested_task_ids)" in sql_0028
    assert "difference.task_id = ANY(requested_task_ids)" in sql_0028
    assert "disposition.task_id = ANY(requested_task_ids)" in sql_0028
    terminal_union_lock_order = tuple(
        sql_0028.index(lock_clause)
        for lock_clause in (
            "FOR UPDATE OF task",
            "FOR UPDATE OF account",
            "FOR UPDATE OF location",
            "FOR UPDATE OF organization",
            "FOR UPDATE OF custody",
            "FOR UPDATE OF material",
            "FOR UPDATE OF policy",
            "FOR UPDATE OF lot",
            "FOR UPDATE OF qr",
        )
    )
    assert terminal_union_lock_order == tuple(sorted(terminal_union_lock_order))


    control_lock_source = sql_0027.split(
        "CREATE FUNCTION public.rsc_lock_opening_control_import_0027",
        1,
    )[1].split(
        "CREATE FUNCTION public.rsc_lock_opening_stocktake_start_reference_0027",
        1,
    )[0]
    assert control_lock_source.index("FROM public.source_systems") < (
        control_lock_source.index("FROM public.sync_runs")
    )
    assert control_lock_source.index("FROM public.sync_runs") < (
        control_lock_source.index("FROM public.sync_batches")
    )
    assert control_lock_source.index("FROM public.sync_batches") < (
        control_lock_source.index("FROM public.sync_inbox_events")
    )
    assert control_lock_source.index("FROM public.sync_inbox_events") < (
        control_lock_source.index("FROM public.external_objects")
    )
    assert control_lock_source.index("FROM public.external_objects") < (
        control_lock_source.index("FROM public.external_object_versions")
    )
    assert "EXECUTE " not in control_lock_source

    start_lock_source = sql_0027.split(
        "CREATE FUNCTION public.rsc_lock_opening_stocktake_start_reference_0027",
        1,
    )[1].split(
        "CREATE FUNCTION public.rsc_lock_opening_stocktake_task_evidence_0027",
        1,
    )[0]
    start_lock_order = tuple(
        start_lock_source.index(lock_clause)
        for lock_clause in (
            "FOR UPDATE OF account",
            "FOR UPDATE OF location",
            "FOR UPDATE OF organization",
            "FOR UPDATE OF custody",
            "FOR UPDATE OF material",
            "FOR UPDATE OF policy",
            "FOR UPDATE OF lot",
            "FOR UPDATE OF qr",
        )
    )
    assert start_lock_order == tuple(sorted(start_lock_order))
    assert (
        "unnest(requested_owner_org_ids, requested_location_ids)"
        in start_lock_source
    )
    assert (
        "cardinality(requested_location_ids) <> requested_count"
        in start_lock_source
    )
    assert "SELECT DISTINCT requested.owner_org_id" in start_lock_source
    assert "requested.owner_org_id = account.owner_org_id" in start_lock_source
    assert "requested.location_id = account.location_id" in start_lock_source
    assert "NOT (account.owner_org_id" not in start_lock_source
    assert "requested_location_count" in start_lock_source
    assert "locked_material_ids" in start_lock_source
    assert "EXECUTE " not in start_lock_source

    evidence_lock_source = sql_0027.split(
        "CREATE FUNCTION public.rsc_lock_opening_stocktake_task_evidence_0027",
        1,
    )[1].split(
        "CREATE FUNCTION public.rsc_lock_inventory_reference_graph_0027",
        1,
    )[0]
    for shared_table in (
        "public.stock_accounts",
        "public.stock_locations",
        "public.materials",
        "public.material_inventory_policies",
        "public.inventory_lots",
        "public.inventory_serials",
        "public.serial_current_positions",
        "public.stock_balances",
        "public.inventory_ledger_heads",
    ):
        assert shared_table not in evidence_lock_source
    assert "round_row.round_no = task.current_round_no" not in evidence_lock_source
    assert "round_row.task_id = requested_task_id" in evidence_lock_source
    assert "EXECUTE " not in evidence_lock_source

    reference_lock_source = sql_0027.split(
        "CREATE FUNCTION public.rsc_lock_inventory_reference_graph_0027",
        1,
    )[1].split(
        "CREATE FUNCTION public.rsc_lock_inventory_serial_graph_0027",
        1,
    )[0]
    reference_order = tuple(
        reference_lock_source.index(table_name)
        for table_name in (
            "public.stock_accounts",
            "public.stock_locations",
            "public.materials",
            "public.material_inventory_policies",
            "public.inventory_lots",
        )
    )
    assert reference_order == tuple(sorted(reference_order))
    assert "EXECUTE " not in reference_lock_source

    serial_lock_source = sql_0027.split(
        "CREATE FUNCTION public.rsc_lock_inventory_serial_graph_0027",
        1,
    )[1].split("DO $$", 1)[0]
    serial_order = tuple(
        serial_lock_source.index(table_name)
        for table_name in (
            "public.inventory_serials",
            "public.serial_current_positions",
            "public.qr_codes",
        )
    )
    assert serial_order == tuple(sorted(serial_order))
    assert "EXECUTE " not in serial_lock_source
    assert "CREATE TABLE users" in sql
    assert "CREATE TABLE organizations" in sql
    assert "CREATE TABLE auth_refresh_tokens" in sql
    assert "CREATE TABLE audit_chain_heads" in sql
    assert "CREATE TABLE auth_idempotency_operations" in sql
    assert "CREATE TABLE auth_login_rate_limit_buckets" in sql
    assert "ALTER TABLE materials RENAME TO legacy_v09_materials" in sql
    assert (
        "ALTER TABLE legacy_v09_materials RENAME CONSTRAINT materials_pkey "
        "TO legacy_v09_materials_pkey"
    ) in sql
    assert "CREATE TABLE inventory_transactions" in sql
    assert "CREATE TABLE inventory_movement_serials" in sql
    assert "rsc_block_inventory_ledger_mutation_0009" in sql
    assert (
        "ALTER TABLE stocktake_tasks RENAME TO legacy_v09_stocktake_tasks"
        in sql
    )
    assert (
        "ALTER TABLE stocktake_items RENAME TO legacy_v09_stocktake_items"
        in sql
    )
    assert "legacy_v09_stocktake_tasks_pkey" in sql
    for table_name in OPENING_STOCKTAKE_TABLES:
        assert f"CREATE TABLE {table_name}" in sql
    for table_name in OPENING_COUNT_OBSERVATION_TABLES:
        assert f"CREATE TABLE {table_name}" in sql
    assert "rsc_block_stocktake_fact_mutation_0010" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON stocktake_count_lines" in sql
    assert "rsc_validate_stocktake_posting_item_0010" in sql
    assert "control-only difference cannot produce inventory movement" in sql
    assert "rsc_validate_stocktake_evidence_insert_0010" in sql
    assert "stocktake differences are sealed after review begins" in sql
    assert "stocktake posting items are sealed by establishment" in sql
    assert "rsc_validate_opening_round_insert_0010" in sql
    assert "opening first round requires complete sealed start facts" in sql
    assert "LEFT JOIN inventory_freezes AS freeze_row" in sql
    assert sql.count("inventory_freezes AS freeze_row") >= 5
    assert " AS freeze\n" not in sql
    assert "rsc_validate_opening_task_mutation_0010" in sql
    assert "opening task cannot post before every scope is established" in sql
    assert "rsc_validate_inventory_freeze_mutation_0010" in sql
    assert "inventory freeze binding is immutable" in sql
    assert "ADD COLUMN observed_line_id UUID" in sql
    assert "fk_stocktake_differences_observed_line" in sql
    assert "rsc_validate_stocktake_scope_completion_insert_0011" in sql
    assert "must cover every cutoff snapshot account exactly once" in sql
    assert "trg_stocktake_count_lines_immutable_0011" in sql
    assert "trg_stocktake_rounds_submission_manifest_update_0011" in sql
    assert (
        "0012 preflight failed: duplicate resolved stocktake serial "
        "observations must be reviewed before migration"
    ) in sql
    assert (
        "CREATE UNIQUE INDEX uq_stocktake_count_observations_round_serial_id "
        "ON stocktake_count_observations (round_id, serial_id) "
        "WHERE serial_id IS NOT NULL"
    ) in sql
    assert (
        "0013 preflight failed: existing resolved stocktake serial observations "
        "violate identifier mapping rules"
    ) in sql
    assert "rsc_validate_stocktake_observation_insert_0013" in sql
    assert "serial.serial_no = NEW.serial_no_raw" in sql
    assert "serial.qr_code = NEW.serial_no_raw" in sql
    assert "mapping.object_type = 'serial'" in sql
    assert "mapping.status = 'active'" in sql
    assert (
        "0014 preflight failed: round submission sealing completion is "
        "missing or ambiguous"
    ) in sql
    assert "ADD COLUMN sealing_completion_id UUID" in sql
    assert (
        "ALTER TABLE stocktake_round_submissions DISABLE TRIGGER "
        "trg_stocktake_round_submissions_immutable_0011"
    ) in sql
    assert (
        "ALTER TABLE stocktake_round_submissions ENABLE TRIGGER "
        "trg_stocktake_round_submissions_immutable_0011"
    ) in sql
    assert "rsc_validate_round_sealing_completion_0014" in sql
    assert "uq_stocktake_round_submissions_sealing_completion" in sql
    assert "rsc_reject_audit_event_mutation_0015" in sql
    assert (
        "LOCK TABLE audit_events, audit_chain_heads "
        "IN SHARE ROW EXCLUSIVE MODE"
    ) in sql
    assert "BEFORE UPDATE OR DELETE ON audit_events" in sql
    assert "BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT" in sql
    assert "rsc_validate_audit_chain_head_mutation_0015" in sql
    assert "BEFORE UPDATE OR DELETE ON audit_chain_heads FOR EACH ROW" in sql
    assert "BEFORE TRUNCATE ON audit_chain_heads FOR EACH STATEMENT" in sql
    assert (
        "ALTER TABLE audit_events ENABLE ALWAYS TRIGGER "
        "trg_audit_events_immutable_0015"
    ) in sql
    assert (
        "ALTER TABLE audit_chain_heads ENABLE ALWAYS TRIGGER "
        "trg_audit_chain_heads_forward_only_0015"
    ) in sql
    assert (
        "0015 populated audit chains require an online canonical-hash preflight"
        in sql
    )
    assert (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert (
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert "REVOKE SELECT (%1$I), INSERT (%1$I), UPDATE (%1$I)" in sql
    assert "GRANT SELECT ON TABLE audit_chain_heads, audit_events" in sql
    assert "GRANT INSERT ON TABLE audit_events" in sql
    assert "GRANT UPDATE ON TABLE audit_chain_heads" in sql
    assert "GRANT DELETE ON TABLE auth_login_rate_limit_buckets" in sql
    assert (
        "0016 preflight failed: existing stocktake submission, review or "
        "difference evidence cannot be safely inferred"
    ) in sql
    assert "ADD COLUMN count_manifest_sha256 VARCHAR(64) NOT NULL" in sql
    assert "CREATE TABLE stocktake_observation_dispositions" in sql
    assert "CREATE TABLE stocktake_difference_set_completions" in sql
    assert "submission.count_manifest_sha256 = NEW.count_manifest_sha256" in sql
    assert "rsc_validate_stocktake_observation_disposition_0016" in sql
    assert "rsc_validate_stocktake_difference_set_completion_0016" in sql
    assert "rsc_require_stocktake_difference_completion_0016" in sql
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE stocktake_observation_dispositions, "
        "stocktake_difference_set_completions FROM PUBLIC, star_oam_api"
    ) in sql
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "rsc_validate_stocktake_observation_disposition_0016() "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert (
        "0017 populated audit streams require an online canonical mapping "
        "preflight"
    ) in sql
    assert "LOCK TABLE audit_chain_heads IN ACCESS EXCLUSIVE MODE" in sql
    assert "LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE" in sql
    assert "ADD COLUMN stream_key VARCHAR(160) NOT NULL" in sql
    assert "ADD COLUMN stream_version BIGINT NOT NULL" in sql
    assert "ck_audit_events_stream_key_0017" in sql
    assert "ck_audit_events_stream_version_0017" in sql
    assert "uq_audit_events_stream_version_0017" in sql
    assert "fk_audit_events_stream_key_0017" in sql
    assert "rsc_validate_audit_stream_head_binding_0017" in sql
    assert (
        "CREATE CONSTRAINT TRIGGER trg_audit_events_commit_binding_0017 "
        "AFTER INSERT ON audit_events DEFERRABLE INITIALLY DEFERRED"
    ) in sql
    assert (
        "CREATE FUNCTION rsc_require_audit_event_commit_binding_0017()\n"
        "RETURNS trigger\nLANGUAGE plpgsql\nAS $$\nBEGIN\n    IF NOT EXISTS"
    ) in sql
    assert "AS $$\nBEGIN\nBEGIN" not in sql
    assert (
        "head.version >= NEW.stream_version"
    ) in sql
    assert (
        "ALTER TABLE audit_events ENABLE ALWAYS TRIGGER "
        "trg_audit_events_commit_binding_0017"
    ) in sql
    assert (
        "REVOKE EXECUTE ON FUNCTION "
        "rsc_require_audit_event_commit_binding_0017() "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert (
        "CREATE FUNCTION public."
        "rsc_validate_stocktake_technician_personal_location_0019()"
    ) in sql
    assert (
        "CREATE TRIGGER "
        "trg_stocktake_scope_count_completions_technician_personal_location_0019 "
        "BEFORE INSERT ON public.stocktake_scope_count_completions"
    ) in sql
    assert (
        "CREATE TRIGGER "
        "trg_stocktake_recount_scope_assignments_technician_personal_location_0019 "
        "BEFORE INSERT ON public.stocktake_recount_scope_assignments"
    ) in sql
    assert (
        "ENABLE ALWAYS TRIGGER "
        "trg_stocktake_scope_count_completions_technician_personal_location_0019"
    ) in sql
    assert (
        "ENABLE ALWAYS TRIGGER "
        "trg_stocktake_recount_scope_assignments_technician_personal_location_0019"
    ) in sql
    assert (
        "REVOKE EXECUTE ON FUNCTION public."
        "rsc_validate_stocktake_technician_personal_location_0019() "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert (
        "CREATE FUNCTION public.rsc_preserve_stocktake_personal_location_0020()"
        in sql
    )
    assert (
        "CREATE TRIGGER trg_stock_locations_stocktake_personal_continuity_0020 "
        "BEFORE UPDATE ON public.stock_locations"
    ) in sql
    assert (
        "ALTER TABLE public.stock_locations ENABLE ALWAYS TRIGGER "
        "trg_stock_locations_stocktake_personal_continuity_0020"
    ) in sql
    assert (
        "REVOKE EXECUTE ON FUNCTION public."
        "rsc_preserve_stocktake_personal_location_0020() "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert "uq_formal_stocktake_tasks_active_opening_region" in sql
    assert "'stocktake', 'read'" in sql
    assert "'stocktake', 'count'" in sql
    assert "'stocktake', 'manage'" in sql
    assert "'stocktake', 'review_region'" in sql
    assert "'stocktake', 'review_headquarters'" in sql
    assert "'stocktake', 'post_opening'" in sql
    assert "response_ciphertext BYTEA" in sql
    assert "response_nonce BYTEA" in sql
    assert "ck_auth_idempotency_operations_response_evidence" in sql
    assert "ck_auth_idempotency_operations_request_hashes" in sql
    assert "ck_auth_idempotency_operations_time_order" in sql
    assert (
        "CREATE INDEX ix_auth_idempotency_operations_scope_status_expires ON "
        "auth_idempotency_operations (scope_hash, status, expires_at)"
    ) in sql
    assert (
        "CREATE INDEX ix_auth_idempotency_operations_status_expires ON "
        "auth_idempotency_operations (status, expires_at)"
    ) in sql
    assert (
        "CREATE INDEX ix_auth_login_rate_limit_buckets_cleanup_after ON "
        "auth_login_rate_limit_buckets (cleanup_after)"
    ) in sql
    assert "ck_auth_login_rate_limit_buckets_hash_version" in sql
    assert (
        "0007 preflight failed: duplicate active authentication device families"
        in sql
    )
    assert (
        "CREATE UNIQUE INDEX uq_auth_sessions_active_device_family ON "
        "auth_sessions (user_id, client_type, device_id) "
        "WHERE revoked_at IS NULL"
    ) in sql
    assert (
        "CREATE INDEX ix_login_challenges_requested_ip_created ON "
        "login_challenges (requested_ip_hash, created_at)"
    ) in sql
    assert "ADD COLUMN verification_mode VARCHAR(24)" in sql
    assert "ALTER COLUMN code_hash DROP NOT NULL" in sql
    assert "ck_login_challenges_verification_material" in sql
    assert "provider_managed" in sql
    assert "legacy_unknown" in sql
    assert "ADD COLUMN person_id UUID" in sql
    assert "ADD COLUMN account_status VARCHAR(32) DEFAULT 'pending_identity'" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql
    assert "BOOLEAN NOT NULL" in sql
    assert "VARCHAR(36) NOT NULL" in sql
    assert "UUID NOT NULL" in sql
    assert "JSONB NOT NULL" in sql
    assert "NUMERIC(18, 3)" in sql
    assert "INSERT INTO roles" in sql
    assert "INSERT INTO permissions" in sql
    assert "INSERT INTO role_permissions" in sql
    assert "'people', 'read_minimal'" in sql
    assert "'role_assignment', 'manage_provincial'" in sql
    assert "'authorization'" in sql
    assert "'authentication'" in sql
    assert "'inventory'" in sql
    assert "30000000-0000-4000-8000-000000000001" in sql
    assert "30000000-0000-4000-8000-000000000002" in sql
    assert "30000000-0000-4000-8000-000000000003" in sql
    assert "0004 preflight failed: duplicate current role assignments" in sql
    assert (
        "CREATE UNIQUE INDEX uq_role_assignments_current_scope ON "
        "role_assignments (user_id, role_id, scope_type, scope_id) "
        "WHERE status IN ('scheduled', 'active')"
    ) in sql
    assert "star_headquarters_approver" in sql
    assert "auditor" not in sql
    assert "warehouse_manager" not in sql
    assert "metadata.create_all" not in sql


def test_0031_postgresql_difference_guard_has_fixed_namespace_and_optional_acl(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )

    command.upgrade(
        config,
        f"{PRE_STOCKTAKE_DIFFERENCE_HEAD_REVISION}:20260901_0031",
        sql=True,
    )

    sql = output.getvalue()
    signature = (
        "public.rsc_validate_stocktake_difference_set_completion_0031()"
    )
    assert f"CREATE FUNCTION {signature}" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert (
        "CREATE TRIGGER trg_stocktake_difference_set_completions_validate_0031 "
        "BEFORE INSERT ON public.stocktake_difference_set_completions"
    ) in sql
    assert f"EXECUTE FUNCTION {signature}" in sql
    assert f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC" in sql
    assert "SELECT 1 FROM pg_catalog.pg_roles" in sql
    assert "rolname = 'star_oam_api'" in sql
    assert "FROM PUBLIC, star_oam_api" not in sql
    assert "GRANT " not in sql
    assert "task_kind = 'opening'" in sql
    assert (
        "task_kind IN ('full', 'sample', 'ad_hoc', 'personal', 'termination')"
        in sql
    )
    assert "role.code = 'provincial_manager'" in sql
    assert "role.code = 'admin'" in sql
    assert "role.code = 'technician'" not in sql


def test_0032_postgresql_review_recount_guards_have_fixed_namespace_and_acl(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )

    command.upgrade(
        config,
        "20260901_0031:20260901_0032",
        sql=True,
    )

    sql = output.getvalue()
    assert "rsc_lock_nonopening_stocktake_review_graph_0032" in sql
    assert "rsc_require_nonopening_stocktake_review_graph_0032" in sql
    assert "rsc_validate_stocktake_recount_case_0032" in sql
    assert (
        "\n    THEN\n"
        "        RAISE EXCEPTION 'stocktake recount case causality is invalid';"
        in sql
    )
    assert (
        "\n    ) THEN\n"
        "        RAISE EXCEPTION 'stocktake recount case causality is invalid';"
        not in sql
    )
    assert "rsc_stocktake_recount_scope_graph_valid_0032" in sql
    assert "rsc_validate_stocktake_recount_task_advance_0032" in sql
    assert "rsc_validate_stocktake_recount_round_0032" in sql
    assert "rsc_require_stocktake_recount_graph_0032" in sql
    assert sql.count("SET search_path = pg_catalog, public") == 7
    assert "CREATE CONSTRAINT TRIGGER trg_nonopening_review_graph_task_0032" in sql
    assert "CREATE CONSTRAINT TRIGGER trg_nonopening_review_graph_review_0032" in sql
    assert "CREATE CONSTRAINT TRIGGER trg_nonopening_review_graph_item_0032" in sql
    assert (
        "CREATE TRIGGER trg_stocktake_recount_cases_review_path_0032 "
        "BEFORE INSERT ON public.stocktake_recount_cases"
    ) in sql
    assert (
        "CREATE TRIGGER trg_stocktake_tasks_recount_causality_0032 "
        "BEFORE UPDATE ON public.stocktake_tasks"
    ) in sql
    assert "('full', 'sample', 'ad_hoc', 'personal', 'termination')" in sql
    assert "review_stage = 'region'" in sql
    assert "review_stage = 'headquarters'" in sql
    assert "'stocktake_pending_verification'" in sql
    assert "'accept_for_posting'" in sql
    assert "SECURITY DEFINER" in sql
    assert "SELECT 1 FROM pg_catalog.pg_roles" in sql
    assert "rolname = 'star_oam_api'" in sql
    assert "rolname = 'star_oam_migrator'" in sql
    assert "GRANT EXECUTE ON FUNCTION public.rsc_lock_nonopening_stocktake_review_graph_0032" in sql
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert f"GRANT {privilege}" not in sql
    assert "count_ledger_cursor" not in sql


def test_0032_sqlite_installs_scoped_guards_and_restores_opening_guards(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'review-recount-0032.db'}"
    config = _config(database_url)

    command.upgrade(config, NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION_ID)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            triggers = dict(
                connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type = 'trigger'"
                ).all()
            )
        expected = {
            "trg_stocktake_tasks_nonopening_review_graph_0032",
            "trg_stocktake_recount_cases_review_path_0032",
            "trg_stocktake_tasks_recount_causality_0032",
            "trg_stocktake_rounds_recount_causality_0032_insert",
            "trg_stocktake_rounds_recount_causality_0032_update",
        }
        assert expected <= triggers.keys()
        assert "trg_stocktake_recount_cases_review_path_0021" not in triggers
        assert "trg_stocktake_tasks_recount_causality_0018" not in triggers
        assert "('full', 'sample', 'ad_hoc', 'personal', 'termination')" in triggers[
            "trg_stocktake_tasks_nonopening_review_graph_0032"
        ]
        assert "task.task_type = 'opening'" in triggers[
            "trg_stocktake_recount_cases_review_path_0032"
        ]
        assert "pending_verification" in triggers[
            "trg_stocktake_recount_cases_review_path_0032"
        ]
        assert "replace(historical_assignment.scope_id, '-', '')" in triggers[
            "trg_stocktake_tasks_nonopening_review_graph_0032"
        ]
    finally:
        engine.dispose()

    command.downgrade(config, "20260901_0031")
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).all()
            }
        assert not expected.intersection(trigger_names)
        assert {
            "trg_stocktake_recount_cases_review_path_0021",
            "trg_stocktake_tasks_recount_causality_0018",
            "trg_stocktake_rounds_recount_causality_0018_insert",
            "trg_stocktake_rounds_recount_causality_0018_update",
        } <= trigger_names
    finally:
        engine.dispose()


def test_0033_postgresql_count_cursor_guard_has_fixed_namespace_and_acl(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )

    command.upgrade(
        config,
        "20260901_0032:20260901_0033",
        sql=True,
    )

    sql = output.getvalue()
    signature = "public.rsc_validate_stocktake_count_ledger_boundary_0033()"
    assert "ADD COLUMN count_ledger_cursor BIGINT" in sql
    assert (
        "CONSTRAINT ck_stocktake_scope_count_completions_count_cursor "
        "CHECK (count_ledger_cursor IS NULL OR count_ledger_cursor >= 0)"
    ) in sql
    assert f"CREATE FUNCTION {signature}" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "FOR UPDATE OF head" in sql
    assert "NEW.count_ledger_cursor <> head_next_cursor - 1" in sql
    assert "NEW.count_ledger_cursor < cutoff_cursor" in sql
    assert (
        "CREATE TRIGGER trg_stocktake_scope_count_ledger_boundary_0033 "
        "BEFORE INSERT ON public.stocktake_scope_count_completions"
    ) in sql
    assert f"EXECUTE FUNCTION {signature}" in sql
    assert f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC" in sql
    assert "rolname = 'star_oam_api'" in sql
    assert "rolname = 'star_oam_migrator'" in sql
    assert "GRANT " not in sql


def test_0033_sqlite_installs_and_enforces_count_cursor_boundary(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'count-cursor-0033.db'}"
    config = _config(database_url)
    command.upgrade(config, STOCKTAKE_COUNT_LEDGER_BOUNDARY_REVISION_ID)
    engine = sa.create_engine(database_url)
    try:
        columns = {
            row["name"]: row for row in inspect(engine).get_columns(
                "stocktake_scope_count_completions"
            )
        }
        checks = {
            row["name"]: row["sqltext"]
            for row in inspect(engine).get_check_constraints(
                "stocktake_scope_count_completions"
            )
        }
        assert columns["count_ledger_cursor"]["nullable"] is True
        assert (
            "count_ledger_cursor IS NULL OR count_ledger_cursor >= 0"
            in checks["ck_stocktake_scope_count_completions_count_cursor"]
        )
        with engine.connect() as connection:
            trigger_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_scope_count_ledger_boundary_0033'"
            ).scalar_one()
        assert "NEW.count_ledger_cursor IS NULL" in trigger_sql
        assert "head.next_cursor - 1" in trigger_sql
        assert "task.cutoff_ledger_cursor" in trigger_sql
    finally:
        engine.dispose()

    isolated = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with isolated.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_tasks ("
                "id TEXT PRIMARY KEY, task_type TEXT NOT NULL, "
                "cutoff_ledger_cursor BIGINT)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE inventory_ledger_heads ("
                "id TEXT PRIMARY KEY, stream_key TEXT NOT NULL, "
                "next_cursor BIGINT NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_scope_count_completions ("
                "task_id TEXT NOT NULL, count_ledger_cursor BIGINT)"
            )
            connection.exec_driver_sql(trigger_sql)
            connection.exec_driver_sql(
                "INSERT INTO inventory_ledger_heads "
                "(id, stream_key, next_cursor) VALUES "
                "('40000000000040008000000000000001', 'inventory', 5)"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_tasks VALUES ('opening', 'opening', 0)"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_tasks VALUES "
                "('managed', 'sample', 2)"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('opening', NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('managed', 4)"
            )
            for statement in (
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('opening', 4)",
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('managed', NULL)",
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('managed', 3)",
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('managed', 1)",
            ):
                with pytest.raises(sa.exc.IntegrityError, match="ledger boundary"):
                    connection.exec_driver_sql(statement)
    finally:
        isolated.dispose()

    command.downgrade(config, NONOPENING_STOCKTAKE_REVIEW_RECOUNT_REVISION_ID)
    downgraded = sa.create_engine(database_url)
    try:
        assert "count_ledger_cursor" not in {
            row["name"]
            for row in inspect(downgraded).get_columns(
                "stocktake_scope_count_completions"
            )
        }
    finally:
        downgraded.dispose()


def _load_0034_migration_module():
    spec = importlib.util.spec_from_file_location(
        "stocktake_recount_selected_scope_migration_0034",
        STOCKTAKE_RECOUNT_SELECTED_SCOPE_REVISION,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0034_postgresql_upgrade_and_old_guard_restore_sql_are_well_shaped(
    monkeypatch,
) -> None:
    module = _load_0034_migration_module()
    old_sql = module._postgresql_round_submission_function_sql(
        round_aware=False
    )
    assert old_sql.count("IF required_scope_count = 0 OR EXISTS (") == 1
    assert "IF required_scope_count = 0 OR EXISTS (\n    IF" not in old_sql
    assert "CREATE FUNCTION public.rsc_validate_stocktake_round_submission_insert_0011()" in old_sql
    assert "SECURITY DEFINER" in old_sql
    assert "SET search_path = pg_catalog, public" in old_sql

    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(
        config,
        "20260901_0033:20260901_0034",
        sql=True,
    )
    sql = output.getvalue()
    assert "CREATE FUNCTION public.rsc_validate_recount_completion_scope_0034()" in sql
    assert "CREATE FUNCTION public.rsc_validate_stocktake_round_submission_insert_0034()" in sql
    assert "non-opening recount submission requires exact selected scopes" in sql
    assert "ENABLE ALWAYS TRIGGER trg_stocktake_recount_completion_scope_0034" in sql
    assert "ENABLE ALWAYS TRIGGER trg_stocktake_round_submissions_validate_insert_0034" in sql
    assert "REVOKE EXECUTE ON FUNCTION" in sql
    assert "GRANT " not in sql


def test_0034_preflight_rejects_standalone_completion_outside_assignment() -> None:
    module = _load_0034_migration_module()
    invalid_sql = module._invalid_existing_graph_sql("sqlite", prefix="")
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_tasks (id TEXT PRIMARY KEY, task_type TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_rounds (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, "
                "round_no INTEGER NOT NULL, round_type TEXT NOT NULL, recount_case_id TEXT)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_scopes (id TEXT PRIMARY KEY, task_id TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_recount_cases (id TEXT PRIMARY KEY, "
                "task_id TEXT NOT NULL, next_round_no INTEGER NOT NULL, scope_count INTEGER NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_recount_scope_assignments ("
                "recount_case_id TEXT NOT NULL, task_id TEXT NOT NULL, scope_id TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_scope_count_completions ("
                "task_id TEXT NOT NULL, round_id TEXT NOT NULL, scope_id TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE stocktake_round_submissions (task_id TEXT, round_id TEXT)"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_tasks VALUES ('task', 'sample')"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_rounds VALUES "
                "('round', 'task', 2, 'recount', 'case')"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_scopes VALUES ('selected', 'task'), ('extra', 'task')"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_recount_cases VALUES ('case', 'task', 2, 1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_recount_scope_assignments VALUES "
                "('case', 'task', 'selected')"
            )
            connection.exec_driver_sql(
                "INSERT INTO stocktake_scope_count_completions VALUES "
                "('task', 'round', 'extra')"
            )
            assert connection.exec_driver_sql(
                f"SELECT 1 WHERE {invalid_sql}"
            ).scalar_one() == 1
            connection.exec_driver_sql(
                "UPDATE stocktake_scope_count_completions SET scope_id = 'selected'"
            )
            assert connection.exec_driver_sql(
                f"SELECT 1 WHERE {invalid_sql}"
            ).first() is None
    finally:
        engine.dispose()


def test_0026_postgresql_hash_contract_has_fixed_cross_language_vectors() -> None:
    source = OPENING_CONTROL_RECONCILIATION_REVISION.read_text(encoding="utf-8")
    event_payload = (
        b"cloud_oam.opening_control_reconciliation.event.v1\0"
        b"explain_opening\0fixture-anchor\0outbox"
    )
    assert hashlib.sha256(event_payload).hexdigest() == (
        "347b4851a2160c5ef4b95bc9a8d46939"
        "d1cc480d8a13ea9a97ecdefae7d567cc"
    )
    document = {
        "schema": "cloud_oam.opening_control_reconciliation.fixture.v1",
        "comment": "中文解释",
        "enabled": True,
        "optional": None,
        "version": 1,
        "items": [
            {"expected_version": 0, "evidence_file_id": None},
        ],
    }
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == (
        "cca528e3bd98d8e8f409dc638ba47a98"
        "f3495e386f9d4faec00aebdc2c9bfe05"
    )
    assert "decode('00', 'hex')" in source
    assert "ORDER BY entry.key" in source
    assert "WITH ORDINALITY" in source
    assert "ORDER BY difference.difference_no" in source
    assert "FM999999999999990.000" in source
    assert "binding_row.item_manifest_sha256 IS DISTINCT FROM" in source


def test_0026_postgresql_offline_downgrade_removes_close_guard_first(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        "20260831_0026:20260831_0025",
        sql=True,
    )
    sql = output.getvalue()
    trigger_drop = (
        "DROP TRIGGER trg_stocktake_tasks_reconciliation_close_guard_0026 "
        "ON public.stocktake_tasks"
    )
    function_drop = (
        "DROP FUNCTION public."
        "rsc_guard_opening_reconciliation_task_close_0026()"
    )
    assert trigger_drop in sql
    assert function_drop in sql
    assert sql.index(trigger_drop) < sql.index(function_drop)
    for signature in (
        "rsc_lock_formal_principal_graph_0026(text[])",
        "rsc_lock_opening_reconciliation_files_0026(uuid, uuid[])",
        "rsc_lock_opening_reconciliation_run_0026(uuid)",
        "rsc_lock_opening_reconciliation_source_0026(uuid)",
    ):
        assert f"DROP FUNCTION public.{signature}" in sql


def test_0027_postgresql_offline_downgrade_drops_only_lock_graph(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        "20260831_0027:20260831_0026",
        sql=True,
    )
    sql = output.getvalue()
    for signature in (
        "rsc_lock_opening_control_import_0027(uuid, uuid)",
        "rsc_lock_opening_stocktake_start_reference_0027"
        "(uuid, uuid[], uuid[], uuid[], timestamptz)",
        "rsc_lock_opening_stocktake_task_evidence_0027(uuid, uuid)",
        "rsc_lock_inventory_reference_graph_0027(uuid[], timestamptz)",
        "rsc_lock_inventory_serial_graph_0027(uuid[])",
    ):
        assert f"DROP FUNCTION public.{signature}" in sql
    assert "DROP TABLE" not in sql
    assert "REVOKE UPDATE" not in sql
    assert "GRANT UPDATE" not in sql


def test_0028_postgresql_offline_upgrade_is_exact_bounded_union(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.upgrade(config, "20260831_0028", sql=True)
    marker = "-- Running upgrade 20260831_0027 -> 20260831_0028"
    sql = output.getvalue().split(marker, 1)[1]
    signature = (
        "public.rsc_lock_opening_terminal_reference_union_0028"
        "(uuid[], uuid[], uuid[], uuid[], uuid[])"
    )
    assert f"ALTER FUNCTION {signature} OWNER TO star_oam_migrator" in sql
    assert (
        f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC, star_oam_api"
        in sql
    )
    assert f"GRANT EXECUTE ON FUNCTION {signature} TO star_oam_api" in sql
    assert "task.status IN ('posted', 'closed')" in sql
    assert sql.count("EXCEPT") >= 6
    assert "control.material_id IS NOT NULL" in sql
    assert "expected_account_ids" in sql
    assert "expected_material_ids" in sql
    assert "relevant_location_ids" in sql
    assert "relevant_organization_ids" in sql
    lock_offsets = tuple(
        sql.index(clause)
        for clause in (
            "FOR UPDATE OF task",
            "FOR UPDATE OF account",
            "FOR UPDATE OF location",
            "FOR UPDATE OF organization",
            "FOR UPDATE OF custody",
            "FOR UPDATE OF material",
            "FOR UPDATE OF policy",
            "FOR UPDATE OF lot",
            "FOR UPDATE OF qr",
        )
    )
    assert lock_offsets == tuple(sorted(lock_offsets))
    body_start = sql.index("AS $$") + len("AS $$")
    body_end = sql.index("$$", body_start)
    assert hashlib.sha256(
        sql[body_start:body_end].encode("utf-8")
    ).hexdigest() == (
        "a5445f4651364a179223695d28ba7ce9434f0f20307098a9291067b64b07d066"
    )


def test_0028_postgresql_offline_downgrade_drops_only_terminal_union_helper(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        "20260831_0028:20260831_0027",
        sql=True,
    )
    sql = output.getvalue()
    assert (
        "DROP FUNCTION public.rsc_lock_opening_terminal_reference_union_0028"
        "(uuid[], uuid[], uuid[], uuid[], uuid[])"
    ) in sql
    assert sql.count("DROP FUNCTION") == 1
    assert "DROP TABLE" not in sql
    assert "GRANT UPDATE" not in sql


def test_0023_sqlite_posting_item_guard_has_both_exact_source_branches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'opening-observation-0023.db'}"
    config = _config(database_url)
    command.upgrade(config, HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            trigger_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_posting_items_validate_insert_0010'"
            ).scalar_one()
        assert "NEW.count_line_id IS NOT NULL" in trigger_sql
        assert "NEW.difference_id IS NOT NULL" in trigger_sql
        assert "difference.difference_type = 'excess'" in trigger_sql
        assert "difference.difference_type <> 'control_unassigned'" in trigger_sql
        assert "observation.verification_status = 'verified'" in trigger_sql
        assert "round_row.round_no > 1" in trigger_sql
        assert "round_row.round_type = 'recount'" in trigger_sql
        assert "round_row.round_no = task.current_round_no" in trigger_sql
        assert "account.owner_org_id = observation.owner_org_id" in trigger_sql
        assert "account.location_id = observation.location_id" in trigger_sql
        assert "account.material_id = observation.material_id" in trigger_sql
        assert "observation.counted_qty = NEW.quantity" in trigger_sql
        assert "movement_serial.serial_id" in trigger_sql
        assert "region_item.decision = 'accept_for_posting'" in trigger_sql
        assert "hq_item.decision = 'accept_for_posting'" in trigger_sql

        command.downgrade(config, "20260831_0022")
        with engine.connect() as connection:
            legacy_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_posting_items_validate_insert_0010'"
            ).scalar_one()
        assert "opening posting items require matching physical count lines" in legacy_sql
        assert "round_row.round_type = 'recount'" not in legacy_sql
    finally:
        engine.dispose()


def test_0023_migrated_sqlite_allows_two_serial_observations_to_share_account_and_blocks_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exercise the service against the real migrated SQLite trigger set.

    PostgreSQL owns the deferred terminal/account graph in production, while
    SQLite is the local executable seam for the posting-item validator and all
    immutable fact triggers.  Two serial observations must share the formal
    account dimension; a later attempt to rewrite either the SN edge or its
    movement must fail at the database boundary.
    """

    from itertools import count

    from sqlalchemy.orm import Session

    import test_opening_stocktake_finalize_service as finalize_support
    import test_opening_stocktake_review_service as review_support
    from app.foundation_models import (
        AuditChainHead,
        Permission,
        Role,
        RolePermission,
    )
    from app.inventory_models import (
        InventoryLedgerHead,
        InventoryMovement,
        InventoryMovementSerial,
        SerialCurrentPosition,
        StockAccount,
        StockBalance,
    )
    from app.stocktake_models import StocktakePostingItem

    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0023-two-serials.db'}"
    # This is a current-service integration test.  Keep the migrated schema at
    # the current head so later additive ORM columns (for example the 0033
    # count ledger boundary) remain present while the original 0023 trigger
    # and immutability assertions are exercised.
    command.upgrade(_config(database_url), HEAD_REVISION)
    engine = sa.create_engine(database_url)

    @sa.event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(
        review_support.opening_service,
        "_database_now",
        lambda _db: review_support.NOW,
    )
    monkeypatch.setattr(
        review_support.count_service,
        "_database_now",
        lambda _db: review_support.NOW + review_support.timedelta(hours=1),
    )
    monkeypatch.setattr(
        review_support.disposition_service,
        "_database_now",
        lambda _db: review_support.NOW
        + review_support.timedelta(hours=1, minutes=30),
    )
    review_ticks = count()
    monkeypatch.setattr(
        review_support.review_service,
        "_database_now",
        lambda _db: review_support.NOW
        + review_support.timedelta(hours=2, microseconds=next(review_ticks)),
    )
    try:
        with Session(engine) as db:
            # Migration seeds the production role/permission catalog and both
            # chain heads.  The shared integration fixture deliberately owns
            # its isolated catalog and heads, so clear only those empty seed
            # rows before constructing its local business world.  Current-head
            # material-request route templates reference the fixed role codes,
            # so remove those static template rows first in this disposable DB.
            db.execute(sa.text("DELETE FROM approval_route_step_defs"))
            db.execute(sa.text("DELETE FROM approval_route_versions"))
            db.query(RolePermission).delete()
            db.query(Permission).delete()
            db.query(Role).delete()
            db.query(InventoryLedgerHead).delete()
            head_delete_triggers = db.execute(
                sa.text(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'audit_chain_heads' "
                    "AND upper(sql) LIKE '%BEFORE DELETE%'"
                )
            ).all()
            for trigger_name, _trigger_sql in head_delete_triggers:
                db.execute(sa.text(f'DROP TRIGGER "{trigger_name}"'))
            db.query(AuditChainHead).delete()
            for _trigger_name, trigger_sql in head_delete_triggers:
                db.execute(sa.text(trigger_sql))
            db.commit()

            review_world = review_support.world.__wrapped__(db)
            world = finalize_support.world.__wrapped__(review_world)
            context = (
                finalize_support
                ._exercise_recount_two_serial_observations_share_one_account(
                    world,
                    monkeypatch,
                    verify_tamper=False,
                )
            )

            assert db.scalar(
                sa.select(sa.func.count()).select_from(StockAccount).where(
                    StockAccount.id == context.account_id
                )
            ) == 1
            assert db.get(StockBalance, context.account_id).quantity == 2
            assert db.scalar(
                sa.select(sa.func.count()).select_from(StocktakePostingItem).where(
                    StocktakePostingItem.posting_id == context.posting_id
                )
            ) == 2
            assert db.scalar(
                sa.select(sa.func.count()).select_from(InventoryMovement).where(
                    InventoryMovement.transaction_id == context.transaction_id
                )
            ) == 2
            assert {
                db.get(SerialCurrentPosition, serial_id).stock_account_id
                for serial_id in (
                    context.first_serial_id,
                    context.second_serial_id,
                )
            } == {context.account_id}

            serial_edge = db.scalar(
                sa.select(InventoryMovementSerial).where(
                    InventoryMovementSerial.transaction_id
                    == context.transaction_id,
                    InventoryMovementSerial.serial_id
                    == context.first_serial_id,
                )
            )
            assert serial_edge is not None
            serial_edge.serial_id = context.tamper_serial_id
            with pytest.raises(sa.exc.IntegrityError, match="immutable"):
                db.flush()
            db.rollback()

            movement = db.scalar(
                sa.select(InventoryMovement).where(
                    InventoryMovement.transaction_id == context.transaction_id
                )
            )
            assert movement is not None
            db.delete(movement)
            with pytest.raises(sa.exc.IntegrityError, match="immutable"):
                db.flush()
            db.rollback()
    finally:
        engine.dispose()


def test_0023_sqlite_downgrade_blocks_difference_backed_opening_fact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'opening-observation-block.db'}"
    config = _config(database_url)
    command.upgrade(config, PRE_DEMAND_HEAD_REVISION)
    engine = sa.create_engine(database_url)
    posting_id = "93000000000040008000000000000001"
    task_id = "93000000000040008000000000000002"
    round_id = "93000000000040008000000000000003"
    movement_id = "93000000000040008000000000000004"
    difference_id = "93000000000040008000000000000005"
    try:
        with engine.connect() as connection:
            insert_triggers = connection.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name IN ('stocktake_postings', "
                "'stocktake_posting_items') AND upper(sql) LIKE '%BEFORE INSERT%' "
                "ORDER BY name"
            ).all()
            assert {
                row[0] for row in insert_triggers
            } >= {
                "trg_stocktake_posting_items_validate_insert_0010",
                "trg_stocktake_postings_opening_unique_0022",
            }
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            with connection.begin():
                for trigger_name, _trigger_sql in insert_triggers:
                    connection.exec_driver_sql(
                        f'DROP TRIGGER "{trigger_name}"'
                    )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_postings "
                    "(id, task_id, round_id, posting_kind, "
                    "inventory_transaction_id, total_quantity, "
                    "posted_by_user_id, posted_at, idempotency_key_hash, "
                    "request_hash, created_at) VALUES "
                    "(?, ?, ?, 'opening', NULL, 0, ?, ?, ?, ?, ?)",
                    (
                        posting_id,
                        task_id,
                        round_id,
                        "00000000-0000-0000-0000-000000009301",
                        "2026-08-31 00:00:00+00:00",
                        "a" * 64,
                        "b" * 64,
                        "2026-08-31 00:00:00+00:00",
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_posting_items "
                    "(posting_id, inventory_movement_id, task_id, round_id, "
                    "count_line_id, difference_id, quantity, created_at) "
                    "VALUES (?, ?, ?, ?, NULL, ?, 1, ?)",
                    (
                        posting_id,
                        movement_id,
                        task_id,
                        round_id,
                        difference_id,
                        "2026-08-31 00:00:00+00:00",
                    ),
                )
                for _trigger_name, trigger_sql in insert_triggers:
                    connection.exec_driver_sql(trigger_sql)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="opening observation posting facts"):
        command.downgrade(config, "20260831_0022")

    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0023"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_posting_items_validate_insert_0010'"
            ).scalar_one() == 1
    finally:
        verification.dispose()


def test_0023_postgresql_offline_downgrade_checks_before_restore(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        "20260831_0023:20260831_0022",
        sql=True,
    )
    sql = output.getvalue()
    blocker = "cannot downgrade 0023: opening observation posting facts"
    assert "LOCK TABLE public.stock_accounts" in sql
    assert "item.difference_id IS NOT NULL" in sql
    assert blocker in sql
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE public.stock_accounts "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    assert "GRANT SELECT ON TABLE public.stock_accounts TO star_oam_api" in sql
    assert "GRANT INSERT ON TABLE public.stock_accounts" not in sql
    assert (
        "DROP TRIGGER trg_stock_accounts_opening_observation_commit_0023 "
        "ON public.stock_accounts"
    ) in sql
    assert "opening posting items require physical count lines" in sql
    assert sql.index(blocker) < sql.index(
        "DROP TRIGGER trg_stock_accounts_opening_observation_commit_0023"
    )


def test_0024_postgresql_offline_downgrade_restores_0023_acl(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        "20260831_0024:20260831_0023",
        sql=True,
    )
    sql = output.getvalue()
    assert (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        "FROM PUBLIC, star_oam_api"
    ) in sql
    insert_grant = next(
        statement
        for statement in sql.split(";")
        if "GRANT INSERT ON TABLE" in statement
    )
    select_grant = next(
        statement
        for statement in sql.split(";")
        if "GRANT SELECT ON TABLE" in statement
    )
    assert "public.stock_accounts" in insert_grant
    assert "public.stocktake_postings" in insert_grant
    assert "public.stocktake_tasks" not in insert_grant
    assert "public.stocktake_rounds" not in insert_grant
    assert "public.inventory_freezes" not in insert_grant
    assert "public.qr_codes" not in select_grant
    assert (
        "GRANT UPDATE (status, posted_at, closed_at, version, updated_at) "
        "ON TABLE public.stocktake_tasks TO star_oam_api"
    ) in sql
    assert "ON TABLE public.stocktake_rounds TO star_oam_api" not in sql


def _seed_scope_region_owner_world(engine: sa.Engine) -> dict[str, str]:
    metadata = sa.MetaData()
    metadata.reflect(
        bind=engine,
        only=(
            "organizations",
            "people",
            "users",
            "stock_locations",
            "stocktake_tasks",
        ),
    )
    organizations = metadata.tables["organizations"]
    people = metadata.tables["people"]
    users = metadata.tables["users"]
    locations = metadata.tables["stock_locations"]
    tasks = metadata.tables["stocktake_tasks"]
    now = datetime.now(timezone.utc)

    values = {
        name: uuid.uuid4().hex
        for name in (
            "hq",
            "region",
            "sibling_region",
            "inactive_region",
            "task_department",
            "active_department",
            "inactive_department",
            "nested_owner",
            "inactive_path_owner",
            "cycle_owner",
            "cycle_department",
            "person",
            "location",
            "descendant_location",
            "cross_region_location",
            "cross_region_parent_location",
            "parent_cross_region_location",
            "inactive_parent_location",
            "child_of_inactive_location",
            "inactive_location",
            "cycle_location_a",
            "cycle_location_b",
            "missing_parent_location",
            "missing_parent_id",
            "missing_location",
            "transit_location",
            "cycle_owner_location",
            "task",
        )
    }
    values["user"] = str(uuid.uuid4())

    def organization_row(
        key: str,
        *,
        parent_key: str | None,
        org_type: str,
        status: str = "active",
    ) -> dict[str, object]:
        return {
            "id": values[key],
            "external_object_id": None,
            "code": f"SCOPE-{key.upper()}-{uuid.uuid4().hex[:8]}",
            "name": f"范围门禁 {key}",
            "parent_id": values[parent_key] if parent_key else None,
            "org_type": org_type,
            "province_code": None,
            "status": status,
            "updated_at": now,
            "created_at": now,
        }

    def location_row(
        key: str,
        *,
        owner_key: str,
        parent_key: str | None = None,
        location_type: str = "region",
        status: str = "active",
        custodian: bool = False,
    ) -> dict[str, object]:
        return {
            "id": values[key],
            "code": f"SCOPE-LOC-{key.upper()}-{uuid.uuid4().hex[:8]}",
            "name": f"范围门禁库位 {key}",
            "location_type": location_type,
            "owner_org_id": values[owner_key],
            "parent_id": values[parent_key] if parent_key else None,
            "custodian_person_id": values["person"] if custodian else None,
            "status": status,
            "created_at": now,
            "updated_at": now,
        }

    with engine.begin() as connection:
        connection.execute(
            organizations.insert(),
            organization_row(
                "hq", parent_key=None, org_type="headquarters"
            ),
        )
        connection.execute(
            organizations.insert(),
            [
                organization_row(
                    "region", parent_key="hq", org_type="region_company"
                ),
                organization_row(
                    "sibling_region",
                    parent_key="hq",
                    org_type="region_company",
                ),
                organization_row(
                    "inactive_region",
                    parent_key="hq",
                    org_type="region_company",
                    status="inactive",
                ),
                organization_row(
                    "task_department",
                    parent_key="hq",
                    org_type="department",
                ),
            ],
        )
        connection.execute(
            organizations.insert(),
            [
                organization_row(
                    "active_department",
                    parent_key="region",
                    org_type="department",
                ),
                organization_row(
                    "inactive_department",
                    parent_key="region",
                    org_type="department",
                    status="inactive",
                ),
            ],
        )
        connection.execute(
            organizations.insert(),
            [
                organization_row(
                    "nested_owner",
                    parent_key="active_department",
                    org_type="region_company",
                ),
                organization_row(
                    "inactive_path_owner",
                    parent_key="inactive_department",
                    org_type="region_company",
                ),
                organization_row(
                    "cycle_owner",
                    parent_key="region",
                    org_type="region_company",
                ),
                organization_row(
                    "cycle_department",
                    parent_key="region",
                    org_type="department",
                ),
            ],
        )
        connection.execute(
            organizations.update()
            .where(organizations.c.id == values["cycle_owner"])
            .values(parent_id=values["cycle_department"])
        )
        connection.execute(
            organizations.update()
            .where(organizations.c.id == values["cycle_department"])
            .values(parent_id=values["cycle_owner"])
        )
        connection.execute(
            people.insert(),
            {
                "id": values["person"],
                "external_object_id": None,
                "organization_id": values["hq"],
                "employee_no": f"SCOPE-{uuid.uuid4().hex[:10]}",
                "name": "范围门禁测试人员",
                "mobile_encrypted": None,
                "mobile_hash": None,
                "employment_status": "active",
                "source_updated_at": now,
                "updated_at": now,
                "created_at": now,
            },
        )
        connection.execute(
            users.insert(),
            {
                "id": values["user"],
                "person_id": values["person"],
                "account_status": "active",
                "last_login_at": None,
                "authorization_version": 1,
                "mobile": f"199{uuid.uuid4().int % 10**8:08d}",
                "name": "范围门禁测试人员",
                "password_hash": "local-test-only",
                "role": "technician",
                "province": None,
                "is_active": True,
                "require_password_change": False,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            locations.insert(),
            [
                location_row("location", owner_key="region"),
                location_row(
                    "cross_region_location",
                    owner_key="sibling_region",
                ),
                location_row(
                    "cross_region_parent_location",
                    owner_key="sibling_region",
                ),
                location_row(
                    "inactive_parent_location",
                    owner_key="region",
                    status="inactive",
                ),
                location_row(
                    "inactive_location",
                    owner_key="region",
                    status="inactive",
                ),
                location_row("cycle_location_a", owner_key="region"),
                location_row("cycle_location_b", owner_key="region"),
                location_row(
                    "missing_parent_location",
                    owner_key="region",
                    parent_key="missing_parent_id",
                ),
                location_row(
                    "transit_location",
                    owner_key="region",
                    location_type="transit",
                ),
                location_row(
                    "cycle_owner_location",
                    owner_key="cycle_owner",
                ),
            ],
        )
        connection.execute(
            locations.insert(),
            [
                location_row(
                    "descendant_location",
                    owner_key="nested_owner",
                    parent_key="location",
                    location_type="personal",
                    custodian=True,
                ),
                location_row(
                    "parent_cross_region_location",
                    owner_key="region",
                    parent_key="cross_region_parent_location",
                ),
                location_row(
                    "child_of_inactive_location",
                    owner_key="region",
                    parent_key="inactive_parent_location",
                ),
            ],
        )
        connection.execute(
            locations.update()
            .where(locations.c.id == values["cycle_location_a"])
            .values(parent_id=values["cycle_location_b"])
        )
        connection.execute(
            locations.update()
            .where(locations.c.id == values["cycle_location_b"])
            .values(parent_id=values["cycle_location_a"])
        )
        connection.execute(
            tasks.insert(),
            _stocktake_task_row(
                task_id=values["task"],
                task_no=f"SCOPE-TASK-{uuid.uuid4().hex[:12]}",
                region_org_id=values["region"],
                user_id=values["user"],
                now=now,
            ),
        )
    return values


def _stocktake_task_row(
    *,
    task_id: str,
    task_no: str,
    region_org_id: str,
    user_id: str,
    now: datetime,
) -> dict[str, object]:
    return {
        "id": task_id,
        "task_no": task_no,
        "task_type": "full",
        "region_org_id": region_org_id,
        "status": "draft",
        "blind_count": True,
        "cutoff_ledger_cursor": None,
        "cutoff_at": None,
        "scope_manifest_sha256": None,
        "snapshot_manifest_sha256": None,
        "control_source_system_id": None,
        "control_sync_run_id": None,
        "control_snapshot_at": None,
        "control_manifest_sha256": None,
        "current_round_no": 0,
        "created_by_user_id": user_id,
        "deadline": None,
        "issued_at": None,
        "frozen_at": None,
        "submitted_at": None,
        "posted_at": None,
        "closed_at": None,
        "cancelled_at": None,
        "version": 0,
        "note": "",
        "created_at": now,
        "updated_at": now,
    }


def _add_stocktake_scope(
    connection,
    world: dict[str, str],
    *,
    owner_org_id: str,
    location_id: str | None = None,
    task_id: str | None = None,
    scope_no: int = 1,
) -> str:
    scopes = sa.Table("stocktake_scopes", sa.MetaData(), autoload_with=connection)
    scope_id = uuid.uuid4().hex
    scope_key = f"scope-guard:{task_id or world['task']}:{scope_no}:{scope_id}"
    connection.execute(
        scopes.insert(),
        {
            "id": scope_id,
            "task_id": task_id or world["task"],
            "scope_no": scope_no,
            "scope_mode": "location_all",
            "location_id": location_id or world["location"],
            "owner_org_id": owner_org_id,
            "custodian_person_id_snapshot": None,
            "assignee_user_id": world["user"],
            "material_id": None,
            "condition_code": None,
            "availability_bucket": None,
            "scope_key": scope_key,
            "scope_sha256": hashlib.sha256(
                scope_key.encode("utf-8")
            ).hexdigest(),
            "created_at": datetime.now(timezone.utc),
        },
    )
    return scope_id


def test_0025_sqlite_upgrade_preflight_rejects_cross_region_scope_without_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0025-polluted.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0024")
    engine = sa.create_engine(database_url)
    try:
        world = _seed_scope_region_owner_world(engine)
        with engine.begin() as connection:
            scope_id = _add_stocktake_scope(
                connection,
                world,
                owner_org_id=world["sibling_region"],
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="0025 preflight failed"):
        command.upgrade(config, HEAD_REVISION)

    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0024"
            assert connection.exec_driver_sql(
                "SELECT owner_org_id FROM stocktake_scopes WHERE id = ?",
                (scope_id,),
            ).scalar_one() == world["sibling_region"]
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_scopes_region_owner_0025'"
            ).scalar_one() == 0
    finally:
        verification.dispose()


@pytest.mark.parametrize(
    "location_key",
    (
        "cross_region_location",
        "parent_cross_region_location",
        "child_of_inactive_location",
        "cycle_location_a",
        "missing_parent_location",
    ),
)
def test_0025_sqlite_upgrade_preflight_rejects_invalid_physical_location_graph(
    tmp_path: Path,
    monkeypatch,
    location_key: str,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = (
        f"sqlite+pysqlite:///{tmp_path / f'0025-location-{location_key}.db'}"
    )
    config = _config(database_url)
    command.upgrade(config, "20260831_0024")
    engine = sa.create_engine(database_url)
    try:
        world = _seed_scope_region_owner_world(engine)
        with engine.begin() as connection:
            scope_id = _add_stocktake_scope(
                connection,
                world,
                owner_org_id=world["nested_owner"],
                location_id=world[location_key],
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="0025 preflight failed"):
        command.upgrade(config, HEAD_REVISION)

    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            persisted = connection.exec_driver_sql(
                "SELECT location_id FROM stocktake_scopes WHERE id = ?",
                (scope_id,),
            ).scalar_one()
            assert persisted == world[location_key]
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0024"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_scopes_region_owner_0025'"
            ).scalar_one() == 0
    finally:
        verification.dispose()


def test_0025_sqlite_upgrade_preflight_accepts_active_descendant_location(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0025-location-valid.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0024")
    engine = sa.create_engine(database_url)
    try:
        world = _seed_scope_region_owner_world(engine)
        with engine.begin() as connection:
            scope_id = _add_stocktake_scope(
                connection,
                world,
                owner_org_id=world["nested_owner"],
                location_id=world["descendant_location"],
            )
    finally:
        engine.dispose()

    command.upgrade(config, PRE_DEMAND_HEAD_REVISION)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT location_id FROM stocktake_scopes WHERE id = ?",
                (scope_id,),
            ).scalar_one() == world["descendant_location"]
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PRE_DEMAND_HEAD_REVISION
    finally:
        verification.dispose()


def test_0025_sqlite_insert_guard_accepts_only_active_region_owner_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0025-scope-guard.db'}"
    config = _config(database_url)
    command.upgrade(config, HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        world = _seed_scope_region_owner_world(engine)
        with engine.begin() as connection:
            _add_stocktake_scope(
                connection,
                world,
                owner_org_id=world["nested_owner"],
            )
            _add_stocktake_scope(
                connection,
                world,
                owner_org_id=world["nested_owner"],
                location_id=world["descendant_location"],
                scope_no=2,
            )

        for scope_no, owner_key in enumerate(
            (
                "sibling_region",
                "inactive_region",
                "task_department",
                "inactive_path_owner",
                "cycle_owner",
            ),
            start=10,
        ):
            with pytest.raises(sa.exc.IntegrityError, match="active task region"):
                with engine.begin() as connection:
                    _add_stocktake_scope(
                        connection,
                        world,
                        owner_org_id=world[owner_key],
                        scope_no=scope_no,
                    )

        for scope_no, location_key in enumerate(
            (
                "cross_region_location",
                "parent_cross_region_location",
                "child_of_inactive_location",
                "inactive_location",
                "cycle_location_a",
                "missing_parent_location",
                "missing_location",
                "transit_location",
                "cycle_owner_location",
            ),
            start=30,
        ):
            with pytest.raises(sa.exc.IntegrityError, match="active task region"):
                with engine.begin() as connection:
                    _add_stocktake_scope(
                        connection,
                        world,
                        owner_org_id=world["nested_owner"],
                        location_id=world[location_key],
                        scope_no=scope_no,
                    )

        inactive_task_id = uuid.uuid4().hex
        department_task_id = uuid.uuid4().hex
        task_table = sa.Table(
            "stocktake_tasks", sa.MetaData(), autoload_with=engine
        )
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                task_table.insert(),
                [
                    _stocktake_task_row(
                        task_id=inactive_task_id,
                        task_no=f"SCOPE-INACTIVE-{uuid.uuid4().hex[:10]}",
                        region_org_id=world["inactive_region"],
                        user_id=world["user"],
                        now=now,
                    ),
                    _stocktake_task_row(
                        task_id=department_task_id,
                        task_no=f"SCOPE-DEPT-{uuid.uuid4().hex[:10]}",
                        region_org_id=world["task_department"],
                        user_id=world["user"],
                        now=now,
                    ),
                ],
            )
        for scope_no, invalid_task in (
            (20, inactive_task_id),
            (21, department_task_id),
        ):
            with pytest.raises(sa.exc.IntegrityError, match="active task region"):
                with engine.begin() as connection:
                    _add_stocktake_scope(
                        connection,
                        world,
                        owner_org_id=world["nested_owner"],
                        task_id=invalid_task,
                        scope_no=scope_no,
                    )

        with engine.connect() as connection:
            trigger_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_scopes_region_owner_0025'"
            ).scalar_one()
        assert "WITH RECURSIVE asset_owner_path" in trigger_sql
        assert "location_path(" in trigger_sql
        assert "location_owner_path(" in trigger_sql
        assert "region.status = 'active'" in trigger_sql
        assert "region.org_type = 'region_company'" in trigger_sql
        assert "owner.status = 'active'" in trigger_sql
        assert "owner.org_type = 'region_company'" in trigger_sql
        assert "target_location.status = 'active'" in trigger_sql
        assert "target_location.location_type IN ('region', 'personal')" in trigger_sql
        assert "path.location_status <> 'active'" in trigger_sql
        assert "path.parent_location_id IS NOT NULL" in trigger_sql
        assert "SELECT count(*) FROM stock_locations" in trigger_sql
        assert "path.depth > (SELECT count(*)" in trigger_sql
    finally:
        engine.dispose()


def test_0025_downgrade_is_blocked_with_scopes_and_safe_when_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    populated_url = f"sqlite+pysqlite:///{tmp_path / '0025-populated.db'}"
    populated_config = _config(populated_url)
    command.upgrade(populated_config, PRE_DEMAND_HEAD_REVISION)
    populated_engine = sa.create_engine(populated_url)
    try:
        world = _seed_scope_region_owner_world(populated_engine)
        with populated_engine.begin() as connection:
            _add_stocktake_scope(
                connection,
                world,
                owner_org_id=world["nested_owner"],
            )
    finally:
        populated_engine.dispose()

    with pytest.raises(RuntimeError, match="cannot downgrade 0025"):
        command.downgrade(populated_config, "20260831_0024")

    populated_verification = sa.create_engine(populated_url)
    try:
        with populated_verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0025"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_scopes_region_owner_0025'"
            ).scalar_one() == 1
    finally:
        populated_verification.dispose()

    empty_url = f"sqlite+pysqlite:///{tmp_path / '0025-empty.db'}"
    empty_config = _config(empty_url)
    command.upgrade(empty_config, PRE_DEMAND_HEAD_REVISION)
    command.downgrade(empty_config, "20260831_0024")
    empty_engine = sa.create_engine(empty_url)
    try:
        with empty_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0024"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_stocktake_scopes_region_owner_0025'"
            ).scalar_one() == 0
    finally:
        empty_engine.dispose()


def test_0025_postgresql_offline_downgrade_checks_before_guard_removal(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    config = _config(
        "postgresql+psycopg://offline:offline@localhost/offline",
        output_buffer=output,
    )
    command.downgrade(
        config,
        "20260831_0025:20260831_0024",
        sql=True,
    )
    sql = output.getvalue()
    blocker = "cannot downgrade 0025 while formal stocktake scopes exist"
    drop_trigger = (
        "DROP TRIGGER trg_stocktake_scopes_region_owner_0025 "
        "ON public.stocktake_scopes"
    )
    assert blocker in sql
    assert drop_trigger in sql
    assert sql.index(blocker) < sql.index(drop_trigger)
    assert "GRANT " not in sql
    assert "REVOKE ALL PRIVILEGES" not in sql


def _insert_reconciliation_projection_sentinel(
    connection: sa.Connection,
) -> tuple[str, str]:
    source_id = "92000000000040008000000000000001"
    run_id = "92000000000040008000000000000002"
    timestamp = "2026-08-31 12:00:00.000000"
    connection.exec_driver_sql(
        "INSERT INTO source_systems "
        "(id, code, name, mode, enabled, configuration_jsonb, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_id,
            "0026-sentinel",
            "0026 sentinel",
            "read_only",
            1,
            "{}",
            timestamp,
            timestamp,
        ),
    )
    connection.exec_driver_sql(
        "INSERT INTO reconciliation_runs "
        "(id, run_key, source_system_id, scope, external_snapshot_at, "
        "local_ledger_cursor, status, summary_jsonb, started_at, completed_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            "0026-sentinel-run",
            source_id,
            "0026-sentinel-scope",
            timestamp,
            "1",
            "differences",
            "{}",
            timestamp,
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    return source_id, run_id


def test_0026_sqlite_preflight_rejects_unproven_prototype_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0026-preflight.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0025")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_reconciliation_projection_sentinel(connection)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="existing reconciliation projections"):
        command.upgrade(config, HEAD_REVISION)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0025"
    finally:
        verification.dispose()


def test_0026_sqlite_projection_guards_and_populated_downgrade_blocker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0026-guards.db'}"
    config = _config(database_url)
    command.upgrade(config, HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE '%reconciliation%0026'"
                ).all()
            }
            assert {
                "trg_reconciliation_commands_insert_guard_0026",
                "trg_reconciliation_commands_update_guard_0026",
                "trg_reconciliation_commands_delete_guard_0026",
                "trg_opening_reconciliation_runs_insert_guard_0026",
                "trg_opening_reconciliation_runs_update_guard_0026",
                "trg_opening_reconciliation_runs_delete_guard_0026",
                "trg_opening_reconciliation_items_insert_guard_0026",
                "trg_opening_reconciliation_items_update_guard_0026",
                "trg_opening_reconciliation_items_delete_guard_0026",
                "trg_reconciliation_runs_update_guard_0026",
                "trg_reconciliation_runs_delete_guard_0026",
                "trg_reconciliation_items_update_guard_0026",
                "trg_reconciliation_items_delete_guard_0026",
                "trg_reconciliation_runs_insert_guard_0026",
                "trg_reconciliation_items_insert_guard_0026",
                "trg_stocktake_tasks_reconciliation_close_guard_0026",
            } <= trigger_names
            run_binding_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'opening_control_reconciliation_runs'"
            ).scalar_one()
            item_binding_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'opening_control_reconciliation_items'"
            ).scalar_one()
            assert run_binding_sql.count("DEFERRABLE INITIALLY DEFERRED") == 2
            assert item_binding_sql.count("DEFERRABLE INITIALLY DEFERRED") == 1

            connection.rollback()
            transaction = connection.begin()
            with pytest.raises(sa.exc.DBAPIError, match="invariant violated"):
                _insert_reconciliation_projection_sentinel(connection)
            transaction.rollback()
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM reconciliation_runs"
            ).scalar_one() == 0
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM source_systems "
                "WHERE code = '0026-sentinel'"
            ).scalar_one() == 0

        # Force one impossible orphan into this test-only database by briefly
        # removing only the run INSERT guard.  This simulates privileged
        # out-of-band corruption so the downgrade preflight can be exercised;
        # the runtime role cannot drop guards.
        with engine.begin() as connection:
            run_insert_trigger_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_reconciliation_runs_insert_guard_0026'"
            ).scalar_one()
            connection.exec_driver_sql(
                "DROP TRIGGER trg_reconciliation_runs_insert_guard_0026"
            )
            _, run_id = _insert_reconciliation_projection_sentinel(connection)
            connection.exec_driver_sql(run_insert_trigger_sql)

        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.rollback()
            transaction = connection.begin()
            with pytest.raises(sa.exc.DBAPIError, match="invariant violated"):
                connection.exec_driver_sql(
                    "INSERT INTO reconciliation_items "
                    "(id, run_id, business_key, external_qty, local_qty, "
                    "difference, status, explanation, evidence_file_id, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "92000000000040008000000000000003",
                        run_id,
                        "0026-orphan-item",
                        1,
                        0,
                        1,
                        "difference",
                        "",
                        None,
                        "2026-08-31 12:00:00.000000",
                        "2026-08-31 12:00:00.000000",
                    ),
                )
            transaction.rollback()
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM reconciliation_items"
            ).scalar_one() == 0
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="cannot downgrade 0026"):
        command.downgrade(config, "20260831_0025")


def test_0026_sqlite_deferred_create_seal_blocks_commit_without_command(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0026-create-seal.db'}"
    command.upgrade(_config(database_url), PRE_DEMAND_HEAD_REVISION)
    engine = sa.create_engine(database_url)
    run_id = "93000000000040008000000000000001"
    command_id = "93000000000040008000000000000002"
    timestamp = "2026-08-31 12:00:00.000000"
    insert_guard_names = (
        "trg_reconciliation_runs_insert_guard_0026",
        "trg_reconciliation_commands_insert_guard_0026",
        "trg_opening_reconciliation_runs_insert_guard_0026",
        "trg_opening_reconciliation_consumptions_insert_guard_0026",
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            with connection.begin():
                insert_guard_sql = {
                    name: connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                        "AND name = ?",
                        (name,),
                    ).scalar_one()
                    for name in insert_guard_names
                }
                for name in insert_guard_names:
                    connection.exec_driver_sql(f"DROP TRIGGER {name}")
                connection.exec_driver_sql(
                    "INSERT INTO source_systems "
                    "(id, code, name, mode, enabled, configuration_jsonb, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "93000000000040008000000000000003",
                        "0026-create-seal",
                        "0026 create seal",
                        "read_only",
                        1,
                        "{}",
                        timestamp,
                        timestamp,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO reconciliation_runs "
                    "(id, run_key, source_system_id, scope, external_snapshot_at, "
                    "local_ledger_cursor, status, summary_jsonb, started_at, "
                    "completed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        "0026-create-seal-run",
                        "93000000000040008000000000000003",
                        "0026-create-seal-scope",
                        timestamp,
                        "1",
                        "differences",
                        "{}",
                        timestamp,
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO reconciliation_commands "
                    "(id, operation, run_id, target_version, idempotency_key_hash, "
                    "request_reference, request_hash, result_hash, request_jsonb, "
                    "result_jsonb, actor_user_id, actor_person_id, "
                    "actor_role_assignment_id, authorization_version, occurred_at, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        command_id,
                        "create_opening",
                        run_id,
                        0,
                        "1" * 64,
                        "opening-reconciliation-request-" + "1" * 64,
                        "2" * 64,
                        "3" * 64,
                        "{}",
                        "{}",
                        "93000000-0000-4000-8000-000000000004",
                        "93000000000040008000000000000005",
                        "93000000000040008000000000000006",
                        1,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.exec_driver_sql(
                    "INSERT INTO "
                    "opening_control_reconciliation_command_consumptions "
                    "(command_id, run_id, operation, target_version, consumed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (command_id, run_id, "create_opening", 0, timestamp),
                )
                connection.exec_driver_sql(
                    "INSERT INTO opening_control_reconciliation_runs "
                    "(run_id, create_command_id, task_id, round_id, "
                    "region_org_id, posting_id, control_sync_run_id, version, "
                    "item_count, item_manifest_sha256, created_by_user_id, "
                    "created_by_person_id, created_role_assignment_id, "
                    "created_authorization_version, approved_by_user_id, "
                    "approved_by_person_id, approved_role_assignment_id, "
                    "approved_authorization_version, approved_at, approval_comment, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        command_id,
                        "93000000000040008000000000000007",
                        "93000000000040008000000000000008",
                        "93000000000040008000000000000009",
                        "9300000000004000800000000000000a",
                        "9300000000004000800000000000000b",
                        0,
                        1,
                        "a" * 64,
                        "93000000-0000-4000-8000-000000000004",
                        "93000000000040008000000000000005",
                        "93000000000040008000000000000006",
                        1,
                        None,
                        None,
                        None,
                        None,
                        None,
                        "",
                        timestamp,
                        timestamp,
                    ),
                )
                for name in insert_guard_names:
                    connection.exec_driver_sql(insert_guard_sql[name])

            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
            assert not any(
                row[2] == "reconciliation_commands"
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_check(opening_control_reconciliation_runs)"
                ).all()
            )
            connection.rollback()

            transaction = connection.begin()
            delete_guard_sql = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_reconciliation_commands_delete_guard_0026'"
            ).scalar_one()
            connection.exec_driver_sql(
                "DROP TRIGGER trg_reconciliation_commands_delete_guard_0026"
            )
            connection.exec_driver_sql(
                "DELETE FROM reconciliation_commands WHERE id = ?",
                (command_id,),
            )
            assert any(
                row[2] == "reconciliation_commands"
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_check(opening_control_reconciliation_runs)"
                ).all()
            )
            with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                transaction.commit()
            # SQLite keeps the failed COMMIT transaction open.  SQLAlchemy's
            # RootTransaction is already inactive, so roll the DBAPI handle
            # back explicitly before re-reading the durable state.
            connection.connection.driver_connection.rollback()
            connection.rollback()
            connection.exec_driver_sql(delete_guard_sql)
            connection.commit()
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM reconciliation_commands WHERE id = ?",
                (command_id,),
            ).scalar_one() == 1
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_reconciliation_commands_delete_guard_0026'"
            ).scalar_one() == 1
    finally:
        engine.dispose()


def test_0026_migrated_sqlite_service_commits_complete_consumed_graph(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exercise the real command/projection/effect/seal order on migrated DDL."""

    from sqlalchemy.orm import Session

    import test_opening_control_reconciliation_service as reconciliation_test
    import test_opening_stocktake_review_service as review_test

    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0026-service-graph.db'}"
    # The fixture executes the current opening/reconciliation services rather
    # than a frozen 0026 service binary, so it requires the current additive
    # schema while still proving the 0026 command/projection guards.
    command.upgrade(_config(database_url), HEAD_REVISION)
    engine = sa.create_engine(database_url)

    @sa.event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    review_test._fixed_database_times.__wrapped__(monkeypatch)
    reconciliation_test._fixed_reconciliation_times.__wrapped__(monkeypatch)
    try:
        # The migration seeds the production RBAC rows.  The reusable service
        # fixture deliberately creates its own UUID-backed equivalents, so
        # clear only those empty-DB seeds in this isolated integration DB.
        # Current-head approval route templates hold role-code foreign keys.
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM approval_route_step_defs")
            connection.exec_driver_sql("DELETE FROM approval_route_versions")
            connection.exec_driver_sql("DELETE FROM role_permissions")
            connection.exec_driver_sql("DELETE FROM permissions")
            connection.exec_driver_sql("DELETE FROM roles")
            head_delete_guard = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_audit_chain_heads_no_delete_0015'"
            ).scalar_one()
            connection.exec_driver_sql(
                "DROP TRIGGER trg_audit_chain_heads_no_delete_0015"
            )
            connection.exec_driver_sql("DELETE FROM audit_chain_heads")
            connection.exec_driver_sql(head_delete_guard)
            connection.exec_driver_sql("DELETE FROM inventory_ledger_heads")
        with Session(engine) as session:
            review_world = review_test.world.__wrapped__(session)
            world = reconciliation_test.world.__wrapped__(review_world)
            posted = reconciliation_test._posted_task(world)
            started = reconciliation_test._start_reconciliation(
                world,
                posted,
                key="opening-reconciliation-migrated-create-0001",
            )
            session.commit()
            session.expire_all()
            created_detail = (
                reconciliation_test.opening_control_reconciliation_detail(
                    session,
                    actor=world.principals["manager_x"],
                    reconciliation_run_id=started.reconciliation_run_id,
                )
            )
            assert created_detail.version == 0
            assert created_detail.status == "differences"
            with pytest.raises(sa.exc.DBAPIError, match="invariant violated"):
                session.execute(
                    sa.text(
                        "UPDATE stocktake_tasks SET status = 'closed', "
                        "closed_at = :closed_at, version = version + 1, "
                        "updated_at = :closed_at WHERE id = :task_id"
                    ),
                    {
                        "closed_at": started.created_at.astimezone(
                            timezone.utc
                        ).replace(tzinfo=None).isoformat(
                            " ", timespec="microseconds"
                        ),
                        "task_id": posted.task.id.hex,
                    },
                )
            session.rollback()
            explained = (
                reconciliation_test.explain_opening_control_reconciliation(
                    session,
                    actor=world.principals["manager_x"],
                    command=reconciliation_test._explain_command(
                        world,
                        started.reconciliation_run_id,
                        started.version,
                    ),
                    idempotency_key=(
                        "opening-reconciliation-migrated-explain-0001"
                    ),
                    request_id=(
                        "opening-reconciliation-migrated-explain-0001-request"
                    ),
                )
            )
            session.commit()
            session.expire_all()
            explained_detail = (
                reconciliation_test.opening_control_reconciliation_detail(
                    session,
                    actor=world.principals["admin_two"],
                    reconciliation_run_id=started.reconciliation_run_id,
                )
            )
            assert explained_detail.version == 1
            assert all(row.status == "explained" for row in explained_detail.items)

            # The surviving target-1 explain command cannot be reused to
            # advance the projection to target 2.
            with pytest.raises(sa.exc.DBAPIError, match="invariant violated"):
                session.execute(
                    sa.text(
                        "UPDATE opening_control_reconciliation_runs "
                        "SET version = 2, updated_at = :updated_at "
                        "WHERE run_id = :run_id"
                    ),
                    {
                        "run_id": started.reconciliation_run_id.hex,
                        "updated_at": "2026-08-30 18:00:00.000000",
                    },
                )
            session.rollback()

            # A command timestamp cannot move behind the version it consumes;
            # otherwise the append-only graph would be permanently unreadable.
            monkeypatch.setattr(
                reconciliation_test.reconciliation_service,
                "_database_now",
                lambda _db: reconciliation_test.NOW + timedelta(hours=2),
            )
            with pytest.raises(
                reconciliation_test.OpeningControlReconciliationError
            ) as stale_explain:
                reconciliation_test.explain_opening_control_reconciliation(
                    session,
                    actor=world.principals["manager_x"],
                    command=reconciliation_test._explain_command(
                        world,
                        started.reconciliation_run_id,
                        explained.version,
                    ),
                    idempotency_key=(
                        "opening-reconciliation-migrated-stale-explain-0001"
                    ),
                    request_id=(
                        "opening-reconciliation-migrated-stale-explain-request"
                    ),
                )
            assert stale_explain.value.__cause__ is not None
            session.rollback()

            with pytest.raises(
                reconciliation_test.OpeningControlReconciliationError
            ) as stale_approve:
                reconciliation_test.approve_opening_control_reconciliation(
                    session,
                    actor=world.principals["admin_two"],
                    command=(
                        reconciliation_test.
                        ApproveOpeningControlReconciliationCommand(
                            reconciliation_run_id=(
                                started.reconciliation_run_id
                            ),
                            expected_version=explained.version,
                            comment="时间回退的批准不得写入",
                        )
                    ),
                    idempotency_key=(
                        "opening-reconciliation-migrated-stale-approve-0001"
                    ),
                    request_id=(
                        "opening-reconciliation-migrated-stale-approve-request"
                    ),
                )
            assert stale_approve.value.__cause__ is not None
            session.rollback()

            monkeypatch.setattr(
                reconciliation_test.reconciliation_service,
                "_database_now",
                lambda _db: reconciliation_test.NOW + timedelta(hours=4),
            )
            reexplained = (
                reconciliation_test.explain_opening_control_reconciliation(
                    session,
                    actor=world.principals["manager_x"],
                    command=reconciliation_test._explain_command(
                        world,
                        started.reconciliation_run_id,
                        explained.version,
                    ),
                    idempotency_key=(
                        "opening-reconciliation-migrated-reexplain-0001"
                    ),
                    request_id=(
                        "opening-reconciliation-migrated-reexplain-0001-request"
                    ),
                )
            )
            session.commit()
            session.expire_all()
            reexplained_detail = (
                reconciliation_test.opening_control_reconciliation_detail(
                    session,
                    actor=world.principals["admin_two"],
                    reconciliation_run_id=started.reconciliation_run_id,
                )
            )
            assert reexplained_detail.version == 2

            monkeypatch.setattr(
                reconciliation_test.reconciliation_service,
                "_database_now",
                lambda _db: reconciliation_test.NOW + timedelta(hours=5),
            )
            approved = (
                reconciliation_test.approve_opening_control_reconciliation(
                    session,
                    actor=world.principals["admin_two"],
                    command=(
                        reconciliation_test.
                        ApproveOpeningControlReconciliationCommand(
                            reconciliation_run_id=(
                                started.reconciliation_run_id
                            ),
                            expected_version=reexplained.version,
                            comment="总部复核迁移后完整封印图，同意核销",
                        )
                    ),
                    idempotency_key=(
                        "opening-reconciliation-migrated-approve-0001"
                    ),
                    request_id=(
                        "opening-reconciliation-migrated-approve-0001-request"
                    ),
                )
            )
            session.commit()
            session.expire_all()
            approved_detail = (
                reconciliation_test.opening_control_reconciliation_detail(
                    session,
                    actor=world.principals["manager_x"],
                    reconciliation_run_id=started.reconciliation_run_id,
                )
            )
            assert approved.status == "approved"
            assert approved_detail.status == "approved"
            assert approved_detail.version == 3
            with pytest.raises(sa.exc.DBAPIError, match="invariant violated"):
                session.execute(
                    sa.text(
                        "UPDATE stocktake_tasks SET status = 'closed', "
                        "closed_at = :closed_at, version = version + 1, "
                        "updated_at = :closed_at WHERE id = :task_id"
                    ),
                    {
                        "closed_at": (
                            approved.approved_at.astimezone(timezone.utc)
                            .replace(tzinfo=None)
                            - timedelta(microseconds=1)
                        ).isoformat(" ", timespec="microseconds"),
                        "task_id": posted.task.id.hex,
                    },
                )
            session.rollback()
            assert session.execute(
                sa.text(
                    "SELECT count(*) FROM reconciliation_commands "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": started.reconciliation_run_id.hex},
            ).scalar_one() == 4
            assert session.execute(
                sa.text(
                    "SELECT count(*) FROM "
                    "opening_control_reconciliation_command_consumptions "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": started.reconciliation_run_id.hex},
            ).scalar_one() == 4
            assert session.connection().exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).all() == []

            # Exercise the command -> consumption deferred half of the seal.
            # Guards are removed only in this isolated migration test so a
            # deliberately incomplete graph can reach COMMIT.
            command_insert_guard = session.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = "
                    "'trg_reconciliation_commands_insert_guard_0026'"
                )
            ).scalar_one()
            run_update_guard = session.execute(
                sa.text(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = "
                    "'trg_opening_reconciliation_runs_update_guard_0026'"
                )
            ).scalar_one()
            session.rollback()
            session.execute(
                sa.text(
                    "DROP TRIGGER "
                    "trg_reconciliation_commands_insert_guard_0026"
                )
            )
            session.commit()

            def insert_unsealed_command(
                *, target_version: int, suffix: str
            ) -> None:
                session.execute(
                    sa.text(
                        "INSERT INTO reconciliation_commands "
                        "(id, operation, run_id, target_version, "
                        "idempotency_key_hash, request_reference, request_hash, "
                        "result_hash, request_jsonb, result_jsonb, actor_user_id, "
                        "actor_person_id, actor_role_assignment_id, "
                        "authorization_version, occurred_at, created_at) "
                        "SELECT :id, 'explain_opening', run_id, :target_version, "
                        ":idempotency_hash, :request_reference, :request_hash, "
                        ":result_hash, '{}', '{}', actor_user_id, actor_person_id, "
                        "actor_role_assignment_id, authorization_version, "
                        ":occurred_at, :occurred_at "
                        "FROM reconciliation_commands "
                        "WHERE run_id = :run_id AND target_version = 1"
                    ),
                    {
                        "id": uuid.uuid4().hex,
                        "target_version": target_version,
                        "idempotency_hash": suffix * 64,
                        "request_reference": (
                            "opening-reconciliation-request-" + suffix * 64
                        ),
                        "request_hash": chr(ord(suffix) + 1) * 64,
                        "result_hash": chr(ord(suffix) + 2) * 64,
                        "occurred_at": "2026-08-30 19:00:00.000000",
                        "run_id": started.reconciliation_run_id.hex,
                    },
                )

            try:
                with pytest.raises(sa.exc.IntegrityError, match="CHECK"):
                    insert_unsealed_command(target_version=4, suffix="g")
                session.rollback()

                insert_unsealed_command(target_version=4, suffix="4")
                with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                    session.commit()
                session.rollback()

                # A partial projection without effects/seal is equally unable
                # to commit, even if its row-level guard is test-only removed.
                session.execute(
                    sa.text(
                        "DROP TRIGGER "
                        "trg_opening_reconciliation_runs_update_guard_0026"
                    )
                )
                session.commit()
                insert_unsealed_command(target_version=4, suffix="7")
                session.execute(
                    sa.text(
                        "UPDATE opening_control_reconciliation_runs "
                        "SET version = 4, updated_at = :updated_at "
                        "WHERE run_id = :run_id"
                    ),
                    {
                        "updated_at": "2026-08-30 19:00:00.000000",
                        "run_id": started.reconciliation_run_id.hex,
                    },
                )
                with pytest.raises(sa.exc.IntegrityError, match="FOREIGN KEY"):
                    session.commit()
                session.rollback()

                # The physical unique key rejects a second command for an
                # already consumed target, independent of trigger logic.
                with pytest.raises(sa.exc.IntegrityError, match="UNIQUE"):
                    insert_unsealed_command(target_version=3, suffix="a")
                session.rollback()
            finally:
                if session.execute(
                    sa.text(
                        "SELECT count(*) FROM sqlite_master "
                        "WHERE type = 'trigger' AND name = "
                        "'trg_opening_reconciliation_runs_update_guard_0026'"
                    )
                ).scalar_one() == 0:
                    session.connection().exec_driver_sql(run_update_guard)
                session.connection().exec_driver_sql(command_insert_guard)
                session.commit()
    finally:
        engine.dispose()


def test_0014_sqlite_requires_explicit_sealing_completion_and_round_trips_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'round-sealing.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0014")
    engine = sa.create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert "sealing_completion_id" in {
            column["name"]
            for column in inspector.get_columns("stocktake_round_submissions")
        }
        assert "uq_stocktake_round_submissions_sealing_completion" in {
            index["name"]
            for index in inspector.get_indexes("stocktake_round_submissions")
        }
        with engine.connect() as connection:
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'stocktake_round_submissions'"
                ).all()
            }
        assert "trg_stocktake_round_submissions_sealing_completion_0014" in triggers

        with pytest.raises(sa.exc.DatabaseError, match="missing or inconsistent"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO stocktake_round_submissions "
                    "(id, task_id, round_id, sealing_completion_id, scope_count, "
                    "zero_scope_count, count_line_count, observation_line_count, "
                    "serial_count, total_counted_qty, round_manifest_sha256, "
                    "request_sha256, idempotency_key_hash, submitted_by_user_id, "
                    "submitted_by_person_id, submitted_role_assignment_id, "
                    "authorization_version, submitted_at, created_at) VALUES "
                    "(?, ?, ?, NULL, 1, 1, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                    (
                        "92000000000040008000000000000001",
                        "92000000000040008000000000000002",
                        "92000000000040008000000000000003",
                        "a" * 64,
                        "b" * 64,
                        "c" * 64,
                        "00000000-0000-0000-0000-000000009204",
                        "92000000000040008000000000000005",
                        "92000000000040008000000000000006",
                        "2026-08-31 00:00:00+00:00",
                        "2026-08-31 00:00:00+00:00",
                    ),
                )
    finally:
        engine.dispose()

    command.downgrade(config, "20260831_0013")
    verification = sa.create_engine(database_url)
    try:
        assert "sealing_completion_id" not in {
            column["name"]
            for column in inspect(verification).get_columns(
                "stocktake_round_submissions"
            )
        }
    finally:
        verification.dispose()


def test_0015_sqlite_audit_events_are_append_only_and_downgrade_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'immutable-audit.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0015")
    event_id = "91000000000040008000000000000001"

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO audit_events "
                "(id, actor_user_id, action, aggregate_type, aggregate_id, "
                "before_jsonb, after_jsonb, request_id, previous_hash, "
                "event_hash, occurred_at, created_at) "
                "VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?)",
                (
                    event_id,
                    "stocktake.opening.started",
                    "stocktake_task",
                    "task-immutable-sentinel",
                    '{"status":"counting"}',
                    "opening-request-immutable-sentinel",
                    "a" * 64,
                    "2026-08-31 00:00:00+00:00",
                    "2026-08-31 00:00:00+00:00",
                ),
            )
        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE audit_events SET action = 'rewritten' WHERE id = ?",
                    (event_id,),
                )
        with pytest.raises(sa.exc.DatabaseError, match="immutable"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM audit_events WHERE id = ?",
                    (event_id,),
                )
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT action FROM audit_events WHERE id = ?",
                (event_id,),
            ).scalar_one() == "stocktake.opening.started"
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="persisted audit events"):
        command.downgrade(config, "20260831_0014")
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0015"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('stocktake_observation_dispositions', "
                "'stocktake_difference_set_completions')"
            ).scalar_one() == 0
            assert "count_manifest_sha256" not in {
                row[1]
                for row in connection.exec_driver_sql(
                    "PRAGMA table_info(stocktake_round_submissions)"
                ).all()
            }
            triggers = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND tbl_name = 'audit_events'"
                ).all()
            }
            assert {
                "trg_audit_events_immutable_update_0015",
                "trg_audit_events_immutable_delete_0015",
            } <= triggers
    finally:
        verification.dispose()


def test_0015_sqlite_accepts_one_complete_canonical_chain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-preflight-valid.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0014")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            event_hash = _insert_valid_audit_chain_event(connection)
    finally:
        engine.dispose()

    command.upgrade(config, HEAD_REVISION)
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == HEAD_REVISION
            assert connection.exec_driver_sql(
                "SELECT last_hash FROM audit_chain_heads "
                "WHERE stream_key = 'authorization'"
            ).scalar_one() == event_hash
            assert connection.exec_driver_sql(
                "SELECT stream_key, stream_version FROM audit_events"
            ).one() == ("authorization", 1)
    finally:
        verification.dispose()


@pytest.mark.parametrize(
    "corruption", ["orphan", "tampered", "oversized", "missing_head"]
)
def test_0015_sqlite_preflight_refuses_incomplete_or_tampered_audit_evidence(
    tmp_path: Path,
    monkeypatch,
    corruption: str,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / f'audit-{corruption}.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0014")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_valid_audit_chain_event(connection)
            if corruption == "orphan":
                connection.exec_driver_sql(
                    "UPDATE audit_chain_heads SET last_event_id = NULL, "
                    "last_hash = NULL, version = 0 "
                    "WHERE stream_key = 'authorization'"
                )
            elif corruption == "tampered":
                connection.exec_driver_sql(
                    "UPDATE audit_events SET action = 'tampered'"
                )
            elif corruption == "oversized":
                connection.exec_driver_sql(
                    "UPDATE audit_chain_heads SET version = 1000000000 "
                    "WHERE stream_key = 'authorization'"
                )
            else:
                connection.exec_driver_sql(
                    "DELETE FROM audit_chain_heads "
                    "WHERE stream_key = 'authentication'"
                )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="audit chain evidence"):
        command.upgrade(config, HEAD_REVISION)

    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0014"
            triggers = connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'audit_events' AND name LIKE '%0015'"
            ).all()
            assert triggers == []
    finally:
        verification.dispose()


def test_0015_sqlite_resumes_partial_upgrade_and_empty_downgrade(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-partial-retry.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0014")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER trg_audit_events_immutable_update_0015 "
                "BEFORE UPDATE ON audit_events BEGIN "
                "SELECT RAISE(ABORT, 'audit events are immutable'); END"
            )
    finally:
        engine.dispose()

    command.upgrade(config, HEAD_REVISION)
    upgraded = sa.create_engine(database_url)
    try:
        with upgraded.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER trg_audit_events_immutable_update_0015"
            )
    finally:
        upgraded.dispose()

    command.downgrade(config, "20260831_0014")
    verification = sa.create_engine(database_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0014"
            assert connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND tbl_name = 'audit_events' AND name LIKE '%0015'"
            ).all() == []
    finally:
        verification.dispose()


def test_0015_sqlite_audit_chain_head_can_only_advance_one_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-head-forward.db'}"
    config = _config(database_url)
    command.upgrade(config, HEAD_REVISION)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            event_hash = _insert_valid_audit_chain_event(connection)
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version, last_hash FROM audit_chain_heads "
                "WHERE stream_key = 'authorization'"
            ).one() == (1, event_hash)
        with pytest.raises(
            sa.exc.DatabaseError,
            match="advance by one|stream binding",
        ):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE audit_chain_heads SET version = 0, "
                    "last_event_id = NULL, last_hash = NULL "
                    "WHERE stream_key = 'authorization'"
                )
        with pytest.raises(sa.exc.DatabaseError, match="cannot be removed"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM audit_chain_heads "
                    "WHERE stream_key = 'authorization'"
                )
    finally:
        engine.dispose()


def test_0017_sqlite_backfills_exact_stream_versions_and_keeps_v1_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-stream-backfill.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0014")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            first_hash = _insert_valid_audit_chain_event(
                connection,
                event_id="91000000000040008000000000000021",
            )
            second_hash = _insert_valid_audit_chain_event(
                connection,
                event_id="91000000000040008000000000000022",
            )
    finally:
        engine.dispose()

    command.upgrade(config, "20260831_0016")
    command.upgrade(config, "20260831_0017")
    verification = sa.create_engine(database_url)
    try:
        inspector = inspect(verification)
        columns = {row["name"]: row for row in inspector.get_columns("audit_events")}
        assert columns["stream_key"]["nullable"] is False
        assert columns["stream_version"]["nullable"] is False
        assert (
            ("stream_key", "stream_version"),
            "uq_audit_events_stream_version_0017",
        ) in {
            (tuple(row["column_names"]), row["name"])
            for row in inspector.get_unique_constraints("audit_events")
        }
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT stream_key, stream_version, event_hash FROM audit_events "
                "ORDER BY stream_version"
            ).all() == [
                ("authorization", 1, first_hash),
                ("authorization", 2, second_hash),
            ]
            assert {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE '%0017'"
                ).all()
            } == {
                "trg_audit_events_stream_binding_insert_0017",
                "trg_audit_chain_heads_stream_binding_0017",
            }
    finally:
        verification.dispose()


def test_0017_sqlite_rejects_future_or_cross_stream_binding(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-stream-guards.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0017")
    engine = sa.create_engine(database_url)
    event_id = "91000000000040008000000000000031"
    occurred_at = "2026-08-31 00:00:00+00:00"
    event_hash = _canonical_audit_hash(
        stream_key="authorization",
        event_id=event_id,
        action="role_assignment.created",
        aggregate_type="role_assignment",
        aggregate_id="cross-stream-sentinel",
        after_jsonb={"status": "active"},
        request_id="cross-stream-request",
        occurred_at=occurred_at,
    )
    insert_sql = (
        "INSERT INTO audit_events "
        "(id, stream_key, stream_version, actor_user_id, action, aggregate_type, "
        "aggregate_id, before_jsonb, after_jsonb, request_id, previous_hash, "
        "event_hash, occurred_at, created_at) "
        "VALUES (?, 'authorization', ?, NULL, 'role_assignment.created', "
        "'role_assignment', 'cross-stream-sentinel', NULL, ?, "
        "'cross-stream-request', NULL, ?, ?, ?)"
    )
    try:
        with pytest.raises(sa.exc.DatabaseError, match="stream binding"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    insert_sql,
                    (
                        event_id,
                        2,
                        '{"status":"active"}',
                        event_hash,
                        occurred_at,
                        occurred_at,
                    ),
                )
        with pytest.raises(sa.exc.DatabaseError, match="stream binding"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    insert_sql,
                    (
                        event_id,
                        1,
                        '{"status":"active"}',
                        event_hash,
                        occurred_at,
                        occurred_at,
                    ),
                )
                connection.exec_driver_sql(
                    "UPDATE audit_chain_heads SET last_event_id = ?, "
                    "last_hash = ?, version = 1 WHERE stream_key = 'inventory'",
                    (event_id, event_hash),
                )
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM audit_events"
            ).scalar_one() == 0
    finally:
        engine.dispose()


def test_0017_preflight_rejects_orphan_before_any_sqlite_ddl(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-stream-orphan.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0016")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO audit_events "
                "(id, actor_user_id, action, aggregate_type, aggregate_id, "
                "before_jsonb, after_jsonb, request_id, previous_hash, "
                "event_hash, occurred_at, created_at) "
                "VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?)",
                (
                    "91000000000040008000000000000041",
                    "role_assignment.created",
                    "role_assignment",
                    "orphan-sentinel",
                    '{"status":"active"}',
                    "orphan-request",
                    "a" * 64,
                    "2026-08-31 00:00:00+00:00",
                    "2026-08-31 00:00:00+00:00",
                ),
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="stream membership"):
        command.upgrade(config, "20260831_0017")
    verification = sa.create_engine(database_url)
    try:
        assert "stream_key" not in {
            row["name"] for row in inspect(verification).get_columns("audit_events")
        }
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0016"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE '%0017'"
            ).scalar_one() == 0
    finally:
        verification.dispose()


def test_0017_nonempty_downgrade_is_blocked_without_removing_guards(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-stream-downgrade.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0017")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            _insert_valid_audit_chain_event(connection)
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="persisted audit events"):
        command.downgrade(config, "20260831_0016")
    verification = sa.create_engine(database_url)
    try:
        assert "stream_key" in {
            row["name"] for row in inspect(verification).get_columns("audit_events")
        }
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0017"
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE '%0017'"
            ).scalar_one() == 2
    finally:
        verification.dispose()


def test_0017_sqlite_failed_rebuild_rolls_back_and_retries_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / 'audit-stream-retry.db'}"
    config = _config(database_url)
    command.upgrade(config, "20260831_0016")
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE _alembic_tmp_audit_events (id INTEGER)"
            )
    finally:
        engine.dispose()

    with pytest.raises(sa.exc.OperationalError, match="already exists"):
        command.upgrade(config, "20260831_0017")
    verification = sa.create_engine(database_url)
    try:
        assert "stream_key" not in {
            row["name"] for row in inspect(verification).get_columns("audit_events")
        }
        with verification.begin() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0016"
            assert {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE '%0015'"
                ).all()
            } >= {
                "trg_audit_events_immutable_update_0015",
                "trg_audit_events_immutable_delete_0015",
                "trg_audit_chain_heads_forward_only_0015",
                "trg_audit_chain_heads_no_delete_0015",
            }
            connection.exec_driver_sql("DROP TABLE _alembic_tmp_audit_events")
    finally:
        verification.dispose()

    command.upgrade(config, "20260831_0017")
    final_engine = sa.create_engine(database_url)
    try:
        with final_engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == "20260831_0017"
    finally:
        final_engine.dispose()


def test_0016_migrated_sqlite_count_service_seals_independent_manifests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Exercise the real Alembic head, not only ORM-created test tables."""

    from decimal import Decimal
    from types import SimpleNamespace

    from sqlalchemy.orm import Session

    import test_opening_stocktake_service as support
    from app.formal_access import load_formal_principal
    from app.foundation_models import Role, SourceSystem
    from app.formal_services.opening_stocktake import (
        OpeningStocktakeScopeInput,
        StartOpeningStocktakeCommand,
    )
    from app.inventory_models import (
        FormalMaterial,
        InventoryMovement,
        InventoryTransaction,
        StockAccount,
        StockBalance,
        StockLocation,
    )
    from app.stocktake_models import (
        StocktakeCountLine,
        StocktakeDifferenceSetCompletion,
        StocktakeRound,
        StocktakeRoundSubmission,
        StocktakeScopeCountCompletion,
    )

    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0016-service-head.db'}"
    command.upgrade(_config(database_url), HEAD_REVISION)
    engine = sa.create_engine(database_url)

    @sa.event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    monkeypatch.setattr(
        support.opening_stocktake_service,
        "_database_now",
        lambda _db: support.NOW,
    )
    monkeypatch.setattr(
        support.opening_count_service,
        "_database_now",
        lambda _db: support.NOW,
    )
    try:
        with Session(engine) as db:
            hq = support._organization(db, "MIG-HQ", "总部", "headquarters")
            region = support._organization(
                db, "MIG-REG", "迁移区域", "region_company", parent=hq
            )
            source = SourceSystem(
                id=uuid.uuid4(),
                code="OAM",
                name="OAM 只读镜像",
                mode="read_only",
                enabled=True,
                configuration_jsonb={},
            )
            db.add(source)
            db.flush()
            manager_role = db.scalar(
                sa.select(Role).where(Role.code == "provincial_manager")
            )
            assert manager_role is not None
            manager = support._user_with_role(
                db,
                region,
                manager_role,
                "organization",
                str(region.id),
                "Migrated Manager",
            )
            material: FormalMaterial = support._material(db, source, "none")
            location = StockLocation(
                id=uuid.uuid4(),
                code="MIG-REG-WH",
                name="迁移区域仓",
                location_type="region",
                owner_org_id=region.id,
                parent_id=None,
                custodian_person_id=None,
                status="active",
            )
            db.add(location)
            db.flush()
            account = StockAccount(
                id=uuid.uuid4(),
                owner_org_id=region.id,
                custodian_person_id=None,
                location_id=location.id,
                material_id=material.id,
                condition_code="new",
                availability_bucket="available",
                lot_id=None,
            )
            db.add(account)
            db.flush()
            db.add(
                StockBalance(
                    stock_account_id=account.id,
                    quantity=Decimal("0.000"),
                    ledger_cursor=0,
                    version=1,
                )
            )
            control = support._install_control_sync(
                db,
                source=source,
                region=region,
                material=material,
                rows=(
                    {
                        "external_business_key": "MIG-CONTROL-0",
                        "material_id": material.id,
                        "condition_code": "new",
                        "control_qty": Decimal("0.000"),
                        "mapping_status": "resolved",
                        "mapping_note": "",
                    },
                ),
            )
            db.commit()
            actor = load_formal_principal(db, manager.user.id, now=support.NOW)
            start_command = StartOpeningStocktakeCommand(
                task_no="OPEN-MIGRATED-0016",
                region_org_id=region.id,
                control_source_system_id=source.id,
                control_sync_run_id=control.sync_run.id,
                control_sync_scope_key=control.sync_run.scope_key,
                scopes=(
                    OpeningStocktakeScopeInput(
                        owner_org_id=region.id,
                        location_id=location.id,
                        assignee_user_id=manager.user.id,
                        freeze_mode="hard",
                    ),
                ),
                control_lines=control.lines,
                blind_count=True,
                deadline=support.NOW + support.timedelta(days=2),
                note="migrated SQLite head seam",
            )
            world = SimpleNamespace(
                db=db,
                principals={"manager_x": actor},
                command=start_command,
            )
            started = support._start(
                world,
                actor=actor,
                key="opening-migrated-head-start-0016",
            )
            count_command = support._scope_count_command(
                world,
                started,
                observations=(),
                zero_confirmed=False,
            )
            result = support._submit_scope_count(
                world,
                actor=actor,
                command=count_command,
                key="opening-migrated-head-count-0016",
            )

            assert result.round_sealed is True
            round_row = db.get(StocktakeRound, started.initial_round_id)
            submission = db.scalar(
                sa.select(StocktakeRoundSubmission).where(
                    StocktakeRoundSubmission.round_id == started.initial_round_id
                )
            )
            completion = db.scalar(
                sa.select(StocktakeDifferenceSetCompletion).where(
                    StocktakeDifferenceSetCompletion.round_id
                    == started.initial_round_id
                )
            )
            sealing = db.get(
                StocktakeScopeCountCompletion,
                submission.sealing_completion_id if submission else None,
            )
            assert round_row is not None and round_row.status == "submitted"
            assert submission is not None
            assert submission.count_manifest_sha256 == round_row.count_manifest_sha256
            assert submission.round_manifest_sha256 != submission.count_manifest_sha256
            assert completion is not None
            assert completion.difference_count == 0
            assert sealing is not None
            assert completion.completed_by_user_id == submission.submitted_by_user_id
            assert completion.completed_at == submission.submitted_at
            assert completion.role_code == sealing.role_code
            assert completion.scope_type == sealing.scope_type
            assert completion.scope_id_snapshot == sealing.scope_id_snapshot
            assert completion.authorization_sha256 == sealing.authorization_sha256
            assert db.scalar(
                sa.select(sa.func.count()).select_from(StocktakeCountLine)
            ) == 1
            assert db.scalar(
                sa.select(sa.func.count()).select_from(StockAccount)
            ) == 1
            assert db.scalar(
                sa.select(sa.func.count()).select_from(InventoryTransaction)
            ) == 0
            assert db.scalar(
                sa.select(sa.func.count()).select_from(InventoryMovement)
            ) == 0
    finally:
        engine.dispose()
