"""Add the formal opening-control reconciliation proof boundary.

Revision ID: 20260831_0026
Revises: 20260831_0025
Create Date: 2026-08-31

The V1 opening establishment remains immutable.  OAM control quantities are
read-only comparison facts and never become personal inventory.  This
revision therefore keeps the pre-existing reconciliation run/item tables as
state projections and adds immutable task/round/posting/source bindings,
authorization snapshots, evidence references and append-only idempotent
commands around them.

No historical reconciliation row is inferred.  Upgrade fails closed when the
prototype tables contain data, because there is no trustworthy way to recover
the missing formal coordinates or authorization evidence.  PostgreSQL and
SQLite both install delete/terminal-state guards.  PostgreSQL additionally
resets the runtime role to an exact least-privilege manifest and revokes
execution of every guard function from PUBLIC and the API role.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Mapping, Sequence, Union

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260831_0026"
down_revision: Union[str, Sequence[str], None] = "20260831_0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _lower_hex_remainder(expression: str) -> str:
    for character in "0123456789abcdef":
        expression = f"replace({expression}, '{character}', '')"
    return expression

ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PROVINCIAL_MANAGER_ROLE_ID = uuid.UUID(
    "10000000-0000-4000-8000-000000000002"
)
PERMISSION_ROWS = (
    (
        uuid.UUID("20000000-0000-4000-8000-000000000023"),
        "reconciliation",
        "read",
        "Read formal opening-control reconciliations",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000024"),
        "reconciliation",
        "create_opening",
        "Create a formal opening-control reconciliation",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000025"),
        "reconciliation",
        "explain_opening",
        "Explain all formal opening-control differences for a region",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000026"),
        "reconciliation",
        "approve_opening",
        "Approve a fully explained opening-control reconciliation",
    ),
)
ROLE_PERMISSION_ROWS = (
    (
        uuid.UUID("21000000-0000-4000-8000-000000000043"),
        ADMIN_ROLE_ID,
        PERMISSION_ROWS[0][0],
    ),
    (
        uuid.UUID("21000000-0000-4000-8000-000000000044"),
        ADMIN_ROLE_ID,
        PERMISSION_ROWS[1][0],
    ),
    (
        uuid.UUID("21000000-0000-4000-8000-000000000045"),
        ADMIN_ROLE_ID,
        PERMISSION_ROWS[3][0],
    ),
    (
        uuid.UUID("21000000-0000-4000-8000-000000000046"),
        PROVINCIAL_MANAGER_ROLE_ID,
        PERMISSION_ROWS[0][0],
    ),
    (
        uuid.UUID("21000000-0000-4000-8000-000000000047"),
        PROVINCIAL_MANAGER_ROLE_ID,
        PERMISSION_ROWS[2][0],
    ),
)

UPGRADE_BLOCKER = (
    "0026 preflight failed: existing reconciliation projections cannot be "
    "promoted without formal task, source, evidence and authorization proof"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0026 while reconciliation evidence or projections exist"
)
GUARD_ERROR = "formal opening-control reconciliation invariant violated"

PG_COMMAND_FUNCTION = "rsc_guard_reconciliation_command_0026"
PG_COMMAND_CONSUMPTION_FUNCTION = (
    "rsc_guard_opening_reconciliation_command_consumption_0026"
)
PG_CANONICAL_JSON_FUNCTION = "rsc_canonical_reconciliation_json_0026"
PG_EVENT_KEY_FUNCTION = "rsc_reconciliation_event_key_0026"
PG_EFFECT_FUNCTION = "rsc_guard_reconciliation_effect_0026"
PG_TASK_CLOSE_FUNCTION = "rsc_guard_opening_reconciliation_task_close_0026"
PG_SOURCE_LOCK_FUNCTION = "rsc_lock_opening_reconciliation_source_0026"
PG_RUN_LOCK_FUNCTION = "rsc_lock_opening_reconciliation_run_0026"
PG_FILES_LOCK_FUNCTION = "rsc_lock_opening_reconciliation_files_0026"
PG_PRINCIPAL_GRAPH_LOCK_FUNCTION = "rsc_lock_formal_principal_graph_0026"
PG_LOCK_FUNCTION_SIGNATURES = (
    f"public.{PG_SOURCE_LOCK_FUNCTION}(uuid)",
    f"public.{PG_RUN_LOCK_FUNCTION}(uuid)",
    f"public.{PG_FILES_LOCK_FUNCTION}(uuid, uuid[])",
    f"public.{PG_PRINCIPAL_GRAPH_LOCK_FUNCTION}(text[])",
)
PG_RUN_EXTENSION_FUNCTION = "rsc_guard_opening_reconciliation_run_0026"
PG_ITEM_EXTENSION_FUNCTION = "rsc_guard_opening_reconciliation_item_0026"
PG_RUN_PROJECTION_FUNCTION = "rsc_guard_reconciliation_run_projection_0026"
PG_ITEM_PROJECTION_FUNCTION = "rsc_guard_reconciliation_item_projection_0026"

RECONCILIATION_TRIGGER_NAMES = (
    "trg_reconciliation_commands_immutable_0026",
    "trg_reconciliation_commands_no_truncate_0026",
    "trg_opening_reconciliation_runs_guard_0026",
    "trg_opening_reconciliation_runs_no_delete_0026",
    "trg_opening_reconciliation_runs_no_truncate_0026",
    "trg_opening_reconciliation_items_guard_0026",
    "trg_opening_reconciliation_items_no_delete_0026",
    "trg_opening_reconciliation_items_no_truncate_0026",
    "trg_reconciliation_runs_formal_guard_0026",
    "trg_reconciliation_runs_formal_delete_0026",
    "trg_reconciliation_runs_no_truncate_0026",
    "trg_reconciliation_items_formal_guard_0026",
    "trg_reconciliation_items_formal_delete_0026",
    "trg_reconciliation_items_no_truncate_0026",
    "trg_reconciliation_runs_formal_insert_0026",
    "trg_reconciliation_items_formal_insert_0026",
    "trg_opening_reconciliation_consumptions_guard_0026",
    "trg_opening_reconciliation_consumptions_no_truncate_0026",
    "trg_reconciliation_state_effect_guard_0026",
    "trg_reconciliation_outbox_effect_guard_0026",
    "trg_reconciliation_audit_effect_guard_0026",
    "trg_reconciliation_state_effect_no_truncate_0026",
    "trg_reconciliation_outbox_effect_no_truncate_0026",
    "trg_reconciliation_audit_effect_no_truncate_0026",
    "trg_stocktake_tasks_reconciliation_close_guard_0026",
)

# Exact 0026 runtime manifest.  Keep the literals self-contained: startup and
# tests compare them with app.database_security, and downgrade restores 0024.
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
    "files",
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
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_items",
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
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_items",
    "opening_control_reconciliation_runs",
    "outbox_events",
    "reconciliation_commands",
    "reconciliation_items",
    "reconciliation_runs",
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
    "opening_control_reconciliation_items": (
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
    ),
    "opening_control_reconciliation_runs": (
        "version",
        "approved_by_user_id",
        "approved_by_person_id",
        "approved_role_assignment_id",
        "approved_authorization_version",
        "approved_at",
        "approval_comment",
        "updated_at",
    ),
    "reconciliation_items": (
        "status",
        "explanation",
        "evidence_file_id",
        "updated_at",
    ),
    "reconciliation_runs": ("status", "updated_at"),
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

_NEW_READ_TABLES = {
    "files",
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_items",
    "opening_control_reconciliation_runs",
    "reconciliation_commands",
    "reconciliation_items",
    "reconciliation_runs",
}
_NEW_INSERT_TABLES = {
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_items",
    "opening_control_reconciliation_runs",
    "reconciliation_commands",
    "reconciliation_items",
    "reconciliation_runs",
}
_NEW_UPDATE_COLUMN_TABLES = {
    "opening_control_reconciliation_items",
    "opening_control_reconciliation_runs",
    "reconciliation_items",
    "reconciliation_runs",
}
PREVIOUS_API_READ_TABLES = tuple(
    name for name in API_READ_TABLES if name not in _NEW_READ_TABLES
)
PREVIOUS_API_INSERT_TABLES = tuple(
    name for name in API_INSERT_TABLES if name not in _NEW_INSERT_TABLES
)
PREVIOUS_API_UPDATE_TABLES = API_UPDATE_TABLES
PREVIOUS_API_DELETE_TABLES = API_DELETE_TABLES
PREVIOUS_API_UPDATE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    name: columns
    for name, columns in API_UPDATE_COLUMNS.items()
    if name not in _NEW_UPDATE_COLUMN_TABLES
}


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0026 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0026 SQLite upgrade requires an online connection")
        _postgresql_upgrade_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph()
        _online_upgrade_preflight()

    _create_tables()
    _seed_permissions()
    if dialect == "postgresql":
        _create_postgresql_guards()
        _apply_postgresql_runtime_acl(current=True)
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0026 SQLite downgrade requires an online connection")
        _postgresql_downgrade_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph()
        _online_downgrade_preflight()

    if dialect == "postgresql":
        _drop_postgresql_guards()
    else:
        _drop_sqlite_guards()
    _delete_permissions()
    op.drop_table("opening_control_reconciliation_items")
    op.drop_table("opening_control_reconciliation_runs")
    op.drop_table("reconciliation_commands")
    op.drop_table("opening_control_reconciliation_command_consumptions")
    if dialect == "postgresql":
        _apply_postgresql_runtime_acl(current=False)


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_graph() -> None:
    op.get_bind().exec_driver_sql(
        "LOCK TABLE public.reconciliation_runs, public.reconciliation_items, "
        "public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_differences, public.stocktake_postings "
        "IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_upgrade_preflight() -> None:
    op.execute(
        "LOCK TABLE public.reconciliation_runs, public.reconciliation_items, "
        "public.stocktake_tasks, public.stocktake_rounds, "
        "public.stocktake_differences, public.stocktake_postings "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.reconciliation_runs)
       OR EXISTS (SELECT 1 FROM public.reconciliation_items) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_upgrade_preflight() -> None:
    prefix = "public." if _dialect_name() == "postgresql" else ""
    result = op.get_bind().exec_driver_sql(
        "SELECT 1 WHERE EXISTS (SELECT 1 FROM "
        f"{prefix}reconciliation_runs) OR EXISTS (SELECT 1 FROM "
        f"{prefix}reconciliation_items)"
    ).first()
    if result is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _postgresql_downgrade_preflight() -> None:
    op.execute(
        "LOCK TABLE public.reconciliation_runs, public.reconciliation_items, "
        "public.opening_control_reconciliation_runs, "
        "public.opening_control_reconciliation_items, "
        "public.reconciliation_commands, "
        "public.opening_control_reconciliation_command_consumptions "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.reconciliation_runs)
       OR EXISTS (SELECT 1 FROM public.reconciliation_items)
       OR EXISTS (SELECT 1 FROM public.opening_control_reconciliation_runs)
       OR EXISTS (SELECT 1 FROM public.opening_control_reconciliation_items)
       OR EXISTS (SELECT 1 FROM public.reconciliation_commands)
       OR EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_command_consumptions
       ) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_downgrade_preflight() -> None:
    prefix = "public." if _dialect_name() == "postgresql" else ""
    table_names = (
        "reconciliation_runs",
        "reconciliation_items",
        "opening_control_reconciliation_runs",
        "opening_control_reconciliation_items",
        "reconciliation_commands",
        "opening_control_reconciliation_command_consumptions",
    )
    predicate = " OR ".join(
        f"EXISTS (SELECT 1 FROM {prefix}{name})" for name in table_names
    )
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {predicate}"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _create_command_consumption_table() -> None:
    op.create_table(
        "opening_control_reconciliation_command_consumptions",
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("target_version", sa.BigInteger(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('create_opening', 'explain_opening', "
            "'approve_opening')",
            name="ck_opening_control_reconciliation_consumptions_operation",
        ),
        sa.CheckConstraint(
            "target_version >= 0",
            name="ck_opening_control_reconciliation_consumptions_target_version",
        ),
        sa.PrimaryKeyConstraint(
            "command_id",
            name="pk_opening_control_reconciliation_command_consumptions",
        ),
        sa.UniqueConstraint(
            "command_id",
            "run_id",
            "operation",
            "target_version",
            name="uq_opening_control_reconciliation_consumptions_command",
        ),
        sa.UniqueConstraint(
            "run_id",
            "target_version",
            name="uq_opening_control_reconciliation_consumptions_target",
        ),
    )


def _create_command_table() -> None:
    op.create_table(
        "reconciliation_commands",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("target_version", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_reference", sa.String(160), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("request_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("result_jsonb", JSON_DOCUMENT, nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("actor_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('create_opening', 'explain_opening', "
            "'approve_opening')",
            name="ck_reconciliation_commands_operation",
        ),
        sa.CheckConstraint(
            "length(request_reference) = "
            f"{len('opening-reconciliation-request-') + 64} AND "
            "substr(request_reference, 1, "
            f"{len('opening-reconciliation-request-')}) = "
            "'opening-reconciliation-request-' AND "
            f"length({_lower_hex_remainder('substr(request_reference, ' + str(len('opening-reconciliation-request-') + 1) + ')')}) = 0",
            name="ck_reconciliation_commands_request_reference",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND "
            "length(request_hash) = 64 AND length(result_hash) = 64 AND "
            f"length({_lower_hex_remainder('idempotency_key_hash')}) = 0 AND "
            f"length({_lower_hex_remainder('request_hash')}) = 0 AND "
            f"length({_lower_hex_remainder('result_hash')}) = 0",
            name="ck_reconciliation_commands_hashes",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_reconciliation_commands_authorization_version",
        ),
        sa.CheckConstraint(
            "target_version >= 0",
            name="ck_reconciliation_commands_target_version",
        ),
        sa.CheckConstraint(
            "occurred_at = created_at",
            name="ck_reconciliation_commands_time_binding",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["reconciliation_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["id", "run_id", "operation", "target_version"],
            [
                "opening_control_reconciliation_command_consumptions.command_id",
                "opening_control_reconciliation_command_consumptions.run_id",
                "opening_control_reconciliation_command_consumptions.operation",
                "opening_control_reconciliation_command_consumptions.target_version",
            ],
            name="fk_reconciliation_commands_consumption",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_commands"),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_reconciliation_commands_idempotency",
        ),
        sa.UniqueConstraint(
            "id", "run_id", name="uq_reconciliation_commands_id_run"
        ),
        sa.UniqueConstraint(
            "id",
            "run_id",
            "operation",
            "target_version",
            name="uq_reconciliation_commands_consumption_binding",
        ),
        sa.UniqueConstraint(
            "run_id",
            "target_version",
            name="uq_reconciliation_commands_target",
        ),
    )
    op.create_index(
        "uq_reconciliation_commands_create_run",
        "reconciliation_commands",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("operation = 'create_opening'"),
        sqlite_where=sa.text("operation = 'create_opening'"),
    )
    op.create_index(
        "uq_reconciliation_commands_approve_run",
        "reconciliation_commands",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("operation = 'approve_opening'"),
        sqlite_where=sa.text("operation = 'approve_opening'"),
    )
    op.create_index(
        "ix_reconciliation_commands_run_time",
        "reconciliation_commands",
        ["run_id", "occurred_at"],
    )


def _create_tables() -> None:
    _create_command_consumption_table()
    _create_command_table()
    op.create_table(
        "opening_control_reconciliation_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("create_command_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("region_org_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("control_sync_run_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("item_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("created_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("created_authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("approved_by_user_id", sa.String(36), nullable=True),
        sa.Column("approved_by_person_id", sa.Uuid(), nullable=True),
        sa.Column("approved_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("approved_authorization_version", sa.BigInteger(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 0 AND item_count > 0",
            name="ck_opening_control_reconciliation_runs_counts",
        ),
        sa.CheckConstraint(
            "length(item_manifest_sha256) = 64 AND "
            f"length({_lower_hex_remainder('item_manifest_sha256')}) = 0",
            name="ck_opening_control_reconciliation_runs_manifest",
        ),
        sa.CheckConstraint(
            "created_authorization_version > 0 AND "
            "(approved_authorization_version IS NULL OR "
            "approved_authorization_version > 0)",
            name="ck_opening_control_reconciliation_runs_auth_versions",
        ),
        sa.CheckConstraint(
            "(approved_by_user_id IS NULL AND approved_by_person_id IS NULL "
            "AND approved_role_assignment_id IS NULL "
            "AND approved_authorization_version IS NULL "
            "AND approved_at IS NULL) OR "
            "(approved_by_user_id IS NOT NULL "
            "AND approved_by_person_id IS NOT NULL "
            "AND approved_role_assignment_id IS NOT NULL "
            "AND approved_authorization_version IS NOT NULL "
            "AND approved_at IS NOT NULL)",
            name="ck_opening_control_reconciliation_runs_approval_group",
        ),
        sa.CheckConstraint(
            "approved_at IS NULL OR approved_at >= created_at",
            name="ck_opening_control_reconciliation_runs_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["reconciliation_runs.id"],
            name="fk_opening_control_reconciliation_runs_run",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["create_command_id", "run_id"],
            ["reconciliation_commands.id", "reconciliation_commands.run_id"],
            name="fk_opening_control_reconciliation_runs_create_command",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["stocktake_tasks.id"],
            name="fk_opening_control_reconciliation_runs_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_opening_control_reconciliation_runs_round",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["region_org_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["posting_id", "task_id", "round_id"],
            [
                "stocktake_postings.id",
                "stocktake_postings.task_id",
                "stocktake_postings.round_id",
            ],
            name="fk_opening_control_reconciliation_runs_posting",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_sync_run_id"], ["sync_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approved_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", name="pk_opening_control_reconciliation_runs"
        ),
        sa.UniqueConstraint(
            "task_id", name="uq_opening_control_reconciliation_runs_task"
        ),
        sa.UniqueConstraint(
            "run_id",
            "task_id",
            "round_id",
            name="uq_opening_control_reconciliation_runs_binding",
        ),
    )
    op.create_index(
        "ix_opening_control_reconciliation_runs_region",
        "opening_control_reconciliation_runs",
        ["region_org_id", "created_at"],
    )
    op.create_index(
        "ix_opening_control_reconciliation_runs_posting",
        "opening_control_reconciliation_runs",
        ["posting_id"],
    )

    op.create_table(
        "opening_control_reconciliation_items",
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("control_snapshot_line_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("evidence_reference", sa.String(1000), nullable=False),
        sa.Column("evidence_file_sha256", sa.String(64), nullable=True),
        sa.Column("evidence_file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("evidence_file_mime_type", sa.String(160), nullable=True),
        sa.Column("explained_by_user_id", sa.String(36), nullable=True),
        sa.Column("explained_by_person_id", sa.Uuid(), nullable=True),
        sa.Column("explained_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("explanation_authorization_version", sa.BigInteger(), nullable=True),
        sa.Column("explained_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 0",
            name="ck_opening_control_reconciliation_items_version",
        ),
        sa.CheckConstraint(
            "length(evidence_reference) <= 1000",
            name="ck_opening_control_reconciliation_items_evidence_length",
        ),
        sa.CheckConstraint(
            "(evidence_file_sha256 IS NULL "
            "AND evidence_file_size_bytes IS NULL "
            "AND evidence_file_mime_type IS NULL) OR "
            "(evidence_file_sha256 IS NOT NULL "
            "AND length(evidence_file_sha256) = 64 "
            "AND evidence_file_size_bytes IS NOT NULL "
            "AND evidence_file_size_bytes >= 0 "
            "AND evidence_file_mime_type IS NOT NULL "
            "AND length(trim(evidence_file_mime_type)) > 0)",
            name="ck_opening_control_reconciliation_items_file_snapshot",
        ),
        sa.CheckConstraint(
            "explanation_authorization_version IS NULL OR "
            "explanation_authorization_version > 0",
            name="ck_opening_control_reconciliation_items_auth_version",
        ),
        sa.CheckConstraint(
            "(explained_by_user_id IS NULL AND explained_by_person_id IS NULL "
            "AND explained_role_assignment_id IS NULL "
            "AND explanation_authorization_version IS NULL "
            "AND explained_at IS NULL) OR "
            "(explained_by_user_id IS NOT NULL "
            "AND explained_by_person_id IS NOT NULL "
            "AND explained_role_assignment_id IS NOT NULL "
            "AND explanation_authorization_version IS NOT NULL "
            "AND explained_at IS NOT NULL)",
            name="ck_opening_control_reconciliation_items_explanation_group",
        ),
        sa.CheckConstraint(
            "explained_at IS NULL OR explained_at >= created_at",
            name="ck_opening_control_reconciliation_items_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["reconciliation_items.id"],
            name="fk_opening_control_reconciliation_items_item",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "task_id", "round_id"],
            [
                "opening_control_reconciliation_runs.run_id",
                "opening_control_reconciliation_runs.task_id",
                "opening_control_reconciliation_runs.round_id",
            ],
            name="fk_opening_control_reconciliation_items_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["difference_id", "task_id", "round_id"],
            [
                "stocktake_differences.id",
                "stocktake_differences.task_id",
                "stocktake_differences.round_id",
            ],
            name="fk_opening_control_reconciliation_items_difference",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_snapshot_line_id", "task_id"],
            [
                "stocktake_control_snapshot_lines.id",
                "stocktake_control_snapshot_lines.task_id",
            ],
            name="fk_opening_control_reconciliation_items_control",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["explained_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["explained_by_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["explained_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "item_id", name="pk_opening_control_reconciliation_items"
        ),
        sa.UniqueConstraint(
            "difference_id",
            name="uq_opening_control_reconciliation_items_difference",
        ),
    )
    op.create_index(
        "ix_opening_control_reconciliation_items_run",
        "opening_control_reconciliation_items",
        ["run_id", "item_id"],
    )


def _seed_permissions() -> None:
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String()),
        sa.column("action", sa.String()),
        sa.column("field_code", sa.String()),
        sa.column("description", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    timestamp = datetime(2026, 8, 31, tzinfo=timezone.utc)
    op.bulk_insert(
        permission_table,
        [
            {
                "id": permission_id,
                "resource": resource,
                "action": action,
                "field_code": "",
                "description": description,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            for permission_id, resource, action, description in PERMISSION_ROWS
        ],
    )
    op.bulk_insert(
        role_permission_table,
        [
            {
                "id": row_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "effect": "allow",
                "created_at": timestamp,
            }
            for row_id, role_id, permission_id in ROLE_PERMISSION_ROWS
        ],
    )


def _delete_permissions() -> None:
    role_permission_table = sa.table(
        "role_permissions", sa.column("id", sa.Uuid())
    )
    permission_table = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(
        role_permission_table.delete().where(
            role_permission_table.c.id.in_(
                tuple(row[0] for row in ROLE_PERMISSION_ROWS)
            )
        )
    )
    op.execute(
        permission_table.delete().where(
            permission_table.c.id.in_(tuple(row[0] for row in PERMISSION_ROWS))
        )
    )


def _create_postgresql_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_COMMAND_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    expected_role text;
    expected_scope_type text;
    expected_scope_id text;
    current_version bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        IF EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_command_consumptions
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NULL;
    END IF;
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.request_reference NOT LIKE 'opening-reconciliation-request-%'
       OR length(NEW.request_reference) <> 95 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.operation IN ('create_opening', 'approve_opening') THEN
        expected_role := 'admin';
        expected_scope_type := 'national';
        expected_scope_id := '*';
    ELSE
        expected_role := 'provincial_manager';
        expected_scope_type := 'organization';
        SELECT binding.region_org_id::text
          INTO expected_scope_id
          FROM public.opening_control_reconciliation_runs AS binding
         WHERE binding.run_id = NEW.run_id;
    END IF;

    IF expected_scope_id IS NULL OR NOT EXISTS (
        SELECT 1
          FROM public.role_assignments AS assignment
          JOIN public.roles AS role ON role.id = assignment.role_id
          JOIN public.users AS actor ON actor.id = assignment.user_id
          JOIN public.people AS person ON person.id = actor.person_id
          JOIN public.organizations AS organization
            ON organization.id = person.organization_id
         WHERE assignment.id = NEW.actor_role_assignment_id
           AND assignment.user_id = NEW.actor_user_id
           AND actor.person_id = NEW.actor_person_id
           AND actor.authorization_version = NEW.authorization_version
           AND actor.account_status = 'active'
           AND person.employment_status = 'active'
           AND organization.status = 'active'
           AND role.code = expected_role
           AND role.status = 'active'
           AND role.is_external = false
           AND assignment.scope_type = expected_scope_type
           AND assignment.scope_id = expected_scope_id
           AND assignment.status IN ('scheduled', 'active')
           AND assignment.valid_from <= NEW.occurred_at
           AND (assignment.valid_to IS NULL
                OR NEW.occurred_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL
                OR NEW.occurred_at < assignment.revoked_at)
           AND (expected_role <> 'admin'
                OR organization.org_type = 'headquarters')
           AND (expected_role <> 'provincial_manager'
                OR organization.org_type IN
                   ('headquarters', 'region_company', 'department'))
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT binding.version
      INTO current_version
      FROM public.opening_control_reconciliation_runs AS binding
     WHERE binding.run_id = NEW.run_id;
    IF current_version IS NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.operation <> 'create_opening' AND NOT EXISTS (
        SELECT 1
          FROM public.opening_control_reconciliation_runs AS binding
          JOIN public.reconciliation_commands AS create_command
            ON create_command.id = binding.create_command_id
           AND create_command.run_id = binding.run_id
          JOIN public.opening_control_reconciliation_command_consumptions AS create_seal
            ON create_seal.command_id = create_command.id
           AND create_seal.run_id = create_command.run_id
           AND create_seal.operation = create_command.operation
           AND create_seal.target_version = create_command.target_version
         WHERE binding.run_id = NEW.run_id
           AND create_command.operation = 'create_opening'
           AND create_command.target_version = 0
           AND create_command.actor_user_id = binding.created_by_user_id
           AND create_command.actor_person_id = binding.created_by_person_id
           AND create_command.actor_role_assignment_id =
               binding.created_role_assignment_id
           AND create_command.authorization_version =
               binding.created_authorization_version
           AND create_command.occurred_at = binding.created_at
           AND create_command.created_at = binding.created_at
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.operation = 'create_opening' THEN
        IF NEW.target_version <> 0 OR current_version <> 0 OR NOT EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_runs AS binding
              JOIN public.reconciliation_runs AS run
                ON run.id = binding.run_id
              JOIN public.stocktake_tasks AS task
                ON task.id = binding.task_id
              JOIN public.stocktake_rounds AS round_row
                ON round_row.id = binding.round_id
               AND round_row.task_id = task.id
              JOIN public.stocktake_postings AS posting
                ON posting.id = binding.posting_id
               AND posting.task_id = task.id
               AND posting.round_id = round_row.id
             WHERE binding.run_id = NEW.run_id
               AND binding.create_command_id = NEW.id
               AND run.status = 'differences'
               AND task.task_type = 'opening'
               AND task.status = 'posted'
               AND round_row.round_no = task.current_round_no
               AND posting.posting_kind = 'opening'
               AND binding.region_org_id = task.region_org_id
               AND binding.control_sync_run_id = task.control_sync_run_id
               AND run.source_system_id = task.control_source_system_id
               AND run.external_snapshot_at = task.control_snapshot_at
               AND binding.created_by_user_id = NEW.actor_user_id
               AND binding.created_by_person_id = NEW.actor_person_id
               AND binding.created_role_assignment_id =
                   NEW.actor_role_assignment_id
               AND binding.created_authorization_version =
                   NEW.authorization_version
               AND binding.created_at = NEW.occurred_at
               AND task.posted_at IS NOT NULL
               AND binding.created_at >= task.posted_at
               AND binding.updated_at = binding.created_at
               AND binding.approved_by_user_id IS NULL
               AND binding.approved_by_person_id IS NULL
               AND binding.approved_role_assignment_id IS NULL
               AND binding.approved_authorization_version IS NULL
               AND binding.approved_at IS NULL
               AND binding.approval_comment = ''
               AND binding.item_count = (
                    SELECT count(*)
                      FROM public.stocktake_differences AS difference
                     WHERE difference.task_id = task.id
                       AND difference.round_id = round_row.id
                       AND difference.difference_type = 'control_unassigned'
               )
               AND binding.item_count = (
                    SELECT count(*)
                      FROM public.reconciliation_items AS item
                     WHERE item.run_id = run.id
               )
               AND binding.item_count = (
                    SELECT count(*)
                      FROM public.opening_control_reconciliation_items AS item
                     WHERE item.run_id = run.id
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.stocktake_differences AS difference
                      JOIN public.stocktake_control_snapshot_lines AS control
                        ON control.id = difference.control_snapshot_line_id
                       AND control.task_id = difference.task_id
                      LEFT JOIN public.opening_control_reconciliation_items AS binding_item
                        ON binding_item.difference_id = difference.id
                       AND binding_item.run_id = run.id
                      LEFT JOIN public.reconciliation_items AS item
                        ON item.id = binding_item.item_id
                     WHERE difference.task_id = task.id
                       AND difference.round_id = round_row.id
                       AND difference.difference_type = 'control_unassigned'
                       AND (binding_item.item_id IS NULL
                            OR binding_item.task_id <> task.id
                            OR binding_item.round_id <> round_row.id
                            OR binding_item.control_snapshot_line_id <> control.id
                            OR binding_item.version <> 0
                            OR binding_item.evidence_reference <> ''
                            OR binding_item.evidence_file_sha256 IS NOT NULL
                            OR binding_item.evidence_file_size_bytes IS NOT NULL
                            OR binding_item.evidence_file_mime_type IS NOT NULL
                            OR binding_item.explained_by_user_id IS NOT NULL
                            OR binding_item.explained_by_person_id IS NOT NULL
                            OR binding_item.explained_role_assignment_id IS NOT NULL
                            OR binding_item.explanation_authorization_version IS NOT NULL
                            OR binding_item.explained_at IS NOT NULL
                            OR binding_item.created_at <> NEW.occurred_at
                            OR binding_item.updated_at <> binding_item.created_at
                            OR item.run_id <> run.id
                            OR item.business_key <> control.external_business_key
                            OR item.external_qty <> difference.book_qty
                            OR item.local_qty <> difference.counted_qty
                            OR item.difference <> difference.book_qty - difference.counted_qty
                            OR item.difference = 0
                            OR item.status <> 'difference'
                            OR item.explanation <> ''
                            OR item.evidence_file_id IS NOT NULL)
               )
               AND EXISTS (
                    SELECT 1
                      FROM public.inventory_opening_establishments AS establishment
                     WHERE establishment.task_id = task.id
                       AND establishment.round_id = round_row.id
                       AND establishment.posting_id = posting.id
                       AND establishment.has_pending_control_difference = true
                       AND run.local_ledger_cursor =
                           establishment.established_ledger_cursor::text
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.inventory_opening_establishments AS establishment
                     WHERE establishment.task_id = task.id
                       AND (establishment.round_id <> round_row.id
                            OR establishment.posting_id <> posting.id
                            OR establishment.has_pending_control_difference = false
                            OR run.local_ledger_cursor <>
                               establishment.established_ledger_cursor::text)
               )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF NEW.operation = 'explain_opening' THEN
        IF NEW.target_version <> current_version + 1 OR NOT EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_runs AS binding
              JOIN public.reconciliation_runs AS run ON run.id = binding.run_id
             WHERE binding.run_id = NEW.run_id
               AND run.status = 'differences'
               AND NEW.occurred_at >= binding.updated_at
               AND binding.approved_at IS NULL
               AND binding.approval_comment = ''
               AND binding.item_count > 0
               AND binding.item_count = (
                    SELECT count(*) FROM public.reconciliation_items AS item
                     WHERE item.run_id = run.id
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.reconciliation_items AS item
                      JOIN public.opening_control_reconciliation_items AS item_binding
                        ON item_binding.item_id = item.id
                     WHERE item.run_id = run.id
                       AND (item.status NOT IN ('difference', 'explained')
                            OR item_binding.version <> binding.version)
               )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        IF NEW.target_version <> current_version + 1 OR NOT EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_runs AS binding
              JOIN public.reconciliation_runs AS run ON run.id = binding.run_id
             WHERE binding.run_id = NEW.run_id
               AND run.status = 'differences'
               AND NEW.occurred_at >= binding.updated_at
               AND binding.approved_at IS NULL
               AND binding.approval_comment = ''
               AND binding.item_count > 0
               AND binding.item_count = (
                    SELECT count(*) FROM public.reconciliation_items AS item
                     WHERE item.run_id = run.id
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.reconciliation_items AS item
                      JOIN public.opening_control_reconciliation_items AS item_binding
                        ON item_binding.item_id = item.id
                     WHERE item.run_id = run.id
                       AND (item.status <> 'explained'
                            OR item_binding.version <> binding.version
                            OR length(trim(item.explanation)) < 4
                            OR length(trim(item_binding.evidence_reference)) < 4
                            OR item_binding.explained_by_user_id = NEW.actor_user_id
                            OR (item.evidence_file_id IS NULL AND
                                (item_binding.evidence_file_sha256 IS NOT NULL
                                 OR item_binding.evidence_file_size_bytes IS NOT NULL
                                 OR item_binding.evidence_file_mime_type IS NOT NULL))
                            OR (item.evidence_file_id IS NOT NULL AND NOT EXISTS (
                                SELECT 1 FROM public.files AS evidence
                                 WHERE evidence.id = item.evidence_file_id
                                   AND evidence.status = 'available'
                                   AND item_binding.evidence_file_sha256 = evidence.sha256
                                   AND item_binding.evidence_file_size_bytes =
                                       evidence.size_bytes
                                   AND item_binding.evidence_file_mime_type =
                                       evidence.mime_type)))
               )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[0]} "
        "BEFORE INSERT OR UPDATE OR DELETE ON public.reconciliation_commands "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_COMMAND_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[1]} "
        "BEFORE TRUNCATE ON public.reconciliation_commands "
        f"FOR EACH STATEMENT EXECUTE FUNCTION public.{PG_COMMAND_FUNCTION}()"
    )

    # Python hashes the compact, sorted, UTF-8 JSON representation.  PostgreSQL
    # jsonb already gives us typed values; this recursive renderer reproduces
    # that canonical representation for the command documents used here
    # (objects, arrays, strings, booleans, null and integral numbers).
    op.execute(
        f"""
