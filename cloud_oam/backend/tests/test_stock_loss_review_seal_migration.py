"""Private seal proof catalog, parseable SQL, immutable schema and SQLite refusal."""
from io import StringIO
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser

from app import database_security as security, oam_sync_scope_security as scope
from app.stock_operation_models import StockLossReviewRequestSeal as Seal
from migration_script_cache import cache_migration_compilation


@pytest.fixture(scope='module')
def migration():
    folder = Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder/'20261205_0156_stock_loss_review_request_seals.py'))


def test_private_catalog_and_complete_bidirectional_guards(migration):
    m = migration
    assert m['OLD_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0155['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH'] == scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0156['rsc_oam_runtime_binding_ready_0044()'][6]
    for key, (args, result, body) in m['FUNCTIONS'].items():
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[key] == m['FUNCTION_HASHES'][key]
        assert security.FORMAL_FILE_INTERNAL_FUNCTION_SHAPES[key] == ('f',result,False)
        assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
        parser.parse_plpgsql_json(f'CREATE FUNCTION guard({args}) RETURNS {result} LANGUAGE plpgsql AS $b${body}$b$')
        assert not sa.text(body)._bindparams
    for name, (table, events, function, kind, deferred) in m['TRIGGERS'].items():
        assert len(name) <= 63
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table,function,'A',kind,deferred,deferred,deferred)
        if table == 'audit_events':
            assert security.EXPECTED_AUDIT_TRIGGERS[name] == (table,function,kind,deferred,deferred,deferred)
    assert m['TABLE'] in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES


@pytest.mark.parametrize('direction', ['upgrade','downgrade'])
def test_atomic_owner_transition_sql_and_history_retention(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        migration[direction]()
    sql = output.getvalue(); parser.parse_sql(sql)
    assert sql.index('direct schema owner required') < sql.index('LOCK TABLE public.alembic_version')
    assert 'DELETE FROM' not in sql and 'INSERT INTO permissions' not in sql
    if direction == 'downgrade':
        assert sql.index('history requires retention') < sql.index('DROP FUNCTION') < sql.index('DROP TABLE')
    else:
        assert 'GRANT SELECT,INSERT ON public.stock_loss_review_request_seals TO star_oam_api' in sql
        for name in migration['TRIGGERS']:assert 'ENABLE ALWAYS TRIGGER '+name in sql


def test_sqlite_insert_closed_empty_roundtrip_and_retained_seals(migration):
    engine = sa.create_engine('sqlite://')
    with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        for table in ('users','people','organizations'):
            db.exec_driver_sql('CREATE TABLE '+table+'(id CHAR(36) PRIMARY KEY)')
        db.exec_driver_sql('CREATE TABLE stock_operation_orders(id CHAR(36) PRIMARY KEY,operation_type TEXT,UNIQUE(id,operation_type))')
        migration['upgrade']()
        assert set(Seal.__table__.columns.keys()) == {c['name'] for c in sa.inspect(db).get_columns(migration['TABLE'])}
        values=dict(id=uuid4(),operation_id=uuid4(),operation_type='loss_report',stage='regional',
            owner_org_id=uuid4(),actor_user_id=str(uuid4()),reviewer_person_id=uuid4(),authorization_version=1,
            request_id='synthetic-original-request',idempotency_key_hash='a'*64,request_hash='b'*64,
            submission_plan_hash='c'*64,command_intent_jsonb={})
        with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL approval seal proof required'):
            db.execute(Seal.__table__.insert().values(**values))
        db.commit(); migration['downgrade']()
        assert migration['TABLE'] not in sa.inspect(db).get_table_names()
        migration['upgrade']()
        db.exec_driver_sql('DROP TRIGGER trg_loss_review_seal_insert_0156')
        db.execute(Seal.__table__.insert().values(**values)); db.commit()
        with pytest.raises(RuntimeError,match='history requires retention'):migration['downgrade']()
        for statement in ('DELETE FROM '+migration['TABLE'],'UPDATE '+migration['TABLE']+' SET stage=stage'):
            with pytest.raises(sa.exc.IntegrityError,match='immutable approval seals'):db.exec_driver_sql(statement)
    engine.dispose()
