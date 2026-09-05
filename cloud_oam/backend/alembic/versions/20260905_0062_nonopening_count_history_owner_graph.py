"""Own immutable multi-round non-opening count history in one bounded graph.

Revision ID: 20260905_0062
Revises: 20260905_0061

This migration adds a read-only, migration-owned lock capability. It never
rewrites historical migrations, grants UPDATE, or treats locks as proof of
authorization. Runtime readiness is advanced atomically with the capability.
"""

from __future__ import annotations

from alembic import context, op

revision: str = "20260905_0062"
down_revision: str | None = "20260905_0061"
branch_labels: str | None = None
depends_on: str | None = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
MAXIMUM_LOCK_ROWS = 100_000
INVENTORY_LEDGER_HEAD_ID = "40000000-0000-4000-8000-000000000001"
LOCK_FUNCTION = "rsc_lock_nonopening_stocktake_count_history_graph_0062"
LOCK_SIGNATURE = f"public.{LOCK_FUNCTION}(uuid, uuid, text)"
LOCK_BODY_SHA256 = "53fbad62f31d9fa93103bb376ba67f0da54ff1ac937b9d6e49bc6e752580393a"
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_BODY_SHA256_0061 = (
    "900dd22c6ff47e807dddd6dd6110449e433dde3bc410057ad03472e7216bc53c"
)
RUNTIME_READY_BODY_SHA256_0062 = "d019a572ad39a3570ca175479ee50a4a7987eff3a0655c5c910c5b83744ea04d"
PRINCIPAL_HELPER_BODY_SHA256 = (
    "4b7d3e47541a1de999f33af65d95a697ffd44cfea8930f6fbeb265d4fd727aaf"
)
REVIEW_HELPER_BODY_SHA256 = (
    "ce7dda6f207c9a17bfde049749aa6e689e3f84d9e2c3892c66f17ac15c411ee3"
)
SEAL_BODY_SHA256 = (
    "7a5ae0c9fd3117cb784dce34c7882b8301759e846321ac7a6233235c6333222c"
)
CONTROL_GUARD_BODY_SHA256 = (
    "40d7a6223b6250316f7bed15c0dd4749238066865f7581156646c5b89b509249"
)
CATALOG_ERROR = "0062 count history owner catalog mismatch"

LOCK_TABLES = (
    "alembic_version",
    "auth_identities",
    "custody_assignments",
    "document_attachments",
    "files",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_serials",
    "inventory_transactions",
    "material_inventory_policies",
    "materials",
    "organizations",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "state_transition_events",
    "stock_accounts",
    "stock_locations",
    "stocktake_close_completions",
    "stocktake_close_reconciliation_accounts",
    "stocktake_close_reconciliation_completions",
    "stocktake_close_reconciliation_serials",
    "stocktake_close_transition_acks",
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_effective_approval_completions",
    "stocktake_effective_approval_items",
    "stocktake_effective_approval_scopes",
    "stocktake_observation_dispositions",
    "stocktake_posting_completion_items",
    "stocktake_posting_completions",
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
    "stocktake_start_completions",
    "stocktake_tasks",
    "users",
)


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        # SQLite has no owner ACL/row locking equivalent and claims none.
        return
    _lock_postgresql_boundary()
    _verify_postgresql_catalog(expected_ready_hash=RUNTIME_READY_BODY_SHA256_0061, installed=False)
    op.execute(_postgresql_lock_function_sql())
    op.execute(f"REVOKE ALL ON FUNCTION {LOCK_SIGNATURE} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION {LOCK_SIGNATURE} OWNER TO {MIGRATION_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {LOCK_SIGNATURE} TO {PRODUCTION_API_ROLE}")
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0061,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0062,
        old_revision=down_revision,
        new_revision=revision,
    )
    _verify_postgresql_catalog(expected_ready_hash=RUNTIME_READY_BODY_SHA256_0062, installed=True)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0062 downgrade requires online catalog and readiness checks")
    if _dialect_name() == "sqlite":
        return
    _lock_postgresql_boundary()
    _verify_postgresql_catalog(expected_ready_hash=RUNTIME_READY_BODY_SHA256_0062, installed=True)
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0062,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0061,
        old_revision=revision,
        new_revision=down_revision,
    )
    op.execute(f"DROP FUNCTION {LOCK_SIGNATURE}")
    _verify_postgresql_catalog(expected_ready_hash=RUNTIME_READY_BODY_SHA256_0061, installed=False)
    # No facts are removed. Applications requiring 0062 refuse the old schema
    # and missing manifest capability; old applications keep their own checks.


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0062 supports only PostgreSQL and SQLite")
    return dialect


def _lock_postgresql_boundary() -> None:
    op.execute("LOCK TABLE " + ", ".join(f"public.{table}" for table in LOCK_TABLES)
               + " IN ACCESS EXCLUSIVE MODE")


def _postgresql_lock_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{LOCK_FUNCTION}(
    requested_task_id uuid,
    requested_round_id uuid,
    requested_actor_user_id text
)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    account_ids uuid[] := ARRAY[]::uuid[];
    attachment_ids uuid[] := ARRAY[]::uuid[];
    completion_ids uuid[] := ARRAY[]::uuid[];
    custody_ids uuid[] := ARRAY[]::uuid[];
    file_ids uuid[] := ARRAY[]::uuid[];
    graph_count bigint := 0;
    location_ids uuid[] := ARRAY[]::uuid[];
    lot_ids uuid[] := ARRAY[]::uuid[];
    material_ids uuid[] := ARRAY[]::uuid[];
    max_count_cursor bigint;
    movement_ids uuid[] := ARRAY[]::uuid[];
    organization_ids uuid[] := ARRAY[]::uuid[];
    people_ids uuid[] := ARRAY[]::uuid[];
    policy_ids uuid[] := ARRAY[]::uuid[];
    principal_user_ids text[] := ARRAY[]::text[];
    replay_transaction_ids uuid[] := ARRAY[]::uuid[];
    scoped_account_ids uuid[] := ARRAY[]::uuid[];
    serial_ids uuid[] := ARRAY[]::uuid[];
    task_cutoff_at timestamptz;
    task_cutoff_cursor bigint;
    task_region_id uuid;
    locked_count bigint;
