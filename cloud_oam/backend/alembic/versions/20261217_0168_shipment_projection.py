"""Project proven carrier handovers without rewriting original command results.

Under table locks the migration repairs only the derived shipment column.
Versions, timestamps, immutable commands, inventory and audit facts stay intact.
The temporary migration-only guard cannot survive the transaction boundary.
"""
from hashlib import sha256
import json
from pathlib import Path
import runpy

from alembic import context, op
from sqlalchemy import text

revision = '20261217_0168'
down_revision = '20261216_0167'
branch_labels = depends_on = None
FOLDER = Path(__file__).parents[1] / 'shipment_projection_0168'
RAW = (FOLDER / 'functions.json').read_bytes()
if sha256(RAW).hexdigest() != '4d4507caf3377f8a3f57b490d24b333a8dbb5dc36d4badc73eee4e25f9d1cc0e':
    raise ValueError('0168 frozen function catalog changed')
DATA = json.loads(RAW)['functions']
SQL = runpy.run_path(str(FOLDER / 'projection.py'))
GUARD = 'rsc_guard_material_request_identity_0029()'
READY = 'rsc_oam_runtime_binding_ready_0044()'
SUPPLY = 'rsc_validate_material_request_supply_causality_0059(uuid, bigint)'


def _sources():
    return {'public.' + key: (row['before'], row['after']) for key, row in DATA.items()}


def _replace(signature, before, after):
    helper = runpy.run_path(str(Path(__file__).with_name('20260909_0069_stock_reservations.py')))
    helper['_replace_function_source'](
        signature='public.' + signature, expected_hash=sha256(before.encode()).hexdigest(),
        replacement_hash=sha256(after.encode()).hexdigest(), replacements=((before, after),),
        label='shipment_0168')


def _postgresql(db, up):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16:
        raise ValueError('0168 direct PostgreSQL16 migrator required')
    if any(db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                           'FROM pg_roles WHERE rolname=current_user')).one()):
        raise ValueError('0168 unprivileged migrator required')
    db.execute(text('LOCK TABLE public.alembic_version, public.material_requests, '
        'public.material_request_commands, public.shipments, public.shipment_lines, '
        'public.outbound_postings IN SHARE ROW EXCLUSIVE MODE'))
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != [down_revision if up else revision]:
        raise ValueError('0168 exact predecessor required')
    side = 'before' if up else 'after'
    for signature, row in DATA.items():
        actual = db.scalar(text('SELECT prosrc FROM pg_proc WHERE oid=to_regprocedure(:signature)'),
                           {'signature': 'public.' + signature})
        if actual != row[side]:
            raise ValueError('0168 predecessor function drift: ' + signature)
    if not up:
        if db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.material_requests WHERE shipment_status <> 'not_started')")):
            raise ValueError('0168 downgrade blocked: shipment projection facts exist')
        for signature, row in DATA.items():
            _replace(signature, row['after'], row['before'])
        db.execute(text('REVOKE UPDATE (shipment_status) ON public.material_requests FROM star_oam_api'))
        return
    # Validate retained history with the predecessor's complete command/audit
    # validator before changing its projection. No guessed or orphan package
    # can acquire a shipped read state through the data repair.
    db.execute(text('SELECT public.rsc_validate_material_request_approval_projection_0045(id) '
                    'FROM public.material_requests'))
    before = DATA[GUARD]['before']
    bypass = f"""
    IF TG_OP = 'UPDATE' AND session_user = 'star_oam_migrator'
       AND OLD.shipment_status = 'not_started' AND NEW.shipment_status = 'shipped'
       AND (to_jsonb(NEW) - 'shipment_status') = (to_jsonb(OLD) - 'shipment_status')
       AND {SQL['evidence_sql']()} THEN
        RETURN NEW;
    END IF;
"""
    temporary = before.replace('\nBEGIN\n', '\nBEGIN\n' + bypass, 1)
    _replace(GUARD, before, temporary)
    _replace(SUPPLY, DATA[SUPPLY]['before'], DATA[SUPPLY]['after'])
    db.execute(text("UPDATE public.material_requests AS request_row SET shipment_status='shipped' "
        "WHERE shipment_status='not_started' AND " + SQL['evidence_sql'](request='request_row')))
    _replace(GUARD, temporary, DATA[GUARD]['after'])
    _replace(READY, DATA[READY]['before'], DATA[READY]['after'])
    db.execute(text('GRANT UPDATE (shipment_status) ON public.material_requests TO star_oam_api'))
    db.execute(text('SELECT public.rsc_validate_material_request_approval_projection_0045(id) '
                    'FROM public.material_requests'))
    db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    db.execute(text('SET CONSTRAINTS ALL DEFERRED'))


def _sqlite(db, up):
    name = 'trg_material_requests_update_guard_0029'
    source = db.exec_driver_sql('SELECT sql FROM sqlite_master WHERE name=?', (name,)).scalar_one()
    before = "  OR NEW.shipment_status <> 'not_started'"
    after = f"""  OR NEW.shipment_status IS NOT ({SQL['state_sql'](schema='')})
  OR (NEW.shipment_status IS NOT OLD.shipment_status AND (
      NEW.version <> OLD.version + 1 OR NEW.updated_at <= OLD.updated_at
      OR NOT {SQL['evidence_sql'](schema='', current_command=True)}))"""
    old, new = (before, after) if up else (after, before)
    if source.count(old) != 1:
        raise ValueError('0168 exact SQLite predecessor guard required')
    if not up and db.scalar(text("SELECT EXISTS(SELECT 1 FROM material_requests WHERE shipment_status <> 'not_started')")):
        raise ValueError('0168 downgrade blocked: shipment projection facts exist')
    if up:
        columns = [row[1] for row in db.exec_driver_sql('PRAGMA table_info(material_requests)')]
        unchanged = ' AND '.join(f'NEW."{column}" IS OLD."{column}"' for column in columns if column != 'shipment_status')
        bypass = f"OLD.shipment_status='not_started' AND NEW.shipment_status='shipped' AND {unchanged} AND {SQL['evidence_sql'](schema='')}"
        prefix, condition = source.split('\nWHEN ', 1)
        condition, body = condition.split('\nBEGIN', 1)
        temporary = prefix + '\nWHEN NOT (' + bypass + ') AND (' + condition + ')\nBEGIN' + body
        db.exec_driver_sql('DROP TRIGGER ' + name)
        db.exec_driver_sql(temporary)
        db.execute(text("UPDATE material_requests AS request_row SET shipment_status='shipped' "
            "WHERE shipment_status='not_started' AND " + SQL['evidence_sql'](schema='', request='request_row')))
    db.exec_driver_sql('DROP TRIGGER ' + name)
    db.exec_driver_sql(source.replace(old, new))


def _transition(up):
    if context.is_offline_mode():
        raise ValueError('0168 online history verification required')
    db = op.get_bind()
    if db.dialect.name == 'postgresql':
        _postgresql(db, up)
    elif db.dialect.name == 'sqlite':
        _sqlite(db, up)
    else:
        raise ValueError('0168 requires PostgreSQL16 or SQLite schema tooling')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
