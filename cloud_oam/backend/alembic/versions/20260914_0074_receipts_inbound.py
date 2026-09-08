"""Append-only logistics, receipt, and personal-inbound facts after shipment."""
from alembic import op
import sqlalchemy as sa

revision = "20260914_0074"
down_revision = "20260913_0073"
branch_labels = depends_on = None
TABLES = ("logistics_events", "receipts", "receipt_lines", "receipt_serials", "receipt_exceptions", "inbound_orders")

def _immutable(table, dialect):
    name = f"trg_{table}_immutable_0074"
    if dialect == "sqlite":
        op.execute(f"CREATE TRIGGER {name}_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, '0074 facts are append-only'); END")
        op.execute(f"CREATE TRIGGER {name}_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, '0074 facts are append-only'); END")
    else:
        op.execute(f"""CREATE OR REPLACE FUNCTION public.rsc_guard_{table}_immutable_0074() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ BEGIN RAISE EXCEPTION '0074 facts are append-only' USING ERRCODE='55000'; END $$""")
        op.execute(f"REVOKE ALL ON FUNCTION public.rsc_guard_{table}_immutable_0074() FROM PUBLIC, star_oam_api")
        op.execute(f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_{table}_immutable_0074()")
        op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER {name}")

def upgrade():
    d = op.get_bind().dialect.name
    op.create_table("logistics_events", sa.Column("id",sa.Uuid(),primary_key=True), sa.Column("shipment_id",sa.Uuid(),sa.ForeignKey("shipments.id",ondelete="RESTRICT"),nullable=False), sa.Column("event_type",sa.String(32),nullable=False), sa.Column("event_at",sa.DateTime(timezone=True),nullable=False), sa.Column("source",sa.String(32),nullable=False), sa.Column("evidence_file_id",sa.Uuid()), sa.Column("external_ref",sa.String(200)), sa.Column("idempotency_key_hash",sa.String(64),nullable=False), sa.Column("actor_user_id",sa.String(36),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.CheckConstraint("event_type IN ('pickup','transit','signed','exception')",name="ck_logistics_events_type"), sa.UniqueConstraint("idempotency_key_hash",name="uq_logistics_events_key"))
    op.create_table("receipts", sa.Column("id",sa.Uuid(),primary_key=True), sa.Column("receipt_no",sa.String(100),nullable=False), sa.Column("shipment_id",sa.Uuid(),sa.ForeignKey("shipments.id",ondelete="RESTRICT"),nullable=False), sa.Column("status",sa.String(32),nullable=False), sa.Column("received_at",sa.DateTime(timezone=True),nullable=False), sa.Column("receiver_person_id",sa.Uuid(),nullable=False), sa.Column("request_hash",sa.String(64),nullable=False), sa.Column("idempotency_key_hash",sa.String(64),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.CheckConstraint("status IN ('draft','partially_accepted','accepted','exception','rejected')",name="ck_receipts_status"), sa.UniqueConstraint("receipt_no",name="uq_receipts_number"), sa.UniqueConstraint("idempotency_key_hash",name="uq_receipts_key"))
    op.create_table("receipt_lines", sa.Column("id",sa.Uuid(),primary_key=True), sa.Column("receipt_id",sa.Uuid(),sa.ForeignKey("receipts.id",ondelete="RESTRICT"),nullable=False), sa.Column("shipment_line_id",sa.Uuid(),sa.ForeignKey("shipment_lines.id",ondelete="RESTRICT"),nullable=False), sa.Column("accepted_qty",sa.Numeric(18,3),nullable=False), sa.Column("rejected_qty",sa.Numeric(18,3),nullable=False), sa.Column("condition",sa.String(32),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.CheckConstraint("accepted_qty >= 0 AND rejected_qty >= 0 AND accepted_qty + rejected_qty > 0",name="ck_receipt_lines_qty"), sa.CheckConstraint("condition IN ('normal','shortage','damaged','wrong_material','wrong_serial','rejected')",name="ck_receipt_lines_condition"), sa.UniqueConstraint("receipt_id","shipment_line_id",name="uq_receipt_lines_shipment"))
    op.create_table("receipt_serials", sa.Column("receipt_line_id",sa.Uuid(),sa.ForeignKey("receipt_lines.id",ondelete="RESTRICT"),nullable=False), sa.Column("serial_id",sa.Uuid(),nullable=False), sa.Column("accepted",sa.Boolean(),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.PrimaryKeyConstraint("receipt_line_id","serial_id"))
    op.create_table("receipt_exceptions", sa.Column("id",sa.Uuid(),primary_key=True), sa.Column("receipt_id",sa.Uuid(),sa.ForeignKey("receipts.id",ondelete="RESTRICT"),nullable=False), sa.Column("receipt_line_id",sa.Uuid(),sa.ForeignKey("receipt_lines.id",ondelete="RESTRICT")), sa.Column("exception_type",sa.String(32),nullable=False), sa.Column("detail",sa.String(1000),nullable=False), sa.Column("evidence_file_id",sa.Uuid()), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False))
    op.create_table("inbound_orders", sa.Column("id",sa.Uuid(),primary_key=True), sa.Column("inbound_no",sa.String(100),nullable=False), sa.Column("receipt_id",sa.Uuid(),sa.ForeignKey("receipts.id",ondelete="RESTRICT"),nullable=False), sa.Column("target_location_id",sa.Uuid(),nullable=False), sa.Column("target_person_id",sa.Uuid(),nullable=False), sa.Column("status",sa.String(32),nullable=False), sa.Column("posting_transaction_id",sa.Uuid(),sa.ForeignKey("inventory_transactions.id",ondelete="RESTRICT")), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.CheckConstraint("status IN ('pending','posted','exception')",name="ck_inbound_orders_status"), sa.UniqueConstraint("inbound_no",name="uq_inbound_orders_number"))
    for table in TABLES: _immutable(table, d)
    if d == "postgresql": op.execute("GRANT SELECT, INSERT ON TABLE public.logistics_events, public.receipts, public.receipt_lines, public.receipt_serials, public.receipt_exceptions, public.inbound_orders TO star_oam_api")

def downgrade():
    for table in reversed(TABLES): op.drop_table(table)
