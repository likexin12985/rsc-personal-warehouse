"""Preserve old inbound requests and post new damage-aware receipt partitions.

Development candidate. Install its frozen catalog beside the migration before
registering; native migration, runtime catalog and release proof are mandatory.
"""
from functools import cache
from pathlib import Path
import hashlib
import json
import runpy

import sqlalchemy as sa
from alembic import context, op

revision = '20261211_0162'
down_revision = '20261210_0161'
branch_labels = depends_on = None
FOLDER = Path(__file__).parent
CATALOG_SHA = '64e499dd5d521e3e835de21bcecceab34ae7def5515af416c69f3cce9dcc29bc'
TABLE = 'stock_operation_return_inbound_lines'
OLD_UNIQUE = 'uq_stock_operation_return_inbound_lines_origin'
NEW_UNIQUE = 'uq_stock_operation_return_inbound_lines_condition'
GUARD = 'rsc_require_return_inbound_quality_0162'
TRIGGER = 'trg_return_inbound_quality_0162'


@cache
def catalog():
    raw = (FOLDER.parent/'return_inbound_quality_0162/catalog.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA:
        raise RuntimeError('0162 frozen function catalog digest mismatch')
    result = json.loads(raw)
    if (result['previousRevision'],result['revision']) != (down_revision,revision):
        raise RuntimeError('0162 exact forward revision pair required')
    return result


def _sources():
    return {key:(row['before'],row['after']) for key,row in catalog()['patches'].items()}


def _unique(db, *, upgraded):
    expected = NEW_UNIQUE if upgraded else OLD_UNIQUE
    unwanted = OLD_UNIQUE if upgraded else NEW_UNIQUE
    columns = ['inbound_id','receipt_line_id'] + (['condition_code'] if upgraded else [])
    constraints = sa.inspect(db).get_unique_constraints(TABLE)
    matching = [row for row in constraints if row['name'] == expected]
    if len(matching) != 1 or matching[0]['column_names'] != columns or any(row['name']==unwanted for row in constraints):
        raise RuntimeError('0162 exact inbound origin uniqueness required')


def _schema(db, *, up):
    _unique(db,upgraded=not up)
    saved = ()
    if db.dialect.name == 'sqlite':
        if db.exec_driver_sql('PRAGMA foreign_keys').scalar():
            raise RuntimeError('0162 SQLite tooling requires foreign_keys OFF before migration')
        saved = tuple(db.execute(sa.text("SELECT name,sql FROM sqlite_master WHERE type='trigger' "
            "AND instr(lower(sql),'stock_operation_return_inbound_lines')>0 ORDER BY name")))
        for name,_ in saved:
            db.exec_driver_sql('DROP TRIGGER '+db.dialect.identifier_preparer.quote(name))
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(OLD_UNIQUE if up else NEW_UNIQUE,type_='unique')
        batch.create_unique_constraint(NEW_UNIQUE if up else OLD_UNIQUE,
            ['inbound_id','receipt_line_id']+(['condition_code'] if up else []))
    for _,sql in saved:
        db.exec_driver_sql(sql)
    _unique(db,upgraded=up)


def _native_guard(db, *, exists):
    row = db.execute(sa.text("""SELECT p.prosrc,p.prosecdef,p.provolatile,p.proconfig,
        pg_get_userbyid(p.proowner) AS owner,p.prorettype::regtype::text AS result,
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.grantee<>p.proowner) AS foreign_grant
        FROM pg_proc p WHERE p.oid=to_regprocedure('public.rsc_require_return_inbound_quality_0162()')""")).mappings().one_or_none()
    triggers = tuple(db.execute(sa.text("""SELECT t.tgenabled,t.tgtype::int,t.tgdeferrable,t.tginitdeferred,
        t.tgnargs,t.tgqual IS NULL,t.tgattr=''::int2vector,t.tgconstraint=0,
        t.tgfoid=to_regprocedure('public.rsc_require_return_inbound_quality_0162()')
        FROM pg_trigger t WHERE t.tgrelid='public.stock_operation_return_inbounds'::regclass
          AND t.tgname='trg_return_inbound_quality_0162' AND NOT t.tgisinternal""")))
    if not exists:
        if row is not None or triggers:
            raise RuntimeError('0162 unexpected predecessor guard')
        return
    expected = dict(prosrc=catalog()['newFactGuard'],prosecdef=True,provolatile='v',
        proconfig=['search_path=pg_catalog'],owner='star_oam_migrator',result='trigger',foreign_grant=False)
    if row is None or dict(row)!=expected or [tuple(r) for r in triggers]!=[('A',7,False,False,0,True,True,True,True)]:
        raise RuntimeError('0162 exact private new-fact guard required')


def _transition(*, up):
    if context.is_offline_mode():
        raise RuntimeError('0162 requires online history and function evidence')
    helper = runpy.run_path(str(FOLDER/'20260927_0087_inbound_fulfillment_boundary.py'))
    helper['_begin_sqlite']()
    db = op.get_bind()
    if db.dialect.name not in ('postgresql','sqlite'):
        raise RuntimeError('0162 requires PostgreSQL16 or SQLite tooling')
    if db.dialect.name == 'postgresql':
        op.execute("DO $$ BEGIN IF current_user<>'star_oam_migrator' OR session_user<>'star_oam_migrator' "
            "THEN RAISE EXCEPTION '0162 direct schema owner required'; END IF; END $$")
        op.execute('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE')
        op.execute('LOCK TABLE public.inventory_ledger_heads,public.stock_accounts,public.inventory_transactions,'
            'public.inventory_movements,public.stock_balances,public.stock_operation_receipts,'
            'public.stock_operation_receipt_lines,public.stock_operation_receipt_serials,'
            'public.stock_operation_return_inbounds,public.stock_operation_return_inbound_lines,'
            'public.stock_operation_return_inbound_serials,public.stock_operation_return_inbound_postings '
            'IN SHARE ROW EXCLUSIVE MODE')
        _native_guard(db,exists=not up)
    if not up:
        helper['_preflight']("EXISTS(SELECT 1 FROM stock_operation_return_inbounds "
            "WHERE plan_jsonb->>'schema_version' IS DISTINCT FROM '1.0')",
            '0162 immutable condition-aware inbound history requires retention')
    _schema(db,up=up)
    if db.dialect.name == 'sqlite':
        if up:
            op.execute("CREATE TRIGGER trg_return_inbound_quality_0162 BEFORE INSERT ON stock_operation_return_inbounds "
                "BEGIN SELECT RAISE(ABORT,'0162 PostgreSQL condition-aware inbound proof required'); END")
        else:
            op.execute('DROP TRIGGER trg_return_inbound_quality_0162')
        return
    replace = runpy.run_path(str(FOLDER/'20260909_0069_stock_reservations.py'))['_replace_function_source']
    for signature,row in catalog()['patches'].items():
        old,new = row['before'],row['after']
        replace(signature=signature,expected_hash=row['beforeSha256' if up else 'afterSha256'],
            replacement_hash=row['afterSha256' if up else 'beforeSha256'],
            replacements=((old,new),) if up else ((new,old),),label='return_inbound_quality_0162')
    if up:
        op.execute('CREATE FUNCTION public.'+GUARD+'() RETURNS trigger LANGUAGE plpgsql VOLATILE '
            'SECURITY DEFINER SET search_path=pg_catalog AS $body$'+catalog()['newFactGuard']+'$body$')
        op.execute('REVOKE ALL ON FUNCTION public.'+GUARD+'() FROM PUBLIC,star_oam_api,star_oam_backup,star_oam_projector,edge_inbox')
        op.execute('CREATE TRIGGER '+TRIGGER+' BEFORE INSERT ON public.stock_operation_return_inbounds '
            'FOR EACH ROW EXECUTE FUNCTION public.'+GUARD+'()')
        op.execute('ALTER TABLE public.stock_operation_return_inbounds ENABLE ALWAYS TRIGGER '+TRIGGER)
    else:
        op.execute('DROP TRIGGER '+TRIGGER+' ON public.stock_operation_return_inbounds')
        op.execute('DROP FUNCTION public.'+GUARD+'()')
    _native_guard(db,exists=up)


def upgrade():
    _transition(up=True)


def downgrade():
    _transition(up=False)
