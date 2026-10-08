"""New 0180 function-catalog drift negatives; no old suite replay."""
from copy import deepcopy
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parents[1] / "alembic/versions/20261229_0180_openbao_data_key_pins.py"


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("binding_readiness_guard_0180", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _catalog_db(monkeypatch, migration, rows):
    class Result:
        def mappings(self):
            return self

        def all(self):
            return rows

    class Database:
        def execute(self, statement, parameters):
            assert str(statement).strip().startswith("SELECT")
            assert parameters == {"name": "rsc_oam_runtime_binding_ready_0044"}
            return Result()

    monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: Database()))


@pytest.mark.parametrize("up", [True, False])
def test_exact_readiness_catalog_is_accepted_independent_of_acl_order(migration, monkeypatch, up):
    row = deepcopy(migration.DATA["readiness"]["before" if up else "after"])
    row["acl"].reverse()
    _catalog_db(monkeypatch, migration, [row])
    migration._check_readiness_source(up)


@pytest.mark.parametrize("field,value", [
    ("owner", "star_oam_api"), ("prosecdef", False),
    ("proconfig", ["search_path=public, pg_catalog"]),
    ("provolatile", "v"), ("proparallel", "s"),
    ("proisstrict", True), ("proleakproof", True),
    ("identity_arguments", "actor text"),
    ("signature", "rsc_oam_runtime_binding_ready_0044(text)"),
    ("prosrc", "SELECT true"), ("definition", "CREATE FUNCTION unsafe() RETURNS boolean"),
])
def test_readiness_metadata_or_body_drift_is_rejected(migration, monkeypatch, field, value):
    row = deepcopy(migration.DATA["readiness"]["before"])
    row[field] = value
    _catalog_db(monkeypatch, migration, [row])
    with pytest.raises(ValueError, match="exact readiness function catalog"):
        migration._check_readiness_source(True)


@pytest.mark.parametrize("acl_change", ["public", "grantable", "missing"])
def test_readiness_acl_drift_is_rejected(migration, monkeypatch, acl_change):
    row = deepcopy(migration.DATA["readiness"]["before"])
    if acl_change == "public":
        row["acl"].append(dict(grantee="PUBLIC", privilege="EXECUTE", grantable=False))
    elif acl_change == "grantable":
        row["acl"][0]["grantable"] = True
    else:
        row["acl"].pop()
    _catalog_db(monkeypatch, migration, [row])
    with pytest.raises(ValueError, match="exact readiness function catalog"):
        migration._check_readiness_source(True)


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_overloaded_readiness_function_is_rejected(migration, monkeypatch, count):
    rows = [deepcopy(migration.DATA["readiness"]["before"]) for _ in range(count)]
    if count == 2:
        rows[1]["signature"] = "rsc_oam_runtime_binding_ready_0044(text)"
    _catalog_db(monkeypatch, migration, rows)
    with pytest.raises(ValueError, match="exact readiness function identity"):
        migration._check_readiness_source(True)
