"""Persist the seal-first outcome of a durable stocktake post command.

Revision ID: 20260906_0065
Revises: 20260906_0063

This is an append-only evidence table.  It does not backfill existing posting
completions because a request reference cannot be reconstructed safely from
historical rows.  The posting service can therefore record either a confirmed
completion or a seal-first ``sealed_not_executed`` tombstone without replaying
an unknown request.
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260906_0065"
down_revision: str | None = "20260906_0063"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = "stocktake_posting_command_outcomes"
IMMUTABLE_TRIGGER = "trg_stocktake_posting_command_outcomes_immutable_0065"
IMMUTABLE_FUNCTION = "rsc_guard_stocktake_posting_command_outcome_immutable_0065"
SQLITE_UPDATE_TRIGGER = f"{IMMUTABLE_TRIGGER}_update"
SQLITE_DELETE_TRIGGER = f"{IMMUTABLE_TRIGGER}_delete"
DOWNGRADE_BLOCKER = "cannot downgrade 0065 while stocktake posting command outcomes exist"


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0065 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "LOCK TABLE public.alembic_version, public.stocktake_tasks, "
            "public.stocktake_posting_completions, public.users, public.people, "
            "public.role_assignments IN ACCESS EXCLUSIVE MODE"
        )

    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("request_reference", sa.String(length=160), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("completion_id", sa.Uuid(), nullable=True),
        sa.Column("expected_task_version", sa.BigInteger(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("sealed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("sealed_by_person_id", sa.Uuid(), nullable=True),
        sa.Column("sealed_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column(
            "sealed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stocktake_posting_command_outcomes_0065"),
        sa.UniqueConstraint(
            "task_id",
            "request_reference",
            name="uq_stocktake_posting_command_outcomes_request_0065",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["stocktake_tasks.id"],
            name="fk_stocktake_posting_command_outcomes_task_0065",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["completion_id", "task_id"],
            ["stocktake_posting_completions.id", "stocktake_posting_completions.task_id"],
            name="fk_stocktake_posting_command_outcomes_completion_0065",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sealed_by_user_id"], ["users.id"],
            name="fk_stocktake_posting_command_outcomes_user_0065",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sealed_by_person_id"], ["people.id"],
            name="fk_stocktake_posting_command_outcomes_person_0065",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sealed_role_assignment_id"], ["role_assignments.id"],
            name="fk_stocktake_posting_command_outcomes_assignment_0065",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "disposition IN ('posted', 'sealed_not_executed')",
            name="ck_stocktake_posting_command_outcomes_disposition_0065",
        ),
        sa.CheckConstraint(
            "expected_task_version >= 0 AND authorization_version > 0",
            name="ck_stocktake_posting_command_outcomes_versions_0065",
        ),
        sa.CheckConstraint(
            "length(request_reference) > 0",
            name="ck_stocktake_posting_command_outcomes_request_reference_0065",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64",
            name="ck_stocktake_posting_command_outcomes_request_hash_0065",
        ),
        sa.CheckConstraint(
            "((disposition = 'posted' AND completion_id IS NOT NULL AND "
            "sealed_by_user_id IS NULL AND sealed_by_person_id IS NULL AND "
            "sealed_role_assignment_id IS NULL AND sealed_at IS NULL) OR "
            "(disposition = 'sealed_not_executed' AND completion_id IS NULL AND "
            "sealed_by_user_id IS NOT NULL AND sealed_by_person_id IS NOT NULL AND "
            "sealed_role_assignment_id IS NOT NULL AND sealed_at IS NOT NULL))",
            name="ck_stocktake_posting_command_outcomes_binding_0065",
        ),
        sa.CheckConstraint(
            "sealed_at IS NULL OR sealed_at = created_at",
            name="ck_stocktake_posting_command_outcomes_chronology_0065",
        ),
    )
    op.create_index(
        "ix_stocktake_posting_command_outcomes_request_0065",
        TABLE,
        ["request_reference"],
    )

    if dialect == "postgresql":
        op.execute(f"""
CREATE FUNCTION public.{IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION 'stocktake posting command outcomes are immutable'
        USING ERRCODE = '55000';
END
$$
""")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{IMMUTABLE_FUNCTION}() FROM PUBLIC"
        )
        op.execute(
            f"CREATE TRIGGER {IMMUTABLE_TRIGGER} BEFORE UPDATE OR DELETE ON public.{TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{IMMUTABLE_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{TABLE} ENABLE ALWAYS TRIGGER {IMMUTABLE_TRIGGER}"
        )
        op.execute(
            f"REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.{TABLE} FROM star_oam_api"
        )
        op.execute(f"GRANT SELECT, INSERT ON TABLE public.{TABLE} TO star_oam_api")
    else:
        op.execute(
            f"CREATE TRIGGER {SQLITE_UPDATE_TRIGGER} BEFORE UPDATE ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'stocktake posting command outcomes are immutable'); END"
        )
        op.execute(
            f"CREATE TRIGGER {SQLITE_DELETE_TRIGGER} BEFORE DELETE ON {TABLE} "
            "BEGIN SELECT RAISE(ABORT, 'stocktake posting command outcomes are immutable'); END"
        )


def downgrade() -> None:
    dialect = _dialect_name()
    bind = op.get_bind()
    if bind.execute(sa.text(f"SELECT 1 FROM {TABLE} LIMIT 1")).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)
    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS {IMMUTABLE_TRIGGER} ON public.{TABLE}")
        op.execute(f"DROP FUNCTION IF EXISTS public.{IMMUTABLE_FUNCTION}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}")
    op.drop_index("ix_stocktake_posting_command_outcomes_request_0065", table_name=TABLE)
    op.drop_table(TABLE)
