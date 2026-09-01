from __future__ import annotations

import io
from pathlib import Path

from alembic import command
import pytest
import sqlalchemy as sa
from sqlalchemy import event, inspect

from app.models import Base
from test_material_request_approval_schema import (
    HASH_A,
    HASH_B,
    LATER,
    NOW,
    _config,
    _insert_approval_graph,
    _insert_principal_graph,
    _insert_request,
    _uuid,
)


REVISION_0029 = "20260831_0029"
REVISION_0030 = "20260901_0030"
ROUTE_VERSION_ID = "22000000000040008000000000000001"


def _engine_with_foreign_keys(url: str) -> sa.Engine:
    engine = sa.create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    return engine


def _legacy_graph_then_0030(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    name: str,
    seed: int,
) -> tuple[sa.Engine, object, dict[str, str], dict[str, str], dict[str, str]]:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / name}"
    config = _config(url)
    command.upgrade(config, REVISION_0029)
    engine = _engine_with_foreign_keys(url)
    with engine.begin() as connection:
        ids = _insert_principal_graph(connection, seed)
        request = _insert_request(connection, ids, seed)
        graph = _insert_approval_graph(connection, ids, request, seed)
    command.upgrade(config, REVISION_0030)
    return engine, config, ids, request, graph


def _insert_command(
    connection: sa.Connection,
    *,
    command_id: str,
    request_id: str,
    operation: str,
    target_version: int,
    actor_user_id: str,
    actor_person_id: str,
    actor_assignment_id: str,
    occurred_at: str = NOW,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO material_request_commands "
        "(id, operation, request_id, target_version, idempotency_key_hash, "
        "request_reference, request_hash, result_hash, request_jsonb, result_jsonb, "
        "actor_user_id, actor_person_id, actor_role_assignment_id, "
        "authorization_version, occurred_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?, ?, 1, ?, ?)",
        (
            command_id,
            operation,
            request_id,
            target_version,
            f"{target_version:064x}"[-64:],
            f"schema-test-{command_id}",
            HASH_A,
            HASH_B,
            actor_user_id,
            actor_person_id,
            actor_assignment_id,
            occurred_at,
            occurred_at,
        ),
    )


def _insert_action(
    connection: sa.Connection,
    *,
    action_id: str,
    instance_id: str,
    step_id: str,
    command_id: str,
    action: str,
    actor_user_id: str,
    actor_person_id: str,
    actor_assignment_id: str,
    source_mode: str = "internal",
    occurred_at: str = NOW,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO approval_actions "
        "(id, instance_id, step_id, command_id, action, actor_user_id, "
        "actor_person_id, actor_role_assignment_id, authorization_version, "
        "source_mode, comment, occurred_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, '', ?, ?)",
        (
            action_id,
            instance_id,
            step_id,
            command_id,
            action,
            actor_user_id,
            actor_person_id,
            actor_assignment_id,
            source_mode,
            occurred_at,
            occurred_at,
        ),
    )


def _approve_and_open_next(
    connection: sa.Connection,
    *,
    ids: dict[str, str],
    request: dict[str, str],
    graph: dict[str, str],
    step_no: int,
    input_qty: str,
    approved_qty: str,
    command_seed: int,
    next_status: str = "open",
) -> None:
    step_id = graph[f"step{step_no}"]
    next_step_id = graph[f"step{step_no + 1}"]
    if step_no == 1:
        actor_prefix = "region"
        operation = "region_decide"
    else:
        actor_prefix = "hq"
        operation = "headquarters_decide"
    rejected_qty = str(float(input_qty) - float(approved_qty))
    reason = "" if rejected_qty == "0.0" else "bounded reduction"
    connection.exec_driver_sql(
        "INSERT INTO approval_step_line_decisions "
        "(id, step_id, request_line_id, input_qty, approved_qty, rejected_qty, "
        "reason, decision_source, external_registration_id, decided_by_user_id, "
        "decided_by_person_id, decided_role_assignment_id, authorization_version, "
        "decided_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'internal', NULL, "
        "?, ?, ?, 1, ?, ?)",
        (
            _uuid(command_seed + 1),
            step_id,
            request["line"],
            input_qty,
            approved_qty,
            rejected_qty,
            reason,
            ids[f"{actor_prefix}_user"],
            ids[f"{actor_prefix}_person"],
            ids[f"{actor_prefix}_assignment"],
            NOW,
            NOW,
        ),
    )
    command_id = _uuid(command_seed + 2)
    _insert_command(
        connection,
        command_id=command_id,
        request_id=request["request"],
        operation=operation,
        target_version=command_seed,
        actor_user_id=ids[f"{actor_prefix}_user"],
        actor_person_id=ids[f"{actor_prefix}_person"],
        actor_assignment_id=ids[f"{actor_prefix}_assignment"],
    )
    _insert_action(
        connection,
        action_id=_uuid(command_seed + 3),
        instance_id=graph["instance"],
        step_id=step_id,
        command_id=command_id,
        action="approve" if rejected_qty == "0.0" else "partial_approve",
        actor_user_id=ids[f"{actor_prefix}_user"],
        actor_person_id=ids[f"{actor_prefix}_person"],
        actor_assignment_id=ids[f"{actor_prefix}_assignment"],
    )
    terminal = "approved" if rejected_qty == "0.0" else "partially_approved"
    connection.exec_driver_sql(
        "UPDATE approval_steps SET status=?, decision_manifest_sha256=?, "
        "decided_at=?, updated_at=? WHERE id=?",
        (terminal, HASH_A, NOW, NOW, step_id),
    )
    connection.exec_driver_sql(
        "UPDATE approval_steps SET status=?, opened_at=?, updated_at=? WHERE id=?",
        (next_status, NOW, NOW, next_step_id),
    )
    connection.exec_driver_sql(
        "UPDATE approval_instances SET current_step_no=?, current_step_id=?, "
        "version=version+1, updated_at=? WHERE id=?",
        (step_no + 1, next_step_id, NOW, graph["instance"]),
    )


