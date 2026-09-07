"""Create the immutable source-allocation facts.

Revision 0068 deliberately stops before reservation or inventory movement.
Each row binds one approved request line to one source stock account and the
projection versions used when the allocation was decided.  The runtime API
may append and read these facts; it cannot update or delete them.
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260908_0068"
down_revision: str | None = "20260907_0067"
branch_labels: str | None = None
depends_on: str | None = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
TABLES = ("stock_allocations", "stock_allocation_serials")
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"
RUNTIME_READY_PREVIOUS_REVISION = "20260906_0063"
RUNTIME_READY_BODY_SHA256_0063 = (
    "dd43dabe3a816b44b888b4cda1fdcbc84eeefef8b659323b031fb80a55c82cc4"
)
# This is the SHA256 of pg_proc.prosrc after the single, exact revision
# replacement 20260906_0063 -> 20260908_0068.  Keep it beside the catalog
# verifier so a readiness edit cannot silently drift in production.
RUNTIME_READY_BODY_SHA256_0068 = (
    "b248454938a77c33684cc39652d8d709abfb3408d4a96d19036010fb0730292f"
)
CATALOG_ERROR = "0068 stock allocation catalog mismatch"


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0068 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        _lock_postgresql_upgrade_boundary()
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0063)
    op.create_table(
        "stock_allocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("allocation_no", sa.String(length=100), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("request_version", sa.BigInteger(), nullable=False),
        sa.Column("source_stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("allocated_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("source_balance_version", sa.BigInteger(), nullable=False),
        sa.Column("source_ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="allocated", nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["request_id"], ["material_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_line_id"], ["material_request_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_stock_account_id"], ["stock_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_person_id"], ["people.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("allocation_no", name="uq_stock_allocations_number"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_stock_allocations_idempotency"),
        sa.CheckConstraint("allocated_qty > 0", name="ck_stock_allocations_quantity"),
        sa.CheckConstraint("request_version >= 0", name="ck_stock_allocations_request_version"),
        sa.CheckConstraint("revision_no > 0", name="ck_stock_allocations_revision"),
        sa.CheckConstraint("source_balance_version >= 0", name="ck_stock_allocations_balance_version"),
        sa.CheckConstraint("source_ledger_cursor >= 0", name="ck_stock_allocations_ledger_cursor"),
        sa.CheckConstraint("authorization_version > 0", name="ck_stock_allocations_authorization_version"),
        sa.CheckConstraint("status = 'allocated'", name="ck_stock_allocations_status"),
        sa.CheckConstraint("length(idempotency_key_hash) = 64 AND length(request_hash) = 64", name="ck_stock_allocations_hashes"),
    )
    op.create_index("ix_stock_allocations_request_line", "stock_allocations", ["request_line_id", "status"])
    op.create_index("ix_stock_allocations_source_account", "stock_allocations", ["source_stock_account_id", "status"])
    op.create_table(
        "stock_allocation_serials",
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["allocation_id"], ["stock_allocations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["serial_id"], ["inventory_serials.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("allocation_id", "serial_id", name="pk_stock_allocation_serials"),
        sa.UniqueConstraint("serial_id", name="uq_stock_allocation_serials_serial"),
    )
    if dialect == "postgresql":
        for table in TABLES:
            op.execute(f"ALTER TABLE public.{table} OWNER TO {MIGRATION_ROLE}")
            op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
            op.execute(f"GRANT SELECT, INSERT ON TABLE public.{table} TO {PRODUCTION_API_ROLE}")
        op.execute(
            f"GRANT UPDATE (allocation_status) ON TABLE public.material_requests TO {PRODUCTION_API_ROLE}"
        )
        _replace_runtime_ready(
            expected_hash=RUNTIME_READY_BODY_SHA256_0063,
            replacement_hash=RUNTIME_READY_BODY_SHA256_0068,
            old_revision=RUNTIME_READY_PREVIOUS_REVISION,
            new_revision=revision,
        )
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0068)


def downgrade() -> None:
    dialect = _dialect_name()
    if not context.is_offline_mode():
        if dialect == "postgresql":
            _lock_postgresql_downgrade_boundary()
        bind = op.get_bind()
        if any(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})" )).scalar() for table in TABLES):
            raise RuntimeError("cannot downgrade 0068 while allocation facts exist")
    if dialect == "postgresql":
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0068)
        _replace_runtime_ready(
            expected_hash=RUNTIME_READY_BODY_SHA256_0068,
            replacement_hash=RUNTIME_READY_BODY_SHA256_0063,
            old_revision=revision,
            new_revision=RUNTIME_READY_PREVIOUS_REVISION,
        )
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0063)
        op.execute(
            f"REVOKE UPDATE (allocation_status) ON TABLE public.material_requests FROM {PRODUCTION_API_ROLE}"
        )
    op.drop_table("stock_allocation_serials")
    op.drop_index("ix_stock_allocations_source_account", table_name="stock_allocations")
    op.drop_index("ix_stock_allocations_request_line", table_name="stock_allocations")
    op.drop_table("stock_allocations")


def _lock_postgresql_upgrade_boundary() -> None:
    op.execute(
        "LOCK TABLE public.alembic_version, public.material_requests, "
        "public.material_request_lines, public.stock_accounts, "
        "public.inventory_serials IN ACCESS EXCLUSIVE MODE"
    )


def _lock_postgresql_downgrade_boundary() -> None:
    op.execute(
        "LOCK TABLE public.alembic_version, public.stock_allocation_serials, "
        "public.stock_allocations IN ACCESS EXCLUSIVE MODE"
    )


def _verify_runtime_ready(expected_hash: str) -> None:
    if expected_hash not in {
        RUNTIME_READY_BODY_SHA256_0063,
        RUNTIME_READY_BODY_SHA256_0068,
    }:
        raise ValueError("unsupported 0068 readiness hash")
    op.execute(
        f"""
