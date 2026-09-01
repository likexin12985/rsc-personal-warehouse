"""Keep every formal stocktake asset and physical location in its task region.

Revision ID: 20260831_0025
Revises: 20260831_0024
Create Date: 2026-08-31

The V1 stocktake task region is the authorization/review boundary while each
scope keeps asset ownership and physical location as independent dimensions.
Those dimensions must not become unrelated: an asset owner or any physical
location owner outside the task's active organization tree can be created by
a national caller but cannot later be reviewed safely by the regional
workflow.

This revision rewrites no business row.  Upgrade locks and preflights the
existing graph, then installs an INSERT-only guard.  Both the task region and
asset owner must be active ``region_company`` organizations, every node from
the owner through the task region must be active, and recursive parent cycles
fail closed.  The target location must exist, be active and be ``region`` or
``personal``.  Its complete active parent-location chain must be unbroken and
acyclic, and every location owner must reach the same task region through an
active organization path.  PostgreSQL uses an ``ENABLE ALWAYS`` trigger whose
function is not executable by the runtime API role.  SQLite provides the same
immediate guard for local sequential tests; it is not production concurrency
evidence.

Downgrade is deliberately blocked while any formal scope exists.  Removing
the guard would otherwise weaken the meaning of already-established scope
facts for an older application version.  No historical row is guessed,
reparented, activated, deleted or otherwise repaired by this migration.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260831_0025"
down_revision: Union[str, Sequence[str], None] = "20260831_0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
PG_FUNCTION = "rsc_validate_stocktake_scope_region_owner_0025"
SCOPE_TRIGGER = "trg_stocktake_scopes_region_owner_0025"

UPGRADE_BLOCKER = (
    "0025 preflight failed: an existing stocktake scope asset or physical "
    "location is outside its active task region tree"
)
GUARD_ERROR = (
    "stocktake scope asset and active physical location must be inside the "
    "active task region tree"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0025 while formal stocktake scopes exist"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0025 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0025 SQLite upgrade requires an online connection")
        _postgresql_upgrade_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_scope_graph()
        _online_upgrade_preflight()

    if dialect == "postgresql":
        _create_postgresql_guard()
    else:
        _create_sqlite_guard()


def downgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0025 SQLite downgrade requires an online connection")
        _postgresql_downgrade_preflight()
    else:
        if dialect == "sqlite":
            _ensure_sqlite_migration_transaction()
        else:
            _lock_postgresql_scope_graph()
        _online_downgrade_preflight()

    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER {SCOPE_TRIGGER} ON public.stocktake_scopes"
        )
        op.execute(f"DROP FUNCTION public.{PG_FUNCTION}()")
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {SCOPE_TRIGGER}")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_scope_graph() -> None:
    op.get_bind().exec_driver_sql(
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_upgrade_preflight() -> None:
    op.execute(
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF {_invalid_existing_scope_graph_sql(schema_prefix="public.")} THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_upgrade_preflight() -> None:
    schema_prefix = "public." if _dialect_name() == "postgresql" else ""
    invalid_graph = _invalid_existing_scope_graph_sql(
        schema_prefix=schema_prefix
    )
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE {invalid_graph}"
    ).first() is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _postgresql_downgrade_preflight() -> None:
    op.execute(
        "LOCK TABLE public.organizations, public.stock_locations, "
        "public.stocktake_tasks, public.stocktake_scopes "
        "IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.stocktake_scopes) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _online_downgrade_preflight() -> None:
    schema_prefix = "public." if _dialect_name() == "postgresql" else ""
    if op.get_bind().exec_driver_sql(
        f"SELECT 1 WHERE EXISTS (SELECT 1 FROM "
        f"{schema_prefix}stocktake_scopes)"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


def _invalid_existing_scope_graph_sql(*, schema_prefix: str) -> str:
    organizations = f"{schema_prefix}organizations"
    locations = f"{schema_prefix}stock_locations"
    tasks = f"{schema_prefix}stocktake_tasks"
    scopes = f"{schema_prefix}stocktake_scopes"
    return f"""EXISTS (
        WITH RECURSIVE asset_owner_path(
            scope_id,
            task_region_org_id,
            current_org_id,
            parent_org_id,
            depth,
            reached_region,
            invalid_active_path
        ) AS (
            SELECT scope.id,
                   task.region_org_id,
                   owner.id,
                   owner.parent_id,
                   1,
                   CASE WHEN owner.id = task.region_org_id THEN 1 ELSE 0 END,
                   CASE WHEN owner.status = 'active' THEN 0 ELSE 1 END
              FROM {scopes} AS scope
              JOIN {tasks} AS task
                ON task.id = scope.task_id
              JOIN {organizations} AS owner
                ON owner.id = scope.owner_org_id
            UNION ALL
            SELECT path.scope_id,
                   path.task_region_org_id,
                   parent.id,
                   parent.parent_id,
                   path.depth + 1,
                   CASE
                       WHEN path.reached_region = 1
                            OR parent.id = path.task_region_org_id
                       THEN 1 ELSE 0
                   END,
                   CASE
                       WHEN path.reached_region = 1
                       THEN path.invalid_active_path
                       WHEN parent.status = 'active'
                       THEN path.invalid_active_path
                       ELSE 1
                   END
              FROM asset_owner_path AS path
              JOIN {organizations} AS parent
                ON parent.id = path.parent_org_id
             WHERE path.reached_region = 0
               AND path.depth <= (SELECT count(*) FROM {organizations})
        ),
        location_path(
            scope_id,
            task_region_org_id,
            current_location_id,
            parent_location_id,
            location_owner_org_id,
            location_status,
            location_type,
            depth
        ) AS (
            SELECT scope.id,
                   task.region_org_id,
                   location.id,
                   location.parent_id,
                   location.owner_org_id,
                   location.status,
                   location.location_type,
                   1
              FROM {scopes} AS scope
              JOIN {tasks} AS task
                ON task.id = scope.task_id
              JOIN {locations} AS location
                ON location.id = scope.location_id
            UNION ALL
            SELECT path.scope_id,
                   path.task_region_org_id,
                   parent.id,
                   parent.parent_id,
                   parent.owner_org_id,
                   parent.status,
                   parent.location_type,
                   path.depth + 1
              FROM location_path AS path
              JOIN {locations} AS parent
                ON parent.id = path.parent_location_id
             WHERE path.depth <= (SELECT count(*) FROM {locations})
        ),
        location_owner_path(
            scope_id,
            location_id,
            task_region_org_id,
            current_org_id,
            parent_org_id,
            depth,
            reached_region,
            invalid_active_path
        ) AS (
            SELECT path.scope_id,
                   path.current_location_id,
                   path.task_region_org_id,
                   owner.id,
                   owner.parent_id,
                   1,
                   CASE
                       WHEN owner.id = path.task_region_org_id THEN 1 ELSE 0
                   END,
                   CASE WHEN owner.status = 'active' THEN 0 ELSE 1 END
              FROM location_path AS path
              JOIN {organizations} AS owner
                ON owner.id = path.location_owner_org_id
            UNION ALL
            SELECT path.scope_id,
                   path.location_id,
                   path.task_region_org_id,
                   parent.id,
                   parent.parent_id,
                   path.depth + 1,
                   CASE
                       WHEN path.reached_region = 1
                            OR parent.id = path.task_region_org_id
                       THEN 1 ELSE 0
                   END,
                   CASE
                       WHEN path.reached_region = 1
                       THEN path.invalid_active_path
                       WHEN parent.status = 'active'
                       THEN path.invalid_active_path
                       ELSE 1
                   END
              FROM location_owner_path AS path
              JOIN {organizations} AS parent
                ON parent.id = path.parent_org_id
             WHERE path.reached_region = 0
               AND path.depth <= (SELECT count(*) FROM {organizations})
        )
        SELECT 1
          FROM {scopes} AS scope
          LEFT JOIN {tasks} AS task
            ON task.id = scope.task_id
          LEFT JOIN {organizations} AS region
            ON region.id = task.region_org_id
          LEFT JOIN {organizations} AS owner
            ON owner.id = scope.owner_org_id
          LEFT JOIN {locations} AS target_location
            ON target_location.id = scope.location_id
         WHERE task.id IS NULL
            OR region.id IS NULL
            OR region.status <> 'active'
            OR region.org_type <> 'region_company'
            OR owner.id IS NULL
            OR owner.status <> 'active'
            OR owner.org_type <> 'region_company'
            OR NOT EXISTS (
                SELECT 1
                  FROM asset_owner_path AS path
                 WHERE path.scope_id = scope.id
                   AND path.current_org_id = task.region_org_id
                   AND path.invalid_active_path = 0
            )
            OR EXISTS (
                SELECT 1
                  FROM asset_owner_path AS path
                 WHERE path.scope_id = scope.id
                   AND path.depth > (SELECT count(*) FROM {organizations})
            )
            OR target_location.id IS NULL
            OR target_location.status <> 'active'
            OR target_location.location_type NOT IN ('region', 'personal')
            OR EXISTS (
                SELECT 1
                  FROM location_path AS path
                 WHERE path.scope_id = scope.id
                   AND path.location_status <> 'active'
            )
            OR EXISTS (
                SELECT 1
                  FROM location_path AS path
                  LEFT JOIN {locations} AS parent
                    ON parent.id = path.parent_location_id
                 WHERE path.scope_id = scope.id
                   AND path.parent_location_id IS NOT NULL
                   AND parent.id IS NULL
            )
            OR EXISTS (
                SELECT 1
                  FROM location_path AS path
                 WHERE path.scope_id = scope.id
                   AND path.depth > (SELECT count(*) FROM {locations})
            )
            OR EXISTS (
                SELECT 1
                  FROM location_path AS path
                 WHERE path.scope_id = scope.id
                   AND (
                       NOT EXISTS (
                           SELECT 1
                             FROM location_owner_path AS owner_path
                            WHERE owner_path.scope_id = path.scope_id
                              AND owner_path.location_id =
                                  path.current_location_id
                              AND owner_path.current_org_id =
                                  path.task_region_org_id
                              AND owner_path.invalid_active_path = 0
                       )
                       OR EXISTS (
                           SELECT 1
                             FROM location_owner_path AS owner_path
                            WHERE owner_path.scope_id = path.scope_id
                              AND owner_path.location_id =
                                  path.current_location_id
                              AND owner_path.depth >
                                  (SELECT count(*) FROM {organizations})
                       )
                   )
            )
    )"""


def _create_postgresql_guard() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_region_org_id uuid;
    current_org_id uuid;
    current_parent_org_id uuid;
    current_status text;
    current_org_type text;
    visited_org_ids uuid[];
    current_location_id uuid;
    current_parent_location_id uuid;
    current_location_owner_org_id uuid;
    current_location_status text;
    current_location_type text;
    visited_location_ids uuid[];
    is_target_location boolean;
    asset_owner_reached_region boolean;
    location_owner_reached_region boolean;
BEGIN
    SELECT task.region_org_id
      INTO task_region_org_id
      FROM public.stocktake_tasks AS task
     WHERE task.id = NEW.task_id
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;

    SELECT region.status, region.org_type
      INTO current_status, current_org_type
      FROM public.organizations AS region
     WHERE region.id = task_region_org_id
     FOR SHARE;
    IF NOT FOUND
       OR current_status <> 'active'
       OR current_org_type <> 'region_company' THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;

    current_org_id := NEW.owner_org_id;
    visited_org_ids := ARRAY[]::uuid[];
    asset_owner_reached_region := FALSE;
    WHILE current_org_id IS NOT NULL LOOP
        IF current_org_id = ANY(visited_org_ids) THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;
        visited_org_ids := array_append(visited_org_ids, current_org_id);

        SELECT organization.parent_id,
               organization.status,
               organization.org_type
          INTO current_parent_org_id, current_status, current_org_type
          FROM public.organizations AS organization
         WHERE organization.id = current_org_id
         FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;
        IF current_status <> 'active' THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;
        IF current_org_id = NEW.owner_org_id
           AND current_org_type <> 'region_company' THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;
        IF current_org_id = task_region_org_id THEN
            asset_owner_reached_region := TRUE;
            EXIT;
        END IF;
        current_org_id := current_parent_org_id;
    END LOOP;

    IF NOT asset_owner_reached_region THEN
        RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
    END IF;

    current_location_id := NEW.location_id;
    visited_location_ids := ARRAY[]::uuid[];
    is_target_location := TRUE;
    WHILE current_location_id IS NOT NULL LOOP
        IF current_location_id = ANY(visited_location_ids) THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;
        visited_location_ids := array_append(
            visited_location_ids,
            current_location_id
        );

        SELECT location.parent_id,
               location.owner_org_id,
               location.status,
               location.location_type
          INTO current_parent_location_id,
               current_location_owner_org_id,
               current_location_status,
               current_location_type
          FROM public.stock_locations AS location
         WHERE location.id = current_location_id
         FOR SHARE;
        IF NOT FOUND
           OR current_location_status <> 'active'
           OR (
               is_target_location
               AND current_location_type NOT IN ('region', 'personal')
           ) THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;

        current_org_id := current_location_owner_org_id;
        visited_org_ids := ARRAY[]::uuid[];
        location_owner_reached_region := FALSE;
        WHILE current_org_id IS NOT NULL LOOP
            IF current_org_id = ANY(visited_org_ids) THEN
                RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
            END IF;
            visited_org_ids := array_append(visited_org_ids, current_org_id);

            SELECT organization.parent_id, organization.status
              INTO current_parent_org_id, current_status
              FROM public.organizations AS organization
             WHERE organization.id = current_org_id
             FOR SHARE;
            IF NOT FOUND OR current_status <> 'active' THEN
                RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
            END IF;
            IF current_org_id = task_region_org_id THEN
                location_owner_reached_region := TRUE;
                EXIT;
            END IF;
            current_org_id := current_parent_org_id;
        END LOOP;
        IF NOT location_owner_reached_region THEN
            RAISE EXCEPTION '{GUARD_ERROR}' USING ERRCODE = '23514';
        END IF;

        current_location_id := current_parent_location_id;
        is_target_location := FALSE;
    END LOOP;

    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {SCOPE_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_scopes FOR EACH ROW EXECUTE FUNCTION "
        f"public.{PG_FUNCTION}()"
    )
    op.execute(
        "ALTER TABLE public.stocktake_scopes ENABLE ALWAYS TRIGGER "
        f"{SCOPE_TRIGGER}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )


def _create_sqlite_guard() -> None:
    op.execute(
        f"""
