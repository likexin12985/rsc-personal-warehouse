from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa

from app.foundation_models import AuditEvent


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0039_material_request_command_status_lookup.py"
)
INDEX_NAME = "uq_audit_events_material_request_request_id_0039"


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_0039", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "audit_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("stream_key", sa.String(160), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(160), nullable=False),
    )


def test_0039_creates_exact_partial_unique_lookup_and_downgrades() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    audit_events = _table(metadata)
    metadata.create_all(engine)
    module = _migration_module()

    with engine.begin() as connection:
        connection.execute(
            audit_events.insert(),
            (
                {
                    "stream_key": "material_request",
                    "action": "material_request.created",
                    "request_id": "trace-one",
                },
                {
                    "stream_key": "material_request",
                    "action": "material_request.submitted",
                    "request_id": "trace-one",
                },
                {
                    "stream_key": "material_request",
                    "action": "material_request.withdraw",
                    "request_id": "trace-lifecycle",
                },
                {
                    "stream_key": "authentication",
                    "action": "authentication.test",
                    "request_id": "trace-one",
                },
                {
                    "stream_key": "authentication",
                    "action": "authentication.test",
                    "request_id": "trace-one",
                },
            ),
        )
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        index_sql = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            (INDEX_NAME,),
        ).scalar_one()
        assert "UNIQUE INDEX" in index_sql
        assert "request_id" in index_sql
        assert "stream_key = 'material_request'" in index_sql
        assert "material_request.withdraw" in index_sql
        assert "material_request.cancel" in index_sql

        # Other actions and streams keep their historical replay semantics.
        connection.execute(
            audit_events.insert().values(
                stream_key="authorization",
                action="authorization.test",
                request_id="trace-one",
            )
        )
        connection.execute(
            audit_events.insert().values(
                stream_key="material_request",
                action="file.upload_intent.replayed",
                request_id="trace-lifecycle",
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                audit_events.insert().values(
                    stream_key="material_request",
                    action="material_request.cancel",
                    request_id="trace-lifecycle",
                )
            )

    # The expected duplicate insert aborts only its implicit statement in
    # SQLite; use a fresh transaction for the reversible schema assertion.
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.downgrade()
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
            (INDEX_NAME,),
        ).scalar_one() == 0
    engine.dispose()


def test_0039_preflight_blocks_duplicate_nonempty_material_request_sentinels() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    audit_events = _table(metadata)
    metadata.create_all(engine)
    module = _migration_module()

    with engine.begin() as connection:
        connection.execute(
            audit_events.insert(),
            (
                {
                    "stream_key": "material_request",
                    "action": "material_request.withdraw",
                    "request_id": "trace-duplicate",
                },
                {
                    "stream_key": "material_request",
                    "action": "material_request.cancel",
                    "request_id": "trace-duplicate",
                },
            ),
        )
        module.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="0039 preflight failed"):
            module.upgrade()
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
            (INDEX_NAME,),
        ).scalar_one() == 0
    engine.dispose()


def test_0039_preflight_reports_duplicate_empty_legacy_coordinates() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    metadata = sa.MetaData()
    audit_events = _table(metadata)
    metadata.create_all(engine)
    module = _migration_module()

    with engine.begin() as connection:
        connection.execute(
            audit_events.insert(),
            (
                {
                    "stream_key": "material_request",
                    "action": "material_request.withdraw",
                    "request_id": "",
                },
                {
                    "stream_key": "material_request",
                    "action": "material_request.cancel",
                    "request_id": "",
                },
            ),
        )
        module.op = Operations(MigrationContext.configure(connection))
        with pytest.raises(RuntimeError, match="duplicate empty request_id"):
            module.upgrade()
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?",
            (INDEX_NAME,),
        ).scalar_one() == 0
    engine.dispose()


def test_0039_model_and_revision_coordinates_match_migration() -> None:
    module = _migration_module()
    assert module.revision == "20260901_0039"
    assert module.down_revision == "20260901_0038"
    index = next(
        row for row in AuditEvent.__table__.indexes if row.name == INDEX_NAME
    )
    assert index.unique is True
    assert tuple(column.name for column in index.columns) == ("request_id",)
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "stream_key = 'material_request' AND action IN "
        "('material_request.withdraw', 'material_request.cancel')"
    )
