"""Activate the minimum runtime ACL for the mounted opening workflow.

Revision ID: 20260831_0024
Revises: 20260831_0023
Create Date: 2026-08-31

Revision 0022 first installed the terminal opening-posting boundary and reset
the production API role to an exact ACL manifest.  The complete formal opening
router was subsequently mounted, but the manifest still omitted the tables
written by start, count/seal, observation disposition, two-stage review and
recount.  SQLite service tests cannot expose that PostgreSQL role failure.

This ACL-only revision re-applies a self-contained least-privilege manifest.
It adds SELECT only for the QR master used during exact scan resolution,
INSERT only for the append-only opening workflow facts, and column-level
UPDATE only for the task/round state coordinates changed by the services.  It
does not grant DELETE, sequence access, function execution, table-level UPDATE
on stocktake tasks/rounds, or any QR/master-data mutation.  SQLite is a no-op.
"""

from __future__ import annotations

from typing import Mapping, Sequence, Union

from alembic import op


revision: str = "20260831_0024"
down_revision: Union[str, Sequence[str], None] = "20260831_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"

# This is the executable runtime ACL manifest.  Production startup validation
# and migration tests parse these literals directly; keep them self-contained
# instead of importing mutable application configuration.
API_READ_TABLES = (
    "audit_chain_heads",
    "audit_events",
    "auth_identities",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "custody_assignments",
    "external_objects",
    "external_object_versions",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_serials",
    "inventory_transactions",
    "login_challenges",
    "material_inventory_policies",
    "materials",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
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
)
API_INSERT_TABLES = (
    "audit_events",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "inventory_freezes",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_transactions",
    "login_challenges",
    "outbox_events",
    "role_assignments",
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
)
API_UPDATE_TABLES = (
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "users",
)
API_DELETE_TABLES = ("auth_login_rate_limit_buckets",)
API_UPDATE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "audit_chain_heads": (
        "last_event_id",
        "last_hash",
        "version",
        "updated_at",
    ),
    "inventory_freezes": (
        "status",
        "valid_to",
        "released_by_user_id",
        "release_reason",
        "version",
        "updated_at",
    ),
    "inventory_ledger_heads": ("next_cursor", "updated_at"),
    "serial_current_positions": (
        "stock_account_id",
        "last_movement_id",
        "updated_at",
    ),
    "stock_balances": (
        "quantity",
        "ledger_cursor",
        "version",
        "updated_at",
    ),
    "stocktake_rounds": (
        "status",
        "submitted_by_user_id",
        "submitted_at",
        "count_manifest_sha256",
        "updated_at",
    ),
    "stocktake_tasks": (
        "status",
        "submitted_at",
        "current_round_no",
        "posted_at",
        "closed_at",
        "version",
        "updated_at",
    ),
}

# Exact revision-0023 manifest restored by downgrade.  Keep this explicit so a
# rollback cannot retain a privilege that its application version did not own.
PREVIOUS_API_READ_TABLES = (
    "audit_chain_heads",
    "audit_events",
    "auth_identities",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "custody_assignments",
    "external_objects",
    "external_object_versions",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_serials",
    "inventory_transactions",
    "login_challenges",
    "material_inventory_policies",
    "materials",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
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
)
PREVIOUS_API_INSERT_TABLES = (
    "audit_events",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_transactions",
    "login_challenges",
    "outbox_events",
    "role_assignments",
    "serial_current_positions",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stocktake_posting_items",
    "stocktake_postings",
)
PREVIOUS_API_UPDATE_TABLES = API_UPDATE_TABLES
PREVIOUS_API_DELETE_TABLES = API_DELETE_TABLES
PREVIOUS_API_UPDATE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "audit_chain_heads": (
        "last_event_id",
        "last_hash",
        "version",
        "updated_at",
    ),
    "inventory_freezes": (
        "status",
        "valid_to",
        "released_by_user_id",
        "release_reason",
        "version",
        "updated_at",
    ),
    "inventory_ledger_heads": ("next_cursor", "updated_at"),
    "serial_current_positions": (
        "stock_account_id",
        "last_movement_id",
        "updated_at",
    ),
    "stock_balances": (
        "quantity",
        "ledger_cursor",
        "version",
        "updated_at",
    ),
    "stocktake_tasks": (
        "status",
        "posted_at",
        "closed_at",
        "version",
        "updated_at",
    ),
}


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0024 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    if _dialect_name() == "postgresql":
        _apply_postgresql_runtime_acl(current=True)


def downgrade() -> None:
    if _dialect_name() == "postgresql":
        _apply_postgresql_runtime_acl(current=False)


def _apply_postgresql_runtime_acl(*, current: bool) -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0024 requires the provisioned star_oam_api runtime role';
    END IF;
END
$$
"""
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    _revoke_postgresql_column_acl()
    op.execute(
        "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    read_tables = API_READ_TABLES if current else PREVIOUS_API_READ_TABLES
    insert_tables = API_INSERT_TABLES if current else PREVIOUS_API_INSERT_TABLES
    update_tables = API_UPDATE_TABLES if current else PREVIOUS_API_UPDATE_TABLES
    delete_tables = API_DELETE_TABLES if current else PREVIOUS_API_DELETE_TABLES
    update_columns = (
        API_UPDATE_COLUMNS if current else PREVIOUS_API_UPDATE_COLUMNS
    )
    _grant_table_privileges("SELECT", read_tables)
    _grant_table_privileges("INSERT", insert_tables)
    _grant_table_privileges("UPDATE", update_tables)
    _grant_table_privileges("DELETE", delete_tables)
    for table_name, columns in sorted(update_columns.items()):
        op.execute(
            f"GRANT UPDATE ({', '.join(columns)}) ON TABLE "
            f"public.{table_name} TO {PRODUCTION_API_ROLE}"
        )


def _grant_table_privileges(
    privileges: str, table_names: Sequence[str]
) -> None:
    if table_names:
        op.execute(
            f"GRANT {privileges} ON TABLE "
            f"{', '.join(f'public.{name}' for name in table_names)} "
            f"TO {PRODUCTION_API_ROLE}"
        )


def _revoke_postgresql_column_acl() -> None:
    op.execute(
        f"""
DO $$
DECLARE
    column_row record;
BEGIN
    FOR column_row IN
        SELECT class_row.relname, attribute_row.attname
          FROM pg_class AS class_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = class_row.relnamespace
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = class_row.oid
         WHERE namespace_row.nspname = 'public'
           AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
         ORDER BY class_row.relname, attribute_row.attnum
    LOOP
        EXECUTE format(
            'REVOKE SELECT (%1$I), INSERT (%1$I), UPDATE (%1$I), '
            'REFERENCES (%1$I) ON TABLE public.%2$I FROM PUBLIC, '
            '{PRODUCTION_API_ROLE}',
            column_row.attname,
            column_row.relname
        );
    END LOOP;
END
$$
"""
    )
