"""Pin KMS encrypted application-key identities in the database.

Revision ID: 20260901_0040
Revises: 20260901_0039
Create Date: 2026-09-01

The table stores only non-secret KMS coordinates and a SHA-256 fingerprint of
``CiphertextBlob``.  It never stores the blob or plaintext data key.  Rows are
provisioned explicitly by the migration role after two-person review; the API
can only read them.  Existing coordinates are immutable so one application key
version can never be rebound to different AES material across deployments.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260901_0040"
down_revision: Union[str, Sequence[str], None] = "20260901_0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "kms_data_key_pins"
PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
BACKUP_ROLE = "star_oam_backup"
EDGE_ROLE = "star_oam_edge"
PG_IMMUTABLE_FUNCTION = "rsc_reject_kms_data_key_pin_mutation_0040"
PG_MUTATION_TRIGGER = "trg_kms_data_key_pins_immutable_0040"
PG_TRUNCATE_TRIGGER = "trg_kms_data_key_pins_no_truncate_0040"
SQLITE_UPDATE_TRIGGER = "trg_kms_data_key_pins_immutable_update_0040"
SQLITE_DELETE_TRIGGER = "trg_kms_data_key_pins_immutable_delete_0040"
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0040 while KMS data-key pins or encrypted references exist"
)


def _lower_hex_remainder(expression: str) -> str:
    for character in "0123456789abcdef":
        expression = f"replace({expression}, '{character}', '')"
    return expression


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0040 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    op.create_table(
        TABLE_NAME,
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("kms_key_id", sa.String(length=256), nullable=False),
        sa.Column("application_key_version", sa.Integer(), nullable=False),
        sa.Column("kms_key_version_id", sa.String(length=128), nullable=False),
        sa.Column("ciphertext_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('authentication_idempotency', "
            "'material_request_contact')",
            name="ck_kms_data_key_pins_purpose_0040",
        ),
        sa.CheckConstraint(
            "application_key_version BETWEEN 1 AND 2147483647",
            name="ck_kms_data_key_pins_version_0040",
        ),
        sa.CheckConstraint(
            "kms_key_id = trim(kms_key_id) AND "
            "kms_key_version_id = trim(kms_key_version_id) AND "
            "length(kms_key_id) BETWEEN 3 AND 256 AND "
            "length(kms_key_version_id) BETWEEN 8 AND 128",
            name="ck_kms_data_key_pins_coordinates_0040",
        ),
        sa.CheckConstraint(
            "length(ciphertext_sha256) = 64 AND "
            f"length({_lower_hex_remainder('ciphertext_sha256')}) = 0",
            name="ck_kms_data_key_pins_sha256_0040",
        ),
        sa.PrimaryKeyConstraint(
            "purpose",
            "kms_key_id",
            "application_key_version",
            name="pk_kms_data_key_pins_coordinate_0040",
        ),
        sa.UniqueConstraint(
            "ciphertext_sha256",
            name="uq_kms_data_key_pins_ciphertext_0040",
        ),
        sa.UniqueConstraint(
            "purpose",
            "application_key_version",
            name="uq_kms_data_key_pins_purpose_version_0040",
        ),
    )
    if dialect == "postgresql":
        op.execute(_postgresql_immutable_function_sql())
        op.execute(
            f"CREATE TRIGGER {PG_MUTATION_TRIGGER} "
            f"BEFORE UPDATE OR DELETE ON public.{TABLE_NAME} FOR EACH ROW "
            f"EXECUTE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER {PG_TRUNCATE_TRIGGER} "
            f"BEFORE TRUNCATE ON public.{TABLE_NAME} FOR EACH STATEMENT "
            f"EXECUTE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()"
        )
        op.execute(
            f"ALTER TABLE public.{TABLE_NAME} ENABLE ALWAYS TRIGGER "
            f"{PG_MUTATION_TRIGGER}"
        )
        op.execute(
            f"ALTER TABLE public.{TABLE_NAME} ENABLE ALWAYS TRIGGER "
            f"{PG_TRUNCATE_TRIGGER}"
        )
        _apply_postgresql_acl()
        return
    _create_sqlite_immutable_triggers()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("0040 downgrade requires an online evidence check")
    dialect = _dialect_name()
    _lock_downgrade_evidence(dialect)
    if op.get_bind().exec_driver_sql(
        _downgrade_blocker_sql(dialect)
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)
    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_MUTATION_TRIGGER} "
            f"ON public.{TABLE_NAME}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS {PG_TRUNCATE_TRIGGER} "
            f"ON public.{TABLE_NAME}"
        )
        op.execute(
            f"DROP FUNCTION IF EXISTS public.{PG_IMMUTABLE_FUNCTION}()"
        )
        _revoke_postgresql_acl()
    else:
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}")
        op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}")
    op.drop_table(TABLE_NAME)


def _postgresql_immutable_function_sql() -> str:
    return f"""
