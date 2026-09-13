"""Independent return-receipt inbound facts and their immutable posting.

The 0105 receipt is an acceptance observation only.  This forward migration
adds the separate local-inventory fact that moves accepted quantities from the
original transit account into the verified region/personal account.  It never
reuses the generic demand-receipt/inbound tables and it is append-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import runpy

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261016_0106"
down_revision = "20261015_0105"
branch_labels = depends_on = None

TABLES = (
    "stock_operation_return_inbounds",
    "stock_operation_return_inbound_lines",
    "stock_operation_return_inbound_serials",
    "stock_operation_return_inbound_postings",
)

# The readiness function is advanced only after its exact body transition is
# installed and recorded in the runtime manifest.  Keeping this predecessor
# hash explicit makes the 0106 schema work fail closed until that catalog step
# is completed; it must not silently accept an unrecorded function body.
OLD_HASH = "65b5e8ea7d0442ecdd289b46bebe0531371e3691aa930d88817e9ceba9a7a240"
NEW_HASH = "f1f86651bc55ab85977b91d9bb5d02c56fc5b45a1dfd83e13ac53be75d163ca4"


INBOUND_CHECK_BODY = r"""
DECLARE
    inbound public.stock_operation_return_inbounds%ROWTYPE;
    receipt public.stock_operation_receipts%ROWTYPE;
    shipment public.stock_operation_shipments%ROWTYPE;
    transaction public.inventory_transactions%ROWTYPE;
    line public.stock_operation_return_inbound_lines%ROWTYPE;
    source public.stock_accounts%ROWTYPE;
    target public.stock_accounts%ROWTYPE;
    expected_count integer;
BEGIN
    SELECT * INTO inbound
      FROM public.stock_operation_return_inbounds
     WHERE id = checked_inbound;
    IF inbound.id IS NULL THEN
        RAISE EXCEPTION '0106 detached return inbound' USING ERRCODE = '23514';
    END IF;
    SELECT * INTO receipt FROM public.stock_operation_receipts WHERE id = inbound.receipt_id;
    SELECT * INTO shipment FROM public.stock_operation_shipments WHERE id = inbound.shipment_id;
    SELECT * INTO transaction FROM public.inventory_transactions WHERE id = inbound.posting_transaction_id;
    IF receipt.id IS NULL OR shipment.id IS NULL OR transaction.id IS NULL
       OR receipt.shipment_id <> inbound.shipment_id
       OR shipment.target_custody_assignment_id <> inbound.target_custody_assignment_id
       OR inbound.status <> 'posted'
       OR transaction.status <> 'posted'
       OR transaction.movement_type <> 'transfer'
       OR transaction.source_document_type <> 'stock_return_receipt_inbound'
       OR transaction.source_document_id <> inbound.id::text
       OR transaction.posting_key <> 'stock-return-receipt-inbound:' || inbound.receipt_id::text
       OR transaction.actor_user_id <> inbound.actor_user_id THEN
        RAISE EXCEPTION '0106 return inbound coordinate or posting mismatch' USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO expected_count
      FROM public.stock_operation_return_inbound_lines
     WHERE inbound_id = inbound.id;
    IF expected_count < 1 THEN
        RAISE EXCEPTION '0106 return inbound has no accepted lines' USING ERRCODE = '23514';
    END IF;
    FOR line IN
        SELECT * FROM public.stock_operation_return_inbound_lines
         WHERE inbound_id = inbound.id ORDER BY line_no
    LOOP
        SELECT * INTO source FROM public.stock_accounts WHERE id = line.source_account_id;
        SELECT * INTO target FROM public.stock_accounts WHERE id = line.target_account_id;
        IF source.id IS NULL OR target.id IS NULL
           OR source.availability_bucket <> 'in_transit'
           OR target.availability_bucket <> 'available'
           OR source.material_id <> line.material_id
           OR target.material_id <> line.material_id
           OR source.condition_code <> line.condition_code
           OR target.condition_code <> line.condition_code
           OR source.lot_id IS DISTINCT FROM line.lot_id
           OR target.lot_id IS DISTINCT FROM line.lot_id
           OR source.id = target.id
           OR NOT EXISTS (
               SELECT 1 FROM public.stock_operation_receipt_lines accepted
                WHERE accepted.id = line.receipt_line_id
                  AND accepted.receipt_id = inbound.receipt_id
                  AND accepted.accepted_qty = line.accepted_qty
           ) THEN
            RAISE EXCEPTION '0106 return inbound line binding mismatch' USING ERRCODE = '23514';
        END IF;
    END LOOP;
    RETURN;
