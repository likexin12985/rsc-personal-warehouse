"""Keep technician-referenced stocktake locations personal.

Revision ID: 20260831_0020
Revises: 20260831_0019
Create Date: 2026-08-31

Revision 0019 validates both technician evidence write boundaries.  This
append-only hardening preserves that fact when a stock location is edited: a
personal location referenced by an exact technician completion or recount
assignment cannot later be reclassified.  PostgreSQL also serializes an actual
demotion against concurrent evidence INSERT statements; SQLite serializes
writes and supplies the equivalent immediate update guard for local tests.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260831_0020"
down_revision: Union[str, Sequence[str], None] = "20260831_0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COMPLETION_TABLE = "stocktake_scope_count_completions"
RECOUNT_ASSIGNMENT_TABLE = "stocktake_recount_scope_assignments"
PRODUCTION_API_ROLE = "star_oam_api"

PG_FUNCTION = "rsc_preserve_stocktake_personal_location_0020"
LOCATION_TRIGGER = "trg_stock_locations_stocktake_personal_continuity_0020"

UPGRADE_BLOCKER = (
    "0020 preflight failed: technician stocktake evidence does not resolve "
    "to a current personal location"
)
GUARD_ERROR = (
    "technician stocktake evidence prevents personal location reclassification"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0020 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0020 SQLite upgrade requires an online connection")
        _postgresql_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_graph_for_migration()
        _online_preflight()

    if dialect == "postgresql":
        _create_postgresql_guard()
    else:
        _create_sqlite_guard()


def downgrade() -> None:
    if _dialect_name() == "postgresql":
        op.execute(
            f"DROP TRIGGER {LOCATION_TRIGGER} ON public.stock_locations"
        )
        op.execute(f"DROP FUNCTION public.{PG_FUNCTION}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {LOCATION_TRIGGER}")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_graph_for_migration() -> None:
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
    IF {_invalid_technician_location_graph_sql(schema_prefix="public.")} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_preflight() -> None:
    schema_prefix = "public." if _dialect_name() == "postgresql" else ""
    invalid_graph = _invalid_technician_location_graph_sql(
        schema_prefix=schema_prefix
    )
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {invalid_graph}"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _invalid_technician_location_graph_sql(*, schema_prefix: str) -> str:
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


def _create_postgresql_guard() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF OLD.location_type = 'personal'
       AND NEW.location_type <> 'personal' THEN
        LOCK TABLE public.stocktake_scope_count_completions,
                   public.stocktake_recount_scope_assignments
            IN SHARE MODE;
        IF EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS scope
              JOIN public.stocktake_scope_count_completions AS completion
                ON completion.scope_id = scope.id
               AND completion.task_id = scope.task_id
             WHERE scope.location_id = OLD.id
               AND completion.role_code = 'technician'
        ) OR EXISTS (
            SELECT 1
              FROM public.stocktake_scopes AS scope
              JOIN public.stocktake_recount_scope_assignments AS assignment
                ON assignment.scope_id = scope.id
               AND assignment.task_id = scope.task_id
             WHERE scope.location_id = OLD.id
               AND assignment.role_code = 'technician'
        ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {LOCATION_TRIGGER} "
        "BEFORE UPDATE ON public.stock_locations FOR EACH ROW "
        f"EXECUTE FUNCTION public.{PG_FUNCTION}()"
    )
    op.execute(
        "ALTER TABLE public.stock_locations ENABLE ALWAYS TRIGGER "
        f"{LOCATION_TRIGGER}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _create_sqlite_guard() -> None:
    op.execute(
        f"""
CREATE TRIGGER {LOCATION_TRIGGER}
BEFORE UPDATE ON stock_locations
BEGIN
    SELECT CASE WHEN OLD.location_type = 'personal'
                      AND NEW.location_type <> 'personal'
                      AND (EXISTS (
            SELECT 1
              FROM stocktake_scopes AS scope
              JOIN stocktake_scope_count_completions AS completion
                ON completion.scope_id = scope.id
               AND completion.task_id = scope.task_id
             WHERE scope.location_id = OLD.id
               AND completion.role_code = 'technician'
        ) OR EXISTS (
            SELECT 1
              FROM stocktake_scopes AS scope
              JOIN stocktake_recount_scope_assignments AS assignment
                ON assignment.scope_id = scope.id
               AND assignment.task_id = scope.task_id
             WHERE scope.location_id = OLD.id
               AND assignment.role_code = 'technician'
        )) THEN RAISE(ABORT, '{GUARD_ERROR}') END;
END
"""
    )
