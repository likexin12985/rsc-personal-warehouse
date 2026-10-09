"""Admit explicitly pinned contact v2 while retaining immutable v1 history.

Only contact validation and the exact readiness revision change. Existing
identity, revision, shipment, reservation, audit and permission guards remain.
No key, claim, ciphertext or historical business row is inserted or rewritten.
"""
from hashlib import sha256
import json
from pathlib import Path
import runpy

from alembic import context, op
from sqlalchemy import text


revision = '20261230_0181'
down_revision = '20261229_0180'
branch_labels = depends_on = None
FOLDER = Path(__file__).parents[1] / 'contact_envelope_0181'
RAW = (FOLDER / 'catalog.json').read_bytes()
# This digest is replaced only when the independently captured predecessor and
# reviewed candidate are frozen, never inferred from the database being tested.
CATALOG_SHA256 = 'd4bab569c99db684fc8f588c2c350d628fa81f218163c5e5a64e1dc4cd00e32e'
if sha256(RAW).hexdigest() != CATALOG_SHA256:
    raise ValueError('0181 frozen contact catalog changed')
DATA = json.loads(RAW)
DOWNGRADE_BLOCKER = 'cannot downgrade 0181 while non-v1 contact history exists'


def _sources():
    return {'public.' + name: (row['before'], row['after'])
            for name, row in DATA['functions'].items()}


def _check_history(db, *, sqlite, down):
    """Scan every current and historical envelope, including superseded rows."""
    schema = '' if sqlite else 'public.'
    if down:
        extract = ("json_extract(contact_snapshot_jsonb, '$.schema') IS NOT 'rsc.material_request_contact.v1'"
                   if sqlite else "contact_snapshot_jsonb->>'schema' IS DISTINCT FROM 'rsc.material_request_contact.v1'")
        for table in ('material_requests', 'material_request_revisions'):
            if db.scalar(text(f'SELECT EXISTS(SELECT 1 FROM {schema}{table} WHERE {extract})')):
                raise ValueError(DOWNGRADE_BLOCKER)
    checks = DATA['history_checks']['sqlite' if sqlite else 'postgresql']
    for query in checks.values():
        if db.scalar(text(query)):
            raise ValueError('0181 current or historical contact envelope invalid')


def _postgresql(db, up):
    identity = db.execute(text("SELECT current_user,session_user,current_setting('server_version_num')::int,"
                               "current_setting('transaction_isolation')")).one()
    if identity[:2] != ('star_oam_migrator', 'star_oam_migrator') or identity[2] // 10000 != 16 \
            or identity[3] != 'read committed':
        raise ValueError('0181 direct PostgreSQL16 read-committed migrator required')
    if any(db.execute(text('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls '
                           'FROM pg_roles WHERE rolname=current_user')).one()):
        raise ValueError('0181 unprivileged migrator required')
    db.execute(text('LOCK TABLE public.alembic_version, public.material_requests, '
                    'public.material_request_revisions, public.kms_data_key_pins, '
                    'public.openbao_data_key_pins, public.application_key_version_claims '
                    'IN SHARE ROW EXCLUSIVE MODE'))
    if db.execute(text('SELECT version_num FROM public.alembic_version')).scalars().all() != [down_revision if up else revision]:
        raise ValueError('0181 exact predecessor required')
    probe = runpy.run_path(str(FOLDER / 'catalog_probe.py'))
    actual = probe['snapshot'](db, table_names=tuple(DATA['catalog']['tables']),
                               function_names=tuple(DATA['observed_function_names']))
    expected = dict(tables=DATA['catalog']['tables'], functions=DATA['catalog']['functions']['before' if up else 'after'])
    if actual != expected:
        raise ValueError('0181 exact predecessor catalog required')
    _check_history(db, sqlite=False, down=not up)
    helper = runpy.run_path(str(Path(__file__).with_name('20260909_0069_stock_reservations.py')))
    before, after = ('before', 'after') if up else ('after', 'before')
    for signature, row in DATA['functions'].items():
        helper['_replace_function_source'](
            signature='public.' + signature,
            expected_hash=row[before + 'Sha256'], replacement_hash=row[after + 'Sha256'],
            replacements=((row[before], row[after]),), label='contact_envelope_0181')
    final = probe['snapshot'](db, table_names=tuple(DATA['catalog']['tables']),
                              function_names=tuple(DATA['observed_function_names']))
    if final != dict(tables=DATA['catalog']['tables'], functions=DATA['catalog']['functions'][after]):
        raise ValueError('0181 exact resulting catalog required')


def _sqlite(db, up):
    if db.execute(text('SELECT version_num FROM alembic_version')).scalars().all() != [down_revision if up else revision]:
        raise ValueError('0181 exact SQLite predecessor required')
    before, after = ('before', 'after') if up else ('after', 'before')
    for name, row in DATA['sqlite_triggers'].items():
        found = db.exec_driver_sql('SELECT sql FROM sqlite_master WHERE type=? AND name=?', ('trigger', name)).scalars().all()
        if found != [row[before]]:
            raise ValueError('0181 exact SQLite predecessor guard required: ' + name)
    _check_history(db, sqlite=True, down=not up)
    for name, row in DATA['sqlite_triggers'].items():
        db.exec_driver_sql('DROP TRIGGER ' + name)
        db.exec_driver_sql(row[after])
    for name, row in DATA['sqlite_triggers'].items():
        if db.exec_driver_sql('SELECT sql FROM sqlite_master WHERE name=?', (name,)).scalar_one() != row[after]:
            raise ValueError('0181 exact resulting SQLite guard required')


def _transition(up):
    if context.is_offline_mode():
        raise ValueError('0181 online predecessor and history verification required')
    db = op.get_bind()
    if db.dialect.name == 'postgresql':
        _postgresql(db, up)
    elif db.dialect.name == 'sqlite':
        _sqlite(db, up)
    else:
        raise ValueError('0181 requires PostgreSQL16 or SQLite schema tooling')


def upgrade():
    _transition(True)


def downgrade():
    _transition(False)
