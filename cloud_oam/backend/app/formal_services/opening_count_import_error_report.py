"""Deterministic private error artifacts from a persisted, rejected preview.

This module does not grant access or publish a result. The job worker must
authorize the exact job before calling it, persist write-attempt state before
PUT, and revalidate authority when binding the verified object to the job.
"""

from dataclasses import dataclass
from hashlib import md5, sha256
import json
from uuid import UUID, uuid5

from formal_file_integrity import (
    OPENING_IMPORT_ERROR_MAX_BYTES, FileUploadIntentInput, StoredObjectHead,
    _prepare_upload, _storage_key, _validate_object_head,
)

from ..foundation_models import FileObject
from .file_storage import FileStorageAdapter, FileStorageError
from .opening_count_import_document import load_import_binding, load_import_preview
from .opening_count_import_workbook import render_opening_count_error_report


PURPOSE = "opening_count_import_error"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_NAMESPACE = UUID("f02933cb-8faa-4be4-b4c4-32e1681d8d17")


@dataclass(frozen=True, slots=True)
class OpeningCountErrorArtifact:
    job_id: UUID
    file_id: UUID
    storage_key: str
    filename: str
    preview_sha256: str
    sha256: str
    payload: bytes


def build_opening_count_error_artifact(
    *, job_id: UUID, binding_document: dict, preview_document: dict,
) -> OpeningCountErrorArtifact:
    """Retain source row coordinates but never echo raw uploaded cells."""
    if not isinstance(job_id, UUID) or job_id.int == 0:
        raise ValueError("导入任务标识无效")
    binding = load_import_binding(binding_document)
    preview = load_import_preview(preview_document, binding=binding)
    if preview.ready or not preview.errors:
        raise ValueError("导入预检没有可生成报告的错误")
    payload = render_opening_count_error_report(preview.errors)
    digest = sha256(payload).hexdigest()
    file_id = uuid5(_NAMESPACE, str(job_id))
    filename = f"RSC_import_errors_{job_id.hex}.xlsx"
    _prepare_upload(FileUploadIntentInput(purpose=PURPOSE,
        original_filename=filename, size_bytes=len(payload), mime_type=MIME, sha256=digest),
        maximum_size_bytes=OPENING_IMPORT_ERROR_MAX_BYTES)
    document_digest = sha256(json.dumps(preview_document, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return OpeningCountErrorArtifact(job_id, file_id, _storage_key(PURPOSE, file_id),
                                     filename, document_digest, digest, payload)


def write_or_recover_opening_count_error(
    storage: FileStorageAdapter, *, artifact: OpeningCountErrorArtifact,
    allow_new_put: bool,
) -> StoredObjectHead:
    """A recovery reads the exact original object and never repeats its PUT."""
    if (type(allow_new_put) is not bool or type(artifact) is not OpeningCountErrorArtifact
            or not isinstance(artifact.job_id, UUID)
            or artifact.file_id != uuid5(_NAMESPACE, str(artifact.job_id))
            or artifact.storage_key != _storage_key(PURPOSE, artifact.file_id)
            or not isinstance(artifact.payload, bytes)
            or not 0 < len(artifact.payload) <= OPENING_IMPORT_ERROR_MAX_BYTES
            or sha256(artifact.payload).hexdigest() != artifact.sha256
            or getattr(storage, "provider_code", None) != "aliyun_oss_v2"):
        raise FileStorageError("import error artifact or private storage is invalid")
    if allow_new_put:
        head = storage.put_opening_count_error(storage_key=artifact.storage_key,
            file_id=str(artifact.file_id), sha256=artifact.sha256, payload=artifact.payload)
    else:
        head = storage.head_object(storage_key=artifact.storage_key)
    # Reuse the strict metadata check, including duplicate normalized keys.
    reference = FileObject(id=artifact.file_id, storage_key=artifact.storage_key,
        sha256=artifact.sha256, size_bytes=len(artifact.payload), mime_type=MIME)
    _validate_object_head(reference, head)
    if head.etag.strip('"').lower() != md5(artifact.payload).hexdigest():
        raise FileStorageError("import error object does not match the original single PUT")
    return head
