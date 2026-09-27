"""Attachment FK/immutability prerequisites do not activate loss posting."""
from io import StringIO
from pathlib import Path
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
import pytest
import sqlalchemy as sa

from app import database_security as security, oam_sync_scope_security as scope
from app.stock_operation_models import StockLossFile
from migration_script_cache import cache_migration_compilation


@pytest.fixture(scope='module')
def migration():
    versions = Path(__file__).parents[1] / 'alembic/versions'
    with cache_migration_compilation(versions):
        return runpy.run_path(str(versions / '20261123_0144_stock_loss_file_bindings.py'))


def test_catalog_and_private_binding_body_are_exact(migration):
    m = migration
    coordinate = (m['FUNCTION'], '')
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == m['BODY_HASH']
    assert security.FORMAL_FILE_INTERNAL_FUNCTIONS[coordinate] == ('v', True, 'plpgsql', ('search_path=pg_catalog, public',))
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[coordinate] == ('f', 'trigger', False)
    assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert m['OLD_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0143['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0144['rsc_oam_runtime_binding_ready_0044()'][6]
    for name, (table, _, function, kind, deferred) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (
            table, function, 'A', kind, deferred, deferred, deferred)
    parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $b$'+m['BODY']+'$b$')
    assert not sa.text(m['BODY'])._bindparams


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_postgresql_ddl_retains_closed_admission_and_history(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})):
        migration[direction]()
    sql = output.getvalue()
    parser.parse_sql(sql)
    assert sql.index('LOCK TABLE public.alembic_version') < sql.index('LOCK TABLE public.inventory_ledger_heads')
    assert 'DROP TRIGGER trg_stock_operation' not in sql
    assert 'INSERT INTO permissions' not in sql and 'DELETE FROM' not in sql
    if direction == 'upgrade':
        assert 'DEFERRABLE INITIALLY DEFERRED' in sql
        assert 'GRANT SELECT,INSERT' in sql
        assert 'FOREIGN KEY(operation_id, operation_type)' in sql
    else:
        assert sql.index('requires retention') < sql.index('DROP TABLE stock_loss_files')


def test_sqlite_keeps_insert_closed_and_empty_roundtrip(migration):
    engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            # Only structural prerequisites; no synthetic business admission.
            db.exec_driver_sql('CREATE TABLE stock_operation_orders(id CHAR(32), operation_type TEXT, UNIQUE(id,operation_type))')
            db.exec_driver_sql('CREATE TABLE files(id CHAR(32) PRIMARY KEY)')
            migration['upgrade']()
            inspector = sa.inspect(db)
            constraints = inspector.get_foreign_keys('stock_loss_files')
            assert any(fk['constrained_columns'] == ['operation_id','operation_type']
                       and fk['referred_table'] == 'stock_operation_orders' for fk in constraints)
            assert {c['name'] for c in inspector.get_columns('stock_loss_files')} == set(StockLossFile.__table__.columns.keys())
            with pytest.raises(sa.exc.IntegrityError, match='PostgreSQL loss proof required'):
                db.execute(StockLossFile.__table__.insert().values(id=uuid4(), operation_id=uuid4(),
                    operation_type='loss_report', file_id=uuid4(), metadata_sha256='a'*64))
            assert db.scalar(sa.text('SELECT count(*) FROM stock_loss_files')) == 0
            migration['downgrade']()
            assert 'stock_loss_files' not in sa.inspect(db).get_table_names()
    finally:
        engine.dispose()


def test_retained_binding_blocks_downgrade_before_ddl(migration):
    engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            # Isolated historical-row fixture; never a bypass on a migrated DB.
            db.exec_driver_sql('CREATE TABLE stock_loss_files(id TEXT PRIMARY KEY)')
            db.exec_driver_sql("INSERT INTO stock_loss_files VALUES ('synthetic-retained-binding')")
            before = tuple(db.exec_driver_sql('SELECT * FROM stock_loss_files'))
            with pytest.raises(RuntimeError, match='history requires retention'):
                migration['downgrade']()
            assert tuple(db.exec_driver_sql('SELECT * FROM stock_loss_files')) == before
    finally:
        engine.dispose()
