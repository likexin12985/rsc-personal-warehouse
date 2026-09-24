"""Shared pure formal-file integrity rules for the API and backup worker.

No SQLAlchemy, application settings, database engine or provider is imported.
The API and standalone backup process both import these exact definitions.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import hashlib,json,re,unicodedata,uuid
from typing import Any,Final,Mapping

@dataclass(slots=True)
class FileObject:
    id: uuid.UUID
    storage_key: str
    sha256: str
    size_bytes: int
    mime_type: str
    original_filename: str | None
    uploaded_by: str | None
    status: str
    metadata_jsonb: dict[str, Any]
    created_at: datetime

@dataclass(frozen=True, slots=True)
class StoredObjectHead:
    storage_key: str
    size_bytes: int
    mime_type: str
    metadata: Mapping[str, str]
    etag: str


FILE_METADATA_SCHEMA: Final[str] = "cloud_oam.formal_file_upload_intent.v1"


PURPOSES: Final[frozenset[str]] = frozenset(
    {
        "request_attachment",
        "external_approval_evidence",
        "stocktake_evidence",
        "receipt_exception_evidence",
        "source_configuration_evidence",
        "daily_reconciliation_evidence",
        "inventory_report_export",
        "opening_count_import",
    }
)


_ALLOWED_MIME_EXTENSIONS: Final[dict[str, frozenset[str]]] = {
    "application/pdf": frozenset({".pdf"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/png": frozenset({".png"}),
    "image/webp": frozenset({".webp"}),
    "image/heic": frozenset({".heic"}),
    "image/heif": frozenset({".heif"}),
    "video/mp4": frozenset({".mp4"}),
    "video/quicktime": frozenset({".mov"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": frozenset({".xlsx"}),
}


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


_STORAGE_KEY = re.compile(
    r"^formal-files/v1/(request_attachment|external_approval_evidence|stocktake_evidence|receipt_exception_evidence|source_configuration_evidence|daily_reconciliation_evidence|inventory_report_export|opening_count_import)/[0-9a-f]{2}/[0-9a-f]{32}$"
)


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
class _PreparedUpload:
    purpose: str
    original_filename: str
    size_bytes: int
    mime_type: str
    sha256: str


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
    is_workbook_purpose = purpose in {"inventory_report_export", "opening_count_import"}
    is_workbook = mime == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if is_workbook_purpose != is_workbook:
        _fail("file_purpose_mime_mismatch", "invalid_request", "文件用途与类型不一致")
    if purpose == "opening_count_import" and value.size_bytes > 8 * 1024 * 1024:
        _fail("file_size_invalid", "invalid_request", "文件大小无效")
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


def _storage_key(purpose: str, file_id: uuid.UUID) -> str:
    return f"formal-files/v1/{purpose}/{file_id.hex[:2]}/{file_id.hex}"


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


def _metadata_invalid() -> None:
    _fail("file_metadata_invalid", "service_unavailable", "文件意图证据无效")


def _fail(code: str, category: str, message: str) -> None:
    raise FormalFileError(code, category, message)
