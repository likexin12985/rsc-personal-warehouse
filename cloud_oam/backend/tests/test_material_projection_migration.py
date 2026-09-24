"""0119 actual SQLite retention plus frozen PG role/graph SQL contracts."""
from io import StringIO
import hashlib
import runpy
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security,oam_sync_scope_security as scope
from app.database import Base
from app.formal_services.audit_chain import append_audit_event
from test_material_projection import db,source_db,ingress_db,transport_world,world,login,PATH,Publication,Line,prepare,apply,facts,authority


def test_actual_empty_roundtrip_matches_metadata_without_permission_changes(db):
    migration=runpy.run_path(str(PATH))
    for table in migration['TABLES']:
        assert set(Base.metadata.tables[table].columns.keys())=={c['name'] for c in sa.inspect(db.connection()).get_columns(table)}
    before=facts(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']();migration['upgrade']()
    assert facts(db)==before


def test_postgresql_function_trigger_acl_and_readiness_manifests():
    m=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    assert m['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0118['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0119['rsc_oam_runtime_binding_ready_0044()'][6]
    for name,(body,definer) in m['FUNCTIONS'].items():
        key=(name,'')
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==hashlib.sha256(body.encode()).hexdigest()
        assert (key in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS)==definer
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body$'+body+'$body$')
    for name,(table,function,events,kind,defer) in m['TRIGGERS'].items():
        assert len(name)<=63 and security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,'A',kind,defer,defer,defer)
    for table in m['TABLES']:
        assert table in security.CONTROL_PRIVATE_TABLES and not security._expected_table_privileges(table)
    for direction in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            m[direction]()
        sql=output.getvalue();parser.parse_sql(sql)
        if direction=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==15
            assert sql.count('DEFERRABLE INITIALLY DEFERRED')==7
            assert 'TO star_oam_api' not in sql and 'TO edge_inbox' not in sql and 'TO star_oam_projector' not in sql
        else:assert sql.index('material publications must be retained')<sql.index('DROP TABLE')


@pytest.mark.parametrize('orphan',[False,True])
def test_retention_or_orphan_audit_blocks_downgrade_without_deleting_anything(db,world,login,orphan):
    if orphan:
        now=authority._now(db)
        append_audit_event(db,stream_key='authorization',actor_user_id=world.actor.user_id,action='material_source.publish',
            aggregate_type='material_projection_publication',aggregate_id=str(uuid4()),request_id='synthetic-'+uuid4().hex,
            before_jsonb={},after_jsonb={},occurred_at=now,created_at=now)
        db.commit()
    else:
        _,cmd=prepare(db,world,login);apply(db,login,cmd)
    before=facts(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='material publications must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


@pytest.mark.parametrize('table',[Publication.__tablename__,Line.__tablename__])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_actual_facts_are_append_only(db,world,login,table,operation):
    _,cmd=prepare(db,world,login);apply(db,login,cmd);before=facts(db)
    sql=f'UPDATE {table} SET payload_sha256=payload_sha256' if operation=='UPDATE' else f'DELETE FROM {table}'
    with pytest.raises(sa.exc.IntegrityError,match='append-only'):db.execute(sa.text(sql))
    db.rollback();assert facts(db)==before


@pytest.mark.parametrize('table',[Publication.__tablename__,Line.__tablename__])
@pytest.mark.parametrize('change',['missing','read','write','other_role'])
def test_private_publication_acl_drift_blocks_runtime_startup(table,change):
    from test_database_security import _valid_table_acl
    rows=_valid_table_acl();row=next(row for row in rows if row['table_name']==table)
    if change=='missing':rows.remove(row)
    elif change=='read':row['can_select']=True
    elif change=='write':row['can_insert']=True
    else:row['has_unexpected_control_acl']=True
    with pytest.raises(security.DatabaseSecurityBoundaryError):security._assert_runtime_table_acl(rows,expected_migration_role='star_oam_migrator')
