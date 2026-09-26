"""Current-authority grants for the exact published private import error file.

The caller commits the grant audit before returning its short-lived URL. No
source read, object write, job transition or count occurs during a download.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal
from ..foundation_models import FileObject
from ..stocktake_models import FormalStocktakeScope, StocktakeRound, StocktakeRecountScopeAssignment
from . import formal_files
from .file_storage import DownloadIntent, FileStorageAdapter, FileStorageError
from .opening_count_import_error_jobs import _artifact, _audit
from .opening_count_import_error_report import PURPOSE
from .opening_count_import_intake import _request_id
from .opening_count_import_jobs import OpeningCountImportJobError, _lock_owned_import_job


@dataclass(frozen=True, slots=True)
class OpeningCountErrorDownload:
    job_id: UUID
    file_id: UUID
    filename: str
    sha256: str
    size_bytes: int
    download: DownloadIntent


def create_opening_count_error_download(
    db: Session, *, actor: FormalPrincipal, job_id: UUID, request_id: str,
    storage: FileStorageAdapter, ttl_seconds: int,
) -> OpeningCountErrorDownload:
    """Authorize the original requester, current scope and immutable file binding."""
    _request_id(request_id)
    formal_files._require_storage_provider(storage)
    formal_files._require_ttl(ttl_seconds, minimum=30, maximum=600)
    job, binding, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.status != "failed" or job.error_file_id is None:
        raise OpeningCountImportJobError("opening_import_error_unavailable", 404,
                                        "该导入任务尚无可下载的错误报告")
    scope = db.scalar(select(FormalStocktakeScope).where(
        FormalStocktakeScope.id == binding.scope_id, FormalStocktakeScope.task_id == binding.task_id))
    round_row = db.scalar(select(StocktakeRound).where(
        StocktakeRound.id == binding.round_id, StocktakeRound.task_id == binding.task_id))
    assigned = bool(scope is not None and round_row is not None and (
        scope.assignee_user_id == current.user_id if round_row.round_no == 1 else
        db.scalar(select(StocktakeRecountScopeAssignment.id).where(
            StocktakeRecountScopeAssignment.recount_case_id == round_row.recount_case_id,
            StocktakeRecountScopeAssignment.task_id == binding.task_id,
            StocktakeRecountScopeAssignment.scope_id == binding.scope_id,
            StocktakeRecountScopeAssignment.assignee_user_id == current.user_id))))
    if not assigned:
        raise OpeningCountImportJobError("opening_import_scope_forbidden", 403,
                                        "当前账号不再是该盘点范围的执行人")
    target = (("person", str(scope.custodian_person_id_snapshot)) if scope.custodian_person_id_snapshot
              else ("organization", str(scope.owner_org_id)))
    if not current.allows(db, "stocktake", "count", target_scope_type=target[0], target_scope_id=target[1]):
        raise OpeningCountImportJobError("opening_import_scope_forbidden", 403,
                                        "当前账号没有该盘点范围的权限")
    artifact = _artifact(job)
    row = db.scalar(select(FileObject).where(FileObject.id == job.error_file_id)
                    .with_for_update().execution_options(populate_existing=True))
    if not formal_files.is_available_formal_file_for_purpose(
        row, purpose=PURPOSE, uploader_user_id=current.user_id,
    ):
        raise OpeningCountImportJobError("opening_import_error_unavailable", 404,
                                        "错误报告不存在或尚未完成核验")
    metadata = row.metadata_jsonb
    if (row.id != artifact.file_id or row.storage_key != artifact.storage_key
            or row.sha256 != artifact.sha256 or row.size_bytes != len(artifact.payload)
            or metadata.get("authorization_version") != current.authorization_version
            or metadata.get("uploader_person_id") != str(current.person_id)
            or metadata.get("idempotency_key_hash") != job.idempotency_key
            or metadata.get("provider") != storage.provider_code):
        raise OpeningCountImportJobError("opening_import_error_binding_changed", 412,
                                        "错误报告与原任务或当前身份不一致")
    try:
        intent = storage.create_download_intent(storage_key=row.storage_key, ttl_seconds=ttl_seconds)
    except FileStorageError:
        raise OpeningCountImportJobError("opening_import_error_storage_unavailable", 503,
                                        "错误报告存储暂不可用，请稍后重新申请下载") from None
    formal_files._validate_download_intent(intent, row=row,
        now=formal_files._database_wall_clock(db), ttl_seconds=ttl_seconds)
    _audit(db, actor=current, job=job, action="opening_count_import_error_download_granted",
        detail={"error_file_id": str(row.id), "error_sha256": row.sha256,
                "authorization_version": current.authorization_version,
                "expires_at": intent.expires_at.isoformat()}, request_id=request_id)
    return OpeningCountErrorDownload(job.id, row.id, artifact.filename, row.sha256, row.size_bytes, intent)
