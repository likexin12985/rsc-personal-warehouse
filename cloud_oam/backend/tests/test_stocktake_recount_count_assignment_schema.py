from __future__ import annotations

import io

from alembic import command
import pytest
import sqlalchemy as sa

import test_opening_count_observation_schema as support
import test_opening_recount_schema as recount_support


REVISION_0018 = "20260831_0018"
REVISION_0017 = "20260831_0017"
REVISION_0020 = "20260831_0020"
REVISION_0021 = "20260831_0021"
EVIDENCE_AT = "2026-08-30 00:00:04+00:00"
ADMIN_ROLE_ID = "10000000000040008000000000000001"


def _insert_personal_scope_locations(
    connection: sa.Connection, facts: dict[str, str]
) -> None:
    parent_id = support._uuid(2100)
    connection.exec_driver_sql(
        "INSERT INTO stock_locations "
        "(id, code, name, location_type, owner_org_id, parent_id, "
        "custodian_person_id, status, updated_at, created_at) VALUES "
        "(?, 'REGION-0021', 'region', 'region', ?, NULL, NULL, 'active', ?, ?)",
        (parent_id, facts["owner"], support.NOW, support.NOW),
    )
    for ordinal, location_id in enumerate(
        (
            facts["location_observed"],
            facts["location_snapshot"],
            facts["location_zero"],
        ),
        start=1,
    ):
        connection.exec_driver_sql(
            "INSERT INTO stock_locations "
            "(id, code, name, location_type, owner_org_id, parent_id, "
            "custodian_person_id, status, updated_at, created_at) VALUES "
            "(?, ?, ?, 'personal', ?, ?, ?, 'active', ?, ?)",
            (
                location_id,
                f"PERSONAL-0021-{ordinal}",
                f"personal-{ordinal}",
                facts["owner"],
                parent_id,
                facts["person"],
                support.NOW,
                support.NOW,
            ),
        )


def _prepare_round_two(
    connection: sa.Connection,
) -> tuple[dict[str, str], tuple[str, str, str], str, str]:
    facts, completion_id, review_id, manager = (
        recount_support._insert_reviewed_recount_source(connection)
    )
    _insert_personal_scope_locations(connection, facts)
    case_id = recount_support._insert_recount_case(
        connection, facts, completion_id, review_id, manager
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
            "assignee_role_assignment_id, authorization_version, role_code, "
            "scope_type, scope_id_snapshot, authorization_sha256, "
            "assignment_sha256, assigned_at, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, 1, 'provincial_manager', "
            "'organization', ?, ?, ?, ?, ?)",
            (
                support._uuid(2110 + ordinal),
                case_id,
                facts["task"],
                facts["round"],
                scope_id,
                *manager,
                facts["owner"],
                "d" * 64,
                f"{ordinal}" * 64,
                recount_support.OPENED_AT,
                recount_support.OPENED_AT,
            ),
        )
    round_two_id = support._uuid(2120)
    connection.exec_driver_sql(
        "UPDATE stocktake_tasks SET status = 'counting', current_round_no = 2, "
        "version = version + 1, updated_at = ? WHERE id = ?",
        (recount_support.OPENED_AT, facts["task"]),
    )
    connection.exec_driver_sql(
        "INSERT INTO stocktake_rounds "
        "(id, task_id, round_no, round_type, status, submitted_by_user_id, "
        "started_at, submitted_at, count_manifest_sha256, "
        "idempotency_key_hash, recount_case_id, created_at, updated_at) "
        "VALUES (?, ?, 2, 'recount', 'counting', NULL, ?, NULL, NULL, ?, ?, ?, ?)",
        (
            round_two_id,
            facts["task"],
            recount_support.OPENED_AT,
            "e" * 64,
            case_id,
            recount_support.OPENED_AT,
            recount_support.OPENED_AT,
        ),
    )
    return facts, manager, case_id, round_two_id


