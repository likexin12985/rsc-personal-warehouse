"""Release unpicked reservation slices after other fulfillment has started.

The existing immutable quantity/SN graph stays enabled. Only the obsolete
whole-request stage gate changes, with the per-reservation picked cap added.
"""
from hashlib import sha256
import json
from pathlib import Path
import runpy
from alembic import context, op
from sqlalchemy import text

revision = '20261219_0170'
down_revision = '20261218_0169'
branch_labels = depends_on = None
RAW = (Path(__file__).parents[1] / 'partial_release_0170/functions.json').read_bytes()
if sha256(RAW).hexdigest() != '21bb7895960dc8e913fa0f9a3efbd6fed59d1628da81ffd70008b5c6a2304d87':
    raise ValueError('0170 frozen function catalog changed')
DATA = json.loads(RAW)
RETAINED = '''SELECT EXISTS(SELECT 1 FROM stock_reservation_releases release
    JOIN stock_reservation_picks pick ON pick.request_id=release.request_id
    WHERE pick.request_version<release.request_version)'''


def _sources():
    return {'public.'+signature:(row['before'],row['after']) for signature,row in DATA['functions'].items()}


def _transition(up):
    if context.is_offline_mode():
        raise ValueError('0170 online predecessor verification required')
    db=op.get_bind()
    if db.dialect.name=='sqlite':
        # Structural tooling only; no new tables or pretend PG16 stock guards.
        if not up and db.scalar(text(RETAINED)):
            raise ValueError('0170 downgrade blocked: partial release facts exist')
        return
    if db.dialect.name!='postgresql':
        raise ValueError('0170 requires PostgreSQL16 or SQLite schema tooling')
    identity=db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2]!=('star_oam_migrator','star_oam_migrator') or identity[2]//10000!=16 or identity[3]!='read committed':
        raise ValueError('0170 direct PostgreSQL16 read-committed migrator required')
    if any(db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()):
        raise ValueError('0170 unprivileged migrator required')
    db.execute(text('LOCK TABLE public.alembic_version, public.material_requests, public.stock_reservations, '
        'public.stock_reservation_releases, public.stock_reservation_picks, public.material_request_commands '
        'IN SHARE ROW EXCLUSIVE MODE'))
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all()!=[down_revision if up else revision]:
        raise ValueError('0170 exact predecessor required')
    if not up and db.scalar(text(RETAINED)):
        raise ValueError('0170 downgrade blocked: partial release facts exist')
    old,new=('before','after') if up else ('after','before')
    for signature,row in DATA['functions'].items():
        actual=db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:name)'),{'name':'public.'+signature})
        if actual!=row[old]:
            raise ValueError('0170 predecessor function drift: '+signature)
    helper=runpy.run_path(str(Path(__file__).with_name('20260909_0069_stock_reservations.py')))
    for signature,row in DATA['functions'].items():
        helper['_replace_function_source'](signature='public.'+signature,
            expected_hash=row[old+'Sha256'],replacement_hash=row[new+'Sha256'],
            replacements=((row[old],row[new]),),label='partial_release_0170')
    # Original global graph still binds approval, original reservations,
    # releases, picks, inventory transactions, serials and their audit records.
    db.execute(text('SELECT public.rsc_validate_picking_graph_0071(request_id) '
                    'FROM public.stock_reservations GROUP BY request_id'))


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
