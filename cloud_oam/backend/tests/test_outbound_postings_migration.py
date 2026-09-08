from pathlib import Path
import hashlib
import runpy

import pytest
import sqlalchemy as sa

from app.database_security import MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic/versions/20260912_0072_outbound_postings.py"


def test_outbound_guard_sources_match_runtime_and_parse(monkeypatch):
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


def test_outbound_source_replacements_are_unambiguous_in_both_directions():
    migration = runpy.run_path(str(MIGRATION))
    # The real source CAS rejects an already-present replacement. A prefix
    # replacement can upgrade but cannot downgrade: the old fragment remains
    # inside the new one. Keep complete statements in both directions.
    for coordinate, pairs in migration["source_changes"]().items():
        for before, after in pairs:
            assert before not in after and after not in before, coordinate
            upgraded = before.replace(before, after)
            assert before not in upgraded
            assert upgraded.replace(after, before) == before


def test_outbound_table_metadata_matches_forward_migration(monkeypatch):
    from app.inventory_models import OutboundPosting, OutboundPostingSerial
    migration = runpy.run_path(str(MIGRATION))
    tables = {}
    monkeypatch.setattr(migration["op"], "create_table", lambda name, *args: tables.setdefault(name, args))
    monkeypatch.setattr(migration["op"], "create_index", lambda *args: None)
    migration["_create_tables"]()
    for model in (OutboundPosting, OutboundPostingSerial):
        columns = {column.name: column for column in tables[model.__tablename__] if isinstance(column, sa.Column)}
        assert set(columns) == set(model.__table__.columns.keys())
        for name, column in columns.items():
            assert str(column.type) == str(model.__table__.columns[name].type)
            assert column.nullable == model.__table__.columns[name].nullable
    assert migration["down_revision"] == "20260911_0071"
    assert "original.picked_qty" in migration["BINDING_BODY"]
    assert "NEW.source_stock_account_id <> original.target_stock_account_id" in migration["BINDING_BODY"]
