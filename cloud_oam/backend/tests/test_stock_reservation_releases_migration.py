from pathlib import Path
import hashlib
import runpy

import pytest
import sqlalchemy as sa

from app.database_security import MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260910_0070_stock_reservation_releases.py"


def test_release_guard_sources_match_runtime_and_parse(monkeypatch):
    migration = runpy.run_path(str(MIGRATION))
    statements = []
    monkeypatch.setattr(migration["op"], "execute", statements.append)
    migration["_create_postgresql_guards"]()
    migration["_replace_functions"](upgrade=True)
    migration["_replace_functions"](upgrade=False)
    for name, (arguments, _, body) in migration["FUNCTIONS"].items():
        signature = ", ".join(part.split()[-1] for part in arguments.split(", ")) if arguments else ""
        assert hashlib.sha256(body.encode()).hexdigest() == MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[(name, signature)]
    parser = pytest.importorskip("pglast.parser")
    for statement in statements:
        assert not sa.text(statement)._bindparams
        parser.parse_sql(statement)
        if statement.lstrip().startswith(("CREATE FUNCTION", "DO ")):
            parser.parse_plpgsql_json(statement)


def test_release_table_metadata_matches_forward_migration(monkeypatch):
    from app.inventory_models import StockReservationRelease, StockReservationReleaseSerial
    migration = runpy.run_path(str(MIGRATION))
    tables = {}
    monkeypatch.setattr(migration["op"], "create_table", lambda name, *args: tables.setdefault(name, args))
    monkeypatch.setattr(migration["op"], "create_index", lambda *args: None)
    migration["_create_tables"]()
    for model in (StockReservationRelease, StockReservationReleaseSerial):
        columns = {column.name: column for column in tables[model.__tablename__] if isinstance(column, sa.Column)}
        assert set(columns) == set(model.__table__.columns.keys())
        for name, column in columns.items():
            assert str(column.type) == str(model.__table__.columns[name].type)
            assert column.nullable == model.__table__.columns[name].nullable
    assert migration["down_revision"] == "20260909_0069"
    assert "original.reserved_qty" in migration["RELEASE_BINDING_BODY"]
    assert "NEW.source_stock_account_id <> original.stock_account_id" in migration["RELEASE_BINDING_BODY"]
