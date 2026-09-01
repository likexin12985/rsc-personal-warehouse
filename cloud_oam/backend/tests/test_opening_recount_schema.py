from __future__ import annotations

import io

from alembic import command
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect

import test_opening_count_observation_schema as support


REVISION_0017 = "20260831_0017"
REVISION_0018 = "20260831_0018"
OPENED_AT = "2026-08-30 00:00:03+00:00"


def _insert_reviewed_recount_source(
    connection: sa.Connection,
) -> tuple[dict[str, str], str, str, tuple[str, str, str]]:
    facts, submission_id = support._insert_0016_submitted_fixture(
        connection, pending_observation=False
    )
    manager = support._insert_0016_manager(connection, facts)
    completion_id = support._uuid(1811)
    review_id = support._uuid(1815)
    connection.exec_driver_sql(
        "INSERT INTO stocktake_difference_set_completions "
        "(id, task_id, round_id, round_submission_id, difference_count, "
        "physical_difference_count, control_difference_count, "
        "pending_observation_difference_count, total_affected_qty, "
        "difference_manifest_sha256, request_sha256, idempotency_key_hash, "
        "completed_by_user_id, completed_by_person_id, "
        "completed_role_assignment_id, authorization_version, completed_at, "
        "created_at) VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?, "
        "1, ?, ?)",
        (
            completion_id,
            facts["task"],
            facts["round"],
            submission_id,
            support.HASH_A,
            support.HASH_B,
            "8" * 64,
            facts["user"],
            facts["person"],
            facts["assignment"],
            support.LATER,
            support.LATER,
        ),
    )
    connection.exec_driver_sql(
        "INSERT INTO stocktake_reviews "
        "(id, task_id, round_id, review_stage, reviewer_user_id, "
        "reviewer_person_id, reviewer_role_assignment_id, "
        "authorization_version, decision, comment, decision_manifest_sha256, "
        "idempotency_key_hash, reviewed_at, created_at) VALUES "
        "(?, ?, ?, 'region', ?, ?, ?, 1, 'recount', 'controlled recount', ?, "
        "?, '2026-08-30 00:00:02+00:00', "
        "'2026-08-30 00:00:02+00:00')",
        (
            review_id,
            facts["task"],
            facts["round"],
            *manager,
            support.HASH_C,
            "9" * 64,
        ),
    )
    connection.exec_driver_sql(
        "UPDATE stocktake_tasks SET status = 'recount_required', "
        "updated_at = '2026-08-30 00:00:02+00:00' WHERE id = ?",
        (facts["task"],),
    )
    return facts, completion_id, review_id, manager


def _insert_recount_case(
    connection: sa.Connection,
    facts: dict[str, str],
    completion_id: str,
    review_id: str,
    manager: tuple[str, str, str],
) -> str:
    case_id = support._uuid(1818)
    submission_id = connection.exec_driver_sql(
        "SELECT id FROM stocktake_round_submissions WHERE task_id = ?",
        (facts["task"],),
    ).scalar_one()
    connection.exec_driver_sql(
        "INSERT INTO stocktake_recount_cases "
        "(id, task_id, source_round_id, source_round_submission_id, "
        "source_difference_completion_id, trigger_review_id, next_round_no, "
        "scope_count, scope_manifest_sha256, assignment_manifest_sha256, "
        "recount_manifest_sha256, request_sha256, idempotency_key_hash, "
        "reason, opened_by_user_id, opened_by_person_id, "
        "opened_role_assignment_id, authorization_version, role_code, "
        "scope_type, scope_id_snapshot, authorization_sha256, opened_at, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, 2, 3, ?, ?, ?, ?, ?, "
        "'review requires recount', ?, ?, ?, 1, 'provincial_manager', "
        "'organization', ?, ?, ?, ?)",
        (
            case_id,
            facts["task"],
            facts["round"],
            submission_id,
            completion_id,
            review_id,
            support.HASH_A,
            "1" * 64,
            "2" * 64,
            "3" * 64,
            "4" * 64,
            *manager,
            facts["owner"],
            "5" * 64,
            OPENED_AT,
            OPENED_AT,
        ),
    )
    return case_id