CREATE TRIGGER {SCOPE_TRIGGER}
BEFORE INSERT ON stocktake_scopes
BEGIN
    SELECT CASE WHEN {_invalid_sqlite_insert_graph_sql()}
        THEN RAISE(ABORT, '{GUARD_ERROR}') END;
END
"""
    )


def _invalid_sqlite_insert_graph_sql() -> str:
    return """NOT EXISTS (
        WITH RECURSIVE asset_owner_path(
            task_region_org_id,
            current_org_id,
            parent_org_id,
            depth,
            reached_region,
            invalid_active_path
        ) AS (
            SELECT task.region_org_id,
                   owner.id,
                   owner.parent_id,
                   1,
                   CASE WHEN owner.id = task.region_org_id THEN 1 ELSE 0 END,
                   CASE WHEN owner.status = 'active' THEN 0 ELSE 1 END
              FROM stocktake_tasks AS task
              JOIN organizations AS owner
                ON owner.id = NEW.owner_org_id
             WHERE task.id = NEW.task_id
            UNION ALL
            SELECT path.task_region_org_id,
                   parent.id,
                   parent.parent_id,
                   path.depth + 1,
                   CASE
                       WHEN path.reached_region = 1
                            OR parent.id = path.task_region_org_id
                       THEN 1 ELSE 0
                   END,
                   CASE
                       WHEN path.reached_region = 1
                       THEN path.invalid_active_path
                       WHEN parent.status = 'active'
                       THEN path.invalid_active_path
                       ELSE 1
                   END
              FROM asset_owner_path AS path
              JOIN organizations AS parent
                ON parent.id = path.parent_org_id
             WHERE path.reached_region = 0
               AND path.depth <= (SELECT count(*) FROM organizations)
        ),
        location_path(
            task_region_org_id,
            current_location_id,
            parent_location_id,
            location_owner_org_id,
            location_status,
            location_type,
            depth
        ) AS (
            SELECT task.region_org_id,
                   location.id,
                   location.parent_id,
                   location.owner_org_id,
                   location.status,
                   location.location_type,
                   1
              FROM stocktake_tasks AS task
              JOIN stock_locations AS location
                ON location.id = NEW.location_id
             WHERE task.id = NEW.task_id
            UNION ALL
            SELECT path.task_region_org_id,
                   parent.id,
                   parent.parent_id,
                   parent.owner_org_id,
                   parent.status,
                   parent.location_type,
                   path.depth + 1
              FROM location_path AS path
              JOIN stock_locations AS parent
                ON parent.id = path.parent_location_id
             WHERE path.depth <= (SELECT count(*) FROM stock_locations)
        ),
        location_owner_path(
            location_id,
            task_region_org_id,
            current_org_id,
            parent_org_id,
            depth,
            reached_region,
            invalid_active_path
        ) AS (
            SELECT path.current_location_id,
                   path.task_region_org_id,
                   owner.id,
                   owner.parent_id,
                   1,
                   CASE
                       WHEN owner.id = path.task_region_org_id THEN 1 ELSE 0
                   END,
                   CASE WHEN owner.status = 'active' THEN 0 ELSE 1 END
              FROM location_path AS path
              JOIN organizations AS owner
                ON owner.id = path.location_owner_org_id
            UNION ALL
            SELECT path.location_id,
                   path.task_region_org_id,
                   parent.id,
                   parent.parent_id,
                   path.depth + 1,
                   CASE
                       WHEN path.reached_region = 1
                            OR parent.id = path.task_region_org_id
                       THEN 1 ELSE 0
                   END,
                   CASE
                       WHEN path.reached_region = 1
                       THEN path.invalid_active_path
                       WHEN parent.status = 'active'
                       THEN path.invalid_active_path
                       ELSE 1
                   END
              FROM location_owner_path AS path
              JOIN organizations AS parent
                ON parent.id = path.parent_org_id
             WHERE path.reached_region = 0
               AND path.depth <= (SELECT count(*) FROM organizations)
        )
        SELECT 1
          FROM stocktake_tasks AS task
          JOIN organizations AS region
            ON region.id = task.region_org_id
          JOIN organizations AS owner
            ON owner.id = NEW.owner_org_id
          JOIN stock_locations AS target_location
            ON target_location.id = NEW.location_id
         WHERE task.id = NEW.task_id
           AND region.status = 'active'
           AND region.org_type = 'region_company'
           AND owner.status = 'active'
           AND owner.org_type = 'region_company'
           AND EXISTS (
               SELECT 1
                 FROM asset_owner_path AS path
                WHERE path.current_org_id = task.region_org_id
                  AND path.invalid_active_path = 0
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM asset_owner_path AS path
                WHERE path.depth > (SELECT count(*) FROM organizations)
           )
           AND target_location.status = 'active'
           AND target_location.location_type IN ('region', 'personal')
           AND NOT EXISTS (
               SELECT 1
                 FROM location_path AS path
                WHERE path.location_status <> 'active'
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM location_path AS path
                 LEFT JOIN stock_locations AS parent
                   ON parent.id = path.parent_location_id
                WHERE path.parent_location_id IS NOT NULL
                  AND parent.id IS NULL
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM location_path AS path
                WHERE path.depth > (SELECT count(*) FROM stock_locations)
           )
           AND NOT EXISTS (
               SELECT 1
                 FROM location_path AS path
                WHERE NOT EXISTS (
                    SELECT 1
                      FROM location_owner_path AS owner_path
                     WHERE owner_path.location_id = path.current_location_id
                       AND owner_path.current_org_id = task.region_org_id
                       AND owner_path.invalid_active_path = 0
                )
                   OR EXISTS (
                       SELECT 1
                         FROM location_owner_path AS owner_path
                        WHERE owner_path.location_id = path.current_location_id
                          AND owner_path.depth >
                              (SELECT count(*) FROM organizations)
                   )
           )
    )"""
