"""Keep independent audit streams independent of inventory locks.

No stock, request, audit or session rows are rewritten. The previous exact
function and ACL must match before either direction replaces its body.
"""
from functools import cache
from pathlib import Path
import hashlib
import json

from alembic import context, op
from sqlalchemy import text

revision = '20261215_0166'
down_revision = '20261214_0165'
branch_labels = depends_on = None
FOLDER = Path(__file__).parents[1] / 'scrap_authentication_fence_0166'
CATALOG_SHA = '412639a1b8a3f77ad071f959e135981ea3da5f9887d2752771a3ec36c4cacb43'


@cache
def _catalog():
    raw = (FOLDER / 'catalog.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != CATALOG_SHA:
        raise ValueError('0166 frozen authentication fence catalog changed')
    return json.loads(raw)


def _sources():
    return {'public.' + p['before']['signature']: (p['before']['prosrc'], p['after']['prosrc'])
            for p in _catalog()['patches']}


def _verify(db, expected):
    if db.scalar(text("SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname=:name"),
                 {'name': expected['proname']}) != 1:
        raise ValueError('0166 missing function or unexpected overload')
    row = db.execute(text('''
        SELECT pg_get_functiondef(p.oid) AS definition, p.proname, p.prosrc,
               p.prosecdef, p.provolatile, p.proparallel, p.proisstrict,
               p.proleakproof, p.proconfig, pg_get_userbyid(p.proowner) AS owner,
               pg_get_function_identity_arguments(p.oid) AS identity_arguments,
               ARRAY(SELECT jsonb_build_object(
                   'grantee', CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                   'privilege', a.privilege_type, 'grantable', a.is_grantable)
                   FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                   ORDER BY a.grantee,a.privilege_type) AS acl
          FROM pg_proc p WHERE p.oid=to_regprocedure(:signature)
    '''), {'signature': 'public.' + expected['signature']}).mappings().one_or_none()
    if row is None:
        raise ValueError('0166 missing predecessor function')
    actual = dict(row)
    wanted = {key: expected[key] for key in actual}
    for item in (actual, wanted):
        item['acl'] = sorted(item['acl'], key=lambda a: (a['grantee'], a['privilege'], a['grantable']))
    if actual != wanted:
        raise ValueError('0166 exact function source, ownership or ACL drift')


def _transition(*, up):
    data = _catalog()
    if context.is_offline_mode():
        raise RuntimeError('0166 online predecessor verification required')
    db = op.get_bind()
    if db.dialect.name == 'sqlite':
        # Schema tooling only. Authentication writes remain PostgreSQL-owned.
        return
    if db.dialect.name != 'postgresql':
        raise RuntimeError('0166 requires PostgreSQL16 or SQLite schema tooling')
    role = db.execute(text('''SELECT current_user, session_user,
        current_setting('server_version_num')::int / 10000,
        current_setting('transaction_isolation'), r.rolsuper, r.rolcreatedb,
        r.rolcreaterole, r.rolreplication, r.rolbypassrls
        FROM pg_roles r WHERE r.rolname=current_user''')).one()
    if tuple(role) != ('star_oam_migrator', 'star_oam_migrator', 16, 'read committed',
                       False, False, False, False, False):
        raise ValueError('0166 direct non-superuser PG16 migrator required')
    expected_revision = down_revision if up else revision
    if tuple(db.scalars(text('SELECT version_num FROM public.alembic_version'))) != (expected_revision,):
        raise ValueError('0166 exact revision predecessor required')
    tables = sorted({row['table_name'] for row in data['triggers']} | {'inventory_ledger_heads'})
    quote = db.dialect.identifier_preparer.quote
    db.execute(text('LOCK TABLE ' + ', '.join('public.' + quote(name) for name in tables)
                    + ' IN SHARE ROW EXCLUSIVE MODE'))
    actual_triggers = [dict(row) for row in db.execute(text('''
        SELECT n.nspname AS schema_name, c.relname AS table_name, t.tgname AS name, pg_get_triggerdef(t.oid) AS definition,
               t.tgenabled, t.tgtype, t.tgdeferrable, t.tginitdeferred,
               p.proname || '(' || oidvectortypes(p.proargtypes) || ')' AS function_signature
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE p.oid='public.rsc_fence_scrap_seals_0165()'::regprocedure
        ORDER BY n.nspname,c.relname,t.tgname
    ''')).mappings()]
    expected_triggers = [dict(schema_name='public', **row) for row in data['triggers']]
    if actual_triggers != sorted(expected_triggers, key=lambda row:(row['schema_name'],row['table_name'],row['name'])):
        raise ValueError('0166 exact retained seal trigger graph required')
    before, after = ('before', 'after') if up else ('after', 'before')
    for patch in data['patches']:
        _verify(db, patch[before])
    for patch in data['patches']:
        db.execute(text(patch[after]['definition']))
    for patch in data['patches']:
        _verify(db, patch[after])


def upgrade():
    _transition(up=True)


def downgrade():
    _transition(up=False)
