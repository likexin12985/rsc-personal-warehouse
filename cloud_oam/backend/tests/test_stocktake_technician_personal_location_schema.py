from __future__ import annotations

import io

from alembic import command
import pytest
import sqlalchemy as sa

import test_opening_count_observation_schema as support
import test_opening_recount_schema as recount_support


REVISION_0018 = "20260831_0018"
REVISION_0019 = "20260831_0019"
PG_FUNCTION = "rsc_validate_stocktake_technician_personal_location_0019"
COMPLETION_TRIGGER = (
    "trg_stocktake_scope_count_completions_technician_personal_location_0019"
)
RECOUNT_ASSIGNMENT_TRIGGER = (
    "trg_stocktake_recount_scope_assignments_technician_personal_location_0019"
)


def _insert_location(
    connection: sa.Connection,
    *,
    location_id: str,
    code: str,
    owner_org_id: str,
    location_type: str,
    parent_id: str | None,
    custodian_person_id: str | None,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stock_locations "
        "(id, code, name, location_type, owner_org_id, parent_id, "
        "custodian_person_id, status, updated_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (
            location_id,
            code,
            code,
            location_type,
            owner_org_id,
            parent_id,
            custodian_person_id,
            support.NOW,
            support.NOW,
        ),
    )


def _insert_fixture_locations(
    connection: sa.Connection,
    facts: dict[str, str],
    *,
    observed_type: str,
    all_other_personal: bool = True,
) -> None:
    parent_id = support._uuid(1900)
    _insert_location(
        connection,
        location_id=parent_id,
        code="0019-PARENT",
        owner_org_id=facts["owner"],
        location_type="region",
        parent_id=None,
        custodian_person_id=None,
    )
    _insert_location(
        connection,
        location_id=facts["location_observed"],
        code="0019-OBSERVED",
        owner_org_id=facts["owner"],
        location_type=observed_type,
        parent_id=parent_id if observed_type == "personal" else None,
        # A custodian on a region location reproduces the former unsafe seam.
        custodian_person_id=facts["person"],
    )
    other_type = "personal" if all_other_personal else "region"
    for code, location_id in (
        ("0019-SNAPSHOT", facts["location_snapshot"]),
        ("0019-ZERO", facts["location_zero"]),
    ):
        _insert_location(
            connection,
            location_id=location_id,
            code=code,
            owner_org_id=facts["owner"],
            location_type=other_type,
            parent_id=parent_id if other_type == "personal" else None,
            custodian_person_id=facts["person"],
        )


def _insert_recount_assignment(
    connection: sa.Connection,
    *,
    assignment_id: str,
    case_id: str,
    facts: dict[str, str],
    scope_id: str,
    assignment_hash: str,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_recount_scope_assignments "
        "(id, recount_case_id, task_id, source_round_id, scope_id, "
        "assignee_user_id, assignee_person_id, assignee_role_assignment_id, "
        "authorization_version, role_code, scope_type, scope_id_snapshot, "
        "authorization_sha256, assignment_sha256, assigned_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'technician', 'person', ?, ?, "
        "?, ?, ?)",
        (
            assignment_id,
            case_id,
            facts["task"],
            facts["round"],
            scope_id,
            facts["user"],
            facts["person"],
            facts["assignment"],
            facts["person"],
            "e" * 64,
            assignment_hash,
            recount_support.OPENED_AT,
            recount_support.OPENED_AT,
        ),
    )


def test_0019_sqlite_completion_rejects_region_with_custodian_and_allows_personal(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0019-completion.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        facts = support._insert_opening_fixture(connection)
        _insert_fixture_locations(
            connection,
            facts,
            observed_type="region",
        )

    command.upgrade(config, REVISION_0019)
    with engine.begin() as connection:
        rejected = connection.begin_nested()
        with pytest.raises(sa.exc.DatabaseError, match="personal stock location"):
            support._insert_completion(
                connection,
                facts,
                completion_id=support._uuid(1911),
                scope_id=facts["scope_observed"],
                count_lines=0,
                observations=0,
                total=0,
                zero=True,
                key_hash="1" * 64,
            )
        rejected.rollback()

        support._insert_completion(
            connection,
            facts,
            completion_id=support._uuid(1912),
            scope_id=facts["scope_zero"],
            count_lines=0,
            observations=0,
            total=0,
            zero=True,
            key_hash="2" * 64,
        )
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM stocktake_scope_count_completions"
        ).scalar_one() == 1


