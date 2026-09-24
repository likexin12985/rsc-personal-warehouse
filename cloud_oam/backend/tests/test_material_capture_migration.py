"""0116 SQL/schema/ACL contracts, with actual PostgreSQL execution still separate."""
import hashlib
import re
from io import StringIO
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.database import Base
from app import oam_sync_scope_security as scope
from app.edge_database_security import _BOUNDARY_SQL
from test_material_capture_ingress import db,world,PATH,Binding,Receipt,receive,facts


def test_actual_empty_migration_roundtrip_matches_metadata(db):
    migration=runpy.run_path(str(PATH))
    for table in (Binding.__tablename__,Receipt.__tablename__):
        assert set(Base.metadata.tables[table].columns.keys())=={row['name'] for row in sa.inspect(db.connection()).get_columns(table)}
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']()
        assert Receipt.__tablename__ not in sa.inspect(db.connection()).get_table_names()
        migration['upgrade']()
    assert db.scalar(sa.select(sa.func.count()).select_from(Receipt))==0


@pytest.mark.parametrize('received',[False,True])
def test_registered_keys_and_receipts_are_retained_even_without_formal_projection(db,world,received):
    if received:receive(db,world.capture)
    before=facts(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='material transport evidence must be retained'):
            runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


def test_postgresql_whole_ddl_function_bodies_and_catalog_queries_parse():
    parser=pytest.importorskip('pglast.parser')
    migration=runpy.run_path(str(PATH))
    for signature,(args,result,body,private) in migration['FUNCTIONS'].items():
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$')
        assert scope.OAM_SYNC_FUNCTION_MANIFEST[signature]==(private,'v','plpgsql',result,False,'u',hashlib.sha256(body.encode()).hexdigest())
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            migration[action]()
        sql=output.getvalue()
        parser.parse_sql(sql)
        if action=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==4 and sql.count('FORCE ROW LEVEL SECURITY')==2
            assert sql.count('CREATE POLICY')==6
            assert 'TO star_oam_api' not in sql and 'TO star_oam_projector' not in sql
        else:
            assert sql.index('material transport evidence must be retained')<sql.index('DROP TABLE')
    for sql in (str(scope._RLS_BOUNDARY_SQL),str(_BOUNDARY_SQL)):
        parser.parse_sql(sql.replace(':runtime_role',"'edge_inbox'").replace(':migration_role',"'star_oam_migrator'").replace(':edge_role',"'edge_inbox'"))


def test_readiness_and_exact_material_acl_and_always_triggers_are_pinned():
    migration=runpy.run_path(str(PATH))
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0115['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0116['rsc_oam_runtime_binding_ready_0044()'][6]
    older=runpy.run_path(str(next(PATH.parent.glob('*0052*.py'))))
    body=older['_oam_runtime_ready_function_sql'](older['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
    assert hashlib.sha256(body.replace(older['revision'],migration['revision']).encode()).hexdigest()==migration['NEW_HASH']
    assert migration['POLICY']==scope.MATERIAL_POLICY
    assert len(scope.MATERIAL_POLICIES)==6
    for table,name,function,_,kind in migration['TRIGGERS']:
        assert (table,name,function,kind,False,False,False) in scope.EXPECTED_TRIGGERS
    sql=str(scope._RLS_BOUNDARY_SQL)
    assert 'material_acl_boundary' in sql and 'expected.signature NOT IN' in sql
    assert "('oam_receipt_evidence','inventory_control_capture_attestations'," in sql
    assert "('oam_material_capture_receipts', TRUE, TRUE, FALSE, FALSE, FALSE, FALSE, FALSE)" in str(_BOUNDARY_SQL)
    assert "('oam_material_capture_bindings', TRUE" not in str(_BOUNDARY_SQL)


def test_real_pg_helper_is_wired_after_all_prior_retention_checks():
    from pg16_material_capture_gate import assert_material_capture_gate
    assert callable(assert_material_capture_gate)
    source=(PATH.parents[2]/'tests/test_postgresql16_release_gate.py').read_text()
    tail=source[source.rindex('from pg16_inventory_control_preparation_gate import'):]
    assert tail.index('0115 downgrade blocked')<tail.index('assert_material_capture_gate(control_owner_engine')<tail.index('0116 downgrade blocked')


def test_transport_provisioning_is_explicit_preview_and_retains_exact_metadata():
    parser=pytest.importorskip('pglast.parser')
    path=PATH.parents[3]/'deployment/provision_oam_material_capture.sql'
    source=path.read_text()
    sql='\n'.join(line for line in source.splitlines() if not line.startswith('\\'))
    sql=sql.replace(":'registration_json'","'{}'").replace(":'apply'","'PREVIEW'")
    parser.parse_sql(sql)
    parser.parse_plpgsql_json('CREATE FUNCTION provisioning_check() RETURNS void LANGUAGE plpgsql AS $body$'+source.split('DO $body$',1)[1].split('$body$;',1)[0]+'$body$')
    assert '\\set apply PREVIEW' in source and "='REGISTER_TRANSPORT_ONLY'" in source
    assert 'refusing replacement' in source and 'revoked_at IS NULL' in source
    assert 'INSERT INTO public.oam_material_capture_bindings' in source
    assert 'INSERT INTO public.materials' not in source and 'DELETE FROM' not in source


def test_edge_deployment_grants_and_verification_sql_parse_with_material_scope():
    parser = pytest.importorskip('pglast.parser')
    for name in ('create_oam_edge_staging.sql', 'verify_oam_edge_staging.sql'):
        source = (PATH.parents[3] / 'deployment' / name).read_text()
        sql = '\n'.join(';' if line.startswith(('\\gset', '\\gexec')) else line
                        for line in source.splitlines()
                        if not line.startswith('\\') or line.startswith(('\\gset', '\\gexec')))
        sql = re.sub(r":'([a-z_]+)'", "'edge_inbox'", sql)
        sql = re.sub(r':"([a-z_]+)"', 'edge_inbox', sql)
        parser.parse_sql(sql)
        assert 'rsc_oam_material_capture_binding_0116(text,text,text)' in source
        assert 'rsc_oam_material_capture_visible_0116(text)' in source
