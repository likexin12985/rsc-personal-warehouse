"""Lock one exact reference union for terminal opening-task batches.

Revision ID: 20260831_0028
Revises: 20260831_0027
Create Date: 2026-08-31

The 0027 helpers deliberately cover one opening task at a time.  Re-entering
those helpers for several historical tasks can invert shared owner locks when
their reference graphs overlap.  This revision therefore exposes one bounded,
migration-owned SECURITY DEFINER entrypoint that proves the caller supplied
the exact task/scope/account/material union and locks that union once in the
global inventory order.

The helper does not return business data and does not widen any table or
column privilege.  SQLite retains its single-process test semantics and has no
equivalent function.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260831_0028"
down_revision: Union[str, Sequence[str], None] = "20260831_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
LOCK_GRAPH_ERROR = "formal opening terminal reference union invariant violated"
MAXIMUM_LOCK_ROWS = 100_000
PG_OPENING_TERMINAL_REFERENCE_UNION_FUNCTION = (
    "rsc_lock_opening_terminal_reference_union_0028"
)
PG_FUNCTION_SIGNATURE = (
    f"public.{PG_OPENING_TERMINAL_REFERENCE_UNION_FUNCTION}"
    "(uuid[], uuid[], uuid[], uuid[], uuid[])"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0028 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _create_postgresql_union_lock_function()
    _apply_postgresql_function_acl()


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    op.execute(f"DROP FUNCTION {PG_FUNCTION_SIGNATURE}")


def _create_postgresql_union_lock_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_OPENING_TERMINAL_REFERENCE_UNION_FUNCTION}(
    requested_task_ids uuid[],
    requested_owner_org_ids uuid[],
    requested_location_ids uuid[],
    requested_material_ids uuid[],
    requested_account_ids uuid[]
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    requested_count integer;
    requested_scope_count integer;
    unique_count integer;
    graph_count bigint;
    expected_account_ids uuid[];
    expected_material_ids uuid[];
    relevant_location_ids uuid[];
    relevant_organization_ids uuid[];
BEGIN
    requested_count := cardinality(requested_task_ids);
    IF requested_task_ids IS NULL OR requested_count IS NULL
       OR requested_count < 1 OR requested_count > {MAXIMUM_LOCK_ROWS}
       OR array_ndims(requested_task_ids) <> 1
       OR array_position(requested_task_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(DISTINCT value)
      INTO unique_count
      FROM unnest(requested_task_ids) AS requested(value);
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    requested_scope_count := cardinality(requested_owner_org_ids);
    IF requested_owner_org_ids IS NULL OR requested_scope_count IS NULL
       OR requested_scope_count < 1
       OR requested_scope_count > {MAXIMUM_LOCK_ROWS}
       OR array_ndims(requested_owner_org_ids) <> 1
       OR array_position(requested_owner_org_ids, NULL) IS NOT NULL
       OR requested_location_ids IS NULL
       OR cardinality(requested_location_ids) <> requested_scope_count
       OR array_ndims(requested_location_ids) <> 1
       OR array_position(requested_location_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(*)
      INTO unique_count
      FROM (
        SELECT DISTINCT requested.owner_org_id, requested.location_id
          FROM unnest(requested_owner_org_ids, requested_location_ids)
               AS requested(owner_org_id, location_id)
      ) AS unique_scopes;
    IF unique_count <> requested_scope_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    requested_count := cardinality(requested_material_ids);
    IF requested_material_ids IS NULL OR requested_count IS NULL
       OR requested_count > {MAXIMUM_LOCK_ROWS}
       OR (requested_count > 0 AND array_ndims(requested_material_ids) <> 1)
       OR array_position(requested_material_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(DISTINCT value)
      INTO unique_count
      FROM unnest(requested_material_ids) AS requested(value);
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    requested_count := cardinality(requested_account_ids);
    IF requested_account_ids IS NULL OR requested_count IS NULL
       OR requested_count > {MAXIMUM_LOCK_ROWS}
       OR (requested_count > 0 AND array_ndims(requested_account_ids) <> 1)
       OR array_position(requested_account_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(DISTINCT value)
      INTO unique_count
      FROM unnest(requested_account_ids) AS requested(value);
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    -- The caller already holds ledger -> every task -> principal union ->
    -- every task-local evidence row.  Re-locking the same task rows is
    -- idempotent and makes direct invocation fail closed.
    SELECT count(*)
      INTO graph_count
      FROM public.stocktake_tasks AS task
     WHERE task.id = ANY(requested_task_ids)
       AND task.task_type = 'opening'
       AND task.status IN ('posted', 'closed');
    IF graph_count <> cardinality(requested_task_ids) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM task.id
      FROM public.stocktake_tasks AS task
     WHERE task.id = ANY(requested_task_ids)
       AND task.task_type = 'opening'
       AND task.status IN ('posted', 'closed')
     ORDER BY task.id
     FOR UPDATE OF task;

    -- Owner/location coordinates must equal the distinct scope union of the
    -- supplied tasks.  Neither independently deduped dimension is accepted.
    SELECT count(*)
      INTO graph_count
      FROM (
        SELECT scope.owner_org_id, scope.location_id
          FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = ANY(requested_task_ids)
         GROUP BY scope.owner_org_id, scope.location_id
      ) AS expected_scopes;
    IF graph_count <> requested_scope_count
       OR graph_count > {MAXIMUM_LOCK_ROWS}
       OR EXISTS (
            SELECT expected.owner_org_id, expected.location_id
              FROM (
                SELECT scope.owner_org_id, scope.location_id
                  FROM public.stocktake_scopes AS scope
                 WHERE scope.task_id = ANY(requested_task_ids)
                 GROUP BY scope.owner_org_id, scope.location_id
              ) AS expected
            EXCEPT
            SELECT requested.owner_org_id, requested.location_id
              FROM unnest(requested_owner_org_ids, requested_location_ids)
                   AS requested(owner_org_id, location_id)
       )
       OR EXISTS (
            SELECT requested.owner_org_id, requested.location_id
              FROM unnest(requested_owner_org_ids, requested_location_ids)
                   AS requested(owner_org_id, location_id)
            EXCEPT
            SELECT expected.owner_org_id, expected.location_id
              FROM (
                SELECT scope.owner_org_id, scope.location_id
                  FROM public.stocktake_scopes AS scope
                 WHERE scope.task_id = ANY(requested_task_ids)
                 GROUP BY scope.owner_org_id, scope.location_id
              ) AS expected
       ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    -- The exact account union is all accounts currently under an exact scope
    -- pair plus every account explicitly sealed into task evidence.
    SELECT coalesce(
               array_agg(expected.account_id ORDER BY expected.account_id),
               ARRAY[]::uuid[]
           )
      INTO expected_account_ids
      FROM (
        SELECT account.id AS account_id
          FROM public.stock_accounts AS account
         WHERE EXISTS (
            SELECT 1
              FROM unnest(requested_owner_org_ids, requested_location_ids)
                   AS requested(owner_org_id, location_id)
             WHERE requested.owner_org_id = account.owner_org_id
               AND requested.location_id = account.location_id
         )
        UNION
        SELECT snapshot.stock_account_id
          FROM public.stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = ANY(requested_task_ids)
        UNION
        SELECT count_line.stock_account_id
          FROM public.stocktake_count_lines AS count_line
         WHERE count_line.task_id = ANY(requested_task_ids)
        UNION
        SELECT difference.expected_account_id
          FROM public.stocktake_differences AS difference
         WHERE difference.task_id = ANY(requested_task_ids)
           AND difference.expected_account_id IS NOT NULL
        UNION
        SELECT difference.observed_account_id
          FROM public.stocktake_differences AS difference
         WHERE difference.task_id = ANY(requested_task_ids)
           AND difference.observed_account_id IS NOT NULL
      ) AS expected;
    IF cardinality(expected_account_ids) > {MAXIMUM_LOCK_ROWS}
       OR cardinality(expected_account_ids) <> cardinality(requested_account_ids)
       OR EXISTS (
            SELECT value FROM unnest(expected_account_ids) AS expected(value)
            EXCEPT
            SELECT value FROM unnest(requested_account_ids) AS requested(value)
       )
       OR EXISTS (
            SELECT value FROM unnest(requested_account_ids) AS requested(value)
            EXCEPT
            SELECT value FROM unnest(expected_account_ids) AS expected(value)
       ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    -- Material coordinates mirror the Python planner: scope/control/direct
    -- evidence IDs, disposition resolutions, all selected accounts, and the
    -- active SKU/QR candidates for raw observations.
    SELECT coalesce(
               array_agg(expected.material_id ORDER BY expected.material_id),
               ARRAY[]::uuid[]
           )
      INTO expected_material_ids
      FROM (
        SELECT scope.material_id
          FROM public.stocktake_scopes AS scope
         WHERE scope.task_id = ANY(requested_task_ids)
           AND scope.material_id IS NOT NULL
        UNION
        SELECT control.material_id
          FROM public.stocktake_control_snapshot_lines AS control
         WHERE control.task_id = ANY(requested_task_ids)
           AND control.material_id IS NOT NULL
        UNION
        SELECT observation.material_id
          FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = ANY(requested_task_ids)
           AND observation.material_id IS NOT NULL
        UNION
        SELECT disposition.resolved_material_id
          FROM public.stocktake_observation_dispositions AS disposition
         WHERE disposition.task_id = ANY(requested_task_ids)
           AND disposition.resolved_material_id IS NOT NULL
        UNION
        SELECT difference.material_id
          FROM public.stocktake_differences AS difference
         WHERE difference.task_id = ANY(requested_task_ids)
           AND difference.material_id IS NOT NULL
        UNION
        SELECT account.material_id
          FROM public.stock_accounts AS account
         WHERE account.id = ANY(expected_account_ids)
        UNION
        SELECT material.id
          FROM public.materials AS material
          JOIN public.stocktake_count_observations AS observation
            ON observation.material_identifier_type = 'sku_code'
           AND observation.material_identifier_raw = material.sku_code
         WHERE observation.task_id = ANY(requested_task_ids)
           AND material.status = 'active'
        UNION
        SELECT qr.object_id
          FROM public.qr_codes AS qr
          JOIN public.stocktake_count_observations AS observation
            ON observation.material_identifier_type = 'qr_code'
           AND observation.material_identifier_raw = qr.code
         WHERE observation.task_id = ANY(requested_task_ids)
           AND qr.object_type = 'material'
           AND qr.status = 'active'
      ) AS expected;
    IF cardinality(expected_material_ids) > {MAXIMUM_LOCK_ROWS}
       OR cardinality(expected_material_ids) <> cardinality(requested_material_ids)
       OR EXISTS (
            SELECT value FROM unnest(expected_material_ids) AS expected(value)
            EXCEPT
            SELECT value FROM unnest(requested_material_ids) AS requested(value)
       )
       OR EXISTS (
            SELECT value FROM unnest(requested_material_ids) AS requested(value)
            EXCEPT
            SELECT value FROM unnest(expected_material_ids) AS expected(value)
       ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    -- Global owner order starts with accounts.  Exact existence is checked
    -- even for an empty union so the helper never silently drops a coordinate.
    SELECT count(*) INTO graph_count
      FROM public.stock_accounts AS account
     WHERE account.id = ANY(requested_account_ids);
    IF graph_count <> cardinality(requested_account_ids) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM account.id
      FROM public.stock_accounts AS account
     WHERE account.id = ANY(requested_account_ids)
     ORDER BY account.id
     FOR UPDATE OF account;

    SELECT coalesce(
               array_agg(graph.id ORDER BY graph.id),
               ARRAY[]::uuid[]
           )
      INTO relevant_location_ids
      FROM (
        WITH RECURSIVE location_graph(id, parent_id) AS (
            SELECT location.id, location.parent_id
              FROM public.stock_locations AS location
             WHERE location.id = ANY(requested_location_ids)
                OR location.id IN (
                    SELECT account.location_id
                      FROM public.stock_accounts AS account
                     WHERE account.id = ANY(requested_account_ids)
                )
            UNION
            SELECT parent.id, parent.parent_id
              FROM public.stock_locations AS parent
              JOIN location_graph AS child ON parent.id = child.parent_id
        )
        SELECT id FROM location_graph
      ) AS graph;
    IF cardinality(relevant_location_ids) < cardinality(requested_location_ids)
       OR cardinality(relevant_location_ids) > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM location.id
      FROM public.stock_locations AS location
     WHERE location.id = ANY(relevant_location_ids)
     ORDER BY location.id
     FOR UPDATE OF location;

    SELECT coalesce(
               array_agg(graph.id ORDER BY graph.id),
               ARRAY[]::uuid[]
           )
      INTO relevant_organization_ids
      FROM (
        WITH RECURSIVE
        organization_seeds(id) AS (
            SELECT task.region_org_id
              FROM public.stocktake_tasks AS task
             WHERE task.id = ANY(requested_task_ids)
            UNION
            SELECT value
              FROM unnest(requested_owner_org_ids) AS owner(value)
            UNION
            SELECT location.owner_org_id
              FROM public.stock_locations AS location
             WHERE location.id = ANY(relevant_location_ids)
            UNION
            SELECT account.owner_org_id
              FROM public.stock_accounts AS account
             WHERE account.id = ANY(requested_account_ids)
        ),
        organization_graph(id, parent_id) AS (
            SELECT organization.id, organization.parent_id
              FROM public.organizations AS organization
              JOIN organization_seeds AS seed ON seed.id = organization.id
            UNION
            SELECT parent.id, parent.parent_id
              FROM public.organizations AS parent
              JOIN organization_graph AS child ON parent.id = child.parent_id
        )
        SELECT id FROM organization_graph
      ) AS graph;
    IF cardinality(relevant_organization_ids) < 1
       OR cardinality(relevant_organization_ids) > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM organization.id
      FROM public.organizations AS organization
     WHERE organization.id = ANY(relevant_organization_ids)
     ORDER BY organization.id
     FOR UPDATE OF organization;

    SELECT count(*) INTO graph_count
      FROM public.custody_assignments AS custody
     WHERE custody.location_id = ANY(relevant_location_ids);
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM custody.id
      FROM public.custody_assignments AS custody
     WHERE custody.location_id = ANY(relevant_location_ids)
     ORDER BY custody.location_id, custody.valid_from, custody.id
     FOR UPDATE OF custody;

    SELECT count(*) INTO graph_count
      FROM public.materials AS material
     WHERE material.id = ANY(requested_material_ids);
    IF graph_count <> cardinality(requested_material_ids) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM material.id
      FROM public.materials AS material
     WHERE material.id = ANY(requested_material_ids)
     ORDER BY material.id
     FOR UPDATE OF material;

    SELECT count(*) INTO graph_count
      FROM public.material_inventory_policies AS policy
     WHERE policy.material_id = ANY(requested_material_ids);
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM policy.id
      FROM public.material_inventory_policies AS policy
     WHERE policy.material_id = ANY(requested_material_ids)
     ORDER BY policy.material_id, policy.effective_from, policy.id
     FOR UPDATE OF policy;

    SELECT count(*) INTO graph_count
      FROM public.inventory_lots AS lot
     WHERE lot.material_id = ANY(requested_material_ids);
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM lot.id
      FROM public.inventory_lots AS lot
     WHERE lot.material_id = ANY(requested_material_ids)
     ORDER BY lot.material_id, lot.id
     FOR UPDATE OF lot;

    SELECT count(*) INTO graph_count
      FROM public.qr_codes AS qr
     WHERE qr.object_type = 'material'
       AND qr.object_id = ANY(requested_material_ids);
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM qr.id
      FROM public.qr_codes AS qr
     WHERE qr.object_type = 'material'
       AND qr.object_id = ANY(requested_material_ids)
     ORDER BY qr.object_id, qr.id
     FOR UPDATE OF qr;
END
$$
"""
    )
    op.execute(
        f"ALTER FUNCTION {PG_FUNCTION_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )


def _apply_postgresql_function_acl() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{MIGRATION_ROLE}')
       OR NOT EXISTS (
            SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
       ) THEN
        RAISE EXCEPTION
            '0028 requires provisioned migration and API database roles';
    END IF;
END
$$
"""
    )
    op.execute(
        f"REVOKE ALL ON FUNCTION {PG_FUNCTION_SIGNATURE} FROM "
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
                'REVOKE ALL ON FUNCTION {PG_FUNCTION_SIGNATURE} FROM %I',
                role_name
            );
        END IF;
    END LOOP;
END
$$
"""
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {PG_FUNCTION_SIGNATURE} "
        f"TO {PRODUCTION_API_ROLE}"
    )
