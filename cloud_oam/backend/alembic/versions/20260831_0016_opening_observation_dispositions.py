"""Seal opening-count manifests, pending-observation handling and differences.

Revision ID: 20260831_0016
Revises: 20260831_0015
Create Date: 2026-08-31

The round submission manifest introduced in 0011 accidentally compared the
completion/observation manifest with the count-line/SN posting manifest.  They
are independent evidence sets.  This revision gives the immutable submission
both hashes, records append-only dispositions for unresolved physical
observations, and seals the complete difference set before either review may
start.  None of these facts creates a stock account, balance, movement or
ledger entry, and no OAM control quantity is interpreted as physical stock.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260831_0016"
down_revision: Union[str, Sequence[str], None] = "20260831_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SUBMISSION_TABLE = "stocktake_round_submissions"
DISPOSITION_TABLE = "stocktake_observation_dispositions"
DIFFERENCE_COMPLETION_TABLE = "stocktake_difference_set_completions"
PRODUCTION_API_ROLE = "star_oam_api"
UPGRADE_BLOCKER = (
    "0016 preflight failed: existing stocktake submission, review or "
    "difference evidence cannot be safely inferred"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0016: persisted opening review evidence requires "
    "independent manifests and sealed difference facts"
)

PG_ROUND_FUNCTION = "rsc_require_stocktake_round_submission_0016"
PG_ROUND_TRIGGER = "trg_stocktake_rounds_submission_manifest_0016"
PG_IMMUTABLE_FUNCTION = "rsc_block_stocktake_review_fact_mutation_0016"
PG_DISPOSITION_FUNCTION = "rsc_validate_stocktake_observation_disposition_0016"
PG_DISPOSITION_TRIGGER = "trg_stocktake_observation_dispositions_validate_0016"
PG_DIFFERENCE_COMPLETION_FUNCTION = (
    "rsc_validate_stocktake_difference_set_completion_0016"
)
PG_DIFFERENCE_COMPLETION_TRIGGER = (
    "trg_stocktake_difference_set_completions_validate_0016"
)
PG_DIFFERENCE_SEAL_FUNCTION = "rsc_block_stocktake_difference_after_seal_0016"
PG_DIFFERENCE_SEAL_TRIGGER = "trg_stocktake_differences_completion_seal_0016"
PG_REVIEW_FUNCTION = "rsc_require_stocktake_difference_completion_0016"
PG_REVIEW_TRIGGER = "trg_stocktake_reviews_difference_completion_0016"

SQLITE_ROUND_INSERT_TRIGGER = (
    "trg_stocktake_rounds_submission_manifest_insert_0016"
)
SQLITE_ROUND_UPDATE_TRIGGER = (
    "trg_stocktake_rounds_submission_manifest_update_0016"
)
SQLITE_DISPOSITION_TRIGGER = (
    "trg_stocktake_observation_dispositions_validate_insert_0016"
)
SQLITE_DIFFERENCE_COMPLETION_TRIGGER = (
    "trg_stocktake_difference_set_completions_validate_insert_0016"
)
SQLITE_DIFFERENCE_SEAL_TRIGGER = (
    "trg_stocktake_differences_completion_seal_insert_0016"
)
SQLITE_REVIEW_TRIGGER = "trg_stocktake_reviews_difference_completion_insert_0016"


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0016 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0016 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
    _assert_no_existing_review_evidence(dialect)

    op.add_column(
        SUBMISSION_TABLE,
        sa.Column(
            "count_manifest_sha256",
            sa.String(length=64),
            sa.CheckConstraint(
                "length(count_manifest_sha256) = 64",
                name="ck_stocktake_round_submissions_count_manifest_sha256",
            ),
            nullable=False,
        ),
    )
    _create_tables()
    _replace_round_submission_guard(dialect, target_revision="0016")
    if dialect == "postgresql":
        _create_postgresql_guards()
        _apply_postgresql_acl()
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0016 downgrade requires an online connection for fail-closed "
            "review-evidence checks"
        )
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        op.get_bind().exec_driver_sql(
            "LOCK TABLE stocktake_round_submissions, stocktake_reviews, "
            "stocktake_differences, stocktake_observation_dispositions, "
            "stocktake_difference_set_completions, stocktake_rounds "
            "IN ACCESS EXCLUSIVE MODE"
        )
    if op.get_bind().exec_driver_sql(
        "SELECT 1 WHERE "
        "EXISTS (SELECT 1 FROM stocktake_round_submissions) OR "
        "EXISTS (SELECT 1 FROM stocktake_reviews) OR "
        "EXISTS (SELECT 1 FROM stocktake_differences) OR "
        "EXISTS (SELECT 1 FROM stocktake_observation_dispositions) OR "
        "EXISTS (SELECT 1 FROM stocktake_difference_set_completions)"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    _drop_guards(dialect)
    op.drop_table(DIFFERENCE_COMPLETION_TABLE)
    op.drop_table(DISPOSITION_TABLE)
    _replace_round_submission_guard(dialect, target_revision="0011")
    op.drop_column(SUBMISSION_TABLE, "count_manifest_sha256")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _assert_no_existing_review_evidence(dialect: str) -> None:
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0016 SQLite upgrade requires an online connection")
        op.execute(
            "LOCK TABLE stocktake_round_submissions, stocktake_reviews, "
            "stocktake_differences IN ACCESS EXCLUSIVE MODE"
        )
        op.execute(
            f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM stocktake_round_submissions)
       OR EXISTS (SELECT 1 FROM stocktake_reviews)
       OR EXISTS (SELECT 1 FROM stocktake_differences) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
        )
        return

    bind = op.get_bind()
    if dialect == "postgresql":
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_round_submissions, stocktake_reviews, "
            "stocktake_differences IN ACCESS EXCLUSIVE MODE"
        )
    if bind.exec_driver_sql(
        "SELECT 1 WHERE "
        "EXISTS (SELECT 1 FROM stocktake_round_submissions) OR "
        "EXISTS (SELECT 1 FROM stocktake_reviews) OR "
        "EXISTS (SELECT 1 FROM stocktake_differences)"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _create_tables() -> None:
    op.create_table(
        DISPOSITION_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("resolved_material_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_lot_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_serial_id", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("disposition_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("decided_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("decided_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("role_code", sa.String(length=40), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("scope_id_snapshot", sa.String(length=80), nullable=False),
        sa.Column("authorization_sha256", sa.String(length=64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_observation_dispositions"),
        sa.UniqueConstraint(
            "observation_id",
            name="uq_stocktake_observation_dispositions_observation",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_observation_dispositions_idempotency",
        ),
        sa.UniqueConstraint(
            "id",
            "task_id",
            "round_id",
            name="uq_stocktake_observation_dispositions_id_task_round",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "task_id", "round_id", "scope_id"],
            [
                "stocktake_count_observations.id",
                "stocktake_count_observations.task_id",
                "stocktake_count_observations.round_id",
                "stocktake_count_observations.scope_id",
            ],
            name="fk_stocktake_observation_dispositions_observation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_lot_id", "resolved_material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            name="fk_stocktake_observation_dispositions_lot_material",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_material_id"],
            ["materials.id"],
            name="fk_stocktake_observation_dispositions_material",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_serial_id"],
            ["inventory_serials.id"],
            name="fk_stocktake_observation_dispositions_serial",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name="fk_stocktake_observation_dispositions_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_person_id"],
            ["people.id"],
            name="fk_stocktake_observation_dispositions_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_role_assignment_id"],
            ["role_assignments.id"],
            name="fk_stocktake_observation_dispositions_assignment",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "disposition IN ('resolved_existing_master', "
            "'pending_verification', 'requires_recount')",
            name="ck_stocktake_observation_dispositions_disposition",
        ),
        sa.CheckConstraint(
            "(disposition = 'resolved_existing_master' AND "
            "resolved_material_id IS NOT NULL) OR "
            "(disposition IN ('pending_verification', 'requires_recount') AND "
            "resolved_material_id IS NULL AND resolved_lot_id IS NULL AND "
            "resolved_serial_id IS NULL)",
            name="ck_stocktake_observation_dispositions_resolution",
        ),
        sa.CheckConstraint(
            "length(trim(reason_code)) > 0 AND "
            "(disposition = 'resolved_existing_master' OR "
            "length(trim(comment)) > 0)",
            name="ck_stocktake_observation_dispositions_reason",
        ),
        sa.CheckConstraint(
            "length(disposition_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64 AND "
            "length(authorization_sha256) = 64",
            name="ck_stocktake_observation_dispositions_hashes",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_observation_dispositions_authorization_version",
        ),
        sa.CheckConstraint(
            "(role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*') OR "
            "(role_code = 'provincial_manager' AND "
            "scope_type = 'organization' AND "
            "length(trim(scope_id_snapshot)) > 0)",
            name="ck_stocktake_observation_dispositions_authorization_snapshot",
        ),
        sa.CheckConstraint(
            "disposition <> 'resolved_existing_master' OR "
            "(role_code = 'admin' AND scope_type = 'national' AND "
            "scope_id_snapshot = '*')",
            name="ck_stocktake_observation_dispositions_resolution_role",
        ),
        sa.CheckConstraint(
            "created_at = decided_at",
            name="ck_stocktake_observation_dispositions_chronology",
        ),
    )
    op.create_index(
        "ix_stocktake_observation_dispositions_round",
        DISPOSITION_TABLE,
        ["task_id", "round_id"],
    )
    op.create_index(
        "ix_stocktake_observation_dispositions_status",
        DISPOSITION_TABLE,
        ["disposition", "decided_at"],
    )

    op.create_table(
        DIFFERENCE_COMPLETION_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("round_submission_id", sa.Uuid(), nullable=False),
        sa.Column("difference_count", sa.Integer(), nullable=False),
        sa.Column("physical_difference_count", sa.Integer(), nullable=False),
        sa.Column("control_difference_count", sa.Integer(), nullable=False),
        sa.Column(
            "pending_observation_difference_count", sa.Integer(), nullable=False
        ),
        sa.Column("total_affected_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("difference_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("completed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("completed_by_person_id", sa.Uuid(), nullable=False),
        sa.Column("completed_role_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "id", name="pk_stocktake_difference_set_completions"
        ),
        sa.UniqueConstraint(
            "task_id",
            "round_id",
            name="uq_stocktake_difference_set_completions_round",
        ),
        sa.UniqueConstraint(
            "round_submission_id",
            name="uq_stocktake_difference_set_completions_submission",
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_stocktake_difference_set_completions_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_stocktake_difference_set_completions_round_task",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["round_submission_id"],
            ["stocktake_round_submissions.id"],
            name="fk_stocktake_difference_set_completions_submission",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"],
            ["users.id"],
            name="fk_stocktake_difference_set_completions_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_person_id"],
            ["people.id"],
            name="fk_stocktake_difference_set_completions_person",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completed_role_assignment_id"],
            ["role_assignments.id"],
            name="fk_stocktake_difference_set_completions_assignment",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "difference_count >= 0 AND physical_difference_count >= 0 AND "
            "control_difference_count >= 0 AND "
            "physical_difference_count + control_difference_count = "
            "difference_count AND pending_observation_difference_count >= 0 "
            "AND pending_observation_difference_count <= "
            "physical_difference_count AND total_affected_qty >= 0",
            name="ck_stocktake_difference_set_completions_totals",
        ),
        sa.CheckConstraint(
            "length(difference_manifest_sha256) = 64 AND "
            "length(request_sha256) = 64 AND "
            "length(idempotency_key_hash) = 64",
            name="ck_stocktake_difference_set_completions_hashes",
        ),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stocktake_difference_set_completions_authorization_version",
        ),
        sa.CheckConstraint(
            "created_at = completed_at",
            name="ck_stocktake_difference_set_completions_chronology",
        ),
    )
    op.create_index(
        "ix_stocktake_difference_set_completions_task",
        DIFFERENCE_COMPLETION_TABLE,
        ["task_id", "round_id"],
    )


def _replace_round_submission_guard(dialect: str, *, target_revision: str) -> None:
    if dialect == "postgresql":
        old_revision = "0011" if target_revision == "0016" else "0016"
        old_trigger = (
            "trg_stocktake_rounds_submission_manifest_update_0011"
            if old_revision == "0011"
            else PG_ROUND_TRIGGER
        )
        old_function = (
            "rsc_require_stocktake_round_submission_0011"
            if old_revision == "0011"
            else PG_ROUND_FUNCTION
        )
        op.execute(f"DROP TRIGGER {old_trigger} ON stocktake_rounds")
        op.execute(f"DROP FUNCTION {old_function}()")
        function_name = (
            PG_ROUND_FUNCTION
            if target_revision == "0016"
            else "rsc_require_stocktake_round_submission_0011"
        )
        manifest_column = (
            "count_manifest_sha256"
            if target_revision == "0016"
            else "round_manifest_sha256"
        )
        op.execute(
            f"""
