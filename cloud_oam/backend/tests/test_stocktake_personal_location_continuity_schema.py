from __future__ import annotations

import io

from alembic import command
import pytest
import sqlalchemy as sa

import test_opening_count_observation_schema as support
import test_stocktake_technician_personal_location_schema as support_0019


REVISION_0018 = "20260831_0018"
REVISION_0019 = "20260831_0019"
REVISION_0020 = "20260831_0020"
PG_FUNCTION = "rsc_preserve_stocktake_personal_location_0020"
LOCATION_TRIGGER = "trg_stock_locations_stocktake_personal_continuity_0020"


def test_0020_sqlite_preserves_personal_completion_location(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0020-completion.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0019)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        facts = support._insert_opening_fixture(connection)
        support_0019._insert_fixture_locations(
            connection,
            facts,
            observed_type="personal",
        )

    command.upgrade(config, REVISION_0020)
    with engine.begin() as connection:
        support._insert_completion(
            connection,
            facts,
            completion_id=support._uuid(2011),
            scope_id=facts["scope_observed"],
            count_lines=0,
            observations=0,
            total=0,
            zero=True,
            key_hash="1" * 64,
        )

        rejected = connection.begin_nested()
        with pytest.raises(sa.exc.DatabaseError, match="reclassification"):
            connection.exec_driver_sql(
                "UPDATE stock_locations SET location_type = 'region' "
                "WHERE id = ?",
                (facts["location_observed"],),
            )
        rejected.rollback()

        connection.exec_driver_sql(
            "UPDATE stock_locations SET name = 'renamed personal location' "
            "WHERE id = ?",
            (facts["location_observed"],),
        )
        connection.exec_driver_sql(
            "UPDATE stock_locations SET location_type = 'region' WHERE id = ?",
            (facts["location_zero"],),
        )
        assert connection.exec_driver_sql(
            "SELECT location_type, name FROM stock_locations WHERE id = ?",
            (facts["location_observed"],),
        ).one() == ("personal", "renamed personal location")
        assert connection.exec_driver_sql(
            "SELECT location_type FROM stock_locations WHERE id = ?",
            (facts["location_zero"],),
        ).scalar_one() == "region"


def test_0020_sqlite_preserves_personal_recount_assignment_location(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0020-assignment.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0020)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        facts = support._insert_opening_fixture(connection)
        support_0019._insert_fixture_locations(
            connection,
            facts,
            observed_type="personal",
        )
        # Isolate the 0020 assignment branch without constructing unrelated
        # submitted-round evidence. The 0019 location guard remains enabled.
        connection.exec_driver_sql(
            "DROP TRIGGER trg_stocktake_recount_scope_assignments_validate_0018"
        )
        support_0019._insert_recount_assignment(
            connection,
            assignment_id=support._uuid(2021),
            case_id=support._uuid(2022),
            facts=facts,
            scope_id=facts["scope_observed"],
            assignment_hash="2" * 64,
        )

        rejected = connection.begin_nested()
        with pytest.raises(sa.exc.DatabaseError, match="reclassification"):
            connection.exec_driver_sql(
                "UPDATE stock_locations SET location_type = 'region' "
                "WHERE id = ?",
                (facts["location_observed"],),
            )
        rejected.rollback()
        assert connection.exec_driver_sql(
            "SELECT location_type FROM stock_locations WHERE id = ?",
            (facts["location_observed"],),
        ).scalar_one() == "personal"


def test_0020_preflight_blocks_existing_completion_location_drift(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0020-drift.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0019)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        facts = support._insert_opening_fixture(connection)
        support_0019._insert_fixture_locations(
            connection,
            facts,
            observed_type="personal",
        )
        support._insert_completion(
            connection,
            facts,
            completion_id=support._uuid(2031),
            scope_id=facts["scope_observed"],
            count_lines=0,
            observations=0,
            total=0,
            zero=True,
            key_hash="3" * 64,
        )
        connection.exec_driver_sql(
            "UPDATE stock_locations SET location_type = 'region' WHERE id = ?",
            (facts["location_observed"],),
        )

    with pytest.raises(RuntimeError, match="current personal location"):
        command.upgrade(config, REVISION_0020)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0019
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
            "AND name = ?",
            (LOCATION_TRIGGER,),
        ).scalar_one() == 0


def test_0020_preflight_blocks_existing_assignment_with_missing_location_graph(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0020-orphan.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0019)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "DROP TRIGGER trg_stocktake_recount_scope_assignments_validate_0018"
        )
        connection.exec_driver_sql(
            "DROP TRIGGER "
            "trg_stocktake_recount_scope_assignments_technician_personal_location_0019"
        )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_recount_scope_assignments "
            "(id, recount_case_id, task_id, source_round_id, scope_id, "
            "assignee_user_id, assignee_person_id, "
            "assignee_role_assignment_id, authorization_version, role_code, "
            "scope_type, scope_id_snapshot, authorization_sha256, "
            "assignment_sha256, assigned_at, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, 1, 'technician', 'person', ?, ?, ?, ?, ?)",
            (
                support._uuid(2041),
                support._uuid(2042),
                support._uuid(2043),
                support._uuid(2044),
                support._uuid(2045),
                "00000000-0000-0000-0000-000000002046",
                support._uuid(2047),
                support._uuid(2048),
                support._uuid(2047),
                "4" * 64,
                "5" * 64,
                support.NOW,
                support.NOW,
            ),
        )

    with pytest.raises(RuntimeError, match="current personal location"):
        command.upgrade(config, REVISION_0020)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0019


def test_0020_postgresql_offline_sql_has_unfiltered_always_update_guard(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        support._config(
            "postgresql+psycopg://migration_user:password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0019}:{REVISION_0020}",
        sql=True,
    )
    sql = output.getvalue()
    assert "FROM public.stocktake_scope_count_completions AS completion" in sql
    assert (
        "FROM public.stocktake_recount_scope_assignments AS assignment" in sql
    )
    assert f"CREATE FUNCTION public.{PG_FUNCTION}()" in sql
    assert (
        "LOCK TABLE public.stocktake_scope_count_completions,\n"
        "                   public.stocktake_recount_scope_assignments\n"
        "            IN SHARE MODE"
    ) in sql
    start = sql.index(f"CREATE TRIGGER {LOCATION_TRIGGER}")
    trigger_statement = sql[start : sql.index(";", start)]
    assert "BEFORE UPDATE ON public.stock_locations" in trigger_statement
    assert "UPDATE OF" not in trigger_statement
    assert " WHEN " not in trigger_statement
    assert (
        "ALTER TABLE public.stock_locations ENABLE ALWAYS TRIGGER "
        f"{LOCATION_TRIGGER}"
    ) in sql
    assert (
        f"REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() "
        "FROM PUBLIC, star_oam_api"
    ) in sql


def test_0020_downgrade_only_removes_its_guard(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0020-downgrade.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0020)
    command.downgrade(config, REVISION_0019)
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).all()
        }
        assert LOCATION_TRIGGER not in triggers
        assert support_0019.COMPLETION_TRIGGER in triggers
        assert support_0019.RECOUNT_ASSIGNMENT_TRIGGER in triggers
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0019