DO $rsc_0068_readiness$
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
$rsc_0068_readiness$
"""
    )


def _replace_runtime_ready(
    *, expected_hash: str, replacement_hash: str,
    old_revision: str, new_revision: str,
) -> None:
    if (expected_hash, replacement_hash, old_revision, new_revision) not in {
        (
            RUNTIME_READY_BODY_SHA256_0063,
            RUNTIME_READY_BODY_SHA256_0068,
            RUNTIME_READY_PREVIOUS_REVISION,
            revision,
        ),
        (
            RUNTIME_READY_BODY_SHA256_0068,
            RUNTIME_READY_BODY_SHA256_0063,
            revision,
            RUNTIME_READY_PREVIOUS_REVISION,
        ),
    }:
        raise ValueError("unsupported 0068 readiness replacement")
    op.execute(
        f"""
DO $rsc_0068_replace_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    original_owner oid;
    original_acl aclitem[];
    original_security boolean;
    original_config text[];
    function_source text;
    function_definition text;
BEGIN
    SELECT function_row.proowner, function_row.proacl, function_row.prosecdef,
           function_row.proconfig, function_row.prosrc,
           pg_catalog.pg_get_functiondef(function_row.oid)
      INTO original_owner, original_acl, original_security, original_config,
           function_source, function_definition
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
    IF pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}') IS DISTINCT FROM function_oid
       OR (SELECT function_row.proowner IS DISTINCT FROM original_owner
                  OR function_row.proacl IS DISTINCT FROM original_acl
                  OR function_row.prosecdef IS DISTINCT FROM original_security
                  OR function_row.proconfig IS DISTINCT FROM original_config
                  OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                         function_row.prosrc, 'UTF8')), 'hex')
                     IS DISTINCT FROM '{replacement_hash}'
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness replacement drift';
    END IF;
END
$rsc_0068_replace_readiness$
"""
    )


__all__ = [
    "CATALOG_ERROR",
    "RUNTIME_READY_BODY_SHA256_0063",
    "RUNTIME_READY_BODY_SHA256_0068",
    "RUNTIME_READY_PREVIOUS_REVISION",
    "RUNTIME_READY_SIGNATURE",
    "TABLES",
]