CREATE FUNCTION public.{PG_CANONICAL_JSON_FUNCTION}(document jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $$
DECLARE
    document_type text;
    rendered text;
BEGIN
    document_type := jsonb_typeof(document);
    IF document_type = 'object' THEN
        SELECT '{{' || coalesce(
                   string_agg(
                       to_jsonb(entry.key)::text || ':' ||
                       public.{PG_CANONICAL_JSON_FUNCTION}(entry.value),
                       ',' ORDER BY entry.key
                   ),
                   ''
               ) || '}}'
          INTO rendered
          FROM jsonb_each(document) AS entry;
        RETURN rendered;
    ELSIF document_type = 'array' THEN
        SELECT '[' || coalesce(
                   string_agg(
                       public.{PG_CANONICAL_JSON_FUNCTION}(entry.value),
                       ',' ORDER BY entry.ordinality
                   ),
                   ''
               ) || ']'
          INTO rendered
          FROM jsonb_array_elements(document) WITH ORDINALITY AS entry(
              value, ordinality
          );
        RETURN rendered;
    END IF;
    RETURN document::text;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_EVENT_KEY_FUNCTION}(
    operation_name text,
    anchor text,
    suffix text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
SET search_path = pg_catalog, public
AS $$
SELECT 'opening-reconciliation-' || suffix || '-' || encode(
    sha256(
        convert_to(
            'cloud_oam.opening_control_reconciliation.event.v1', 'UTF8'
        ) || decode('00', 'hex') ||
        convert_to(operation_name, 'UTF8') || decode('00', 'hex') ||
        convert_to(anchor, 'UTF8') || decode('00', 'hex') ||
        convert_to(suffix, 'UTF8')
    ),
    'hex'
)
$$
"""
    )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_COMMAND_CONSUMPTION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    command_row public.reconciliation_commands%ROWTYPE;
    binding_row public.opening_control_reconciliation_runs%ROWTYPE;
    run_row public.reconciliation_runs%ROWTYPE;
    run_status text;
    event_type text;
    expected_state_count bigint;
    expected_scoped_state_count bigint;
    expected_before jsonb;
    expected_after jsonb;
    expected_item_versions jsonb;
    expected_role text;
    expected_scope_type text;
    expected_scope_id text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT command.* INTO command_row
      FROM public.reconciliation_commands AS command
     WHERE command.id = NEW.command_id
       AND command.run_id = NEW.run_id
       AND command.operation = NEW.operation
       AND command.target_version = NEW.target_version;
    SELECT binding.* INTO binding_row
      FROM public.opening_control_reconciliation_runs AS binding
     WHERE binding.run_id = NEW.run_id;
    SELECT run.* INTO run_row
      FROM public.reconciliation_runs AS run
     WHERE run.id = NEW.run_id;
    run_status := run_row.status;
    IF command_row.id IS NULL
       OR binding_row.run_id IS NULL
       OR NEW.consumed_at IS DISTINCT FROM command_row.occurred_at
       OR jsonb_typeof(command_row.request_jsonb) <> 'object'
       OR jsonb_typeof(command_row.result_jsonb) <> 'object'
       OR jsonb_typeof(command_row.request_jsonb -> 'actor') <> 'object'
       OR (SELECT count(*)
             FROM jsonb_object_keys(command_row.request_jsonb -> 'actor')) <> 3
       OR command_row.request_jsonb -> 'actor' -> 'user_id'
          IS DISTINCT FROM to_jsonb(command_row.actor_user_id)
       OR command_row.request_jsonb -> 'actor' -> 'person_id'
          IS DISTINCT FROM to_jsonb(command_row.actor_person_id::text)
       OR command_row.request_jsonb -> 'actor' -> 'authorization_version'
          IS DISTINCT FROM to_jsonb(command_row.authorization_version)
       OR command_row.request_jsonb -> 'actor' ->>
          'authorization_version' IS DISTINCT FROM
          command_row.authorization_version::text
       OR command_row.request_hash !~ '^[0-9a-f]{{64}}$'
       OR command_row.result_hash !~ '^[0-9a-f]{{64}}$'
       OR command_row.idempotency_key_hash !~ '^[0-9a-f]{{64}}$'
       OR command_row.request_reference !~
          '^opening-reconciliation-request-[0-9a-f]{{64}}$'
       OR encode(
              sha256(convert_to(
                  public.{PG_CANONICAL_JSON_FUNCTION}(
                      command_row.request_jsonb
                  ),
                  'UTF8'
              )),
              'hex'
          ) IS DISTINCT FROM command_row.request_hash
       OR encode(
              sha256(convert_to(
                  public.{PG_CANONICAL_JSON_FUNCTION}(
                      command_row.result_jsonb
                  ),
                  'UTF8'
              )),
              'hex'
          ) IS DISTINCT FROM command_row.result_hash
       OR (SELECT count(*)
             FROM jsonb_object_keys(command_row.result_jsonb)) <> 7
       OR command_row.result_jsonb -> 'reconciliation_run_id'
          IS DISTINCT FROM to_jsonb(NEW.run_id::text)
       OR command_row.result_jsonb -> 'task_id'
          IS DISTINCT FROM to_jsonb(binding_row.task_id::text)
       OR command_row.result_jsonb -> 'version' IS DISTINCT FROM
          to_jsonb(NEW.target_version)
       OR command_row.result_jsonb ->> 'version' IS DISTINCT FROM
          NEW.target_version::text THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    event_type := 'reconciliation.opening.' ||
                  replace(NEW.operation, '_opening', '');
    IF NEW.operation = 'explain_opening' THEN
        expected_role := 'provincial_manager';
        expected_scope_type := 'organization';
        expected_scope_id := binding_row.region_org_id::text;
    ELSE
        expected_role := 'admin';
        expected_scope_type := 'national';
        expected_scope_id := '*';
    END IF;

    IF NEW.operation = 'create_opening' THEN
        expected_state_count := 1;
        expected_scoped_state_count := 1;
        expected_before := jsonb_build_object(
            'status', NULL, 'version', NULL
        );
        IF (SELECT count(*)
              FROM jsonb_object_keys(command_row.request_jsonb)) <> 4
           OR command_row.request_jsonb -> 'schema' IS DISTINCT FROM
              to_jsonb('cloud_oam.opening_control_reconciliation.create.v1'::text)
           OR command_row.request_jsonb -> 'task_id' IS DISTINCT FROM
              to_jsonb(binding_row.task_id::text)
           OR command_row.request_jsonb -> 'expected_task_version'
              IS DISTINCT FROM (
                  SELECT to_jsonb(task.version)
                    FROM public.stocktake_tasks AS task
                   WHERE task.id = binding_row.task_id
              )
           OR command_row.request_jsonb ->> 'expected_task_version'
              IS DISTINCT FROM (
                  SELECT task.version::text
                    FROM public.stocktake_tasks AS task
                   WHERE task.id = binding_row.task_id
              )
           OR command_row.result_jsonb -> 'schema' IS DISTINCT FROM
              to_jsonb(
                  'cloud_oam.opening_control_reconciliation.create_result.v1'::text
              )
           OR command_row.result_jsonb -> 'status' IS DISTINCT FROM
              to_jsonb('differences'::text)
           OR command_row.result_jsonb -> 'item_count' IS DISTINCT FROM
              to_jsonb(binding_row.item_count)
           OR command_row.result_jsonb ->> 'item_count' IS DISTINCT FROM
              binding_row.item_count::text
           OR command_row.result_jsonb ->> 'created_at' IS DISTINCT FROM
              to_char(
                  command_row.occurred_at AT TIME ZONE 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
              )
           OR NEW.target_version <> 0
           OR binding_row.create_command_id <> command_row.id
           OR binding_row.version <> 0
           OR run_status <> 'differences'
           OR binding_row.created_by_user_id <> command_row.actor_user_id
           OR binding_row.created_by_person_id <> command_row.actor_person_id
           OR binding_row.created_role_assignment_id <>
              command_row.actor_role_assignment_id
           OR binding_row.created_authorization_version <>
              command_row.authorization_version
           OR binding_row.created_at <> command_row.occurred_at
           OR NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_tasks AS task
                 WHERE task.id = binding_row.task_id
                   AND task.posted_at IS NOT NULL
                   AND binding_row.created_at >= task.posted_at
           )
           OR binding_row.updated_at <> binding_row.created_at
           OR run_row.started_at <> command_row.occurred_at
           OR run_row.completed_at <> command_row.occurred_at
           OR run_row.created_at <> command_row.occurred_at
           OR run_row.updated_at <> command_row.occurred_at
           OR binding_row.item_manifest_sha256 IS DISTINCT FROM (
                SELECT encode(
                    sha256(convert_to(
                        public.{PG_CANONICAL_JSON_FUNCTION}(
                            jsonb_build_object(
                                'schema',
                                'cloud_oam.opening_control_reconciliation.items.v1',
                                'task_id', binding_row.task_id::text,
                                'round_id', binding_row.round_id::text,
                                'items', coalesce(
                                    jsonb_agg(
                                        jsonb_build_object(
                                            'difference_id', difference.id::text,
                                            'difference_no', difference.difference_no,
                                            'control_snapshot_line_id',
                                                control.id::text,
                                            'business_key',
                                                control.external_business_key,
                                            'material_id',
                                                difference.material_id::text,
                                            'external_qty', to_char(
                                                difference.book_qty,
                                                'FM999999999999990.000'
                                            ),
                                            'local_qty', to_char(
                                                difference.counted_qty,
                                                'FM999999999999990.000'
                                            ),
                                            'difference', to_char(
                                                difference.book_qty -
                                                    difference.counted_qty,
                                                'FM999999999999990.000'
                                            )
                                        ) ORDER BY difference.difference_no
                                    ),
                                    '[]'::jsonb
                                )
                            )
                        ),
                        'UTF8'
                    )),
                    'hex'
                )
                  FROM public.stocktake_differences AS difference
                  JOIN public.stocktake_control_snapshot_lines AS control
                    ON control.id = difference.control_snapshot_line_id
                   AND control.task_id = difference.task_id
                 WHERE difference.task_id = binding_row.task_id
                   AND difference.round_id = binding_row.round_id
                   AND difference.difference_type = 'control_unassigned'
           )
           OR binding_row.item_count <= 0
           OR binding_row.item_count <> (
                SELECT count(*) FROM public.reconciliation_items AS item
                 WHERE item.run_id = NEW.run_id
           )
           OR binding_row.item_count <> (
                SELECT count(*)
                  FROM public.opening_control_reconciliation_items AS item
                 WHERE item.run_id = NEW.run_id
           )
           OR EXISTS (
                SELECT 1
                  FROM public.reconciliation_items AS item
                  JOIN public.opening_control_reconciliation_items AS item_binding
                    ON item_binding.item_id = item.id
                   AND item_binding.run_id = item.run_id
                 WHERE item.run_id = NEW.run_id
                   AND (item.status <> 'difference'
                        OR item.explanation <> ''
                        OR item.evidence_file_id IS NOT NULL
                        OR item_binding.version <> 0
                        OR item_binding.evidence_reference <> ''
                        OR item_binding.evidence_file_sha256 IS NOT NULL
                        OR item_binding.evidence_file_size_bytes IS NOT NULL
                        OR item_binding.evidence_file_mime_type IS NOT NULL
                        OR item_binding.explained_by_user_id IS NOT NULL
                        OR item_binding.explained_by_person_id IS NOT NULL
                        OR item_binding.explained_role_assignment_id IS NOT NULL
                        OR item_binding.explanation_authorization_version IS NOT NULL
                        OR item_binding.explained_at IS NOT NULL)
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        IF (SELECT count(*)
              FROM jsonb_object_keys(command_row.request_jsonb)) <> 5
           OR command_row.request_jsonb -> 'reconciliation_run_id'
              IS DISTINCT FROM to_jsonb(NEW.run_id::text)
           OR command_row.request_jsonb -> 'expected_version'
              IS DISTINCT FROM to_jsonb(NEW.target_version - 1)
           OR command_row.request_jsonb ->> 'expected_version'
              IS DISTINCT FROM (NEW.target_version - 1)::text THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        SELECT coalesce(
                   jsonb_object_agg(
                       item.id::text,
                       NEW.target_version - 1
                       ORDER BY item.id::text
                   ),
                   '{{}}'::jsonb
               )
          INTO expected_item_versions
          FROM public.reconciliation_items AS item
         WHERE item.run_id = NEW.run_id;
        expected_before := jsonb_build_object(
            'status', 'differences',
            'version', NEW.target_version - 1,
            'item_versions', expected_item_versions
        );
        IF NEW.target_version <= 0
           OR binding_row.version <> NEW.target_version
           OR binding_row.updated_at <> command_row.occurred_at
           OR binding_row.item_count <= 0
           OR binding_row.item_count <> (
                SELECT count(*) FROM public.reconciliation_items AS item
                 WHERE item.run_id = NEW.run_id
           )
           OR binding_row.item_count <> (
                SELECT count(*)
                  FROM public.opening_control_reconciliation_items AS item
                 WHERE item.run_id = NEW.run_id
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;

        IF NEW.operation = 'explain_opening' THEN
            expected_state_count := CASE
                WHEN NEW.target_version = 1 THEN binding_row.item_count
                ELSE 0
            END;
            expected_scoped_state_count := binding_row.item_count;
            IF jsonb_typeof(command_row.request_jsonb -> 'items') <> 'array' THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
            IF command_row.request_jsonb -> 'schema' IS DISTINCT FROM
                  to_jsonb(
                      'cloud_oam.opening_control_reconciliation.explain.v1'::text
                  )
               OR jsonb_array_length(
                      command_row.request_jsonb -> 'items'
                  ) <> binding_row.item_count
               OR EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(
                               command_row.request_jsonb -> 'items'
                           ) AS supplied(value)
                     WHERE jsonb_typeof(supplied.value) <> 'object'
                        OR (SELECT count(*)
                              FROM jsonb_object_keys(supplied.value)) <> 5
                        OR NOT EXISTS (
                            SELECT 1
                              FROM public.reconciliation_items AS item
                              JOIN public.opening_control_reconciliation_items
                                   AS item_binding
                                ON item_binding.item_id = item.id
                               AND item_binding.run_id = item.run_id
                             WHERE item.run_id = NEW.run_id
                               AND supplied.value -> 'reconciliation_item_id'
                                   IS NOT DISTINCT FROM to_jsonb(item.id::text)
                               AND supplied.value -> 'expected_version'
                                   IS NOT DISTINCT FROM
                                       to_jsonb(NEW.target_version - 1)
                               AND supplied.value ->> 'expected_version' =
                                   (NEW.target_version - 1)::text
                               AND supplied.value -> 'explanation'
                                   IS NOT DISTINCT FROM
                                       to_jsonb(item.explanation)
                               AND supplied.value -> 'evidence_reference'
                                   IS NOT DISTINCT FROM
                                       to_jsonb(item_binding.evidence_reference)
                               AND (
                                   (item.evidence_file_id IS NULL
                                   AND jsonb_typeof(
                                        supplied.value -> 'evidence_file_id'
                                    ) = 'null')
                                   OR (item.evidence_file_id IS NOT NULL
                                       AND supplied.value -> 'evidence_file_id'
                                           IS NOT DISTINCT FROM
                                               to_jsonb(
                                                   item.evidence_file_id::text
                                               ))
                               )
                        )
               )
               OR EXISTS (
                    SELECT 1
                      FROM public.reconciliation_items AS item
                      JOIN public.opening_control_reconciliation_items
                           AS item_binding
                        ON item_binding.item_id = item.id
                       AND item_binding.run_id = item.run_id
                     WHERE item.run_id = NEW.run_id
                       AND NOT EXISTS (
                           SELECT 1
                             FROM jsonb_array_elements(
                                      command_row.request_jsonb -> 'items'
                                  ) AS supplied(value)
                            WHERE supplied.value -> 'reconciliation_item_id'
                                  IS NOT DISTINCT FROM to_jsonb(item.id::text)
                       )
               )
               OR command_row.result_jsonb -> 'schema' IS DISTINCT FROM
                  to_jsonb(
                      'cloud_oam.opening_control_reconciliation.explain_result.v1'::text
                  )
               OR command_row.result_jsonb -> 'status' IS DISTINCT FROM
                  to_jsonb('differences'::text)
               OR command_row.result_jsonb -> 'explained_item_count'
                  IS DISTINCT FROM to_jsonb(binding_row.item_count)
               OR command_row.result_jsonb ->> 'explained_item_count'
                  IS DISTINCT FROM binding_row.item_count::text
               OR command_row.result_jsonb ->> 'explained_at' IS DISTINCT FROM
                  to_char(
                      command_row.occurred_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                  )
               OR run_status <> 'differences'
               OR binding_row.approved_at IS NOT NULL
               OR EXISTS (
                    SELECT 1
                      FROM public.reconciliation_items AS item
                      JOIN public.opening_control_reconciliation_items AS item_binding
                        ON item_binding.item_id = item.id
                       AND item_binding.run_id = item.run_id
                     WHERE item.run_id = NEW.run_id
                       AND (item.status <> 'explained'
                            OR item.updated_at <> command_row.occurred_at
                            OR length(trim(item.explanation)) < 4
                            OR item.explanation <> trim(item.explanation)
                            OR item_binding.version <> NEW.target_version
                            OR item_binding.updated_at <> command_row.occurred_at
                            OR length(trim(item_binding.evidence_reference)) < 4
                            OR item_binding.evidence_reference <>
                               trim(item_binding.evidence_reference)
                            OR item_binding.explained_by_user_id <>
                               command_row.actor_user_id
                            OR item_binding.explained_by_person_id <>
                               command_row.actor_person_id
                            OR item_binding.explained_role_assignment_id <>
                               command_row.actor_role_assignment_id
                            OR item_binding.explanation_authorization_version <>
                               command_row.authorization_version
                            OR item_binding.explained_at <>
                               command_row.occurred_at
                            OR (item.evidence_file_id IS NULL AND
                                (item_binding.evidence_file_sha256 IS NOT NULL
                                 OR item_binding.evidence_file_size_bytes IS NOT NULL
                                 OR item_binding.evidence_file_mime_type IS NOT NULL))
                            OR (item.evidence_file_id IS NOT NULL AND NOT EXISTS (
                                SELECT 1 FROM public.files AS evidence
                                 WHERE evidence.id = item.evidence_file_id
                                   AND evidence.status = 'available'
                                   AND item_binding.evidence_file_sha256 = evidence.sha256
                                   AND item_binding.evidence_file_size_bytes =
                                       evidence.size_bytes
                                   AND item_binding.evidence_file_mime_type =
                                       evidence.mime_type)))
               ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSE
            expected_state_count := binding_row.item_count + 1;
            expected_scoped_state_count := expected_state_count;
            IF command_row.request_jsonb -> 'schema' IS DISTINCT FROM
                  to_jsonb(
                      'cloud_oam.opening_control_reconciliation.approve.v1'::text
                  )
               OR command_row.request_jsonb -> 'comment' IS DISTINCT FROM
                  to_jsonb(binding_row.approval_comment)
               OR command_row.result_jsonb -> 'schema' IS DISTINCT FROM
                  to_jsonb(
                      'cloud_oam.opening_control_reconciliation.approve_result.v1'::text
                  )
               OR command_row.result_jsonb -> 'status' IS DISTINCT FROM
                  to_jsonb('approved'::text)
               OR command_row.result_jsonb -> 'resolved_item_count'
                  IS DISTINCT FROM to_jsonb(binding_row.item_count)
               OR command_row.result_jsonb ->> 'resolved_item_count'
                  IS DISTINCT FROM binding_row.item_count::text
               OR command_row.result_jsonb ->> 'approved_at' IS DISTINCT FROM
                  to_char(
                      command_row.occurred_at AT TIME ZONE 'UTC',
                      'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                  )
               OR run_status <> 'approved'
               OR binding_row.approved_by_user_id <> command_row.actor_user_id
               OR binding_row.approved_by_person_id <> command_row.actor_person_id
               OR binding_row.approved_role_assignment_id <>
                  command_row.actor_role_assignment_id
               OR binding_row.approved_authorization_version <>
                  command_row.authorization_version
               OR binding_row.approved_at <> command_row.occurred_at
               OR run_row.updated_at <> command_row.occurred_at
               OR EXISTS (
                    SELECT 1
                      FROM public.reconciliation_items AS item
                      JOIN public.opening_control_reconciliation_items AS item_binding
                        ON item_binding.item_id = item.id
                       AND item_binding.run_id = item.run_id
                     WHERE item.run_id = NEW.run_id
                       AND (item.status <> 'resolved'
                            OR item.updated_at <> command_row.occurred_at
                            OR item_binding.version <> NEW.target_version
                            OR item_binding.updated_at <> command_row.occurred_at)
               ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        END IF;
    END IF;

    expected_after := jsonb_build_object(
        'result', command_row.result_jsonb,
        'result_hash', command_row.result_hash,
        'actor_person_id', command_row.actor_person_id::text,
        'actor_role_assignment_id', command_row.actor_role_assignment_id::text,
        'actor_role_code', expected_role,
        'actor_scope_type', expected_scope_type,
        'actor_scope_id', expected_scope_id,
        'authorization_version', command_row.authorization_version
    );

    IF expected_state_count <> (
        SELECT count(*)
          FROM public.state_transition_events AS transition
         WHERE transition.reason = event_type
           AND transition.actor_id = command_row.actor_user_id
           AND transition.occurred_at = command_row.occurred_at
           AND transition.created_at = command_row.occurred_at
           AND transition.metadata_jsonb = jsonb_build_object(
                'reconciliation_run_id', NEW.run_id::text,
                'result_hash', command_row.result_hash
           )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF expected_scoped_state_count <> (
        SELECT count(*)
          FROM public.state_transition_events AS transition
         WHERE transition.reason = event_type
           AND transition.metadata_jsonb ->> 'reconciliation_run_id' =
               NEW.run_id::text
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.operation = 'create_opening' AND NOT EXISTS (
        SELECT 1 FROM public.state_transition_events AS transition
         WHERE transition.reason = event_type
           AND transition.aggregate_type = 'reconciliation_run'
           AND transition.aggregate_id = NEW.run_id::text
           AND transition.from_status IS NULL
           AND transition.to_status = 'differences'
           AND transition.actor_id = command_row.actor_user_id
           AND transition.idempotency_key =
               public.{PG_EVENT_KEY_FUNCTION}(
                   NEW.operation,
                   command_row.idempotency_key_hash,
                   'state-1'
               )
           AND transition.occurred_at = command_row.occurred_at
           AND transition.metadata_jsonb ->> 'result_hash' =
               command_row.result_hash
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.operation = 'explain_opening'
       AND NEW.target_version = 1
       AND EXISTS (
            SELECT 1 FROM public.reconciliation_items AS item
             WHERE item.run_id = NEW.run_id
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.state_transition_events AS transition
                     WHERE transition.reason = event_type
                       AND transition.aggregate_type = 'reconciliation_item'
                       AND transition.aggregate_id = item.id::text
                       AND transition.from_status = 'difference'
                       AND transition.to_status = 'explained'
                       AND transition.actor_id = command_row.actor_user_id
                       AND transition.idempotency_key =
                           public.{PG_EVENT_KEY_FUNCTION}(
                               NEW.operation,
                               command_row.idempotency_key_hash,
                               'state-' || (
                                   1 + (
                                       SELECT count(*)
                                         FROM public.reconciliation_items AS prior
                                        WHERE prior.run_id = item.run_id
                                          AND prior.id < item.id
                                   )
                               )::text
                           )
                       AND transition.occurred_at = command_row.occurred_at
                       AND transition.metadata_jsonb ->> 'result_hash' =
                           command_row.result_hash
               )
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NEW.operation = 'approve_opening' AND (
        NOT EXISTS (
            SELECT 1 FROM public.state_transition_events AS transition
             WHERE transition.reason = event_type
               AND transition.aggregate_type = 'reconciliation_run'
               AND transition.aggregate_id = NEW.run_id::text
               AND transition.from_status = 'differences'
               AND transition.to_status = 'approved'
               AND transition.actor_id = command_row.actor_user_id
               AND transition.idempotency_key =
                   public.{PG_EVENT_KEY_FUNCTION}(
                       NEW.operation,
                       command_row.idempotency_key_hash,
                       'state-' || (binding_row.item_count + 1)::text
                   )
               AND transition.occurred_at = command_row.occurred_at
               AND transition.metadata_jsonb ->> 'result_hash' =
                   command_row.result_hash
        ) OR EXISTS (
            SELECT 1 FROM public.reconciliation_items AS item
             WHERE item.run_id = NEW.run_id
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.state_transition_events AS transition
                     WHERE transition.reason = event_type
                       AND transition.aggregate_type = 'reconciliation_item'
                       AND transition.aggregate_id = item.id::text
                       AND transition.from_status = 'explained'
                       AND transition.to_status = 'resolved'
                       AND transition.actor_id = command_row.actor_user_id
                       AND transition.idempotency_key =
                           public.{PG_EVENT_KEY_FUNCTION}(
                               NEW.operation,
                               command_row.idempotency_key_hash,
                               'state-' || (
                                   1 + (
                                       SELECT count(*)
                                         FROM public.reconciliation_items AS prior
                                        WHERE prior.run_id = item.run_id
                                          AND prior.id < item.id
                                   )
                               )::text
                           )
                       AND transition.occurred_at = command_row.occurred_at
                       AND transition.metadata_jsonb ->> 'result_hash' =
                           command_row.result_hash
               )
        )
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF 1 <> (
        SELECT count(*) FROM public.outbox_events AS outbox
         WHERE outbox.event_type = event_type
           AND outbox.aggregate_type = 'reconciliation_run'
           AND outbox.aggregate_id = NEW.run_id::text
           AND outbox.idempotency_key =
               public.{PG_EVENT_KEY_FUNCTION}(
                   NEW.operation,
                   command_row.idempotency_key_hash,
                   'outbox'
               )
           AND outbox.payload_jsonb = jsonb_build_object(
                'reconciliation_run_id', NEW.run_id::text,
                'result', command_row.result_jsonb,
                'result_hash', command_row.result_hash
           )
           AND outbox.status = 'pending'
           AND outbox.attempts = 0
           AND outbox.available_at = command_row.occurred_at
           AND outbox.created_at = command_row.occurred_at
           AND outbox.updated_at = command_row.occurred_at
           AND outbox.locked_at IS NULL
           AND outbox.locked_by IS NULL
           AND outbox.published_at IS NULL
           AND outbox.last_error IS NULL
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF (
        SELECT count(*) FROM public.reconciliation_commands AS command
         WHERE command.run_id = NEW.run_id
           AND command.operation = NEW.operation
    ) <> (
        SELECT count(*) FROM public.outbox_events AS outbox
         WHERE outbox.event_type = event_type
           AND outbox.aggregate_type = 'reconciliation_run'
           AND outbox.aggregate_id = NEW.run_id::text
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF 1 <> (
        SELECT count(*) FROM public.audit_events AS audit
         WHERE audit.stream_key = 'inventory'
           AND audit.actor_user_id = command_row.actor_user_id
           AND audit.action = event_type
           AND audit.aggregate_type = 'reconciliation_run'
           AND audit.aggregate_id = NEW.run_id::text
           AND audit.before_jsonb = expected_before
           AND audit.after_jsonb = expected_after
           AND audit.request_id = command_row.request_reference
           AND audit.occurred_at = command_row.occurred_at
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF (
        SELECT count(*) FROM public.reconciliation_commands AS command
         WHERE command.run_id = NEW.run_id
           AND command.operation = NEW.operation
    ) <> (
        SELECT count(*) FROM public.audit_events AS audit
         WHERE audit.stream_key = 'inventory'
           AND audit.action = event_type
           AND audit.aggregate_type = 'reconciliation_run'
           AND audit.aggregate_id = NEW.run_id::text
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[16]} "
        "BEFORE INSERT OR UPDATE OR DELETE ON "
        "public.opening_control_reconciliation_command_consumptions "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_COMMAND_CONSUMPTION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[17]} "
        "BEFORE TRUNCATE ON "
        "public.opening_control_reconciliation_command_consumptions "
        "FOR EACH STATEMENT EXECUTE FUNCTION "
        f"public.{PG_COMMAND_CONSUMPTION_FUNCTION}()"
    )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_EFFECT_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    old_run_id text;
    old_event_type text;
    new_run_id text;
    new_event_type text;
    operation_name text;
    command_row public.reconciliation_commands%ROWTYPE;
    binding_row public.opening_control_reconciliation_runs%ROWTYPE;
    expected_before jsonb;
    expected_after jsonb;
    expected_item_versions jsonb;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        IF TG_TABLE_NAME = 'state_transition_events' THEN
            old_run_id := OLD.metadata_jsonb ->> 'reconciliation_run_id';
            old_event_type := OLD.reason;
        ELSIF TG_TABLE_NAME = 'outbox_events' THEN
            IF OLD.aggregate_type = 'reconciliation_run' THEN
                old_run_id := OLD.aggregate_id;
            END IF;
            old_event_type := OLD.event_type;
        ELSE
            IF OLD.stream_key = 'inventory'
               AND OLD.aggregate_type = 'reconciliation_run' THEN
                old_run_id := OLD.aggregate_id;
            END IF;
            old_event_type := OLD.action;
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        IF TG_TABLE_NAME = 'state_transition_events' THEN
            new_run_id := NEW.metadata_jsonb ->> 'reconciliation_run_id';
            new_event_type := NEW.reason;
        ELSIF TG_TABLE_NAME = 'outbox_events' THEN
            IF NEW.aggregate_type = 'reconciliation_run' THEN
                new_run_id := NEW.aggregate_id;
            END IF;
            new_event_type := NEW.event_type;
        ELSE
            IF NEW.stream_key = 'inventory'
               AND NEW.aggregate_type = 'reconciliation_run' THEN
                new_run_id := NEW.aggregate_id;
            END IF;
            new_event_type := NEW.action;
        END IF;
    END IF;

    IF TG_OP <> 'INSERT' AND (
        old_event_type IN (
            'reconciliation.opening.create',
            'reconciliation.opening.explain',
            'reconciliation.opening.approve'
        )
        OR new_event_type IN (
            'reconciliation.opening.create',
            'reconciliation.opening.explain',
            'reconciliation.opening.approve'
        )
    ) THEN
        IF TG_TABLE_NAME = 'outbox_events' AND TG_OP = 'UPDATE'
           AND NEW.id IS NOT DISTINCT FROM OLD.id
           AND NEW.event_type IS NOT DISTINCT FROM OLD.event_type
           AND NEW.aggregate_type IS NOT DISTINCT FROM OLD.aggregate_type
           AND NEW.aggregate_id IS NOT DISTINCT FROM OLD.aggregate_id
           AND NEW.payload_jsonb IS NOT DISTINCT FROM OLD.payload_jsonb
           AND NEW.idempotency_key IS NOT DISTINCT FROM OLD.idempotency_key
           AND NEW.available_at IS NOT DISTINCT FROM OLD.available_at
           AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RETURN NEW;
    END IF;

    SELECT binding.* INTO binding_row
      FROM public.opening_control_reconciliation_runs AS binding
     WHERE binding.run_id::text = new_run_id;
    IF new_event_type NOT IN (
        'reconciliation.opening.create',
        'reconciliation.opening.explain',
        'reconciliation.opening.approve'
    ) THEN
        RETURN NEW;
    END IF;
    IF binding_row.run_id IS NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    operation_name := replace(
        replace(new_event_type, 'reconciliation.opening.', ''),
        'create', 'create_opening'
    );
    IF operation_name = 'explain' THEN
        operation_name := 'explain_opening';
    ELSIF operation_name = 'approve' THEN
        operation_name := 'approve_opening';
    END IF;

    IF TG_TABLE_NAME = 'state_transition_events' THEN
        SELECT command.* INTO command_row
          FROM public.reconciliation_commands AS command
         WHERE command.run_id = binding_row.run_id
           AND command.operation = operation_name
           AND command.actor_user_id = NEW.actor_id
           AND command.occurred_at = NEW.occurred_at
           AND NEW.created_at = command.occurred_at
           AND NEW.metadata_jsonb = jsonb_build_object(
                   'reconciliation_run_id', command.run_id::text,
                   'result_hash', command.result_hash
               );
    ELSIF TG_TABLE_NAME = 'outbox_events' THEN
        SELECT command.* INTO command_row
          FROM public.reconciliation_commands AS command
         WHERE command.run_id = binding_row.run_id
           AND command.operation = operation_name
           AND NEW.payload_jsonb = jsonb_build_object(
                   'reconciliation_run_id', command.run_id::text,
                   'result', command.result_jsonb,
                   'result_hash', command.result_hash
               )
           AND NEW.available_at = command.occurred_at
           AND NEW.created_at = command.occurred_at;
    ELSE
        SELECT command.* INTO command_row
          FROM public.reconciliation_commands AS command
         WHERE command.run_id = binding_row.run_id
           AND command.operation = operation_name
           AND command.actor_user_id = NEW.actor_user_id
           AND command.request_reference = NEW.request_id
           AND command.occurred_at = NEW.occurred_at
           AND NEW.after_jsonb -> 'result' = command.result_jsonb
           AND NEW.after_jsonb ->> 'result_hash' = command.result_hash;
    END IF;
    IF command_row.id IS NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.opening_control_reconciliation_command_consumptions
               AS consumption
         WHERE consumption.command_id = command_row.id
           AND consumption.run_id = command_row.run_id
           AND consumption.operation = command_row.operation
           AND consumption.target_version = command_row.target_version
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF TG_TABLE_NAME = 'state_transition_events' THEN
        IF operation_name = 'create_opening' THEN
            IF NEW.aggregate_type <> 'reconciliation_run'
               OR NEW.aggregate_id <> command_row.run_id::text
               OR NEW.from_status IS NOT NULL
               OR NEW.to_status <> 'differences'
               OR NEW.idempotency_key <>
                  public.{PG_EVENT_KEY_FUNCTION}(
                      operation_name,
                      command_row.idempotency_key_hash,
                      'state-1'
                  ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF operation_name = 'explain_opening' THEN
            IF command_row.target_version <> 1 OR NOT EXISTS (
                SELECT 1
                  FROM public.reconciliation_items AS item
                 WHERE item.run_id = command_row.run_id
                   AND NEW.aggregate_type = 'reconciliation_item'
                   AND NEW.aggregate_id = item.id::text
                   AND NEW.from_status = 'difference'
                   AND NEW.to_status = 'explained'
                   AND NEW.idempotency_key =
                       public.{PG_EVENT_KEY_FUNCTION}(
                           operation_name,
                           command_row.idempotency_key_hash,
                           'state-' || (
                               1 + (
                                   SELECT count(*)
                                     FROM public.reconciliation_items AS prior
                                    WHERE prior.run_id = item.run_id
                                      AND prior.id < item.id
                               )
                           )::text
                       )
            ) THEN
                RAISE EXCEPTION '{GUARD_ERROR}';
            END IF;
        ELSIF NOT (
            (NEW.aggregate_type = 'reconciliation_run'
             AND NEW.aggregate_id = command_row.run_id::text
             AND NEW.from_status = 'differences'
             AND NEW.to_status = 'approved'
             AND NEW.idempotency_key = public.{PG_EVENT_KEY_FUNCTION}(
                 operation_name,
                 command_row.idempotency_key_hash,
                 'state-' || (binding_row.item_count + 1)::text
             ))
            OR EXISTS (
                SELECT 1
                  FROM public.reconciliation_items AS item
                 WHERE item.run_id = command_row.run_id
                   AND NEW.aggregate_type = 'reconciliation_item'
                   AND NEW.aggregate_id = item.id::text
                   AND NEW.from_status = 'explained'
                   AND NEW.to_status = 'resolved'
                   AND NEW.idempotency_key =
                       public.{PG_EVENT_KEY_FUNCTION}(
                           operation_name,
                           command_row.idempotency_key_hash,
                           'state-' || (
                               1 + (
                                   SELECT count(*)
                                     FROM public.reconciliation_items AS prior
                                    WHERE prior.run_id = item.run_id
                                      AND prior.id < item.id
                               )
                           )::text
                       )
            )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF TG_TABLE_NAME = 'outbox_events' THEN
        IF NEW.aggregate_type <> 'reconciliation_run'
           OR NEW.aggregate_id <> command_row.run_id::text
           OR NEW.idempotency_key <> public.{PG_EVENT_KEY_FUNCTION}(
               operation_name, command_row.idempotency_key_hash, 'outbox'
           )
           OR NEW.status <> 'pending'
           OR NEW.attempts <> 0
           OR NEW.updated_at <> command_row.occurred_at
           OR NEW.locked_at IS NOT NULL
           OR NEW.locked_by IS NOT NULL
           OR NEW.published_at IS NOT NULL
           OR NEW.last_error IS NOT NULL THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        SELECT coalesce(
                   jsonb_object_agg(
                       item.id::text,
                       command_row.target_version - 1
                       ORDER BY item.id::text
                   ),
                   '{{}}'::jsonb
               )
          INTO expected_item_versions
          FROM public.reconciliation_items AS item
         WHERE item.run_id = command_row.run_id;
        expected_before := CASE
            WHEN operation_name = 'create_opening' THEN
                jsonb_build_object('status', NULL, 'version', NULL)
            ELSE jsonb_build_object(
                'status', 'differences',
                'version', command_row.target_version - 1,
                'item_versions', expected_item_versions
            )
        END;
        expected_after := jsonb_build_object(
            'result', command_row.result_jsonb,
            'result_hash', command_row.result_hash,
            'actor_person_id', command_row.actor_person_id::text,
            'actor_role_assignment_id',
                command_row.actor_role_assignment_id::text,
            'actor_role_code', CASE
                WHEN operation_name = 'explain_opening' THEN
                    'provincial_manager'
                ELSE 'admin'
            END,
            'actor_scope_type', CASE
                WHEN operation_name = 'explain_opening' THEN 'organization'
                ELSE 'national'
            END,
            'actor_scope_id', CASE
                WHEN operation_name = 'explain_opening' THEN
                    binding_row.region_org_id::text
                ELSE '*'
            END,
            'authorization_version', command_row.authorization_version
        );
        IF NEW.stream_key <> 'inventory'
           OR NEW.aggregate_type <> 'reconciliation_run'
           OR NEW.aggregate_id <> command_row.run_id::text
           OR NEW.before_jsonb IS DISTINCT FROM expected_before
           OR NEW.after_jsonb IS DISTINCT FROM expected_after THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""
    )
    for table_name, trigger_name in (
        ("state_transition_events", RECONCILIATION_TRIGGER_NAMES[18]),
        ("outbox_events", RECONCILIATION_TRIGGER_NAMES[19]),
        ("audit_events", RECONCILIATION_TRIGGER_NAMES[20]),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT OR UPDATE OR DELETE ON "
            f"public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_EFFECT_FUNCTION}()"
        )
    for table_name, trigger_name in (
        ("state_transition_events", RECONCILIATION_TRIGGER_NAMES[21]),
        ("outbox_events", RECONCILIATION_TRIGGER_NAMES[22]),
        ("audit_events", RECONCILIATION_TRIGGER_NAMES[23]),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE TRUNCATE ON "
            f"public.{table_name} FOR EACH STATEMENT "
            f"EXECUTE FUNCTION public.{PG_EFFECT_FUNCTION}()"
        )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_TASK_CLOSE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    difference_count bigint;
    approved_proof_count bigint;
BEGIN
    IF NOT (
        OLD.task_type = 'opening'
        AND OLD.status = 'posted'
        AND NEW.status = 'closed'
    ) THEN
        RETURN NEW;
    END IF;

    SELECT count(*)
      INTO difference_count
      FROM public.stocktake_differences AS difference
      JOIN public.stocktake_rounds AS round_row
        ON round_row.id = difference.round_id
       AND round_row.task_id = difference.task_id
     WHERE difference.task_id = NEW.id
       AND round_row.round_no = NEW.current_round_no
       AND difference.difference_type = 'control_unassigned';

    IF difference_count = 0 THEN
        IF EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_runs AS binding
             WHERE binding.task_id = NEW.id
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;

    SELECT count(*)
      INTO approved_proof_count
      FROM public.opening_control_reconciliation_runs AS binding
      JOIN public.reconciliation_runs AS run ON run.id = binding.run_id
      JOIN public.stocktake_rounds AS round_row
        ON round_row.id = binding.round_id
       AND round_row.task_id = binding.task_id
      JOIN public.reconciliation_commands AS approval
        ON approval.run_id = binding.run_id
       AND approval.operation = 'approve_opening'
       AND approval.target_version = binding.version
      JOIN public.opening_control_reconciliation_command_consumptions AS seal
        ON seal.command_id = approval.id
       AND seal.run_id = approval.run_id
       AND seal.operation = approval.operation
       AND seal.target_version = approval.target_version
     WHERE binding.task_id = NEW.id
       AND round_row.round_no = NEW.current_round_no
       AND run.status = 'approved'
       AND binding.approved_at IS NOT NULL
       AND NEW.closed_at IS NOT NULL
       AND NEW.closed_at >= binding.approved_at;
    IF approved_proof_count <> 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[24]} BEFORE UPDATE ON "
        "public.stocktake_tasks FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_TASK_CLOSE_FUNCTION}()"
    )

    # The API role intentionally has no UPDATE privilege on immutable source,
    # command, consumption, or file tables.  These owner-executed helpers expose
    # row locking only, with fixed SQL and deterministic ordering; they return
    # no data and grant no mutation path.
    op.execute(
        f"""
CREATE FUNCTION public.{PG_SOURCE_LOCK_FUNCTION}(source_task_id uuid)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    PERFORM posting.id
      FROM public.stocktake_postings AS posting
      JOIN public.stocktake_tasks AS task ON task.id = posting.task_id
     WHERE task.id = source_task_id
       AND task.task_type = 'opening'
       AND task.status IN ('posted', 'closed')
       AND posting.posting_kind = 'opening'
     ORDER BY posting.id
     FOR UPDATE OF posting;
    PERFORM establishment.id
      FROM public.inventory_opening_establishments AS establishment
      JOIN public.stocktake_tasks AS task ON task.id = establishment.task_id
     WHERE task.id = source_task_id
       AND task.task_type = 'opening'
       AND task.status IN ('posted', 'closed')
     ORDER BY establishment.id
     FOR UPDATE OF establishment;
    PERFORM difference.id
      FROM public.stocktake_differences AS difference
      JOIN public.stocktake_tasks AS task ON task.id = difference.task_id
     WHERE task.id = source_task_id
       AND task.task_type = 'opening'
       AND task.status IN ('posted', 'closed')
       AND difference.difference_type = 'control_unassigned'
     ORDER BY difference.id
     FOR UPDATE OF difference;
    PERFORM control.id
      FROM public.stocktake_control_snapshot_lines AS control
      JOIN public.stocktake_tasks AS task ON task.id = control.task_id
     WHERE task.id = source_task_id
       AND task.task_type = 'opening'
       AND task.status IN ('posted', 'closed')
     ORDER BY control.id
     FOR UPDATE OF control;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_RUN_LOCK_FUNCTION}(reconciliation_run_id uuid)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    source_task_id uuid;
