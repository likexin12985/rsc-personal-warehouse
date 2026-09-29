"""Loss shipment proof is private, exact, reversible only before posting."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
import pytest
import sqlalchemy as sa

from app import database_security as security, oam_sync_scope_security as scope
from migration_script_cache import cache_migration_compilation


@pytest.fixture(scope='module')
def migration():
    folder = Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder/'20261203_0154_stock_loss_return_shipments.py'))


def test_private_proof_catalog_and_ordinary_source_chain(migration):
    m = migration
    assert m['down_revision'] == '20261202_0153'
    for key, (args, result, body) in m['FUNCTIONS'].items():
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key] == hashlib.sha256(body.encode()).hexdigest()
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key] == ('f', result, False)
        assert security.FORMAL_FILE_INTERNAL_FUNCTIONS[key] == ('v', True, 'plpgsql', ('search_path=pg_catalog, public',))
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        sql = f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$'
        assert not sa.text(sql)._bindparams
        parser.parse_plpgsql_json(sql)
    key = ('rsc_check_stock_return_shipment_0104', 'uuid')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256_THROUGH_0153[key] == m['OLD_HASH']
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key] == m['NEW_HASH']
    # Removing only the new dispatch must recover the full ordinary proof.
    prefix, remainder = m['DISPATCHED'].split('    END IF;\n', 1)
    assert prefix.endswith('        RETURN;\n')
    assert m['BASE'] == m['DISPATCHED'].split('BEGIN\n', 1)[0] + 'BEGIN\n' + remainder
    for name, (table, function) in m['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table, function, 'A', 5, True, True, True)
    for version, label in (('0153', 'OLD'), ('0154', 'NEW')):
        manifest = getattr(scope, 'OAM_SYNC_FUNCTION_MANIFEST_THROUGH_'+version)
        assert manifest['rsc_oam_runtime_binding_ready_0044()'][6] == m[label+'_READY_HASH']
    for table in ('stock_locations', 'custody_assignments'):
        assert table not in security.RUNTIME_UPDATE_TABLES


@pytest.mark.parametrize('direction', ('upgrade', 'downgrade'))
def test_atomic_owner_transition_parses_and_never_deletes_history(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',
            opts={'as_sql':True, 'output_buffer':output})):
        migration[direction]()
    sql = output.getvalue()
    parser.parse_sql(sql)
    assert sql.index('direct schema owner required') < sql.index('LOCK TABLE public.alembic_version')
    assert 'DELETE FROM' not in sql and 'TRUNCATE ' not in sql and 'GRANT ' not in sql
    assert '0154 loss shipment trigger catalog drift' in sql
    if direction == 'downgrade':
        assert sql.index('0154 loss shipment history requires retention') < sql.index('EXECUTE function_definition') < sql.index('DROP TRIGGER')
    else:
        for name in migration['TRIGGERS']:
            assert 'ENABLE ALWAYS TRIGGER '+name in sql


def test_sqlite_keeps_ordinary_history_and_refuses_loss_posting_and_unsafe_downgrade(migration):
    engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            db.exec_driver_sql('CREATE TABLE stock_operation_orders(id TEXT PRIMARY KEY,loss_headquarters_decision_id TEXT)')
            db.exec_driver_sql('CREATE TABLE stock_operation_shipments(id TEXT PRIMARY KEY,operation_id TEXT)')
            db.exec_driver_sql("INSERT INTO stock_operation_orders VALUES('ordinary',NULL),('loss','approved')")
            db.exec_driver_sql("INSERT INTO stock_operation_shipments VALUES('ordinary-shipment','ordinary')")
            db.commit()
            migration['upgrade']()
            with pytest.raises(sa.exc.IntegrityError, match='PostgreSQL loss shipment proof required'):
                db.exec_driver_sql("INSERT INTO stock_operation_shipments VALUES('loss-shipment','loss')")
            migration['downgrade']()
            assert tuple(db.exec_driver_sql('SELECT * FROM stock_operation_shipments')) == (('ordinary-shipment', 'ordinary'),)
            # Owner-only malformed preexisting fixture: even this history must
            # prevent dropping the new proof on a downgrade.
            db.exec_driver_sql("INSERT INTO stock_operation_shipments VALUES('retained','loss')")
            migration['upgrade']()
            db.commit()
            before = tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))
            with pytest.raises(RuntimeError, match='0154 loss shipment history requires retention'):
                migration['downgrade']()
            assert tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger'")) == before
            assert db.scalar(sa.text('SELECT count(*) FROM stock_operation_shipments')) == 2
    finally:
        engine.dispose()


def test_native_and_hosted_gate_rejects_invalid_tracking_before_access():
    from pg16_stock_loss_return_shipment_gate import release
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid tracking reached database')
    for tracking in ('', 'both', 'production'):
        with pytest.raises(ValueError, match='tracking must be quantity or serial'):
            release({}, tracking=tracking, migrate=forbidden, provision=forbidden)
