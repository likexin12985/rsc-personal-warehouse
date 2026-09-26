"""Exercise persistent import state using the actual API role and count graph.

The source object is synthetic; this proves database lifecycle/atomicity, not
the future OSS/worker/HTTP integration or a production import acceptance.
"""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from queue import Queue
import time
from uuid import UUID, uuid4
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from openpyxl import Workbook
from formal_file_integrity import FileUploadIntentInput, _prepare_upload

from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, FileJob
from app.stocktake_models import FormalStocktakeTask
from app.formal_services import opening_stocktake_count as count
from app.formal_services.opening_count_import_document import OpeningCountImportBinding, dump_import_preview
from app.formal_services.opening_count_import_prevalidation import OpeningCountImportBusinessPreview
from app.formal_services.opening_count_import_source import AuthorizedOpeningCountSource
from app.formal_services.opening_count_import_workbook import (
    FIELDS, HEADERS, SHEET_NAME, prevalidate_opening_count_workbook,
)
from app.formal_services.opening_count_import_jobs import (
    OpeningCountImportJobError, confirm_persisted_opening_count_import,
    read_opening_count_import_job,
)
from pg16_opening_source_purpose_gate import _file
from app.formal_services.opening_count_import_intake import (
    create_opening_count_import_job, opening_import_request_key, recover_opening_count_import_job,
)
from app.formal_services.opening_count_import_prevalidation_jobs import claim_opening_count_import
from app.opening_count_import_worker import process_one_opening_count_import


