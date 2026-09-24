"""0122 patches one frozen function without widening the runtime privileges."""
from io import StringIO
from pathlib import Path
import hashlib
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security, oam_sync_scope_security as scope
from test_inventory_control_projection import (
    db, material_db, mapping_db, admission_db, authority_db,
)

PATH = Path(__file__).parents[1]/'alembic/versions/20261101_0122_zero_control_opening.py'


def test_sqlite_roundtrip_does_not_modify_schema_or_existing_rows(db):
    before = tuple(db.connection().exec_driver_sql('SELECT type,name,sql FROM sqlite_master ORDER BY type,name'))
    migration = runpy.run_path(str(PATH))
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['upgrade'](); migration['downgrade'](); migration['upgrade']()
    assert tuple(db.connection().exec_driver_sql('SELECT type,name,sql FROM sqlite_master ORDER BY type,name')) == before


def test_exact_forward_and_reverse_function_patch_and_offline_sql():
    m = runpy.run_path(str(PATH))
    old = runpy.run_path(str(PATH.with_name('20260831_0027_postgresql_lock_graph.py')))
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        old['_create_opening_control_import_function']()
    legacy = output.getvalue().split('AS $$')[1].split('$$')[0]
    assert hashlib.sha256(legacy.encode()).hexdigest() == m['LEGACY_HASH']
    assert legacy.count(m['LEGACY_FRAGMENT']) == 1
    repaired = legacy.replace(m['LEGACY_FRAGMENT'],m['FIXED_FRAGMENT'])
    assert repaired.replace(m['FIXED_FRAGMENT'],m['LEGACY_FRAGMENT']) == legacy
    assert hashlib.sha256(repaired.encode()).hexdigest() == m['FIXED_HASH']
    assert m['FIXED_HASH'] == security.RUNTIME_FUNCTION_BODY_SHA256[('rsc_lock_opening_control_import_0027','uuid, uuid')]
    assert m['OLD_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0121['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0122['rsc_oam_runtime_binding_ready_0044()'][6]
    parser = pytest.importorskip('pglast.parser')
    parser.parse_plpgsql_json('CREATE FUNCTION lock_graph(requested_source_system_id uuid,requested_sync_run_id uuid) RETURNS void LANGUAGE plpgsql AS $body$'+repaired+'$body$')
    for direction in ('upgrade','downgrade'):
        output = StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            m[direction]()
        sql = output.getvalue(); parser.parse_sql(sql)
        assert 'GRANT ' not in sql and 'DROP TABLE ' not in sql and 'CREATE TABLE ' not in sql
        assert 'ACCESS EXCLUSIVE MODE' in sql and m['LEGACY_HASH'] in sql and m['FIXED_HASH'] in sql
        if direction == 'downgrade':
            assert sql.index('zero-control opening history must be retained') < sql.index('EXECUTE function_definition')
