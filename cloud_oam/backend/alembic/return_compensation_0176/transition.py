"""Frozen 0176 returned compensation transition with exact predecessor and retention checks.

Only literal, digest-checked SQL and catalog observations are used here.
No application metadata, candidate compiler or historical migration executes.
The caller owns one transaction; failed validation must roll it back.
"""
from pathlib import Path
import hashlib
import importlib.util
import json

from sqlalchemy import text

FOLDER = Path(__file__).resolve().parent
CATALOG_SHA = 'cee400aa0a6eecea00ef95cf22a3dd00f471a272b23ce5b860b165079e58d4b0'
PROBE_SHA = 'e29201954a9188b39a15d912fc61f9e737784b1f041d10447f49a27191e02a14'
raw = (FOLDER / 'catalog.json').read_bytes()
if hashlib.sha256(raw).hexdigest() != CATALOG_SHA:
    raise ValueError('0176 frozen catalog digest mismatch')
DATA = json.loads(raw)
if hashlib.sha256((FOLDER / 'catalog_probe.py').read_bytes()).hexdigest() != PROBE_SHA:
    raise ValueError('0176 frozen catalog probe digest mismatch')
spec = importlib.util.spec_from_file_location('return_compensation_0176_catalog_probe', FOLDER / 'catalog_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
TABLES = tuple(name for name, row in DATA['tables'].items() if row['before'] is None)
PREVIOUS = DATA['previousRevision']
# Original registration/progress columns and historical rows remain untouched.


def quote(value):
    if not value or not value.replace('_', '').isalnum() or value[0].isdigit():
        raise ValueError('0176 unexpected frozen identifier')
    return '"' + value + '"'


def table_name(name):
    return 'public.' + quote(name)


def function_name(row):
    return 'public.' + quote(row['proname']) + '(' + row['signature'].split('(', 1)[1]


def preflight(db, expected_revision=PREVIOUS):
    if not db.in_transaction():
        raise ValueError('0176 requires a caller-owned transaction')
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,"
        "current_setting('transaction_isolation'),current_setting('transaction_read_only')")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16 \
            or identity[3:] != ('read committed', 'off'):
        raise ValueError('0176 direct PostgreSQL16 migrator at READ COMMITTED required')
    flags = db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
        'FROM pg_roles WHERE rolname=current_user')).one()
    if any(flags):
        raise ValueError('0176 unprivileged migration role required')
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != [expected_revision]:
        raise ValueError('0176 exact single predecessor revision required')


def verify_uuid(db):
    row = db.execute(text("""SELECT e.extversion,n.nspname,pg_get_userbyid(e.extowner),p.prosrc,p.probin,
        l.lanname,p.provolatile,p.proisstrict,p.prosecdef,p.proparallel,p.prorettype::regtype::text,
        has_schema_privilege('star_oam_api',n.oid,'USAGE'),has_function_privilege('star_oam_api',p.oid,'EXECUTE'),
        has_function_privilege('star_oam_migrator',p.oid,'EXECUTE'),
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
            WHERE a.privilege_type='EXECUTE' AND a.grantee NOT IN(p.proowner,'star_oam_migrator'::regrole))
        FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
        JOIN pg_depend d ON d.refclassid='pg_extension'::regclass AND d.refobjid=e.oid AND d.deptype='e'
        JOIN pg_proc p ON d.classid='pg_proc'::regclass AND d.objid=p.oid JOIN pg_language l ON l.oid=p.prolang
        WHERE e.extname='uuid-ossp' AND p.oid=to_regprocedure('rsc_loss_uuid_0159.uuid_generate_v5(uuid,text)')""")).one_or_none()
    if row is None or tuple(row) != ('1.1', 'rsc_loss_uuid_0159', 'postgres', 'uuid_generate_v5',
            '$libdir/uuid-ossp', 'c', 'i', True, False, 's', 'uuid', False, False, True, False):
        raise ValueError('0176 separately provisioned private UUID dependency required')


