"""Add the empty V1.0 opening-stocktake and establishment foundation.

Revision ID: 20260830_0010
Revises: 20260830_0009
Create Date: 2026-08-30

The two v0.9 stocktake tables are renamed in place.  Their rows remain legacy
prototype evidence and are never copied into the formal stocktake, immutable
inventory ledger, or opening-establishment tables.
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260830_0010"
down_revision: Union[str, Sequence[str], None] = "20260830_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PROVINCIAL_MANAGER_ROLE_ID = uuid.UUID(
    "10000000-0000-4000-8000-000000000002"
)
TECHNICIAN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000003")

PERMISSION_ROWS = (
    (
        uuid.UUID("20000000-0000-4000-8000-000000000017"),
        "stocktake",
        "read",
        "Read formal stocktake evidence within assigned scope",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000018"),
        "stocktake",
        "count",
        "Record formal stocktake counts within assigned scope",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000019"),
        "stocktake",
        "manage",
        "Create and manage formal stocktake tasks",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000020"),
        "stocktake",
        "review_region",
        "Perform the regional opening-stocktake review",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000021"),
        "stocktake",
        "review_headquarters",
        "Perform the NIO headquarters opening-stocktake review",
    ),
    (
        uuid.UUID("20000000-0000-4000-8000-000000000022"),
        "stocktake",
        "post_opening",
        "Finalize a reviewed opening stocktake into the immutable ledger",
    ),
)

ROLE_PERMISSION_ROWS = (
    # Headquarters administrator: no implicit regional-review substitution.
    (uuid.UUID("21000000-0000-4000-8000-000000000032"), ADMIN_ROLE_ID, PERMISSION_ROWS[0][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000033"), ADMIN_ROLE_ID, PERMISSION_ROWS[1][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000034"), ADMIN_ROLE_ID, PERMISSION_ROWS[2][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000035"), ADMIN_ROLE_ID, PERMISSION_ROWS[4][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000036"), ADMIN_ROLE_ID, PERMISSION_ROWS[5][0]),
    # Provincial manager permissions require an explicit scoped assignment.
    (uuid.UUID("21000000-0000-4000-8000-000000000037"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[0][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000038"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[1][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000039"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[2][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000040"), PROVINCIAL_MANAGER_ROLE_ID, PERMISSION_ROWS[3][0]),
    # Technicians only read/count within their person scope.
    (uuid.UUID("21000000-0000-4000-8000-000000000041"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[0][0]),
    (uuid.UUID("21000000-0000-4000-8000-000000000042"), TECHNICIAN_ROLE_ID, PERMISSION_ROWS[1][0]),
)

FORMAL_TABLES = (
    "inventory_opening_establishments",
    "stocktake_posting_items",
    "stocktake_review_items",
    "stocktake_postings",
    "stocktake_reviews",
    "stocktake_differences",
    "stocktake_count_serials",
    "stocktake_count_lines",
    "stocktake_rounds",
    "stocktake_snapshot_lines",
    "inventory_freezes",
    "stocktake_control_snapshot_lines",
    "stocktake_scopes",
    "stocktake_tasks",
)

ALWAYS_IMMUTABLE_TABLES = (
    "stocktake_scopes",
    "stocktake_control_snapshot_lines",
    "stocktake_snapshot_lines",
    "stocktake_differences",
    "stocktake_reviews",
    "stocktake_review_items",
    "stocktake_postings",
    "stocktake_posting_items",
    "inventory_opening_establishments",
)


def upgrade() -> None:
    _prepare_legacy_rename()
    op.rename_table("stocktake_tasks", "legacy_v09_stocktake_tasks")
    op.rename_table("stocktake_items", "legacy_v09_stocktake_items")
    if _dialect_name() == "postgresql":
        op.execute(
            "ALTER TABLE legacy_v09_stocktake_tasks RENAME CONSTRAINT "
            "stocktake_tasks_pkey TO legacy_v09_stocktake_tasks_pkey"
        )
        op.execute(
            "ALTER TABLE legacy_v09_stocktake_items RENAME CONSTRAINT "
            "stocktake_items_pkey TO legacy_v09_stocktake_items_pkey"
        )

    _create_task_and_scope_tables()
    _create_count_evidence_tables()
    _create_review_and_posting_tables()
    _create_opening_establishment_table()
    _seed_permissions()
    _create_immutability_triggers()


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0010 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def _prepare_legacy_rename() -> None:
    """Fail closed unless the source is exactly the unused 0009 namespace."""

    dialect = _dialect_name()
    if dialect == "postgresql":
        # The lock and namespace checks are emitted in offline SQL too, so the
        # deployment artifact retains the same safety boundary as an online run.
        op.execute(
            "LOCK TABLE stocktake_tasks, stocktake_items IN ACCESS EXCLUSIVE MODE"
        )
        op.execute(
            """
DO $$
BEGIN
    IF to_regclass('stocktake_tasks') IS NULL
       OR to_regclass('stocktake_items') IS NULL THEN
        RAISE EXCEPTION 'cannot upgrade 0010: v0.9 stocktake source tables are missing';
    END IF;
    IF to_regclass('legacy_v09_stocktake_tasks') IS NOT NULL
       OR to_regclass('legacy_v09_stocktake_items') IS NOT NULL THEN
        RAISE EXCEPTION 'cannot upgrade 0010: legacy stocktake namespace already exists';
    END IF;
