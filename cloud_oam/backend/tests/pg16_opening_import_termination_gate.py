"""Cancellation and definite failure through the real PG16/API-role boundary."""

from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from formal_file_integrity import FileUploadIntentInput, _prepare_upload
from app.config import get_settings
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, FileJob
from app.opening_count_import_worker import process_one_opening_count_import
from app.formal_services.file_storage import FileStorageError
from app.formal_services.opening_count_import_intake import create_opening_count_import_job
from app.formal_services.opening_count_import_jobs import OpeningCountImportJobError
from app.formal_services.opening_count_import_prevalidation_jobs import claim_opening_count_import
from app.formal_services.opening_count_import_source import AuthorizedOpeningCountSource
from app.formal_services.opening_count_import_termination import (
    cancel_opening_count_import, fail_invalid_opening_import_source, sweep_awaiting_opening_imports,
)
from app.routers import formal_opening_imports as http
from pg16_opening_source_purpose_gate import _file, MIME


def exercise_import_termination(api, *, actor, other_actor_id, source, data, command):
    cases = []

    class Storage:
        provider_code = "aliyun_oss_v2"

        def __init__(self, content=data, *, timeout=False):
            self.content, self.calls, self.timeout = content, [], timeout

        def read_opening_count_source(self, **coordinates):
            self.calls.append("source")
            if self.timeout:
                raise FileStorageError("synthetic unknown source transport")
            return self.content

    def create(source_id=source.id):
        key = uuid4().hex
        with Session(api) as db:
            identifier, _ = create_opening_count_import_job(db, actor=actor,
                source_file_id=source_id, task_id=command.task_id,
                round_id=command.round_id, scope_id=command.scope_id,
                idempotency_key=key, request_id="pg16-termination-create")
            db.commit()
        return identifier, key

    def audit_count(identifier):
        with Session(api) as db:
            return len(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_id == str(identifier),
                AuditEvent.action == "opening_count_import_terminated")).all())

    def cancel(identifier, key):
        with Session(api) as db:
            result = cancel_opening_count_import(db, actor=actor, job_id=identifier,
                idempotency_key=key, request_id="pg16-termination-cancel")
            db.commit()
            return result

    queued, key = create()
    with Session(api) as db:
        other = load_formal_principal(db, other_actor_id)
    for reader, request_key, expected in ((other, key, "opening_import_job_not_found"),
                                         (actor, uuid4().hex, "opening_import_idempotency_conflict")):
        with Session(api) as db:
            try:
                cancel_opening_count_import(db, actor=reader, job_id=queued,
                    idempotency_key=request_key, request_id="pg16-wrong-cancel")
            except OpeningCountImportJobError as error:
                assert error.code == expected
            else:
                raise AssertionError("unrelated cancellation accepted")
        cases.append("cancel-" + expected)
    for label, status, reason, fragment in (
        ("unaudited-cancel", "cancelled", "opening_import_cancelled", "termination requires same transaction audit"),
        ("unaudited-failure", "failed", "opening_import_context_changed", "termination requires same transaction audit"),
        ("arbitrary-failure-text", "failed", "private raw exception", "import transition invalid"),
    ):
        with Session(api) as db:
            job = db.get(FileJob, queued)
            job.status, job.error_detail = status, reason
            job.completed_at = db.scalar(text("SELECT clock_timestamp()"))
            try:
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate == "23514" and fragment in str(error.orig)
                db.rollback()
            else:
                raise AssertionError("unaudited or uncontrolled termination committed")
        cases.append(label + "-refused")
    with Session(api) as db:
        cancel_opening_count_import(db, actor=actor, job_id=queued, idempotency_key=key,
                                   request_id="pg16-cancel-rollback")
        db.rollback()
    assert audit_count(queued) == 0
    with Session(api) as db:
        assert db.get(FileJob, queued).status == "queued"
    assert cancel(queued, key)[0].status == "cancelled"
    assert cancel(queued, key)[1] is True and audit_count(queued) == 1
    idle_storage = Storage()
    assert process_one_opening_count_import(lambda: Session(api), storage=idle_storage,
                                            job_id=queued).status == "cancelled"
    assert not idle_storage.calls
    cases.extend(("cancel-rollback-preserves-original", "cancel-replay-audit-once", "cancelled-worker-no-source-io"))

    for stage in ("prevalidating", "awaiting_confirmation"):
        identifier, request_key = create()
        if stage == "prevalidating":
            with Session(api) as db:
                claim_opening_count_import(db, actor=actor, job_id=identifier, request_id="pg16-cancel-claim")
                db.commit()
        else:
            ready = process_one_opening_count_import(lambda: Session(api), storage=Storage(), job_id=identifier)
            assert ready.status == stage
        result, replayed = cancel(identifier, request_key)
        assert result.status == "cancelled" and result.completion_id is None and not replayed
        assert audit_count(identifier) == 1
        cases.append("cancel-" + stage + "-without-count")

    timeout_id, timeout_key = create()
    timed = Storage(timeout=True)
    try:
        process_one_opening_count_import(lambda: Session(api), storage=timed, job_id=timeout_id)
    except FileStorageError:
        pass
    else:
        raise AssertionError("transport timeout incorrectly reported as a terminal job")
    with Session(api) as db:
        assert db.get(FileJob, timeout_id).status == "prevalidating"
    assert audit_count(timeout_id) == 0 and timed.calls == ["source"]
    cancel(timeout_id, timeout_key)
    cases.append("source-timeout-not-a-definite-failure")

    # Revoke actual authority inside an API-role transaction. All worker
    # sessions join the same root transaction; force deferred guards before
    # rollback, preserving the production audit's exact transaction identity.
    revoked_id, revoked_key = create()
    with api.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id"),
                           {"id": actor.user_id})
        revoked_storage = Storage()
        factory = lambda: Session(connection, join_transaction_mode="rollback_only")
        terminated = process_one_opening_count_import(factory, storage=revoked_storage, job_id=revoked_id)
        assert terminated.status == "failed" and not revoked_storage.calls
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        with factory() as db:
            job = db.get(FileJob, revoked_id)
            assert job.error_detail == "opening_import_context_changed" and job.import_completion_id is None
            event = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id == str(revoked_id),
                AuditEvent.action == "opening_count_import_terminated"))
            assert event.actor_user_id is None
        transaction.rollback()
    cancel(revoked_id, revoked_key)
    cases.append("worker-live-revocation-terminates-with-system-audit")

    awaiting = [create(), create()]
    for identifier, _ in awaiting:
        assert process_one_opening_count_import(lambda: Session(api), storage=Storage(),
                                                job_id=identifier).status == "awaiting_confirmation"
    first_page = sweep_awaiting_opening_imports(lambda: Session(api), limit=1)
    second_page = sweep_awaiting_opening_imports(lambda: Session(api), after_id=first_page.next_after_id, limit=1)
    assert first_page.checked == second_page.checked == 1
    assert {first_page.next_after_id, second_page.next_after_id} == {identifier for identifier, _ in awaiting}
    assert sweep_awaiting_opening_imports(lambda: Session(api), after_id=second_page.next_after_id,
                                        limit=1).next_after_id is None
    with api.connect() as connection:
        transaction = connection.begin()
        connection.execute(text("UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id"),
                           {"id": actor.user_id})
        factory = lambda: Session(connection, join_transaction_mode="rollback_only")
        assert sweep_awaiting_opening_imports(factory).checked == 2
        connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
        with factory() as db:
            assert all(db.get(FileJob, identifier).status == "failed" for identifier, _ in awaiting)
        transaction.rollback()
    for identifier, original_key in awaiting:
        cancel(identifier, original_key)
    cases.extend(("awaiting-context-sweep-rotates-without-starvation", "awaiting-revocation-sweep-ends-stale-jobs"))

    invalid = b"this is not an XLSX container"
    prepared = _prepare_upload(FileUploadIntentInput(purpose="opening_count_import",
        original_filename="invalid.xlsx", size_bytes=len(invalid), mime_type=MIME,
        sha256=sha256(invalid).hexdigest()), maximum_size_bytes=8 * 1024 * 1024)
    with Session(api, expire_on_commit=False) as db:
        bad_source = _file(user_id=actor.user_id, person_id=actor.person_id,
                           size_bytes=len(invalid), prepared=prepared)
        bad_source.sha256 = sha256(invalid).hexdigest()
        bad_source.original_filename = "invalid.xlsx"
        bad_source.created_at = db.scalar(text("SELECT transaction_timestamp()"))
        db.add(bad_source)
        db.flush()
        bad_source.status = "available"
        bad_source.metadata_jsonb = {**bad_source.metadata_jsonb, "completion": {
            "etag_sha256": "a" * 64, "head_manifest_sha256": "b" * 64,
            "verified_at": bad_source.created_at.isoformat()}}
        db.commit()
    invalid_id, _ = create(bad_source.id)
    bad_storage = Storage(invalid)
    failed = process_one_opening_count_import(lambda: Session(api), storage=bad_storage, job_id=invalid_id)
    assert failed.status == "failed" and audit_count(invalid_id) == 1
    assert process_one_opening_count_import(lambda: Session(api), storage=bad_storage,
                                            job_id=invalid_id).status == "failed"
    assert bad_storage.calls == ["source"]
    with Session(api) as db:
        job = db.get(FileJob, invalid_id)
        assert job.error_detail == "opening_import_source_invalid" and job.import_completion_id is None
        assert job.error_file_id is None and job.import_error_sha256 is None
    cases.append("invalid-verified-xlsx-terminates-once-without-count")

    good_id, good_key = create()
    with Session(api) as db:
        claim_opening_count_import(db, actor=actor, job_id=good_id, request_id="pg16-good-source-claim")
        db.commit()
    with Session(api) as db:
        try:
            fail_invalid_opening_import_source(db, job_id=good_id,
                source=AuthorizedOpeningCountSource(source.id, source.sha256, len(data), data),
                request_id="pg16-good-source-false-failure")
        except OpeningCountImportJobError as error:
            assert error.code == "opening_import_source_failure_unproved"
        else:
            raise AssertionError("valid source marked as a definite format failure")
    cancel(good_id, good_key)
    cases.append("definite-source-failure-must-be-reproduced")

    http_id, http_key = create()
    app = FastAPI()
    app.include_router(http.router, prefix="/api")
    app.dependency_overrides[get_formal_principal] = lambda: actor
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        opening_count_import_enabled=True, database_url="postgresql+psycopg://synthetic",
        file_storage_configuration_ready=lambda: True)
    app.dependency_overrides[http.get_formal_file_storage_adapter] = Storage

    class LostAcknowledgement(Session):
        def commit(self):
            super().commit()
            raise RuntimeError("synthetic cancelled COMMIT acknowledgement loss")

    app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: LostAcknowledgement(api)
    root = "/api/v1/stocktakes/opening/imports/opening-count/jobs"
    headers = {"Idempotency-Key": http_key, "X-Request-ID": "pg16-cancel-http"}
    with TestClient(app) as client:
        response = client.post(root + f"/{http_id}/cancel", json={}, headers=headers)
        assert response.status_code == 503 and "acknowledgement" not in response.text
        app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: Session(api)
        recovered = client.get(root + "/recovery", headers=headers)
        assert recovered.status_code == 200 and recovered.json()["status"] == "cancelled"
        assert recovered.json()["failure_code"] == "opening_import_cancelled"
        repeated = client.post(root + f"/{http_id}/cancel", json={}, headers=headers)
        assert repeated.status_code == 200 and repeated.json()["replayed"]
        refused = client.post(root + f"/{http_id}/confirm", json={}, headers=headers)
        assert refused.status_code == 409
    assert audit_count(http_id) == 1
    cases.append("http-cancel-unknown-commit-recovery-no-replay")
    return cases
