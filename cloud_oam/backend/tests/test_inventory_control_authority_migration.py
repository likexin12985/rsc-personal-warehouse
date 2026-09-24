"""0113 migration structure and exact startup catalog; PG16 runtime is separate."""
from io import StringIO
from pathlib import Path
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security
from app.database import Base
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0112,OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0113
from test_inventory_control_authority import db, PATH
from test_inventory_control_authority import world, prepared, history


def test_empty_actual_migration_roundtrip_and_permission_seed(db):
    migration=runpy.run_path(str(PATH));table=Decision.__tablename__
    assert set(Base.metadata.tables[table].columns.keys())=={row['name'] for row in sa.inspect(db.connection()).get_columns(table)}
    assert db.execute(sa.text("SELECT resource,action FROM permissions WHERE resource='inventory_control'")).one()==('inventory_control','authorize')
    assert db.execute(sa.text("SELECT r.code,rp.effect FROM role_permissions rp JOIN roles r ON r.id=rp.role_id JOIN permissions p ON p.id=rp.permission_id WHERE p.resource='inventory_control'")).one()==('admin','allow')
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']()
        assert not db.scalar(sa.text("SELECT count(*) FROM permissions WHERE resource='inventory_control'"))
        migration['upgrade']()
    assert db.scalar(sa.text('SELECT count(*) FROM inventory_control_authority_decisions'))==0


def test_pg16_sql_body_permissions_and_readiness_are_pinned():
    migration=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    assert migration['OLD_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0112['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0113['rsc_oam_runtime_binding_ready_0044()'][6]
    key=(migration['FUNCTION_NAME'],'')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==migration['FUNCTION_HASH']
    assert key not in set(security.RUNTIME_EXECUTE_FUNCTIONS)|security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert migration['TABLE'] in security.CONTROL_PRIVATE_TABLES
    assert not security._expected_table_privileges(migration['TABLE'])
    for name,(table,_,kind) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,migration['FUNCTION_NAME'],'A',kind,False,False,False)
    parser.parse_plpgsql_json(f"CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body${migration['BODY']}$body$")
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            migration[action]()
        sql=output.getvalue();parser.parse_sql(sql)
        if action=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==2
            assert 'TO star_oam_api' not in sql and 'TO edge_inbox' not in sql and 'TO star_oam_projector' not in sql
        else:assert sql.index('authority must be retained')<sql.index('DROP TABLE')


@pytest.mark.parametrize('change',['missing','select','insert','other_grantee','column'])
def test_private_authority_acl_drift_stops_startup(change):
    from test_database_security import _valid_table_acl
    rows=_valid_table_acl();row=next(row for row in rows if row['table_name']==Decision.__tablename__)
    if change=='missing':rows.remove(row)
    elif change=='select':row['can_select']=True
    elif change=='insert':row['can_insert']=True
    elif change=='other_grantee':row['has_unexpected_control_acl']=True
    else:row['has_explicit_runtime_column_acl']=True
    with pytest.raises(security.DatabaseSecurityBoundaryError):
        security._assert_runtime_table_acl(rows,expected_migration_role='star_oam_migrator')


def test_pg16_authority_import_and_retention_order():
    from pg16_inventory_control_authority_gate import assert_inventory_control_authority_gate
    assert callable(assert_inventory_control_authority_gate)
    text=(Path(__file__).parent/'test_postgresql16_release_gate.py').read_text()
    tail=text[text.rindex('from pg16_inventory_control_preparation_gate import'):]
    assert tail.index('0112 downgrade blocked')<tail.index('assert_inventory_control_authority_gate(control_owner_engine')
    assert tail.index('assert_inventory_control_authority_gate(control_owner_engine')<tail.index('0113 downgrade blocked')


def test_permission_catalog_drift_is_retained_on_downgrade(db):
    db.execute(sa.text("UPDATE role_permissions SET effect='deny'"));db.commit()
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='permission catalog drift'):runpy.run_path(str(PATH))['downgrade']()
    assert db.scalar(sa.text('SELECT effect FROM role_permissions'))=='deny'


def test_orphan_authority_audit_prevents_downgrade(db,world):
    from uuid import uuid4
    from app.inventory_control_authority import append_audit_event,_now
    now=_now(db)
    append_audit_event(db,stream_key='authorization',actor_user_id=world.actor.user_id,
        action='inventory_control.authority.source_grant',aggregate_type='inventory_control_authority_decision',
        aggregate_id=str(uuid4()),request_id='synthetic-orphan-'+uuid4().hex,before_jsonb={},after_jsonb={},occurred_at=now,created_at=now)
    db.commit();before=history(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='authority must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert history(db)==before