def _insert_admin(
    connection: sa.Connection, facts: dict[str, str]
) -> tuple[str, str, str]:
    user_id = "00000000-0000-0000-0000-000000002150"
    person_id = support._uuid(2151)
    assignment_id = support._uuid(2152)
    connection.exec_driver_sql(
        "INSERT INTO people "
        "(id, external_object_id, organization_id, employee_no, name, "
        "mobile_encrypted, mobile_hash, employment_status, source_updated_at, "
        "created_at, updated_at) VALUES "
        "(?, NULL, ?, 'ADMIN-0021', 'admin', NULL, NULL, 'active', NULL, ?, ?)",
        (person_id, facts["owner"], support.NOW, support.NOW),
    )
    connection.exec_driver_sql(
        "INSERT INTO users "
        "(id, mobile, name, password_hash, role, province, is_active, "
        "require_password_change, created_at, updated_at, person_id, "
        "account_status, last_login_at, authorization_version) VALUES "
        "(?, '13800002150', 'admin', 'not-a-password', 'admin', NULL, 1, 0, "
        "?, ?, ?, 'active', NULL, 1)",
        (user_id, support.NOW, support.NOW, person_id),
    )
    connection.exec_driver_sql(
        "INSERT INTO role_assignments "
        "(id, user_id, role_id, scope_type, scope_id, valid_from, valid_to, "
        "status, assigned_by, updated_at, created_at, revoked_at, revoked_by, "
        "reason) VALUES "
        "(?, ?, ?, 'national', '*', ?, NULL, 'active', ?, ?, ?, NULL, NULL, '')",
        (
            assignment_id,
            user_id,
            ADMIN_ROLE_ID,
            support.NOW,
            user_id,
            support.NOW,
            support.NOW,
        ),
    )
    return user_id, person_id, assignment_id


def _insert_review_path_source(
    connection: sa.Connection,
    *,
    region_decision: str,
    headquarters_decision: str | None,
    same_headquarters_actor: bool,
) -> tuple[
    dict[str, str],
    str,
    tuple[str, str, str],
    str,
    str | None,
]:
    facts, submission_id = support._insert_0016_submitted_fixture(
        connection, pending_observation=False
    )
    _insert_personal_scope_locations(connection, facts)
    manager = support._insert_0016_manager(connection, facts)
    admin = _insert_admin(connection, facts)
    completion_id = support._uuid(2160)
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
            "a" * 64,
            facts["user"],
            facts["person"],
            facts["assignment"],
            support.LATER,
            support.LATER,
        ),
    )
    region_review_id = support._uuid(2161)
    connection.exec_driver_sql(
        "INSERT INTO stocktake_reviews "
        "(id, task_id, round_id, review_stage, reviewer_user_id, "
        "reviewer_person_id, reviewer_role_assignment_id, "
        "authorization_version, decision, comment, decision_manifest_sha256, "
        "idempotency_key_hash, reviewed_at, created_at) VALUES "
        "(?, ?, ?, 'region', ?, ?, ?, 1, ?, 'region review', ?, ?, "
        "'2026-08-30 00:00:02+00:00', '2026-08-30 00:00:02+00:00')",
        (
            region_review_id,
            facts["task"],
            facts["round"],
            *manager,
            region_decision,
            "b" * 64,
            "c" * 64,
        ),
    )
    headquarters_review_id = None
    if headquarters_decision is not None:
        headquarters_review_id = support._uuid(2162)
        if same_headquarters_actor:
            manager_admin_assignment_id = support._uuid(2153)
            connection.exec_driver_sql(
                "INSERT INTO role_assignments "
                "(id, user_id, role_id, scope_type, scope_id, valid_from, "
                "valid_to, status, assigned_by, updated_at, created_at, "
                "revoked_at, revoked_by, reason) VALUES "
                "(?, ?, ?, 'national', '*', ?, NULL, 'active', ?, ?, ?, "
                "NULL, NULL, '')",
                (
                    manager_admin_assignment_id,
                    manager[0],
                    ADMIN_ROLE_ID,
                    support.NOW,
                    manager[0],
                    support.NOW,
                    support.NOW,
                ),
            )
            headquarters_actor = (
                manager[0],
                manager[1],
                manager_admin_assignment_id,
            )
        else:
            headquarters_actor = admin
        connection.exec_driver_sql(
            "INSERT INTO stocktake_reviews "
            "(id, task_id, round_id, review_stage, reviewer_user_id, "
            "reviewer_person_id, reviewer_role_assignment_id, "
            "authorization_version, decision, comment, "
            "decision_manifest_sha256, idempotency_key_hash, reviewed_at, "
            "created_at) VALUES "
            "(?, ?, ?, 'headquarters', ?, ?, ?, 1, ?, 'headquarters review', "
            "?, ?, '2026-08-30 00:00:03+00:00', "
            "'2026-08-30 00:00:03+00:00')",
            (
                headquarters_review_id,
                facts["task"],
                facts["round"],
                *headquarters_actor,
                headquarters_decision,
                "d" * 64,
                "e" * 64,
            ),
        )
    connection.exec_driver_sql(
        "UPDATE stocktake_tasks SET status = 'recount_required', updated_at = "
        "'2026-08-30 00:00:03+00:00' WHERE id = ?",
        (facts["task"],),
    )
    return (
        facts,
        completion_id,
        manager,
        region_review_id,
        headquarters_review_id,
    )


