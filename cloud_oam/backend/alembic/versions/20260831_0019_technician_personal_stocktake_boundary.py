"""Require technician stocktake evidence to bind a personal location.

Revision ID: 20260831_0019
Revises: 20260831_0018
Create Date: 2026-08-31

The existing completion and recount-assignment guards already prove the
technician is the frozen custodian.  This append-only hardening closes the
remaining location-class gap: a technician may only append either fact when
the exact task/scope resolves to a current ``personal`` stock location.
PostgreSQL is the production database; SQLite supplies an equivalent immediate
INSERT guard for local tests.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260831_0019"
down_revision: Union[str, Sequence[str], None] = "20260831_0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COMPLETION_TABLE = "stocktake_scope_count_completions"
RECOUNT_ASSIGNMENT_TABLE = "stocktake_recount_scope_assignments"
PRODUCTION_API_ROLE = "star_oam_api"

PG_FUNCTION = "rsc_validate_stocktake_technician_personal_location_0019"
COMPLETION_TRIGGER = (
    "trg_stocktake_scope_count_completions_technician_personal_location_0019"
)
RECOUNT_ASSIGNMENT_TRIGGER = (
    "trg_stocktake_recount_scope_assignments_technician_personal_location_0019"
)

UPGRADE_BLOCKER = (
    "0019 preflight failed: technician stocktake evidence is not bound to an "
    "exact personal location"
)
GUARD_ERROR = (
    "technician stocktake evidence requires an exact personal stock location"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0019 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0019 SQLite upgrade requires an online connection")
        _postgresql_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_write_graph()
        _online_preflight()

    if dialect == "postgresql":
        _create_postgresql_guards()
    else:
        _create_sqlite_guards()


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER {RECOUNT_ASSIGNMENT_TRIGGER} "
            f"ON public.{RECOUNT_ASSIGNMENT_TABLE}"
        )
        op.execute(
            f"DROP TRIGGER {COMPLETION_TRIGGER} ON public.{COMPLETION_TABLE}"
        )
        op.execute(f"DROP FUNCTION public.{PG_FUNCTION}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {RECOUNT_ASSIGNMENT_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {COMPLETION_TRIGGER}")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_write_graph() -> None:
    op.get_bind().exec_driver_sql(
        "LOCK TABLE public.stocktake_scope_count_completions, "
        "public.stocktake_recount_scope_assignments, "
        "public.stocktake_scopes, public.stock_locations "
        "IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stocktake_scope_count_completions, "
        "public.stocktake_recount_scope_assignments, "
        "public.stocktake_scopes, public.stock_locations "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF {_invalid_technician_graph_sql(schema_prefix="public.")} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight() -> None:
    schema_prefix = "public." if _dialect_name() == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        "SELECT 1 WHERE "
        f"{_invalid_technician_graph_sql(schema_prefix=schema_prefix)}"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _invalid_technician_graph_sql(*, schema_prefix: str) -> str:
    scope_table = f"{schema_prefix}stocktake_scopes"
    location_table = f"{schema_prefix}stock_locations"
    completion_table = f"{schema_prefix}{COMPLETION_TABLE}"
    assignment_table = f"{schema_prefix}{RECOUNT_ASSIGNMENT_TABLE}"
    return f"""EXISTS (
        SELECT 1
          FROM {completion_table} AS completion
          LEFT JOIN {scope_table} AS scope
            ON scope.id = completion.scope_id
           AND scope.task_id = completion.task_id
          LEFT JOIN {location_table} AS location
            ON location.id = scope.location_id
         WHERE completion.role_code = 'technician'
           AND (scope.id IS NULL OR location.id IS NULL
                OR location.location_type <> 'personal')
    ) OR EXISTS (
        SELECT 1
          FROM {assignment_table} AS assignment
          LEFT JOIN {scope_table} AS scope
            ON scope.id = assignment.scope_id
           AND scope.task_id = assignment.task_id
          LEFT JOIN {location_table} AS location
            ON location.id = scope.location_id
         WHERE assignment.role_code = 'technician'
           AND (scope.id IS NULL OR location.id IS NULL
                OR location.location_type <> 'personal')
    )"""


def _create_postgresql_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF NEW.role_code = 'technician' AND NOT EXISTS (
        SELECT 1
          FROM public.stocktake_scopes AS scope
          JOIN public.stock_locations AS location
            ON location.id = scope.location_id
         WHERE scope.id = NEW.scope_id
           AND scope.task_id = NEW.task_id
           AND location.location_type = 'personal'
    ) THEN
        RAISE EXCEPTION '{GUARD_ERROR}';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    for table_name, trigger_name in (
        (COMPLETION_TABLE, COMPLETION_TRIGGER),
        (RECOUNT_ASSIGNMENT_TABLE, RECOUNT_ASSIGNMENT_TRIGGER),
    ):
        op.execute(
            f"CREATE TRIGGER {trigger_name} "
            f"BEFORE INSERT ON public.{table_name} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
            f"{trigger_name}"
        )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _create_sqlite_guards() -> None:
    for table_name, trigger_name in (
        (COMPLETION_TABLE, COMPLETION_TRIGGER),
        (RECOUNT_ASSIGNMENT_TABLE, RECOUNT_ASSIGNMENT_TRIGGER),
    ):
        op.execute(
            f"""
CREATE TRIGGER {trigger_name}
BEFORE INSERT ON {table_name}
BEGIN
    SELECT CASE WHEN NEW.role_code = 'technician' AND NOT EXISTS (
        SELECT 1
          FROM stocktake_scopes AS scope
          JOIN stock_locations AS location ON location.id = scope.location_id
         WHERE scope.id = NEW.scope_id
           AND scope.task_id = NEW.task_id
           AND location.location_type = 'personal'
    ) THEN RAISE(ABORT, '{GUARD_ERROR}') END;
END
"""
        )