def _insert_return_target(
    connection: sa.Connection,
    *,
    ids: dict[str, str],
    graph: dict[str, str],
    source_step_no: int,
    target_step_id: str,
    predecessor_step_id: str | None,
) -> None:
    target_no = source_step_no - 1
    old_target_id = graph[f"step{target_no}"]
    source_id = graph[f"step{source_step_no}"]
    actor_user_id = ids["region_user"] if target_no == 1 else ids["hq_user"]
    connection.exec_driver_sql(
        "INSERT INTO approval_steps "
        "(id, instance_id, step_no, attempt_no, predecessor_step_id, "
        "supersedes_step_id, reopened_from_step_id, source_mode, status, "
        "assignee_user_id, assignee_snapshot_jsonb, decision_manifest_sha256, "
        "opened_at, decided_at, version, created_at, updated_at) "
        "VALUES (?, ?, ?, 2, ?, ?, ?, 'internal', 'pending', ?, '{}', NULL, "
        "NULL, NULL, 0, ?, ?)",
        (
            target_step_id,
            graph["instance"],
            target_no,
            predecessor_step_id,
            old_target_id,
            source_id,
            actor_user_id,
            NOW,
            NOW,
        ),
    )


def _insert_return_fact(
    connection: sa.Connection,
    *,
    fact_id: str,
    action_id: str,
    ids: dict[str, str],
    request: dict[str, str],
    graph: dict[str, str],
    source_step_id: str,
    target_kind: str,
    target_step_id: str | None,
    returned_input: str,
    target_max: str,
    required_review: str,
    actor_prefix: str,
    occurred_at: str = NOW,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO approval_return_line_facts "
        "(id, return_action_id, instance_id, returned_from_step_id, target_kind, "
        "target_step_id, request_id, request_revision_id, request_line_id, "
        "returned_step_input_qty, target_step_max_qty, required_review_qty, "
        "reason, actor_user_id, actor_person_id, actor_role_assignment_id, "
        "authorization_version, occurred_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '补充逐行依据', ?, ?, ?, 1, ?, ?)",
        (
            fact_id,
            action_id,
            graph["instance"],
            source_step_id,
            target_kind,
            target_step_id,
            request["request"],
            request["revision"],
            request["line"],
            returned_input,
            target_max,
            required_review,
            ids[f"{actor_prefix}_user"],
            ids[f"{actor_prefix}_person"],
            ids[f"{actor_prefix}_assignment"],
            occurred_at,
            occurred_at,
        ),
    )


def test_0030_orm_exposes_exact_current_and_return_causality() -> None:
    assert "approval_return_line_facts" in Base.metadata.tables
    instance = Base.metadata.tables["approval_instances"]
    step = Base.metadata.tables["approval_steps"]
    fact = Base.metadata.tables["approval_return_line_facts"]
    assert "current_step_id" in instance.c
    assert {"supersedes_step_id", "reopened_from_step_id"} <= set(step.c.keys())
    assert {
        "returned_step_input_qty",
        "target_step_max_qty",
        "required_review_qty",
        "target_kind",
        "target_step_id",
    } <= set(fact.c.keys())
    assert "fk_approval_instances_current_step_0030" in {
        constraint.name for constraint in instance.constraints
    }
    assert "uq_approval_steps_one_current_0030" in {
        index.name for index in step.indexes
    }


def test_0030_empty_upgrade_and_postgresql_ddl_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'causality-empty.db'}"
    config = _config(url)
    command.upgrade(config, REVISION_0030)
    engine = sa.create_engine(url)
    try:
        inspector = inspect(engine)
        assert "approval_return_line_facts" in inspector.get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == REVISION_0030
        command.downgrade(config, REVISION_0029)
        assert "approval_return_line_facts" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    output = io.StringIO()
    pg_config = _config(
        "postgresql+psycopg://migration:local-only@localhost/rsc", output_buffer=output
    )
    command.upgrade(pg_config, f"{REVISION_0029}:{REVISION_0030}", sql=True)
    sql = output.getvalue()
    assert "current_step_id" in sql
    assert "approval_return_line_facts" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "CREATE CONSTRAINT TRIGGER" in sql
    assert "REVOKE EXECUTE" in sql
    assert "GRANT " not in sql.upper()


