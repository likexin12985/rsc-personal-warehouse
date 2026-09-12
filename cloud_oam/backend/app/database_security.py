"""Fail-closed production database-role verification for the main API.

The runtime API may append reviewed inventory/opening terminal facts and
advance only the exact projection/head columns needed by those transactions.
It must never own schema objects or hold DDL, TRUNCATE, trigger-control, or
migrator membership.  This module performs read-only catalog checks before the
API accepts traffic.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .formal_services.audit_chain import calculate_audit_event_hash
from .oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST


class DatabaseSecurityBoundaryError(RuntimeError):
    """The production database identity or ACL boundary is not proven."""


RUNTIME_READ_TABLES = frozenset(
    {
        "audit_chain_heads",
        "audit_events",
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
        "auth_identities",
        "auth_idempotency_operations",
        "auth_login_rate_limit_buckets",
        "auth_refresh_tokens",
        "auth_sessions",
        "custody_assignments",
        "document_attachments",
        "external_objects",
        "external_object_versions",
        "files",
        "inventory_freezes",
        "inventory_ledger_heads",
        "inventory_lots",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_opening_establishments",
        "inventory_serials",
        "inventory_transactions",
        "kms_data_key_pins",
        "login_challenges",
        "material_request_cancellation_line_facts",
        "material_request_commands",
        "material_request_files",
        "material_request_lines",
        "material_request_revisions",
        "material_requests",
        "material_substitutions",
        "material_inventory_policies",
        "materials",
        "notification_events",
        "oam_work_orders",
        "work_order_material_operations", "work_order_material_lines", "work_order_material_serials", "work_order_replacement_pairs", "work_order_replacements", "work_order_command_seals",
        "opening_control_reconciliation_items",
        "opening_control_reconciliation_command_consumptions",
        "opening_control_reconciliation_runs",
        "organizations",
        "outbox_events",
        "people",
        "permissions",
        "qr_codes",
        "reconciliation_commands",
        "reconciliation_items",
        "reconciliation_runs",
        "role_assignments",
        "role_permissions",
        "roles",
        "serial_current_positions",
        "sms_challenge_dispatches",
        "source_systems",
        "state_transition_events",
        "stock_accounts",
        "stock_allocations",
        "stock_allocation_serials",
        "stock_reservations",
        "stock_reservation_serials",
        "outbound_orders", "outbound_lines", "stock_reservation_picks", "stock_reservation_pick_serials",
        "outbound_postings", "outbound_posting_serials",
        "shipments", "shipment_lines", "shipment_serials",
        "logistics_events", "receipts", "receipt_lines", "receipt_serials", "receipt_exceptions", "oam_receipt_evidence", "inbound_orders", "inbound_postings",
        "stock_reservation_releases",
        "stock_reservation_release_serials",
        "stock_balances",
        "stock_locations",
        "stocktake_control_snapshot_lines",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_count_serials",
        "stocktake_close_completions",
        "stocktake_close_reconciliation_accounts",
        "stocktake_close_reconciliation_completions",
        "stocktake_close_reconciliation_serials",
        "stocktake_close_transition_acks",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_effective_approval_completions",
        "stocktake_effective_approval_items",
        "stocktake_effective_approval_scopes",
        "stocktake_observation_dispositions",
        "stocktake_posting_items",
        "stocktake_posting_completion_items",
        "stocktake_posting_command_outcomes",
        "stocktake_posting_completions",
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
        "substitution_decisions",
        "supply_tasks",
        "sync_batches",
        "sync_inbox_events",
        "sync_runs",
        "users",
    }
)
RUNTIME_INSERT_TABLES = frozenset(
    {
        "audit_events",
        "approval_actions",
        "approval_external_registration_lines",
        "approval_external_registrations",
        "approval_instances",
        "approval_return_line_facts",
        "approval_step_candidates",
        "approval_step_line_decisions",
        "approval_steps",
        "auth_idempotency_operations",
        "auth_login_rate_limit_buckets",
        "auth_refresh_tokens",
        "auth_sessions",
        "inventory_freezes",
        "document_attachments",
        "files",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_opening_establishments",
        "inventory_transactions",
        "login_challenges",
        "material_request_cancellation_line_facts",
        "material_request_commands",
        "material_request_files",
        "material_request_lines",
        "material_request_revisions",
        "material_requests",
        "opening_control_reconciliation_items",
        "opening_control_reconciliation_command_consumptions",
        "opening_control_reconciliation_runs",
        "outbox_events",
        "reconciliation_commands",
        "reconciliation_items",
        "reconciliation_runs",
        "role_assignments",
        "serial_current_positions",
        "sms_challenge_dispatches",
        "state_transition_events",
        "stock_accounts",
        "stock_allocations",
        "stock_allocation_serials",
        "stock_reservations",
        "stock_reservation_serials",
        "outbound_orders", "outbound_lines", "stock_reservation_picks", "stock_reservation_pick_serials",
        "outbound_postings", "outbound_posting_serials",
        "shipments", "shipment_lines", "shipment_serials",
        "logistics_events", "receipts", "receipt_lines", "receipt_serials", "receipt_exceptions", "inbound_orders", "inbound_postings",
        "stock_reservation_releases",
        "stock_reservation_release_serials",
        "work_order_material_operations", "work_order_material_lines", "work_order_material_serials", "work_order_replacement_pairs", "work_order_replacements", "work_order_command_seals",
        "stock_balances",
        "stocktake_control_snapshot_lines",
        "stocktake_count_lines",
        "stocktake_count_observations",
        "stocktake_count_serials",
        "stocktake_close_completions",
        "stocktake_close_reconciliation_accounts",
        "stocktake_close_reconciliation_completions",
        "stocktake_close_reconciliation_serials",
        "stocktake_difference_set_completions",
        "stocktake_differences",
        "stocktake_effective_approval_completions",
        "stocktake_effective_approval_items",
        "stocktake_effective_approval_scopes",
        "stocktake_observation_dispositions",
        "stocktake_posting_items",
        "stocktake_posting_completion_items",
        "stocktake_posting_command_outcomes",
        "stocktake_posting_completions",
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
        "supply_tasks",
    }
)
RUNTIME_UPDATE_TABLES = frozenset(
    {
        "auth_idempotency_operations",
        "auth_login_rate_limit_buckets",
        "auth_refresh_tokens",
        "auth_sessions",
        "role_assignments",
        "users",
    }
)
RUNTIME_DELETE_TABLES = frozenset(
    {
        "auth_login_rate_limit_buckets",
        "material_request_files",
        "material_request_lines",
    }
)
RUNTIME_UPDATE_COLUMNS = {
    "inventory_serials": frozenset({"lifecycle_status", "updated_at"}),
    "login_challenges": frozenset(
        {
            "attempts",
            "status",
            "provider_reference",
            "verified_at",
            "consumed_at",
        }
    ),
    "sms_challenge_dispatches": frozenset(
        {
            "status",
            "owner_token_hash",
            "provider_reference",
            "claimed_at",
            "lease_expires_at",
            "accepted_at",
            "uncertain_at",
            "expired_at",
        }
    ),
    "audit_chain_heads": frozenset(
        {"last_event_id", "last_hash", "version", "updated_at"}
    ),
    "inventory_freezes": frozenset(
        {
            "status",
            "valid_to",
            "released_by_user_id",
            "release_reason",
            "version",
            "updated_at",
        }
    ),
    "files": frozenset({"status", "metadata_jsonb"}),
    "approval_external_registrations": frozenset(
        {
            "status",
            "verified_by_user_id",
            "verified_by_person_id",
            "verified_role_assignment_id",
            "verified_authorization_version",
            "verified_at",
            "verification_comment",
            "version",
            "updated_at",
        }
    ),
    "approval_instances": frozenset(
        {
            "status",
            "current_step_no",
            "current_step_id",
            "completed_at",
            "version",
            "updated_at",
        }
    ),
    "approval_steps": frozenset(
        {
            "status",
            "opened_at",
            "decided_at",
            "decision_manifest_sha256",
            "version",
            "updated_at",
        }
    ),
    "material_request_lines": frozenset(
        {
            "status",
            "final_approved_qty",
            "cancelled_qty",
            "version",
            "updated_at",
        }
    ),
    "material_request_revisions": frozenset(
        {
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
        }
    ),
    "material_requests": frozenset(
        {
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
        }
    ),
    "inventory_ledger_heads": frozenset({"next_cursor", "updated_at"}),
    "opening_control_reconciliation_items": frozenset(
        {
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
    ),
    "opening_control_reconciliation_runs": frozenset(
        {
            "version",
            "approved_by_user_id",
            "approved_by_person_id",
            "approved_role_assignment_id",
            "approved_authorization_version",
            "approved_at",
            "approval_comment",
            "updated_at",
        }
    ),
    "reconciliation_items": frozenset(
        {"status", "explanation", "evidence_file_id", "updated_at"}
    ),
    "reconciliation_runs": frozenset({"status", "updated_at"}),
    "serial_current_positions": frozenset(
        {"stock_account_id", "last_movement_id", "updated_at"}
    ),
    "stock_balances": frozenset(
        {"quantity", "ledger_cursor", "version", "updated_at"}
    ),
    "stocktake_rounds": frozenset(
        {
            "status",
            "submitted_by_user_id",
            "submitted_at",
            "count_manifest_sha256",
            "updated_at",
        }
    ),
    "stocktake_tasks": frozenset(
        {
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
    ),
    "supply_tasks": frozenset(
        {
            "reference_no",
            "expected_date",
            "status",
            "cancelled_by_user_id",
            "cancelled_at",
            "version",
            "updated_at",
        }
    ),
}
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
EXPECTED_AUDIT_HEAD_IDS = {
    "authorization": "30000000-0000-4000-8000-000000000001",
    "authentication": "30000000-0000-4000-8000-000000000002",
    "inventory": "30000000-0000-4000-8000-000000000003",
    "material_request": "30000000-0000-4000-8000-000000000004",
}
EXPECTED_AUDIT_TRIGGERS = {
    "trg_audit_events_immutable_0015": (
        "audit_events",
        "rsc_reject_audit_event_mutation_0015",
        27,
        False,
        False,
        False,
    ),
    "trg_audit_events_immutable_truncate_0015": (
        "audit_events",
        "rsc_reject_audit_event_mutation_0015",
        34,
        False,
        False,
        False,
    ),
    "trg_audit_chain_heads_forward_only_0015": (
        "audit_chain_heads",
        "rsc_validate_audit_chain_head_mutation_0015",
        27,
        False,
        False,
        False,
    ),
    "trg_audit_chain_heads_no_truncate_0015": (
        "audit_chain_heads",
        "rsc_validate_audit_chain_head_mutation_0015",
        34,
        False,
        False,
        False,
    ),
    "trg_audit_chain_heads_stream_binding_0017": (
        "audit_chain_heads",
        "rsc_validate_audit_stream_head_binding_0017",
        19,
        False,
        False,
        False,
    ),
    "trg_audit_events_commit_binding_0017": (
        "audit_events",
        "rsc_require_audit_event_commit_binding_0017",
        5,
        True,
        True,
        True,
    ),
    "trg_audit_events_opening_commit_0022": (
        "audit_events",
        "rsc_require_opening_terminal_graph_0022",
        5,
        True,
        True,
        True,
    ),
    "trg_audit_events_opening_graph_0052": (
        "audit_events",
        "rsc_require_opening_live_graph_0052",
        5,
        True,
        True,
        True,
    ),
    "trg_reconciliation_audit_effect_guard_0026": (
        "audit_events",
        "rsc_guard_reconciliation_effect_0026",
        31,
        False,
        False,
        False,
    ),
    "trg_reconciliation_audit_effect_no_truncate_0026": (
        "audit_events",
        "rsc_guard_reconciliation_effect_0026",
        34,
        False,
        False,
        False,
    ),
    "trg_audit_events_cancellation_graph_0037": (
        "audit_events",
        "rsc_require_material_request_cancellation_graph_0037",
        5,
        True,
        True,
        True,
    ),
    "trg_audit_events_nonopening_stocktake_close_guard_0038": (
        "audit_events",
        "rsc_guard_nonopening_stocktake_close_event_0038",
        7,
        False,
        False,
        False,
    ),
    "trg_audit_events_approval_projection_0045": (
        "audit_events",
        "rsc_dispatch_material_request_approval_projection_0045",
        29,
        True,
        True,
        True,
    ),
    "trg_audit_events_supply_causality_0059": (
        "audit_events",
        "rsc_dispatch_material_request_supply_causality_0059",
        29,
        True,
        True,
        True,
    ),
    "trg_audit_events_stocktake_start_causality_0047": (
        "audit_events",
        "rsc_dispatch_nonopening_stocktake_start_causality_0047",
        29,
        True,
        True,
        True,
    ),
    "trg_audit_events_stocktake_start_sealed_0047": (
        "audit_events",
        "rsc_guard_stocktake_start_completion_0047",
        31,
        False,
        False,
        False,
    ),
}
EXPECTED_AUDIT_STREAM_CONSTRAINTS = {
    "ck_audit_events_stream_key_0017": "check_stream_key",
    "ck_audit_events_stream_version_0017": "check_stream_version",
    "uq_audit_events_stream_version_0017": "unique_stream_version",
    "fk_audit_events_stream_key_0017": "foreign_stream_head",
}
EXPECTED_STOCKTAKE_RECOUNT_COLUMNS = {
    ("stocktake_rounds", "recount_case_id"): (False, "uuid"),
    ("stocktake_recount_cases", "id"): (True, "uuid"),
    ("stocktake_recount_cases", "task_id"): (True, "uuid"),
    ("stocktake_recount_cases", "source_round_id"): (True, "uuid"),
    ("stocktake_recount_cases", "source_round_submission_id"): (
        True,
        "uuid",
    ),
    ("stocktake_recount_cases", "source_difference_completion_id"): (
        True,
        "uuid",
    ),
    ("stocktake_recount_cases", "trigger_review_id"): (True, "uuid"),
    ("stocktake_recount_cases", "next_round_no"): (True, "integer"),
    ("stocktake_recount_cases", "scope_count"): (True, "integer"),
    ("stocktake_recount_cases", "scope_manifest_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_cases", "assignment_manifest_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_cases", "recount_manifest_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_cases", "request_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_cases", "idempotency_key_hash"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_cases", "reason"): (True, "text"),
    ("stocktake_recount_cases", "opened_by_user_id"): (
        True,
        "character varying(36)",
    ),
    ("stocktake_recount_cases", "opened_by_person_id"): (True, "uuid"),
    ("stocktake_recount_cases", "opened_role_assignment_id"): (True, "uuid"),
    ("stocktake_recount_cases", "authorization_version"): (True, "bigint"),
    ("stocktake_recount_cases", "role_code"): (
        True,
        "character varying(40)",
    ),
    ("stocktake_recount_cases", "scope_type"): (
        True,
        "character varying(24)",
    ),
    ("stocktake_recount_cases", "scope_id_snapshot"): (
        True,
        "character varying(80)",
    ),
    ("stocktake_recount_cases", "authorization_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_cases", "opened_at"): (
        True,
        "timestamp with time zone",
    ),
    ("stocktake_recount_scope_assignments", "id"): (True, "uuid"),
    ("stocktake_recount_scope_assignments", "recount_case_id"): (
        True,
        "uuid",
    ),
    ("stocktake_recount_scope_assignments", "task_id"): (True, "uuid"),
    ("stocktake_recount_scope_assignments", "source_round_id"): (
        True,
        "uuid",
    ),
    ("stocktake_recount_scope_assignments", "scope_id"): (True, "uuid"),
    ("stocktake_recount_scope_assignments", "assignee_user_id"): (
        True,
        "character varying(36)",
    ),
    ("stocktake_recount_scope_assignments", "assignee_person_id"): (
        True,
        "uuid",
    ),
    ("stocktake_recount_scope_assignments", "assignee_role_assignment_id"): (
        True,
        "uuid",
    ),
    ("stocktake_recount_scope_assignments", "authorization_version"): (
        True,
        "bigint",
    ),
    ("stocktake_recount_scope_assignments", "role_code"): (
        True,
        "character varying(40)",
    ),
    ("stocktake_recount_scope_assignments", "scope_type"): (
        True,
        "character varying(24)",
    ),
    ("stocktake_recount_scope_assignments", "scope_id_snapshot"): (
        True,
        "character varying(80)",
    ),
    ("stocktake_recount_scope_assignments", "authorization_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_scope_assignments", "assignment_sha256"): (
        True,
        "character varying(64)",
    ),
    ("stocktake_recount_scope_assignments", "assigned_at"): (
        True,
        "timestamp with time zone",
    ),
}
EXPECTED_STOCKTAKE_RECOUNT_CONSTRAINTS = {
    "pk_stocktake_recount_cases": {
        "table": "stocktake_recount_cases",
        "type": "p",
        "columns": ("id",),
    },
    "pk_stocktake_recount_scope_assignments": {
        "table": "stocktake_recount_scope_assignments",
        "type": "p",
        "columns": ("id",),
    },
    "fk_stocktake_rounds_recount_case_0018": {
        "table": "stocktake_rounds",
        "type": "f",
        "columns": ("recount_case_id",),
        "referenced_table": "stocktake_recount_cases",
        "referenced_columns": ("id",),
    },
    "fk_stocktake_recount_cases_source_round_0018": {
        "table": "stocktake_recount_cases",
        "type": "f",
        "columns": ("source_round_id", "task_id"),
        "referenced_table": "stocktake_rounds",
        "referenced_columns": ("id", "task_id"),
    },
    "fk_stocktake_recount_cases_trigger_review_0018": {
        "table": "stocktake_recount_cases",
        "type": "f",
        "columns": ("trigger_review_id", "task_id", "source_round_id"),
        "referenced_table": "stocktake_reviews",
        "referenced_columns": ("id", "task_id", "round_id"),
    },
    "fk_stocktake_recount_scope_assignments_case_0018": {
        "table": "stocktake_recount_scope_assignments",
        "type": "f",
        "columns": ("recount_case_id", "task_id", "source_round_id"),
        "referenced_table": "stocktake_recount_cases",
        "referenced_columns": ("id", "task_id", "source_round_id"),
    },
    "fk_stocktake_recount_scope_assignments_scope_0018": {
        "table": "stocktake_recount_scope_assignments",
        "type": "f",
        "columns": ("scope_id", "task_id"),
        "referenced_table": "stocktake_scopes",
        "referenced_columns": ("id", "task_id"),
    },
    "ck_stocktake_recount_cases_round_0018": {
        "table": "stocktake_recount_cases",
        "type": "c",
        "definition_tokens": ("next_round_no", "scope_count"),
    },
    "ck_stocktake_recount_cases_hashes_0018": {
        "table": "stocktake_recount_cases",
        "type": "c",
        "definition_tokens": (
            "scope_manifest_sha256",
            "assignment_manifest_sha256",
            "recount_manifest_sha256",
            "request_sha256",
            "idempotency_key_hash",
            "authorization_sha256",
        ),
    },
    "ck_stocktake_recount_scope_assignments_hashes_0018": {
        "table": "stocktake_recount_scope_assignments",
        "type": "c",
        "definition_tokens": ("authorization_sha256", "assignment_sha256"),
    },
}
EXPECTED_STOCKTAKE_RECOUNT_INDEXES = {
    "uq_stocktake_rounds_recount_case_0018": {
        "table": "stocktake_rounds",
        "columns": ("recount_case_id",),
        "predicate": "not_null_recount_case",
    },
    "uq_stocktake_rounds_one_counting_0018": {
        "table": "stocktake_rounds",
        "columns": ("task_id",),
        "predicate": "counting",
    },
    "uq_stocktake_recount_cases_source_round_0018": {
        "table": "stocktake_recount_cases",
        "columns": ("source_round_id",),
        "predicate": None,
    },
    "uq_stocktake_recount_cases_trigger_review_0018": {
        "table": "stocktake_recount_cases",
        "columns": ("trigger_review_id",),
        "predicate": None,
    },
    "uq_stocktake_recount_cases_task_next_round_0018": {
        "table": "stocktake_recount_cases",
        "columns": ("task_id", "next_round_no"),
        "predicate": None,
    },
    "uq_stocktake_recount_cases_idempotency_0018": {
        "table": "stocktake_recount_cases",
        "columns": ("idempotency_key_hash",),
        "predicate": None,
    },
    "uq_stocktake_recount_scope_assignments_case_scope_0018": {
        "table": "stocktake_recount_scope_assignments",
        "columns": ("recount_case_id", "scope_id"),
        "predicate": None,
    },
}
POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019 = (
    "trg_stocktake_scope_count_completions_technician_personal_locat"
)
POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019 = (
    "trg_stocktake_recount_scope_assignments_technician_personal_loc"
)
STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031 = (
    "rsc_validate_stocktake_difference_set_completion_0031"
)
STOCKTAKE_DIFFERENCE_COMPLETION_TRIGGER_0031 = (
    "trg_stocktake_difference_set_completions_validate_0031"
)
EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS = {
    "trg_stocktake_control_snapshot_00_nonopening_0057": (
        "stocktake_control_snapshot_lines",
        "rsc_guard_nonopening_control_snapshot_0057",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_control_snapshot_lines_immutable_0010": (
        "stocktake_control_snapshot_lines",
        "rsc_block_stocktake_fact_mutation_0010",
        "O",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_control_snapshot_lines_sealed_insert_0010": (
        "stocktake_control_snapshot_lines",
        "rsc_seal_opening_start_evidence_0010",
        "O",
        7,
        False,
        False,
        False,
    ),
    "trg_stock_locations_stocktake_personal_continuity_0020": (
        "stock_locations",
        "rsc_preserve_stocktake_personal_location_0020",
        "A",
        19,
        False,
        False,
        False,
    ),
    "trg_stocktake_scope_count_completions_immutable_0011": (
        "stocktake_scope_count_completions",
        "rsc_block_opening_count_fact_mutation_0011",
        "O",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_scope_count_completions_current_0052": (
        "stocktake_scope_count_completions",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_scope_count_completions_graph_0052": (
        "stocktake_scope_count_completions",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
        True,
        True,
        True,
    ),
    "trg_stocktake_scope_completions_assignment_0021": (
        "stocktake_scope_count_completions",
        "rsc_validate_stocktake_scope_completion_insert_0021",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_scope_count_ledger_boundary_0033": (
        "stocktake_scope_count_completions",
        "rsc_validate_stocktake_count_ledger_boundary_0033",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_completion_scope_0034": (
        "stocktake_scope_count_completions",
        "rsc_validate_recount_completion_scope_0034",
        "A",
        7,
        False,
        False,
        False,
    ),
    POSTGRESQL_COMPLETION_PERSONAL_TRIGGER_0019: (
        "stocktake_scope_count_completions",
        "rsc_validate_stocktake_technician_personal_location_0019",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_scope_assignments_validate_0018": (
        "stocktake_recount_scope_assignments",
        "rsc_validate_stocktake_recount_scope_assignment_0018",
        "A",
        7,
        False,
        False,
        False,
    ),
    POSTGRESQL_RECOUNT_PERSONAL_TRIGGER_0019: (
        "stocktake_recount_scope_assignments",
        "rsc_validate_stocktake_technician_personal_location_0019",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_scope_assignments_immutable_0018": (
        "stocktake_recount_scope_assignments",
        "rsc_block_stocktake_recount_fact_mutation_0018",
        "A",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_scope_assignments_immutable_truncate_0018": (
        "stocktake_recount_scope_assignments",
        "rsc_block_stocktake_recount_fact_mutation_0018",
        "A",
        34,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_graph_assignment_0032": (
        "stocktake_recount_scope_assignments",
        "rsc_require_stocktake_recount_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
    "trg_stocktake_count_lines_submitted_immutable_0010": (
        "stocktake_count_lines",
        "rsc_block_submitted_stocktake_count_mutation_0010",
        "O",
        31,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_lines_immutable_0011": (
        "stocktake_count_lines",
        "rsc_block_opening_count_fact_mutation_0011",
        "O",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_lines_assignment_0021": (
        "stocktake_count_lines",
        "rsc_validate_stocktake_count_line_insert_0021",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_lines_current_0052": (
        "stocktake_count_lines",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_lines_graph_0052": (
        "stocktake_count_lines",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
        True,
        True,
        True,
    ),
    "trg_stocktake_count_observations_immutable_0011": (
        "stocktake_count_observations",
        "rsc_block_opening_count_fact_mutation_0011",
        "O",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_observations_assignment_0021": (
        "stocktake_count_observations",
        "rsc_validate_stocktake_observation_insert_0021",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_observations_current_0052": (
        "stocktake_count_observations",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_count_observations_graph_0052": (
        "stocktake_count_observations",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
        True,
        True,
        True,
    ),
}
EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS = {
    STOCKTAKE_DIFFERENCE_COMPLETION_TRIGGER_0031: (
        "stocktake_difference_set_completions",
        STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031,
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_observation_dispositions_validate_0016": (
        "stocktake_observation_dispositions",
        "rsc_validate_stocktake_observation_disposition_0016",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_nonopening_review_graph_task_0032": (
        "stocktake_tasks",
        "rsc_require_nonopening_stocktake_review_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
    "trg_nonopening_review_graph_review_0032": (
        "stocktake_reviews",
        "rsc_require_nonopening_stocktake_review_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
    "trg_nonopening_review_graph_item_0032": (
        "stocktake_review_items",
        "rsc_require_nonopening_stocktake_review_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
    "trg_nonopening_review_version_0063": (
        "stocktake_reviews",
        "rsc_validate_nonopening_stocktake_review_version_0063",
        "A",
        23,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_cases_review_path_0032": (
        "stocktake_recount_cases",
        "rsc_validate_stocktake_recount_case_0032",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_cases_immutable_0018": (
        "stocktake_recount_cases",
        "rsc_block_stocktake_recount_fact_mutation_0018",
        "A",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_cases_immutable_truncate_0018": (
        "stocktake_recount_cases",
        "rsc_block_stocktake_recount_fact_mutation_0018",
        "A",
        34,
        False,
        False,
        False,
    ),
    "trg_stocktake_tasks_recount_causality_0032": (
        "stocktake_tasks",
        "rsc_validate_stocktake_recount_task_advance_0032",
        "A",
        19,
        False,
        False,
        False,
    ),
    "trg_stocktake_rounds_recount_causality_0032": (
        "stocktake_rounds",
        "rsc_validate_stocktake_recount_round_0032",
        "A",
        23,
        False,
        False,
        False,
    ),
    "trg_stocktake_recount_graph_task_0032": (
        "stocktake_tasks",
        "rsc_require_stocktake_recount_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
    "trg_stocktake_recount_graph_round_0032": (
        "stocktake_rounds",
        "rsc_require_stocktake_recount_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
    "trg_stocktake_recount_graph_case_0032": (
        "stocktake_recount_cases",
        "rsc_require_stocktake_recount_graph_0032",
        "A",
        29,
        True,
        True,
        True,
    ),
}
EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS = {
    **EXPECTED_STOCKTAKE_SENSITIVE_TRIGGERS,
    **EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS,
}
EXPECTED_STOCKTAKE_SCOPE_TRIGGERS = {
    "trg_stocktake_scopes_immutable_0010": (
        "stocktake_scopes",
        "rsc_block_stocktake_fact_mutation_0010",
        "O",
        27,
        False,
        False,
        False,
    ),
    "trg_stocktake_scopes_sealed_insert_0010": (
        "stocktake_scopes",
        "rsc_seal_opening_start_evidence_0010",
        "O",
        7,
        False,
        False,
        False,
    ),
    # Revision 0025 retains its catalog identity while the function enforces
    # both the asset-owner and complete physical-location region graphs.
    "trg_stocktake_scopes_region_owner_0025": (
        "stocktake_scopes",
        "rsc_validate_stocktake_scope_region_owner_0025",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_stocktake_scopes_stocktake_start_sealed_0047": (
        "stocktake_scopes",
        "rsc_guard_stocktake_start_completion_0047",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_stocktake_scopes_stocktake_start_causality_0047": (
        "stocktake_scopes",
        "rsc_dispatch_nonopening_stocktake_start_causality_0047",
        "A",
        29,
        True,
        True,
        True,
    ),
}
EXPECTED_RECONCILIATION_TRIGGERS = {
    "trg_opening_reconciliation_consumptions_guard_0026": (
        "opening_control_reconciliation_command_consumptions",
        "rsc_guard_opening_reconciliation_command_consumption_0026",
        "A",
        31,
    ),
    "trg_opening_reconciliation_consumptions_no_truncate_0026": (
        "opening_control_reconciliation_command_consumptions",
        "rsc_guard_opening_reconciliation_command_consumption_0026",
        "A",
        34,
    ),
    "trg_reconciliation_commands_immutable_0026": (
        "reconciliation_commands",
        "rsc_guard_reconciliation_command_0026",
        "A",
        31,
    ),
    "trg_reconciliation_commands_no_truncate_0026": (
        "reconciliation_commands",
        "rsc_guard_reconciliation_command_0026",
        "A",
        34,
    ),
    "trg_opening_reconciliation_runs_guard_0026": (
        "opening_control_reconciliation_runs",
        "rsc_guard_opening_reconciliation_run_0026",
        "A",
        23,
    ),
    "trg_opening_reconciliation_runs_no_delete_0026": (
        "opening_control_reconciliation_runs",
        "rsc_guard_opening_reconciliation_run_0026",
        "A",
        11,
    ),
    "trg_opening_reconciliation_runs_no_truncate_0026": (
        "opening_control_reconciliation_runs",
        "rsc_guard_opening_reconciliation_run_0026",
        "A",
        34,
    ),
    "trg_opening_reconciliation_items_guard_0026": (
        "opening_control_reconciliation_items",
        "rsc_guard_opening_reconciliation_item_0026",
        "A",
        23,
    ),
    "trg_opening_reconciliation_items_no_delete_0026": (
        "opening_control_reconciliation_items",
        "rsc_guard_opening_reconciliation_item_0026",
        "A",
        11,
    ),
    "trg_opening_reconciliation_items_no_truncate_0026": (
        "opening_control_reconciliation_items",
        "rsc_guard_opening_reconciliation_item_0026",
        "A",
        34,
    ),
    "trg_reconciliation_runs_formal_guard_0026": (
        "reconciliation_runs",
        "rsc_guard_reconciliation_run_projection_0026",
        "A",
        19,
    ),
    "trg_reconciliation_runs_formal_delete_0026": (
        "reconciliation_runs",
        "rsc_guard_reconciliation_run_projection_0026",
        "A",
        11,
    ),
    "trg_reconciliation_runs_no_truncate_0026": (
        "reconciliation_runs",
        "rsc_guard_reconciliation_run_projection_0026",
        "A",
        34,
    ),
    "trg_reconciliation_items_formal_guard_0026": (
        "reconciliation_items",
        "rsc_guard_reconciliation_item_projection_0026",
        "A",
        19,
    ),
    "trg_reconciliation_items_formal_delete_0026": (
        "reconciliation_items",
        "rsc_guard_reconciliation_item_projection_0026",
        "A",
        11,
    ),
    "trg_reconciliation_items_no_truncate_0026": (
        "reconciliation_items",
        "rsc_guard_reconciliation_item_projection_0026",
        "A",
        34,
    ),
    "trg_reconciliation_runs_formal_insert_0026": (
        "reconciliation_runs",
        "rsc_guard_reconciliation_run_projection_0026",
        "A",
        7,
    ),
    "trg_reconciliation_items_formal_insert_0026": (
        "reconciliation_items",
        "rsc_guard_reconciliation_item_projection_0026",
        "A",
        7,
    ),
    "trg_reconciliation_state_effect_guard_0026": (
        "state_transition_events",
        "rsc_guard_reconciliation_effect_0026",
        "A",
        31,
    ),
    "trg_reconciliation_outbox_effect_guard_0026": (
        "outbox_events",
        "rsc_guard_reconciliation_effect_0026",
        "A",
        31,
    ),
    "trg_reconciliation_audit_effect_guard_0026": (
        "audit_events",
        "rsc_guard_reconciliation_effect_0026",
        "A",
        31,
    ),
    "trg_reconciliation_state_effect_no_truncate_0026": (
        "state_transition_events",
        "rsc_guard_reconciliation_effect_0026",
        "A",
        34,
    ),
    "trg_reconciliation_outbox_effect_no_truncate_0026": (
        "outbox_events",
        "rsc_guard_reconciliation_effect_0026",
        "A",
        34,
    ),
    "trg_reconciliation_audit_effect_no_truncate_0026": (
        "audit_events",
        "rsc_guard_reconciliation_effect_0026",
        "A",
        34,
    ),
    "trg_stocktake_tasks_reconciliation_close_guard_0026": (
        "stocktake_tasks",
        "rsc_guard_opening_reconciliation_task_close_0026",
        "A",
        19,
    ),
}
EXPECTED_RECONCILIATION_CONSTRAINTS = {
    "fk_reconciliation_commands_consumption": {
        "table": "reconciliation_commands",
        "type": "f",
        "columns": ("id", "run_id", "operation", "target_version"),
        "referenced_table": (
            "opening_control_reconciliation_command_consumptions"
        ),
        "referenced_columns": (
            "command_id",
            "run_id",
            "operation",
            "target_version",
        ),
        "deferred": True,
    },
    "fk_opening_control_reconciliation_runs_run": {
        "table": "opening_control_reconciliation_runs",
        "type": "f",
        "columns": ("run_id",),
        "referenced_table": "reconciliation_runs",
        "referenced_columns": ("id",),
        "deferred": True,
    },
    "fk_opening_control_reconciliation_runs_create_command": {
        "table": "opening_control_reconciliation_runs",
        "type": "f",
        "columns": ("create_command_id", "run_id"),
        "referenced_table": "reconciliation_commands",
        "referenced_columns": ("id", "run_id"),
        "deferred": True,
    },
    "fk_opening_control_reconciliation_items_item": {
        "table": "opening_control_reconciliation_items",
        "type": "f",
        "columns": ("item_id",),
        "referenced_table": "reconciliation_items",
        "referenced_columns": ("id",),
        "deferred": True,
    },
    "uq_reconciliation_commands_target": {
        "table": "reconciliation_commands",
        "type": "u",
        "columns": ("run_id", "target_version"),
    },
    "uq_reconciliation_commands_id_run": {
        "table": "reconciliation_commands",
        "type": "u",
        "columns": ("id", "run_id"),
    },
    "uq_opening_control_reconciliation_consumptions_command": {
        "table": "opening_control_reconciliation_command_consumptions",
        "type": "u",
        "columns": ("command_id", "run_id", "operation", "target_version"),
    },
    "uq_opening_control_reconciliation_consumptions_target": {
        "table": "opening_control_reconciliation_command_consumptions",
        "type": "u",
        "columns": ("run_id", "target_version"),
    },
    "ck_reconciliation_commands_target_version": {
        "table": "reconciliation_commands",
        "type": "c",
        "tokens": ("target_version", ">= 0"),
    },
    "ck_reconciliation_commands_operation": {
        "table": "reconciliation_commands",
        "type": "c",
        "tokens": (
            "operation",
            "create_opening",
            "explain_opening",
            "approve_opening",
        ),
    },
    "ck_reconciliation_commands_request_reference": {
        "table": "reconciliation_commands",
        "type": "c",
        "tokens": (
            "request_reference",
            "opening-reconciliation-request-",
            "substr",
            "= 95",
            "replace",
        ),
    },
    "ck_reconciliation_commands_hashes": {
        "table": "reconciliation_commands",
        "type": "c",
        "tokens": (
            "idempotency_key_hash",
            "request_hash",
            "result_hash",
            "= 64",
            "replace",
        ),
    },
    "ck_opening_control_reconciliation_runs_manifest": {
        "table": "opening_control_reconciliation_runs",
        "type": "c",
        "tokens": (
            "item_manifest_sha256",
            "= 64",
            "replace",
        ),
    },
    "ck_opening_control_reconciliation_items_file_snapshot": {
        "table": "opening_control_reconciliation_items",
        "type": "c",
        "tokens": (
            "evidence_file_sha256",
            "evidence_file_size_bytes",
            "evidence_file_mime_type",
            "= 64",
            ">= 0",
        ),
    },
}
EXPECTED_RECONCILIATION_PARTIAL_INDEXES = {
    "uq_reconciliation_commands_create_run": "create_opening",
    "uq_reconciliation_commands_approve_run": "approve_opening",
}
EXPECTED_OPENING_TERMINAL_TRIGGERS = {
    "trg_stock_accounts_opening_observation_commit_0023": (
        "stock_accounts",
        "rsc_require_opening_observation_account_0023",
        "A",
        5,
    ),
    "trg_audit_events_opening_commit_0022": (
        "audit_events",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_inventory_freezes_opening_commit_0022": (
        "inventory_freezes",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        17,
    ),
    "trg_inventory_transactions_opening_commit_0022": (
        "inventory_transactions",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_inventory_transactions_immutable_0022": (
        "inventory_transactions",
        "rsc_block_inventory_ledger_mutation_0022",
        "A",
        27,
    ),
    "trg_inventory_transactions_immutable_truncate_0022": (
        "inventory_transactions",
        "rsc_block_inventory_ledger_mutation_0022",
        "A",
        34,
    ),
    "trg_inventory_movements_opening_commit_0022": (
        "inventory_movements",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_inventory_movements_immutable_0022": (
        "inventory_movements",
        "rsc_block_inventory_ledger_mutation_0022",
        "A",
        27,
    ),
    "trg_inventory_movements_immutable_truncate_0022": (
        "inventory_movements",
        "rsc_block_inventory_ledger_mutation_0022",
        "A",
        34,
    ),
    "trg_inventory_movement_serials_opening_commit_0022": (
        "inventory_movement_serials",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_inventory_movement_serials_immutable_0022": (
        "inventory_movement_serials",
        "rsc_block_inventory_ledger_mutation_0022",
        "A",
        27,
    ),
    "trg_inventory_movement_serials_immutable_truncate_0022": (
        "inventory_movement_serials",
        "rsc_block_inventory_ledger_mutation_0022",
        "A",
        34,
    ),
    "trg_stocktake_tasks_opening_mutation_0010": (
        "stocktake_tasks",
        "rsc_validate_opening_task_mutation_0010",
        "O",
        27,
    ),
    "trg_inventory_freezes_transition_0010": (
        "inventory_freezes",
        "rsc_validate_inventory_freeze_mutation_0010",
        "O",
        27,
    ),
    "trg_stocktake_postings_opening_unique_0022": (
        "stocktake_postings",
        "rsc_validate_opening_posting_unique_0022",
        "A",
        7,
    ),
    "trg_stocktake_postings_opening_commit_0022": (
        "stocktake_postings",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_stocktake_posting_items_opening_commit_0022": (
        "stocktake_posting_items",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_stocktake_posting_items_validate_insert_0010": (
        "stocktake_posting_items",
        "rsc_validate_stocktake_posting_item_0010",
        "A",
        7,
    ),
    "trg_stocktake_postings_terminal_0022": (
        "stocktake_postings",
        "rsc_block_opening_terminal_mutation_0022",
        "A",
        27,
    ),
    "trg_stocktake_postings_terminal_truncate_0022": (
        "stocktake_postings",
        "rsc_block_opening_terminal_mutation_0022",
        "A",
        34,
    ),
    "trg_stocktake_posting_items_terminal_0022": (
        "stocktake_posting_items",
        "rsc_block_opening_terminal_mutation_0022",
        "A",
        27,
    ),
    "trg_stocktake_posting_items_terminal_truncate_0022": (
        "stocktake_posting_items",
        "rsc_block_opening_terminal_mutation_0022",
        "A",
        34,
    ),
    "trg_inventory_opening_establishments_terminal_0022": (
        "inventory_opening_establishments",
        "rsc_block_opening_terminal_mutation_0022",
        "A",
        27,
    ),
    "trg_inventory_opening_establishments_terminal_truncate_0022": (
        "inventory_opening_establishments",
        "rsc_block_opening_terminal_mutation_0022",
        "A",
        34,
    ),
    "trg_inventory_opening_establishments_commit_0022": (
        "inventory_opening_establishments",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_outbox_events_opening_commit_0022": (
        "outbox_events",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_state_transition_events_opening_commit_0022": (
        "state_transition_events",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        5,
    ),
    "trg_stocktake_tasks_opening_commit_0022": (
        "stocktake_tasks",
        "rsc_require_opening_terminal_graph_0022",
        "A",
        17,
    ),
    "trg_stocktake_reviews_difference_completion_0016": (
        "stocktake_reviews",
        "rsc_require_stocktake_difference_completion_0016",
        "A",
        7,
    ),
    "trg_stocktake_difference_set_completions_immutable_0016": (
        "stocktake_difference_set_completions",
        "rsc_block_stocktake_review_fact_mutation_0016",
        "A",
        27,
    ),
    # The 0016 DDL token is 64 bytes; PostgreSQL stores 63 bytes.
    "trg_stocktake_difference_set_completions_immutable_truncate_001": (
        "stocktake_difference_set_completions",
        "rsc_block_stocktake_review_fact_mutation_0016",
        "A",
        34,
    ),
    "trg_stocktake_observation_dispositions_immutable_0016": (
        "stocktake_observation_dispositions",
        "rsc_block_stocktake_review_fact_mutation_0016",
        "A",
        27,
    ),
    "trg_stocktake_observation_dispositions_immutable_truncate_0016": (
        "stocktake_observation_dispositions",
        "rsc_block_stocktake_review_fact_mutation_0016",
        "A",
        34,
    ),
    "trg_stocktake_tasks_opening_insert_graph_0052": (
        "stocktake_tasks",
        "rsc_require_opening_task_insert_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_count_lines_current_0052": (
        "stocktake_count_lines",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
    ),
    "trg_stocktake_count_serials_current_0052": (
        "stocktake_count_serials",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
    ),
    "trg_stocktake_count_observations_current_0052": (
        "stocktake_count_observations",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
    ),
    "trg_stocktake_scope_count_completions_current_0052": (
        "stocktake_scope_count_completions",
        "rsc_require_opening_count_write_current_0052",
        "O",
        7,
    ),
    "trg_stocktake_count_lines_graph_0052": (
        "stocktake_count_lines",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_count_serials_graph_0052": (
        "stocktake_count_serials",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_count_observations_graph_0052": (
        "stocktake_count_observations",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_scope_count_completions_graph_0052": (
        "stocktake_scope_count_completions",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_round_submissions_graph_0052": (
        "stocktake_round_submissions",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_rounds_graph_0052": (
        "stocktake_rounds",
        "rsc_require_opening_live_graph_0052",
        "A",
        21,
    ),
    "trg_stocktake_reviews_graph_0052": (
        "stocktake_reviews",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_review_items_graph_0052": (
        "stocktake_review_items",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_differences_graph_0052": (
        "stocktake_differences",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_difference_set_completions_graph_0052": (
        "stocktake_difference_set_completions",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_observation_dispositions_graph_0052": (
        "stocktake_observation_dispositions",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_stocktake_postings_graph_0052": (
        "stocktake_postings",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_state_transition_events_opening_graph_0052": (
        "state_transition_events",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_outbox_events_opening_graph_0052": (
        "outbox_events",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
    "trg_audit_events_opening_graph_0052": (
        "audit_events",
        "rsc_require_opening_live_graph_0052",
        "A",
        5,
    ),
}
EXPECTED_OPENING_TERMINAL_INDEX = (
    "uq_stocktake_postings_one_opening_task_0022"
)
EXPECTED_FORMAL_FILE_TRIGGERS = {
    "trg_document_attachments_00_stocktake_evidence_seal_0057": (
        "document_attachments",
        "rsc_guard_stocktake_evidence_seal_0057",
        "A",
        7,
    ),
    "trg_files_formal_runtime_guard_0036": (
        "files", "rsc_guard_formal_file_object_0036", "A", 31
    ),
    "trg_receipt_exceptions_evidence_guard_0086": (
        "receipt_exceptions", "rsc_guard_formal_file_binding_0036", "A", 7
    ),
    "trg_files_formal_no_truncate_0036": (
        "files", "rsc_guard_formal_file_object_0036", "A", 34
    ),
    "trg_material_request_files_formal_guard_0036": (
        "material_request_files", "rsc_guard_formal_file_binding_0036", "A", 7
    ),
    "trg_approval_external_registrations_evidence_guard_0036": (
        "approval_external_registrations",
        "rsc_guard_formal_file_binding_0036",
        "A",
        31,
    ),
    "trg_approval_external_registrations_evidence_no_truncate_0036": (
        "approval_external_registrations",
        "rsc_guard_formal_file_binding_0036",
        "A",
        34,
    ),
    "trg_document_attachments_stocktake_evidence_guard_0036": (
        "document_attachments", "rsc_guard_formal_file_binding_0036", "A", 31
    ),
    "trg_document_attachments_stocktake_evidence_no_truncate_0036": (
        "document_attachments", "rsc_guard_formal_file_binding_0036", "A", 34
    ),
}
EXPECTED_FORMAL_FILE_INDEXES = {
    "uq_approval_external_registrations_evidence_file_0036": {
        "table": "approval_external_registrations",
        "columns": ("evidence_file_id",),
        "predicate": None,
    },
    "uq_document_attachments_stocktake_evidence_file_0036": {
        "table": "document_attachments",
        "columns": ("file_id",),
        "predicate": "stocktake_evidence",
    },
}
_MATERIAL_REQUEST_APPROVAL_FACT_TABLES_0029 = (
    "approval_step_candidates",
    "material_request_commands",
    "approval_external_registration_lines",
    "approval_step_line_decisions",
    "approval_actions",
)
EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS = {
    'trg_work_order_seals_proof_0094': ('work_order_command_seals', 'rsc_guard_work_order_command_seal_0094', 'A', 5, True, True, True),
    'trg_work_order_operations_seal_0094': ('work_order_material_operations', 'rsc_guard_work_order_command_seal_0094', 'A', 5, True, True, True),
    'trg_work_order_seals_immutable_0094': ('work_order_command_seals', 'rsc_guard_work_order_facts_0090', 'A', 27, False, False, False),
    'trg_work_order_seals_no_truncate_0094': ('work_order_command_seals', 'rsc_guard_work_order_facts_0090', 'A', 34, False, False, False),

    'trg_work_order_replacements_proof_0093': ('work_order_replacements', 'rsc_dispatch_work_order_replacement_0093', 'A', 5, True, True, True),
    'trg_work_order_operations_replacement_0093': ('work_order_material_operations', 'rsc_dispatch_work_order_replacement_0093', 'A', 5, True, True, True),
    'trg_work_order_pairs_replacement_0093': ('work_order_replacement_pairs', 'rsc_dispatch_work_order_replacement_0093', 'A', 5, True, True, True),
    'trg_work_order_replacements_immutable_0093': ('work_order_replacements', 'rsc_guard_work_order_facts_0090', 'A', 27, False, False, False),
    'trg_work_order_replacements_no_truncate_0093': ('work_order_replacements', 'rsc_guard_work_order_facts_0090', 'A', 34, False, False, False),

    'trg_inventory_serials_lifecycle_0092': ('inventory_serials', 'rsc_dispatch_serial_lifecycle_0092', 'A', 21, True, True, True),
    'trg_serial_current_positions_lifecycle_0092': ('serial_current_positions', 'rsc_dispatch_serial_lifecycle_0092', 'A', 29, True, True, True),
    'trg_inventory_movement_serials_lifecycle_0092': ('inventory_movement_serials', 'rsc_dispatch_serial_lifecycle_0092', 'A', 5, True, True, True),
    'trg_inventory_serials_identity_0092': ('inventory_serials', 'rsc_guard_serial_identity_0092', 'A', 27, False, False, False),
    'trg_inventory_serials_no_truncate_0092': ('inventory_serials', 'rsc_guard_serial_identity_0092', 'A', 34, False, False, False),
    'trg_serial_current_positions_no_truncate_0092': ('serial_current_positions', 'rsc_guard_serial_identity_0092', 'A', 34, False, False, False),
    'trg_inventory_transactions_proof_0090': ('inventory_transactions', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_inventory_movements_proof_0090': ('inventory_movements', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_inventory_movement_serials_proof_0090': ('inventory_movement_serials', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_work_order_material_operations_proof_0090': ('work_order_material_operations', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_work_order_material_lines_proof_0090': ('work_order_material_lines', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_work_order_material_serials_proof_0090': ('work_order_material_serials', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_work_order_replacement_pairs_proof_0090': ('work_order_replacement_pairs', 'rsc_dispatch_work_order_material_0090', 'A', 5, True, True, True),
    'trg_inventory_transactions_work_order_lock_0090': ('inventory_transactions', 'rsc_lock_work_order_material_0090', 'A', 7, False, False, False),
    'trg_work_order_material_operations_no_truncate_0090': ('work_order_material_operations', 'rsc_guard_work_order_facts_0090', 'A', 34, False, False, False),
    'trg_work_order_material_operations_immutable_0077': ('work_order_material_operations', 'rsc_guard_work_order_material_operations_immutable_0077', 'A', 27, False, False, False),
    'trg_work_order_material_lines_no_truncate_0090': ('work_order_material_lines', 'rsc_guard_work_order_facts_0090', 'A', 34, False, False, False),
    'trg_work_order_material_lines_immutable_0077': ('work_order_material_lines', 'rsc_guard_work_order_material_lines_immutable_0077', 'A', 27, False, False, False),
    'trg_work_order_material_serials_no_truncate_0090': ('work_order_material_serials', 'rsc_guard_work_order_facts_0090', 'A', 34, False, False, False),
    'trg_work_order_material_serials_immutable_0077': ('work_order_material_serials', 'rsc_guard_work_order_material_serials_immutable_0077', 'A', 27, False, False, False),
    'trg_work_order_replacement_pairs_no_truncate_0090': ('work_order_replacement_pairs', 'rsc_guard_work_order_facts_0090', 'A', 34, False, False, False),
    'trg_work_order_replacement_pairs_immutable_0077': ('work_order_replacement_pairs', 'rsc_guard_work_order_replacement_pairs_immutable_0077', 'A', 27, False, False, False),

    "trg_inbound_postings_facts_0087": ("inbound_postings", "rsc_guard_inbound_posting_0087", "A", 31, False, False, False),
    "trg_inbound_postings_no_truncate_0087": ("inbound_postings", "rsc_guard_inbound_posting_0087", "A", 34, False, False, False),
    **{
        f"trg_{table}_immutable_0073": (table, f"rsc_guard_{table}_immutable_0073", "A", 27, False, False, False)
        for table in ("shipments", "shipment_lines", "shipment_serials")
    },
    **{
        f"trg_{table}_outbound_graph_0072": (table, "rsc_dispatch_outbound_graph_0072", "A", 21, True, True, True)
        for table in ("outbound_postings", "outbound_posting_serials", "stock_reservation_picks",
                      "material_requests", "material_request_commands", "inventory_transactions")
    },
    **{
        f"trg_{table}_{suffix}_0072": (table, "rsc_guard_reservation_release_immutable_0070", "A", kind, False, False, False)
        for table in ("outbound_postings", "outbound_posting_serials")
        for suffix, kind in (("immutable", 27), ("no_truncate", 34))
    },
    "trg_outbound_postings_binding_0072": ("outbound_postings", "rsc_guard_outbound_binding_0072", "A", 7, False, False, False),
    **{
        f"trg_{table}_picking_graph_0071": (table, "rsc_dispatch_picking_graph_0071", "A", 21, True, True, True)
        for table in ("outbound_orders", "outbound_lines", "stock_reservation_picks", "stock_reservation_pick_serials",
                      "stock_reservation_releases", "stock_reservation_release_serials",
                      "material_requests", "material_request_commands", "inventory_transactions")
    },
    **{
        f"trg_{table}_{suffix}_0071": (table, "rsc_guard_reservation_release_immutable_0070", "A", kind, False, False, False)
        for table in ("outbound_orders", "outbound_lines", "stock_reservation_picks", "stock_reservation_pick_serials")
        for suffix, kind in (("immutable", 27), ("no_truncate", 34))
    },
    "trg_stock_reservation_picks_binding_0071": ("stock_reservation_picks", "rsc_guard_picking_binding_0071", "A", 7, False, False, False),

    **{
        f"trg_{table}_reservation_graph_0070": (
            table, "rsc_dispatch_reservation_graph_0070", "A", 21, True, True, True,
        )
        for table in (
            "stock_reservation_releases", "stock_reservation_release_serials", "stock_reservations",
            "stock_reservation_serials", "material_requests", "material_request_commands", "inventory_transactions",
        )
    },
    **{
        f"trg_{table}_{suffix}_0070": (
            table, "rsc_guard_reservation_release_immutable_0070", "A", trigger_type, False, False, False,
        )
        for table in ("stock_reservation_releases", "stock_reservation_release_serials")
        for suffix, trigger_type in (("immutable", 27), ("no_truncate", 34))
    },
    "trg_stock_reservation_releases_binding_0070": (
        "stock_reservation_releases", "rsc_guard_reservation_release_binding_0070", "A", 7, False, False, False,
    ),
    **{
        f"trg_{table_name}_{purpose}_0069": (
            table_name, function_name, "A", trigger_type, False, False, False,
        )
        for table_name, function_name in (
            ("stock_reservations", "rsc_guard_stock_reservation_0069"),
            ("stock_reservation_serials", "rsc_guard_stock_reservation_serials_binding_0069"),
        )
        for purpose, trigger_type in (("binding", 7), ("immutable", 27))
    },
    **{
        f"trg_{table_name}_immutable_0029": (
            table_name,
            "rsc_guard_material_request_fact_immutable_0029",
            "A",
            27,
            False,
            False,
            False,
        )
        for table_name in _MATERIAL_REQUEST_APPROVAL_FACT_TABLES_0029
    },
    **{
        f"trg_{table_name}_no_truncate_0029": (
            table_name,
            "rsc_guard_material_request_fact_immutable_0029",
            "A",
            34,
            False,
            False,
            False,
        )
        for table_name in _MATERIAL_REQUEST_APPROVAL_FACT_TABLES_0029
    },
    "trg_material_requests_guard_0029": (
        "material_requests",
        "rsc_guard_material_request_identity_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_material_request_revisions_guard_0029": (
        "material_request_revisions",
        "rsc_guard_material_request_revision_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_material_request_lines_guard_0029": (
        "material_request_lines",
        "rsc_guard_material_request_original_line_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_approval_instances_guard_0029": (
        "approval_instances",
        "rsc_guard_material_request_approval_instance_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_material_request_files_guard_0029": (
        "material_request_files",
        "rsc_guard_material_request_file_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_approval_delegations_guard_0029": (
        "approval_delegations",
        "rsc_guard_approval_delegation_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_approval_external_registrations_guard_0029": (
        "approval_external_registrations",
        "rsc_guard_external_registration_core_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_approval_external_registration_lines_quantity_0029": (
        "approval_external_registration_lines",
        "rsc_guard_external_registration_quantity_0029",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_approval_step_line_decisions_quantity_0029": (
        "approval_step_line_decisions",
        "rsc_guard_material_request_decision_quantity_0029",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_substitution_decisions_guard_0029": (
        "substitution_decisions",
        "rsc_guard_substitution_decision_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_supply_tasks_guard_0029": (
        "supply_tasks",
        "rsc_guard_supply_task_quantity_0029",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_supply_tasks_00_owner_guard_0059": (
        "supply_tasks",
        "rsc_guard_material_request_supply_task_0059",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_material_request_commands_000_supply_owner_guard_0060": (
        "material_request_commands",
        "rsc_guard_material_request_supply_write_0060",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_supply_tasks_000_owner_guard_0060": (
        "supply_tasks",
        "rsc_guard_material_request_supply_write_0060",
        "A",
        7,
        False,
        False,
        False,
    ),
    **{
        f"trg_{table_name}_supply_causality_0059": (
            table_name,
            "rsc_dispatch_material_request_supply_causality_0059",
            "A",
            29,
            True,
            True,
            True,
        )
        for table_name in (
            "audit_events",
            "material_request_commands",
            "material_requests",
            "state_transition_events",
            "supply_tasks",
        )
    },
    **{
        f"trg_{table_name}_no_truncate_0029": (
            table_name,
            "rsc_guard_material_request_fact_immutable_0029",
            "A",
            34,
            False,
            False,
            False,
        )
        for table_name in (
            "material_requests",
            "material_request_revisions",
            "material_request_lines",
            "material_request_files",
            "approval_instances",
            "approval_steps",
            "approval_external_registrations",
            "substitution_decisions",
            "supply_tasks",
            "approval_delegations",
        )
    },
    "trg_approval_steps_no_delete_0029": (
        "approval_steps",
        "rsc_guard_material_request_fact_immutable_0029",
        "A",
        11,
        False,
        False,
        False,
    ),
    "trg_approval_steps_write_guard_0030": (
        "approval_steps",
        "rsc_guard_approval_step_write_0030",
        "A",
        31,
        False,
        False,
        False,
    ),
    "trg_approval_step_candidates_write_guard_0030": (
        "approval_step_candidates",
        "rsc_guard_approval_candidate_write_0030",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_approval_actions_write_guard_0030": (
        "approval_actions",
        "rsc_guard_approval_action_write_0030",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_approval_step_line_decisions_current_guard_0030": (
        "approval_step_line_decisions",
        "rsc_guard_approval_decision_write_0030",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_approval_return_line_facts_write_guard_0030": (
        "approval_return_line_facts",
        "rsc_guard_approval_return_fact_0030",
        "A",
        7,
        False,
        False,
        False,
    ),
    "trg_approval_return_line_facts_immutable_0030": (
        "approval_return_line_facts",
        "rsc_guard_approval_return_fact_immutable_0030",
        "A",
        27,
        False,
        False,
        False,
    ),
    "trg_approval_return_line_facts_no_truncate_0030": (
        "approval_return_line_facts",
        "rsc_guard_approval_return_fact_immutable_0030",
        "A",
        34,
        False,
        False,
        False,
    ),
    **{
        f"trg_{table_name}_causality_0030": (
            table_name,
            "rsc_dispatch_approval_causality_0030",
            "A",
            21,
            True,
            True,
            True,
        )
        for table_name in (
            "approval_instances",
            "approval_steps",
            "approval_step_candidates",
            "approval_actions",
            "approval_step_line_decisions",
            "approval_return_line_facts",
        )
    },
    "trg_material_requests_status_transition_0045": (
        "material_requests",
        "rsc_guard_material_request_status_transition_0045",
        "A",
        23,
        False,
        False,
        False,
    ),
    "trg_material_request_lines_projection_write_0045": (
        "material_request_lines",
        "rsc_guard_material_request_line_projection_0045",
        "A",
        19,
        False,
        False,
        False,
    ),
    "trg_material_request_commands_parent_lock_0045": (
        "material_request_commands",
        "rsc_lock_material_request_command_parent_0045",
        "A",
        7,
        False,
        False,
        False,
    ),
    **{
        (
            "trg_approval_external_registration_lines_projection_0045"
            if table_name == "approval_external_registration_lines"
            else f"trg_{table_name}_approval_projection_0045"
        ): (
            table_name,
            "rsc_dispatch_material_request_approval_projection_0045",
            "A",
            21 if table_name == "material_requests" else 29,
            True,
            True,
            True,
        )
        for table_name in (
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
    },
    **{
        f"trg_{table_name}_content_write_0046": (
            table_name,
            "rsc_guard_material_request_content_write_0046",
            "A",
            7 if table_name == "material_request_commands" else 31,
            False,
            False,
            False,
        )
        for table_name in (
            "material_request_revisions",
            "material_request_lines",
            "material_request_files",
            "material_request_commands",
        )
    },
    **{
        f"trg_{table_name}_content_causality_0046": (
            table_name,
            "rsc_dispatch_material_request_content_causality_0046",
            "A",
            29,
            True,
            True,
            True,
        )
        for table_name in (
            "material_request_revisions",
            "material_request_lines",
            "material_request_files",
            "material_request_commands",
        )
    },
}
EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN = {
    "table_name": "material_request_commands",
    "column_name": "projection_manifest_sha256",
    "data_type": "character varying(64)",
    "is_not_null": False,
    "identity_kind": "",
    "generated_kind": "",
    "default_expression": None,
    "comment": "PostgreSQL trigger-owned material-request content projection digest",
}
EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK = {
    "constraint_name": "ck_material_request_commands_projection_manifest_0046",
    "table_name": "material_request_commands",
    "constraint_type": "c",
    "constrained_columns": ("operation", "projection_manifest_sha256"),
}
MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256 = {
    ('rsc_guard_work_order_command_seal_0094', ''): '4f8b97af1362ce7796aacd73fdcfa612017426f9e81556c31a06f9a2aded029b',

    ('rsc_check_work_order_replacement_0093', 'uuid'): 'd725c7c3a13482a8c4ddc8254e9ec819606ad0b6f0e5d3a5ec8cad1bbdc6606d',
    ('rsc_dispatch_work_order_replacement_0093', ''): 'e77457b79adfd9cf84e375916c05336812d23e788728e053e0a3f4062c2f779d',

    ('rsc_check_serial_lifecycle_0092', 'uuid'): '291ec36bca26d8765e61265f07a344baf57936f7f7d661cebfb38e2c27cee1b2',
    ('rsc_dispatch_serial_lifecycle_0092', ''): '612fc9726be5b13325f809b8dbf10185681f4073802b81c3771adceb83869135',
    ('rsc_guard_serial_identity_0092', ''): 'ead9491447496e5dab54787909a3cd9d0b9912a6ff007eef6394f5b99c1dd4bd',
    ('rsc_lock_work_order_material_0090', ''): '3020ff345109d942e1287ab48d8a2f53c5d722ff0fda403850649a0602c1f2a7',
    ('rsc_check_work_order_material_transaction_0090', 'uuid'): '85e03a1f8e4dcea6eda55501051e45e1b57944833a8d1ec6e5fe1a9772984458',
    ('rsc_dispatch_work_order_material_0090', ''): 'ea3172e2be65bc40e2832953d5698e67c0246aefd2eae5063061a19ac811d0ca',
    ('rsc_guard_work_order_facts_0090', ''): '216f8cc9b38fce14a09c3ecb79ef15547fbdd89a8de35f1e8b42e498012d6cca',
    ('rsc_guard_work_order_material_operations_immutable_0077', ''): 'cd8be861e17432dc90d8129908eaa963424b8358641301fe7d5b120a8b1dc02e',
    ('rsc_guard_work_order_material_lines_immutable_0077', ''): 'cd8be861e17432dc90d8129908eaa963424b8358641301fe7d5b120a8b1dc02e',
    ('rsc_guard_work_order_material_serials_immutable_0077', ''): 'cd8be861e17432dc90d8129908eaa963424b8358641301fe7d5b120a8b1dc02e',
    ('rsc_guard_work_order_replacement_pairs_immutable_0077', ''): 'cd8be861e17432dc90d8129908eaa963424b8358641301fe7d5b120a8b1dc02e',

    ("rsc_guard_inbound_posting_0087", ""): "c17d062d8a02384506c7dc0b533427787be3feeeb9763e84f2a8b8227524e458",
    ('rsc_material_request_outbound_state_0072', 'uuid, bigint'): "a89e922b7d9fbd19d46b4207fb3b36fffe1a063668073f59b1cf94db400a97ce",
    ('rsc_guard_outbound_binding_0072', ''): "9a5fcfd28a0af565202008beb20c33bf55a31ef64e7cae63ae73a01eb52a79ea",
    ('rsc_validate_outbound_graph_0072', 'uuid'): "be4206fc41fe8ea96e4b19423b25ea4d04df783b9bc25b03d626651d27424054",
    ('rsc_dispatch_outbound_graph_0072', ''): "d5309a548e6cd2644ffd8d2d789c0bd256aa530a25fb598d0793b9578770e5f8",
    ("rsc_material_request_picking_state_0071", "uuid, bigint"): "a239056dc39f5406fcac9a920660b597d86f21d53b101568f9380639f722b57f",
    ("rsc_guard_picking_binding_0071", ""): "b2a91c42311e5fb59882292550d1335d73c4f51f9349103c77b0a180518fa271",
    ("rsc_validate_picking_graph_0071", "uuid"): "5a97fd00534fca9523b96f330b3ed09208435c76b1afa9d884a231d5cf7a108d",
    ("rsc_dispatch_picking_graph_0071", ""): "37850e337166eb0391a4cc42dc977cc48546dc534c4cc38e0dec67a980ab7a01",
    ("rsc_material_request_reservation_state_0070", "uuid, bigint"):
        "6e0db6d66c96e7eaf106abc26ba89be98def947beac1117d693acf2c844503ea",
    ("rsc_guard_reservation_release_immutable_0070", ""):
        "c1b63bee1f3115cba6f66e084eb4d32cc626d51c5a14afc7ac85590e644a1bb3",
    ("rsc_guard_reservation_release_binding_0070", ""):
        "824782566f5c4e3101e21ccbb11e920af90ecee6277e48529378ae3395fb159c",
    ("rsc_validate_reservation_graph_0070", "uuid"):
        "e291af80827bff6d42f377a7b27d9ca70999fef23e52dc9b50e9c97e905e38e1",
    ("rsc_dispatch_reservation_graph_0070", ""):
        "73eafea5e22b4f239921e0917be4367c06b98815671654107d289c700f927d93",
    ("rsc_guard_stock_reservation_0069", ""):
        "2907a15a3bd47c0a736a30cb6e26ebf844a070f1bec655d8068e27c61f32f631",
    ("rsc_guard_stock_reservation_serials_binding_0069", ""):
        "617c93f38efeec117c5f416ffc4108053d9a409450876419553d1b08db7920b0",
    ("rsc_guard_material_request_fact_immutable_0029", ""):
        "af27608187df58d0652ba6d425e06ef27b74fe8c9683a48a8033fc4695f1502b",
    ("rsc_guard_material_request_original_line_0029", ""):
        "d00a8775d26e1b48e2a72798595354eedff873e2b83a7508c9c2961eb7d5d672",
    ("rsc_guard_material_request_identity_0029", ""):
        "349ba279bb1a9c3d46ac76aa8ad2b4a5b99f5de67db332ed6829e6b19ccac8f6",
    ("rsc_guard_material_request_revision_0029", ""):
        "ea718216fbf47320670b171a4aca22019fdbf4540533742aac6ac7573b31ab83",
    ("rsc_guard_material_request_approval_instance_0029", ""):
        "03c6b5dd42810dafccd5fa300eb0e82798afd8a4133cb67c1e81e82b303450c2",
    ("rsc_guard_material_request_decision_quantity_0029", ""):
        "ae517d9d4639cadd1da79aa3743e28a6e8ada5206ad4d64aca64195c0ad0c2c6",
    ("rsc_guard_external_registration_quantity_0029", ""):
        "047ff0975b18a789db9d82d2dcc4ed03eb5c409354b736d4158733390800940b",
    ("rsc_guard_external_registration_core_0029", ""):
        "0c8cfd10287b64840f97d7eaae69bd511888e27379486d29ec6c836303d3691d",
    ("rsc_guard_material_request_file_0029", ""):
        "185b12b8c78ed077683e7cbf13fffbfa1c7e2d85b77971508231a12238eb1e83",
    ("rsc_guard_approval_delegation_0029", ""):
        "3f5242fdf4933e5933ba404abf2aaca3f1d55326bc4f2f26219c25f6739c5bdc",
    ("rsc_guard_substitution_decision_0029", ""):
        "9ad0afc923c0e0debd2188f622896ff7c2c3f9e31859749b76258780355129eb",
    ("rsc_guard_supply_task_quantity_0029", ""):
        "af13776d92e55285cb7b6a45f3075886c1019eb7ae3f6a8fd15ea6693d57295a",
    ("rsc_validate_approval_instance_causality_0030", "uuid"):
        "aab04ccebcebc56b55eef4eab021e4f41a5e5c7cdb6b29f660cb462ca57f3b44",
    ("rsc_dispatch_approval_causality_0030", ""):
        "c799bdc350c7397a1acc7599058b5416e990db8a8f0147af9c386c791b4e8758",
    ("rsc_guard_approval_step_write_0030", ""):
        "aff44deeff0f7f324c7c67fa259f18907e69d9f0588f692976ffc4b58f868d66",
    ("rsc_guard_approval_candidate_write_0030", ""):
        "568e525fa0f797f389f52b9ee65032fa70ec0e035f61b0461da886e11ed6fa62",
    ("rsc_guard_approval_action_write_0030", ""):
        "75761b0a5eb0c2af643baf953758f26b935d4bc3e5770fba2208854c6a3b46b4",
    ("rsc_guard_approval_decision_write_0030", ""):
        "7b305ed5a54fa819a2d5b098203ba20d165981516e03cdcf85f921a9be25e463",
    ("rsc_guard_approval_return_fact_0030", ""):
        "ecb59a37bcc6981d3b7852c22e4f0e3a844575b531608bbc011034e5e079467e",
    ("rsc_guard_approval_return_fact_immutable_0030", ""):
        "b5bf3a7eee382e02928d4ca9d413b9c7fcec942cb9213f736720cb9a9bcd6654",
    ("rsc_guard_material_request_status_transition_0045", ""):
        "b699a13ff58137a3a679745b5f5c2757f882615ec9763b4805f0e8d33d9a8625",
    ("rsc_guard_material_request_line_projection_0045", ""):
        "e464574227a024c1c7696bff4c9989de87298bf45f034d26d10163e734fb90b3",
    ("rsc_lock_material_request_command_parent_0045", ""):
        "d7993645f27d679c2427fe2146d8a2f5f51ad5aecc11de774aeafbf65c77c79b",
    (
        "rsc_validate_material_request_terminal_causality_0045",
        "uuid, uuid, uuid, bigint",
    ): "4f7ce45088d9005b70d17418b6677344be11c6bdc4a2026d0c24f979083d2a47",
    ("rsc_validate_material_request_return_causality_0045", "uuid, uuid"):
        "e610f8b38ec4c38cc3eb271d99737ae9df7e91c123080ad400b642c8447cb4b2",
    ("rsc_validate_material_request_external_causality_0045", "uuid"):
        "0f5bd6658edcb46dac6282109b71a109c14003862c89b3f5700d89f1ac13fa26",
    ("rsc_validate_material_request_approval_projection_0045", "uuid"):
        "9b62535f2529a416a338ce9dee244850a94c0da23a1422c3f01ee9727192f9ff",
    ("rsc_dispatch_material_request_approval_projection_0045", ""):
        "244d188e126e66fd4b020da01c076a3018f2c8841230bd255da45b7913776d54",
    ("rsc_guard_material_request_supply_task_0059", ""):
        "913d606ff9f47fd05feda92d75ef76477daf6823b71ecdb9c47cabf5355a5398",
    ("rsc_guard_material_request_supply_write_0060", ""):
        "0fe289826a8aa4d14e2ecd48a9900484bf8929a054dbfa52340fa27786453f26",
    ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"):
        "ef61be0a9be9435412d44d07745460fb395a2a77c443a7452c6ea84f333c2922",
    ("rsc_dispatch_material_request_supply_causality_0059", ""):
        "4228949da83f59ea1b46a8badd7c0fe8c58e188b7a88eac318ac032acd339e92",
    ("rsc_guard_material_request_content_write_0046", ""):
        "a1dac8272cf64272d782f02f6270aab5fe285334fb13f88c11aaafc6d1d0364c",
    ("rsc_validate_material_request_content_causality_0046", "uuid"):
        "bb1af385573b281207dcc149f9d3c421ae9b45dc19e549979d9e646fe2f73eae",
    ("rsc_dispatch_material_request_content_causality_0046", ""):
        "d9e51c26520897b17b597cdaa3ea754039a92b06f6e0beee321f3a500dfe2da9",
}
MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS = frozenset(
    {
        ("rsc_guard_work_order_command_seal_0094", ""),
        ('rsc_check_work_order_replacement_0093', 'uuid'),
        ('rsc_dispatch_work_order_replacement_0093', ''),

        ('rsc_check_serial_lifecycle_0092', 'uuid'),
        ('rsc_dispatch_serial_lifecycle_0092', ''),
        ('rsc_guard_serial_identity_0092', ''),
        ('rsc_lock_work_order_material_0090', ''),
        ('rsc_check_work_order_material_transaction_0090', 'uuid'),
        ('rsc_dispatch_work_order_material_0090', ''),
        ('rsc_guard_work_order_facts_0090', ''),
        ('rsc_guard_work_order_material_operations_immutable_0077', ''),
        ('rsc_guard_work_order_material_lines_immutable_0077', ''),
        ('rsc_guard_work_order_material_serials_immutable_0077', ''),
        ('rsc_guard_work_order_replacement_pairs_immutable_0077', ''),

        ("rsc_guard_inbound_posting_0087", ""),
        ('rsc_material_request_outbound_state_0072', 'uuid, bigint'),
        ('rsc_guard_outbound_binding_0072', ''),
        ('rsc_validate_outbound_graph_0072', 'uuid'),
        ('rsc_dispatch_outbound_graph_0072', ''),
        ("rsc_material_request_picking_state_0071", "uuid, bigint"),
        ("rsc_guard_picking_binding_0071", ""),
        ("rsc_validate_picking_graph_0071", "uuid"),
        ("rsc_dispatch_picking_graph_0071", ""),
        ("rsc_material_request_reservation_state_0070", "uuid, bigint"),
        ("rsc_guard_reservation_release_immutable_0070", ""),
        ("rsc_guard_reservation_release_binding_0070", ""),
        ("rsc_validate_reservation_graph_0070", "uuid"),
        ("rsc_dispatch_reservation_graph_0070", ""),
        ("rsc_guard_stock_reservation_0069", ""),
        ("rsc_guard_stock_reservation_serials_binding_0069", ""),
        ("rsc_guard_material_request_file_0029", ""),
        ("rsc_dispatch_approval_causality_0030", ""),
        (
            "rsc_validate_material_request_terminal_causality_0045",
            "uuid, uuid, uuid, bigint",
        ),
        ("rsc_validate_material_request_return_causality_0045", "uuid, uuid"),
        ("rsc_validate_material_request_external_causality_0045", "uuid"),
        ("rsc_validate_material_request_approval_projection_0045", "uuid"),
        ("rsc_dispatch_material_request_approval_projection_0045", ""),
        ("rsc_guard_material_request_supply_task_0059", ""),
        ("rsc_guard_material_request_supply_write_0060", ""),
        ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"),
        ("rsc_dispatch_material_request_supply_causality_0059", ""),
        ("rsc_guard_material_request_content_write_0046", ""),
        ("rsc_validate_material_request_content_causality_0046", "uuid"),
        ("rsc_dispatch_material_request_content_causality_0046", ""),
    }
)
MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS = frozenset(
    {
        ("rsc_check_work_order_replacement_0093", "uuid"),
        ("rsc_check_serial_lifecycle_0092", "uuid"),
        ("rsc_check_work_order_material_transaction_0090", "uuid"),
        ("rsc_validate_outbound_graph_0072", "uuid"),
        ("rsc_validate_picking_graph_0071", "uuid"),
        ("rsc_validate_reservation_graph_0070", "uuid"),
        ("rsc_validate_approval_instance_causality_0030", "uuid"),
        (
            "rsc_validate_material_request_terminal_causality_0045",
            "uuid, uuid, uuid, bigint",
        ),
        ("rsc_validate_material_request_return_causality_0045", "uuid, uuid"),
        ("rsc_validate_material_request_external_causality_0045", "uuid"),
        ("rsc_validate_material_request_approval_projection_0045", "uuid"),
        ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"),
        ("rsc_validate_material_request_content_causality_0046", "uuid"),
    }
)
POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037 = (
    "trg_material_request_cancellation_line_facts_cancellation_graph"
)
EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS = {
    "trg_material_request_cancellation_facts_guard_0037": (
        "material_request_cancellation_line_facts",
        "rsc_guard_material_request_cancellation_fact_0037",
        "A",
        31,
    ),
    "trg_material_request_cancellation_facts_no_truncate_0037": (
        "material_request_cancellation_line_facts",
        "rsc_guard_material_request_cancellation_fact_0037",
        "A",
        34,
    ),
    "trg_approval_actions_cancel_guard_0037": (
        "approval_actions", "rsc_guard_material_request_cancel_action_0037", "A", 7
    ),
    "trg_material_request_commands_parent_lock_0037": (
        "material_request_commands",
        "rsc_lock_material_request_parent_write_0037",
        "A",
        7,
    ),
    "trg_substitution_decisions_request_parent_lock_0037": (
        "substitution_decisions",
        "rsc_lock_material_request_parent_write_0037",
        "A",
        23,
    ),
    "trg_supply_tasks_request_parent_lock_0037": (
        "supply_tasks", "rsc_lock_material_request_parent_write_0037", "A", 23
    ),
    "trg_inventory_transactions_request_parent_lock_0037": (
        "inventory_transactions",
        "rsc_lock_material_request_parent_write_0037",
        "A",
        7,
    ),
    "trg_notification_events_request_parent_lock_0037": (
        "notification_events",
        "rsc_lock_material_request_parent_write_0037",
        "A",
        7,
    ),
    "trg_outbox_events_request_parent_lock_0037": (
        "outbox_events", "rsc_lock_material_request_parent_write_0037", "A", 7
    ),
    **{
        (
            POSTGRESQL_MATERIAL_REQUEST_CANCELLATION_FACT_GRAPH_TRIGGER_0037
            if table_name == "material_request_cancellation_line_facts"
            else f"trg_{table_name}_cancellation_graph_0037"
        ): (
            table_name,
            "rsc_require_material_request_cancellation_graph_0037",
            "A",
            trigger_type,
        )
        for table_name, trigger_type in {
            "material_requests": 17,
            "material_request_lines": 21,
            "material_request_commands": 5,
            "approval_instances": 17,
            "approval_actions": 5,
            "material_request_cancellation_line_facts": 5,
            "substitution_decisions": 21,
            "supply_tasks": 21,
            "state_transition_events": 5,
            "audit_events": 5,
            "inventory_transactions": 5,
            "notification_events": 5,
            "outbox_events": 5,
        }.items()
    },
}
EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES = {
    "uq_approval_actions_cancel_fact_identity_0037": {
        "table": "approval_actions",
        "columns": ("id", "instance_id", "command_id"),
        "unique": True,
    },
    "ix_material_request_cancel_facts_instance_0037": {
        "table": "material_request_cancellation_line_facts",
        "columns": ("instance_id", "occurred_at"),
        "unique": False,
    },
    "ix_material_request_cancel_facts_request_0037": {
        "table": "material_request_cancellation_line_facts",
        "columns": ("request_id",),
        "unique": False,
    },
    "ix_material_request_cancel_facts_command_0037": {
        "table": "material_request_cancellation_line_facts",
        "columns": ("cancel_command_id",),
        "unique": False,
    },
}
EXPECTED_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX = {
    "name": "uq_audit_events_material_request_request_id_0039",
    "table": "audit_events",
    "columns": ("request_id",),
    "literals": frozenset(
        {
            "material_request",
            "material_request.withdraw",
            "material_request.cancel",
        }
    ),
}
EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS = {
    "trg_kms_data_key_pins_immutable_0040": (
        "kms_data_key_pins",
        "rsc_reject_kms_data_key_pin_mutation_0040",
        "A",
        27,
    ),
    "trg_kms_data_key_pins_no_truncate_0040": (
        "kms_data_key_pins",
        "rsc_reject_kms_data_key_pin_mutation_0040",
        "A",
        34,
    ),
}
EXPECTED_KMS_DATA_KEY_PIN_COLUMNS = (
    ("purpose", "character varying(64)"),
    ("kms_key_id", "character varying(256)"),
    ("application_key_version", "integer"),
    ("kms_key_version_id", "character varying(128)"),
    ("ciphertext_sha256", "character varying(64)"),
    ("created_at", "timestamp with time zone"),
)
EXPECTED_KMS_DATA_KEY_PIN_CONSTRAINTS = {
    "ck_kms_data_key_pins_coordinates_0040": {
        "type": "c",
        "columns": ("kms_key_id", "kms_key_version_id"),
        "backing_index": None,
        "no_inherit": False,
    },
    "ck_kms_data_key_pins_purpose_0040": {
        "type": "c",
        "columns": ("purpose",),
        "backing_index": None,
        "no_inherit": False,
    },
    "ck_kms_data_key_pins_sha256_0040": {
        "type": "c",
        "columns": ("ciphertext_sha256",),
        "backing_index": None,
        "no_inherit": False,
    },
    "ck_kms_data_key_pins_version_0040": {
        "type": "c",
        "columns": ("application_key_version",),
        "backing_index": None,
        "no_inherit": False,
    },
    "pk_kms_data_key_pins_coordinate_0040": {
        "type": "p",
        "columns": ("purpose", "kms_key_id", "application_key_version"),
        "backing_index": "pk_kms_data_key_pins_coordinate_0040",
        "no_inherit": True,
    },
    "uq_kms_data_key_pins_ciphertext_0040": {
        "type": "u",
        "columns": ("ciphertext_sha256",),
        "backing_index": "uq_kms_data_key_pins_ciphertext_0040",
        "no_inherit": True,
    },
    "uq_kms_data_key_pins_purpose_version_0040": {
        "type": "u",
        "columns": ("purpose", "application_key_version"),
        "backing_index": "uq_kms_data_key_pins_purpose_version_0040",
        "no_inherit": True,
    },
}
EXPECTED_KMS_DATA_KEY_PIN_INDEXES = {
    "pk_kms_data_key_pins_coordinate_0040": {
        "constraint": "pk_kms_data_key_pins_coordinate_0040",
        "columns": ("purpose", "kms_key_id", "application_key_version"),
        "primary": True,
    },
    "uq_kms_data_key_pins_ciphertext_0040": {
        "constraint": "uq_kms_data_key_pins_ciphertext_0040",
        "columns": ("ciphertext_sha256",),
        "primary": False,
    },
    "uq_kms_data_key_pins_purpose_version_0040": {
        "constraint": "uq_kms_data_key_pins_purpose_version_0040",
        "columns": ("purpose", "application_key_version"),
        "primary": False,
    },
}
EXPECTED_SMS_DISPATCH_TRIGGERS = {
    "trg_sms_challenge_dispatches_guard_0041": (
        "sms_challenge_dispatches",
        "rsc_guard_sms_challenge_dispatch_0041",
        "A",
        31,
    ),
    "trg_sms_challenge_dispatches_no_truncate_0041": (
        "sms_challenge_dispatches",
        "rsc_guard_sms_challenge_dispatch_0041",
        "A",
        34,
    ),
}
EXPECTED_SMS_DISPATCH_COLUMNS = (
    ("challenge_id", "uuid", True),
    ("provider", "character varying(40)", True),
    ("mobile_hash", "character varying(64)", True),
    ("status", "character varying(24)", True),
    ("request_sha256", "character varying(64)", True),
    ("owner_token_hash", "character varying(64)", False),
    ("provider_reference", "character varying(160)", False),
    ("claimed_at", "timestamp with time zone", False),
    ("lease_expires_at", "timestamp with time zone", False),
    ("accepted_at", "timestamp with time zone", False),
    ("uncertain_at", "timestamp with time zone", False),
    ("expired_at", "timestamp with time zone", False),
    ("created_at", "timestamp with time zone", True),
)
EXPECTED_SMS_DISPATCH_CONSTRAINTS = {
    "ck_sms_challenge_dispatches_status": "c",
    "ck_sms_challenge_dispatches_request_sha256": "c",
    "ck_sms_challenge_dispatches_mobile_hash": "c",
    "ck_sms_challenge_dispatches_owner_hash": "c",
    "ck_sms_challenge_dispatches_state_evidence": "c",
    "ck_sms_challenge_dispatches_lease_order": "c",
    "ck_sms_challenge_dispatches_accepted_order": "c",
    "ck_sms_challenge_dispatches_uncertain_order": "c",
    "ck_sms_challenge_dispatches_expired_order": "c",
    "pk_sms_challenge_dispatches_0041": "p",
    "fk_sms_challenge_dispatches_challenge_0041": "f",
}
EXPECTED_SMS_DISPATCH_NONINHERIT_CONSTRAINTS = frozenset(
    {
        "pk_sms_challenge_dispatches_0041",
        "fk_sms_challenge_dispatches_challenge_0041",
    }
)
EXPECTED_SMS_DISPATCH_INDEXES = {
    "pk_sms_challenge_dispatches_0041": {
        "columns": ("challenge_id",),
        "unique": True,
        "primary": True,
        "predicate": None,
    },
    "ix_sms_challenge_dispatches_status": {
        "columns": ("status",),
        "unique": False,
        "primary": False,
        "predicate": None,
    },
    "ix_sms_challenge_dispatches_unresolved_lease": {
        "columns": ("status", "lease_expires_at"),
        "unique": False,
        "primary": False,
        "predicate": None,
    },
    "uq_sms_challenge_dispatches_provider_reference": {
        "columns": ("provider", "provider_reference"),
        "unique": True,
        "primary": False,
        "predicate": "provider_reference is not null",
    },
    "uq_sms_challenge_dispatches_unresolved_mobile": {
        "columns": ("provider", "mobile_hash"),
        "unique": True,
        "primary": False,
        "predicate_literals": ("sending", "uncertain"),
    },
}
EXPECTED_SMS_DISPATCH_TABLE_PRIVILEGES = frozenset({"SELECT", "INSERT"})
EXPECTED_SMS_DISPATCH_UPDATE_COLUMNS = frozenset(
    {
        "status",
        "owner_token_hash",
        "provider_reference",
        "claimed_at",
        "lease_expires_at",
        "accepted_at",
        "uncertain_at",
        "expired_at",
    }
)
_STOCKTAKE_START_CAUSALITY_TABLES = (
    "stocktake_tasks",
    "stocktake_scopes",
    "inventory_freezes",
    "stocktake_snapshot_lines",
    "stocktake_rounds",
    "stocktake_start_completions",
    "state_transition_events",
    "audit_events",
)
EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS = {
    "trg_stocktake_start_completions_guard_0047": (
        "stocktake_start_completions",
        "rsc_guard_stocktake_start_completion_0047",
        "A",
        31,
        False,
        False,
        False,
    ),
    **{
        f"trg_{table_name}_stocktake_start_sealed_0047": (
            table_name,
            "rsc_guard_stocktake_start_completion_0047",
            "A",
            31,
            False,
            False,
            False,
        )
        for table_name in _STOCKTAKE_START_CAUSALITY_TABLES
        if table_name != "stocktake_start_completions"
    },
    **{
        f"trg_{table_name}_stocktake_start_causality_0047": (
            table_name,
            "rsc_dispatch_nonopening_stocktake_start_causality_0047",
            "A",
            29,
            True,
            True,
            True,
        )
        for table_name in _STOCKTAKE_START_CAUSALITY_TABLES
    },
}
EXPECTED_STOCKTAKE_START_COMPLETION_COLUMNS = (
    ("id", "uuid", True),
    ("task_id", "uuid", True),
    ("initial_round_id", "uuid", True),
    ("expected_task_version", "bigint", True),
    ("started_task_version", "bigint", True),
    ("cutoff_ledger_cursor", "bigint", True),
    ("cutoff_at", "timestamp with time zone", True),
    ("scope_count", "integer", True),
    ("snapshot_line_count", "integer", True),
    ("active_freeze_count", "integer", True),
    ("scope_manifest_sha256", "character varying(64)", True),
    ("snapshot_manifest_sha256", "character varying(64)", True),
    ("request_sha256", "character varying(64)", True),
    ("idempotency_key_hash", "character varying(64)", True),
    ("started_by_user_id", "character varying(36)", True),
    ("started_by_person_id", "uuid", True),
    ("started_role_assignment_id", "uuid", True),
    ("authorization_version", "bigint", True),
    ("role_code", "character varying(40)", True),
    ("scope_type", "character varying(24)", True),
    ("scope_id_snapshot", "character varying(80)", True),
    ("authorization_sha256", "character varying(64)", True),
    ("graph_manifest_sha256", "character varying(64)", True),
    ("started_at", "timestamp with time zone", True),
    ("created_at", "timestamp with time zone", True),
)
EXPECTED_STOCKTAKE_START_COMPLETION_CONSTRAINTS = {
    "pk_stocktake_start_completions_0047": ("p", ("id",), None, ()),
    "uq_stocktake_start_completions_task_0047": (
        "u", ("task_id",), None, ()),
    "uq_stocktake_start_completions_round_0047": (
        "u", ("initial_round_id",), None, ()),
    "uq_stocktake_start_completions_idempotency_0047": (
        "u", ("idempotency_key_hash",), None, ()),
    "uq_stocktake_start_completions_id_task_0047": (
        "u", ("id", "task_id"), None, ()),
    "fk_stocktake_start_completions_task_0047": (
        "f", ("task_id",), "stocktake_tasks", ("id",)),
    "fk_stocktake_start_completions_round_0047": (
        "f", ("initial_round_id", "task_id"), "stocktake_rounds",
        ("id", "task_id")),
    "fk_stocktake_start_completions_user_0047": (
        "f", ("started_by_user_id",), "users", ("id",)),
    "fk_stocktake_start_completions_person_0047": (
        "f", ("started_by_person_id",), "people", ("id",)),
    "fk_stocktake_start_completions_assignment_0047": (
        "f", ("started_role_assignment_id",), "role_assignments", ("id",)),
    "ck_stocktake_start_completions_versions_0047": ("c", (), None, ()),
    "ck_stocktake_start_completions_counts_0047": ("c", (), None, ()),
    "ck_stocktake_start_completions_authorization_0047": (
        "c", (), None, ()),
    "ck_stocktake_start_completions_hashes_0047": ("c", (), None, ()),
    "ck_stocktake_start_completions_chronology_0047": (
        "c", (), None, ()),
}
EXPECTED_STOCKTAKE_START_COMPLETION_INDEXES = {
    "pk_stocktake_start_completions_0047": (("id",), True, True),
    "uq_stocktake_start_completions_task_0047": (("task_id",), True, False),
    "uq_stocktake_start_completions_round_0047": (
        ("initial_round_id",), True, False),
    "uq_stocktake_start_completions_idempotency_0047": (
        ("idempotency_key_hash",), True, False),
    "uq_stocktake_start_completions_id_task_0047": (
        ("id", "task_id"), True, False),
    "ix_stocktake_start_completions_actor_0047": (
        ("started_by_user_id", "started_at"), False, False),
}
_STOCKTAKE_CLOSE_FACT_TABLES = (
    "stocktake_close_transition_acks",
    "stocktake_close_reconciliation_completions",
    "stocktake_close_reconciliation_accounts",
    "stocktake_close_reconciliation_serials",
    "stocktake_close_completions",
)
EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS = {
    "trg_stocktake_tasks_close_graph_0038": (
        "stocktake_tasks",
        "rsc_require_nonopening_stocktake_close_graph_0038",
        "A",
        29,
        True,
        True,
        True,
        False,
    ),
    "trg_stocktake_close_reconciliations_graph_0038": (
        "stocktake_close_reconciliation_completions",
        "rsc_require_nonopening_stocktake_close_graph_0038",
        "A", 29, True, True, True, False,
    ),
    "trg_stocktake_close_reconciliation_accounts_graph_0038": (
        "stocktake_close_reconciliation_accounts",
        "rsc_require_nonopening_stocktake_close_graph_0038",
        "A", 29, True, True, True, False,
    ),
    "trg_stocktake_close_reconciliation_serials_graph_0038": (
        "stocktake_close_reconciliation_serials",
        "rsc_require_nonopening_stocktake_close_graph_0038",
        "A", 29, True, True, True, False,
    ),
    "trg_stocktake_close_completions_graph_0038": (
        "stocktake_close_completions",
        "rsc_require_nonopening_stocktake_close_graph_0038",
        "A", 29, True, True, True, False,
    ),
    "trg_stocktake_close_transition_acks_graph_0038": (
        "stocktake_close_transition_acks",
        "rsc_require_nonopening_stocktake_close_graph_0038",
        "A", 29, True, True, True, False,
    ),
    "trg_stocktake_tasks_close_ack_0038": (
        "stocktake_tasks",
        "rsc_record_nonopening_stocktake_close_ack_0038",
        "A", 17, False, False, False, True,
    ),
    "trg_stocktake_close_transition_acks_insert_0038": (
        "stocktake_close_transition_acks",
        "rsc_guard_nonopening_stocktake_close_ack_0038",
        "A", 7, False, False, False, False,
    ),
    "trg_audit_events_nonopening_stocktake_close_guard_0038": (
        "audit_events",
        "rsc_guard_nonopening_stocktake_close_event_0038",
        "A", 7, False, False, False, False,
    ),
    "trg_state_events_nonopening_stocktake_close_guard_0038": (
        "state_transition_events",
        "rsc_guard_nonopening_stocktake_close_event_0038",
        "A", 7, False, False, False, False,
    ),
    **{
        f"trg_{table_name}_immutable_0038": (
            table_name,
            "rsc_reject_nonopening_stocktake_close_mutation_0038",
            "A",
            27,
            False,
            False,
            False,
            False,
        )
        for table_name in _STOCKTAKE_CLOSE_FACT_TABLES
    },
    **{
        f"trg_{table_name}_no_truncate_0038": (
            table_name,
            "rsc_reject_nonopening_stocktake_close_mutation_0038",
            "A",
            34,
            False,
            False,
            False,
            False,
        )
        for table_name in _STOCKTAKE_CLOSE_FACT_TABLES
    },
}
EXPECTED_NONOPENING_STOCKTAKE_CLOSE_INDEXES = {
    "ix_stocktake_close_transition_acks_kind_0038": {
        "table": "stocktake_close_transition_acks",
        "columns": ("transition_kind", "occurred_at"),
        "unique": False,
    },
    "ix_stocktake_close_reconciliation_task_0038": {
        "table": "stocktake_close_reconciliation_completions",
        "columns": ("task_id", "reconciliation_no"),
        "unique": False,
    },
    "ix_stocktake_close_reconciliation_accounts_scope_0038": {
        "table": "stocktake_close_reconciliation_accounts",
        "columns": ("task_id", "scope_id"),
        "unique": False,
    },
    "ix_stocktake_close_reconciliation_serials_scope_0038": {
        "table": "stocktake_close_reconciliation_serials",
        "columns": ("task_id", "evidence_scope_id"),
        "unique": False,
    },
    "ix_stocktake_close_completions_actor_0038": {
        "table": "stocktake_close_completions",
        "columns": ("closed_by_user_id", "closed_at"),
        "unique": False,
    },
}
EXPECTED_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINTS = {
    "fk_stocktake_close_reconciliation_transition_ack_0038": {
        "table": "stocktake_close_reconciliation_completions",
        "columns": ("task_id", "reconciled_task_version", "id"),
        "referenced_table": "stocktake_close_transition_acks",
        "referenced_columns": (
            "task_id",
            "target_task_version",
            "reconciliation_completion_id",
        ),
    },
    "fk_stocktake_close_completion_transition_ack_0038": {
        "table": "stocktake_close_completions",
        "columns": ("task_id", "closed_task_version", "id"),
        "referenced_table": "stocktake_close_transition_acks",
        "referenced_columns": (
            "task_id",
            "target_task_version",
            "close_completion_id",
        ),
    },
}
OPENING_COMMIT_TRIGGER_NAMES = frozenset(
    {
        "trg_audit_events_opening_commit_0022",
        "trg_inventory_freezes_opening_commit_0022",
        "trg_inventory_movement_serials_opening_commit_0022",
        "trg_inventory_movements_opening_commit_0022",
        "trg_inventory_transactions_opening_commit_0022",
        "trg_inventory_opening_establishments_commit_0022",
        "trg_outbox_events_opening_commit_0022",
        "trg_state_transition_events_opening_commit_0022",
        "trg_stock_accounts_opening_observation_commit_0023",
        "trg_stocktake_posting_items_opening_commit_0022",
        "trg_stocktake_postings_opening_commit_0022",
        "trg_stocktake_tasks_opening_commit_0022",
        "trg_stocktake_tasks_opening_insert_graph_0052",
        "trg_stocktake_count_lines_graph_0052",
        "trg_stocktake_count_serials_graph_0052",
        "trg_stocktake_count_observations_graph_0052",
        "trg_stocktake_scope_count_completions_graph_0052",
        "trg_stocktake_round_submissions_graph_0052",
        "trg_stocktake_rounds_graph_0052",
        "trg_stocktake_reviews_graph_0052",
        "trg_stocktake_review_items_graph_0052",
        "trg_stocktake_differences_graph_0052",
        "trg_stocktake_difference_set_completions_graph_0052",
        "trg_stocktake_observation_dispositions_graph_0052",
        "trg_stocktake_postings_graph_0052",
        "trg_state_transition_events_opening_graph_0052",
        "trg_outbox_events_opening_graph_0052",
        "trg_audit_events_opening_graph_0052",
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SQL_STRING_LITERAL_PATTERN = re.compile(r"'((?:''|[^'])*)'")
RECEIPT_SYNC_RUNTIME_FUNCTION = ("rsc_oam_receipt_rls_check_0082", "text, text, text, jsonb")
OAM_SYNC_RUNTIME_FUNCTIONS = {
    RECEIPT_SYNC_RUNTIME_FUNCTION,
    ("rsc_oam_rls_check_0044", "text, text, jsonb"),
    ("rsc_oam_runtime_binding_ready_0044", ""),
}
OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS = {
    coordinate: (
        OAM_SYNC_FUNCTION_MANIFEST[
            f"{coordinate[0]}({coordinate[1].replace(', ', ',')})"
        ][1],
        True,
        OAM_SYNC_FUNCTION_MANIFEST[
            f"{coordinate[0]}({coordinate[1].replace(', ', ',')})"
        ][2],
        ("search_path=pg_catalog",),
    )
    for coordinate in OAM_SYNC_RUNTIME_FUNCTIONS
}
OAM_SYNC_RUNTIME_FUNCTION_SHAPES = {
    coordinate: (
        "f",
        OAM_SYNC_FUNCTION_MANIFEST[
            f"{coordinate[0]}({coordinate[1].replace(', ', ',')})"
        ][3],
        OAM_SYNC_FUNCTION_MANIFEST[
            f"{coordinate[0]}({coordinate[1].replace(', ', ',')})"
        ][4],
    )
    for coordinate in OAM_SYNC_RUNTIME_FUNCTIONS
}
OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256 = {
    coordinate: OAM_SYNC_FUNCTION_MANIFEST[
        f"{coordinate[0]}({coordinate[1].replace(', ', ',')})"
    ][6]
    for coordinate in OAM_SYNC_RUNTIME_FUNCTIONS
}
RUNTIME_EXECUTE_FUNCTIONS = {
    ("rsc_canonical_reconciliation_json_0026", "jsonb"): (
        "i",
        False,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_reconciliation_event_key_0026", "text, text, text"): (
        "i",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_opening_reconciliation_source_0026", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_opening_reconciliation_run_0026", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_opening_reconciliation_files_0026", "uuid, uuid[]"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_formal_principal_graph_0026", "text[]"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_opening_control_import_0027", "uuid, uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_lock_opening_stocktake_start_reference_0027",
        "uuid, uuid[], uuid[], uuid[], timestamp with time zone",
    ): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_opening_stocktake_task_evidence_0027", "uuid, uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_lock_inventory_reference_graph_0027",
        "uuid[], timestamp with time zone",
    ): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_inventory_serial_graph_0027", "uuid[]"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_lock_opening_terminal_reference_union_0028",
        "uuid[], uuid[], uuid[], uuid[], uuid[]",
    ): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_nonopening_stocktake_review_graph_0032", "uuid, uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_nonopening_stocktake_posting_graph_0035", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_nonopening_stocktake_close_graph_0038", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_lock_nonopening_stocktake_difference_replay_graph_0057",
        "uuid, uuid, text",
    ): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_lock_nonopening_stocktake_count_history_graph_0062",
        "uuid, uuid, text",
    ): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_stocktake_finalizer_organization_0064", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_material_request_work_order_reference_0042", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
}
RUNTIME_FUNCTION_SHAPES = {
    coordinate: (
        "f",
        "text" if coordinate[0] in {
            "rsc_canonical_reconciliation_json_0026",
            "rsc_reconciliation_event_key_0026",
        } else "void",
        coordinate[0] in {
            "rsc_canonical_reconciliation_json_0026",
            "rsc_reconciliation_event_key_0026",
        },
    )
    for coordinate in RUNTIME_EXECUTE_FUNCTIONS
}
RUNTIME_FUNCTION_BODY_SHA256 = {
    ("rsc_canonical_reconciliation_json_0026", "jsonb"):
        "35a956052a13a94d1c6b57f252273f46fa806b2e9c596531205a148114b7dc53",
    ("rsc_reconciliation_event_key_0026", "text, text, text"):
        "9ec2f326f040fa1cd223e570b83ae6d6ff3dad7eb81f56e55e3342b45d9e5446",
    ("rsc_lock_opening_reconciliation_source_0026", "uuid"):
        "6b83d273493cc9a264108eade1d3245dd0775194f4583903a453ddb442746d8b",
    ("rsc_lock_opening_reconciliation_run_0026", "uuid"):
        "655628a9fd8c27007a6697285f125d4c50f935c6ad6c8600f99e235dd753a028",
    ("rsc_lock_opening_reconciliation_files_0026", "uuid, uuid[]"):
        "fb97101626b1f5e67717f6f1f057e12c859d23e086f53ba50398545bbc036fac",
    ("rsc_lock_formal_principal_graph_0026", "text[]"):
        "4b7d3e47541a1de999f33af65d95a697ffd44cfea8930f6fbeb265d4fd727aaf",
    ("rsc_lock_opening_control_import_0027", "uuid, uuid"):
        "de1a1ada12225d2c38d695448a475ebf16f404fdefb7a438a1f8fb8f50d996d1",
    (
        "rsc_lock_opening_stocktake_start_reference_0027",
        "uuid, uuid[], uuid[], uuid[], timestamp with time zone",
    ): "dbd0d5a71b839a4c31a8defc7089177c2d4c37409cd323710b302f6b1ba1a2e9",
    ("rsc_lock_opening_stocktake_task_evidence_0027", "uuid, uuid"):
        "cd166490b4e7124cd5b852368902bacbce883678a380637218cda3a2c77ba866",
    (
        "rsc_lock_inventory_reference_graph_0027",
        "uuid[], timestamp with time zone",
    ): "a7491fb40e05a7827cd1b73c4b6cfee07020b71d612098ece6a196cd44fc0de0",
    ("rsc_lock_inventory_serial_graph_0027", "uuid[]"):
        "75f4ab5039567650ad4867f29fa6b32c2c61e22782b65c5ec327e1a55b1bfea1",
    (
        "rsc_lock_opening_terminal_reference_union_0028",
        "uuid[], uuid[], uuid[], uuid[], uuid[]",
    ): "a5445f4651364a179223695d28ba7ce9434f0f20307098a9291067b64b07d066",
    ("rsc_lock_nonopening_stocktake_review_graph_0032", "uuid, uuid"):
        "ce7dda6f207c9a17bfde049749aa6e689e3f84d9e2c3892c66f17ac15c411ee3",
    ("rsc_lock_nonopening_stocktake_posting_graph_0035", "uuid"):
        "ebf8f6e2a7eecfcc6dca09a8df90f732977382d0ed9d973204ab14b14e4e9dd2",
    ("rsc_lock_nonopening_stocktake_close_graph_0038", "uuid"):
        "45c71e5a7129800399e1c20480bf7e2a000c478743635cff99db024c0b62118e",
    (
        "rsc_lock_nonopening_stocktake_difference_replay_graph_0057",
        "uuid, uuid, text",
    ): "7771bc7f9b59465fb47426eaabbff79deeb92967c0c77bbed79c0fe01585596c",
    (
        "rsc_lock_nonopening_stocktake_count_history_graph_0062",
        "uuid, uuid, text",
    ): "53fbad62f31d9fa93103bb376ba67f0da54ff1ac937b9d6e49bc6e752580393a",
    ("rsc_lock_stocktake_finalizer_organization_0064", "uuid"):
        "e97ad36d80cbeafa5ea97290b8ecd131213c2f2f965178f77f99977b12051502",
    ("rsc_lock_material_request_work_order_reference_0042", "uuid"):
        "d889b397912e98e1b9c2ec1de03ada750f42df01803c87239b9d04a624221982",
}
FORMAL_FILE_INTERNAL_FUNCTIONS = {
    (
        "rsc_stocktake_actor_assignment_valid_0011",
        "text, uuid, uuid, bigint, timestamp with time zone, text, text, text",
    ): (
        "s",
        False,
        "sql",
        (),
    ),
    ("rsc_require_stocktake_difference_completion_0016", ""): (
        "v",
        False,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_block_stocktake_review_fact_mutation_0016", ""): (
        "v",
        False,
        "plpgsql",
        (),
    ),
    ("rsc_validate_stocktake_observation_disposition_0016", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_recount_scope_assignment_0018", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_stocktake_round_assignment_valid_0021",
        (
            "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, text, "
            "timestamp with time zone, boolean"
        ),
    ): (
        "s",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_count_line_insert_0021", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_observation_insert_0021", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_scope_completion_insert_0021", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_opening_terminal_graph_complete_0022", "uuid, uuid"): (
        "s",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_opening_terminal_graph_0022", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_opening_observation_account_0023", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_opening_start_graph_complete_0052", "uuid, boolean"): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_opening_round_submission_complete_0052",
        "uuid, uuid, boolean",
    ): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_opening_scope_count_complete_0052",
        "uuid, uuid, uuid, boolean",
    ): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_opening_review_complete_0052", "uuid, boolean"): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_opening_recount_complete_0052", "uuid, boolean"): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_opening_observation_disposition_complete_0052",
        "uuid, boolean",
    ): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    (
        "rsc_opening_terminal_side_effects_complete_0052",
        "uuid, boolean",
    ): (
        "v",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_opening_task_insert_graph_0052", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_opening_count_write_current_0052", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_opening_live_graph_0052", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_scope_region_owner_0025", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_reconciliation_effect_0026", ""): (
        "v",
        False,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    (STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031, ""): (
        "v",
        False,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_stocktake_recount_scope_graph_valid_0032", "uuid"): (
        "s",
        False,
        "sql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_nonopening_stocktake_review_graph_0032", ""): (
        "v",
        False,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_recount_case_0032", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_recount_task_advance_0032", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_stocktake_recount_round_0032", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_stocktake_recount_graph_0032", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_formal_file_object_0036", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_formal_file_binding_0036", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_material_request_cancellation_0037", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_material_request_cancellation_graph_0037", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_material_request_cancellation_fact_0037", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_material_request_cancel_action_0037", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_lock_material_request_parent_write_0037", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_require_nonopening_stocktake_close_graph_0038", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_reject_nonopening_stocktake_close_mutation_0038", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_record_nonopening_stocktake_close_ack_0038", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_nonopening_stocktake_close_ack_0038", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_nonopening_stocktake_close_event_0038", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_reject_kms_data_key_pin_mutation_0040", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_sms_challenge_dispatch_0041", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_stocktake_evidence_seal_0057", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_nonopening_control_snapshot_0057", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_guard_stocktake_start_completion_0047", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_nonopening_stocktake_start_causality_0047", "uuid"): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_dispatch_nonopening_stocktake_start_causality_0047", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
    ("rsc_validate_nonopening_stocktake_review_version_0063", ""): (
        "v",
        True,
        "plpgsql",
        ("search_path=pg_catalog, public",),
    ),
}
FORMAL_FILE_INTERNAL_FUNCTION_SHAPES = {
    coordinate: (
        "f",
        "boolean"
        if coordinate in {
            (
                "rsc_stocktake_actor_assignment_valid_0011",
                (
                    "text, uuid, uuid, bigint, timestamp with time zone, "
                    "text, text, text"
                ),
            ),
            (
                "rsc_stocktake_round_assignment_valid_0021",
                (
                    "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, "
                    "text, timestamp with time zone, boolean"
                ),
            ),
            ("rsc_stocktake_recount_scope_graph_valid_0032", "uuid"),
            ("rsc_opening_terminal_graph_complete_0022", "uuid, uuid"),
            ("rsc_opening_start_graph_complete_0052", "uuid, boolean"),
            (
                "rsc_opening_round_submission_complete_0052",
                "uuid, uuid, boolean",
            ),
            (
                "rsc_opening_scope_count_complete_0052",
                "uuid, uuid, uuid, boolean",
            ),
            ("rsc_opening_review_complete_0052", "uuid, boolean"),
            ("rsc_opening_recount_complete_0052", "uuid, boolean"),
            (
                "rsc_opening_observation_disposition_complete_0052",
                "uuid, boolean",
            ),
            (
                "rsc_opening_terminal_side_effects_complete_0052",
                "uuid, boolean",
            ),
        }
        else "void"
        if coordinate in {
            ("rsc_validate_material_request_cancellation_0037", "uuid"),
            (
                "rsc_validate_nonopening_stocktake_start_causality_0047",
                "uuid",
            ),
        }
        else "trigger",
        False,
    )
    for coordinate in FORMAL_FILE_INTERNAL_FUNCTIONS
}
FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256 = {
    (
        "rsc_stocktake_actor_assignment_valid_0011",
        "text, uuid, uuid, bigint, timestamp with time zone, text, text, text",
    ): "09720a289e550a66f2ea400fdb0541d1646916d661538af0d2706f9fe5c326d1",
    ("rsc_require_stocktake_difference_completion_0016", ""):
        "13dbc1a56efe49c1cd7065b65374c05f824f779a7e7d5c7f1434dd5047e973ac",
    ("rsc_block_stocktake_review_fact_mutation_0016", ""):
        "abb2ae9087445ec056fdd8c7e3b5dae2a598af6f0fc1e61d469646e8aa2d1726",
    ("rsc_validate_stocktake_observation_disposition_0016", ""):
        "94cb47e8e75bd3eac0b448334992299c2cd3cd96f33798ab46e14e2dfe1f9c97",
    ("rsc_validate_stocktake_recount_scope_assignment_0018", ""):
        "2c4cd18b9b5dce1e4b0e0a9e2823dff1e8e16c08ddbcc77ba6f8d0c20e7c85d9",
    (
        "rsc_stocktake_round_assignment_valid_0021",
        (
            "uuid, uuid, uuid, text, uuid, uuid, bigint, text, text, text, "
            "timestamp with time zone, boolean"
        ),
    ): "d6c31c4284d2861a8eea3bc98a845e44d4013e454c81a9c591860cd015931708",
    ("rsc_validate_stocktake_count_line_insert_0021", ""):
        "319b1804e6fa6af3c7d3510524755d4b4d74b556a90640f6adfa6efa17f19693",
    ("rsc_validate_stocktake_observation_insert_0021", ""):
        "06cf2fafa1d90f120fe4bba21cc1dc55dba70bd63f649671b4333a6159af06bb",
    ("rsc_validate_stocktake_scope_completion_insert_0021", ""):
        "9fda4b71155e3bdb6802e2f09bf32b90284b0f0643f4d518ee3aa286bf9efdc9",
    ("rsc_opening_terminal_graph_complete_0022", "uuid, uuid"):
        "1eaf4e9bae4bac31f821ba1470d70059d9249d5459b7463e89683b03f0dc73d2",
    ("rsc_require_opening_terminal_graph_0022", ""):
        "4678c65493a2ca0c8d596343053977db4d93d7758e00f1291e30e6be038d36e8",
    ("rsc_require_opening_observation_account_0023", ""):
        "48cea5c4fdd20c4870f0eb963377d570f92c8a4c175590042b9ac00f2633ba60",
    ("rsc_opening_start_graph_complete_0052", "uuid, boolean"):
        "6db62f66efe1b87c556211e9392ab701cd2d8c6fa2c86150c1b95ee0d7f15833",
    (
        "rsc_opening_round_submission_complete_0052",
        "uuid, uuid, boolean",
    ): "d57abd63b6be3b13ac0c19786f76fcc6e7eeaf4ec4dc9bd84d45fbe2452ff206",
    (
        "rsc_opening_scope_count_complete_0052",
        "uuid, uuid, uuid, boolean",
    ): "e0c2628cdf871c1b4e7adb8234fdce6ef1dd3ee9dc80930b8cb71684f670b1e7",
    ("rsc_opening_review_complete_0052", "uuid, boolean"):
        "f6b614e42e35da34e072cd12ba103688163a53fc763d3c48979883813369126a",
    ("rsc_opening_recount_complete_0052", "uuid, boolean"):
        "b3a26276b6b46f8e7bcbbfbd5a33f9e8171be582f471ed330dba69ea85ebcd0f",
    (
        "rsc_opening_observation_disposition_complete_0052",
        "uuid, boolean",
    ): "4a5006029f95b5425704ba2b994c0ee2ff242c74e4e5c1660d368a400ffe5b0b",
    (
        "rsc_opening_terminal_side_effects_complete_0052",
        "uuid, boolean",
    ): "d816a65969b6112edb19a6d817918eded8d470f0e2ccb3087fabd9f55dac426d",
    ("rsc_require_opening_task_insert_graph_0052", ""):
        "9241a81d1261fe3e3a81b81e631893b9e1b4f82112379338e09eedbb5f6e1668",
    ("rsc_require_opening_count_write_current_0052", ""):
        "1eba1b857922dbe1b60a9e7b3c98c5b2250162057262c8849a504441bebeb537",
    ("rsc_require_opening_live_graph_0052", ""):
        "4fd3e6f9dd5ab04b21d86b9c6575171c1de92a2a54f7391ecd226ac09b4d0564",
    ("rsc_validate_stocktake_scope_region_owner_0025", ""):
        "904a443c2c5930356af0f15f444f29ec6b6ce61f32294e4b2e3b40dd3a0e4e8e",
    ("rsc_guard_reconciliation_effect_0026", ""):
        "6e7e845ac518f378139b6f79218da0f08a55f9398b63429686af47f8f10024fe",
    (STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031, ""):
        "ead5a0a72c25cd326a1d036dddd384bb583b66141512f568b8929ab31b4773a9",
    ("rsc_stocktake_recount_scope_graph_valid_0032", "uuid"):
        "a1308f871cb0c7ba6fb0584dc28520b0ea4361d956d3c3d30a758f8ffafa1619",
    ("rsc_require_nonopening_stocktake_review_graph_0032", ""):
        "17903808923c509cb2695f818152c598908c6fb12077abf8b398a52191caf58e",
    ("rsc_validate_stocktake_recount_case_0032", ""):
        "65db070bee60e91203b32a6afe56092992ae7d1984ec92d6490faabc6930e211",
    ("rsc_validate_stocktake_recount_task_advance_0032", ""):
        "0d6b43d9069eb641a89197c37ae4d1f806eb401b4d9d3719d10843dbb50d4b5f",
    ("rsc_validate_stocktake_recount_round_0032", ""):
        "271d01c965cc4cc939d183afdcae7f52de4c22f193d9317b2938408e3c8b3904",
    ("rsc_require_stocktake_recount_graph_0032", ""):
        "294e748d5020b37057851154ecfed2ee66a85fde62bc9d0e4f8cae08aa7be1a2",
    ("rsc_guard_formal_file_object_0036", ""):
        "76401f3c71eacdbac4c4b4d07e5a49f1159cc2cea878933df36e817c9a85e39f",
    ("rsc_guard_formal_file_binding_0036", ""):
        "9ad4d0b6ee9d9a4bab3cd460ca7553adb9df6c8e0040b97480019ca103696a38",
    ("rsc_validate_material_request_cancellation_0037", "uuid"):
        "64d7775a5013859d91f0ea6ec938f17a4bbfa4dd0d105069d35b6dc4db9c8617",
    ("rsc_require_material_request_cancellation_graph_0037", ""):
        "98c206ba9bca0a30a78420dc1e7f283f1c141082930f8b241cfcae19a21c8e36",
    ("rsc_guard_material_request_cancellation_fact_0037", ""):
        "cf22a8d853d9271bb8ef64a19d078e5f3c79aa2a255a302dcc17cc857f5040ed",
    ("rsc_guard_material_request_cancel_action_0037", ""):
        "32737f80f3404b132cba236d1f51a5dfc72a5b42fc2b3c15933c3da13c21b11d",
    ("rsc_lock_material_request_parent_write_0037", ""):
        "35566cd293d7fb29d35c1a452e7bc26529af9ab1fff90afaaf06f60272848dcf",
    ("rsc_require_nonopening_stocktake_close_graph_0038", ""):
        "bcb713005c869c909ec212ae6775f4894cc67bb339276cdbe88242601352f582",
    ("rsc_reject_nonopening_stocktake_close_mutation_0038", ""):
        "7f70f843d60fe72ad54970b12025cb4aa070c1062e5990ef805fa48d575bd593",
    ("rsc_record_nonopening_stocktake_close_ack_0038", ""):
        "916552fdb20506bf46dcaaf7909e9730de48f7ef08c46aa647b24425b5214559",
    ("rsc_guard_nonopening_stocktake_close_ack_0038", ""):
        "2e344060231cf933822dcb02677257b78584f8940cbc88f11264cd61b9a52782",
    ("rsc_guard_nonopening_stocktake_close_event_0038", ""):
        "acf4f3fe8a070ebf860b73de031d23a5f09a644a7a483af12316e69217bd9982",
    ("rsc_reject_kms_data_key_pin_mutation_0040", ""):
        "17588eaffe3b5225272a5b9c342088ff0d934c7492625db18748217cf15feabf",
    ("rsc_guard_sms_challenge_dispatch_0041", ""):
        "42201b13bb8998ea8522b190bfed67bbc7faab4c7bc355b4a7f5c2c13cd59993",
    ("rsc_guard_stocktake_evidence_seal_0057", ""):
        "7a5ae0c9fd3117cb784dce34c7882b8301759e846321ac7a6233235c6333222c",
    ("rsc_guard_nonopening_control_snapshot_0057", ""):
        "40d7a6223b6250316f7bed15c0dd4749238066865f7581156646c5b89b509249",
    ("rsc_guard_stocktake_start_completion_0047", ""):
        "19fcb84567c36bbcf426eac4f844bfa70e32ee3247f50c402a444171c5879718",
    ("rsc_validate_nonopening_stocktake_start_causality_0047", "uuid"):
        "82537a53254a6493eea33f07795b8b47d941ecc6bd25ed8dcab8c35bcf18d3f8",
    ("rsc_dispatch_nonopening_stocktake_start_causality_0047", ""):
        "7ae3cda26d356cabf5529bae88eb33e0f85bcab538e8ab9b8bb256eebf54ac40",
    ("rsc_validate_nonopening_stocktake_review_version_0063", ""):
        "e7b996ad8f85b6ffc099bc88adb622dc71e56fc239a737ff8475b980b99e4744",
}


_ROLE_EVIDENCE_SQL = text(
    """