BEGIN
    IF requested_task_id IS NULL OR requested_round_id IS NULL THEN
        RAISE EXCEPTION 'non-opening count history lock graph invariant violated';
    END IF;

    -- The ledger head is deliberately the first row lock.  Every legitimate
    -- inventory posting/account creation path shares this serialization point.
    PERFORM head.id
      FROM public.inventory_ledger_heads AS head
     WHERE head.id = '{INVENTORY_LEDGER_HEAD_ID}'::uuid
       AND head.stream_key = 'inventory'
     ORDER BY head.id FOR UPDATE OF head;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening count history lock graph invariant violated';
    END IF;

    SELECT task.region_org_id, task.cutoff_at, task.cutoff_ledger_cursor
      INTO task_region_id, task_cutoff_at, task_cutoff_cursor
      FROM public.stocktake_tasks AS task
     WHERE task.id = requested_task_id
       AND task.task_type IN {NONOPENING_SQL}
       AND task.status IN ('counting', 'submitted', 'region_review', 'hq_review',
                           'recount_required', 'approved', 'posted', 'closed')
       AND task.cutoff_at IS NOT NULL
       AND task.cutoff_ledger_cursor IS NOT NULL
     ORDER BY task.id FOR UPDATE OF task;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening count history lock graph invariant violated';
    END IF;

    SELECT COALESCE(
               pg_catalog.array_agg(user_id ORDER BY user_id), ARRAY[]::text[])
      INTO principal_user_ids
      FROM (
          SELECT requested_actor_user_id AS user_id
          UNION
          SELECT scope.assignee_user_id
            FROM public.stocktake_scopes AS scope
           WHERE scope.task_id = requested_task_id
          UNION SELECT created_by_user_id FROM public.stocktake_tasks
           WHERE id = requested_task_id AND created_by_user_id IS NOT NULL
          UNION SELECT started_by_user_id FROM public.stocktake_start_completions
           WHERE task_id = requested_task_id AND started_by_user_id IS NOT NULL
          UNION SELECT submitted_by_user_id FROM public.stocktake_rounds
           WHERE task_id = requested_task_id AND submitted_by_user_id IS NOT NULL
          UNION SELECT counted_by_user_id FROM public.stocktake_count_lines
           WHERE task_id = requested_task_id AND counted_by_user_id IS NOT NULL
          UNION SELECT counted_by_user_id FROM public.stocktake_count_observations
           WHERE task_id = requested_task_id AND counted_by_user_id IS NOT NULL
          UNION SELECT completed_by_user_id FROM public.stocktake_scope_count_completions
           WHERE task_id = requested_task_id AND completed_by_user_id IS NOT NULL
          UNION SELECT submitted_by_user_id FROM public.stocktake_round_submissions
           WHERE task_id = requested_task_id AND submitted_by_user_id IS NOT NULL
          UNION SELECT completed_by_user_id FROM public.stocktake_difference_set_completions
           WHERE task_id = requested_task_id AND completed_by_user_id IS NOT NULL
          UNION SELECT decided_by_user_id FROM public.stocktake_observation_dispositions
           WHERE task_id = requested_task_id AND decided_by_user_id IS NOT NULL
          UNION SELECT reviewer_user_id FROM public.stocktake_reviews
           WHERE task_id = requested_task_id AND reviewer_user_id IS NOT NULL
          UNION SELECT opened_by_user_id FROM public.stocktake_recount_cases
           WHERE task_id = requested_task_id AND opened_by_user_id IS NOT NULL
          UNION SELECT assignee_user_id FROM public.stocktake_recount_scope_assignments
           WHERE task_id = requested_task_id AND assignee_user_id IS NOT NULL
          UNION SELECT completed_by_user_id FROM public.stocktake_effective_approval_completions
           WHERE task_id = requested_task_id AND completed_by_user_id IS NOT NULL
          UNION SELECT posted_by_user_id FROM public.stocktake_postings
           WHERE task_id = requested_task_id AND posted_by_user_id IS NOT NULL
          UNION SELECT posted_by_user_id FROM public.stocktake_posting_completions
           WHERE task_id = requested_task_id AND posted_by_user_id IS NOT NULL
          UNION SELECT reconciled_by_user_id FROM public.stocktake_close_reconciliation_completions
           WHERE task_id = requested_task_id AND reconciled_by_user_id IS NOT NULL
          UNION SELECT closed_by_user_id FROM public.stocktake_close_completions
           WHERE task_id = requested_task_id AND closed_by_user_id IS NOT NULL
          UNION SELECT created_by_user_id FROM public.inventory_freezes
           WHERE task_id = requested_task_id AND created_by_user_id IS NOT NULL
          UNION SELECT released_by_user_id FROM public.inventory_freezes
           WHERE task_id = requested_task_id AND released_by_user_id IS NOT NULL
      ) AS principal_union;
    IF requested_actor_user_id IS NULL
       OR pg_catalog.length(requested_actor_user_id) NOT BETWEEN 1 AND 36
       OR pg_catalog.btrim(requested_actor_user_id) <> requested_actor_user_id
       OR pg_catalog.cardinality(principal_user_ids) NOT BETWEEN 1 AND 1000
       OR pg_catalog.array_position(principal_user_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'non-opening count history principal graph invariant violated';
    END IF;
    IF (SELECT pg_catalog.count(*) FROM public.users AS app_user
         WHERE app_user.id::text = ANY(principal_user_ids)) <>
       pg_catalog.cardinality(principal_user_ids) THEN
        RAISE EXCEPTION 'non-opening count history principal reference missing';
    END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(principal_user_ids);

    PERFORM round_row.id
      FROM public.stocktake_rounds AS round_row
     WHERE round_row.id = requested_round_id
       AND round_row.task_id = requested_task_id
       AND ((round_row.round_no = 1 AND round_row.round_type = 'initial')
            OR (round_row.round_no > 1 AND round_row.round_type = 'recount'))
       AND round_row.status IN ('counting', 'submitted')
     ORDER BY round_row.id FOR UPDATE OF round_row;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening count history lock graph invariant violated';
    END IF;

    -- The absence sentinel is legal before any scope has completed. Historical
    -- rounds and partial scopes use the maximum of ALL task completions.
    SELECT COALESCE(pg_catalog.max(completion.count_ledger_cursor), task_cutoff_cursor),
           COALESCE(pg_catalog.array_agg(completion.id ORDER BY completion.id), ARRAY[]::uuid[])
      INTO max_count_cursor, completion_ids
      FROM public.stocktake_scope_count_completions AS completion
     WHERE completion.task_id = requested_task_id;
    IF max_count_cursor < task_cutoff_cursor
       OR EXISTS (
           SELECT 1 FROM public.stocktake_scope_count_completions AS completion
            WHERE completion.task_id = requested_task_id
              AND (completion.count_ledger_cursor IS NULL
                   OR completion.count_ledger_cursor < task_cutoff_cursor)
       ) OR max_count_cursor >= (
           SELECT head.next_cursor FROM public.inventory_ledger_heads AS head
            WHERE head.id = '{INVENTORY_LEDGER_HEAD_ID}'::uuid
       ) THEN
        RAISE EXCEPTION 'non-opening count history cursor invariant violated';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stocktake_control_snapshot_lines AS control_line
         WHERE control_line.task_id = requested_task_id
    ) THEN
        RAISE EXCEPTION 'non-opening count history contains forbidden control evidence';
    END IF;

    SELECT COALESCE(
               pg_catalog.array_agg(id ORDER BY id), ARRAY[]::uuid[])
      INTO scoped_account_ids
      FROM (
          SELECT account.id
            FROM public.stock_accounts AS account
           WHERE EXISTS (
               SELECT 1 FROM public.stocktake_scopes AS scope
                WHERE scope.task_id = requested_task_id
                  AND scope.owner_org_id = account.owner_org_id
                  AND scope.location_id = account.location_id
           )
          UNION
          SELECT snapshot.stock_account_id
            FROM public.stocktake_snapshot_lines AS snapshot
           WHERE snapshot.task_id = requested_task_id
      ) AS scoped_seed;

    SELECT COALESCE(pg_catalog.array_agg(DISTINCT id ORDER BY id), ARRAY[]::uuid[])
      INTO replay_transaction_ids FROM (
          SELECT transaction_row.id
            FROM public.inventory_transactions AS transaction_row
            JOIN public.inventory_movements AS movement ON movement.transaction_id = transaction_row.id
           WHERE transaction_row.ledger_cursor > task_cutoff_cursor
             AND transaction_row.ledger_cursor <= max_count_cursor
             AND (movement.from_account_id = ANY(scoped_account_ids)
                  OR movement.to_account_id = ANY(scoped_account_ids))
          UNION SELECT posting.inventory_transaction_id FROM public.stocktake_postings AS posting
           WHERE posting.task_id = requested_task_id
      ) AS transaction_union WHERE id IS NOT NULL;
    SELECT COALESCE(pg_catalog.array_agg(movement.id ORDER BY movement.id), ARRAY[]::uuid[])
      INTO movement_ids FROM public.inventory_movements AS movement
     WHERE movement.transaction_id = ANY(replay_transaction_ids);
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT id ORDER BY id), ARRAY[]::uuid[])
      INTO account_ids FROM (
          SELECT value.id FROM pg_catalog.unnest(scoped_account_ids) AS value(id)
          UNION SELECT from_account_id FROM public.inventory_movements WHERE id = ANY(movement_ids)
          UNION SELECT to_account_id FROM public.inventory_movements WHERE id = ANY(movement_ids)
          UNION SELECT stock_account_id FROM public.stocktake_count_lines WHERE task_id = requested_task_id
          UNION SELECT expected_account_id FROM public.stocktake_differences WHERE task_id = requested_task_id
          UNION SELECT observed_account_id FROM public.stocktake_differences WHERE task_id = requested_task_id
          UNION SELECT stock_account_id FROM public.stocktake_close_reconciliation_accounts WHERE task_id = requested_task_id
          UNION SELECT physical_account_id_at_count FROM public.stocktake_close_reconciliation_serials WHERE task_id = requested_task_id
          UNION SELECT expected_current_account_id FROM public.stocktake_close_reconciliation_serials WHERE task_id = requested_task_id
          UNION SELECT current_position_account_id FROM public.stocktake_close_reconciliation_serials WHERE task_id = requested_task_id
      ) AS account_union WHERE id IS NOT NULL;
    IF pg_catalog.cardinality(account_ids) > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION 'non-opening count history lock graph exceeds 100000 rows';
    END IF;
    PERFORM account.id FROM public.stock_accounts AS account
     WHERE account.id = ANY(account_ids)
     ORDER BY account.id FOR UPDATE OF account;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> pg_catalog.cardinality(account_ids) THEN
        RAISE EXCEPTION 'non-opening count history lock graph invariant violated';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = requested_task_id
           AND pg_catalog.jsonb_typeof(snapshot.serial_snapshot_jsonb) <> 'array'
    ) OR EXISTS (
        SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
         CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(snapshot.serial_snapshot_jsonb) AS element(value)
         WHERE snapshot.task_id = requested_task_id
           AND (pg_catalog.jsonb_typeof(element.value) <> 'object'
                OR pg_catalog.jsonb_typeof(element.value->'serial_id') IS DISTINCT FROM 'string')
    ) THEN
        RAISE EXCEPTION 'non-opening count history serial snapshot malformed';
    END IF;
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT serial_id ORDER BY serial_id), ARRAY[]::uuid[])
      INTO serial_ids FROM (
          SELECT (element.value->>'serial_id')::uuid AS serial_id
            FROM public.stocktake_snapshot_lines AS snapshot
            CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(snapshot.serial_snapshot_jsonb) AS element(value)
           WHERE snapshot.task_id = requested_task_id
          UNION SELECT count_serial.serial_id
            FROM public.stocktake_count_serials AS count_serial
            JOIN public.stocktake_count_lines AS count_line
              ON count_line.id = count_serial.count_line_id
             AND count_line.round_id = count_serial.round_id
           WHERE count_line.task_id = requested_task_id
          UNION SELECT observation.serial_id
            FROM public.stocktake_count_observations AS observation
           WHERE observation.task_id = requested_task_id
             AND observation.serial_id IS NOT NULL
          UNION SELECT movement_serial.serial_id
            FROM public.inventory_movement_serials AS movement_serial
           WHERE movement_serial.movement_id = ANY(movement_ids)
          UNION SELECT serial_id FROM public.stocktake_differences WHERE task_id = requested_task_id AND serial_id IS NOT NULL
          UNION SELECT resolved_serial_id FROM public.stocktake_observation_dispositions WHERE task_id = requested_task_id AND resolved_serial_id IS NOT NULL
          UNION SELECT serial_id FROM public.stocktake_close_reconciliation_serials WHERE task_id = requested_task_id
      ) AS serial_union;
    IF pg_catalog.array_position(serial_ids, NULL) IS NOT NULL
       OR (SELECT pg_catalog.count(*) FROM public.inventory_serials
            WHERE id = ANY(serial_ids)) <> pg_catalog.cardinality(serial_ids) THEN
        RAISE EXCEPTION 'non-opening count history serial reference missing';
    END IF;


    WITH RECURSIVE location_tree(id, parent_id, owner_org_id, custodian_person_id) AS (
        SELECT location.id, location.parent_id, location.owner_org_id,
               location.custodian_person_id
          FROM public.stock_locations AS location
         WHERE location.id IN (
             SELECT scope.location_id FROM public.stocktake_scopes AS scope
              WHERE scope.task_id = requested_task_id
             UNION SELECT account.location_id FROM public.stock_accounts AS account
              WHERE account.id = ANY(account_ids)
             UNION SELECT observation.location_id
              FROM public.stocktake_count_observations AS observation
              WHERE observation.task_id = requested_task_id
             UNION SELECT personal.id FROM public.stock_locations AS personal
              WHERE personal.location_type = 'personal'
                AND personal.custodian_person_id IN (
                    SELECT scope.custodian_person_id_snapshot
                      FROM public.stocktake_scopes AS scope
                     WHERE scope.task_id = requested_task_id
                       AND scope.custodian_person_id_snapshot IS NOT NULL
                )
         )
        UNION
        SELECT parent.id, parent.parent_id, parent.owner_org_id,
               parent.custodian_person_id
          FROM public.stock_locations AS parent
          JOIN location_tree AS child ON child.parent_id = parent.id
    )
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT id ORDER BY id), ARRAY[]::uuid[])
      INTO location_ids FROM location_tree;

    SELECT COALESCE(pg_catalog.array_agg(DISTINCT person_id ORDER BY person_id), ARRAY[]::uuid[])
      INTO people_ids
      FROM (
          SELECT account.custodian_person_id AS person_id
            FROM public.stock_accounts AS account WHERE account.id = ANY(account_ids)
          UNION SELECT scope.custodian_person_id_snapshot
            FROM public.stocktake_scopes AS scope WHERE scope.task_id = requested_task_id
          UNION SELECT observation.custodian_person_id_snapshot
            FROM public.stocktake_count_observations AS observation
           WHERE observation.task_id = requested_task_id
          UNION SELECT location.custodian_person_id
            FROM public.stock_locations AS location WHERE location.id = ANY(location_ids)
          UNION SELECT custody.custodian_person_id FROM public.custody_assignments AS custody
           WHERE custody.location_id = ANY(location_ids)
          UNION SELECT app_user.person_id FROM public.users AS app_user
           WHERE app_user.id::text = ANY(principal_user_ids)
      ) AS person_union WHERE person_id IS NOT NULL;

    WITH RECURSIVE organization_tree(id, parent_id) AS (
        SELECT organization.id, organization.parent_id
          FROM public.organizations AS organization
         WHERE organization.id IN (
             SELECT task_region_id
             UNION SELECT account.owner_org_id FROM public.stock_accounts AS account
              WHERE account.id = ANY(account_ids)
             UNION SELECT scope.owner_org_id FROM public.stocktake_scopes AS scope
              WHERE scope.task_id = requested_task_id
             UNION SELECT observation.owner_org_id
              FROM public.stocktake_count_observations AS observation
              WHERE observation.task_id = requested_task_id
             UNION SELECT location.owner_org_id FROM public.stock_locations AS location
              WHERE location.id = ANY(location_ids)
             UNION SELECT person.organization_id FROM public.people AS person
              WHERE person.id = ANY(people_ids)
         )
        UNION
        SELECT parent.id, parent.parent_id
          FROM public.organizations AS parent
          JOIN organization_tree AS child ON child.parent_id = parent.id
    )
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT id ORDER BY id), ARRAY[]::uuid[])
      INTO organization_ids FROM organization_tree;

    SELECT COALESCE(pg_catalog.array_agg(custody.id ORDER BY custody.id), ARRAY[]::uuid[])
      INTO custody_ids FROM public.custody_assignments AS custody
     WHERE custody.location_id = ANY(location_ids);
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT material_id ORDER BY material_id), ARRAY[]::uuid[])
      INTO material_ids FROM (
          SELECT account.material_id FROM public.stock_accounts AS account
           WHERE account.id = ANY(account_ids)
          UNION SELECT scope.material_id FROM public.stocktake_scopes AS scope
           WHERE scope.task_id = requested_task_id AND scope.material_id IS NOT NULL
          UNION SELECT observation.material_id
           FROM public.stocktake_count_observations AS observation
           WHERE observation.task_id = requested_task_id
             AND observation.material_id IS NOT NULL
          UNION SELECT material_id FROM public.inventory_serials WHERE id = ANY(serial_ids)
      ) AS material_union;
    SELECT COALESCE(pg_catalog.array_agg(policy.id ORDER BY policy.id), ARRAY[]::uuid[])
      INTO policy_ids FROM public.material_inventory_policies AS policy
     WHERE policy.material_id = ANY(material_ids);
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT lot_id ORDER BY lot_id), ARRAY[]::uuid[])
      INTO lot_ids FROM (
          SELECT account.lot_id FROM public.stock_accounts AS account
           WHERE account.id = ANY(account_ids)
          UNION SELECT observation.lot_id
           FROM public.stocktake_count_observations AS observation
           WHERE observation.task_id = requested_task_id
          UNION SELECT lot_id FROM public.inventory_serials WHERE id = ANY(serial_ids)
      ) AS lot_union WHERE lot_id IS NOT NULL;

    SELECT COALESCE(pg_catalog.array_agg(attachment.id ORDER BY attachment.id), ARRAY[]::uuid[]),
           COALESCE(pg_catalog.array_agg(DISTINCT attachment.file_id ORDER BY attachment.file_id), ARRAY[]::uuid[])
      INTO attachment_ids, file_ids
      FROM public.document_attachments AS attachment
     WHERE attachment.document_type = 'stocktake_scope_count_completion'
       AND attachment.attachment_type = 'stocktake_evidence'
       AND pg_catalog.replace(attachment.document_id, '-', '') IN (
           SELECT pg_catalog.replace(value.completion_id::text, '-', '')
             FROM pg_catalog.unnest(completion_ids) AS value(completion_id)
       );

    graph_count := 3
        + (SELECT pg_catalog.count(*) FROM public.users AS app_user
            WHERE app_user.id::text = ANY(principal_user_ids))
        + (SELECT pg_catalog.count(*) FROM public.people AS person
            WHERE EXISTS (SELECT 1 FROM public.users AS app_user
                WHERE app_user.person_id = person.id
                  AND app_user.id::text = ANY(principal_user_ids)))
        + (SELECT pg_catalog.count(*) FROM public.auth_identities AS identity
            WHERE identity.user_id::text = ANY(principal_user_ids))
        + (SELECT pg_catalog.count(*) FROM public.role_assignments AS assignment
            WHERE assignment.user_id::text = ANY(principal_user_ids))
        + (SELECT pg_catalog.count(*) FROM public.roles AS role
            WHERE EXISTS (SELECT 1 FROM public.role_assignments AS assignment
                WHERE assignment.role_id = role.id
                  AND assignment.user_id::text = ANY(principal_user_ids)))
        + (SELECT pg_catalog.count(*) FROM public.role_permissions AS binding
            WHERE EXISTS (SELECT 1 FROM public.role_assignments AS assignment
                WHERE assignment.role_id = binding.role_id
                  AND assignment.user_id::text = ANY(principal_user_ids)))
        + (SELECT pg_catalog.count(*) FROM public.permissions AS permission
            WHERE EXISTS (SELECT 1 FROM public.role_permissions AS binding
                JOIN public.role_assignments AS assignment
                  ON assignment.role_id = binding.role_id
                WHERE binding.permission_id = permission.id
                  AND assignment.user_id::text = ANY(principal_user_ids)))
        + pg_catalog.cardinality(account_ids)
        + pg_catalog.cardinality(organization_ids)
        + pg_catalog.cardinality(location_ids)
        + pg_catalog.cardinality(people_ids)
        + pg_catalog.cardinality(custody_ids)
        + pg_catalog.cardinality(material_ids)
        + pg_catalog.cardinality(policy_ids)
        + pg_catalog.cardinality(lot_ids)
        + pg_catalog.cardinality(serial_ids)
        + pg_catalog.cardinality(replay_transaction_ids)
        + pg_catalog.cardinality(movement_ids)
        + (SELECT pg_catalog.count(*) FROM public.inventory_movement_serials
            WHERE movement_id = ANY(movement_ids))
        + pg_catalog.cardinality(attachment_ids)
        + pg_catalog.cardinality(file_ids)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_scopes WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.inventory_freezes WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_snapshot_lines WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_control_snapshot_lines WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_rounds WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_count_lines WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_count_observations WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_count_serials AS serial_row
            JOIN public.stocktake_count_lines AS line ON line.id = serial_row.count_line_id
            WHERE line.task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_scope_count_completions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_round_submissions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_difference_set_completions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_differences WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_reviews WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_review_items WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_recount_cases WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_recount_scope_assignments WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.state_transition_events
            WHERE aggregate_type = 'stocktake_task'
              AND aggregate_id = requested_task_id::text)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_postings WHERE task_id = requested_task_id);
    graph_count := graph_count
        + (SELECT pg_catalog.count(*) FROM public.stocktake_start_completions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_observation_dispositions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_effective_approval_completions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_effective_approval_scopes WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_effective_approval_items WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_posting_items WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_posting_completions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_posting_completion_items WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_close_transition_acks WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_close_reconciliation_completions WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_close_reconciliation_accounts WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_close_reconciliation_serials WHERE task_id = requested_task_id)
        + (SELECT pg_catalog.count(*) FROM public.stocktake_close_completions WHERE task_id = requested_task_id);
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION 'non-opening count history lock graph exceeds 100000 rows';
    END IF;

    PERFORM organization.id FROM public.organizations AS organization
     WHERE organization.id = ANY(organization_ids)
     ORDER BY organization.id FOR UPDATE OF organization;
    PERFORM location.id FROM public.stock_locations AS location
     WHERE location.id = ANY(location_ids)
     ORDER BY location.id FOR UPDATE OF location;
    PERFORM person.id FROM public.people AS person
     WHERE person.id = ANY(people_ids)
     ORDER BY person.id FOR UPDATE OF person;
    PERFORM custody.id FROM public.custody_assignments AS custody
     WHERE custody.id = ANY(custody_ids)
     ORDER BY custody.location_id, custody.valid_from, custody.id FOR UPDATE OF custody;
    PERFORM material.id FROM public.materials AS material
     WHERE material.id = ANY(material_ids)
     ORDER BY material.id FOR UPDATE OF material;
    PERFORM policy.id FROM public.material_inventory_policies AS policy
     WHERE policy.id = ANY(policy_ids)
     ORDER BY policy.material_id, policy.effective_from, policy.id FOR UPDATE OF policy;
    PERFORM lot.id FROM public.inventory_lots AS lot
     WHERE lot.id = ANY(lot_ids)
     ORDER BY lot.id FOR UPDATE OF lot;
    PERFORM serial.id FROM public.inventory_serials AS serial
     WHERE serial.id = ANY(serial_ids)
     ORDER BY serial.id FOR UPDATE OF serial;

    -- Master rows could have changed while this transaction waited for their
    -- locks. Prove the exact 0032-derived subset BEFORE invoking that helper,
    -- so it cannot acquire a newly discovered owner out of canonical order.
    IF EXISTS (
        SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
         WHERE snapshot.task_id = requested_task_id
           AND NOT snapshot.stock_account_id = ANY(account_ids)
    ) OR EXISTS (
        SELECT 1 FROM public.stocktake_scopes AS scope WHERE scope.task_id = requested_task_id
         AND (NOT scope.owner_org_id = ANY(organization_ids)
              OR NOT scope.location_id = ANY(location_ids)
              OR (scope.custodian_person_id_snapshot IS NOT NULL
                  AND NOT scope.custodian_person_id_snapshot = ANY(people_ids)))
    ) OR EXISTS (
        SELECT 1 FROM public.stocktake_count_observations AS observation
         WHERE observation.task_id = requested_task_id
           AND (NOT observation.owner_org_id = ANY(organization_ids)
                OR NOT observation.location_id = ANY(location_ids)
                OR (observation.custodian_person_id_snapshot IS NOT NULL
                    AND NOT observation.custodian_person_id_snapshot = ANY(people_ids))
                OR (observation.material_id IS NOT NULL AND NOT observation.material_id = ANY(material_ids))
                OR (observation.lot_id IS NOT NULL AND NOT observation.lot_id = ANY(lot_ids))
                OR (observation.serial_id IS NOT NULL AND NOT observation.serial_id = ANY(serial_ids)))
    ) OR EXISTS (
        SELECT 1 FROM public.stock_accounts AS account WHERE account.id = ANY(account_ids)
         AND (NOT account.owner_org_id = ANY(organization_ids)
              OR NOT account.location_id = ANY(location_ids)
              OR (account.custodian_person_id IS NOT NULL AND NOT account.custodian_person_id = ANY(people_ids))
              OR NOT account.material_id = ANY(material_ids)
              OR (account.lot_id IS NOT NULL AND NOT account.lot_id = ANY(lot_ids)))
    ) OR EXISTS (
        SELECT 1 FROM public.stock_locations AS location WHERE location.id = ANY(location_ids)
         AND ((location.parent_id IS NOT NULL AND NOT location.parent_id = ANY(location_ids))
              OR NOT location.owner_org_id = ANY(organization_ids)
              OR (location.custodian_person_id IS NOT NULL AND NOT location.custodian_person_id = ANY(people_ids)))
    ) OR EXISTS (
        SELECT 1 FROM public.organizations AS organization WHERE organization.id = ANY(organization_ids)
         AND organization.parent_id IS NOT NULL AND NOT organization.parent_id = ANY(organization_ids)
    ) OR EXISTS (
        SELECT 1 FROM public.people AS person WHERE person.id = ANY(people_ids)
         AND NOT person.organization_id = ANY(organization_ids)
    ) OR EXISTS (
        SELECT 1 FROM public.custody_assignments AS custody WHERE custody.location_id = ANY(location_ids)
         AND (NOT custody.id = ANY(custody_ids) OR NOT custody.custodian_person_id = ANY(people_ids))
    ) OR EXISTS (
        SELECT 1 FROM public.material_inventory_policies AS policy WHERE policy.material_id = ANY(material_ids)
         AND NOT policy.id = ANY(policy_ids)
    ) OR EXISTS (
        SELECT 1 FROM public.inventory_serials AS serial WHERE serial.id = ANY(serial_ids)
         AND (NOT serial.material_id = ANY(material_ids)
              OR (serial.lot_id IS NOT NULL AND NOT serial.lot_id = ANY(lot_ids)))
    ) OR EXISTS (
        SELECT 1 FROM public.stocktake_count_serials AS count_serial
         JOIN public.stocktake_count_lines AS count_line ON count_line.id = count_serial.count_line_id
         WHERE count_line.task_id = requested_task_id
           AND NOT count_serial.serial_id = ANY(serial_ids)
    ) THEN
        RAISE EXCEPTION 'non-opening count history pre-helper owner union changed';
    END IF;

    -- Reuse the mature task-local 0032 graph only after the complete account
    -- and master union is held.  Its account/master locks are then re-entrant,
    -- while its complete task-local evidence set remains in canonical order.
    PERFORM public.rsc_lock_nonopening_stocktake_review_graph_0032(
        requested_task_id, requested_round_id
    );

    PERFORM id FROM public.stocktake_start_completions
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM id FROM public.stocktake_observation_dispositions
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM id FROM public.stocktake_effective_approval_completions
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM completion_id, scope_id FROM public.stocktake_effective_approval_scopes
     WHERE task_id = requested_task_id ORDER BY completion_id, scope_id FOR UPDATE;
    PERFORM completion_id, difference_id FROM public.stocktake_effective_approval_items
     WHERE task_id = requested_task_id ORDER BY completion_id, difference_id FOR UPDATE;
    PERFORM id FROM public.stocktake_postings
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM posting_id, inventory_movement_id FROM public.stocktake_posting_items
     WHERE task_id = requested_task_id ORDER BY posting_id, inventory_movement_id FOR UPDATE;
    PERFORM id FROM public.stocktake_posting_completions
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM completion_id, difference_id FROM public.stocktake_posting_completion_items
     WHERE task_id = requested_task_id ORDER BY completion_id, difference_id FOR UPDATE;
    PERFORM task_id, target_task_version FROM public.stocktake_close_transition_acks
     WHERE task_id = requested_task_id ORDER BY task_id, target_task_version FOR UPDATE;
    PERFORM id FROM public.stocktake_close_reconciliation_completions
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;
    PERFORM completion_id, stock_account_id FROM public.stocktake_close_reconciliation_accounts
     WHERE task_id = requested_task_id ORDER BY completion_id, stock_account_id FOR UPDATE;
    PERFORM completion_id, serial_id FROM public.stocktake_close_reconciliation_serials
     WHERE task_id = requested_task_id ORDER BY completion_id, serial_id FOR UPDATE;
    PERFORM id FROM public.stocktake_close_completions
     WHERE task_id = requested_task_id ORDER BY id FOR UPDATE;

    PERFORM transaction_row.id FROM public.inventory_transactions AS transaction_row
     WHERE transaction_row.id = ANY(replay_transaction_ids)
     ORDER BY transaction_row.ledger_cursor, transaction_row.id FOR UPDATE OF transaction_row;
    PERFORM movement.id FROM public.inventory_movements AS movement
      JOIN public.inventory_transactions AS transaction_row
        ON transaction_row.id = movement.transaction_id
     WHERE movement.id = ANY(movement_ids)
     ORDER BY transaction_row.ledger_cursor, movement.line_no, movement.id
     FOR UPDATE OF movement;
    PERFORM movement_serial.serial_id
      FROM public.inventory_movement_serials AS movement_serial
     WHERE movement_serial.movement_id = ANY(movement_ids)
     ORDER BY movement_serial.movement_id, movement_serial.serial_id
     FOR UPDATE OF movement_serial;
    PERFORM attachment.id FROM public.document_attachments AS attachment
     WHERE attachment.id = ANY(attachment_ids)
     ORDER BY attachment.document_id, attachment.file_id, attachment.id
     FOR UPDATE OF attachment;
    PERFORM file_row.id FROM public.files AS file_row
     WHERE file_row.id = ANY(file_ids)
     ORDER BY file_row.id FOR UPDATE OF file_row;

    IF (SELECT pg_catalog.count(*) FROM public.stock_accounts WHERE id = ANY(account_ids)) <> pg_catalog.cardinality(account_ids)
       OR (SELECT pg_catalog.count(*) FROM public.organizations WHERE id = ANY(organization_ids)) <> pg_catalog.cardinality(organization_ids)
       OR (SELECT pg_catalog.count(*) FROM public.stock_locations WHERE id = ANY(location_ids)) <> pg_catalog.cardinality(location_ids)
       OR (SELECT pg_catalog.count(*) FROM public.people WHERE id = ANY(people_ids)) <> pg_catalog.cardinality(people_ids)
       OR (SELECT pg_catalog.count(*) FROM public.materials WHERE id = ANY(material_ids)) <> pg_catalog.cardinality(material_ids)
       OR (SELECT pg_catalog.count(*) FROM public.inventory_lots WHERE id = ANY(lot_ids)) <> pg_catalog.cardinality(lot_ids)
       OR (SELECT pg_catalog.count(*) FROM public.inventory_transactions WHERE id = ANY(replay_transaction_ids)) <> pg_catalog.cardinality(replay_transaction_ids)
       OR (SELECT pg_catalog.count(*) FROM public.files WHERE id = ANY(file_ids)) <> pg_catalog.cardinality(file_ids)
       OR EXISTS (
           SELECT 1 FROM public.stock_locations AS location WHERE id = ANY(location_ids)
            AND ((parent_id IS NOT NULL AND NOT parent_id = ANY(location_ids))
                 OR NOT owner_org_id = ANY(organization_ids)
                 OR (custodian_person_id IS NOT NULL AND NOT custodian_person_id = ANY(people_ids)))
       ) OR EXISTS (
           SELECT 1 FROM public.organizations WHERE id = ANY(organization_ids)
            AND parent_id IS NOT NULL AND NOT parent_id = ANY(organization_ids)
       ) OR EXISTS (
           SELECT 1 FROM public.people WHERE id = ANY(people_ids)
            AND NOT organization_id = ANY(organization_ids)
       ) OR EXISTS (
           SELECT 1 FROM public.custody_assignments WHERE location_id = ANY(location_ids)
            AND (NOT id = ANY(custody_ids) OR NOT custodian_person_id = ANY(people_ids))
       ) OR EXISTS (
           SELECT 1 FROM public.material_inventory_policies WHERE material_id = ANY(material_ids)
            AND NOT id = ANY(policy_ids)
       ) OR EXISTS (
           SELECT 1 FROM public.stock_accounts AS account
            WHERE EXISTS (SELECT 1 FROM public.stocktake_scopes AS scope
                           WHERE scope.task_id = requested_task_id
                             AND scope.owner_org_id = account.owner_org_id
                             AND scope.location_id = account.location_id)
              AND NOT account.id = ANY(account_ids)
       ) OR EXISTS (
           SELECT 1 FROM public.document_attachments AS attachment
            WHERE document_type = 'stocktake_scope_count_completion'
              AND attachment_type = 'stocktake_evidence'
              AND pg_catalog.replace(document_id, '-', '') IN (
                  SELECT pg_catalog.replace(value.id::text, '-', '')
                    FROM pg_catalog.unnest(completion_ids) AS value(id))
              AND (NOT attachment.id = ANY(attachment_ids) OR NOT file_id = ANY(file_ids))
       ) THEN
        RAISE EXCEPTION 'non-opening count history owner reference missing';
    END IF;

    -- Audit-head and hash-chain proof are intentionally last, in the caller.
    -- This function owns locks only; it never asserts actor authorization or
    -- historical validity and never writes facts or commits the transaction.
