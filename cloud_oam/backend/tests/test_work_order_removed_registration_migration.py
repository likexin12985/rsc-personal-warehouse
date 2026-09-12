"""0096 admits only audited identity facts and retains admitted history."""
import hashlib
from io import StringIO
from pathlib import Path
import runpy

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0096 as OAM_SYNC_FUNCTION_MANIFEST

MIGRATION=Path(__file__).parents[1]/"alembic/versions/20261006_0096_removed_serial_registrations.py"


def test_registration_migration_pins_sources_and_keeps_master_insert_closed():
    m=runpy.run_path(str(MIGRATION))
    old=runpy.run_path(str(MIGRATION.with_name("20261005_0095_work_order_replacement_seals.py")))
    assert m["down_revision"]==old["revision"] and m["OLD_HASH"]==old["NEW_HASH"]
    assert OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][6]==m["NEW_HASH"]
    for coordinate,(_,_,body) in m["FUNCTIONS"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]==hashlib.sha256(body.encode()).hexdigest()
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for name,(table,_,function,kind,deferred) in m["TRIGGERS"].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,"A",kind,deferred,deferred,deferred)
    assert m["TABLE"] in security.RUNTIME_INSERT_TABLES
    assert not {"inventory_serials","qr_codes"}&set(security.RUNTIME_INSERT_TABLES)


@pytest.mark.parametrize("operation",["upgrade","downgrade"])
def test_registration_postgresql_ddl_and_function_bodies_parse(operation):
    m=runpy.run_path(str(MIGRATION));output=StringIO()
    with Operations.context(MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":output})):
        m[operation]()
    sql=output.getvalue();parser=pytest.importorskip("pglast.parser")
    parser.parse_sql(sql)
    # Parsing the outer CREATE does not parse the actual trigger body.
    for (name,_),(_,_,body) in m["FUNCTIONS"].items():
        parser.parse_plpgsql_json(f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $body${body}$body$")
    assert "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE" in sql
    if operation=="upgrade":
        assert "GRANT SELECT, INSERT ON public.work_order_removed_serial_registrations" in sql
        assert "GRANT INSERT ON public.inventory_serials" not in sql
        assert sql.count("CREATE CONSTRAINT TRIGGER")==3
    else:
        assert "removed registrations and seals must be retained" in sql
        assert sql.index("0096 downgrade blocked")<sql.index("DROP TRIGGER")


@pytest.mark.parametrize("history",["registration","seal"])
def test_sqlite_retains_parent_seals_and_refuses_losing_new_identity_history(history):
    m=runpy.run_path(str(MIGRATION));folder=MIGRATION.parent
    old=runpy.run_path(str(folder/"20261004_0094_work_order_command_seals.py"))
    parent=runpy.run_path(str(folder/"20261005_0095_work_order_replacement_seals.py"))
    engine=sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as db,Operations.context(MigrationContext.configure(db)):
            for name in ("oam_work_orders","people","users","inventory_serials","stock_accounts","materials","inventory_lots"):
                db.exec_driver_sql(f"CREATE TABLE {name} (id VARCHAR(36) PRIMARY KEY)")
            old["_create_table"]();parent["upgrade"]()
            db.exec_driver_sql("""INSERT INTO work_order_command_seals VALUES
                ('00000000000040008000000000000001','00000000000040008000000000000002',
                'synthetic-user','00000000000040008000000000000003',1,'replace',
                'synthetic-request','synthetic-reference','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '2026-09-13 00:00:00','2026-09-13 00:00:00')""")
            before=db.exec_driver_sql("SELECT * FROM work_order_command_seals").all()
            m["upgrade"]();assert db.exec_driver_sql("SELECT * FROM work_order_command_seals").all()==before
            m["downgrade"]();assert db.exec_driver_sql("SELECT * FROM work_order_command_seals").all()==before
            m["upgrade"]()
            if history=="seal":
                db.exec_driver_sql("""INSERT INTO work_order_command_seals SELECT
                    '00000000000040008000000000000004',oam_work_order_id,actor_user_id,operator_person_id,
                    authorization_version,'register_removed',request_id,request_reference,request_hash,sealed_at,created_at
                    FROM work_order_command_seals""")
            else:
                db.exec_driver_sql("""INSERT INTO work_order_removed_serial_registrations VALUES
                    ('1','WORS-TEST','2','3','synthetic-user','4',1,'v1','5','6',NULL,
                     'synthetic-request','hash','key','{}','2026-09-13','2026-09-13')""")
                for sql in ("UPDATE work_order_removed_serial_registrations SET request_id=request_id","DELETE FROM work_order_removed_serial_registrations"):
                    with pytest.raises(sa.exc.IntegrityError,match="immutable"):db.exec_driver_sql(sql)
            for sql in ("UPDATE work_order_command_seals SET request_id=request_id","DELETE FROM work_order_command_seals"):
                with pytest.raises(sa.exc.IntegrityError,match="append-only"):db.exec_driver_sql(sql)
            with pytest.raises(RuntimeError,match="0096 downgrade blocked"):m["downgrade"]()
            assert sa.inspect(db).has_table(m["TABLE"])
    finally:engine.dispose()