END;
$$
"""
        )
        return

    if context.is_offline_mode():
        raise RuntimeError(
            "0010 SQLite upgrade requires an online connection for namespace checks"
        )
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if not {"stocktake_tasks", "stocktake_items"}.issubset(tables):
        raise RuntimeError(
            "cannot upgrade 0010: v0.9 stocktake source tables are missing"
        )
    if {"legacy_v09_stocktake_tasks", "legacy_v09_stocktake_items"} & tables:
        raise RuntimeError(
            "cannot upgrade 0010: legacy stocktake namespace already exists"
        )


def _create_task_and_scope_tables() -> None:
    op.create_table(
        "stocktake_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_no", sa.String(length=100), nullable=False),
        sa.Column("task_type", sa.String(length=24), nullable=False),
        # Region is the task/batch boundary. Asset owner remains on each scope.
        sa.Column("region_org_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("blind_count", sa.Boolean(), nullable=False),
        sa.Column("cutoff_ledger_cursor", sa.BigInteger(), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("snapshot_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("control_source_system_id", sa.Uuid(), nullable=True),
        sa.Column("control_sync_run_id", sa.Uuid(), nullable=True),
        sa.Column("control_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("control_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("current_round_no", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "task_type IN ('opening', 'full', 'sample', 'ad_hoc', "
            "'personal', 'termination')",
            name="ck_formal_stocktake_tasks_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'issued', 'frozen', 'counting', 'submitted', "
            "'region_review', 'hq_review', 'approved', 'recount_required', "
            "'posted', 'closed', 'cancelled')",
            name="ck_formal_stocktake_tasks_status",
        ),
        sa.CheckConstraint(
            "cutoff_ledger_cursor IS NULL OR cutoff_ledger_cursor >= 0",
            name="ck_formal_stocktake_tasks_cutoff_cursor",
        ),
        sa.CheckConstraint(
            "(cutoff_ledger_cursor IS NULL) = (cutoff_at IS NULL)",
            name="ck_formal_stocktake_tasks_cutoff_pair",
        ),
        sa.CheckConstraint(
            "current_round_no >= 0 AND version >= 0",
            name="ck_formal_stocktake_tasks_versions",
        ),
        sa.CheckConstraint(
            "(scope_manifest_sha256 IS NULL OR "
            "length(scope_manifest_sha256) = 64) AND "
            "(snapshot_manifest_sha256 IS NULL OR "
            "length(snapshot_manifest_sha256) = 64) AND "
            "(control_manifest_sha256 IS NULL OR "
            "length(control_manifest_sha256) = 64)",
            name="ck_formal_stocktake_tasks_hashes",
        ),
        sa.CheckConstraint(
            "task_type <> 'opening' OR status = 'draft' OR "
            "(control_source_system_id IS NOT NULL AND "
            "control_sync_run_id IS NOT NULL AND control_snapshot_at IS NOT NULL "
            "AND control_manifest_sha256 IS NOT NULL)",
            name="ck_formal_stocktake_tasks_opening_control",
        ),
        sa.CheckConstraint(
            "submitted_at IS NULL OR frozen_at IS NOT NULL",
            name="ck_formal_stocktake_tasks_submission_order",
        ),
        sa.CheckConstraint(
            "posted_at IS NULL OR submitted_at IS NOT NULL",
            name="ck_formal_stocktake_tasks_posting_order",
        ),
        sa.CheckConstraint(
            "closed_at IS NULL OR posted_at IS NOT NULL",
            name="ck_formal_stocktake_tasks_close_order",
        ),
        sa.ForeignKeyConstraint(
            ["region_org_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["control_source_system_id"],
            ["source_systems.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_sync_run_id"], ["sync_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_formal_stocktake_tasks"),
        sa.UniqueConstraint("task_no", name="uq_formal_stocktake_tasks_no"),
    )
    op.create_index(
        "uq_formal_stocktake_tasks_active_opening_region",
        "stocktake_tasks",
        ["region_org_id"],
        unique=True,
        postgresql_where=sa.text(
            "task_type = 'opening' AND status NOT IN ('closed', 'cancelled')"
        ),
        sqlite_where=sa.text(
            "task_type = 'opening' AND status NOT IN ('closed', 'cancelled')"
        ),
    )
    op.create_index(
        "ix_formal_stocktake_tasks_region_status",
        "stocktake_tasks",
        ["region_org_id", "status"],
    )
    op.create_index(
        "ix_formal_stocktake_tasks_type_status",
        "stocktake_tasks",
        ["task_type", "status"],
    )

    op.create_table(
        "stocktake_scopes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("scope_no", sa.Integer(), nullable=False),
        sa.Column("scope_mode", sa.String(length=24), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("owner_org_id", sa.Uuid(), nullable=False),
        sa.Column("custodian_person_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("condition_code", sa.String(length=20), nullable=True),
        sa.Column("availability_bucket", sa.String(length=24), nullable=True),
        sa.Column("scope_key", sa.String(length=300), nullable=False),
        sa.Column("scope_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_no > 0", name="ck_formal_stocktake_scopes_number"
        ),
        sa.CheckConstraint(
            "scope_mode IN ('location_all', 'filtered')",
            name="ck_formal_stocktake_scopes_mode",
        ),
        sa.CheckConstraint(
            "(scope_mode = 'location_all' AND material_id IS NULL AND "
            "condition_code IS NULL AND availability_bucket IS NULL) OR "
            "(scope_mode = 'filtered' AND (material_id IS NOT NULL OR "
            "condition_code IS NOT NULL OR availability_bucket IS NOT NULL))",
            name="ck_formal_stocktake_scopes_filter_binding",
        ),
        sa.CheckConstraint(
            "condition_code IS NULL OR condition_code IN "
            "('new', 'used', 'damaged', 'scrapped')",
            name="ck_formal_stocktake_scopes_condition",
        ),
        sa.CheckConstraint(
            "availability_bucket IS NULL OR availability_bucket IN "
            "('available', 'reserved', 'picking', 'outbound', 'in_transit', "
            "'arrived_pending', 'frozen', 'return_pending', 'scrap_pending')",
            name="ck_formal_stocktake_scopes_availability",
        ),
        sa.CheckConstraint(
            "length(scope_sha256) = 64",
            name="ck_formal_stocktake_scopes_hash",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["stocktake_tasks.id"],
            name="fk_formal_stocktake_scopes_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["stock_locations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["owner_org_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["custodian_person_id_snapshot"],
            ["people.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_formal_stocktake_scopes"),
        sa.UniqueConstraint(
            "task_id", "scope_no", name="uq_formal_stocktake_scopes_number"
        ),
        sa.UniqueConstraint(
            "task_id", "scope_key", name="uq_formal_stocktake_scopes_key"
        ),
        sa.UniqueConstraint(
            "task_id", "scope_sha256", name="uq_formal_stocktake_scopes_hash"
        ),
        sa.UniqueConstraint(
            "id", "task_id", name="uq_formal_stocktake_scopes_id_task"
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "scope_key",
            name="uq_formal_stocktake_scopes_freeze_binding",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "owner_org_id",
            "location_id",
            name="uq_formal_stocktake_scopes_establishment",
        ),
    )
    op.create_index(
        "ix_formal_stocktake_scopes_location",
        "stocktake_scopes",
        ["location_id"],
    )
    op.create_index(
        "ix_formal_stocktake_scopes_assignee",
        "stocktake_scopes",
        ["assignee_user_id", "task_id"],
    )

    op.create_table(
        "stocktake_control_snapshot_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("external_business_key", sa.String(length=300), nullable=False),
        sa.Column("external_object_version_id", sa.Uuid(), nullable=True),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("condition_code", sa.String(length=20), nullable=True),
        sa.Column("control_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("mapping_status", sa.String(length=20), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("mapping_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "line_no > 0", name="ck_stocktake_control_snapshot_lines_number"
        ),
        sa.CheckConstraint(
            "control_qty >= 0",
            name="ck_stocktake_control_snapshot_lines_quantity",
        ),
        sa.CheckConstraint(
            "mapping_status IN ('resolved', 'unresolved')",
            name="ck_stocktake_control_snapshot_lines_mapping",
        ),
        sa.CheckConstraint(
            "(mapping_status = 'resolved' AND material_id IS NOT NULL AND "
            "condition_code IS NOT NULL) OR "
            "(mapping_status = 'unresolved' AND length(trim(mapping_note)) > 0)",
            name="ck_stocktake_control_snapshot_lines_resolution",
        ),
        sa.CheckConstraint(
            "condition_code IS NULL OR condition_code IN "
            "('new', 'used', 'damaged', 'scrapped')",
            name="ck_stocktake_control_snapshot_lines_condition",
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_stocktake_control_snapshot_lines_hash",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["stocktake_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["external_object_version_id"],
            ["external_object_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_stocktake_control_snapshot_lines"
        ),
        sa.UniqueConstraint(
            "task_id",
            "line_no",
            name="uq_stocktake_control_snapshot_lines_number",
        ),
        sa.UniqueConstraint(
            "task_id",
            "external_business_key",
            name="uq_stocktake_control_snapshot_lines_business",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            name="uq_stocktake_control_snapshot_lines_id_task",
        ),
    )
    op.create_index(
        "ix_stocktake_control_snapshot_lines_material",
        "stocktake_control_snapshot_lines",
        ["material_id"],
    )

    op.create_table(
        "inventory_freezes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("stocktake_scope_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=300), nullable=False),
        sa.Column("freeze_mode", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("released_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "freeze_mode IN ('hard', 'cutoff_replay')",
            name="ck_inventory_freezes_mode",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'cancelled')",
            name="ck_inventory_freezes_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND valid_to IS NULL) OR "
            "(status IN ('released', 'cancelled') AND valid_to IS NOT NULL)",
            name="ck_inventory_freezes_status_time",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_inventory_freezes_validity",
        ),
        sa.CheckConstraint("version >= 0", name="ck_inventory_freezes_version"),
        sa.ForeignKeyConstraint(
            ["stocktake_scope_id", "task_id", "scope_key"],
            [
                "stocktake_scopes.id",
                "stocktake_scopes.task_id",
                "stocktake_scopes.scope_key",
            ],
            name="fk_inventory_freezes_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["released_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inventory_freezes"),
        sa.UniqueConstraint(
            "task_id", "stocktake_scope_id", name="uq_inventory_freezes_scope"
        ),
    )
    op.create_index(
        "uq_inventory_freezes_active_scope",
        "inventory_freezes",
        ["scope_key"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_inventory_freezes_status_time",
        "inventory_freezes",
        ["status", "valid_from"],
    )

    op.create_table(
        "stocktake_snapshot_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("book_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("account_dimension_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "serial_snapshot_jsonb",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("serial_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("serial_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "book_qty >= 0", name="ck_stocktake_snapshot_lines_quantity"
        ),
        sa.CheckConstraint(
            "ledger_cursor >= 0", name="ck_stocktake_snapshot_lines_cursor"
        ),
        sa.CheckConstraint(
            "serial_count >= 0", name="ck_stocktake_snapshot_lines_serial_count"
        ),
        sa.CheckConstraint(
            "length(account_dimension_sha256) = 64 AND "
            "length(serial_snapshot_sha256) = 64",
            name="ck_stocktake_snapshot_lines_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_snapshot_lines_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stock_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_snapshot_lines"),
        sa.UniqueConstraint(
            "task_id",
            "stock_account_id",
            name="uq_stocktake_snapshot_lines_account",
        ),
    )
    op.create_index(
        "ix_stocktake_snapshot_lines_scope",
        "stocktake_snapshot_lines",
        ["scope_id"],
    )


def _create_count_evidence_tables() -> None:
    op.create_table(
        "stocktake_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("round_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("count_manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "round_no > 0", name="ck_stocktake_rounds_number"
        ),
        sa.CheckConstraint(
            "round_type IN ('initial', 'recount')",
            name="ck_stocktake_rounds_type",
        ),
        sa.CheckConstraint(
            "(round_no = 1 AND round_type = 'initial') OR "
            "(round_no > 1 AND round_type = 'recount')",
            name="ck_stocktake_rounds_type_number",
        ),
        sa.CheckConstraint(
            "status IN ('counting', 'submitted', 'superseded')",
            name="ck_stocktake_rounds_status",
        ),
        sa.CheckConstraint(
            "(status = 'counting' AND submitted_at IS NULL AND "
            "submitted_by_user_id IS NULL AND count_manifest_sha256 IS NULL) OR "
            "(status IN ('submitted', 'superseded') AND submitted_at IS NOT NULL "
            "AND submitted_by_user_id IS NOT NULL AND "
            "count_manifest_sha256 IS NOT NULL)",
            name="ck_stocktake_rounds_submission",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND "
            "(count_manifest_sha256 IS NULL OR "
            "length(count_manifest_sha256) = 64)",
            name="ck_stocktake_rounds_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["stocktake_tasks.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_rounds"),
        sa.UniqueConstraint(
            "task_id", "round_no", name="uq_stocktake_rounds_number"
        ),
        sa.UniqueConstraint(
            "id", "task_id", name="uq_stocktake_rounds_id_task"
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash", name="uq_stocktake_rounds_idempotency"
        ),
    )
    op.create_index(
        "ix_stocktake_rounds_task_status",
        "stocktake_rounds",
        ["task_id", "status"],
    )

    op.create_table(
        "stocktake_count_lines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("counted_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("count_method", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("remark", sa.Text(), nullable=False),
        sa.Column("counted_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "counted_qty >= 0", name="ck_stocktake_count_lines_quantity"
        ),
        sa.CheckConstraint(
            "count_method IN ('scan', 'manual', 'import')",
            name="ck_stocktake_count_lines_method",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_count_lines_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_count_lines_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stock_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["counted_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_count_lines"),
        sa.UniqueConstraint(
            "round_id",
            "stock_account_id",
            name="uq_stocktake_count_lines_account",
        ),
        sa.UniqueConstraint(
            "id", "round_id", name="uq_stocktake_count_lines_id_round"
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_count_lines_id_task_round",
        ),
    )
    op.create_index(
        "ix_stocktake_count_lines_scope",
        "stocktake_count_lines",
        ["scope_id"],
    )
    op.create_index(
        "ix_stocktake_count_lines_counter",
        "stocktake_count_lines",
        ["counted_by_user_id"],
    )

    op.create_table(
        "stocktake_count_serials",
        sa.Column("count_line_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("result", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "result IN ('present', 'missing', 'unexpected', 'wrong_location', "
            "'wrong_condition', 'wrong_lot', 'wrong_serial')",
            name="ck_stocktake_count_serials_result",
        ),
        sa.ForeignKeyConstraint(
            ["count_line_id", "round_id"],
            ["stocktake_count_lines.id", "stocktake_count_lines.round_id"],
            name="fk_stocktake_count_serials_line_round",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["serial_id"], ["inventory_serials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint(
            "count_line_id", "serial_id", name="pk_stocktake_count_serials"
        ),
        sa.UniqueConstraint(
            "round_id", "serial_id", name="uq_stocktake_count_serials_round"
        ),
    )

    op.create_table(
        "stocktake_differences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("control_snapshot_line_id", sa.Uuid(), nullable=True),
        sa.Column("difference_no", sa.Integer(), nullable=False),
        sa.Column("difference_type", sa.String(length=28), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("expected_account_id", sa.Uuid(), nullable=True),
        sa.Column("observed_account_id", sa.Uuid(), nullable=True),
        sa.Column("serial_id", sa.Uuid(), nullable=True),
        sa.Column("book_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("counted_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("difference_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("affected_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("reason_text", sa.Text(), nullable=False),
        sa.Column("evidence_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "difference_no > 0", name="ck_stocktake_differences_number"
        ),
        sa.CheckConstraint(
            "difference_type IN ('missing', 'excess', 'wrong_location', "
            "'wrong_condition', 'wrong_lot', 'wrong_serial', "
            "'control_unassigned')",
            name="ck_stocktake_differences_type",
        ),
        sa.CheckConstraint(
            "book_qty >= 0 AND counted_qty >= 0 AND affected_qty > 0",
            name="ck_stocktake_differences_quantities",
        ),
        sa.CheckConstraint(
            "difference_qty = counted_qty - book_qty",
            name="ck_stocktake_differences_arithmetic",
        ),
        sa.CheckConstraint(
            "(difference_type = 'missing' AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
            "expected_account_id IS NOT NULL AND observed_account_id IS NULL "
            "AND difference_qty < 0) OR "
            "(difference_type = 'excess' AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
            "expected_account_id IS NULL AND observed_account_id IS NOT NULL "
            "AND difference_qty > 0) OR "
            "(difference_type IN ('wrong_location', 'wrong_condition', "
            "'wrong_lot') AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
            "expected_account_id IS NOT NULL AND "
            "observed_account_id IS NOT NULL AND "
            "expected_account_id <> observed_account_id) OR "
            "(difference_type = 'wrong_serial' AND scope_id IS NOT NULL AND "
            "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
            "serial_id IS NOT NULL AND "
            "(expected_account_id IS NOT NULL OR observed_account_id IS NOT NULL)) "
            "OR (difference_type = 'control_unassigned' AND "
            "scope_id IS NULL AND control_snapshot_line_id IS NOT NULL AND "
            "expected_account_id IS NULL AND observed_account_id IS NULL AND "
            "difference_qty <> 0)",
            name="ck_stocktake_differences_binding",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_differences_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_differences_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["control_snapshot_line_id", "task_id"],
            [
                "stocktake_control_snapshot_lines.id",
                "stocktake_control_snapshot_lines.task_id",
            ],
            name="fk_stocktake_differences_control_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["expected_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["observed_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["serial_id"], ["inventory_serials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_differences"),
        sa.UniqueConstraint(
            "task_id",
            "round_id",
            "difference_no",
            name="uq_stocktake_differences_number",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_differences_id_task_round",
        ),
    )
    op.create_index(
        "ix_stocktake_differences_task_type",
        "stocktake_differences",
        ["task_id", "round_id", "difference_type"],
    )
    op.create_index(
        "ix_stocktake_differences_material",
        "stocktake_differences",
        ["material_id"],
    )
    op.create_index(
        "ix_stocktake_differences_serial",
        "stocktake_differences",
        ["serial_id"],
    )
    op.create_index(
        "ix_stocktake_differences_control_line",
        "stocktake_differences",
        ["control_snapshot_line_id"],
    )


def _create_review_and_posting_tables() -> None:
    op.create_table(
        "stocktake_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("review_stage", sa.String(length=20), nullable=False),
        sa.Column("reviewer_user_id", sa.String(length=36), nullable=False),
        sa.Column("reviewer_person_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("decision_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "review_stage IN ('region', 'headquarters')",
            name="ck_stocktake_reviews_stage",
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'recount', 'reject')",
            name="ck_stocktake_reviews_decision",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_reviews_authorization_version",
        ),
        sa.CheckConstraint(
            "length(decision_manifest_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64",
            name="ck_stocktake_reviews_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_reviews_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_reviews"),
        sa.UniqueConstraint(
            "task_id",
            "round_id",
            "review_stage",
            name="uq_stocktake_reviews_stage",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_reviews_id_task_round",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash", name="uq_stocktake_reviews_idempotency"
        ),
    )
    op.create_index(
        "ix_stocktake_reviews_reviewer",
        "stocktake_reviews",
        ["reviewer_user_id", "reviewed_at"],
    )

    op.create_table(
        "stocktake_review_items",
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("difference_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('accept_for_posting', 'pending_verification', "
            "'no_adjustment', 'recount', 'reject')",
            name="ck_stocktake_review_items_decision",
        ),
        sa.ForeignKeyConstraint(
            ["review_id", "task_id", "round_id"],
            [
                "stocktake_reviews.id",
                "stocktake_reviews.task_id",
                "stocktake_reviews.round_id",
            ],
            name="fk_stocktake_review_items_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["difference_id", "task_id", "round_id"],
            [
                "stocktake_differences.id",
                "stocktake_differences.task_id",
                "stocktake_differences.round_id",
            ],
            name="fk_stocktake_review_items_difference",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "review_id", "difference_id", name="pk_stocktake_review_items"
        ),
    )
    op.create_index(
        "ix_stocktake_review_items_difference",
        "stocktake_review_items",
        ["difference_id"],
    )

    op.create_table(
        "stocktake_postings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("posting_kind", sa.String(length=28), nullable=False),
        sa.Column("inventory_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("total_quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("posted_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "posting_kind IN ('opening', 'difference_adjustment')",
            name="ck_stocktake_postings_kind",
        ),
        sa.CheckConstraint(
            "total_quantity >= 0", name="ck_stocktake_postings_quantity"
        ),
        sa.CheckConstraint(
            "(total_quantity = 0 AND inventory_transaction_id IS NULL) OR "
            "(total_quantity > 0 AND inventory_transaction_id IS NOT NULL)",
            name="ck_stocktake_postings_transaction_binding",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(request_hash) = 64",
            name="ck_stocktake_postings_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_postings_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_transaction_id"],
            ["inventory_transactions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["posted_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_postings"),
        sa.UniqueConstraint(
            "task_id",
            "round_id",
            "posting_kind",
            name="uq_stocktake_postings_kind",
        ),
        sa.UniqueConstraint(
            "inventory_transaction_id",
            name="uq_stocktake_postings_transaction",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_postings_id_task_round",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash", name="uq_stocktake_postings_idempotency"
        ),
    )
    op.create_index(
        "ix_stocktake_postings_task",
        "stocktake_postings",
        ["task_id", "posted_at"],
    )

    op.create_table(
        "stocktake_posting_items",
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("inventory_movement_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("count_line_id", sa.Uuid(), nullable=True),
        sa.Column("difference_id", sa.Uuid(), nullable=True),
        sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(count_line_id IS NOT NULL AND difference_id IS NULL) OR "
            "(count_line_id IS NULL AND difference_id IS NOT NULL)",
            name="ck_stocktake_posting_items_source",
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_stocktake_posting_items_quantity"
        ),
        sa.ForeignKeyConstraint(
            ["posting_id", "task_id", "round_id"],
            [
                "stocktake_postings.id",
                "stocktake_postings.task_id",
                "stocktake_postings.round_id",
            ],
            name="fk_stocktake_posting_items_posting",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["count_line_id", "task_id", "round_id"],
            [
                "stocktake_count_lines.id",
                "stocktake_count_lines.task_id",
                "stocktake_count_lines.round_id",
            ],
            name="fk_stocktake_posting_items_count_line",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["difference_id", "task_id", "round_id"],
            [
                "stocktake_differences.id",
                "stocktake_differences.task_id",
                "stocktake_differences.round_id",
            ],
            name="fk_stocktake_posting_items_difference",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_movement_id"],
            ["inventory_movements.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "posting_id",
            "inventory_movement_id",
            name="pk_stocktake_posting_items",
        ),
        sa.UniqueConstraint(
            "inventory_movement_id", name="uq_stocktake_posting_items_movement"
        ),
    )
    op.create_index(
        "ix_stocktake_posting_items_count_line",
        "stocktake_posting_items",
        ["count_line_id"],
    )
    op.create_index(
        "ix_stocktake_posting_items_difference",
        "stocktake_posting_items",
        ["difference_id"],
    )


def _create_opening_establishment_table() -> None:
    op.create_table(
        "inventory_opening_establishments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("owner_org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("posting_id", sa.Uuid(), nullable=False),
        sa.Column("regional_review_id", sa.Uuid(), nullable=False),
        sa.Column("headquarters_review_id", sa.Uuid(), nullable=False),
        sa.Column("cutoff_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("established_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("scope_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("count_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("control_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("has_pending_control_difference", sa.Boolean(), nullable=False),
        sa.Column("established_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("established_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "cutoff_ledger_cursor >= 0 AND "
            "established_ledger_cursor >= cutoff_ledger_cursor",
            name="ck_inventory_opening_establishments_cursor",
        ),
        sa.CheckConstraint(
            "length(scope_manifest_sha256) = 64 AND "
            "length(snapshot_manifest_sha256) = 64 AND "
            "length(count_manifest_sha256) = 64 AND "
            "length(control_manifest_sha256) = 64",
            name="ck_inventory_opening_establishments_hashes",
        ),
        sa.CheckConstraint(
            "established_at >= cutoff_at",
            name="ck_inventory_opening_establishments_time_order",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id", "owner_org_id", "location_id"],
            [
                "stocktake_scopes.id",
                "stocktake_scopes.task_id",
                "stocktake_scopes.owner_org_id",
                "stocktake_scopes.location_id",
            ],
            name="fk_inventory_opening_establishments_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_inventory_opening_establishments_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["posting_id", "task_id", "round_id"],
            [
                "stocktake_postings.id",
                "stocktake_postings.task_id",
                "stocktake_postings.round_id",
            ],
            name="fk_inventory_opening_establishments_posting",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["regional_review_id", "task_id", "round_id"],
            [
                "stocktake_reviews.id",
                "stocktake_reviews.task_id",
                "stocktake_reviews.round_id",
            ],
            name="fk_inventory_opening_establishments_region_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["headquarters_review_id", "task_id", "round_id"],
            [
                "stocktake_reviews.id",
                "stocktake_reviews.task_id",
                "stocktake_reviews.round_id",
            ],
            name="fk_inventory_opening_establishments_hq_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_org_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["stock_locations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["established_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_inventory_opening_establishments"
        ),
        sa.UniqueConstraint(
            "owner_org_id",
            "location_id",
            name="uq_inventory_opening_establishments_scope",
        ),
        sa.UniqueConstraint(
            "task_id",
            "scope_id",
            name="uq_inventory_opening_establishments_task_scope",
        ),
    )
    op.create_index(
        "ix_inventory_opening_establishments_task",
        "inventory_opening_establishments",
        ["task_id", "established_at"],
    )
    op.create_index(
        "ix_inventory_opening_establishments_location",
        "inventory_opening_establishments",
        ["location_id"],
    )


def _seed_permissions() -> None:
    seeded_at = datetime(2026, 8, 30, tzinfo=timezone.utc)
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String(length=100)),
        sa.column("action", sa.String(length=80)),
        sa.column("field_code", sa.String(length=100)),
        sa.column("description", sa.String(length=300)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "id": permission_id,
                "resource": resource,
                "action": action,
                "field_code": "",
                "description": description,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
            for permission_id, resource, action, description in PERMISSION_ROWS
        ],
    )

    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(length=12)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        role_permission_table,
        [
            {
                "id": role_permission_id,
                "role_id": role_id,
                "permission_id": permission_id,
                "effect": "allow",
                "created_at": seeded_at,
            }
            for role_permission_id, role_id, permission_id in ROLE_PERMISSION_ROWS
        ],
    )


def _create_immutability_triggers() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            """
CREATE FUNCTION rsc_block_stocktake_fact_mutation_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'formal stocktake fact rows are immutable';
END;
$$
"""
        )
        for table_name in ALWAYS_IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_0010 "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION "
                "rsc_block_stocktake_fact_mutation_0010()"
            )

        op.execute(
            """
