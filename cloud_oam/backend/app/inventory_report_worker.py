"""Run bounded, recoverable inventory report export jobs.

Each processing transaction handles at most one job. A running job is selected
before a new queued job so a lost OSS or database acknowledgement can be
reread by the same deterministic file key. No credentials or signed URLs are
printed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import signal
from threading import Event
from typing import TYPE_CHECKING, Callable
import uuid

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from .config import get_settings

if TYPE_CHECKING:
    from .formal_services.file_storage import FileStorageAdapter


@dataclass(frozen=True, slots=True)
class WorkerResult:
    job_id: uuid.UUID | None
    status: str
    recovered: bool = False


def process_one_inventory_report(
    session_factory: Callable[[], Session],
    *,
    storage: FileStorageAdapter,
    job_id: uuid.UUID | None = None,
) -> WorkerResult:
    """Commit claim before OSS; commit exact file, job result and audit together."""

    from .foundation_models import FileJob
    from .formal_services.inventory_report_jobs import claim_inventory_report_job, finish_inventory_report_job
    from .formal_services.inventory_query import InventoryReadError

    with session_factory() as db:
        if job_id is None:
            candidate = db.scalar(
                select(FileJob.id)
                .where(FileJob.job_type == "export", FileJob.status.in_(("running", "queued")))
                .order_by(
                    case((FileJob.status == "running", 0), else_=1),
                    FileJob.created_at,
                    FileJob.id,
                )
                .limit(1)
            )
        else:
            candidate = job_id
        if candidate is None:
            return WorkerResult(job_id=None, status="idle")
        selected_status = db.scalar(select(FileJob.status).where(FileJob.id == candidate))
        allow_new_put = selected_status == "queued"
        if selected_status == "queued":
            claim = claim_inventory_report_job(
                db, job_id=candidate, request_id=f"inventory-report-claim:{candidate}",
            )
            db.commit()
            if claim.status == "failed":
                return WorkerResult(job_id=candidate, status="failed")
        elif selected_status != "running" and not (job_id is not None and selected_status == "succeeded"):
            raise InventoryReadError(
                code="inventory_report_job_not_runnable",
                status_code=409,
                message="报表任务不在待处理或恢复状态",
            )

    with session_factory() as db:
        job, recovered = finish_inventory_report_job(
            db,
            job_id=candidate,
            storage=storage,
            request_id=f"inventory-report-finish:{candidate}",
            allow_new_put=allow_new_put,
        )
        db.commit()
        return WorkerResult(job_id=candidate, status=job.status, recovered=recovered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=uuid.UUID)
    parser.add_argument("--poll-seconds", type=int)
    args = parser.parse_args(argv)
    if args.poll_seconds is not None and (
        args.job_id is not None or not 5 <= args.poll_seconds <= 300
    ):
        _emit({"ok": False, "code": "inventory_report_worker_poll_invalid"})
        return 2
    try:
        settings = get_settings()
    except Exception:
        _emit({"ok": False, "code": "inventory_report_worker_not_configured"})
        return 2
    if (
        settings.environment not in {"staging", "production"}
        or not settings.database_url.startswith("postgresql+psycopg://")
        or not settings.inventory_report_export_enabled
        or not settings.file_storage_configuration_ready()
    ):
        _emit({"ok": False, "code": "inventory_report_worker_not_configured"})
        return 2
    inventory_error_type: type[Exception] | None = None
    storage_error_type: type[Exception] | None = None
    try:
        from .database import SessionLocal, engine
        from .database_security import validate_production_database_security
        from .formal_services.file_storage import AliyunOssV2StorageAdapter, FileStorageError
        from .formal_services.inventory_query import InventoryReadError

        inventory_error_type = InventoryReadError
        storage_error_type = FileStorageError
        validate_production_database_security(
            engine,
            expected_runtime_role=settings.database_expected_runtime_role,
            expected_migration_role=settings.database_expected_migration_role,
        )

        storage = AliyunOssV2StorageAdapter(
            region=settings.file_storage_region.strip(),
            bucket=settings.file_storage_bucket.strip(),
        )
        if args.poll_seconds is not None:
            stop_event = Event()

            def stop(_number: int, _frame: object) -> None:
                stop_event.set()

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            _poll_inventory_reports(
                SessionLocal, storage=storage,
                poll_seconds=args.poll_seconds, stop_event=stop_event,
            )
            return 0
        result = process_one_inventory_report(
            SessionLocal, storage=storage, job_id=args.job_id,
        )
    except Exception as exc:
        code = (
            "inventory_report_worker_retry_or_review_required"
            if (storage_error_type is not None and isinstance(exc, storage_error_type))
            or (inventory_error_type is not None and isinstance(exc, inventory_error_type))
            else "inventory_report_worker_failed"
        )
        _emit({"ok": False, "code": code})
        return 2
    _emit({
        "ok": result.status in {"idle", "succeeded"},
        "job_id": str(result.job_id) if result.job_id is not None else None,
        "status": result.status,
        "recovered": result.recovered,
    })
    return 0 if result.status in {"idle", "succeeded"} else 2


def _poll_inventory_reports(
    session_factory: Callable[[], Session],
    *,
    storage: FileStorageAdapter,
    poll_seconds: int,
    stop_event: Event,
) -> None:
    """Wait only when idle; fail out of the supervisor on uncertain results."""

    while not stop_event.is_set():
        result = process_one_inventory_report(session_factory, storage=storage)
        if result.status == "idle":
            stop_event.wait(poll_seconds)
            continue
        if result.status not in {"succeeded", "failed"}:
            raise RuntimeError("inventory report worker returned an unknown state")
        _emit({
            "ok": result.status == "succeeded",
            "job_id": str(result.job_id),
            "status": result.status,
            "recovered": result.recovered,
        })


def _emit(document: dict[str, object]) -> None:
    print(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