def test_0030_backfills_unique_0029_current_step_and_normalizes_step_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, _, _, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-backfill.db", seed=41000
    )
    try:
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT current_step_no, current_step_id FROM approval_instances "
                "WHERE id=?",
                (graph["instance"],),
            ).one()
            assert row == (1, graph["step1"])
            assert connection.exec_driver_sql(
                "SELECT step_no, attempt_no FROM approval_steps "
                "WHERE instance_id=? ORDER BY step_no",
                (graph["instance"],),
            ).all() == [(1, 1), (2, 1), (3, 1)]
    finally:
        engine.dispose()


def test_0030_upgrade_rejects_ambiguous_0029_current_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'causality-ambiguous.db'}"
    config = _config(url)
    command.upgrade(config, REVISION_0029)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 42000)
            request = _insert_request(connection, ids, 42000)
            graph = _insert_approval_graph(connection, ids, request, 42000)
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='open', opened_at=? WHERE id=?",
                (NOW, graph["step2"]),
            )
        with pytest.raises(RuntimeError, match="not uniquely reconstructable"):
            command.upgrade(config, REVISION_0030)
    finally:
        engine.dispose()


def test_0030_upgrade_rejects_0029_instance_with_zero_request_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'causality-zero-lines.db'}"
    config = _config(url)
    command.upgrade(config, REVISION_0029)
    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            ids = _insert_principal_graph(connection, 42500)
            request = _insert_request(connection, ids, 42500)
            _insert_approval_graph(connection, ids, request, 42500)
            # Simulate legacy corruption that predates the 0029 immutable guard.
            connection.exec_driver_sql(
                "DROP TRIGGER trg_material_request_lines_delete_guard_0029"
            )
            connection.exec_driver_sql(
                "DELETE FROM material_request_lines WHERE id=?", (request["line"],)
            )
        with pytest.raises(RuntimeError, match="not uniquely reconstructable"):
            command.upgrade(config, REVISION_0030)
    finally:
        engine.dispose()


def test_0030_active_instance_without_steps_fails_at_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, _, _ = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-no-steps.db", seed=43000
    )
    try:
        with engine.begin() as connection:
            request = _insert_request(connection, ids, 43100)
            connection.exec_driver_sql(
                "UPDATE material_request_revisions SET status='sealed', "
                "content_manifest_sha256=?, sealed_at=?, sealed_by_user_id=?, "
                "updated_at=? WHERE id=?",
                (HASH_A, NOW, ids["requester_user"], NOW, request["revision"]),
            )
            connection.exec_driver_sql(
                "UPDATE material_requests SET status='approval_in_progress', "
                "submitted_at=?, updated_at=? WHERE id=?",
                (NOW, NOW, request["request"]),
            )
        connection = engine.connect()
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(
                "INSERT INTO approval_instances "
                "(id, request_id, request_revision_id, revision_no, route_version_id, "
                "attempt_no, status, current_step_no, current_step_id, version, "
                "completed_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, ?, 1, 'active', 1, ?, 0, NULL, ?, ?)",
                (
                    _uuid(43990),
                    request["request"],
                    request["revision"],
                    ROUTE_VERSION_ID,
                    _uuid(43991),
                    NOW,
                    NOW,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                transaction.commit()
        finally:
            if transaction.is_active:
                transaction.rollback()
            connection.close()
    finally:
        engine.dispose()


def test_0030_blocks_route_attempt_posthoc_candidate_and_decisionless_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, request, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-direct-dml.db", seed=44000
    )
    try:
        with engine.begin() as connection:
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO approval_steps "
                    "(id, instance_id, step_no, attempt_no, predecessor_step_id, "
                    "supersedes_step_id, reopened_from_step_id, source_mode, status, "
                    "assignee_user_id, assignee_snapshot_jsonb, decision_manifest_sha256, "
                    "opened_at, decided_at, version, created_at, updated_at) "
                    "VALUES (?, ?, 1, 2, NULL, ?, NULL, 'external_registration', "
                    "'pending', NULL, '{}', NULL, NULL, NULL, 0, ?, ?)",
                    (_uuid(44900), graph["instance"], graph["step1"], NOW, NOW),
                )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO approval_steps "
                    "(id, instance_id, step_no, attempt_no, predecessor_step_id, "
                    "supersedes_step_id, reopened_from_step_id, source_mode, status, "
                    "assignee_user_id, assignee_snapshot_jsonb, decision_manifest_sha256, "
                    "opened_at, decided_at, version, created_at, updated_at) "
                    "VALUES (?, ?, 1, 3, NULL, ?, NULL, 'internal', 'pending', NULL, "
                    "'{}', NULL, NULL, NULL, 0, ?, ?)",
                    (_uuid(44901), graph["instance"], graph["step1"], NOW, NOW),
                )

            command_id = _uuid(44910)
            _insert_command(
                connection,
                command_id=command_id,
                request_id=request["request"],
                operation="region_decide",
                target_version=44910,
                actor_user_id=ids["region_user"],
                actor_person_id=ids["region_person"],
                actor_assignment_id=ids["region_assignment"],
            )
            _insert_action(
                connection,
                action_id=_uuid(44911),
                instance_id=graph["instance"],
                step_id=graph["step1"],
                command_id=command_id,
                action="approve",
                actor_user_id=ids["region_user"],
                actor_person_id=ids["region_person"],
                actor_assignment_id=ids["region_assignment"],
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE approval_steps SET status='approved', "
                    "decision_manifest_sha256=?, decided_at=?, updated_at=? WHERE id=?",
                    (HASH_A, NOW, NOW, graph["step1"]),
                )

        with engine.begin() as connection:
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=1,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=44920,
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_candidates "
                    "(id, step_id, user_id, person_id, role_assignment_id, "
                    "authorization_version, candidate_kind, snapshot_jsonb, created_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, 'assignee', '{}', ?)",
                    (
                        _uuid(44930),
                        graph["step1"],
                        ids["hq_user"],
                        ids["hq_person"],
                        ids["hq_assignment"],
                        NOW,
                    ),
                )
    finally:
        engine.dispose()


