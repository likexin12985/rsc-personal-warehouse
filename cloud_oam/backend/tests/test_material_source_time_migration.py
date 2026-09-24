"""Populated 0118 time widening and lossless downgrade boundaries."""
from io import StringIO
from pathlib import Path
import hashlib
import runpy

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app import oam_sync_scope_security as scope

PATH = Path(__file__).parents[1] / 'alembic/versions/20261028_0118_material_unknown_source_time.py'


@pytest.fixture
def engine(tmp_path):
    engine = sa.create_engine('sqlite:///' + str(tmp_path / 'material-time.sqlite'))
    with engine.begin() as db:
        # Old non-null parent plus an actual referring row and independent trigger.
        db.exec_driver_sql('CREATE TABLE external_objects (id CHAR(32) PRIMARY KEY)')
        db.exec_driver_sql('CREATE TABLE materials (id CHAR(32) PRIMARY KEY, external_object_id CHAR(32) NOT NULL REFERENCES external_objects(id), sku_code VARCHAR(80) NOT NULL UNIQUE, name VARCHAR(200) NOT NULL, specification VARCHAR(300) NOT NULL, base_unit VARCHAR(32) NOT NULL, status VARCHAR(20) NOT NULL CHECK (status IN (\'active\',\'inactive\')), source_updated_at DATETIME NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)')
        db.exec_driver_sql('CREATE INDEX ix_time_material_name ON materials(name)')
        db.exec_driver_sql('CREATE TABLE time_reference (id INTEGER PRIMARY KEY, material_id CHAR(32) REFERENCES materials(id) ON DELETE CASCADE)')
        db.exec_driver_sql('CREATE TABLE time_update_log (material_id CHAR(32))')
        db.exec_driver_sql('CREATE TRIGGER preserve_material_trigger AFTER UPDATE ON materials BEGIN INSERT INTO time_update_log VALUES (NEW.id); END')
        db.exec_driver_sql('CREATE TRIGGER referencing_material_trigger BEFORE UPDATE ON time_reference WHEN NOT EXISTS (SELECT 1 FROM materials WHERE id=NEW.material_id) BEGIN SELECT RAISE(ABORT,\'missing material\'); END')
        db.exec_driver_sql("INSERT INTO external_objects VALUES ('source')")
        db.exec_driver_sql("INSERT INTO materials VALUES ('known','source','SKU-KNOWN','Synthetic material','','EA','active','2026-09-01 01:02:03.123456','2026-09-02','2026-09-03')")
        db.exec_driver_sql("INSERT INTO time_reference VALUES (1,'known')")
    yield engine
    engine.dispose()


def migrate(connection, action):
    with Operations.context(MigrationContext.configure(connection)):
        runpy.run_path(str(PATH))[action]()


def snapshot(db):
    return {name: db.exec_driver_sql('SELECT * FROM '+name).all() for name in ('external_objects','materials','time_reference','time_update_log')}


def test_populated_upgrade_and_known_time_downgrade_preserve_data_references_and_triggers(engine):
    with engine.begin() as db:
        before=snapshot(db)
        trigger=db.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='preserve_material_trigger'").scalar()
        triggers_before=db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").all()
        setting_before=db.exec_driver_sql('PRAGMA legacy_alter_table').scalar()
        migrate(db,'upgrade')
        assert snapshot(db)==before
        assert sa.inspect(db).get_columns('materials')[7]['nullable']
        assert db.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='preserve_material_trigger'").scalar()==trigger
        assert db.exec_driver_sql('PRAGMA foreign_key_check').all()==[]
        assert db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").all()==triggers_before
        assert db.exec_driver_sql('PRAGMA legacy_alter_table').scalar()==setting_before
        migrate(db,'downgrade')
        assert snapshot(db)==before and not sa.inspect(db).get_columns('materials')[7]['nullable']
        assert db.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='preserve_material_trigger'").scalar()==trigger


