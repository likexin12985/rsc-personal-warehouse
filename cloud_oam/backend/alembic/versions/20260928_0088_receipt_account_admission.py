"""Admit a new personal stock dimension only with its accepted inbound fact."""
from pathlib import Path
import hashlib
import runpy

from alembic import op

revision = "20260928_0088"
down_revision = "20260927_0087"
branch_labels = depends_on = None
OLD_HASH = "1c8de32ac17d4db5e3a12049d0b2e4806a8fbb1bb94fa5248729b0a01e58d34a"
NEW_HASH = "f96331a32393d71f3d9613936232ce3486981ad4ff78ed783d355e491eb11c99"
ACCOUNT_OLD_HASH = "c0079cfdaf15a4e9f9b66d76596828c0901b322d32ff4ea63fbbadc3acff163b"
ACCOUNT_NEW_HASH = "e3de9261d8947e854622ba2605be5575727d4a9e6c24e784ace79749f078e11c"
ANCHOR = "    IF NOT EXISTS (\n        SELECT 1\n          FROM public.inventory_movements AS movement"
ACCOUNT_BRANCH = """    IF EXISTS (
        SELECT 1 FROM public.inbound_postings posting
        JOIN public.inbound_orders inbound ON inbound.id = posting.inbound_order_id
        JOIN public.receipts receipt ON receipt.id = inbound.receipt_id
        JOIN public.shipments shipment ON shipment.id = receipt.shipment_id
        JOIN public.inventory_transactions tx ON tx.id = posting.inventory_transaction_id
        JOIN public.inventory_movements movement ON movement.transaction_id = tx.id AND movement.to_account_id = NEW.id
        JOIN public.stock_accounts source ON source.id = movement.from_account_id
        JOIN public.stock_balances balance ON balance.stock_account_id = NEW.id
          AND balance.version = 1 AND balance.ledger_cursor = tx.ledger_cursor
        JOIN public.stock_locations location ON location.id = NEW.location_id
        JOIN public.inventory_opening_establishments established ON established.owner_org_id = NEW.owner_org_id
          AND established.location_id = NEW.location_id
        WHERE tx.status = 'posted' AND tx.movement_type = 'transfer'
          AND tx.source_document_type = 'personal_inbound' AND tx.source_document_id = inbound.id::text
          AND tx.posting_key = 'personal-inbound:' || inbound.id::text
          AND inbound.status = 'pending' AND inbound.posting_transaction_id IS NULL
          AND receipt.status IN ('accepted', 'exception')
          AND receipt.receiver_person_id = shipment.target_person_id
          AND NEW.location_id = inbound.target_location_id AND NEW.location_id = shipment.target_location_id
          AND NEW.custodian_person_id = inbound.target_person_id AND NEW.custodian_person_id = shipment.target_person_id
          AND location.location_type = 'personal' AND location.status = 'active'
          AND location.custodian_person_id = NEW.custodian_person_id
          AND NEW.availability_bucket = 'available' AND source.availability_bucket = 'in_transit'
          AND NEW.owner_org_id = source.owner_org_id AND NEW.material_id = source.material_id
          AND NEW.condition_code = source.condition_code AND NEW.lot_id IS NOT DISTINCT FROM source.lot_id
          AND NEW.created_at >= receipt.created_at AND NEW.created_at <= tx.created_at
          AND NEW.updated_at = NEW.created_at AND established.established_at <= NEW.created_at
          AND balance.quantity = (SELECT sum(initial.quantity) FROM public.inventory_movements initial
            WHERE initial.transaction_id = tx.id AND initial.to_account_id = NEW.id)
          AND EXISTS (SELECT 1 FROM public.receipt_lines accepted
            JOIN public.shipment_lines shipped ON shipped.id = accepted.shipment_line_id
            JOIN public.outbound_postings outbound ON outbound.id = shipped.outbound_posting_id
            WHERE accepted.receipt_id = receipt.id AND accepted.accepted_qty > 0
              AND accepted.condition IN ('normal', 'shortage') AND shipped.shipment_id = shipment.id
              AND outbound.target_stock_account_id = source.id)
          AND NOT EXISTS (SELECT 1 FROM public.inventory_movements earlier
            JOIN public.inventory_transactions previous ON previous.id = earlier.transaction_id
            WHERE (earlier.from_account_id = NEW.id OR earlier.to_account_id = NEW.id)
              AND previous.ledger_cursor < tx.ledger_cursor)
    ) THEN
        RETURN NEW;
    END IF;

"""


def _previous():
    return runpy.run_path(str(Path(__file__).with_name("20260927_0087_inbound_fulfillment_boundary.py")))


def _account_sources():
    directory = Path(__file__).parent
    observation = runpy.run_path(str(directory / "20260831_0023_opening_observation_posting.py"))
    terminal = runpy.run_path(str(directory / "20260903_0052_opening_terminal_guard_execution.py"))
    old = observation["_postgresql_account_function_sql"]().split("AS $$", 1)[1].rsplit("$$", 1)[0]
    old = old.replace(terminal["LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT"], terminal["FIXED_ACCOUNT_PRINCIPAL_FRAGMENT"])
    new = old.replace(ANCHOR, ACCOUNT_BRANCH + ANCHOR)
    if (old.count(ANCHOR) != 1 or hashlib.sha256(old.encode()).hexdigest() != ACCOUNT_OLD_HASH
            or hashlib.sha256(new.encode()).hexdigest() != ACCOUNT_NEW_HASH):
        raise RuntimeError("0088 canonical account admission source drift")
    # The inherited CAS forbids the replacement text occurring in the source.
    # Whole bodies are distinct in both directions; an insertion's suffix is not.
    return old, new


def _transition(upgrade):
    db = op.get_bind()
    if db.dialect.name not in {'postgresql', 'sqlite'}:
        raise RuntimeError('0088 supports only PostgreSQL and SQLite')
    prior = _previous()
    prior['_begin_sqlite']()
    if db.dialect.name == 'postgresql':
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.stock_accounts, public.inbound_postings IN SHARE ROW EXCLUSIVE MODE')
    if not upgrade:
        prior['_preflight']('EXISTS (SELECT 1 FROM inbound_postings)',
            '0088 downgrade blocked: inbound account admission facts require review')
    if db.dialect.name == 'postgresql':
        source = runpy.run_path(str(Path(__file__).with_name('20260912_0072_outbound_postings.py')))
        replace = source['_previous']()['_previous']()['_previous']()['_replace_function_source']
        account_old, account_new = _account_sources()
        replace(signature='public.rsc_require_opening_observation_account_0023()',
            expected_hash=ACCOUNT_OLD_HASH if upgrade else ACCOUNT_NEW_HASH,
            replacement_hash=ACCOUNT_NEW_HASH if upgrade else ACCOUNT_OLD_HASH,
            replacements=((account_old, account_new),) if upgrade else ((account_new, account_old),),
            label='receipt_account_admission_0088')
        replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
            expected_hash=OLD_HASH if upgrade else NEW_HASH,
            replacement_hash=NEW_HASH if upgrade else OLD_HASH,
            replacements=((down_revision, revision),) if upgrade else ((revision, down_revision),),
            label='receipt_account_readiness_0088')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
