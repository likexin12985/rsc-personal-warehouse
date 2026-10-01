"""Isolated migration structure, retention and atomicity; PG16 proof is separate."""
from pathlib import Path
from io import StringIO
import importlib.util
import runpy
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pglast import parser

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(scope='module')
def migration():
    path=ROOT/'backend/alembic/versions/20261207_0158_loss_disposition_seals.py'
    spec=importlib.util.spec_from_file_location('candidate_execution_seals',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.FOLDER=ROOT/'backend/alembic/versions'
    return module

@pytest.fixture
def database():
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    with engine.connect() as db:
        db.exec_driver_sql('CREATE TABLE unrelated(id INTEGER PRIMARY KEY,note TEXT NOT NULL)')
        db.exec_driver_sql("INSERT INTO unrelated VALUES(1,'preserve original facts')")
        db.commit()
        with Operations.context(MigrationContext.configure(db)):
            yield db
    engine.dispose()


def catalog(db):
    return tuple(db.exec_driver_sql("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))


@pytest.mark.parametrize('direction',('upgrade','downgrade'))
def test_postgresql_complete_ddl_parses_and_private_fences_are_pinned(migration,direction):
    output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
        getattr(migration,direction)()
    sql=output.getvalue();parser.parse_sql(sql)
    assert not sa.text(sql)._bindparams
    assert 'DELETE FROM' not in sql and 'TRUNCATE TABLE' not in sql
    if direction=='upgrade':
        for name,(table,*_) in migration.TRIGGERS.items():
            assert f'ENABLE ALWAYS TRIGGER {name}' in sql
        assert 'GRANT SELECT,INSERT ON public.'+migration.TABLE in sql
        for (name,signature),(args,result,body) in migration.FUNCTIONS.items():
            parser.parse_plpgsql_json(f'CREATE FUNCTION {name}({args}) RETURNS {result} LANGUAGE plpgsql AS $body${body}$body$')
            assert f'REVOKE ALL ON FUNCTION public.{name}({signature}) FROM PUBLIC,star_oam_api' in sql
    else:
        assert sql.index('immutable execution seal history requires retention')<sql.index('DROP TRIGGER')<sql.index('DROP TABLE')
    assert sql.index('LOCK TABLE public.alembic_version')<sql.index('loss_execution_seal_ready_0158')


def test_empty_roundtrip_preserves_unrelated_facts_and_blocks_sqlite_writes(database,migration):
    db=database;before=catalog(db)
    for _ in range(2):
        migration.upgrade();db.commit()
        with pytest.raises(sa.exc.IntegrityError,match='PostgreSQL execution seal proof required'):
            db.exec_driver_sql('INSERT INTO '+migration.TABLE+' (id) VALUES (?)',(uuid4().hex,))
        db.rollback()
        migration.downgrade();db.commit()
        assert catalog(db)==before
        assert tuple(db.exec_driver_sql('SELECT * FROM unrelated'))==((1,'preserve original facts'),)


def test_retained_seal_stops_downgrade_before_any_schema_change(database,migration):
    db=database;migration.upgrade();db.commit()
    trigger=db.scalar(sa.text("SELECT sql FROM sqlite_master WHERE name='trg_loss_disposition_seal_insert_0158'"))
    db.exec_driver_sql('DROP TRIGGER trg_loss_disposition_seal_insert_0158')
    values={c['name']:uuid4().hex for c in sa.inspect(db).get_columns(migration.TABLE)}
    values.update(operation_type='loss_report',flow='disposition',authorization_version=1,
        disposition_key_hash='a'*64,return_key_hash='b'*64,request_hash='c'*64,plan_hash='d'*64,
        command_jsonb='{}',created_at='2026-09-30T00:00:00+00:00')
    db.execute(sa.text('INSERT INTO '+migration.TABLE+' ('+','.join(values)+') VALUES ('+','.join(':'+k for k in values)+')'),values)
    db.exec_driver_sql(trigger);db.commit()
    before=catalog(db);facts=tuple(db.exec_driver_sql('SELECT * FROM '+migration.TABLE))
    with pytest.raises(RuntimeError,match='immutable execution seal history requires retention'):migration.downgrade()
    assert catalog(db)==before and tuple(db.exec_driver_sql('SELECT * FROM '+migration.TABLE))==facts
    db.rollback()


def test_late_schema_failure_rolls_back_new_table_indexes_and_guards(database,migration,monkeypatch):
    db=database;before=catalog(db);execute=migration.op.execute
    def fail(statement,*args,**kwargs):
        if 'CREATE TRIGGER trg_loss_disposition_seal_delete_0158' in str(statement):
            raise RuntimeError('synthetic late migration failure')
        return execute(statement,*args,**kwargs)
    monkeypatch.setattr(migration.op,'execute',fail)
    with pytest.raises(RuntimeError,match='synthetic late migration failure'):migration.upgrade()
    db.rollback();assert catalog(db)==before
    assert tuple(db.exec_driver_sql('SELECT * FROM unrelated'))==((1,'preserve original facts'),)


def test_readiness_pins_match_published_body_without_retaining_predecessor_graph(migration):
    import hashlib
    prior=runpy.run_path(str(migration.FOLDER/'20260903_0052_opening_terminal_guard_execution.py'))
    body=prior['_oam_runtime_ready_function_sql'](prior['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
    for revision,expected in ((migration.down_revision,migration.OLD_READY_HASH),(migration.revision,migration.NEW_READY_HASH)):
        assert hashlib.sha256(body.replace(prior['revision'],revision).encode()).hexdigest()==expected
    assert not any(name in vars(migration) for name in ('previous','_previous','ready','_ready'))


def test_sqlite_column_keys_and_indexes_match_candidate_orm(database,migration):
    from app.stock_operation_models import StockLossDispositionRequestSeal as Seal
    migration.upgrade();inspector=sa.inspect(database);table=Seal.__table__
    actual={c['name']:(str(c['type']),c['nullable']) for c in inspector.get_columns(migration.TABLE)}
    expected={c.name:(str(c.type.compile(dialect=database.dialect)),c.nullable) for c in table.columns}
    assert actual==expected
    assert {(i['name'],tuple(i['column_names'])) for i in inspector.get_indexes(migration.TABLE)}=={
        (i.name,tuple(c.name for c in i.columns)) for i in table.indexes}
    assert {(i['name'],tuple(i['column_names'])) for i in inspector.get_unique_constraints(migration.TABLE)}=={
        (i.name,tuple(c.name for c in i.columns)) for i in table.constraints if isinstance(i,sa.UniqueConstraint)}
    assert {(tuple(i['constrained_columns']),i['referred_table'],tuple(i['referred_columns']),i['options']['ondelete'])
        for i in inspector.get_foreign_keys(migration.TABLE)}=={
        (tuple(e.parent.name for e in i.elements),i.elements[0].target_fullname.split('.')[0],
            tuple(e.target_fullname.split('.')[-1] for e in i.elements),i.ondelete)
        for i in table.constraints if isinstance(i,sa.ForeignKeyConstraint)}


def test_execution_fence_columns_match_real_request_tables(migration):
    # Receipt/shipment operation rows bind their physical documents; their
    # idempotency key lives on receipts/shipments, not on both layers.
    from app.database import Base
    from app import stock_operation_models, inventory_models
    for name, keyed in migration.OTHER_REQUEST_TABLES:
        columns=Base.metadata.tables[name].c
        assert {'actor_user_id','request_id'} <= set(columns.keys()),name
        assert keyed == ('idempotency_key_hash' in columns),name
    for name in ('inventory_transactions','shipments','receipts'):
        assert 'idempotency_key_hash' in Base.metadata.tables[name].c,name