SELECT
    current_user AS role_name,
    session_user AS session_role_name,
    role_row.rolsuper AS is_superuser,
    role_row.rolcreatedb AS can_create_database,
    role_row.rolcreaterole AS can_create_role,
    role_row.rolreplication AS can_replicate,
    role_row.rolbypassrls AS can_bypass_rls,
    has_database_privilege(current_user, current_database(), 'CREATE')
        AS can_create_in_database,
    has_database_privilege(current_user, current_database(), 'TEMPORARY')
        AS can_create_temporary_tables,
    EXISTS (
        SELECT 1
          FROM pg_database AS database_acl_row
          CROSS JOIN LATERAL aclexplode(database_acl_row.datacl)
            AS database_acl
         WHERE database_acl_row.datname = current_database()
           AND database_acl.grantee IN (0, role_row.oid)
           AND database_acl.is_grantable
    ) AS has_database_grant_option,
    (SELECT pg_get_userbyid(database_row.datdba)
       FROM pg_database AS database_row
      WHERE database_row.datname = current_database()) AS database_owner,
    has_schema_privilege(current_user, 'public', 'USAGE')
        AS can_use_schema,
    has_schema_privilege(current_user, 'public', 'CREATE')
        AS can_create_in_schema,
    EXISTS (
        SELECT 1
          FROM pg_namespace AS schema_acl_row
          CROSS JOIN LATERAL aclexplode(schema_acl_row.nspacl) AS schema_acl
         WHERE schema_acl_row.nspname = 'public'
           AND schema_acl.grantee IN (0, role_row.oid)
           AND schema_acl.is_grantable
    ) AS has_schema_grant_option,
    current_schema() AS current_schema_name,
    current_schemas(FALSE) AS current_schema_path,
    EXISTS (
        SELECT 1
          FROM pg_namespace AS candidate_schema
         WHERE pg_get_userbyid(candidate_schema.nspowner) = current_user
            OR has_schema_privilege(
                current_user, candidate_schema.oid, 'CREATE'
            )
            OR (
                candidate_schema.nspname NOT LIKE 'pg_%'
                AND candidate_schema.nspname <> 'information_schema'
                AND candidate_schema.nspname <> 'public'
                AND has_schema_privilege(
                    current_user, candidate_schema.oid, 'USAGE'
                )
           )
    ) AS has_non_system_schema_control,
    (SELECT pg_get_userbyid(namespace_row.nspowner)
       FROM pg_namespace AS namespace_row
      WHERE namespace_row.nspname = 'public') AS schema_owner,
    (SELECT pg_get_userbyid(class_row.relowner)
       FROM pg_class AS class_row
       JOIN pg_namespace AS namespace_row
         ON namespace_row.oid = class_row.relnamespace
      WHERE namespace_row.nspname = 'public'
        AND class_row.relname = 'audit_events') AS audit_events_owner,
    (SELECT pg_get_userbyid(class_row.relowner)
       FROM pg_class AS class_row
       JOIN pg_namespace AS namespace_row
         ON namespace_row.oid = class_row.relnamespace
      WHERE namespace_row.nspname = 'public'
        AND class_row.relname = 'audit_chain_heads') AS audit_heads_owner,
    EXISTS (
        SELECT 1 FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ) AS migration_role_exists,
    EXISTS (
        SELECT 1 FROM pg_auth_members AS membership
         WHERE membership.member = role_row.oid
    ) AS has_any_role_membership,
    EXISTS (
        SELECT 1 FROM pg_auth_members AS membership
         WHERE membership.roleid = role_row.oid
    ) AS has_any_role_members,
    COALESCE((
        SELECT migration_role.rolsuper
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_is_superuser,
    COALESCE((
        SELECT migration_role.rolcreatedb
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_create_database,
    COALESCE((
        SELECT migration_role.rolcreaterole
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_create_role,
    COALESCE((
        SELECT migration_role.rolreplication
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_replicate,
    COALESCE((
        SELECT migration_role.rolbypassrls
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_can_bypass_rls,
    COALESCE((
        SELECT EXISTS (
            SELECT 1 FROM pg_auth_members AS migration_membership
             WHERE migration_membership.member = migration_role.oid
        )
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_has_any_membership,
    COALESCE((
        SELECT EXISTS (
            SELECT 1 FROM pg_auth_members AS migration_members
             WHERE migration_members.roleid = migration_role.oid
        )
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS migration_role_has_any_members,
    COALESCE((
        SELECT pg_has_role(current_user, migration_role.oid, 'MEMBER')
          FROM pg_roles AS migration_role
         WHERE migration_role.rolname = :migration_role
    ), TRUE) AS is_migration_role_member,
    has_parameter_privilege(
        current_user, 'session_replication_role', 'SET'
    ) AS can_disable_replication_guards,
    current_setting('session_replication_role') AS session_replication_role,
    has_table_privilege(current_user, 'public.audit_events', 'SELECT')
        AS audit_can_select,
    has_table_privilege(current_user, 'public.audit_events', 'INSERT')
        AS audit_can_insert,
    has_table_privilege(current_user, 'public.audit_events', 'UPDATE')
        AS audit_can_update,
    has_table_privilege(current_user, 'public.audit_events', 'DELETE')
        AS audit_can_delete,
    has_table_privilege(current_user, 'public.audit_events', 'TRUNCATE')
        AS audit_can_truncate,
    has_table_privilege(current_user, 'public.audit_events', 'TRIGGER')
        AS audit_can_control_trigger,
    has_table_privilege(current_user, 'public.audit_chain_heads', 'SELECT')
        AS heads_can_select,
    has_table_privilege(current_user, 'public.audit_chain_heads', 'UPDATE')
        AS heads_can_update,
    has_table_privilege(current_user, 'public.audit_chain_heads', 'INSERT')
        AS heads_can_insert,
    has_table_privilege(current_user, 'public.audit_chain_heads', 'DELETE')
        AS heads_can_delete,
    has_table_privilege(current_user, 'public.audit_chain_heads', 'TRUNCATE')
        AS heads_can_truncate,
    has_table_privilege(current_user, 'public.audit_chain_heads', 'TRIGGER')
        AS heads_can_control_trigger,
    has_table_privilege(current_user, 'public.alembic_version', 'SELECT')
        AS alembic_can_select,
    has_table_privilege(current_user, 'public.alembic_version', 'INSERT')
        AS alembic_can_insert,
    has_table_privilege(current_user, 'public.alembic_version', 'UPDATE')
        AS alembic_can_update,
    has_table_privilege(current_user, 'public.alembic_version', 'DELETE')
        AS alembic_can_delete
FROM pg_roles AS role_row
WHERE role_row.rolname = current_user
"""
)

_TABLE_ACL_SQL = text(
    """
SELECT
    class_row.relname AS table_name,
    pg_get_userbyid(class_row.relowner) AS owner_name,
    has_table_privilege(current_user, class_row.oid, 'SELECT') AS can_select,
    has_table_privilege(current_user, class_row.oid, 'INSERT') AS can_insert,
    has_table_privilege(current_user, class_row.oid, 'UPDATE') AS can_update,
    has_table_privilege(current_user, class_row.oid, 'DELETE') AS can_delete,
    has_table_privilege(current_user, class_row.oid, 'TRUNCATE') AS can_truncate,
    has_table_privilege(current_user, class_row.oid, 'REFERENCES') AS can_reference,
    has_table_privilege(current_user, class_row.oid, 'TRIGGER') AS can_trigger
    ,EXISTS (
        SELECT 1
          FROM pg_attribute AS attribute_row
          CROSS JOIN LATERAL aclexplode(attribute_row.attacl) AS column_acl
         WHERE attribute_row.attrelid = class_row.oid
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
           AND column_acl.grantee IN (0, role_row.oid)
    ) AS has_explicit_runtime_column_acl
    ,EXISTS (
        SELECT 1
          FROM aclexplode(class_row.relacl) AS table_acl
         WHERE table_acl.grantee = 0
    ) AS has_public_table_acl
    ,EXISTS (
        SELECT 1
          FROM aclexplode(class_row.relacl) AS table_acl
         WHERE table_acl.grantee = role_row.oid
           AND table_acl.is_grantable
    ) AS has_runtime_grant_option
FROM pg_class AS class_row
JOIN pg_namespace AS namespace_row
  ON namespace_row.oid = class_row.relnamespace
JOIN pg_roles AS role_row
  ON role_row.rolname = current_user
WHERE namespace_row.nspname = 'public'
  AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
ORDER BY class_row.relname
"""
)

_COLUMN_ACL_SQL = text(
    """
SELECT
    class_row.relname AS table_name,
    attribute_row.attname AS column_name,
    CASE
        WHEN column_acl.grantee = 0 THEN 'PUBLIC'
        ELSE pg_get_userbyid(column_acl.grantee)
    END AS grantee_name,
    upper(column_acl.privilege_type) AS privilege_type,
    column_acl.is_grantable AS is_grantable
FROM pg_catalog.pg_class AS class_row
JOIN pg_catalog.pg_namespace AS namespace_row
  ON namespace_row.oid = class_row.relnamespace
JOIN pg_catalog.pg_attribute AS attribute_row
  ON attribute_row.attrelid = class_row.oid
JOIN pg_catalog.pg_roles AS role_row
  ON role_row.rolname = current_user
CROSS JOIN LATERAL pg_catalog.aclexplode(attribute_row.attacl) AS column_acl
WHERE namespace_row.nspname = 'public'
  AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
  AND column_acl.grantee IN (0, role_row.oid)
ORDER BY class_row.relname, attribute_row.attnum, column_acl.grantee,
         column_acl.privilege_type
"""
)

_SEQUENCE_ACL_SQL = text(
    """
SELECT
    class_row.relname AS sequence_name,
    pg_get_userbyid(class_row.relowner) AS owner_name,
    has_sequence_privilege(current_user, class_row.oid, 'USAGE') AS can_use,
    has_sequence_privilege(current_user, class_row.oid, 'SELECT') AS can_select,
    has_sequence_privilege(current_user, class_row.oid, 'UPDATE') AS can_update
FROM pg_class AS class_row
JOIN pg_namespace AS namespace_row
  ON namespace_row.oid = class_row.relnamespace
WHERE namespace_row.nspname = 'public'
  AND class_row.relkind = 'S'
ORDER BY class_row.relname
"""
)

_FUNCTION_ACL_SQL = text(
    """
SELECT
    function_row.oid AS function_id,
    function_row.proname AS function_name,
    oidvectortypes(function_row.proargtypes) AS argument_types,
    function_row.prokind AS function_kind,
    format_type(function_row.prorettype, NULL) AS result_type,
    function_row.proretset AS returns_set,
    function_row.provariadic AS variadic_type,
    function_row.proargmodes AS argument_modes,
    function_row.pronargdefaults AS argument_default_count,
    function_row.proisstrict AS is_strict,
    function_row.prosrc AS source_body,
    function_row.provolatile AS volatility,
    function_row.proparallel AS parallel_safety,
    function_row.proleakproof AS is_leakproof,
    function_row.prosecdef AS is_security_definer,
    language_row.lanname AS language_name,
    function_row.proconfig AS configuration,
    pg_get_userbyid(function_row.proowner) AS owner_name,
    has_function_privilege(current_user, function_row.oid, 'EXECUTE')
        AS can_execute,
    EXISTS (
        SELECT 1
          FROM aclexplode(
                   coalesce(
                       function_row.proacl,
                       acldefault('f', function_row.proowner)
                   )
               ) AS function_acl
          JOIN pg_roles AS runtime_role
            ON runtime_role.oid = function_acl.grantee
         WHERE runtime_role.rolname = current_user
           AND function_acl.privilege_type = 'EXECUTE'
           AND function_acl.is_grantable
    ) AS api_execute_is_grantable,
    (
        SELECT count(*)
          FROM aclexplode(
                   coalesce(
                       function_row.proacl,
                       acldefault('f', function_row.proowner)
                   )
               ) AS function_acl
         WHERE function_acl.privilege_type = 'EXECUTE'
           AND function_acl.grantee <> function_row.proowner
           AND function_acl.grantee <> 0
           AND NOT EXISTS (
                SELECT 1
                  FROM pg_roles AS runtime_role
                 WHERE runtime_role.oid = function_acl.grantee
                   AND runtime_role.rolname = current_user
           )
           AND NOT (
                (
                    (
                        function_row.proname = 'rsc_oam_rls_check_0044'
                        AND oidvectortypes(function_row.proargtypes)
                            = 'text, text, jsonb'
                    )
                    OR (
                        function_row.proname =
                            'rsc_oam_runtime_binding_ready_0044'
                        AND oidvectortypes(function_row.proargtypes) = ''
                    )
                    OR (
                        function_row.proname = 'rsc_oam_receipt_rls_check_0082'
                        AND oidvectortypes(function_row.proargtypes) = 'text, text, text, jsonb'
                    )
                )
                AND function_acl.is_grantable IS FALSE
                AND EXISTS (
                    SELECT 1
                      FROM pg_roles AS oam_runtime_role
                     WHERE oam_runtime_role.oid = function_acl.grantee
                       AND oam_runtime_role.rolname IN (
                           'edge_inbox', 'star_oam_projector'
                       )
                       AND (oam_runtime_role.rolname <> 'edge_inbox'
                            OR function_row.proname <> 'rsc_oam_receipt_rls_check_0082')
                )
           )
    ) AS unexpected_execute_grantee_count,
    EXISTS (
        SELECT 1
          FROM aclexplode(
                   coalesce(
                       function_row.proacl,
                       acldefault('f', function_row.proowner)
                   )
               ) AS function_acl
         WHERE function_acl.grantee = 0
           AND function_acl.privilege_type = 'EXECUTE'
    ) AS public_can_execute,
    coalesce((
        SELECT has_function_privilege(
                   role_row.oid, function_row.oid, 'EXECUTE'
               )
          FROM pg_roles AS role_row
         WHERE role_row.rolname = 'star_oam_edge'
    ), false) AS edge_can_execute,
    coalesce((
        SELECT has_function_privilege(
                   role_row.oid, function_row.oid, 'EXECUTE'
               )
          FROM pg_roles AS role_row
         WHERE role_row.rolname = 'star_oam_backup'
    ), false) AS backup_can_execute,
    coalesce((
        SELECT has_function_privilege(
                   role_row.oid, function_row.oid, 'EXECUTE'
               )
          FROM pg_roles AS role_row
         WHERE role_row.rolname = 'edge_inbox'
    ), false) AS edge_receiver_can_execute,
    coalesce((
        SELECT has_function_privilege(
                   role_row.oid, function_row.oid, 'EXECUTE'
               )
          FROM pg_roles AS role_row
         WHERE role_row.rolname = 'star_oam_projector'
    ), false) AS projector_can_execute
FROM pg_proc AS function_row
JOIN pg_namespace AS namespace_row
  ON namespace_row.oid = function_row.pronamespace
JOIN pg_language AS language_row
  ON language_row.oid = function_row.prolang
WHERE namespace_row.nspname = 'public'
ORDER BY function_row.oid
"""
)

_AUDIT_TRIGGER_SQL = text(
    """
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row
  ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema
  ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row
  ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND table_row.relname IN ('audit_events', 'audit_chain_heads')
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_AUDIT_STREAM_COLUMN_SQL = text(
    """
SELECT
    attribute_row.attname AS column_name,
    attribute_row.attnotnull AS is_not_null,
    format_type(attribute_row.atttypid, attribute_row.atttypmod) AS data_type
FROM pg_attribute AS attribute_row
JOIN pg_class AS table_row
  ON table_row.oid = attribute_row.attrelid
JOIN pg_namespace AS schema_row
  ON schema_row.oid = table_row.relnamespace
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'audit_events'
  AND attribute_row.attname IN ('stream_key', 'stream_version')
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
ORDER BY attribute_row.attname
"""
)

_AUDIT_STREAM_CONSTRAINT_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    referenced_table.relname AS referenced_table,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.confkey) WITH ORDINALITY AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.confrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS referenced_columns,
    constraint_row.confupdtype AS update_action,
    constraint_row.confdeltype AS delete_action
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row
  ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row
  ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS referenced_table
  ON referenced_table.oid = constraint_row.confrelid
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'audit_events'
  AND constraint_row.conname IN (
      'ck_audit_events_stream_key_0017',
      'ck_audit_events_stream_version_0017',
      'uq_audit_events_stream_version_0017',
      'fk_audit_events_stream_key_0017'
  )
ORDER BY constraint_row.conname
"""
)

_STOCKTAKE_RECOUNT_COLUMN_SQL = text(
    """
SELECT
    table_row.relname AS table_name,
    table_row.relkind AS table_kind,
    attribute_row.attname AS column_name,
    attribute_row.attnotnull AS is_not_null,
    format_type(attribute_row.atttypid, attribute_row.atttypmod) AS data_type
FROM pg_attribute AS attribute_row
JOIN pg_class AS table_row
  ON table_row.oid = attribute_row.attrelid
JOIN pg_namespace AS schema_row
  ON schema_row.oid = table_row.relnamespace
WHERE schema_row.nspname = 'public'
  AND table_row.relname IN (
      'stocktake_rounds',
      'stocktake_recount_cases',
      'stocktake_recount_scope_assignments'
  )
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
ORDER BY table_row.relname, attribute_row.attnum
"""
)

_STOCKTAKE_RECOUNT_CONSTRAINT_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    table_row.relname AS table_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
            AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    referenced_table.relname AS referenced_table,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.confkey) WITH ORDINALITY
            AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.confrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS referenced_columns,
    constraint_row.confupdtype AS update_action,
    constraint_row.confdeltype AS delete_action
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row
  ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row
  ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS referenced_table
  ON referenced_table.oid = constraint_row.confrelid
WHERE schema_row.nspname = 'public'
  AND table_row.relname IN (
      'stocktake_rounds',
      'stocktake_recount_cases',
      'stocktake_recount_scope_assignments'
  )
ORDER BY constraint_row.conname
"""
)

_STOCKTAKE_RECOUNT_INDEX_SQL = text(
    """
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(
            index_metadata.indexrelid,
            key_position,
            TRUE
        )
          FROM generate_series(
              1,
              index_metadata.indnkeyatts
          ) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row
  ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row
  ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row
  ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method
  ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND index_row.relname IN (
      'uq_stocktake_rounds_recount_case_0018',
      'uq_stocktake_rounds_one_counting_0018',
      'uq_stocktake_recount_cases_source_round_0018',
      'uq_stocktake_recount_cases_trigger_review_0018',
      'uq_stocktake_recount_cases_task_next_round_0018',
      'uq_stocktake_recount_cases_idempotency_0018',
      'uq_stocktake_recount_scope_assignments_case_scope_0018'
  )
ORDER BY index_row.relname
"""
)

_STOCKTAKE_RECOUNT_TRIGGER_NAME_LITERALS = ",\n      ".join(
    f"'{name}'"
    for name in sorted(EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS)
)
_STOCKTAKE_RECOUNT_TRIGGER_FUNCTION_NAME_LITERALS = ",\n      ".join(
    f"'{name}'"
    for name in sorted(
        {
            definition[1]
            for definition in EXPECTED_STOCKTAKE_RECOUNT_GRAPH_TRIGGERS.values()
        }
        | {
            "rsc_validate_stocktake_recount_scope_assignment_0018",
            "rsc_validate_stocktake_count_line_insert_0021",
            "rsc_validate_stocktake_observation_insert_0021",
            "rsc_validate_stocktake_scope_completion_insert_0021",
        }
    )
)
_STOCKTAKE_SENSITIVE_TRIGGER_SQL = text(
    """
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row
  ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema
  ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row
  ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND table_row.relname IN (
      'stock_locations',
      'stocktake_control_snapshot_lines',
      'stocktake_count_lines',
      'stocktake_count_observations',
      'stocktake_scope_count_completions',
      'stocktake_recount_scope_assignments'
  )
  AND NOT trigger_row.tgisinternal
ORDER BY table_row.relname, trigger_row.tgname
"""
)
_STOCKTAKE_RECOUNT_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter,
    trigger_row.tgnargs AS argument_count
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row
  ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema
  ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row
  ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND (
      trigger_row.tgname IN (
          {_STOCKTAKE_RECOUNT_TRIGGER_NAME_LITERALS}
      )
      OR (
          function_row.proname IN (
              {_STOCKTAKE_RECOUNT_TRIGGER_FUNCTION_NAME_LITERALS}
          )
          AND (
              function_row.proname =
                  '{STOCKTAKE_DIFFERENCE_COMPLETION_FUNCTION_0031}'
              OR table_row.relname NOT IN (
                  'stock_locations',
                  'stocktake_count_lines',
                  'stocktake_count_observations',
                  'stocktake_scope_count_completions',
                  'stocktake_recount_scope_assignments'
              )
          )
      )
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_STOCKTAKE_SCOPE_TRIGGER_SQL = text(
    """
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row
  ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema
  ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row
  ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND table_row.relname = 'stocktake_scopes'
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_OPENING_TERMINAL_TRIGGER_NAME_LITERALS = ",\n      ".join(
    f"'{name}'" for name in sorted(EXPECTED_OPENING_TERMINAL_TRIGGERS)
)
_OPENING_TERMINAL_EXACT_CALLER_FUNCTION_LITERALS = ", ".join(
    f"'{name}'"
    for name in sorted(
        {
            definition[1]
            for definition in EXPECTED_OPENING_TERMINAL_TRIGGERS.values()
        }
    )
)

_RECONCILIATION_TRIGGER_NAME_LITERALS = ",\n      ".join(
    f"'{name}'" for name in sorted(EXPECTED_RECONCILIATION_TRIGGERS)
)
_RECONCILIATION_TRIGGER_SQL = text(
    """
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row
  ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema
  ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row
  ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND trigger_row.tgname LIKE '%reconciliation%0026'
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)
_RECONCILIATION_CONSTRAINT_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    table_row.relname AS table_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
            AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    referenced_table.relname AS referenced_table,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.confkey) WITH ORDINALITY
            AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.confrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS referenced_columns,
    constraint_row.confupdtype AS update_action,
    constraint_row.confdeltype AS delete_action
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS referenced_table
  ON referenced_table.oid = constraint_row.confrelid
WHERE schema_row.nspname = 'public'
  AND constraint_row.conname IN (
      'fk_reconciliation_commands_consumption',
      'fk_opening_control_reconciliation_runs_run',
      'fk_opening_control_reconciliation_runs_create_command',
      'fk_opening_control_reconciliation_items_item',
      'uq_reconciliation_commands_target',
      'uq_reconciliation_commands_id_run',
      'uq_opening_control_reconciliation_consumptions_command',
      'uq_opening_control_reconciliation_consumptions_target',
      'ck_reconciliation_commands_target_version',
      'ck_reconciliation_commands_operation',
      'ck_reconciliation_commands_request_reference',
      'ck_reconciliation_commands_hashes',
      'ck_opening_control_reconciliation_runs_manifest',
      'ck_opening_control_reconciliation_items_file_snapshot'
  )
ORDER BY constraint_row.conname
"""
)
_RECONCILIATION_PARTIAL_INDEX_SQL = text(
    """
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'reconciliation_commands'
  AND index_metadata.indpred IS NOT NULL
ORDER BY index_row.relname
"""
)
_OPENING_TERMINAL_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    table_schema.nspname AS table_schema,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row
  ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema
  ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row
  ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE (
      (
          table_schema.nspname = 'public'
          AND trigger_row.tgname IN (
              {_OPENING_TERMINAL_TRIGGER_NAME_LITERALS}
          )
      )
      OR (
          function_schema.nspname = 'public'
          AND function_row.proname IN (
              {_OPENING_TERMINAL_EXACT_CALLER_FUNCTION_LITERALS}
          )
          AND pg_catalog.oidvectortypes(function_row.proargtypes) = ''
      )
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_OPENING_TERMINAL_INDEX_SQL = text(
    f"""
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(
            index_metadata.indexrelid,
            key_position,
            TRUE
        )
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row
  ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row
  ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row
  ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method
  ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND index_row.relname = '{EXPECTED_OPENING_TERMINAL_INDEX}'
"""
)

_FORMAL_FILE_TRIGGER_NAME_LITERALS = ",\n      ".join(
    f"'{name}'" for name in sorted(EXPECTED_FORMAL_FILE_TRIGGERS)
)
_FORMAL_FILE_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND trigger_row.tgname IN ({_FORMAL_FILE_TRIGGER_NAME_LITERALS})
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_FORMAL_FILE_INDEX_NAME_LITERALS = ",\n      ".join(
    f"'{name}'" for name in sorted(EXPECTED_FORMAL_FILE_INDEXES)
)
_FORMAL_FILE_INDEX_SQL = text(
    f"""
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND index_row.relname IN ({_FORMAL_FILE_INDEX_NAME_LITERALS})
ORDER BY index_row.relname
"""
)

_MATERIAL_REQUEST_APPROVAL_TRIGGER_FUNCTION_LITERALS = ",\n      ".join(
    f"'{function_name}'"
    for function_name in sorted(
        {
            expected[1]
            for expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.values()
        }
    )
)
_MATERIAL_REQUEST_APPROVAL_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND (
      trigger_row.tgname ~ '_(0029|0030|0045|0046|0059|0060|0069|0070|0071|0072)$'
      OR function_row.proname IN (
          {_MATERIAL_REQUEST_APPROVAL_TRIGGER_FUNCTION_LITERALS}
      )
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_SQL = text(
    """
SELECT
    table_row.relname AS table_name,
    attribute_row.attname AS column_name,
    format_type(attribute_row.atttypid, attribute_row.atttypmod) AS data_type,
    attribute_row.attnotnull AS is_not_null,
    attribute_row.attidentity AS identity_kind,
    attribute_row.attgenerated AS generated_kind,
    pg_get_expr(default_row.adbin, default_row.adrelid, TRUE)
        AS default_expression,
    col_description(table_row.oid, attribute_row.attnum) AS comment
FROM pg_class AS table_row
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_attribute AS attribute_row ON attribute_row.attrelid = table_row.oid
LEFT JOIN pg_attrdef AS default_row
  ON default_row.adrelid = table_row.oid
 AND default_row.adnum = attribute_row.attnum
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'material_request_commands'
  AND attribute_row.attname = 'projection_manifest_sha256'
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
ORDER BY attribute_row.attnum
"""
)

_MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    table_row.relname AS table_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    constraint_row.connoinherit AS is_no_inherit,
    constraint_row.conislocal AS is_local,
    constraint_row.coninhcount AS inheritance_count,
    constraint_row.conparentid AS parent_constraint_id,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
               AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    index_row.relname AS backing_index_name
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS index_row ON index_row.oid = constraint_row.conindid
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'material_request_commands'
  AND constraint_row.conname =
      'ck_material_request_commands_projection_manifest_0046'
ORDER BY constraint_row.conname
"""
)

_MATERIAL_REQUEST_CANCELLATION_TRIGGER_FUNCTION_LITERALS = ",\n      ".join(
    f"'{function_name}'"
    for function_name in sorted(
        {
            function_name
            for _, function_name, _, _ in (
                EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS.values()
            )
        }
    )
)
_MATERIAL_REQUEST_CANCELLATION_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND function_row.proname IN (
      {_MATERIAL_REQUEST_CANCELLATION_TRIGGER_FUNCTION_LITERALS}
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_MATERIAL_REQUEST_CANCELLATION_INDEX_NAME_LITERALS = ",\n      ".join(
    f"'{name}'"
    for name in sorted(EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES)
)
_MATERIAL_REQUEST_CANCELLATION_INDEX_SQL = text(
    f"""
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND index_row.relname IN (
      {_MATERIAL_REQUEST_CANCELLATION_INDEX_NAME_LITERALS}
  )
ORDER BY index_row.relname
"""
)

_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX_SQL = text(
    f"""
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND index_row.relname = '{EXPECTED_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX["name"]}'
ORDER BY index_row.relname
"""
)

_KMS_DATA_KEY_PIN_TRIGGER_SQL = text(
    """
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND (
      table_row.relname = 'kms_data_key_pins'
      OR trigger_row.tgname LIKE '%0040'
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_KMS_DATA_KEY_PIN_COLUMN_SQL = text(
    """
SELECT
    table_row.relkind AS relation_kind,
    table_row.relpersistence AS persistence,
    table_row.relrowsecurity AS row_security,
    table_row.relforcerowsecurity AS force_row_security,
    attribute_row.attnum AS ordinal_position,
    attribute_row.attname AS column_name,
    format_type(attribute_row.atttypid, attribute_row.atttypmod) AS data_type,
    attribute_row.attnotnull AS is_not_null,
    attribute_row.attidentity AS identity_kind,
    attribute_row.attgenerated AS generated_kind,
    pg_get_expr(default_row.adbin, default_row.adrelid, TRUE)
        AS default_expression
FROM pg_class AS table_row
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_attribute AS attribute_row ON attribute_row.attrelid = table_row.oid
LEFT JOIN pg_attrdef AS default_row
  ON default_row.adrelid = table_row.oid
 AND default_row.adnum = attribute_row.attnum
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'kms_data_key_pins'
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
ORDER BY attribute_row.attnum
"""
)

_KMS_DATA_KEY_PIN_CONSTRAINT_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    constraint_row.connoinherit AS is_no_inherit,
    constraint_row.conislocal AS is_local,
    constraint_row.coninhcount AS inheritance_count,
    constraint_row.conparentid AS parent_constraint_id,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
               AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    index_row.relname AS backing_index_name
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS index_row ON index_row.oid = constraint_row.conindid
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'kms_data_key_pins'
ORDER BY constraint_row.conname
"""
)

_KMS_DATA_KEY_PIN_INDEX_SQL = text(
    """
SELECT
    index_row.relname AS index_name,
    pg_get_userbyid(index_row.relowner) AS owner_name,
    constraint_row.conname AS constraint_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisprimary AS is_primary,
    index_metadata.indisexclusion AS is_exclusion,
    index_metadata.indimmediate AS is_immediate,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    index_metadata.indnullsnotdistinct AS nulls_not_distinct,
    index_metadata.indnkeyatts AS key_attribute_count,
    index_metadata.indnatts AS total_attribute_count,
    index_metadata.indexprs IS NOT NULL AS has_expressions,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
LEFT JOIN pg_constraint AS constraint_row
  ON constraint_row.conindid = index_row.oid
 AND constraint_row.conrelid = table_row.oid
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'kms_data_key_pins'
ORDER BY index_row.relname
"""
)

_SMS_DISPATCH_TRIGGER_SQL = text(
    """
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND (
      table_row.relname = 'sms_challenge_dispatches'
      OR trigger_row.tgname LIKE '%0041'
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_SMS_DISPATCH_COLUMN_SQL = text(
    """
SELECT
    table_row.relkind AS relation_kind,
    table_row.relpersistence AS persistence,
    table_row.relrowsecurity AS row_security,
    table_row.relforcerowsecurity AS force_row_security,
    attribute_row.attnum AS ordinal_position,
    attribute_row.attname AS column_name,
    format_type(attribute_row.atttypid, attribute_row.atttypmod) AS data_type,
    attribute_row.attnotnull AS is_not_null,
    attribute_row.attidentity AS identity_kind,
    attribute_row.attgenerated AS generated_kind,
    pg_get_expr(default_row.adbin, default_row.adrelid, TRUE)
        AS default_expression
FROM pg_class AS table_row
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_attribute AS attribute_row ON attribute_row.attrelid = table_row.oid
LEFT JOIN pg_attrdef AS default_row
  ON default_row.adrelid = table_row.oid
 AND default_row.adnum = attribute_row.attnum
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'sms_challenge_dispatches'
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
ORDER BY attribute_row.attnum
"""
)

_SMS_DISPATCH_CONSTRAINT_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    constraint_row.connoinherit AS is_no_inherit,
    constraint_row.conislocal AS is_local,
    constraint_row.coninhcount AS inheritance_count,
    constraint_row.conparentid AS parent_constraint_id,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
               AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    referenced_table.relname AS referenced_table,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.confkey) WITH ORDINALITY
               AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.confrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS referenced_columns,
    constraint_row.confdeltype AS delete_action
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS referenced_table
  ON referenced_table.oid = constraint_row.confrelid
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'sms_challenge_dispatches'
ORDER BY constraint_row.conname
"""
)

_SMS_DISPATCH_INDEX_SQL = text(
    """
SELECT
    index_row.relname AS index_name,
    pg_get_userbyid(index_row.relowner) AS owner_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisprimary AS is_primary,
    index_metadata.indisexclusion AS is_exclusion,
    index_metadata.indimmediate AS is_immediate,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    index_metadata.indnullsnotdistinct AS nulls_not_distinct,
    index_metadata.indnkeyatts AS key_attribute_count,
    index_metadata.indnatts AS total_attribute_count,
    index_metadata.indexprs IS NOT NULL AS has_expressions,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'sms_challenge_dispatches'
ORDER BY index_row.relname
"""
)

_SMS_DISPATCH_TABLE_ACL_SQL = text(
    """
WITH target AS (
    SELECT
        table_row.oid,
        table_row.relowner,
        table_row.relacl,
        pg_get_userbyid(table_row.relowner) AS owner_name
      FROM pg_class AS table_row
      JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'public'
       AND table_row.relname = 'sms_challenge_dispatches'
)
SELECT
    target.owner_name,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_backup')
        AS backup_role_exists,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_edge')
        AS edge_role_exists,
    CASE
        WHEN acl.grantee = 0 THEN 'PUBLIC'
        ELSE grantee_role.rolname
    END AS grantee_name,
    acl.privilege_type,
    acl.is_grantable
FROM target
LEFT JOIN LATERAL aclexplode(
    COALESCE(target.relacl, acldefault('r', target.relowner))
) AS acl ON acl.grantee <> target.relowner
LEFT JOIN pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
ORDER BY grantee_name, acl.privilege_type
"""
)

_SMS_DISPATCH_COLUMN_ACL_SQL = text(
    """
WITH target AS (
    SELECT
        table_row.oid,
        table_row.relowner,
        pg_get_userbyid(table_row.relowner) AS owner_name
      FROM pg_class AS table_row
      JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'public'
       AND table_row.relname = 'sms_challenge_dispatches'
), column_grants AS (
    SELECT
        attribute_row.attname AS column_name,
        CASE
            WHEN acl.grantee = 0 THEN 'PUBLIC'
            ELSE grantee_role.rolname
        END AS grantee_name,
        acl.privilege_type,
        acl.is_grantable
      FROM target
      JOIN pg_attribute AS attribute_row
        ON attribute_row.attrelid = target.oid
      CROSS JOIN LATERAL aclexplode(attribute_row.attacl) AS acl
      LEFT JOIN pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
     WHERE attribute_row.attnum > 0
       AND NOT attribute_row.attisdropped
       AND acl.grantee <> target.relowner
)
SELECT
    target.owner_name,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_backup')
        AS backup_role_exists,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_edge')
        AS edge_role_exists,
    column_grants.column_name,
    column_grants.grantee_name,
    column_grants.privilege_type,
    column_grants.is_grantable
FROM target
LEFT JOIN column_grants ON TRUE
ORDER BY column_grants.column_name, column_grants.grantee_name,
         column_grants.privilege_type
"""
)

_SMS_DISPATCH_ROLE_ACCESS_SQL = text(
    """
WITH target_table AS (
    SELECT table_row.oid
      FROM pg_class AS table_row
      JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'public'
       AND table_row.relname = 'sms_challenge_dispatches'
), current_database_owner AS (
    SELECT database_row.datdba AS role_oid
      FROM pg_database AS database_row
     WHERE database_row.datname = current_database()
), protected_roles(role_label, requested_name) AS (
    VALUES
        ('backup', CAST('star_oam_backup' AS name)),
        ('edge', CAST('star_oam_edge' AS name)),
        ('runtime', CAST(:runtime_role AS name)),
        ('migration', CAST(:migration_role AS name))
), role_capabilities AS (
    SELECT
        role_row.oid AS role_oid,
        target_table.oid AS table_oid,
        has_table_privilege(role_row.oid, target_table.oid, 'SELECT')
            OR has_any_column_privilege(
                role_row.oid, target_table.oid, 'SELECT'
            ) AS can_select,
        has_table_privilege(role_row.oid, target_table.oid, 'INSERT')
            OR has_table_privilege(role_row.oid, target_table.oid, 'UPDATE')
            OR has_table_privilege(role_row.oid, target_table.oid, 'DELETE')
            OR has_table_privilege(role_row.oid, target_table.oid, 'TRUNCATE')
            OR has_table_privilege(
                role_row.oid, target_table.oid, 'REFERENCES'
            )
            OR has_table_privilege(role_row.oid, target_table.oid, 'TRIGGER')
            OR has_any_column_privilege(
                role_row.oid, target_table.oid, 'INSERT'
            )
            OR has_any_column_privilege(
                role_row.oid, target_table.oid, 'UPDATE'
            )
            OR has_any_column_privilege(
                role_row.oid, target_table.oid, 'REFERENCES'
            ) AS can_write
      FROM pg_roles AS role_row
      CROSS JOIN target_table
), role_evidence AS (
    SELECT
        protected.role_label,
        CAST(protected.requested_name AS text) AS role_name,
        role_row.oid AS role_oid,
        role_row.oid IS NOT NULL AS role_exists,
        COALESCE(role_row.rolinherit, FALSE) AS role_inherits,
        COALESCE(capabilities.can_select, FALSE) AS can_select,
        COALESCE(capabilities.can_write, FALSE) AS can_write
      FROM protected_roles AS protected
      LEFT JOIN pg_roles AS role_row
        ON role_row.rolname = protected.requested_name
      LEFT JOIN role_capabilities AS capabilities
        ON capabilities.role_oid = role_row.oid
)
SELECT
    source.role_label,
    source.role_name,
    source.role_exists,
    source.role_inherits,
    source.can_select,
    source.can_write,
    EXISTS (
        SELECT 1
          FROM pg_roles AS target_role
         WHERE source.role_oid IS NOT NULL
           AND target_role.oid <> source.role_oid
           AND pg_has_role(source.role_oid, target_role.oid, 'MEMBER')
           AND NOT (
               target_role.rolname = 'pg_database_owner'
               AND source.role_oid = (
                   SELECT owner.role_oid FROM current_database_owner AS owner
               )
           )
    ) AS is_member_of_any_role,
    EXISTS (
        SELECT 1
          FROM pg_roles AS candidate_role
         WHERE source.role_oid IS NOT NULL
           AND NOT candidate_role.rolsuper
           AND candidate_role.oid <> source.role_oid
           AND pg_has_role(candidate_role.oid, source.role_oid, 'MEMBER')
    ) AS has_any_nonsuper_member,
    EXISTS (
        SELECT 1
          FROM role_capabilities AS target_role
         WHERE source.role_oid IS NOT NULL
           AND target_role.role_oid <> source.role_oid
           AND pg_has_role(source.role_oid, target_role.role_oid, 'SET')
           AND target_role.can_select
    ) AS can_set_select_role,
    EXISTS (
        SELECT 1
          FROM role_capabilities AS target_role
         WHERE source.role_oid IS NOT NULL
           AND target_role.role_oid <> source.role_oid
           AND pg_has_role(source.role_oid, target_role.role_oid, 'SET')
           AND target_role.can_write
    ) AS can_set_write_role,
    EXISTS (
        SELECT 1
          FROM role_capabilities AS target_role
         WHERE source.role_oid IS NOT NULL
           AND target_role.role_oid <> source.role_oid
           AND pg_has_role(
               source.role_oid,
               target_role.role_oid,
               'MEMBER WITH ADMIN OPTION'
           )
           AND target_role.can_select
    ) AS can_admin_select_role,
    EXISTS (
        SELECT 1
          FROM role_capabilities AS target_role
         WHERE source.role_oid IS NOT NULL
           AND target_role.role_oid <> source.role_oid
           AND pg_has_role(
               source.role_oid,
               target_role.role_oid,
               'MEMBER WITH ADMIN OPTION'
           )
           AND target_role.can_write
    ) AS can_admin_write_role,
    EXISTS (
        SELECT 1
          FROM pg_roles AS candidate_role
         WHERE source.role_oid IS NOT NULL
           AND NOT candidate_role.rolsuper
           AND candidate_role.oid <> source.role_oid
           AND pg_has_role(candidate_role.oid, source.role_oid, 'USAGE')
    ) AS inherited_by_other_role,
    EXISTS (
        SELECT 1
          FROM pg_roles AS candidate_role
         WHERE source.role_oid IS NOT NULL
           AND NOT candidate_role.rolsuper
           AND candidate_role.oid <> source.role_oid
           AND pg_has_role(candidate_role.oid, source.role_oid, 'SET')
    ) AS settable_by_other_role,
    EXISTS (
        SELECT 1
          FROM pg_roles AS candidate_role
         WHERE source.role_oid IS NOT NULL
           AND NOT candidate_role.rolsuper
           AND candidate_role.oid <> source.role_oid
           AND pg_has_role(
               candidate_role.oid,
               source.role_oid,
               'MEMBER WITH ADMIN OPTION'
           )
    ) AS administered_by_other_role,
    EXISTS (
        SELECT 1
          FROM role_capabilities AS capability_role
          JOIN pg_roles AS candidate_role
            ON candidate_role.oid <> capability_role.role_oid
           AND NOT candidate_role.rolsuper
         WHERE (capability_role.can_select OR capability_role.can_write)
           AND pg_has_role(
               candidate_role.oid,
               capability_role.role_oid,
               'MEMBER WITH ADMIN OPTION'
           )
    ) AS capability_role_has_admin_member
FROM role_evidence AS source
ORDER BY source.role_label
"""
)

_KMS_DATA_KEY_PIN_TABLE_ACL_SQL = text(
    """
WITH target AS (
    SELECT
        table_row.oid,
        table_row.relowner,
        table_row.relacl,
        pg_get_userbyid(table_row.relowner) AS owner_name
      FROM pg_class AS table_row
      JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
     WHERE schema_row.nspname = 'public'
       AND table_row.relname = 'kms_data_key_pins'
)
SELECT
    target.owner_name,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_backup')
        AS backup_role_exists,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_edge')
        AS edge_role_exists,
    CASE
        WHEN acl.grantee = 0 THEN 'PUBLIC'
        ELSE grantee_role.rolname
    END AS grantee_name,
    acl.privilege_type,
    acl.is_grantable
FROM target
LEFT JOIN LATERAL aclexplode(
    COALESCE(target.relacl, acldefault('r', target.relowner))
) AS acl ON acl.grantee <> target.relowner
LEFT JOIN pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
ORDER BY grantee_name, acl.privilege_type
"""
)

_KMS_DATA_KEY_PIN_FUNCTION_ACL_SQL = text(
    """
WITH target AS (
    SELECT
        function_row.proowner,
        function_row.proacl,
        pg_get_userbyid(function_row.proowner) AS owner_name
      FROM pg_proc AS function_row
      JOIN pg_namespace AS schema_row
        ON schema_row.oid = function_row.pronamespace
     WHERE schema_row.nspname = 'public'
       AND function_row.proname = 'rsc_reject_kms_data_key_pin_mutation_0040'
       AND function_row.pronargs = 0
)
SELECT
    target.owner_name,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_backup')
        AS backup_role_exists,
    EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'star_oam_edge')
        AS edge_role_exists,
    CASE
        WHEN acl.grantee = 0 THEN 'PUBLIC'
        ELSE grantee_role.rolname
    END AS grantee_name,
    acl.privilege_type,
    acl.is_grantable
FROM target
LEFT JOIN LATERAL aclexplode(
    COALESCE(target.proacl, acldefault('f', target.proowner))
) AS acl ON acl.grantee <> target.proowner
LEFT JOIN pg_roles AS grantee_role ON grantee_role.oid = acl.grantee
ORDER BY grantee_name, acl.privilege_type
"""
)

_NONOPENING_STOCKTAKE_START_FUNCTION_NAME_LITERALS = ",\n      ".join(
    f"'{name}'"
    for name in (
        "rsc_guard_stocktake_start_completion_0047",
        "rsc_validate_nonopening_stocktake_start_causality_0047",
        "rsc_dispatch_nonopening_stocktake_start_causality_0047",
    )
)
_NONOPENING_STOCKTAKE_START_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND (
      table_row.relname = 'stocktake_start_completions'
      OR trigger_row.tgname LIKE '%0047'
      OR function_row.proname IN (
          {_NONOPENING_STOCKTAKE_START_FUNCTION_NAME_LITERALS}
      )
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_STOCKTAKE_START_COMPLETION_COLUMN_SQL = text(
    """
SELECT
    table_row.relkind AS relation_kind,
    table_row.relpersistence AS persistence,
    table_row.relrowsecurity AS row_security,
    table_row.relforcerowsecurity AS force_row_security,
    attribute_row.attnum AS ordinal_position,
    attribute_row.attname AS column_name,
    format_type(attribute_row.atttypid, attribute_row.atttypmod) AS data_type,
    attribute_row.attnotnull AS is_not_null,
    attribute_row.attidentity AS identity_kind,
    attribute_row.attgenerated AS generated_kind,
    pg_get_expr(default_row.adbin, default_row.adrelid, TRUE)
        AS default_expression
FROM pg_class AS table_row
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_attribute AS attribute_row ON attribute_row.attrelid = table_row.oid
LEFT JOIN pg_attrdef AS default_row
  ON default_row.adrelid = table_row.oid
 AND default_row.adnum = attribute_row.attnum
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'stocktake_start_completions'
  AND attribute_row.attnum > 0
  AND NOT attribute_row.attisdropped
ORDER BY attribute_row.attnum
"""
)

_STOCKTAKE_START_COMPLETION_CONSTRAINT_SQL = text(
    """
SELECT
    constraint_row.conname AS constraint_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    constraint_row.connoinherit AS is_no_inherit,
    constraint_row.conislocal AS is_local,
    constraint_row.coninhcount AS inheritance_count,
    constraint_row.conparentid AS parent_constraint_id,
    pg_get_constraintdef(constraint_row.oid, TRUE) AS definition,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
               AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    referenced_table.relname AS referenced_table,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.confkey) WITH ORDINALITY
               AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.confrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS referenced_columns,
    constraint_row.confdeltype AS delete_action
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS referenced_table
  ON referenced_table.oid = constraint_row.confrelid
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'stocktake_start_completions'
  AND constraint_row.contype IN ('c', 'f', 'p', 'u')
ORDER BY constraint_row.conname
"""
)

_STOCKTAKE_START_COMPLETION_INDEX_SQL = text(
    """
SELECT
    index_row.relname AS index_name,
    pg_get_userbyid(index_row.relowner) AS owner_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisprimary AS is_primary,
    index_metadata.indisexclusion AS is_exclusion,
    index_metadata.indimmediate AS is_immediate,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    index_metadata.indnullsnotdistinct AS nulls_not_distinct,
    index_metadata.indnkeyatts AS key_attribute_count,
    index_metadata.indnatts AS total_attribute_count,
    index_metadata.indexprs IS NOT NULL AS has_expressions,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND table_row.relname = 'stocktake_start_completions'
ORDER BY index_row.relname
"""
)

_NONOPENING_STOCKTAKE_CLOSE_FACT_TABLE_LITERALS = ",\n      ".join(
    f"'{name}'" for name in sorted(_STOCKTAKE_CLOSE_FACT_TABLES)
)
_NONOPENING_STOCKTAKE_CLOSE_TRIGGER_SQL = text(
    f"""
SELECT
    trigger_row.tgname AS trigger_name,
    table_row.relname AS table_name,
    function_row.proname AS function_name,
    function_schema.nspname AS function_schema,
    trigger_row.tgenabled AS enabled,
    trigger_row.tgtype AS trigger_type,
    trigger_row.tgconstraint <> 0 AS is_constraint_trigger,
    trigger_row.tgdeferrable AS is_deferrable,
    trigger_row.tginitdeferred AS is_initially_deferred,
    trigger_row.tgqual IS NOT NULL AS has_when_clause,
    trigger_row.tgattr::text <> '' AS has_column_filter
FROM pg_trigger AS trigger_row
JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
JOIN pg_namespace AS table_schema ON table_schema.oid = table_row.relnamespace
JOIN pg_proc AS function_row ON function_row.oid = trigger_row.tgfoid
JOIN pg_namespace AS function_schema
  ON function_schema.oid = function_row.pronamespace
WHERE table_schema.nspname = 'public'
  AND (
      table_row.relname IN (
          {_NONOPENING_STOCKTAKE_CLOSE_FACT_TABLE_LITERALS}
      )
      OR trigger_row.tgname LIKE '%0038'
  )
  AND NOT trigger_row.tgisinternal
ORDER BY trigger_row.tgname
"""
)

_NONOPENING_STOCKTAKE_CLOSE_INDEX_NAME_LITERALS = ",\n      ".join(
    f"'{name}'"
    for name in sorted(EXPECTED_NONOPENING_STOCKTAKE_CLOSE_INDEXES)
)
_NONOPENING_STOCKTAKE_CLOSE_INDEX_SQL = text(
    f"""
SELECT
    index_row.relname AS index_name,
    table_row.relname AS table_name,
    access_method.amname AS access_method,
    index_metadata.indisunique AS is_unique,
    index_metadata.indisvalid AS is_valid,
    index_metadata.indisready AS is_ready,
    index_metadata.indislive AS is_live,
    ARRAY(
        SELECT pg_get_indexdef(index_metadata.indexrelid, key_position, TRUE)
          FROM generate_series(1, index_metadata.indnkeyatts) AS key_position
         ORDER BY key_position
    ) AS key_columns,
    pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE)
        AS predicate
FROM pg_index AS index_metadata
JOIN pg_class AS index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class AS table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am AS access_method ON access_method.oid = index_row.relam
WHERE schema_row.nspname = 'public'
  AND index_row.relname IN (
      {_NONOPENING_STOCKTAKE_CLOSE_INDEX_NAME_LITERALS}
  )
ORDER BY index_row.relname
"""
)

_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINT_NAME_LITERALS = ",\n      ".join(
    f"'{name}'"
    for name in sorted(EXPECTED_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINTS)
)
_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINT_SQL = text(
    f"""
SELECT
    constraint_row.conname AS constraint_name,
    table_row.relname AS table_name,
    constraint_row.contype AS constraint_type,
    constraint_row.convalidated AS is_validated,
    constraint_row.condeferrable AS is_deferrable,
    constraint_row.condeferred AS is_initially_deferred,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.conkey) WITH ORDINALITY
            AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.conrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS constrained_columns,
    referenced_table.relname AS referenced_table,
    COALESCE((
        SELECT array_agg(attribute_row.attname ORDER BY key_row.ordinality)
          FROM unnest(constraint_row.confkey) WITH ORDINALITY
            AS key_row(attnum, ordinality)
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = constraint_row.confrelid
           AND attribute_row.attnum = key_row.attnum
    ), ARRAY[]::name[]) AS referenced_columns,
    constraint_row.confupdtype AS update_action,
    constraint_row.confdeltype AS delete_action
FROM pg_constraint AS constraint_row
JOIN pg_class AS table_row ON table_row.oid = constraint_row.conrelid
JOIN pg_namespace AS schema_row ON schema_row.oid = table_row.relnamespace
LEFT JOIN pg_class AS referenced_table
  ON referenced_table.oid = constraint_row.confrelid
WHERE schema_row.nspname = 'public'
  AND constraint_row.conname IN (
      {_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINT_NAME_LITERALS}
  )
ORDER BY constraint_row.conname
"""
)

_AUDIT_HEAD_SQL = text(
    """
SELECT
    head.id,
    head.stream_key,
    head.version,
    head.last_event_id,
    head.last_hash,
    event.id AS bound_event_id,
    event.event_hash AS bound_event_hash
FROM audit_chain_heads AS head
LEFT JOIN audit_events AS event
  ON event.id = head.last_event_id
 AND event.event_hash = head.last_hash
ORDER BY head.stream_key
"""
)

_AUDIT_EVENT_SQL = text(
    """
SELECT
    event.id,
    event.stream_key,
    event.stream_version,
    event.actor_user_id,
    event.action,
    event.aggregate_type,
    event.aggregate_id,
    event.before_jsonb,
    event.after_jsonb,
    event.request_id,
    event.previous_hash,
    event.event_hash,
    event.occurred_at
FROM audit_events AS event
ORDER BY event.id
"""
)


def _select_material_request_approval_functions(
    rows: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    # Keep unknown functions in these migration families visible to the exact
    # catalog-set validator. Selecting only manifest names would hide drift.
    return [
        row
        for row in rows
        if isinstance(row.get("function_name"), str)
        and row["function_name"].endswith(
            ("_0029", "_0030", "_0045", "_0046", "_0059", "_0060", "_0069", "_0070", "_0071", "_0072", "_0077", "_0087", "_0090", "_0092", "_0093", "_0094")
        )
    ]


_INBOUND_RECEIPT_INDEX_SQL = text("""
SELECT table_row.relname AS table_name, index_metadata.indisunique AS is_unique,
       index_metadata.indisvalid AS is_valid, index_metadata.indisready AS is_ready,
       index_metadata.indislive AS is_live, index_metadata.indnkeyatts AS key_count,
       index_metadata.indnatts AS column_count, method.amname AS access_method,
       pg_get_indexdef(index_metadata.indexrelid, 1, TRUE) AS key_column,
       pg_get_expr(index_metadata.indpred, index_metadata.indrelid, TRUE) AS predicate
FROM pg_index index_metadata
JOIN pg_class index_row ON index_row.oid = index_metadata.indexrelid
JOIN pg_class table_row ON table_row.oid = index_metadata.indrelid
JOIN pg_namespace schema_row ON schema_row.oid = table_row.relnamespace
JOIN pg_am method ON method.oid = index_row.relam
WHERE schema_row.nspname = 'public' AND index_row.relname = 'uq_inbound_orders_receipt'
""")


_WORK_ORDER_POSTING_INDEX_SQL = text(str(_INBOUND_RECEIPT_INDEX_SQL).replace("uq_inbound_orders_receipt", "uq_work_order_material_posting_0090"))


def _assert_work_order_posting_index(rows) -> None:
    expected = dict(table_name="work_order_material_operations", is_unique=True, is_valid=True, is_ready=True,
        is_live=True, key_count=1, column_count=1, access_method="btree", key_column="posting_transaction_id", predicate=None)
    if len(rows) != 1 or dict(rows[0]) != expected:
        raise DatabaseSecurityBoundaryError("production database work order posting uniqueness guard failed")


def _assert_inbound_receipt_index(rows) -> None:
    expected = dict(table_name="inbound_orders", is_unique=True, is_valid=True, is_ready=True,
        is_live=True, key_count=1, column_count=1, access_method="btree", key_column="receipt_id", predicate=None)
    if len(rows) != 1 or dict(rows[0]) != expected:
        raise DatabaseSecurityBoundaryError("production database inbound receipt uniqueness guard failed")


def validate_production_database_security(
    engine: Engine,
    *,
    expected_runtime_role: str,
    expected_migration_role: str,
) -> None:
    """Read PostgreSQL catalogs and reject any over-privileged API identity."""

    try:
        with engine.connect() as raw_connection:
            # Every catalog and audit-graph query must observe one database
            # snapshot.  Another healthy API replica may append an event while
            # this instance starts; READ COMMITTED could otherwise mix the old
            # event set with the new head (or the reverse) and fail spuriously.
            connection = raw_connection.execution_options(
                isolation_level="REPEATABLE READ"
            )
            evidence = connection.execute(
                _ROLE_EVIDENCE_SQL,
                {"migration_role": expected_migration_role},
            ).mappings().one_or_none()
            table_acl = connection.execute(_TABLE_ACL_SQL).mappings().all()
            column_acl = connection.execute(_COLUMN_ACL_SQL).mappings().all()
            sequence_acl = connection.execute(_SEQUENCE_ACL_SQL).mappings().all()
            function_acl = connection.execute(_FUNCTION_ACL_SQL).mappings().all()
            material_request_approval_functions = (
                _select_material_request_approval_functions(function_acl)
            )
            audit_triggers = connection.execute(_AUDIT_TRIGGER_SQL).mappings().all()
            audit_stream_columns = connection.execute(
                _AUDIT_STREAM_COLUMN_SQL
            ).mappings().all()
            audit_stream_constraints = connection.execute(
                _AUDIT_STREAM_CONSTRAINT_SQL
            ).mappings().all()
            stocktake_recount_columns = connection.execute(
                _STOCKTAKE_RECOUNT_COLUMN_SQL
            ).mappings().all()
            stocktake_recount_constraints = connection.execute(
                _STOCKTAKE_RECOUNT_CONSTRAINT_SQL
            ).mappings().all()
            stocktake_recount_indexes = connection.execute(
                _STOCKTAKE_RECOUNT_INDEX_SQL
            ).mappings().all()
            stocktake_sensitive_triggers = connection.execute(
                _STOCKTAKE_SENSITIVE_TRIGGER_SQL
            ).mappings().all()
            stocktake_recount_graph_triggers = connection.execute(
                _STOCKTAKE_RECOUNT_TRIGGER_SQL
            ).mappings().all()
            stocktake_scope_triggers = connection.execute(
                _STOCKTAKE_SCOPE_TRIGGER_SQL
            ).mappings().all()
            reconciliation_triggers = connection.execute(
                _RECONCILIATION_TRIGGER_SQL
            ).mappings().all()
            reconciliation_constraints = connection.execute(
                _RECONCILIATION_CONSTRAINT_SQL
            ).mappings().all()
            reconciliation_partial_indexes = connection.execute(
                _RECONCILIATION_PARTIAL_INDEX_SQL
            ).mappings().all()
            opening_terminal_triggers = connection.execute(
                _OPENING_TERMINAL_TRIGGER_SQL
            ).mappings().all()
            opening_terminal_indexes = connection.execute(
                _OPENING_TERMINAL_INDEX_SQL
            ).mappings().all()
            formal_file_triggers = connection.execute(
                _FORMAL_FILE_TRIGGER_SQL
            ).mappings().all()
            formal_file_indexes = connection.execute(
                _FORMAL_FILE_INDEX_SQL
            ).mappings().all()
            material_request_approval_triggers = connection.execute(
                _MATERIAL_REQUEST_APPROVAL_TRIGGER_SQL
            ).mappings().all()
            inbound_receipt_indexes = connection.execute(_INBOUND_RECEIPT_INDEX_SQL).mappings().all()
            work_order_posting_indexes = connection.execute(_WORK_ORDER_POSTING_INDEX_SQL).mappings().all()
            material_request_content_manifest_columns = connection.execute(
                _MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_SQL
            ).mappings().all()
            material_request_content_manifest_checks = connection.execute(
                _MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK_SQL
            ).mappings().all()
            material_request_cancellation_triggers = connection.execute(
                _MATERIAL_REQUEST_CANCELLATION_TRIGGER_SQL
            ).mappings().all()
            material_request_cancellation_indexes = connection.execute(
                _MATERIAL_REQUEST_CANCELLATION_INDEX_SQL
            ).mappings().all()
            material_request_command_recovery_indexes = connection.execute(
                _MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX_SQL
            ).mappings().all()
            kms_data_key_pin_triggers = connection.execute(
                _KMS_DATA_KEY_PIN_TRIGGER_SQL
            ).mappings().all()
            kms_data_key_pin_columns = connection.execute(
                _KMS_DATA_KEY_PIN_COLUMN_SQL
            ).mappings().all()
            kms_data_key_pin_constraints = connection.execute(
                _KMS_DATA_KEY_PIN_CONSTRAINT_SQL
            ).mappings().all()
            kms_data_key_pin_indexes = connection.execute(
                _KMS_DATA_KEY_PIN_INDEX_SQL
            ).mappings().all()
            sms_dispatch_triggers = connection.execute(
                _SMS_DISPATCH_TRIGGER_SQL
            ).mappings().all()
            sms_dispatch_columns = connection.execute(
                _SMS_DISPATCH_COLUMN_SQL
            ).mappings().all()
            sms_dispatch_constraints = connection.execute(
                _SMS_DISPATCH_CONSTRAINT_SQL
            ).mappings().all()
            sms_dispatch_indexes = connection.execute(
                _SMS_DISPATCH_INDEX_SQL
            ).mappings().all()
            sms_dispatch_table_acl = connection.execute(
                _SMS_DISPATCH_TABLE_ACL_SQL
            ).mappings().all()
            sms_dispatch_column_acl = connection.execute(
                _SMS_DISPATCH_COLUMN_ACL_SQL
            ).mappings().all()
            sms_dispatch_role_access = connection.execute(
                _SMS_DISPATCH_ROLE_ACCESS_SQL,
                {
                    "runtime_role": expected_runtime_role,
                    "migration_role": expected_migration_role,
                },
            ).mappings().all()
            kms_data_key_pin_table_acl = connection.execute(
                _KMS_DATA_KEY_PIN_TABLE_ACL_SQL
            ).mappings().all()
            kms_data_key_pin_function_acl = connection.execute(
                _KMS_DATA_KEY_PIN_FUNCTION_ACL_SQL
            ).mappings().all()
            nonopening_stocktake_start_triggers = connection.execute(
                _NONOPENING_STOCKTAKE_START_TRIGGER_SQL
            ).mappings().all()
            stocktake_start_completion_columns = connection.execute(
                _STOCKTAKE_START_COMPLETION_COLUMN_SQL
            ).mappings().all()
            stocktake_start_completion_constraints = connection.execute(
                _STOCKTAKE_START_COMPLETION_CONSTRAINT_SQL
            ).mappings().all()
            stocktake_start_completion_indexes = connection.execute(
                _STOCKTAKE_START_COMPLETION_INDEX_SQL
            ).mappings().all()
            nonopening_stocktake_close_triggers = connection.execute(
                _NONOPENING_STOCKTAKE_CLOSE_TRIGGER_SQL
            ).mappings().all()
            nonopening_stocktake_close_indexes = connection.execute(
                _NONOPENING_STOCKTAKE_CLOSE_INDEX_SQL
            ).mappings().all()
            nonopening_stocktake_close_constraints = connection.execute(
                _NONOPENING_STOCKTAKE_CLOSE_CONSTRAINT_SQL
            ).mappings().all()
            audit_heads = connection.execute(_AUDIT_HEAD_SQL).mappings().all()
            audit_events = connection.execute(_AUDIT_EVENT_SQL).mappings().all()
    except Exception:
        raise DatabaseSecurityBoundaryError(
            "production database security evidence could not be verified"
        ) from None
    if evidence is None:
        raise DatabaseSecurityBoundaryError(
            "production database runtime role is not present"
        )
    _assert_production_database_evidence(
        evidence,
        expected_runtime_role=expected_runtime_role,
        expected_migration_role=expected_migration_role,
    )
    _assert_runtime_table_acl(
        table_acl,
        expected_migration_role=expected_migration_role,
    )
    _assert_runtime_column_acl(
        column_acl,
        expected_runtime_role=expected_runtime_role,
    )
    _assert_runtime_sequence_acl(
        sequence_acl,
        expected_migration_role=expected_migration_role,
    )
    _assert_runtime_function_acl(
        function_acl,
        expected_migration_role=expected_migration_role,
    )
    _assert_audit_trigger_guards(audit_triggers)
    _assert_audit_stream_schema(
        columns=audit_stream_columns,
        constraints=audit_stream_constraints,
    )
    _assert_stocktake_recount_schema(
        columns=stocktake_recount_columns,
        constraints=stocktake_recount_constraints,
        indexes=stocktake_recount_indexes,
        triggers=[
            *stocktake_sensitive_triggers,
            *stocktake_recount_graph_triggers,
        ],
    )
    _assert_stocktake_scope_triggers(stocktake_scope_triggers)
    _assert_reconciliation_triggers(reconciliation_triggers)
    _assert_reconciliation_schema(
        constraints=reconciliation_constraints,
        partial_indexes=reconciliation_partial_indexes,
    )
    _assert_opening_terminal_triggers(opening_terminal_triggers)
    _assert_opening_terminal_index(opening_terminal_indexes)
    _assert_formal_file_guards(
        triggers=formal_file_triggers,
        indexes=formal_file_indexes,
    )
    _assert_material_request_approval_guards(
        triggers=material_request_approval_triggers,
        functions=material_request_approval_functions,
        manifest_columns=material_request_content_manifest_columns,
        manifest_checks=material_request_content_manifest_checks,
        expected_migration_role=expected_migration_role,
    )
    _assert_inbound_receipt_index(inbound_receipt_indexes)
    _assert_work_order_posting_index(work_order_posting_indexes)
    _assert_material_request_cancellation_guards(
        triggers=material_request_cancellation_triggers,
        indexes=material_request_cancellation_indexes,
    )
    _assert_material_request_command_recovery_index(
        material_request_command_recovery_indexes
    )
    _assert_kms_data_key_pin_guards(
        triggers=kms_data_key_pin_triggers,
        columns=kms_data_key_pin_columns,
        constraints=kms_data_key_pin_constraints,
        indexes=kms_data_key_pin_indexes,
        table_acl=kms_data_key_pin_table_acl,
        function_acl=kms_data_key_pin_function_acl,
        expected_runtime_role=expected_runtime_role,
        expected_migration_role=expected_migration_role,
    )
    _assert_sms_dispatch_guards(
        triggers=sms_dispatch_triggers,
        columns=sms_dispatch_columns,
        constraints=sms_dispatch_constraints,
        indexes=sms_dispatch_indexes,
        table_acl=sms_dispatch_table_acl,
        column_acl=sms_dispatch_column_acl,
        role_access=sms_dispatch_role_access,
        expected_runtime_role=expected_runtime_role,
        expected_migration_role=expected_migration_role,
    )
    _assert_nonopening_stocktake_start_guards(
        triggers=nonopening_stocktake_start_triggers,
        columns=stocktake_start_completion_columns,
        constraints=stocktake_start_completion_constraints,
        indexes=stocktake_start_completion_indexes,
        expected_migration_role=expected_migration_role,
    )
    _assert_nonopening_stocktake_close_guards(
        triggers=nonopening_stocktake_close_triggers,
        indexes=nonopening_stocktake_close_indexes,
        constraints=nonopening_stocktake_close_constraints,
    )
    _assert_fixed_audit_heads(audit_heads)
    _assert_complete_audit_graph(heads=audit_heads, events=audit_events)


def _assert_production_database_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_runtime_role: str,
    expected_migration_role: str,
) -> None:
    expected_values: dict[str, Any] = {
        "role_name": expected_runtime_role,
        "session_role_name": expected_runtime_role,
        "is_superuser": False,
        "can_create_database": False,
        "can_create_role": False,
        "can_replicate": False,
        "can_bypass_rls": False,
        "can_create_in_database": False,
        "can_create_temporary_tables": False,
        "has_database_grant_option": False,
        "can_use_schema": True,
        "can_create_in_schema": False,
        "has_schema_grant_option": False,
        "current_schema_name": "public",
        "current_schema_path": ["public"],
        "has_non_system_schema_control": False,
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
    failed = [
        key
        for key, expected in expected_values.items()
        if evidence.get(key) != expected
    ]
    for owner_key in (
        "database_owner",
        "schema_owner",
        "audit_events_owner",
        "audit_heads_owner",
    ):
        if evidence.get(owner_key) != expected_migration_role:
            failed.append(owner_key)
    if failed:
        raise DatabaseSecurityBoundaryError(
            "production database role boundary failed: " + ", ".join(sorted(failed))
        )


def _expected_table_privileges(table_name: str) -> frozenset[str]:
    privileges: set[str] = set()
    if table_name in RUNTIME_READ_TABLES:
        privileges.add("SELECT")
    if table_name in RUNTIME_INSERT_TABLES:
        privileges.add("INSERT")
    if table_name in RUNTIME_UPDATE_TABLES:
        privileges.add("UPDATE")
    if table_name in RUNTIME_DELETE_TABLES:
        privileges.add("DELETE")
    return frozenset(privileges)


def _assert_runtime_table_acl(
    rows: list[Mapping[str, Any]],
    *,
    expected_migration_role: str,
) -> None:
    actual_names: set[str] = set()
    failures: list[str] = []
    field_by_privilege = {
        "SELECT": "can_select",
        "INSERT": "can_insert",
        "UPDATE": "can_update",
        "DELETE": "can_delete",
        "TRUNCATE": "can_truncate",
        "REFERENCES": "can_reference",
        "TRIGGER": "can_trigger",
    }
    for row in rows:
        table_name = row.get("table_name")
        if not isinstance(table_name, str) or table_name in actual_names:
            failures.append("table_identity")
            continue
        actual_names.add(table_name)
        if row.get("owner_name") != expected_migration_role:
            failures.append(f"{table_name}.owner")
        expected = _expected_table_privileges(table_name)
        for privilege in TABLE_PRIVILEGES:
            actual = row.get(field_by_privilege[privilege])
            if actual != (privilege in expected):
                failures.append(f"{table_name}.{privilege.lower()}")
        if row.get("has_explicit_runtime_column_acl") is not (
            table_name in RUNTIME_UPDATE_COLUMNS
        ):
            failures.append(f"{table_name}.has_explicit_runtime_column_acl")
        for field in (
            "has_public_table_acl",
            "has_runtime_grant_option",
        ):
            if row.get(field) is not False:
                failures.append(f"{table_name}.{field}")
    expected_names = (
        RUNTIME_READ_TABLES
        | RUNTIME_INSERT_TABLES
        | RUNTIME_UPDATE_TABLES
        | RUNTIME_DELETE_TABLES
        | set(RUNTIME_UPDATE_COLUMNS)
    )
    missing = expected_names - actual_names
    failures.extend(f"{name}.missing" for name in sorted(missing))
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database table ACL failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_runtime_column_acl(
    rows: list[Mapping[str, Any]],
    *,
    expected_runtime_role: str,
) -> None:
    expected = {
        (table_name, column_name, expected_runtime_role, "UPDATE", False)
        for table_name, column_names in RUNTIME_UPDATE_COLUMNS.items()
        for column_name in column_names
    }
    actual: set[tuple[Any, Any, Any, Any, Any]] = set()
    failures: list[str] = []
    for row in rows:
        key = (
            row.get("table_name"),
            row.get("column_name"),
            row.get("grantee_name"),
            row.get("privilege_type"),
            row.get("is_grantable"),
        )
        if key in actual:
            failures.append("column_acl_identity")
        actual.add(key)
    for key in sorted(expected - actual, key=str):
        failures.append(f"{key[0]}.{key[1]}.missing")
    for key in sorted(actual - expected, key=str):
        failures.append(f"{key[0]}.{key[1]}.excess")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database column ACL failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_runtime_sequence_acl(
    rows: list[Mapping[str, Any]],
    *,
    expected_migration_role: str,
) -> None:
    failures: list[str] = []
    for row in rows:
        sequence_name = row.get("sequence_name")
        if not isinstance(sequence_name, str):
            failures.append("sequence_identity")
            continue
        if row.get("owner_name") != expected_migration_role:
            failures.append(f"{sequence_name}.owner")
        if any(
            row.get(field) is not False
            for field in ("can_use", "can_select", "can_update")
        ):
            failures.append(f"{sequence_name}.privilege")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database sequence ACL failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_runtime_function_acl(
    rows: list[Mapping[str, Any]],
    *,
    expected_migration_role: str,
) -> None:
    failures: list[str] = []
    function_ids: set[Any] = set()
    allowed_seen: set[tuple[str, str]] = set()
    internal_seen: set[tuple[str, str]] = set()
    oam_sync_seen: set[tuple[str, str]] = set()
    for row in rows:
        function_id = row.get("function_id")
        function_name = row.get("function_name")
        label = function_name if isinstance(function_name, str) else "function"
        if function_id is None or function_id in function_ids:
            failures.append("function_identity")
            continue
        function_ids.add(function_id)
        if row.get("api_execute_is_grantable") is not False:
            failures.append(f"{label}.execute_grant_option")
        if row.get("unexpected_execute_grantee_count") != 0:
            failures.append(f"{label}.unexpected_execute_grantee")
        if row.get("owner_name") != expected_migration_role:
            failures.append(f"{label}.owner")
        argument_types = str(row.get("argument_types") or "")
        coordinate = (label, argument_types)
        expected_helper = RUNTIME_EXECUTE_FUNCTIONS.get(coordinate)
        expected_internal = FORMAL_FILE_INTERNAL_FUNCTIONS.get(coordinate)
        expected_oam_sync = OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS.get(
            coordinate
        )
        if (
            expected_helper is None
            and expected_internal is None
            and expected_oam_sync is None
        ):
            if row.get("can_execute") is not False:
                failures.append(f"{label}.execute")
            for audience in (
                "edge_receiver_can_execute",
                "projector_can_execute",
            ):
                if row.get(audience) is not False:
                    failures.append(f"{label}.{audience}")
            continue
        if expected_helper is not None:
            allowed_seen.add(coordinate)
            expected_shape = RUNTIME_FUNCTION_SHAPES.get(coordinate)
            expected_body_hash = RUNTIME_FUNCTION_BODY_SHA256.get(coordinate)
            expected_execute = True
            expected_definition = expected_helper
        elif expected_internal is not None:
            internal_seen.add(coordinate)
            expected_shape = FORMAL_FILE_INTERNAL_FUNCTION_SHAPES.get(coordinate)
            expected_body_hash = FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256.get(
                coordinate
            )
            expected_execute = False
            expected_definition = expected_internal
        else:
            oam_sync_seen.add(coordinate)
            expected_shape = OAM_SYNC_RUNTIME_FUNCTION_SHAPES.get(coordinate)
            expected_body_hash = OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256.get(
                coordinate
            )
            expected_execute = False
            expected_definition = expected_oam_sync
        if expected_shape is None:
            failures.append(f"{label}.shape_manifest")
            continue
        (
            volatility,
            is_security_definer,
            language_name,
            configuration,
        ) = expected_definition
        function_kind, result_type, is_strict = expected_shape
        source_body = row.get("source_body")
        if row.get("can_execute") is not expected_execute:
            failures.append(f"{label}.execute")
        if row.get("volatility") != volatility:
            failures.append(f"{label}.volatility")
        if row.get("is_security_definer") is not is_security_definer:
            failures.append(f"{label}.security_definer")
        if row.get("language_name") != language_name:
            failures.append(f"{label}.language")
        if row.get("function_kind") != function_kind:
            failures.append(f"{label}.kind")
        if row.get("result_type") != result_type:
            failures.append(f"{label}.result_type")
        if row.get("returns_set") is not False:
            failures.append(f"{label}.returns_set")
        if row.get("variadic_type") != 0:
            failures.append(f"{label}.variadic_type")
        if row.get("argument_modes") is not None:
            failures.append(f"{label}.argument_modes")
        if row.get("argument_default_count") != 0:
            failures.append(f"{label}.argument_defaults")
        if row.get("is_strict") is not is_strict:
            failures.append(f"{label}.strict")
        if (
            not isinstance(source_body, str)
            or hashlib.sha256(source_body.encode("utf-8")).hexdigest()
            != expected_body_hash
        ):
            failures.append(f"{label}.body")
        if tuple(row.get("configuration") or ()) != configuration:
            failures.append(f"{label}.configuration")
        denied_audiences = (
            "public_can_execute",
            "edge_can_execute",
            "backup_can_execute",
        )
        for audience in denied_audiences:
            if row.get(audience) is not False:
                failures.append(f"{label}.{audience}")
        for audience in (
            "edge_receiver_can_execute",
            "projector_can_execute",
        ):
            expected_audience = (
                expected_oam_sync is not None
                and (audience != "edge_receiver_can_execute"
                     or coordinate != RECEIPT_SYNC_RUNTIME_FUNCTION)
            )
            if row.get(audience) is not expected_audience:
                failures.append(f"{label}.{audience}")
        expected_parallel = "u"
        if expected_oam_sync is not None:
            expected_parallel = OAM_SYNC_FUNCTION_MANIFEST[
                f"{coordinate[0]}({coordinate[1].replace(', ', ',')})"
            ][5]
        if row.get("parallel_safety") != expected_parallel:
            failures.append(f"{label}.parallel_safety")
        if row.get("is_leakproof") is not False:
            failures.append(f"{label}.leakproof")
    if (
        allowed_seen != set(RUNTIME_EXECUTE_FUNCTIONS)
        or set(RUNTIME_FUNCTION_SHAPES) != set(RUNTIME_EXECUTE_FUNCTIONS)
        or set(RUNTIME_FUNCTION_BODY_SHA256) != set(RUNTIME_EXECUTE_FUNCTIONS)
        or internal_seen != set(FORMAL_FILE_INTERNAL_FUNCTIONS)
        or set(FORMAL_FILE_INTERNAL_FUNCTION_SHAPES)
        != set(FORMAL_FILE_INTERNAL_FUNCTIONS)
        or set(FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256)
        != set(FORMAL_FILE_INTERNAL_FUNCTIONS)
        or oam_sync_seen != OAM_SYNC_RUNTIME_FUNCTIONS
        or set(OAM_SYNC_RUNTIME_FUNCTION_DEFINITIONS)
        != OAM_SYNC_RUNTIME_FUNCTIONS
        or set(OAM_SYNC_RUNTIME_FUNCTION_SHAPES)
        != OAM_SYNC_RUNTIME_FUNCTIONS
        or set(OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256)
        != OAM_SYNC_RUNTIME_FUNCTIONS
    ):
        failures.append("runtime_helper_set")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database function ACL failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_audit_trigger_guards(rows: list[Mapping[str, Any]]) -> None:
    failures: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual:
            failures.append("trigger_identity")
            continue
        actual[name] = row
    expected_names = set(EXPECTED_AUDIT_TRIGGERS)
    actual_names = set(actual)
    missing_names = sorted(expected_names - actual_names)
    unexpected_names = sorted(actual_names - expected_names)
    if missing_names:
        failures.append(f"trigger_set.missing={missing_names}")
    if unexpected_names:
        failures.append(f"trigger_set.unexpected={unexpected_names}")
    for name, (
        table_name,
        function_name,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    ) in (
        EXPECTED_AUDIT_TRIGGERS.items()
    ):
        row = actual.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != "A":
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        if row.get("is_constraint_trigger") is not is_constraint_trigger:
            failures.append(f"{name}.constraint")
        if row.get("is_deferrable") is not is_deferrable:
            failures.append(f"{name}.deferrable")
        if row.get("is_initially_deferred") is not is_initially_deferred:
            failures.append(f"{name}.initially_deferred")
        if row.get("has_when_clause") is not False:
            failures.append(f"{name}.when")
        if row.get("has_column_filter") is not False:
            failures.append(f"{name}.columns")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database audit trigger guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_audit_stream_schema(
    *,
    columns: list[Mapping[str, Any]],
    constraints: list[Mapping[str, Any]],
) -> None:
    failures: list[str] = []
    actual_columns: dict[str, Mapping[str, Any]] = {}
    for row in columns:
        name = row.get("column_name")
        if not isinstance(name, str) or name in actual_columns:
            failures.append("column_identity")
            continue
        actual_columns[name] = row
    expected_column_types = {
        "stream_key": "character varying(160)",
        "stream_version": "bigint",
    }
    if set(actual_columns) != set(expected_column_types):
        failures.append("column_set")
    for name, data_type in expected_column_types.items():
        row = actual_columns.get(name)
        if row is None:
            continue
        if row.get("is_not_null") is not True:
            failures.append(f"{name}.not_null")
        if str(row.get("data_type", "")).lower() != data_type:
            failures.append(f"{name}.type")

    actual_constraints: dict[str, Mapping[str, Any]] = {}
    for row in constraints:
        name = row.get("constraint_name")
        if not isinstance(name, str) or name in actual_constraints:
            failures.append("constraint_identity")
            continue
        actual_constraints[name] = row
    if set(actual_constraints) != set(EXPECTED_AUDIT_STREAM_CONSTRAINTS):
        failures.append("constraint_set")
    for name, purpose in EXPECTED_AUDIT_STREAM_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            continue
        if row.get("is_validated") is not True:
            failures.append(f"{name}.validated")
        if row.get("is_deferrable") is not False:
            failures.append(f"{name}.deferrable")
        if row.get("is_initially_deferred") is not False:
            failures.append(f"{name}.initially_deferred")
        columns_tuple = tuple(row.get("constrained_columns") or ())
        definition = " ".join(
            str(row.get("definition") or "").lower().replace('"', "").split()
        )
        if purpose == "check_stream_key":
            stream_key_literals = [
                item.replace("''", "'")
                for item in _SQL_STRING_LITERAL_PATTERN.findall(definition)
            ]
            if row.get("constraint_type") != "c" or columns_tuple != (
                "stream_key",
            ):
                failures.append(f"{name}.shape")
            if (
                not definition.startswith("check")
                or "stream_key" not in definition
                or len(stream_key_literals) != len(EXPECTED_AUDIT_HEAD_IDS)
                or set(stream_key_literals) != set(EXPECTED_AUDIT_HEAD_IDS)
                or " or " in definition
            ):
                failures.append(f"{name}.definition")
        elif purpose == "check_stream_version":
            if row.get("constraint_type") != "c" or columns_tuple != (
                "stream_version",
            ):
                failures.append(f"{name}.shape")
            if (
                not definition.startswith("check")
                or "stream_version" not in definition
                or re.search(r">\s*0", definition) is None
                or " or " in definition
            ):
                failures.append(f"{name}.definition")
        elif purpose == "unique_stream_version":
            if row.get("constraint_type") != "u" or columns_tuple != (
                "stream_key",
                "stream_version",
            ):
                failures.append(f"{name}.shape")
        elif (
            row.get("constraint_type") != "f"
            or columns_tuple != ("stream_key",)
            or row.get("referenced_table") != "audit_chain_heads"
            or tuple(row.get("referenced_columns") or ()) != ("stream_key",)
            or row.get("update_action") != "r"
            or row.get("delete_action") != "r"
        ):
            failures.append(f"{name}.shape")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database audit stream schema guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_stocktake_recount_schema(
    *,
    columns: list[Mapping[str, Any]],
    constraints: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
    triggers: list[Mapping[str, Any]],
) -> None:
    """Prove the recount graph guarded by revisions 0018 through 0024.

    Revision 0024 grants only the INSERT and exact task/round column updates
    required by the mounted recount workflow.  Fail-closed preflights,
    deferred graph triggers and assignment-evidence triggers still own the
    complete and contiguous row-graph enforcement.  Startup verifies those
    catalog objects in the same repeatable-read snapshot as every other
    database-security check.
    """

    failures: list[str] = []
    actual_columns: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in columns:
        table_name = row.get("table_name")
        column_name = row.get("column_name")
        key = (table_name, column_name)
        if (
            not isinstance(table_name, str)
            or not isinstance(column_name, str)
            or key in actual_columns
        ):
            failures.append("column_identity")
            continue
        actual_columns[key] = row
    for key, (is_not_null, data_type) in EXPECTED_STOCKTAKE_RECOUNT_COLUMNS.items():
        row = actual_columns.get(key)
        label = ".".join(key)
        if row is None:
            failures.append(f"{label}.missing")
            continue
        if row.get("table_kind") not in {"r", "p"}:
            failures.append(f"{label}.table_kind")
        if row.get("is_not_null") is not is_not_null:
            failures.append(f"{label}.not_null")
        if str(row.get("data_type", "")).lower() != data_type:
            failures.append(f"{label}.type")

    actual_constraints: dict[str, Mapping[str, Any]] = {}
    for row in constraints:
        name = row.get("constraint_name")
        if not isinstance(name, str) or name in actual_constraints:
            failures.append("constraint_identity")
            continue
        actual_constraints[name] = row
    for name, expected in EXPECTED_STOCKTAKE_RECOUNT_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            failures.append(f"{name}.missing")
            continue
        if row.get("table_name") != expected["table"]:
            failures.append(f"{name}.table")
        if row.get("constraint_type") != expected["type"]:
            failures.append(f"{name}.type")
        if row.get("is_validated") is not True:
            failures.append(f"{name}.validated")
        if row.get("is_deferrable") is not False:
            failures.append(f"{name}.deferrable")
        if row.get("is_initially_deferred") is not False:
            failures.append(f"{name}.initially_deferred")
        definition = " ".join(
            str(row.get("definition") or "").lower().replace('"', "").split()
        )
        if expected["type"] == "c":
            definition_tokens = expected["definition_tokens"]
            if (
                not definition.startswith("check")
                or " or " in definition
                or any(token not in definition for token in definition_tokens)
            ):
                failures.append(f"{name}.definition")
            elif name == "ck_stocktake_recount_cases_round_0018":
                if (
                    re.search(r"next_round_no\s*>\s*1", definition) is None
                    or re.search(r"scope_count\s*>\s*0", definition) is None
                ):
                    failures.append(f"{name}.definition")
            elif definition.count("= 64") < len(definition_tokens):
                failures.append(f"{name}.definition")
            continue
        if expected["type"] == "p":
            if tuple(row.get("constrained_columns") or ()) != expected[
                "columns"
            ]:
                failures.append(f"{name}.shape")
            continue
        if (
            tuple(row.get("constrained_columns") or ())
            != expected["columns"]
            or row.get("referenced_table") != expected["referenced_table"]
            or tuple(row.get("referenced_columns") or ())
            != expected["referenced_columns"]
            or row.get("update_action") != "a"
            or row.get("delete_action") != "r"
        ):
            failures.append(f"{name}.shape")

    actual_indexes: dict[str, Mapping[str, Any]] = {}
    for row in indexes:
        name = row.get("index_name")
        if not isinstance(name, str) or name in actual_indexes:
            failures.append("index_identity")
            continue
        actual_indexes[name] = row
    if set(actual_indexes) != set(EXPECTED_STOCKTAKE_RECOUNT_INDEXES):
        failures.append("index_set")
    for name, expected in EXPECTED_STOCKTAKE_RECOUNT_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        key_columns = tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        )
        if row.get("table_name") != expected["table"]:
            failures.append(f"{name}.table")
        if row.get("access_method") != "btree":
            failures.append(f"{name}.method")
        if any(
            row.get(field) is not True
            for field in ("is_unique", "is_valid", "is_ready", "is_live")
        ):
            failures.append(f"{name}.state")
        if key_columns != expected["columns"]:
            failures.append(f"{name}.columns")
        predicate = " ".join(
            str(row.get("predicate") or "")
            .lower()
            .replace('"', "")
            .split()
        )
        predicate_purpose = expected["predicate"]
        if predicate_purpose is None:
            if predicate:
                failures.append(f"{name}.predicate")
        elif predicate_purpose == "not_null_recount_case":
            if (
                "recount_case_id is not null" not in predicate
                or " or " in predicate
            ):
                failures.append(f"{name}.predicate")
        elif predicate_purpose == "counting":
            literals = [
                item.replace("''", "'")
                for item in _SQL_STRING_LITERAL_PATTERN.findall(predicate)
            ]
            if (
                "status" not in predicate
                or "=" not in predicate
                or literals != ["counting"]
                or " or " in predicate
            ):
                failures.append(f"{name}.predicate")

    _collect_stocktake_recount_trigger_failures(triggers, failures)
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database stocktake recount schema guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _collect_stocktake_recount_trigger_failures(
    rows: list[Mapping[str, Any]],
    failures: list[str],
) -> None:
    actual: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual:
            failures.append("trigger_identity")
            continue
        actual[name] = row
    if set(actual) != set(EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS):
        failures.append("trigger_set")
    for name, (
        table_name,
        function_name,
        enabled,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    ) in EXPECTED_STOCKTAKE_RECOUNT_TRIGGERS.items():
        row = actual.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        if row.get("is_constraint_trigger") is not is_constraint_trigger:
            failures.append(f"{name}.constraint")
        if row.get("is_deferrable") is not is_deferrable:
            failures.append(f"{name}.deferrable")
        if row.get("is_initially_deferred") is not is_initially_deferred:
            failures.append(f"{name}.initially_deferred")
        if row.get("has_when_clause") is not False:
            failures.append(f"{name}.when")
        if row.get("has_column_filter") is not False:
            failures.append(f"{name}.columns")
        if (
            name == STOCKTAKE_DIFFERENCE_COMPLETION_TRIGGER_0031
            and row.get("argument_count") != 0
        ):
            failures.append(f"{name}.arguments")


def _assert_stocktake_scope_triggers(
    rows: list[Mapping[str, Any]],
) -> None:
    """Prove the complete trigger catalog on immutable scope facts."""

    failures: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual:
            failures.append("trigger_identity")
            continue
        actual[name] = row
    if set(actual) != set(EXPECTED_STOCKTAKE_SCOPE_TRIGGERS):
        failures.append("trigger_set")
    for name, (
        table_name,
        function_name,
        enabled,
        trigger_type,
        is_constraint_trigger,
        is_deferrable,
        is_initially_deferred,
    ) in EXPECTED_STOCKTAKE_SCOPE_TRIGGERS.items():
        row = actual.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        if row.get("is_constraint_trigger") is not is_constraint_trigger:
            failures.append(f"{name}.constraint")
        if row.get("is_deferrable") is not is_deferrable:
            failures.append(f"{name}.deferrable")
        if row.get("is_initially_deferred") is not is_initially_deferred:
            failures.append(f"{name}.initially_deferred")
        if row.get("has_when_clause") is not False:
            failures.append(f"{name}.when")
        if row.get("has_column_filter") is not False:
            failures.append(f"{name}.columns")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database stocktake scope trigger guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_reconciliation_triggers(
    rows: list[Mapping[str, Any]],
) -> None:
    """Prove append-only and terminal reconciliation trigger coverage."""

    failures: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual:
            failures.append("trigger_identity")
            continue
        actual[name] = row
    if set(actual) != set(EXPECTED_RECONCILIATION_TRIGGERS):
        failures.append("trigger_set")
    for name, (
        table_name,
        function_name,
        enabled,
        trigger_type,
    ) in EXPECTED_RECONCILIATION_TRIGGERS.items():
        row = actual.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        for field in (
            "is_constraint_trigger",
            "is_deferrable",
            "is_initially_deferred",
            "has_when_clause",
            "has_column_filter",
        ):
            if row.get(field) is not False:
                failures.append(f"{name}.{field}")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database reconciliation trigger guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_reconciliation_schema(
    *,
    constraints: list[Mapping[str, Any]],
    partial_indexes: list[Mapping[str, Any]],
) -> None:
    failures: list[str] = []
    actual_constraints = {
        str(row.get("constraint_name")): row for row in constraints
    }
    if set(actual_constraints) != set(EXPECTED_RECONCILIATION_CONSTRAINTS):
        failures.append("constraint_set")
    for name, expected in EXPECTED_RECONCILIATION_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            continue
        if row.get("table_name") != expected["table"]:
            failures.append(f"{name}.table")
        if row.get("constraint_type") != expected["type"]:
            failures.append(f"{name}.type")
        if row.get("is_validated") is not True:
            failures.append(f"{name}.validated")
        columns = tuple(row.get("constrained_columns") or ())
        if expected["type"] in {"f", "u"} and columns != expected["columns"]:
            failures.append(f"{name}.columns")
        if expected["type"] == "f":
            if (
                row.get("referenced_table") != expected["referenced_table"]
                or tuple(row.get("referenced_columns") or ())
                != expected["referenced_columns"]
                or row.get("update_action") != "a"
                or row.get("delete_action") != "a"
                or row.get("is_deferrable") is not expected["deferred"]
                or row.get("is_initially_deferred") is not expected["deferred"]
            ):
                failures.append(f"{name}.shape")
        else:
            if row.get("is_deferrable") is not False:
                failures.append(f"{name}.deferrable")
            if row.get("is_initially_deferred") is not False:
                failures.append(f"{name}.initially_deferred")
        if expected["type"] == "c":
            definition = " ".join(
                str(row.get("definition") or "")
                .lower()
                .replace('"', "")
                .split()
            )
            if not definition.startswith("check") or any(
                token not in definition for token in expected["tokens"]
            ):
                failures.append(f"{name}.definition")

    actual_indexes = {
        str(row.get("index_name")): row for row in partial_indexes
    }
    if set(actual_indexes) != set(EXPECTED_RECONCILIATION_PARTIAL_INDEXES):
        failures.append("partial_index_set")
    for name, operation in EXPECTED_RECONCILIATION_PARTIAL_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        predicate = " ".join(
            str(row.get("predicate") or "")
            .lower()
            .replace('"', "")
            .split()
        )
        if (
            row.get("table_name") != "reconciliation_commands"
            or row.get("access_method") != "btree"
            or tuple(row.get("key_columns") or ()) != ("run_id",)
            or any(
                row.get(field) is not True
                for field in ("is_unique", "is_valid", "is_ready", "is_live")
            )
            or operation not in predicate
            or " or " in predicate
        ):
            failures.append(f"{name}.shape")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database reconciliation schema guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_opening_terminal_triggers(
    rows: list[Mapping[str, Any]],
) -> None:
    failures: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual:
            failures.append("trigger_identity")
            continue
        actual[name] = row
    if set(actual) != set(EXPECTED_OPENING_TERMINAL_TRIGGERS):
        failures.append("trigger_set")
    for name, (
        table_name,
        function_name,
        enabled,
        trigger_type,
    ) in EXPECTED_OPENING_TERMINAL_TRIGGERS.items():
        row = actual.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if row.get("table_schema") != "public":
            failures.append(f"{name}.table_schema")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        is_commit_guard = name in OPENING_COMMIT_TRIGGER_NAMES
        for field in (
            "is_constraint_trigger",
            "is_deferrable",
            "is_initially_deferred",
        ):
            if row.get(field) is not is_commit_guard:
                failures.append(f"{name}.{field}")
        for field in ("has_when_clause", "has_column_filter"):
            if row.get(field) is not False:
                failures.append(f"{name}.{field}")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database opening terminal trigger guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_opening_terminal_index(rows: list[Mapping[str, Any]]) -> None:
    failures: list[str] = []
    if len(rows) != 1:
        failures.append("index_set")
        row: Mapping[str, Any] | None = None
    else:
        row = rows[0]
    if row is not None:
        if row.get("index_name") != EXPECTED_OPENING_TERMINAL_INDEX:
            failures.append("index_name")
        if row.get("table_name") != "stocktake_postings":
            failures.append("table")
        if row.get("access_method") != "btree":
            failures.append("method")
        if any(
            row.get(field) is not True
            for field in ("is_unique", "is_valid", "is_ready", "is_live")
        ):
            failures.append("state")
        if tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        ) != ("task_id",):
            failures.append("columns")
        predicate = " ".join(
            str(row.get("predicate") or "").lower().replace('"', "").split()
        )
        literals = [
            item.replace("''", "'")
            for item in _SQL_STRING_LITERAL_PATTERN.findall(predicate)
        ]
        if (
            "posting_kind" not in predicate
            or "=" not in predicate
            or literals != ["opening"]
            or " or " in predicate
        ):
            failures.append("predicate")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database opening terminal unique index failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_formal_file_guards(
    *,
    triggers: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
) -> None:
    """Prove the exact formal-file mutation and single-use evidence guards."""

    failures: list[str] = []
    actual_triggers: dict[str, Mapping[str, Any]] = {}
    for row in triggers:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual_triggers:
            failures.append("trigger_identity")
            continue
        actual_triggers[name] = row
    if set(actual_triggers) != set(EXPECTED_FORMAL_FILE_TRIGGERS):
        failures.append("trigger_set")
    for name, (table_name, function_name, enabled, trigger_type) in (
        EXPECTED_FORMAL_FILE_TRIGGERS.items()
    ):
        row = actual_triggers.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        for field in (
            "is_constraint_trigger",
            "is_deferrable",
            "is_initially_deferred",
            "has_when_clause",
            "has_column_filter",
        ):
            if row.get(field) is not False:
                failures.append(f"{name}.{field}")

    actual_indexes: dict[str, Mapping[str, Any]] = {}
    for row in indexes:
        name = row.get("index_name")
        if not isinstance(name, str) or name in actual_indexes:
            failures.append("index_identity")
            continue
        actual_indexes[name] = row
    if set(actual_indexes) != set(EXPECTED_FORMAL_FILE_INDEXES):
        failures.append("index_set")
    for name, expected in EXPECTED_FORMAL_FILE_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        if row.get("table_name") != expected["table"]:
            failures.append(f"{name}.table")
        if row.get("access_method") != "btree":
            failures.append(f"{name}.method")
        if any(
            row.get(field) is not True
            for field in ("is_unique", "is_valid", "is_ready", "is_live")
        ):
            failures.append(f"{name}.state")
        if tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        ) != expected["columns"]:
            failures.append(f"{name}.columns")
        predicate = " ".join(
            str(row.get("predicate") or "")
            .lower()
            .replace('"', "")
            .split()
        )
        if expected["predicate"] is None:
            if predicate:
                failures.append(f"{name}.predicate")
        elif (
            "document_type" not in predicate
            or "stocktake_scope_count_completion" not in predicate
            or "attachment_type" not in predicate
            or "stocktake_evidence" not in predicate
            or "status" in predicate
            or " or " in predicate
        ):
            failures.append(f"{name}.predicate")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database formal file guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_material_request_approval_guards(
    *,
    triggers: list[Mapping[str, Any]],
    functions: list[Mapping[str, Any]],
    manifest_columns: list[Mapping[str, Any]],
    manifest_checks: list[Mapping[str, Any]],
    expected_migration_role: str,
) -> None:
    """Prove the complete 0029/0030/0045/0046 request guard catalog."""

    failures: list[str] = []
    actual_triggers: dict[str, Mapping[str, Any]] = {}
    for row in triggers:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual_triggers:
            failures.append("trigger_identity")
            continue
        actual_triggers[name] = row
    if set(actual_triggers) != set(EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS):
        failures.append("trigger_set")
    for name, expected in EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS.items():
        row = actual_triggers.get(name)
        if row is None:
            continue
        (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
        ) = expected
        expected_values = {
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
        for field, value in expected_values.items():
            if row.get(field) != value:
                failures.append(f"{name}.{field}")

    actual_functions: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in functions:
        name = row.get("function_name")
        argument_types = str(row.get("argument_types") or "")
        coordinate = (name, argument_types)
        if (
            not isinstance(name, str)
            or coordinate in actual_functions
        ):
            failures.append("function_identity")
            continue
        actual_functions[coordinate] = row
    if set(actual_functions) != set(MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256):
        failures.append("function_set")
    for coordinate, expected_body_hash in (
        MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256.items()
    ):
        row = actual_functions.get(coordinate)
        if row is None:
            continue
        label = coordinate[0]
        expected_values = {
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
            "volatility": "v",
            "parallel_safety": "u",
            "is_leakproof": False,
            "is_security_definer": (
                coordinate in MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
            ),
            "language_name": "plpgsql",
            "configuration": ("search_path=pg_catalog, public",),
            "owner_name": expected_migration_role,
            "can_execute": False,
            "api_execute_is_grantable": False,
            "unexpected_execute_grantee_count": 0,
            "public_can_execute": False,
            "edge_can_execute": False,
            "backup_can_execute": False,
            "edge_receiver_can_execute": False,
            "projector_can_execute": False,
        }
        for field, value in expected_values.items():
            actual = tuple(row.get(field) or ()) if field == "configuration" else row.get(field)
            if actual != value:
                failures.append(f"{label}.{field}")
        source_body = row.get("source_body")
        if (
            not isinstance(source_body, str)
            or hashlib.sha256(source_body.encode("utf-8")).hexdigest()
            != expected_body_hash
        ):
            failures.append(f"{label}.body")

    if len(manifest_columns) != 1:
        failures.append("projection_manifest_sha256.column_set")
    manifest_column = manifest_columns[0] if len(manifest_columns) == 1 else None
    if manifest_column is not None:
        for field, expected_value in (
            EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN.items()
        ):
            actual_value = manifest_column.get(field)
            if field == "default_expression" and actual_value == "":
                actual_value = None
            if (
                actual_value is not expected_value
                if field == "is_not_null"
                else actual_value != expected_value
            ):
                failures.append(f"projection_manifest_sha256.{field}")

    if len(manifest_checks) != 1:
        failures.append("projection_manifest_0046.constraint_set")
    manifest_check = manifest_checks[0] if len(manifest_checks) == 1 else None
    if manifest_check is not None:
        for field, expected_value in (
            EXPECTED_MATERIAL_REQUEST_CONTENT_MANIFEST_CHECK.items()
        ):
            actual_value = manifest_check.get(field)
            if field == "constrained_columns":
                actual_value = tuple(actual_value or ())
            if actual_value != expected_value:
                failures.append(f"projection_manifest_0046.{field}")
        for field, expected_value in {
            "inheritance_count": 0,
            "parent_constraint_id": 0,
            "backing_index_name": None,
        }.items():
            actual_value = manifest_check.get(field)
            if field == "backing_index_name" and actual_value == "":
                actual_value = None
            if actual_value != expected_value:
                failures.append(f"projection_manifest_0046.{field}")
        for field, expected_value in {
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": False,
            "is_local": True,
        }.items():
            if manifest_check.get(field) is not expected_value:
                failures.append(f"projection_manifest_0046.{field}")
        if not _material_request_content_check_definition_matches(
            manifest_check.get("definition")
        ):
            failures.append("projection_manifest_0046.definition")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database material-request approval guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _material_request_content_check_definition_matches(value: object) -> bool:
    """Accept only PostgreSQL's exact IN/ANY renderings of the 0046 CHECK."""

    if not isinstance(value, str) or not value or '"' in value:
        return False
    if tuple(_SQL_STRING_LITERAL_PATTERN.findall(value)) != (
        "create",
        "update_draft",
        "submit",
        "^[0-9a-f]{64}$",
        "create",
        "update_draft",
        "submit",
    ):
        return False
    normalized = _lower_sql_outside_string_literals(value)
    normalized = re.sub(
        r"::\s*(?:character\s+varying|varchar|text)"
        r"(?:\s*\(\s*\d+\s*\))?(?:\s*\[\s*\])?",
        "",
        normalized,
    )
    compact = re.sub(
        r"\s+",
        "",
        normalized.replace("(", "").replace(")", ""),
    )
    positive_in = "operationin'create','update_draft','submit'"
    positive_any = "operation=anyarray['create','update_draft','submit']"
    negative_in = "operationnotin'create','update_draft','submit'"
    negative_not_any = "notoperation=anyarray['create','update_draft','submit']"
    negative_all = "operation<>allarray['create','update_draft','submit']"
    suffix = (
        "andprojection_manifest_sha256isnotnull"
        "andprojection_manifest_sha256~'^[0-9a-f]{64}$'or"
    )
    null_suffix = "andprojection_manifest_sha256isnull"
    return compact in {
        f"check{positive}{suffix}{negative}{null_suffix}"
        for positive in (positive_in, positive_any)
        for negative in (negative_in, negative_not_any, negative_all)
    }


def _assert_material_request_cancellation_guards(
    *,
    triggers: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
) -> None:
    """Prove direct-cancel parent locks, deferred graph and fact indexes."""

    failures: list[str] = []
    actual_triggers: dict[str, Mapping[str, Any]] = {}
    for row in triggers:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual_triggers:
            failures.append("trigger_identity")
            continue
        actual_triggers[name] = row
    if set(actual_triggers) != set(EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS):
        failures.append("trigger_set")
    for name, (table_name, function_name, enabled, trigger_type) in (
        EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS.items()
    ):
        row = actual_triggers.get(name)
        if row is None:
            continue
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        is_deferred = function_name == (
            "rsc_require_material_request_cancellation_graph_0037"
        )
        for field in (
            "is_constraint_trigger",
            "is_deferrable",
            "is_initially_deferred",
        ):
            if row.get(field) is not is_deferred:
                failures.append(f"{name}.{field}")
        for field in ("has_when_clause", "has_column_filter"):
            if row.get(field) is not False:
                failures.append(f"{name}.{field}")

    actual_indexes: dict[str, Mapping[str, Any]] = {}
    for row in indexes:
        name = row.get("index_name")
        if not isinstance(name, str) or name in actual_indexes:
            failures.append("index_identity")
            continue
        actual_indexes[name] = row
    if set(actual_indexes) != set(EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES):
        failures.append("index_set")
    for name, expected in EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        if row.get("table_name") != expected["table"]:
            failures.append(f"{name}.table")
        if row.get("access_method") != "btree":
            failures.append(f"{name}.method")
        if row.get("is_unique") is not expected["unique"]:
            failures.append(f"{name}.unique")
        if any(
            row.get(field) is not True
            for field in ("is_valid", "is_ready", "is_live")
        ):
            failures.append(f"{name}.state")
        if tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        ) != expected["columns"]:
            failures.append(f"{name}.columns")
        if row.get("predicate") not in (None, ""):
            failures.append(f"{name}.predicate")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database material request cancellation guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_material_request_command_recovery_index(
    rows: list[Mapping[str, Any]],
) -> None:
    """Prove the exact 0039 X-Request-ID lifecycle recovery sentinel."""

    expected = EXPECTED_MATERIAL_REQUEST_COMMAND_RECOVERY_INDEX
    failures: list[str] = []
    if len(rows) != 1:
        failures.append("index_set")
    row = rows[0] if len(rows) == 1 else None
    if row is not None:
        if row.get("index_name") != expected["name"]:
            failures.append("index_name")
        if row.get("table_name") != expected["table"]:
            failures.append("table")
        if row.get("access_method") != "btree":
            failures.append("method")
        if row.get("is_unique") is not True:
            failures.append("unique")
        if any(
            row.get(field) is not True
            for field in ("is_valid", "is_ready", "is_live")
        ):
            failures.append("state")
        if tuple(
            str(value).lower() for value in (row.get("key_columns") or ())
        ) != expected["columns"]:
            failures.append("columns")
        raw_predicate = str(row.get("predicate") or "")
        literal_sequence = tuple(
            _SQL_STRING_LITERAL_PATTERN.findall(raw_predicate)
        )
        predicate = " ".join(
            _lower_sql_outside_string_literals(raw_predicate).split()
        )
        predicate_without_text_casts = re.sub(
            r"::\s*(?:character\s+varying|varchar|text)(?:\s*\[\s*\])?",
            "",
            predicate,
        )
        # ``pg_get_expr`` may add redundant parentheses and varchar/text casts,
        # but every other character is semantically relevant.  Compare the
        # complete remaining stream so a hidden function, operator, array
        # slice or extra narrowing condition can never pass by being skipped by
        # a permissive tokenizer.
        compact_predicate = re.sub(
            r"\s+",
            "",
            predicate_without_text_casts.replace("(", "").replace(")", ""),
        )
        exact_in_predicate = (
            "stream_key='material_request'andactionin"
            "'material_request.withdraw','material_request.cancel'"
        )
        exact_any_predicate = (
            "stream_key='material_request'andaction=anyarray["
            "'material_request.withdraw','material_request.cancel']"
        )
        if (
            literal_sequence
            != (
                "material_request",
                "material_request.withdraw",
                "material_request.cancel",
            )
            or compact_predicate
            not in (exact_in_predicate, exact_any_predicate)
        ):
            failures.append("predicate")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database material request command recovery index failed: "
            + ", ".join(sorted(set(failures)))
        )


def _lower_sql_outside_string_literals(value: str) -> str:
    """Normalize SQL syntax without changing case-sensitive string values."""

    output: list[str] = []
    index = 0
    in_literal = False
    while index < len(value):
        character = value[index]
        if character == "'":
            output.append(character)
            if in_literal and index + 1 < len(value) and value[index + 1] == "'":
                output.append("'")
                index += 2
                continue
            in_literal = not in_literal
        else:
            output.append(character if in_literal else character.lower())
        index += 1
    return "".join(output)


def _assert_kms_data_key_pin_guards(
    *,
    triggers: list[Mapping[str, Any]],
    columns: list[Mapping[str, Any]],
    constraints: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
    table_acl: list[Mapping[str, Any]],
    function_acl: list[Mapping[str, Any]],
    expected_runtime_role: str,
    expected_migration_role: str,
) -> None:
    """Prove the exact migration-owned immutable KMS fingerprint ledger."""

    failures: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}
    for row in triggers:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual:
            failures.append("trigger_identity")
            continue
        actual[name] = row
    if set(actual) != set(EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS):
        failures.append("trigger_set")
    for name, expected in EXPECTED_KMS_DATA_KEY_PIN_TRIGGERS.items():
        row = actual.get(name)
        if row is None:
            continue
        table_name, function_name, enabled, trigger_type = expected
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if (
            row.get("function_schema") != "public"
            or row.get("function_name") != function_name
        ):
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        for field in (
            "is_constraint_trigger",
            "is_deferrable",
            "is_initially_deferred",
            "has_when_clause",
            "has_column_filter",
        ):
            if row.get(field) is not False:
                failures.append(f"{name}.{field}")

    if len(columns) != len(EXPECTED_KMS_DATA_KEY_PIN_COLUMNS):
        failures.append("column_set")
    for ordinal, expected in enumerate(
        EXPECTED_KMS_DATA_KEY_PIN_COLUMNS,
        start=1,
    ):
        row = columns[ordinal - 1] if len(columns) >= ordinal else None
        if row is None:
            continue
        if (
            row.get("relation_kind") != "r"
            or row.get("persistence") != "p"
            or row.get("row_security") is not False
            or row.get("force_row_security") is not False
        ):
            failures.append("table_shape")
        if (
            row.get("ordinal_position") != ordinal
            or row.get("column_name") != expected[0]
            or row.get("data_type") != expected[1]
            or row.get("is_not_null") is not True
            or row.get("identity_kind") != ""
            or row.get("generated_kind") != ""
            or row.get("default_expression") not in (None, "")
        ):
            failures.append(f"column_{ordinal}")

    actual_constraints: dict[str, Mapping[str, Any]] = {}
    for row in constraints:
        name = row.get("constraint_name")
        if not isinstance(name, str) or name in actual_constraints:
            failures.append("constraint_identity")
            continue
        actual_constraints[name] = row
    if set(actual_constraints) != set(EXPECTED_KMS_DATA_KEY_PIN_CONSTRAINTS):
        failures.append("constraint_set")
    for name, expected in EXPECTED_KMS_DATA_KEY_PIN_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            continue
        equality_fields = {
            "constraint_type": expected["type"],
            "inheritance_count": 0,
            "parent_constraint_id": 0,
            "backing_index_name": expected["backing_index"],
        }
        for field, expected_value in equality_fields.items():
            if row.get(field) != expected_value:
                failures.append(f"{name}.{field}")
        identity_fields = {
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": expected["no_inherit"],
            "is_local": True,
        }
        for field, expected_value in identity_fields.items():
            if row.get(field) is not expected_value:
                failures.append(f"{name}.{field}")
        if tuple(row.get("constrained_columns") or ()) != expected["columns"]:
            failures.append(f"{name}.constrained_columns")
        if expected["type"] == "c" and not _kms_pin_check_definition_matches(
            name,
            row.get("definition"),
        ):
            failures.append(f"{name}.definition")

    actual_indexes: dict[str, Mapping[str, Any]] = {}
    for row in indexes:
        name = row.get("index_name")
        if not isinstance(name, str) or name in actual_indexes:
            failures.append("index_identity")
            continue
        actual_indexes[name] = row
    if set(actual_indexes) != set(EXPECTED_KMS_DATA_KEY_PIN_INDEXES):
        failures.append("index_set")
    for name, expected in EXPECTED_KMS_DATA_KEY_PIN_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        expected_columns = expected["columns"]
        equality_fields = {
            "owner_name": expected_migration_role,
            "constraint_name": expected["constraint"],
            "access_method": "btree",
            "key_attribute_count": len(expected_columns),
            "total_attribute_count": len(expected_columns),
        }
        for field, expected_value in equality_fields.items():
            if row.get(field) != expected_value:
                failures.append(f"{name}.{field}")
        identity_fields = {
            "is_unique": True,
            "is_primary": expected["primary"],
            "is_exclusion": False,
            "is_immediate": True,
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "nulls_not_distinct": False,
            "has_expressions": False,
        }
        for field, expected_value in identity_fields.items():
            if row.get(field) is not expected_value:
                failures.append(f"{name}.{field}")
        if tuple(row.get("key_columns") or ()) != expected_columns:
            failures.append(f"{name}.key_columns")
        if row.get("predicate") not in (None, ""):
            failures.append(f"{name}.predicate")

    _assert_kms_pin_acl_rows(
        rows=table_acl,
        expected_runtime_role=expected_runtime_role,
        expected_migration_role=expected_migration_role,
        table=True,
        failures=failures,
    )
    _assert_kms_pin_acl_rows(
        rows=function_acl,
        expected_runtime_role=expected_runtime_role,
        expected_migration_role=expected_migration_role,
        table=False,
        failures=failures,
    )
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database KMS data-key pin guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_sms_dispatch_guards(
    *,
    triggers: list[Mapping[str, Any]],
    columns: list[Mapping[str, Any]],
    constraints: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
    table_acl: list[Mapping[str, Any]],
    column_acl: list[Mapping[str, Any]],
    role_access: list[Mapping[str, Any]],
    expected_runtime_role: str,
    expected_migration_role: str,
) -> None:
    """Prove the exact single-owner SMS provider side-effect ledger."""

    failures: list[str] = []
    actual_triggers = {
        row.get("trigger_name"): row
        for row in triggers
        if isinstance(row.get("trigger_name"), str)
    }
    if (
        len(actual_triggers) != len(triggers)
        or set(actual_triggers) != set(EXPECTED_SMS_DISPATCH_TRIGGERS)
    ):
        failures.append("trigger_set")
    for name, expected in EXPECTED_SMS_DISPATCH_TRIGGERS.items():
        row = actual_triggers.get(name)
        if row is None:
            continue
        table_name, function_name, enabled, trigger_type = expected
        if (
            row.get("table_name") != table_name
            or row.get("function_schema") != "public"
            or row.get("function_name") != function_name
            or row.get("enabled") != enabled
            or row.get("trigger_type") != trigger_type
            or any(
                row.get(field) is not False
                for field in (
                    "is_constraint_trigger",
                    "is_deferrable",
                    "is_initially_deferred",
                    "has_when_clause",
                    "has_column_filter",
                )
            )
        ):
            failures.append(f"{name}.shape")

    if len(columns) != len(EXPECTED_SMS_DISPATCH_COLUMNS):
        failures.append("column_set")
    for ordinal, expected in enumerate(EXPECTED_SMS_DISPATCH_COLUMNS, start=1):
        row = columns[ordinal - 1] if len(columns) >= ordinal else None
        if row is None:
            continue
        column_name, data_type, is_not_null = expected
        if (
            row.get("ordinal_position") != ordinal
            or row.get("column_name") != column_name
            or row.get("data_type") != data_type
            or row.get("is_not_null") is not is_not_null
            or row.get("identity_kind") not in (None, "")
            or row.get("generated_kind") not in (None, "")
            or row.get("default_expression") is not None
            or row.get("relation_kind") != "r"
            or row.get("persistence") != "p"
            or row.get("row_security") is not False
            or row.get("force_row_security") is not False
        ):
            failures.append(f"{column_name}.shape")

    actual_constraints = {
        row.get("constraint_name"): row
        for row in constraints
        if isinstance(row.get("constraint_name"), str)
    }
    if (
        len(actual_constraints) != len(constraints)
        or set(actual_constraints) != set(EXPECTED_SMS_DISPATCH_CONSTRAINTS)
    ):
        failures.append("constraint_set")
    definition_tokens = {
        "ck_sms_challenge_dispatches_status": (
            "prepared", "sending", "accepted", "uncertain", "expired",
        ),
        "ck_sms_challenge_dispatches_request_sha256": ("request_sha256", "64"),
        "ck_sms_challenge_dispatches_mobile_hash": ("mobile_hash", "64"),
        "ck_sms_challenge_dispatches_owner_hash": ("owner_token_hash", "64"),
        "ck_sms_challenge_dispatches_state_evidence": (
            "owner_token_hash",
            "provider_reference",
            "claimed_at",
            "lease_expires_at",
            "accepted_at",
            "uncertain_at",
            "expired_at",
        ),
        "ck_sms_challenge_dispatches_lease_order": (
            "lease_expires_at", "claimed_at",
        ),
        "ck_sms_challenge_dispatches_accepted_order": (
            "accepted_at", "claimed_at",
        ),
        "ck_sms_challenge_dispatches_uncertain_order": (
            "uncertain_at", "claimed_at",
        ),
        "ck_sms_challenge_dispatches_expired_order": (
            "expired_at", "claimed_at",
        ),
    }
    for name, expected_type in EXPECTED_SMS_DISPATCH_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            continue
        definition = str(row.get("definition") or "").lower()
        equality_fields = {
            "constraint_type": expected_type,
            "inheritance_count": 0,
            "parent_constraint_id": 0,
        }
        for field, expected_value in equality_fields.items():
            if row.get(field) != expected_value:
                failures.append(f"{name}.{field}")
        identity_fields = {
            "is_validated": True,
            "is_deferrable": False,
            "is_initially_deferred": False,
            "is_no_inherit": (
                name in EXPECTED_SMS_DISPATCH_NONINHERIT_CONSTRAINTS
            ),
            "is_local": True,
        }
        for field, expected_value in identity_fields.items():
            if row.get(field) is not expected_value:
                failures.append(f"{name}.{field}")
        if any(
            token not in definition
            for token in definition_tokens.get(name, ())
        ):
            failures.append(f"{name}.definition")
    primary = actual_constraints.get("pk_sms_challenge_dispatches_0041")
    if primary is not None and tuple(primary.get("constrained_columns") or ()) != (
        "challenge_id",
    ):
        failures.append("primary.columns")
    foreign = actual_constraints.get("fk_sms_challenge_dispatches_challenge_0041")
    if foreign is not None and (
        tuple(foreign.get("constrained_columns") or ()) != ("challenge_id",)
        or foreign.get("referenced_table") != "login_challenges"
        or tuple(foreign.get("referenced_columns") or ()) != ("id",)
        or foreign.get("delete_action") != "r"
    ):
        failures.append("foreign.shape")

    actual_indexes = {
        row.get("index_name"): row
        for row in indexes
        if isinstance(row.get("index_name"), str)
    }
    if (
        len(actual_indexes) != len(indexes)
        or set(actual_indexes) != set(EXPECTED_SMS_DISPATCH_INDEXES)
    ):
        failures.append("index_set")
    for name, expected in EXPECTED_SMS_DISPATCH_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        key_columns = tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        )
        if (
            row.get("owner_name") != expected_migration_role
            or row.get("access_method") != "btree"
            or row.get("is_unique") is not expected["unique"]
            or row.get("is_primary") is not expected["primary"]
            or row.get("is_exclusion") is not False
            or row.get("is_immediate") is not True
            or row.get("is_valid") is not True
            or row.get("is_ready") is not True
            or row.get("is_live") is not True
            or row.get("nulls_not_distinct") is not False
            or row.get("has_expressions") is not False
            or row.get("key_attribute_count") != len(expected["columns"])
            or row.get("total_attribute_count") != len(expected["columns"])
            or key_columns != expected["columns"]
            or not _sms_dispatch_predicate_matches(expected, row.get("predicate"))
        ):
            failures.append(f"{name}.shape")

    table_role_evidence = _assert_sms_dispatch_acl_rows(
        rows=table_acl,
        label="table_acl",
        expected_migration_role=expected_migration_role,
        failures=failures,
    )
    column_role_evidence = _assert_sms_dispatch_acl_rows(
        rows=column_acl,
        label="column_acl",
        expected_migration_role=expected_migration_role,
        failures=failures,
    )
    if (
        table_role_evidence is not None
        and column_role_evidence is not None
        and table_role_evidence != column_role_evidence
    ):
        failures.append("acl.role_evidence")
    effective_role_evidence = _assert_sms_dispatch_role_access(
        rows=role_access,
        expected_runtime_role=expected_runtime_role,
        expected_migration_role=expected_migration_role,
        failures=failures,
    )
    if (
        table_role_evidence is not None
        and effective_role_evidence is not None
        and table_role_evidence != effective_role_evidence
    ):
        failures.append("acl.effective_role_evidence")

    table_grants = [
        (
            row.get("grantee_name"),
            row.get("privilege_type"),
            row.get("is_grantable"),
        )
        for row in table_acl
        if any(
            row.get(field) is not None
            for field in ("grantee_name", "privilege_type", "is_grantable")
        )
    ]
    expected_table_grants = {
        (expected_runtime_role, privilege, False)
        for privilege in EXPECTED_SMS_DISPATCH_TABLE_PRIVILEGES
    }
    if (
        table_role_evidence is not None
        and table_role_evidence[0] is True
    ):
        expected_table_grants.add(("star_oam_backup", "SELECT", False))
    if (
        len(table_grants) != len(set(table_grants))
        or set(table_grants) != expected_table_grants
    ):
        failures.append("table_acl.grant_set")

    column_grants = [
        (
            row.get("column_name"),
            row.get("grantee_name"),
            row.get("privilege_type"),
            row.get("is_grantable"),
        )
        for row in column_acl
        if any(
            row.get(field) is not None
            for field in (
                "column_name",
                "grantee_name",
                "privilege_type",
                "is_grantable",
            )
        )
    ]
    expected_column_grants = {
        (column_name, expected_runtime_role, "UPDATE", False)
        for column_name in EXPECTED_SMS_DISPATCH_UPDATE_COLUMNS
    }
    if (
        len(column_grants) != len(set(column_grants))
        or set(column_grants) != expected_column_grants
    ):
        failures.append("column_acl.grant_set")

    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database SMS dispatch guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _sms_dispatch_predicate_matches(
    expected: Mapping[str, Any],
    value: object,
) -> bool:
    raw = "" if value is None else str(value)
    normalized = " ".join(
        _lower_sql_outside_string_literals(raw)
        .replace("(", " ")
        .replace(")", " ")
        .split()
    )
    normalized = re.sub(
        r"::\s*(?:character\s+varying|varchar|text)",
        "",
        normalized,
    )
    if expected.get("predicate") is not None:
        return normalized == expected["predicate"]
    literals = expected.get("predicate_literals")
    if literals is not None:
        predicate_without_text_casts = re.sub(
            r"::\s*(?:character\s+varying|varchar|text)(?:\s*\[\s*\])?",
            "",
            _lower_sql_outside_string_literals(raw),
        )
        compact_predicate = re.sub(
            r"\s+",
            "",
            predicate_without_text_casts.replace("(", "").replace(")", ""),
        )
        return tuple(_SQL_STRING_LITERAL_PATTERN.findall(raw)) == literals and (
            compact_predicate
            in (
                "statusin'sending','uncertain'",
                "status=anyarray['sending','uncertain']",
            )
        )
    return normalized == ""


def _assert_sms_dispatch_acl_rows(
    *,
    rows: list[Mapping[str, Any]],
    label: str,
    expected_migration_role: str,
    failures: list[str],
) -> tuple[object, object] | None:
    if not rows:
        failures.append(f"{label}.missing")
        return None
    backup_exists = rows[0].get("backup_role_exists")
    edge_exists = rows[0].get("edge_role_exists")
    if type(backup_exists) is not bool or type(edge_exists) is not bool:
        failures.append(f"{label}.role_evidence")
    for row in rows:
        if (
            row.get("owner_name") != expected_migration_role
            or row.get("backup_role_exists") is not backup_exists
            or row.get("edge_role_exists") is not edge_exists
        ):
            failures.append(f"{label}.owner_or_roles")
    return backup_exists, edge_exists


def _assert_sms_dispatch_role_access(
    *,
    rows: list[Mapping[str, Any]],
    expected_runtime_role: str,
    expected_migration_role: str,
    failures: list[str],
) -> tuple[object, object] | None:
    """Reject effective SMS access gained through any reachable role."""

    expected_names = {
        "backup": "star_oam_backup",
        "edge": "star_oam_edge",
        "runtime": expected_runtime_role,
        "migration": expected_migration_role,
    }
    actual_rows = {
        row.get("role_label"): row
        for row in rows
        if isinstance(row.get("role_label"), str)
    }
    if (
        len(actual_rows) != len(rows)
        or set(actual_rows) != set(expected_names)
    ):
        failures.append("role_access.role_set")

    boolean_fields = (
        "role_exists",
        "role_inherits",
        "can_select",
        "can_write",
        "is_member_of_any_role",
        "has_any_nonsuper_member",
        "can_set_select_role",
        "can_set_write_role",
        "can_admin_select_role",
        "can_admin_write_role",
        "inherited_by_other_role",
        "settable_by_other_role",
        "administered_by_other_role",
        "capability_role_has_admin_member",
    )
    for label, expected_name in expected_names.items():
        row = actual_rows.get(label)
        if row is None:
            continue
        if row.get("role_name") != expected_name or any(
            type(row.get(field)) is not bool for field in boolean_fields
        ):
            failures.append(f"role_access.{label}.evidence")

    backup = actual_rows.get("backup")
    edge = actual_rows.get("edge")
    runtime = actual_rows.get("runtime")
    migration = actual_rows.get("migration")

    for label, row in (("runtime", runtime), ("migration", migration)):
        if row is not None and (
            row.get("role_exists") is not True
            or row.get("is_member_of_any_role") is not False
            or row.get("has_any_nonsuper_member") is not False
            or row.get("can_set_select_role") is not False
            or row.get("can_set_write_role") is not False
            or row.get("inherited_by_other_role") is not False
            or row.get("settable_by_other_role") is not False
            or row.get("administered_by_other_role") is not False
            or row.get("can_admin_select_role") is not False
            or row.get("can_admin_write_role") is not False
            or row.get("capability_role_has_admin_member") is not False
        ):
            failures.append(f"role_access.{label}.closure")

    if backup is not None:
        backup_exists = backup.get("role_exists")
        if backup_exists is True:
            if (
                backup.get("can_select") is not True
                or backup.get("can_write") is not False
                or backup.get("is_member_of_any_role") is not False
                or backup.get("has_any_nonsuper_member") is not False
                or backup.get("can_set_select_role") is not False
                or backup.get("can_set_write_role") is not False
                or backup.get("can_admin_select_role") is not False
                or backup.get("can_admin_write_role") is not False
                or backup.get("inherited_by_other_role") is not False
                or backup.get("settable_by_other_role") is not False
                or backup.get("administered_by_other_role") is not False
                or backup.get("capability_role_has_admin_member") is not False
            ):
                failures.append("role_access.backup.read_only")
        elif any(
            backup.get(field) is not False
            for field in boolean_fields[1:]
        ):
            failures.append("role_access.backup.absent")

    if edge is not None:
        edge_exists = edge.get("role_exists")
        if edge.get("is_member_of_any_role") is not False or any(
            edge.get(field) is not False
            for field in (
                "can_select",
                "can_write",
                "can_set_select_role",
                "can_set_write_role",
                "can_admin_select_role",
                "can_admin_write_role",
                "capability_role_has_admin_member",
            )
        ):
            failures.append("role_access.edge.denied")
        if edge_exists is not True and any(
            edge.get(field) is not False
            for field in boolean_fields[1:]
        ):
            failures.append("role_access.edge.absent")

    if backup is None or edge is None:
        return None
    return backup.get("role_exists"), edge.get("role_exists")


def _assert_kms_pin_acl_rows(
    *,
    rows: list[Mapping[str, Any]],
    expected_runtime_role: str,
    expected_migration_role: str,
    table: bool,
    failures: list[str],
) -> None:
    label = "table_acl" if table else "function_acl"
    if not rows:
        failures.append(f"{label}.missing")
        return
    backup_exists = rows[0].get("backup_role_exists")
    edge_exists = rows[0].get("edge_role_exists")
    if not isinstance(backup_exists, bool) or not isinstance(edge_exists, bool):
        failures.append(f"{label}.role_evidence")
    grants: list[tuple[object, object, object]] = []
    for row in rows:
        if (
            row.get("owner_name") != expected_migration_role
            or row.get("backup_role_exists") is not backup_exists
            or row.get("edge_role_exists") is not edge_exists
        ):
            failures.append(f"{label}.owner_or_roles")
        if row.get("grantee_name") is not None:
            grants.append(
                (
                    row.get("grantee_name"),
                    row.get("privilege_type"),
                    row.get("is_grantable"),
                )
            )
    expected_grants: set[tuple[object, object, object]] = set()
    if table:
        expected_grants.add((expected_runtime_role, "SELECT", False))
        if backup_exists is True:
            expected_grants.add(("star_oam_backup", "SELECT", False))
    if len(grants) != len(set(grants)) or set(grants) != expected_grants:
        failures.append(f"{label}.grant_set")


def _kms_pin_check_definition_matches(name: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    literal_sequence = tuple(_SQL_STRING_LITERAL_PATTERN.findall(value))
    expected_literals = {
        "ck_kms_data_key_pins_purpose_0040": (
            "authentication_idempotency",
            "material_request_contact",
        ),
        "ck_kms_data_key_pins_version_0040": (),
        "ck_kms_data_key_pins_coordinates_0040": (),
        "ck_kms_data_key_pins_sha256_0040": tuple(
            item
            for character in "0123456789abcdef"
            for item in (character, "")
        ),
    }
    if literal_sequence != expected_literals.get(name):
        return False
    compact = _compact_kms_pin_check_definition(value)
    if compact is None:
        return False
    purpose_forms = {
        "checkpurposein'authentication_idempotency',"
        "'material_request_contact'",
        "checkpurpose=anyarray['authentication_idempotency',"
        "'material_request_contact']",
    }
    version_forms = {
        "checkapplication_key_versionbetween1and2147483647",
        "checkapplication_key_version>=1and"
        "application_key_version<=2147483647",
    }
    coordinates_between = (
        "checkkms_key_id=trimkms_key_idand"
        "kms_key_version_id=trimkms_key_version_idand"
        "lengthkms_key_idbetween3and256and"
        "lengthkms_key_version_idbetween8and128"
    )
    coordinates_compared = (
        "checkkms_key_id=trimkms_key_idand"
        "kms_key_version_id=trimkms_key_version_idand"
        "lengthkms_key_id>=3andlengthkms_key_id<=256and"
        "lengthkms_key_version_id>=8andlengthkms_key_version_id<=128"
    )
    sha_expression = "ciphertext_sha256"
    for character in "0123456789abcdef":
        sha_expression = f"replace({sha_expression}, '{character}', '')"
    sha_form = _compact_kms_pin_check_definition(
        "CHECK (length(ciphertext_sha256) = 64 AND "
        f"length({sha_expression}) = 0)"
    )
    expected_forms = {
        "ck_kms_data_key_pins_purpose_0040": purpose_forms,
        "ck_kms_data_key_pins_version_0040": version_forms,
        "ck_kms_data_key_pins_coordinates_0040": {
            coordinates_between,
            coordinates_compared,
        },
        "ck_kms_data_key_pins_sha256_0040": {sha_form},
    }
    return compact in expected_forms.get(name, set())


def _compact_kms_pin_check_definition(value: object) -> str | None:
    if not isinstance(value, str) or not value or '"' in value:
        return None
    normalized = _lower_sql_outside_string_literals(value)
    normalized = re.sub(
        r"::\s*(?:character\s+varying|varchar|text)(?:\s*\[\s*\])?",
        "",
        normalized,
    )
    normalized = re.sub(
        r"\btrim\s*\(\s*both\s+from\s+",
        "trim(",
        normalized,
    )
    normalized = re.sub(r"\bbtrim\b", "trim", normalized)
    return re.sub(
        r"\s+",
        "",
        normalized.replace("(", "").replace(")", ""),
    )


def _assert_nonopening_stocktake_start_guards(
    *,
    triggers: list[Mapping[str, Any]],
    columns: list[Mapping[str, Any]],
    constraints: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
    expected_migration_role: str,
) -> None:
    """Prove the non-opening start completion and deferred graph boundary."""

    failures: list[str] = []
    actual_triggers: dict[str, Mapping[str, Any]] = {}
    for row in triggers:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual_triggers:
            failures.append("trigger_identity")
            continue
        actual_triggers[name] = row
    if set(actual_triggers) != set(EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS):
        failures.append("trigger_set")
    for name, expected in EXPECTED_NONOPENING_STOCKTAKE_START_TRIGGERS.items():
        row = actual_triggers.get(name)
        if row is None:
            continue
        (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
        ) = expected
        expected_values = {
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
        for field, value in expected_values.items():
            if row.get(field) != value:
                failures.append(f"{name}.{field}")

    if len(columns) != len(EXPECTED_STOCKTAKE_START_COMPLETION_COLUMNS):
        failures.append("completion.column_set")
    for ordinal, expected in enumerate(
        EXPECTED_STOCKTAKE_START_COMPLETION_COLUMNS, start=1
    ):
        row = columns[ordinal - 1] if len(columns) >= ordinal else None
        if row is None:
            continue
        column_name, data_type, is_not_null = expected
        if (
            row.get("ordinal_position") != ordinal
            or row.get("column_name") != column_name
            or row.get("data_type") != data_type
            or row.get("is_not_null") is not is_not_null
            or row.get("identity_kind") not in (None, "")
            or row.get("generated_kind") not in (None, "")
            or row.get("default_expression") is not None
            or row.get("relation_kind") != "r"
            or row.get("persistence") != "p"
            or row.get("row_security") is not False
            or row.get("force_row_security") is not False
        ):
            failures.append(f"completion.{column_name}.shape")

    actual_constraints = {
        row.get("constraint_name"): row
        for row in constraints
        if isinstance(row.get("constraint_name"), str)
    }
    if (
        len(actual_constraints) != len(constraints)
        or set(actual_constraints)
        != set(EXPECTED_STOCKTAKE_START_COMPLETION_CONSTRAINTS)
    ):
        failures.append("completion.constraint_set")
    check_tokens = {
        "ck_stocktake_start_completions_versions_0047": (
            "expected_task_version", "started_task_version", "+ 1"),
        "ck_stocktake_start_completions_counts_0047": (
            "cutoff_ledger_cursor", "scope_count", "snapshot_line_count",
            "active_freeze_count"),
        "ck_stocktake_start_completions_authorization_0047": (
            "authorization_version", "admin", "provincial_manager",
            "technician", "scope_type", "scope_id_snapshot"),
        "ck_stocktake_start_completions_hashes_0047": (
            "scope_manifest_sha256", "snapshot_manifest_sha256",
            "request_sha256", "idempotency_key_hash",
            "authorization_sha256", "graph_manifest_sha256", "64"),
        "ck_stocktake_start_completions_chronology_0047": (
            "cutoff_at", "started_at", "created_at"),
    }
    for name, expected in EXPECTED_STOCKTAKE_START_COMPLETION_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            continue
        constraint_type, constrained_columns, referenced_table, referenced_columns = expected
        if (
            row.get("constraint_type") != constraint_type
            or (
                constraint_type != "c"
                and tuple(row.get("constrained_columns") or ())
                != constrained_columns
            )
            or row.get("referenced_table") != referenced_table
            or tuple(row.get("referenced_columns") or ())
            != referenced_columns
            or row.get("is_validated") is not True
            or row.get("is_deferrable") is not False
            or row.get("is_initially_deferred") is not False
            or row.get("is_no_inherit") is not (constraint_type != "c")
            or row.get("is_local") is not True
            or row.get("inheritance_count") != 0
            or row.get("parent_constraint_id") != 0
            or (constraint_type == "f" and row.get("delete_action") != "r")
        ):
            failures.append(f"completion.{name}.shape")
        definition = str(row.get("definition") or "").lower()
        if any(token.lower() not in definition for token in check_tokens.get(name, ())):
            failures.append(f"completion.{name}.definition")

    actual_indexes = {
        row.get("index_name"): row
        for row in indexes
        if isinstance(row.get("index_name"), str)
    }
    if (
        len(actual_indexes) != len(indexes)
        or set(actual_indexes) != set(EXPECTED_STOCKTAKE_START_COMPLETION_INDEXES)
    ):
        failures.append("completion.index_set")
    for name, expected in EXPECTED_STOCKTAKE_START_COMPLETION_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        expected_columns, is_unique, is_primary = expected
        key_columns = tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        )
        if (
            row.get("owner_name") != expected_migration_role
            or row.get("access_method") != "btree"
            or row.get("is_unique") is not is_unique
            or row.get("is_primary") is not is_primary
            or row.get("is_exclusion") is not False
            or row.get("is_immediate") is not True
            or row.get("is_valid") is not True
            or row.get("is_ready") is not True
            or row.get("is_live") is not True
            or row.get("nulls_not_distinct") is not False
            or row.get("has_expressions") is not False
            or row.get("key_attribute_count") != len(expected_columns)
            or row.get("total_attribute_count") != len(expected_columns)
            or key_columns != expected_columns
            or row.get("predicate") is not None
        ):
            failures.append(f"completion.{name}.shape")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database non-opening stocktake start guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_nonopening_stocktake_close_guards(
    *,
    triggers: list[Mapping[str, Any]],
    indexes: list[Mapping[str, Any]],
    constraints: list[Mapping[str, Any]],
) -> None:
    """Prove the independent non-opening reconcile/close commit boundary."""

    failures: list[str] = []
    actual_triggers: dict[str, Mapping[str, Any]] = {}
    for row in triggers:
        name = row.get("trigger_name")
        if not isinstance(name, str) or name in actual_triggers:
            failures.append("trigger_identity")
            continue
        actual_triggers[name] = row
    if set(actual_triggers) != set(EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS):
        failures.append("trigger_set")
    for name, expected in EXPECTED_NONOPENING_STOCKTAKE_CLOSE_TRIGGERS.items():
        row = actual_triggers.get(name)
        if row is None:
            continue
        (
            table_name,
            function_name,
            enabled,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred,
            has_column_filter,
        ) = expected
        if row.get("table_name") != table_name:
            failures.append(f"{name}.table")
        if row.get("function_schema") != "public" or row.get(
            "function_name"
        ) != function_name:
            failures.append(f"{name}.function")
        if row.get("enabled") != enabled:
            failures.append(f"{name}.enabled")
        if row.get("trigger_type") != trigger_type:
            failures.append(f"{name}.type")
        if row.get("is_constraint_trigger") is not is_constraint:
            failures.append(f"{name}.constraint")
        if row.get("is_deferrable") is not is_deferrable:
            failures.append(f"{name}.deferrable")
        if row.get("is_initially_deferred") is not is_initially_deferred:
            failures.append(f"{name}.initially_deferred")
        if row.get("has_when_clause") is not False:
            failures.append(f"{name}.when")
        if row.get("has_column_filter") is not has_column_filter:
            failures.append(f"{name}.columns")

    actual_indexes: dict[str, Mapping[str, Any]] = {}
    for row in indexes:
        name = row.get("index_name")
        if not isinstance(name, str) or name in actual_indexes:
            failures.append("index_identity")
            continue
        actual_indexes[name] = row
    if set(actual_indexes) != set(EXPECTED_NONOPENING_STOCKTAKE_CLOSE_INDEXES):
        failures.append("index_set")
    for name, expected in EXPECTED_NONOPENING_STOCKTAKE_CLOSE_INDEXES.items():
        row = actual_indexes.get(name)
        if row is None:
            continue
        if row.get("table_name") != expected["table"]:
            failures.append(f"{name}.table")
        if row.get("access_method") != "btree":
            failures.append(f"{name}.method")
        if row.get("is_unique") is not expected["unique"]:
            failures.append(f"{name}.unique")
        if any(
            row.get(field) is not True
            for field in ("is_valid", "is_ready", "is_live")
        ):
            failures.append(f"{name}.state")
        if tuple(
            str(value).replace('"', "")
            for value in (row.get("key_columns") or ())
        ) != expected["columns"]:
            failures.append(f"{name}.columns")
        if row.get("predicate") not in (None, ""):
            failures.append(f"{name}.predicate")

    actual_constraints: dict[str, Mapping[str, Any]] = {}
    for row in constraints:
        name = row.get("constraint_name")
        if not isinstance(name, str) or name in actual_constraints:
            failures.append("constraint_identity")
            continue
        actual_constraints[name] = row
    if set(actual_constraints) != set(
        EXPECTED_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINTS
    ):
        failures.append("constraint_set")
    for name, expected in EXPECTED_NONOPENING_STOCKTAKE_CLOSE_CONSTRAINTS.items():
        row = actual_constraints.get(name)
        if row is None:
            continue
        if (
            row.get("table_name") != expected["table"]
            or row.get("constraint_type") != "f"
            or row.get("is_validated") is not True
            or row.get("is_deferrable") is not True
            or row.get("is_initially_deferred") is not True
            or tuple(row.get("constrained_columns") or ()) != expected["columns"]
            or row.get("referenced_table") != expected["referenced_table"]
            or tuple(row.get("referenced_columns") or ())
            != expected["referenced_columns"]
            or row.get("update_action") != "a"
            or row.get("delete_action") != "r"
        ):
            failures.append(f"{name}.shape")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database non-opening stocktake close guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_fixed_audit_heads(rows: list[Mapping[str, Any]]) -> None:
    failures: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        stream_key = row.get("stream_key")
        if not isinstance(stream_key, str) or stream_key in actual:
            failures.append("head_identity")
            continue
        actual[stream_key] = row
    if set(actual) != set(EXPECTED_AUDIT_HEAD_IDS):
        failures.append("head_set")
    for stream_key, expected_id in EXPECTED_AUDIT_HEAD_IDS.items():
        row = actual.get(stream_key)
        if row is None:
            continue
        if str(row.get("id")) != expected_id:
            failures.append(f"{stream_key}.id")
        version = row.get("version")
        last_event_id = row.get("last_event_id")
        last_hash = row.get("last_hash")
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            failures.append(f"{stream_key}.version")
            continue
        if version == 0:
            if last_event_id is not None or last_hash is not None:
                failures.append(f"{stream_key}.empty_binding")
        elif (
            last_event_id is None
            or last_hash is None
            or row.get("bound_event_id") != last_event_id
            or row.get("bound_event_hash") != last_hash
        ):
            failures.append(f"{stream_key}.event_binding")
    if failures:
        raise DatabaseSecurityBoundaryError(
            "production database audit head guard failed: "
            + ", ".join(sorted(set(failures)))
        )


def _assert_complete_audit_graph(
    *,
    heads: list[Mapping[str, Any]],
    events: list[Mapping[str, Any]],
) -> None:
    """Prove that every persisted event belongs to exactly one fixed stream.

    The persisted coordinates make membership indexable, while the complete
    walk independently proves that no coordinate, predecessor or canonical v1
    hash was forged.  This remains defense in depth for the deferred commit
    binding installed by revision 0017.
    """

    try:
        normalized_events: list[dict[str, Any]] = []
        events_by_hash: dict[str, list[dict[str, Any]]] = {}
        event_ids: set[uuid.UUID] = set()
        stream_coordinates: set[tuple[str, int]] = set()
        for row in events:
            event_id = _canonical_audit_uuid(row.get("id"))
            if event_id in event_ids:
                raise ValueError("duplicate audit event id")
            event_ids.add(event_id)
            stream_key = row.get("stream_key")
            stream_version = row.get("stream_version")
            if (
                not isinstance(stream_key, str)
                or stream_key not in EXPECTED_AUDIT_HEAD_IDS
                or isinstance(stream_version, bool)
                or not isinstance(stream_version, int)
                or stream_version <= 0
            ):
                raise ValueError("invalid audit stream coordinate")
            coordinate = (stream_key, stream_version)
            if coordinate in stream_coordinates:
                raise ValueError("duplicate audit stream coordinate")
            stream_coordinates.add(coordinate)
            event_hash = _canonical_audit_hash(row.get("event_hash"))
            previous_hash = (
                None
                if row.get("previous_hash") is None
                else _canonical_audit_hash(row.get("previous_hash"))
            )
            event = {
                "id": event_id,
                "stream_key": stream_key,
                "stream_version": stream_version,
                "actor_user_id": row.get("actor_user_id"),
                "action": row.get("action"),
                "aggregate_type": row.get("aggregate_type"),
                "aggregate_id": row.get("aggregate_id"),
                "before_jsonb": _canonical_audit_json(row.get("before_jsonb")),
                "after_jsonb": _canonical_audit_json(row.get("after_jsonb")),
                "request_id": row.get("request_id"),
                "previous_hash": previous_hash,
                "event_hash": event_hash,
                "occurred_at": _canonical_audit_datetime(
                    row.get("occurred_at")
                ),
            }
            normalized_events.append(event)
            events_by_hash.setdefault(event_hash, []).append(event)

        owned_event_ids: set[uuid.UUID] = set()
        stream_keys: set[str] = set()
        total_versions = 0
        for head in heads:
            stream_key = head.get("stream_key")
            if (
                not isinstance(stream_key, str)
                or not stream_key
                or stream_key in stream_keys
                or stream_key not in EXPECTED_AUDIT_HEAD_IDS
            ):
                raise ValueError("invalid audit stream")
            stream_keys.add(stream_key)
            if str(_canonical_audit_uuid(head.get("id"))) != (
                EXPECTED_AUDIT_HEAD_IDS[stream_key]
            ):
                raise ValueError("invalid audit head id")
            version = head.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise ValueError("invalid audit head version")
            total_versions += version
            if version > len(normalized_events) or total_versions > len(
                normalized_events
            ):
                raise ValueError("audit head version exceeds event count")
            last_event_id = (
                None
                if head.get("last_event_id") is None
                else _canonical_audit_uuid(head.get("last_event_id"))
            )
            last_hash = (
                None
                if head.get("last_hash") is None
                else _canonical_audit_hash(head.get("last_hash"))
            )
            if (version == 0) != (last_event_id is None and last_hash is None):
                raise ValueError("invalid audit head binding")
            if (last_event_id is None) != (last_hash is None):
                raise ValueError("partial audit head binding")

            expected_hash = last_hash
            visited_in_stream: set[uuid.UUID] = set()
            for position in range(version):
                candidates = events_by_hash.get(expected_hash or "", [])
                if len(candidates) != 1:
                    raise ValueError("audit hash is not unique and complete")
                event = candidates[0]
                if position == 0 and event["id"] != last_event_id:
                    raise ValueError("audit head id/hash mismatch")
                if event["id"] in visited_in_stream:
                    raise ValueError("audit chain cycle")
                if event["id"] in owned_event_ids:
                    raise ValueError("audit event belongs to multiple streams")
                if event["stream_key"] != stream_key or event[
                    "stream_version"
                ] != version - position:
                    raise ValueError("audit event stream coordinate mismatch")
                visited_in_stream.add(event["id"])
                owned_event_ids.add(event["id"])
                calculated_hash = calculate_audit_event_hash(
                    stream_key=stream_key,
                    event_id=event["id"],
                    actor_user_id=event["actor_user_id"],
                    action=event["action"],
                    aggregate_type=event["aggregate_type"],
                    aggregate_id=event["aggregate_id"],
                    before_jsonb=event["before_jsonb"],
                    after_jsonb=event["after_jsonb"],
                    request_id=event["request_id"],
                    previous_hash=event["previous_hash"],
                    occurred_at=event["occurred_at"],
                )
                if calculated_hash != event["event_hash"]:
                    raise ValueError("audit event hash mismatch")
                expected_hash = event["previous_hash"]
            if expected_hash is not None:
                raise ValueError("audit chain does not reach genesis")

        if stream_keys != set(EXPECTED_AUDIT_HEAD_IDS):
            raise ValueError("fixed audit streams are incomplete")
        if total_versions != len(normalized_events):
            raise ValueError("audit event count does not match head versions")
        if owned_event_ids != event_ids:
            raise ValueError("orphan audit event")
    except Exception:
        raise DatabaseSecurityBoundaryError(
            "production database audit graph guard failed"
        ) from None


def _canonical_audit_uuid(value: Any) -> uuid.UUID:
    parsed = uuid.UUID(str(value))
    if parsed.int == 0:
        raise ValueError("zero UUID")
    return parsed


def _canonical_audit_hash(value: Any) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid SHA-256 digest")
    return value


def _canonical_audit_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        raise ValueError("audit snapshot must be an object")
    return json.loads(
        json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _canonical_audit_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("invalid audit timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
