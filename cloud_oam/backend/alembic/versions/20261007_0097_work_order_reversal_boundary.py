"""Keep generic reversals from detaching immutable work-order stock facts.

This migration does not enable work-order reversal. Dedicated compensation must
extend this proof with reservation attribution and complete paired inverses.
Existing detached inverses require review; no history is rewritten or deleted.
"""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20261007_0097"
down_revision = "20261006_0096"
branch_labels = depends_on = None
OLD_HASH = "1c842e6876982e06ef99d0fe7e9ccb8ba0c0256dafa9e51763ee0946b678147f"
NEW_HASH = "7d8e425310139aed3ce2da6a5344ce8cabb37f3b4555b58c4933b7e307b0d62a"
SIGNATURE = "public.rsc_check_work_order_material_transaction_0090(uuid)"


def forbidden_reversals(schema=""):
    return f"""SELECT 1 FROM {schema}inventory_transactions inverse
        JOIN {schema}inventory_transactions original ON original.id = inverse.reversed_transaction_id
        WHERE (original.source_document_type = 'work_order_material'
            OR inverse.source_document_type = 'work_order_material'
            OR EXISTS (SELECT 1 FROM {schema}work_order_material_operations operation
                WHERE operation.posting_transaction_id = original.id))"""


GUARD = f"""    -- Inspect both ends; an unrelated inverse source cannot escape this guard.
    -- The existing deferred dispatcher also checks late operation bindings.
    IF EXISTS ({forbidden_reversals('public.')}
        AND (inverse.id = tx.id OR original.id = tx.id)) THEN
        RAISE EXCEPTION '0097 work order reversal requires dedicated compensation proof' USING ERRCODE = '23514';
    END IF;
"""


def sources():
    prior = runpy.run_path(str(Path(__file__).with_name("20261003_0093_work_order_replacements.py")))
    old = prior["_sources"]()[SIGNATURE][1]
    anchor = "    IF tx.source_document_type <> 'work_order_material' THEN"
    if old.count(anchor) != 1:
        raise RuntimeError("0097 original work-order guard source mismatch")
    return old, old.replace(anchor, GUARD + anchor)


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0097 supports PostgreSQL and SQLite only")
    folder = Path(__file__).parent
    helper = runpy.run_path(str(folder / "20260927_0087_inbound_fulfillment_boundary.py"))
    helper["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.inventory_transactions, public.work_order_material_operations IN SHARE ROW EXCLUSIVE MODE")
    helper["_preflight"]("EXISTS (" + forbidden_reversals() + ")",
        "0097 transition blocked: detached work-order reversals require reviewed compensation")
    if db.dialect.name != "postgresql":
        return
    source = runpy.run_path(str(folder / "20260912_0072_outbound_postings.py"))
    replace = source["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
    old, new = sources()
    replace(signature=SIGNATURE,
        expected_hash=hashlib.sha256((old if upgrade else new).encode()).hexdigest(),
        replacement_hash=hashlib.sha256((new if upgrade else old).encode()).hexdigest(),
        replacements=((old, new),) if upgrade else ((new, old),), label="work_order_reversal_boundary_0097")
    replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
        expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
        replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
        label="work_order_reversal_readiness_0097")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