def exercise_import_job(api, *, policy_engine, actor_id, command, preview, key, snapshot, other_actor_id, owned_process_checks=False):
    cases = []
    raw_key = key
    key = opening_import_request_key(actor_id, raw_key)
    parameters = {"import": "opening_count", "template_version": 1}
    workbook = Workbook()
    workbook.active.title = SHEET_NAME
    workbook.active.append(HEADERS)
    for observation in command.physical_observations:
        workbook.active.append(tuple(str(getattr(observation, field)) if field == "counted_qty"
                                     else getattr(observation, field) for field in FIELDS))
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    data = stream.getvalue()
    parsed = prevalidate_opening_count_workbook(data)
    assert parsed.ready
    prepared = _prepare_upload(FileUploadIntentInput(purpose="opening_count_import",
        original_filename="期初盘点.xlsx", size_bytes=len(data),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256=parsed.source_sha256), maximum_size_bytes=8 * 1024 * 1024)
    with Session(api, expire_on_commit=False) as db:
        actor = load_formal_principal(db, actor_id)
        source = _file(user_id=actor_id, person_id=actor.person_id,
                       size_bytes=len(data), prepared=prepared)
        source.sha256 = parsed.source_sha256
        source.created_at = source.updated_at = db.scalar(text("SELECT transaction_timestamp()"))
        db.add(source)
        db.commit()
        source.metadata_jsonb = {**source.metadata_jsonb, "completion": {
            "etag_sha256": "a" * 64, "head_manifest_sha256": "b" * 64,
            "verified_at": db.scalar(text("SELECT transaction_timestamp()")).isoformat(),
        }}
        source.status = "available"
        db.commit()
        preview = count.prevalidate_opening_stocktake_scope_count(db, actor=actor,
            command=command, idempotency_key=key, request_id="pg16-import-intake-preview")
        db.commit()
        binding = OpeningCountImportBinding(
            source_file_id=source.id, source_sha256=source.sha256,
            task_id=command.task_id, round_id=command.round_id, scope_id=command.scope_id,
            authorization_version=actor.authorization_version, count_key_sha256=count._storage_hash(key),
        )
    document = dump_import_preview(OpeningCountImportBusinessPreview(
        source.sha256, parsed.payload_sha256, parsed.row_count, (), preview,
    ), binding=binding)
    defaults = dict(job_type="import", requested_by=actor_id, parameters_jsonb=parameters,
        parameters_hash=sha256(json.dumps(parameters,sort_keys=True,separators=(",",":")).encode()).hexdigest(),
        status="queued", import_binding_jsonb=binding.model_dump(mode="json"),
        idempotency_key=key)

    from pg16_opening_import_seal_gate import exercise_import_seals
    cases.extend(exercise_import_seals(api,policy_engine=policy_engine,actor=actor,manager_id=other_actor_id,command=command))

    from pg16_opening_import_error_gate import exercise_import_errors
    cases.extend(exercise_import_errors(api, actor_id=actor_id, other_actor_id=other_actor_id,
        data=data, defaults=defaults))
    from pg16_opening_import_termination_gate import exercise_import_termination
    cases.extend(exercise_import_termination(api, actor=actor, other_actor_id=other_actor_id,
        source=source, data=data, command=command))
    from pg16_opening_import_queue_gate import exercise_import_queue
    cases.extend(exercise_import_queue(api, actor=actor, source=source, data=data,
        command=command, snapshot=snapshot))
    assert type(owned_process_checks) is bool
    owned_process_cases = []
    if owned_process_checks:
        from pg16_opening_import_deadline_gate import exercise_import_deadline
        owned_process_cases = exercise_import_deadline(api, actor=actor, source=source,
            data=data, command=command, snapshot=snapshot)
        assert len(owned_process_cases) == 2
        cases.extend(owned_process_cases)

    def reject(label, action, *, states=("23514",)):
        with Session(api) as db:
            try:
                action(db)
                db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate in states, (label, error.orig.sqlstate)
                db.rollback()
            else:
                raise AssertionError("import job accepted " + label)
        cases.append(label)

    for field, value in (("status", "succeeded"), ("import_completion_id", uuid4())):
        def malformed(db, field=field, value=value):
            db.add(FileJob(**{**defaults, field: value}))
            db.flush()
        reject("forged-insert-" + field, malformed)
    for field, value in (("source_sha256", "0" * 64), ("scope_id", str(uuid4())),
                         ("authorization_version", actor.authorization_version + 1),
                         ("count_key_sha256", "f" * 64), ("version", True)):
        def bad_binding(db, field=field, value=value):
            db.add(FileJob(**{**defaults, "import_binding_jsonb": {**defaults["import_binding_jsonb"],field:value}}))
            db.flush()
        reject("binding-" + field, bad_binding)
    with Session(api, expire_on_commit=False) as db:
        intake = dict(actor=actor, source_file_id=source.id, task_id=command.task_id,
            round_id=command.round_id, scope_id=command.scope_id, idempotency_key=raw_key,
            request_id="pg16-import-intake")
        job_id, replayed = create_opening_count_import_job(db, **intake)
        assert not replayed
        db.commit()
        assert create_opening_count_import_job(db, **intake) == (job_id, True)
        db.commit()
        assert recover_opening_count_import_job(db, actor=actor, idempotency_key=raw_key).job_id == job_id
        db.rollback()
        try:
            create_opening_count_import_job(db, **{**intake, "scope_id": uuid4()})
        except OpeningCountImportJobError as error:
            assert error.code == "opening_import_idempotency_conflict"
            db.rollback()
        else:
            raise AssertionError("original import key accepted changed coordinates")
    cases.extend(("intake-replays-original-job", "intake-original-key-recovery", "intake-coordinate-conflict"))
    before = snapshot()

    def change(db, **fields):
        job = db.get(FileJob, job_id)
        for name, value in fields.items(): setattr(job, name, value)
        db.flush()

    reject("immutable-binding-column", lambda db: change(db, import_binding_jsonb={}), states=("42501",))
    reject("skip-prevalidation", lambda db: change(db, status="awaiting_confirmation"))
    reject("forged-completion", lambda db: change(db, status="succeeded", import_completion_id=uuid4()))
    with Session(api) as db:
        claimed = claim_opening_count_import(db, actor=actor, job_id=job_id, request_id="pg16-import-claim")
        assert claimed.status == "prevalidating" and not claimed.recovered
        db.commit()
    for label, alter in (
        ("preview-scope", lambda d: d["count"].update(scope_id=str(uuid4()))),
        ("preview-pending", lambda d: d["count"].update(pending_verification_input_ordinals=[1])),
        ("preview-version-type", lambda d: d.update(version="1")),
        ("preview-extra", lambda d: d.update(raw_cells=["untrusted"])),
        ("preview-quantity-type", lambda d: d["count"].update(observation_count="1")),
        ("preview-missing-row", lambda d: d.update(row_count=d["row_count"] + 1)),
    ):
        changed = deepcopy(document)
        alter(changed)
        reject(label, lambda db, changed=changed: change(db, status="awaiting_confirmation", import_preview_jsonb=changed))
    class SourceStorage:
        provider_code = "aliyun_oss_v2"
        calls = []

        def read_opening_count_source(self, **coordinates):
            assert coordinates == {"storage_key": source.storage_key, "file_id": str(source.id),
                "sha256": source.sha256, "size_bytes": len(data)}
            self.calls.append("source")
            return data

    storage = SourceStorage()
    worker = process_one_opening_count_import(lambda: Session(api), storage=storage, job_id=job_id)
    assert worker.status == "awaiting_confirmation"
    assert storage.calls == ["source"]
    with Session(api) as db:
        job = db.get(FileJob, job_id)
        assert job.import_preview_jsonb == document and job.import_completion_id is None
    repeated = process_one_opening_count_import(lambda: Session(api), storage=storage, job_id=job_id)
    assert repeated.status == "awaiting_confirmation" and repeated.recovered
    assert storage.calls == ["source"]
    before = snapshot()
    cases.extend(("worker-prevalidates-without-counting", "worker-ready-recovery-no-source-io"))
    from app.routers import formal_opening_imports as http
    from app.config import get_settings
    from app.dependencies import get_formal_principal
    app = FastAPI()
    app.include_router(http.router, prefix="/api")
    app.dependency_overrides[get_formal_principal] = lambda: actor
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        opening_count_import_enabled=True, database_url="postgresql+psycopg://synthetic",
        file_storage_configuration_ready=lambda: True)
    app.dependency_overrides[http.get_formal_file_storage_adapter] = lambda: storage
    app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: Session(api)
    client = TestClient(app)
    endpoint = "/api/v1/stocktakes/opening/imports/opening-count/jobs"
    headers = {"Idempotency-Key": raw_key, "X-Request-ID": "pg16-import-http"}
    payload = {k: str(intake[k]) for k in ("source_file_id", "task_id", "round_id", "scope_id")}
    replay = client.post(endpoint, json=payload, headers=headers)
    assert replay.status_code == 202, replay.text
    assert replay.json()["job_id"] == str(job_id) and replay.json()["replayed"]
    assert "no-store" in replay.headers["cache-control"]
    recovered = client.get(endpoint + "/recovery", headers=headers)
    assert recovered.status_code == 200 and recovered.json()["status"] == "awaiting_confirmation"
    injected = client.post(endpoint + f"/{job_id}/confirm", json={"preview": document}, headers=headers)
    assert injected.status_code == 422 and storage.calls == ["source"]
    assert snapshot() == before
    cases.extend(("http-original-key-recovery", "http-intake-replay", "http-preview-injection-refused"))
    review = client.get(endpoint + f"/{job_id}/review")
    assert review.status_code == 200, review.text
    reviewed = review.json()
    assert reviewed["can_confirm"] and reviewed["result"]["status"] == "awaiting_confirmation"
    assert {k: reviewed[k] for k in payload} == payload
    assert reviewed["source_sha256"] == source.sha256 and reviewed["size_bytes"] == len(data)
    assert reviewed["actor_person_id"] == str(actor.person_id)
    assert reviewed["authorization_version"] == actor.authorization_version
    assert "no-store" in review.headers["cache-control"]
    assert set(reviewed) == {"job_id", "task_id", "round_id", "scope_id", "actor_person_id",
        "authorization_version", "source_file_id", "source_sha256", "size_bytes", "original_filename", "can_confirm", "result"}
    assert "storage_key" not in review.text and "counted_qty" not in review.text
    assert storage.calls == ["source"] and snapshot() == before
    cases.append("http-import-review-exact-binding-no-object-io-or-counts")
    from pg16_opening_import_management_gate import exercise_import_management
    cases.extend(exercise_import_management(api, policy_engine=policy_engine, actor=actor, manager_id=other_actor_id,
        command=command, source=source, raw_key=raw_key, job_id=job_id))
    with Session(api) as db:
        other_reader = load_formal_principal(db, other_actor_id)
    for reader, expected_status in ((other_reader, 404),
        (replace(actor, authorization_version=actor.authorization_version + 1), 412)):
        app.dependency_overrides[get_formal_principal] = lambda reader=reader: reader
        denied = client.get(endpoint + f"/{job_id}/review")
        assert denied.status_code == expected_status, denied.text
        assert source.sha256 not in denied.text and str(source.id) not in denied.text
    app.dependency_overrides[get_formal_principal] = lambda: actor
    cases.append("http-import-review-other-owner-and-stale-identity-refused")
    changed = deepcopy(document)
    changed["count"]["binding_sha256"] = "0" * 64
    reject("immutable-preview", lambda db: change(db, import_preview_jsonb=changed))
    reject("unconfirmed-running", lambda db: change(db, status="running"))
    reject("running-cannot-commit-alone", lambda db: change(db, status="running", confirmed_by=actor_id))

    def forged_terminal(db):
        change(db, status="running", confirmed_by=actor_id)
        change(db, status="succeeded", import_completion_id=uuid4(),
               completed_at=db.scalar(text("SELECT clock_timestamp()")))
    reject("success-requires-same-transaction-count", forged_terminal)
    assert snapshot() == before

    authorized_source = AuthorizedOpeningCountSource(source.id, source.sha256, len(data), data)

    def confirm(db):
        actor = load_formal_principal(db, actor_id)
        return confirm_persisted_opening_count_import(db, actor=actor, job_id=job_id,
            source=authorized_source, request_id="pg16-import-job:" + str(job_id))

    with Session(api) as db:
        actor = load_formal_principal(db, actor_id)
        try:
            confirm_persisted_opening_count_import(db, actor=actor, job_id=job_id,
                source=replace(authorized_source, file_id=uuid4()), request_id="pg16-import-wrong-source")
        except OpeningCountImportJobError as error:
            assert error.code == "opening_import_source_changed"
        else:
            raise AssertionError("persisted confirmation accepted a different source")
        db.rollback()
    assert snapshot() == before

    with Session(api) as db:
        assert confirm(db).scope_completed
        db.rollback()
    assert snapshot() == before
    _assert_advisory_before_task(api, command=command, key=key, confirm=confirm)
    assert snapshot() == before
    with Session(api) as db:
        job = db.get(FileJob, job_id)
        assert job.status == "awaiting_confirmation" and job.import_completion_id is None
    class LostCommitAcknowledgement(Session):
        def commit(self):
            super().commit()
            raise RuntimeError("synthetic connection lost after successful COMMIT")

    app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: LostCommitAcknowledgement(api)
    uncertain = client.post(endpoint + f"/{job_id}/confirm", json={}, headers=headers)
    assert uncertain.status_code == 503, uncertain.text
    assert uncertain.json()["detail"]["code"] == "opening_import_result_unknown"
    app.dependency_overrides[http.get_import_session_factory] = lambda: lambda: Session(api)
    confirmed = client.get(endpoint + "/recovery", headers=headers)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "succeeded" and confirmed.json()["completion_id"]
    assert storage.calls == ["source", "source"]
    from app.stocktake_models import StocktakeRound, StocktakeScopeCountCompletion
    with Session(api) as db:
        completion = db.get(StocktakeScopeCountCompletion, UUID(confirmed.json()["completion_id"]))
        assert completion.task_id == command.task_id and completion.scope_id == command.scope_id
        result = SimpleNamespace(scope_completed=True,
            round_sealed=db.get(StocktakeRound, command.round_id).status == "submitted",
            task_status=db.get(FormalStocktakeTask, command.task_id).status)
    again = client.post(endpoint + f"/{job_id}/confirm", json={}, headers=headers)
    assert again.status_code == 409 and storage.calls == ["source", "source"]
    recovered = client.get(endpoint + "/recovery", headers=headers)
    assert recovered.status_code == 200 and recovered.json()["completion_id"] == confirmed.json()["completion_id"]
    cases.extend(("http-confirm-count-and-job-atomic", "http-confirm-replay-no-source-io",
                  "http-confirm-lost-ack-recovery-no-replay"))
    historical = client.get(endpoint + f"/{job_id}/review")
    assert historical.status_code == 200, historical.text
    assert not historical.json()["can_confirm"] and historical.json()["result"]["status"] == "succeeded"
    assert {k: historical.json()[k] for k in payload} == payload
    assert storage.calls == ["source", "source"]
    cases.append("http-import-review-terminal-remains-original-binding-not-confirmable")
    cases.extend(exercise_import_management(api, policy_engine=policy_engine, actor=actor, manager_id=other_actor_id,
        command=command, source=source, raw_key=raw_key, job_id=job_id, succeeded=True))
    with Session(api) as db:
        recovered = db.scalar(select(FileJob).where(FileJob.idempotency_key==key))
        assert recovered.id==job_id and recovered.status=="succeeded" and recovered.import_completion_id is not None
        status = read_opening_count_import_job(db, actor=load_formal_principal(db, actor_id), job_id=job_id)
        assert status.status == "succeeded" and status.completion_id == recovered.import_completion_id
        audit = db.scalars(select(AuditEvent).where(
            AuditEvent.aggregate_type == "file_job", AuditEvent.aggregate_id == str(job_id),
            AuditEvent.action == "opening_count_import_confirmed")).all()
        assert len(audit) == 1 and audit[0].after_jsonb["count_key_sha256"] == binding.count_key_sha256
    for forbidden_actor, code in ((other_actor_id, "opening_import_job_not_found"),
                                  (actor_id, "opening_import_actor_stale")):
        with Session(api) as db:
            reader = load_formal_principal(db, forbidden_actor)
            if forbidden_actor == actor_id:
                reader = replace(reader, authorization_version=reader.authorization_version + 1)
            try:
                read_opening_count_import_job(db, actor=reader, job_id=job_id)
            except OpeningCountImportJobError as error:
                assert error.code == code
            else:
                raise AssertionError("import recovery ignored ownership or current authority")
    after = snapshot()
    with Session(api) as db:
        try:
            confirm(db)
        except OpeningCountImportJobError as error:
            assert error.code == "opening_import_not_awaiting_confirmation"
        else:
            raise AssertionError("completed import was confirmed again")
        db.rollback()
    assert snapshot() == after
    reject("terminal-is-immutable", lambda db: change(db, status="cancelled"))
    cases.extend(("count-and-job-rollback-together", "original-key-commits-once", "readonly-original-key-recovery",
                  "persisted-confirmation-uses-real-xlsx", "wrong-source-rejected", "reconfirmation-does-not-replay"))
    cases.extend(("advisory-wait-does-not-hold-task-row", "confirmation-audit-exactly-once",
                  "other-user-recovery-refused", "stale-identity-recovery-refused"))
    return result, {"status":"passed", "cases":cases, "source":"synthetic-xlsx", "httpWorkerAcceptance":"synthetic-principal-and-storage", "internalWorkerPositive":True,
        "owned_process_checks":{"enabled":owned_process_checks,"case_count":len(owned_process_cases),
            "scope":"owned-native-pg16-synthetic-source","githubReleaseGate":False,"productionAcceptance":False}}