def _insert_case_for_review(
    connection: sa.Connection,
    *,
    facts: dict[str, str],
    completion_id: str,
    manager: tuple[str, str, str],
    trigger_review_id: str,
) -> None:
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
        "'review-path-0021', ?, ?, ?, 1, 'provincial_manager', "
        "'organization', ?, ?, '2026-08-30 00:00:04+00:00', "
        "'2026-08-30 00:00:04+00:00')",
        (
            support._uuid(2163),
            facts["task"],
            facts["round"],
            submission_id,
            completion_id,
            trigger_review_id,
            support.HASH_A,
            "1" * 64,
            "2" * 64,
            "3" * 64,
            "4" * 64,
            *manager,
            facts["owner"],
            "5" * 64,
        ),
    )


def _insert_round_two_count_line(
    connection: sa.Connection,
    facts: dict[str, str],
    round_id: str,
    *,
    user_id: str,
    ordinal: int,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_count_lines "
        "(id, task_id, round_id, scope_id, stock_account_id, counted_qty, "
        "count_method, reason_code, remark, counted_by_user_id, counted_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, 'manual', NULL, "
        "'', ?, ?, ?, ?)",
        (
            support._uuid(ordinal),
            facts["task"],
            round_id,
            facts["scope_snapshot"],
            facts["account_cutoff"],
            user_id,
            EVIDENCE_AT,
            EVIDENCE_AT,
            EVIDENCE_AT,
        ),
    )


def _insert_round_two_observation(
    connection: sa.Connection,
    facts: dict[str, str],
    round_id: str,
    *,
    user_id: str,
    ordinal: int,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO stocktake_count_observations "
        "(id, task_id, round_id, scope_id, observation_no, owner_org_id, "
        "location_id, custodian_person_id_snapshot, material_id, "
        "material_identifier_raw, material_identifier_type, condition_code, "
        "availability_bucket, lot_id, lot_no_raw, serial_id, serial_no_raw, "
        "serial_identifier_type, counted_qty, verification_status, "
        "count_method, reason_code, remark, counted_by_user_id, counted_at, "
        "dimension_sha256, request_sha256, idempotency_key_hash, created_at) "
        "VALUES (?, ?, ?, ?, 1, ?, ?, ?, NULL, 'unexpected-0021', 'unknown', "
        "'new', 'available', NULL, NULL, NULL, NULL, NULL, 1, "
        "'pending_verification', 'manual', NULL, '', ?, ?, ?, ?, ?, ?)",
        (
            support._uuid(ordinal),
            facts["task"],
            round_id,
            facts["scope_observed"],
            facts["owner"],
            facts["location_observed"],
            facts["person"],
            user_id,
            EVIDENCE_AT,
            "4" * 64,
            "5" * 64,
            "6" * 64,
            EVIDENCE_AT,
        ),
    )


def _insert_round_two_completion(
    connection: sa.Connection,
    facts: dict[str, str],
    manager: tuple[str, str, str],
    round_id: str,
    *,
    scope_id: str,
    count_lines: int,
    observations: int,
    total: int,
    zero: bool,
    ordinal: int,
    authorization_version: int = 2,
    actor: tuple[str, str, str] | None = None,
    role_code: str = "provincial_manager",
    scope_type: str = "organization",
    scope_id_snapshot: str | None = None,
) -> None:
    actor = actor or manager
    connection.exec_driver_sql(
        "INSERT INTO stocktake_scope_count_completions "
        "(id, task_id, round_id, scope_id, count_line_count, "
        "observation_line_count, serial_count, total_counted_qty, "
        "zero_confirmed, evidence_manifest_sha256, request_sha256, "
        "idempotency_key_hash, completed_by_user_id, completed_by_person_id, "
        "completed_role_assignment_id, authorization_version, role_code, "
        "scope_type, scope_id_snapshot, authorization_sha256, completed_at, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?)",
        (
            support._uuid(ordinal),
            facts["task"],
            round_id,
            scope_id,
            count_lines,
            observations,
            total,
            zero,
            "7" * 64,
            "8" * 64,
            f"{ordinal % 10}" * 64,
            *actor,
            authorization_version,
            role_code,
            scope_type,
            scope_id_snapshot or facts["owner"],
            "9" * 64,
            EVIDENCE_AT,
            EVIDENCE_AT,
        ),
    )