END;
"""


DISPATCH_BODY = r"""
DECLARE identifier uuid;
BEGIN
    IF TG_TABLE_NAME = 'stock_operation_return_inbounds' THEN
        identifier := NEW.id;
    ELSIF TG_TABLE_NAME = 'stock_operation_return_inbound_lines' THEN
        identifier := NEW.inbound_id;
    ELSIF TG_TABLE_NAME = 'stock_operation_return_inbound_serials' THEN
        SELECT inbound_id INTO identifier
          FROM public.stock_operation_return_inbound_lines
         WHERE id = NEW.line_id;
    ELSIF TG_TABLE_NAME = 'stock_operation_return_inbound_postings' THEN
        identifier := NEW.inbound_id;
    END IF;
    IF identifier IS NULL THEN
        RAISE EXCEPTION '0106 detached return inbound evidence' USING ERRCODE = '23514';
    END IF;
    PERFORM public.rsc_check_stock_return_inbound_0106(identifier);
    RETURN NULL;
END;
"""


FUNCTIONS = {
    ("rsc_check_stock_return_inbound_0106", "uuid"):
        ("checked_inbound uuid", "void", INBOUND_CHECK_BODY),
    ("rsc_dispatch_stock_return_inbound_0106", ""):
        ("", "trigger", DISPATCH_BODY),
}
FUNCTION_HASHES = {
    key: hashlib.sha256(value[2].encode("utf-8")).hexdigest()
    for key, value in FUNCTIONS.items()
}


def _create_tables() -> None:
    document = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        TABLES[0],
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("inbound_no", sa.String(100), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("stock_operation_receipts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shipment_id", sa.Uuid(), sa.ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operator_person_id", sa.Uuid(), sa.ForeignKey("people.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_location_id", sa.Uuid(), sa.ForeignKey("stock_locations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_custody_assignment_id", sa.Uuid(), sa.ForeignKey("custody_assignments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("receipt_plan_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("audit_version", sa.BigInteger(), nullable=False),
        sa.Column("command_jsonb", document, nullable=False),
        sa.Column("plan_jsonb", document, nullable=False),
        sa.Column("posting_transaction_id", sa.Uuid(), sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inbound_no", name="uq_stock_operation_return_inbounds_no"),
        sa.UniqueConstraint("receipt_id", name="uq_stock_operation_return_inbounds_receipt"),
        sa.UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_return_inbounds_request"),
        sa.UniqueConstraint("idempotency_key_hash", name="uq_stock_operation_return_inbounds_key"),
        sa.UniqueConstraint("posting_transaction_id", name="uq_stock_operation_return_inbounds_posting"),
        sa.CheckConstraint("status = 'posted'", name="ck_stock_operation_return_inbounds_status"),
        sa.CheckConstraint("authorization_version > 0 AND audit_version > 0 AND length(reason) BETWEEN 1 AND 500 AND length(idempotency_key_hash)=64 AND length(request_hash)=64 AND length(receipt_plan_hash)=64 AND length(plan_hash)=64", name="ck_stock_operation_return_inbounds_context"),
    )
    op.create_table(
        TABLES[1],
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("inbound_id", sa.Uuid(), sa.ForeignKey(TABLES[0] + ".id", ondelete="RESTRICT"), nullable=False),
        sa.Column("receipt_line_id", sa.Uuid(), sa.ForeignKey("stock_operation_receipt_lines.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("line_no", sa.BigInteger(), nullable=False),
        sa.Column("source_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("target_account_id", sa.Uuid(), sa.ForeignKey("stock_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("material_id", sa.Uuid(), sa.ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("condition_code", sa.String(24), nullable=False),
        sa.Column("accepted_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inbound_id", "line_no", name="uq_stock_operation_return_inbound_lines_order"),
        sa.UniqueConstraint("inbound_id", "receipt_line_id", name="uq_stock_operation_return_inbound_lines_origin"),
        sa.UniqueConstraint("id", "inbound_id", name="uq_stock_operation_return_inbound_lines_binding"),
        sa.ForeignKeyConstraint(["lot_id", "material_id"], ["inventory_lots.id", "inventory_lots.material_id"], ondelete="RESTRICT"),
        sa.CheckConstraint("line_no > 0 AND accepted_qty > 0 AND condition_code IN ('used','damaged')", name="ck_stock_operation_return_inbound_lines_context"),
    )
    op.create_table(
        TABLES[2],
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("inbound_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_serial_id", sa.Uuid(), sa.ForeignKey("stock_operation_receipt_serials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("serial_id", sa.Uuid(), sa.ForeignKey("inventory_serials.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("line_id", "serial_id", name="uq_stock_operation_return_inbound_serials_line"),
        sa.UniqueConstraint("inbound_id", "serial_id", name="uq_stock_operation_return_inbound_serials_once"),
        sa.UniqueConstraint("receipt_serial_id", name="uq_stock_operation_return_inbound_serials_receipt"),
        sa.ForeignKeyConstraint(["line_id", "inbound_id"], [TABLES[1] + ".id", TABLES[1] + ".inbound_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        TABLES[3],
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("inbound_id", sa.Uuid(), sa.ForeignKey(TABLES[0] + ".id", ondelete="RESTRICT"), nullable=False),
        sa.Column("inventory_transaction_id", sa.Uuid(), sa.ForeignKey("inventory_transactions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("inbound_id", name="uq_stock_operation_return_inbound_postings_inbound"),
        sa.UniqueConstraint("inventory_transaction_id", name="uq_stock_operation_return_inbound_postings_transaction"),
    )
    op.create_index("ix_stock_operation_return_inbounds_receipt_id", TABLES[0], ["receipt_id"])
    op.create_index("ix_stock_operation_return_inbound_lines_receipt_line_id", TABLES[1], ["receipt_line_id"])
    op.create_index("ix_stock_operation_return_inbound_serials_serial_id", TABLES[2], ["serial_id"])


def _transition(upgrade: bool) -> None:
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0106 supports PostgreSQL and SQLite only")
    if db.dialect.name == "postgresql":
        replace = runpy.run_path(
            str(Path(__file__).with_name("20260912_0072_outbound_postings.py"))
        )["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    if upgrade:
        _create_tables()
        if db.dialect.name == "sqlite":
            for table in TABLES:
                for action in ("UPDATE", "DELETE"):
                    op.execute(f"CREATE TRIGGER trg_{table}_{action.lower()}_immutable_0106 BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, '0106 return inbound facts are append-only'); END")
        else:
            for table in TABLES:
                op.execute(f"ALTER TABLE public.{table} OWNER TO star_oam_migrator")
                op.execute(f"REVOKE ALL ON TABLE public.{table} FROM PUBLIC")
            for (name, signature), (args, result, body) in FUNCTIONS.items():
                op.execute(f"CREATE FUNCTION public.{name}({args}) RETURNS {result} LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $body${body}$body$")
                op.execute(f"REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC, star_oam_api")
            for table in TABLES:
                op.execute(f"CREATE CONSTRAINT TRIGGER trg_{table}_proof_0106 AFTER INSERT ON public.{table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.rsc_dispatch_stock_return_inbound_0106()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER trg_{table}_proof_0106")
                op.execute(f"CREATE TRIGGER trg_{table}_immutable_0106 BEFORE UPDATE OR DELETE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.rsc_guard_work_order_facts_0090()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER trg_{table}_immutable_0106")
                op.execute(f"CREATE TRIGGER trg_{table}_no_truncate_0106 BEFORE TRUNCATE ON public.{table} FOR EACH STATEMENT EXECUTE FUNCTION public.rsc_guard_work_order_facts_0090()")
                op.execute(f"ALTER TABLE public.{table} ENABLE ALWAYS TRIGGER trg_{table}_no_truncate_0106")
            op.execute("REVOKE ALL ON TABLE " + ", ".join("public." + t for t in TABLES) + " FROM PUBLIC, star_oam_api")
            op.execute("GRANT SELECT, INSERT ON TABLE " + ", ".join("public." + t for t in TABLES) + " TO star_oam_api")
            replace(
                signature="public.rsc_oam_runtime_binding_ready_0044()",
                expected_hash=OLD_HASH,
                replacement_hash=NEW_HASH,
                replacements=((down_revision, revision),),
                label="stock_return_inbound_readiness_0106",
            )
    else:
        if db.dialect.name == "postgresql":
            replace(
                signature="public.rsc_oam_runtime_binding_ready_0044()",
                expected_hash=NEW_HASH,
                replacement_hash=OLD_HASH,
                replacements=((revision, down_revision),),
                label="stock_return_inbound_readiness_0106",
            )
            for table in reversed(TABLES):
                op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")
                op.execute(f"DO $body$ BEGIN IF EXISTS (SELECT 1 FROM public.{table}) THEN RAISE EXCEPTION '0106 downgrade blocked: return inbound facts must be retained'; END IF; END $body$")
                op.execute(f"DROP TRIGGER trg_{table}_proof_0106 ON public.{table}")
                op.execute(f"DROP TRIGGER trg_{table}_immutable_0106 ON public.{table}")
                op.execute(f"DROP TRIGGER trg_{table}_no_truncate_0106 ON public.{table}")
            for name, signature in reversed(FUNCTIONS):
                op.execute(f"DROP FUNCTION public.{name}({signature})")
        else:
            for table in TABLES:
                if db.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
                    raise RuntimeError("0106 downgrade blocked: return inbound facts must be retained")
                for action in ("update", "delete"):
                    op.execute(f"DROP TRIGGER trg_{table}_{action}_immutable_0106")
        for table in reversed(TABLES):
            op.drop_table(table)


def upgrade() -> None:
    _transition(True)


def downgrade() -> None:
    _transition(False)
