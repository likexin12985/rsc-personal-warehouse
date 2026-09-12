"""Admit a personal reserved dimension only with its original work-order occupy."""
import hashlib
from pathlib import Path
import runpy

from alembic import op

revision = "20261001_0091"
down_revision = "20260930_0090"
branch_labels = depends_on = None
OLD_HASH = "394df0dcbd0168dc59e9c3853748dc0a794db913e658d482864afaf6aa9de583"
NEW_HASH = "025c7a50a93bfb8033c3235374dbb9c92a05e41136c2177b3efeb7c3402d8d8a"
ACCOUNT_OLD_HASH = "e3de9261d8947e854622ba2605be5575727d4a9e6c24e784ace79749f078e11c"
ACCOUNT_NEW_HASH = "d6b09038f5972a45939035bf44434d0a38a5874b1299b9c8f0936a808d028c76"
ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.work_order_material_operations operation
        JOIN public.inventory_transactions tx ON tx.id = operation.posting_transaction_id
        JOIN public.inventory_movements movement ON movement.transaction_id = tx.id AND movement.to_account_id = NEW.id
        JOIN public.work_order_material_lines line ON line.operation_id = operation.id
          AND line.line_no = movement.line_no AND line.stock_account_id = movement.from_account_id
          AND line.quantity = movement.quantity AND line.material_id = NEW.material_id
        JOIN public.stock_accounts source ON source.id = movement.from_account_id
        JOIN public.stock_balances balance ON balance.stock_account_id = NEW.id
          AND balance.version = 1 AND balance.ledger_cursor = tx.ledger_cursor
        JOIN public.stock_locations location ON location.id = NEW.location_id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id = NEW.owner_org_id
          AND established.location_id = NEW.location_id
        JOIN public.oam_work_orders work_order ON work_order.id = operation.oam_work_order_id
        JOIN public.users actor ON actor.id = tx.actor_user_id
        WHERE tx.status = 'posted' AND tx.movement_type = 'reserve'
          AND tx.source_document_type = 'work_order_material' AND tx.source_document_id = work_order.id::text
          AND operation.operation_type = 'occupy' AND operation.status = 'posted'
          AND actor.person_id = operation.operator_person_id AND work_order.engineer_person_id = operation.operator_person_id
          AND operation.operator_person_id = NEW.custodian_person_id AND work_order.status = 'active'
          AND NEW.availability_bucket = 'reserved' AND source.availability_bucket = 'available'
          AND NEW.owner_org_id = source.owner_org_id AND NEW.custodian_person_id = source.custodian_person_id
          AND NEW.location_id = source.location_id AND NEW.material_id = source.material_id
          AND NEW.condition_code = source.condition_code AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND location.location_type = 'personal' AND location.status = 'active'
          AND location.custodian_person_id = NEW.custodian_person_id
          AND NEW.created_at >= established.established_at AND NEW.created_at <= tx.created_at
          AND NEW.updated_at = NEW.created_at
          AND balance.quantity = (SELECT sum(initial.quantity) FROM public.inventory_movements initial
            WHERE initial.transaction_id = tx.id AND initial.to_account_id = NEW.id)
          AND NOT EXISTS (SELECT 1 FROM public.inventory_movements earlier
            JOIN public.inventory_transactions previous ON previous.id = earlier.transaction_id
            WHERE (earlier.from_account_id = NEW.id OR earlier.to_account_id = NEW.id)
              AND previous.ledger_cursor < tx.ledger_cursor)
    ) THEN
        RETURN NEW;
    END IF;

"""
ADMISSION_FACTS = """EXISTS (
    SELECT 1 FROM stock_accounts account
    JOIN inventory_opening_establishments established ON established.owner_org_id = account.owner_org_id
      AND established.location_id = account.location_id
    JOIN inventory_movements movement ON movement.to_account_id = account.id
    JOIN inventory_transactions tx ON tx.id = movement.transaction_id
    WHERE account.availability_bucket = 'reserved' AND account.created_at >= established.established_at
      AND tx.source_document_type = 'work_order_material' AND tx.movement_type = 'reserve' AND tx.status = 'posted'
      AND NOT EXISTS (SELECT 1 FROM inventory_movements earlier
        JOIN inventory_transactions previous ON previous.id = earlier.transaction_id
        WHERE (earlier.from_account_id = account.id OR earlier.to_account_id = account.id)
          AND previous.ledger_cursor < tx.ledger_cursor)
)"""


def _account_sources():
    prior = runpy.run_path(str(Path(__file__).with_name("20260928_0088_receipt_account_admission.py")))
    _, old = prior["_account_sources"]()
    anchor = prior["ANCHOR"]
    new = old.replace(anchor, ACCOUNT_BRANCH + anchor)
    if (old.count(anchor) != 1 or hashlib.sha256(old.encode()).hexdigest() != ACCOUNT_OLD_HASH
            or hashlib.sha256(new.encode()).hexdigest() != ACCOUNT_NEW_HASH):
        raise RuntimeError("0091 canonical account admission source drift")
    return old, new


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("0091 supports only PostgreSQL and SQLite")
    prior = runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))
    prior["_begin_sqlite"]()
    if db.dialect.name == "postgresql":
        op.execute("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
        op.execute("LOCK TABLE public.stock_accounts, public.work_order_material_operations, public.inventory_movements IN SHARE ROW EXCLUSIVE MODE")
    if not upgrade:
        prior["_preflight"](ADMISSION_FACTS, "0091 downgrade blocked: work order account admissions require reviewed migration")
    if db.dialect.name == "postgresql":
        source = runpy.run_path(str(Path(__file__).with_name("20260912_0072_outbound_postings.py")))
        replace = source["_previous"]()["_previous"]()["_previous"]()["_replace_function_source"]
        old, new = _account_sources()
        replace(signature="public.rsc_require_opening_observation_account_0023()",
                expected_hash=ACCOUNT_OLD_HASH if upgrade else ACCOUNT_NEW_HASH,
                replacement_hash=ACCOUNT_NEW_HASH if upgrade else ACCOUNT_OLD_HASH,
                replacements=((old, new),) if upgrade else ((new, old),), label="work_order_account_admission_0091")
        replace(signature="public.rsc_oam_runtime_binding_ready_0044()",
                expected_hash=OLD_HASH if upgrade else NEW_HASH, replacement_hash=NEW_HASH if upgrade else OLD_HASH,
                replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
                label="work_order_account_readiness_0091")


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