def _assert_advisory_before_task(api, *, command, key, confirm):
    """A manual count holding the common advisory lock must still get its task.

    Observe the exact backend waiting on this blocker before testing NOWAIT;
    a delay or submitted future alone is not proof of concurrent contention.
    """
    pid_queue = Queue()

    def concurrent_confirmation():
        with Session(api) as db:
            db.execute(text("SET LOCAL lock_timeout='15s'"))
            pid_queue.put(db.scalar(text("SELECT pg_backend_pid()")))
            assert confirm(db).scope_completed
            db.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(api) as blocker:
            blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
            count.lock_opening_count_coordinates(blocker, task_id=command.task_id,
                round_id=command.round_id, idempotency_key=key)
            future = pool.submit(concurrent_confirmation)
            try:
                waiting_pid = pid_queue.get(timeout=5)
                deadline = time.monotonic() + 10
                while True:
                    with api.connect() as monitor:
                        waiting = monitor.scalar(text("""
                            SELECT :blocker=ANY(pg_blocking_pids(:waiting)) AND EXISTS (
                                SELECT 1 FROM pg_stat_activity WHERE pid=:waiting
                                AND wait_event_type='Lock' AND wait_event='advisory')
                        """), {"blocker": blocker_pid, "waiting": waiting_pid})
                    if waiting:
                        break
                    if future.done():
                        future.result()
                        raise AssertionError("confirmation did not wait on the existing count")
                    if time.monotonic() >= deadline:
                        raise AssertionError("exact import advisory blocker was not observed")
                    time.sleep(0.05)
                # With the old task-before-advisory order this raises 55P03.
                assert blocker.scalar(select(FormalStocktakeTask.id).where(
                    FormalStocktakeTask.id == command.task_id).with_for_update(nowait=True)) == command.task_id
            finally:
                blocker.rollback()
            future.result(timeout=60)
