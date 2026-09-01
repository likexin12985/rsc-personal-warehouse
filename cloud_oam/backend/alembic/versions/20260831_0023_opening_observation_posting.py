"""Close the opening recount observation posting and account-creation boundary.

Revision ID: 20260831_0023
Revises: 20260831_0022
Create Date: 2026-08-31

Revision 0022 deliberately allowed opening postings to originate only from a
cutoff stock account's physical count line.  A verified recount observation
for an exact dimension that did not exist at cutoff therefore had no legal
terminal path: the service could neither bind a posting item nor create the
post-cutoff account needed by the immutable opening ledger.

This revision admits that one narrow path.  PostgreSQL and SQLite both require
an opening ``difference_id`` posting item to bind a verified observation from
the current submitted recount round to an exact excess difference and opening
movement.  PostgreSQL's deferred terminal graph proves the same bijection at
commit.  The API role receives INSERT, but never UPDATE or DELETE, on
``stock_accounts``; a separate deferred account trigger prevents that grant
from being used to commit an orphan empty account.  No balance is backfilled or
overwritten by this migration.  The source graph remains one-to-one from each
observation through its difference, posting item and movement, while multiple
serial observations may correctly share the same serial-free stock-account
dimension and aggregate their movements into one projected balance.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Mapping, Sequence, Union

from alembic import context, op


revision: str = "20260831_0023"
down_revision: Union[str, Sequence[str], None] = "20260831_0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
PG_POSTING_ITEM_FUNCTION = "rsc_validate_stocktake_posting_item_0010"
PG_GRAPH_CHECK_FUNCTION = "rsc_opening_terminal_graph_complete_0022"
PG_ACCOUNT_FUNCTION = "rsc_require_opening_observation_account_0023"
PG_ACCOUNT_TRIGGER = "trg_stock_accounts_opening_observation_commit_0023"
SQLITE_POSTING_ITEM_TRIGGER = (
    "trg_stocktake_posting_items_validate_insert_0010"
)

# These literals are the executable runtime ACL manifest.  Startup validation
# and migration tests parse them directly, so keep them self-contained rather
# than importing mutable application configuration.
API_READ_TABLES = (
    "audit_chain_heads",
    "audit_events",
    "auth_identities",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "custody_assignments",
    "external_objects",
    "external_object_versions",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_serials",
    "inventory_transactions",
    "login_challenges",
    "material_inventory_policies",
    "materials",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "serial_current_positions",
    "source_systems",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stock_locations",
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_observation_dispositions",
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
    "stocktake_tasks",
    "sync_batches",
    "sync_inbox_events",
    "sync_runs",
    "users",
)
API_INSERT_TABLES = (
    "audit_events",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_transactions",
    "login_challenges",
    "outbox_events",
    "role_assignments",
    "serial_current_positions",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stocktake_posting_items",
    "stocktake_postings",
)
API_UPDATE_TABLES = (
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "users",
)
API_DELETE_TABLES = ("auth_login_rate_limit_buckets",)
API_UPDATE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "audit_chain_heads": (
        "last_event_id",
        "last_hash",
        "version",
        "updated_at",
    ),
    "inventory_freezes": (
        "status",
        "valid_to",
        "released_by_user_id",
        "release_reason",
        "version",
        "updated_at",
    ),
    "inventory_ledger_heads": ("next_cursor", "updated_at"),
    "serial_current_positions": (
        "stock_account_id",
        "last_movement_id",
        "updated_at",
    ),
    "stock_balances": (
        "quantity",
        "ledger_cursor",
        "version",
        "updated_at",
    ),
    "stocktake_tasks": (
        "status",
        "posted_at",
        "closed_at",
        "version",
        "updated_at",
    ),
}

DOWNGRADE_BLOCKER = (
    "cannot downgrade 0023: opening observation posting facts require the "
    "recount terminal graph"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0023 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0023 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
        _replace_sqlite_posting_item_trigger(current=True)
        return

    op.execute(_postgresql_posting_item_function_sql(current=True))
    op.execute(
        "ALTER TABLE public.stocktake_posting_items ENABLE ALWAYS TRIGGER "
        f"{SQLITE_POSTING_ITEM_TRIGGER}"
    )
    op.execute(_postgresql_graph_function_sql(current=True))
    op.execute(_postgresql_account_function_sql())
    op.execute(
        f"CREATE CONSTRAINT TRIGGER {PG_ACCOUNT_TRIGGER} "
        "AFTER INSERT ON public.stock_accounts DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION public.{PG_ACCOUNT_FUNCTION}()"
    )
    op.execute(
        "ALTER TABLE public.stock_accounts ENABLE ALWAYS TRIGGER "
        f"{PG_ACCOUNT_TRIGGER}"
    )
    op.execute(
        f"REVOKE EXECUTE ON FUNCTION public.{PG_ACCOUNT_FUNCTION}() "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    _apply_postgresql_stock_account_acl(current=True)


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0023 SQLite downgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
        _sqlite_downgrade_preflight()
        _replace_sqlite_posting_item_trigger(current=False)
        return

    _postgresql_downgrade_preflight()
    _apply_postgresql_stock_account_acl(current=False)
    op.execute(
        f"DROP TRIGGER {PG_ACCOUNT_TRIGGER} ON public.stock_accounts"
    )
    op.execute(f"DROP FUNCTION public.{PG_ACCOUNT_FUNCTION}()")
    op.execute(_postgresql_graph_function_sql(current=False))
    op.execute(_postgresql_posting_item_function_sql(current=False))
    op.execute(
        "ALTER TABLE public.stocktake_posting_items ENABLE TRIGGER "
        f"{SQLITE_POSTING_ITEM_TRIGGER}"
    )


def _load_previous_migration() -> ModuleType:
    path = Path(__file__).with_name(
        "20260831_0022_opening_terminal_runtime_boundary.py"
    )
    spec = importlib.util.spec_from_file_location(
        "cloud_oam_alembic_20260831_0022", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the immutable 0022 migration source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _replace_once(sql: str, old: str, new: str, *, label: str) -> str:
    if sql.count(old) != 1:
        raise RuntimeError(f"0023 cannot locate the 0022 {label} SQL boundary")
    return sql.replace(old, new, 1)


def _postgresql_posting_item_function_sql(*, current: bool) -> str:
    if not current:
        return _postgresql_legacy_posting_item_function_sql()
    return f"""
