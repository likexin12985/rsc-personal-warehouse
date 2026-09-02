"""Add the exact material-request work-order owner-lock boundary.

Revision ID: 20260902_0042
Revises: 20260902_0041
Create Date: 2026-09-02

The production API deliberately keeps the OAM work-order projection
SELECT-only. PostgreSQL therefore requires a migration-owned SECURITY DEFINER
entrypoint to take the exact ``FOR SHARE`` row lock used while a material
request command revalidates status, requester, region and provenance. The
function returns no business data and grants only EXECUTE to the API role.
SQLite retains its single-process test semantics and has no equivalent
function or concurrency claim.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260902_0042"
down_revision: Union[str, Sequence[str], None] = "20260902_0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
PG_FUNCTION = "rsc_lock_material_request_work_order_reference_0042"
PG_FUNCTION_SIGNATURE = f"public.{PG_FUNCTION}(uuid)"
LOCK_ERROR = "material request work-order lock invariant violated"


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0042 supports only PostgreSQL production and SQLite local tests"
        )
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    _require_postgresql_roles()
    _create_postgresql_function()
    _apply_postgresql_acl()


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return
    op.execute(f"DROP FUNCTION {PG_FUNCTION_SIGNATURE}")


def _require_postgresql_roles() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{MIGRATION_ROLE}'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0042 requires provisioned migration and API database roles';
    END IF;
END
$$
"""
    )


def _create_postgresql_function() -> None:
    op.execute(
        f"""
CREATE FUNCTION public.{PG_FUNCTION}(requested_work_order_id uuid)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    locked_count bigint;
BEGIN
    IF requested_work_order_id IS NULL THEN
        RAISE EXCEPTION '{LOCK_ERROR}';
    END IF;

    PERFORM work_order.id
      FROM public.oam_work_orders AS work_order
     WHERE work_order.id = requested_work_order_id
     ORDER BY work_order.id
     FOR SHARE OF work_order;
    GET DIAGNOSTICS locked_count = ROW_COUNT;
    IF locked_count <> 1 THEN
        RAISE EXCEPTION '{LOCK_ERROR}';
    END IF;
END
$$
"""
    )
    op.execute(
        f"ALTER FUNCTION {PG_FUNCTION_SIGNATURE} OWNER TO {MIGRATION_ROLE}"
    )


def _apply_postgresql_acl() -> None:
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
