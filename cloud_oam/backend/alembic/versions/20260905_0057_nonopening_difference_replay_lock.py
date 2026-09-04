"""Seal non-opening count evidence and own the difference replay lock graph.

Revision ID: 20260905_0057
Revises: 20260905_0056
Create Date: 2026-09-05

The API role is intentionally unable to lock immutable evidence and reference
tables directly.  This forward-only repair adds one bounded SECURITY DEFINER
entrypoint which serializes a submitted initial-round replay at the inventory
ledger head, then locks its complete account/evidence/reference/ledger graph.
It also closes the attachment phantom: stocktake evidence may be bound while a
round is counting, but never after the round has been sealed.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260905_0057"
down_revision: Union[str, Sequence[str], None] = "20260905_0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PREVIOUS_SCHEMA_REVISION = "20260905_0056"
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
MAXIMUM_LOCK_ROWS = 100_000
INVENTORY_LEDGER_HEAD_ID = "40000000-0000-4000-8000-000000000001"

LOCK_FUNCTION = "rsc_lock_nonopening_stocktake_difference_replay_graph_0057"
LOCK_SIGNATURE = f"public.{LOCK_FUNCTION}(uuid, uuid, text)"
SEAL_FUNCTION = "rsc_guard_stocktake_evidence_seal_0057"
SEAL_SIGNATURE = f"public.{SEAL_FUNCTION}()"
SEAL_TRIGGER = "trg_document_attachments_00_stocktake_evidence_seal_0057"
SQLITE_SEAL_TRIGGER = SEAL_TRIGGER
CONTROL_GUARD_FUNCTION = "rsc_guard_nonopening_control_snapshot_0057"
CONTROL_GUARD_SIGNATURE = f"public.{CONTROL_GUARD_FUNCTION}()"
CONTROL_GUARD_TRIGGER = "trg_stocktake_control_snapshot_00_nonopening_0057"
RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
RUNTIME_READY_SIGNATURE = f"public.{RUNTIME_READY_FUNCTION}()"
RUNTIME_READY_BODY_SHA256_0056 = (
    "449f0f8526292713d6344d07d25d35654b0c9967183ea83559e989dc982b04c7"
)
RUNTIME_READY_BODY_SHA256_0057 = (
    "9b97c355d0fcbb5ee4dfcf1e90fd76339b2eafd64f5f5b287a21aa3d364cfdc5"
)
LOCK_BODY_SHA256 = (
    "7771bc7f9b59465fb47426eaabbff79deeb92967c0c77bbed79c0fe01585596c"
)
SEAL_BODY_SHA256 = (
    "7a5ae0c9fd3117cb784dce34c7882b8301759e846321ac7a6233235c6333222c"
)
CONTROL_GUARD_BODY_SHA256 = (
    "40d7a6223b6250316f7bed15c0dd4749238066865f7581156646c5b89b509249"
)
PRINCIPAL_HELPER_BODY_SHA256 = (
    "4b7d3e47541a1de999f33af65d95a697ffd44cfea8930f6fbeb265d4fd727aaf"
)
REVIEW_HELPER_BODY_SHA256 = (
    "ce7dda6f207c9a17bfde049749aa6e689e3f84d9e2c3892c66f17ac15c411ee3"
)

DOWNGRADE_BLOCKER = (
    "cannot downgrade 0057 while sealed non-opening count or difference "
    "evidence exists"
)

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
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_difference_set_completions",
    "stocktake_differences",
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
    "stocktake_tasks",
)


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode() and dialect == "sqlite":
        raise RuntimeError("0057 SQLite upgrade requires an online connection")
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
        op.execute(_sqlite_seal_trigger_sql())
        op.execute(_sqlite_control_guard_trigger_sql())
        return

    _lock_postgresql_boundary()
    _verify_postgresql_prerequisites()
    op.execute(_postgresql_seal_function_sql())
    op.execute(_postgresql_control_guard_function_sql())
    op.execute(_postgresql_lock_function_sql())
    op.execute(
        f"CREATE TRIGGER {SEAL_TRIGGER} BEFORE INSERT ON "
        "public.document_attachments FOR EACH ROW EXECUTE FUNCTION "
        f"public.{SEAL_FUNCTION}()"
    )
    op.execute(
        "ALTER TABLE public.document_attachments ENABLE ALWAYS TRIGGER "
        f"{SEAL_TRIGGER}"
    )
    op.execute(
        f"CREATE TRIGGER {CONTROL_GUARD_TRIGGER} BEFORE INSERT ON "
        "public.stocktake_control_snapshot_lines FOR EACH ROW EXECUTE FUNCTION "
        f"public.{CONTROL_GUARD_FUNCTION}()"
    )
    op.execute(
        "ALTER TABLE public.stocktake_control_snapshot_lines ENABLE ALWAYS "
        f"TRIGGER {CONTROL_GUARD_TRIGGER}"
    )
    _apply_postgresql_acl()
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0056,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0057,
        old_revision=PREVIOUS_SCHEMA_REVISION,
        new_revision=revision,
    )
    _verify_postgresql_catalog(expected_ready_hash=RUNTIME_READY_BODY_SHA256_0057)


def downgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        raise RuntimeError("0057 downgrade requires an online evidence check")
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
        _require_no_sealed_nonopening_evidence(DOWNGRADE_BLOCKER, dialect)
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_SEAL_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {CONTROL_GUARD_TRIGGER}")
        return

    _lock_postgresql_boundary()
    _require_no_sealed_nonopening_evidence(DOWNGRADE_BLOCKER, dialect)
    _verify_postgresql_catalog(expected_ready_hash=RUNTIME_READY_BODY_SHA256_0057)
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0057,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0056,
        old_revision=revision,
        new_revision=PREVIOUS_SCHEMA_REVISION,
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {SEAL_TRIGGER} ON public.document_attachments"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS {CONTROL_GUARD_TRIGGER} ON "
        "public.stocktake_control_snapshot_lines"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {CONTROL_GUARD_SIGNATURE}")
    op.execute(f"DROP FUNCTION IF EXISTS {SEAL_SIGNATURE}")
    op.execute(f"DROP FUNCTION IF EXISTS {LOCK_SIGNATURE}")
    _verify_postgresql_prerequisites()


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0057 supports only PostgreSQL and SQLite")
    return dialect


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _lock_postgresql_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _require_no_sealed_nonopening_evidence(blocker: str, dialect: str) -> None:
    prefix = "public." if dialect == "postgresql" else ""
    row = op.get_bind().exec_driver_sql(
        f"""
