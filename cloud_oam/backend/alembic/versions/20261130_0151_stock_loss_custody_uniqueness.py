"""Count every current custodian before posting an approved loss disposition.

Historical proof serialization and existing grants are unchanged. A finite
assignment for another person must not hide behind the open-ended unique index.
"""
from pathlib import Path
import hashlib
import runpy

from alembic import op

revision = '20261130_0151'
down_revision = '20261129_0150'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
previous = runpy.run_path(str(FOLDER / '20261129_0150_stock_loss_disposition.py'))
SIGNATURE = 'public.rsc_check_loss_disposition_0150(uuid, boolean)'
OLD_BODY = previous['CHECK_BODY']
OLD_FRAGMENT = 'AND c.custodian_person_id=source.custodian_person_id AND c.valid_from<=clock_timestamp()'
NEW_FRAGMENT = 'AND c.valid_from<=clock_timestamp()'
if OLD_BODY.count(OLD_FRAGMENT) != 1:
    raise RuntimeError('0151 canonical custody source anchor drift')
NEW_BODY = OLD_BODY.replace(OLD_FRAGMENT, NEW_FRAGMENT)
CHECK_OLD_HASH = '8e3c9c0c5e1093e9428dcc8db9aa1deebf06d4ae1515c22c36a85fd84318dd02'
CHECK_NEW_HASH = '6186d5278a90e85e5cf45cd260e117b7d4a2c5e6875cd2fdb28e6983a2138113'
if (hashlib.sha256(OLD_BODY.encode()).hexdigest() != CHECK_OLD_HASH
        or hashlib.sha256(NEW_BODY.encode()).hexdigest() != CHECK_NEW_HASH):
    raise RuntimeError('0151 canonical custody source digest drift')
OLD_READY_HASH = previous['NEW_READY_HASH']
ready = previous['ready']
NEW_READY_HASH = hashlib.sha256(ready['_ready'].replace(ready['_ready_parent'], revision).encode()).hexdigest()


def _transition(up):
    helper = runpy.run_path(str(FOLDER / '20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    dialect = op.get_bind().dialect.name
    if dialect not in ('postgresql', 'sqlite'):
        raise RuntimeError('0151 requires PostgreSQL or SQLite')
    if dialect == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' THEN RAISE EXCEPTION '0151 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_locations,public.custody_assignments,public.stock_loss_dispositions IN SHARE ROW EXCLUSIVE MODE')
    if not up:
        helper['_preflight']('EXISTS(SELECT 1 FROM stock_loss_dispositions)',
            '0151 disposition custody proof history requires retention')
    if dialect == 'sqlite':
        return  # The existing SQLite insertion blocker remains in force.
    verify = previous['hq']['previous']['previous']['_verify_function']
    verify('rsc_check_loss_disposition_0150', 'uuid, boolean', 'checked_fact uuid, require_current boolean',
        'void', OLD_BODY if up else NEW_BODY)
    replace = runpy.run_path(str(FOLDER / '20260909_0069_stock_reservations.py'))['_replace_function_source']
    replace(signature=SIGNATURE, expected_hash=CHECK_OLD_HASH if up else CHECK_NEW_HASH,
        replacement_hash=CHECK_NEW_HASH if up else CHECK_OLD_HASH,
        replacements=((OLD_BODY, NEW_BODY),) if up else ((NEW_BODY, OLD_BODY),),
        label='loss_disposition_custody_0151')
    verify('rsc_check_loss_disposition_0150', 'uuid, boolean', 'checked_fact uuid, require_current boolean',
        'void', NEW_BODY if up else OLD_BODY)
    replace(signature='public.rsc_oam_runtime_binding_ready_0044()',
        expected_hash=OLD_READY_HASH if up else NEW_READY_HASH,
        replacement_hash=NEW_READY_HASH if up else OLD_READY_HASH,
        replacements=((down_revision, revision),) if up else ((revision, down_revision),),
        label='loss_disposition_custody_ready_0151')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
