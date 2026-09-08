"""Immutable shipment package facts bound to 0072 outbound postings.

A shipment records carrier handover metadata only. It never moves inventory;
receipts and personal inbound are subsequent facts.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260913_0073"
down_revision = "20260912_0072"
branch_labels = depends_on = None
TABLES = ("shipments", "shipment_lines", "shipment_serials")


def _immutable_trigger(table, dialect):
    name = f"trg_{table}_immutable_0073"
    if dialect == "sqlite":
        op.execute(f"CREATE TRIGGER {name}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '0073 shipment facts are append-only'); END")
        op.execute(f"CREATE TRIGGER {name}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '0073 shipment facts are append-only'); END")
        return
    op.execute(f"""CREATE OR REPLACE FUNCTION public.rsc_guard_{table}_immutable_0073() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN RAISE EXCEPTION '0073 shipment facts are append-only' USING ERRCODE='55000'; END $$""")
    op.execute(f"REVOKE ALL ON FUNCTION public.rsc_guard_{table}_immutable_0073() FROM PUBLIC, star_oam_api")
    op.execute(f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_{table}_immutable_0073()")
    op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")


def upgrade():
    dialect = op.get_bind().dialect.name
    op.create_table("shipments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("shipment_no", sa.String(100), nullable=False),
        sa.Column("source_location_id", sa.Uuid(), nullable=False),
        sa.Column("target_location_id", sa.Uuid(), nullable=False),
        sa.Column("target_person_id", sa.Uuid(), nullable=True),
        sa.Column("carrier", sa.String(100), nullable=False),
        sa.Column("tracking_no", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("actor_user_id", sa.String(36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending_handover','shipped','in_transit','exception')", name="ck_shipments_status"),
        sa.CheckConstraint("source_location_id <> target_location_id", name="ck_shipments_locations"),
        sa.UniqueConstraint("shipment_no", name="uq_shipments_number"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_shipments_key"),
    )
    op.create_table("shipment_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("outbound_posting_id", sa.Uuid(), sa.ForeignKey("outbound_postings.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("outbound_line_id", sa.Uuid(), sa.ForeignKey("outbound_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shipped_qty", sa.Numeric(18,3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("shipment_id", "outbound_posting_id", name="uq_shipment_lines_posting"),
        sa.CheckConstraint("shipped_qty > 0", name="ck_shipment_lines_qty"),
    )
    op.create_table("shipment_serials",
        sa.Column("shipment_line_id", sa.Uuid(), sa.ForeignKey("shipment_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("shipment_line_id", "serial_id"),
        sa.UniqueConstraint("serial_id", name="uq_shipment_serial_once"),
    )
    for table in TABLES:
        _immutable_trigger(table, dialect)
    if dialect == "postgresql":
        op.execute("GRANT SELECT, INSERT ON TABLE public.shipments, public.shipment_lines, public.shipment_serials TO star_oam_api")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        if any(bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})" )).scalar() for t in TABLES):
            raise RuntimeError("cannot downgrade 0073 while shipment facts exist")
        for table in TABLES:
            op.execute(f"DROP TRIGGER trg_{table}_immutable_0073 ON public.{table}")
            op.execute(f"DROP FUNCTION public.rsc_guard_{table}_immutable_0073()")
    for table in reversed(TABLES):
        op.drop_table(table)