def test_0019_sqlite_recount_assignment_uses_exact_current_scope_location(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0019-recount.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        facts, completion_id, review_id, manager = (
            recount_support._insert_reviewed_recount_source(connection)
        )
        _insert_fixture_locations(
            connection,
            facts,
            observed_type="personal",
        )

    command.upgrade(config, REVISION_0019)
    with engine.begin() as connection:
        case_id = recount_support._insert_recount_case(
            connection,
            facts,
            completion_id,
            review_id,
            manager,
        )
        _insert_recount_assignment(
            connection,
            assignment_id=support._uuid(1921),
            case_id=case_id,
            facts=facts,
            scope_id=facts["scope_observed"],
            assignment_hash="1" * 64,
        )

        connection.exec_driver_sql(
            "UPDATE stock_locations SET location_type = 'region' WHERE id = ?",
            (facts["location_snapshot"],),
        )
        rejected = connection.begin_nested()
        with pytest.raises(sa.exc.DatabaseError, match="personal stock location"):
            _insert_recount_assignment(
                connection,
                assignment_id=support._uuid(1922),
                case_id=case_id,
                facts=facts,
                scope_id=facts["scope_snapshot"],
                assignment_hash="2" * 64,
            )
        rejected.rollback()
        assert connection.exec_driver_sql(
            "SELECT scope_id FROM stocktake_recount_scope_assignments"
        ).scalar_one() == facts["scope_observed"]


def test_0019_preflight_blocks_existing_technician_completion_on_region(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0019-polluted.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        facts = support._insert_opening_fixture(connection)
        _insert_fixture_locations(
            connection,
            facts,
            observed_type="region",
        )
        support._insert_completion(
            connection,
            facts,
            completion_id=support._uuid(1931),
            scope_id=facts["scope_observed"],
            count_lines=0,
            observations=0,
            total=0,
            zero=True,
            key_hash="3" * 64,
        )

    with pytest.raises(RuntimeError, match="exact personal location"):
        command.upgrade(config, REVISION_0019)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0018
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' "
            "AND name IN (?, ?)",
            (COMPLETION_TRIGGER, RECOUNT_ASSIGNMENT_TRIGGER),
        ).scalar_one() == 0


def test_0019_preflight_blocks_existing_assignment_with_missing_scope_graph(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0019-orphan.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(database_url)
    with engine.begin() as connection:
        # Simulate pre-migration corruption that bypassed the old row guard.
        # 0019 must inspect persisted graph state rather than trust old triggers.
        connection.exec_driver_sql(
            "DROP TRIGGER trg_stocktake_recount_scope_assignments_validate_0018"
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
                support._uuid(1941),
                support._uuid(1942),
                support._uuid(1943),
                support._uuid(1944),
                support._uuid(1945),
                "00000000-0000-0000-0000-000000001946",
                support._uuid(1947),
                support._uuid(1948),
                support._uuid(1947),
                "4" * 64,
                "5" * 64,
                support.NOW,
                support.NOW,
            ),
        )

    with pytest.raises(RuntimeError, match="exact personal location"):
        command.upgrade(config, REVISION_0019)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0018


def test_0019_postgresql_offline_sql_has_exact_always_insert_guards(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        support._config(
            "postgresql+psycopg://migration_user:password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0018}:{REVISION_0019}",
        sql=True,
    )
    sql = output.getvalue()
    assert "FROM public.stocktake_scope_count_completions AS completion" in sql
    assert (
        "FROM public.stocktake_recount_scope_assignments AS assignment" in sql
    )
    assert f"CREATE FUNCTION public.{PG_FUNCTION}()" in sql
    for table_name, trigger_name in (
        ("stocktake_scope_count_completions", COMPLETION_TRIGGER),
        ("stocktake_recount_scope_assignments", RECOUNT_ASSIGNMENT_TRIGGER),
    ):
        start = sql.index(f"CREATE TRIGGER {trigger_name}")
        statement = sql[start : sql.index(";", start)]
        assert f"BEFORE INSERT ON public.{table_name}" in statement
        assert " WHEN " not in statement
        assert "UPDATE OF" not in statement
        assert (
            f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER "
            f"{trigger_name}"
        ) in sql
    assert (
        f"REVOKE EXECUTE ON FUNCTION public.{PG_FUNCTION}() "
        "FROM PUBLIC, star_oam_api"
    ) in sql


def test_0019_downgrade_only_removes_its_guards(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0019-downgrade.db'}"
    config = support._config(database_url)
    command.upgrade(config, REVISION_0019)
    command.downgrade(config, REVISION_0018)
    engine = sa.create_engine(database_url)
    with engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).all()
        }
        assert COMPLETION_TRIGGER not in triggers
        assert RECOUNT_ASSIGNMENT_TRIGGER not in triggers
        assert "trg_stocktake_scope_count_completions_validate_insert_0011" in triggers
        assert "trg_stocktake_recount_scope_assignments_validate_0018" in triggers
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0018
