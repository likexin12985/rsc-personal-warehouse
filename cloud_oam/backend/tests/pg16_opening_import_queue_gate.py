"""Real PG16/API-role import queue isolation; synthetic source transport only.

Run inside the published opening fixture before its successful count. Business
services, row locks, deferred guards, prevalidation and audits remain real.
Fault injection is confined to source transport and database acknowledgements.
This is not an OSS integration, full release, or production acceptance claim.
"""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from queue import Empty, Queue
import time
from uuid import uuid4

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from formal_file_integrity import FileUploadIntentInput, _prepare_upload
from app.foundation_models import AuditEvent, FileJob
from app.formal_services.file_storage import FileStorageError
from app.formal_services.opening_count_import_intake import create_opening_count_import_job
from app.formal_services.opening_count_import_termination import (
    cancel_opening_count_import, sweep_awaiting_opening_imports,
)
from app.opening_count_import_worker import (
    _opening_import_queue_page, _poll_opening_imports,
)
from app.opening_import_worker_database import opening_import_worker_session_factory
from pg16_opening_source_purpose_gate import MIME, _file


class _OnePageStop:
    """Stop after a complete queue page; no business clock or service override."""

    def __init__(self):
        self.waits = 0

    def is_set(self):
        return self.waits != 0

    def wait(self, seconds):
        assert seconds == 5
        self.waits += 1


def _poll_page(factory, storage):
    stop, output = _OnePageStop(), []
    _poll_opening_imports(factory, storage=storage, poll_seconds=5,
                         stop_event=stop, emit=output.append)
    assert stop.waits == 1
    return output