def verify(db, side='after'):
    if side not in ('before', 'after'):
        raise ValueError('0176 exact catalog side required')
    verify_uuid(db)
    actual = probe.snapshot(db)
    for kind in ('tables', 'functions'):
        for name, change in DATA[kind].items():
            if actual[kind].get(name) != change[side]:
                detail = first_difference(actual[kind].get(name), change[side])
                raise ValueError('0176 exact ' + side + ' ' + kind + ' catalog mismatch: ' + name + '; ' + detail)
    # Reject same-name overloads, not merely the expected function signature.
    names = {change['after']['proname'] for change in DATA['functions'].values()}
    wanted = {name for name, change in DATA['functions'].items() if change[side] is not None}
    found = {name for name, row in actual['functions'].items() if row['proname'] in names}
    if found != wanted:
        raise ValueError('0176 unexpected function overload')


def first_difference(actual, expected, path='object'):
    """Bounded catalog-only diagnostic; never includes business row values."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        for key in sorted(set(actual) | set(expected)):
            if actual.get(key) != expected.get(key):
                return first_difference(actual.get(key), expected.get(key), path + '.' + key)
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            return path + ': list lengths ' + str((len(actual), len(expected)))
        for index, (left, right) in enumerate(zip(actual, expected)):
            if left != right:
                return first_difference(left, right, path + '[' + str(index) + ']')
    return path + ': actual=' + repr(actual)[:250] + ', expected=' + repr(expected)[:250]


def lock_existing(db):
    names = {name for name, change in DATA['tables'].items() if change['before'] is not None}
    names.update(('alembic_version', 'inventory_ledger_heads', 'stock_accounts', 'audit_chain_heads'))
    db.execute(text('LOCK TABLE ' + ','.join(table_name(name) for name in sorted(names)) +
        ' IN SHARE ROW EXCLUSIVE MODE'))


def install(db):
    preflight(db)
    lock_existing(db)
    verify(db, 'before')
    for statement in DATA['statements']:
        db.execute(text(statement))
    verify(db, 'after')


def changed_items(change, key):
    before = {row['name']: row for row in change['before'][key]} if change['before'] else {}
    after = {row['name']: row for row in change['after'][key]}
    return before, after, sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))


def remove_empty(db, *, expected_revision=PREVIOUS):
    preflight(db, expected_revision)
    lock_existing(db)
    db.execute(text('LOCK TABLE ' + ','.join(table_name(name) for name in TABLES) + ' IN ACCESS EXCLUSIVE MODE'))
    verify(db, 'after')
    # Do every retention check before changing any catalog object. No records
    # are deleted or rewritten to make a downgrade possible.
    for name in TABLES:
        if db.scalar(text('SELECT EXISTS(SELECT 1 FROM ' + table_name(name) + ')')):
            raise ValueError('0176 immutable history requires retention: ' + name)
    for name, change in DATA['tables'].items():
        if change['before'] is None:
            continue
        old, new, changed = changed_items(change, 'columns')
        added = sorted(set(new) - set(old))
        if added and db.scalar(text('SELECT EXISTS(SELECT 1 FROM ' + table_name(name) + ' WHERE ' +
                ' OR '.join(quote(column) + ' IS NOT NULL' for column in added) + ')')):
            raise ValueError('0176 immutable parent binding requires retention: ' + name)
    for name, change in DATA['tables'].items():
        old, new, changed = changed_items(change, 'triggers')
        for trigger in changed:
            if trigger in old:
                raise ValueError('0176 unexpected replacement of an old trigger')
            db.execute(text('DROP TRIGGER ' + quote(trigger) + ' ON ' + table_name(name)))
    for change in DATA['functions'].values():
        if change['before']:
            db.execute(text(change['before']['definition']))
    # Remove cross-table dependencies explicitly. Never use CASCADE, including
    # when new tables reference each other or an extended old parent key.
    for name, change in DATA['tables'].items():
        old, new, changed = changed_items(change, 'constraints')
        for constraint in changed:
            if constraint in new and new[constraint]['type'] == 'f':
                if constraint in old:
                    raise ValueError('0176 existing FK replacement is forbidden')
                db.execute(text('ALTER TABLE ' + table_name(name) + ' DROP CONSTRAINT ' + quote(constraint)))
    db.execute(text('DROP TABLE ' + ','.join(table_name(name) for name in TABLES)))
    new_functions = [function_name(change['after']) for change in DATA['functions'].values() if change['before'] is None]
    db.execute(text('DROP FUNCTION ' + ','.join(new_functions)))
    verify(db, 'before')
