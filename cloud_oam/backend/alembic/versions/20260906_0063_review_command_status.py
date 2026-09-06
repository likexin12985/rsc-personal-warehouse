"""Persist non-opening review command version coordinates.

Revision ID: 20260906_0063
Revises: 20260906_0064

The 0064 finalizer lock is already released and must not be rewritten.  This
revision is therefore intentionally a linear child of 0064 (the numeric
suffix is a product capability name, not an ordering promise).  It adds the
two historical version coordinates needed to distinguish a review command's
precondition from the version it produced.  Existing opening-review rows stay
NULL-compatible; new non-opening rows are guarded by PostgreSQL and SQLite
triggers and a pair-continuity CHECK constraint.
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import context, op


revision: str = "20260906_0063"
down_revision: str | None = "20260906_0064"
branch_labels: str | None = None
depends_on: str | None = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PREVIOUS_SCHEMA_REVISION = "20260906_0064"
CHECK_CONSTRAINT = "ck_stocktake_reviews_task_version_pair_0063"
TRIGGER_FUNCTION = "rsc_validate_nonopening_stocktake_review_version_0063"
TRIGGER_SIGNATURE = f"public.{TRIGGER_FUNCTION}()"
TRIGGER_NAME = "trg_nonopening_review_version_0063"
SQLITE_INSERT_TRIGGER = f"{TRIGGER_NAME}_insert"
SQLITE_UPDATE_TRIGGER = f"{TRIGGER_NAME}_update"
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_BODY_SHA256_0064 = (
    "b75bb3c37c279a2a406a36be9049a187e9cef61dfd0892a48a2db0e8aef551c0"
)

# Filled from _postgresql_trigger_function_sql() and kept beside the catalog
# verifier so a source edit cannot silently change the production contract.
TRIGGER_BODY_SHA256 = (
    "e7b996ad8f85b6ffc099bc88adb622dc71e56fc239a737ff8475b980b99e4744"
)
RUNTIME_READY_BODY_SHA256_0063 = (
    "dd43dabe3a816b44b888b4cda1fdcbc84eeefef8b659323b031fb80a55c82cc4"
)

NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
CATALOG_ERROR = "0063 review command status catalog mismatch"
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0063 while non-opening review version facts exist"
)

# Keep the lock order deterministic and include the owner graph used by the
# existing review/replay validators.  This is intentionally broader than the
# two altered columns so an upgrade cannot race a live command or audit read.
LOCK_TABLES = (
    "alembic_version",
    "audit_events",
    "auth_identities",
    "organizations",
    "people",
    "permissions",
    "role_assignments",
    "roles",
    "state_transition_events",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_review_items",
    "stocktake_reviews",
    "stocktake_rounds",
    "stocktake_scopes",
    "stocktake_tasks",
    "users",
)


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        _upgrade_sqlite()
        return

    _lock_postgresql_boundary()
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0064)
    _add_columns_and_constraint()
    op.execute(_postgresql_trigger_function_sql())
    _apply_postgresql_acl()
    op.execute(
        f"CREATE TRIGGER {TRIGGER_NAME} "
        f"BEFORE INSERT OR UPDATE ON public.stocktake_reviews "
        f"FOR EACH ROW EXECUTE FUNCTION {TRIGGER_SIGNATURE}"
    )
    op.execute(
        f"ALTER TABLE public.stocktake_reviews ENABLE ALWAYS TRIGGER {TRIGGER_NAME}"
    )
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0064,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0063,
        old_revision=PREVIOUS_SCHEMA_REVISION,
        new_revision=revision,
    )
    _verify_catalog(installed=True)
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0063)


def downgrade() -> None:
    if _is_offline_mode():
        raise RuntimeError("0063 downgrade requires online catalog and readiness checks")
    dialect = _dialect_name()
    if dialect == "sqlite":
        _require_sqlite_downgrade_safe()
        _downgrade_sqlite()
        return

    _lock_postgresql_boundary()
    _verify_catalog(installed=True)
    _require_postgresql_downgrade_safe()
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0063)
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0063,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0064,
        old_revision=revision,
        new_revision=PREVIOUS_SCHEMA_REVISION,
    )
    op.execute(f"DROP TRIGGER {TRIGGER_NAME} ON public.stocktake_reviews")
    op.execute(f"DROP FUNCTION {TRIGGER_SIGNATURE}")
    _drop_columns_and_constraint()
    _verify_catalog(installed=False)
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0064)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0063 supports only PostgreSQL and SQLite")
    return dialect


def _is_offline_mode() -> bool:
    """Read Alembic's mode without breaking direct SQLite migration tests."""

    try:
        return bool(context.is_offline_mode())
    except NameError:
        # Unit tests invoke ``upgrade``/``downgrade`` with an Operations
        # context but without installing Alembic's global EnvironmentContext
        # proxy.  That is an online, bound connection for our purposes.
        return False


