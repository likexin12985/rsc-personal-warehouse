"""Confirm a persisted, reviewed import with its count facts in one transaction.

The caller must read the private source in a separate transaction first and
must commit or roll back this transaction as a whole. No public route or
worker is enabled here. An uncertain commit is resolved by a read, never by
replaying the count command.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError, FormalPrincipal, load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import FileJob
from ..stocktake_models import FormalStocktakeTask, StocktakeScopeCountCompletion
from .audit_chain import append_audit_event
from .opening_count_import_confirmation import confirm_opening_count_import_bytes
from .opening_count_import_document import load_import_binding, load_import_preview
from .opening_count_import_source import AuthorizedOpeningCountSource
from .opening_count_import_prevalidation import prevalidate_opening_count_import_bytes
from .opening_observation_disposition import _task_principal_user_ids
from .opening_stocktake_count import (
    OpeningStocktakeScopeCountResult, lock_opening_count_coordinates,
)


class OpeningCountImportJobError(RuntimeError):
    def __init__(self, code: str, status: int, message: str):
        super().__init__(message)
        self.code = code
        self.http_status_code = status


@dataclass(frozen=True, slots=True)
class OpeningCountImportJobStatus:
    job_id: UUID
    status: str
    completion_id: UUID | None


def _clean(db):
    if db.new or db.dirty or db.deleted:
        raise OpeningCountImportJobError("opening_import_session_not_clean", 412,
                                        "导入任务需要独立、无待写入内容的事务")


def _owned_job(db, actor, job_id, *, lock=False):
    if not isinstance(actor, FormalPrincipal) or not isinstance(job_id, UUID):
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    query = select(FileJob).where(FileJob.id == job_id, FileJob.job_type == "import",
                                 FileJob.requested_by == actor.user_id)
    if lock:
        query = query.with_for_update()
    job = db.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    return job


def _current_actor(db, actor, binding):
    try:
        current = load_formal_principal(db, actor.user_id)
        allowed = current.allows(db, "stocktake", "count")
    except FormalAccessError as exc:
        raise OpeningCountImportJobError("opening_import_actor_not_current", 403,
                                        "当前账号没有有效的正式访问权限") from exc
    if (not allowed or current.access_mode != "active" or current.account_status != "active"
            or current.employment_status != "active"):
        raise OpeningCountImportJobError("opening_import_actor_forbidden", 403,
                                        "当前账号没有盘点导入权限")
    if (current.person_id != actor.person_id
            or current.authorization_version != actor.authorization_version
            or current.authorization_version != binding.authorization_version):
        raise OpeningCountImportJobError("opening_import_actor_stale", 412,
                                        "人员或权限版本已变化，请重新读取")
    return current


def read_opening_count_import_job(
    db: Session, *, actor: FormalPrincipal, job_id: UUID,
) -> OpeningCountImportJobStatus:
    """Read the exact owned result without source I/O, count calls or writes."""
    _clean(db)
    job = _owned_job(db, actor, job_id)
    binding = load_import_binding(job.import_binding_jsonb)
    lock_formal_principal_graph(db, (actor.user_id,))
    _current_actor(db, actor, binding)
    job = _owned_job(db, actor, job_id)
    return OpeningCountImportJobStatus(job.id, job.status, job.import_completion_id)


def _lock_owned_import_job(db, *, actor, job_id):
    """Share the formal count lock order across import transitions."""
    _clean(db)
    job = _owned_job(db, actor, job_id)
    binding = load_import_binding(job.import_binding_jsonb)
    lock_opening_count_coordinates(db, task_id=binding.task_id,
        round_id=binding.round_id, idempotency_key=job.idempotency_key)
    task = db.scalar(select(FormalStocktakeTask).where(
        FormalStocktakeTask.id == binding.task_id).with_for_update())
    if task is None:
        raise OpeningCountImportJobError("opening_import_task_missing", 412, "盘点任务不存在")
    principal_ids = _task_principal_user_ids(db, task_id=task.id,
                                           supplied_user_ids=(actor.user_id,))
    lock_formal_principal_graph(db, tuple(sorted(principal_ids)))
    current = _current_actor(db, actor, binding)
    job = _owned_job(db, current, job_id, lock=True)
    return job, binding, current


def confirm_persisted_opening_count_import(
    db: Session, *, actor: FormalPrincipal, job_id: UUID,
    source: AuthorizedOpeningCountSource, request_id: str,
) -> OpeningStocktakeScopeCountResult:
    """Use only the persisted preview and commit its exact completion link.

    The source reader must have ended its earlier file/principal transaction.
    Browser-supplied preview documents are deliberately not an argument.
    """
    job, binding, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.status != "awaiting_confirmation":
        raise OpeningCountImportJobError("opening_import_not_awaiting_confirmation", 409,
                                        "导入任务不可再次确认，请读取原任务结果")
    preview = load_import_preview(job.import_preview_jsonb, binding=binding)
    if (type(source) is not AuthorizedOpeningCountSource
            or source.file_id != binding.source_file_id
            or source.source_sha256 != binding.source_sha256
            or not isinstance(source.data, bytes) or source.size_bytes != len(source.data)):
        raise OpeningCountImportJobError("opening_import_source_changed", 412,
                                        "当前私有源文件与任务绑定不一致")
    job.status = "running"
    job.confirmed_by = current.user_id
    db.flush()
    # Acquire the complete count reference graph and inventory audit head in
    # the formal service's order before appending our confirmation evidence.
    # The final count rechecks the proof and must leave its round audit at the
    # chain tip, as required by the existing deferred database closure guard.
    current_preview = prevalidate_opening_count_import_bytes(db, actor=current,
        data=source.data, expected_source_sha256=binding.source_sha256,
        task_id=binding.task_id, round_id=binding.round_id, scope_id=binding.scope_id,
        idempotency_key=job.idempotency_key, request_id=request_id)
    if current_preview != preview:
        raise OpeningCountImportJobError("opening_import_confirmation_preview_changed", 412,
                                        "盘点范围、来源解析或授权已变化，请重新预校验")
    confirmed_at = db.scalar(text("SELECT clock_timestamp()"))
    append_audit_event(db, stream_key="inventory", actor_user_id=current.user_id,
        action="opening_count_import_confirmed", aggregate_type="file_job",
        aggregate_id=str(job.id), before_jsonb={"status": "awaiting_confirmation"},
        after_jsonb={"confirmed_by": current.user_id,
                     "source_sha256": binding.source_sha256,
                     "count_key_sha256": binding.count_key_sha256,
                     "request_sha256": preview.count.request_sha256},
        request_id=request_id, occurred_at=confirmed_at, created_at=confirmed_at)
    result = confirm_opening_count_import_bytes(db, actor=current, data=source.data,
        preview=preview, idempotency_key=job.idempotency_key, request_id=request_id)
    completion = db.scalar(select(StocktakeScopeCountCompletion).where(
        StocktakeScopeCountCompletion.task_id == binding.task_id,
        StocktakeScopeCountCompletion.round_id == binding.round_id,
        StocktakeScopeCountCompletion.scope_id == binding.scope_id,
        StocktakeScopeCountCompletion.idempotency_key_hash == binding.count_key_sha256))
    if completion is None:
        raise OpeningCountImportJobError("opening_import_completion_missing", 503,
                                        "导入缺少正式盘点完成记录")
    job.status = "succeeded"
    job.import_completion_id = completion.id
    job.completed_at = db.scalar(text("SELECT clock_timestamp()"))
    db.flush()
    return result