CREATE FUNCTION rsc_block_submitted_stocktake_round_mutation_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('submitted', 'superseded') THEN
        RAISE EXCEPTION 'submitted stocktake rounds are immutable';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(
            "CREATE TRIGGER trg_stocktake_rounds_submitted_immutable_0010 "
            "BEFORE UPDATE OR DELETE ON stocktake_rounds FOR EACH ROW "
            "EXECUTE FUNCTION "
            "rsc_block_submitted_stocktake_round_mutation_0010()"
        )

        op.execute(
            """
CREATE FUNCTION rsc_block_submitted_stocktake_count_mutation_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        SELECT status INTO parent_status
          FROM stocktake_rounds
         WHERE id = OLD.round_id
         FOR UPDATE;
        IF parent_status IS DISTINCT FROM 'counting' THEN
            RAISE EXCEPTION 'submitted stocktake count evidence is immutable';
        END IF;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        parent_status := NULL;
        SELECT status INTO parent_status
          FROM stocktake_rounds
         WHERE id = NEW.round_id
         FOR UPDATE;
        IF parent_status IS DISTINCT FROM 'counting' THEN
            RAISE EXCEPTION 'submitted stocktake count evidence is immutable';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_submitted_immutable_0010 "
                f"BEFORE INSERT OR UPDATE OR DELETE ON {table_name} FOR EACH ROW "
                "EXECUTE FUNCTION "
                "rsc_block_submitted_stocktake_count_mutation_0010()"
            )
        op.execute(
            """
CREATE FUNCTION rsc_validate_stocktake_posting_item_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_kind text;
    parent_transaction uuid;
    movement_transaction uuid;
    movement_from uuid;
    movement_to uuid;
    movement_quantity numeric(18, 3);
    count_account uuid;
    difference_kind text;
BEGIN
    SELECT posting_kind, inventory_transaction_id
      INTO parent_kind, parent_transaction
      FROM stocktake_postings
     WHERE id = NEW.posting_id
       AND task_id = NEW.task_id
       AND round_id = NEW.round_id;
    IF parent_kind IS NULL OR parent_transaction IS NULL THEN
        RAISE EXCEPTION 'stocktake posting item parent is invalid';
    END IF;

    SELECT transaction_id, from_account_id, to_account_id, quantity
      INTO movement_transaction, movement_from, movement_to, movement_quantity
      FROM inventory_movements
     WHERE id = NEW.inventory_movement_id;
    IF movement_transaction IS NULL
       OR movement_transaction IS DISTINCT FROM parent_transaction
       OR movement_quantity IS DISTINCT FROM NEW.quantity THEN
        RAISE EXCEPTION 'stocktake posting item movement is invalid';
    END IF;

    IF NEW.difference_id IS NOT NULL THEN
        SELECT difference_type INTO difference_kind
          FROM stocktake_differences
         WHERE id = NEW.difference_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF difference_kind IS NULL OR difference_kind = 'control_unassigned' THEN
            RAISE EXCEPTION 'control-only difference cannot produce inventory movement';
        END IF;
    END IF;

    IF parent_kind = 'opening' THEN
        IF NEW.count_line_id IS NULL OR NEW.difference_id IS NOT NULL THEN
            RAISE EXCEPTION 'opening posting items require physical count lines';
        END IF;
        SELECT stock_account_id INTO count_account
          FROM stocktake_count_lines
         WHERE id = NEW.count_line_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF count_account IS NULL
           OR movement_from IS NOT NULL
           OR movement_to IS DISTINCT FROM count_account THEN
            RAISE EXCEPTION 'opening movement does not match its physical count line';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(
            "CREATE TRIGGER trg_stocktake_posting_items_validate_insert_0010 "
            "BEFORE INSERT ON stocktake_posting_items FOR EACH ROW "
            "EXECUTE FUNCTION rsc_validate_stocktake_posting_item_0010()"
        )
        op.execute(
            """
CREATE FUNCTION rsc_validate_inventory_freeze_mutation_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'inventory freeze evidence cannot be deleted';
    END IF;
    IF OLD.task_id IS DISTINCT FROM NEW.task_id
       OR OLD.stocktake_scope_id IS DISTINCT FROM NEW.stocktake_scope_id
       OR OLD.scope_key IS DISTINCT FROM NEW.scope_key
       OR OLD.freeze_mode IS DISTINCT FROM NEW.freeze_mode
       OR OLD.valid_from IS DISTINCT FROM NEW.valid_from
       OR OLD.created_by_user_id IS DISTINCT FROM NEW.created_by_user_id
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION 'inventory freeze binding is immutable';
    END IF;
    IF OLD.status <> 'active'
       OR NEW.status NOT IN ('released', 'cancelled')
       OR NEW.valid_to IS NULL
       OR NEW.released_by_user_id IS NULL
       OR length(trim(NEW.release_reason)) = 0
       OR NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'inventory freeze transition is invalid';
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(
            "CREATE TRIGGER trg_inventory_freezes_transition_0010 "
            "BEFORE UPDATE OR DELETE ON inventory_freezes FOR EACH ROW "
            "EXECUTE FUNCTION rsc_validate_inventory_freeze_mutation_0010()"
        )
        op.execute(
            """
CREATE FUNCTION rsc_seal_opening_start_evidence_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Serialize the first round with every scope/snapshot insert for the same
    -- opening task.  A plain EXISTS check is racy under READ COMMITTED.
    PERFORM 1
      FROM stocktake_tasks
     WHERE id = NEW.task_id
     FOR UPDATE;
    IF EXISTS (
        SELECT 1
          FROM stocktake_tasks AS task
         WHERE task.id = NEW.task_id
           AND task.task_type = 'opening'
           AND EXISTS (
               SELECT 1
                 FROM stocktake_rounds AS round_row
                WHERE round_row.task_id = task.id
           )
    ) THEN
        RAISE EXCEPTION 'opening start evidence is sealed after the first round';
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        for table_name in (
            "stocktake_scopes",
            "stocktake_control_snapshot_lines",
            "stocktake_snapshot_lines",
            "inventory_freezes",
        ):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_sealed_insert_0010 "
                f"BEFORE INSERT ON {table_name} FOR EACH ROW "
                "EXECUTE FUNCTION rsc_seal_opening_start_evidence_0010()"
            )
        op.execute(
            """
CREATE FUNCTION rsc_validate_stocktake_evidence_insert_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_round_status text;
    parent_round_submitted_at timestamptz;
    parent_review_stage text;
    parent_review_created_at timestamptz;
    parent_reviewed_at timestamptz;
    regional_reviewed_at timestamptz;
    headquarters_reviewed_at timestamptz;
    parent_posting_kind text;
    parent_posting_total numeric(18, 3);
    parent_posted_at timestamptz;
    parent_task_status text;
    parent_task_posted_at timestamptz;
BEGIN
    -- All post-count evidence for one round shares this row lock.  It closes
    -- difference/review/posting races without taking a global stocktake lock.
    SELECT status, submitted_at
      INTO parent_round_status, parent_round_submitted_at
      FROM stocktake_rounds
     WHERE id = NEW.round_id
       AND task_id = NEW.task_id
     FOR UPDATE;
    IF parent_round_status IS DISTINCT FROM 'submitted'
       OR parent_round_submitted_at IS NULL THEN
        RAISE EXCEPTION 'stocktake evidence requires a submitted round';
    END IF;

    IF TG_TABLE_NAME = 'stocktake_differences' THEN
        IF NEW.created_at < parent_round_submitted_at
           OR EXISTS (
               SELECT 1 FROM stocktake_reviews
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           )
           OR EXISTS (
               SELECT 1 FROM stocktake_postings
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           )
           OR EXISTS (
               SELECT 1 FROM inventory_opening_establishments
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           ) THEN
            RAISE EXCEPTION 'stocktake differences are sealed after review begins';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'stocktake_reviews' THEN
        IF NEW.created_at > NEW.reviewed_at
           OR NEW.reviewed_at < parent_round_submitted_at
           OR EXISTS (
               SELECT 1 FROM stocktake_postings
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           )
           OR EXISTS (
               SELECT 1 FROM inventory_opening_establishments
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           ) THEN
            RAISE EXCEPTION 'stocktake review chronology is invalid or sealed';
        END IF;
        IF NEW.review_stage = 'region' THEN
            IF EXISTS (
                SELECT 1 FROM stocktake_reviews
                 WHERE task_id = NEW.task_id
                   AND round_id = NEW.round_id
                   AND review_stage = 'headquarters'
            ) THEN
                RAISE EXCEPTION 'regional review is sealed by headquarters review';
            END IF;
        ELSE
            SELECT reviewed_at
              INTO regional_reviewed_at
              FROM stocktake_reviews
             WHERE task_id = NEW.task_id
               AND round_id = NEW.round_id
               AND review_stage = 'region'
               AND decision = 'approve';
            IF regional_reviewed_at IS NULL
               OR NEW.reviewed_at <= regional_reviewed_at THEN
                RAISE EXCEPTION 'headquarters review requires an earlier approved regional review';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'stocktake_review_items' THEN
        SELECT review_stage, created_at, reviewed_at
          INTO parent_review_stage, parent_review_created_at, parent_reviewed_at
          FROM stocktake_reviews
         WHERE id = NEW.review_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF parent_review_stage IS NULL
           OR NEW.created_at < parent_round_submitted_at
           OR NEW.created_at < parent_review_created_at
           OR NEW.created_at > parent_reviewed_at
           OR EXISTS (
               SELECT 1 FROM stocktake_postings
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           )
           OR EXISTS (
               SELECT 1 FROM inventory_opening_establishments
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           )
           OR (
               parent_review_stage = 'region'
               AND EXISTS (
                   SELECT 1 FROM stocktake_reviews
                    WHERE task_id = NEW.task_id
                      AND round_id = NEW.round_id
                      AND review_stage = 'headquarters'
               )
           ) THEN
            RAISE EXCEPTION 'stocktake review items are sealed by downstream evidence';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'stocktake_postings' THEN
        IF NEW.created_at > NEW.posted_at
           OR NEW.posted_at < parent_round_submitted_at
           OR EXISTS (
               SELECT 1 FROM inventory_opening_establishments
                WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           ) THEN
            RAISE EXCEPTION 'stocktake posting chronology is invalid or sealed';
        END IF;
        IF NEW.posting_kind = 'opening' THEN
            SELECT reviewed_at
              INTO regional_reviewed_at
              FROM stocktake_reviews
             WHERE task_id = NEW.task_id
               AND round_id = NEW.round_id
               AND review_stage = 'region'
               AND decision = 'approve';
            SELECT reviewed_at
              INTO headquarters_reviewed_at
              FROM stocktake_reviews
             WHERE task_id = NEW.task_id
               AND round_id = NEW.round_id
               AND review_stage = 'headquarters'
               AND decision = 'approve';
            IF regional_reviewed_at IS NULL
               OR headquarters_reviewed_at IS NULL
               OR regional_reviewed_at >= headquarters_reviewed_at
               OR headquarters_reviewed_at > NEW.posted_at THEN
                RAISE EXCEPTION 'opening posting requires ordered approved reviews';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'stocktake_posting_items' THEN
        SELECT posting_kind, total_quantity, posted_at
          INTO parent_posting_kind, parent_posting_total, parent_posted_at
          FROM stocktake_postings
         WHERE id = NEW.posting_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF parent_posting_kind IS NULL
           OR NEW.created_at < parent_round_submitted_at
           OR NEW.created_at > parent_posted_at
           OR EXISTS (
               SELECT 1 FROM inventory_opening_establishments
                WHERE posting_id = NEW.posting_id
                  AND task_id = NEW.task_id
                  AND round_id = NEW.round_id
           ) THEN
            RAISE EXCEPTION 'stocktake posting items are sealed by establishment';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME = 'inventory_opening_establishments' THEN
        SELECT status, posted_at
          INTO parent_task_status, parent_task_posted_at
          FROM stocktake_tasks
         WHERE id = NEW.task_id;
        SELECT posting_kind, total_quantity, posted_at
          INTO parent_posting_kind, parent_posting_total, parent_posted_at
          FROM stocktake_postings
         WHERE id = NEW.posting_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        SELECT reviewed_at
          INTO regional_reviewed_at
          FROM stocktake_reviews
         WHERE id = NEW.regional_review_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id
           AND review_stage = 'region'
           AND decision = 'approve';
        SELECT reviewed_at
          INTO headquarters_reviewed_at
          FROM stocktake_reviews
         WHERE id = NEW.headquarters_review_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id
           AND review_stage = 'headquarters'
           AND decision = 'approve';
        IF parent_task_status IS DISTINCT FROM 'approved'
           OR parent_task_posted_at IS NOT NULL
           OR parent_posting_kind IS DISTINCT FROM 'opening'
           OR regional_reviewed_at IS NULL
           OR headquarters_reviewed_at IS NULL
           OR regional_reviewed_at >= headquarters_reviewed_at
           OR headquarters_reviewed_at > parent_posted_at
           OR NEW.created_at IS DISTINCT FROM NEW.established_at
           OR NEW.established_at < parent_posted_at
           OR (
               parent_posting_total > 0
               AND NOT EXISTS (
                   SELECT 1 FROM stocktake_posting_items
                    WHERE posting_id = NEW.posting_id
               )
           ) THEN
            RAISE EXCEPTION 'opening establishment evidence is incomplete or out of order';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'unsupported stocktake evidence trigger target';
END;
$$
"""
        )
        for table_name in (
            "stocktake_differences",
            "stocktake_reviews",
            "stocktake_review_items",
            "stocktake_postings",
            "stocktake_posting_items",
            "inventory_opening_establishments",
        ):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_chronology_insert_0010 "
                f"BEFORE INSERT ON {table_name} FOR EACH ROW "
                "EXECUTE FUNCTION rsc_validate_stocktake_evidence_insert_0010()"
            )
        op.execute(
            """
CREATE FUNCTION rsc_validate_opening_round_insert_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_task stocktake_tasks%ROWTYPE;
BEGIN
    SELECT * INTO parent_task
      FROM stocktake_tasks
     WHERE id = NEW.task_id
     FOR UPDATE;
    IF parent_task.id IS NULL THEN
        RAISE EXCEPTION 'stocktake round parent task is missing';
    END IF;
    IF parent_task.task_type = 'opening' AND NEW.round_no = 1 THEN
        IF parent_task.status IS DISTINCT FROM 'counting'
           OR parent_task.current_round_no IS DISTINCT FROM 1
           OR parent_task.cutoff_ledger_cursor IS NULL
           OR parent_task.cutoff_at IS NULL
           OR parent_task.scope_manifest_sha256 IS NULL
           OR parent_task.snapshot_manifest_sha256 IS NULL
           OR parent_task.control_source_system_id IS NULL
           OR parent_task.control_sync_run_id IS NULL
           OR parent_task.control_snapshot_at IS NULL
           OR parent_task.control_manifest_sha256 IS NULL
           OR parent_task.issued_at IS DISTINCT FROM parent_task.cutoff_at
           OR parent_task.frozen_at IS DISTINCT FROM parent_task.cutoff_at
           OR NOT EXISTS (
               SELECT 1 FROM stocktake_scopes WHERE task_id = NEW.task_id
           )
           OR EXISTS (
               SELECT 1
                 FROM stocktake_scopes AS scope
                 LEFT JOIN inventory_freezes AS freeze_row
                   ON freeze_row.task_id = scope.task_id
                  AND freeze_row.stocktake_scope_id = scope.id
                  AND freeze_row.scope_key = scope.scope_key
                  AND freeze_row.status = 'active'
                  AND freeze_row.valid_from = parent_task.cutoff_at
                  AND freeze_row.valid_to IS NULL
                WHERE scope.task_id = NEW.task_id
                  AND freeze_row.id IS NULL
           ) THEN
            RAISE EXCEPTION 'opening first round requires complete sealed start facts';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(
            "CREATE TRIGGER trg_stocktake_rounds_opening_insert_0010 "
            "BEFORE INSERT ON stocktake_rounds FOR EACH ROW "
            "EXECUTE FUNCTION rsc_validate_opening_round_insert_0010()"
        )
        op.execute(
            """
CREATE FUNCTION rsc_validate_opening_task_mutation_0010()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_round_id uuid;
    opening_posting_id uuid;
    opening_posted_at timestamptz;
    scope_count bigint;
    establishment_count bigint;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.task_type = 'opening'
           AND EXISTS (SELECT 1 FROM stocktake_rounds WHERE task_id = OLD.id) THEN
            RAISE EXCEPTION 'opening task with evidence cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.task_type <> 'opening' AND NEW.task_type <> 'opening' THEN
        RETURN NEW;
    END IF;

    IF OLD.task_type = 'opening'
       AND EXISTS (SELECT 1 FROM stocktake_rounds WHERE task_id = OLD.id) THEN
        IF OLD.task_type IS DISTINCT FROM NEW.task_type
           OR OLD.region_org_id IS DISTINCT FROM NEW.region_org_id
           OR OLD.blind_count IS DISTINCT FROM NEW.blind_count
           OR OLD.cutoff_ledger_cursor IS DISTINCT FROM NEW.cutoff_ledger_cursor
           OR OLD.cutoff_at IS DISTINCT FROM NEW.cutoff_at
           OR OLD.scope_manifest_sha256 IS DISTINCT FROM NEW.scope_manifest_sha256
           OR OLD.snapshot_manifest_sha256 IS DISTINCT FROM NEW.snapshot_manifest_sha256
           OR OLD.control_source_system_id IS DISTINCT FROM NEW.control_source_system_id
           OR OLD.control_sync_run_id IS DISTINCT FROM NEW.control_sync_run_id
           OR OLD.control_snapshot_at IS DISTINCT FROM NEW.control_snapshot_at
           OR OLD.control_manifest_sha256 IS DISTINCT FROM NEW.control_manifest_sha256
           OR OLD.created_by_user_id IS DISTINCT FROM NEW.created_by_user_id
           OR OLD.issued_at IS DISTINCT FROM NEW.issued_at
           OR OLD.frozen_at IS DISTINCT FROM NEW.frozen_at
           OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
            RAISE EXCEPTION 'opening task sealed start facts are immutable';
        END IF;
    END IF;

    IF OLD.status = 'closed' THEN
        RAISE EXCEPTION 'closed opening task is immutable';
    END IF;
    IF OLD.status = 'posted'
       AND NEW.status NOT IN ('posted', 'closed') THEN
        RAISE EXCEPTION 'posted opening task cannot move backwards';
    END IF;
    IF OLD.posted_at IS NOT NULL
       AND OLD.posted_at IS DISTINCT FROM NEW.posted_at THEN
        RAISE EXCEPTION 'opening task posting timestamp is immutable';
    END IF;
    IF NEW.status NOT IN ('posted', 'closed') AND NEW.posted_at IS NOT NULL THEN
        RAISE EXCEPTION 'opening task posting timestamp requires posted status';
    END IF;

    IF NEW.task_type = 'opening'
       AND NEW.status = 'posted'
       AND OLD.status <> 'posted' THEN
        IF OLD.status <> 'approved' OR NEW.posted_at IS NULL THEN
            RAISE EXCEPTION 'opening task can post only after approval';
        END IF;
        SELECT id INTO current_round_id
          FROM stocktake_rounds
         WHERE task_id = NEW.id
           AND round_no = NEW.current_round_no
           AND status = 'submitted'
         FOR UPDATE;
        SELECT id, posted_at INTO opening_posting_id, opening_posted_at
          FROM stocktake_postings
         WHERE task_id = NEW.id
           AND round_id = current_round_id
           AND posting_kind = 'opening';
        SELECT count(*) INTO scope_count
          FROM stocktake_scopes
         WHERE task_id = NEW.id;
        SELECT count(*) INTO establishment_count
          FROM inventory_opening_establishments
         WHERE task_id = NEW.id;
        IF current_round_id IS NULL
           OR opening_posting_id IS NULL
           OR opening_posted_at IS DISTINCT FROM NEW.posted_at
           OR scope_count = 0
           OR establishment_count <> scope_count
           OR EXISTS (
               SELECT 1
                 FROM stocktake_scopes AS scope
                 LEFT JOIN inventory_opening_establishments AS establishment
                   ON establishment.task_id = scope.task_id
                  AND establishment.scope_id = scope.id
                WHERE scope.task_id = NEW.id
                  AND (
                      establishment.id IS NULL
                      OR establishment.posting_id <> opening_posting_id
                  )
           ) THEN
            RAISE EXCEPTION 'opening task cannot post before every scope is established';
        END IF;
    END IF;

    IF NEW.status = 'closed'
       AND (NEW.posted_at IS NULL OR NEW.closed_at IS NULL
            OR NEW.closed_at < NEW.posted_at) THEN
        RAISE EXCEPTION 'opening task close chronology is invalid';
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        op.execute(
            "CREATE TRIGGER trg_stocktake_tasks_opening_mutation_0010 "
            "BEFORE UPDATE OR DELETE ON stocktake_tasks FOR EACH ROW "
            "EXECUTE FUNCTION rsc_validate_opening_task_mutation_0010()"
        )
        return

    for table_name in ALWAYS_IMMUTABLE_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_"
                f"{operation.lower()}_0010 BEFORE {operation} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, "
                "'formal stocktake fact rows are immutable'); END"
            )
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            "CREATE TRIGGER trg_stocktake_rounds_submitted_immutable_"
            f"{operation.lower()}_0010 BEFORE {operation} ON stocktake_rounds "
            "WHEN OLD.status IN ('submitted', 'superseded') "
            "BEGIN SELECT RAISE(ABORT, "
            "'submitted stocktake rounds are immutable'); END"
        )
        for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
            if operation == "UPDATE":
                status_condition = (
                    "COALESCE((SELECT status FROM stocktake_rounds WHERE id = "
                    "OLD.round_id), 'missing') <> 'counting' OR "
                    "COALESCE((SELECT status FROM stocktake_rounds WHERE id = "
                    "NEW.round_id), 'missing') <> 'counting'"
                )
            else:
                status_condition = (
                    "COALESCE((SELECT status FROM stocktake_rounds WHERE id = "
                    "OLD.round_id), 'missing') <> 'counting'"
                )
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_submitted_immutable_"
                f"{operation.lower()}_0010 BEFORE {operation} ON {table_name} "
                f"WHEN {status_condition} "
                "BEGIN SELECT RAISE(ABORT, "
                "'submitted stocktake count evidence is immutable'); END"
            )
    for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_submitted_immutable_insert_0010 "
            f"BEFORE INSERT ON {table_name} "
            "WHEN COALESCE((SELECT status FROM stocktake_rounds "
            "WHERE id = NEW.round_id), 'missing') <> 'counting' "
            "BEGIN SELECT RAISE(ABORT, "
            "'submitted stocktake count evidence is immutable'); END"
        )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_posting_items_validate_insert_0010
