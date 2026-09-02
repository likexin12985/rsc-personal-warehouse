from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import edge_main
from app.edge_database_security import (
    EdgeDatabaseBoundaryError,
    _BOUNDARY_SQL,
    verify_edge_database_boundary,
)


class _Mappings:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _Mappings(self.row)


class _Connection:
    def __init__(self, row, calls):
        self.row = row
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement, parameters):
        self.calls.append((statement, parameters))
        return _Result(self.row)


class _Engine:
    def __init__(self, row, *, dialect="postgresql"):
        self.row = row
        self.dialect = SimpleNamespace(name=dialect)
        self.calls = []

    def connect(self):
        return _Connection(self.row, self.calls)


def test_edge_database_boundary_accepts_only_complete_positive_evidence():
    engine = _Engine({"boundary_ok": True, "boundary_failures": ""})

    verify_edge_database_boundary(
        engine,
        expected_role="edge_inbox",
        expected_migration_role="star_oam_migrator",
    )

    assert len(engine.calls) == 1
    assert engine.calls[0][1] == {
        "edge_role": "edge_inbox",
        "migration_role": "star_oam_migrator",
    }


@pytest.mark.parametrize(
    "row",
    [
        None,
        {"boundary_ok": False, "boundary_failures": "table_acl"},
        {"boundary_ok": True, "boundary_failures": "grant_options"},
        {"boundary_ok": 1, "boundary_failures": ""},
    ],
)
def test_edge_database_boundary_fails_closed_on_missing_or_invalid_evidence(row):
    with pytest.raises(EdgeDatabaseBoundaryError):
        verify_edge_database_boundary(_Engine(row))


def test_edge_database_boundary_is_postgresql_only():
    engine = _Engine(None, dialect="sqlite")

    verify_edge_database_boundary(engine)

    assert engine.calls == []


def test_edge_database_boundary_sql_covers_full_effective_acl_closure():
    sql = str(_BOUNDARY_SQL)
    for required in (
        "current_user = :edge_role",
        "session_user = :edge_role",
        "external_sync_snapshots",
        "external_sync_snapshot_batches",
        "external_sync_snapshot_records",
        "external_sync_current_records",
        "audit_logs",
        "expected_update_columns",
        "has_table_privilege",
        "has_column_privilege",
        "has_any_column_privilege",
        "has_function_privilege",
        "has_sequence_privilege",
        "has_database_privilege",
        "has_schema_privilege",
        "pg_auth_members",
        "has_parameter_privilege",
        "aclexplode(column_row.attacl)",
        "cross_schema",
        "public_acl",
        "cluster_objects",
        "cross_database",
        "pg_largeobject_metadata",
        "pg_parameter_acl",
        "grant_options",
        "TEMPORARY",
    ):
        assert required in sql


def test_edge_health_rechecks_acl_and_returns_sanitized_503(monkeypatch):
    monkeypatch.setattr(
        edge_main,
        "verify_edge_database_boundary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            EdgeDatabaseBoundaryError("sensitive database evidence")
        ),
    )

    with pytest.raises(HTTPException) as captured:
        edge_main.health()

    assert captured.value.status_code == 503
    assert captured.value.detail == "边缘接收器数据库安全边界未就绪"
    assert "sensitive" not in captured.value.detail


def test_edge_health_uses_expected_role_and_health_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(
        edge_main,
        "verify_edge_database_boundary",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = edge_main.health()

    assert response["ok"] is True
    assert response["mode"] == "staging_only"
    assert calls == [
        (
            (edge_main.health_engine,),
            {
                "expected_role": edge_main.settings.database_expected_edge_role,
                "expected_migration_role": (
                    edge_main.settings.database_expected_migration_role
                ),
            },
        )
    ]
