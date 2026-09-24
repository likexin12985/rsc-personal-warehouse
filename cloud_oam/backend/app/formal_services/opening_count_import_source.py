"""Read one completed private XLSX source for its current uploader.

This is the source half of opening-count import prevalidation.  The caller
must end this transaction before entering the count service, whose lock order
starts at the stocktake task.  A source read is never an authorization to
submit a count or create a file job.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import FileObject
from .file_storage import FileStorageAdapter
from .formal_files import is_available_formal_file_for_purpose


class OpeningCountImportSourceError(RuntimeError):
    def __init__(self, code: str, http_status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status_code = http_status_code


@dataclass(frozen=True, slots=True)
class AuthorizedOpeningCountSource:
    file_id: UUID
    source_sha256: str
    size_bytes: int
    data: bytes


def read_authorized_opening_count_source(
    db: Session,
    *,
    actor: FormalPrincipal,
    file_id: UUID,
    storage: FileStorageAdapter,
) -> AuthorizedOpeningCountSource:
    """Lock current uploader and completed file before a bounded OSS read.

    Close this read transaction before calling the business prevalidator.  Its
    later transaction must recheck the current task, round, scope and actor.
    """

    if db.new or db.dirty or db.deleted:
        raise OpeningCountImportSourceError(
            "opening_import_source_session_not_clean", 412,
            "读取导入源文件需要独立的只读事务",
        )
    if not isinstance(file_id, UUID):
        raise OpeningCountImportSourceError(
            "opening_import_source_id_invalid", 422, "源文件标识无效",
        )
    if not isinstance(actor, FormalPrincipal):
        raise OpeningCountImportSourceError(
            "opening_import_source_actor_invalid", 403, "当前账号无权读取该源文件",
        )
    lock_formal_principal_graph(db, (actor.user_id,))
    try:
        current = load_formal_principal(db, actor.user_id)
    except FormalAccessError as exc:
        raise OpeningCountImportSourceError(
            "opening_import_source_actor_not_current", 403,
            "当前账号没有有效的正式访问权限",
        ) from exc
    if (
        current.user_id != actor.user_id
        or current.person_id != actor.person_id
        or current.authorization_version != actor.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        raise OpeningCountImportSourceError(
            "opening_import_source_actor_stale", 412,
            "人员或权限版本已变化，请重新读取",
        )
    try:
        allowed = current.allows(db, "stocktake", "count")
    except FormalAccessError:
        allowed = False
    if not allowed:
        raise OpeningCountImportSourceError(
            "opening_import_source_forbidden", 403,
            "当前账号不能读取期初盘点导入源文件",
        )

    row = db.scalar(
        select(FileObject).where(FileObject.id == file_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    if not is_available_formal_file_for_purpose(
        row, purpose="opening_count_import", uploader_user_id=current.user_id,
    ):
        raise OpeningCountImportSourceError(
            "opening_import_source_unavailable", 404,
            "期初盘点导入源文件不存在或未完成核验",
        )
    metadata = row.metadata_jsonb
    if (
        metadata.get("uploader_person_id") != str(current.person_id)
        or metadata.get("authorization_version") != current.authorization_version
        or metadata.get("provider") != storage.provider_code
    ):
        raise OpeningCountImportSourceError(
            "opening_import_source_binding_changed", 412,
            "源文件上传身份或存储绑定已变化",
        )
    return AuthorizedOpeningCountSource(
        file_id=row.id,
        source_sha256=row.sha256,
        size_bytes=row.size_bytes,
        data=storage.read_opening_count_source(
            storage_key=row.storage_key,
            file_id=str(row.id),
            sha256=row.sha256,
            size_bytes=row.size_bytes,
        ),
    )
