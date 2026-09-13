"""Reviewed transit scopes extend, rather than bypass, formal opening."""
from io import StringIO
from pathlib import Path
import runpy
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0102

PATH = Path(__file__).parents[1] / 'alembic/versions/20261012_0102_transit_opening_scopes.py'


def test_manifest_pins_every_changed_body_and_new_guard():
    m = runpy.run_path(str(PATH))
    expected = {**security.RUNTIME_FUNCTION_BODY_SHA256, **security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256}
    for signature, (old, new, changes) in m['SOURCE_CHANGES'].items():
        assert old != new and changes
        if signature == 'rsc_oam_runtime_binding_ready_0044()':
            assert OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0102[signature][6] == new
        else:
            name, args = signature[:-1].split('(', 1)
            assert expected[(name, args)] == new
            assert all('regional_parent.location_type' not in old for old, _, _ in changes)
    key = (m['GUARD_NAME'], '')
    assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key] == m['GUARD_HASH']
    for name, (table, _, flags) in m['TRIGGERS'].items():
        value = (table, m['GUARD_NAME'], 'A', flags, False, False, False)
        assert security.EXPECTED_OPENING_TERMINAL_TRIGGERS[name] == value[:4]
        if table == 'stocktake_scopes': assert security.EXPECTED_STOCKTAKE_SCOPE_TRIGGERS[name] == value


@pytest.mark.parametrize('action', ['upgrade', 'downgrade'])
def test_transition_parses_without_bind_parameters(action):
    m = runpy.run_path(str(PATH)); output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})):
        m[action]()
    parser = pytest.importorskip('pglast.parser')
    parser.parse_sql(output.getvalue())
    definition = f"CREATE FUNCTION {m['GUARD_NAME']}() RETURNS trigger LANGUAGE plpgsql AS $body${m['GUARD_BODY']}$body$"
    parser.parse_plpgsql_json(definition)
    assert not sa.text(definition)._bindparams


def test_sqlite_downgrade_retains_used_transit_scope():
    m = runpy.run_path(str(PATH)); engine = sa.create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE stock_locations(id TEXT PRIMARY KEY, location_type TEXT)')
        db.exec_driver_sql('CREATE TABLE stocktake_scopes(location_id TEXT)')
        m['upgrade'](); m['downgrade'](); m['upgrade']()
        db.exec_driver_sql("INSERT INTO stock_locations VALUES ('synthetic-transit','transit')")
        db.exec_driver_sql("INSERT INTO stocktake_scopes VALUES ('synthetic-transit')")
        with pytest.raises(RuntimeError, match='transit stocktake history must be retained'): m['downgrade']()
        assert db.exec_driver_sql('SELECT count(*) FROM stocktake_scopes').scalar_one() == 1
