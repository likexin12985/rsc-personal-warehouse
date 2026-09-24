"""0115 schema, guards, ACL and retention; actual PG16 execution is separate."""
from io import StringIO
import hashlib
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security
from app import oam_sync_scope_security as scope
from app.database import Base
from app.inventory_control_mapping_models import InventoryControlMappingDecision as Decision
from test_inventory_control_mapping import db,admission_db,authority_db,capture_world,world,login,PATH,command,apply
from test_inventory_control_admission import facts


def test_empty_actual_roundtrip_matches_metadata(db):
    migration=runpy.run_path(str(PATH));table=Decision.__tablename__
    assert set(Base.metadata.tables[table].columns.keys())=={row['name'] for row in sa.inspect(db.connection()).get_columns(table)}
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']();migration['upgrade']()
    assert db.scalar(sa.text('SELECT count(*) FROM inventory_control_mapping_decisions'))==0


def test_guards_and_runtime_manifest_pin_actual_sql():
    migration=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    key=(migration['FUNCTION_NAME'],'')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==migration['FUNCTION_HASH']
    assert key not in set(security.RUNTIME_EXECUTE_FUNCTIONS)|security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert migration['TABLE'] in security.CONTROL_PRIVATE_TABLES and not security._expected_table_privileges(migration['TABLE'])
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0114['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0115['rsc_oam_runtime_binding_ready_0044()'][6]
    older=runpy.run_path(str(next(PATH.parent.glob('*0052*.py'))))
    body=older['_oam_runtime_ready_function_sql'](older['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
    assert hashlib.sha256(body.replace(older['revision'],migration['revision']).encode()).hexdigest()==migration['NEW_HASH']
    parser.parse_plpgsql_json(f"CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body${migration['BODY']}$body$")
    for name,(table,_,kind) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,migration['FUNCTION_NAME'],'A',kind,False,False,False)
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):migration[action]()
        sql=output.getvalue();parser.parse_sql(sql)
        if action=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==2
            assert 'TO star_oam_api' not in sql and 'TO edge_inbox' not in sql and 'TO star_oam_projector' not in sql
        else:assert sql.index('mapping decisions must be retained')<sql.index('DROP TABLE')


@pytest.mark.parametrize('change',['missing','select','insert','other_grantee','column'])
def test_private_mapping_acl_drift_stops_startup(change):
    from test_database_security import _valid_table_acl
    rows=_valid_table_acl();row=next(row for row in rows if row['table_name']==Decision.__tablename__)
    if change=='missing':rows.remove(row)
    elif change=='select':row['can_select']=True
    elif change=='insert':row['can_insert']=True
    elif change=='other_grantee':row['has_unexpected_control_acl']=True
    else:row['has_explicit_runtime_column_acl']=True
    with pytest.raises(security.DatabaseSecurityBoundaryError):security._assert_runtime_table_acl(rows,expected_migration_role='star_oam_migrator')


def test_decisions_cannot_be_updated_deleted_or_downgraded(db,world,login):
    apply(db,login,command(db,world));before=facts(db)
    for sql in ['UPDATE inventory_control_mapping_decisions SET rules_revision=rules_revision','DELETE FROM inventory_control_mapping_decisions']:
        with pytest.raises(sa.exc.IntegrityError,match='append-only'):
            with db.begin_nested():db.execute(sa.text(sql))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='mapping decisions must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


def test_orphan_mapping_audit_is_retained(db,world):
    from uuid import uuid4
    from app.inventory_control_authority import _now
    from app.formal_services.audit_chain import append_audit_event
    now=_now(db)
    append_audit_event(db,stream_key='authorization',actor_user_id=world.actor.user_id,
        action='inventory_control.mapping.grant',aggregate_type='inventory_control_mapping_decision',aggregate_id=str(uuid4()),
        request_id='synthetic-orphan-'+uuid4().hex,before_jsonb={},after_jsonb={},occurred_at=now,created_at=now)
    db.commit();before=facts(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='mapping decisions must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


def test_pg16_helper_runs_after_prior_retention_and_before_mapping_retention():
    from pg16_inventory_control_mapping_gate import assert_inventory_control_mapping_gate
    assert callable(assert_inventory_control_mapping_gate)
    source=(PATH.parents[2]/'tests/test_postgresql16_release_gate.py').read_text()
    tail=source[source.rindex('from pg16_inventory_control_preparation_gate import'):]
    assert tail.index('0114 downgrade blocked')<tail.index('assert_inventory_control_mapping_gate(control_owner_engine')<tail.index('0115 downgrade blocked')
