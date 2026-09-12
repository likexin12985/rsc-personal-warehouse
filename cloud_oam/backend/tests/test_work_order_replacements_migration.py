"""Exact security catalog and source transitions for atomic replacements."""
import hashlib
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import database_security as security
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0093

MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261003_0093_work_order_replacements.py"


def test_replacement_security_pins_match_sources_and_preserve_minimum_privileges():
    m = runpy.run_path(str(MIGRATION))
    for coordinate,(_,result,body) in m["FUNCTIONS"].items():
        assert security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[coordinate] == hashlib.sha256(body.encode()).hexdigest()
        assert coordinate in security.MATERIAL_REQUEST_APPROVAL_SECURITY_DEFINER_FUNCTIONS
        if result == "void":
            assert coordinate in security.MATERIAL_REQUEST_APPROVAL_VOID_FUNCTIONS
    for signature,(_,body) in m["_sources"]().items():
        name,args = signature.removeprefix("public.").split("(")
        coordinate = (name,args.removesuffix(")"))
        catalog = security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256 if "opening_observation" in name else security.MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256
        assert catalog[coordinate] == hashlib.sha256(body.encode()).hexdigest()
    for name,(table,_,function,kind,deferred) in m["TRIGGERS"].items():
        assert security.EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table,function,"A",kind,deferred,deferred,deferred)
    assert "work_order_replacements" in security.RUNTIME_READ_TABLES & security.RUNTIME_INSERT_TABLES
    assert "work_order_replacements" not in security.RUNTIME_UPDATE_TABLES | security.RUNTIME_DELETE_TABLES
    assert OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0093["rsc_oam_runtime_binding_ready_0044()"][6] == m["NEW_HASH"]


@pytest.mark.parametrize("operation", ["upgrade","downgrade"])
def test_replacement_migration_uses_deferred_foreign_keys_and_sql_without_accidental_bindings(monkeypatch,operation):
    m = runpy.run_path(str(MIGRATION))
    statements,ddl = [],[]
    monkeypatch.setattr(m["op"],"get_bind",lambda:SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(m["op"],"execute",statements.append)
    for name in ("create_table","drop_table","add_column","drop_column","create_foreign_key","drop_constraint"):
        monkeypatch.setattr(m["op"],name,lambda *args,_name=name,**kwargs:ddl.append((_name,args,kwargs)))
    m[operation]()
    assert statements[0] == "LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE"
    assert "stock_accounts" in statements[1] and "inventory_serials" in statements[1]
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith(("CREATE FUNCTION","DO ")):
            parser.parse_plpgsql_json(statement)
    if operation == "upgrade":
        create = next(args for name,args,kwargs in ddl if name=="create_table")
        links = [arg for arg in create if isinstance(arg,sa.Column) and arg.name in {"consume_operation_id","recover_operation_id"}]
        assert len(links)==2
        assert all(next(iter(column.foreign_keys)).deferrable and next(iter(column.foreign_keys)).initially=="DEFERRED" for column in links)
        reverse = next(kwargs for name,args,kwargs in ddl if name=="create_foreign_key" and args[0]=="fk_work_order_operation_replacement_0093")
        assert reverse["deferrable"] and reverse["initially"]=="DEFERRED"
        assert "GRANT SELECT, INSERT ON TABLE public.work_order_replacements TO star_oam_api" in statements
    else:
        assert any("replacement facts require reviewed migration" in sql for sql in statements)
        assert sum("0093 function source, configuration or ownership drift" in sql for sql in statements)==2
    assert "work_order_replacement_readiness_0093" in statements[-1]
