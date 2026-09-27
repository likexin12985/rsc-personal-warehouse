"""0149 must extend exact source, preserve privileges and retain inbound history."""
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


@pytest.fixture(scope='module')
def migration():
    return runpy.run_path(str(Path(__file__).parents[1] / 'alembic/versions/'
        '20261128_0149_stock_return_inbound_account_admission.py'))


def test_exact_account_source_readiness_and_unchanged_private_acl(migration):
    m = migration
    old, new = m['_sources']()[m['ACCOUNT_SIGNATURE']]
    assert hashlib.sha256(old.encode()).hexdigest() == m['ACCOUNT_OLD_HASH']
    assert new.replace(m['ACCOUNT_BRANCH'], '') == old
    assert hashlib.sha256(new.encode()).hexdigest() == m['ACCOUNT_NEW_HASH']
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0149[
        ('rsc_require_opening_observation_account_0023', '')] == m['ACCOUNT_NEW_HASH']
    assert ('rsc_require_opening_observation_account_0023', '') not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert m['OLD_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0148['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0149['rsc_oam_runtime_binding_ready_0044()'][6]
    parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $b$' + new + '$b$')
    assert not sa.text(new)._bindparams


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_owner_only_locked_source_cas_without_new_grants(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',
            opts={'as_sql': True, 'output_buffer': output})):
        migration[direction]()
    sql = output.getvalue(); parser.parse_sql(sql)
    assert sql.index('direct schema owner required') < sql.index('LOCK TABLE public.alembic_version')
    assert sql.index('LOCK TABLE public.alembic_version') < sql.index('LOCK TABLE public.inventory_ledger_heads')
    assert 'GRANT ' not in sql and 'DELETE FROM' not in sql
    if direction == 'downgrade':
        assert sql.index('history requires retention') < sql.index('return_inbound_account_admission_0149')


def test_existing_inbound_survives_upgrade_and_blocks_downgrade(migration):
    engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
            db.exec_driver_sql('CREATE TABLE stock_operation_return_inbounds(id TEXT)')
            migration['upgrade'](); migration['downgrade'](); migration['upgrade']()
            db.exec_driver_sql("INSERT INTO stock_operation_return_inbounds VALUES ('synthetic')")
            migration['upgrade']()
            with pytest.raises(RuntimeError, match='0149 return inbound account admission history requires retention'):
                migration['downgrade']()
            assert db.scalar(sa.text('SELECT count(*) FROM stock_operation_return_inbounds')) == 1
    finally:
        engine.dispose()
