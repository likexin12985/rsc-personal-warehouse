"""0099 retains ordinary seal facts and independently guards parent commands."""
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0099 as OAM_SYNC_FUNCTION_MANIFEST

MIGRATION=Path(__file__).parents[1]/"alembic/versions/20261009_0099_work_order_reversal_seals.py"


def test_parent_seal_migration_is_linear_and_adds_exact_guard_sources():
    m=runpy.run_path(str(MIGRATION))
    old=runpy.run_path(str(MIGRATION.with_name("20261004_0094_work_order_command_seals.py")))
    assert m["down_revision"]=="20261008_0098" and m["OLD_HASH"]=="c3b1e7dbd15f5e98e0e73820fce6d57a14fa7c4be2bcc7029e861f9b825bd61c"
    assert OAM_SYNC_FUNCTION_MANIFEST["rsc_oam_runtime_binding_ready_0044()"][6]==m["NEW_HASH"]
    for coordinate,(_,_,body) in (old["FUNCTIONS"]|m["FUNCTIONS"]).items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate]==hashlib.sha256(body.encode()).hexdigest()
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
    for name,(table,_,function,kind,deferred) in (old["TRIGGERS"]|m["TRIGGERS"]).items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name]==(table,function,"A",kind,deferred,deferred,deferred)


@pytest.mark.parametrize("operation",["upgrade","downgrade"])
def test_parent_seal_sql_parses_and_preserves_the_old_immutable_audit_guards(monkeypatch,operation):
    m=runpy.run_path(str(MIGRATION));statements=[]
    monkeypatch.setattr(m["op"],"get_bind",lambda:SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(m["op"],"execute",statements.append)
    m[operation]()
    parser=pytest.importorskip("pglast.parser")
    for sql in statements:
        assert not sa.text(sql)._bindparams
        parser.parse_sql(sql)
        if sql.lstrip().startswith(("CREATE FUNCTION","DO ")):parser.parse_plpgsql_json(sql)
    assert statements[0]=="LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"
    assert "work_order_reversals" in statements[1] and "work_order_command_seals" in statements[1]
    assert not any("DROP TRIGGER trg_work_order_seals_immutable_0094" in sql for sql in statements)
    if operation=="upgrade":
        assert sum("CREATE CONSTRAINT TRIGGER" in sql for sql in statements)==2
        assert "0099 executed reversal cannot be sealed" in m["CHECK_BODY"]
        assert "0099 sealed reversal cannot execute" in m["CHECK_BODY"]
    else:
        assert any("reversal command seals must be retained" in sql for sql in statements)
        assert any("0099 function source, configuration or ownership drift" in sql for sql in statements)


def test_sqlite_change_retains_ordinary_rows_and_immutable_guards_then_refuses_losing_parent_seals():
    m=runpy.run_path(str(MIGRATION))
    old=runpy.run_path(str(MIGRATION.with_name("20261004_0094_work_order_command_seals.py")))
    engine=sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as db, Operations.context(MigrationContext.configure(db)):
            for name in ("oam_work_orders","people","users"):
                db.exec_driver_sql(f"CREATE TABLE {name} (id VARCHAR(36) PRIMARY KEY)")
            old["_create_table"]()
            prior = runpy.run_path(str(MIGRATION.with_name("20261005_0095_work_order_replacement_seals.py")))
            prior["upgrade"]()
            db.exec_driver_sql("""INSERT INTO work_order_command_seals VALUES
                ('00000000000040008000000000000001','00000000000040008000000000000002',
                'synthetic-user','00000000000040008000000000000003',1,'occupy',
                'synthetic-request','synthetic-reference','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '2026-09-13 00:00:00','2026-09-13 00:00:00')""")
            before=db.exec_driver_sql("SELECT * FROM work_order_command_seals").all()
            m["upgrade"]()
            assert db.exec_driver_sql("SELECT * FROM work_order_command_seals").all()==before
            m["downgrade"]()
            assert db.exec_driver_sql("SELECT * FROM work_order_command_seals").all()==before
            m["upgrade"]()
            for sql in ("UPDATE work_order_command_seals SET request_id=request_id","DELETE FROM work_order_command_seals"):
                with pytest.raises(sa.exc.IntegrityError,match="append-only"):
                    db.exec_driver_sql(sql)
            db.exec_driver_sql("""INSERT INTO work_order_command_seals
                SELECT '00000000000040008000000000000004',oam_work_order_id,actor_user_id,operator_person_id,
                    authorization_version,'reverse',request_id,request_reference,request_hash,sealed_at,created_at
                FROM work_order_command_seals""")
            parent=db.exec_driver_sql("SELECT * FROM work_order_command_seals ORDER BY id").all()
            with pytest.raises(RuntimeError,match="0099 downgrade blocked"):
                m["downgrade"]()
            assert db.exec_driver_sql("SELECT * FROM work_order_command_seals ORDER BY id").all()==parent
            db.rollback()
    finally:engine.dispose()