BEGIN
    SELECT binding.task_id
      INTO source_task_id
      FROM public.opening_control_reconciliation_runs AS binding
     WHERE binding.run_id = reconciliation_run_id;
    IF source_task_id IS NULL THEN
        RETURN;
    END IF;
    PERFORM public.{PG_SOURCE_LOCK_FUNCTION}(source_task_id);
    PERFORM command.id
      FROM public.reconciliation_commands AS command
     WHERE command.run_id = reconciliation_run_id
     ORDER BY command.target_version, command.id
     FOR UPDATE;
    PERFORM seal.command_id
      FROM public.opening_control_reconciliation_command_consumptions AS seal
     WHERE seal.run_id = reconciliation_run_id
     ORDER BY seal.target_version, seal.command_id
     FOR UPDATE;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_FILES_LOCK_FUNCTION}(
    reconciliation_run_id uuid,
    evidence_file_ids uuid[]
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    maximum_file_count bigint;
    requested_file_count integer;
    unique_requested_file_count integer;
    current_file_count bigint;
    expected_file_count bigint;
    locked_file_count bigint;
BEGIN
    requested_file_count := cardinality(evidence_file_ids);
    IF evidence_file_ids IS NULL
       OR requested_file_count IS NULL
       OR (requested_file_count > 0 AND array_ndims(evidence_file_ids) <> 1)
       OR array_position(evidence_file_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT count(DISTINCT requested.file_id)
      INTO unique_requested_file_count
      FROM unnest(evidence_file_ids) AS requested(file_id);
    IF unique_requested_file_count <> requested_file_count THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT binding.item_count
      INTO maximum_file_count
      FROM public.opening_control_reconciliation_runs AS binding
     WHERE binding.run_id = reconciliation_run_id;
    IF maximum_file_count IS NULL
       OR requested_file_count > maximum_file_count THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT count(*)
      INTO current_file_count
      FROM (
            SELECT DISTINCT item.evidence_file_id
              FROM public.reconciliation_items AS item
             WHERE item.run_id = reconciliation_run_id
               AND item.evidence_file_id IS NOT NULL
       ) AS current_files;
    IF current_file_count > maximum_file_count
       OR current_file_count + requested_file_count > 2 * maximum_file_count THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT count(*)
      INTO expected_file_count
      FROM (
            SELECT item.evidence_file_id
              FROM public.reconciliation_items AS item
             WHERE item.run_id = reconciliation_run_id
               AND item.evidence_file_id IS NOT NULL
            UNION
            SELECT requested.file_id
              FROM unnest(evidence_file_ids) AS requested(file_id)
       ) AS expected_files;
    SELECT count(*)
      INTO locked_file_count
      FROM (
            SELECT evidence.id
              FROM public.files AS evidence
             WHERE evidence.id IN (
                    SELECT item.evidence_file_id
                      FROM public.reconciliation_items AS item
                     WHERE item.run_id = reconciliation_run_id
                       AND item.evidence_file_id IS NOT NULL
                    UNION
                    SELECT requested.file_id
                      FROM unnest(evidence_file_ids) AS requested(file_id)
               )
             ORDER BY evidence.id
             FOR UPDATE OF evidence
       ) AS locked_files;
    IF locked_file_count <> expected_file_count THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{PG_PRINCIPAL_GRAPH_LOCK_FUNCTION}(
    actor_user_ids text[]
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    requested_count integer;
    unique_count integer;
BEGIN
    requested_count := cardinality(actor_user_ids);
    IF actor_user_ids IS NULL
       OR array_ndims(actor_user_ids) <> 1
       OR requested_count IS NULL
       OR requested_count < 1
       OR requested_count > 1000
       OR array_position(actor_user_ids, NULL) IS NOT NULL
       OR EXISTS (
            SELECT 1
              FROM unnest(actor_user_ids) AS requested(value)
             WHERE length(requested.value) < 1
                OR length(requested.value) > 36
                OR btrim(requested.value) <> requested.value
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    SELECT count(DISTINCT requested.value)
      INTO unique_count
      FROM unnest(actor_user_ids) AS requested(value);
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    PERFORM actor.id
      FROM public.users AS actor
     WHERE actor.id::text = ANY(actor_user_ids)
     ORDER BY actor.id
     FOR UPDATE OF actor;
    PERFORM person.id
      FROM public.people AS person
     WHERE EXISTS (
            SELECT 1
              FROM public.users AS actor
             WHERE actor.person_id = person.id
               AND actor.id::text = ANY(actor_user_ids)
       )
     ORDER BY person.id
     FOR UPDATE OF person;
    PERFORM identity.id
      FROM public.auth_identities AS identity
     WHERE identity.user_id::text = ANY(actor_user_ids)
     ORDER BY identity.id
     FOR UPDATE OF identity;
    PERFORM assignment.id
      FROM public.role_assignments AS assignment
     WHERE assignment.user_id::text = ANY(actor_user_ids)
     ORDER BY assignment.id
     FOR UPDATE OF assignment;
    PERFORM role.id
      FROM public.roles AS role
     WHERE EXISTS (
            SELECT 1
              FROM public.role_assignments AS assignment
             WHERE assignment.role_id = role.id
               AND assignment.user_id::text = ANY(actor_user_ids)
       )
     ORDER BY role.id
     FOR UPDATE OF role;
    PERFORM role_permission.id
      FROM public.role_permissions AS role_permission
     WHERE EXISTS (
            SELECT 1
              FROM public.role_assignments AS assignment
             WHERE assignment.role_id = role_permission.role_id
               AND assignment.user_id::text = ANY(actor_user_ids)
       )
     ORDER BY role_permission.id
     FOR UPDATE OF role_permission;
    PERFORM permission.id
      FROM public.permissions AS permission
     WHERE EXISTS (
            SELECT 1
              FROM public.role_permissions AS role_permission
              JOIN public.role_assignments AS assignment
                ON assignment.role_id = role_permission.role_id
             WHERE role_permission.permission_id = permission.id
               AND assignment.user_id::text = ANY(actor_user_ids)
       )
     ORDER BY permission.id
     FOR UPDATE OF permission;
END
$$
"""
    )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_RUN_EXTENSION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    operation_row public.reconciliation_commands%ROWTYPE;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.version <> 0
           OR NEW.updated_at <> NEW.created_at
           OR NEW.approved_by_user_id IS NOT NULL
           OR NEW.approved_by_person_id IS NOT NULL
           OR NEW.approved_role_assignment_id IS NOT NULL
           OR NEW.approved_authorization_version IS NOT NULL
           OR NEW.approved_at IS NOT NULL
           OR NEW.approval_comment <> ''
           OR EXISTS (
                SELECT 1 FROM public.reconciliation_runs AS run
                 WHERE run.id = NEW.run_id
           )
           OR EXISTS (
                SELECT 1 FROM public.reconciliation_commands AS command
                 WHERE command.id = NEW.create_command_id
                    OR command.run_id = NEW.run_id
           )
           OR NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_tasks AS task
                  JOIN public.stocktake_rounds AS round_row
                    ON round_row.id = NEW.round_id
                   AND round_row.task_id = task.id
                  JOIN public.stocktake_postings AS posting
                    ON posting.id = NEW.posting_id
                   AND posting.task_id = task.id
                   AND posting.round_id = round_row.id
                 WHERE task.id = NEW.task_id
                   AND task.task_type = 'opening'
                   AND task.status = 'posted'
                   AND round_row.round_no = task.current_round_no
                   AND posting.posting_kind = 'opening'
                   AND NEW.region_org_id = task.region_org_id
                   AND NEW.control_sync_run_id = task.control_sync_run_id
                   AND NEW.item_count = (
                        SELECT count(*)
                          FROM public.stocktake_differences AS difference
                         WHERE difference.task_id = task.id
                           AND difference.round_id = round_row.id
                           AND difference.difference_type = 'control_unassigned'
                   )
                   AND NEW.item_count > 0
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.reconciliation_runs AS run
         WHERE run.id = OLD.run_id AND run.status = 'approved'
    )
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.create_command_id IS DISTINCT FROM OLD.create_command_id
       OR NEW.task_id IS DISTINCT FROM OLD.task_id
       OR NEW.round_id IS DISTINCT FROM OLD.round_id
       OR NEW.region_org_id IS DISTINCT FROM OLD.region_org_id
       OR NEW.posting_id IS DISTINCT FROM OLD.posting_id
       OR NEW.control_sync_run_id IS DISTINCT FROM OLD.control_sync_run_id
       OR NEW.item_count IS DISTINCT FROM OLD.item_count
       OR NEW.item_manifest_sha256 IS DISTINCT FROM OLD.item_manifest_sha256
       OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
       OR NEW.created_by_person_id IS DISTINCT FROM OLD.created_by_person_id
       OR NEW.created_role_assignment_id IS DISTINCT FROM OLD.created_role_assignment_id
       OR NEW.created_authorization_version IS DISTINCT FROM
          OLD.created_authorization_version
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT command.* INTO operation_row
      FROM public.reconciliation_commands AS command
     WHERE command.run_id = NEW.run_id
       AND command.target_version = NEW.version
       AND command.operation IN ('explain_opening', 'approve_opening')
     ORDER BY command.id
     LIMIT 1;
    IF operation_row.id IS NULL
       OR NEW.updated_at IS DISTINCT FROM operation_row.occurred_at THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF operation_row.operation = 'explain_opening' THEN
        IF NEW.approved_by_user_id IS DISTINCT FROM OLD.approved_by_user_id
           OR NEW.approved_by_person_id IS DISTINCT FROM OLD.approved_by_person_id
           OR NEW.approved_role_assignment_id IS DISTINCT FROM
              OLD.approved_role_assignment_id
           OR NEW.approved_authorization_version IS DISTINCT FROM
              OLD.approved_authorization_version
           OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
           OR NEW.approval_comment IS DISTINCT FROM OLD.approval_comment THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        IF OLD.approved_by_user_id IS NOT NULL
           OR NEW.approved_by_user_id IS DISTINCT FROM operation_row.actor_user_id
           OR NEW.approved_by_person_id IS DISTINCT FROM operation_row.actor_person_id
           OR NEW.approved_role_assignment_id IS DISTINCT FROM
              operation_row.actor_role_assignment_id
           OR NEW.approved_authorization_version IS DISTINCT FROM
              operation_row.authorization_version
           OR NEW.approved_at IS DISTINCT FROM operation_row.occurred_at
           OR length(trim(NEW.approval_comment)) < 4
           OR NEW.approval_comment <> trim(NEW.approval_comment)
           OR EXISTS (
                SELECT 1
                  FROM public.opening_control_reconciliation_items AS item
                 WHERE item.run_id = NEW.run_id
                   AND item.explained_by_user_id = NEW.approved_by_user_id
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""
    )
    for trigger_name, event in (
        (RECONCILIATION_TRIGGER_NAMES[2], "INSERT OR UPDATE"),
        (RECONCILIATION_TRIGGER_NAMES[3], "DELETE"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {event} ON "
            "public.opening_control_reconciliation_runs FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_RUN_EXTENSION_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[4]} BEFORE TRUNCATE ON "
        "public.opening_control_reconciliation_runs FOR EACH STATEMENT "
        f"EXECUTE FUNCTION public.{PG_RUN_EXTENSION_FUNCTION}()"
    )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_ITEM_EXTENSION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    operation_row public.reconciliation_commands%ROWTYPE;
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.version <> 0
           OR NEW.updated_at <> NEW.created_at
           OR NEW.evidence_reference <> ''
           OR NEW.explained_by_user_id IS NOT NULL
           OR NEW.explained_by_person_id IS NOT NULL
           OR NEW.explained_role_assignment_id IS NOT NULL
           OR NEW.explanation_authorization_version IS NOT NULL
           OR NEW.explained_at IS NOT NULL
           OR NEW.evidence_file_sha256 IS NOT NULL
           OR NEW.evidence_file_size_bytes IS NOT NULL
           OR NEW.evidence_file_mime_type IS NOT NULL
           OR EXISTS (
                SELECT 1 FROM public.reconciliation_items AS item
                 WHERE item.id = NEW.item_id
           )
           OR EXISTS (
                SELECT 1 FROM public.reconciliation_commands AS command
                 WHERE command.run_id = NEW.run_id
           )
           OR NOT EXISTS (
                SELECT 1
                  FROM public.opening_control_reconciliation_runs AS run_binding
                  JOIN public.stocktake_differences AS difference
                    ON difference.id = NEW.difference_id
                   AND difference.task_id = run_binding.task_id
                   AND difference.round_id = run_binding.round_id
                  JOIN public.stocktake_control_snapshot_lines AS control
                    ON control.id = NEW.control_snapshot_line_id
                   AND control.task_id = run_binding.task_id
                 WHERE run_binding.run_id = NEW.run_id
                   AND NEW.task_id = run_binding.task_id
                   AND NEW.round_id = run_binding.round_id
                   AND difference.difference_type = 'control_unassigned'
                   AND difference.control_snapshot_line_id = control.id
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM public.reconciliation_runs AS run
         WHERE run.id = OLD.run_id AND run.status = 'approved'
    )
       OR NEW.item_id IS DISTINCT FROM OLD.item_id
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.task_id IS DISTINCT FROM OLD.task_id
       OR NEW.round_id IS DISTINCT FROM OLD.round_id
       OR NEW.difference_id IS DISTINCT FROM OLD.difference_id
       OR NEW.control_snapshot_line_id IS DISTINCT FROM
          OLD.control_snapshot_line_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    SELECT command.* INTO operation_row
      FROM public.reconciliation_commands AS command
     WHERE command.run_id = NEW.run_id
       AND command.target_version = NEW.version
       AND command.operation IN ('explain_opening', 'approve_opening')
     ORDER BY command.id
     LIMIT 1;
    IF operation_row.id IS NULL
       OR NEW.updated_at IS DISTINCT FROM operation_row.occurred_at THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF operation_row.operation = 'explain_opening' THEN
        IF length(trim(NEW.evidence_reference)) < 4
           OR NEW.evidence_reference <> trim(NEW.evidence_reference)
           OR NEW.explained_by_user_id IS DISTINCT FROM operation_row.actor_user_id
           OR NEW.explained_by_person_id IS DISTINCT FROM operation_row.actor_person_id
           OR NEW.explained_role_assignment_id IS DISTINCT FROM
              operation_row.actor_role_assignment_id
           OR NEW.explanation_authorization_version IS DISTINCT FROM
              operation_row.authorization_version
           OR NEW.explained_at IS DISTINCT FROM operation_row.occurred_at THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        IF NOT EXISTS (
            SELECT 1
              FROM public.reconciliation_items AS item
              LEFT JOIN public.files AS evidence
                ON evidence.id = item.evidence_file_id
             WHERE item.id = NEW.item_id
               AND ((item.evidence_file_id IS NULL
                     AND NEW.evidence_file_sha256 IS NULL
                     AND NEW.evidence_file_size_bytes IS NULL
                     AND NEW.evidence_file_mime_type IS NULL)
                    OR (item.evidence_file_id IS NOT NULL
                        AND evidence.status = 'available'
                        AND NEW.evidence_file_sha256 = evidence.sha256
                        AND NEW.evidence_file_size_bytes = evidence.size_bytes
                        AND NEW.evidence_file_mime_type = evidence.mime_type))
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        IF NEW.evidence_reference IS DISTINCT FROM OLD.evidence_reference
           OR NEW.evidence_file_sha256 IS DISTINCT FROM
              OLD.evidence_file_sha256
           OR NEW.evidence_file_size_bytes IS DISTINCT FROM
              OLD.evidence_file_size_bytes
           OR NEW.evidence_file_mime_type IS DISTINCT FROM
              OLD.evidence_file_mime_type
           OR NEW.explained_by_user_id IS DISTINCT FROM OLD.explained_by_user_id
           OR NEW.explained_by_person_id IS DISTINCT FROM OLD.explained_by_person_id
           OR NEW.explained_role_assignment_id IS DISTINCT FROM
              OLD.explained_role_assignment_id
           OR NEW.explanation_authorization_version IS DISTINCT FROM
              OLD.explanation_authorization_version
           OR NEW.explained_at IS DISTINCT FROM OLD.explained_at THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END
$$
"""
    )
    for trigger_name, event in (
        (RECONCILIATION_TRIGGER_NAMES[5], "INSERT OR UPDATE"),
        (RECONCILIATION_TRIGGER_NAMES[6], "DELETE"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {event} ON "
            "public.opening_control_reconciliation_items FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_ITEM_EXTENSION_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[7]} BEFORE TRUNCATE ON "
        "public.opening_control_reconciliation_items FOR EACH STATEMENT "
        f"EXECUTE FUNCTION public.{PG_ITEM_EXTENSION_FUNCTION}()"
    )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_RUN_PROJECTION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_runs AS binding
              JOIN public.stocktake_tasks AS task ON task.id = binding.task_id
              JOIN public.stocktake_rounds AS round_row
                ON round_row.id = binding.round_id
               AND round_row.task_id = task.id
             WHERE binding.run_id = NEW.id
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.reconciliation_commands AS command
                     WHERE command.id = binding.create_command_id
                        OR command.run_id = NEW.id
               )
               AND NEW.run_key = 'opening-control:' || task.id::text || ':' ||
                   round_row.id::text
               AND NEW.source_system_id = task.control_source_system_id
               AND NEW.scope = 'opening:' || task.region_org_id::text || ':' ||
                   task.id::text
               AND NEW.external_snapshot_at = task.control_snapshot_at
               AND NEW.status = 'differences'
               AND NEW.summary_jsonb = jsonb_build_object(
                    'schema',
                    'cloud_oam.opening_control_reconciliation.summary.v1',
                    'task_id', task.id::text,
                    'round_id', round_row.id::text,
                    'item_count', binding.item_count,
                    'item_manifest_sha256', binding.item_manifest_sha256
               )
               AND NEW.started_at = binding.created_at
               AND NEW.completed_at = binding.created_at
               AND NEW.created_at = binding.created_at
               AND NEW.updated_at = binding.created_at
               AND EXISTS (
                    SELECT 1
                      FROM public.inventory_opening_establishments AS establishment
                     WHERE establishment.task_id = task.id
                       AND establishment.round_id = round_row.id
                       AND establishment.posting_id = binding.posting_id
                       AND establishment.has_pending_control_difference = true
                       AND NEW.local_ledger_cursor =
                           establishment.established_ledger_cursor::text
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.inventory_opening_establishments AS establishment
                     WHERE establishment.task_id = task.id
                       AND (establishment.round_id <> round_row.id
                            OR establishment.posting_id <> binding.posting_id
                            OR establishment.has_pending_control_difference = false
                            OR NEW.local_ledger_cursor <>
                               establishment.established_ledger_cursor::text)
               )
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.opening_control_reconciliation_runs AS binding
         WHERE binding.run_id = OLD.id
    )
       OR OLD.status = 'approved'
       OR OLD.status <> 'differences'
       OR NEW.status <> 'approved'
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.run_key IS DISTINCT FROM OLD.run_key
       OR NEW.source_system_id IS DISTINCT FROM OLD.source_system_id
       OR NEW.scope IS DISTINCT FROM OLD.scope
       OR NEW.external_snapshot_at IS DISTINCT FROM OLD.external_snapshot_at
       OR NEW.local_ledger_cursor IS DISTINCT FROM OLD.local_ledger_cursor
       OR NEW.summary_jsonb IS DISTINCT FROM OLD.summary_jsonb
       OR NEW.started_at IS DISTINCT FROM OLD.started_at
       OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NOT EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_runs AS binding
              JOIN public.stocktake_tasks AS task ON task.id = binding.task_id
              JOIN public.stocktake_rounds AS round_row
                ON round_row.id = binding.round_id
               AND round_row.task_id = task.id
              JOIN public.stocktake_postings AS posting
                ON posting.id = binding.posting_id
               AND posting.task_id = task.id
               AND posting.round_id = round_row.id
              JOIN public.reconciliation_commands AS approval
                ON approval.run_id = binding.run_id
               AND approval.operation = 'approve_opening'
               AND approval.target_version = binding.version
             WHERE binding.run_id = NEW.id
               AND task.task_type = 'opening'
               AND task.status = 'posted'
               AND round_row.round_no = task.current_round_no
               AND posting.posting_kind = 'opening'
               AND binding.region_org_id = task.region_org_id
               AND binding.control_sync_run_id = task.control_sync_run_id
               AND NEW.source_system_id = task.control_source_system_id
               AND NEW.external_snapshot_at = task.control_snapshot_at
               AND binding.approved_by_user_id = approval.actor_user_id
               AND binding.approved_by_person_id = approval.actor_person_id
               AND binding.approved_role_assignment_id =
                   approval.actor_role_assignment_id
               AND binding.approved_authorization_version =
                   approval.authorization_version
               AND binding.approved_at = approval.occurred_at
               AND NEW.updated_at = approval.occurred_at
               AND length(trim(binding.approval_comment)) >= 4
               AND binding.approval_comment = trim(binding.approval_comment)
               AND binding.version = 1 + (
                    SELECT count(*)
                      FROM public.reconciliation_commands AS explanation
                     WHERE explanation.run_id = NEW.id
                       AND explanation.operation = 'explain_opening'
               )
               AND 1 = (
                    SELECT count(*)
                      FROM public.reconciliation_commands AS create_command
                     WHERE create_command.run_id = NEW.id
                       AND create_command.operation = 'create_opening'
               )
               AND 1 = (
                    SELECT count(*)
                      FROM public.reconciliation_commands AS approve_command
                     WHERE approve_command.run_id = NEW.id
                       AND approve_command.operation = 'approve_opening'
               )
               AND binding.item_count > 0
               AND binding.item_count = (
                    SELECT count(*)
                      FROM public.reconciliation_items AS item
                     WHERE item.run_id = NEW.id
               )
               AND binding.item_count = (
                    SELECT count(*)
                      FROM public.stocktake_differences AS difference
                     WHERE difference.task_id = task.id
                       AND difference.round_id = round_row.id
                       AND difference.difference_type = 'control_unassigned'
               )
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.reconciliation_items AS item
                      JOIN public.opening_control_reconciliation_items AS item_binding
                        ON item_binding.item_id = item.id
                      JOIN public.stocktake_differences AS difference
                        ON difference.id = item_binding.difference_id
                       AND difference.task_id = binding.task_id
                       AND difference.round_id = binding.round_id
                      JOIN public.stocktake_control_snapshot_lines AS control
                        ON control.id = item_binding.control_snapshot_line_id
                       AND control.task_id = binding.task_id
                     WHERE item.run_id = NEW.id
                       AND (item_binding.run_id <> NEW.id
                            OR difference.difference_type <> 'control_unassigned'
                            OR difference.control_snapshot_line_id <> control.id
                            OR item.business_key <> control.external_business_key
                            OR item.external_qty <> difference.book_qty
                            OR item.local_qty <> difference.counted_qty
                            OR item.difference <> difference.book_qty - difference.counted_qty
                            OR item.difference = 0
                            OR item.status <> 'resolved'
                            OR item_binding.version <> binding.version
                            OR length(trim(item.explanation)) < 4
                            OR item.explanation <> trim(item.explanation)
                            OR length(trim(item_binding.evidence_reference)) < 4
                            OR item_binding.evidence_reference <>
                               trim(item_binding.evidence_reference)
                            OR item_binding.explained_by_user_id =
                               binding.approved_by_user_id
                            OR (item.evidence_file_id IS NULL AND
                                (item_binding.evidence_file_sha256 IS NOT NULL
                                 OR item_binding.evidence_file_size_bytes IS NOT NULL
                                 OR item_binding.evidence_file_mime_type IS NOT NULL))
                            OR (item.evidence_file_id IS NOT NULL AND NOT EXISTS (
                                SELECT 1 FROM public.files AS evidence
                                 WHERE evidence.id = item.evidence_file_id
                                   AND evidence.status = 'available'
                                   AND item_binding.evidence_file_sha256 = evidence.sha256
                                   AND item_binding.evidence_file_size_bytes =
                                       evidence.size_bytes
                                   AND item_binding.evidence_file_mime_type =
                                       evidence.mime_type)))
               )
       ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    for trigger_name, event in (
        (RECONCILIATION_TRIGGER_NAMES[8], "UPDATE"),
        (RECONCILIATION_TRIGGER_NAMES[9], "DELETE"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {event} ON "
            "public.reconciliation_runs FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_RUN_PROJECTION_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[10]} BEFORE TRUNCATE ON "
        "public.reconciliation_runs FOR EACH STATEMENT "
        f"EXECUTE FUNCTION public.{PG_RUN_PROJECTION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[14]} BEFORE INSERT ON "
        "public.reconciliation_runs FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_RUN_PROJECTION_FUNCTION}()"
    )

    op.execute(
        f"""
CREATE FUNCTION public.{PG_ITEM_PROJECTION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM public.opening_control_reconciliation_items AS binding
              JOIN public.opening_control_reconciliation_runs AS run_binding
                ON run_binding.run_id = binding.run_id
              JOIN public.stocktake_differences AS difference
                ON difference.id = binding.difference_id
               AND difference.task_id = binding.task_id
               AND difference.round_id = binding.round_id
              JOIN public.stocktake_control_snapshot_lines AS control
                ON control.id = binding.control_snapshot_line_id
               AND control.task_id = binding.task_id
             WHERE binding.item_id = NEW.id
               AND binding.run_id = NEW.run_id
               AND NOT EXISTS (
                    SELECT 1
                      FROM public.reconciliation_commands AS command
                     WHERE command.run_id = NEW.run_id
               )
               AND difference.difference_type = 'control_unassigned'
               AND difference.control_snapshot_line_id = control.id
               AND NEW.business_key = control.external_business_key
               AND NEW.external_qty = difference.book_qty
               AND NEW.local_qty = difference.counted_qty
               AND NEW.difference = difference.book_qty - difference.counted_qty
               AND NEW.difference <> 0
               AND NEW.status = 'difference'
               AND NEW.explanation = ''
               AND NEW.evidence_file_id IS NULL
               AND binding.evidence_file_sha256 IS NULL
               AND binding.evidence_file_size_bytes IS NULL
               AND binding.evidence_file_mime_type IS NULL
               AND NEW.created_at = binding.created_at
               AND NEW.updated_at = binding.created_at
               AND run_binding.run_id = NEW.run_id
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.opening_control_reconciliation_items AS binding
         WHERE binding.item_id = OLD.id AND binding.run_id = OLD.run_id
    )
       OR OLD.status = 'resolved'
       OR NEW.id IS DISTINCT FROM OLD.id
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.business_key IS DISTINCT FROM OLD.business_key
       OR NEW.external_qty IS DISTINCT FROM OLD.external_qty
       OR NEW.local_qty IS DISTINCT FROM OLD.local_qty
       OR NEW.difference IS DISTINCT FROM OLD.difference
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;

    IF NEW.status = 'explained' AND OLD.status IN ('difference', 'explained') THEN
        IF length(trim(NEW.explanation)) < 4
           OR NEW.explanation <> trim(NEW.explanation)
           OR (NEW.evidence_file_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM public.files AS evidence
                 WHERE evidence.id = NEW.evidence_file_id
                   AND evidence.status = 'available'))
           OR NOT EXISTS (
                SELECT 1
                  FROM public.reconciliation_commands AS command
                  JOIN public.opening_control_reconciliation_items AS binding
                    ON binding.item_id = OLD.id
                   AND binding.run_id = OLD.run_id
                 WHERE command.run_id = NEW.run_id
                   AND command.operation = 'explain_opening'
                   AND command.target_version = binding.version + 1
                   AND command.occurred_at = NEW.updated_at
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSIF NEW.status = 'resolved' AND OLD.status = 'explained' THEN
        IF NEW.explanation IS DISTINCT FROM OLD.explanation
           OR NEW.evidence_file_id IS DISTINCT FROM OLD.evidence_file_id
           OR NOT EXISTS (
                SELECT 1
                  FROM public.reconciliation_commands AS command
                  JOIN public.opening_control_reconciliation_items AS binding
                    ON binding.item_id = OLD.id
                   AND binding.run_id = OLD.run_id
                 WHERE command.run_id = NEW.run_id
                   AND command.operation = 'approve_opening'
                   AND command.target_version = binding.version + 1
                   AND command.occurred_at = NEW.updated_at
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    ELSE
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    for trigger_name, event in (
        (RECONCILIATION_TRIGGER_NAMES[11], "UPDATE"),
        (RECONCILIATION_TRIGGER_NAMES[12], "DELETE"),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {event} ON "
            "public.reconciliation_items FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_ITEM_PROJECTION_FUNCTION}()"
        )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[13]} BEFORE TRUNCATE ON "
        "public.reconciliation_items FOR EACH STATEMENT "
        f"EXECUTE FUNCTION public.{PG_ITEM_PROJECTION_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {RECONCILIATION_TRIGGER_NAMES[15]} BEFORE INSERT ON "
        "public.reconciliation_items FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_ITEM_PROJECTION_FUNCTION}()"
    )

    for table_name, trigger_name in (
        ("reconciliation_commands", RECONCILIATION_TRIGGER_NAMES[0]),
        ("reconciliation_commands", RECONCILIATION_TRIGGER_NAMES[1]),
        ("opening_control_reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[2]),
        ("opening_control_reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[3]),
        ("opening_control_reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[4]),
        ("opening_control_reconciliation_items", RECONCILIATION_TRIGGER_NAMES[5]),
        ("opening_control_reconciliation_items", RECONCILIATION_TRIGGER_NAMES[6]),
        ("opening_control_reconciliation_items", RECONCILIATION_TRIGGER_NAMES[7]),
        ("reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[8]),
        ("reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[9]),
        ("reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[10]),
        ("reconciliation_items", RECONCILIATION_TRIGGER_NAMES[11]),
        ("reconciliation_items", RECONCILIATION_TRIGGER_NAMES[12]),
        ("reconciliation_items", RECONCILIATION_TRIGGER_NAMES[13]),
        ("reconciliation_runs", RECONCILIATION_TRIGGER_NAMES[14]),
        ("reconciliation_items", RECONCILIATION_TRIGGER_NAMES[15]),
        (
            "opening_control_reconciliation_command_consumptions",
            RECONCILIATION_TRIGGER_NAMES[16],
        ),
        (
            "opening_control_reconciliation_command_consumptions",
            RECONCILIATION_TRIGGER_NAMES[17],
        ),
        ("state_transition_events", RECONCILIATION_TRIGGER_NAMES[18]),
        ("outbox_events", RECONCILIATION_TRIGGER_NAMES[19]),
        ("audit_events", RECONCILIATION_TRIGGER_NAMES[20]),
        ("state_transition_events", RECONCILIATION_TRIGGER_NAMES[21]),
        ("outbox_events", RECONCILIATION_TRIGGER_NAMES[22]),
        ("audit_events", RECONCILIATION_TRIGGER_NAMES[23]),
        ("stocktake_tasks", RECONCILIATION_TRIGGER_NAMES[24]),
    ):
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )
    for function_name in (
        PG_COMMAND_FUNCTION,
        PG_COMMAND_CONSUMPTION_FUNCTION,
        PG_RUN_EXTENSION_FUNCTION,
        PG_ITEM_EXTENSION_FUNCTION,
        PG_RUN_PROJECTION_FUNCTION,
        PG_ITEM_PROJECTION_FUNCTION,
        PG_EFFECT_FUNCTION,
        PG_TASK_CLOSE_FUNCTION,
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}() FROM "
            f"PUBLIC, {PRODUCTION_API_ROLE}"
        )
    for function_signature in (
        f"public.{PG_CANONICAL_JSON_FUNCTION}(jsonb)",
        f"public.{PG_EVENT_KEY_FUNCTION}(text, text, text)",
        *PG_LOCK_FUNCTION_SIGNATURES,
    ):
        op.execute(
            f"REVOKE ALL ON FUNCTION {function_signature} FROM "
            f"PUBLIC, {PRODUCTION_API_ROLE}"
        )
        op.execute(
            f"""
DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['star_oam_backup', 'star_oam_edge'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION {function_signature} FROM %I',
                role_name
            );
        END IF;
    END LOOP;
END
$$
"""
        )


def _drop_postgresql_guards() -> None:
    table_by_trigger = {
        RECONCILIATION_TRIGGER_NAMES[0]: "reconciliation_commands",
        RECONCILIATION_TRIGGER_NAMES[1]: "reconciliation_commands",
        RECONCILIATION_TRIGGER_NAMES[2]: "opening_control_reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[3]: "opening_control_reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[4]: "opening_control_reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[5]: "opening_control_reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[6]: "opening_control_reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[7]: "opening_control_reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[8]: "reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[9]: "reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[10]: "reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[11]: "reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[12]: "reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[13]: "reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[14]: "reconciliation_runs",
        RECONCILIATION_TRIGGER_NAMES[15]: "reconciliation_items",
        RECONCILIATION_TRIGGER_NAMES[16]: (
            "opening_control_reconciliation_command_consumptions"
        ),
        RECONCILIATION_TRIGGER_NAMES[17]: (
            "opening_control_reconciliation_command_consumptions"
        ),
        RECONCILIATION_TRIGGER_NAMES[18]: "state_transition_events",
        RECONCILIATION_TRIGGER_NAMES[19]: "outbox_events",
        RECONCILIATION_TRIGGER_NAMES[20]: "audit_events",
        RECONCILIATION_TRIGGER_NAMES[21]: "state_transition_events",
        RECONCILIATION_TRIGGER_NAMES[22]: "outbox_events",
        RECONCILIATION_TRIGGER_NAMES[23]: "audit_events",
        RECONCILIATION_TRIGGER_NAMES[24]: "stocktake_tasks",
    }
    for trigger_name, table_name in table_by_trigger.items():
        op.execute(f"DROP TRIGGER {trigger_name} ON public.{table_name}")
    for function_name in (
        PG_COMMAND_FUNCTION,
        PG_COMMAND_CONSUMPTION_FUNCTION,
        PG_RUN_EXTENSION_FUNCTION,
        PG_ITEM_EXTENSION_FUNCTION,
        PG_RUN_PROJECTION_FUNCTION,
        PG_ITEM_PROJECTION_FUNCTION,
        PG_EFFECT_FUNCTION,
        PG_TASK_CLOSE_FUNCTION,
    ):
        op.execute(f"DROP FUNCTION public.{function_name}()")
    for function_signature in reversed(PG_LOCK_FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION {function_signature}")
    op.execute(f"DROP FUNCTION public.{PG_EVENT_KEY_FUNCTION}(text, text, text)")
    op.execute(f"DROP FUNCTION public.{PG_CANONICAL_JSON_FUNCTION}(jsonb)")


SQLITE_TRIGGER_NAMES = (
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
    "trg_opening_reconciliation_consumptions_insert_guard_0026",
    "trg_opening_reconciliation_consumptions_update_guard_0026",
    "trg_opening_reconciliation_consumptions_delete_guard_0026",
    "trg_reconciliation_state_effect_insert_guard_0026",
    "trg_reconciliation_state_effect_update_guard_0026",
    "trg_reconciliation_state_effect_delete_guard_0026",
    "trg_reconciliation_outbox_effect_insert_guard_0026",
    "trg_reconciliation_outbox_effect_update_guard_0026",
    "trg_reconciliation_outbox_effect_delete_guard_0026",
    "trg_reconciliation_audit_effect_insert_guard_0026",
    "trg_reconciliation_audit_effect_update_guard_0026",
    "trg_reconciliation_audit_effect_delete_guard_0026",
    "trg_stocktake_tasks_reconciliation_close_guard_0026",
)


def _sqlite_guard_trigger(
    name: str,
    table_name: str,
    event: str,
    invalid_predicate: str,
) -> None:
    op.execute(
        f"""
CREATE TRIGGER {name}
BEFORE {event} ON {table_name}
FOR EACH ROW
WHEN {invalid_predicate}
BEGIN
    SELECT RAISE(ABORT, '{GUARD_ERROR}');
END
"""
    )


def _create_sqlite_guards() -> None:
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[0],
        "reconciliation_commands",
        "INSERT",
        """
NEW.request_reference NOT LIKE 'opening-reconciliation-request-%'
OR length(NEW.request_reference) <> 95
OR NOT EXISTS (
    SELECT 1
      FROM opening_control_reconciliation_runs AS binding
      JOIN reconciliation_runs AS run ON run.id = binding.run_id
      JOIN stocktake_tasks AS task ON task.id = binding.task_id
      JOIN role_assignments AS assignment
        ON assignment.id = NEW.actor_role_assignment_id
      JOIN roles AS role ON role.id = assignment.role_id
      JOIN users AS actor ON actor.id = assignment.user_id
      JOIN people AS person ON person.id = actor.person_id
      JOIN organizations AS organization
        ON organization.id = person.organization_id
     WHERE binding.run_id = NEW.run_id
       AND (NEW.operation = 'create_opening' OR EXISTS (
            SELECT 1 FROM reconciliation_commands AS create_command
            JOIN opening_control_reconciliation_command_consumptions AS create_seal
              ON create_seal.command_id = create_command.id
             AND create_seal.run_id = create_command.run_id
             AND create_seal.operation = create_command.operation
             AND create_seal.target_version = create_command.target_version
             WHERE create_command.id = binding.create_command_id
               AND create_command.run_id = binding.run_id
               AND create_command.operation = 'create_opening'
               AND create_command.target_version = 0
               AND create_command.actor_user_id = binding.created_by_user_id
               AND create_command.actor_person_id = binding.created_by_person_id
               AND create_command.actor_role_assignment_id =
                   binding.created_role_assignment_id
               AND create_command.authorization_version =
                   binding.created_authorization_version
               AND create_command.occurred_at = binding.created_at
               AND create_command.created_at = binding.created_at))
       AND assignment.user_id = NEW.actor_user_id
       AND actor.person_id = NEW.actor_person_id
       AND actor.authorization_version = NEW.authorization_version
       AND actor.account_status = 'active'
       AND person.employment_status = 'active'
       AND organization.status = 'active'
       AND role.status = 'active'
       AND role.is_external = 0
       AND assignment.status IN ('scheduled', 'active')
       AND assignment.valid_from <= NEW.occurred_at
       AND (assignment.valid_to IS NULL OR NEW.occurred_at < assignment.valid_to)
       AND (assignment.revoked_at IS NULL OR NEW.occurred_at < assignment.revoked_at)
       AND ((NEW.operation IN ('create_opening', 'approve_opening')
             AND role.code = 'admin'
             AND assignment.scope_type = 'national'
             AND assignment.scope_id = '*'
             AND organization.org_type = 'headquarters')
            OR (NEW.operation = 'explain_opening'
                AND role.code = 'provincial_manager'
                AND assignment.scope_type = 'organization'
                AND replace(assignment.scope_id, '-', '') = binding.region_org_id
                AND organization.org_type IN
                    ('headquarters', 'region_company', 'department')))
       AND ((NEW.operation = 'create_opening'
             AND NEW.target_version = 0
             AND binding.create_command_id = NEW.id
             AND run.status = 'differences'
             AND binding.version = 0
             AND binding.created_by_user_id = NEW.actor_user_id
             AND binding.created_by_person_id = NEW.actor_person_id
             AND binding.created_role_assignment_id = NEW.actor_role_assignment_id
             AND binding.created_authorization_version = NEW.authorization_version
             AND binding.created_at = NEW.occurred_at
             AND task.posted_at IS NOT NULL
             AND binding.created_at >= task.posted_at
             AND binding.updated_at = binding.created_at
             AND binding.approved_by_user_id IS NULL
             AND binding.approved_by_person_id IS NULL
             AND binding.approved_role_assignment_id IS NULL
             AND binding.approved_authorization_version IS NULL
             AND binding.approved_at IS NULL
             AND binding.approval_comment = ''
             AND binding.item_count = (
                 SELECT count(*) FROM reconciliation_items AS item
                  WHERE item.run_id = run.id)
             AND binding.item_count = (
                 SELECT count(*) FROM opening_control_reconciliation_items AS item
                  WHERE item.run_id = run.id)
             AND binding.item_count = (
                 SELECT count(*) FROM stocktake_differences AS difference
                  WHERE difference.task_id = task.id
                    AND difference.round_id = binding.round_id
                    AND difference.difference_type = 'control_unassigned'))
            OR (NEW.operation = 'explain_opening'
                AND NEW.target_version = binding.version + 1
                AND NEW.occurred_at >= binding.updated_at
                AND run.status = 'differences'
                AND binding.approved_at IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM reconciliation_items AS item
                    JOIN opening_control_reconciliation_items AS item_binding
                      ON item_binding.item_id = item.id
                    WHERE item.run_id = run.id
                      AND (item.status NOT IN ('difference', 'explained')
                           OR item_binding.version <> binding.version)))
            OR (NEW.operation = 'approve_opening'
                AND NEW.target_version = binding.version + 1
                AND NEW.occurred_at >= binding.updated_at
                AND run.status = 'differences'
                AND binding.approved_at IS NULL
                AND NOT EXISTS (
                    SELECT 1 FROM reconciliation_items AS item
                    JOIN opening_control_reconciliation_items AS item_binding
                      ON item_binding.item_id = item.id
                    WHERE item.run_id = run.id
                      AND (item.status <> 'explained'
                           OR item_binding.version <> binding.version
                           OR length(trim(item.explanation)) < 4
                           OR length(trim(item_binding.evidence_reference)) < 4
                           OR item_binding.explained_by_user_id = NEW.actor_user_id
                           OR (item.evidence_file_id IS NULL AND
                               (item_binding.evidence_file_sha256 IS NOT NULL
                                OR item_binding.evidence_file_size_bytes IS NOT NULL
                                OR item_binding.evidence_file_mime_type IS NOT NULL))
                           OR (item.evidence_file_id IS NOT NULL AND NOT EXISTS (
                               SELECT 1 FROM files AS evidence
                                WHERE evidence.id = item.evidence_file_id
                                  AND evidence.status = 'available'
                                  AND item_binding.evidence_file_sha256 = evidence.sha256
                                  AND item_binding.evidence_file_size_bytes =
                                      evidence.size_bytes
                                  AND item_binding.evidence_file_mime_type =
                                      evidence.mime_type))))))
)
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[1],
        "reconciliation_commands",
        "UPDATE",
        "1 = 1",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[2],
        "reconciliation_commands",
        "DELETE",
        "1 = 1",
    )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[3],
        "opening_control_reconciliation_runs",
        "INSERT",
        """
NEW.version <> 0
OR NEW.updated_at <> NEW.created_at
OR NEW.approved_by_user_id IS NOT NULL
OR NEW.approved_by_person_id IS NOT NULL
OR NEW.approved_role_assignment_id IS NOT NULL
OR NEW.approved_authorization_version IS NOT NULL
OR NEW.approved_at IS NOT NULL
OR NEW.approval_comment <> ''
OR EXISTS (SELECT 1 FROM reconciliation_runs AS run WHERE run.id = NEW.run_id)
OR EXISTS (
    SELECT 1 FROM reconciliation_commands AS command
     WHERE command.id = NEW.create_command_id OR command.run_id = NEW.run_id)
OR NOT EXISTS (
    SELECT 1 FROM stocktake_tasks AS task
    JOIN stocktake_rounds AS round_row
      ON round_row.id = NEW.round_id AND round_row.task_id = task.id
    JOIN stocktake_postings AS posting
      ON posting.id = NEW.posting_id
     AND posting.task_id = task.id
     AND posting.round_id = round_row.id
    WHERE task.id = NEW.task_id
      AND task.task_type = 'opening'
      AND task.status = 'posted'
      AND round_row.round_no = task.current_round_no
      AND posting.posting_kind = 'opening'
      AND NEW.region_org_id = task.region_org_id
      AND NEW.control_sync_run_id = task.control_sync_run_id
      AND NEW.item_count > 0
      AND NEW.item_count = (
          SELECT count(*) FROM stocktake_differences AS difference
           WHERE difference.task_id = task.id
             AND difference.round_id = round_row.id
             AND difference.difference_type = 'control_unassigned'))
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[4],
        "opening_control_reconciliation_runs",
        "UPDATE",
        """
EXISTS (SELECT 1 FROM reconciliation_runs AS run
         WHERE run.id = OLD.run_id AND run.status = 'approved')
OR NEW.run_id IS NOT OLD.run_id
OR NEW.create_command_id IS NOT OLD.create_command_id
OR NEW.task_id IS NOT OLD.task_id
OR NEW.round_id IS NOT OLD.round_id
OR NEW.region_org_id IS NOT OLD.region_org_id
OR NEW.posting_id IS NOT OLD.posting_id
OR NEW.control_sync_run_id IS NOT OLD.control_sync_run_id
OR NEW.item_count IS NOT OLD.item_count
OR NEW.item_manifest_sha256 IS NOT OLD.item_manifest_sha256
OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
OR NEW.created_by_person_id IS NOT OLD.created_by_person_id
OR NEW.created_role_assignment_id IS NOT OLD.created_role_assignment_id
OR NEW.created_authorization_version IS NOT OLD.created_authorization_version
OR NEW.created_at IS NOT OLD.created_at
OR NEW.version <> OLD.version + 1
OR NOT EXISTS (
    SELECT 1 FROM reconciliation_commands AS command
     WHERE command.run_id = NEW.run_id
       AND command.target_version = NEW.version
       AND command.occurred_at = NEW.updated_at
       AND ((command.operation = 'explain_opening'
             AND NEW.approved_by_user_id IS OLD.approved_by_user_id
             AND NEW.approved_by_person_id IS OLD.approved_by_person_id
             AND NEW.approved_role_assignment_id IS OLD.approved_role_assignment_id
             AND NEW.approved_authorization_version IS
                 OLD.approved_authorization_version
             AND NEW.approved_at IS OLD.approved_at
             AND NEW.approval_comment IS OLD.approval_comment)
            OR (command.operation = 'approve_opening'
                AND OLD.approved_by_user_id IS NULL
                AND NEW.approved_by_user_id = command.actor_user_id
                AND NEW.approved_by_person_id = command.actor_person_id
                AND NEW.approved_role_assignment_id = command.actor_role_assignment_id
                AND NEW.approved_authorization_version = command.authorization_version
                AND NEW.approved_at = command.occurred_at
                AND length(trim(NEW.approval_comment)) >= 4
                AND NEW.approval_comment = trim(NEW.approval_comment)
                AND NOT EXISTS (
                    SELECT 1 FROM opening_control_reconciliation_items AS item
                     WHERE item.run_id = NEW.run_id
                       AND item.explained_by_user_id = NEW.approved_by_user_id))))
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[5],
        "opening_control_reconciliation_runs",
        "DELETE",
        "1 = 1",
    )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[6],
        "opening_control_reconciliation_items",
        "INSERT",
        """
NEW.version <> 0
OR NEW.updated_at <> NEW.created_at
OR NEW.evidence_reference <> ''
OR NEW.explained_by_user_id IS NOT NULL
OR NEW.explained_by_person_id IS NOT NULL
OR NEW.explained_role_assignment_id IS NOT NULL
OR NEW.explanation_authorization_version IS NOT NULL
OR NEW.explained_at IS NOT NULL
OR NEW.evidence_file_sha256 IS NOT NULL
OR NEW.evidence_file_size_bytes IS NOT NULL
OR NEW.evidence_file_mime_type IS NOT NULL
OR EXISTS (SELECT 1 FROM reconciliation_items AS item WHERE item.id = NEW.item_id)
OR EXISTS (
    SELECT 1 FROM reconciliation_commands AS command
     WHERE command.run_id = NEW.run_id)
OR NOT EXISTS (
    SELECT 1 FROM opening_control_reconciliation_runs AS run_binding
    JOIN stocktake_differences AS difference
      ON difference.id = NEW.difference_id
     AND difference.task_id = run_binding.task_id
     AND difference.round_id = run_binding.round_id
    JOIN stocktake_control_snapshot_lines AS control
      ON control.id = NEW.control_snapshot_line_id
     AND control.task_id = run_binding.task_id
    WHERE run_binding.run_id = NEW.run_id
      AND NEW.task_id = run_binding.task_id
      AND NEW.round_id = run_binding.round_id
      AND difference.difference_type = 'control_unassigned'
      AND difference.control_snapshot_line_id = control.id)
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[7],
        "opening_control_reconciliation_items",
        "UPDATE",
        """