def test_0030_first_step_return_preserves_requester_revision_line_fact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, request, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-return-requester.db", seed=45000
    )
    try:
        with engine.begin() as connection:
            command_id = _uuid(45900)
            action_id = _uuid(45901)
            fact_id = _uuid(45902)
            _insert_command(
                connection,
                command_id=command_id,
                request_id=request["request"],
                operation="region_decide",
                target_version=45900,
                actor_user_id=ids["region_user"],
                actor_person_id=ids["region_person"],
                actor_assignment_id=ids["region_assignment"],
            )
            _insert_action(
                connection,
                action_id=action_id,
                instance_id=graph["instance"],
                step_id=graph["step1"],
                command_id=command_id,
                action="return",
                actor_user_id=ids["region_user"],
                actor_person_id=ids["region_person"],
                actor_assignment_id=ids["region_assignment"],
            )
            _insert_return_fact(
                connection,
                fact_id=fact_id,
                action_id=action_id,
                ids=ids,
                request=request,
                graph=graph,
                source_step_id=graph["step1"],
                target_kind="requester_revision",
                target_step_id=None,
                returned_input="10.000",
                target_max="10.000",
                required_review="10.000",
                actor_prefix="region",
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='returned', decided_at=?, "
                "updated_at=? WHERE id=?",
                (NOW, NOW, graph["step1"]),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET status='returned', current_step_no=NULL, "
                "current_step_id=NULL, completed_at=?, updated_at=? WHERE id=?",
                (NOW, NOW, graph["instance"]),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE approval_return_line_facts SET reason='tampered' WHERE id=?",
                    (fact_id,),
                )
    finally:
        engine.dispose()