def test_0021_round_two_uses_case_assignment_and_accepts_later_auth_version(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'round-assignment.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        facts, manager, _case_id, round_two_id = _prepare_round_two(connection)
    command.upgrade(config, REVISION_0021)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE users SET authorization_version = 2 WHERE id = ?",
            (manager[0],),
        )
        with pytest.raises(sa.exc.DatabaseError, match="unassigned"):
            _insert_round_two_count_line(
                connection,
                facts,
                round_two_id,
                user_id=facts["user"],
                ordinal=2130,
            )
        _insert_round_two_count_line(
            connection,
            facts,
            round_two_id,
            user_id=manager[0],
            ordinal=2131,
        )

        with pytest.raises(sa.exc.DatabaseError, match="unassigned"):
            _insert_round_two_observation(
                connection,
                facts,
                round_two_id,
                user_id=facts["user"],
                ordinal=2132,
            )
        _insert_round_two_observation(
            connection,
            facts,
            round_two_id,
            user_id=manager[0],
            ordinal=2133,
        )

        with pytest.raises(sa.exc.DatabaseError, match="canonical"):
            _insert_round_two_completion(
                connection,
                facts,
                manager,
                round_two_id,
                scope_id=facts["scope_zero"],
                count_lines=0,
                observations=0,
                total=0,
                zero=True,
                ordinal=2134,
                authorization_version=1,
                actor=(facts["user"], facts["person"], facts["assignment"]),
                role_code="technician",
                scope_type="person",
                scope_id_snapshot=facts["person"],
            )
        _insert_round_two_completion(
            connection,
            facts,
            manager,
            round_two_id,
            scope_id=facts["scope_observed"],
            count_lines=0,
            observations=1,
            total=1,
            zero=False,
            ordinal=2135,
        )
        _insert_round_two_completion(
            connection,
            facts,
            manager,
            round_two_id,
            scope_id=facts["scope_snapshot"],
            count_lines=1,
            observations=0,
            total=0,
            zero=False,
            ordinal=2136,
        )
        _insert_round_two_completion(
            connection,
            facts,
            manager,
            round_two_id,
            scope_id=facts["scope_zero"],
            count_lines=0,
            observations=0,
            total=0,
            zero=True,
            ordinal=2137,
        )

        assert connection.exec_driver_sql(
            "SELECT count(*) FROM stocktake_scope_count_completions "
            "WHERE round_id = ? AND completed_by_user_id = ?",
            (round_two_id, manager[0]),
        ).scalar_one() == 3


@pytest.mark.parametrize(
    ("region_decision", "headquarters_decision"),
    [
        ("recount", None),
        ("reject", None),
        ("approve", "reject"),
    ],
)
def test_0021_accepts_only_supported_recount_review_paths(
    tmp_path,
    monkeypatch,
    region_decision: str,
    headquarters_decision: str | None,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    suffix = headquarters_decision or region_decision
    url = f"sqlite+pysqlite:///{tmp_path / f'legal-review-{suffix}.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0017)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        facts, completion_id, manager, region_review_id, hq_review_id = (
            _insert_review_path_source(
                connection,
                region_decision=region_decision,
                headquarters_decision=headquarters_decision,
                same_headquarters_actor=False,
            )
        )
    command.upgrade(config, REVISION_0021)
    with engine.begin() as connection:
        _insert_case_for_review(
            connection,
            facts=facts,
            completion_id=completion_id,
            manager=manager,
            trigger_review_id=hq_review_id or region_review_id,
        )
        assert connection.exec_driver_sql(
            "SELECT trigger_review_id FROM stocktake_recount_cases"
        ).scalar_one() == (hq_review_id or region_review_id)