BEFORE INSERT ON stocktake_posting_items
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
          FROM stocktake_postings AS posting
          JOIN inventory_movements AS movement
            ON movement.id = NEW.inventory_movement_id
         WHERE posting.id = NEW.posting_id
           AND posting.task_id = NEW.task_id
           AND posting.round_id = NEW.round_id
           AND posting.inventory_transaction_id IS NOT NULL
           AND movement.transaction_id = posting.inventory_transaction_id
           AND movement.quantity = NEW.quantity
    ) THEN RAISE(ABORT, 'stocktake posting item movement is invalid') END;
    SELECT CASE WHEN NEW.difference_id IS NOT NULL AND NOT EXISTS (
        SELECT 1
          FROM stocktake_differences AS difference
         WHERE difference.id = NEW.difference_id
           AND difference.task_id = NEW.task_id
           AND difference.round_id = NEW.round_id
           AND difference.difference_type <> 'control_unassigned'
    ) THEN RAISE(ABORT,
        'control-only difference cannot produce inventory movement') END;
    SELECT CASE WHEN EXISTS (
        SELECT 1
          FROM stocktake_postings AS posting
         WHERE posting.id = NEW.posting_id
           AND posting.task_id = NEW.task_id
           AND posting.round_id = NEW.round_id
           AND posting.posting_kind = 'opening'
    ) AND (
        NEW.count_line_id IS NULL
        OR NEW.difference_id IS NOT NULL
        OR NOT EXISTS (
            SELECT 1
              FROM stocktake_count_lines AS count_line
              JOIN inventory_movements AS movement
                ON movement.id = NEW.inventory_movement_id
             WHERE count_line.id = NEW.count_line_id
               AND count_line.task_id = NEW.task_id
               AND count_line.round_id = NEW.round_id
               AND movement.from_account_id IS NULL
               AND movement.to_account_id = count_line.stock_account_id
        )
    ) THEN RAISE(ABORT,
        'opening posting items require matching physical count lines') END;
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_inventory_freezes_transition_update_0010
BEFORE UPDATE ON inventory_freezes
BEGIN
    SELECT CASE WHEN
        OLD.task_id <> NEW.task_id
        OR OLD.stocktake_scope_id <> NEW.stocktake_scope_id
        OR OLD.scope_key <> NEW.scope_key
        OR OLD.freeze_mode <> NEW.freeze_mode
        OR OLD.valid_from <> NEW.valid_from
        OR OLD.created_by_user_id <> NEW.created_by_user_id
        OR OLD.created_at <> NEW.created_at
    THEN RAISE(ABORT, 'inventory freeze binding is immutable') END;
    SELECT CASE WHEN
        OLD.status <> 'active'
        OR NEW.status NOT IN ('released', 'cancelled')
        OR NEW.valid_to IS NULL
        OR NEW.released_by_user_id IS NULL
        OR length(trim(NEW.release_reason)) = 0
        OR NEW.version <> OLD.version + 1
    THEN RAISE(ABORT, 'inventory freeze transition is invalid') END;