def test_0018_empty_upgrade_round_trip_and_postgresql_guards(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'recount-empty.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0018)
    inspector = inspect(sa.create_engine(url))
    assert {
        "stocktake_recount_cases",
        "stocktake_recount_scope_assignments",
    } <= set(inspector.get_table_names())
    assert "recount_case_id" in {
        row["name"] for row in inspector.get_columns("stocktake_rounds")
    }
    command.downgrade(config, REVISION_0017)

    output = io.StringIO()
    command.upgrade(
        support._config(
            "postgresql+psycopg://migration_user:password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0017}:{REVISION_0018}",
        sql=True,
    )
    sql = output.getvalue()
    assert "ENABLE ALWAYS TRIGGER trg_stocktake_recount_graph_task_0018" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert (
        "REVOKE ALL PRIVILEGES ON TABLE stocktake_recount_cases, "
        "stocktake_recount_scope_assignments FROM PUBLIC, star_oam_api"
    ) in sql


def test_0018_preflight_blocks_legacy_round_two_before_ddl(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'recount-polluted.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0017)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        facts = support._insert_opening_fixture(connection)
        connection.exec_driver_sql(
            "INSERT INTO stocktake_rounds "
            "(id, task_id, round_no, round_type, status, submitted_by_user_id, "
            "started_at, submitted_at, count_manifest_sha256, "
            "idempotency_key_hash, created_at, updated_at) VALUES "
            "(?, ?, 2, 'recount', 'counting', NULL, ?, NULL, NULL, ?, ?, ?)",
            (
                support._uuid(1820),
                facts["task"],
                support.LATER,
                "6" * 64,
                support.LATER,
                support.LATER,
            ),
        )
    with pytest.raises(RuntimeError, match="clean single-round graph"):
        command.upgrade(config, REVISION_0018)
    assert "stocktake_recount_cases" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0017


def test_0018_raw_round_two_cross_task_and_branch_are_rejected(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'recount-guards.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0017)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        facts, completion_id, review_id, manager = _insert_reviewed_recount_source(
            connection
        )
    command.upgrade(config, REVISION_0018)

    with engine.begin() as connection:
        with pytest.raises(sa.exc.DatabaseError, match="causality"):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_rounds "
                "(id, task_id, round_no, round_type, status, "
                "submitted_by_user_id, started_at, submitted_at, "
                "count_manifest_sha256, idempotency_key_hash, "
                "recount_case_id, created_at, updated_at) VALUES "
                "(?, ?, 2, 'recount', 'counting', NULL, ?, NULL, NULL, ?, "
                "NULL, ?, ?)",
                (
                    support._uuid(1821),
                    facts["task"],
                    OPENED_AT,
                    "7" * 64,
                    OPENED_AT,
                    OPENED_AT,
                ),
            )

        case_id = _insert_recount_case(
            connection, facts, completion_id, review_id, manager
        )
        with pytest.raises(sa.exc.DatabaseError, match="assignment is invalid"):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_recount_scope_assignments "
                "(id, recount_case_id, task_id, source_round_id, scope_id, "
                "assignee_user_id, assignee_person_id, "
                "assignee_role_assignment_id, authorization_version, "
                "role_code, scope_type, scope_id_snapshot, "
                "authorization_sha256, assignment_sha256, assigned_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, "
                "'technician', 'person', ?, ?, ?, ?, ?)",
                (
                    support._uuid(1822),
                    case_id,
                    support._uuid(9999),
                    facts["round"],
                    facts["scope_observed"],
                    facts["user"],
                    facts["person"],
                    facts["assignment"],
                    facts["person"],
                    "8" * 64,
                    "9" * 64,
                    OPENED_AT,
                    OPENED_AT,
                ),
            )

        for ordinal, scope_id in enumerate(
            (
                facts["scope_observed"],
                facts["scope_snapshot"],
                facts["scope_zero"],
            ),
            start=1,
        ):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_recount_scope_assignments "
                "(id, recount_case_id, task_id, source_round_id, scope_id, "
                "assignee_user_id, assignee_person_id, "
                "assignee_role_assignment_id, authorization_version, "
                "role_code, scope_type, scope_id_snapshot, "
                "authorization_sha256, assignment_sha256, assigned_at, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, "
                "'technician', 'person', ?, ?, ?, ?, ?)",
                (
                    support._uuid(1830 + ordinal),
                    case_id,
                    facts["task"],
                    facts["round"],
                    scope_id,
                    facts["user"],
                    facts["person"],
                    facts["assignment"],
                    facts["person"],
                    "a" * 64,
                    f"{ordinal}" * 64,
                    OPENED_AT,
                    OPENED_AT,
                ),
            )
        connection.exec_driver_sql(
            "UPDATE stocktake_tasks SET status = 'counting', "
            "current_round_no = 2, version = version + 1, updated_at = ? "
            "WHERE id = ?",
            (OPENED_AT, facts["task"]),
        )
        connection.exec_driver_sql(
            "INSERT INTO stocktake_rounds "
            "(id, task_id, round_no, round_type, status, submitted_by_user_id, "
            "started_at, submitted_at, count_manifest_sha256, "
            "idempotency_key_hash, recount_case_id, created_at, updated_at) "
            "VALUES (?, ?, 2, 'recount', 'counting', NULL, ?, NULL, NULL, ?, "
            "?, ?, ?)",
            (
                support._uuid(1840),
                facts["task"],
                OPENED_AT,
                "b" * 64,
                case_id,
                OPENED_AT,
                OPENED_AT,
            ),
        )
        assert connection.exec_driver_sql(
            "SELECT status FROM stocktake_rounds WHERE id = ?",
            (facts["round"],),
        ).scalar_one() == "submitted"
        with pytest.raises(sa.exc.IntegrityError, match="UNIQUE constraint"):
            connection.exec_driver_sql(
                "INSERT INTO stocktake_rounds "
                "(id, task_id, round_no, round_type, status, "
                "submitted_by_user_id, started_at, submitted_at, "
                "count_manifest_sha256, idempotency_key_hash, "
                "recount_case_id, created_at, updated_at) VALUES "
                "(?, ?, 2, 'recount', 'counting', NULL, ?, NULL, NULL, ?, ?, "
                "?, ?)",
                (
                    support._uuid(1841),
                    facts["task"],
                    OPENED_AT,
                    "c" * 64,
                    case_id,
                    OPENED_AT,
                    OPENED_AT,
                ),
            )
        with pytest.raises(sa.exc.DatabaseError, match="causality"):
            connection.exec_driver_sql(
                "UPDATE stocktake_rounds SET status = 'superseded' WHERE id = ?",
                (support._uuid(1840),),
            )
    with pytest.raises(RuntimeError, match="cannot downgrade 0018"):
        command.downgrade(config, REVISION_0017)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == REVISION_0018
