"""Commit prevalidation evidence; never execute the formal count command."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal
from .audit_chain import append_audit_event
from .opening_count_import_document import dump_import_preview
from .opening_count_import_error_jobs import PreparedOpeningCountError, prepare_persisted_opening_count_error
from .opening_count_import_intake import _request_id
from .opening_count_import_jobs import OpeningCountImportJobError, _lock_owned_import_job
from .opening_count_import_prevalidation import prevalidate_opening_count_import_bytes
from .opening_count_import_source import AuthorizedOpeningCountSource


@dataclass(frozen=True, slots=True)
class OpeningImportPrevalidationResult:
    job_id: UUID
    status: str
    error: PreparedOpeningCountError | None = None
    recovered: bool = False


def claim_opening_count_import(
    db: Session, *, actor: FormalPrincipal, job_id: UUID, request_id: str,
) -> OpeningImportPrevalidationResult:
    _request_id(request_id)
    job, _, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.status != "queued":
        return OpeningImportPrevalidationResult(job.id, job.status, recovered=True)
    now = db.scalar(text("SELECT clock_timestamp()"))
    job.status = "prevalidating"
    job.started_at = now
    db.flush()
    append_audit_event(db, stream_key="inventory", actor_user_id=current.user_id,
        action="opening_count_import_prevalidation_started", aggregate_type="file_job",
        aggregate_id=str(job.id), before_jsonb={"status": "queued"},
        after_jsonb={"status": job.status}, request_id=request_id, occurred_at=now, created_at=now)
    return OpeningImportPrevalidationResult(job.id, job.status)


def prevalidate_persisted_opening_count_import(
    db: Session, *, actor: FormalPrincipal, job_id: UUID,
    source: AuthorizedOpeningCountSource, request_id: str,
) -> OpeningImportPrevalidationResult:
    _request_id(request_id)
    job, binding, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.status in {"awaiting_confirmation", "succeeded", "failed", "cancelled"}:
        return OpeningImportPrevalidationResult(job.id, job.status, recovered=True)
    if job.status != "prevalidating":
        raise OpeningCountImportJobError("opening_import_not_prevalidating", 409,
                                        "导入任务不在预校验阶段")
    if (type(source) is not AuthorizedOpeningCountSource or source.file_id != binding.source_file_id
            or source.source_sha256 != binding.source_sha256 or not isinstance(source.data, bytes)
            or source.size_bytes != len(source.data)):
        raise OpeningCountImportJobError("opening_import_source_changed", 412, "原私有源文件已变化")
    if job.import_error_sha256 is not None:
        error = prepare_persisted_opening_count_error(db, actor=current, job_id=job_id,
            source=source, request_id=request_id)
        return OpeningImportPrevalidationResult(job.id, job.status, error, True)
    preview = prevalidate_opening_count_import_bytes(db, actor=current, data=source.data,
        expected_source_sha256=binding.source_sha256, task_id=binding.task_id,
        round_id=binding.round_id, scope_id=binding.scope_id,
        idempotency_key=job.idempotency_key, request_id=request_id)
    if not preview.ready:
        error = prepare_persisted_opening_count_error(db, actor=current, job_id=job_id,
            source=source, request_id=request_id)
        return OpeningImportPrevalidationResult(job.id, job.status, error)
    job.import_preview_jsonb = dump_import_preview(preview, binding=binding)
    job.status = "awaiting_confirmation"
    db.flush()
    now = db.scalar(text("SELECT clock_timestamp()"))
    append_audit_event(db, stream_key="inventory", actor_user_id=current.user_id,
        action="opening_count_import_prevalidated", aggregate_type="file_job", aggregate_id=str(job.id),
        before_jsonb={"status": "prevalidating"},
        after_jsonb={"status": job.status, "row_count": preview.row_count,
                     "payload_sha256": preview.payload_sha256},
        request_id=request_id, occurred_at=now, created_at=now)
    return OpeningImportPrevalidationResult(job.id, job.status)
