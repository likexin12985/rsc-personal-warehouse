"""Admit first regional return accounts only with an exact posted inbound.

Existing acceptance, ledger, authority and immutable proof guards remain in
force. This revision adds no table, role permission or external stock write.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op

revision = '20261128_0149'
down_revision = '20261127_0148'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
ACCOUNT_SIGNATURE = 'public.rsc_require_opening_observation_account_0023()'
ACCOUNT_OLD_HASH = '42eb336ee4cbf9cff4770790fedb2875ab49cf9a8b802c5d1b4f18fb33238089'
ACCOUNT_NEW_HASH = 'c9fa3bcfaf73e9d913cba1681805af51dabc9002a40ce61f23885d5735de721d'
previous = runpy.run_path(str(FOLDER / '20261127_0148_stock_loss_headquarters_review.py'))
OLD_READY_HASH = previous['NEW_READY_HASH']
ready = previous['ready']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()
ACCOUNT_BRANCH = "    IF EXISTS (\n        SELECT 1\n        FROM public.stock_operation_return_inbound_lines line\n        JOIN public.stock_operation_return_inbounds inbound ON inbound.id = line.inbound_id\n        JOIN public.stock_operation_return_inbound_postings posting ON posting.inbound_id = inbound.id\n          AND posting.inventory_transaction_id = inbound.posting_transaction_id\n        JOIN public.stock_operation_receipts receipt ON receipt.id = inbound.receipt_id\n          AND receipt.shipment_id = inbound.shipment_id\n        JOIN public.stock_operation_receipt_lines accepted ON accepted.id = line.receipt_line_id\n          AND accepted.receipt_id = receipt.id AND accepted.accepted_qty = line.accepted_qty\n        JOIN public.inventory_transactions tx ON tx.id = inbound.posting_transaction_id\n        JOIN public.inventory_movements movement ON movement.transaction_id = tx.id\n          AND movement.from_account_id = line.source_account_id\n          AND movement.to_account_id = line.target_account_id\n          AND movement.line_no = line.line_no AND movement.quantity = line.accepted_qty\n        JOIN public.stock_accounts source ON source.id = line.source_account_id\n        JOIN public.stock_locations location ON location.id = inbound.target_location_id\n        JOIN public.custody_assignments custody ON custody.id = inbound.target_custody_assignment_id\n          AND custody.location_id = location.id AND custody.custodian_person_id = inbound.operator_person_id\n        JOIN public.inventory_opening_establishments established ON established.owner_org_id = NEW.owner_org_id\n          AND established.location_id = NEW.location_id\n        JOIN public.stock_balances balance ON balance.stock_account_id = NEW.id\n          AND balance.version = 1 AND balance.ledger_cursor = tx.ledger_cursor\n        WHERE line.target_account_id = NEW.id AND line.accepted_qty > 0\n          AND inbound.status = 'posted' AND tx.status = 'posted' AND tx.movement_type = 'transfer'\n          AND tx.source_document_type = 'stock_return_receipt_inbound'\n          AND tx.source_document_id = inbound.id::text\n          AND tx.posting_key = 'stock-return-receipt-inbound:' || receipt.id::text\n          AND tx.actor_user_id = inbound.actor_user_id AND tx.reversed_transaction_id IS NULL\n          AND source.availability_bucket = 'in_transit' AND NEW.availability_bucket = 'available'\n          AND NEW.owner_org_id = source.owner_org_id AND NEW.owner_org_id = location.owner_org_id\n          AND NEW.location_id = location.id AND location.location_type = 'region' AND location.status = 'active'\n          AND NEW.custodian_person_id = inbound.operator_person_id\n          AND location.custodian_person_id = NEW.custodian_person_id\n          AND NEW.material_id = source.material_id AND NEW.material_id = line.material_id\n          AND NEW.condition_code = source.condition_code AND NEW.condition_code = line.condition_code\n          AND NEW.condition_code IN ('used', 'damaged')\n          AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id AND NEW.lot_id IS NOT DISTINCT FROM line.lot_id\n          AND custody.valid_from <= tx.effective_at\n          AND (custody.valid_to IS NULL OR custody.valid_to > inbound.created_at)\n          AND NEW.created_at = tx.effective_at AND NEW.updated_at = NEW.created_at\n          AND NEW.created_at >= established.established_at AND NEW.created_at >= receipt.created_at\n          AND NEW.created_at <= inbound.created_at AND inbound.created_at <= tx.created_at\n          AND balance.quantity = (\n              SELECT sum(initial.quantity) FROM public.inventory_movements initial\n              WHERE initial.transaction_id = tx.id AND initial.to_account_id = NEW.id)\n          AND NOT EXISTS (\n              SELECT 1 FROM public.inventory_movements other\n              JOIN public.inventory_transactions prior ON prior.id = other.transaction_id\n              WHERE (other.from_account_id = NEW.id OR other.to_account_id = NEW.id)\n                AND (prior.ledger_cursor < tx.ledger_cursor OR other.from_account_id = NEW.id))\n    ) THEN\n        RETURN NEW;\n    END IF;\n\n"


def _sources():
    prior = runpy.run_path(str(FOLDER / '20261124_0145_stock_loss_submission_proof.py'))
    old = prior['_sources']()[ACCOUNT_SIGNATURE][1]
    anchor = runpy.run_path(str(FOLDER / '20260928_0088_receipt_account_admission.py'))['ANCHOR']
    if old.count(anchor) != 1 or ACCOUNT_BRANCH in old:
        raise RuntimeError('0149 account source anchor drift')
    new = old.replace(anchor, ACCOUNT_BRANCH + anchor)
    if (hashlib.sha256(old.encode()).hexdigest() != ACCOUNT_OLD_HASH
            or hashlib.sha256(new.encode()).hexdigest() != ACCOUNT_NEW_HASH):
        raise RuntimeError('0149 canonical account source drift')
    return {ACCOUNT_SIGNATURE: (old, new)}


def _transition(up):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0149 requires PostgreSQL or SQLite')
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0149 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_accounts,public.inventory_transactions,public.inventory_movements,public.stock_balances,public.stock_operation_receipts,public.stock_operation_receipt_lines,public.stock_operation_return_inbounds,public.stock_operation_return_inbound_lines,public.stock_operation_return_inbound_postings IN SHARE ROW EXCLUSIVE MODE')
    if not up:
        helper['_preflight']('EXISTS(SELECT 1 FROM stock_operation_return_inbounds)',
            '0149 return inbound account admission history requires retention')
    if dialect == 'sqlite':
        return  # No production account-admission permission is introduced here.
    replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
    for signature, (old, new) in _sources().items():
        replace(signature=signature,
            expected_hash=hashlib.sha256((old if up else new).encode()).hexdigest(),
            replacement_hash=hashlib.sha256((new if up else old).encode()).hexdigest(),
            replacements=((old, new),) if up else ((new, old),), label='return_inbound_account_admission_0149')
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
        expected_hash=OLD_READY_HASH if up else NEW_READY_HASH, replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
        replacements=((down_revision, revision),) if up else ((revision, down_revision),),
        label='return_inbound_account_readiness_0149')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