CREATE OR REPLACE FUNCTION public.{PG_POSTING_ITEM_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_kind text;
    parent_transaction uuid;
    parent_posted_at timestamptz;
    movement_transaction uuid;
    movement_from uuid;
    movement_to uuid;
    movement_boundary text;
    movement_quantity numeric(18, 3);
    difference_kind text;
BEGIN
    SELECT posting_kind, inventory_transaction_id, posted_at
      INTO parent_kind, parent_transaction, parent_posted_at
      FROM public.stocktake_postings
     WHERE id = NEW.posting_id
       AND task_id = NEW.task_id
       AND round_id = NEW.round_id;
    IF parent_kind IS NULL OR parent_transaction IS NULL THEN
        RAISE EXCEPTION 'stocktake posting item parent is invalid'
            USING ERRCODE = '23514';
    END IF;

    SELECT transaction_id, from_account_id, to_account_id,
           external_boundary_code, quantity
      INTO movement_transaction, movement_from, movement_to,
           movement_boundary, movement_quantity
      FROM public.inventory_movements
     WHERE id = NEW.inventory_movement_id;
    IF movement_transaction IS NULL
       OR movement_transaction IS DISTINCT FROM parent_transaction
       OR movement_quantity IS DISTINCT FROM NEW.quantity THEN
        RAISE EXCEPTION 'stocktake posting item movement is invalid'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.difference_id IS NOT NULL THEN
        SELECT difference_type INTO difference_kind
          FROM public.stocktake_differences
         WHERE id = NEW.difference_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF difference_kind IS NULL OR difference_kind = 'control_unassigned' THEN
            RAISE EXCEPTION
                'control-only difference cannot produce inventory movement'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF parent_kind = 'opening' THEN
        IF movement_from IS NOT NULL
           OR movement_to IS NULL
           OR movement_boundary IS DISTINCT FROM
              'approved-opening-stocktake' THEN
            RAISE EXCEPTION 'opening posting item movement is invalid'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.count_line_id IS NOT NULL AND NEW.difference_id IS NULL THEN
            IF NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_lines AS count_line
                 WHERE count_line.id = NEW.count_line_id
                   AND count_line.task_id = NEW.task_id
                   AND count_line.round_id = NEW.round_id
                   AND count_line.stock_account_id = movement_to
                   AND count_line.counted_qty = NEW.quantity
                   AND count_line.counted_qty > 0
            ) THEN
                RAISE EXCEPTION
                    'opening movement does not match its physical count line'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.count_line_id IS NULL AND NEW.difference_id IS NOT NULL THEN
            IF NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_differences AS difference
                  JOIN public.stocktake_count_observations AS observation
                    ON observation.id = difference.observed_line_id
                   AND observation.task_id = difference.task_id
                   AND observation.round_id = difference.round_id
                   AND observation.scope_id = difference.scope_id
                  JOIN public.stocktake_rounds AS round_row
                    ON round_row.id = observation.round_id
                   AND round_row.task_id = observation.task_id
                  JOIN public.stocktake_tasks AS task
                    ON task.id = observation.task_id
                  JOIN public.stocktake_scopes AS scope
                    ON scope.id = observation.scope_id
                   AND scope.task_id = observation.task_id
                  JOIN public.stock_accounts AS account
                    ON account.id = movement_to
                 WHERE difference.id = NEW.difference_id
                   AND difference.task_id = NEW.task_id
                   AND difference.round_id = NEW.round_id
                   AND difference.difference_type = 'excess'
                   AND difference.control_snapshot_line_id IS NULL
                   AND difference.expected_account_id IS NULL
                   AND difference.observed_account_id IS NULL
                   AND difference.material_id = observation.material_id
                   AND difference.serial_id IS NOT DISTINCT FROM
                       observation.serial_id
                   AND difference.book_qty = 0
                   AND difference.counted_qty = observation.counted_qty
                   AND difference.difference_qty = observation.counted_qty
                   AND difference.affected_qty = observation.counted_qty
                   AND difference.reason_code =
                       'opening_unexpected_dimension'
                   AND difference.evidence_required
                   AND observation.verification_status = 'verified'
                   AND observation.material_id IS NOT NULL
                   AND observation.counted_qty = NEW.quantity
                   AND round_row.round_no > 1
                   AND round_row.round_no = task.current_round_no
                   AND round_row.round_type = 'recount'
                   AND round_row.status = 'submitted'
                   AND task.task_type = 'opening'
                   AND task.status IN ('approved', 'posted', 'closed')
                   AND task.cutoff_at IS NOT NULL
                   AND scope.owner_org_id = observation.owner_org_id
                   AND scope.location_id = observation.location_id
                   AND scope.custodian_person_id_snapshot IS NOT DISTINCT FROM
                       observation.custodian_person_id_snapshot
                   AND account.owner_org_id = observation.owner_org_id
                   AND account.custodian_person_id IS NOT DISTINCT FROM
                       observation.custodian_person_id_snapshot
                   AND account.location_id = observation.location_id
                   AND account.material_id = observation.material_id
                   AND account.condition_code = observation.condition_code
                   AND account.availability_bucket =
                       observation.availability_bucket
                   AND account.lot_id IS NOT DISTINCT FROM observation.lot_id
                   AND account.created_at > task.cutoff_at
                   AND account.created_at <= parent_posted_at
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_reviews AS region_review
                         JOIN public.stocktake_review_items AS region_item
                           ON region_item.review_id = region_review.id
                          AND region_item.task_id = region_review.task_id
                          AND region_item.round_id = region_review.round_id
                          AND region_item.difference_id = difference.id
                          AND region_item.decision = 'accept_for_posting'
                        WHERE region_review.task_id = task.id
                          AND region_review.round_id = round_row.id
                          AND region_review.review_stage = 'region'
                          AND region_review.decision = 'approve'
                   )
                   AND EXISTS (
                       SELECT 1
                         FROM public.stocktake_reviews AS hq_review
                         JOIN public.stocktake_review_items AS hq_item
                           ON hq_item.review_id = hq_review.id
                          AND hq_item.task_id = hq_review.task_id
                          AND hq_item.round_id = hq_review.round_id
                          AND hq_item.difference_id = difference.id
                          AND hq_item.decision = 'accept_for_posting'
                        WHERE hq_review.task_id = task.id
                          AND hq_review.round_id = round_row.id
                          AND hq_review.review_stage = 'headquarters'
                          AND hq_review.decision = 'approve'
                   )
            ) THEN
                RAISE EXCEPTION
                    'opening observation movement is not a verified recount excess'
                    USING ERRCODE = '23514';
            END IF;
        ELSE
            RAISE EXCEPTION
                'opening posting item source is invalid'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_legacy_posting_item_function_sql() -> str:
    return f"""
CREATE OR REPLACE FUNCTION public.{PG_POSTING_ITEM_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    parent_kind text;
    parent_transaction uuid;
    movement_transaction uuid;
    movement_from uuid;
    movement_to uuid;
    movement_quantity numeric(18, 3);
    count_account uuid;
    difference_kind text;
BEGIN
    SELECT posting_kind, inventory_transaction_id
      INTO parent_kind, parent_transaction
      FROM public.stocktake_postings
     WHERE id = NEW.posting_id
       AND task_id = NEW.task_id
       AND round_id = NEW.round_id;
    IF parent_kind IS NULL OR parent_transaction IS NULL THEN
        RAISE EXCEPTION 'stocktake posting item parent is invalid';
    END IF;

    SELECT transaction_id, from_account_id, to_account_id, quantity
      INTO movement_transaction, movement_from, movement_to, movement_quantity
      FROM public.inventory_movements
     WHERE id = NEW.inventory_movement_id;
    IF movement_transaction IS NULL
       OR movement_transaction IS DISTINCT FROM parent_transaction
       OR movement_quantity IS DISTINCT FROM NEW.quantity THEN
        RAISE EXCEPTION 'stocktake posting item movement is invalid';
    END IF;

    IF NEW.difference_id IS NOT NULL THEN
        SELECT difference_type INTO difference_kind
          FROM public.stocktake_differences
         WHERE id = NEW.difference_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF difference_kind IS NULL OR difference_kind = 'control_unassigned' THEN
            RAISE EXCEPTION
                'control-only difference cannot produce inventory movement';
        END IF;
    END IF;

    IF parent_kind = 'opening' THEN
        IF NEW.count_line_id IS NULL OR NEW.difference_id IS NOT NULL THEN
            RAISE EXCEPTION
                'opening posting items require physical count lines';
        END IF;
        SELECT stock_account_id INTO count_account
          FROM public.stocktake_count_lines
         WHERE id = NEW.count_line_id
           AND task_id = NEW.task_id
           AND round_id = NEW.round_id;
        IF count_account IS NULL
           OR movement_from IS NOT NULL
           OR movement_to IS DISTINCT FROM count_account THEN
            RAISE EXCEPTION
                'opening movement does not match its physical count line';
        END IF;
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_graph_function_sql(*, current: bool) -> str:
    previous = _load_previous_migration()
    sql = previous._postgresql_graph_function_sql()
    sql = _replace_once(
        sql,
        f"CREATE FUNCTION public.{PG_GRAPH_CHECK_FUNCTION}",
        f"CREATE OR REPLACE FUNCTION public.{PG_GRAPH_CHECK_FUNCTION}",
        label="terminal graph declaration",
    )
    if not current:
        return sql

    old_total = """           AND posting.total_quantity = (
                SELECT COALESCE(sum(count_line.counted_qty), 0)
                  FROM public.stocktake_count_lines AS count_line
                 WHERE count_line.task_id = task.id
                   AND count_line.round_id = posting.round_id
           )"""
    new_total = """           AND posting.total_quantity = (
                SELECT COALESCE(sum(source_row.quantity), 0)
                  FROM (
                       SELECT count_line.counted_qty AS quantity
                         FROM public.stocktake_count_lines AS count_line
                        WHERE count_line.task_id = task.id
                          AND count_line.round_id = posting.round_id
                       UNION ALL
                       SELECT observation.counted_qty AS quantity
                         FROM public.stocktake_count_observations AS observation
                        WHERE observation.task_id = task.id
                          AND observation.round_id = posting.round_id
                          AND observation.verification_status = 'verified'
                  ) AS source_row
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_observations AS observation
                 WHERE observation.task_id = task.id
                   AND observation.round_id = posting.round_id
                   AND observation.verification_status <> 'verified'
           )"""
    sql = _replace_once(
        sql,
        old_total,
        new_total,
        label="posting total",
    )

    old_count_coverage = """           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_lines AS count_line
                 WHERE count_line.task_id = task.id
                   AND count_line.round_id = posting.round_id
                   AND count_line.counted_qty > 0
                   AND NOT EXISTS (
                       SELECT 1
                         FROM public.stocktake_posting_items AS item
                        WHERE item.posting_id = posting.id
                          AND item.task_id = task.id
                          AND item.round_id = posting.round_id
                          AND item.count_line_id = count_line.id
                          AND item.difference_id IS NULL
                          AND item.quantity = count_line.counted_qty
                   )
           )"""
    new_count_and_observation_coverage = old_count_coverage + """
           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_count_observations AS observation
                 WHERE observation.task_id = task.id
                   AND observation.round_id = posting.round_id
                   AND observation.verification_status = 'verified'
                   AND (
                       (
                           SELECT count(*)
                             FROM public.stocktake_differences AS difference
                            WHERE difference.observed_line_id = observation.id
                              AND difference.task_id = observation.task_id
                              AND difference.round_id = observation.round_id
                              AND difference.scope_id = observation.scope_id
                       ) <> 1
                       OR NOT EXISTS (
                           SELECT 1
                             FROM public.stocktake_differences AS difference
                             JOIN public.stocktake_posting_items AS item
                               ON item.posting_id = posting.id
                              AND item.task_id = difference.task_id
                              AND item.round_id = difference.round_id
                              AND item.count_line_id IS NULL
                              AND item.difference_id = difference.id
                              AND item.quantity = observation.counted_qty
                            WHERE difference.observed_line_id = observation.id
                              AND difference.task_id = observation.task_id
                              AND difference.round_id = observation.round_id
                              AND difference.scope_id = observation.scope_id
                       )
                   )
           )"""
    sql = _replace_once(
        sql,
        old_count_coverage,
        new_count_and_observation_coverage,
        label="source coverage",
    )

    old_item_validity = """           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_posting_items AS item
                  LEFT JOIN public.stocktake_count_lines AS count_line
                    ON count_line.id = item.count_line_id
                   AND count_line.task_id = item.task_id
                   AND count_line.round_id = item.round_id
                   AND count_line.counted_qty = item.quantity
                   AND count_line.counted_qty > 0
                 WHERE item.posting_id = posting.id
                   AND count_line.id IS NULL
           )"""
    new_item_validity = """           AND NOT EXISTS (
                SELECT 1
                  FROM public.stocktake_posting_items AS item
                 WHERE item.posting_id = posting.id
                   AND NOT (
                       (
                           item.count_line_id IS NOT NULL
                           AND item.difference_id IS NULL
                           AND EXISTS (
                               SELECT 1
                                 FROM public.stocktake_count_lines AS count_line
                                WHERE count_line.id = item.count_line_id
                                  AND count_line.task_id = item.task_id
                                  AND count_line.round_id = item.round_id
                                  AND count_line.counted_qty = item.quantity
                                  AND count_line.counted_qty > 0
                           )
                       )
                       OR
                       (
                           item.count_line_id IS NULL
                           AND item.difference_id IS NOT NULL
                           AND EXISTS (
                               SELECT 1
                                 FROM public.stocktake_differences AS difference
                                 JOIN public.stocktake_count_observations AS observation
                                   ON observation.id = difference.observed_line_id
                                  AND observation.task_id = difference.task_id
                                  AND observation.round_id = difference.round_id
                                  AND observation.scope_id = difference.scope_id
                                 JOIN public.stocktake_scopes AS scope
                                   ON scope.id = observation.scope_id
                                  AND scope.task_id = observation.task_id
                                 JOIN public.stock_accounts AS account
                                   ON account.owner_org_id = observation.owner_org_id
                                  AND account.custodian_person_id IS NOT DISTINCT FROM
                                      observation.custodian_person_id_snapshot
                                  AND account.location_id = observation.location_id
                                  AND account.material_id = observation.material_id
                                  AND account.condition_code = observation.condition_code
                                  AND account.availability_bucket =
                                      observation.availability_bucket
                                  AND account.lot_id IS NOT DISTINCT FROM
                                      observation.lot_id
                                 JOIN public.inventory_movements AS movement
                                   ON movement.id = item.inventory_movement_id
                                  AND movement.transaction_id = transaction_row.id
                                  AND movement.from_account_id IS NULL
                                  AND movement.to_account_id = account.id
                                  AND movement.quantity = item.quantity
                                  AND movement.external_boundary_code =
                                      'approved-opening-stocktake'
                                WHERE difference.id = item.difference_id
                                  AND difference.task_id = item.task_id
                                  AND difference.round_id = item.round_id
                                  AND difference.difference_type = 'excess'
                                  AND difference.control_snapshot_line_id IS NULL
                                  AND difference.expected_account_id IS NULL
                                  AND difference.observed_account_id IS NULL
                                  AND difference.material_id = observation.material_id
                                  AND difference.serial_id IS NOT DISTINCT FROM
                                      observation.serial_id
                                  AND difference.book_qty = 0
                                  AND difference.counted_qty =
                                      observation.counted_qty
                                  AND difference.difference_qty =
                                      observation.counted_qty
                                  AND difference.affected_qty =
                                      observation.counted_qty
                                  AND difference.reason_code =
                                      'opening_unexpected_dimension'
                                  AND difference.evidence_required
                                  AND observation.verification_status = 'verified'
                                  AND observation.material_id IS NOT NULL
                                  AND observation.counted_qty = item.quantity
                                  AND scope.owner_org_id = observation.owner_org_id
                                  AND scope.location_id = observation.location_id
                                  AND scope.custodian_person_id_snapshot
                                      IS NOT DISTINCT FROM
                                      observation.custodian_person_id_snapshot
                                  AND round_row.round_no > 1
                                  AND round_row.round_type = 'recount'
                                  AND account.created_at > task.cutoff_at
                                  AND account.created_at <= posting.posted_at
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_reviews
                                             AS region_review
                                        JOIN public.stocktake_review_items
                                             AS region_item
                                          ON region_item.review_id =
                                             region_review.id
                                         AND region_item.task_id =
                                             region_review.task_id
                                         AND region_item.round_id =
                                             region_review.round_id
                                         AND region_item.difference_id =
                                             difference.id
                                         AND region_item.decision =
                                             'accept_for_posting'
                                       WHERE region_review.task_id = task.id
                                         AND region_review.round_id = round_row.id
                                         AND region_review.review_stage = 'region'
                                         AND region_review.decision = 'approve'
                                  )
                                  AND EXISTS (
                                      SELECT 1
                                        FROM public.stocktake_reviews AS hq_review
                                        JOIN public.stocktake_review_items AS hq_item
                                          ON hq_item.review_id = hq_review.id
                                         AND hq_item.task_id = hq_review.task_id
                                         AND hq_item.round_id = hq_review.round_id
                                         AND hq_item.difference_id = difference.id
                                         AND hq_item.decision =
                                             'accept_for_posting'
                                       WHERE hq_review.task_id = task.id
                                         AND hq_review.round_id = round_row.id
                                         AND hq_review.review_stage =
                                             'headquarters'
                                         AND hq_review.decision = 'approve'
                                  )
                                  AND NOT EXISTS (
                                      SELECT 1
                                        FROM public.inventory_movements AS prior
                                        JOIN public.inventory_transactions AS prior_tx
                                          ON prior_tx.id = prior.transaction_id
                                       WHERE prior.id <> movement.id
                                         AND (
                                              prior.from_account_id = account.id
                                              OR prior.to_account_id = account.id
                                         )
                                         AND prior_tx.ledger_cursor <
                                             transaction_row.ledger_cursor
                                  )
                           )
                       )
                   )
           )"""
    sql = _replace_once(
        sql,
        old_item_validity,
        new_item_validity,
        label="posting item validity",
    )

    old_movement_serial_coverage = """                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          JOIN public.inventory_movement_serials AS movement_serial
                            ON movement_serial.movement_id =
                               item.inventory_movement_id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                          LEFT JOIN public.stocktake_count_serials AS count_serial
                            ON count_serial.count_line_id = item.count_line_id
                           AND count_serial.round_id = item.round_id
                           AND count_serial.serial_id = movement_serial.serial_id
                         WHERE item.posting_id = posting.id
                           AND count_serial.serial_id IS NULL
                    )"""
    new_movement_serial_coverage = """                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          JOIN public.inventory_movement_serials AS movement_serial
                            ON movement_serial.movement_id =
                               item.inventory_movement_id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                         WHERE item.posting_id = posting.id
                           AND NOT (
                               (
                                   item.count_line_id IS NOT NULL
                                   AND item.difference_id IS NULL
                                   AND EXISTS (
                                       SELECT 1
                                         FROM public.stocktake_count_serials
                                              AS count_serial
                                        WHERE count_serial.count_line_id =
                                              item.count_line_id
                                          AND count_serial.round_id = item.round_id
                                          AND count_serial.serial_id =
                                              movement_serial.serial_id
                                   )
                               )
                               OR
                               (
                                   item.count_line_id IS NULL
                                   AND item.difference_id IS NOT NULL
                                   AND EXISTS (
                                       SELECT 1
                                         FROM public.stocktake_differences AS difference
                                         JOIN public.stocktake_count_observations
                                              AS observation
                                           ON observation.id =
                                              difference.observed_line_id
                                          AND observation.task_id =
                                              difference.task_id
                                          AND observation.round_id =
                                              difference.round_id
                                        WHERE difference.id = item.difference_id
                                          AND observation.serial_id =
                                              movement_serial.serial_id
                                   )
                               )
                           )
                    )"""
    sql = _replace_once(
        sql,
        old_movement_serial_coverage,
        new_movement_serial_coverage,
        label="movement serial coverage",
    )

    old_source_serial_coverage = """                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          JOIN public.stocktake_count_serials AS count_serial
                            ON count_serial.count_line_id = item.count_line_id
                           AND count_serial.round_id = item.round_id
                          LEFT JOIN public.inventory_movement_serials AS movement_serial
                            ON movement_serial.movement_id =
                               item.inventory_movement_id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                           AND movement_serial.serial_id = count_serial.serial_id
                         WHERE item.posting_id = posting.id
                           AND movement_serial.serial_id IS NULL
                    )"""
    new_source_serial_coverage = old_source_serial_coverage + """
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.stocktake_posting_items AS item
                          JOIN public.stocktake_differences AS difference
                            ON difference.id = item.difference_id
                           AND difference.task_id = item.task_id
                           AND difference.round_id = item.round_id
                          JOIN public.stocktake_count_observations AS observation
                            ON observation.id = difference.observed_line_id
                           AND observation.task_id = difference.task_id
                           AND observation.round_id = difference.round_id
                          LEFT JOIN public.inventory_movement_serials AS movement_serial
                            ON movement_serial.movement_id =
                               item.inventory_movement_id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                           AND movement_serial.serial_id = observation.serial_id
                         WHERE item.posting_id = posting.id
                           AND observation.serial_id IS NOT NULL
                           AND movement_serial.serial_id IS NULL
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.inventory_movement_serials
                               AS movement_serial
                          JOIN public.inventory_movements AS movement
                            ON movement.id = movement_serial.movement_id
                           AND movement.transaction_id = transaction_row.id
                          LEFT JOIN public.serial_current_positions AS position
                            ON position.serial_id = movement_serial.serial_id
                           AND position.stock_account_id = movement.to_account_id
                           AND position.last_movement_id = movement.id
                         WHERE movement_serial.transaction_id =
                               transaction_row.id
                           AND position.serial_id IS NULL
                    )
                    AND NOT EXISTS (
                        SELECT 1
                          FROM public.serial_current_positions AS position
                          JOIN public.inventory_movements AS movement
                            ON movement.id = position.last_movement_id
                           AND movement.transaction_id = transaction_row.id
                          LEFT JOIN public.inventory_movement_serials
                               AS movement_serial
                            ON movement_serial.movement_id = movement.id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                           AND movement_serial.serial_id = position.serial_id
                         WHERE movement_serial.serial_id IS NULL
                    )"""
    return _replace_once(
        sql,
        old_source_serial_coverage,
        new_source_serial_coverage,
        label="source serial coverage",
    )


def _postgresql_account_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_ACCOUNT_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF current_user <> '{PRODUCTION_API_ROLE}' THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM public.inventory_movements AS movement
          JOIN public.inventory_transactions AS transaction_row
            ON transaction_row.id = movement.transaction_id
           AND transaction_row.movement_type = 'opening'
           AND transaction_row.status = 'posted'
           AND transaction_row.source_document_type = 'opening_stocktake'
          JOIN public.stocktake_posting_items AS item
            ON item.inventory_movement_id = movement.id
           AND item.count_line_id IS NULL
           AND item.difference_id IS NOT NULL
           AND item.quantity = movement.quantity
          JOIN public.stocktake_postings AS posting
            ON posting.id = item.posting_id
           AND posting.task_id = item.task_id
           AND posting.round_id = item.round_id
           AND posting.posting_kind = 'opening'
           AND posting.inventory_transaction_id = transaction_row.id
          JOIN public.stocktake_differences AS difference
            ON difference.id = item.difference_id
           AND difference.task_id = item.task_id
           AND difference.round_id = item.round_id
          JOIN public.stocktake_count_observations AS observation
            ON observation.id = difference.observed_line_id
           AND observation.task_id = difference.task_id
           AND observation.round_id = difference.round_id
           AND observation.scope_id = difference.scope_id
          JOIN public.stocktake_rounds AS round_row
            ON round_row.id = observation.round_id
           AND round_row.task_id = observation.task_id
          JOIN public.stocktake_tasks AS task
            ON task.id = observation.task_id
          JOIN public.stocktake_scopes AS scope
            ON scope.id = observation.scope_id
           AND scope.task_id = observation.task_id
          JOIN public.materials AS material
            ON material.id = NEW.material_id
           AND material.status = 'active'
          JOIN public.stock_locations AS location
            ON location.id = NEW.location_id
           AND location.status = 'active'
          JOIN public.stock_balances AS balance
            ON balance.stock_account_id = NEW.id
           AND balance.ledger_cursor = transaction_row.ledger_cursor
           AND balance.version = 1
         WHERE movement.from_account_id IS NULL
           AND movement.to_account_id = NEW.id
           AND movement.external_boundary_code =
               'approved-opening-stocktake'
           AND transaction_row.source_document_id = task.id::text
           AND transaction_row.ledger_cursor > task.cutoff_ledger_cursor
           AND posting.total_quantity > 0
           AND posting.posted_at = transaction_row.posted_at
           AND task.task_type = 'opening'
           AND task.status IN ('posted', 'closed')
           AND task.posted_at = posting.posted_at
           AND task.current_round_no = round_row.round_no
           AND round_row.round_no > 1
           AND round_row.round_type = 'recount'
           AND round_row.status = 'submitted'
           AND observation.verification_status = 'verified'
           AND observation.material_id IS NOT NULL
           AND observation.counted_qty = item.quantity
           AND difference.difference_type = 'excess'
           AND difference.control_snapshot_line_id IS NULL
           AND difference.expected_account_id IS NULL
           AND difference.observed_account_id IS NULL
           AND difference.material_id = observation.material_id
           AND difference.serial_id IS NOT DISTINCT FROM observation.serial_id
           AND difference.book_qty = 0
           AND difference.counted_qty = observation.counted_qty
           AND difference.difference_qty = observation.counted_qty
           AND difference.affected_qty = observation.counted_qty
           AND difference.reason_code = 'opening_unexpected_dimension'
           AND difference.evidence_required
           AND scope.owner_org_id = observation.owner_org_id
           AND scope.location_id = observation.location_id
           AND scope.custodian_person_id_snapshot IS NOT DISTINCT FROM
               observation.custodian_person_id_snapshot
           AND NEW.owner_org_id = observation.owner_org_id
           AND NEW.custodian_person_id IS NOT DISTINCT FROM
               observation.custodian_person_id_snapshot
           AND NEW.location_id = observation.location_id
           AND NEW.material_id = observation.material_id
           AND NEW.condition_code = observation.condition_code
           AND NEW.availability_bucket = observation.availability_bucket
           AND NEW.lot_id IS NOT DISTINCT FROM observation.lot_id
           AND NEW.created_at > task.cutoff_at
           AND NEW.created_at <= posting.posted_at
           AND NEW.updated_at = NEW.created_at
           AND balance.quantity = (
                SELECT COALESCE(sum(account_movement.quantity), 0)
                  FROM public.inventory_movements AS account_movement
                 WHERE account_movement.transaction_id = transaction_row.id
                   AND account_movement.from_account_id IS NULL
                   AND account_movement.to_account_id = NEW.id
                   AND account_movement.external_boundary_code =
                       'approved-opening-stocktake'
           )
           AND EXISTS (
                SELECT 1
                  FROM public.stocktake_reviews AS region_review
                  JOIN public.stocktake_review_items AS region_item
                    ON region_item.review_id = region_review.id
                   AND region_item.task_id = region_review.task_id
                   AND region_item.round_id = region_review.round_id
                   AND region_item.difference_id = difference.id
                   AND region_item.decision = 'accept_for_posting'
                 WHERE region_review.task_id = task.id
                   AND region_review.round_id = round_row.id
                   AND region_review.review_stage = 'region'
                   AND region_review.decision = 'approve'
           )
           AND EXISTS (
                SELECT 1
                  FROM public.stocktake_reviews AS hq_review
                  JOIN public.stocktake_review_items AS hq_item
                    ON hq_item.review_id = hq_review.id
                   AND hq_item.task_id = hq_review.task_id
                   AND hq_item.round_id = hq_review.round_id
                   AND hq_item.difference_id = difference.id
                   AND hq_item.decision = 'accept_for_posting'
                 WHERE hq_review.task_id = task.id
                   AND hq_review.round_id = round_row.id
                   AND hq_review.review_stage = 'headquarters'
                   AND hq_review.decision = 'approve'
           )
           AND (
                location.location_type <> 'personal'
                OR (
                    location.custodian_person_id IS NOT NULL
                    AND location.custodian_person_id = NEW.custodian_person_id
                )
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM public.inventory_movements AS prior
                  JOIN public.inventory_transactions AS prior_tx
                    ON prior_tx.id = prior.transaction_id
                 WHERE prior.id <> movement.id
                   AND (
                        prior.from_account_id = NEW.id
                        OR prior.to_account_id = NEW.id
                   )
                   AND prior_tx.ledger_cursor < transaction_row.ledger_cursor
           )
           AND NOT EXISTS (
                SELECT 1
                  FROM public.inventory_movement_serials AS movement_serial
                 WHERE movement_serial.movement_id = movement.id
                   AND movement_serial.transaction_id = transaction_row.id
                   AND movement_serial.serial_id IS DISTINCT FROM
                       observation.serial_id
           )
           AND (
                observation.serial_id IS NULL
                OR (
                    EXISTS (
                        SELECT 1
                          FROM public.inventory_movement_serials
                               AS movement_serial
                         WHERE movement_serial.movement_id = movement.id
                           AND movement_serial.transaction_id =
                               transaction_row.id
                           AND movement_serial.serial_id = observation.serial_id
                    )
                    AND EXISTS (
                        SELECT 1
                          FROM public.serial_current_positions AS position
                         WHERE position.serial_id = observation.serial_id
                           AND position.stock_account_id = NEW.id
                           AND position.last_movement_id = movement.id
                    )
                )
           )
           AND public.{PG_GRAPH_CHECK_FUNCTION}(
                   task.id,
                   transaction_row.id
               )
    ) THEN
        RAISE EXCEPTION
            'API stock account insert must terminate a verified opening recount observation graph'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$
"""