SELECT 1
  FROM {prefix}stocktake_tasks AS task
 WHERE task.task_type IN {NONOPENING_SQL}
   AND (
       EXISTS (
           SELECT 1 FROM {prefix}stocktake_round_submissions AS submission
            WHERE submission.task_id = task.id
       )
       OR EXISTS (
           SELECT 1
             FROM {prefix}stocktake_difference_set_completions AS completion
            WHERE completion.task_id = task.id
       )
   )
 LIMIT 1
"""
    ).first()
    if row is not None:
        raise RuntimeError(blocker)


def _postgresql_seal_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{SEAL_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    locked_count bigint;
    target_round_id uuid;
    target_task_id uuid;
BEGIN
    IF NEW.document_type <> 'stocktake_scope_count_completion'
       OR NEW.attachment_type <> 'stocktake_evidence' THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.stocktake_scope_count_completions AS completion
          JOIN public.stocktake_tasks AS task ON task.id = completion.task_id
         WHERE pg_catalog.replace(NEW.document_id, '-', '') =
               pg_catalog.replace(completion.id::text, '-', '')
           AND task.task_type IN {NONOPENING_SQL}
    ) THEN
        RETURN NEW;
    END IF;

    SELECT completion.task_id, completion.round_id
      INTO target_task_id, target_round_id
      FROM public.stocktake_scope_count_completions AS completion
      JOIN public.stocktake_tasks AS task ON task.id = completion.task_id
     WHERE pg_catalog.replace(NEW.document_id, '-', '') =
           pg_catalog.replace(completion.id::text, '-', '')
       AND task.task_type IN {NONOPENING_SQL};
    IF target_task_id IS NULL OR target_round_id IS NULL THEN
        RETURN NEW;
    END IF;

    PERFORM task.id FROM public.stocktake_tasks AS task
     WHERE task.id = target_task_id
       AND task.task_type IN {NONOPENING_SQL}
       AND task.status = 'counting'
     ORDER BY task.id FOR UPDATE OF task;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'sealed non-opening stocktake evidence is immutable'
            USING ERRCODE = '55000';
    END IF;
    PERFORM round_row.id FROM public.stocktake_rounds AS round_row
     WHERE round_row.id = target_round_id
       AND round_row.task_id = target_task_id
       AND round_row.status = 'counting'
     ORDER BY round_row.id FOR UPDATE OF round_row;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 OR EXISTS (
        SELECT 1 FROM public.stocktake_round_submissions AS submission
         WHERE submission.task_id = target_task_id
           AND submission.round_id = target_round_id
    ) THEN
        RAISE EXCEPTION 'sealed non-opening stocktake evidence is immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$
"""


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
        RAISE EXCEPTION 'non-opening difference replay lock graph invariant violated';
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
        RAISE EXCEPTION 'non-opening difference replay lock graph invariant violated';
    END IF;

    SELECT task.region_org_id, task.cutoff_at, task.cutoff_ledger_cursor
      INTO task_region_id, task_cutoff_at, task_cutoff_cursor
      FROM public.stocktake_tasks AS task
     WHERE task.id = requested_task_id
       AND task.task_type IN {NONOPENING_SQL}
       AND task.status = 'submitted'
       AND task.current_round_no = 1
       AND task.cutoff_at IS NOT NULL
       AND task.cutoff_ledger_cursor IS NOT NULL
     ORDER BY task.id FOR UPDATE OF task;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening difference replay lock graph invariant violated';
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
      ) AS principal_union;
    IF requested_actor_user_id IS NULL
       OR pg_catalog.length(requested_actor_user_id) NOT BETWEEN 1 AND 36
       OR pg_catalog.btrim(requested_actor_user_id) <> requested_actor_user_id
       OR pg_catalog.cardinality(principal_user_ids) NOT BETWEEN 1 AND 1000
       OR pg_catalog.array_position(principal_user_ids, NULL) IS NOT NULL THEN
        RAISE EXCEPTION 'non-opening difference replay principal graph invariant violated';
    END IF;
    PERFORM public.rsc_lock_formal_principal_graph_0026(principal_user_ids);

    PERFORM round_row.id
      FROM public.stocktake_rounds AS round_row
     WHERE round_row.id = requested_round_id
       AND round_row.task_id = requested_task_id
       AND round_row.round_no = 1
       AND round_row.round_type = 'initial'
       AND round_row.status = 'submitted'
     ORDER BY round_row.id FOR UPDATE OF round_row;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'non-opening difference replay lock graph invariant violated';
    END IF;

    SELECT pg_catalog.max(completion.count_ledger_cursor),
           pg_catalog.array_agg(completion.id ORDER BY completion.id)
      INTO max_count_cursor, completion_ids
      FROM public.stocktake_scope_count_completions AS completion
     WHERE completion.task_id = requested_task_id
       AND completion.round_id = requested_round_id;
    completion_ids := COALESCE(completion_ids, ARRAY[]::uuid[]);
    IF pg_catalog.cardinality(completion_ids) <> (
           SELECT pg_catalog.count(*)
             FROM public.stocktake_scopes AS scope
            WHERE scope.task_id = requested_task_id
       )
       OR pg_catalog.cardinality(completion_ids) = 0
       OR max_count_cursor IS NULL
       OR max_count_cursor < task_cutoff_cursor
       OR EXISTS (
           SELECT 1
             FROM public.stocktake_scope_count_completions AS completion
            WHERE completion.task_id = requested_task_id
              AND completion.round_id = requested_round_id
              AND (completion.count_ledger_cursor IS NULL
                   OR completion.count_ledger_cursor < task_cutoff_cursor)
       )
       OR (SELECT pg_catalog.count(*)
             FROM public.stocktake_round_submissions AS submission
            WHERE submission.task_id = requested_task_id
              AND submission.round_id = requested_round_id) <> 1
       OR max_count_cursor >= (
           SELECT head.next_cursor
             FROM public.inventory_ledger_heads AS head
            WHERE head.id = '{INVENTORY_LEDGER_HEAD_ID}'::uuid
       ) THEN
        RAISE EXCEPTION 'non-opening difference replay lock graph invariant violated';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.stocktake_control_snapshot_lines AS control_line
         WHERE control_line.task_id = requested_task_id
    ) THEN
        RAISE EXCEPTION 'non-opening difference replay contains forbidden control evidence';
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

    WITH replay_movement AS (
        SELECT movement.id, movement.transaction_id,
               movement.from_account_id, movement.to_account_id
          FROM public.inventory_movements AS movement
          JOIN public.inventory_transactions AS transaction_row
            ON transaction_row.id = movement.transaction_id
         WHERE transaction_row.ledger_cursor > task_cutoff_cursor
           AND transaction_row.ledger_cursor <= max_count_cursor
           AND (movement.from_account_id = ANY(scoped_account_ids)
                OR movement.to_account_id = ANY(scoped_account_ids))
    ), complete_account AS (
        SELECT scoped_id.id
          FROM pg_catalog.unnest(scoped_account_ids) AS scoped_id(id)
        UNION SELECT from_account_id FROM replay_movement
              WHERE from_account_id IS NOT NULL
        UNION SELECT to_account_id FROM replay_movement
              WHERE to_account_id IS NOT NULL
    )
    SELECT COALESCE(
               pg_catalog.array_agg(id ORDER BY id), ARRAY[]::uuid[])
      INTO account_ids FROM complete_account;

    IF pg_catalog.cardinality(account_ids) > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION 'non-opening difference replay lock graph exceeds 100000 rows';
    END IF;
    PERFORM account.id FROM public.stock_accounts AS account
     WHERE account.id = ANY(account_ids)
     ORDER BY account.id FOR UPDATE OF account;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> pg_catalog.cardinality(account_ids) THEN
        RAISE EXCEPTION 'non-opening difference replay lock graph invariant violated';
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
      ) AS material_union;
    SELECT COALESCE(pg_catalog.array_agg(policy.id ORDER BY policy.id), ARRAY[]::uuid[])
      INTO policy_ids FROM public.material_inventory_policies AS policy
     WHERE policy.material_id = ANY(material_ids)
       AND policy.effective_from <= task_cutoff_at
       AND (policy.effective_to IS NULL OR policy.effective_to > task_cutoff_at);
    SELECT COALESCE(pg_catalog.array_agg(DISTINCT lot_id ORDER BY lot_id), ARRAY[]::uuid[])
      INTO lot_ids FROM (
          SELECT account.lot_id FROM public.stock_accounts AS account
           WHERE account.id = ANY(account_ids)
          UNION SELECT observation.lot_id
           FROM public.stocktake_count_observations AS observation
           WHERE observation.task_id = requested_task_id
      ) AS lot_union WHERE lot_id IS NOT NULL;

    WITH replay_transaction AS (
        SELECT DISTINCT transaction_row.id
          FROM public.inventory_transactions AS transaction_row
          JOIN public.inventory_movements AS movement
            ON movement.transaction_id = transaction_row.id
         WHERE transaction_row.ledger_cursor > task_cutoff_cursor
           AND transaction_row.ledger_cursor <= max_count_cursor
           AND (movement.from_account_id = ANY(scoped_account_ids)
                OR movement.to_account_id = ANY(scoped_account_ids))
    )
    SELECT COALESCE(pg_catalog.array_agg(id ORDER BY id), ARRAY[]::uuid[])
      INTO replay_transaction_ids FROM replay_transaction;
    SELECT COALESCE(pg_catalog.array_agg(movement.id ORDER BY movement.id), ARRAY[]::uuid[])
      INTO movement_ids FROM public.inventory_movements AS movement
     WHERE movement.transaction_id = ANY(replay_transaction_ids)
       AND (movement.from_account_id = ANY(scoped_account_ids)
            OR movement.to_account_id = ANY(scoped_account_ids));

    SELECT COALESCE(pg_catalog.array_agg(DISTINCT serial_id ORDER BY serial_id), ARRAY[]::uuid[])
      INTO serial_ids FROM (
          SELECT serial.id AS serial_id
            FROM public.stocktake_snapshot_lines AS snapshot
            CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(snapshot.serial_snapshot_jsonb) AS element(value)
            JOIN public.inventory_serials AS serial
              ON serial.id::text = element.value->>'serial_id'
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
      ) AS serial_union;

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
    IF graph_count > {MAXIMUM_LOCK_ROWS} THEN
        RAISE EXCEPTION 'non-opening difference replay lock graph exceeds 100000 rows';
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

    -- Reuse the mature task-local 0032 graph only after the complete account
    -- and master union is held.  Its account/master locks are then re-entrant,
    -- while its complete task-local evidence set remains in canonical order.
    PERFORM public.rsc_lock_nonopening_stocktake_review_graph_0032(
        requested_task_id, requested_round_id
    );

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

    -- 0032 already owns reviews/differences; postings are a separate downstream
    -- set and are locked explicitly.  Their legitimate producer must first
    -- advance the task, which is blocked by the task lock held above.
    PERFORM posting.id FROM public.stocktake_postings AS posting
     WHERE posting.task_id = requested_task_id
     ORDER BY posting.round_id, posting.posting_kind, posting.id
     FOR UPDATE OF posting;