def test_0030_second_stage_return_reopens_first_stage_with_contiguous_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, request, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-return-step2.db", seed=46000
    )
    try:
        with engine.begin() as connection:
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=1,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=46900,
            )
            target_id = _uuid(46910)
            _insert_return_target(
                connection,
                ids=ids,
                graph=graph,
                source_step_no=2,
                target_step_id=target_id,
                predecessor_step_id=None,
            )
            command_id = _uuid(46911)
            action_id = _uuid(46912)
            _insert_command(
                connection,
                command_id=command_id,
                request_id=request["request"],
                operation="headquarters_decide",
                target_version=46911,
                actor_user_id=ids["hq_user"],
                actor_person_id=ids["hq_person"],
                actor_assignment_id=ids["hq_assignment"],
            )
            _insert_action(
                connection,
                action_id=action_id,
                instance_id=graph["instance"],
                step_id=graph["step2"],
                command_id=command_id,
                action="return",
                actor_user_id=ids["hq_user"],
                actor_person_id=ids["hq_person"],
                actor_assignment_id=ids["hq_assignment"],
            )
            _insert_return_fact(
                connection,
                fact_id=_uuid(46913),
                action_id=action_id,
                ids=ids,
                request=request,
                graph=graph,
                source_step_id=graph["step2"],
                target_kind="approval_step",
                target_step_id=target_id,
                returned_input="10.000",
                target_max="10.000",
                required_review="10.000",
                actor_prefix="hq",
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='returned', decided_at=?, "
                "updated_at=? WHERE id=?",
                (NOW, NOW, graph["step2"]),
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='open', opened_at=?, updated_at=? WHERE id=?",
                (LATER, LATER, target_id),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET current_step_no=1, current_step_id=?, "
                "version=version+1, updated_at=? WHERE id=?",
                (target_id, LATER, graph["instance"]),
            )
            assert connection.exec_driver_sql(
                "SELECT attempt_no, supersedes_step_id, reopened_from_step_id "
                "FROM approval_steps WHERE id=?",
                (target_id,),
            ).one() == (2, graph["step1"], graph["step2"])

            reopened_candidates = connection.exec_driver_sql(
                "SELECT user_id, person_id, role_assignment_id, authorization_version, "
                "candidate_kind, snapshot_jsonb FROM approval_step_candidates WHERE step_id=?",
                (graph["step1"],),
            ).all()
            for offset, candidate in enumerate(reopened_candidates):
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_candidates "
                    "(id, step_id, user_id, person_id, role_assignment_id, "
                    "authorization_version, candidate_kind, snapshot_jsonb, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(46930 + offset), target_id, *candidate, LATER),
                )
            connection.exec_driver_sql(
                "INSERT INTO approval_step_line_decisions "
                "(id, step_id, request_line_id, input_qty, approved_qty, rejected_qty, "
                "reason, decision_source, external_registration_id, decided_by_user_id, "
                "decided_by_person_id, decided_role_assignment_id, authorization_version, "
                "decided_at, created_at) VALUES (?, ?, ?, 10, 10, 0, '', 'internal', "
                "NULL, ?, ?, ?, 1, ?, ?)",
                (
                    _uuid(46914),
                    target_id,
                    request["line"],
                    ids["region_user"],
                    ids["region_person"],
                    ids["region_assignment"],
                    LATER,
                    LATER,
                ),
            )
            reapprove_command = _uuid(46915)
            _insert_command(
                connection,
                command_id=reapprove_command,
                request_id=request["request"],
                operation="region_decide",
                target_version=46915,
                actor_user_id=ids["region_user"],
                actor_person_id=ids["region_person"],
                actor_assignment_id=ids["region_assignment"],
                occurred_at=LATER,
            )
            _insert_action(
                connection,
                action_id=_uuid(46916),
                instance_id=graph["instance"],
                step_id=target_id,
                command_id=reapprove_command,
                action="approve",
                actor_user_id=ids["region_user"],
                actor_person_id=ids["region_person"],
                actor_assignment_id=ids["region_assignment"],
                occurred_at=LATER,
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='approved', "
                "decision_manifest_sha256=?, decided_at=?, updated_at=? WHERE id=?",
                (HASH_A, LATER, LATER, target_id),
            )
            step2_attempt2 = _uuid(46917)
            connection.exec_driver_sql(
                "INSERT INTO approval_steps "
                "(id, instance_id, step_no, attempt_no, predecessor_step_id, "
                "supersedes_step_id, reopened_from_step_id, source_mode, status, "
                "assignee_user_id, assignee_snapshot_jsonb, decision_manifest_sha256, "
                "opened_at, decided_at, version, created_at, updated_at) "
                "SELECT ?, instance_id, 2, 2, ?, id, NULL, source_mode, 'pending', "
                "assignee_user_id, assignee_snapshot_jsonb, NULL, NULL, NULL, 0, ?, ? "
                "FROM approval_steps WHERE id=?",
                (step2_attempt2, target_id, LATER, LATER, graph["step2"]),
            )
            candidates = connection.exec_driver_sql(
                "SELECT user_id, person_id, role_assignment_id, authorization_version, "
                "candidate_kind, snapshot_jsonb FROM approval_step_candidates WHERE step_id=?",
                (graph["step2"],),
            ).all()
            for offset, candidate in enumerate(candidates):
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_candidates "
                    "(id, step_id, user_id, person_id, role_assignment_id, "
                    "authorization_version, candidate_kind, snapshot_jsonb, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(46918 + offset), step2_attempt2, *candidate, LATER),
                )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='open', opened_at=?, updated_at=? WHERE id=?",
                (LATER, LATER, step2_attempt2),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET current_step_no=2, current_step_id=?, "
                "version=version+1, updated_at=? WHERE id=?",
                (step2_attempt2, LATER, graph["instance"]),
            )
            assert connection.exec_driver_sql(
                "SELECT attempt_no, predecessor_step_id, supersedes_step_id, "
                "reopened_from_step_id, status FROM approval_steps WHERE id=?",
                (step2_attempt2,),
            ).one() == (2, target_id, graph["step2"], None, "open")
    finally:
        engine.dispose()