END
"""
    )
    op.execute(
        "CREATE TRIGGER trg_inventory_freezes_transition_delete_0010 "
        "BEFORE DELETE ON inventory_freezes BEGIN SELECT RAISE(ABORT, "
        "'inventory freeze evidence cannot be deleted'); END"
    )
    for table_name in (
        "stocktake_scopes",
        "stocktake_control_snapshot_lines",
        "stocktake_snapshot_lines",
        "inventory_freezes",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_sealed_insert_0010 "
            f"BEFORE INSERT ON {table_name} "
            "WHEN EXISTS (SELECT 1 FROM stocktake_tasks AS task "
            "WHERE task.id = NEW.task_id AND task.task_type = 'opening' "
            "AND EXISTS (SELECT 1 FROM stocktake_rounds AS round_row "
            "WHERE round_row.task_id = task.id)) "
            "BEGIN SELECT RAISE(ABORT, "
            "'opening start evidence is sealed after the first round'); END"
        )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_differences_chronology_insert_0010
BEFORE INSERT ON stocktake_differences
WHEN COALESCE((
        SELECT status FROM stocktake_rounds
         WHERE id = NEW.round_id AND task_id = NEW.task_id
    ), 'missing') <> 'submitted'
 OR (SELECT submitted_at FROM stocktake_rounds
      WHERE id = NEW.round_id AND task_id = NEW.task_id) IS NULL
 OR NEW.created_at < (SELECT submitted_at FROM stocktake_rounds
                       WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_reviews
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
BEGIN
    SELECT RAISE(ABORT, 'stocktake differences are sealed after review begins');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_reviews_chronology_insert_0010
BEFORE INSERT ON stocktake_reviews
WHEN COALESCE((
        SELECT status FROM stocktake_rounds
         WHERE id = NEW.round_id AND task_id = NEW.task_id
    ), 'missing') <> 'submitted'
 OR (SELECT submitted_at FROM stocktake_rounds
      WHERE id = NEW.round_id AND task_id = NEW.task_id) IS NULL
 OR NEW.created_at > NEW.reviewed_at
 OR NEW.reviewed_at < (SELECT submitted_at FROM stocktake_rounds
                        WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR (
      NEW.review_stage = 'region'
      AND EXISTS (SELECT 1 FROM stocktake_reviews
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                     AND review_stage = 'headquarters')
    )
 OR (
      NEW.review_stage = 'headquarters'
      AND NOT EXISTS (
          SELECT 1 FROM stocktake_reviews
           WHERE task_id = NEW.task_id AND round_id = NEW.round_id
             AND review_stage = 'region' AND decision = 'approve'
             AND reviewed_at < NEW.reviewed_at
      )
    )
BEGIN
    SELECT RAISE(ABORT, 'stocktake review chronology is invalid or sealed');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_review_items_chronology_insert_0010
BEFORE INSERT ON stocktake_review_items
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_reviews AS review
         WHERE review.id = NEW.review_id
           AND review.task_id = NEW.task_id
           AND review.round_id = NEW.round_id
           AND NEW.created_at >= (
               SELECT submitted_at FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id
           )
           AND NEW.created_at >= review.created_at
           AND NEW.created_at <= review.reviewed_at
    )
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (
        SELECT 1 FROM stocktake_reviews AS parent_review
         WHERE parent_review.id = NEW.review_id
           AND parent_review.review_stage = 'region'
           AND EXISTS (
               SELECT 1 FROM stocktake_reviews AS downstream_review
                WHERE downstream_review.task_id = NEW.task_id
                  AND downstream_review.round_id = NEW.round_id
                  AND downstream_review.review_stage = 'headquarters'
           )
    )
BEGIN
    SELECT RAISE(ABORT, 'stocktake review items are sealed by downstream evidence');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_postings_chronology_insert_0010
BEFORE INSERT ON stocktake_postings
WHEN COALESCE((
        SELECT status FROM stocktake_rounds
         WHERE id = NEW.round_id AND task_id = NEW.task_id
    ), 'missing') <> 'submitted'
 OR NEW.created_at > NEW.posted_at
 OR NEW.posted_at < (SELECT submitted_at FROM stocktake_rounds
                      WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR (
      NEW.posting_kind = 'opening'
      AND NOT EXISTS (
          SELECT 1
            FROM stocktake_reviews AS region_review
            JOIN stocktake_reviews AS hq_review
              ON hq_review.task_id = region_review.task_id
             AND hq_review.round_id = region_review.round_id
           WHERE region_review.task_id = NEW.task_id
             AND region_review.round_id = NEW.round_id
             AND region_review.review_stage = 'region'
             AND region_review.decision = 'approve'
             AND hq_review.review_stage = 'headquarters'
             AND hq_review.decision = 'approve'
             AND region_review.reviewed_at < hq_review.reviewed_at
             AND hq_review.reviewed_at <= NEW.posted_at
      )
    )
BEGIN
    SELECT RAISE(ABORT, 'stocktake posting chronology is invalid or sealed');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_posting_items_chronology_insert_0010
BEFORE INSERT ON stocktake_posting_items
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_postings AS posting
         WHERE posting.id = NEW.posting_id
           AND posting.task_id = NEW.task_id
           AND posting.round_id = NEW.round_id
           AND NEW.created_at >= (
               SELECT submitted_at FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id
           )
           AND NEW.created_at <= posting.posted_at
    )
 OR EXISTS (
        SELECT 1 FROM inventory_opening_establishments
         WHERE posting_id = NEW.posting_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id
    )
BEGIN
    SELECT RAISE(ABORT, 'stocktake posting items are sealed by establishment');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_inventory_opening_establishments_chronology_insert_0010
BEFORE INSERT ON inventory_opening_establishments
WHEN NOT EXISTS (
        SELECT 1
          FROM stocktake_tasks AS task
          JOIN stocktake_postings AS posting
            ON posting.id = NEW.posting_id
           AND posting.task_id = NEW.task_id
           AND posting.round_id = NEW.round_id
          JOIN stocktake_reviews AS region_review
            ON region_review.id = NEW.regional_review_id
           AND region_review.task_id = NEW.task_id
           AND region_review.round_id = NEW.round_id
          JOIN stocktake_reviews AS hq_review
            ON hq_review.id = NEW.headquarters_review_id
           AND hq_review.task_id = NEW.task_id
           AND hq_review.round_id = NEW.round_id
         WHERE task.id = NEW.task_id
           AND task.status = 'approved'
           AND task.posted_at IS NULL
           AND posting.posting_kind = 'opening'
           AND region_review.review_stage = 'region'
           AND region_review.decision = 'approve'
           AND hq_review.review_stage = 'headquarters'
           AND hq_review.decision = 'approve'
           AND region_review.reviewed_at < hq_review.reviewed_at
           AND hq_review.reviewed_at <= posting.posted_at
           AND NEW.created_at = NEW.established_at
           AND NEW.established_at >= posting.posted_at
           AND (
               posting.total_quantity = 0
               OR EXISTS (
                   SELECT 1 FROM stocktake_posting_items AS posting_item
                    WHERE posting_item.posting_id = posting.id
               )
           )
    )
BEGIN
    SELECT RAISE(ABORT, 'opening establishment evidence is incomplete or out of order');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_rounds_opening_insert_0010
BEFORE INSERT ON stocktake_rounds
WHEN EXISTS (
        SELECT 1 FROM stocktake_tasks AS task
         WHERE task.id = NEW.task_id
           AND task.task_type = 'opening'
           AND NEW.round_no = 1
           AND (
               task.status <> 'counting'
               OR task.current_round_no <> 1
               OR task.cutoff_ledger_cursor IS NULL
               OR task.cutoff_at IS NULL
               OR task.scope_manifest_sha256 IS NULL
               OR task.snapshot_manifest_sha256 IS NULL
               OR task.control_source_system_id IS NULL
               OR task.control_sync_run_id IS NULL
               OR task.control_snapshot_at IS NULL
               OR task.control_manifest_sha256 IS NULL
               OR task.issued_at IS NOT task.cutoff_at
               OR task.frozen_at IS NOT task.cutoff_at
               OR NOT EXISTS (
                   SELECT 1 FROM stocktake_scopes
                    WHERE task_id = NEW.task_id
               )
               OR EXISTS (
                   SELECT 1
                     FROM stocktake_scopes AS scope
                     LEFT JOIN inventory_freezes AS freeze_row
                       ON freeze_row.task_id = scope.task_id
                      AND freeze_row.stocktake_scope_id = scope.id
                      AND freeze_row.scope_key = scope.scope_key
                      AND freeze_row.status = 'active'
                      AND freeze_row.valid_from = task.cutoff_at
                      AND freeze_row.valid_to IS NULL
                    WHERE scope.task_id = NEW.task_id
                      AND freeze_row.id IS NULL
               )
           )
    )
BEGIN
    SELECT RAISE(ABORT, 'opening first round requires complete sealed start facts');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_tasks_opening_sealed_update_0010
BEFORE UPDATE ON stocktake_tasks
WHEN OLD.task_type = 'opening'
 AND EXISTS (SELECT 1 FROM stocktake_rounds WHERE task_id = OLD.id)
 AND (
      OLD.task_type IS NOT NEW.task_type
      OR OLD.region_org_id IS NOT NEW.region_org_id
      OR OLD.blind_count IS NOT NEW.blind_count
      OR OLD.cutoff_ledger_cursor IS NOT NEW.cutoff_ledger_cursor
      OR OLD.cutoff_at IS NOT NEW.cutoff_at
      OR OLD.scope_manifest_sha256 IS NOT NEW.scope_manifest_sha256
      OR OLD.snapshot_manifest_sha256 IS NOT NEW.snapshot_manifest_sha256
      OR OLD.control_source_system_id IS NOT NEW.control_source_system_id
      OR OLD.control_sync_run_id IS NOT NEW.control_sync_run_id
      OR OLD.control_snapshot_at IS NOT NEW.control_snapshot_at
      OR OLD.control_manifest_sha256 IS NOT NEW.control_manifest_sha256
      OR OLD.created_by_user_id IS NOT NEW.created_by_user_id
      OR OLD.issued_at IS NOT NEW.issued_at
      OR OLD.frozen_at IS NOT NEW.frozen_at
      OR OLD.created_at IS NOT NEW.created_at
 )
BEGIN
    SELECT RAISE(ABORT, 'opening task sealed start facts are immutable');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_tasks_opening_terminal_update_0010
BEFORE UPDATE ON stocktake_tasks
WHEN OLD.task_type = 'opening'
 AND (
      OLD.status = 'closed'
      OR (OLD.status = 'posted' AND NEW.status NOT IN ('posted', 'closed'))
      OR (OLD.posted_at IS NOT NULL AND OLD.posted_at IS NOT NEW.posted_at)
      OR (NEW.status NOT IN ('posted', 'closed') AND NEW.posted_at IS NOT NULL)
      OR (
          NEW.status = 'closed'
          AND (
              NEW.posted_at IS NULL OR NEW.closed_at IS NULL
              OR NEW.closed_at < NEW.posted_at
          )
      )
 )
BEGIN
    SELECT RAISE(ABORT, 'opening task terminal transition is invalid');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_tasks_opening_post_update_0010
BEFORE UPDATE ON stocktake_tasks
WHEN NEW.task_type = 'opening'
 AND NEW.status = 'posted'
 AND OLD.status <> 'posted'
 AND (
      OLD.status <> 'approved'
      OR NEW.posted_at IS NULL
      OR NOT EXISTS (
          SELECT 1
            FROM stocktake_rounds AS round_row
            JOIN stocktake_postings AS posting
              ON posting.task_id = NEW.id
             AND posting.round_id = round_row.id
             AND posting.posting_kind = 'opening'
             AND posting.posted_at = NEW.posted_at
           WHERE round_row.task_id = NEW.id
             AND round_row.round_no = NEW.current_round_no
             AND round_row.status = 'submitted'
      )
      OR (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id) = 0
      OR (SELECT count(*) FROM inventory_opening_establishments
           WHERE task_id = NEW.id)
         <> (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.id)
      OR EXISTS (
          SELECT 1
            FROM stocktake_scopes AS scope
            LEFT JOIN inventory_opening_establishments AS establishment
              ON establishment.task_id = scope.task_id
             AND establishment.scope_id = scope.id
            LEFT JOIN stocktake_postings AS posting
              ON posting.id = establishment.posting_id
             AND posting.task_id = NEW.id
             AND posting.round_id = (
                 SELECT id FROM stocktake_rounds
                  WHERE task_id = NEW.id
                    AND round_no = NEW.current_round_no
                    AND status = 'submitted'
             )
             AND posting.posting_kind = 'opening'
           WHERE scope.task_id = NEW.id
             AND posting.id IS NULL
      )
 )
BEGIN
    SELECT RAISE(ABORT, 'opening task cannot post before every scope is established');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_tasks_opening_evidence_delete_0010
BEFORE DELETE ON stocktake_tasks
WHEN OLD.task_type = 'opening'
 AND EXISTS (SELECT 1 FROM stocktake_rounds WHERE task_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'opening task with evidence cannot be deleted');
END
"""
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0010 downgrade requires an online connection for fail-closed data checks"
        )

    bind = op.get_bind()
    dialect = _dialect_name()
    if dialect == "postgresql":
        lock_tables = ", ".join(
            (
                *reversed(FORMAL_TABLES),
                "legacy_v09_stocktake_tasks",
                "legacy_v09_stocktake_items",
                "permissions",
                "role_permissions",
                "inventory_transactions",
                "document_attachments",
                "audit_events",
                "state_transition_events",
                "outbox_events",
                "notification_events",
            )
        )
        bind.exec_driver_sql(
            f"LOCK TABLE {lock_tables} IN ACCESS EXCLUSIVE MODE"
        )

    for table_name in reversed(FORMAL_TABLES):
        table = sa.table(table_name, sa.column("_sentinel"))
        if bind.execute(
            sa.select(sa.literal(1)).select_from(table).limit(1)
        ).first() is not None:
            raise RuntimeError(
                f"cannot downgrade 0010: formal stocktake table {table_name} "
                "contains business data"
            )

    inventory_transaction_table = sa.table(
        "inventory_transactions",
        sa.column("movement_type", sa.String(length=32)),
        sa.column("source_document_type", sa.String(length=80)),
    )
    if bind.execute(
        sa.select(sa.literal(1))
        .select_from(inventory_transaction_table)
        .where(
            sa.or_(
                inventory_transaction_table.c.movement_type == "opening",
                inventory_transaction_table.c.source_document_type.in_(
                    (
                        "opening_stocktake",
                        "opening_stocktake_batch",
                        "inventory_opening_establishment",
                    )
                ),
            )
        )
        .limit(1)
    ).first() is not None:
        raise RuntimeError(
            "cannot downgrade 0010: opening-stocktake inventory posting exists"
        )

    reference_columns = (
        ("document_attachments", "document_type"),
        ("audit_events", "aggregate_type"),
        ("state_transition_events", "aggregate_type"),
        ("outbox_events", "aggregate_type"),
        ("notification_events", "business_type"),
    )
    explicit_reference_types = (
        "opening_stocktake",
        "stocktake_task",
        "stocktake_scope",
        "stocktake_round",
        "stocktake_difference",
        "stocktake_review",
        "stocktake_posting",
        "inventory_opening_establishment",
    )
    for table_name, column_name in reference_columns:
        column = sa.column(column_name, sa.String(length=80))
        table = sa.table(table_name, column)
        if bind.execute(
            sa.select(sa.literal(1))
            .select_from(table)
            .where(
                sa.or_(
                    sa.func.lower(column).like("stocktake%"),
                    sa.func.lower(column).in_(explicit_reference_types),
                )
            )
            .limit(1)
        ).first() is not None:
            raise RuntimeError(
                "cannot downgrade 0010: generic evidence references formal "
                f"stocktake data in {table_name}"
            )

    _verify_permission_seeds_are_unused(bind)
    _drop_immutability_triggers()

    role_permission_table = sa.table(
        "role_permissions", sa.column("id", sa.Uuid())
    )
    op.execute(
        role_permission_table.delete().where(
            role_permission_table.c.id.in_(
                tuple(row[0] for row in ROLE_PERMISSION_ROWS)
            )
        )
    )
    permission_table = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(
        permission_table.delete().where(
            permission_table.c.id.in_(tuple(row[0] for row in PERMISSION_ROWS))
        )
    )

    for table_name in FORMAL_TABLES:
        op.drop_table(table_name)

    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE legacy_v09_stocktake_tasks RENAME CONSTRAINT "
            "legacy_v09_stocktake_tasks_pkey TO stocktake_tasks_pkey"
        )
        op.execute(
            "ALTER TABLE legacy_v09_stocktake_items RENAME CONSTRAINT "
            "legacy_v09_stocktake_items_pkey TO stocktake_items_pkey"
        )
    op.rename_table("legacy_v09_stocktake_items", "stocktake_items")
    op.rename_table("legacy_v09_stocktake_tasks", "stocktake_tasks")


