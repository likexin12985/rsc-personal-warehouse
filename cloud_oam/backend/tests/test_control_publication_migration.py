"""0121 real SQLite preservation and exact PostgreSQL guard contracts."""
from io import StringIO
import hashlib
import runpy
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app import database_security as security, oam_sync_scope_security as scope
from app.database import Base
from app.formal_services.audit_chain import append_audit_event
from test_inventory_control_projection import (
    db, material_db, mapping_db, admission_db, authority_db, capture_world, world, login, fresh,
    material_world, grants, facts, apply, command, credentials, capture_rows, authority, setup, request,
    PATH, publish,
)


def test_empty_roundtrip_metadata_and_permissions(db):
    m=runpy.run_path(str(PATH));before=facts(db)
    for table in m['TABLES']:
        assert set(Base.metadata.tables[table].columns.keys())=={c['name'] for c in sa.inspect(db.connection()).get_columns(table)}
    with Operations.context(MigrationContext.configure(db.connection())):
        m['downgrade']();m['upgrade']()
    assert facts(db)==before


def test_exact_postgres_function_trigger_acl_and_readiness():
    m=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    assert m['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0120['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0121['rsc_oam_runtime_binding_ready_0044()'][6]
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
            assert sql.count('ENABLE ALWAYS TRIGGER')==len(m['TRIGGERS'])+1
            assert 'TO star_oam_api' not in sql and 'TO edge_inbox' not in sql and 'TO star_oam_projector' not in sql
        else:assert sql.index('control publications must be retained')<sql.index('DROP TABLE')


@pytest.mark.parametrize('orphan',[False,True])
def test_retention_and_orphan_audit_refuse_downgrade(db,world,login,fresh,material_world,orphan):
    if orphan:
        now=authority._now(db)
        append_audit_event(db,stream_key='authorization',actor_user_id=world.actor.user_id,action='inventory_control.publish',
            aggregate_type='control_projection_publication',aggregate_id=str(uuid4()),request_id=uuid4().hex,
            before_jsonb={},after_jsonb={},occurred_at=now,created_at=now);db.commit()
    else:
        grant=setup(db,world,login,fresh);publish(db,world,login,grant)
    before=facts(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError,match='control publications must be retained'):runpy.run_path(str(PATH))['downgrade']()
    assert facts(db)==before


@pytest.mark.parametrize('table',['control_projection_publications','control_projection_lines','control_projection_origins','control_projection_closures'])
@pytest.mark.parametrize('operation',['UPDATE','DELETE'])
def test_actual_facts_append_only(db,world,login,fresh,material_world,table,operation):
    grant=setup(db,world,login,fresh);publish(db,world,login,grant)
    fresh(records=[]);publish(db,world,login,grant)
    before=facts(db)
    sql=f'UPDATE {table} SET payload_sha256=payload_sha256' if operation=='UPDATE' else f'DELETE FROM {table}'
    with pytest.raises(sa.exc.IntegrityError,match='append-only'):db.execute(sa.text(sql))
    db.rollback();assert facts(db)==before