EXISTS (SELECT 1 FROM reconciliation_runs AS run
         WHERE run.id = OLD.run_id AND run.status = 'approved')
OR NEW.item_id IS NOT OLD.item_id
OR NEW.run_id IS NOT OLD.run_id
OR NEW.task_id IS NOT OLD.task_id
OR NEW.round_id IS NOT OLD.round_id
OR NEW.difference_id IS NOT OLD.difference_id
OR NEW.control_snapshot_line_id IS NOT OLD.control_snapshot_line_id
OR NEW.created_at IS NOT OLD.created_at
OR NEW.version <> OLD.version + 1
OR NOT EXISTS (
    SELECT 1 FROM reconciliation_commands AS command
     WHERE command.run_id = NEW.run_id
       AND command.target_version = NEW.version
       AND command.occurred_at = NEW.updated_at
       AND ((command.operation = 'explain_opening'
             AND length(trim(NEW.evidence_reference)) >= 4
             AND NEW.evidence_reference = trim(NEW.evidence_reference)
             AND NEW.explained_by_user_id = command.actor_user_id
             AND NEW.explained_by_person_id = command.actor_person_id
             AND NEW.explained_role_assignment_id = command.actor_role_assignment_id
             AND NEW.explanation_authorization_version = command.authorization_version
             AND NEW.explained_at = command.occurred_at
             AND EXISTS (
                 SELECT 1 FROM reconciliation_items AS item
                 LEFT JOIN files AS evidence ON evidence.id = item.evidence_file_id
                  WHERE item.id = NEW.item_id
                    AND ((item.evidence_file_id IS NULL
                          AND NEW.evidence_file_sha256 IS NULL
                          AND NEW.evidence_file_size_bytes IS NULL
                          AND NEW.evidence_file_mime_type IS NULL)
                         OR (item.evidence_file_id IS NOT NULL
                             AND evidence.status = 'available'
                             AND NEW.evidence_file_sha256 = evidence.sha256
                             AND NEW.evidence_file_size_bytes = evidence.size_bytes
                             AND NEW.evidence_file_mime_type = evidence.mime_type))))
            OR (command.operation = 'approve_opening'
                AND NEW.evidence_reference IS OLD.evidence_reference
                AND NEW.evidence_file_sha256 IS OLD.evidence_file_sha256
                AND NEW.evidence_file_size_bytes IS OLD.evidence_file_size_bytes
                AND NEW.evidence_file_mime_type IS OLD.evidence_file_mime_type
                AND NEW.explained_by_user_id IS OLD.explained_by_user_id
                AND NEW.explained_by_person_id IS OLD.explained_by_person_id
                AND NEW.explained_role_assignment_id IS
                    OLD.explained_role_assignment_id
                AND NEW.explanation_authorization_version IS
                    OLD.explanation_authorization_version
                AND NEW.explained_at IS OLD.explained_at)))
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[8],
        "opening_control_reconciliation_items",
        "DELETE",
        "1 = 1",
    )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[13],
        "reconciliation_runs",
        "INSERT",
        """
NOT EXISTS (
    SELECT 1
      FROM opening_control_reconciliation_runs AS binding
      JOIN stocktake_tasks AS task ON task.id = binding.task_id
      JOIN stocktake_rounds AS round_row
        ON round_row.id = binding.round_id AND round_row.task_id = task.id
      JOIN inventory_opening_establishments AS establishment
        ON establishment.task_id = task.id
       AND establishment.round_id = round_row.id
       AND establishment.posting_id = binding.posting_id
     WHERE binding.run_id = NEW.id
       AND task.task_type = 'opening'
       AND task.status = 'posted'
       AND round_row.round_no = task.current_round_no
       AND binding.region_org_id = task.region_org_id
       AND binding.control_sync_run_id = task.control_sync_run_id
       AND NOT EXISTS (
            SELECT 1 FROM reconciliation_commands AS command
             WHERE command.id = binding.create_command_id
                OR command.run_id = NEW.id)
       AND replace(NEW.run_key, '-', '') =
           'openingcontrol:' || replace(task.id, '-', '') || ':' ||
           replace(round_row.id, '-', '')
       AND NEW.source_system_id = task.control_source_system_id
       AND replace(NEW.scope, '-', '') =
           'opening:' || replace(task.region_org_id, '-', '') || ':' ||
           replace(task.id, '-', '')
       AND NEW.external_snapshot_at = task.control_snapshot_at
       AND replace(NEW.local_ledger_cursor, '-', '') =
           replace(establishment.established_ledger_cursor, '-', '')
       AND NEW.status = 'differences'
       AND json_valid(NEW.summary_jsonb)
       AND json_extract(NEW.summary_jsonb, '$.schema') =
           'cloud_oam.opening_control_reconciliation.summary.v1'
       AND replace(json_extract(NEW.summary_jsonb, '$.task_id'), '-', '') =
           replace(task.id, '-', '')
       AND replace(json_extract(NEW.summary_jsonb, '$.round_id'), '-', '') =
           replace(round_row.id, '-', '')
       AND json_extract(NEW.summary_jsonb, '$.item_count') = binding.item_count
       AND json_extract(NEW.summary_jsonb, '$.item_manifest_sha256') =
           binding.item_manifest_sha256
       AND (SELECT count(*) FROM json_each(NEW.summary_jsonb)) = 5
       AND NEW.started_at = binding.created_at
       AND NEW.completed_at = binding.created_at
       AND NEW.created_at = binding.created_at
       AND NEW.updated_at = binding.created_at
       AND establishment.has_pending_control_difference = 1
       AND NOT EXISTS (
            SELECT 1
              FROM inventory_opening_establishments AS other_establishment
             WHERE other_establishment.task_id = task.id
               AND (other_establishment.round_id <> round_row.id
                    OR other_establishment.posting_id <> binding.posting_id
                    OR other_establishment.has_pending_control_difference = 0
                    OR replace(NEW.local_ledger_cursor, '-', '') <>
                       replace(other_establishment.established_ledger_cursor, '-', '')))
)
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[14],
        "reconciliation_items",
        "INSERT",
        """
