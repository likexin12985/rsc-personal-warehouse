"""Frozen, exact 0166-to-0167 readiness function transition.

The stock transition owns the transaction, role/version checks and locks.
Only the pinned revision literal changes; ownership and ACL are preserved.
"""
from hashlib import sha256
import json
from pathlib import Path
from sqlalchemy import text

RAW = Path(__file__).with_suffix('.json').read_bytes()
if sha256(RAW).hexdigest() != '2432894c13542bdc64180038cb791217edab45d5b7b40f143f09127732420bfb':
    raise ValueError('0167 frozen readiness catalog digest mismatch')
DATA = json.loads(RAW)


def verify(db, side):
    expected = DATA[side]
    rows = db.execute(text('''
        SELECT p.proname || '(' || oidvectortypes(p.proargtypes) || ')' AS signature,
               pg_get_functiondef(p.oid) AS definition, p.proname, p.prosrc,
               p.prosecdef, p.provolatile, p.proparallel, p.proisstrict,
               p.proleakproof, p.proconfig, pg_get_userbyid(p.proowner) AS owner,
               pg_get_function_identity_arguments(p.oid) AS identity_arguments,
               ARRAY(SELECT jsonb_build_object(
                   'grantee', CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,
                   'privilege', a.privilege_type, 'grantable', a.is_grantable)
                   FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
                   ORDER BY a.grantee,a.privilege_type) AS acl
          FROM pg_proc p WHERE p.pronamespace='public'::regnamespace AND p.proname=:name
    '''), {'name': expected['proname']}).mappings().all()
    if len(rows) != 1:
        raise ValueError('0167 exact readiness function without overloads required')
    actual, wanted = dict(rows[0]), dict(expected)
    for row in (actual, wanted):
        row['acl'] = sorted(row['acl'], key=lambda a: (a['grantee'], a['privilege'], a['grantable']))
    if actual != wanted:
        raise ValueError('0167 readiness source, ownership or ACL drift')


def replace(db, *, up):
    before, after = ('before', 'after') if up else ('after', 'before')
    verify(db, before)
    db.execute(text(DATA[after]['definition']))
    verify(db, after)
