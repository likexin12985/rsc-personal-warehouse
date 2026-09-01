from __future__ import annotations

import pytest

from app.database import engine


@pytest.mark.skipif(engine.dialect.name != "sqlite", reason="SQLite runtime guard")
def test_sqlite_runtime_enforces_foreign_keys() -> None:
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
