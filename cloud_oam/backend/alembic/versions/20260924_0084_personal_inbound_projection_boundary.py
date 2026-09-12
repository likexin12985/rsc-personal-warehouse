"""Permit only fact-derived personal-inbound projection updates."""
from pathlib import Path
import runpy

from alembic import op

revision = "20260924_0084"
down_revision = "20260923_0083"
branch_labels = depends_on = None
OLD_HASH = "88d751f169702542808c0d9def764b608b93774821e54bfbcaba177c236da838"
NEW_HASH = "5f5ac9e237353067ee6fe3277487715eefcb05804731bc252378814524866dbe"


def state_sql(schema="public.", request="NEW"):
    # Compare quantities per current approved material line. EXISTS prevents
    # duplicate inbound bindings from multiplying an accepted quantity.
    return f"""WITH line_totals AS (
        SELECT line.id, line.final_approved_qty - line.cancelled_qty AS net,
            COALESCE((SELECT sum(receipt_line.accepted_qty)
                FROM {schema}receipt_lines receipt_line
                JOIN {schema}receipts receipt ON receipt.id = receipt_line.receipt_id
                JOIN {schema}shipment_lines shipped ON shipped.id = receipt_line.shipment_line_id
                JOIN {schema}outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                WHERE outbound.request_id = {request}.id AND outbound.request_line_id = line.id
                  AND receipt.status IN ('accepted', 'exception')), 0) AS accepted,
            COALESCE((SELECT sum(receipt_line.accepted_qty)
                FROM {schema}receipt_lines receipt_line
                JOIN {schema}receipts receipt ON receipt.id = receipt_line.receipt_id
                JOIN {schema}shipment_lines shipped ON shipped.id = receipt_line.shipment_line_id
                JOIN {schema}outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                WHERE outbound.request_id = {request}.id AND outbound.request_line_id = line.id
                  AND receipt.status IN ('accepted', 'exception') AND EXISTS (
                    SELECT 1 FROM {schema}inbound_orders inbound
                    JOIN {schema}inbound_postings posting ON posting.inbound_order_id = inbound.id
                    JOIN {schema}inventory_transactions tx ON tx.id = posting.inventory_transaction_id
                    WHERE inbound.receipt_id = receipt_line.receipt_id
                      AND tx.status = 'posted' AND tx.movement_type = 'transfer'
                      AND tx.source_document_type = 'personal_inbound'
                      AND replace(CAST(inbound.id AS text), '-', '') = replace(tx.source_document_id, '-', '')
                )), 0) AS posted,
            CASE WHEN EXISTS (SELECT 1 FROM {schema}shipment_lines shipped
                JOIN {schema}outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
                WHERE outbound.request_id = {request}.id AND outbound.request_line_id = line.id
                  AND shipped.shipped_qty > 0) THEN 1 ELSE 0 END AS shipped
        FROM {schema}material_request_lines line
        WHERE line.request_id = {request}.id AND line.revision_no = {request}.revision_no
          AND line.final_approved_qty > line.cancelled_qty
    ) SELECT CASE
        WHEN count(*) = 0 THEN 'not_started'
        WHEN sum(CASE WHEN posted >= net THEN 1 ELSE 0 END) = count(*) THEN 'posted'
        WHEN sum(CASE WHEN accepted >= net THEN 1 ELSE 0 END) = count(*) THEN 'accepted'
        WHEN sum(CASE WHEN accepted > 0 THEN 1 ELSE 0 END) > 0 THEN 'partially_accepted'
        WHEN sum(shipped) > 0 THEN 'pending_acceptance'
        ELSE 'not_started' END FROM line_totals"""


REQUEST_OLD = """    IF NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'
       OR NEW.oam_receipt_status <> 'not_occurred'
       OR NEW.personal_inbound_status <> 'not_started'
       OR NEW.notification_status <> 'not_started'"""
REQUEST_NEW = f"""    IF NEW.personal_inbound_status IS DISTINCT FROM OLD.personal_inbound_status THEN
        IF NEW.version <> OLD.version + 1 OR NEW.updated_at <= OLD.updated_at
           OR NEW.personal_inbound_status IS DISTINCT FROM ({state_sql()}) THEN
            RAISE EXCEPTION '0084 personal inbound projection does not match fulfillment facts' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'
       OR NEW.oam_receipt_status <> 'not_occurred'
       OR NEW.notification_status <> 'not_started'"""