def test_0030_third_stage_external_return_reopens_second_stage_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, request, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-return-step3.db", seed=47000
    )
    try:
        with engine.begin() as connection:
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=1,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=47900,
            )
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=2,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=47910,
                next_status="awaiting_external_evidence",
            )
            target_id = _uuid(47920)
            _insert_return_target(
                connection,
                ids=ids,
                graph=graph,
                source_step_no=3,
                target_step_id=target_id,
                predecessor_step_id=graph["step1"],
            )
            registration_id = _uuid(47921)
            connection.exec_driver_sql(
                "INSERT INTO approval_external_registrations "
                "(id, step_id, registration_no, external_action, status, evidence_file_id, "
                "external_approver_snapshot_jsonb, external_decided_at, "
                "decision_manifest_sha256, registered_by_user_id, registered_by_person_id, "
                "registered_role_assignment_id, authorization_version, registered_at, "
                "verified_by_user_id, verified_by_person_id, verified_role_assignment_id, "
                "verified_authorization_version, verification_comment, verified_at, "
                "version, created_at, updated_at) VALUES (?, ?, ?, 'return', "
                "'pending_verification', ?, '{}', ?, ?, ?, ?, ?, 1, ?, NULL, NULL, "
                "NULL, NULL, '', NULL, 0, ?, ?)",
                (
                    registration_id,
                    graph["step3"],
                    "REG-47921",
                    ids["evidence_file"],
                    NOW,
                    HASH_A,
                    ids["registrar_user"],
                    ids["registrar_person"],
                    ids["registrar_assignment"],
                    NOW,
                    NOW,
                    NOW,
                ),
            )
            connection.exec_driver_sql(
                "UPDATE approval_external_registrations SET status='accepted', "
                "verified_by_user_id=?, verified_by_person_id=?, "
                "verified_role_assignment_id=?, verified_authorization_version=1, "
                "verification_comment='复核通过', verified_at=?, version=1, updated_at=? "
                "WHERE id=?",
                (
                    ids["verifier_user"],
                    ids["verifier_person"],
                    ids["verifier_assignment"],
                    LATER,
                    LATER,
                    registration_id,
                ),
            )
            command_id = _uuid(47922)
            action_id = _uuid(47923)
            _insert_command(
                connection,
                command_id=command_id,
                request_id=request["request"],
                operation="verify_external",
                target_version=47922,
                actor_user_id=ids["verifier_user"],
                actor_person_id=ids["verifier_person"],
                actor_assignment_id=ids["verifier_assignment"],
                occurred_at=LATER,
            )
            _insert_action(
                connection,
                action_id=action_id,
                instance_id=graph["instance"],
                step_id=graph["step3"],
                command_id=command_id,
                action="verify_external_accept",
                actor_user_id=ids["verifier_user"],
                actor_person_id=ids["verifier_person"],
                actor_assignment_id=ids["verifier_assignment"],
                source_mode="external_registration",
                occurred_at=LATER,
            )
            _insert_return_fact(
                connection,
                fact_id=_uuid(47924),
                action_id=action_id,
                ids=ids,
                request=request,
                graph=graph,
                source_step_id=graph["step3"],
                target_kind="approval_step",
                target_step_id=target_id,
                returned_input="10.000",
                target_max="10.000",
                required_review="10.000",
                actor_prefix="verifier",
                occurred_at=LATER,
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='returned', decided_at=?, updated_at=? "
                "WHERE id=?",
                (LATER, LATER, graph["step3"]),
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='open', opened_at=?, updated_at=? WHERE id=?",
                (LATER, LATER, target_id),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET current_step_no=2, current_step_id=?, "
                "version=version+1, updated_at=? WHERE id=?",
                (target_id, LATER, graph["instance"]),
            )
            assert connection.exec_driver_sql(
                "SELECT target_step_max_qty, required_review_qty "
                "FROM approval_return_line_facts WHERE returned_from_step_id=?",
                (graph["step3"],),
            ).one() == (10, 10)

            reopened_candidates = connection.exec_driver_sql(
                "SELECT user_id, person_id, role_assignment_id, authorization_version, "
                "candidate_kind, snapshot_jsonb FROM approval_step_candidates WHERE step_id=?",
                (graph["step2"],),
            ).all()
            for offset, candidate in enumerate(reopened_candidates):
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_candidates "
                    "(id, step_id, user_id, person_id, role_assignment_id, "
                    "authorization_version, candidate_kind, snapshot_jsonb, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(47940 + offset), target_id, *candidate, LATER),
                )
            connection.exec_driver_sql(
                "INSERT INTO approval_step_line_decisions "
                "(id, step_id, request_line_id, input_qty, approved_qty, rejected_qty, "
                "reason, decision_source, external_registration_id, decided_by_user_id, "
                "decided_by_person_id, decided_role_assignment_id, authorization_version, "
                "decided_at, created_at) VALUES (?, ?, ?, 10, 10, 0, '', 'internal', "
                "NULL, ?, ?, ?, 1, ?, ?)",
                (
                    _uuid(47925),
                    target_id,
                    request["line"],
                    ids["hq_user"],
                    ids["hq_person"],
                    ids["hq_assignment"],
                    LATER,
                    LATER,
                ),
            )
            reapprove_command = _uuid(47926)
            _insert_command(
                connection,
                command_id=reapprove_command,
                request_id=request["request"],
                operation="headquarters_decide",
                target_version=47926,
                actor_user_id=ids["hq_user"],
                actor_person_id=ids["hq_person"],
                actor_assignment_id=ids["hq_assignment"],
                occurred_at=LATER,
            )
            _insert_action(
                connection,
                action_id=_uuid(47927),
                instance_id=graph["instance"],
                step_id=target_id,
                command_id=reapprove_command,
                action="approve",
                actor_user_id=ids["hq_user"],
                actor_person_id=ids["hq_person"],
                actor_assignment_id=ids["hq_assignment"],
                occurred_at=LATER,
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='approved', "
                "decision_manifest_sha256=?, decided_at=?, updated_at=? WHERE id=?",
                (HASH_A, LATER, LATER, target_id),
            )
            step3_attempt2 = _uuid(47928)
            connection.exec_driver_sql(
                "INSERT INTO approval_steps "
                "(id, instance_id, step_no, attempt_no, predecessor_step_id, "
                "supersedes_step_id, reopened_from_step_id, source_mode, status, "
                "assignee_user_id, assignee_snapshot_jsonb, decision_manifest_sha256, "
                "opened_at, decided_at, version, created_at, updated_at) "
                "SELECT ?, instance_id, 3, 2, ?, id, NULL, source_mode, 'pending', "
                "assignee_user_id, assignee_snapshot_jsonb, NULL, NULL, NULL, 0, ?, ? "
                "FROM approval_steps WHERE id=?",
                (step3_attempt2, target_id, LATER, LATER, graph["step3"]),
            )
            candidates = connection.exec_driver_sql(
                "SELECT user_id, person_id, role_assignment_id, authorization_version, "
                "candidate_kind, snapshot_jsonb FROM approval_step_candidates WHERE step_id=?",
                (graph["step3"],),
            ).all()
            for offset, candidate in enumerate(candidates):
                connection.exec_driver_sql(
                    "INSERT INTO approval_step_candidates "
                    "(id, step_id, user_id, person_id, role_assignment_id, "
                    "authorization_version, candidate_kind, snapshot_jsonb, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid(47929 + offset), step3_attempt2, *candidate, LATER),
                )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='awaiting_external_evidence', "
                "opened_at=?, updated_at=? WHERE id=?",
                (LATER, LATER, step3_attempt2),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET current_step_no=3, current_step_id=?, "
                "version=version+1, updated_at=? WHERE id=?",
                (step3_attempt2, LATER, graph["instance"]),
            )
            assert connection.exec_driver_sql(
                "SELECT attempt_no, predecessor_step_id, supersedes_step_id, "
                "reopened_from_step_id, status FROM approval_steps WHERE id=?",
                (step3_attempt2,),
            ).one() == (
                2,
                target_id,
                graph["step3"],
                None,
                "awaiting_external_evidence",
            )
    finally:
        engine.dispose()