END
$$
"""



def _verify_postgresql_catalog(*, expected_ready_hash: str, installed: bool) -> None:
    if (expected_ready_hash, installed) not in {
        (RUNTIME_READY_BODY_SHA256_0061, False),
        (RUNTIME_READY_BODY_SHA256_0062, True),
    }:
        raise ValueError("unsupported 0062 catalog state")
    _verify_function(
        signature="public.rsc_lock_formal_principal_graph_0026(text[])",
        arguments="text[]", argument_names="actor_user_ids",
        body_hash=PRINCIPAL_HELPER_BODY_SHA256,
    )
    _verify_function(
        signature="public.rsc_lock_nonopening_stocktake_review_graph_0032(uuid, uuid)",
        arguments="uuid, uuid", argument_names="requested_task_id,requested_round_id",
        body_hash=REVIEW_HELPER_BODY_SHA256,
    )
    _verify_function(
        signature="public.rsc_guard_stocktake_evidence_seal_0057()",
        body_hash=SEAL_BODY_SHA256, return_type="trigger", grants=(MIGRATION_ROLE,),
    )
    _verify_function(
        signature="public.rsc_guard_nonopening_control_snapshot_0057()",
        body_hash=CONTROL_GUARD_BODY_SHA256, return_type="trigger", grants=(MIGRATION_ROLE,),
    )
    _verify_function(
        signature=RUNTIME_READY_SIGNATURE, body_hash=expected_ready_hash,
        language="sql", return_type="boolean", volatility="s",
        function_config="search_path=pg_catalog",
        grants=(MIGRATION_ROLE, "star_oam_projector", "edge_inbox"),
    )
    if installed:
        _verify_function(
            signature=LOCK_SIGNATURE, body_hash=LOCK_BODY_SHA256,
            arguments="uuid, uuid, text",
            argument_names="requested_task_id,requested_round_id,requested_actor_user_id",
        )
    op.execute(f"""