def _verify_permission_seeds_are_unused(bind: sa.Connection) -> None:
    permission_ids = tuple(row[0] for row in PERMISSION_ROWS)
    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String(length=100)),
        sa.column("action", sa.String(length=80)),
        sa.column("field_code", sa.String(length=100)),
        sa.column("description", sa.String(length=300)),
    )
    rows = bind.execute(
        sa.select(
            permission_table.c.id,
            permission_table.c.resource,
            permission_table.c.action,
            permission_table.c.field_code,
            permission_table.c.description,
        ).where(
            sa.or_(
                permission_table.c.id.in_(permission_ids),
                permission_table.c.resource == "stocktake",
            )
        )
    ).all()
    expected_permissions = {
        (permission_id, resource, action, "", description)
        for permission_id, resource, action, description in PERMISSION_ROWS
    }
    if set(rows) != expected_permissions:
        raise RuntimeError(
            "cannot downgrade 0010: stocktake permission seeds were changed"
        )

    role_permission_ids = tuple(row[0] for row in ROLE_PERMISSION_ROWS)
    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(length=12)),
    )
    role_rows = bind.execute(
        sa.select(
            role_permission_table.c.id,
            role_permission_table.c.role_id,
            role_permission_table.c.permission_id,
            role_permission_table.c.effect,
        ).where(
            sa.or_(
                role_permission_table.c.id.in_(role_permission_ids),
                role_permission_table.c.permission_id.in_(permission_ids),
            )
        )
    ).all()
    expected_role_permissions = {
        (role_permission_id, role_id, permission_id, "allow")
        for role_permission_id, role_id, permission_id in ROLE_PERMISSION_ROWS
    }
    if set(role_rows) != expected_role_permissions:
        raise RuntimeError(
            "cannot downgrade 0010: stocktake role-permission seeds were changed"
        )