CREATE FUNCTION public.{PG_IMMUTABLE_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = '55000',
        MESSAGE = 'KMS data-key pins are immutable';
END
$$
"""


def _lock_downgrade_evidence(dialect: str) -> None:
    """Serialize the blocker proof with every table that can add a reference."""

    connection = op.get_bind()
    if dialect == "postgresql":
        connection.exec_driver_sql(
            "LOCK TABLE public.auth_idempotency_operations, "
            "public.kms_data_key_pins, public.material_request_revisions, "
            "public.material_requests IN ACCESS EXCLUSIVE MODE"
        )
        return
    # Alembic already owns a transaction.  A no-op DML statement upgrades the
    # deferred SQLite transaction to a database-wide writer lock without
    # firing the per-row immutable trigger.
    connection.exec_driver_sql(
        f"UPDATE {TABLE_NAME} SET purpose = purpose WHERE 0"
    )


def _downgrade_blocker_sql(dialect: str) -> str:
    prefix = "public." if dialect == "postgresql" else ""
    return f"""
SELECT 1
WHERE EXISTS (SELECT 1 FROM {prefix}{TABLE_NAME})
   OR EXISTS (
       SELECT 1
         FROM {prefix}auth_idempotency_operations
        WHERE response_ciphertext IS NOT NULL
           OR response_nonce IS NOT NULL
           OR response_sha256 IS NOT NULL
           OR encryption_key_version IS NOT NULL
   )
   OR EXISTS (SELECT 1 FROM {prefix}material_requests)
   OR EXISTS (SELECT 1 FROM {prefix}material_request_revisions)
LIMIT 1
"""


def _create_sqlite_immutable_triggers() -> None:
    op.execute(
        f"CREATE TRIGGER {SQLITE_UPDATE_TRIGGER} BEFORE UPDATE ON {TABLE_NAME} "
        "FOR EACH ROW BEGIN SELECT RAISE(ABORT, "
        "'KMS data-key pins are immutable'); END"
    )
    op.execute(
        f"CREATE TRIGGER {SQLITE_DELETE_TRIGGER} BEFORE DELETE ON {TABLE_NAME} "
        "FOR EACH ROW BEGIN SELECT RAISE(ABORT, "
        "'KMS data-key pins are immutable'); END"
    )


def _apply_postgresql_acl() -> None:
    function_signature = f"public.{PG_IMMUTABLE_FUNCTION}()"
    op.execute(f"REVOKE ALL ON TABLE public.{TABLE_NAME} FROM PUBLIC")
    op.execute(f"REVOKE EXECUTE ON FUNCTION {function_signature} FROM PUBLIC")
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{MIGRATION_ROLE}') THEN
        EXECUTE 'ALTER TABLE public.{TABLE_NAME} OWNER TO {MIGRATION_ROLE}';
        EXECUTE 'ALTER FUNCTION {function_signature} OWNER TO {MIGRATION_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {PRODUCTION_API_ROLE}';
        EXECUTE 'GRANT SELECT ON TABLE public.{TABLE_NAME} TO {PRODUCTION_API_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {function_signature} FROM {PRODUCTION_API_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BACKUP_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {BACKUP_ROLE}';
        EXECUTE 'GRANT SELECT ON TABLE public.{TABLE_NAME} TO {BACKUP_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {function_signature} FROM {BACKUP_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{EDGE_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {EDGE_ROLE}';
        EXECUTE 'REVOKE EXECUTE ON FUNCTION {function_signature} FROM {EDGE_ROLE}';
    END IF;
END
$$
"""
    )


def _revoke_postgresql_acl() -> None:
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {PRODUCTION_API_ROLE}';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{BACKUP_ROLE}') THEN
        EXECUTE 'REVOKE ALL ON TABLE public.{TABLE_NAME} FROM {BACKUP_ROLE}';
    END IF;
END
$$
"""
    )