NOT EXISTS (
    SELECT 1
      FROM opening_control_reconciliation_items AS binding
      JOIN opening_control_reconciliation_runs AS run_binding
        ON run_binding.run_id = binding.run_id
       AND run_binding.task_id = binding.task_id
       AND run_binding.round_id = binding.round_id
      JOIN stocktake_differences AS difference
        ON difference.id = binding.difference_id
       AND difference.task_id = binding.task_id
       AND difference.round_id = binding.round_id
      JOIN stocktake_control_snapshot_lines AS control
        ON control.id = binding.control_snapshot_line_id
       AND control.task_id = binding.task_id
     WHERE binding.item_id = NEW.id
       AND binding.run_id = NEW.run_id
       AND NOT EXISTS (
            SELECT 1 FROM reconciliation_commands AS command
             WHERE command.run_id = NEW.run_id)
       AND difference.difference_type = 'control_unassigned'
       AND difference.control_snapshot_line_id = control.id
       AND NEW.business_key = control.external_business_key
       AND NEW.external_qty = difference.book_qty
       AND NEW.local_qty = difference.counted_qty
       AND NEW.difference = difference.book_qty - difference.counted_qty
       AND NEW.difference <> 0
       AND NEW.status = 'difference'
       AND NEW.explanation = ''
       AND NEW.evidence_file_id IS NULL
       AND binding.evidence_file_sha256 IS NULL
       AND binding.evidence_file_size_bytes IS NULL
       AND binding.evidence_file_mime_type IS NULL
       AND NEW.created_at = binding.created_at
       AND NEW.updated_at = binding.created_at
)
""",
    )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[9],
        "reconciliation_runs",
        "UPDATE",
        """
