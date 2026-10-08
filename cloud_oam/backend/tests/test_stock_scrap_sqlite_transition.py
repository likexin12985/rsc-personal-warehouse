"""Real 0164 tooling upgrade/rollback and fail-closed history retention.

SQLite cannot prove native stock business behavior. These tests only prove
structural migration, atomicity and refusal to use it as a business database.
"""
from pathlib import Path
import runpy
import sqlite3

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
SUPPORT = runpy.run_path(str(ROOT / 'backend/alembic/stock_scrap_0165/sqlite_transition.py'))
DATA = SUPPORT['DATA']


@pytest.fixture(scope='module')
def predecessor(tmp_path_factory):
    path = tmp_path_factory.mktemp('scrap-sqlite') / '0164.db'
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', 'sqlite+pysqlite:///' + str(path))
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv('OAM_DATABASE_URL', raising=False)
        command.upgrade(config, '20261213_0164')
    return path


@pytest.fixture
def engine(predecessor, tmp_path):
    path = tmp_path / 'case.db'
    with sqlite3.connect(predecessor) as source, sqlite3.connect(path) as target:
        source.backup(target)
    engine = create_engine('sqlite+pysqlite:///' + str(path))
    yield engine
    engine.dispose()


def snapshot(db):
    return tuple((kind, name, table, SUPPORT['table_identity'](sql) if kind == 'table' else sql)
                 for kind, name, table, sql in db.exec_driver_sql(
                     'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')
                 if not (kind == 'index' and sql is None and table in DATA['parentTables']))


def apply(db, *, up):
    SUPPORT['transition'](db, up=up)
    db.exec_driver_sql('UPDATE alembic_version SET version_num=?',
                      (DATA['revision' if up else 'previousRevision'],))


def test_formal_alembic_revision_roundtrip_matches_registered_models(engine):
    from app import models
    from sqlalchemy import inspect
    config = Config(str(ROOT / 'alembic.ini'))
    config.set_main_option('sqlalchemy.url', engine.url.render_as_string(hide_password=False))
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv('OAM_DATABASE_URL', raising=False)
        command.upgrade(config, '20261214_0165')
        with engine.connect() as db:
            SUPPORT['verify'](db, 'after')
            assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == DATA['revision']
            from pg16_stock_operation_permission_policy import DEFAULTS
            expected = {(role, action, 'allow') for role, actions in DEFAULTS.items() for action in actions}
            policy_rows = db.exec_driver_sql("SELECT r.code,p.action,rp.effect FROM role_permissions rp "
                "JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id "
                "WHERE p.resource='stock_operation'").all()
            actions = {action for _,action,_ in expected}
            assert {(r,a,e) for r,a,e in policy_rows if a in actions} == expected
            authorization_rows = tuple(db.exec_driver_sql('SELECT * FROM role_permissions ORDER BY id'))
            inspector = inspect(db)
            for name in (*DATA['parentTables'], *DATA['newTables']):
                actual = {col['name']: col['nullable'] for col in inspector.get_columns(name)}
                expected = {col.name: col.nullable for col in models.Base.metadata.tables[name].columns}
                assert actual == expected, name
        command.downgrade(config, '20261213_0164')
        with engine.connect() as db:
            SUPPORT['verify'](db, 'before')
            assert tuple(db.exec_driver_sql('SELECT * FROM role_permissions ORDER BY id')) == authorization_rows
        command.upgrade(config, '20261214_0165')
        with engine.connect() as db:
            SUPPORT['verify'](db, 'after')
            assert tuple(db.exec_driver_sql('SELECT * FROM role_permissions ORDER BY id')) == authorization_rows


