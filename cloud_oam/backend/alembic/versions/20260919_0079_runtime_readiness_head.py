"""Advance the OAM runtime readiness marker to the current migration head."""

from pathlib import Path
import runpy

from alembic import op


revision = "20260919_0079"
down_revision = "20260918_0078"
branch_labels = depends_on = None

RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
# 0073-0078 left this function at 0072. Restore that exact source on downgrade
# so the historical 0072-to-0071 downgrade can still verify its pinned hash.
RUNTIME_READY_SCHEMA_REVISION_0078 = "20260912_0072"
RUNTIME_READY_BODY_SHA256_0072 = (
    "7bc4ca43bc203431db82c14473ec0d177831ff31ccf9c4711b142a74e6a1cd43"
)
RUNTIME_READY_BODY_SHA256_0079 = (
    "9dbb698d8c5f6f01883c8692a8a1e87204137a326948d840f3a191d26f40e3c0"
)


def _runtime_migration():
    return runpy.run_path(
        str(Path(__file__).with_name("20260912_0072_outbound_postings.py"))
    )


def _replace_readiness(*, upgrade: bool) -> None:
    migration = _runtime_migration()
    replace = (
        migration["_previous"]()["_previous"]()["_previous"]()[
            "_replace_function_source"
        ]
    )
    old_revision, new_revision = (
        (RUNTIME_READY_SCHEMA_REVISION_0078, revision)
        if upgrade
        else (revision, RUNTIME_READY_SCHEMA_REVISION_0078)
    )
    expected_hash, replacement_hash = (
        (RUNTIME_READY_BODY_SHA256_0072, RUNTIME_READY_BODY_SHA256_0079)
        if upgrade
        else (RUNTIME_READY_BODY_SHA256_0079, RUNTIME_READY_BODY_SHA256_0072)
    )
    replace(
        signature=RUNTIME_READY_SIGNATURE,
        expected_hash=expected_hash,
        replacement_hash=replacement_hash,
        replacements=((old_revision, new_revision),),
        label="runtime_readiness_0079",
    )


def _lock_boundary() -> None:
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")


def _verify_runtime_ready(expected_hash: str) -> None:
    if expected_hash not in {
        RUNTIME_READY_BODY_SHA256_0072,
        RUNTIME_READY_BODY_SHA256_0079,
    }:
        raise ValueError("unsupported 0079 readiness hash")
    op.execute(
        f"""
DO $rsc_0079_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('star_oam_migrator');
BEGIN
    IF current_user <> 'star_oam_migrator' OR session_user <> 'star_oam_migrator'
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
        RAISE EXCEPTION '0079 readiness function identity or hash mismatch';
    END IF;
END
$rsc_0079_readiness$
"""
    )


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0079 supports only PostgreSQL and SQLite")
    _lock_boundary()
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0072)
    _replace_readiness(upgrade=True)
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0079)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        return
    if dialect != "postgresql":
        raise RuntimeError("0079 supports only PostgreSQL and SQLite")
    _lock_boundary()
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0079)
    _replace_readiness(upgrade=False)
    _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0072)
