"""Install bounded PostgreSQL owner-lock graphs for formal opening flows.

Revision ID: 20260831_0027
Revises: 20260831_0026
Create Date: 2026-08-31

The production API role intentionally has no UPDATE privilege on immutable
source, reference, or stocktake-evidence tables.  PostgreSQL nevertheless
requires an UPDATE-capable owner to take row locks on those relations.  This
revision exposes five migration-owned, fixed-SQL SECURITY DEFINER functions
that return no business data and lock only their documented graphs.

Every input collection is one-dimensional, NULL-free, and bounded.  Opening
scope owner/location arrays are equal-length parallel coordinates with unique
pairs; the remaining identifier arrays are individually unique.  Every
derived graph is also bounded before row locks are taken.  SQLite keeps its
existing single-process test semantics and therefore has no equivalent
functions.  No table or column privilege changes in this revision.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260831_0027"
down_revision: Union[str, Sequence[str], None] = "20260831_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
LOCK_GRAPH_ERROR = "formal PostgreSQL lock graph invariant violated"
MAXIMUM_LOCK_ROWS = 100_000
OPENING_CONTROL_ENTITY_TYPE = "oam_inventory_control"

PG_OPENING_CONTROL_IMPORT_FUNCTION = "rsc_lock_opening_control_import_0027"
PG_OPENING_START_REFERENCE_FUNCTION = (
    "rsc_lock_opening_stocktake_start_reference_0027"
)
PG_OPENING_TASK_EVIDENCE_FUNCTION = (
    "rsc_lock_opening_stocktake_task_evidence_0027"
)
PG_INVENTORY_REFERENCE_FUNCTION = "rsc_lock_inventory_reference_graph_0027"
PG_INVENTORY_SERIAL_FUNCTION = "rsc_lock_inventory_serial_graph_0027"

PG_FUNCTION_SIGNATURES = (
    f"public.{PG_OPENING_CONTROL_IMPORT_FUNCTION}(uuid, uuid)",
    f"public.{PG_OPENING_START_REFERENCE_FUNCTION}"
    "(uuid, uuid[], uuid[], uuid[], timestamptz)",
    f"public.{PG_OPENING_TASK_EVIDENCE_FUNCTION}(uuid, uuid)",
    f"public.{PG_INVENTORY_REFERENCE_FUNCTION}(uuid[], timestamptz)",
    f"public.{PG_INVENTORY_SERIAL_FUNCTION}(uuid[])",
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0027 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _create_postgresql_lock_functions()
    _apply_postgresql_function_acl()


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    for signature in reversed(PG_FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION {signature}")


def _create_postgresql_lock_functions() -> None:
    _create_opening_control_import_function()
    _create_opening_start_reference_function()
    _create_opening_task_evidence_function()
    _create_inventory_reference_function()
    _create_inventory_serial_function()
    for signature in PG_FUNCTION_SIGNATURES:
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {MIGRATION_ROLE}")


def _create_opening_control_import_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_OPENING_CONTROL_IMPORT_FUNCTION}(
    requested_source_system_id uuid,
    requested_sync_run_id uuid
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    graph_count bigint;
    locked_count bigint;
    expected_object_count bigint;
BEGIN
    IF requested_source_system_id IS NULL OR requested_sync_run_id IS NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    PERFORM source.id
      FROM public.source_systems AS source
     WHERE source.id = requested_source_system_id
     ORDER BY source.id
     FOR UPDATE OF source;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    PERFORM run.id
      FROM public.sync_runs AS run
     WHERE run.id = requested_sync_run_id
       AND run.source_system_id = requested_source_system_id
     ORDER BY run.id
     FOR UPDATE OF run;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    SELECT count(*)
      INTO graph_count
      FROM public.sync_batches AS batch
     WHERE batch.run_id = requested_sync_run_id
       AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}';
    IF graph_count < 1 OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM batch.id
      FROM public.sync_batches AS batch
     WHERE batch.run_id = requested_sync_run_id
       AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
     ORDER BY batch.sequence, batch.id
     FOR UPDATE OF batch;

    SELECT count(*)
      INTO graph_count
      FROM public.sync_inbox_events AS event
      JOIN public.sync_batches AS batch ON batch.id = event.batch_id
     WHERE batch.run_id = requested_sync_run_id
       AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND event.source_system_id = requested_source_system_id
       AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}';
    IF graph_count < 1 OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM event.id
      FROM public.sync_inbox_events AS event
      JOIN public.sync_batches AS batch ON batch.id = event.batch_id
     WHERE batch.run_id = requested_sync_run_id
       AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND event.source_system_id = requested_source_system_id
       AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
     ORDER BY event.id
     FOR UPDATE OF event;

    SELECT count(DISTINCT event.external_id)
      INTO expected_object_count
      FROM public.sync_inbox_events AS event
      JOIN public.sync_batches AS batch ON batch.id = event.batch_id
     WHERE batch.run_id = requested_sync_run_id
       AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND event.source_system_id = requested_source_system_id
       AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}';
    SELECT count(*)
      INTO locked_count
      FROM public.external_objects AS object
     WHERE object.source_system_id = requested_source_system_id
       AND object.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND EXISTS (
            SELECT 1
              FROM public.sync_inbox_events AS event
              JOIN public.sync_batches AS batch ON batch.id = event.batch_id
             WHERE batch.run_id = requested_sync_run_id
               AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.source_system_id = requested_source_system_id
               AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.external_id = object.external_id
       );
    IF locked_count <> expected_object_count
       OR locked_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM object.id
      FROM public.external_objects AS object
     WHERE object.source_system_id = requested_source_system_id
       AND object.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND EXISTS (
            SELECT 1
              FROM public.sync_inbox_events AS event
              JOIN public.sync_batches AS batch ON batch.id = event.batch_id
             WHERE batch.run_id = requested_sync_run_id
               AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.source_system_id = requested_source_system_id
               AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.external_id = object.external_id
       )
     ORDER BY object.id
     FOR UPDATE OF object;

    SELECT count(*)
      INTO graph_count
      FROM public.external_object_versions AS version
      JOIN public.external_objects AS object
        ON object.id = version.external_object_id
     WHERE object.source_system_id = requested_source_system_id
       AND object.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND EXISTS (
            SELECT 1
              FROM public.sync_inbox_events AS event
              JOIN public.sync_batches AS batch ON batch.id = event.batch_id
             WHERE batch.run_id = requested_sync_run_id
               AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.source_system_id = requested_source_system_id
               AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.external_id = object.external_id
       );
    IF graph_count < locked_count OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM version.id
      FROM public.external_object_versions AS version
      JOIN public.external_objects AS object
        ON object.id = version.external_object_id
     WHERE object.source_system_id = requested_source_system_id
       AND object.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
       AND EXISTS (
            SELECT 1
              FROM public.sync_inbox_events AS event
              JOIN public.sync_batches AS batch ON batch.id = event.batch_id
             WHERE batch.run_id = requested_sync_run_id
               AND batch.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.source_system_id = requested_source_system_id
               AND event.entity_type = '{OPENING_CONTROL_ENTITY_TYPE}'
               AND event.external_id = object.external_id
       )
     ORDER BY version.external_object_id, version.valid_from, version.id
     FOR UPDATE OF version;
END
$$
"""
    )