def _replace_sqlite_posting_item_trigger(*, current: bool) -> None:
    op.execute(f"DROP TRIGGER {SQLITE_POSTING_ITEM_TRIGGER}")
    if not current:
        op.execute(_sqlite_legacy_posting_item_trigger_sql())
        return
    op.execute(_sqlite_posting_item_trigger_sql())


def _sqlite_posting_item_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_POSTING_ITEM_TRIGGER}
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
    ) AND NOT (
        (
            NEW.count_line_id IS NOT NULL
            AND NEW.difference_id IS NULL
            AND EXISTS (
                SELECT 1
                  FROM stocktake_count_lines AS count_line
                  JOIN inventory_movements AS movement
                    ON movement.id = NEW.inventory_movement_id
                 WHERE count_line.id = NEW.count_line_id
                   AND count_line.task_id = NEW.task_id
                   AND count_line.round_id = NEW.round_id
                   AND count_line.counted_qty = NEW.quantity
                   AND count_line.counted_qty > 0
                   AND movement.from_account_id IS NULL
                   AND movement.to_account_id = count_line.stock_account_id
                   AND movement.external_boundary_code =
                       'approved-opening-stocktake'
            )
        )
        OR
        (
            NEW.count_line_id IS NULL
            AND NEW.difference_id IS NOT NULL
            AND EXISTS (
                SELECT 1
                  FROM stocktake_differences AS difference
                  JOIN stocktake_count_observations AS observation
                    ON observation.id = difference.observed_line_id
                   AND observation.task_id = difference.task_id
                   AND observation.round_id = difference.round_id
                   AND observation.scope_id = difference.scope_id
                  JOIN stocktake_rounds AS round_row
                    ON round_row.id = observation.round_id
                   AND round_row.task_id = observation.task_id
                  JOIN stocktake_tasks AS task
                    ON task.id = observation.task_id
                  JOIN stocktake_scopes AS scope
                    ON scope.id = observation.scope_id
                   AND scope.task_id = observation.task_id
                  JOIN inventory_movements AS movement
                    ON movement.id = NEW.inventory_movement_id
                  JOIN stock_accounts AS account
                    ON account.id = movement.to_account_id
                 WHERE difference.id = NEW.difference_id
                   AND difference.task_id = NEW.task_id
                   AND difference.round_id = NEW.round_id
                   AND difference.difference_type = 'excess'
                   AND difference.control_snapshot_line_id IS NULL
                   AND difference.expected_account_id IS NULL
                   AND difference.observed_account_id IS NULL
                   AND difference.material_id = observation.material_id
                   AND difference.serial_id IS observation.serial_id
                   AND difference.book_qty = 0
                   AND difference.counted_qty = observation.counted_qty
                   AND difference.difference_qty = observation.counted_qty
                   AND difference.affected_qty = observation.counted_qty
                   AND difference.reason_code =
                       'opening_unexpected_dimension'
                   AND difference.evidence_required = 1
                   AND observation.verification_status = 'verified'
                   AND observation.material_id IS NOT NULL
                   AND observation.counted_qty = NEW.quantity
                   AND round_row.round_no > 1
                   AND round_row.round_no = task.current_round_no
                   AND round_row.round_type = 'recount'
                   AND round_row.status = 'submitted'
                   AND task.task_type = 'opening'
                   AND task.status IN ('approved', 'posted', 'closed')
                   AND task.cutoff_at IS NOT NULL
                   AND scope.owner_org_id = observation.owner_org_id
                   AND scope.location_id = observation.location_id
                   AND scope.custodian_person_id_snapshot IS
                       observation.custodian_person_id_snapshot
                   AND account.owner_org_id = observation.owner_org_id
                   AND account.custodian_person_id IS
                       observation.custodian_person_id_snapshot
                   AND account.location_id = observation.location_id
                   AND account.material_id = observation.material_id
                   AND account.condition_code = observation.condition_code
                   AND account.availability_bucket =
                       observation.availability_bucket
                   AND account.lot_id IS observation.lot_id
                   AND account.created_at > task.cutoff_at
                   AND account.created_at <= (
                       SELECT posting.posted_at
                         FROM stocktake_postings AS posting
                        WHERE posting.id = NEW.posting_id
                   )
                   AND EXISTS (
                       SELECT 1
                         FROM stocktake_reviews AS region_review
                         JOIN stocktake_review_items AS region_item
                           ON region_item.review_id = region_review.id
                          AND region_item.task_id = region_review.task_id
                          AND region_item.round_id = region_review.round_id
                          AND region_item.difference_id = difference.id
                          AND region_item.decision = 'accept_for_posting'
                        WHERE region_review.task_id = task.id
                          AND region_review.round_id = round_row.id
                          AND region_review.review_stage = 'region'
                          AND region_review.decision = 'approve'
                   )
                   AND EXISTS (
                       SELECT 1
                         FROM stocktake_reviews AS hq_review
                         JOIN stocktake_review_items AS hq_item
                           ON hq_item.review_id = hq_review.id
                          AND hq_item.task_id = hq_review.task_id
                          AND hq_item.round_id = hq_review.round_id
                          AND hq_item.difference_id = difference.id
                          AND hq_item.decision = 'accept_for_posting'
                        WHERE hq_review.task_id = task.id
                          AND hq_review.round_id = round_row.id
                          AND hq_review.review_stage = 'headquarters'
                          AND hq_review.decision = 'approve'
                   )
                   AND movement.from_account_id IS NULL
                   AND movement.external_boundary_code =
                       'approved-opening-stocktake'
                   AND NOT EXISTS (
                       SELECT 1
                         FROM inventory_movement_serials AS movement_serial
                        WHERE movement_serial.movement_id = movement.id
                          AND movement_serial.serial_id IS NOT
                              observation.serial_id
                   )
                   AND (
                       observation.serial_id IS NULL
                       OR EXISTS (
                           SELECT 1
                             FROM inventory_movement_serials AS movement_serial
                            WHERE movement_serial.movement_id = movement.id
                              AND movement_serial.serial_id =
                                  observation.serial_id
                       )
                   )
            )
        )
    ) THEN RAISE(ABORT,
        'opening posting item source is not a physical line or verified recount observation') END;
