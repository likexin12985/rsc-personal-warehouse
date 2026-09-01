"""Add immutable opening-count observations and submission manifests.

Revision ID: 20260830_0011
Revises: 20260830_0010
Create Date: 2026-08-30

The observation table records physical dimensions for which no exact stock
account existed at the opening cutoff.  It never creates an account, balance,
movement, lot, serial, or personal-stock fact and never interprets an OAM
control quantity as local stock.  Scope completion and round submission rows
are post-child immutable seals.
"""

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260830_0011"
down_revision: Union[str, Sequence[str], None] = "20260830_0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_TABLES = (
    "stocktake_round_submissions",
    "stocktake_scope_count_completions",
    "stocktake_count_observations",
)


DIFFERENCE_BINDING_CHECK = (
    "(difference_type = 'missing' AND scope_id IS NOT NULL AND "
    "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
    "expected_account_id IS NOT NULL AND observed_account_id IS NULL AND "
    "observed_line_id IS NULL AND difference_qty < 0) OR "
    "(difference_type = 'excess' AND scope_id IS NOT NULL AND "
    "control_snapshot_line_id IS NULL AND expected_account_id IS NULL AND "
    "((observed_account_id IS NOT NULL AND observed_line_id IS NULL AND "
    "material_id IS NOT NULL) OR (observed_account_id IS NULL AND "
    "observed_line_id IS NOT NULL)) AND difference_qty > 0) OR "
    "(difference_type IN ('wrong_location', 'wrong_condition', 'wrong_lot') "
    "AND scope_id IS NOT NULL AND control_snapshot_line_id IS NULL AND "
    "expected_account_id IS NOT NULL AND ((observed_account_id IS NOT NULL "
    "AND observed_line_id IS NULL AND material_id IS NOT NULL AND "
    "expected_account_id <> observed_account_id) OR (observed_account_id IS "
    "NULL AND observed_line_id IS NOT NULL))) OR "
    "(difference_type = 'wrong_serial' AND scope_id IS NOT NULL AND "
    "control_snapshot_line_id IS NULL AND ((observed_line_id IS NULL AND "
    "material_id IS NOT NULL AND serial_id IS NOT NULL AND "
    "(expected_account_id IS NOT NULL OR observed_account_id IS NOT NULL)) OR "
    "(observed_line_id IS NOT NULL AND observed_account_id IS NULL))) OR "
    "(difference_type = 'control_unassigned' AND scope_id IS NULL AND "
    "control_snapshot_line_id IS NOT NULL AND expected_account_id IS NULL AND "
    "observed_account_id IS NULL AND observed_line_id IS NULL AND "
    "difference_qty <> 0)"
)


