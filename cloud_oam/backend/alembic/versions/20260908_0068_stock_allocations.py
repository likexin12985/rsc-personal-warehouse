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


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0068 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        op.execute(
            "LOCK TABLE public.material_requests, public.material_request_lines, "
            "public.stock_accounts, public.inventory_serials IN SHARE MODE"
        )
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


def downgrade() -> None:
    dialect = _dialect_name()
    if not context.is_offline_mode():
        bind = op.get_bind()
        if any(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})" )).scalar() for table in TABLES):
            raise RuntimeError("cannot downgrade 0068 while allocation facts exist")
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.stock_allocation_serials, public.stock_allocations IN ACCESS EXCLUSIVE MODE")
    op.drop_table("stock_allocation_serials")
    op.drop_index("ix_stock_allocations_source_account", table_name="stock_allocations")
    op.drop_index("ix_stock_allocations_request_line", table_name="stock_allocations")
    op.drop_table("stock_allocations")
