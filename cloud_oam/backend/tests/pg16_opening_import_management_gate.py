"""Management inspection must not inherit the original requester's authority."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text, event
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import FileJob
from app.formal_services.opening_count_import_document import load_import_binding
from app.formal_services.opening_count_import_management import _terminal_audit
from app.formal_services.opening_count_import_termination import terminate_ineligible_opening_import
from app.routers import formal_opening_imports as http


def exercise_import_management(api, *, policy_engine, actor, manager_id, command, source, raw_key, job_id, succeeded=False):
    cases = []
    with Session(api) as db:
        reviewer = load_formal_principal(db, manager_id)
    current = {"actor": reviewer, "factory": lambda: Session(api)}
    app = FastAPI()
    app.include_router(http.router, prefix="/api")
    app.dependency_overrides[get_formal_principal] = lambda: current["actor"]
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(opening_count_import_enabled=True,
        database_url="postgresql+psycopg://synthetic", file_storage_configuration_ready=lambda: True)
    # No storage methods exist: management may not read, sign or upload objects.
    app.dependency_overrides[http.get_formal_file_storage_adapter] = lambda: SimpleNamespace(provider_code="aliyun_oss_v2")
    app.dependency_overrides[http.get_import_session_factory] = lambda: current["factory"]
    client = TestClient(app)
    base = "/api/v1/stocktakes/opening/imports/opening-count/jobs"
    params = dict(task_id=str(command.task_id), round_id=str(command.round_id),
                  scope_id=str(command.scope_id), source_file_id=str(source.id))
    headers = {"Idempotency-Key": raw_key}

    def read(**changes):
        statements = []
        def capture(_conn, _cursor, statement, *_):
            statements.append(statement.lstrip().split(None, 1)[0].upper())
        event.listen(api, "before_cursor_execute", capture)
        try:
            result = client.get(base + "/management-recovery", params={**params, **changes}, headers=headers)
        finally:
            event.remove(api, "before_cursor_execute", capture)
        assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "MERGE", "COMMIT"}, statements
        return result

    response = read()
    assert response.status_code == 200, response.text
    proof = response.json()
    assert proof["job_id"] == str(job_id) and not proof["automatic_retry_allowed"]
    assert proof["reviewer_person_id"] == str(reviewer.person_id)
    assert proof["actor_person_id"] == str(actor.person_id)
    assert proof["authorization_version"] == actor.authorization_version
    assert proof["source_sha256"] == source.sha256 and proof["size_bytes"] == source.size_bytes
    assert all(proof[k] == v for k, v in params.items())
    assert "no-store" in response.headers["cache-control"]
    assert not set(proof) & {"original_filename", "storage_key", "download", "preview", "row_count", "error_count"}
    if succeeded:
        assert proof["status"] == "succeeded" and proof["terminal_verified"]
        assert proof["completion_id"] and proof["terminal_audit_id"]
        return ["management-success-verifies-count-link-and-audit-without-write"]
    assert proof["status"] == "awaiting_confirmation" and not proof["terminal_verified"]
    assert proof["terminal_audit_id"] is None and proof["completion_id"] is None
    cases.append("management-active-does-not-grant-retry-or-terminal-proof")

    current["actor"] = actor
    assert read().status_code == 200  # Current assigned regional manager.
    current["actor"] = replace(reviewer, authorization_version=reviewer.authorization_version + 1)
    assert read().status_code == 412
    current["actor"] = reviewer
    for field in ("task_id", "round_id", "scope_id", "source_file_id"):
        rejected = read(**{field: str(uuid4())})
        assert rejected.status_code in (404, 412) and "source_sha256" not in rejected.text
    missing = client.get(base + "/management-recovery", params=params, headers={"Idempotency-Key": uuid4().hex})
    assert missing.status_code == 404 and "terminal_verified" not in missing.text
    cases.extend(("management-current-regional-reader-and-stale-reviewer-boundaries",
                  "management-wrong-coordinates-or-absent-key-never-authorize-clear"))

    # A real authority-version change leaves historical source metadata intact.
    # The normal endpoint must still reject it, while management may inspect it.
    with api.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id"), {"id": actor.user_id})
        current["factory"] = lambda: Session(connection, join_transaction_mode="rollback_only")
        with current["factory"]() as db:
            changed_owner = load_formal_principal(db, actor.user_id)
        current["actor"] = changed_owner
        ordinary = client.get(base + "/recovery", headers=headers)
        assert ordinary.status_code == 412
        current["actor"] = reviewer
        assert read().json()["terminal_verified"] is False
        with current["factory"]() as db:
            stopped = terminate_ineligible_opening_import(db, job_id=job_id, request_id="management-test-worker-invalidated")
            assert stopped.status == "failed"
            db.commit()
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        ended = read()
        assert ended.status_code == 200, ended.text
        result = ended.json()
        assert result["status"] == "failed" and result["terminal_verified"] and result["terminal_audit_id"]
        assert result["authorization_version"] == actor.authorization_version and result["completion_id"] is None
        current["actor"] = changed_owner
        assert read().json()["terminal_verified"] is True
        transaction.rollback()
    current["factory"] = lambda: Session(api)
    current["actor"] = reviewer
    assert read().json()["terminal_verified"] is False
    cases.append("management-revoked-original-inspection-never-restores-count-or-download-authority")

    # Actual persisted grant changes, not a forged principal in a request body.
    for kind in ("outside-region", "technician"):
        with api.connect() as connection:
            transaction = connection.begin()
            if kind == "outside-region":
                target = connection.scalar(text(
                    "SELECT id::text FROM organizations WHERE org_type='region_company' AND status='active' AND id<>(SELECT region_org_id FROM stocktake_tasks WHERE id=:task) ORDER BY id LIMIT 1"), {"task": str(command.task_id)})
                assert target is not None
                connection.execute(text("UPDATE role_assignments SET scope_id=:scope WHERE user_id=:user"),
                                   {"scope": target, "user": actor.user_id})
            else:
                connection.execute(text("UPDATE role_assignments SET role_id=(SELECT id FROM roles WHERE code='technician'), scope_type='person', scope_id=:person WHERE user_id=:user"),
                                   {"person": str(actor.person_id), "user": actor.user_id})
            current["factory"] = lambda: Session(connection, join_transaction_mode="rollback_only")
            with current["factory"]() as db:
                current["actor"] = load_formal_principal(db, actor.user_id)
            denied = read()
            assert denied.status_code == 403 and "source_sha256" not in denied.text, denied.text
            transaction.rollback()
        current["factory"] = lambda: Session(api)
        current["actor"] = reviewer
        cases.append("management-actual-" + kind + "-grant-refused")

    # Permission policy is deliberately NOT writable by the API login. The
    # isolated fixture's migrator sets a real policy, and API sessions inspect
    # it after commit. Always restore each original row before continuing.
    with policy_engine.begin() as connection:
        original_policy = connection.execute(text("""SELECT id,effect FROM role_permissions
            WHERE role_id IN (SELECT role_id FROM role_assignments WHERE user_id=:reader)
              AND permission_id IN (SELECT id FROM permissions WHERE resource='stocktake' AND action='read')
            FOR UPDATE"""), {"reader": manager_id}).all()
        assert original_policy
        for identifier, _effect in original_policy:
            connection.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"), {"id": identifier})
    try:
        with Session(api) as db:
            current["actor"] = load_formal_principal(db, manager_id)
            assert current["actor"].allows(db, "stocktake", "manage")
            assert not current["actor"].allows(db, "stocktake", "read")
        denied = read()
        assert denied.status_code == 403 and "source_sha256" not in denied.text, denied.text
    finally:
        with policy_engine.begin() as connection:
            for identifier, effect in original_policy:
                connection.execute(text("UPDATE role_permissions SET effect=:effect WHERE id=:id"),
                                   {"id": identifier, "effect": effect})
        with Session(api) as db:
            current["actor"] = load_formal_principal(db, manager_id)
    cases.append("management-manage-permission-cannot-bypass-explicit-read-deny")

    with Session(api) as db:
        for row in db.scalars(select(FileJob).where(FileJob.job_type == "import", FileJob.status.in_(("cancelled", "failed")))):
            assert _terminal_audit(db, row, load_import_binding(row.import_binding_jsonb)) is not None
    cases.append("management-cancellation-and-error-publication-audits-verified")
    return cases
