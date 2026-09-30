"""0157 formal catalog, parse and SQLite retention checks; actual PG16 proof is separate."""
from io import StringIO
from pathlib import Path
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser
from migration_script_cache import cache_migration_compilation

from app import database_security as security, oam_sync_scope_security as scope
from app.stock_operation_models import StockOperationCommandSeal
import hashlib
import runpy

@pytest.fixture(scope='module')
def migration():
    folder = Path(__file__).parents[1]/'alembic/versions'
    with cache_migration_compilation(folder):
        return runpy.run_path(str(folder/'20261206_0157_loss_return_sender_seals.py'))


def test_formal_catalog_orm_and_head_match_reviewed_migration(migration):
    m=migration; key=(m['NAME'],'')
    assert m['OLD_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0156['rsc_oam_runtime_binding_ready_0044()'][6]
    assert m['NEW_READY_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0157['rsc_oam_runtime_binding_ready_0044()'][6]
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256_THROUGH_0156[key]==hashlib.sha256(m['BASE'].encode()).hexdigest()
    assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[key]==hashlib.sha256(m['BODY'].encode()).hexdigest()
    assert key not in security.RUNTIME_EXECUTE_FUNCTIONS
    source=next(c for c in StockOperationCommandSeal.__table__.constraints if c.name=='ck_stock_operation_seals_source')
    assert str(source.sqltext)==m['SOURCE_CHECK']
    for name,table in m['GUARD_TRIGGERS'].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,m['NAME'],'A',5,True,True,True)


@pytest.mark.parametrize('direction', ('upgrade', 'downgrade'))
def test_postgresql_transition_is_parseable_owner_only_and_non_destructive(migration, direction):
    output = StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql', opts={
            'as_sql': True, 'output_buffer': output})):
        migration[direction]()
    sql = output.getvalue()
    parser.parse_sql(sql)
    parser.parse_plpgsql_json('CREATE FUNCTION guard() RETURNS trigger LANGUAGE plpgsql AS $b$'
                             + migration['BODY'] + '$b$')
    assert not sa.text(migration['BODY'])._bindparams
    assert sql.index('direct schema owner required') < sql.index('LOCK TABLE public.alembic_version')
    assert all(word not in sql for word in ('DELETE FROM', 'TRUNCATE ', 'GRANT '))
    if direction == 'downgrade':
        assert sql.index('immutable loss sender seals require retention') < sql.index('DROP CONSTRAINT')


def test_real_previous_schema_keeps_sqlite_closed_and_preserves_cross_table_guards(migration):
    engine = sa.create_engine('sqlite://')
    try:
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            for statement in (
                'CREATE TABLE oam_work_orders(id UUID PRIMARY KEY)',
                'CREATE TABLE stock_loss_dispositions(id UUID PRIMARY KEY)',
                'CREATE TABLE stock_operation_orders(id UUID PRIMARY KEY,loss_headquarters_decision_id UUID)',
                'CREATE TABLE stock_operation_shipments(id UUID PRIMARY KEY,operation_id UUID)',
                'CREATE TABLE stock_operation_receipts(id UUID PRIMARY KEY,shipment_id UUID)',
                'CREATE TABLE stock_operation_command_seals(id UUID PRIMARY KEY,oam_work_order_id UUID NOT NULL,operation_type TEXT NOT NULL)',
                "CREATE TABLE stock_operation_return_inbound_lines(id UUID PRIMARY KEY,line_no INT,accepted_qty NUMERIC,condition_code TEXT,CONSTRAINT ck_stock_operation_return_inbound_lines_context CHECK(line_no>0 AND accepted_qty>0 AND condition_code IN ('used','damaged')))",
                "CREATE TRIGGER guard_existing_seals BEFORE DELETE ON stock_operation_command_seals BEGIN SELECT RAISE(ABORT,'historical seal retained'); END",
                "CREATE TRIGGER guard_cross_table BEFORE UPDATE ON stock_operation_orders WHEN EXISTS(SELECT 1 FROM stock_operation_command_seals) BEGIN SELECT RAISE(ABORT,'existing seal dependency'); END",
            ): db.exec_driver_sql(statement)
            db.commit()
            migration['previous']['previous']['upgrade']()
            db.exec_driver_sql("INSERT INTO stock_operation_command_seals VALUES('ordinary','wo','outbound_return',NULL)")
            db.commit()
            saved = tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"))
            for direction in ('upgrade', 'downgrade', 'upgrade'):
                migration[direction]()
                assert tuple(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name")) == saved
                assert db.exec_driver_sql('SELECT count(*) FROM stock_operation_command_seals').scalar() == 1
                for kind in ('outbound_return', 'ship_return', 'receive_return'):
                    with pytest.raises(sa.exc.IntegrityError, match='PostgreSQL loss receipt seal proof required'):
                        db.exec_driver_sql('INSERT INTO stock_operation_command_seals VALUES(?,NULL,?,?)',
                                          (kind, kind, 'loss-disposition'))
                with pytest.raises(sa.exc.IntegrityError, match='historical seal retained'):
                    db.exec_driver_sql('DELETE FROM stock_operation_command_seals')
                with pytest.raises(sa.exc.IntegrityError):
                    db.exec_driver_sql("INSERT INTO stock_operation_command_seals VALUES('mixed','wo','ship_return','loss-disposition')")
                db.commit()
            # Inject a retained historical row only in this throwaway SQLite
            # fixture, to prove downgrade refuses before changing any schema.
            db.exec_driver_sql('DROP TRIGGER trg_loss_receipt_seal_block_0155')
            db.exec_driver_sql("INSERT INTO stock_operation_command_seals VALUES('retained',NULL,'ship_return','loss-disposition')")
            db.commit()
            before = tuple(db.exec_driver_sql("SELECT type,name,sql FROM sqlite_master ORDER BY type,name"))
            with pytest.raises(RuntimeError, match='immutable loss sender seals require retention'):
                migration['downgrade']()
            assert tuple(db.exec_driver_sql("SELECT type,name,sql FROM sqlite_master ORDER BY type,name")) == before
    finally:
        engine.dispose()