CREATE FUNCTION {function_name}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status = 'submitted' AND TG_OP = 'INSERT' THEN
        RAISE EXCEPTION
            'submitted stocktake round requires its immutable submission manifest';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.status = 'submitted'
       AND OLD.status = 'counting' AND NOT EXISTS (
        SELECT 1 FROM stocktake_round_submissions AS submission
         WHERE submission.task_id = NEW.task_id
           AND submission.round_id = NEW.id
           AND submission.submitted_by_user_id = NEW.submitted_by_user_id
           AND submission.submitted_at = NEW.submitted_at
           AND submission.{manifest_column} = NEW.count_manifest_sha256
       ) THEN
        RAISE EXCEPTION
            'submitted stocktake round requires its immutable submission manifest';
    END IF;
    RETURN NEW;
END;
$$
"""
        )
        trigger_name = (
            PG_ROUND_TRIGGER
            if target_revision == "0016"
            else "trg_stocktake_rounds_submission_manifest_update_0011"
        )
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT OR UPDATE ON "
            f"stocktake_rounds FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )
        if target_revision == "0011":
            _revoke_function(function_name)
        return

    for trigger_name in (
        "trg_stocktake_rounds_submission_manifest_insert_0011",
        "trg_stocktake_rounds_submission_manifest_update_0011",
        SQLITE_ROUND_INSERT_TRIGGER,
        SQLITE_ROUND_UPDATE_TRIGGER,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    suffix = target_revision
    manifest_column = (
        "count_manifest_sha256"
        if target_revision == "0016"
        else "round_manifest_sha256"
    )
    op.execute(
        f"""