END
$$
"""


def _sqlite_seal_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_SEAL_TRIGGER}
BEFORE INSERT ON document_attachments
WHEN NEW.document_type = 'stocktake_scope_count_completion'
 AND NEW.attachment_type = 'stocktake_evidence'
 AND EXISTS (
     SELECT 1
       FROM stocktake_scope_count_completions AS completion
       JOIN stocktake_tasks AS task ON task.id = completion.task_id
       JOIN stocktake_rounds AS round_row
         ON round_row.id = completion.round_id
        AND round_row.task_id = completion.task_id
      WHERE replace(NEW.document_id, '-', '') = replace(completion.id, '-', '')
        AND task.task_type IN {NONOPENING_SQL}
        AND (
            task.status <> 'counting'
            OR round_row.status <> 'counting'
            OR EXISTS (
            SELECT 1 FROM stocktake_round_submissions AS submission
             WHERE submission.task_id = completion.task_id
               AND submission.round_id = completion.round_id
            )
        )
 )
BEGIN
    SELECT RAISE(ABORT, 'sealed non-opening stocktake evidence is immutable');
END
"""


def _postgresql_control_guard_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{CONTROL_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_kind text;
BEGIN
    SELECT task.task_type INTO task_kind
      FROM public.stocktake_tasks AS task
     WHERE task.id = NEW.task_id
     ORDER BY task.id FOR UPDATE OF task;
    IF task_kind IS NULL THEN
        RAISE EXCEPTION 'stocktake control snapshot task is missing'
            USING ERRCODE = '23503';
    END IF;
    IF task_kind IN {NONOPENING_SQL} THEN
        RAISE EXCEPTION 'non-opening stocktake control snapshot is forbidden'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$
"""


def _sqlite_control_guard_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {CONTROL_GUARD_TRIGGER}
BEFORE INSERT ON stocktake_control_snapshot_lines
WHEN EXISTS (
    SELECT 1 FROM stocktake_tasks AS task
     WHERE task.id = NEW.task_id
       AND task.task_type IN {NONOPENING_SQL}
)
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake control snapshot is forbidden');
END
"""


def _apply_postgresql_acl() -> None:
    for signature in (
        LOCK_SIGNATURE,
        SEAL_SIGNATURE,
        CONTROL_GUARD_SIGNATURE,
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM PUBLIC")
        op.execute(
            f"ALTER FUNCTION {signature} OWNER TO {MIGRATION_ROLE}"
        )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {LOCK_SIGNATURE} TO {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION {SEAL_SIGNATURE} FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION {CONTROL_GUARD_SIGNATURE} "
        f"FROM {PRODUCTION_API_ROLE}"
    )


def _verify_postgresql_prerequisites() -> None:
    op.execute(
        f"""
DO $rsc_0057_prerequisites$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    principal_oid oid := pg_catalog.to_regprocedure(
        'public.rsc_lock_formal_principal_graph_0026(text[])'
    );
    ready_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    review_oid oid := pg_catalog.to_regprocedure(
        'public.rsc_lock_nonopening_stocktake_review_graph_0032(uuid, uuid)'
    );
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR migrator_oid IS NULL OR principal_oid IS NULL
       OR review_oid IS NULL
       OR ready_oid IS NULL
       OR (SELECT pg_catalog.encode(
                  pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
                  'hex')
             FROM pg_catalog.pg_proc AS row
            WHERE row.oid = ready_oid) IS DISTINCT FROM
          '{RUNTIME_READY_BODY_SHA256_0056}' THEN
        RAISE EXCEPTION '0057 prerequisite catalog mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
         WHERE row.oid = principal_oid AND row.proowner = migrator_oid
           AND row.prokind = 'f' AND row.prorettype = 'void'::pg_catalog.regtype
           AND NOT row.proretset AND row.pronargs = 1
           AND pg_catalog.oidvectortypes(row.proargtypes) = 'text[]'
           AND row.proargnames = ARRAY['actor_user_ids']::text[]
           AND row.proallargtypes IS NULL AND row.proargmodes IS NULL
           AND row.pronargdefaults = 0 AND row.proargdefaults IS NULL
           AND row.provariadic = 0 AND language_row.lanname = 'plpgsql'
           AND row.provolatile = 'v' AND row.prosecdef
           AND NOT row.proisstrict AND NOT row.proleakproof
           AND row.proparallel = 'u'
           AND row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]
           AND pg_catalog.encode(
               pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
               'hex') = '{PRINCIPAL_HELPER_BODY_SHA256}'
    ) OR NOT pg_catalog.has_function_privilege(api_oid, principal_oid, 'EXECUTE')
      OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_proc AS row
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
          ) AS acl
          WHERE row.oid = principal_oid
            AND (acl.privilege_type <> 'EXECUTE'
                 OR acl.grantor <> migrator_oid
                 OR acl.grantee NOT IN (migrator_oid, api_oid)
                 OR acl.is_grantable)
      ) OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
            ) AS acl
            WHERE row.oid = principal_oid AND acl.privilege_type = 'EXECUTE'
              AND acl.grantor = migrator_oid
              AND acl.grantee IN (migrator_oid, api_oid)
              AND NOT acl.is_grantable) <> 2 THEN
        RAISE EXCEPTION '0057 principal helper prerequisite mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
         WHERE row.oid = review_oid AND row.proowner = migrator_oid
           AND row.prokind = 'f' AND row.prorettype = 'void'::pg_catalog.regtype
           AND NOT row.proretset AND row.pronargs = 2
           AND pg_catalog.oidvectortypes(row.proargtypes) = 'uuid, uuid'
           AND row.proargnames = ARRAY[
               'requested_task_id', 'requested_round_id'
           ]::text[]
           AND row.proallargtypes IS NULL AND row.proargmodes IS NULL
           AND row.pronargdefaults = 0 AND row.proargdefaults IS NULL
           AND row.provariadic = 0 AND language_row.lanname = 'plpgsql'
           AND row.provolatile = 'v' AND row.prosecdef
           AND NOT row.proisstrict AND NOT row.proleakproof
           AND row.proparallel = 'u'
           AND row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]
           AND pg_catalog.encode(
               pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
               'hex') = '{REVIEW_HELPER_BODY_SHA256}'
    ) OR NOT pg_catalog.has_function_privilege(api_oid, review_oid, 'EXECUTE')
      OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_proc AS row
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
          ) AS acl
          WHERE row.oid = review_oid
            AND (acl.privilege_type <> 'EXECUTE'
                 OR acl.grantor <> migrator_oid
                 OR acl.grantee NOT IN (migrator_oid, api_oid)
                 OR acl.is_grantable)
      ) OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
            ) AS acl
            WHERE row.oid = review_oid AND acl.privilege_type = 'EXECUTE'
              AND acl.grantor = migrator_oid
              AND acl.grantee IN (migrator_oid, api_oid)
              AND NOT acl.is_grantable) <> 2 THEN
        RAISE EXCEPTION '0057 review helper prerequisite mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trigger_row
          JOIN pg_catalog.pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = table_row.relnamespace
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname =
               'trg_document_attachments_stocktake_evidence_guard_0036'
           AND namespace_row.nspname = 'public'
           AND table_row.relname = 'document_attachments'
           AND trigger_row.tgenabled = 'A'
    ) THEN
        RAISE EXCEPTION '0057 formal evidence prerequisite mismatch';
    END IF;
END
$rsc_0057_prerequisites$
"""
    )


def _verify_postgresql_catalog(*, expected_ready_hash: str) -> None:
    if expected_ready_hash != RUNTIME_READY_BODY_SHA256_0057:
        raise ValueError("unsupported 0057 catalog state")
    op.execute(
        f"""
DO $rsc_0057_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    control_oid oid := pg_catalog.to_regprocedure('{CONTROL_GUARD_SIGNATURE}');
    edge_oid oid := pg_catalog.to_regrole('edge_inbox');
    lock_oid oid := pg_catalog.to_regprocedure('{LOCK_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    projector_oid oid := pg_catalog.to_regrole('star_oam_projector');
    ready_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    seal_oid oid := pg_catalog.to_regprocedure('{SEAL_SIGNATURE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR migrator_oid IS NULL OR control_oid IS NULL
       OR edge_oid IS NULL OR lock_oid IS NULL OR projector_oid IS NULL
       OR ready_oid IS NULL OR seal_oid IS NULL THEN
        RAISE EXCEPTION '0057 catalog identity mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
         WHERE row.oid = lock_oid AND row.proowner = migrator_oid
           AND row.prokind = 'f'
           AND row.prorettype = 'void'::pg_catalog.regtype
           AND NOT row.proretset
           AND row.pronargs = 3
           AND pg_catalog.oidvectortypes(row.proargtypes) = 'uuid, uuid, text'
           AND row.proargnames = ARRAY[
               'requested_task_id', 'requested_round_id',
               'requested_actor_user_id'
           ]::text[]
           AND row.proallargtypes IS NULL AND row.proargmodes IS NULL
           AND row.pronargdefaults = 0 AND row.proargdefaults IS NULL
           AND row.provariadic = 0
           AND language_row.lanname = 'plpgsql' AND row.provolatile = 'v'
           AND row.prosecdef AND NOT row.proisstrict AND NOT row.proleakproof
           AND row.proparallel = 'u'
           AND row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]
           AND pg_catalog.encode(
               pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
               'hex') = '{LOCK_BODY_SHA256}'
    ) OR NOT pg_catalog.has_function_privilege(api_oid, lock_oid, 'EXECUTE')
      OR EXISTS (
          SELECT 1 FROM pg_catalog.pg_proc AS row
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
          ) AS acl
            WHERE row.oid = lock_oid
            AND (acl.privilege_type <> 'EXECUTE'
                 OR acl.grantor <> migrator_oid
                 OR acl.grantee NOT IN (migrator_oid, api_oid)
                 OR acl.is_grantable)
      ) OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
            ) AS acl
            WHERE row.oid = lock_oid AND acl.privilege_type = 'EXECUTE'
              AND acl.grantee IN (migrator_oid, api_oid)
              AND NOT acl.is_grantable) <> 2 THEN
        RAISE EXCEPTION '0057 lock function catalog mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES
            (seal_oid, '{SEAL_BODY_SHA256}'),
            (control_oid, '{CONTROL_GUARD_BODY_SHA256}')
        ) AS expected(function_oid, body_sha256)
        WHERE NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc AS row
              JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
             WHERE row.oid = expected.function_oid AND row.proowner = migrator_oid
               AND row.prokind = 'f'
               AND row.prorettype = 'trigger'::pg_catalog.regtype
               AND NOT row.proretset AND row.pronargs = 0
               AND pg_catalog.oidvectortypes(row.proargtypes) = ''
               AND row.proargnames IS NULL AND row.proallargtypes IS NULL
               AND row.proargmodes IS NULL AND row.pronargdefaults = 0
               AND row.proargdefaults IS NULL AND row.provariadic = 0
               AND language_row.lanname = 'plpgsql'
               AND row.provolatile = 'v' AND row.prosecdef
               AND NOT row.proisstrict AND NOT row.proleakproof
               AND row.proparallel = 'u'
               AND row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]
               AND pg_catalog.encode(
                   pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
                   'hex') = expected.body_sha256
        ) OR pg_catalog.has_function_privilege(api_oid, expected.function_oid, 'EXECUTE')
          OR EXISTS (
              SELECT 1 FROM pg_catalog.pg_proc AS row
              CROSS JOIN LATERAL pg_catalog.aclexplode(
                  COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
              ) AS acl
              WHERE row.oid = expected.function_oid
                AND (acl.privilege_type <> 'EXECUTE'
                     OR acl.grantee <> migrator_oid OR acl.is_grantable)
          ) OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
                CROSS JOIN LATERAL pg_catalog.aclexplode(
                    COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
                ) AS acl
                WHERE row.oid = expected.function_oid
                  AND acl.privilege_type = 'EXECUTE'
                  AND acl.grantee = migrator_oid
                  AND NOT acl.is_grantable) <> 1
    ) THEN
        RAISE EXCEPTION '0057 trigger function catalog mismatch';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (VALUES
            ('{SEAL_TRIGGER}', 'document_attachments', seal_oid),
            ('{CONTROL_GUARD_TRIGGER}', 'stocktake_control_snapshot_lines', control_oid)
        ) AS expected(trigger_name, table_name, function_oid)
        WHERE (SELECT pg_catalog.count(*)
                 FROM pg_catalog.pg_trigger AS trigger_row
                 JOIN pg_catalog.pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
                 JOIN pg_catalog.pg_namespace AS namespace_row
                   ON namespace_row.oid = table_row.relnamespace
                WHERE NOT trigger_row.tgisinternal
                  AND trigger_row.tgname = expected.trigger_name
                  AND namespace_row.nspname = 'public'
                  AND table_row.relname = expected.table_name
                  AND trigger_row.tgfoid = expected.function_oid
                  AND trigger_row.tgenabled = 'A'
                  AND trigger_row.tgtype = 7
                  AND trigger_row.tgconstraint = 0
                  AND NOT trigger_row.tgdeferrable
                  AND NOT trigger_row.tginitdeferred
                  AND trigger_row.tgconstrrelid = 0
                  AND trigger_row.tgconstrindid = 0
                  AND trigger_row.tgparentid = 0
                  AND trigger_row.tgqual IS NULL
                  AND trigger_row.tgoldtable IS NULL
                  AND trigger_row.tgnewtable IS NULL
                  AND trigger_row.tgnargs = 0
                  AND trigger_row.tgattr = ''::pg_catalog.int2vector) <> 1
    ) OR '{SEAL_TRIGGER}' >=
         'trg_document_attachments_stocktake_evidence_guard_0036' THEN
        RAISE EXCEPTION '0057 trigger binding catalog mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
         WHERE row.oid = ready_oid AND row.proowner = migrator_oid
           AND row.prokind = 'f' AND row.prorettype = 'boolean'::pg_catalog.regtype
           AND NOT row.proretset AND row.pronargs = 0
           AND pg_catalog.oidvectortypes(row.proargtypes) = ''
           AND row.proargnames IS NULL AND row.proallargtypes IS NULL
           AND row.proargmodes IS NULL AND row.pronargdefaults = 0
           AND row.proargdefaults IS NULL AND row.provariadic = 0
           AND language_row.lanname = 'sql' AND row.provolatile = 's'
           AND row.prosecdef AND NOT row.proisstrict AND NOT row.proleakproof
           AND row.proparallel = 'u'
           AND row.proconfig = ARRAY['search_path=pg_catalog']::text[]
           AND pg_catalog.encode(
               pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
               'hex') = '{expected_ready_hash}'
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
        CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
        ) AS acl
        WHERE row.oid = ready_oid
          AND (acl.privilege_type <> 'EXECUTE'
               OR acl.grantor <> migrator_oid
               OR acl.grantee NOT IN (
                   migrator_oid,
                   projector_oid,
                   edge_oid
               )
               OR acl.is_grantable)
    ) OR (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
          CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(row.proacl, pg_catalog.acldefault('f', row.proowner))
          ) AS acl
          WHERE row.oid = ready_oid AND acl.privilege_type = 'EXECUTE'
            AND acl.grantor = migrator_oid
            AND acl.grantee IN (
                migrator_oid,
                projector_oid,
                edge_oid
            ) AND NOT acl.is_grantable) <> 3 THEN
        RAISE EXCEPTION '0057 runtime readiness catalog mismatch';
    END IF;
END
$rsc_0057_catalog$
"""
    )


def _replace_runtime_ready(
    *,
    expected_hash: str,
    replacement_hash: str,
    old_revision: str,
    new_revision: str,
) -> None:
    op.execute(
        f"""
DO $rsc_0057_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    function_source text;
    function_definition text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION '0057 migration role mismatch';
    END IF;
    SELECT function_row.prosrc, pg_catalog.pg_get_functiondef(function_row.oid)
      INTO function_source, function_definition
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(
             pg_catalog.sha256(pg_catalog.convert_to(function_row.prosrc, 'UTF8')),
             'hex') = '{expected_hash}';
    IF function_source IS NULL
       OR pg_catalog.strpos(function_source, '{old_revision}') = 0
       OR pg_catalog.strpos(function_source, '{new_revision}') > 0 THEN
        RAISE EXCEPTION '0057 runtime readiness source mismatch';
    END IF;
    EXECUTE pg_catalog.replace(
        function_definition, '{old_revision}', '{new_revision}'
    );
    IF (SELECT pg_catalog.encode(
                   pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')),
                   'hex')
          FROM pg_catalog.pg_proc AS row
         WHERE row.oid = function_oid) IS DISTINCT FROM '{replacement_hash}' THEN
        RAISE EXCEPTION '0057 runtime readiness replacement mismatch';
    END IF;
END
$rsc_0057_readiness$
"""
    )