def test_unknown_time_blocks_downgrade_without_filling_or_losing_data(engine):
    with engine.begin() as db:
        migrate(db,'upgrade')
        db.exec_driver_sql("UPDATE materials SET source_updated_at=NULL WHERE id='known'")
        before=snapshot(db)
        with pytest.raises(RuntimeError,match='unknown material source times must be retained'):
            migrate(db,'downgrade')
        assert snapshot(db)==before and before['materials'][0][7] is None
        assert before['time_update_log']==[('known',)]
        assert db.exec_driver_sql('PRAGMA foreign_key_check').all()==[]


def test_sqlite_foreign_key_enforcement_refuses_parent_rebuild_without_cascading(engine):
    with engine.connect() as db:
        db.exec_driver_sql('PRAGMA foreign_keys=ON')
        before=snapshot(db)
        with pytest.raises(RuntimeError,match='foreign_keys OFF before'):
            migrate(db,'upgrade')
        assert snapshot(db)==before
        assert db.exec_driver_sql('PRAGMA foreign_keys').scalar()==1


def test_upgrade_column_drift_refuses_before_ddl(engine):
    with engine.begin() as db:
        migrate(db,'upgrade');before=snapshot(db)
        with pytest.raises(RuntimeError,match='column drift'):migrate(db,'upgrade')
        assert snapshot(db)==before


def test_failed_trigger_restore_rolls_back_the_entire_sqlite_parent_rebuild(engine, monkeypatch):
    with engine.connect() as db:
        before=snapshot(db)
        original_schema=db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type IN ('table','trigger','index') ORDER BY name").all()
    original=Operations.execute
    def fail(self, statement, *args, **kwargs):
        if str(statement).startswith('CREATE TRIGGER preserve_material_trigger'):
            raise RuntimeError('synthetic restore failure')
        return original(self,statement,*args,**kwargs)
    monkeypatch.setattr(Operations,'execute',fail)
    with pytest.raises(RuntimeError,match='synthetic restore failure'):
        with engine.begin() as db: migrate(db,'upgrade')
    with engine.connect() as db:
        assert snapshot(db)==before
        assert db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type IN ('table','trigger','index') ORDER BY name").all()==original_schema
        assert db.exec_driver_sql('PRAGMA legacy_alter_table').scalar()==0


def test_real_pg_helper_runs_after_prior_retention_boundaries():
    from pg16_material_source_time_gate import assert_material_source_time_gate
    assert callable(assert_material_source_time_gate)
    source=(PATH.parents[2]/'tests/test_postgresql16_release_gate.py').read_text()
    tail=source[source.rindex('from pg16_inventory_control_preparation_gate import'):]
    assert tail.index('0117 downgrade blocked')<tail.index('assert_material_source_time_gate(control_owner_engine')<tail.index('0118 downgrade blocked')


def test_postgresql_ddl_is_locked_lossless_and_pins_current_readiness():
    parser=pytest.importorskip('pglast.parser')
    module=runpy.run_path(str(PATH))
    assert module['OLD_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0117['rsc_oam_runtime_binding_ready_0044()'][6]
    assert module['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0118['rsc_oam_runtime_binding_ready_0044()'][6]
    older=runpy.run_path(str(next(PATH.parent.glob('*0052*.py'))))
    body=older['_oam_runtime_ready_function_sql'](older['revision']).split('AS $$',1)[1].rsplit('$$',1)[0]
    assert hashlib.sha256(body.replace(older['revision'],module['revision']).encode()).hexdigest()==module['NEW_HASH']
    for action in ('upgrade','downgrade'):
        output=StringIO()
        with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':output})):
            module[action]()
        sql=output.getvalue();parser.parse_sql(sql)
        assert sql.index('LOCK TABLE public.alembic_version, public.materials IN ACCESS EXCLUSIVE MODE')<sql.index('ALTER TABLE materials')
        assert 'UPDATE materials' not in sql and 'DELETE FROM' not in sql and 'GRANT' not in sql
        if action=='upgrade':assert 'ALTER COLUMN source_updated_at DROP NOT NULL' in sql
        else:assert sql.index('unknown material source times must be retained')<sql.index('ALTER COLUMN source_updated_at SET NOT NULL')
