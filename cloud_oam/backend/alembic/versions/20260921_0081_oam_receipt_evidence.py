"""Add immutable read-only OAM receipt evidence bound to shipments."""
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa

revision = "20260921_0081"
down_revision = "20260920_0080"
branch_labels = depends_on = None
RUNTIME_READY_BODY_SHA256_0080 = "39c99f7b25ea5cbb22befa6176e8ea6b0237ace130e3848792d2aa864edd3583"
RUNTIME_READY_BODY_SHA256_0081 = "efb632b66d584cd8fe26414abd5420ddb59d7fbf97ad50e13864bb73237b14b6"


def _replace_readiness(*, upgrade: bool) -> None:
    previous = runpy.run_path(str(Path(__file__).with_name("20260920_0080_inbound_posting_acl.py")))
    migration = previous["_migration_0072"]()
    replace = migration["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    replace(
        signature="public.rsc_oam_runtime_binding_ready_0044()",
        expected_hash=RUNTIME_READY_BODY_SHA256_0080 if upgrade else RUNTIME_READY_BODY_SHA256_0081,
        replacement_hash=RUNTIME_READY_BODY_SHA256_0081 if upgrade else RUNTIME_READY_BODY_SHA256_0080,
        replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
        label="runtime_readiness_0081",
    )


def _immutable(dialect: str) -> None:
    name = "trg_oam_receipt_evidence_immutable_0081"
    if dialect == "sqlite":
        op.execute(f"CREATE TRIGGER {name}_update BEFORE UPDATE ON oam_receipt_evidence BEGIN SELECT RAISE(ABORT, '0081 evidence is append-only'); END")
        op.execute(f"CREATE TRIGGER {name}_delete BEFORE DELETE ON oam_receipt_evidence BEGIN SELECT RAISE(ABORT, '0081 evidence is append-only'); END")
    else:
        op.execute("""CREATE FUNCTION public.rsc_guard_oam_receipt_evidence_immutable_0081() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN RAISE EXCEPTION '0081 evidence is append-only' USING ERRCODE='55000'; END $$""")
        op.execute("REVOKE ALL ON FUNCTION public.rsc_guard_oam_receipt_evidence_immutable_0081() FROM PUBLIC, star_oam_api")
        op.execute(f"CREATE TRIGGER {name} BEFORE UPDATE OR DELETE ON public.oam_receipt_evidence FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_oam_receipt_evidence_immutable_0081()")
        op.execute(f"ALTER TABLE public.oam_receipt_evidence ENABLE ALWAYS TRIGGER {name}")


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("0081 supports only PostgreSQL and SQLite")
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        _replace_readiness(upgrade=True)
    op.create_table(
        "oam_receipt_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("external_object_id", sa.Uuid(), sa.ForeignKey("external_objects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("shipments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("source_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.String(160), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('synced','exception')", name="ck_oam_receipt_evidence_status"),
        sa.UniqueConstraint("external_object_id", name="uq_oam_receipt_evidence_external"),
    )
    op.create_index("ix_oam_receipt_evidence_shipment", "oam_receipt_evidence", ["shipment_id", "source_time"])
    _immutable(dialect)
    if dialect == "postgresql":
        op.execute("GRANT SELECT, INSERT ON TABLE public.oam_receipt_evidence TO star_oam_api")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("0081 supports only PostgreSQL and SQLite")
    if dialect == "postgresql":
        op.execute("LOCK TABLE public.alembic_version, public.oam_receipt_evidence IN ACCESS EXCLUSIVE MODE")
        op.execute("""DO $$ BEGIN IF EXISTS (SELECT 1 FROM public.oam_receipt_evidence) THEN RAISE EXCEPTION '0081 downgrade blocked: OAM receipt evidence exists'; END IF; END $$""")
    elif op.get_bind().execute(sa.text("SELECT 1 FROM oam_receipt_evidence LIMIT 1")).first() is not None:
        raise RuntimeError("0081 downgrade blocked: OAM receipt evidence exists")
    if dialect == "postgresql":
        op.execute("REVOKE SELECT, INSERT ON TABLE public.oam_receipt_evidence FROM star_oam_api")
    op.drop_index("ix_oam_receipt_evidence_shipment", table_name="oam_receipt_evidence")
    op.drop_table("oam_receipt_evidence")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION public.rsc_guard_oam_receipt_evidence_immutable_0081()")
        _replace_readiness(upgrade=False)