def _drop_immutability_triggers() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        for table_name in ALWAYS_IMMUTABLE_TABLES:
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_0010 ON {table_name}"
            )
        op.execute(
            "DROP TRIGGER trg_stocktake_rounds_submitted_immutable_0010 "
            "ON stocktake_rounds"
        )
        for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_submitted_immutable_0010 "
                f"ON {table_name}"
            )
        op.execute(
            "DROP TRIGGER trg_stocktake_posting_items_validate_insert_0010 "
            "ON stocktake_posting_items"
        )
        op.execute(
            "DROP TRIGGER trg_inventory_freezes_transition_0010 "
            "ON inventory_freezes"
        )
        for table_name in (
            "stocktake_scopes",
            "stocktake_control_snapshot_lines",
            "stocktake_snapshot_lines",
            "inventory_freezes",
        ):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_sealed_insert_0010 ON {table_name}"
            )
        for table_name in (
            "stocktake_differences",
            "stocktake_reviews",
            "stocktake_review_items",
            "stocktake_postings",
            "stocktake_posting_items",
            "inventory_opening_establishments",
        ):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_chronology_insert_0010 "
                f"ON {table_name}"
            )
        op.execute(
            "DROP TRIGGER trg_stocktake_rounds_opening_insert_0010 "
            "ON stocktake_rounds"
        )
        op.execute(
            "DROP TRIGGER trg_stocktake_tasks_opening_mutation_0010 "
            "ON stocktake_tasks"
        )
        op.execute("DROP FUNCTION rsc_validate_opening_task_mutation_0010()")
        op.execute("DROP FUNCTION rsc_validate_opening_round_insert_0010()")
        op.execute("DROP FUNCTION rsc_validate_stocktake_evidence_insert_0010()")
        op.execute("DROP FUNCTION rsc_seal_opening_start_evidence_0010()")
        op.execute("DROP FUNCTION rsc_validate_inventory_freeze_mutation_0010()")
        op.execute("DROP FUNCTION rsc_validate_stocktake_posting_item_0010()")
        op.execute(
            "DROP FUNCTION rsc_block_submitted_stocktake_count_mutation_0010()"
        )
        op.execute(
            "DROP FUNCTION rsc_block_submitted_stocktake_round_mutation_0010()"
        )
        op.execute("DROP FUNCTION rsc_block_stocktake_fact_mutation_0010()")
        return

    for table_name in ALWAYS_IMMUTABLE_TABLES:
        for operation in ("update", "delete"):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_{operation}_0010"
            )
    for operation in ("update", "delete"):
        op.execute(
            "DROP TRIGGER trg_stocktake_rounds_submitted_immutable_"
            f"{operation}_0010"
        )
        for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_submitted_immutable_"
                f"{operation}_0010"
            )
    for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
        op.execute(
            f"DROP TRIGGER trg_{table_name}_submitted_immutable_insert_0010"
        )
    op.execute("DROP TRIGGER trg_stocktake_posting_items_validate_insert_0010")
    op.execute("DROP TRIGGER trg_inventory_freezes_transition_update_0010")
    op.execute("DROP TRIGGER trg_inventory_freezes_transition_delete_0010")
    for table_name in (
        "stocktake_scopes",
        "stocktake_control_snapshot_lines",
        "stocktake_snapshot_lines",
        "inventory_freezes",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_sealed_insert_0010")
    for trigger_name in (
        "trg_stocktake_differences_chronology_insert_0010",
        "trg_stocktake_reviews_chronology_insert_0010",
        "trg_stocktake_review_items_chronology_insert_0010",
        "trg_stocktake_postings_chronology_insert_0010",
        "trg_stocktake_posting_items_chronology_insert_0010",
        "trg_inventory_opening_establishments_chronology_insert_0010",
    ):
        op.execute(f"DROP TRIGGER {trigger_name}")
    for trigger_name in (
        "trg_stocktake_rounds_opening_insert_0010",
        "trg_stocktake_tasks_opening_sealed_update_0010",
        "trg_stocktake_tasks_opening_terminal_update_0010",
        "trg_stocktake_tasks_opening_post_update_0010",
        "trg_stocktake_tasks_opening_evidence_delete_0010",
    ):
        op.execute(f"DROP TRIGGER {trigger_name}")