DO $rsc_0062_boundary$
BEGIN
    IF (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS function_row
         JOIN pg_catalog.pg_namespace AS namespace_row ON namespace_row.oid = function_row.pronamespace
         WHERE namespace_row.nspname = 'public'
           AND function_row.proname = '{LOCK_FUNCTION}') <> {int(installed)} THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: capability identity mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES
            ('document_attachments', 'trg_document_attachments_00_stocktake_evidence_seal_0057',
             'public.rsc_guard_stocktake_evidence_seal_0057()'),
            ('stocktake_control_snapshot_lines', 'trg_stocktake_control_snapshot_00_nonopening_0057',
             'public.rsc_guard_nonopening_control_snapshot_0057()')
        ) AS expected(table_name, trigger_name, function_signature)
        WHERE NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger AS trigger_row
             WHERE trigger_row.tgrelid = pg_catalog.to_regclass('public.' || expected.table_name)
               AND trigger_row.tgname = expected.trigger_name
               AND trigger_row.tgfoid = pg_catalog.to_regprocedure(expected.function_signature)
               AND trigger_row.tgenabled = 'A' AND NOT trigger_row.tgisinternal
               AND trigger_row.tgtype = 7 AND trigger_row.tgnargs = 0
               AND trigger_row.tgqual IS NULL
        )
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: immutable evidence seal mismatch';
    END IF;