NOT EXISTS (SELECT 1 FROM opening_control_reconciliation_runs AS binding
            WHERE binding.run_id = OLD.id)
OR OLD.status <> 'differences'
OR NEW.status <> 'approved'
OR NEW.id IS NOT OLD.id
OR NEW.run_key IS NOT OLD.run_key
OR NEW.source_system_id IS NOT OLD.source_system_id
OR NEW.scope IS NOT OLD.scope
OR NEW.external_snapshot_at IS NOT OLD.external_snapshot_at
OR NEW.local_ledger_cursor IS NOT OLD.local_ledger_cursor
OR NEW.summary_jsonb IS NOT OLD.summary_jsonb
OR NEW.started_at IS NOT OLD.started_at
OR NEW.completed_at IS NOT OLD.completed_at
OR NEW.created_at IS NOT OLD.created_at
OR NOT EXISTS (
    SELECT 1 FROM opening_control_reconciliation_runs AS binding
    JOIN reconciliation_commands AS approval
      ON approval.run_id = binding.run_id
     AND approval.operation = 'approve_opening'
     AND approval.target_version = binding.version
    WHERE binding.run_id = NEW.id
      AND binding.approved_by_user_id = approval.actor_user_id
      AND binding.approved_by_person_id = approval.actor_person_id
      AND binding.approved_role_assignment_id = approval.actor_role_assignment_id
      AND binding.approved_authorization_version = approval.authorization_version
      AND binding.approved_at = approval.occurred_at
      AND NEW.updated_at = approval.occurred_at
      AND binding.version = 1 + (
          SELECT count(*) FROM reconciliation_commands AS explanation
           WHERE explanation.run_id = NEW.id
             AND explanation.operation = 'explain_opening')
      AND binding.item_count = (
          SELECT count(*) FROM reconciliation_items AS item
           WHERE item.run_id = NEW.id)
      AND NOT EXISTS (
          SELECT 1 FROM reconciliation_items AS item
          JOIN opening_control_reconciliation_items AS item_binding
            ON item_binding.item_id = item.id
          WHERE item.run_id = NEW.id
            AND (item.status <> 'resolved'
                 OR item_binding.version <> binding.version
                 OR length(trim(item.explanation)) < 4
                 OR length(trim(item_binding.evidence_reference)) < 4
                 OR item_binding.explained_by_user_id = binding.approved_by_user_id
                 OR (item.evidence_file_id IS NULL AND
                     (item_binding.evidence_file_sha256 IS NOT NULL
                      OR item_binding.evidence_file_size_bytes IS NOT NULL
                      OR item_binding.evidence_file_mime_type IS NOT NULL))
                 OR (item.evidence_file_id IS NOT NULL AND NOT EXISTS (
                     SELECT 1 FROM files AS evidence
                      WHERE evidence.id = item.evidence_file_id
                        AND evidence.status = 'available'
                        AND item_binding.evidence_file_sha256 = evidence.sha256
                        AND item_binding.evidence_file_size_bytes = evidence.size_bytes
                        AND item_binding.evidence_file_mime_type =
                            evidence.mime_type)))))
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[10],
        "reconciliation_runs",
        "DELETE",
        "1 = 1",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[11],
        "reconciliation_items",
        "UPDATE",
        """
NOT EXISTS (SELECT 1 FROM opening_control_reconciliation_items AS binding
            WHERE binding.item_id = OLD.id AND binding.run_id = OLD.run_id)
OR OLD.status = 'resolved'
OR NEW.id IS NOT OLD.id
OR NEW.run_id IS NOT OLD.run_id
OR NEW.business_key IS NOT OLD.business_key
OR NEW.external_qty IS NOT OLD.external_qty
OR NEW.local_qty IS NOT OLD.local_qty
OR NEW.difference IS NOT OLD.difference
OR NEW.created_at IS NOT OLD.created_at
OR NOT ((NEW.status = 'explained'
         AND OLD.status IN ('difference', 'explained')
         AND length(trim(NEW.explanation)) >= 4
         AND NEW.explanation = trim(NEW.explanation)
         AND (NEW.evidence_file_id IS NULL OR EXISTS (
             SELECT 1 FROM files AS evidence
              WHERE evidence.id = NEW.evidence_file_id
                AND evidence.status = 'available'))
         AND EXISTS (
             SELECT 1 FROM reconciliation_commands AS command
             JOIN opening_control_reconciliation_items AS binding
               ON binding.item_id = OLD.id AND binding.run_id = OLD.run_id
              WHERE command.run_id = NEW.run_id
                AND command.operation = 'explain_opening'
                AND command.target_version = binding.version + 1
                AND command.occurred_at = NEW.updated_at))
        OR (NEW.status = 'resolved'
            AND OLD.status = 'explained'
            AND NEW.explanation IS OLD.explanation
            AND NEW.evidence_file_id IS OLD.evidence_file_id
            AND EXISTS (
                SELECT 1 FROM reconciliation_commands AS command
                JOIN opening_control_reconciliation_items AS binding
                  ON binding.item_id = OLD.id AND binding.run_id = OLD.run_id
                 WHERE command.run_id = NEW.run_id
                   AND command.operation = 'approve_opening'
                   AND command.target_version = binding.version + 1
                   AND command.occurred_at = NEW.updated_at)))
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[12],
        "reconciliation_items",
        "DELETE",
        "1 = 1",
    )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[15],
        "opening_control_reconciliation_command_consumptions",
        "INSERT",
        """
