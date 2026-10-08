"""Keep typed return receipts outside material-request closure routing."""
from hashlib import sha256
import json
from pathlib import Path
import runpy
from alembic import context, op
from sqlalchemy import text

revision = '20261228_0179'
down_revision = '20261227_0178'
branch_labels = depends_on = None
RAW = (Path(__file__).parents[1] / 'return_receipt_routing_0179/functions.json').read_bytes()
if sha256(RAW).hexdigest() != '13c8d63deb452a759537b320e6f242f24e891adf035caf83e5a2998d1ed0fe02':
    raise ValueError('0179 frozen function catalog changed')
DATA = json.loads(RAW)



def _sources():
    return {'public.'+signature:(row['before'],row['after']) for signature,row in DATA['functions'].items()}


def _transition(up):
    if context.is_offline_mode():
        raise ValueError('0179 online predecessor verification required')
    db=op.get_bind()
    if db.dialect.name=='sqlite':
        # Structural tooling only; no new tables or pretend PG16 stock guards.
        return
    if db.dialect.name!='postgresql':
        raise ValueError('0179 requires PostgreSQL16 or SQLite schema tooling')
    identity=db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2]!=('star_oam_migrator','star_oam_migrator') or identity[2]//10000!=16 or identity[3]!='read committed':
        raise ValueError('0179 direct PostgreSQL16 read-committed migrator required')
    if any(db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()):
        raise ValueError('0179 unprivileged migrator required')
    db.execute(text('LOCK TABLE public.alembic_version, public.material_requests, public.receipts, '
        'public.shipments, public.shipment_lines, public.stock_operation_shipments, '
        'public.stock_operation_receipts IN SHARE ROW EXCLUSIVE MODE'))
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all()!=[down_revision if up else revision]:
        raise ValueError('0179 exact predecessor required')
    # The narrow header route relies on the existing independent COMMIT proof.
    if db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgrelid='public.receipts'::regclass "
        "AND tgname='trg_receipts_return_receipt_0105' AND tgenabled='A' AND tgtype=5 "
        "AND tgdeferrable AND tginitdeferred "
        "AND tgfoid='public.rsc_dispatch_stock_return_receipt_0105()'::regprocedure")) != 1:
        raise ValueError('0179 typed receipt COMMIT guard required')
    old,new=('before','after') if up else ('after','before')
    for signature,row in DATA['functions'].items():
        actual=db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:name)'),{'name':'public.'+signature})
        if actual!=row[old]:
            raise ValueError('0179 predecessor function drift: '+signature)
    helper=runpy.run_path(str(Path(__file__).with_name('20260909_0069_stock_reservations.py')))
    for signature,row in DATA['functions'].items():
        helper['_replace_function_source'](signature='public.'+signature,
            expected_hash=row[old+'Sha256'],replacement_hash=row[new+'Sha256'],
            replacements=((row[old],row[new]),),label='return_receipt_routing_0179')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