END
$rsc_0062_boundary$
""")


def _verify_function(
    *, signature: str, body_hash: str, arguments: str = "",
    argument_names: str = "", language: str = "plpgsql",
    return_type: str = "void", volatility: str = "v",
    function_config: str = "search_path=pg_catalog, public",
    grants: tuple[str, ...] = (MIGRATION_ROLE, PRODUCTION_API_ROLE),
) -> None:
    # Only fixed module constants supply SQL coordinates, never HTTP input.
    grant_names = ", ".join(f"'{name}'" for name in grants)
    op.execute(f"""
DO $rsc_0062_function$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{signature}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR migrator_oid IS NULL OR function_oid IS NULL
       OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_roles
            WHERE rolname IN ({grant_names})) <> {len(grants)} THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: identity mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
          JOIN pg_catalog.pg_namespace AS namespace_row ON namespace_row.oid = row.pronamespace
         WHERE row.oid = function_oid AND namespace_row.nspname = 'public'
           AND row.proowner = migrator_oid AND row.prokind = 'f'
           AND row.prorettype = '{return_type}'::pg_catalog.regtype
           AND NOT row.proretset AND row.pronargs = {0 if not arguments else len(arguments.split(","))}
           AND pg_catalog.oidvectortypes(row.proargtypes) = '{arguments}'
           AND COALESCE(pg_catalog.array_to_string(row.proargnames, ','), '') = '{argument_names}'
           AND row.proallargtypes IS NULL AND row.proargmodes IS NULL
           AND row.pronargdefaults = 0 AND row.proargdefaults IS NULL AND row.provariadic = 0
           AND language_row.lanname = '{language}' AND row.provolatile = '{volatility}'
           AND row.prosecdef AND NOT row.proisstrict AND NOT row.proleakproof
           AND row.proparallel = 'u'
           AND row.proconfig = ARRAY['{function_config}']::text[]
           AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')), 'hex') = '{body_hash}'
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: function shape or body mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
         CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(row.proacl,
             pg_catalog.acldefault('f', row.proowner))) AS acl
         WHERE row.oid = function_oid
           AND (acl.privilege_type <> 'EXECUTE' OR acl.grantor <> migrator_oid
                OR acl.grantee NOT IN (SELECT oid FROM pg_catalog.pg_roles WHERE rolname IN ({grant_names}))
                OR acl.is_grantable)
    ) OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(row.proacl,
              pg_catalog.acldefault('f', row.proowner))) AS acl
          WHERE row.oid = function_oid) <> {len(grants)} THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: exact ACL mismatch';
    END IF;