def test_0030_external_return_without_accepted_evidence_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, request, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-return-no-evidence.db", seed=48000
    )
    try:
        connection = engine.connect()
        transaction = connection.begin()
        try:
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=1,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=48900,
            )
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=2,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=48910,
                next_status="awaiting_external_evidence",
            )
            target_id = _uuid(48920)
            _insert_return_target(
                connection,
                ids=ids,
                graph=graph,
                source_step_no=3,
                target_step_id=target_id,
                predecessor_step_id=graph["step1"],
            )
            command_id = _uuid(48921)
            action_id = _uuid(48922)
            _insert_command(
                connection,
                command_id=command_id,
                request_id=request["request"],
                operation="verify_external",
                target_version=48921,
                actor_user_id=ids["verifier_user"],
                actor_person_id=ids["verifier_person"],
                actor_assignment_id=ids["verifier_assignment"],
                occurred_at=LATER,
            )
            _insert_action(
                connection,
                action_id=action_id,
                instance_id=graph["instance"],
                step_id=graph["step3"],
                command_id=command_id,
                action="verify_external_accept",
                actor_user_id=ids["verifier_user"],
                actor_person_id=ids["verifier_person"],
                actor_assignment_id=ids["verifier_assignment"],
                source_mode="external_registration",
                occurred_at=LATER,
            )
            _insert_return_fact(
                connection,
                fact_id=_uuid(48923),
                action_id=action_id,
                ids=ids,
                request=request,
                graph=graph,
                source_step_id=graph["step3"],
                target_kind="approval_step",
                target_step_id=target_id,
                returned_input="10.000",
                target_max="10.000",
                required_review="10.000",
                actor_prefix="verifier",
                occurred_at=LATER,
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE approval_steps SET status='returned', decided_at=?, "
                    "updated_at=? WHERE id=?",
                    (LATER, LATER, graph["step3"]),
                )
        finally:
            transaction.rollback()
            connection.close()
    finally:
        engine.dispose()