CREATE TRIGGER trg_stocktake_rounds_submission_manifest_insert_{suffix}
BEFORE INSERT ON stocktake_rounds
WHEN NEW.status = 'submitted'
BEGIN
    SELECT RAISE(ABORT,
        'submitted stocktake round requires its immutable submission manifest');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER trg_stocktake_rounds_submission_manifest_update_{suffix}
BEFORE UPDATE ON stocktake_rounds
WHEN NEW.status = 'submitted' AND OLD.status = 'counting'
 AND NOT EXISTS (
    SELECT 1 FROM stocktake_round_submissions AS submission
     WHERE submission.task_id = NEW.task_id AND submission.round_id = NEW.id
       AND submission.submitted_by_user_id = NEW.submitted_by_user_id
       AND submission.submitted_at = NEW.submitted_at
       AND submission.{manifest_column} = NEW.count_manifest_sha256
 )
BEGIN
    SELECT RAISE(ABORT,
        'submitted stocktake round requires its immutable submission manifest');
END
"""
    )


def _create_postgresql_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION {PG_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'opening review evidence is immutable' USING ERRCODE = '55000';
END;
$$
"""
    )
    for table_name in (DISPOSITION_TABLE, DIFFERENCE_COMPLETION_TABLE):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_0016 BEFORE UPDATE OR "
            f"DELETE ON {table_name} FOR EACH ROW EXECUTE FUNCTION "
            f"{PG_IMMUTABLE_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable_truncate_0016 BEFORE "
            f"TRUNCATE ON {table_name} FOR EACH STATEMENT EXECUTE FUNCTION "
            f"{PG_IMMUTABLE_FUNCTION}()"
        )

    op.execute(_postgresql_disposition_function_sql())
    op.execute(
        f"CREATE TRIGGER {PG_DISPOSITION_TRIGGER} BEFORE INSERT ON "
        f"{DISPOSITION_TABLE} FOR EACH ROW EXECUTE FUNCTION "
        f"{PG_DISPOSITION_FUNCTION}()"
    )
    op.execute(_postgresql_difference_completion_function_sql())
    op.execute(
        f"CREATE TRIGGER {PG_DIFFERENCE_COMPLETION_TRIGGER} BEFORE INSERT ON "
        f"{DIFFERENCE_COMPLETION_TABLE} FOR EACH ROW EXECUTE FUNCTION "
        f"{PG_DIFFERENCE_COMPLETION_FUNCTION}()"
    )
    op.execute(
        f"""
CREATE FUNCTION {PG_DIFFERENCE_SEAL_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM stocktake_difference_set_completions
         WHERE task_id = NEW.task_id AND round_id = NEW.round_id
    ) THEN
        RAISE EXCEPTION 'stocktake difference set is already sealed';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {PG_DIFFERENCE_SEAL_TRIGGER} BEFORE INSERT ON "
        f"stocktake_differences FOR EACH ROW EXECUTE FUNCTION "
        f"{PG_DIFFERENCE_SEAL_FUNCTION}()"
    )
    op.execute(_postgresql_review_function_sql())
    op.execute(
        f"CREATE TRIGGER {PG_REVIEW_TRIGGER} BEFORE INSERT ON stocktake_reviews "
        f"FOR EACH ROW EXECUTE FUNCTION {PG_REVIEW_FUNCTION}()"
    )

    trigger_names = [PG_ROUND_TRIGGER, PG_DISPOSITION_TRIGGER,
                     PG_DIFFERENCE_COMPLETION_TRIGGER,
                     PG_DIFFERENCE_SEAL_TRIGGER, PG_REVIEW_TRIGGER]
    trigger_tables = ["stocktake_rounds", DISPOSITION_TABLE,
                      DIFFERENCE_COMPLETION_TABLE, "stocktake_differences",
                      "stocktake_reviews"]
    for table_name in (DISPOSITION_TABLE, DIFFERENCE_COMPLETION_TABLE):
        trigger_names.extend(
            [
                f"trg_{table_name}_immutable_0016",
                f"trg_{table_name}_immutable_truncate_0016",
            ]
        )
        trigger_tables.extend([table_name, table_name])
    for table_name, trigger_name in zip(trigger_tables, trigger_names):
        op.execute(f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER {trigger_name}")


def _postgresql_disposition_function_sql() -> str:
    return f"""
CREATE FUNCTION {PG_DISPOSITION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cutoff_at_value timestamptz;
    observation_row stocktake_count_observations%ROWTYPE;
BEGIN
    SELECT observation.*
      INTO observation_row
      FROM stocktake_count_observations AS observation
     WHERE observation.id = NEW.observation_id
       AND observation.task_id = NEW.task_id
       AND observation.round_id = NEW.round_id
       AND observation.scope_id = NEW.scope_id;
    SELECT task.cutoff_at
      INTO cutoff_at_value
      FROM stocktake_tasks AS task
      JOIN stocktake_rounds AS round_row ON round_row.task_id = task.id
     WHERE task.id = NEW.task_id AND task.task_type = 'opening'
       AND round_row.id = NEW.round_id AND round_row.status = 'submitted'
       AND round_row.submitted_at IS NOT NULL
       AND NEW.decided_at >= round_row.submitted_at
       AND EXISTS (
           SELECT 1 FROM stocktake_round_submissions AS submission
            WHERE submission.task_id = NEW.task_id
              AND submission.round_id = NEW.round_id
       )
     FOR UPDATE OF round_row;
    IF observation_row.id IS NULL
       OR observation_row.verification_status <> 'pending_verification'
       OR cutoff_at_value IS NULL
       OR EXISTS (SELECT 1 FROM stocktake_reviews
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
       OR EXISTS (SELECT 1 FROM stocktake_postings
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
       OR EXISTS (SELECT 1 FROM inventory_opening_establishments
                   WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
       OR NOT EXISTS (
           SELECT 1 FROM stocktake_differences
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND scope_id = NEW.scope_id
              AND observed_line_id = NEW.observation_id
       )
       OR NOT rsc_stocktake_actor_assignment_valid_0011(
           NEW.decided_by_user_id, NEW.decided_by_person_id,
           NEW.decided_role_assignment_id, NEW.authorization_version,
           NEW.decided_at, NEW.role_code, NEW.scope_type,
           NEW.scope_id_snapshot
       )
       OR NOT (
           (NEW.role_code = 'admin' AND NEW.scope_type = 'national'
            AND NEW.scope_id_snapshot = '*')
           OR (NEW.role_code = 'provincial_manager'
               AND NEW.scope_type = 'organization'
               AND NEW.scope_id_snapshot = observation_row.owner_org_id::text)
       ) THEN
        RAISE EXCEPTION 'stocktake observation disposition is not canonical';
    END IF;

    IF NEW.disposition = 'resolved_existing_master' AND (
       NOT EXISTS (
           SELECT 1 FROM materials AS material
            WHERE material.id = NEW.resolved_material_id
              AND material.status = 'active'
       )
       OR NOT (
           (observation_row.material_id IS NOT NULL
            AND observation_row.material_id = NEW.resolved_material_id)
           OR (observation_row.material_id IS NULL
               AND observation_row.material_identifier_type = 'sku_code'
               AND 1 = (SELECT count(*) FROM materials AS material
                         WHERE material.id = NEW.resolved_material_id
                           AND material.status = 'active'
                           AND material.sku_code =
                               observation_row.material_identifier_raw))
           OR (observation_row.material_id IS NULL
               AND observation_row.material_identifier_type = 'qr_code'
               AND 1 = (SELECT count(*) FROM qr_codes AS mapping
                         WHERE mapping.code = observation_row.material_identifier_raw
                           AND mapping.object_type = 'material'
                           AND mapping.object_id = NEW.resolved_material_id
                           AND mapping.status = 'active'))
       )
       OR NOT (
           (observation_row.lot_no_raw IS NULL AND NEW.resolved_lot_id IS NULL)
           OR (observation_row.lot_no_raw IS NOT NULL
               AND EXISTS (SELECT 1 FROM inventory_lots AS lot
                            WHERE lot.id = NEW.resolved_lot_id
                              AND lot.material_id = NEW.resolved_material_id
                              AND lot.lot_no = observation_row.lot_no_raw))
       )
       OR NOT (
           (observation_row.serial_no_raw IS NULL
            AND NEW.resolved_serial_id IS NULL)
           OR (observation_row.serial_identifier_type = 'serial_no'
               AND EXISTS (
                   SELECT 1 FROM inventory_serials AS serial
                    WHERE serial.id = NEW.resolved_serial_id
                      AND serial.material_id = NEW.resolved_material_id
                      AND serial.lot_id IS NOT DISTINCT FROM NEW.resolved_lot_id
                      AND serial.serial_no = observation_row.serial_no_raw
                      AND serial.lifecycle_status = 'active'
               ))
           OR (observation_row.serial_identifier_type = 'qr_code'
               AND EXISTS (
                   SELECT 1 FROM inventory_serials AS serial
                    WHERE serial.id = NEW.resolved_serial_id
                      AND serial.material_id = NEW.resolved_material_id
                      AND serial.lot_id IS NOT DISTINCT FROM NEW.resolved_lot_id
                      AND serial.qr_code = observation_row.serial_no_raw
                      AND serial.lifecycle_status = 'active'
               )
               AND 1 = (SELECT count(*) FROM qr_codes AS mapping
                         WHERE mapping.code = observation_row.serial_no_raw
                           AND mapping.object_type = 'serial'
                           AND mapping.object_id = NEW.resolved_serial_id
                           AND mapping.status = 'active'))
       )
       OR (SELECT count(*) FROM material_inventory_policies AS policy
            WHERE policy.material_id = NEW.resolved_material_id
              AND policy.effective_from <= cutoff_at_value
              AND (policy.effective_to IS NULL OR
                   cutoff_at_value < policy.effective_to)) <> 1
       OR EXISTS (
           SELECT 1 FROM material_inventory_policies AS policy
            WHERE policy.material_id = NEW.resolved_material_id
              AND policy.effective_from <= cutoff_at_value
              AND (policy.effective_to IS NULL OR
                   cutoff_at_value < policy.effective_to)
              AND (
                  observation_row.counted_qty <>
                      round(observation_row.counted_qty, policy.quantity_scale)
                  OR (NOT policy.allow_fraction AND
                      observation_row.counted_qty <>
                          round(observation_row.counted_qty, 0))
                  OR (policy.tracking_mode = 'none' AND
                      (NEW.resolved_lot_id IS NOT NULL OR
                       NEW.resolved_serial_id IS NOT NULL))
                  OR (policy.tracking_mode = 'lot' AND
                      (NEW.resolved_lot_id IS NULL OR
                       NEW.resolved_serial_id IS NOT NULL))
                  OR (policy.tracking_mode = 'serial' AND
                      (NEW.resolved_lot_id IS NOT NULL OR
                       NEW.resolved_serial_id IS NULL OR
                       observation_row.counted_qty <> 1))
                  OR (policy.tracking_mode = 'lot_and_serial' AND
                      (NEW.resolved_lot_id IS NULL OR
                       NEW.resolved_serial_id IS NULL OR
                       observation_row.counted_qty <> 1))
              )
       )
       OR EXISTS (
           SELECT 1 FROM stock_accounts AS account
            WHERE account.owner_org_id = observation_row.owner_org_id
              AND account.location_id = observation_row.location_id
              AND account.custodian_person_id IS NOT DISTINCT FROM
                  observation_row.custodian_person_id_snapshot
              AND account.material_id = NEW.resolved_material_id
              AND account.condition_code = observation_row.condition_code
              AND account.availability_bucket = observation_row.availability_bucket
              AND account.lot_id IS NOT DISTINCT FROM NEW.resolved_lot_id
              AND account.created_at <= cutoff_at_value
       )
       OR EXISTS (
           SELECT 1 FROM stocktake_snapshot_lines AS snapshot
           JOIN stock_accounts AS account ON account.id = snapshot.stock_account_id
            WHERE snapshot.task_id = NEW.task_id
              AND snapshot.scope_id = NEW.scope_id
              AND account.owner_org_id = observation_row.owner_org_id
              AND account.location_id = observation_row.location_id
              AND account.custodian_person_id IS NOT DISTINCT FROM
                  observation_row.custodian_person_id_snapshot
              AND account.material_id = NEW.resolved_material_id
              AND account.condition_code = observation_row.condition_code
              AND account.availability_bucket = observation_row.availability_bucket
              AND account.lot_id IS NOT DISTINCT FROM NEW.resolved_lot_id
       )
    ) THEN
        RAISE EXCEPTION 'stocktake observation resolution is not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_difference_completion_function_sql() -> str:
    return f"""
CREATE FUNCTION {PG_DIFFERENCE_COMPLETION_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    actual_count integer;
    actual_physical integer;
    actual_control integer;
    actual_pending integer;
    actual_total numeric(18, 3);
    actual_min integer;
    actual_max integer;
BEGIN
    PERFORM 1 FROM stocktake_rounds AS round_row
     WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
       AND round_row.status = 'submitted'
     FOR UPDATE;
    IF NOT FOUND OR NOT EXISTS (
        SELECT 1
          FROM stocktake_round_submissions AS submission
          JOIN stocktake_scope_count_completions AS sealing
            ON sealing.id = submission.sealing_completion_id
         WHERE submission.id = NEW.round_submission_id
           AND submission.task_id = NEW.task_id
           AND submission.round_id = NEW.round_id
           AND submission.submitted_by_user_id = NEW.completed_by_user_id
           AND submission.submitted_by_person_id = NEW.completed_by_person_id
           AND submission.submitted_role_assignment_id =
               NEW.completed_role_assignment_id
           AND submission.authorization_version = NEW.authorization_version
           AND submission.submitted_at = NEW.completed_at
           AND sealing.task_id = NEW.task_id
           AND sealing.round_id = NEW.round_id
           AND sealing.completed_by_user_id = NEW.completed_by_user_id
           AND sealing.completed_by_person_id = NEW.completed_by_person_id
           AND sealing.completed_role_assignment_id =
               NEW.completed_role_assignment_id
           AND sealing.authorization_version = NEW.authorization_version
           AND sealing.completed_at = NEW.completed_at
    ) OR EXISTS (SELECT 1 FROM stocktake_reviews
                  WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
      OR EXISTS (SELECT 1 FROM stocktake_postings
                  WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
      OR EXISTS (SELECT 1 FROM inventory_opening_establishments
                  WHERE task_id = NEW.task_id AND round_id = NEW.round_id) THEN
        RAISE EXCEPTION 'stocktake difference completion is not canonical';
    END IF;

    SELECT count(*),
           count(*) FILTER (WHERE difference_type <> 'control_unassigned'),
           count(*) FILTER (WHERE difference_type = 'control_unassigned'),
           count(*) FILTER (WHERE observed_line_id IS NOT NULL AND EXISTS (
               SELECT 1 FROM stocktake_count_observations AS observation
                WHERE observation.id = difference.observed_line_id
                  AND observation.verification_status = 'pending_verification'
           )),
           COALESCE(sum(affected_qty), 0), min(difference_no), max(difference_no)
      INTO actual_count, actual_physical, actual_control, actual_pending,
           actual_total, actual_min, actual_max
      FROM stocktake_differences AS difference
     WHERE difference.task_id = NEW.task_id AND difference.round_id = NEW.round_id;
    IF NEW.difference_count <> actual_count
       OR NEW.physical_difference_count <> actual_physical
       OR NEW.control_difference_count <> actual_control
       OR NEW.pending_observation_difference_count <> actual_pending
       OR NEW.total_affected_qty <> actual_total
       OR (actual_count > 0 AND (actual_min <> 1 OR actual_max <> actual_count)) THEN
        RAISE EXCEPTION 'stocktake difference completion totals are not canonical';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_review_function_sql() -> str:
    return f"""
CREATE FUNCTION {PG_REVIEW_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM stocktake_difference_set_completions AS completion
         WHERE completion.task_id = NEW.task_id
           AND completion.round_id = NEW.round_id
           AND completion.completed_at <= NEW.reviewed_at
    ) OR EXISTS (
        SELECT 1 FROM stocktake_count_observations AS observation
         WHERE observation.task_id = NEW.task_id
           AND observation.round_id = NEW.round_id
           AND observation.verification_status = 'pending_verification'
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_observation_dispositions AS disposition
                WHERE disposition.observation_id = observation.id
                  AND disposition.task_id = NEW.task_id
                  AND disposition.round_id = NEW.round_id
                  AND disposition.decided_at <= NEW.reviewed_at
           )
    ) THEN
        RAISE EXCEPTION
            'stocktake review requires sealed differences and observation dispositions';
    END IF;
    RETURN NEW;
END;
$$
"""


def _create_sqlite_guards() -> None:
    for table_name in (DISPOSITION_TABLE, DIFFERENCE_COMPLETION_TABLE):
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable_{operation.lower()}_0016 "
                f"BEFORE {operation} ON {table_name} BEGIN SELECT RAISE(ABORT, "
                "'opening review evidence is immutable'); END"
            )
    op.execute(_sqlite_disposition_trigger_sql())
    op.execute(_sqlite_difference_completion_trigger_sql())
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_DIFFERENCE_SEAL_TRIGGER}
BEFORE INSERT ON stocktake_differences
WHEN EXISTS (
    SELECT 1 FROM stocktake_difference_set_completions
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
)
BEGIN
    SELECT RAISE(ABORT, 'stocktake difference set is already sealed');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_REVIEW_TRIGGER}
BEFORE INSERT ON stocktake_reviews
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_difference_set_completions AS completion
         WHERE completion.task_id = NEW.task_id
           AND completion.round_id = NEW.round_id
           AND completion.completed_at <= NEW.reviewed_at
     )
 OR EXISTS (
        SELECT 1 FROM stocktake_count_observations AS observation
         WHERE observation.task_id = NEW.task_id
           AND observation.round_id = NEW.round_id
           AND observation.verification_status = 'pending_verification'
           AND NOT EXISTS (
               SELECT 1 FROM stocktake_observation_dispositions AS disposition
                WHERE disposition.observation_id = observation.id
                  AND disposition.task_id = NEW.task_id
                  AND disposition.round_id = NEW.round_id
                  AND disposition.decided_at <= NEW.reviewed_at
           )
     )
BEGIN
    SELECT RAISE(ABORT,
        'stocktake review requires sealed differences and observation dispositions');
END
"""
    )


def _sqlite_disposition_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_DISPOSITION_TRIGGER}
BEFORE INSERT ON stocktake_observation_dispositions
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_count_observations AS observation
         WHERE observation.id = NEW.observation_id
           AND observation.task_id = NEW.task_id
           AND observation.round_id = NEW.round_id
           AND observation.scope_id = NEW.scope_id
           AND observation.verification_status = 'pending_verification'
     )
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_rounds AS round_row
        JOIN stocktake_tasks AS task ON task.id = round_row.task_id
         WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
           AND task.task_type = 'opening' AND task.cutoff_at IS NOT NULL
           AND round_row.status = 'submitted'
           AND round_row.submitted_at IS NOT NULL
           AND NEW.decided_at >= round_row.submitted_at
     )
 OR NOT EXISTS (SELECT 1 FROM stocktake_round_submissions
                 WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_reviews
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NOT EXISTS (SELECT 1 FROM stocktake_differences
                 WHERE task_id = NEW.task_id AND round_id = NEW.round_id
                   AND scope_id = NEW.scope_id
                   AND observed_line_id = NEW.observation_id)
 OR NOT EXISTS (
        SELECT 1 FROM role_assignments AS assignment
        JOIN roles AS role ON role.id = assignment.role_id
        JOIN users AS actor ON actor.id = assignment.user_id
        JOIN stocktake_count_observations AS observation
          ON observation.id = NEW.observation_id
         AND observation.task_id = NEW.task_id
         AND observation.round_id = NEW.round_id
         AND observation.scope_id = NEW.scope_id
         WHERE assignment.id = NEW.decided_role_assignment_id
           AND assignment.user_id = NEW.decided_by_user_id
           AND actor.person_id = NEW.decided_by_person_id
           AND actor.authorization_version = NEW.authorization_version
           AND assignment.status IN ('active', 'expired', 'revoked')
           AND assignment.valid_from <= NEW.decided_at
           AND (assignment.valid_to IS NULL OR NEW.decided_at < assignment.valid_to)
           AND (assignment.revoked_at IS NULL OR NEW.decided_at < assignment.revoked_at)
           AND role.is_external = 0 AND role.code = NEW.role_code
           AND assignment.scope_type = NEW.scope_type
           AND assignment.scope_id = NEW.scope_id_snapshot
           AND ((NEW.role_code = 'admin' AND NEW.scope_type = 'national'
                 AND NEW.scope_id_snapshot = '*')
                OR (NEW.role_code = 'provincial_manager'
                    AND NEW.scope_type = 'organization'
                    AND replace(NEW.scope_id_snapshot, '-', '') =
                        replace(CAST(observation.owner_org_id AS TEXT), '-', '')))
     )
 OR (NEW.disposition = 'resolved_existing_master' AND (
        NOT EXISTS (SELECT 1 FROM materials
                     WHERE id = NEW.resolved_material_id AND status = 'active')
        OR NOT EXISTS (
            SELECT 1 FROM stocktake_count_observations AS observation
             WHERE observation.id = NEW.observation_id
               AND ((observation.material_id IS NOT NULL
                     AND observation.material_id = NEW.resolved_material_id)
                    OR (observation.material_id IS NULL
                        AND observation.material_identifier_type = 'sku_code'
                        AND (SELECT count(*) FROM materials AS material
                              WHERE material.id = NEW.resolved_material_id
                                AND material.status = 'active'
                                AND material.sku_code =
                                    observation.material_identifier_raw) = 1)
                    OR (observation.material_id IS NULL
                        AND observation.material_identifier_type = 'qr_code'
                        AND (SELECT count(*) FROM qr_codes AS mapping
                              WHERE mapping.code = observation.material_identifier_raw
                                AND mapping.object_type = 'material'
                                AND mapping.object_id = NEW.resolved_material_id
                                AND mapping.status = 'active') = 1))
               AND ((observation.lot_no_raw IS NULL
                     AND NEW.resolved_lot_id IS NULL)
                    OR (observation.lot_no_raw IS NOT NULL AND EXISTS (
                        SELECT 1 FROM inventory_lots AS lot
                         WHERE lot.id = NEW.resolved_lot_id
                           AND lot.material_id = NEW.resolved_material_id
                           AND lot.lot_no = observation.lot_no_raw)))
               AND ((observation.serial_no_raw IS NULL
                     AND NEW.resolved_serial_id IS NULL)
                    OR (observation.serial_identifier_type = 'serial_no'
                        AND EXISTS (SELECT 1 FROM inventory_serials AS serial
                         WHERE serial.id = NEW.resolved_serial_id
                           AND serial.material_id = NEW.resolved_material_id
                           AND serial.lot_id IS NEW.resolved_lot_id
                           AND serial.serial_no = observation.serial_no_raw
                           AND serial.lifecycle_status = 'active'))
                    OR (observation.serial_identifier_type = 'qr_code'
                        AND EXISTS (SELECT 1 FROM inventory_serials AS serial
                         WHERE serial.id = NEW.resolved_serial_id
                           AND serial.material_id = NEW.resolved_material_id
                           AND serial.lot_id IS NEW.resolved_lot_id
                           AND serial.qr_code = observation.serial_no_raw
                           AND serial.lifecycle_status = 'active')
                        AND (SELECT count(*) FROM qr_codes AS mapping
                              WHERE mapping.code = observation.serial_no_raw
                                AND mapping.object_type = 'serial'
                                AND mapping.object_id = NEW.resolved_serial_id
                                AND mapping.status = 'active') = 1))
        )
        OR (SELECT count(*) FROM material_inventory_policies AS policy
             WHERE policy.material_id = NEW.resolved_material_id
               AND policy.effective_from <=
                   (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
               AND (policy.effective_to IS NULL OR
                    (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                    < policy.effective_to)) <> 1
        OR EXISTS (
            SELECT 1 FROM stocktake_count_observations AS observation
            JOIN material_inventory_policies AS policy
              ON policy.material_id = NEW.resolved_material_id
             AND policy.effective_from <=
                 (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
             AND (policy.effective_to IS NULL OR
                  (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                  < policy.effective_to)
             WHERE observation.id = NEW.observation_id
               AND (observation.counted_qty <>
                        round(observation.counted_qty, policy.quantity_scale)
                    OR (policy.allow_fraction = 0 AND
                        observation.counted_qty <> round(observation.counted_qty, 0))
                    OR (policy.tracking_mode = 'none' AND
                        (NEW.resolved_lot_id IS NOT NULL OR
                         NEW.resolved_serial_id IS NOT NULL))
                    OR (policy.tracking_mode = 'lot' AND
                        (NEW.resolved_lot_id IS NULL OR
                         NEW.resolved_serial_id IS NOT NULL))
                    OR (policy.tracking_mode = 'serial' AND
                        (NEW.resolved_lot_id IS NOT NULL OR
                         NEW.resolved_serial_id IS NULL OR
                         observation.counted_qty <> 1))
                    OR (policy.tracking_mode = 'lot_and_serial' AND
                        (NEW.resolved_lot_id IS NULL OR
                         NEW.resolved_serial_id IS NULL OR
                         observation.counted_qty <> 1)))
        )
        OR EXISTS (
            SELECT 1 FROM stocktake_count_observations AS observation
            JOIN stock_accounts AS account
              ON account.owner_org_id = observation.owner_org_id
             AND account.location_id = observation.location_id
             AND account.custodian_person_id IS
                 observation.custodian_person_id_snapshot
             AND account.material_id = NEW.resolved_material_id
             AND account.condition_code = observation.condition_code
             AND account.availability_bucket = observation.availability_bucket
             AND account.lot_id IS NEW.resolved_lot_id
             WHERE observation.id = NEW.observation_id
               AND account.created_at <=
                   (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
        )
        OR EXISTS (
            SELECT 1 FROM stocktake_count_observations AS observation
            JOIN stocktake_snapshot_lines AS snapshot
              ON snapshot.task_id = NEW.task_id
             AND snapshot.scope_id = NEW.scope_id
            JOIN stock_accounts AS account ON account.id = snapshot.stock_account_id
             WHERE observation.id = NEW.observation_id
               AND account.owner_org_id = observation.owner_org_id
               AND account.location_id = observation.location_id
               AND account.custodian_person_id IS
                   observation.custodian_person_id_snapshot
               AND account.material_id = NEW.resolved_material_id
               AND account.condition_code = observation.condition_code
               AND account.availability_bucket = observation.availability_bucket
               AND account.lot_id IS NEW.resolved_lot_id
        )
     ))
BEGIN
    SELECT RAISE(ABORT, 'stocktake observation disposition is not canonical');
END
"""


def _sqlite_difference_completion_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_DIFFERENCE_COMPLETION_TRIGGER}
BEFORE INSERT ON stocktake_difference_set_completions
WHEN NOT EXISTS (
        SELECT 1 FROM stocktake_rounds AS round_row
        JOIN stocktake_round_submissions AS submission
          ON submission.id = NEW.round_submission_id
         AND submission.task_id = NEW.task_id
         AND submission.round_id = NEW.round_id
        JOIN stocktake_scope_count_completions AS sealing
          ON sealing.id = submission.sealing_completion_id
         WHERE round_row.id = NEW.round_id AND round_row.task_id = NEW.task_id
           AND round_row.status = 'submitted'
           AND submission.submitted_by_user_id = NEW.completed_by_user_id
           AND submission.submitted_by_person_id = NEW.completed_by_person_id
           AND submission.submitted_role_assignment_id =
               NEW.completed_role_assignment_id
           AND submission.authorization_version = NEW.authorization_version
           AND submission.submitted_at = NEW.completed_at
           AND sealing.task_id = NEW.task_id AND sealing.round_id = NEW.round_id
           AND sealing.completed_by_user_id = NEW.completed_by_user_id
           AND sealing.completed_by_person_id = NEW.completed_by_person_id
           AND sealing.completed_role_assignment_id =
               NEW.completed_role_assignment_id
           AND sealing.authorization_version = NEW.authorization_version
           AND sealing.completed_at = NEW.completed_at
     )
 OR EXISTS (SELECT 1 FROM stocktake_reviews
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_postings
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM inventory_opening_establishments
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR NEW.difference_count <> (SELECT count(*) FROM stocktake_differences
                              WHERE task_id = NEW.task_id
                                AND round_id = NEW.round_id)
 OR NEW.physical_difference_count <> (SELECT count(*) FROM stocktake_differences
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND difference_type <> 'control_unassigned')
 OR NEW.control_difference_count <> (SELECT count(*) FROM stocktake_differences
     WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       AND difference_type = 'control_unassigned')
 OR NEW.pending_observation_difference_count <> (
        SELECT count(*) FROM stocktake_differences AS difference
        JOIN stocktake_count_observations AS observation
          ON observation.id = difference.observed_line_id
         WHERE difference.task_id = NEW.task_id
           AND difference.round_id = NEW.round_id
           AND observation.verification_status = 'pending_verification')
 OR NEW.total_affected_qty <> COALESCE((SELECT sum(affected_qty)
      FROM stocktake_differences WHERE task_id = NEW.task_id
       AND round_id = NEW.round_id), 0)
 OR ((SELECT count(*) FROM stocktake_differences
       WHERE task_id = NEW.task_id AND round_id = NEW.round_id) > 0 AND (
        (SELECT min(difference_no) FROM stocktake_differences
          WHERE task_id = NEW.task_id AND round_id = NEW.round_id) <> 1
        OR (SELECT max(difference_no) FROM stocktake_differences
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id) <>
           (SELECT count(*) FROM stocktake_differences
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)))
BEGIN
    SELECT RAISE(ABORT, 'stocktake difference completion is not canonical');
END
"""


def _apply_postgresql_acl() -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {DISPOSITION_TABLE}, "
        f"{DIFFERENCE_COMPLETION_TABLE} FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    for function_name in (
        PG_ROUND_FUNCTION,
        PG_IMMUTABLE_FUNCTION,
        PG_DISPOSITION_FUNCTION,
        PG_DIFFERENCE_COMPLETION_FUNCTION,
        PG_DIFFERENCE_SEAL_FUNCTION,
        PG_REVIEW_FUNCTION,
    ):
        _revoke_function(function_name)


def _revoke_function(function_name: str) -> None:
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION {function_name}() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _drop_guards(dialect: str) -> None:
    if dialect == "postgresql":
        for table_name, trigger_name in (
            ("stocktake_reviews", PG_REVIEW_TRIGGER),
            ("stocktake_differences", PG_DIFFERENCE_SEAL_TRIGGER),
            (DIFFERENCE_COMPLETION_TABLE, PG_DIFFERENCE_COMPLETION_TRIGGER),
            (DISPOSITION_TABLE, PG_DISPOSITION_TRIGGER),
        ):
            op.execute(f"DROP TRIGGER {trigger_name} ON {table_name}")
        for table_name in (DISPOSITION_TABLE, DIFFERENCE_COMPLETION_TABLE):
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_0016 ON {table_name}"
            )
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable_truncate_0016 "
                f"ON {table_name}"
            )
        for function_name in (
            PG_REVIEW_FUNCTION,
            PG_DIFFERENCE_SEAL_FUNCTION,
            PG_DIFFERENCE_COMPLETION_FUNCTION,
            PG_DISPOSITION_FUNCTION,
            PG_IMMUTABLE_FUNCTION,
        ):
            op.execute(f"DROP FUNCTION {function_name}()")
        return

    for trigger_name in (
        SQLITE_REVIEW_TRIGGER,
        SQLITE_DIFFERENCE_SEAL_TRIGGER,
        SQLITE_DIFFERENCE_COMPLETION_TRIGGER,
        SQLITE_DISPOSITION_TRIGGER,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
    for table_name in (DISPOSITION_TABLE, DIFFERENCE_COMPLETION_TABLE):
        for operation in ("update", "delete"):
            op.execute(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_immutable_"
                f"{operation}_0016"
            )