END
$rsc_0062_function$
""")


def _replace_runtime_ready(
    *, expected_hash: str, replacement_hash: str,
    old_revision: str, new_revision: str,
) -> None:
    op.execute(f"""
DO $rsc_0062_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    original_owner oid;
    original_acl aclitem[];
    original_security boolean;
    original_config text[];
    function_source text;
    function_definition text;
BEGIN
    SELECT row.proowner, row.proacl, row.prosecdef, row.proconfig,
           row.prosrc, pg_catalog.pg_get_functiondef(row.oid)
      INTO original_owner, original_acl, original_security, original_config,
           function_source, function_definition
      FROM pg_catalog.pg_proc AS row WHERE row.oid = function_oid
       AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')), 'hex') = '{expected_hash}';
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR function_source IS NULL
       OR (pg_catalog.length(function_source) - pg_catalog.length(
           pg_catalog.replace(function_source, '{old_revision}', ''))) / pg_catalog.length('{old_revision}') <> 1
       OR pg_catalog.strpos(function_source, '{new_revision}') <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness source mismatch';
    END IF;
    EXECUTE pg_catalog.replace(function_definition, '{old_revision}', '{new_revision}');
    IF pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}') IS DISTINCT FROM function_oid
       OR (SELECT row.proowner IS DISTINCT FROM original_owner OR row.proacl IS DISTINCT FROM original_acl
                  OR row.prosecdef IS DISTINCT FROM original_security OR row.proconfig IS DISTINCT FROM original_config
                  OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')), 'hex')
                     IS DISTINCT FROM '{replacement_hash}'
             FROM pg_catalog.pg_proc AS row WHERE row.oid = function_oid) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness replacement drift';
    END IF;
END
$rsc_0062_readiness$
""")
