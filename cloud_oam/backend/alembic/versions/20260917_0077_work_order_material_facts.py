"""Add immutable formal work-order material fact tables."""
from alembic import op
import sqlalchemy as sa

revision = "20260917_0077"
down_revision = "20260916_0076"
branch_labels = depends_on = None
TABLES = ("work_order_material_operations", "work_order_material_lines", "work_order_material_serials", "work_order_replacement_pairs")

def _immutable(table: str, dialect: str) -> None:
    name = f"trg_{table}_immutable_0077"
    if dialect == "sqlite":
        op.execute(f"CREATE TRIGGER {name}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '0077 facts are append-only'); END")
        op.execute(f"CREATE TRIGGER {name}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '0077 facts are append-only'); END")
    else:
        op.execute(f"""CREATE OR REPLACE FUNCTION public.rsc_guard_{table}_immutable_0077() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ BEGIN RAISE EXCEPTION '0077 facts are append-only' USING ERRCODE='55000'; END $$""")
        op.execute(f"REVOKE ALL ON FUNCTION public.rsc_guard_{table}_immutable_0077() FROM PUBLIC, star_oam_api")
        op.execute(f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_{table}_immutable_0077()")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")

def upgrade():
    d = op.get_bind().dialect.name
    op.create_table("work_order_material_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_no", sa.String(100), nullable=False),
        sa.Column("oam_work_order_id", sa.Uuid(), sa.ForeignKey("oam_work_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_type", sa.String(24), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("posting_transaction_id", sa.Uuid(), sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT")),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("operation_type IN ('occupy','release','consume','recover','reverse')", name="ck_work_order_material_operations_type"),
        sa.CheckConstraint("status IN ('posted','cancelled','reversed')", name="ck_work_order_material_operations_status"),
        sa.UniqueConstraint("operation_no", name="uq_work_order_material_operations_no"), sa.UniqueConstraint("idempotency_key_hash", name="uq_work_order_material_operations_key"),
    )
    op.create_index("ix_work_order_material_operations_work_order", "work_order_material_operations", ["oam_work_order_id", "created_at"])
    op.create_table("work_order_material_lines",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("work_order_material_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("line_no", sa.BigInteger(), nullable=False), sa.Column("material_id", sa.Uuid(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("stock_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False), sa.Column("quantity", sa.Numeric(18, 3), nullable=False),
        sa.Column("condition_before", sa.String(24), nullable=False), sa.Column("condition_after", sa.String(24)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_work_order_material_lines_positive"), sa.UniqueConstraint("operation_id", "line_no", name="uq_work_order_material_lines_no"),
    )
    op.create_index("ix_work_order_material_lines_material", "work_order_material_lines", ["material_id", "stock_account_id"])
    op.create_table("work_order_material_serials",
        sa.Column("operation_line_id", sa.Uuid(), sa.ForeignKey("work_order_material_lines.id", ondelete="RESTRICT"), nullable=False), sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("sku_verified", sa.Boolean(), nullable=False), sa.Column("qr_verified", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("operation_line_id", "serial_id", name="pk_work_order_material_serials"),
    )
    op.create_table("work_order_replacement_pairs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("operation_id", sa.Uuid(), sa.ForeignKey("work_order_material_operations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("installed_serial_id", sa.Uuid(), nullable=False), sa.Column("removed_serial_id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("operation_id", "installed_serial_id", name="uq_work_order_replacement_installed"), sa.UniqueConstraint("operation_id", "removed_serial_id", name="uq_work_order_replacement_removed"),
    )
    for table in TABLES: _immutable(table, d)
    if d == "postgresql": op.execute("GRANT SELECT, INSERT ON TABLE public.work_order_material_operations, public.work_order_material_lines, public.work_order_material_serials, public.work_order_replacement_pairs TO star_oam_api")

def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT EXISTS (SELECT 1 FROM work_order_material_operations) OR EXISTS (SELECT 1 FROM work_order_material_lines) OR EXISTS (SELECT 1 FROM work_order_material_serials) OR EXISTS (SELECT 1 FROM work_order_replacement_pairs)")).scalar():
        raise RuntimeError("cannot downgrade 0077 while work-order material facts exist")
    for table in reversed(TABLES):
        op.drop_table(table)
