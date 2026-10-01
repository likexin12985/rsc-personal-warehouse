"""Real Alembic invocations cannot replace live correction migration evidence."""

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[2]
REVISIONS = (
    ("20261207_0158", "20261208_0159"),
    ("20261208_0159", "20261209_0160"),
    ("20261209_0160", "20261210_0161"),
)


def config(url, output=None):
    result = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    result.set_main_option("script_location", str(ROOT / "backend/alembic"))
    result.set_main_option("sqlalchemy.url", url)
    return result


@pytest.mark.parametrize("previous,current", REVISIONS)
@pytest.mark.parametrize("direction", ("upgrade", "downgrade"))
@pytest.mark.parametrize("url", (
    "postgresql+psycopg://offline:offline@localhost/offline",
    "sqlite+pysqlite:///:memory:",
))
def test_offline_transition_refuses_before_emitting_unverified_sql(
    monkeypatch, previous, current, direction, url,
):
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    start, end = (previous, current) if direction == "upgrade" else (current, previous)
    revision_number = current.rsplit("_", 1)[1]
    with pytest.raises(RuntimeError, match=(
        f"^{revision_number} {direction} requires an online evidence check$"
    )):
        getattr(command, direction)(config(url, output), f"{start}:{end}", sql=True)
    # Alembic may emit BEGIN and its revision comment, but no catalog queries,
    # business DDL, version advancement or successful COMMIT is allowed.
    statements = [line.strip() for line in output.getvalue().splitlines()
                  if line.strip() and not line.lstrip().startswith("--")]
    assert statements in ([], ["BEGIN;"])


def test_online_sqlite_roundtrip_keeps_live_preflight_and_retention_guards(
    monkeypatch, tmp_path,
):
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = "sqlite+pysqlite:///" + str(tmp_path / "correction-offline-boundary.db")
    settings = config(url)
    command.upgrade(settings, REVISIONS[0][0])
    command.upgrade(settings, REVISIONS[-1][1])
    engine = sa.create_engine(url)
    try:
        with engine.connect() as db:
            assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == REVISIONS[-1][1]
            assert db.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            with pytest.raises(sa.exc.IntegrityError, match="PostgreSQL correction proof required"):
                db.exec_driver_sql("INSERT INTO stock_loss_correction_executions DEFAULT VALUES")
            db.rollback()
        command.downgrade(settings, REVISIONS[0][0])
        with engine.connect() as db:
            assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == REVISIONS[0][0]
            assert not sa.inspect(db).has_table("stock_loss_correction_executions")
        command.upgrade(settings, REVISIONS[-1][1])
        with engine.connect() as db:
            assert db.scalar(sa.text("SELECT version_num FROM alembic_version")) == REVISIONS[-1][1]
            assert sa.inspect(db).has_table("stock_loss_correction_executions")
    finally:
        engine.dispose()
