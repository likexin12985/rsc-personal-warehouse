"""0117 real SQLite/seed retention and frozen PostgreSQL contracts."""
from io import StringIO
import hashlib
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security
from app import oam_sync_scope_security as scope
from app.database import Base
from app.foundation_models import Permission,RolePermission,Role
from app.formal_services.audit_chain import append_audit_event
from test_material_source_authority import db,ingress_db,transport_world,world,login,PATH,Decision,command,apply,facts,authority


def test_empty_actual_roundtrip_matches_metadata_and_seeds_only_headquarters_permission(db):
    migration=runpy.run_path(str(PATH));table=Decision.__tablename__
    assert set(Base.metadata.tables[table].columns.keys())=={row['name'] for row in sa.inspect(db.connection()).get_columns(table)}
    permission=db.get(Permission,migration['PERMISSION_ID'])
    assert permission.resource=='material_source' and permission.action=='authorize'
    rows=db.execute(sa.select(Role.code,RolePermission.effect).join(RolePermission,RolePermission.role_id==Role.id)
        .where(RolePermission.permission_id==permission.id)).all()
    assert rows==[('admin','allow')]
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']();migration['upgrade']()
    assert db.scalar(sa.select(sa.func.count()).select_from(Decision))==0


def test_pg_guards_readiness_acl_and_seed_are_pinned():
    migration=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser');key=(migration['FUNCTION_NAME'],'')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==migration['FUNCTION_HASH']
    assert key not in set(security.RUNTIME_EXECUTE_FUNCTIONS)|security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    assert migration['TABLE'] in security.CONTROL_PRIVATE_TABLES and not security._expected_table_privileges(migration['TABLE'])
    assert migration['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0116['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0117['rsc_oam_runtime_binding_ready_0044()'][6]
    older=runpy.run_path(str(next(PATH.parent.glob('*0052*.py'))))
    body=older['_oam_runtime_ready_function_sql'](older['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
    assert hashlib.sha256(body.replace(older['revision'],migration['revision']).encode()).hexdigest()==migration['NEW_HASH']
    parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body$'+migration['BODY']+'$body$')
    assert "permission.resource='material_source'" in migration['BODY'] and "p.resource='material_source'" in migration['BODY']
    assert 'inventory_control_source_bindings' not in migration['BODY']
    for name,(table,_,kind) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,migration['FUNCTION_NAME'],'A',kind,False,False,False)
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):migration[action]()
        sql=output.getvalue();parser.parse_sql(sql)
        if action=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==2
            assert 'TO star_oam_api' not in sql and 'TO edge_inbox' not in sql and 'TO star_oam_projector' not in sql
        else:
            assert sql.index('material source authority must be retained')<sql.index('DROP TABLE')
            assert sql.index('LOCK TABLE public.permissions, public.role_permissions IN SHARE MODE')<sql.index('material authority permission catalog drift')<sql.index('DELETE FROM role_permissions')


@pytest.mark.parametrize('change',['resource','field','effect','extra','missing'])
def test_empty_downgrade_preserves_changed_permission_catalog(db,change):
    migration=runpy.run_path(str(PATH))
    permission=db.get(Permission,migration['PERMISSION_ID'])
    relation=db.get(RolePermission,migration['ROLE_PERMISSION_ID'])
    if change=='resource':permission.resource='synthetic_material_review'
    elif change=='field':permission.field_code='synthetic'
    elif change=='effect':relation.effect='deny'
    elif change=='missing':db.delete(relation)
    else:
        role=Role(code='provincial_manager',name='Synthetic reviewer',is_external=False,status='active')
        db.add(role);db.flush()
        db.add(RolePermission(role_id=role.id,permission_id=permission.id,effect='allow'))
    db.commit()
    def snapshot():
        return [db.execute(sa.text('SELECT * FROM '+name+' ORDER BY id')).all() for name in ('permissions','role_permissions')]
    before=snapshot()
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='material authority permission catalog drift'):migration['downgrade']()
    assert snapshot()==before and Decision.__tablename__ in sa.inspect(db.connection()).get_table_names()


@pytest.mark.parametrize('change',['missing','select','insert','other_grantee','column'])
def test_material_authority_acl_drift_stops_startup(change):
    from test_database_security import _valid_table_acl
    rows=_valid_table_acl();row=next(row for row in rows if row['table_name']==Decision.__tablename__)
    if change=='missing':rows.remove(row)
    elif change=='select':row['can_select']=True
    elif change=='insert':row['can_insert']=True
    elif change=='other_grantee':row['has_unexpected_control_acl']=True
    else:row['has_explicit_runtime_column_acl']=True
    with pytest.raises(security.DatabaseSecurityBoundaryError):security._assert_runtime_table_acl(rows,expected_migration_role='star_oam_migrator')


@pytest.mark.parametrize('orphan',[False,True])
def test_decisions_or_orphan_audit_prevent_destructive_downgrade(db,world,login,orphan):
    if orphan:
        now=authority._now(db)
        append_audit_event(db,stream_key='authorization',actor_user_id=world.actor.user_id,
            action='material_source.authority.grant',aggregate_type='material_source_authority_decision',aggregate_id=str(uuid4()),
            request_id='synthetic-material-orphan-'+uuid4().hex,before_jsonb={},after_jsonb={},occurred_at=now,created_at=now)
        db.commit()
    else:apply(db,login,command(db,world))
    before=facts(db)
    if not orphan:
        for sql in ['UPDATE material_source_authority_decisions SET subject_sha256=subject_sha256','DELETE FROM material_source_authority_decisions']:
            with pytest.raises(sa.exc.IntegrityError,match='append-only'):
                with db.begin_nested():db.execute(sa.text(sql))
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='material source authority must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


def test_pg_helper_runs_after_prior_retention_checks():
    from pg16_material_source_authority_gate import assert_material_source_authority_gate
    assert callable(assert_material_source_authority_gate)
    source=(PATH.parents[2]/'tests/test_postgresql16_release_gate.py').read_text()
    tail=source[source.rindex('from pg16_inventory_control_preparation_gate import'):]
    assert tail.index('0116 downgrade blocked')<tail.index('assert_material_source_authority_gate(control_owner_engine')<tail.index('0117 downgrade blocked')