ORIGINAL_DIFFERENCE_BINDING_CHECK = (
    "(difference_type = 'missing' AND scope_id IS NOT NULL AND "
    "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
    "expected_account_id IS NOT NULL AND observed_account_id IS NULL AND "
    "difference_qty < 0) OR "
    "(difference_type = 'excess' AND scope_id IS NOT NULL AND "
    "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
    "expected_account_id IS NULL AND observed_account_id IS NOT NULL AND "
    "difference_qty > 0) OR "
    "(difference_type IN ('wrong_location', 'wrong_condition', 'wrong_lot') "
    "AND scope_id IS NOT NULL AND control_snapshot_line_id IS NULL AND "
    "material_id IS NOT NULL AND expected_account_id IS NOT NULL AND "
    "observed_account_id IS NOT NULL AND expected_account_id <> "
    "observed_account_id) OR "
    "(difference_type = 'wrong_serial' AND scope_id IS NOT NULL AND "
    "control_snapshot_line_id IS NULL AND material_id IS NOT NULL AND "
    "serial_id IS NOT NULL AND (expected_account_id IS NOT NULL OR "
    "observed_account_id IS NOT NULL)) OR "
    "(difference_type = 'control_unassigned' AND scope_id IS NULL AND "
    "control_snapshot_line_id IS NOT NULL AND expected_account_id IS NULL AND "
    "observed_account_id IS NULL AND difference_qty <> 0)"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0011 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite" and context.is_offline_mode():
        raise RuntimeError("0011 SQLite upgrade requires an online connection")
    _preflight_existing_rounds()
    _create_observation_tables()
    _extend_stocktake_differences()
    _create_count_contract_triggers()


def _preflight_existing_rounds() -> None:
    """Never invent 0011 submission manifests for already sealed rounds."""

    if _dialect_name() == "postgresql":
        op.execute("LOCK TABLE stocktake_rounds IN SHARE ROW EXCLUSIVE MODE")
        op.execute(
            """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM stocktake_rounds
         WHERE status IN ('submitted', 'superseded')
    ) THEN
        RAISE EXCEPTION 'cannot upgrade 0011: a round was submitted without an immutable 0011 submission manifest';
    END IF;
END;
$$
"""
        )
        return
    sealed_round = op.get_bind().execute(
        sa.text(
            "SELECT 1 FROM stocktake_rounds "
            "WHERE status IN ('submitted', 'superseded') LIMIT 1"
        )
    ).first()
    if sealed_round is not None:
        raise RuntimeError(
            "cannot upgrade 0011: a round was submitted without an immutable "
            "0011 submission manifest"
        )


def _create_observation_tables() -> None:
    op.create_table(
        "stocktake_count_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("observation_no", sa.Integer(), nullable=False),
        sa.Column("owner_org_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("custodian_person_id_snapshot", sa.Uuid(), nullable=True),
        sa.Column("material_id", sa.Uuid(), nullable=True),
        sa.Column("material_identifier_raw", sa.String(length=300), nullable=False),
        sa.Column("material_identifier_type", sa.String(length=24), nullable=False),
        sa.Column("condition_code", sa.String(length=20), nullable=False),
        sa.Column("availability_bucket", sa.String(length=24), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("lot_no_raw", sa.String(length=160), nullable=True),
        sa.Column("serial_id", sa.Uuid(), nullable=True),
        sa.Column("serial_no_raw", sa.String(length=200), nullable=True),
        sa.Column("serial_identifier_type", sa.String(length=24), nullable=True),
        sa.Column("counted_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("verification_status", sa.String(length=24), nullable=False),
        sa.Column("count_method", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("remark", sa.Text(), nullable=False),
        sa.Column("counted_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dimension_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "observation_no > 0",
            name="ck_stocktake_count_observations_number",
        ),
        sa.CheckConstraint(
            "counted_qty > 0",
            name="ck_stocktake_count_observations_quantity",
        ),
        sa.CheckConstraint(
            "condition_code IN ('new', 'used', 'damaged', 'scrapped')",
            name="ck_stocktake_count_observations_condition",
        ),
        sa.CheckConstraint(
            "availability_bucket IN ('available', 'reserved', 'picking', "
            "'outbound', 'in_transit', 'arrived_pending', 'frozen', "
            "'return_pending', 'scrap_pending')",
            name="ck_stocktake_count_observations_availability",
        ),
        sa.CheckConstraint(
            "count_method IN ('scan', 'manual', 'import')",
            name="ck_stocktake_count_observations_method",
        ),
        sa.CheckConstraint(
            "material_identifier_type IN "
            "('sku_code', 'qr_code', 'external_code', 'unknown') AND "
            "((serial_no_raw IS NULL AND serial_identifier_type IS NULL) OR "
            "(serial_no_raw IS NOT NULL AND serial_identifier_type IN "
            "('serial_no', 'qr_code', 'unknown')))",
            name="ck_stocktake_count_observations_identifier_types",
        ),
        sa.CheckConstraint(
            "verification_status IN ('verified', 'pending_verification')",
            name="ck_stocktake_count_observations_verification_status",
        ),
        sa.CheckConstraint(
            "length(trim(material_identifier_raw)) > 0 AND "
            "(lot_no_raw IS NULL OR length(trim(lot_no_raw)) > 0) AND "
            "(serial_no_raw IS NULL OR length(trim(serial_no_raw)) > 0)",
            name="ck_stocktake_count_observations_raw_identifiers",
        ),
        sa.CheckConstraint(
            "(verification_status = 'verified' AND material_id IS NOT NULL "
            "AND (lot_no_raw IS NULL OR lot_id IS NOT NULL) "
            "AND (serial_no_raw IS NULL OR serial_id IS NOT NULL)) OR "
            "(verification_status = 'pending_verification' AND "
            "(material_id IS NULL OR (lot_no_raw IS NOT NULL AND lot_id IS NULL) "
            "OR (serial_no_raw IS NOT NULL AND serial_id IS NULL)))",
            name="ck_stocktake_count_observations_verification_binding",
        ),
        sa.CheckConstraint(
            "(lot_id IS NULL OR (material_id IS NOT NULL AND lot_no_raw IS NOT NULL)) "
            "AND (serial_id IS NULL OR (material_id IS NOT NULL AND "
            "serial_no_raw IS NOT NULL)) AND "
            "(serial_no_raw IS NULL OR counted_qty = 1)",
            name="ck_stocktake_count_observations_tracking_binding",
        ),
        sa.CheckConstraint(
            "length(dimension_sha256) = 64 AND length(request_sha256) = 64 "
            "AND length(idempotency_key_hash) = 64",
            name="ck_stocktake_count_observations_hashes",
        ),
        sa.CheckConstraint(
            "created_at = counted_at",
            name="ck_stocktake_count_observations_chronology",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_count_observations_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_count_observations_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_org_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["stock_locations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["custodian_person_id_snapshot"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["inventory_lots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["serial_id"], ["inventory_serials.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["counted_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_count_observations"),
        sa.UniqueConstraint(
            "round_id",
            "observation_no",
            name="uq_stocktake_count_observations_number",
        ),
        sa.UniqueConstraint(
            "round_id",
            "dimension_sha256",
            name="uq_stocktake_count_observations_dimension",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_count_observations_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_count_observations_id_task_round",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            "scope_id",
            name="uq_stocktake_count_observations_difference_binding",
        ),
    )
    op.create_index(
        "ix_stocktake_count_observations_scope",
        "stocktake_count_observations",
        ["scope_id", "round_id"],
    )
    op.create_index(
        "ix_stocktake_count_observations_material",
        "stocktake_count_observations",
        ["material_id", "verification_status"],
    )
    op.create_index(
        "ix_stocktake_count_observations_serial_raw",
        "stocktake_count_observations",
        ["serial_no_raw"],
    )
    op.create_index(
        "uq_stocktake_count_observations_round_serial_raw",
        "stocktake_count_observations",
        ["round_id", "serial_identifier_type", "serial_no_raw"],
        unique=True,
        postgresql_where=sa.text("serial_no_raw IS NOT NULL"),
        sqlite_where=sa.text("serial_no_raw IS NOT NULL"),
    )

    op.create_table(
        "stocktake_scope_count_completions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("count_line_count", sa.Integer(), nullable=False),
        sa.Column("observation_line_count", sa.Integer(), nullable=False),
        sa.Column("serial_count", sa.Integer(), nullable=False),
        sa.Column("total_counted_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("zero_confirmed", sa.Boolean(), nullable=False),
        sa.Column("evidence_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("completed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("completed_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("completed_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(length=80), nullable=False),
        sa.Column("authorization_sha256", sa.String(length=64), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "count_line_count >= 0 AND observation_line_count >= 0 AND "
            "serial_count >= 0 AND total_counted_qty >= 0",
            name="ck_stocktake_scope_count_completions_totals",
        ),
        sa.CheckConstraint(
            "(zero_confirmed AND count_line_count = 0 AND "
            "observation_line_count = 0 AND serial_count = 0 AND "
            "total_counted_qty = 0) OR (NOT zero_confirmed AND "
            "count_line_count + observation_line_count > 0)",
            name="ck_stocktake_scope_count_completions_nonblank",
        ),
        sa.CheckConstraint(
            "length(evidence_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND length(idempotency_key_hash) = 64",
            name="ck_stocktake_scope_count_completions_hashes",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_scope_count_completions_authorization_version",
        ),
        sa.CheckConstraint(
            "role_code IN ('admin', 'provincial_manager', 'technician') AND "
            "scope_type IN ('national', 'organization', 'person') AND "
            "length(trim(scope_id_snapshot)) > 0 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_scope_count_completions_authorization_snapshot",
        ),
        sa.CheckConstraint(
            "created_at = completed_at",
            name="ck_stocktake_scope_count_completions_chronology",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_scope_count_completions_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scope_id", "task_id"],
            ["stocktake_scopes.id", "stocktake_scopes.task_id"],
            name="fk_stocktake_scope_count_completions_scope_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["completed_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name="pk_stocktake_scope_count_completions"
        ),
        sa.UniqueConstraint(
            "round_id",
            "scope_id",
            name="uq_stocktake_scope_count_completions_scope",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_scope_count_completions_idempotency",
        ),
    )
    op.create_index(
        "ix_stocktake_scope_count_completions_task",
        "stocktake_scope_count_completions",
        ["task_id", "round_id"],
    )

    op.create_table(
        "stocktake_round_submissions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_count", sa.Integer(), nullable=False),
        sa.Column("zero_scope_count", sa.Integer(), nullable=False),
        sa.Column("count_line_count", sa.Integer(), nullable=False),
        sa.Column("observation_line_count", sa.Integer(), nullable=False),
        sa.Column("serial_count", sa.Integer(), nullable=False),
        sa.Column("total_counted_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("round_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("submitted_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("submitted_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_count > 0 AND zero_scope_count >= 0 AND "
            "zero_scope_count <= scope_count AND count_line_count >= 0 AND "
            "observation_line_count >= 0 AND serial_count >= 0 AND "
            "total_counted_qty >= 0",
            name="ck_stocktake_round_submissions_totals",
        ),
        sa.CheckConstraint(
            "length(round_manifest_sha256) = 64 AND length(request_sha256) = 64 "
            "AND length(idempotency_key_hash) = 64",
            name="ck_stocktake_round_submissions_hashes",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_round_submissions_authorization_version",
        ),
        sa.CheckConstraint(
            "created_at = submitted_at",
            name="ck_stocktake_round_submissions_chronology",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_round_submissions_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["submitted_role_assignment_id"],
            ["role_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_round_submissions"),
        sa.UniqueConstraint(
            "task_id", "round_id", name="uq_stocktake_round_submissions_round"
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_round_submissions_idempotency",
        ),
    )
    op.create_index(
        "ix_stocktake_round_submissions_task",
        "stocktake_round_submissions",
        ["task_id"],
    )


def _drop_sqlite_difference_0010_triggers() -> None:
    for name in (
        "trg_stocktake_differences_immutable_update_0010",
        "trg_stocktake_differences_immutable_delete_0010",
        "trg_stocktake_differences_chronology_insert_0010",
        "trg_stocktake_posting_items_validate_insert_0010",
    ):
        op.execute(f"DROP TRIGGER {name}")


def _extend_stocktake_differences() -> None:
    if _dialect_name() == "postgresql":
        op.add_column(
            "stocktake_differences",
            sa.Column("observed_line_id", sa.Uuid(), nullable=True),
        )
        op.drop_constraint(
            "ck_stocktake_differences_binding",
            "stocktake_differences",
            type_="check",
        )
        op.create_check_constraint(
            "ck_stocktake_differences_binding",
            "stocktake_differences",
            DIFFERENCE_BINDING_CHECK,
        )
        op.create_foreign_key(
            "fk_stocktake_differences_observed_line",
            "stocktake_differences",
            "stocktake_count_observations",
            ["observed_line_id", "task_id", "round_id", "scope_id"],
            ["id", "task_id", "round_id", "scope_id"],
            ondelete="RESTRICT",
        )
    else:
        _drop_sqlite_difference_0010_triggers()
        with op.batch_alter_table(
            "stocktake_differences", recreate="always"
        ) as batch_op:
            batch_op.add_column(sa.Column("observed_line_id", sa.Uuid(), nullable=True))
            batch_op.drop_constraint(
                "ck_stocktake_differences_binding", type_="check"
            )
            batch_op.create_check_constraint(
                "ck_stocktake_differences_binding", DIFFERENCE_BINDING_CHECK
            )
            batch_op.create_foreign_key(
                "fk_stocktake_differences_observed_line",
                "stocktake_count_observations",
                ["observed_line_id", "task_id", "round_id", "scope_id"],
                ["id", "task_id", "round_id", "scope_id"],
                ondelete="RESTRICT",
            )
    op.create_index(
        "ix_stocktake_differences_observed_line",
        "stocktake_differences",
        ["observed_line_id"],
    )


def _create_count_contract_triggers() -> None:
    if _dialect_name() == "postgresql":
        _create_postgresql_count_contract_triggers()
    else:
        _create_sqlite_count_contract_triggers()


def _create_postgresql_count_contract_triggers() -> None:
    op.execute(
        """
CREATE FUNCTION rsc_stocktake_actor_assignment_valid_0011(
    p_user_id text,
    p_person_id uuid,
    p_assignment_id uuid,
    p_authorization_version bigint,
    p_occurred_at timestamptz,
    p_role_code text,
    p_scope_type text,
    p_scope_id text
)
RETURNS boolean
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
          FROM role_assignments AS assignment
          JOIN roles AS role ON role.id = assignment.role_id
          JOIN users AS actor ON actor.id = assignment.user_id
         WHERE assignment.id = p_assignment_id
           AND assignment.user_id = p_user_id
           AND actor.authorization_version = p_authorization_version
           AND (p_person_id IS NULL OR actor.person_id = p_person_id)
           AND assignment.status IN ('active', 'expired', 'revoked')
           AND assignment.valid_from <= p_occurred_at
           AND (assignment.valid_to IS NULL OR p_occurred_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL OR p_occurred_at < assignment.revoked_at)
           AND role.is_external = false
           AND role.code IN ('admin', 'provincial_manager', 'technician')
           AND (p_role_code IS NULL OR role.code = p_role_code)
           AND (p_scope_type IS NULL OR assignment.scope_type = p_scope_type)
           AND (p_scope_id IS NULL OR assignment.scope_id = p_scope_id)
    )
$$
"""
    )
    op.execute(
        """
CREATE FUNCTION rsc_block_opening_count_fact_mutation_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'opening count observation and completion facts are immutable';
END;
$$
"""
    )
    for table_name in NEW_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_0011 "
            f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION rsc_block_opening_count_fact_mutation_0011()"
        )
    for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_0011 "
            f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION rsc_block_opening_count_fact_mutation_0011()"
        )

    op.execute(
        """
CREATE FUNCTION rsc_validate_stocktake_observation_insert_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    parent_cutoff_at timestamptz;
    scope_owner uuid;
    scope_location uuid;
    scope_custodian uuid;
    scope_assignee text;
    scope_mode text;
    scope_material uuid;
    scope_condition text;
    scope_availability text;
    policy_count integer;
    policy_tracking text;
    policy_scale integer;
BEGIN
    SELECT round_row.status, round_row.started_at, task.cutoff_at
      INTO parent_status, parent_started_at, parent_cutoff_at
      FROM stocktake_rounds AS round_row
      JOIN stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id
       AND round_row.task_id = NEW.task_id
     FOR UPDATE OF round_row;
    IF parent_status IS DISTINCT FROM 'counting'
       OR parent_cutoff_at IS NULL
       OR NEW.counted_at < parent_started_at
       OR EXISTS (
           SELECT 1 FROM stocktake_round_submissions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       )
       OR EXISTS (
           SELECT 1 FROM stocktake_scope_count_completions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND scope_id = NEW.scope_id
       ) THEN
        RAISE EXCEPTION 'stocktake observation scope is already sealed';
    END IF;

    SELECT owner_org_id, location_id, custodian_person_id_snapshot,
           assignee_user_id, scope_mode, material_id, condition_code,
           availability_bucket
      INTO scope_owner, scope_location, scope_custodian, scope_assignee,
           scope_mode, scope_material, scope_condition, scope_availability
      FROM stocktake_scopes
     WHERE id = NEW.scope_id AND task_id = NEW.task_id;
    IF scope_owner IS NULL
       OR NEW.owner_org_id IS DISTINCT FROM scope_owner
       OR NEW.location_id IS DISTINCT FROM scope_location
       OR NEW.custodian_person_id_snapshot IS DISTINCT FROM scope_custodian
       OR NEW.counted_by_user_id IS DISTINCT FROM scope_assignee
       OR (scope_mode = 'filtered' AND (
           (scope_material IS NOT NULL AND NEW.material_id IS DISTINCT FROM scope_material)
           OR (scope_condition IS NOT NULL AND NEW.condition_code IS DISTINCT FROM scope_condition)
           OR (scope_availability IS NOT NULL AND NEW.availability_bucket IS DISTINCT FROM scope_availability)
       )) THEN
        RAISE EXCEPTION 'stocktake observation is outside its frozen scope';
    END IF;
    IF NEW.material_id IS NOT NULL THEN
        SELECT count(*), min(tracking_mode), min(quantity_scale)
          INTO policy_count, policy_tracking, policy_scale
          FROM material_inventory_policies
         WHERE material_id = NEW.material_id
           AND effective_from <= parent_cutoff_at
           AND (effective_to IS NULL OR parent_cutoff_at < effective_to);
        IF policy_count <> 1
           OR NEW.counted_qty <> round(NEW.counted_qty, policy_scale)
           OR (policy_tracking = 'none' AND
               (NEW.lot_no_raw IS NOT NULL OR NEW.serial_no_raw IS NOT NULL))
           OR (policy_tracking = 'lot' AND
               (NEW.lot_no_raw IS NULL OR NEW.serial_no_raw IS NOT NULL))
           OR (policy_tracking = 'serial' AND
               (NEW.lot_no_raw IS NOT NULL OR NEW.serial_no_raw IS NULL))
           OR (policy_tracking = 'lot_and_serial' AND
               (NEW.lot_no_raw IS NULL OR NEW.serial_no_raw IS NULL)) THEN
            RAISE EXCEPTION 'stocktake observation violates cutoff tracking policy';
        END IF;
    END IF;
    IF NEW.lot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_lots
         WHERE id = NEW.lot_id AND material_id = NEW.material_id
           AND lot_no = NEW.lot_no_raw
    ) THEN
        RAISE EXCEPTION 'stocktake observation lot mapping is inconsistent';
    END IF;
    IF NEW.serial_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_serials
         WHERE id = NEW.serial_id AND material_id = NEW.material_id
           AND serial_no = NEW.serial_no_raw
           AND lot_id IS NOT DISTINCT FROM NEW.lot_id
    ) THEN
        RAISE EXCEPTION 'stocktake observation serial mapping is inconsistent';
    END IF;

    -- Only a fully verified dimension is eligible for an exact account test.
    -- Pending raw identifiers remain physical evidence even when some known
    -- dimensions happen to resemble an existing account.
    IF NEW.verification_status = 'verified' AND (
        EXISTS (
            SELECT 1 FROM stock_accounts AS account
             WHERE account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS NOT DISTINCT FROM
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NOT DISTINCT FROM NEW.lot_id
               AND account.created_at <= parent_cutoff_at
        ) OR EXISTS (
            SELECT 1
              FROM stocktake_snapshot_lines AS snapshot
              JOIN stock_accounts AS account ON account.id = snapshot.stock_account_id
             WHERE snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS NOT DISTINCT FROM
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NOT DISTINCT FROM NEW.lot_id
        )
    ) THEN
        RAISE EXCEPTION 'verified observation must use the cutoff account count line';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        "CREATE TRIGGER trg_stocktake_count_observations_validate_insert_0011 "
        "BEFORE INSERT ON stocktake_count_observations FOR EACH ROW "
        "EXECUTE FUNCTION rsc_validate_stocktake_observation_insert_0011()"
    )

    op.execute(
        """
CREATE FUNCTION rsc_validate_stocktake_existing_count_child_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    child_round_id uuid;
    child_task_id uuid;
    child_scope_id uuid;
    child_user_id text;
    child_counted_at timestamptz;
    child_assignment_id uuid;
    child_authorization_version bigint;
    parent_status text;
    parent_started_at timestamptz;
BEGIN
    IF TG_TABLE_NAME = 'stocktake_count_lines' THEN
        child_round_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.round_id ELSE NEW.round_id END;
        child_task_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.task_id ELSE NEW.task_id END;
        child_scope_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.scope_id ELSE NEW.scope_id END;
        child_user_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.counted_by_user_id ELSE NEW.counted_by_user_id END;
        child_counted_at := CASE WHEN TG_OP = 'DELETE' THEN OLD.counted_at ELSE NEW.counted_at END;
        SELECT id, authorization_version
          INTO child_assignment_id, child_authorization_version
          FROM role_assignments
         WHERE user_id = child_user_id
           AND status IN ('active', 'expired', 'revoked')
           AND valid_from <= child_counted_at
           AND (valid_to IS NULL OR child_counted_at < valid_to)
           AND (revoked_at IS NULL OR child_counted_at < revoked_at)
         ORDER BY valid_from DESC, id
         LIMIT 1;
    ELSE
        SELECT line.round_id, line.task_id, line.scope_id, line.counted_by_user_id,
               line.counted_at
          INTO child_round_id, child_task_id, child_scope_id, child_user_id,
               child_counted_at
          FROM stocktake_count_lines AS line
         WHERE line.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.count_line_id ELSE NEW.count_line_id END
           AND line.round_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.round_id ELSE NEW.round_id END;
    END IF;
    SELECT status, started_at INTO parent_status, parent_started_at
      FROM stocktake_rounds
     WHERE id = child_round_id AND task_id = child_task_id
     FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'counting'
       OR child_scope_id IS NULL
       OR child_counted_at < parent_started_at
       OR EXISTS (SELECT 1 FROM stocktake_round_submissions
                   WHERE task_id = child_task_id AND round_id = child_round_id)
       OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions
                   WHERE task_id = child_task_id AND round_id = child_round_id
                     AND scope_id = child_scope_id) THEN
        RAISE EXCEPTION 'stocktake count child is sealed by completion';
    END IF;
    IF TG_TABLE_NAME = 'stocktake_count_lines' AND TG_OP <> 'DELETE' THEN
        IF NOT EXISTS (
            SELECT 1
              FROM stocktake_scopes AS scope
              JOIN stock_accounts AS account ON account.id = NEW.stock_account_id
              JOIN stocktake_snapshot_lines AS snapshot
                ON snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND snapshot.stock_account_id = NEW.stock_account_id
             WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
               AND scope.assignee_user_id = NEW.counted_by_user_id
               AND scope.owner_org_id = account.owner_org_id
               AND scope.location_id = account.location_id
               AND (scope.scope_mode = 'location_all' OR (
                    (scope.material_id IS NULL OR scope.material_id = account.material_id)
                AND (scope.condition_code IS NULL OR scope.condition_code = account.condition_code)
                AND (scope.availability_bucket IS NULL OR
                     scope.availability_bucket = account.availability_bucket)
               ))
        ) THEN
            RAISE EXCEPTION 'stocktake count line must bind one cutoff snapshot account';
        END IF;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$
"""
    )
    for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_completion_seal_0011 "
            f"BEFORE INSERT ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION rsc_validate_stocktake_existing_count_child_0011()"
        )

    op.execute(
        """
CREATE FUNCTION rsc_validate_stocktake_scope_completion_insert_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    parent_cutoff_at timestamptz;
    scope_assignee text;
    scope_owner uuid;
    scope_custodian uuid;
    actual_count_lines integer;
    actual_observations integer;
    actual_serials integer;
    actual_total numeric(18, 3);
BEGIN
    SELECT round_row.status, round_row.started_at, task.cutoff_at
      INTO parent_status, parent_started_at, parent_cutoff_at
      FROM stocktake_rounds AS round_row
      JOIN stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
     FOR UPDATE OF round_row;
    SELECT assignee_user_id, owner_org_id, custodian_person_id_snapshot
      INTO scope_assignee, scope_owner, scope_custodian
      FROM stocktake_scopes
     WHERE id = NEW.scope_id AND task_id = NEW.task_id;
    IF parent_status IS DISTINCT FROM 'counting'
       OR scope_assignee IS NULL
       OR NEW.completed_by_user_id IS DISTINCT FROM scope_assignee
       OR NEW.completed_at < parent_started_at
       OR EXISTS (SELECT 1 FROM stocktake_round_submissions
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id) THEN
        RAISE EXCEPTION 'stocktake scope completion is invalid or sealed';
    END IF;
    IF NOT rsc_stocktake_actor_assignment_valid_0011(
        NEW.completed_by_user_id, NEW.completed_by_person_id,
        NEW.completed_role_assignment_id, NEW.authorization_version,
        NEW.completed_at, NEW.role_code, NEW.scope_type, NEW.scope_id_snapshot
    ) OR NOT (
        (NEW.role_code = 'admin' AND NEW.scope_type = 'national'
         AND NEW.scope_id_snapshot = '*')
        OR (NEW.role_code = 'provincial_manager'
            AND NEW.scope_type = 'organization'
            AND NEW.scope_id_snapshot = scope_owner::text)
        OR (NEW.role_code = 'technician' AND NEW.scope_type = 'person'
            AND scope_custodian IS NOT NULL
            AND NEW.scope_id_snapshot = scope_custodian::text)
    ) THEN
        RAISE EXCEPTION 'stocktake scope completion authorization is invalid';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stocktake_count_lines
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id
           AND counted_by_user_id <> NEW.completed_by_user_id
    ) OR EXISTS (
        SELECT 1 FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id
           AND counted_by_user_id <> NEW.completed_by_user_id
    ) THEN
        RAISE EXCEPTION 'scope completion cannot combine different count actors';
    END IF;
    IF EXISTS (
        SELECT 1 FROM stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = NEW.task_id AND snapshot.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_count_lines AS line
                WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
                  AND line.scope_id = NEW.scope_id
                  AND line.stock_account_id = snapshot.stock_account_id
           )
    ) OR EXISTS (
        SELECT 1 FROM stocktake_count_lines AS line
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_snapshot_lines AS snapshot
                WHERE snapshot.task_id = NEW.task_id
                  AND snapshot.scope_id = NEW.scope_id
                  AND snapshot.stock_account_id = line.stock_account_id
           )
    ) THEN
        RAISE EXCEPTION 'scope completion must cover every cutoff snapshot account exactly once';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM stocktake_count_lines AS line
          JOIN stock_accounts AS account ON account.id = line.stock_account_id
          JOIN LATERAL (
              SELECT count(*) AS policy_count,
                     min(policy.tracking_mode) AS tracking_mode,
                     min(policy.quantity_scale) AS quantity_scale,
                     bool_and(policy.allow_fraction) AS allow_fraction
                FROM material_inventory_policies AS policy
               WHERE policy.material_id = account.material_id
                 AND policy.effective_from <= parent_cutoff_at
                 AND (policy.effective_to IS NULL
                      OR parent_cutoff_at < policy.effective_to)
          ) AS cutoff_policy ON true
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (
               cutoff_policy.policy_count <> 1
               OR line.counted_qty <> round(
                   line.counted_qty, cutoff_policy.quantity_scale)
               OR (NOT cutoff_policy.allow_fraction
                   AND line.counted_qty <> trunc(line.counted_qty))
               OR (cutoff_policy.tracking_mode IN ('none', 'serial')
                   AND account.lot_id IS NOT NULL)
               OR (cutoff_policy.tracking_mode IN ('lot', 'lot_and_serial')
                   AND account.lot_id IS NULL)
               OR (cutoff_policy.tracking_mode IN ('none', 'lot') AND EXISTS (
                   SELECT 1 FROM stocktake_count_serials AS serial_row
                    WHERE serial_row.count_line_id = line.id
                      AND serial_row.round_id = line.round_id
               ))
               OR (cutoff_policy.tracking_mode IN ('serial', 'lot_and_serial')
                   AND line.counted_qty <> (
                       SELECT count(*)
                         FROM stocktake_count_serials AS serial_row
                        WHERE serial_row.count_line_id = line.id
                          AND serial_row.round_id = line.round_id
                          AND serial_row.result <> 'missing'
                   ))
           )
    ) THEN
        RAISE EXCEPTION 'count line violates its cutoff inventory tracking policy';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN stock_accounts AS account ON account.id = line.stock_account_id
          JOIN inventory_serials AS serial ON serial.id = serial_row.serial_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (serial.material_id IS DISTINCT FROM account.material_id
                OR serial.lot_id IS DISTINCT FROM account.lot_id)
    ) THEN
        RAISE EXCEPTION 'count serial material or lot does not match its account';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN inventory_serials AS serial ON serial.id = serial_row.serial_id
          JOIN stocktake_count_observations AS observation
            ON observation.round_id = line.round_id
           AND observation.serial_no_raw IS NOT NULL
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND (
               observation.serial_id = serial_row.serial_id
               OR (observation.serial_identifier_type IN ('qr_code', 'unknown')
                   AND observation.serial_no_raw = serial.qr_code)
               OR (observation.serial_identifier_type IN ('serial_no', 'unknown')
                   AND observation.serial_no_raw = serial.serial_no
                   AND (observation.material_id IS NULL
                        OR observation.material_id = serial.material_id))
           )
    ) THEN
        RAISE EXCEPTION 'one physical serial cannot be both count serial and observation';
    END IF;
    SELECT count(*), COALESCE(sum(counted_qty), 0)
      INTO actual_count_lines, actual_total
      FROM stocktake_count_lines
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND scope_id = NEW.scope_id;
    SELECT count(*), actual_total + COALESCE(sum(counted_qty), 0)
      INTO actual_observations, actual_total
      FROM stocktake_count_observations
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND scope_id = NEW.scope_id;
    SELECT count(*) INTO actual_serials
      FROM stocktake_count_serials AS serial_row
      JOIN stocktake_count_lines AS line
        ON line.id = serial_row.count_line_id AND line.round_id = serial_row.round_id
     WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
       AND line.scope_id = NEW.scope_id;
    actual_serials := actual_serials + (
        SELECT count(*) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id AND serial_no_raw IS NOT NULL
    );
    IF NEW.count_line_count <> actual_count_lines
       OR NEW.observation_line_count <> actual_observations
       OR NEW.serial_count <> actual_serials
       OR NEW.total_counted_qty <> actual_total
       OR (NEW.zero_confirmed AND EXISTS (
           SELECT 1 FROM stocktake_snapshot_lines
            WHERE task_id = NEW.task_id AND scope_id = NEW.scope_id
       )) THEN
        RAISE EXCEPTION 'stocktake scope completion totals are not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        "CREATE TRIGGER trg_stocktake_scope_count_completions_validate_insert_0011 "
        "BEFORE INSERT ON stocktake_scope_count_completions FOR EACH ROW "
        "EXECUTE FUNCTION rsc_validate_stocktake_scope_completion_insert_0011()"
    )

    op.execute(
        """
CREATE FUNCTION rsc_validate_stocktake_round_submission_insert_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    actual_scope_count integer;
    actual_zero_count integer;
    actual_count_lines integer;
    actual_observations integer;
    actual_serials integer;
    actual_total numeric(18, 3);
    latest_completion_at timestamptz;
BEGIN
    SELECT status, started_at INTO parent_status, parent_started_at
      FROM stocktake_rounds
     WHERE id = NEW.round_id AND task_id = NEW.task_id
     FOR UPDATE;
    IF parent_status IS DISTINCT FROM 'counting'
       OR NEW.submitted_at < parent_started_at
       OR NOT rsc_stocktake_actor_assignment_valid_0011(
           NEW.submitted_by_user_id, NEW.submitted_by_person_id,
           NEW.submitted_role_assignment_id, NEW.authorization_version,
           NEW.submitted_at, NULL, NULL, NULL
       )
       OR NOT EXISTS (
           SELECT 1 FROM stocktake_scope_count_completions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND completed_by_user_id = NEW.submitted_by_user_id
              AND completed_by_person_id = NEW.submitted_by_person_id
       ) THEN
        RAISE EXCEPTION 'stocktake round submission actor or chronology is invalid';
    END IF;
    SELECT count(*) INTO actual_scope_count
      FROM stocktake_scopes WHERE task_id = NEW.task_id;
    IF actual_scope_count = 0 OR EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.task_id = NEW.task_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_scope_count_completions AS completion
                WHERE completion.task_id = NEW.task_id
                  AND completion.round_id = NEW.round_id
                  AND completion.scope_id = scope.id
           )
    ) THEN
        RAISE EXCEPTION 'stocktake round submission requires every scope completion';
    END IF;
    SELECT count(*), count(*) FILTER (WHERE zero_confirmed),
           sum(count_line_count), sum(observation_line_count), sum(serial_count),
           sum(total_counted_qty), max(completed_at)
      INTO actual_scope_count, actual_zero_count, actual_count_lines,
           actual_observations, actual_serials, actual_total, latest_completion_at
      FROM stocktake_scope_count_completions
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id;
    IF NEW.scope_count <> actual_scope_count
       OR NEW.zero_scope_count <> actual_zero_count
       OR NEW.count_line_count <> actual_count_lines
       OR NEW.observation_line_count <> actual_observations
       OR NEW.serial_count <> actual_serials
       OR NEW.total_counted_qty <> actual_total
       OR NEW.submitted_at < latest_completion_at THEN
        RAISE EXCEPTION 'stocktake round submission totals are not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        "CREATE TRIGGER trg_stocktake_round_submissions_validate_insert_0011 "
        "BEFORE INSERT ON stocktake_round_submissions FOR EACH ROW "
        "EXECUTE FUNCTION rsc_validate_stocktake_round_submission_insert_0011()"
    )

    op.execute(
        """
CREATE FUNCTION rsc_require_stocktake_round_submission_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'submitted' AND TG_OP = 'INSERT' THEN
        RAISE EXCEPTION 'submitted stocktake round requires its immutable submission manifest';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.status = 'submitted'
       AND OLD.status = 'counting' AND NOT EXISTS (
        SELECT 1 FROM stocktake_round_submissions AS submission
         WHERE submission.task_id = NEW.task_id
           AND submission.round_id = NEW.id
           AND submission.submitted_by_user_id = NEW.submitted_by_user_id
           AND submission.submitted_at = NEW.submitted_at
           AND submission.round_manifest_sha256 = NEW.count_manifest_sha256
       ) THEN
        RAISE EXCEPTION 'submitted stocktake round requires its immutable submission manifest';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        "CREATE TRIGGER trg_stocktake_rounds_submission_manifest_update_0011 "
        "BEFORE INSERT OR UPDATE ON stocktake_rounds FOR EACH ROW "
        "EXECUTE FUNCTION rsc_require_stocktake_round_submission_0011()"
    )

    op.execute(
        """
CREATE FUNCTION rsc_validate_stocktake_observed_difference_0011()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    observation_material uuid;
    observation_serial uuid;
    observation_qty numeric(18, 3);
    observation_status text;
BEGIN
    IF NEW.observed_line_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT material_id, serial_id, counted_qty, verification_status
      INTO observation_material, observation_serial, observation_qty,
           observation_status
      FROM stocktake_count_observations
     WHERE id = NEW.observed_line_id AND task_id = NEW.task_id
       AND round_id = NEW.round_id AND scope_id = NEW.scope_id;
    IF observation_status IS NULL
       OR NEW.material_id IS DISTINCT FROM observation_material
       OR NEW.serial_id IS DISTINCT FROM observation_serial
       OR (NEW.difference_type = 'excess' AND (
           NEW.book_qty <> 0 OR NEW.counted_qty <> observation_qty
           OR NEW.difference_qty <> observation_qty
           OR NEW.affected_qty <> observation_qty
       )) THEN
        RAISE EXCEPTION 'stocktake difference observed line binding is invalid';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        "CREATE TRIGGER trg_stocktake_differences_observed_line_insert_0011 "
        "BEFORE INSERT ON stocktake_differences FOR EACH ROW "
        "EXECUTE FUNCTION rsc_validate_stocktake_observed_difference_0011()"
    )


def _create_sqlite_count_contract_triggers() -> None:
    _recreate_sqlite_posting_item_0010_trigger()
    for table_name in NEW_TABLES:
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_"
                f"{operation.lower()}_0011 BEFORE {operation} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'opening count observation and "
                "completion facts are immutable'); END"
            )
    for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_"
                f"{operation.lower()}_0011 BEFORE {operation} ON {table_name} "
                "BEGIN SELECT RAISE(ABORT, 'opening count observation and "
                "completion facts are immutable'); END"
            )

    # The SQLite batch rebuild used to add observed_line_id drops the 0010
    # triggers attached to stocktake_differences.  Recreate their exact safety
    # semantics under this revision before adding the new binding validator.
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_stocktake_differences_immutable_"
            f"{operation.lower()}_0011 BEFORE {operation} ON stocktake_differences "
            "BEGIN SELECT RAISE(ABORT, 'formal stocktake fact rows are immutable'); "
            "END"
        )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_differences_chronology_insert_0011
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
CREATE TRIGGER trg_stocktake_count_observations_validate_insert_0011
BEFORE INSERT ON stocktake_count_observations
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id) IS NULL
 OR NEW.counted_at < (SELECT started_at FROM stocktake_rounds
                       WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_round_submissions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id)
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
           AND scope.owner_org_id = NEW.owner_org_id
           AND scope.location_id = NEW.location_id
           AND scope.custodian_person_id_snapshot IS
               NEW.custodian_person_id_snapshot
           AND scope.assignee_user_id = NEW.counted_by_user_id
           AND (scope.scope_mode = 'location_all' OR (
                (scope.material_id IS NULL OR scope.material_id = NEW.material_id)
            AND (scope.condition_code IS NULL OR
                 scope.condition_code = NEW.condition_code)
            AND (scope.availability_bucket IS NULL OR
                 scope.availability_bucket = NEW.availability_bucket)
           ))
    )
 OR (NEW.material_id IS NOT NULL AND (
        (SELECT count(*) FROM material_inventory_policies
          WHERE material_id = NEW.material_id
            AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                     WHERE id = NEW.task_id)
            AND (effective_to IS NULL OR
                 (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                 < effective_to)) <> 1
        OR NEW.counted_qty <> round(
            NEW.counted_qty,
            (SELECT quantity_scale FROM material_inventory_policies
              WHERE material_id = NEW.material_id
                AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                         WHERE id = NEW.task_id)
                AND (effective_to IS NULL OR
                     (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                     < effective_to))
        )
        OR CASE (SELECT tracking_mode FROM material_inventory_policies
                  WHERE material_id = NEW.material_id
                    AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                             WHERE id = NEW.task_id)
                    AND (effective_to IS NULL OR
                         (SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < effective_to))
             WHEN 'none' THEN NEW.lot_no_raw IS NOT NULL
                              OR NEW.serial_no_raw IS NOT NULL
             WHEN 'lot' THEN NEW.lot_no_raw IS NULL
                             OR NEW.serial_no_raw IS NOT NULL
             WHEN 'serial' THEN NEW.lot_no_raw IS NOT NULL
                                OR NEW.serial_no_raw IS NULL
             WHEN 'lot_and_serial' THEN NEW.lot_no_raw IS NULL
                                        OR NEW.serial_no_raw IS NULL
             ELSE 1
           END
    ))
 OR (NEW.lot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_lots WHERE id = NEW.lot_id
          AND material_id = NEW.material_id AND lot_no = NEW.lot_no_raw
    ))
 OR (NEW.serial_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_serials WHERE id = NEW.serial_id
          AND material_id = NEW.material_id AND serial_no = NEW.serial_no_raw
          AND lot_id IS NEW.lot_id
    ))
 OR (NEW.verification_status = 'verified' AND (
        EXISTS (
            SELECT 1 FROM stock_accounts AS account
             WHERE account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NEW.lot_id
               AND account.created_at <=
                   (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
        ) OR EXISTS (
            SELECT 1
              FROM stocktake_snapshot_lines AS snapshot
              JOIN stock_accounts AS account
                ON account.id = snapshot.stock_account_id
             WHERE snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NEW.lot_id
        )
    ))
BEGIN
    SELECT RAISE(ABORT,
        'stocktake observation is invalid, sealed, outside scope, or not unexpected');
END
"""
    )

    line_seal_condition = """
COALESCE((SELECT status FROM stocktake_rounds
           WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
    <> 'counting'
OR NEW.counted_at < (SELECT started_at FROM stocktake_rounds
                      WHERE id = NEW.round_id AND task_id = NEW.task_id)
OR EXISTS (SELECT 1 FROM stocktake_round_submissions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND scope_id = NEW.scope_id)
OR NOT EXISTS (
    SELECT 1
      FROM stocktake_scopes AS scope
      JOIN stock_accounts AS account ON account.id = NEW.stock_account_id
      JOIN stocktake_snapshot_lines AS snapshot
        ON snapshot.task_id = NEW.task_id
       AND snapshot.scope_id = NEW.scope_id
       AND snapshot.stock_account_id = NEW.stock_account_id
     WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
       AND scope.assignee_user_id = NEW.counted_by_user_id
       AND scope.owner_org_id = account.owner_org_id
       AND scope.location_id = account.location_id
       AND (scope.scope_mode = 'location_all' OR (
            (scope.material_id IS NULL OR scope.material_id = account.material_id)
        AND (scope.condition_code IS NULL OR
             scope.condition_code = account.condition_code)
        AND (scope.availability_bucket IS NULL OR
             scope.availability_bucket = account.availability_bucket)
       ))
)
"""
    for operation in ("INSERT",):
        op.execute(
            f"CREATE TRIGGER trg_stocktake_count_lines_completion_seal_"
            f"{operation.lower()}_0011 BEFORE {operation} ON stocktake_count_lines "
            f"WHEN {line_seal_condition} "
            "BEGIN SELECT RAISE(ABORT, 'stocktake count child is sealed by "
            "completion or is outside the cutoff snapshot'); END"
        )
    serial_parent = (
        "SELECT line.scope_id FROM stocktake_count_lines AS line "
        "WHERE line.id = {prefix}.count_line_id AND line.round_id = {prefix}.round_id"
    )
    for operation in ("INSERT",):
        parent = serial_parent.format(prefix="NEW")
        op.execute(
            f"CREATE TRIGGER trg_stocktake_count_serials_completion_seal_"
            f"{operation.lower()}_0011 BEFORE {operation} ON stocktake_count_serials "
            "WHEN COALESCE((SELECT status FROM stocktake_rounds WHERE id = "
            "NEW.round_id), 'missing') <> 'counting' OR EXISTS (SELECT 1 FROM "
            "stocktake_round_submissions WHERE round_id = NEW.round_id) OR "
            "EXISTS (SELECT 1 FROM stocktake_scope_count_completions WHERE "
            f"round_id = NEW.round_id AND scope_id = ({parent})) "
            "BEGIN SELECT RAISE(ABORT, 'stocktake count child is sealed by "
            "completion'); END"
        )

    op.execute(
        """
CREATE TRIGGER trg_stocktake_scope_count_completions_validate_insert_0011
BEFORE INSERT ON stocktake_scope_count_completions
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR NEW.completed_at < (SELECT started_at FROM stocktake_rounds
                         WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_round_submissions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
           AND scope.assignee_user_id = NEW.completed_by_user_id
    )
 OR NOT EXISTS (
        SELECT 1
          FROM role_assignments AS assignment
          JOIN roles AS role ON role.id = assignment.role_id
          JOIN users AS actor ON actor.id = assignment.user_id
         WHERE assignment.id = NEW.completed_role_assignment_id
           AND assignment.user_id = NEW.completed_by_user_id
           AND actor.person_id = NEW.completed_by_person_id
           AND actor.authorization_version = NEW.authorization_version
           AND assignment.status IN ('active', 'expired', 'revoked')
           AND assignment.valid_from <= NEW.completed_at
           AND (assignment.valid_to IS NULL OR NEW.completed_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL OR NEW.completed_at < assignment.revoked_at)
           AND role.is_external = 0 AND role.code = NEW.role_code
           AND assignment.scope_type = NEW.scope_type
           AND assignment.scope_id = NEW.scope_id_snapshot
    )
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
           AND (
               (NEW.role_code = 'admin' AND NEW.scope_type = 'national'
                AND NEW.scope_id_snapshot = '*')
               OR (NEW.role_code = 'provincial_manager'
                   AND NEW.scope_type = 'organization'
                   AND replace(NEW.scope_id_snapshot, '-', '') =
                       replace(CAST(scope.owner_org_id AS TEXT), '-', ''))
               OR (NEW.role_code = 'technician' AND NEW.scope_type = 'person'
                   AND scope.custodian_person_id_snapshot IS NOT NULL
                   AND replace(NEW.scope_id_snapshot, '-', '') =
                       replace(CAST(scope.custodian_person_id_snapshot AS TEXT), '-', ''))
           )
    )
 OR EXISTS (SELECT 1 FROM stocktake_count_lines
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id
               AND counted_by_user_id <> NEW.completed_by_user_id)
 OR EXISTS (SELECT 1 FROM stocktake_count_observations
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id
               AND counted_by_user_id <> NEW.completed_by_user_id)
 OR EXISTS (
        SELECT 1 FROM stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = NEW.task_id AND snapshot.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_count_lines AS line
                WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
                  AND line.scope_id = NEW.scope_id
                  AND line.stock_account_id = snapshot.stock_account_id
           )
    )
 OR EXISTS (
        SELECT 1 FROM stocktake_count_lines AS line
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_snapshot_lines AS snapshot
                WHERE snapshot.task_id = NEW.task_id
                  AND snapshot.scope_id = NEW.scope_id
                  AND snapshot.stock_account_id = line.stock_account_id
           )
    )
 OR EXISTS (
        SELECT 1
          FROM stocktake_count_lines AS line
          JOIN stock_accounts AS account ON account.id = line.stock_account_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (
               (SELECT count(*) FROM material_inventory_policies AS policy
                 WHERE policy.material_id = account.material_id
                   AND policy.effective_from <= (
                       SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                   AND (policy.effective_to IS NULL OR (
                       SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                       < policy.effective_to)) <> 1
               OR line.counted_qty <> round(
                   line.counted_qty,
                   COALESCE((SELECT min(policy.quantity_scale)
                     FROM material_inventory_policies AS policy
                    WHERE policy.material_id = account.material_id
                      AND policy.effective_from <= (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id)
                      AND (policy.effective_to IS NULL OR (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < policy.effective_to)), 0))
               OR (COALESCE((SELECT min(CAST(policy.allow_fraction AS INTEGER))
                     FROM material_inventory_policies AS policy
                    WHERE policy.material_id = account.material_id
                      AND policy.effective_from <= (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id)
                      AND (policy.effective_to IS NULL OR (
                          SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < policy.effective_to)), 0) = 0
                   AND line.counted_qty <> round(line.counted_qty, 0))
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('none', 'serial') AND account.lot_id IS NOT NULL)
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('lot', 'lot_and_serial') AND account.lot_id IS NULL)
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('none', 'lot') AND EXISTS (
                       SELECT 1 FROM stocktake_count_serials AS serial_row
                        WHERE serial_row.count_line_id = line.id
                          AND serial_row.round_id = line.round_id))
               OR ((SELECT min(policy.tracking_mode)
                      FROM material_inventory_policies AS policy
                     WHERE policy.material_id = account.material_id
                       AND policy.effective_from <= (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id)
                       AND (policy.effective_to IS NULL OR (
                           SELECT cutoff_at FROM stocktake_tasks
                            WHERE id = NEW.task_id) < policy.effective_to))
                   IN ('serial', 'lot_and_serial')
                   AND line.counted_qty <> (
                       SELECT count(*) FROM stocktake_count_serials AS serial_row
                        WHERE serial_row.count_line_id = line.id
                          AND serial_row.round_id = line.round_id
                          AND serial_row.result <> 'missing'))
           )
    )
 OR EXISTS (
        SELECT 1
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN stock_accounts AS account ON account.id = line.stock_account_id
          JOIN inventory_serials AS serial ON serial.id = serial_row.serial_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id
           AND (serial.material_id IS NOT account.material_id
                OR serial.lot_id IS NOT account.lot_id)
    )
 OR EXISTS (
        SELECT 1
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
          JOIN inventory_serials AS serial ON serial.id = serial_row.serial_id
          JOIN stocktake_count_observations AS observation
            ON observation.round_id = line.round_id
           AND observation.serial_no_raw IS NOT NULL
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND (
               observation.serial_id = serial_row.serial_id
               OR (observation.serial_identifier_type IN ('qr_code', 'unknown')
                   AND observation.serial_no_raw = serial.qr_code)
               OR (observation.serial_identifier_type IN ('serial_no', 'unknown')
                   AND observation.serial_no_raw = serial.serial_no
                   AND (observation.material_id IS NULL
                        OR observation.material_id = serial.material_id))
           )
    )
 OR NEW.count_line_count <> (
        SELECT count(*) FROM stocktake_count_lines
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id)
 OR NEW.observation_line_count <> (
        SELECT count(*) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id)
 OR NEW.serial_count <> (
        SELECT count(*)
          FROM stocktake_count_serials AS serial_row
          JOIN stocktake_count_lines AS line
            ON line.id = serial_row.count_line_id
           AND line.round_id = serial_row.round_id
         WHERE line.task_id = NEW.task_id AND line.round_id = NEW.round_id
           AND line.scope_id = NEW.scope_id) + (
        SELECT count(*) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id AND serial_no_raw IS NOT NULL)
 OR NEW.total_counted_qty <> COALESCE((
        SELECT sum(counted_qty) FROM stocktake_count_lines
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id), 0) + COALESCE((
        SELECT sum(counted_qty) FROM stocktake_count_observations
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND scope_id = NEW.scope_id), 0)
 OR (NEW.zero_confirmed AND EXISTS (
        SELECT 1 FROM stocktake_snapshot_lines
         WHERE task_id = NEW.task_id AND scope_id = NEW.scope_id))
BEGIN
    SELECT RAISE(ABORT, 'stocktake scope completion is not canonical');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_round_submissions_validate_insert_0011
BEFORE INSERT ON stocktake_round_submissions
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR NEW.submitted_at < (SELECT started_at FROM stocktake_rounds
                         WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR NOT EXISTS (
        SELECT 1
          FROM role_assignments AS assignment
          JOIN roles AS role ON role.id = assignment.role_id
          JOIN users AS actor ON actor.id = assignment.user_id
         WHERE assignment.id = NEW.submitted_role_assignment_id
           AND assignment.user_id = NEW.submitted_by_user_id
           AND actor.person_id = NEW.submitted_by_person_id
           AND actor.authorization_version = NEW.authorization_version
           AND assignment.status IN ('active', 'expired', 'revoked')
           AND assignment.valid_from <= NEW.submitted_at
           AND (assignment.valid_to IS NULL OR NEW.submitted_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL OR NEW.submitted_at < assignment.revoked_at)
           AND role.is_external = 0
           AND role.code IN ('admin', 'provincial_manager', 'technician')
    )
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scope_count_completions
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
           AND completed_by_user_id = NEW.submitted_by_user_id
           AND completed_by_person_id = NEW.submitted_by_person_id
    )
 OR (SELECT count(*) FROM stocktake_scopes WHERE task_id = NEW.task_id) = 0
 OR EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.task_id = NEW.task_id
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_scope_count_completions AS completion
                WHERE completion.task_id = NEW.task_id
                  AND completion.round_id = NEW.round_id
                  AND completion.scope_id = scope.id
           )
    )
 OR NEW.scope_count <> (SELECT count(*) FROM stocktake_scope_count_completions
                          WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NEW.zero_scope_count <> (SELECT count(*) FROM stocktake_scope_count_completions
                              WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                                AND zero_confirmed = 1)
 OR NEW.count_line_count <> COALESCE((SELECT sum(count_line_count)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.observation_line_count <> COALESCE((SELECT sum(observation_line_count)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.serial_count <> COALESCE((SELECT sum(serial_count)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.total_counted_qty <> COALESCE((SELECT sum(total_counted_qty)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR NEW.submitted_at < (SELECT max(completed_at)
      FROM stocktake_scope_count_completions WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id)
BEGIN
    SELECT RAISE(ABORT, 'stocktake round submission is not canonical');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_rounds_submission_manifest_insert_0011
BEFORE INSERT ON stocktake_rounds
WHEN NEW.status = 'submitted'
BEGIN
    SELECT RAISE(ABORT,
        'submitted stocktake round requires its immutable submission manifest');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_rounds_submission_manifest_update_0011
BEFORE UPDATE ON stocktake_rounds
WHEN NEW.status = 'submitted' AND OLD.status = 'counting'
 AND NOT EXISTS (
    SELECT 1 FROM stocktake_round_submissions AS submission
     WHERE submission.task_id = NEW.task_id AND submission.round_id = NEW.id
       AND submission.submitted_by_user_id = NEW.submitted_by_user_id
       AND submission.submitted_at = NEW.submitted_at
       AND submission.round_manifest_sha256 = NEW.count_manifest_sha256
 )
BEGIN
    SELECT RAISE(ABORT,
        'submitted stocktake round requires its immutable submission manifest');
END
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_stocktake_differences_observed_line_insert_0011
BEFORE INSERT ON stocktake_differences
WHEN NEW.observed_line_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM stocktake_count_observations AS observation
     WHERE observation.id = NEW.observed_line_id
       AND observation.task_id = NEW.task_id
       AND observation.round_id = NEW.round_id
       AND observation.scope_id = NEW.scope_id
       AND observation.material_id IS NEW.material_id
       AND observation.serial_id IS NEW.serial_id
       AND (NEW.difference_type <> 'excess' OR (
           NEW.book_qty = 0
           AND NEW.counted_qty = observation.counted_qty
           AND NEW.difference_qty = observation.counted_qty
           AND NEW.affected_qty = observation.counted_qty
       ))
)
BEGIN
    SELECT RAISE(ABORT, 'stocktake difference observed line binding is invalid');
END
"""
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0011 downgrade requires an online connection for fail-closed evidence checks"
        )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_count_observations, "
            "stocktake_scope_count_completions, stocktake_round_submissions, "
            "stocktake_differences, stocktake_rounds, stocktake_count_lines, "
            "stocktake_count_serials IN ACCESS EXCLUSIVE MODE"
        )
    for table_name in NEW_TABLES:
        table = sa.table(table_name, sa.column("id"))
        if bind.execute(
            sa.select(sa.literal(1)).select_from(table).limit(1)
        ).first() is not None:
            raise RuntimeError(
                f"cannot downgrade 0011: immutable evidence table {table_name} "
                "contains business data"
            )
    difference = sa.table(
        "stocktake_differences", sa.column("observed_line_id", sa.Uuid())
    )
    if bind.execute(
        sa.select(sa.literal(1))
        .select_from(difference)
        .where(difference.c.observed_line_id.is_not(None))
        .limit(1)
    ).first() is not None:
        raise RuntimeError(
            "cannot downgrade 0011: stocktake difference references an observation"
        )

    _drop_count_contract_triggers()
    op.drop_index(
        "ix_stocktake_differences_observed_line",
        table_name="stocktake_differences",
    )
    if _dialect_name() == "postgresql":
        op.drop_constraint(
            "fk_stocktake_differences_observed_line",
            "stocktake_differences",
            type_="foreignkey",
        )
        op.drop_constraint(
            "ck_stocktake_differences_binding",
            "stocktake_differences",
            type_="check",
        )
        op.drop_column("stocktake_differences", "observed_line_id")
        op.create_check_constraint(
            "ck_stocktake_differences_binding",
            "stocktake_differences",
            ORIGINAL_DIFFERENCE_BINDING_CHECK,
        )
    else:
        with op.batch_alter_table(
            "stocktake_differences", recreate="always"
        ) as batch_op:
            batch_op.drop_constraint(
                "fk_stocktake_differences_observed_line", type_="foreignkey"
            )
            batch_op.drop_constraint(
                "ck_stocktake_differences_binding", type_="check"
            )
            batch_op.drop_column("observed_line_id")
            batch_op.create_check_constraint(
                "ck_stocktake_differences_binding",
                ORIGINAL_DIFFERENCE_BINDING_CHECK,
            )
        _recreate_sqlite_difference_0010_triggers()

    for table_name in NEW_TABLES:
        op.drop_table(table_name)


def _drop_count_contract_triggers() -> None:
    if _dialect_name() == "postgresql":
        op.execute(
            "DROP TRIGGER trg_stocktake_differences_observed_line_insert_0011 "
            "ON stocktake_differences"
        )
        op.execute(
            "DROP TRIGGER trg_stocktake_rounds_submission_manifest_update_0011 "
            "ON stocktake_rounds"
        )
        op.execute(
            "DROP TRIGGER trg_stocktake_round_submissions_validate_insert_0011 "
            "ON stocktake_round_submissions"
        )
        op.execute(
            "DROP TRIGGER "
            "trg_stocktake_scope_count_completions_validate_insert_0011 "
            "ON stocktake_scope_count_completions"
        )
        for table_name in ("stocktake_count_lines", "stocktake_count_serials"):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_completion_seal_0011 "
                f"ON {table_name}"
            )
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_0011 ON {table_name}"
            )
        op.execute(
            "DROP TRIGGER trg_stocktake_count_observations_validate_insert_0011 "
            "ON stocktake_count_observations"
        )
        for table_name in NEW_TABLES:
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_0011 ON {table_name}"
            )
        op.execute("DROP FUNCTION rsc_validate_stocktake_observed_difference_0011()")
        op.execute("DROP FUNCTION rsc_require_stocktake_round_submission_0011()")
        op.execute(
            "DROP FUNCTION rsc_validate_stocktake_round_submission_insert_0011()"
        )
        op.execute(
            "DROP FUNCTION rsc_validate_stocktake_scope_completion_insert_0011()"
        )
        op.execute(
            "DROP FUNCTION rsc_validate_stocktake_existing_count_child_0011()"
        )
        op.execute(
            "DROP FUNCTION rsc_validate_stocktake_observation_insert_0011()"
        )
        op.execute("DROP FUNCTION rsc_block_opening_count_fact_mutation_0011()")
        op.execute(
            "DROP FUNCTION rsc_stocktake_actor_assignment_valid_0011("
            "text, uuid, uuid, bigint, timestamptz, text, text, text)"
        )
        return

    for trigger_name in (
        "trg_stocktake_differences_observed_line_insert_0011",
        "trg_stocktake_rounds_submission_manifest_insert_0011",
        "trg_stocktake_rounds_submission_manifest_update_0011",
        "trg_stocktake_round_submissions_validate_insert_0011",
        "trg_stocktake_scope_count_completions_validate_insert_0011",
        "trg_stocktake_count_lines_completion_seal_insert_0011",
        "trg_stocktake_count_serials_completion_seal_insert_0011",
        "trg_stocktake_count_observations_validate_insert_0011",
        "trg_stocktake_differences_chronology_insert_0011",
        "trg_stocktake_differences_immutable_update_0011",
        "trg_stocktake_differences_immutable_delete_0011",
        "trg_stocktake_posting_items_validate_insert_0010",
    ):
        op.execute(f"DROP TRIGGER {trigger_name}")
    for table_name in (
        *NEW_TABLES,
        "stocktake_count_lines",
        "stocktake_count_serials",
    ):
        for operation in ("update", "delete"):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_{operation}_0011"
            )


def _recreate_sqlite_difference_0010_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER trg_stocktake_differences_immutable_"
            f"{operation.lower()}_0010 BEFORE {operation} ON stocktake_differences "
            "BEGIN SELECT RAISE(ABORT, 'formal stocktake fact rows are immutable'); "
            "END"
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
    _recreate_sqlite_posting_item_0010_trigger()


def _recreate_sqlite_posting_item_0010_trigger() -> None:
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
