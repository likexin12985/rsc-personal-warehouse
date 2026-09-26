"""Persist error evidence before I/O and publish only its verified private object.

Callers commit preparation before issuing the first PUT. A retry obtains only
HEAD recovery permission. Publication is a separate, newly authorized database
transaction; neither entry point commits or performs network I/O itself.
"""

from dataclasses import dataclass
from hashlib import md5, sha256
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from formal_file_integrity import (
    FILE_METADATA_SCHEMA, OPENING_IMPORT_ERROR_MAX_BYTES, FileUploadIntentInput,
    StoredObjectHead, _head_manifest_sha256, _prepare_upload,
    _upload_request_hash, _validate_object_head,
)

from ..formal_access import FormalPrincipal
from ..foundation_models import FileObject
from .audit_chain import append_audit_event
from .opening_count_import_document import dump_import_preview
from .opening_count_import_error_report import (
    MIME, PURPOSE, OpeningCountErrorArtifact, build_opening_count_error_artifact,
)
from .opening_count_import_jobs import OpeningCountImportJobError, _lock_owned_import_job
from .opening_count_import_prevalidation import prevalidate_opening_count_import_bytes
from .opening_count_import_source import AuthorizedOpeningCountSource


@dataclass(frozen=True, slots=True)
class PreparedOpeningCountError:
    artifact: OpeningCountErrorArtifact
    new_write_allowed: bool


def _artifact(job):
    artifact = build_opening_count_error_artifact(job_id=job.id,
        binding_document=job.import_binding_jsonb, preview_document=job.import_preview_jsonb)
    if (job.import_error_sha256 != artifact.sha256
            or job.import_error_size_bytes != len(artifact.payload)):
        raise OpeningCountImportJobError("opening_import_error_artifact_changed", 412,
                                        "错误报告内容与原任务不一致")
    return artifact


def _audit(db, *, actor, job, action, detail, request_id):
    now = db.scalar(text("SELECT clock_timestamp()"))
    append_audit_event(db, stream_key="inventory", actor_user_id=actor.user_id,
        action=action, aggregate_type="file_job", aggregate_id=str(job.id),
        before_jsonb=None, after_jsonb=detail, request_id=request_id,
        occurred_at=now, created_at=now)


def prepare_persisted_opening_count_error(
    db: Session, *, actor: FormalPrincipal, job_id: UUID,
    source: AuthorizedOpeningCountSource, request_id: str,
) -> PreparedOpeningCountError:
    """Generate errors from the exact source, never from browser-supplied rows."""
    job, binding, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.status != "prevalidating":
        raise OpeningCountImportJobError("opening_import_not_prevalidating", 409,
                                        "导入任务不在预校验阶段")
    if (type(source) is not AuthorizedOpeningCountSource
            or source.file_id != binding.source_file_id
            or source.source_sha256 != binding.source_sha256
            or not isinstance(source.data, bytes) or source.size_bytes != len(source.data)
            or sha256(source.data).hexdigest() != binding.source_sha256):
        raise OpeningCountImportJobError("opening_import_source_changed", 412,
                                        "当前私有源文件与任务绑定不一致")
    if job.import_error_sha256 is not None:
        return PreparedOpeningCountError(_artifact(job), False)
    preview = prevalidate_opening_count_import_bytes(db, actor=current,
        data=source.data, expected_source_sha256=binding.source_sha256,
        task_id=binding.task_id, round_id=binding.round_id, scope_id=binding.scope_id,
        idempotency_key=job.idempotency_key, request_id=request_id)
    if preview.ready or not preview.errors:
        raise OpeningCountImportJobError("opening_import_has_no_errors", 409,
                                        "预校验没有行错误，不能生成错误报告")
    document = dump_import_preview(preview, binding=binding)
    artifact = build_opening_count_error_artifact(job_id=job.id,
        binding_document=job.import_binding_jsonb, preview_document=document)
    job.import_preview_jsonb = document
    job.import_error_sha256 = artifact.sha256
    job.import_error_size_bytes = len(artifact.payload)
    db.flush()
    _audit(db, actor=current, job=job, action="opening_count_import_error_prepared",
        detail={"preview_sha256": artifact.preview_sha256,
                "error_sha256": artifact.sha256, "error_file_id": str(artifact.file_id)},
        request_id=request_id)
    return PreparedOpeningCountError(artifact, True)


def publish_persisted_opening_count_error(
    db: Session, *, actor: FormalPrincipal, job_id: UUID,
    head: StoredObjectHead, request_id: str,
) -> UUID:
    """Bind validated HEAD evidence and the failed job atomically, without PUT."""
    job, binding, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.status != "prevalidating" or job.import_error_sha256 is None:
        raise OpeningCountImportJobError("opening_import_error_not_prepared", 409,
                                        "请读取原任务结果，不能重复发布错误报告")
    artifact = _artifact(job)
    prepared = _prepare_upload(FileUploadIntentInput(purpose=PURPOSE,
        original_filename=artifact.filename, size_bytes=len(artifact.payload),
        mime_type=MIME, sha256=artifact.sha256), maximum_size_bytes=OPENING_IMPORT_ERROR_MAX_BYTES)
    now = db.scalar(text("SELECT transaction_timestamp()"))
    file = FileObject(id=artifact.file_id, storage_key=artifact.storage_key,
        sha256=artifact.sha256, size_bytes=len(artifact.payload), mime_type=MIME,
        original_filename=artifact.filename, uploaded_by=current.user_id, status="pending",
        created_at=now, metadata_jsonb={
            "authorization_version": binding.authorization_version,
            "file_id": str(artifact.file_id), "idempotency_key_hash": job.idempotency_key,
            "provider": "aliyun_oss_v2", "purpose": PURPOSE,
            "request_sha256": _upload_request_hash(prepared), "schema": FILE_METADATA_SCHEMA,
            "storage_key": artifact.storage_key, "uploader_person_id": str(current.person_id),
            "uploader_user_id": current.user_id,
        })
    _validate_object_head(file, head)
    if head.etag.strip('"').lower() != md5(artifact.payload).hexdigest():
        raise OpeningCountImportJobError("opening_import_error_head_changed", 412,
                                        "对象存储回读与原错误报告不一致")
    db.add(file)
    db.flush()
    file.metadata_jsonb = {**file.metadata_jsonb, "completion": {
        "etag_sha256": sha256(head.etag.encode()).hexdigest(),
        "head_manifest_sha256": _head_manifest_sha256(head), "verified_at": now.isoformat(),
    }}
    file.status = "available"
    db.flush()
    job.status = "failed"
    job.error_file_id = file.id
    job.error_detail = "opening_import_prevalidation_failed"
    job.completed_at = db.scalar(text("SELECT clock_timestamp()"))
    db.flush()
    _audit(db, actor=current, job=job, action="opening_count_import_error_published",
        detail={"status": job.status, "error_file_id": str(file.id),
                "error_sha256": artifact.sha256}, request_id=request_id)
    return file.id