def test_actual_predecessor_roundtrip_preserves_all_schema_and_unrelated_rows(engine):
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE retained_example (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO retained_example VALUES (1,'keep this old row')")
        db.exec_driver_sql('CREATE VIEW retained_view AS SELECT id FROM stock_operation_orders')
        db.exec_driver_sql('CREATE TRIGGER retained_trigger BEFORE DELETE ON retained_example '
                           "BEGIN SELECT RAISE(ABORT,'keep'); END")
    with engine.connect() as db:
        before = snapshot(db)
        rows = db.exec_driver_sql('SELECT * FROM retained_example').all()
    for _ in range(2):
        with engine.begin() as db:
            apply(db, up=True)
            assert db.exec_driver_sql('PRAGMA foreign_key_check').all() == []
            assert db.exec_driver_sql('SELECT * FROM retained_view').all() == []
            assert db.exec_driver_sql('SELECT * FROM retained_example').all() == rows
        with engine.begin() as db:
            apply(db, up=False)
        with engine.connect() as db:
            assert snapshot(db) == before
            assert db.exec_driver_sql('SELECT * FROM retained_example').all() == rows


def test_failure_after_rebuilding_parents_rolls_back_entire_transition(engine, monkeypatch):
    with engine.connect() as db:
        before = snapshot(db)
    original = SUPPORT['transition'].__globals__['verify']
    def verify(db, side):
        original(db, side)
        if side == 'after':
            raise RuntimeError('injected after rebuild')
    monkeypatch.setitem(SUPPORT['transition'].__globals__, 'verify', verify)
    with pytest.raises(RuntimeError, match='injected after rebuild'), engine.begin() as db:
        apply(db, up=True)
    with engine.connect() as db:
        assert snapshot(db) == before
        assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == DATA['previousRevision']


@pytest.mark.parametrize('name', DATA['newTables'])
def test_every_new_table_refuses_sqlite_business_insert(engine, name):
    with engine.begin() as db:
        apply(db, up=True)
    with pytest.raises(IntegrityError, match='PostgreSQL scrap proof'), engine.begin() as db:
        db.exec_driver_sql('INSERT INTO "' + name + '" DEFAULT VALUES')


def injected_row(db, name):
    # Deliberately corrupt a disposable test database, restore all guards,
    # and prove even malformed retained facts cannot authorize a downgrade.
    trigger = 'trg_0165_' + name + '_insert'
    db.exec_driver_sql('DROP TRIGGER "' + trigger + '"')
    db.exec_driver_sql('PRAGMA ignore_check_constraints=ON')
    columns = list(db.exec_driver_sql('PRAGMA table_info("' + name + '")'))
    names = ','.join('"' + row[1] + '"' for row in columns)
    values = tuple(1 if any(t in row[2].upper() for t in ('INT','NUMERIC')) else '0' * 32 for row in columns)
    db.exec_driver_sql('INSERT INTO "' + name + '" (' + names + ') VALUES (' + ','.join('?' for _ in columns) + ')', values)
    db.exec_driver_sql('PRAGMA ignore_check_constraints=OFF')
    db.exec_driver_sql(DATA['guards'][trigger])


@pytest.mark.parametrize('name', DATA['newTables'])
def test_any_new_fact_refuses_downgrade_and_cannot_be_updated_or_deleted(engine, name):
    with engine.begin() as db:
        apply(db, up=True)
        injected_row(db, name)
    with engine.connect() as db:
        before = snapshot(db)
        rows = db.exec_driver_sql('SELECT * FROM "' + name + '"').all()
    with engine.connect() as db:
        column = db.exec_driver_sql('PRAGMA table_info("' + name + '")').first()[1]
    for sql in ('UPDATE "' + name + '" SET "' + column + '"="' + column + '"', 'DELETE FROM "' + name + '"'):
        with pytest.raises(IntegrityError, match='history is immutable'), engine.begin() as db:
            db.exec_driver_sql(sql)
    with pytest.raises(ValueError, match='history requires retention'), engine.begin() as db:
        apply(db, up=False)
    with engine.connect() as db:
        assert snapshot(db) == before
        assert db.exec_driver_sql('SELECT * FROM "' + name + '"').all() == rows
        assert db.exec_driver_sql('SELECT version_num FROM alembic_version').scalar() == DATA['revision']


def test_table_identity_ignores_constraint_order_only():
    identity = SUPPORT['table_identity']
    original = "CREATE TABLE example (a TEXT, b TEXT, CHECK(a IN ('one,two','it''s (quoted)')), UNIQUE(a,b))"
    reordered = "CREATE TABLE example (a TEXT, b TEXT, UNIQUE(a,b), CHECK(a IN ('one,two','it''s (quoted)')))"
    assert identity(original) == identity(reordered)
    for changed in (original.replace('a TEXT, b TEXT', 'b TEXT, a TEXT'),
                    original.replace('UNIQUE(a,b)', 'UNIQUE(b,a)'),
                    original.replace('one,two', 'one, two'),
                    original.replace('a TEXT', 'a TEXT NOT NULL'),
                    original[:-1] + ', UNIQUE(a,b))', original + ' WITHOUT ROWID'):
        assert identity(changed) != identity(original)


@pytest.mark.parametrize('mutation', ['revision', 'extra_column', 'missing_guard', 'foreign_keys'])
def test_unexpected_predecessor_is_rejected_before_any_ddl(engine, mutation):
    with engine.begin() as db:
        if mutation == 'revision':
            db.exec_driver_sql("UPDATE alembic_version SET version_num='unexpected'")
        elif mutation == 'extra_column':
            db.exec_driver_sql('ALTER TABLE stock_loss_dispositions ADD COLUMN unexpected TEXT')
        elif mutation == 'missing_guard':
            trigger = next(name for name, row in DATA['before'].items() if row['type'] == 'trigger')
            db.exec_driver_sql('DROP TRIGGER "' + trigger + '"')
    with engine.connect() as db:
        if mutation == 'foreign_keys':
            db.exec_driver_sql('PRAGMA foreign_keys=ON')
        before = snapshot(db)
        with pytest.raises(ValueError):
            apply(db, up=True)
        db.rollback()
        assert snapshot(db) == before