NOT EXISTS (
    SELECT 1
      FROM reconciliation_commands AS command
      JOIN opening_control_reconciliation_runs AS binding
        ON binding.run_id = command.run_id
      JOIN reconciliation_runs AS run ON run.id = binding.run_id
     WHERE command.id = NEW.command_id
       AND command.run_id = NEW.run_id
       AND command.operation = NEW.operation
       AND command.target_version = NEW.target_version
       AND command.occurred_at = NEW.consumed_at
       AND json_type(command.request_jsonb) = 'object'
       AND json_type(command.result_jsonb) = 'object'
       AND json_type(command.request_jsonb, '$.actor') = 'object'
       AND (SELECT count(*)
              FROM json_each(command.request_jsonb, '$.actor')) = 3
       AND json_extract(command.request_jsonb, '$.actor.user_id') =
           command.actor_user_id
       AND replace(
               json_extract(command.request_jsonb, '$.actor.person_id'),
               '-', ''
           ) = command.actor_person_id
       AND json_type(
               command.request_jsonb, '$.actor.authorization_version'
           ) = 'integer'
       AND json_extract(
               command.request_jsonb, '$.actor.authorization_version'
           ) = command.authorization_version
       AND length(command.request_hash) = 64
       AND command.request_hash NOT GLOB '*[^0-9a-f]*'
       AND length(command.result_hash) = 64
       AND command.result_hash NOT GLOB '*[^0-9a-f]*'
       AND length(command.idempotency_key_hash) = 64
       AND command.idempotency_key_hash NOT GLOB '*[^0-9a-f]*'
       AND substr(
               command.request_reference,
               1,
               length('opening-reconciliation-request-')
           ) = 'opening-reconciliation-request-'
       AND length(command.request_reference) =
           length('opening-reconciliation-request-') + 64
       AND substr(
               command.request_reference,
               length('opening-reconciliation-request-') + 1
           ) NOT GLOB '*[^0-9a-f]*'
       AND (SELECT count(*) FROM json_each(command.result_jsonb)) = 7
       AND replace(
               json_extract(
                   command.result_jsonb, '$.reconciliation_run_id'
               ), '-', ''
           ) = NEW.run_id
       AND replace(json_extract(command.result_jsonb, '$.task_id'), '-', '') =
           binding.task_id
       AND json_type(command.result_jsonb, '$.version') = 'integer'
       AND json_extract(command.result_jsonb, '$.version') = NEW.target_version
       AND binding.item_count > 0
       AND binding.item_count = (
            SELECT count(*) FROM reconciliation_items AS item
             WHERE item.run_id = NEW.run_id)
       AND binding.item_count = (
            SELECT count(*) FROM opening_control_reconciliation_items AS item
             WHERE item.run_id = NEW.run_id)
       AND ((NEW.operation = 'create_opening'
             AND NEW.target_version = 0
             AND (SELECT count(*) FROM json_each(command.request_jsonb)) = 4
             AND json_extract(command.request_jsonb, '$.schema') =
                 'cloud_oam.opening_control_reconciliation.create.v1'
             AND replace(
                     json_extract(command.request_jsonb, '$.task_id'), '-', ''
                 ) = binding.task_id
             AND json_type(
                     command.request_jsonb, '$.expected_task_version'
                 ) = 'integer'
             AND json_extract(
                     command.request_jsonb, '$.expected_task_version'
                 ) = (
                     SELECT task.version FROM stocktake_tasks AS task
                      WHERE task.id = binding.task_id)
             AND json_extract(command.result_jsonb, '$.schema') =
                 'cloud_oam.opening_control_reconciliation.create_result.v1'
             AND json_extract(command.result_jsonb, '$.status') = 'differences'
             AND json_type(command.result_jsonb, '$.item_count') = 'integer'
             AND json_extract(command.result_jsonb, '$.item_count') =
                 binding.item_count
             AND length(command.occurred_at) = 26
             AND json_extract(command.result_jsonb, '$.created_at') =
                 substr(command.occurred_at, 1, 10) || 'T' ||
                 substr(command.occurred_at, 12, 15) || 'Z'
             AND binding.create_command_id = command.id
             AND binding.version = 0
             AND binding.created_by_user_id = command.actor_user_id
             AND binding.created_by_person_id = command.actor_person_id
             AND binding.created_role_assignment_id =
                 command.actor_role_assignment_id
             AND binding.created_authorization_version =
                 command.authorization_version
             AND binding.created_at = command.occurred_at
             AND (SELECT task.posted_at FROM stocktake_tasks AS task
                    WHERE task.id = binding.task_id) IS NOT NULL
             AND binding.created_at >= (
                 SELECT task.posted_at FROM stocktake_tasks AS task
                  WHERE task.id = binding.task_id
             )
             AND binding.updated_at = binding.created_at
             AND run.started_at = command.occurred_at
             AND run.completed_at = command.occurred_at
             AND run.created_at = command.occurred_at
             AND run.updated_at = command.occurred_at
             AND run.status = 'differences'
             AND NOT EXISTS (
                 SELECT 1 FROM reconciliation_items AS item
                 JOIN opening_control_reconciliation_items AS item_binding
                   ON item_binding.item_id = item.id
                  AND item_binding.run_id = item.run_id
                  WHERE item.run_id = NEW.run_id
                    AND (item.status <> 'difference'
                         OR item.explanation <> ''
                         OR item.evidence_file_id IS NOT NULL
                         OR item_binding.version <> 0
                         OR item_binding.evidence_reference <> ''
                         OR item_binding.evidence_file_sha256 IS NOT NULL
                         OR item_binding.evidence_file_size_bytes IS NOT NULL
                         OR item_binding.evidence_file_mime_type IS NOT NULL
                         OR item_binding.explained_by_user_id IS NOT NULL
                         OR item_binding.explained_by_person_id IS NOT NULL
                         OR item_binding.explained_role_assignment_id IS NOT NULL
                         OR item_binding.explanation_authorization_version IS NOT NULL
                         OR item_binding.explained_at IS NOT NULL)))
            OR (NEW.operation = 'explain_opening'
                AND (SELECT count(*) FROM json_each(command.request_jsonb)) = 5
                AND json_extract(command.request_jsonb, '$.schema') =
                    'cloud_oam.opening_control_reconciliation.explain.v1'
                AND replace(
                        json_extract(
                            command.request_jsonb, '$.reconciliation_run_id'
                        ), '-', ''
                    ) = NEW.run_id
                AND json_type(
                        command.request_jsonb, '$.expected_version'
                    ) = 'integer'
                AND json_extract(
                        command.request_jsonb, '$.expected_version'
                    ) = NEW.target_version - 1
                AND json_type(command.request_jsonb, '$.items') = 'array'
                AND json_array_length(
                        command.request_jsonb, '$.items'
                    ) = binding.item_count
                AND NOT EXISTS (
                    SELECT 1
                      FROM json_each(
                               command.request_jsonb, '$.items'
                           ) AS supplied
                     WHERE json_type(supplied.value) <> 'object'
                        OR (SELECT count(*)
                              FROM json_each(supplied.value)) <> 5
                        OR NOT EXISTS (
                            SELECT 1
                              FROM reconciliation_items AS supplied_item
                              JOIN opening_control_reconciliation_items
                                   AS supplied_binding
                                ON supplied_binding.item_id = supplied_item.id
                               AND supplied_binding.run_id = supplied_item.run_id
                             WHERE supplied_item.run_id = NEW.run_id
                               AND replace(
                                       json_extract(
                                           supplied.value,
                                           '$.reconciliation_item_id'
                                       ), '-', ''
                                   ) = supplied_item.id
                               AND json_type(
                                       supplied.value, '$.expected_version'
                                   ) = 'integer'
                               AND json_extract(
                                       supplied.value, '$.expected_version'
                                   ) = NEW.target_version - 1
                               AND json_extract(
                                       supplied.value, '$.explanation'
                                   ) = supplied_item.explanation
                               AND json_extract(
                                       supplied.value, '$.evidence_reference'
                                   ) = supplied_binding.evidence_reference
                               AND (
                                   (supplied_item.evidence_file_id IS NULL
                                    AND json_type(
                                        supplied.value, '$.evidence_file_id'
                                    ) = 'null')
                                   OR (supplied_item.evidence_file_id IS NOT NULL
                                       AND replace(
                                           json_extract(
                                               supplied.value,
                                               '$.evidence_file_id'
                                           ), '-', ''
                                       ) = supplied_item.evidence_file_id)
                               ))
                )
                AND NOT EXISTS (
                    SELECT 1
                      FROM reconciliation_items AS expected_item
                     WHERE expected_item.run_id = NEW.run_id
                       AND NOT EXISTS (
                           SELECT 1
                             FROM json_each(
                                      command.request_jsonb, '$.items'
                                  ) AS supplied
                            WHERE replace(
                                      json_extract(
                                          supplied.value,
                                          '$.reconciliation_item_id'
                                      ), '-', ''
                                  ) = expected_item.id)
                )
                AND json_extract(command.result_jsonb, '$.schema') =
                    'cloud_oam.opening_control_reconciliation.explain_result.v1'
                AND json_extract(command.result_jsonb, '$.status') = 'differences'
                AND json_type(
                        command.result_jsonb, '$.explained_item_count'
                    ) = 'integer'
                AND json_extract(
                        command.result_jsonb, '$.explained_item_count'
                    ) = binding.item_count
                AND length(command.occurred_at) = 26
                AND json_extract(command.result_jsonb, '$.explained_at') =
                    substr(command.occurred_at, 1, 10) || 'T' ||
                    substr(command.occurred_at, 12, 15) || 'Z'
                AND NEW.target_version > 0
                AND binding.version = NEW.target_version
                AND binding.updated_at = command.occurred_at
                AND binding.approved_at IS NULL
                AND run.status = 'differences'
                AND NOT EXISTS (
                    SELECT 1 FROM reconciliation_items AS item
                    JOIN opening_control_reconciliation_items AS item_binding
                      ON item_binding.item_id = item.id
                     AND item_binding.run_id = item.run_id
                     WHERE item.run_id = NEW.run_id
                       AND (item.status <> 'explained'
                            OR item.updated_at <> command.occurred_at
                            OR length(trim(item.explanation)) < 4
                            OR item.explanation <> trim(item.explanation)
                            OR item_binding.version <> NEW.target_version
                            OR item_binding.updated_at <> command.occurred_at
                            OR length(trim(item_binding.evidence_reference)) < 4
                            OR item_binding.evidence_reference <>
                               trim(item_binding.evidence_reference)
                            OR item_binding.explained_by_user_id <>
                               command.actor_user_id
                            OR item_binding.explained_by_person_id <>
                               command.actor_person_id
                            OR item_binding.explained_role_assignment_id <>
                               command.actor_role_assignment_id
                            OR item_binding.explanation_authorization_version <>
                               command.authorization_version
                            OR item_binding.explained_at <> command.occurred_at
                            OR (item.evidence_file_id IS NULL AND
                                (item_binding.evidence_file_sha256 IS NOT NULL
                                 OR item_binding.evidence_file_size_bytes IS NOT NULL
                                 OR item_binding.evidence_file_mime_type IS NOT NULL))
                            OR (item.evidence_file_id IS NOT NULL AND NOT EXISTS (
                                SELECT 1 FROM files AS evidence
                                 WHERE evidence.id = item.evidence_file_id
                                   AND evidence.status = 'available'
                                   AND item_binding.evidence_file_sha256 = evidence.sha256
                                   AND item_binding.evidence_file_size_bytes =
                                       evidence.size_bytes
                                   AND item_binding.evidence_file_mime_type =
                                       evidence.mime_type)))))
            OR (NEW.operation = 'approve_opening'
                AND (SELECT count(*) FROM json_each(command.request_jsonb)) = 5
                AND json_extract(command.request_jsonb, '$.schema') =
                    'cloud_oam.opening_control_reconciliation.approve.v1'
                AND replace(
                        json_extract(
                            command.request_jsonb, '$.reconciliation_run_id'
                        ), '-', ''
                    ) = NEW.run_id
                AND json_type(
                        command.request_jsonb, '$.expected_version'
                    ) = 'integer'
                AND json_extract(
                        command.request_jsonb, '$.expected_version'
                    ) = NEW.target_version - 1
                AND json_extract(command.request_jsonb, '$.comment') =
                    binding.approval_comment
                AND json_extract(command.result_jsonb, '$.schema') =
                    'cloud_oam.opening_control_reconciliation.approve_result.v1'
                AND json_extract(command.result_jsonb, '$.status') = 'approved'
                AND json_type(
                        command.result_jsonb, '$.resolved_item_count'
                    ) = 'integer'
                AND json_extract(
                        command.result_jsonb, '$.resolved_item_count'
                    ) = binding.item_count
                AND length(command.occurred_at) = 26
                AND json_extract(command.result_jsonb, '$.approved_at') =
                    substr(command.occurred_at, 1, 10) || 'T' ||
                    substr(command.occurred_at, 12, 15) || 'Z'
                AND NEW.target_version > 0
                AND binding.version = NEW.target_version
                AND binding.updated_at = command.occurred_at
                AND binding.approved_by_user_id = command.actor_user_id
                AND binding.approved_by_person_id = command.actor_person_id
                AND binding.approved_role_assignment_id =
                    command.actor_role_assignment_id
                AND binding.approved_authorization_version =
                    command.authorization_version
                AND binding.approved_at = command.occurred_at
                AND run.status = 'approved'
                AND run.updated_at = command.occurred_at
                AND NOT EXISTS (
                    SELECT 1 FROM reconciliation_items AS item
                    JOIN opening_control_reconciliation_items AS item_binding
                      ON item_binding.item_id = item.id
                     AND item_binding.run_id = item.run_id
                     WHERE item.run_id = NEW.run_id
                       AND (item.status <> 'resolved'
                            OR item.updated_at <> command.occurred_at
                            OR item_binding.version <> NEW.target_version
                            OR item_binding.updated_at <> command.occurred_at))))
       AND ((NEW.operation = 'create_opening'
             AND 1 = (
                 SELECT count(*) FROM state_transition_events AS transition
                  WHERE transition.reason = 'reconciliation.opening.create'
                    AND transition.aggregate_type = 'reconciliation_run'
                    AND replace(transition.aggregate_id, '-', '') = NEW.run_id
                    AND transition.from_status IS NULL
                    AND transition.to_status = 'differences'
                    AND transition.actor_id = command.actor_user_id
                    AND substr(
                            transition.idempotency_key,
                            1,
                            length('opening-reconciliation-state-1-')
                        ) = 'opening-reconciliation-state-1-'
                    AND length(transition.idempotency_key) =
                        length('opening-reconciliation-state-1-') + 64
                    AND substr(
                            transition.idempotency_key,
                            length('opening-reconciliation-state-1-') + 1
                        ) NOT GLOB '*[^0-9a-f]*'
                    AND transition.occurred_at = command.occurred_at
                    AND transition.created_at = command.occurred_at
                    AND replace(
                        json_extract(
                            transition.metadata_jsonb,
                            '$.reconciliation_run_id'
                        ), '-', ''
                    ) = NEW.run_id
                    AND json_extract(
                        transition.metadata_jsonb, '$.result_hash'
                    ) = command.result_hash))
            OR (NEW.operation = 'explain_opening'
                AND ((NEW.target_version = 1
                      AND binding.item_count = (
                          SELECT count(*)
                            FROM state_transition_events AS transition
                           WHERE transition.reason =
                                 'reconciliation.opening.explain'
                             AND transition.aggregate_type =
                                 'reconciliation_item'
                             AND transition.from_status = 'difference'
                             AND transition.to_status = 'explained'
                             AND transition.actor_id = command.actor_user_id
                             AND transition.occurred_at = command.occurred_at
                             AND transition.created_at = command.occurred_at
                             AND replace(
                                 json_extract(
                                     transition.metadata_jsonb,
                                     '$.reconciliation_run_id'
                                 ), '-', ''
                             ) = NEW.run_id
                             AND json_extract(
                                 transition.metadata_jsonb, '$.result_hash'
                             ) = command.result_hash)
                      AND NOT EXISTS (
                          SELECT 1 FROM reconciliation_items AS item
                           WHERE item.run_id = NEW.run_id
                             AND NOT EXISTS (
                                 SELECT 1
                                   FROM state_transition_events AS transition
                                  WHERE transition.reason =
                                        'reconciliation.opening.explain'
                                    AND transition.aggregate_type =
                                        'reconciliation_item'
                                    AND replace(transition.aggregate_id, '-', '') =
                                        item.id
                                    AND transition.from_status = 'difference'
                                    AND transition.to_status = 'explained'
                                    AND transition.actor_id = command.actor_user_id
                                    AND substr(
                                            transition.idempotency_key,
                                            1,
                                            length(
                                                'opening-reconciliation-state-' ||
                                                (1 + (
                                                    SELECT count(*)
                                                      FROM reconciliation_items
                                                           AS prior
                                                     WHERE prior.run_id = item.run_id
                                                       AND prior.id < item.id
                                                )) || '-'
                                            )
                                        ) = 'opening-reconciliation-state-' ||
                                            (1 + (
                                                SELECT count(*)
                                                  FROM reconciliation_items AS prior
                                                 WHERE prior.run_id = item.run_id
                                                   AND prior.id < item.id
                                            )) || '-'
                                    AND length(transition.idempotency_key) =
                                        length(
                                            'opening-reconciliation-state-' ||
                                            (1 + (
                                                SELECT count(*)
                                                  FROM reconciliation_items AS prior
                                                 WHERE prior.run_id = item.run_id
                                                   AND prior.id < item.id
                                            )) || '-'
                                        ) + 64
                                    AND substr(
                                            transition.idempotency_key,
                                            length(
                                                'opening-reconciliation-state-' ||
                                                (1 + (
                                                    SELECT count(*)
                                                      FROM reconciliation_items
                                                           AS prior
                                                     WHERE prior.run_id = item.run_id
                                                       AND prior.id < item.id
                                                )) || '-'
                                            ) + 1
                                        ) NOT GLOB '*[^0-9a-f]*'
                                    AND transition.occurred_at =
                                        command.occurred_at
                                    AND json_extract(
                                        transition.metadata_jsonb,
                                        '$.result_hash'
                                    ) = command.result_hash)))
                     OR (NEW.target_version > 1
                         AND 0 = (
                             SELECT count(*)
                               FROM state_transition_events AS transition
                              WHERE transition.reason =
                                    'reconciliation.opening.explain'
                                AND transition.actor_id = command.actor_user_id
                                AND transition.occurred_at = command.occurred_at
                                AND replace(
                                    json_extract(
                                        transition.metadata_jsonb,
                                        '$.reconciliation_run_id'
                                    ), '-', ''
                                ) = NEW.run_id
                                AND json_extract(
                                    transition.metadata_jsonb, '$.result_hash'
                                ) = command.result_hash))))
            OR (NEW.operation = 'approve_opening'
                AND binding.item_count + 1 = (
                    SELECT count(*)
                      FROM state_transition_events AS transition
                     WHERE transition.reason = 'reconciliation.opening.approve'
                       AND transition.actor_id = command.actor_user_id
                       AND transition.occurred_at = command.occurred_at
                       AND transition.created_at = command.occurred_at
                       AND replace(
                           json_extract(
                               transition.metadata_jsonb,
                               '$.reconciliation_run_id'
                           ), '-', ''
                       ) = NEW.run_id
                       AND json_extract(
                           transition.metadata_jsonb, '$.result_hash'
                       ) = command.result_hash)
                AND 1 = (
                    SELECT count(*)
                      FROM state_transition_events AS transition
                     WHERE transition.reason = 'reconciliation.opening.approve'
                       AND transition.aggregate_type = 'reconciliation_run'
                       AND replace(transition.aggregate_id, '-', '') = NEW.run_id
                       AND transition.from_status = 'differences'
                       AND transition.to_status = 'approved'
                       AND transition.actor_id = command.actor_user_id
                       AND substr(
                               transition.idempotency_key,
                               1,
                               length(
                                   'opening-reconciliation-state-' ||
                                   (binding.item_count + 1) || '-'
                               )
                           ) = 'opening-reconciliation-state-' ||
                               (binding.item_count + 1) || '-'
                       AND length(transition.idempotency_key) =
                           length(
                               'opening-reconciliation-state-' ||
                               (binding.item_count + 1) || '-'
                           ) + 64
                       AND substr(
                               transition.idempotency_key,
                               length(
                                   'opening-reconciliation-state-' ||
                                   (binding.item_count + 1) || '-'
                               ) + 1
                           ) NOT GLOB '*[^0-9a-f]*'
                       AND transition.occurred_at = command.occurred_at
                       AND json_extract(
                           transition.metadata_jsonb, '$.result_hash'
                       ) = command.result_hash)
                AND NOT EXISTS (
                    SELECT 1 FROM reconciliation_items AS item
                     WHERE item.run_id = NEW.run_id
                       AND NOT EXISTS (
                           SELECT 1 FROM state_transition_events AS transition
                            WHERE transition.reason =
                                  'reconciliation.opening.approve'
                              AND transition.aggregate_type =
                                  'reconciliation_item'
                              AND replace(transition.aggregate_id, '-', '') =
                                  item.id
                              AND transition.from_status = 'explained'
                              AND transition.to_status = 'resolved'
                              AND transition.actor_id = command.actor_user_id
                              AND substr(
                                      transition.idempotency_key,
                                      1,
                                      length(
                                          'opening-reconciliation-state-' ||
                                          (1 + (
                                              SELECT count(*)
                                                FROM reconciliation_items AS prior
                                               WHERE prior.run_id = item.run_id
                                                 AND prior.id < item.id
                                          )) || '-'
                                      )
                                  ) = 'opening-reconciliation-state-' ||
                                      (1 + (
                                          SELECT count(*)
                                            FROM reconciliation_items AS prior
                                           WHERE prior.run_id = item.run_id
                                             AND prior.id < item.id
                                      )) || '-'
                              AND length(transition.idempotency_key) =
                                  length(
                                      'opening-reconciliation-state-' ||
                                      (1 + (
                                          SELECT count(*)
                                            FROM reconciliation_items AS prior
                                           WHERE prior.run_id = item.run_id
                                             AND prior.id < item.id
                                      )) || '-'
                                  ) + 64
                              AND substr(
                                      transition.idempotency_key,
                                      length(
                                          'opening-reconciliation-state-' ||
                                          (1 + (
                                              SELECT count(*)
                                                FROM reconciliation_items AS prior
                                               WHERE prior.run_id = item.run_id
                                                 AND prior.id < item.id
                                          )) || '-'
                                      ) + 1
                                  ) NOT GLOB '*[^0-9a-f]*'
                              AND transition.occurred_at = command.occurred_at
                              AND json_extract(
                                  transition.metadata_jsonb, '$.result_hash'
                              ) = command.result_hash))))
       AND CASE NEW.operation
               WHEN 'create_opening' THEN 1
               WHEN 'explain_opening' THEN binding.item_count
               ELSE binding.item_count + 1
           END = (
               SELECT count(*)
                 FROM state_transition_events AS transition
                WHERE transition.reason = CASE NEW.operation
                          WHEN 'create_opening' THEN
                              'reconciliation.opening.create'
                          WHEN 'explain_opening' THEN
                              'reconciliation.opening.explain'
                          ELSE 'reconciliation.opening.approve'
                      END
                  AND replace(
                          json_extract(
                              transition.metadata_jsonb,
                              '$.reconciliation_run_id'
                          ), '-', ''
                      ) = NEW.run_id)
       AND (
               SELECT count(*) FROM reconciliation_commands AS scoped_command
                WHERE scoped_command.run_id = NEW.run_id
                  AND scoped_command.operation = NEW.operation
           ) = (
               SELECT count(*) FROM outbox_events AS scoped_outbox
                WHERE scoped_outbox.event_type = CASE NEW.operation
                          WHEN 'create_opening' THEN
                              'reconciliation.opening.create'
                          WHEN 'explain_opening' THEN
                              'reconciliation.opening.explain'
                          ELSE 'reconciliation.opening.approve'
                      END
                  AND scoped_outbox.aggregate_type = 'reconciliation_run'
                  AND replace(scoped_outbox.aggregate_id, '-', '') = NEW.run_id)
       AND (
               SELECT count(*) FROM reconciliation_commands AS scoped_command
                WHERE scoped_command.run_id = NEW.run_id
                  AND scoped_command.operation = NEW.operation
           ) = (
               SELECT count(*) FROM audit_events AS scoped_audit
                WHERE scoped_audit.stream_key = 'inventory'
                  AND scoped_audit.action = CASE NEW.operation
                          WHEN 'create_opening' THEN
                              'reconciliation.opening.create'
                          WHEN 'explain_opening' THEN
                              'reconciliation.opening.explain'
                          ELSE 'reconciliation.opening.approve'
                      END
                  AND scoped_audit.aggregate_type = 'reconciliation_run'
                  AND replace(scoped_audit.aggregate_id, '-', '') = NEW.run_id)
       AND 1 = (
            SELECT count(*) FROM outbox_events AS outbox
             WHERE outbox.event_type = CASE NEW.operation
                       WHEN 'create_opening' THEN 'reconciliation.opening.create'
                       WHEN 'explain_opening' THEN 'reconciliation.opening.explain'
                       ELSE 'reconciliation.opening.approve'
                   END
               AND outbox.aggregate_type = 'reconciliation_run'
               AND replace(outbox.aggregate_id, '-', '') = NEW.run_id
               AND substr(
                       outbox.idempotency_key,
                       1,
                       length('opening-reconciliation-outbox-')
                   ) = 'opening-reconciliation-outbox-'
               AND length(outbox.idempotency_key) =
                   length('opening-reconciliation-outbox-') + 64
               AND substr(
                       outbox.idempotency_key,
                       length('opening-reconciliation-outbox-') + 1
                   ) NOT GLOB '*[^0-9a-f]*'
               AND replace(
                   json_extract(outbox.payload_jsonb, '$.reconciliation_run_id'),
                   '-', ''
               ) = NEW.run_id
               AND json_extract(outbox.payload_jsonb, '$.result_hash') =
                   command.result_hash
               AND (SELECT count(*)
                      FROM json_each(
                          json_extract(outbox.payload_jsonb, '$.result')
                      )) = (
                          SELECT count(*) FROM json_each(command.result_jsonb)
                      )
               AND NOT EXISTS (
                   SELECT 1
                     FROM json_each(
                         json_extract(outbox.payload_jsonb, '$.result')
                     ) AS actual_result
                     LEFT JOIN json_each(command.result_jsonb) AS expected_result
                       ON expected_result.key = actual_result.key
                    WHERE expected_result.key IS NULL
                       OR expected_result.type <> actual_result.type
                       OR expected_result.atom IS NOT actual_result.atom
               )
               AND (SELECT count(*) FROM json_each(outbox.payload_jsonb)) = 3
               AND outbox.status = 'pending'
               AND outbox.attempts = 0
               AND outbox.available_at = command.occurred_at
               AND outbox.created_at = command.occurred_at
               AND outbox.updated_at = command.occurred_at
               AND outbox.locked_at IS NULL
               AND outbox.locked_by IS NULL
               AND outbox.published_at IS NULL
               AND outbox.last_error IS NULL)
       AND 1 = (
            SELECT count(*) FROM audit_events AS audit
             WHERE audit.stream_key = 'inventory'
               AND audit.actor_user_id = command.actor_user_id
               AND audit.action = CASE NEW.operation
                       WHEN 'create_opening' THEN 'reconciliation.opening.create'
                       WHEN 'explain_opening' THEN 'reconciliation.opening.explain'
                       ELSE 'reconciliation.opening.approve'
                   END
               AND audit.aggregate_type = 'reconciliation_run'
               AND replace(audit.aggregate_id, '-', '') = NEW.run_id
               AND audit.request_id = command.request_reference
               AND audit.occurred_at = command.occurred_at
               AND (SELECT count(*)
                      FROM json_each(
                          json_extract(audit.after_jsonb, '$.result')
                      )) = (
                          SELECT count(*) FROM json_each(command.result_jsonb)
                      )
               AND NOT EXISTS (
                   SELECT 1
                     FROM json_each(
                         json_extract(audit.after_jsonb, '$.result')
                     ) AS actual_result
                     LEFT JOIN json_each(command.result_jsonb) AS expected_result
                       ON expected_result.key = actual_result.key
                    WHERE expected_result.key IS NULL
                       OR expected_result.type <> actual_result.type
                       OR expected_result.atom IS NOT actual_result.atom
               )
               AND json_extract(audit.after_jsonb, '$.result_hash') =
                   command.result_hash
               AND replace(
                   json_extract(audit.after_jsonb, '$.actor_person_id'), '-', ''
               ) = command.actor_person_id
               AND replace(
                   json_extract(
                       audit.after_jsonb, '$.actor_role_assignment_id'
                   ), '-', ''
               ) = command.actor_role_assignment_id
               AND json_extract(audit.after_jsonb, '$.authorization_version') =
                   command.authorization_version
               AND json_extract(audit.after_jsonb, '$.actor_role_code') =
                   CASE NEW.operation
                       WHEN 'explain_opening' THEN 'provincial_manager'
                       ELSE 'admin'
                   END
               AND json_extract(audit.after_jsonb, '$.actor_scope_type') =
                   CASE NEW.operation
                       WHEN 'explain_opening' THEN 'organization'
                       ELSE 'national'
                   END
               AND replace(
                   json_extract(audit.after_jsonb, '$.actor_scope_id'), '-', ''
               ) = CASE NEW.operation
                       WHEN 'explain_opening' THEN binding.region_org_id
                       ELSE '*'
                   END
               AND (SELECT count(*) FROM json_each(audit.after_jsonb)) = 8
               AND ((NEW.operation = 'create_opening'
                     AND json_type(audit.before_jsonb, '$.status') = 'null'
                     AND json_type(audit.before_jsonb, '$.version') = 'null'
                     AND (SELECT count(*) FROM json_each(audit.before_jsonb)) = 2)
                    OR (NEW.operation <> 'create_opening'
                        AND json_extract(audit.before_jsonb, '$.status') =
                            'differences'
                        AND json_extract(audit.before_jsonb, '$.version') =
                            NEW.target_version - 1
                        AND (SELECT count(*)
                               FROM json_each(
                                   audit.before_jsonb, '$.item_versions'
                               )) = binding.item_count
                        AND NOT EXISTS (
                            SELECT 1 FROM reconciliation_items AS item
                             WHERE item.run_id = NEW.run_id
                               AND NOT EXISTS (
                                   SELECT 1
                                     FROM json_each(
                                         audit.before_jsonb,
                                         '$.item_versions'
                                     ) AS version_entry
                                    WHERE replace(version_entry.key, '-', '') =
                                          item.id
                                      AND version_entry.value =
                                          NEW.target_version - 1))
                        AND (SELECT count(*)
                               FROM json_each(audit.before_jsonb)) = 3)))
)
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[16],
        "opening_control_reconciliation_command_consumptions",
        "UPDATE",
        "1 = 1",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[17],
        "opening_control_reconciliation_command_consumptions",
        "DELETE",
        "1 = 1",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[18],
        "state_transition_events",
        "INSERT",
        """
NEW.reason IN (
    'reconciliation.opening.create',
    'reconciliation.opening.explain',
    'reconciliation.opening.approve'
) AND NOT EXISTS (
    SELECT 1
      FROM opening_control_reconciliation_runs AS binding
      JOIN reconciliation_commands AS command ON command.run_id = binding.run_id
      LEFT JOIN opening_control_reconciliation_command_consumptions AS seal
        ON seal.command_id = command.id
       AND seal.run_id = command.run_id
       AND seal.operation = command.operation
       AND seal.target_version = command.target_version
     WHERE binding.run_id = replace(
               json_extract(NEW.metadata_jsonb, '$.reconciliation_run_id'),
               '-', ''
           )
       AND command.operation = CASE NEW.reason
               WHEN 'reconciliation.opening.create' THEN 'create_opening'
               WHEN 'reconciliation.opening.explain' THEN 'explain_opening'
               ELSE 'approve_opening'
           END
       AND seal.command_id IS NULL
       AND NEW.actor_id = command.actor_user_id
       AND NEW.occurred_at = command.occurred_at
       AND NEW.created_at = command.occurred_at
       AND (SELECT count(*) FROM json_each(NEW.metadata_jsonb)) = 2
       AND json_extract(NEW.metadata_jsonb, '$.result_hash') = command.result_hash
       AND length(NEW.idempotency_key) >= 65
       AND substr(NEW.idempotency_key, length(NEW.idempotency_key) - 63)
           NOT GLOB '*[^0-9a-f]*'
       AND ((command.operation = 'create_opening'
             AND NEW.aggregate_type = 'reconciliation_run'
             AND replace(NEW.aggregate_id, '-', '') = command.run_id
             AND NEW.from_status IS NULL
             AND NEW.to_status = 'differences'
             AND substr(
                     NEW.idempotency_key,
                     1,
                     length('opening-reconciliation-state-1-')
                 ) = 'opening-reconciliation-state-1-'
             AND length(NEW.idempotency_key) =
                 length('opening-reconciliation-state-1-') + 64)
            OR (command.operation = 'explain_opening'
                AND command.target_version = 1
                AND EXISTS (
                    SELECT 1 FROM reconciliation_items AS item
                     WHERE item.run_id = command.run_id
                       AND NEW.aggregate_type = 'reconciliation_item'
                       AND replace(NEW.aggregate_id, '-', '') = item.id
                       AND NEW.from_status = 'difference'
                       AND NEW.to_status = 'explained'
                       AND substr(
                               NEW.idempotency_key,
                               1,
                               length(
                                   'opening-reconciliation-state-' ||
                                   (1 + (
                                       SELECT count(*)
                                         FROM reconciliation_items AS prior
                                        WHERE prior.run_id = item.run_id
                                          AND prior.id < item.id
                                   )) || '-'
                               )
                           ) = 'opening-reconciliation-state-' ||
                               (1 + (
                                   SELECT count(*)
                                     FROM reconciliation_items AS prior
                                    WHERE prior.run_id = item.run_id
                                      AND prior.id < item.id
                               )) || '-'
                       AND length(NEW.idempotency_key) =
                           length(
                               'opening-reconciliation-state-' ||
                               (1 + (
                                   SELECT count(*)
                                     FROM reconciliation_items AS prior
                                    WHERE prior.run_id = item.run_id
                                      AND prior.id < item.id
                               )) || '-'
                           ) + 64))
            OR (command.operation = 'approve_opening'
                AND ((NEW.aggregate_type = 'reconciliation_run'
                      AND replace(NEW.aggregate_id, '-', '') = command.run_id
                      AND NEW.from_status = 'differences'
                      AND NEW.to_status = 'approved'
                      AND substr(
                              NEW.idempotency_key,
                              1,
                              length(
                                  'opening-reconciliation-state-' ||
                                  (binding.item_count + 1) || '-'
                              )
                          ) = 'opening-reconciliation-state-' ||
                              (binding.item_count + 1) || '-'
                      AND length(NEW.idempotency_key) =
                          length(
                              'opening-reconciliation-state-' ||
                              (binding.item_count + 1) || '-'
                          ) + 64)
                     OR EXISTS (
                         SELECT 1 FROM reconciliation_items AS item
                          WHERE item.run_id = command.run_id
                            AND NEW.aggregate_type = 'reconciliation_item'
                            AND replace(NEW.aggregate_id, '-', '') = item.id
                            AND NEW.from_status = 'explained'
                            AND NEW.to_status = 'resolved'
                            AND substr(
                                    NEW.idempotency_key,
                                    1,
                                    length(
                                        'opening-reconciliation-state-' ||
                                        (1 + (
                                            SELECT count(*)
                                              FROM reconciliation_items AS prior
                                             WHERE prior.run_id = item.run_id
                                               AND prior.id < item.id
                                        )) || '-'
                                    )
                                ) = 'opening-reconciliation-state-' ||
                                    (1 + (
                                        SELECT count(*)
                                          FROM reconciliation_items AS prior
                                         WHERE prior.run_id = item.run_id
                                           AND prior.id < item.id
                                    )) || '-'
                            AND length(NEW.idempotency_key) =
                                length(
                                    'opening-reconciliation-state-' ||
                                    (1 + (
                                        SELECT count(*)
                                          FROM reconciliation_items AS prior
                                         WHERE prior.run_id = item.run_id
                                           AND prior.id < item.id
                                    )) || '-'
                                ) + 64))))
)
""",
    )
    for index, event in ((19, "UPDATE"), (20, "DELETE")):
        _sqlite_guard_trigger(
            SQLITE_TRIGGER_NAMES[index],
            "state_transition_events",
            event,
            "OLD.reason IN ('reconciliation.opening.create', "
            "'reconciliation.opening.explain', "
            "'reconciliation.opening.approve')",
        )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[21],
        "outbox_events",
        "INSERT",
        """
NEW.event_type IN (
    'reconciliation.opening.create',
    'reconciliation.opening.explain',
    'reconciliation.opening.approve'
) AND NOT EXISTS (
    SELECT 1
      FROM opening_control_reconciliation_runs AS binding
      JOIN reconciliation_commands AS command ON command.run_id = binding.run_id
      LEFT JOIN opening_control_reconciliation_command_consumptions AS seal
        ON seal.command_id = command.id
       AND seal.run_id = command.run_id
       AND seal.operation = command.operation
       AND seal.target_version = command.target_version
     WHERE binding.run_id = replace(NEW.aggregate_id, '-', '')
       AND NEW.aggregate_type = 'reconciliation_run'
       AND command.operation = CASE NEW.event_type
               WHEN 'reconciliation.opening.create' THEN 'create_opening'
               WHEN 'reconciliation.opening.explain' THEN 'explain_opening'
               ELSE 'approve_opening'
           END
       AND seal.command_id IS NULL
       AND replace(
               json_extract(NEW.payload_jsonb, '$.reconciliation_run_id'),
               '-', ''
           ) = command.run_id
       AND json_extract(NEW.payload_jsonb, '$.result_hash') = command.result_hash
       AND (SELECT count(*)
              FROM json_each(json_extract(NEW.payload_jsonb, '$.result'))) = (
                  SELECT count(*) FROM json_each(command.result_jsonb)
              )
       AND NOT EXISTS (
           SELECT 1
             FROM json_each(
                 json_extract(NEW.payload_jsonb, '$.result')
             ) AS actual_result
             LEFT JOIN json_each(command.result_jsonb) AS expected_result
               ON expected_result.key = actual_result.key
            WHERE expected_result.key IS NULL
               OR expected_result.type <> actual_result.type
               OR expected_result.atom IS NOT actual_result.atom
       )
       AND (SELECT count(*) FROM json_each(NEW.payload_jsonb)) = 3
       AND NEW.status = 'pending'
       AND NEW.attempts = 0
       AND NEW.idempotency_key LIKE 'opening-reconciliation-outbox-%'
       AND length(NEW.idempotency_key) =
           length('opening-reconciliation-outbox-') + 64
       AND substr(
               NEW.idempotency_key,
               length('opening-reconciliation-outbox-') + 1
           ) NOT GLOB '*[^0-9a-f]*'
       AND NEW.available_at = command.occurred_at
       AND NEW.created_at = command.occurred_at
       AND NEW.updated_at = command.occurred_at
       AND NEW.locked_at IS NULL
       AND NEW.locked_by IS NULL
       AND NEW.published_at IS NULL
       AND NEW.last_error IS NULL
)
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[22],
        "outbox_events",
        "UPDATE",
        """
