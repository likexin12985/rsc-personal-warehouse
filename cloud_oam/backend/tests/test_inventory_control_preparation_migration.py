"""0112 structural/ACL evidence. Real PostgreSQL enforcement is gated apart."""
from io import StringIO
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import database_security as security
from app.database import Base
from app.inventory_control_models import TABLES
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0111,OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0112
from test_inventory_control_preparation import db,prepared,record,facts,PATH


def test_empty_forward_roundtrip_and_orm_match(db):
    migration=runpy.run_path(str(PATH))
    for table in TABLES:
        columns=sa.inspect(db.connection()).get_columns(table)
        assert {column['name'] for column in columns}==set(Base.metadata.tables[table].columns.keys())
    with Operations.context(MigrationContext.configure(db.connection())):
        migration['downgrade']();assert not set(TABLES)&set(sa.inspect(db.connection()).get_table_names())
        migration['upgrade']()
    assert all(not rows for rows in facts(db))


def test_pg16_ddl_private_acl_guard_and_readiness_pins():
    migration=runpy.run_path(str(PATH));parser=pytest.importorskip('pglast.parser')
    assert migration['OLD_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0111['rsc_oam_runtime_binding_ready_0044()'][6]
    assert migration['NEW_HASH']==OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0112['rsc_oam_runtime_binding_ready_0044()'][6]
    assert set(TABLES)==security.CONTROL_PREPARATION_PRIVATE_TABLES
    for table in TABLES:assert not security._expected_table_privileges(table)
    key=(migration['FUNCTION_NAME'],'')
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==migration['FUNCTION_HASH']
    assert key not in set(security.RUNTIME_EXECUTE_FUNCTIONS)|security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for name,(table,events,kind) in migration['TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,migration['FUNCTION_NAME'],'A',kind,False,False,False)
    parser.parse_plpgsql_json(f"CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $body${migration['BODY']}$body$")
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):migration[action]()
        sql=output.getvalue();parser.parse_sql(sql)
        assert 'LOCK TABLE public.alembic_version' in sql
        if action=='upgrade':
            assert sql.count('ENABLE ALWAYS TRIGGER')==10
            assert 'TO star_oam_api' not in sql and 'TO star_oam_projector' not in sql and 'TO edge_inbox' not in sql
            assert 'GRANT SELECT ON TABLE' in sql and 'TO star_oam_backup' in sql
        else:assert sql.index('facts must be retained')<sql.index('DROP TABLE')


@pytest.mark.parametrize('change',['missing','select','insert','unexpected_role','owner','column'])
def test_runtime_startup_refuses_private_fact_table_drift(change):
    from test_database_security import _valid_table_acl
    rows=_valid_table_acl();row=next(row for row in rows if row['table_name']==TABLES[0])
    if change=='missing':rows.remove(row)
    elif change=='select':row['can_select']=True
    elif change=='insert':row['can_insert']=True
    elif change=='unexpected_role':row['has_unexpected_control_acl']=True
    elif change=='column':row['has_explicit_runtime_column_acl']=True
    else:row['owner_name']='star_oam_api'
    with pytest.raises(security.DatabaseSecurityBoundaryError):security._assert_runtime_table_acl(rows,expected_migration_role='star_oam_migrator')


def test_cross_catalog_capture_cannot_be_rebound_by_foreign_keys(db,prepared):
    first=record(db,prepared);db.commit();before=facts(db)
    with pytest.raises(sa.exc.IntegrityError),db.begin_nested():
        db.execute(sa.text("INSERT INTO inventory_control_preparations (id,binding_id,catalog_id,capture_chain_id,checked_at,control_manifest_jsonb,control_manifest_sha256,created_at) SELECT :id,:binding,catalog_id,capture_chain_id,checked_at,control_manifest_jsonb,:digest,created_at FROM inventory_control_preparations"),
            {'id':'f'*32,'binding':'e'*32,'digest':'a'*64})
    assert facts(db)==before


def test_pg16_preparation_helper_import_and_retention_order():
    from pathlib import Path
    from pg16_inventory_control_preparation_gate import assert_inventory_control_preparation_gate
    assert callable(assert_inventory_control_preparation_gate)
    source=(Path(__file__).parent/'test_postgresql16_release_gate.py').read_text()
    tail=source[source.rindex('from pg16_stock_return_inbound_gate import'):]
    assert tail.index('0111 downgrade blocked') < tail.index('assert_inventory_control_preparation_gate(control_owner_engine')
    assert tail.index('assert_inventory_control_preparation_gate(control_owner_engine') < tail.index('0112 downgrade blocked')
