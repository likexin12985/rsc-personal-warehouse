"""Fixed owned-process boundary for one original opening-import job.

No credentials, workbook bytes, source paths or selectable functions cross the
result pipe. Unknown completion is recovered by the original persisted job.
"""
from uuid import UUID

from .daily_reconciliation.process_entry import (
    ProcessEntryError, ProcessOutcomeUnknown, run_owned_job,
)

OPENING_IMPORT_JOB_MAXIMUM_SECONDS = 60
_RESULT_STATUSES = frozenset({"queued", "prevalidating", "awaiting_confirmation", "succeeded", "failed", "cancelled"})


class OpeningImportWorkerSupervisionError(RuntimeError):
    """Stop scheduling when owned-child cleanup or supervision is unproved."""
    def __init__(self, worker_pid=None):
        super().__init__("opening_import_worker_supervision_failed")
        self.worker_pid = worker_pid


def _opening_import_job_worker(payload, expires):
    """Spawn target; all actual connections and object clients belong here."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from .config import get_settings
    from .database_security import validate_production_database_security
    from .formal_services.file_storage import AliyunOssV2StorageAdapter
    from .opening_import_worker_database import opening_import_worker_session_factory
    from .opening_count_import_worker import process_one_opening_count_import

    if type(payload) is not dict or set(payload) != {"job_id"}:
        raise ProcessEntryError("opening_import_job_payload_invalid")
    identifier = UUID(payload["job_id"])
    if str(identifier) != payload["job_id"]:
        raise ProcessEntryError("opening_import_job_payload_invalid")
    settings = get_settings()
    if (settings.environment not in {"staging", "production"}
            or not settings.database_url.startswith("postgresql+psycopg://")
            or not settings.opening_count_import_enabled
            or not settings.file_storage_configuration_ready()):
        raise ProcessEntryError("opening_import_worker_not_configured")
    # Never inherit an API connection or a parent SDK client across spawn.
    # The process supervisor also includes DNS, SDK streaming and parsing in
    # its deadline; SQL-local budgets alone do not cover those phases.
    engine = create_engine(settings.database_url, poolclass=NullPool, hide_parameters=True,
                           connect_args={"connect_timeout": 5, "tcp_user_timeout": 1000})
    try:
        validate_production_database_security(engine,
            expected_runtime_role=settings.database_expected_runtime_role,
            expected_migration_role=settings.database_expected_migration_role)
        factory = opening_import_worker_session_factory(engine)
        storage = AliyunOssV2StorageAdapter(region=settings.file_storage_region.strip(),
                                          bucket=settings.file_storage_bucket.strip())
        result = process_one_opening_count_import(factory, storage=storage, job_id=identifier)
        return {"job_id": str(result.job_id), "status": result.status, "recovered": result.recovered}
    finally:
        engine.dispose()


def process_owned_opening_import(job_id, *, maximum_seconds=OPENING_IMPORT_JOB_MAXIMUM_SECONDS):
    """Return only a validated observed result; never replay an unknown call."""
    from .opening_count_import_worker import OpeningImportWorkerResult
    if type(job_id) is not UUID:
        raise ValueError("opening_import_job_id_invalid")
    try:
        result = run_owned_job(_opening_import_job_worker, {"job_id": str(job_id)},
                               maximum_seconds=maximum_seconds)
    except ProcessOutcomeUnknown:
        # run_owned_job guarantees that its child was reaped on this branch.
        # An unconfirmed cleanup raises ProcessEntryError instead and MUST
        # escape the per-job isolation catch to stop the parent scheduler.
        raise
    except Exception as exc:
        # Only ProcessOutcomeUnknown proves that the executor reaped its
        # child. OS/process cleanup errors can otherwise escape as ordinary
        # exceptions; they must never be treated as an isolated job failure.
        prefix = "worker_cleanup_unconfirmed_pid_"
        suffix = str(exc).removeprefix(prefix)
        pid = int(suffix) if str(exc).startswith(prefix) and suffix.isdecimal() else None
        raise OpeningImportWorkerSupervisionError(pid) from None
    if (type(result) is not dict or set(result) != {"job_id", "status", "recovered"}
            or result["job_id"] != str(job_id) or type(result["status"]) is not str
            or result["status"] not in _RESULT_STATUSES
            or type(result["recovered"]) is not bool):
        raise ProcessOutcomeUnknown("opening_import_worker_result_unknown")
    return OpeningImportWorkerResult(job_id, result["status"], result["recovered"])