def _create_opening_start_reference_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_OPENING_START_REFERENCE_FUNCTION}(
    requested_region_org_id uuid,
    requested_owner_org_ids uuid[],
    requested_location_ids uuid[],
    requested_material_ids uuid[],
    requested_effective_at timestamptz
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
    requested_owner_count integer;
    requested_location_count integer;
    graph_count bigint;
    expected_material_count bigint;
    locked_material_ids uuid[];
BEGIN
    IF requested_region_org_id IS NULL
       OR requested_effective_at IS NULL
       OR NOT pg_catalog.isfinite(requested_effective_at) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    requested_count := cardinality(requested_owner_org_ids);
    IF requested_owner_org_ids IS NULL OR requested_count IS NULL
       OR requested_count < 1 OR requested_count > {MAXIMUM_LOCK_ROWS}
       OR array_ndims(requested_owner_org_ids) <> 1
       OR array_position(requested_owner_org_ids, NULL) IS NOT NULL
       OR requested_location_ids IS NULL
       OR cardinality(requested_location_ids) <> requested_count
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
      ) AS requested_scopes;
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(DISTINCT value) INTO requested_owner_count
      FROM unnest(requested_owner_org_ids) AS requested(value);
    SELECT count(DISTINCT value) INTO requested_location_count
      FROM unnest(requested_location_ids) AS requested(value);

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

    IF NOT EXISTS (
        SELECT 1
          FROM public.organizations AS region
         WHERE region.id = requested_region_org_id
           AND region.status = 'active'
           AND region.org_type = 'region_company'
    ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    IF (SELECT count(*) FROM public.organizations AS owner
         WHERE owner.id = ANY(requested_owner_org_ids)
           AND owner.status = 'active'
           AND owner.org_type = 'region_company') <>
       requested_owner_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    IF EXISTS (
        WITH RECURSIVE owner_path(owner_id, id, parent_id, status) AS (
            SELECT owner.id, owner.id, owner.parent_id, owner.status
              FROM public.organizations AS owner
             WHERE owner.id = ANY(requested_owner_org_ids)
            UNION
            SELECT path.owner_id, parent.id, parent.parent_id, parent.status
              FROM owner_path AS path
              JOIN public.organizations AS parent ON parent.id = path.parent_id
        )
        SELECT 1
          FROM unnest(requested_owner_org_ids) AS requested(owner_id)
         WHERE NOT EXISTS (
                   SELECT 1 FROM owner_path AS path
                    WHERE path.owner_id = requested.owner_id
                      AND path.id = requested_region_org_id
               )
            OR EXISTS (
                   SELECT 1 FROM owner_path AS path
                    WHERE path.owner_id = requested.owner_id
                      AND path.status <> 'active'
               )
    ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    -- All inventory flows take account rows before location rows.  Opening
    -- start already holds the ledger head, but preserving the same row order
    -- also protects callers that reuse this graph under a wider transaction.
    SELECT count(*) INTO graph_count
      FROM public.stock_accounts AS account
     WHERE EXISTS (
        SELECT 1
          FROM unnest(requested_owner_org_ids, requested_location_ids)
               AS requested(owner_org_id, location_id)
         WHERE requested.owner_org_id = account.owner_org_id
           AND requested.location_id = account.location_id
     );
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM account.id
      FROM public.stock_accounts AS account
     WHERE EXISTS (
        SELECT 1
          FROM unnest(requested_owner_org_ids, requested_location_ids)
               AS requested(owner_org_id, location_id)
         WHERE requested.owner_org_id = account.owner_org_id
           AND requested.location_id = account.location_id
     )
     ORDER BY account.id
     FOR UPDATE OF account;

    WITH RECURSIVE
    region_descendants(id, parent_id) AS (
        SELECT region.id, region.parent_id
          FROM public.organizations AS region
         WHERE region.id = requested_region_org_id
        UNION
        SELECT child.id, child.parent_id
          FROM public.organizations AS child
          JOIN region_descendants AS parent ON child.parent_id = parent.id
    ),
    location_path(id, parent_id, owner_org_id) AS (
        SELECT location.id, location.parent_id, location.owner_org_id
          FROM public.stock_locations AS location
         WHERE location.id = ANY(requested_location_ids)
        UNION
        SELECT parent.id, parent.parent_id, parent.owner_org_id
          FROM public.stock_locations AS parent
          JOIN location_path AS child ON parent.id = child.parent_id
    ),
    organization_seeds(id) AS (
        SELECT requested_region_org_id
        UNION SELECT value FROM unnest(requested_owner_org_ids) AS owner(value)
        UNION SELECT path.owner_org_id FROM location_path AS path
        UNION
        SELECT account.owner_org_id
          FROM public.stock_accounts AS account
         WHERE EXISTS (
            SELECT 1
              FROM unnest(requested_owner_org_ids, requested_location_ids)
                   AS requested(owner_org_id, location_id)
             WHERE requested.owner_org_id = account.owner_org_id
               AND requested.location_id = account.location_id
         )
    ),
    organization_ancestors(id, parent_id) AS (
        SELECT organization.id, organization.parent_id
          FROM public.organizations AS organization
          JOIN organization_seeds AS seed ON seed.id = organization.id
        UNION
        SELECT parent.id, parent.parent_id
          FROM public.organizations AS parent
          JOIN organization_ancestors AS child ON parent.id = child.parent_id
    ),
    organization_graph(id) AS (
        SELECT id FROM region_descendants
        UNION
        SELECT id FROM organization_ancestors
    )
    SELECT count(*) INTO graph_count FROM organization_graph;
    IF graph_count < 1 OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    IF (SELECT count(*) FROM public.stock_locations AS location
         WHERE location.id = ANY(requested_location_ids)
           AND location.status = 'active'
           AND location.location_type IN ('region', 'personal')) <>
       requested_location_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    WITH RECURSIVE location_graph(id, parent_id, owner_org_id, status) AS (
        SELECT location.id, location.parent_id, location.owner_org_id,
               location.status
          FROM public.stock_locations AS location
         WHERE location.id = ANY(requested_location_ids)
        UNION
        SELECT parent.id, parent.parent_id, parent.owner_org_id, parent.status
          FROM public.stock_locations AS parent
          JOIN location_graph AS child ON parent.id = child.parent_id
    )
    SELECT count(*) INTO graph_count FROM location_graph;
    IF graph_count < requested_location_count
       OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    IF EXISTS (
        WITH RECURSIVE
        location_graph(id, parent_id, owner_org_id, status) AS (
            SELECT location.id, location.parent_id, location.owner_org_id,
                   location.status
              FROM public.stock_locations AS location
             WHERE location.id = ANY(requested_location_ids)
            UNION
            SELECT parent.id, parent.parent_id, parent.owner_org_id, parent.status
              FROM public.stock_locations AS parent
              JOIN location_graph AS child ON parent.id = child.parent_id
        ),
        location_owner_path(location_id, id, parent_id, status) AS (
            SELECT location.id, owner.id, owner.parent_id, owner.status
              FROM location_graph AS location
              JOIN public.organizations AS owner
                ON owner.id = location.owner_org_id
            UNION
            SELECT path.location_id, parent.id, parent.parent_id, parent.status
              FROM location_owner_path AS path
              JOIN public.organizations AS parent ON parent.id = path.parent_id
        )
        SELECT 1
          FROM location_graph AS location
         WHERE location.status <> 'active'
            OR NOT EXISTS (
                SELECT 1 FROM location_owner_path AS path
                 WHERE path.location_id = location.id
                   AND path.id = requested_region_org_id
            )
            OR EXISTS (
                SELECT 1 FROM location_owner_path AS path
                 WHERE path.location_id = location.id
                   AND path.status <> 'active'
            )
    ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM location.id
      FROM public.stock_locations AS location
     WHERE location.id IN (
        WITH RECURSIVE location_graph(id, parent_id) AS (
            SELECT selected.id, selected.parent_id
              FROM public.stock_locations AS selected
             WHERE selected.id = ANY(requested_location_ids)
            UNION
            SELECT parent.id, parent.parent_id
              FROM public.stock_locations AS parent
              JOIN location_graph AS child ON parent.id = child.parent_id
        )
        SELECT id FROM location_graph
     )
     ORDER BY location.id
     FOR UPDATE OF location;

    PERFORM organization.id
      FROM public.organizations AS organization
     WHERE organization.id IN (
        WITH RECURSIVE
        region_descendants(id, parent_id) AS (
            SELECT region.id, region.parent_id
              FROM public.organizations AS region
             WHERE region.id = requested_region_org_id
            UNION
            SELECT child.id, child.parent_id
              FROM public.organizations AS child
              JOIN region_descendants AS parent ON child.parent_id = parent.id
        ),
        location_path(id, parent_id, owner_org_id) AS (
            SELECT location.id, location.parent_id, location.owner_org_id
              FROM public.stock_locations AS location
             WHERE location.id = ANY(requested_location_ids)
            UNION
            SELECT parent.id, parent.parent_id, parent.owner_org_id
              FROM public.stock_locations AS parent
              JOIN location_path AS child ON parent.id = child.parent_id
        ),
        organization_seeds(id) AS (
            SELECT requested_region_org_id
            UNION SELECT value FROM unnest(requested_owner_org_ids) AS owner(value)
            UNION SELECT path.owner_org_id FROM location_path AS path
            UNION
            SELECT account.owner_org_id
              FROM public.stock_accounts AS account
             WHERE EXISTS (
                SELECT 1
                  FROM unnest(requested_owner_org_ids, requested_location_ids)
                       AS requested(owner_org_id, location_id)
                 WHERE requested.owner_org_id = account.owner_org_id
                   AND requested.location_id = account.location_id
             )
        ),
        organization_ancestors(id, parent_id) AS (
            SELECT organization_row.id, organization_row.parent_id
              FROM public.organizations AS organization_row
              JOIN organization_seeds AS seed ON seed.id = organization_row.id
            UNION
            SELECT parent.id, parent.parent_id
              FROM public.organizations AS parent
              JOIN organization_ancestors AS child ON parent.id = child.parent_id
        )
        SELECT id FROM region_descendants
        UNION
        SELECT id FROM organization_ancestors
     )
     ORDER BY organization.id
     FOR UPDATE OF organization;

    SELECT count(*) INTO graph_count
      FROM public.custody_assignments AS custody
     WHERE custody.location_id = ANY(requested_location_ids)
       AND custody.valid_from <= requested_effective_at
       AND (custody.valid_to IS NULL
            OR custody.valid_to > requested_effective_at);
    IF graph_count > requested_location_count
       OR graph_count > {MAXIMUM_LOCK_ROWS}
       OR EXISTS (
            SELECT 1
              FROM public.custody_assignments AS custody
             WHERE custody.location_id = ANY(requested_location_ids)
               AND custody.valid_from <= requested_effective_at
               AND (custody.valid_to IS NULL
                    OR custody.valid_to > requested_effective_at)
             GROUP BY custody.location_id
            HAVING count(*) > 1
       ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM custody.id
      FROM public.custody_assignments AS custody
     WHERE custody.location_id = ANY(requested_location_ids)
       AND custody.valid_from <= requested_effective_at
       AND (custody.valid_to IS NULL
            OR custody.valid_to > requested_effective_at)
     ORDER BY custody.location_id, custody.id
     FOR UPDATE OF custody;

    SELECT coalesce(
               array_agg(expected.material_id ORDER BY expected.material_id),
               ARRAY[]::uuid[]
           )
      INTO locked_material_ids
      FROM (
        SELECT value AS material_id
          FROM unnest(requested_material_ids) AS requested(value)
        UNION
        SELECT account.material_id
          FROM public.stock_accounts AS account
         WHERE EXISTS (
            SELECT 1
              FROM unnest(requested_owner_org_ids, requested_location_ids)
                   AS requested(owner_org_id, location_id)
             WHERE requested.owner_org_id = account.owner_org_id
               AND requested.location_id = account.location_id
         )
      ) AS expected;
    expected_material_count := cardinality(locked_material_ids);
    IF expected_material_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(*) INTO graph_count
      FROM public.materials AS material
     WHERE material.id = ANY(locked_material_ids);
    IF graph_count <> expected_material_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM material.id
      FROM public.materials AS material
     WHERE material.id = ANY(locked_material_ids)
     ORDER BY material.id
     FOR UPDATE OF material;

    SELECT count(*) INTO graph_count
      FROM public.material_inventory_policies AS policy
     WHERE policy.material_id = ANY(locked_material_ids)
       AND policy.effective_from <= requested_effective_at
       AND (policy.effective_to IS NULL
            OR policy.effective_to > requested_effective_at);
    IF graph_count <> expected_material_count
       OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM policy.id
      FROM public.material_inventory_policies AS policy
     WHERE policy.material_id = ANY(locked_material_ids)
       AND policy.effective_from <= requested_effective_at
       AND (policy.effective_to IS NULL
            OR policy.effective_to > requested_effective_at)
     ORDER BY policy.material_id, policy.effective_from, policy.id
     FOR UPDATE OF policy;

    SELECT count(*) INTO graph_count
      FROM public.inventory_lots AS lot
     WHERE lot.material_id = ANY(locked_material_ids);
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM lot.id
      FROM public.inventory_lots AS lot
     WHERE lot.material_id = ANY(locked_material_ids)
     ORDER BY lot.material_id, lot.id
     FOR UPDATE OF lot;

    SELECT count(*) INTO graph_count
      FROM public.qr_codes AS qr
     WHERE qr.object_type = 'material'
       AND qr.object_id = ANY(locked_material_ids);
    IF graph_count > expected_material_count
       OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM qr.id
      FROM public.qr_codes AS qr
     WHERE qr.object_type = 'material'
       AND qr.object_id = ANY(locked_material_ids)
     ORDER BY qr.object_id, qr.id
     FOR UPDATE OF qr;
END
$$
"""
    )


def _create_opening_task_evidence_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_OPENING_TASK_EVIDENCE_FUNCTION}(
    requested_task_id uuid,
    requested_round_id uuid
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    graph_count bigint;
    locked_count bigint;
BEGIN
    IF requested_task_id IS NULL OR requested_round_id IS NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM task.id
      FROM public.stocktake_tasks AS task
     WHERE task.id = requested_task_id
       AND task.task_type = 'opening'
     ORDER BY task.id
     FOR UPDATE OF task;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.stocktake_rounds AS round_row
         WHERE round_row.id = requested_round_id
           AND round_row.task_id = requested_task_id
    ) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    SELECT
        (SELECT count(*) FROM public.stocktake_scopes
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.inventory_freezes
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_control_snapshot_lines
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_snapshot_lines
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_rounds
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_lines
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_count_observations
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_scope_count_completions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_round_submissions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_observation_dispositions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_difference_set_completions
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_differences
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_reviews
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_review_items
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_recount_cases
          WHERE task_id = requested_task_id)
      + (SELECT count(*) FROM public.stocktake_recount_scope_assignments
          WHERE task_id = requested_task_id)
      + (SELECT count(*)
           FROM public.stocktake_count_serials AS serial_row
           JOIN public.stocktake_count_lines AS count_line
             ON count_line.id = serial_row.count_line_id
            AND count_line.round_id = serial_row.round_id
          WHERE count_line.task_id = requested_task_id)
      INTO graph_count;
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    PERFORM scope.id FROM public.stocktake_scopes AS scope
     WHERE scope.task_id = requested_task_id
     ORDER BY scope.scope_no, scope.id FOR UPDATE OF scope;
    PERFORM freeze.id FROM public.inventory_freezes AS freeze
     WHERE freeze.task_id = requested_task_id
     ORDER BY freeze.stocktake_scope_id, freeze.id FOR UPDATE OF freeze;
    PERFORM control.id FROM public.stocktake_control_snapshot_lines AS control
     WHERE control.task_id = requested_task_id
     ORDER BY control.line_no, control.id FOR UPDATE OF control;
    PERFORM snapshot.id FROM public.stocktake_snapshot_lines AS snapshot
     WHERE snapshot.task_id = requested_task_id
     ORDER BY snapshot.scope_id, snapshot.stock_account_id, snapshot.id
     FOR UPDATE OF snapshot;
    PERFORM round_row.id FROM public.stocktake_rounds AS round_row
     WHERE round_row.task_id = requested_task_id
     ORDER BY round_row.round_no, round_row.id FOR UPDATE OF round_row;
    PERFORM count_line.id FROM public.stocktake_count_lines AS count_line
     WHERE count_line.task_id = requested_task_id
     ORDER BY count_line.round_id, count_line.scope_id,
              count_line.stock_account_id, count_line.id
     FOR UPDATE OF count_line;
    PERFORM serial_row.serial_id
      FROM public.stocktake_count_serials AS serial_row
      JOIN public.stocktake_count_lines AS count_line
        ON count_line.id = serial_row.count_line_id
       AND count_line.round_id = serial_row.round_id
     WHERE count_line.task_id = requested_task_id
     ORDER BY serial_row.round_id, serial_row.count_line_id,
              serial_row.serial_id
     FOR UPDATE OF serial_row;
    PERFORM observation.id
      FROM public.stocktake_count_observations AS observation
     WHERE observation.task_id = requested_task_id
     ORDER BY observation.round_id, observation.scope_id,
              observation.observation_no, observation.id
     FOR UPDATE OF observation;
    PERFORM completion.id
      FROM public.stocktake_scope_count_completions AS completion
     WHERE completion.task_id = requested_task_id
     ORDER BY completion.round_id, completion.scope_id, completion.id
     FOR UPDATE OF completion;
    PERFORM submission.id
      FROM public.stocktake_round_submissions AS submission
     WHERE submission.task_id = requested_task_id
     ORDER BY submission.round_id, submission.id FOR UPDATE OF submission;
    PERFORM disposition.id
      FROM public.stocktake_observation_dispositions AS disposition
     WHERE disposition.task_id = requested_task_id
     ORDER BY disposition.round_id, disposition.observation_id, disposition.id
     FOR UPDATE OF disposition;
    PERFORM completion.id
      FROM public.stocktake_difference_set_completions AS completion
     WHERE completion.task_id = requested_task_id
     ORDER BY completion.round_id, completion.id FOR UPDATE OF completion;
    PERFORM difference.id
      FROM public.stocktake_differences AS difference
     WHERE difference.task_id = requested_task_id
     ORDER BY difference.round_id, difference.difference_no, difference.id
     FOR UPDATE OF difference;
    PERFORM review.id FROM public.stocktake_reviews AS review
     WHERE review.task_id = requested_task_id
     ORDER BY review.round_id, review.review_stage, review.id
     FOR UPDATE OF review;
    PERFORM item.review_id FROM public.stocktake_review_items AS item
     WHERE item.task_id = requested_task_id
     ORDER BY item.round_id, item.review_id, item.difference_id
     FOR UPDATE OF item;
    PERFORM recount.id FROM public.stocktake_recount_cases AS recount
     WHERE recount.task_id = requested_task_id
     ORDER BY recount.next_round_no, recount.id FOR UPDATE OF recount;
    PERFORM assignment.id
      FROM public.stocktake_recount_scope_assignments AS assignment
     WHERE assignment.task_id = requested_task_id
     ORDER BY assignment.recount_case_id, assignment.scope_id, assignment.id
     FOR UPDATE OF assignment;
END
$$
"""
    )


def _create_inventory_reference_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_INVENTORY_REFERENCE_FUNCTION}(
    requested_account_ids uuid[],
    requested_effective_at timestamptz
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
    graph_count bigint;
    expected_location_count bigint;
    material_count bigint;
    expected_material_count bigint;
BEGIN
    requested_count := cardinality(requested_account_ids);
    IF requested_account_ids IS NULL OR requested_count IS NULL
       OR requested_count > {MAXIMUM_LOCK_ROWS}
       OR (requested_count > 0 AND array_ndims(requested_account_ids) <> 1)
       OR array_position(requested_account_ids, NULL) IS NOT NULL
       OR requested_effective_at IS NULL
       OR NOT pg_catalog.isfinite(requested_effective_at) THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(DISTINCT value) INTO unique_count
      FROM unnest(requested_account_ids) AS requested(value);
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;

    SELECT count(*) INTO graph_count FROM public.stock_accounts AS account
     WHERE account.id = ANY(requested_account_ids);
    IF graph_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM account.id FROM public.stock_accounts AS account
     WHERE account.id = ANY(requested_account_ids)
     ORDER BY account.id FOR UPDATE OF account;

    SELECT count(DISTINCT account.location_id)
      INTO expected_location_count
      FROM public.stock_accounts AS account
     WHERE account.id = ANY(requested_account_ids);
    SELECT count(*) INTO graph_count FROM public.stock_locations AS location
     WHERE location.id IN (
        SELECT account.location_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
     );
    IF graph_count <> expected_location_count
       OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM location.id FROM public.stock_locations AS location
     WHERE location.id IN (
        SELECT account.location_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
     )
     ORDER BY location.id FOR UPDATE OF location;

    SELECT count(DISTINCT account.material_id)
      INTO expected_material_count
      FROM public.stock_accounts AS account
     WHERE account.id = ANY(requested_account_ids);
    SELECT count(*) INTO material_count FROM public.materials AS material
     WHERE material.id IN (
        SELECT account.material_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
     );
    IF material_count <> expected_material_count
       OR material_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM material.id FROM public.materials AS material
     WHERE material.id IN (
        SELECT account.material_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
     )
     ORDER BY material.id FOR UPDATE OF material;

    SELECT count(*) INTO graph_count
      FROM public.material_inventory_policies AS policy
     WHERE policy.material_id IN (
        SELECT account.material_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
     )
       AND policy.effective_from <= requested_effective_at
       AND (policy.effective_to IS NULL
            OR policy.effective_to > requested_effective_at);
    IF graph_count <> material_count OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM policy.id FROM public.material_inventory_policies AS policy
     WHERE policy.material_id IN (
        SELECT account.material_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
     )
       AND policy.effective_from <= requested_effective_at
       AND (policy.effective_to IS NULL
            OR policy.effective_to > requested_effective_at)
     ORDER BY policy.material_id, policy.effective_from, policy.id
     FOR UPDATE OF policy;

    SELECT count(*) INTO graph_count FROM public.inventory_lots AS lot
     WHERE lot.id IN (
        SELECT account.lot_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
           AND account.lot_id IS NOT NULL
     );
    IF graph_count <>
       (SELECT count(DISTINCT account.lot_id)
          FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
           AND account.lot_id IS NOT NULL)
       OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM lot.id FROM public.inventory_lots AS lot
     WHERE lot.id IN (
        SELECT account.lot_id FROM public.stock_accounts AS account
         WHERE account.id = ANY(requested_account_ids)
           AND account.lot_id IS NOT NULL
     )
     ORDER BY lot.id FOR UPDATE OF lot;
END
$$
"""
    )


def _create_inventory_serial_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_INVENTORY_SERIAL_FUNCTION}(
    requested_serial_ids uuid[]
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
    graph_count bigint;
BEGIN
    requested_count := cardinality(requested_serial_ids);
    IF requested_serial_ids IS NULL OR requested_count IS NULL
       OR requested_count > {MAXIMUM_LOCK_ROWS}
       OR (requested_count > 0 AND array_ndims(requested_serial_ids) <> 1)
       OR array_position(requested_serial_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(DISTINCT value) INTO unique_count
      FROM unnest(requested_serial_ids) AS requested(value);
    IF unique_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    SELECT count(*) INTO graph_count FROM public.inventory_serials AS serial
     WHERE serial.id = ANY(requested_serial_ids);
    IF graph_count <> requested_count THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM serial.id FROM public.inventory_serials AS serial
     WHERE serial.id = ANY(requested_serial_ids)
     ORDER BY serial.id FOR UPDATE OF serial;
    SELECT count(*) INTO graph_count
      FROM public.serial_current_positions AS position
     WHERE position.serial_id = ANY(requested_serial_ids);
    IF graph_count > requested_count OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM position.serial_id
      FROM public.serial_current_positions AS position
     WHERE position.serial_id = ANY(requested_serial_ids)
     ORDER BY position.serial_id FOR UPDATE OF position;
    SELECT count(*) INTO graph_count FROM public.qr_codes AS qr
     WHERE qr.object_type = 'serial'
       AND qr.object_id = ANY(requested_serial_ids);
    IF graph_count > requested_count OR graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION '{LOCK_GRAPH_ERROR}';
    END IF;
    PERFORM qr.id FROM public.qr_codes AS qr
     WHERE qr.object_type = 'serial'
       AND qr.object_id = ANY(requested_serial_ids)
     ORDER BY qr.object_id, qr.id FOR UPDATE OF qr;
END
$$
"""
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
            '0027 requires provisioned migration and API database roles';
    END IF;
END
$$
"""
    )
    for signature in PG_FUNCTION_SIGNATURES:
        op.execute(
            f"REVOKE ALL ON FUNCTION {signature} FROM "
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
                'REVOKE ALL ON FUNCTION {signature} FROM %I', role_name
            );
        END IF;
    END LOOP;
END
$$
"""
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {', '.join(PG_FUNCTION_SIGNATURES)} "
        f"TO {PRODUCTION_API_ROLE}"
    )
