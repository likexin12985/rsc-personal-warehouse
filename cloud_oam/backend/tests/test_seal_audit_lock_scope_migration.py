"""0123 changes only the placement of an existing no-op audit branch."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from app import database_security as security, oam_sync_scope_security as scope

PATH = Path(__file__).parents[1]/'alembic/versions/20261102_0123_seal_audit_lock_scope.py'


def test_scope_patch_preserves_every_seal_check_and_runtime_acl():
    m = runpy.run_path(str(PATH))
    legacy,repaired = m['_sources']()[m['SIGNATURE']]
    assert hashlib.sha256(legacy.encode()).hexdigest() == m['LEGACY_HASH']
    assert hashlib.sha256(repaired.encode()).hexdigest() == m['FIXED_HASH']
    assert repaired.replace(m['FIXED_FRAGMENT'],m['LEGACY_FRAGMENT']) == legacy
    assert repaired.index("IF TG_TABLE_NAME='audit_events'") < repaired.index('FOR UPDATE')
    assert m['FIXED_HASH'] == security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[('rsc_guard_stock_operation_seal_0101','')]
    assert m['OLD_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0122['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0123['rsc_oam_runtime_binding_ready_0044()'][6]
    parser = pytest.importorskip('pglast.parser')
    parser.parse_plpgsql_json('CREATE FUNCTION checked() RETURNS trigger LANGUAGE plpgsql AS $body$'+repaired+'$body$')
    for signature,(old,new,anchor,fragment) in m['PATCHES'].items():
        assert new.replace(fragment,anchor)==old
        key=(signature.removeprefix('public.').removesuffix('()'),'')
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==hashlib.sha256(new.encode()).hexdigest()
        parser.parse_plpgsql_json('CREATE FUNCTION checked() RETURNS trigger LANGUAGE plpgsql AS $body$'+new+'$body$')



@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_transition_uses_exact_source_and_private_acl_preflight_without_data_changes(direction):
    m = runpy.run_path(str(PATH)); output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        m[direction]()
    sql = output.getvalue(); pytest.importorskip('pglast.parser').parse_sql(sql)
    assert m['LEGACY_HASH'] in sql and m['FIXED_HASH'] in sql
    assert 'ACCESS EXCLUSIVE MODE' in sql and 'a.grantee<>p.proowner' in sql
    assert "t.tgenabled='A' AND t.tgdeferrable AND t.tginitdeferred" in sql
    assert sql.index('0123 return function source, ownership, ACL or trigger drift') < sql.index('EXECUTE function_definition')
    for mutation in ('GRANT ','DELETE FROM ','UPDATE public.','CREATE TABLE ','DROP TABLE ','DROP TRIGGER '):
        assert mutation not in sql


def test_sqlite_roundtrip_leaves_existing_data_and_schema_intact():
    m = runpy.run_path(str(PATH)); engine = create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as db, Operations.context(MigrationContext.configure(db)):
        db.exec_driver_sql('CREATE TABLE retained(id INTEGER PRIMARY KEY, value TEXT)')
        db.exec_driver_sql("INSERT INTO retained VALUES(1,'synthetic immutable history')")
        before = tuple(db.exec_driver_sql('SELECT * FROM sqlite_master'))
        m['upgrade'](); m['downgrade'](); m['upgrade']()
        assert tuple(db.exec_driver_sql('SELECT * FROM sqlite_master')) == before
        assert db.exec_driver_sql('SELECT * FROM retained').one() == (1,'synthetic immutable history')
