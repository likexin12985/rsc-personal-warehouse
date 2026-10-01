"""Frozen function-only transition for account correction generations.

The predecessor schema, immutable facts, minimal ACL and SQLite deny-write
triggers remain exact. No business role grants or HTTP routes are installed.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
from sqlalchemy import text

HERE = Path(__file__).resolve().parent
RAW = (HERE / 'catalog.json').read_bytes()
if hashlib.sha256(RAW).hexdigest() != '90142dbf0721f4dd1efff3bdfbb6d6633eef2536a49f44cbe276d4217bad4c7e':
    raise ValueError('0160 frozen transition catalog digest mismatch')
DATA = json.loads(RAW)
PREVIOUS, REVISION = DATA['previousRevision'], DATA['revision']


def _previous(dialect, *, upgraded=False):
    if dialect not in ('postgresql', 'sqlite'):
        raise ValueError('0160 PostgreSQL16 or SQLite tooling required')
    folder = HERE.parent / 'stock_loss_corrections_0159'
    if hashlib.sha256((folder / 'frozen-catalog.json').read_bytes()).hexdigest() != DATA['sourceFrozenCatalogSha256']:
        raise ValueError('0160 immutable predecessor catalog drift')
    name = 'frozen_install' if dialect == 'postgresql' else 'sqlite_install'
    spec = importlib.util.spec_from_file_location('_loss_0160_predecessor_' + name, folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if dialect == 'postgresql' and upgraded:
        # This is a fresh isolated verifier module, never the shared runtime
        # module or immutable predecessor file. Only five expected bodies vary.
        patches = {row['after']['proname']: row['after'] for row in DATA['functions']}
        assert set(patches).issubset(row['proname'] for row in module.DATA['newFunctions'])
        module.DATA['newFunctions'] = [patches.get(row['proname'], row) for row in module.DATA['newFunctions']]
    return module


def sources():
    values = {'public.' + row['before']['proname'] + '(' +
        ', '.join(row['before']['signature'].split('(', 1)[1][:-1].split(',')) + ')':
        (row['before']['prosrc'], row['after']['prosrc']) for row in DATA['functions']}
    values['public.rsc_oam_runtime_binding_ready_0044()'] = (
        DATA['readiness']['beforeBody'], DATA['readiness']['afterBody'])
    return values


def _ready(db, *, upgraded):
    expected_body = DATA['readiness']['afterBody' if upgraded else 'beforeBody']
    row = db.execute(text("SELECT p.prosrc,pg_get_userbyid(p.proowner) AS owner,p.prosecdef,p.provolatile,"
        "p.proparallel,p.proisstrict,p.proleakproof,p.prokind,p.prorettype::regtype::text AS result,"
        "p.proconfig,l.lanname,pg_get_function_identity_arguments(p.oid) AS args "
        "FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang "
        "WHERE p.oid=to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()')")).mappings().one_or_none()
    expected = dict(prosrc=expected_body, owner='star_oam_migrator', prosecdef=True, provolatile='s',
        proparallel='u', proisstrict=False, proleakproof=False, prokind='f', result='boolean',
        proconfig=['search_path=pg_catalog'], lanname='sql', args='')
    if row is None or dict(row) != expected:
        raise ValueError('0160 exact readiness body or metadata drift')
    return db.execute(text("SELECT CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,"
        "a.privilege_type,a.is_grantable FROM pg_proc p,"
        "LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a "
        "WHERE p.oid=to_regprocedure('public.rsc_oam_runtime_binding_ready_0044()') ORDER BY 1,2,3")).all()


def _require_representable_history(db):
    if db.dialect.name == 'postgresql':
        exists = db.scalar(text("SELECT EXISTS(SELECT 1 FROM public.stock_loss_disposition_reversals "
            "WHERE reversed_correction_id IS NOT NULL) OR EXISTS(SELECT 1 FROM public.stock_loss_inverse_request_seals "
            "WHERE command_jsonb->'intent'->>'reversed_correction_id' IS NOT NULL)"))
    else:
        exists = db.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM stock_loss_disposition_reversals "
            "WHERE reversed_correction_id IS NOT NULL) OR EXISTS(SELECT 1 FROM stock_loss_inverse_request_seals "
            "WHERE json_extract(command_jsonb,'$.intent.reversed_correction_id') IS NOT NULL)").scalar()
    if exists:
        raise ValueError('0160 immutable later-generation history requires retention')


def transition(db, *, up):
    dialect = db.dialect.name
    before = _previous(dialect, upgraded=not up)
    if dialect == 'sqlite':
        before.prepare(db, PREVIOUS if up else REVISION)
        before.verify(db)
        if not up:
            _require_representable_history(db)
        for table in before.TABLES:
            if db.exec_driver_sql('PRAGMA foreign_key_check("' + table + '")').first():
                raise ValueError('0160 SQLite tooling foreign key violation')
        # Alembic advances only the revision; all fifteen unconditional write
        # rejection triggers and every existing object remain unchanged.
        return
    before.preflight(db, PREVIOUS if up else REVISION)
    db.execute(text('LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE'))
    db.execute(text("SELECT stream_key FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE"))
    before.verify(db, runtime=True)
    ready_acl = _ready(db, upgraded=not up)
    if not up:
        _require_representable_history(db)
    records = DATA['functions'] if up else list(reversed(DATA['functions']))
    for row in records:
        db.execute(text(row['after' if up else 'before']['definition']))
    db.execute(text(DATA['readiness']['upgradeSql' if up else 'downgradeSql']))
    _previous(dialect, upgraded=up).verify(db, runtime=True)
    if _ready(db, upgraded=up) != ready_acl:
        raise ValueError('0160 readiness permissions changed during transition')