@pytest.mark.parametrize(
    (
        "case_name",
        "region_decision",
        "headquarters_decision",
        "same_headquarters_actor",
        "trigger_stage",
    ),
    [
        ("region-approve", "approve", None, False, "region"),
        ("hq-approve", "approve", "approve", False, "headquarters"),
        ("hq-recount", "approve", "recount", False, "headquarters"),
        ("hq-same-person", "approve", "reject", True, "headquarters"),
        ("stale-region", "approve", "reject", False, "region"),
    ],
)
def test_0021_rejects_unsupported_or_unseparated_review_paths(
    tmp_path,
    monkeypatch,
    case_name: str,
    region_decision: str,
    headquarters_decision: str | None,
    same_headquarters_actor: bool,
    trigger_stage: str,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / f'illegal-review-{case_name}.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0017)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        facts, completion_id, manager, region_review_id, hq_review_id = (
            _insert_review_path_source(
                connection,
                region_decision=region_decision,
                headquarters_decision=headquarters_decision,
                same_headquarters_actor=same_headquarters_actor,
            )
        )
    command.upgrade(config, REVISION_0021)
    trigger_review_id = (
        region_review_id if trigger_stage == "region" else hq_review_id
    )
    assert trigger_review_id is not None
    with engine.begin() as connection:
        with pytest.raises(sa.exc.DatabaseError, match="causality"):
            _insert_case_for_review(
                connection,
                facts=facts,
                completion_id=completion_id,
                manager=manager,
                trigger_review_id=trigger_review_id,
            )
        assert connection.exec_driver_sql(
            "SELECT count(*) FROM stocktake_recount_cases"
        ).scalar_one() == 0


def test_0021_upgrade_preflight_rejects_round_two_initial_assignee_evidence(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'polluted-assignment.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        facts, _manager, _case_id, round_two_id = _prepare_round_two(connection)
    command.upgrade(config, REVISION_0020)
    with engine.begin() as connection:
        _insert_round_two_count_line(
            connection,
            facts,
            round_two_id,
            user_id=facts["user"],
            ordinal=2140,
        )
    with pytest.raises(RuntimeError, match="0021 preflight failed"):
        command.upgrade(config, REVISION_0021)
    assert engine.connect().exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalar_one() == REVISION_0020


def test_0021_empty_round_trip_and_postgresql_offline_contract(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'empty-round-trip.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0021)
    command.downgrade(config, REVISION_0020)
    engine = sa.create_engine(url)
    with engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).all()
        }
    assert "trg_stocktake_count_lines_completion_seal_insert_0011" in triggers
    assert "trg_stocktake_count_observations_validate_insert_0013" in triggers
    assert (
        "trg_stocktake_scope_count_completions_validate_insert_0011" in triggers
    )
    assert "trg_stocktake_recount_cases_validate_0018" in triggers

    output = io.StringIO()
    command.upgrade(
        support._config(
            "postgresql+psycopg://migration_user:password@localhost/rsc",
            output_buffer=output,
        ),
        f"{REVISION_0020}:{REVISION_0021}",
        sql=True,
    )
    sql = output.getvalue()
    assert "IN ACCESS EXCLUSIVE MODE" in sql
    assert "0021 preflight failed" in sql
    assert "SET search_path = pg_catalog, public" in sql
    assert "ENABLE ALWAYS TRIGGER trg_stocktake_count_lines_assignment_0021" in sql
    assert (
        "ENABLE ALWAYS TRIGGER "
        "trg_stocktake_count_observations_assignment_0021" in sql
    )
    assert (
        "ENABLE ALWAYS TRIGGER trg_stocktake_scope_completions_assignment_0021"
        in sql
    )
    assert "review_stage = 'headquarters'" in sql
    assert "reviewer_person_id <>" in sql
    assert "p_authorization_version >=" in sql
    for function_name in (
        "rsc_stocktake_round_assignment_valid_0021",
        "rsc_validate_stocktake_count_line_insert_0021",
        "rsc_validate_stocktake_observation_insert_0021",
        "rsc_validate_stocktake_scope_completion_insert_0021",
        "rsc_validate_stocktake_recount_case_0021",
    ):
        assert f"REVOKE EXECUTE ON FUNCTION public.{function_name}" in sql


def test_0021_populated_recount_graph_blocks_downgrade(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'downgrade-blocked.db'}"
    config = support._config(url)
    command.upgrade(config, REVISION_0018)
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        _prepare_round_two(connection)
    command.upgrade(config, REVISION_0021)
    with pytest.raises(RuntimeError, match="cannot downgrade 0021"):
        command.downgrade(config, REVISION_0020)
    assert engine.connect().exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalar_one() == REVISION_0021
