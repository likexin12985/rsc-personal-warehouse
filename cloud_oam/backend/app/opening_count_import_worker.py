"""Prevalidate one durable import; confirmation remains a separate user action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .config import get_settings

if TYPE_CHECKING:
    from .formal_services.file_storage import FileStorageAdapter


@dataclass(frozen=True, slots=True)
class OpeningImportWorkerResult:
    job_id: UUID | None
    status: str
    recovered: bool = False


def process_one_opening_count_import(
    session_factory: Callable[[], Session], *, storage: FileStorageAdapter, job_id: UUID | None = None,
) -> OpeningImportWorkerResult:
    from .foundation_models import FileJob
    from .formal_services.opening_count_import_jobs import OpeningCountImportJobError
    from .formal_services.opening_count_import_termination import terminate_ineligible_opening_import

    if getattr(storage, "provider_code", None) != "aliyun_oss_v2":
        raise OpeningCountImportJobError("opening_import_storage_invalid", 503, "私有存储不可用")
    if job_id is not None and not isinstance(job_id, UUID):
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    with session_factory() as db:
        candidate = job_id if job_id is not None else db.scalar(select(FileJob.id).where(
            FileJob.job_type == "import", FileJob.status.in_(("queued", "prevalidating")))
            .order_by(case((FileJob.status == "prevalidating", 0), else_=1),
                      FileJob.created_at, FileJob.id).limit(1))
    if candidate is None:
        return OpeningImportWorkerResult(None, "idle")

    def recover_terminal():
        with session_factory() as db:
            terminal = terminate_ineligible_opening_import(db, job_id=candidate,
                request_id=f"opening-import-context:{candidate}")
            db.commit()
        return (OpeningImportWorkerResult(candidate, terminal.status, True) if terminal else None)

    terminal = recover_terminal()
    if terminal:
        return terminal
    try:
        return _process_selected_import(session_factory, storage=storage, candidate=candidate)
    except Exception:
        # Only a fresh, authoritative context check can end this job. A
        # timeout/SDK/database exception alone never proves a failed upload.
        terminal = recover_terminal()
        if terminal:
            return terminal
        raise


def _process_selected_import(session_factory, *, storage, candidate):
    """Release source-read locks before the task-first prevalidation transaction.

    A durable error preparation authorizes at most one PUT. Unknown PUT or
    commit results propagate to supervision; subsequent processing uses HEAD
    only, and a completed job is returned without source or storage access.
    """
    from .formal_access import load_formal_principal
    from .foundation_models import FileJob
    from .formal_services.opening_count_import_document import load_import_binding
    from .formal_services.opening_count_import_error_jobs import publish_persisted_opening_count_error
    from .formal_services.opening_count_import_error_report import write_or_recover_opening_count_error
    from .formal_services.opening_count_import_jobs import OpeningCountImportJobError, read_opening_count_import_job
    from .formal_services.opening_count_import_prevalidation_jobs import (
        claim_opening_count_import, prevalidate_persisted_opening_count_import,
    )
    from .formal_services.opening_count_import_source import read_authorized_opening_count_source
    from .formal_services.opening_count_import_termination import (
        fail_invalid_opening_import_source, terminate_ineligible_opening_import,
    )
    from .formal_services.opening_count_import_workbook import OpeningCountImportFormatError
    with session_factory() as db:
        job = db.scalar(select(FileJob).where(FileJob.id == candidate, FileJob.job_type == "import"))
        if job is None:
            raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
        actor = load_formal_principal(db, job.requested_by)
        binding = load_import_binding(job.import_binding_jsonb)
        claim = claim_opening_count_import(db, actor=actor, job_id=job.id,
            request_id=f"opening-import-claim:{job.id}")
        db.commit()
    if claim.status != "prevalidating":
        return OpeningImportWorkerResult(candidate, claim.status, True)
    with session_factory() as db:
        source = read_authorized_opening_count_source(db, actor=actor,
            file_id=binding.source_file_id, storage=storage)
    try:
        with session_factory() as db:
            result = prevalidate_persisted_opening_count_import(db, actor=actor, job_id=candidate,
                source=source, request_id=f"opening-import-preview:{candidate}")
            db.commit()
    except OpeningCountImportFormatError:
        with session_factory() as db:
            failed = fail_invalid_opening_import_source(db, job_id=candidate, source=source,
                request_id=f"opening-import-invalid-source:{candidate}")
            db.commit()
        return OpeningImportWorkerResult(candidate, failed.status)
    if result.error is None:
        return OpeningImportWorkerResult(candidate, result.status, result.recovered or claim.recovered)
    with session_factory() as db:
        terminal = terminate_ineligible_opening_import(db, job_id=candidate,
            request_id=f"opening-import-before-object:{candidate}")
        db.commit()
    if terminal:
        return OpeningImportWorkerResult(candidate, terminal.status, True)
    head = write_or_recover_opening_count_error(storage, artifact=result.error.artifact,
        allow_new_put=result.error.new_write_allowed)
    with session_factory() as db:
        # Another recoverer can publish while this worker is doing HEAD.
        # Never retry publication or any object write after an unknown commit.
        status = read_opening_count_import_job(db, actor=actor, job_id=candidate)
    if status.status != "prevalidating":
        return OpeningImportWorkerResult(candidate, status.status, True)
    try:
        with session_factory() as db:
            publish_persisted_opening_count_error(db, actor=actor, job_id=candidate,
                head=head, request_id=f"opening-import-error:{candidate}")
            db.commit()
    except OpeningCountImportJobError as exc:
        if exc.code != "opening_import_error_not_prepared":
            raise
        with session_factory() as db:
            status = read_opening_count_import_job(db, actor=actor, job_id=candidate)
            return OpeningImportWorkerResult(candidate, status.status, True)
    return OpeningImportWorkerResult(candidate, "failed", not result.error.new_write_allowed)


def _opening_import_queue_page(session_factory, *, after_id=None, limit=100):
    """Read a bounded UUID page without reserving or changing job state."""
    from .foundation_models import FileJob
    if (type(limit) is not int or not 1 <= limit <= 100
            or (after_id is not None and type(after_id) is not UUID)):
        raise ValueError("opening_import_queue_page_invalid")
    with session_factory() as db:
        query = select(FileJob.id).where(
            FileJob.job_type == "import", FileJob.status.in_(("queued", "prevalidating")))
        if after_id is not None:
            query = query.where(FileJob.id > after_id)
        return tuple(db.scalars(query.order_by(FileJob.id).limit(limit)))


def _poll_opening_imports(session_factory, *, storage, poll_seconds, stop_event, emit, process_job=None):
    from time import monotonic
    from .opening_import_worker_entry import OpeningImportWorkerSupervisionError
    from .formal_services.opening_count_import_termination import sweep_awaiting_opening_imports

    queue_cursor, sweep_cursor, next_sweep = None, None, 0.0

    def sweep_due():
        nonlocal sweep_cursor, next_sweep
        if monotonic() < next_sweep:
            return
        swept = sweep_awaiting_opening_imports(session_factory, after_id=sweep_cursor)
        sweep_cursor = swept.next_after_id
        next_sweep = monotonic() + poll_seconds
        for identifier in swept.failed_ids:
            emit({"ok": False, "job_id": str(identifier),
                  "code": "opening_import_context_review_required"})

    while not stop_event.is_set():
        # A broken original object must not pin every subsequent job behind it.
        # Advance only the scheduler cursor; never infer business failure from
        # a transport or commit exception, or discard the original evidence.
        identifiers = _opening_import_queue_page(session_factory, after_id=queue_cursor)
        queue_cursor = identifiers[-1] if len(identifiers) == 100 else None
        for identifier in identifiers:
            if stop_event.is_set():
                return
            try:
                result = (process_job(identifier) if process_job is not None else
                    process_one_opening_count_import(session_factory, storage=storage, job_id=identifier))
            except OpeningImportWorkerSupervisionError:
                # A child that might still be running forbids the next job.
                raise
            except Exception:
                emit({"ok": False, "job_id": str(identifier),
                      "code": "opening_import_worker_retry_or_review_required"})
            else:
                emit({"ok": result.status in {"idle", "awaiting_confirmation", "succeeded"},
                      "job_id": str(result.job_id) if result.job_id else None,
                      "status": result.status, "recovered": result.recovered})
            if not stop_event.is_set():
                sweep_due()
        if stop_event.is_set():
            return
        if not identifiers:
            emit({"ok": True, "job_id": None, "status": "idle", "recovered": False})
        sweep_due()
        # This also bounds retries when the only remaining job is unresolved.
        # Queue/sweep discovery errors propagate to CLI supervision; per-job
        # errors remain isolated and never log exception text or source cells.
        stop_event.wait(poll_seconds)


def main(argv=None):
    import argparse
    import json
    import signal
    from threading import Event
    from .opening_import_worker_entry import (
        OpeningImportWorkerSupervisionError, process_owned_opening_import,
    )

    def emit(document):
        print(json.dumps(document, sort_keys=True, separators=(",", ":")), flush=True)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=UUID)
    parser.add_argument("--poll-seconds", type=int)
    args = parser.parse_args(argv)
    if args.poll_seconds is not None and (args.job_id is not None or not 5 <= args.poll_seconds <= 300):
        emit({"ok": False, "code": "opening_import_worker_poll_invalid"})
        return 2
    try:
        settings = get_settings()
        ready = (settings.environment in {"staging", "production"}
                 and settings.database_url.startswith("postgresql+psycopg://")
                 and settings.opening_count_import_enabled and settings.file_storage_configuration_ready())
    except Exception:
        ready = False
    if not ready:
        emit({"ok": False, "code": "opening_import_worker_not_configured"})
        return 2
    try:
        from .database import engine
        from .opening_import_worker_database import opening_import_worker_session_factory
        from .database_security import validate_production_database_security

        validate_production_database_security(engine,
            expected_runtime_role=settings.database_expected_runtime_role,
            expected_migration_role=settings.database_expected_migration_role)
        session_factory = opening_import_worker_session_factory(engine)
        stop = Event()
        if args.poll_seconds is not None:
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            _poll_opening_imports(session_factory, storage=None, poll_seconds=args.poll_seconds,
                                 stop_event=stop, emit=emit, process_job=process_owned_opening_import)
            return 0
        identifier = args.job_id
        if identifier is None:
            identifiers = _opening_import_queue_page(session_factory, limit=1)
            identifier = identifiers[0] if identifiers else None
        result = (process_owned_opening_import(identifier) if identifier is not None
                  else OpeningImportWorkerResult(None, "idle"))
        emit({"ok": result.status in {"idle", "awaiting_confirmation", "succeeded"},
              "job_id": str(result.job_id) if result.job_id else None,
              "status": result.status, "recovered": result.recovered})
        return 0 if result.status in {"idle", "awaiting_confirmation", "succeeded"} else 2
    except OpeningImportWorkerSupervisionError as exc:
        # Retain only an owned OS process identifier for operational recovery.
        emit({"ok": False, "code": "opening_import_worker_supervision_failed",
              "worker_pid": exc.worker_pid})
        return 2
    except Exception:
        # No raw database parameters, source cells, object URLs or credentials.
        # Unknown transport results are retained, never retried as a new PUT.
        emit({"ok": False, "code": "opening_import_worker_retry_or_review_required"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