OLD.event_type IN (
    'reconciliation.opening.create',
    'reconciliation.opening.explain',
    'reconciliation.opening.approve'
) AND (NEW.id <> OLD.id
       OR NEW.event_type <> OLD.event_type
       OR NEW.aggregate_type <> OLD.aggregate_type
       OR NEW.aggregate_id <> OLD.aggregate_id
       OR json(NEW.payload_jsonb) <> json(OLD.payload_jsonb)
       OR NEW.idempotency_key <> OLD.idempotency_key
       OR NEW.available_at <> OLD.available_at
       OR NEW.created_at <> OLD.created_at)
""",
    )
    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[23],
        "outbox_events",
        "DELETE",
        "OLD.event_type IN ('reconciliation.opening.create', "
        "'reconciliation.opening.explain', "
        "'reconciliation.opening.approve')",
    )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[24],
        "audit_events",
        "INSERT",
        """
NEW.action IN (
    'reconciliation.opening.create',
    'reconciliation.opening.explain',
    'reconciliation.opening.approve'
) AND NOT EXISTS (
    SELECT 1
      FROM opening_control_reconciliation_runs AS binding
      JOIN reconciliation_commands AS command ON command.run_id = binding.run_id
      LEFT JOIN opening_control_reconciliation_command_consumptions AS seal
        ON seal.command_id = command.id
       AND seal.run_id = command.run_id
       AND seal.operation = command.operation
       AND seal.target_version = command.target_version
     WHERE binding.run_id = replace(NEW.aggregate_id, '-', '')
       AND NEW.stream_key = 'inventory'
       AND NEW.aggregate_type = 'reconciliation_run'
       AND command.operation = CASE NEW.action
               WHEN 'reconciliation.opening.create' THEN 'create_opening'
               WHEN 'reconciliation.opening.explain' THEN 'explain_opening'
               ELSE 'approve_opening'
           END
       AND seal.command_id IS NULL
       AND NEW.actor_user_id = command.actor_user_id
       AND NEW.request_id = command.request_reference
       AND NEW.occurred_at = command.occurred_at
       AND (SELECT count(*)
              FROM json_each(json_extract(NEW.after_jsonb, '$.result'))) = (
                  SELECT count(*) FROM json_each(command.result_jsonb)
              )
       AND NOT EXISTS (
           SELECT 1
             FROM json_each(
                 json_extract(NEW.after_jsonb, '$.result')
             ) AS actual_result
             LEFT JOIN json_each(command.result_jsonb) AS expected_result
               ON expected_result.key = actual_result.key
            WHERE expected_result.key IS NULL
               OR expected_result.type <> actual_result.type
               OR expected_result.atom IS NOT actual_result.atom
       )
       AND json_extract(NEW.after_jsonb, '$.result_hash') = command.result_hash
       AND (SELECT count(*) FROM json_each(NEW.after_jsonb)) = 8
)
""",
    )
    for index, event in ((25, "UPDATE"), (26, "DELETE")):
        _sqlite_guard_trigger(
            SQLITE_TRIGGER_NAMES[index],
            "audit_events",
            event,
            "OLD.action IN ('reconciliation.opening.create', "
            "'reconciliation.opening.explain', "
            "'reconciliation.opening.approve')",
        )

    _sqlite_guard_trigger(
        SQLITE_TRIGGER_NAMES[27],
        "stocktake_tasks",
        "UPDATE",
        """
OLD.task_type = 'opening'
AND OLD.status = 'posted'
AND NEW.status = 'closed'
AND (
    (0 = (
        SELECT count(*)
          FROM stocktake_differences AS difference
          JOIN stocktake_rounds AS round_row
            ON round_row.id = difference.round_id
           AND round_row.task_id = difference.task_id
         WHERE difference.task_id = NEW.id
           AND round_row.round_no = NEW.current_round_no
           AND difference.difference_type = 'control_unassigned'
    ) AND EXISTS (
        SELECT 1
          FROM opening_control_reconciliation_runs AS binding
         WHERE binding.task_id = NEW.id
    ))
    OR
    (0 < (
        SELECT count(*)
          FROM stocktake_differences AS difference
          JOIN stocktake_rounds AS round_row
            ON round_row.id = difference.round_id
           AND round_row.task_id = difference.task_id
         WHERE difference.task_id = NEW.id
           AND round_row.round_no = NEW.current_round_no
           AND difference.difference_type = 'control_unassigned'
    ) AND 1 <> (
        SELECT count(*)
          FROM opening_control_reconciliation_runs AS binding
          JOIN reconciliation_runs AS run ON run.id = binding.run_id
          JOIN stocktake_rounds AS round_row
            ON round_row.id = binding.round_id
           AND round_row.task_id = binding.task_id
          JOIN reconciliation_commands AS approval
            ON approval.run_id = binding.run_id
           AND approval.operation = 'approve_opening'
           AND approval.target_version = binding.version
          JOIN opening_control_reconciliation_command_consumptions AS seal
            ON seal.command_id = approval.id
           AND seal.run_id = approval.run_id
           AND seal.operation = approval.operation
           AND seal.target_version = approval.target_version
         WHERE binding.task_id = NEW.id
           AND round_row.round_no = NEW.current_round_no
           AND run.status = 'approved'
           AND binding.approved_at IS NOT NULL
           AND NEW.closed_at IS NOT NULL
           AND NEW.closed_at >= binding.approved_at
    ))
)
""",
    )


def _drop_sqlite_guards() -> None:
    for trigger_name in SQLITE_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _apply_postgresql_runtime_acl(*, current: bool) -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0026 requires the provisioned star_oam_api runtime role';
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
    if current:
        op.execute(
            f"GRANT EXECUTE ON FUNCTION "
            f"public.{PG_CANONICAL_JSON_FUNCTION}(jsonb), "
            f"public.{PG_EVENT_KEY_FUNCTION}(text, text, text), "
            f"{', '.join(PG_LOCK_FUNCTION_SIGNATURES)} "
            f"TO {PRODUCTION_API_ROLE}"
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
