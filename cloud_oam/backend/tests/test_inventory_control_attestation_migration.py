"""Forward schema and exact startup contract; not a PostgreSQL runtime result."""
from io import StringIO
from pathlib import Path
import hashlib
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.database import Base
from app import oam_sync_scope_security as scope
from app.edge_database_security import _BOUNDARY_SQL
from app.inventory_control_attestation_models import InventoryControlCaptureAttestation as Receipt
from test_inventory_control_attestation import db, PATH, preparation_db


def test_actual_empty_migration_roundtrip_matches_metadata(db):
    migration=runpy.run_path(str(PATH));table=Receipt.__tablename__
    assert set(Base.metadata.tables[table].columns.keys())=={row['name'] for row in sa.inspect(db.connection()).get_columns(table)}
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']();assert table not in sa.inspect(db.connection()).get_table_names()
        migration['upgrade']()
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0


def test_pg16_guard_rls_acl_and_readiness_sql_are_pinned():
    migration=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    signature=migration['FUNCTION_NAME']+'()'
    assert scope.OAM_SYNC_FUNCTION_MANIFEST[signature]==(True,'v','plpgsql','trigger',False,'u',migration['FUNCTION_HASH'])
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0113['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0114['rsc_oam_runtime_binding_ready_0044()'][6]
    # Derive both readiness hashes from the last complete immutable definition.
    older=runpy.run_path(str(next(PATH.parent.glob('*0052*.py'))))
    body=older['_oam_runtime_ready_function_sql'](older['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
    for revision,expected in ((migration['down_revision'],migration['OLD_HASH']),(migration['revision'],migration['NEW_HASH'])):
        assert hashlib.sha256(body.replace(older['revision'],revision).encode()).hexdigest()==expected
    assert migration['POLICY']==scope.CAPTURE_POLICY
    assert "('inventory_control_capture_attestations', TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE)" in str(_BOUNDARY_SQL)
    assert "UNION ALL SELECT 'capture_acl'" in str(scope._RLS_BOUNDARY_SQL)
    # Parse the whole runtime query as well as the DDL. PUBLIC has no pg_roles
    # row, so NULL must be treated as an unauthorized grantee in this boundary.
    boundary = str(scope._RLS_BOUNDARY_SQL)
    parser.parse_sql(boundary.replace(':runtime_role', "'edge_inbox'").replace(':migration_role', "'star_oam_migrator'"))
    assert 'a.grantee<>c.relowner AND NOT COALESCE((' in boundary
    for name,(_,kind) in migration['TRIGGERS'].items():
        assert (migration['TABLE'],name,signature,kind,False,False,False) in scope.EXPECTED_TRIGGERS
    parser.parse_plpgsql_json(f"CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body${migration['BODY']}$body$")
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            migration[action]()
        sql=output.getvalue();parser.parse_sql(sql)
        if action=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==2 and sql.count('CREATE POLICY')==4
            assert 'FORCE ROW LEVEL SECURITY' in sql and 'GRANT SELECT,INSERT ON TABLE' in sql
            assert 'TO star_oam_api' not in sql and 'TO star_oam_projector' not in sql
        else:assert sql.index('capture receipts must be retained')<sql.index('DROP TABLE')


def test_capture_contract_follows_original_inventory_scope_function():
    sql=str(scope._RLS_BOUNDARY_SQL)
    assert "'external_sync_snapshot_records'::text, 'select'::text" in scope.CAPTURE_POLICY
    assert 'to_jsonb(inventory_control_capture_attestations.*)' in scope.CAPTURE_POLICY
    assert 'capture_acl' in sql and 'edge_inbox' in sql and 'star_oam_backup' in sql
    assert len(scope.CAPTURE_POLICIES)==4
    assert {row[3] for row in scope.CAPTURE_POLICIES}=={'edge_inbox','star_oam_migrator','star_oam_backup'}


def test_pg16_helper_is_after_prior_retention_proofs_and_new_retention_is_last():
    from pg16_inventory_control_attestation_gate import assert_inventory_control_attestation_gate
    assert callable(assert_inventory_control_attestation_gate)
    source=(Path(__file__).parent/'test_postgresql16_release_gate.py').read_text()
    tail=source[source.rindex('from pg16_inventory_control_preparation_gate import'):]
    assert tail.index('0113 downgrade blocked')<tail.index('assert_inventory_control_attestation_gate(control_owner_engine')
    assert tail.index('assert_inventory_control_attestation_gate(control_owner_engine')<tail.index('0114 downgrade blocked')
