"""Add a migration-owned lock for the non-opening stocktake finalizer.

Revision ID: 20260906_0064
Revises: 20260905_0062

Revision 0063 remains reserved for the frozen review-command-status design.
This forward-only capability is deliberately independent: the API role gets
EXECUTE only and never receives UPDATE on ``organizations``.
"""

from __future__ import annotations

from alembic import context, op


revision: str = "20260906_0064"
down_revision: str | None = "20260905_0062"
branch_labels: str | None = None
depends_on: str | None = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
LOCK_FUNCTION = "rsc_lock_stocktake_finalizer_organization_0064"
LOCK_SIGNATURE = f"public.{LOCK_FUNCTION}(uuid)"
LOCK_BODY_SHA256 = "e97ad36d80cbeafa5ea97290b8ecd131213c2f2f965178f77f99977b12051502"
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_BODY_SHA256_0062 = (
    "d019a572ad39a3570ca175479ee50a4a7987eff3a0655c5c910c5b83744ea04d"
)
RUNTIME_READY_BODY_SHA256_0064 = (
    "b75bb3c37c279a2a406a36be9049a187e9cef61dfd0892a48a2db0e8aef551c0"
)
CATALOG_ERROR = "0064 stocktake finalizer organization catalog mismatch"

LOCK_TABLES = ("alembic_version", "organizations")


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _lock_postgresql_boundary()
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0062)
    op.execute(_postgresql_lock_function_sql())
    op.execute(f"REVOKE ALL ON FUNCTION {LOCK_SIGNATURE} FROM PUBLIC")
    op.execute(f"ALTER FUNCTION {LOCK_SIGNATURE} OWNER TO {MIGRATION_ROLE}")
    op.execute(f"GRANT EXECUTE ON FUNCTION {LOCK_SIGNATURE} TO {PRODUCTION_API_ROLE}")
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0062,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0064,
        old_revision=down_revision,
        new_revision=revision,
    )
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0064)
    _verify_lock_function(installed=True)


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0064 downgrade requires online catalog checks")
    if _dialect_name() == "sqlite":
        return
    _lock_postgresql_boundary()
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0064)
    _verify_lock_function(installed=True)
    _replace_runtime_ready(
        expected_hash=RUNTIME_READY_BODY_SHA256_0064,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0062,
        old_revision=revision,
        new_revision=down_revision,
    )
    op.execute(f"DROP FUNCTION {LOCK_SIGNATURE}")
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0062)
    _verify_lock_function(installed=False)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0064 supports only PostgreSQL and SQLite")
    return dialect


def _lock_postgresql_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _postgresql_lock_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{LOCK_FUNCTION}(requested_organization_id uuid)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    locked_count bigint := 0;
BEGIN
    IF requested_organization_id IS NULL THEN
        RAISE EXCEPTION 'stocktake finalizer organization lock requires an organization id';
    END IF;

    -- The row lock is held by the caller's transaction until its completion
    -- seal commits or rolls back.  The API role only has EXECUTE on this
    -- migration-owned SECURITY DEFINER function, never UPDATE on the table.
    PERFORM organizations.id
      FROM public.organizations
     WHERE organizations.id = requested_organization_id
       AND organizations.org_type = 'headquarters'
       AND organizations.status = 'active'
     FOR SHARE OF organizations;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION 'stocktake finalizer organization is not an active headquarters';
    END IF;
END
$$
"""


def _verify_runtime_ready(expected_hash: str) -> None:
    if expected_hash not in {
        RUNTIME_READY_BODY_SHA256_0062,
        RUNTIME_READY_BODY_SHA256_0064,
    }:
        raise ValueError("unsupported 0064 readiness hash")
    op.execute(f"""
DO $rsc_0064_readiness$
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
$rsc_0064_readiness$
""")


def _replace_runtime_ready(*, expected_hash: str, replacement_hash: str,
                           old_revision: str, new_revision: str) -> None:
    op.execute(f"""
DO $rsc_0064_replace_readiness$
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
$rsc_0064_replace_readiness$
""")


def _verify_lock_function(*, installed: bool) -> None:
    op.execute(f"""
DO $rsc_0064_function$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{LOCK_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
BEGIN
    IF (function_oid IS NOT NULL) <> {str(installed).lower()}
       OR migrator_oid IS NULL OR api_oid IS NULL THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: function identity mismatch';
    END IF;
    IF {str(installed).lower()} AND NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
          JOIN pg_catalog.pg_language AS language_row ON language_row.oid = row.prolang
          JOIN pg_catalog.pg_namespace AS namespace_row ON namespace_row.oid = row.pronamespace
         WHERE row.oid = function_oid AND namespace_row.nspname = 'public'
           AND row.proowner = migrator_oid AND row.prokind = 'f'
           AND row.prorettype = 'void'::pg_catalog.regtype
           AND row.pronargs = 1 AND pg_catalog.oidvectortypes(row.proargtypes) = 'uuid'
           AND row.proargnames = ARRAY['requested_organization_id']::text[]
           AND language_row.lanname = 'plpgsql' AND row.provolatile = 'v'
           AND row.prosecdef AND NOT row.proisstrict AND NOT row.proleakproof
           AND row.proparallel = 'u'
           AND row.proconfig = ARRAY['search_path=pg_catalog, public']::text[]
           AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(row.prosrc, 'UTF8')), 'hex') = '{LOCK_BODY_SHA256}'
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: function shape or body mismatch';
    END IF;
    IF {str(installed).lower()} AND EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS row
         CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(row.proacl,
             pg_catalog.acldefault('f', row.proowner))) AS acl
         WHERE row.oid = function_oid
           AND (acl.privilege_type <> 'EXECUTE' OR acl.grantor <> migrator_oid
                OR acl.grantee NOT IN (migrator_oid, api_oid) OR acl.is_grantable)
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: exact function ACL mismatch';
    END IF;
    IF {str(installed).lower()} AND (
        (SELECT pg_catalog.count(*) FROM pg_catalog.pg_proc AS row
          CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(row.proacl,
              pg_catalog.acldefault('f', row.proowner))) AS acl
         WHERE row.oid = function_oid
           AND acl.privilege_type = 'EXECUTE'
           AND acl.grantor = migrator_oid
           AND acl.grantee IN (migrator_oid, api_oid)
           AND NOT acl.is_grantable) <> 2
        OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc AS row
              CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(row.proacl,
                  pg_catalog.acldefault('f', row.proowner))) AS acl
             WHERE row.oid = function_oid AND acl.grantee = migrator_oid
               AND acl.grantor = migrator_oid
               AND acl.privilege_type = 'EXECUTE' AND NOT acl.is_grantable
        )
        OR NOT EXISTS (
            SELECT 1 FROM pg_catalog.pg_proc AS row
              CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(row.proacl,
                  pg_catalog.acldefault('f', row.proowner))) AS acl
             WHERE row.oid = function_oid AND acl.grantee = api_oid
               AND acl.grantor = migrator_oid
               AND acl.privilege_type = 'EXECUTE' AND NOT acl.is_grantable
        )
    ) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: required function ACL grant missing';
    END IF;
END
$rsc_0064_function$
""")