END
"""


def _sqlite_legacy_posting_item_trigger_sql() -> str:
    return f"""
CREATE TRIGGER {SQLITE_POSTING_ITEM_TRIGGER}
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


def _apply_postgresql_stock_account_acl(*, current: bool) -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0023 requires the provisioned star_oam_api runtime role';
    END IF;
END
$$
"""
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.stock_accounts "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"""
DO $$
DECLARE
    column_row record;
BEGIN
    FOR column_row IN
        SELECT attribute_row.attname
          FROM pg_class AS class_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = class_row.relnamespace
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = class_row.oid
         WHERE namespace_row.nspname = 'public'
           AND class_row.relname = 'stock_accounts'
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
         ORDER BY attribute_row.attnum
    LOOP
        EXECUTE format(
            'REVOKE SELECT (%1$I), INSERT (%1$I), UPDATE (%1$I), '
            'REFERENCES (%1$I) ON TABLE public.stock_accounts FROM PUBLIC, '
            '{PRODUCTION_API_ROLE}',
            column_row.attname
        );
    END LOOP;
END
$$
"""
    )
    op.execute(
        "GRANT SELECT ON TABLE public.stock_accounts "
        f"TO {PRODUCTION_API_ROLE}"
    )
    if current:
        op.execute(
            "GRANT INSERT ON TABLE public.stock_accounts "
            f"TO {PRODUCTION_API_ROLE}"
        )


def _postgresql_downgrade_preflight() -> None:
    op.execute(
        "LOCK TABLE public.stock_accounts, public.stocktake_posting_items, "
        "public.stocktake_postings, public.inventory_movements, "
        "public.inventory_transactions IN ACCESS EXCLUSIVE MODE"
    )
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_posting_items AS item
          JOIN public.stocktake_postings AS posting
            ON posting.id = item.posting_id
           AND posting.task_id = item.task_id
           AND posting.round_id = item.round_id
         WHERE posting.posting_kind = 'opening'
           AND item.count_line_id IS NULL
           AND item.difference_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$$
"""
    )


def _sqlite_downgrade_preflight() -> None:
    exists = op.get_bind().exec_driver_sql(
        "SELECT EXISTS ("
        "SELECT 1 FROM stocktake_posting_items AS item "
        "JOIN stocktake_postings AS posting "
        "ON posting.id = item.posting_id "
        "AND posting.task_id = item.task_id "
        "AND posting.round_id = item.round_id "
        "WHERE posting.posting_kind = 'opening' "
        "AND item.count_line_id IS NULL "
        "AND item.difference_id IS NOT NULL)"
    ).scalar_one()
    if bool(exists):
        raise RuntimeError(DOWNGRADE_BLOCKER)