def _lock_postgresql_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{name}" for name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _add_columns_and_constraint() -> None:
    op.add_column(
        "stocktake_reviews",
        sa.Column("expected_task_version", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "stocktake_reviews",
        sa.Column("resulting_task_version", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        CHECK_CONSTRAINT,
        "stocktake_reviews",
        "((expected_task_version IS NULL AND resulting_task_version IS NULL) "
        "OR (expected_task_version IS NOT NULL AND resulting_task_version IS NOT NULL "
        "AND expected_task_version >= 0 AND resulting_task_version >= 0 "
        "AND resulting_task_version = expected_task_version + 1))",
    )


def _drop_columns_and_constraint() -> None:
    op.drop_constraint(CHECK_CONSTRAINT, "stocktake_reviews", type_="check")
    op.drop_column("stocktake_reviews", "resulting_task_version")
    op.drop_column("stocktake_reviews", "expected_task_version")


def _upgrade_sqlite() -> None:
    # Rebuilding this table would temporarily drop the historical 0032/0052
    # triggers that reference it and SQLite rejects the rename.  The local
    # chain therefore uses native ADD COLUMN plus the equivalent trigger
    # predicate; PostgreSQL remains the authoritative CHECK/ACL implementation.
    op.add_column(
        "stocktake_reviews",
        sa.Column("expected_task_version", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "stocktake_reviews",
        sa.Column("resulting_task_version", sa.BigInteger(), nullable=True),
    )
    op.execute(_sqlite_trigger_sql(SQLITE_INSERT_TRIGGER, "INSERT"))
    op.execute(_sqlite_trigger_sql(SQLITE_UPDATE_TRIGGER, "UPDATE OF task_id, expected_task_version, resulting_task_version"))


def _downgrade_sqlite() -> None:
    op.execute(f"DROP TRIGGER {SQLITE_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER {SQLITE_INSERT_TRIGGER}")
    op.drop_column("stocktake_reviews", "resulting_task_version")
    op.drop_column("stocktake_reviews", "expected_task_version")


def _sqlite_trigger_sql(trigger_name: str, event: str) -> str:
    return f"""
CREATE TRIGGER {trigger_name}
BEFORE {event} ON stocktake_reviews
WHEN EXISTS (
    SELECT 1 FROM stocktake_tasks AS task
     WHERE task.id = NEW.task_id
       AND task.task_type IN {NONOPENING_SQL}
       AND (
           NEW.expected_task_version IS NULL
           OR NEW.resulting_task_version IS NULL
           OR NEW.expected_task_version < 0
           OR NEW.resulting_task_version <> NEW.expected_task_version + 1
       )
)
BEGIN
    SELECT RAISE(ABORT, 'non-opening stocktake review version pair is required');
END
"""


def _postgresql_trigger_function_sql() -> str:
    return f"""
CREATE FUNCTION {TRIGGER_SIGNATURE}
RETURNS trigger
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    task_kind text;
BEGIN
    SELECT task.task_type
      INTO task_kind
      FROM public.stocktake_tasks AS task
     WHERE task.id = NEW.task_id
     ORDER BY task.id
     FOR SHARE OF task;
    IF task_kind IS NULL THEN
        RAISE EXCEPTION 'stocktake review task is missing'
            USING ERRCODE = '23503';
    END IF;
    IF task_kind IN {NONOPENING_SQL}
       AND (
           NEW.expected_task_version IS NULL
           OR NEW.resulting_task_version IS NULL
           OR NEW.expected_task_version < 0
           OR NEW.resulting_task_version <> NEW.expected_task_version + 1
       ) THEN
        RAISE EXCEPTION 'non-opening stocktake review version pair is required'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$
"""


def _apply_postgresql_acl() -> None:
    op.execute(f"REVOKE ALL ON FUNCTION {TRIGGER_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"REVOKE ALL ON FUNCTION {TRIGGER_SIGNATURE} FROM {PRODUCTION_API_ROLE}"
    )
    op.execute(f"ALTER FUNCTION {TRIGGER_SIGNATURE} OWNER TO {MIGRATION_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {TRIGGER_SIGNATURE} TO {MIGRATION_ROLE}")


def _verify_catalog(*, installed: bool) -> None:
    if _dialect_name() == "sqlite":
        return
    expected = str(installed).lower()
    op.execute(
        f"""
DO $rsc_0063_catalog$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{TRIGGER_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR migrator_oid IS NULL
       OR (function_oid IS NOT NULL) <> {expected}
       OR ({expected} AND (
           (SELECT pg_catalog.count(*) FROM pg_catalog.pg_attribute AS column_row
             WHERE column_row.attrelid = pg_catalog.to_regclass('public.stocktake_reviews')
               AND column_row.attname IN ('expected_task_version', 'resulting_task_version')
               AND column_row.attnum > 0 AND NOT column_row.attisdropped) <> 2
           OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_attribute AS column_row
                WHERE column_row.attrelid = pg_catalog.to_regclass('public.stocktake_reviews')
                  AND column_row.attname IN ('expected_task_version', 'resulting_task_version')
                  AND column_row.attnum > 0 AND NOT column_row.attisdropped
                  AND column_row.atttypid <> 'int8'::pg_catalog.regtype
           )
       ))
       OR (NOT {expected} AND EXISTS (
           SELECT 1 FROM pg_catalog.pg_attribute AS column_row
            WHERE column_row.attrelid = pg_catalog.to_regclass('public.stocktake_reviews')
              AND column_row.attname IN ('expected_task_version', 'resulting_task_version')
              AND column_row.attnum > 0 AND NOT column_row.attisdropped
       )) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: identity or column mismatch';
    END IF;
    IF ({expected} AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint AS constraint_row
         WHERE constraint_row.conrelid = pg_catalog.to_regclass('public.stocktake_reviews')
           AND constraint_row.conname = '{CHECK_CONSTRAINT}'
           AND constraint_row.contype = 'c'
           AND pg_catalog.lower(pg_catalog.pg_get_constraintdef(constraint_row.oid))
               LIKE '%expected_task_version is null%'
           AND pg_catalog.lower(pg_catalog.pg_get_constraintdef(constraint_row.oid))
               LIKE '%resulting_task_version is null%'
           AND pg_catalog.lower(pg_catalog.pg_get_constraintdef(constraint_row.oid))
               LIKE '%expected_task_version is not null%'
           AND pg_catalog.lower(pg_catalog.pg_get_constraintdef(constraint_row.oid))
               LIKE '%resulting_task_version is not null%'
           AND pg_catalog.lower(pg_catalog.pg_get_constraintdef(constraint_row.oid))
               LIKE '%resulting_task_version = expected_task_version + 1%'
    )) OR (NOT {expected} AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint AS constraint_row
         WHERE constraint_row.conrelid = pg_catalog.to_regclass('public.stocktake_reviews')
           AND constraint_row.conname = '{CHECK_CONSTRAINT}'
    )) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: continuity constraint missing';
    END IF;
    IF {expected} AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = function_row.prolang
          JOIN pg_catalog.pg_namespace AS namespace_row ON namespace_row.oid = function_row.pronamespace
         WHERE function_row.oid = function_oid
           AND namespace_row.nspname = 'public'
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.prorettype = 'trigger'::pg_catalog.regtype
           AND function_row.pronargs = 0
           AND language_row.lanname = 'plpgsql'
           AND function_row.provolatile = 'v'
           AND function_row.prosecdef
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]
           AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(function_row.prosrc, 'UTF8')), 'hex') = '{TRIGGER_BODY_SHA256}'
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: trigger function shape or hash mismatch';
    END IF;
    IF ({expected} AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trigger_row
         WHERE trigger_row.tgrelid = pg_catalog.to_regclass('public.stocktake_reviews')
           AND trigger_row.tgname = '{TRIGGER_NAME}'
           AND trigger_row.tgfoid = function_oid
           AND trigger_row.tgenabled = 'A'
           AND NOT trigger_row.tgisinternal
           AND trigger_row.tgtype = 23
           AND trigger_row.tgnargs = 0
           AND trigger_row.tgqual IS NULL
    )) OR (NOT {expected} AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trigger_row
         WHERE trigger_row.tgrelid = pg_catalog.to_regclass('public.stocktake_reviews')
           AND trigger_row.tgname = '{TRIGGER_NAME}'
           AND NOT trigger_row.tgisinternal
    )) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: trigger shape mismatch';
    END IF;
    IF {expected} AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS function_row
         CROSS JOIN LATERAL pg_catalog.aclexplode(
             COALESCE(function_row.proacl, pg_catalog.acldefault('f', function_row.proowner))
         ) AS acl
        WHERE function_row.oid = function_oid
          AND (acl.privilege_type <> 'EXECUTE'
               OR acl.grantee <> migrator_oid
               OR acl.grantor <> migrator_oid
               OR acl.is_grantable)
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: trigger function ACL mismatch';
    END IF;
END
$rsc_0063_catalog$
"""
    )


def _verify_runtime_ready(expected_hash: str) -> None:
    if expected_hash not in {
        RUNTIME_READY_BODY_SHA256_0064,
        RUNTIME_READY_BODY_SHA256_0063,
    }:
        raise ValueError("unsupported 0063 readiness hash")
    op.execute(
        f"""
DO $rsc_0063_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR function_oid IS NULL OR migrator_oid IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid
              AND function_row.proowner = migrator_oid
              AND function_row.prokind = 'f'
              AND function_row.pronargs = 0
              AND function_row.prorettype = 'boolean'::pg_catalog.regtype
              AND function_row.proretset = false
              AND function_row.proargmodes IS NULL
              AND function_row.pronargdefaults = 0
              AND function_row.provariadic = 0
              AND function_row.proparallel = 'u'
              AND function_row.provolatile = 's'
              AND function_row.prosecdef
              AND NOT function_row.proisstrict
              AND NOT function_row.proleakproof
              AND EXISTS (
                  SELECT 1 FROM pg_catalog.pg_language AS language_row
                   WHERE language_row.oid = function_row.prolang
                     AND language_row.lanname = 'sql'
              )
              AND function_row.proconfig = ARRAY['search_path=pg_catalog']::text[]
       )
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid)
          IS DISTINCT FROM '{expected_hash}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness function identity or hash mismatch';
    END IF;
END
$rsc_0063_readiness$
"""
    )


def _replace_runtime_ready(
    *, expected_hash: str, replacement_hash: str,
    old_revision: str, new_revision: str,
) -> None:
    if (expected_hash, replacement_hash, old_revision, new_revision) not in {
        (
            RUNTIME_READY_BODY_SHA256_0064,
            RUNTIME_READY_BODY_SHA256_0063,
            PREVIOUS_SCHEMA_REVISION,
            revision,
        ),
        (
            RUNTIME_READY_BODY_SHA256_0063,
            RUNTIME_READY_BODY_SHA256_0064,
            revision,
            PREVIOUS_SCHEMA_REVISION,
        ),
    }:
        raise ValueError("unsupported 0063 readiness replacement")
    op.execute(
        f"""
DO $rsc_0063_replace_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    function_source text;
    function_definition text;
BEGIN
    SELECT function_row.prosrc, pg_catalog.pg_get_functiondef(function_row.oid)
      INTO function_source, function_definition
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
             function_row.prosrc, 'UTF8')), 'hex') = '{expected_hash}';
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR function_source IS NULL
       OR (pg_catalog.length(function_source) - pg_catalog.length(
           pg_catalog.replace(function_source, '{old_revision}', ''))) /
           pg_catalog.length('{old_revision}') <> 1
       OR pg_catalog.strpos(function_source, '{new_revision}') <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness source mismatch';
    END IF;
    EXECUTE pg_catalog.replace(function_definition, '{old_revision}', '{new_revision}');
    IF (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               row.prosrc, 'UTF8')), 'hex')
          FROM pg_catalog.pg_proc AS row WHERE row.oid = function_oid)
       IS DISTINCT FROM '{replacement_hash}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness replacement hash mismatch';
    END IF;
END
$rsc_0063_replace_readiness$
"""
    )


def _require_postgresql_downgrade_safe() -> None:
    op.execute(
        f"""
DO $rsc_0063_downgrade$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_reviews AS review
          JOIN public.stocktake_tasks AS task ON task.id = review.task_id
         WHERE task.task_type IN {NONOPENING_SQL}
           AND (review.expected_task_version IS NOT NULL
                OR review.resulting_task_version IS NOT NULL)
    ) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$rsc_0063_downgrade$
"""
    )


def _require_sqlite_downgrade_safe() -> None:
    row = op.get_bind().exec_driver_sql(
        f"""
SELECT 1
  FROM stocktake_reviews AS review
  JOIN stocktake_tasks AS task ON task.id = review.task_id
 WHERE task.task_type IN {NONOPENING_SQL}
   AND (review.expected_task_version IS NOT NULL
        OR review.resulting_task_version IS NOT NULL)
 LIMIT 1
"""
    ).first()
    if row is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)


__all__ = [
    "CHECK_CONSTRAINT",
    "LOCK_TABLES",
    "RUNTIME_READY_BODY_SHA256_0063",
    "RUNTIME_READY_BODY_SHA256_0064",
    "TRIGGER_BODY_SHA256",
    "TRIGGER_FUNCTION",
    "TRIGGER_NAME",
    "TRIGGER_SIGNATURE",
    "_postgresql_trigger_function_sql",
]
