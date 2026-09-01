"""Formal V1 attachment upload, verification and scoped download service.

Only short-lived object-storage intents cross this boundary.  The caller owns
the database transaction; these functions flush but never commit or roll back,
never create notification/outbox facts and never call a business system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import unicodedata
from typing import Any, Final, Mapping, Sequence
from urllib.parse import urlsplit
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..demand_models import (
    ApprovalExternalRegistration,
    ApprovalInstance,
    ApprovalStep,
    MaterialRequestFile,
)
from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import DocumentAttachment, FileObject
from ..stocktake_models import StocktakeScopeCountCompletion
from .audit_chain import AuditChainError, append_audit_event
from .file_storage import (
    DownloadIntent,
    FileStorageAdapter,
    FileStorageError,
    StoredObjectHead,
    UploadIntent,
)
from . import material_request_query, stocktake_query


FILE_METADATA_SCHEMA: Final[str] = "cloud_oam.formal_file_upload_intent.v1"
FILE_AGGREGATE_TYPE: Final[str] = "formal_file"
PURPOSES: Final[frozenset[str]] = frozenset(
    {
        "request_attachment",
        "external_approval_evidence",
        "stocktake_evidence",
    }
)
_PERMISSION_BY_PURPOSE: Final[dict[str, tuple[str, str, str]]] = {
    "request_attachment": ("material_request", "create", ""),
    "external_approval_evidence": (
        "material_request",
        "register_external",
        "approval_evidence",
    ),
    "stocktake_evidence": ("stocktake", "count", ""),
}
_AUDIT_STREAM_BY_PURPOSE: Final[dict[str, str]] = {
    "request_attachment": "material_request",
    "external_approval_evidence": "material_request",
    "stocktake_evidence": "inventory",
}
_ALLOWED_MIME_EXTENSIONS: Final[dict[str, frozenset[str]]] = {
    "application/pdf": frozenset({".pdf"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
    "image/webp": frozenset({".webp"}),
    "image/heic": frozenset({".heic"}),
    "image/heif": frozenset({".heif"}),
    "video/mp4": frozenset({".mp4"}),
    "video/quicktime": frozenset({".mov"}),
}
_SAFE_TRACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII)
_SAFE_IDEMPOTENCY = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STORAGE_KEY = re.compile(
    r"^formal-files/v1/(request_attachment|external_approval_evidence|stocktake_evidence)/[0-9a-f]{2}/[0-9a-f]{32}$"
)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")
_FORBIDDEN_FILENAME_CODEPOINTS = frozenset(
    {
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class FormalFileError(RuntimeError):
    """Stable file failure without SDK, storage URL or database detail."""

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported file error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class FileUploadIntentInput:
    purpose: str
    original_filename: str
    size_bytes: int
    mime_type: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FileUploadIntentResult:
    file_id: uuid.UUID
    purpose: str
    status: str
    upload: UploadIntent | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class FileCompleteResult:
    file_id: uuid.UUID
    purpose: str
    verified_at: datetime
    already_available: bool


@dataclass(frozen=True, slots=True)
class FileDownloadIntentResult:
    file_id: uuid.UUID
    purpose: str
    download: DownloadIntent


@dataclass(frozen=True, slots=True)
class _PreparedUpload:
    purpose: str
    original_filename: str
    size_bytes: int
    mime_type: str
    sha256: str


def is_available_formal_file_for_purpose(
    row: object,
    *,
    purpose: str,
    uploader_user_id: str | None = None,
) -> bool:
    """Check that a file is an exact completed intent for one formal purpose.

    Binding services call this before creating their own immutable attachment
    facts.  A merely ``available`` legacy file must never be relabelled as
    demand, approval or stocktake evidence.
    """

    if not isinstance(row, FileObject) or purpose not in PURPOSES:
        return False
    try:
        metadata = _validate_intent_metadata(row, allow_completed=True)
    except FormalFileError:
        return False
    return bool(
        row.status == "available"
        and metadata.get("purpose") == purpose
        and "completion" in metadata
        and (
            uploader_user_id is None
            or (
                row.uploaded_by == uploader_user_id
                and metadata.get("uploader_user_id") == uploader_user_id
            )
        )
    )


def create_file_upload_intent(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: FileUploadIntentInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
    storage: FileStorageAdapter,
    upload_ttl_seconds: int,
    maximum_size_bytes: int = 120 * 1024 * 1024,
) -> FileUploadIntentResult:
    _require_storage_provider(storage)
    maximum_size = _require_maximum_size(maximum_size_bytes)
    prepared = _prepare_upload(command, maximum_size_bytes=maximum_size)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    trace_id = _require_trace_request_id(trace_request_id)
    ttl = _require_ttl(upload_ttl_seconds, minimum=60, maximum=900)
    supplied = _require_supplied_principal(actor)
    key_hash = _domain_hmac(
        secret,
        "formal-file-upload-idempotency-v1",
        supplied.user_id,
        raw_key,
    )
    file_id = _uuid_from_digest(
        hmac.new(
            secret,
            (
                "formal-file-upload-id-v1\0"
                f"{supplied.user_id}\0{raw_key}"
            ).encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )
    storage_key = _storage_key(prepared.purpose, file_id)
    request_hash = _upload_request_hash(prepared)

    _take_file_advisory_lock(db, file_id)
    current = _current_principal(db, supplied, lock=True)
    _require_upload_permission(db, current, prepared.purpose)
    row = db.scalar(
        select(FileObject)
        .where(FileObject.id == file_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is not None:
        _validate_file_row(row)
        metadata = _validate_intent_metadata(row, allow_completed=True)
        if metadata["provider"] != storage.provider_code:
            _storage_provider_mismatch()
        _require_uploader_continuity(row, metadata, current)
        if (
            row.uploaded_by != current.user_id
            or metadata["idempotency_key_hash"] != key_hash
            or metadata["request_sha256"] != request_hash
            or row.storage_key != storage_key
            or row.original_filename != prepared.original_filename
            or row.sha256 != prepared.sha256
            or row.size_bytes != prepared.size_bytes
            or row.mime_type != prepared.mime_type
            or metadata["purpose"] != prepared.purpose
        ):
            _fail(
                "file_upload_idempotency_conflict",
                "conflict",
                "上传幂等键已用于不同请求",
            )
        if row.status not in {"pending", "available"}:
            _fail("file_upload_state_conflict", "conflict", "文件当前状态不可上传")
        upload = None
        if row.status == "pending":
            upload = _create_and_validate_upload_intent(
                storage,
                row=row,
                ttl_seconds=ttl,
                now=_database_now(db),
            )
        now = _database_now(db)
        _append_file_audit(
            db,
            row=row,
            actor=current,
            action="file.upload_intent.replayed",
            request_id=trace_id,
            occurred_at=now,
            after={"status": row.status},
        )
        return FileUploadIntentResult(
            file_id=row.id,
            purpose=prepared.purpose,
            status=row.status,
            upload=upload,
            replayed=True,
        )

    now = _database_now(db)
    provisional = FileObject(
        id=file_id,
        storage_key=storage_key,
        sha256=prepared.sha256,
        size_bytes=prepared.size_bytes,
        mime_type=prepared.mime_type,
        original_filename=prepared.original_filename,
        uploaded_by=current.user_id,
        status="pending",
        metadata_jsonb=_pending_metadata(
            file_id=file_id,
            storage_key=storage_key,
            prepared=prepared,
            actor=current,
            key_hash=key_hash,
            request_hash=request_hash,
            provider_code=storage.provider_code,
        ),
        created_at=now,
    )
    upload = _create_and_validate_upload_intent(
        storage,
        row=provisional,
        ttl_seconds=ttl,
        now=now,
    )
    db.add(provisional)
    db.flush()
    _append_file_audit(
        db,
        row=provisional,
        actor=current,
        action="file.upload_intent.created",
        request_id=trace_id,
        occurred_at=now,
        after={"status": "pending"},
    )
    return FileUploadIntentResult(
        file_id=provisional.id,
        purpose=prepared.purpose,
        status="pending",
        upload=upload,
        replayed=False,
    )


def complete_file_upload(
    db: Session,
    *,
    actor: FormalPrincipal,
    file_id: uuid.UUID,
    trace_request_id: str,
    storage: FileStorageAdapter,
) -> FileCompleteResult:
    _require_storage_provider(storage)
    identifier = _require_uuid("file_id", file_id)
    trace_id = _require_trace_request_id(trace_request_id)
    supplied = _require_supplied_principal(actor)
    _take_file_advisory_lock(db, identifier)
    current = _current_principal(db, supplied, lock=True)
    row = _lock_file(db, identifier)
    metadata = _validate_intent_metadata(row, allow_completed=True)
    if metadata["provider"] != storage.provider_code:
        _storage_provider_mismatch()
    purpose = str(metadata["purpose"])
    if row.uploaded_by != current.user_id:
        _fail("file_upload_forbidden", "forbidden", "当前账号不能完成该文件上传")
    _require_uploader_continuity(row, metadata, current)
    _require_upload_permission(db, current, purpose)
    if row.status not in {"pending", "available"}:
        _fail("file_complete_state_conflict", "conflict", "文件当前状态不能完成上传")

    try:
        head = storage.head_object(storage_key=row.storage_key)
    except FileStorageError:
        _fail("file_storage_unavailable", "service_unavailable", "文件存储暂不可用")
    _validate_object_head(row, head)
    now = _database_now(db)
    already_available = row.status == "available"
    etag_sha256 = hashlib.sha256(head.etag.encode("utf-8")).hexdigest()
    head_sha256 = _head_manifest_sha256(head)
    if already_available:
        completion = metadata.get("completion")
        if not isinstance(completion, Mapping) or set(completion) != {
            "etag_sha256",
            "head_manifest_sha256",
            "verified_at",
        }:
            _fail(
                "file_completion_evidence_invalid",
                "service_unavailable",
                "文件完成证据无效",
            )
        if (
            completion.get("etag_sha256") != etag_sha256
            or completion.get("head_manifest_sha256") != head_sha256
        ):
            _fail(
                "file_object_changed_after_completion",
                "conflict",
                "文件对象在完成后发生变化",
            )
    else:
        row.status = "available"
        row.metadata_jsonb = {
            **metadata,
            "completion": {
                "etag_sha256": etag_sha256,
                "head_manifest_sha256": head_sha256,
                "verified_at": now.isoformat(),
            },
        }
        db.flush()
    _append_file_audit(
        db,
        row=row,
        actor=current,
        action=(
            "file.upload_completion.reverified"
            if already_available
            else "file.upload_completed"
        ),
        request_id=trace_id,
        occurred_at=now,
        before={"status": "available" if already_available else "pending"},
        after={
            "head_manifest_sha256": head_sha256,
            "status": "available",
        },
    )
    return FileCompleteResult(
        file_id=row.id,
        purpose=purpose,
        verified_at=now,
        already_available=already_available,
    )


def create_file_download_intent(
    db: Session,
    *,
    actor: FormalPrincipal,
    file_id: uuid.UUID,
    trace_request_id: str,
    storage: FileStorageAdapter,
    download_ttl_seconds: int,
) -> FileDownloadIntentResult:
    _require_storage_provider(storage)
    identifier = _require_uuid("file_id", file_id)
    trace_id = _require_trace_request_id(trace_request_id)
    ttl = _require_ttl(download_ttl_seconds, minimum=30, maximum=600)
    supplied = _require_supplied_principal(actor)
    _take_file_advisory_lock(db, identifier)
    current = _current_principal(db, supplied, lock=True)
    row = _lock_file(db, identifier)
    metadata = _validate_intent_metadata(row, allow_completed=True)
    if metadata["provider"] != storage.provider_code:
        _storage_provider_mismatch()
    purpose = str(metadata["purpose"])
    if row.status != "available" or "completion" not in metadata:
        _fail("file_download_not_available", "not_found", "文件不存在或尚不可下载")
    binding_summary = _authorize_download(db, actor=current, row=row, purpose=purpose)
    try:
        intent = storage.create_download_intent(
            storage_key=row.storage_key,
            ttl_seconds=ttl,
        )
    except FileStorageError:
        _fail("file_storage_unavailable", "service_unavailable", "文件存储暂不可用")
    _validate_download_intent(intent, row=row, now=_database_now(db), ttl_seconds=ttl)
    now = _database_now(db)
    _append_file_audit(
        db,
        row=row,
        actor=current,
        action="file.download_intent.created",
        request_id=trace_id,
        occurred_at=now,
        after=binding_summary,
    )
    return FileDownloadIntentResult(
        file_id=row.id,
        purpose=purpose,
        download=intent,
    )


def _authorize_download(
    db: Session,
    *,
    actor: FormalPrincipal,
    row: FileObject,
    purpose: str,
) -> dict[str, Any]:
    request_attachment_ids = tuple(
        sorted(
            set(
                db.scalars(
                    select(MaterialRequestFile.request_id).where(
                        MaterialRequestFile.file_id == row.id
                    )
                ).all()
            ),
            key=str,
        )
    )
    external_request_ids = tuple(
        sorted(
            set(
                db.scalars(
                    select(ApprovalInstance.request_id)
                    .join(ApprovalStep, ApprovalStep.instance_id == ApprovalInstance.id)
                    .join(
                        ApprovalExternalRegistration,
                        ApprovalExternalRegistration.step_id == ApprovalStep.id,
                    )
                    .where(ApprovalExternalRegistration.evidence_file_id == row.id)
                ).all()
            ),
            key=str,
        )
    )
    document_bindings = tuple(
        db.scalars(
            select(DocumentAttachment)
            .where(
                DocumentAttachment.file_id == row.id,
                DocumentAttachment.status == "active",
            )
            .order_by(DocumentAttachment.id)
        ).all()
    )
    has_any_binding = bool(
        request_attachment_ids or external_request_ids or document_bindings
    )
    if not has_any_binding:
        if row.uploaded_by != actor.user_id:
            _fail("file_download_forbidden", "forbidden", "当前账号不能下载该文件")
        _require_uploader_continuity(row, row.metadata_jsonb, actor)
        _require_upload_permission(db, actor, purpose)
        return {"binding_type": "unbound", "binding_count": 0}

    if purpose == "request_attachment":
        if not request_attachment_ids or external_request_ids or document_bindings:
            _binding_invalid()
        _require_material_request_read(db, actor, request_attachment_ids)
        return {
            "binding_type": "material_request_attachment",
            "binding_count": len(request_attachment_ids),
        }
    if purpose == "external_approval_evidence":
        if not external_request_ids or request_attachment_ids or document_bindings:
            _binding_invalid()
        _require_material_request_read(db, actor, external_request_ids)
        return {
            "binding_type": "external_approval_registration",
            "binding_count": len(external_request_ids),
        }
    if purpose == "stocktake_evidence":
        if request_attachment_ids or external_request_ids or not document_bindings:
            _binding_invalid()
        # A formal stocktake proof is deliberately single-use.  Reusing the
        # same object across scopes/tasks makes its evidence scope ambiguous.
        if len(document_bindings) != 1:
            _binding_invalid()
        task_ids = _stocktake_task_ids_for_bindings(db, document_bindings)
        for task_id in task_ids:
            try:
                stocktake_query.stocktake_task_detail(
                    db,
                    actor=actor,
                    task_id=task_id,
                )
            except stocktake_query.StocktakeReadError:
                _fail("file_download_forbidden", "forbidden", "当前账号不能下载该文件")
        return {
            "binding_type": "stocktake_scope_count_completion",
            "binding_count": len(document_bindings),
        }
    _binding_invalid()


def _require_material_request_read(
    db: Session,
    actor: FormalPrincipal,
    request_ids: Sequence[uuid.UUID],
) -> None:
    for request_id in request_ids:
        try:
            material_request_query.material_request_detail(
                db,
                actor=actor,
                request_id=request_id,
            )
        except material_request_query.MaterialRequestReadError:
            _fail("file_download_forbidden", "forbidden", "当前账号不能下载该文件")


def _stocktake_task_ids_for_bindings(
    db: Session,
    bindings: Sequence[DocumentAttachment],
) -> tuple[uuid.UUID, ...]:
    completion_ids: list[uuid.UUID] = []
    for binding in bindings:
        if (
            binding.document_type != "stocktake_scope_count_completion"
            or binding.attachment_type != "stocktake_evidence"
        ):
            _binding_invalid()
        try:
            completion_ids.append(uuid.UUID(binding.document_id))
        except (TypeError, ValueError):
            _binding_invalid()
    completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(StocktakeScopeCountCompletion.id.in_(completion_ids))
            .order_by(StocktakeScopeCountCompletion.id)
        ).all()
    )
    if {row.id for row in completions} != set(completion_ids):
        _binding_invalid()
    return tuple(sorted({row.task_id for row in completions}, key=str))


def _current_principal(
    db: Session,
    supplied: FormalPrincipal,
    *,
    lock: bool,
) -> FormalPrincipal:
    if lock:
        lock_formal_principal_graph(db, (supplied.user_id,))
    try:
        current = load_formal_principal(db, supplied.user_id)
    except FormalAccessError:
        _fail("file_actor_not_current", "forbidden", "当前账号没有有效的正式访问权限")
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail("file_actor_principal_stale", "precondition_failed", "权限版本已变化，请重新读取")
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("file_actor_inactive", "forbidden", "当前账号不能操作文件")
    return current


def _require_upload_permission(
    db: Session,
    actor: FormalPrincipal,
    purpose: str,
) -> None:
    resource, action, field_code = _PERMISSION_BY_PURPOSE[purpose]
    try:
        allowed = actor.allows(
            db,
            resource,
            action,
            field_code=field_code,
        )
    except FormalAccessError:
        allowed = False
    if not allowed:
        _fail("file_purpose_forbidden", "forbidden", "当前账号不能上传该用途文件")


def _require_uploader_continuity(
    row: FileObject,
    metadata: Mapping[str, Any],
    actor: FormalPrincipal,
) -> None:
    if (
        row.uploaded_by != actor.user_id
        or metadata.get("uploader_user_id") != actor.user_id
        or metadata.get("uploader_person_id") != str(actor.person_id)
        or metadata.get("authorization_version") != actor.authorization_version
    ):
        _fail(
            "file_uploader_identity_changed",
            "precondition_failed",
            "上传者身份或权限版本已变化，请重新创建上传意图",
        )


def _prepare_upload(
    value: FileUploadIntentInput,
    *,
    maximum_size_bytes: int,
) -> _PreparedUpload:
    if not isinstance(value, FileUploadIntentInput):
        _fail("file_upload_request_invalid", "invalid_request", "文件上传请求无效")
    purpose = value.purpose.strip() if isinstance(value.purpose, str) else ""
    if purpose not in PURPOSES:
        _fail("file_purpose_invalid", "invalid_request", "文件用途无效")
    filename = _canonical_filename(value.original_filename)
    if isinstance(value.size_bytes, bool) or not isinstance(value.size_bytes, int):
        _fail("file_size_invalid", "invalid_request", "文件大小无效")
    if not 1 <= value.size_bytes <= maximum_size_bytes:
        _fail("file_size_invalid", "invalid_request", "文件大小无效")
    mime = value.mime_type.strip() if isinstance(value.mime_type, str) else ""
    if mime not in _ALLOWED_MIME_EXTENSIONS:
        _fail("file_mime_type_invalid", "invalid_request", "文件类型不受支持")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in _ALLOWED_MIME_EXTENSIONS[mime]:
        _fail("file_extension_mismatch", "invalid_request", "文件名与文件类型不一致")
    sha256 = value.sha256 if isinstance(value.sha256, str) else ""
    if _SHA256.fullmatch(sha256) is None:
        _fail("file_sha256_invalid", "invalid_request", "文件摘要无效")
    return _PreparedUpload(
        purpose=purpose,
        original_filename=filename,
        size_bytes=value.size_bytes,
        mime_type=mime,
        sha256=sha256,
    )


def _canonical_filename(value: object) -> str:
    if not isinstance(value, str) or not value:
        _fail("file_name_invalid", "invalid_request", "文件名无效")
    normalized = unicodedata.normalize("NFC", value)
    if (
        normalized != value
        or normalized != normalized.strip()
        or normalized in {".", ".."}
        or "/" in normalized
        or "\\" in normalized
        or any(
            character in _FORBIDDEN_FILENAME_CODEPOINTS
            or unicodedata.category(character).startswith("C")
            for character in normalized
        )
        or len(normalized) > 200
        or len(normalized.encode("utf-8")) > 255
    ):
        _fail("file_name_invalid", "invalid_request", "文件名无效")
    return normalized


def _pending_metadata(
    *,
    file_id: uuid.UUID,
    storage_key: str,
    prepared: _PreparedUpload,
    actor: FormalPrincipal,
    key_hash: str,
    request_hash: str,
    provider_code: str,
) -> dict[str, Any]:
    return {
        "authorization_version": actor.authorization_version,
        "file_id": str(file_id),
        "idempotency_key_hash": key_hash,
        "provider": provider_code,
        "purpose": prepared.purpose,
        "request_sha256": request_hash,
        "schema": FILE_METADATA_SCHEMA,
        "storage_key": storage_key,
        "uploader_person_id": str(actor.person_id),
        "uploader_user_id": actor.user_id,
    }


def _validate_intent_metadata(
    row: FileObject,
    *,
    allow_completed: bool,
) -> dict[str, Any]:
    value = row.metadata_jsonb
    if not isinstance(value, dict):
        _metadata_invalid()
    required = {
        "authorization_version",
        "file_id",
        "idempotency_key_hash",
        "provider",
        "purpose",
        "request_sha256",
        "schema",
        "storage_key",
        "uploader_person_id",
        "uploader_user_id",
    }
    allowed = required | ({"completion"} if allow_completed else set())
    keys = set(value)
    if keys != required and keys != allowed:
        _metadata_invalid()
    try:
        if (
            value["schema"] != FILE_METADATA_SCHEMA
            or value["purpose"] not in PURPOSES
            or value["file_id"] != str(row.id)
            or value["storage_key"] != row.storage_key
            or row.storage_key != _storage_key(str(value["purpose"]), row.id)
            or value["uploader_user_id"] != row.uploaded_by
            or not isinstance(value["authorization_version"], int)
            or isinstance(value["authorization_version"], bool)
            or value["authorization_version"] <= 0
            or uuid.UUID(str(value["uploader_person_id"])).int == 0
            or _SHA256.fullmatch(str(value["idempotency_key_hash"])) is None
            or _SHA256.fullmatch(str(value["request_sha256"])) is None
            or not isinstance(value["provider"], str)
            or not value["provider"]
        ):
            _metadata_invalid()
    except (KeyError, TypeError, ValueError):
        _metadata_invalid()
    if "completion" in value:
        completion = value["completion"]
        if not isinstance(completion, dict) or set(completion) != {
            "etag_sha256",
            "head_manifest_sha256",
            "verified_at",
        }:
            _metadata_invalid()
        if (
            _SHA256.fullmatch(str(completion.get("etag_sha256", ""))) is None
            or _SHA256.fullmatch(str(completion.get("head_manifest_sha256", ""))) is None
        ):
            _metadata_invalid()
        try:
            verified = datetime.fromisoformat(str(completion["verified_at"]))
        except ValueError:
            _metadata_invalid()
        if verified.tzinfo is None:
            _metadata_invalid()
    if row.status == "available" and "completion" not in value:
        _metadata_invalid()
    if row.status == "pending" and "completion" in value:
        _metadata_invalid()
    try:
        persisted = _prepare_upload(
            FileUploadIntentInput(
                purpose=str(value["purpose"]),
                original_filename=row.original_filename or "",
                size_bytes=row.size_bytes,
                mime_type=row.mime_type,
                sha256=row.sha256,
            ),
            maximum_size_bytes=120 * 1024 * 1024,
        )
    except FormalFileError:
        _metadata_invalid()
    if value["request_sha256"] != _upload_request_hash(persisted):
        _metadata_invalid()
    return dict(value)


def _validate_file_row(row: FileObject) -> None:
    if (
        not isinstance(row.id, uuid.UUID)
        or row.id.int == 0
        or not isinstance(row.storage_key, str)
        or _STORAGE_KEY.fullmatch(row.storage_key) is None
        or _SHA256.fullmatch(row.sha256 or "") is None
        or isinstance(row.size_bytes, bool)
        or not isinstance(row.size_bytes, int)
        or not 1 <= row.size_bytes <= 120 * 1024 * 1024
        or row.mime_type not in _ALLOWED_MIME_EXTENSIONS
        or not isinstance(row.uploaded_by, str)
        or not row.uploaded_by
        or row.status not in {"pending", "available", "quarantined", "deleted"}
    ):
        _fail("file_record_invalid", "service_unavailable", "文件记录无效")


def _lock_file(db: Session, file_id: uuid.UUID) -> FileObject:
    row = db.scalar(
        select(FileObject)
        .where(FileObject.id == file_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        _fail("file_not_found", "not_found", "文件不存在")
    _validate_file_row(row)
    return row


def _create_and_validate_upload_intent(
    storage: FileStorageAdapter,
    *,
    row: FileObject,
    ttl_seconds: int,
    now: datetime,
) -> UploadIntent:
    try:
        intent = storage.create_upload_intent(
            storage_key=row.storage_key,
            file_id=str(row.id),
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            mime_type=row.mime_type,
            ttl_seconds=ttl_seconds,
        )
    except FileStorageError:
        _fail("file_storage_unavailable", "service_unavailable", "文件存储暂不可用")
    _validate_upload_intent(intent, row=row, now=now, ttl_seconds=ttl_seconds)
    return intent


def _validate_upload_intent(
    intent: UploadIntent,
    *,
    row: FileObject,
    now: datetime,
    ttl_seconds: int,
) -> None:
    if not isinstance(intent, UploadIntent) or intent.storage_key != row.storage_key:
        _storage_response_invalid()
    _validate_signed_url(intent.url)
    _validate_expiry(intent.expires_at, now=now, ttl_seconds=ttl_seconds)
    if not isinstance(intent.headers, Mapping):
        _storage_response_invalid()
    header_items = tuple(
        (str(key).lower(), str(value)) for key, value in intent.headers.items()
    )
    headers = dict(header_items)
    expected = {
        "content-type": row.mime_type,
        "x-oss-forbid-overwrite": "true",
        "x-oss-meta-file-id": str(row.id),
        "x-oss-meta-sha256": row.sha256,
    }
    if len(headers) != len(header_items) or headers != expected:
        _storage_response_invalid()


def _validate_download_intent(
    intent: DownloadIntent,
    *,
    row: FileObject,
    now: datetime,
    ttl_seconds: int,
) -> None:
    if not isinstance(intent, DownloadIntent) or intent.storage_key != row.storage_key:
        _storage_response_invalid()
    _validate_signed_url(intent.url)
    _validate_expiry(intent.expires_at, now=now, ttl_seconds=ttl_seconds)


def _validate_object_head(row: FileObject, head: StoredObjectHead) -> None:
    if (
        not isinstance(head, StoredObjectHead)
        or head.storage_key != row.storage_key
        or isinstance(head.size_bytes, bool)
        or head.size_bytes != row.size_bytes
        or head.mime_type != row.mime_type
        or not isinstance(head.etag, str)
        or not head.etag
        or len(head.etag) > 300
        or not isinstance(head.metadata, Mapping)
    ):
        _fail("file_object_verification_failed", "precondition_failed", "文件对象校验失败")
    metadata: dict[str, str] = {}
    for key, value in head.metadata.items():
        normalized = str(key).lower()
        if normalized.startswith("x-oss-meta-"):
            normalized = normalized[len("x-oss-meta-") :]
        if normalized in metadata:
            _fail("file_object_verification_failed", "precondition_failed", "文件对象校验失败")
        metadata[normalized] = str(value)
    if metadata.get("file-id") != str(row.id) or metadata.get("sha256") != row.sha256:
        _fail("file_object_verification_failed", "precondition_failed", "文件对象校验失败")


def _head_manifest_sha256(head: StoredObjectHead) -> str:
    metadata = {
        (str(key).lower()[len("x-oss-meta-") :] if str(key).lower().startswith("x-oss-meta-") else str(key).lower()): str(value)
        for key, value in head.metadata.items()
    }
    return _canonical_hash(
        {
            "etag_sha256": hashlib.sha256(head.etag.encode("utf-8")).hexdigest(),
            "metadata": dict(sorted(metadata.items())),
            "mime_type": head.mime_type,
            "size_bytes": head.size_bytes,
            "storage_key": head.storage_key,
        }
    )


def _append_file_audit(
    db: Session,
    *,
    row: FileObject,
    actor: FormalPrincipal,
    action: str,
    request_id: str,
    occurred_at: datetime,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
) -> None:
    purpose = str(row.metadata_jsonb.get("purpose", ""))
    stream = _AUDIT_STREAM_BY_PURPOSE.get(purpose)
    if stream is None:
        _metadata_invalid()
    safe_after = {
        "mime_type": row.mime_type,
        "purpose": purpose,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        **dict(after or {}),
    }
    try:
        append_audit_event(
            db,
            stream_key=stream,
            actor_user_id=actor.user_id,
            action=action,
            aggregate_type=FILE_AGGREGATE_TYPE,
            aggregate_id=str(row.id),
            before_jsonb=dict(before) if before is not None else None,
            after_jsonb=safe_after,
            request_id=request_id,
            occurred_at=occurred_at,
        )
    except AuditChainError:
        _fail("file_audit_unavailable", "service_unavailable", "文件审计暂不可用")


def _validate_signed_url(value: object) -> None:
    if not isinstance(value, str) or not value or len(value) > 8192:
        _storage_response_invalid()
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError:
        _storage_response_invalid()
    if (
        parts.scheme != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        _storage_response_invalid()


def _validate_expiry(value: object, *, now: datetime, ttl_seconds: int) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _storage_response_invalid()
    expires = value.astimezone(timezone.utc)
    checked_now = now.astimezone(timezone.utc)
    if not checked_now < expires <= checked_now + timedelta(seconds=ttl_seconds + 30):
        _storage_response_invalid()


def _storage_key(purpose: str, file_id: uuid.UUID) -> str:
    return f"formal-files/v1/{purpose}/{file_id.hex[:2]}/{file_id.hex}"


def _take_file_advisory_lock(db: Session, file_id: uuid.UUID) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    coordinate = int.from_bytes(
        hashlib.sha256(f"formal-file:{file_id}".encode("ascii")).digest()[:8],
        "big",
        signed=True,
    )
    db.execute(
        text("SELECT pg_advisory_xact_lock(:coordinate)"),
        {"coordinate": coordinate},
    )


def _require_supplied_principal(value: object) -> FormalPrincipal:
    if not isinstance(value, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "文件操作必须使用正式权限主体")
    return value


def _require_uuid(name: str, value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(f"{name}_invalid", "invalid_request", "文件标识无效")
    return value


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _SAFE_IDEMPOTENCY.fullmatch(value) is None:
        _fail("file_idempotency_key_invalid", "invalid_request", "幂等键无效")
    return value


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or _SAFE_TRACE.fullmatch(value) is None:
        _fail("file_request_id_invalid", "invalid_request", "请求标识无效")
    return value


def _require_hmac_secret(value: object) -> bytes:
    if isinstance(value, str):
        raw = value.strip().encode("utf-8")
        lowered = value.strip().lower()
    elif isinstance(value, bytes):
        raw = value
        lowered = value.decode("utf-8", errors="ignore").lower()
    else:
        raw = b""
        lowered = ""
    if len(raw) < 32 or any(marker in lowered for marker in _PLACEHOLDERS):
        _fail("file_idempotency_unavailable", "service_unavailable", "文件服务未安全配置")
    return raw


def _require_ttl(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail("file_intent_ttl_invalid", "service_unavailable", "文件服务未安全配置")
    return value


def _require_maximum_size(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= 120 * 1024 * 1024
    ):
        _fail("file_size_limit_invalid", "service_unavailable", "文件服务未安全配置")
    return value


def _require_storage_provider(storage: object) -> None:
    if getattr(storage, "provider_code", None) != "aliyun_oss_v2":
        _storage_provider_mismatch()


def _domain_hmac(secret: bytes, domain: str, *values: str) -> str:
    payload = "\0".join((domain, *values)).encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _uuid_from_digest(digest: bytes) -> uuid.UUID:
    raw = bytearray(digest[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _upload_request_hash(value: _PreparedUpload) -> str:
    return _canonical_hash(
        {
            "mime_type": value.mime_type,
            "original_filename": value.original_filename,
            "purpose": value.purpose,
            "sha256": value.sha256,
            "size_bytes": value.size_bytes,
        }
    )


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        _fail("file_database_clock_invalid", "service_unavailable", "数据库时间不可用")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _binding_invalid() -> None:
    _fail("file_binding_invalid", "service_unavailable", "文件业务绑定无法安全验证")


def _metadata_invalid() -> None:
    _fail("file_metadata_invalid", "service_unavailable", "文件意图证据无效")


def _storage_response_invalid() -> None:
    _fail("file_storage_response_invalid", "service_unavailable", "文件存储响应无效")


def _storage_provider_mismatch() -> None:
    _fail("file_storage_provider_mismatch", "service_unavailable", "文件存储配置无效")


def _fail(code: str, category: str, message: str) -> None:
    raise FormalFileError(code, category, message)


__all__ = [
    "FILE_METADATA_SCHEMA",
    "FileCompleteResult",
    "FileDownloadIntentResult",
    "FileUploadIntentInput",
    "FileUploadIntentResult",
    "FormalFileError",
    "complete_file_upload",
    "create_file_download_intent",
    "create_file_upload_intent",
    "is_available_formal_file_for_purpose",
]