def exercise_import_queue(api, *, actor, source, data, command, snapshot):
    """Exercise four isolation boundaries with exact persisted rereads."""
    with api.connect() as db:
        assert db.scalar(text("SELECT current_user")) == "star_oam_api"
        assert int(db.scalar(text("SHOW server_version_num"))) // 10000 == 16

    factory = lambda: Session(api)

    def stock_facts():
        # Prevalidation legitimately appends audit events. Every supplied
        # stocktake/ledger/outbox row must remain unchanged, not only totals.
        return {name: rows for name, rows in snapshot().items()
                if name not in {"audit_events", "audit_chain_heads"}}

    before = stock_facts()
    prepared = _prepare_upload(FileUploadIntentInput(purpose="opening_count_import",
        original_filename="queue-source.xlsx", size_bytes=len(data), mime_type=MIME,
        sha256=sha256(data).hexdigest()), maximum_size_bytes=8 * 1024 * 1024)
    with Session(api, expire_on_commit=False) as db:
        duplicate = _file(user_id=actor.user_id, person_id=actor.person_id,
                          size_bytes=len(data), prepared=prepared)
        duplicate.sha256 = sha256(data).hexdigest()
        duplicate.original_filename = "queue-source.xlsx"
        duplicate.created_at = duplicate.updated_at = db.scalar(text("SELECT transaction_timestamp()"))
        db.add(duplicate)
        db.flush()
        duplicate.status = "available"
        duplicate.metadata_jsonb = {**duplicate.metadata_jsonb, "completion": {
            "etag_sha256": "a" * 64, "head_manifest_sha256": "b" * 64,
            "verified_at": duplicate.created_at.isoformat()}}
        db.commit()

    sources = {str(item.id): item for item in (source, duplicate)}

    class Storage:
        provider_code = "aliyun_oss_v2"

        def __init__(self, unavailable=()):
            self.unavailable = set(unavailable)
            self.reads, self.object_writes = [], []

        def read_opening_count_source(self, **coordinates):
            item = sources[coordinates["file_id"]]
            assert coordinates == {"storage_key": item.storage_key, "file_id": str(item.id),
                                   "sha256": item.sha256, "size_bytes": len(data)}
            self.reads.append(item.id)
            if item.id in self.unavailable:
                raise FileStorageError("synthetic source transport failure: private diagnostic")
            return data

        def put_opening_count_error(self, **coordinates):
            self.object_writes.append("put")
            raise AssertionError("valid source must not create an error object")

        def head_object(self, **coordinates):
            self.object_writes.append("head")
            raise AssertionError("valid source must not recover an error object")

    def create_pair():
        keys, bindings = {}, {}
        for item in (source, duplicate):
            key = uuid4().hex
            with factory() as db:
                identifier, replayed = create_opening_count_import_job(db, actor=actor,
                    source_file_id=item.id, task_id=command.task_id, round_id=command.round_id,
                    scope_id=command.scope_id, idempotency_key=key, request_id="pg16-queue-create")
                assert not replayed
                db.commit()
                keys[identifier] = key
                bindings[identifier] = db.get(FileJob, identifier).import_binding_jsonb
        ordered = tuple(sorted(keys))
        assert _opening_import_queue_page(factory) == ordered
        assert stock_facts() == before
        return ordered, keys, bindings

    def assert_jobs(identifiers, bindings, expected, *, read_factory=factory):
        with read_factory() as db:
            for identifier in identifiers:
                job = db.get(FileJob, identifier)
                assert job.status == expected[identifier]
                assert job.import_binding_jsonb == bindings[identifier]
                assert job.import_completion_id is None and job.error_file_id is None
                assert job.import_error_sha256 is None
                if job.status in {"queued", "prevalidating", "awaiting_confirmation"}:
                    assert job.completed_at is None and job.error_detail is None
                if job.status == "awaiting_confirmation":
                    assert job.import_preview_jsonb and job.import_preview_jsonb["errors"] == []

    def audits(identifier, action, *, read_factory=factory):
        with read_factory() as db:
            return list(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == "file_job",
                AuditEvent.aggregate_id == str(identifier), AuditEvent.action == action)))

    def cancel(keys):
        for identifier, key in keys.items():
            with factory() as db:
                result, replayed = cancel_opening_count_import(db, actor=actor, job_id=identifier,
                    idempotency_key=key, request_id="pg16-queue-cleanup")
                assert result.status == "cancelled" and not replayed
                db.commit()
        assert not _opening_import_queue_page(factory)
        assert stock_facts() == before

    cases = []
    identifiers, keys, bindings = create_pair()
    first, second = identifiers
    failed_source = sources[bindings[first]["source_file_id"]]
    transport = Storage(unavailable=(failed_source.id,))
    output = _poll_page(factory, transport)
    assert output == [
        {"ok": False, "job_id": str(first), "code": "opening_import_worker_retry_or_review_required"},
        {"ok": True, "job_id": str(second), "status": "awaiting_confirmation", "recovered": False},
    ]
    assert_jobs(identifiers, bindings, {first: "prevalidating", second: "awaiting_confirmation"})
    assert transport.reads == [sources[bindings[item]["source_file_id"]].id for item in identifiers]
    assert not transport.object_writes and not audits(first, "opening_count_import_terminated")
    assert stock_facts() == before
    cancel(keys)
    cases.append("queue-source-timeout-retains-original-and-continues-without-count")

    identifiers, keys, bindings = create_pair()
    first, second = identifiers
    transport = Storage()
    fired = []

    class LostPreviewCommitAcknowledgement(Session):
        def commit(self):
            lose_ack = (not fired and self.scalar(select(FileJob.status).where(
                FileJob.id == first)) == "awaiting_confirmation")
            super().commit()
            if lose_ack:
                fired.append(first)
                raise RuntimeError("synthetic preview COMMIT acknowledgement loss")

    output = _poll_page(lambda: LostPreviewCommitAcknowledgement(api), transport)
    assert fired == [first]
    assert output == [
        {"ok": False, "job_id": str(first), "code": "opening_import_worker_retry_or_review_required"},
        {"ok": True, "job_id": str(second), "status": "awaiting_confirmation", "recovered": False},
    ]
    assert_jobs(identifiers, bindings, {identifier: "awaiting_confirmation" for identifier in identifiers})
    original_reads = list(transport.reads)
    assert _poll_page(factory, transport) == [{"ok": True, "job_id": None, "status": "idle", "recovered": False}]
    assert transport.reads == original_reads and len(original_reads) == 2 and not transport.object_writes
    assert all(len(audits(identifier, "opening_count_import_prevalidated")) == 1 for identifier in identifiers)
    assert not any(audits(identifier, "opening_count_import_terminated") for identifier in identifiers)
    assert stock_facts() == before
    cases.append("queue-preview-commit-ack-lost-rereads-original-without-source-replay")

    # Revoke the actual user version only in an outer transaction, matching
    # the existing authority fixture. Inject a connection acquisition failure
    # before the first job starts; the second job still executes every real
    # authority check and termination guard. Do not use savepoints: the audit
    # guard deliberately requires xmin to identify this root transaction.
    with api.connect() as connection:
        root = connection.begin()
        try:
            connection.execute(text("UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id"),
                               {"id": actor.user_id})
            session_attempts = []

            def failed_factory():
                session_attempts.append(len(session_attempts) + 1)
                # Discovery is the first independent session. The second
                # requested session belongs to the first selected job.
                if len(session_attempts) == 2:
                    raise RuntimeError("synthetic database connection acquisition failure")
                return Session(connection, join_transaction_mode="rollback_only")

            read_factory = lambda: Session(connection, join_transaction_mode="rollback_only")
            swept = sweep_awaiting_opening_imports(failed_factory)
            assert session_attempts == [1, 2, 3] and swept.checked == 2 and swept.failed_ids == (first,)
            connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
            assert_jobs(identifiers, bindings, {first: "awaiting_confirmation", second: "failed"},
                        read_factory=read_factory)
            assert not audits(first, "opening_count_import_terminated", read_factory=read_factory)
            assert len(audits(second, "opening_count_import_terminated", read_factory=read_factory)) == 1
            # IMMEDIATE verifies the completed first sweep, but persists in
            # this outer rollback-only fixture. Restore the production
            # transaction's deferred mode before the next service writes its
            # job transition and audit together.
            connection.exec_driver_sql("SET CONSTRAINTS ALL DEFERRED")
            recovered = sweep_awaiting_opening_imports(read_factory)
            assert recovered.checked == 1 and not recovered.failed_ids
            connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
            assert root.is_active and connection.in_transaction()
            assert_jobs(identifiers, bindings, {identifier: "failed" for identifier in identifiers},
                        read_factory=read_factory)
            for identifier in identifiers:
                audit_event, = audits(identifier, "opening_count_import_terminated", read_factory=read_factory)
                assert audit_event.actor_user_id is None
                assert audit_event.after_jsonb["reason"] == "opening_import_context_changed"
        finally:
            root.rollback()
    assert_jobs(identifiers, bindings, {identifier: "awaiting_confirmation" for identifier in identifiers})
    assert transport.reads == original_reads and stock_facts() == before
    cancel(keys)
    cases.append("queue-revocation-sweep-isolates-session-failure-and-enforces-real-audit-guards")

    identifiers, keys, bindings = create_pair()
    first, second = identifiers
    transport, pids = Storage(), Queue()
    bounded = opening_import_worker_session_factory(api, lock_timeout_ms=1000, statement_timeout_ms=5000)
    lock_timeout_states = []

    def capture_lock_timeout(context):
        sqlstate = getattr(context.original_exception, "sqlstate", None)
        if sqlstate == "55P03":
            lock_timeout_states.append(sqlstate)

    # Use the exact same physical connection for before/after observations;
    # taking an arbitrary pooled connection cannot prove local GUC cleanup.
    with api.connect() as connection:
        sql = text("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
        original_budgets = tuple(connection.execute(sql).one())
        connection.rollback()
        local_factory = opening_import_worker_session_factory(connection,
            lock_timeout_ms=1000, statement_timeout_ms=5000)
        for committed in (True, False):
            with local_factory() as db:
                assert tuple(db.execute(sql).one()) == ("1s", "5s")
                if committed:
                    db.commit()
                else:
                    db.rollback()
            assert tuple(connection.execute(sql).one()) == original_budgets
            connection.rollback()

    def traced_factory():
        db = bounded()
        pids.put(db.scalar(text("SELECT pg_backend_pid()")))
        assert tuple(db.execute(text("SELECT current_setting('lock_timeout'), "
                                     "current_setting('statement_timeout')")).one()) == ("1s", "5s")
        return db

    with Session(api) as blocker:
        blocker_pid = blocker.scalar(text("SELECT pg_backend_pid()"))
        assert blocker.scalar(select(FileJob.id).where(FileJob.id == first).with_for_update()) == first
        # Keep the first job locked until the entire page finishes. The
        # second job shares the real task and principal graph, proving those
        # locks are released on the first transaction's timeout.
        with ThreadPoolExecutor(max_workers=1) as pool:
            event.listen(api, "handle_error", capture_lock_timeout)
            future = pool.submit(_poll_page, traced_factory, transport)
            waiting_pids, observed = set(), False
            deadline = time.monotonic() + 10
            try:
                while not observed and time.monotonic() < deadline:
                    while True:
                        try:
                            waiting_pids.add(pids.get_nowait())
                        except Empty:
                            break
                    with api.connect() as monitor:
                        observed = any(monitor.scalar(text("SELECT :blocker=ANY(pg_blocking_pids(:waiter))"),
                            {"blocker": blocker_pid, "waiter": waiter}) for waiter in waiting_pids)
                    if observed or future.done():
                        break
                    time.sleep(0.01)
                assert observed, "no actual wait on the first FileJob row was observed"
                output = future.result(timeout=15)
                assert output == [
                    {"ok": False, "job_id": str(first), "code": "opening_import_worker_retry_or_review_required"},
                    {"ok": True, "job_id": str(second), "status": "awaiting_confirmation", "recovered": False},
                ]
                assert_jobs(identifiers, bindings, {first: "queued", second: "awaiting_confirmation"})
                assert lock_timeout_states == ["55P03"]
                assert not audits(first, "opening_count_import_terminated")
                assert transport.reads == [sources[bindings[second]["source_file_id"]].id]
                assert not transport.object_writes and stock_facts() == before
            finally:
                blocker.rollback()
                event.remove(api, "handle_error", capture_lock_timeout)
            future.result(timeout=15)
    cancel(keys)
    cases.append("queue-real-first-job-row-lock-times-out-and-next-job-prevalidates")
    return cases