# The planning commands precede shipment and keep their original neutral
# personal-inbound axis. The supply write boundary remains unchanged; this
# only stops revalidating old commands against a later fulfillment projection.
SUPPLY_OLD = "       OR request_row.personal_inbound_status <> 'not_started'"
SUPPLY_NEW = "       OR request_row.personal_inbound_status NOT IN ('not_started', 'pending_acceptance', 'partially_accepted', 'accepted', 'posted')"
SUPPLY_AXIS_OLD = """           OR command_row.result_jsonb->'state_axes'->>'personal_inbound_status' <>
               request_row.personal_inbound_status"""
SUPPLY_AXIS_NEW = """           OR command_row.result_jsonb->'state_axes'->>'personal_inbound_status' <>
               'not_started'"""
FUNCTION_HASHES = {
    ("rsc_guard_material_request_identity_0029", ""): (
        "5392ed5737789b4974096e630ed77927952d44b8231c66f59e65d6c88f9603d9", "349ba279bb1a9c3d46ac76aa8ad2b4a5b99f5de67db332ed6829e6b19ccac8f6"),
    ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"): (
        "f2da8696a99dfa4a0a10432f016a9347c3f53f4d066474105eb5390e6f5a16a2", "ef61be0a9be9435412d44d07745460fb395a2a77c443a7452c6ea84f333c2922"),
}


def source_changes():
    return {
        ("rsc_guard_material_request_identity_0029", ""): ((REQUEST_OLD, REQUEST_NEW),),
        ("rsc_validate_material_request_supply_causality_0059", "uuid, bigint"): (
            (SUPPLY_OLD, SUPPLY_NEW), (SUPPLY_AXIS_OLD, SUPPLY_AXIS_NEW)),
    }


def _postgresql(upgrade):
    op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
    prior = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
    replace = prior["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    for coordinate, pairs in source_changes().items():
        before, after = FUNCTION_HASHES[coordinate]
        replace(signature=f"public.{coordinate[0]}({coordinate[1]})",
                expected_hash=before if upgrade else after, replacement_hash=after if upgrade else before,
                replacements=pairs if upgrade else tuple((b, a) for a, b in reversed(pairs)), label="personal_inbound_0084")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
            expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),), label="personal_inbound_readiness_0084")
    op.execute("GRANT UPDATE (personal_inbound_status) ON public.material_requests TO star_oam_api" if upgrade
               else "REVOKE UPDATE (personal_inbound_status) ON public.material_requests FROM star_oam_api")


def _sqlite(upgrade):
    db = op.get_bind()
    source = db.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'trg_material_requests_update_guard_0029'").scalar_one()
    before = "  OR NEW.personal_inbound_status <> 'not_started'"
    after = f"""  OR (NEW.personal_inbound_status IS NOT OLD.personal_inbound_status AND (
      NEW.version <> OLD.version + 1 OR NEW.updated_at <= OLD.updated_at
      OR NEW.personal_inbound_status IS NOT ({state_sql(schema='')})
  ))"""
    old, new = (before, after) if upgrade else (after, before)
    if source.count(old) != 1:
        raise RuntimeError("0084 SQLite request guard does not match reviewed source")
    op.execute("DROP TRIGGER trg_material_requests_update_guard_0029")
    op.execute(source.replace(old, new))


def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        _postgresql(True)
    elif op.get_bind().dialect.name == "sqlite":
        _sqlite(True)
    else:
        raise RuntimeError("0084 supports only PostgreSQL and SQLite")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("LOCK TABLE public.material_requests IN ACCESS EXCLUSIVE MODE")
        op.execute("""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM public.material_requests WHERE personal_inbound_status <> 'not_started') THEN
                RAISE EXCEPTION '0084 downgrade blocked: personal inbound projection facts exist';
            END IF;
        END $$""")
        _postgresql(False)
    elif op.get_bind().dialect.name == "sqlite":
        if op.get_bind().exec_driver_sql("SELECT EXISTS (SELECT 1 FROM material_requests WHERE personal_inbound_status <> 'not_started')").scalar():
            raise RuntimeError("0084 downgrade blocked: personal inbound projection facts exist")
        _sqlite(False)
    else:
        raise RuntimeError("0084 supports only PostgreSQL and SQLite")
