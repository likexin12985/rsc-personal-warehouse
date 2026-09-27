"""0151 strengthens current custody without changing historical proof or grants."""
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
    folder = Path(__file__).parents[1] / 'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder / '20261130_0151_stock_loss_custody_uniqueness.py'))


def test_exact_catalog_preserves_private_shape_and_historical_branch(migration):
    m = migration
    coordinate = ('rsc_check_loss_disposition_0150', 'uuid, boolean')
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0150[coordinate] == m['CHECK_OLD_HASH']
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate] == m['CHECK_NEW_HASH']
    changed = {key for key, value in security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256.items()
        if security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256_THROUGH_0150.get(key) != value}
    assert changed == {coordinate}
    assert coordinate not in security.RUNTIME_EXECUTE_FUNCTIONS
    assert m['OLD_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0150['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST['rsc_oam_runtime_binding_ready_0044()'][6]
    for label in ('OLD', 'NEW'):
        body = m[label + '_BODY']
        assert hashlib.sha256(body.encode()).hexdigest() == m['CHECK_' + label + '_HASH']
        parser.parse_plpgsql_json('CREATE FUNCTION guard(checked_fact uuid, require_current boolean) RETURNS void LANGUAGE plpgsql AS $b$' + body + '$b$')
        assert not sa.text(body)._bindparams
    assert m['OLD_BODY'].split('    IF require_current THEN', 1)[0] == m['NEW_BODY'].split('    IF require_current THEN', 1)[0]
    assert 'custody.custodian_person_id<>source.custodian_person_id' in m['NEW_BODY']
    assert 'PERFORM id FROM public.custody_assignments WHERE location_id=location.id ORDER BY id FOR SHARE' in m['NEW_BODY']


@pytest.mark.parametrize('direction', ['upgrade', 'downgrade'])
def test_exact_atomic_transition_and_no_grant_changes(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',
            opts={'as_sql': True, 'output_buffer': output})):
        migration[direction]()
    sql = output.getvalue()
    parser.parse_sql(sql)
    assert sql.index('direct schema owner required') < sql.index('LOCK TABLE public.alembic_version') < sql.index('LOCK TABLE public.inventory_ledger_heads')
    assert 'public.custody_assignments' in sql
    assert 'GRANT ' not in sql and 'REVOKE ' not in sql
    assert 'DELETE FROM' not in sql and 'INSERT INTO permissions' not in sql
    assert migration['CHECK_OLD_HASH'] in sql and migration['CHECK_NEW_HASH'] in sql
    if direction == 'downgrade':
        assert sql.index('disposition custody proof history requires retention') < sql.index('loss_disposition_custody_0151')


def test_sqlite_roundtrip_keeps_existing_history(migration):
    engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    try:
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            db.execute(sa.text('CREATE TABLE stock_loss_dispositions (id TEXT PRIMARY KEY)'))
            db.commit()
            migration['upgrade']()
            migration['downgrade']()
            migration['upgrade']()
            db.execute(sa.text("INSERT INTO stock_loss_dispositions VALUES ('synthetic')"))
            db.commit()
            with pytest.raises(RuntimeError, match='0151 disposition custody proof history requires retention'):
                migration['downgrade']()
            assert db.scalar(sa.text('SELECT count(*) FROM stock_loss_dispositions')) == 1
    finally:
        engine.dispose()
