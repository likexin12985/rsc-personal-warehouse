"""Bound new supply plans by historically proved remaining demand."""
from hashlib import sha256
import json
from pathlib import Path
import runpy
from alembic import context, op
from sqlalchemy import text

revision = '20261227_0178'
down_revision = '20261226_0177'
branch_labels = depends_on = None
RAW = (Path(__file__).parents[1] / 'supply_capacity_0178/functions.json').read_bytes()
if sha256(RAW).hexdigest() != 'e23e5756863b12ecbadacb6f673ebeedf1f5c303883d2fb04997f00086aa22cb':
    raise ValueError('0178 frozen function catalog changed')
DATA = json.loads(RAW)
RETAINED = '''SELECT EXISTS(SELECT 1 FROM material_request_commands command
    JOIN stock_allocations allocation ON allocation.request_id=command.request_id
    WHERE command.operation = 'create_supply_task'
      AND allocation.request_version<command.target_version)'''


def _sources():
    return {'public.'+signature:(row['before'],row['after']) for signature,row in DATA['functions'].items()}


def _transition(up):
    if context.is_offline_mode():
        raise ValueError('0178 online predecessor verification required')
    db=op.get_bind()
    if db.dialect.name=='sqlite':
        # Structural tooling only; no new tables or pretend PG16 stock guards.
        if not up and db.scalar(text(RETAINED)):
            raise ValueError('0178 downgrade blocked: late supply creation facts exist')
        return
    if db.dialect.name!='postgresql':
        raise ValueError('0178 requires PostgreSQL16 or SQLite schema tooling')
    identity=db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,current_setting('transaction_isolation')")).one()
    if identity[:2]!=('star_oam_migrator','star_oam_migrator') or identity[2]//10000!=16 or identity[3]!='read committed':
        raise ValueError('0178 direct PostgreSQL16 read-committed migrator required')
    if any(db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).one()):
        raise ValueError('0178 unprivileged migrator required')
    db.execute(text('LOCK TABLE public.alembic_version, public.material_requests, public.stock_allocations, '
        'public.stock_allocation_serials, public.supply_tasks, public.audit_events, '
        'public.state_transition_events, public.material_request_commands '
        'IN SHARE ROW EXCLUSIVE MODE'))
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all()!=[down_revision if up else revision]:
        raise ValueError('0178 exact predecessor required')
    if not up and db.scalar(text(RETAINED)):
        raise ValueError('0178 downgrade blocked: late supply creation facts exist')
    old,new=('before','after') if up else ('after','before')
    for signature,row in DATA['functions'].items():
        actual=db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:name)'),{'name':'public.'+signature})
        if actual!=row[old]:
            raise ValueError('0178 predecessor function drift: '+signature)
    helper=runpy.run_path(str(Path(__file__).with_name('20260909_0069_stock_reservations.py')))
    for signature,row in DATA['functions'].items():
        helper['_replace_function_source'](signature='public.'+signature,
            expected_hash=row[old+'Sha256'],replacement_hash=row[new+'Sha256'],
            replacements=((row[old],row[new]),),label='supply_capacity_0178')
    # Revalidate all existing requests with plans using the unchanged approval
    # graph and the newly installed supply validator in the same transaction.
    db.execute(text('SELECT public.rsc_validate_material_request_approval_projection_0045(line.request_id) '
                    'FROM public.supply_tasks task JOIN public.material_request_lines line '
                    'ON line.id=task.request_line_id GROUP BY line.request_id'))


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
