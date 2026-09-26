"""Opt-in owned-native PG16 checks for the real spawned import boundary.

The caller already owns native_cluster. No supplied/remote DSN, OSS service,
business-function mock, SAVEPOINT or file-backed business state is used.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing as mp
import os
import time
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.daily_reconciliation.process_entry import ProcessOutcomeUnknown, run_owned_job
from app.foundation_models import AuditEvent, FileJob, FileObject
from app.formal_services.opening_count_import_intake import create_opening_count_import_job
from app.formal_services.opening_count_import_termination import cancel_opening_count_import
from app.opening_count_import_worker import (
    OpeningImportWorkerResult, _opening_import_queue_page, _poll_opening_imports,
)
from app.opening_import_worker_entry import (
    OPENING_IMPORT_JOB_MAXIMUM_SECONDS, OpeningImportWorkerSupervisionError,
)
from pg16_opening_import_process_fixture import (
    commit_then_hang, healthy, source_lock_then_hang, validate_owned_native_url,
)
from pg16_opening_import_queue_gate import _OnePageStop


MAXIMUM_SECONDS = 15


def exercise_import_deadline(api, *, actor, source, data, command, snapshot):
    """Two real-state fault cases; only an explicit native caller enables this."""
    assert OPENING_IMPORT_JOB_MAXIMUM_SECONDS == 60
    assert type(MAXIMUM_SECONDS) is int and 1 <= MAXIMUM_SECONDS <= OPENING_IMPORT_JOB_MAXIMUM_SECONDS
    # Do not print or persist this local URL. It is accepted only because the
    # enclosing native_cluster has already proved the owned server identity.
    database_url = api.url.render_as_string(hide_password=False)
    validate_owned_native_url(database_url)
    factory = lambda: Session(api)

    def stock_facts():
        return {name: rows for name, rows in snapshot().items()
                if name not in {"audit_events", "audit_chain_heads"}}

    before = stock_facts()

    def audit_count(identifier, action):
        with factory() as db:
            return len(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == "file_job",
                AuditEvent.aggregate_id == str(identifier), AuditEvent.action == action)).all())

    def assert_state(identifier, expected, binding):
        with factory() as db:
            job = db.get(FileJob, identifier)
            assert job.status == expected and job.import_binding_jsonb == binding
            assert job.import_completion_id is None and job.error_file_id is None
            assert job.import_error_sha256 is None and job.error_detail is None
            assert job.completed_at is None
            if expected == "awaiting_confirmation":
                assert job.import_preview_jsonb and job.import_preview_jsonb["errors"] == []

    def create_pair():
        keys, bindings = {}, {}
        for _ in range(2):
            key = uuid4().hex
            with factory() as db:
                identifier, replayed = create_opening_count_import_job(db, actor=actor,
                    source_file_id=source.id, task_id=command.task_id, round_id=command.round_id,
                    scope_id=command.scope_id, idempotency_key=key, request_id="pg16-owned-import-create")
                assert not replayed
                db.commit()
                keys[identifier] = key
                bindings[identifier] = db.get(FileJob, identifier).import_binding_jsonb
        ordered = tuple(sorted(keys))
        assert _opening_import_queue_page(factory) == ordered
        assert stock_facts() == before
        return ordered, keys, bindings

    def cancel(keys):
        for identifier, key in keys.items():
            with factory() as db:
                result, replayed = cancel_opening_count_import(db, actor=actor, job_id=identifier,
                    idempotency_key=key, request_id="pg16-owned-import-cleanup")
                assert result.status == "cancelled" and not replayed
                db.commit()
        assert not _opening_import_queue_page(factory) and stock_facts() == before

    def assert_backend_gone(application_name, backend_pid):
        deadline = time.monotonic() + 5
        while True:
            with api.connect() as db:
                remaining = db.scalar(text("SELECT count(*) FROM pg_stat_activity "
                    "WHERE application_name=:app OR pid=:pid"),
                    {"app": application_name, "pid": backend_pid})
            if remaining == 0:
                return
            assert time.monotonic() < deadline, "owned child database backends remained after process reap"
            time.sleep(0.02)

    observations = []

    def supervise(target, identifier, binding, *, expected_phase=None):
        application_name = "pg16-import-owned-" + uuid4().hex
        rx, tx = mp.get_context("spawn").Pipe(duplex=False)
        payload = {"database_url": database_url, "job_id": str(identifier),
            "source_coordinates": {"storage_key": source.storage_key, "file_id": str(source.id),
                                   "sha256": source.sha256, "size_bytes": len(data)},
            "source_bytes": data, "application_name": application_name, "progress": tx}
        messages, phase_observed, result, unknown = [], False, None, False

        def read_progress():
            nonlocal phase_observed
            while rx.poll(0):
                message = json.loads(rx.recv_bytes(1024))
                assert type(message) is dict and set(message) == {"event", "pid", "backend_pid", "job_id"}
                assert message["job_id"] == str(identifier) and type(message["pid"]) is int and message["pid"] > 0
                assert message["backend_pid"] is None or type(message["backend_pid"]) is int
                assert message["event"] in {"ready", "source_read", "committed", "source_locked"}
                messages.append(message)
                assert len(messages) <= 8
                if message["event"] != expected_phase:
                    continue
                assert not phase_observed, "owned child reported the fault point twice"
                phase_observed = True
                # The fault point must be reached while both the OS process
                # and its actual private PG backend still exist.
                os.kill(message["pid"], 0)
                with api.connect() as db:
                    assert db.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE pid=:pid AND application_name=:app AND usename='star_oam_api')"),
                        {"pid": message["backend_pid"], "app": application_name})
                if expected_phase == "committed":
                    assert_state(identifier, "awaiting_confirmation", binding)
                    assert audit_count(identifier, "opening_count_import_prevalidated") == 1
                else:
                    assert expected_phase == "source_locked"
                    assert_state(identifier, "prevalidating", binding)
                    with factory() as probe:
                        try:
                            probe.scalar(select(FileObject.id).where(FileObject.id == source.id)
                                         .with_for_update(nowait=True))
                        except DBAPIError as error:
                            assert error.orig.sqlstate == "55P03"
                            probe.rollback()
                        else:
                            raise AssertionError("child did not hold the real source FileObject row lock")
                assert stock_facts() == before

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(run_owned_job, target, payload, maximum_seconds=MAXIMUM_SECONDS)
                deadline = time.monotonic() + MAXIMUM_SECONDS + 3
                while not future.done():
                    read_progress()
                    assert time.monotonic() < deadline, "owned process supervisor exceeded its deadline allowance"
                    time.sleep(0.01)
                try:
                    result = future.result()
                except ProcessOutcomeUnknown:
                    unknown = True
                read_progress()
            assert messages and messages[0]["event"] == "ready"
            pids = {message["pid"] for message in messages}
            assert len(pids) == 1
            pid, = pids
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise AssertionError("owned child PID remained before scheduling the next job")
            backend_pids = {message["backend_pid"] for message in messages if message["backend_pid"] is not None}
            assert backend_pids
            for backend_pid in backend_pids:
                assert_backend_gone(application_name, backend_pid)
            if expected_phase is not None:
                assert unknown and phase_observed
            else:
                assert not unknown
            assert len([message for message in messages if message["event"] == "source_read"]) == 1
            # Parent rereads the original FileObject only after the backend
            # disappearance proof; process death alone is not lock evidence.
            with factory() as probe:
                assert probe.scalar(select(FileObject.id).where(FileObject.id == source.id)
                                    .with_for_update(nowait=True)) == source.id
                probe.rollback()
            observations.append({"job_id": identifier, "messages": messages, "unknown": unknown})
            if unknown:
                raise ProcessOutcomeUnknown("owned_native_import_result_unknown_exact_recovery_required")
            assert type(result) is dict and set(result) == {"job_id", "status", "recovered"}
            assert result["job_id"] == str(identifier) and result["status"] == "awaiting_confirmation"
            assert type(result["recovered"]) is bool
            return OpeningImportWorkerResult(identifier, result["status"], result["recovered"])
        finally:
            tx.close()
            rx.close()

    cases = []
    for fault, target, phase in (("commit", commit_then_hang, "committed"),
                                 ("lock", source_lock_then_hang, "source_locked")):
        identifiers, keys, bindings = create_pair()
        first, second = identifiers
        calls = []

        def process(identifier):
            calls.append(identifier)
            try:
                return supervise(target if identifier == first else healthy,
                    identifier, bindings[identifier], expected_phase=phase if identifier == first else None)
            except ProcessOutcomeUnknown:
                raise
            except BaseException as error:
                # Assertions/infrastructure faults must not be swallowed by
                # the worker's intentional per-business-object isolation.
                raise OpeningImportWorkerSupervisionError() from error

        output, stop = [], _OnePageStop()
        _poll_opening_imports(factory, storage=None, poll_seconds=5, stop_event=stop,
                             emit=output.append, process_job=process)
        assert stop.waits == 1 and calls == list(identifiers)
        assert output == [
            {"ok": False, "job_id": str(first), "code": "opening_import_worker_retry_or_review_required"},
            {"ok": True, "job_id": str(second), "status": "awaiting_confirmation", "recovered": False},
        ]
        assert_state(first, "awaiting_confirmation" if fault == "commit" else "prevalidating", bindings[first])
        assert_state(second, "awaiting_confirmation", bindings[second])
        assert audit_count(first, "opening_count_import_prevalidation_started") == 1
        assert audit_count(second, "opening_count_import_prevalidated") == 1
        assert not audit_count(first, "opening_count_import_terminated") and stock_facts() == before
        if fault == "commit":
            original_observation_count = len(observations)
            next_output, next_stop = [], _OnePageStop()
            _poll_opening_imports(factory, storage=None, poll_seconds=5, stop_event=next_stop,
                                 emit=next_output.append, process_job=process)
            assert next_stop.waits == 1 and calls == list(identifiers)
            assert len(observations) == original_observation_count
            assert next_output == [{"ok": True, "job_id": None, "status": "idle", "recovered": False}]
            assert audit_count(first, "opening_count_import_prevalidated") == 1
            cases.append("owned-native-child-commit-then-deadline-reaps-and-rereads-original-without-replay")
        else:
            assert audit_count(first, "opening_count_import_prevalidated") == 0
            recovered = supervise(healthy, first, bindings[first])
            assert recovered.recovered and recovered.job_id == first
            assert_state(first, "awaiting_confirmation", bindings[first])
            assert audit_count(first, "opening_count_import_prevalidated") == 1
            assert audit_count(first, "opening_count_import_prevalidation_started") == 1
            cases.append("owned-native-child-held-source-lock-deadline-releases-backend-before-next-original-job")
        cancel(keys)
    return cases