def test_0030_external_whole_reject_requires_complete_zero_quantity_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _, ids, request, graph = _legacy_graph_then_0030(
        tmp_path, monkeypatch, name="causality-external-reject.db", seed=49000
    )
    try:
        with engine.begin() as connection:
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=1,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=49900,
            )
            _approve_and_open_next(
                connection,
                ids=ids,
                request=request,
                graph=graph,
                step_no=2,
                input_qty="10.000",
                approved_qty="10.000",
                command_seed=49910,
                next_status="awaiting_external_evidence",
            )
            registration_id = _uuid(49920)
            connection.exec_driver_sql(
                "INSERT INTO approval_external_registrations "
                "(id, step_id, registration_no, external_action, status, evidence_file_id, "
                "external_approver_snapshot_jsonb, external_decided_at, "
                "decision_manifest_sha256, registered_by_user_id, registered_by_person_id, "
                "registered_role_assignment_id, authorization_version, registered_at, "
                "verified_by_user_id, verified_by_person_id, verified_role_assignment_id, "
                "verified_authorization_version, verification_comment, verified_at, "
                "version, created_at, updated_at) VALUES (?, ?, ?, 'reject', "
                "'pending_verification', ?, '{}', ?, ?, ?, ?, ?, 1, ?, NULL, NULL, "
                "NULL, NULL, '', NULL, 0, ?, ?)",
                (
                    registration_id,
                    graph["step3"],
                    "REG-49920",
                    ids["evidence_file"],
                    NOW,
                    HASH_A,
                    ids["registrar_user"],
                    ids["registrar_person"],
                    ids["registrar_assignment"],
                    NOW,
                    NOW,
                    NOW,
                ),
            )
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "UPDATE approval_external_registrations SET status='accepted', "
                    "verified_by_user_id=?, verified_by_person_id=?, "
                    "verified_role_assignment_id=?, verified_authorization_version=1, "
                    "verification_comment='复核通过', verified_at=?, version=1, updated_at=? "
                    "WHERE id=?",
                    (
                        ids["verifier_user"],
                        ids["verifier_person"],
                        ids["verifier_assignment"],
                        LATER,
                        LATER,
                        registration_id,
                    ),
                )
            connection.exec_driver_sql(
                "INSERT INTO approval_external_registration_lines "
                "(id, registration_id, request_line_id, input_qty, approved_qty, "
                "rejected_qty, reason, created_at) VALUES (?, ?, ?, 10, 0, 10, ?, ?)",
                (_uuid(49921), registration_id, request["line"], "整单驳回", NOW),
            )
            connection.exec_driver_sql(
                "UPDATE approval_external_registrations SET status='accepted', "
                "verified_by_user_id=?, verified_by_person_id=?, "
                "verified_role_assignment_id=?, verified_authorization_version=1, "
                "verification_comment='复核通过', verified_at=?, version=1, updated_at=? "
                "WHERE id=?",
                (
                    ids["verifier_user"],
                    ids["verifier_person"],
                    ids["verifier_assignment"],
                    LATER,
                    LATER,
                    registration_id,
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO approval_step_line_decisions "
                "(id, step_id, request_line_id, input_qty, approved_qty, rejected_qty, "
                "reason, decision_source, external_registration_id, decided_by_user_id, "
                "decided_by_person_id, decided_role_assignment_id, authorization_version, "
                "decided_at, created_at) VALUES (?, ?, ?, 10, 0, 10, ?, "
                "'external_registration', ?, ?, ?, ?, 1, ?, ?)",
                (
                    _uuid(49922),
                    graph["step3"],
                    request["line"],
                    "整单驳回",
                    registration_id,
                    ids["verifier_user"],
                    ids["verifier_person"],
                    ids["verifier_assignment"],
                    LATER,
                    LATER,
                ),
            )
            command_id = _uuid(49923)
            _insert_command(
                connection,
                command_id=command_id,
                request_id=request["request"],
                operation="verify_external",
                target_version=49923,
                actor_user_id=ids["verifier_user"],
                actor_person_id=ids["verifier_person"],
                actor_assignment_id=ids["verifier_assignment"],
                occurred_at=LATER,
            )
            _insert_action(
                connection,
                action_id=_uuid(49924),
                instance_id=graph["instance"],
                step_id=graph["step3"],
                command_id=command_id,
                action="verify_external_accept",
                actor_user_id=ids["verifier_user"],
                actor_person_id=ids["verifier_person"],
                actor_assignment_id=ids["verifier_assignment"],
                source_mode="external_registration",
                occurred_at=LATER,
            )
            connection.exec_driver_sql(
                "UPDATE approval_steps SET status='rejected', decision_manifest_sha256=?, "
                "decided_at=?, updated_at=? WHERE id=?",
                (HASH_A, LATER, LATER, graph["step3"]),
            )
            connection.exec_driver_sql(
                "UPDATE approval_instances SET status='rejected', current_step_no=NULL, "
                "current_step_id=NULL, completed_at=?, updated_at=? WHERE id=?",
                (LATER, LATER, graph["instance"]),
            )
            assert connection.exec_driver_sql(
                "SELECT approved_qty, rejected_qty, reason "
                "FROM approval_step_line_decisions WHERE step_id=?",
                (graph["step3"],),
            ).one() == (0, 10, "整单驳回")
    finally:
        engine.dispose()
