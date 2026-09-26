"""Stop an import without changing count facts or erasing uncertain object evidence."""

from hashlib import sha256
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal, lock_formal_principal_graph
from ..foundation_models import FileJob, FileObject
from ..stocktake_models import FormalStocktakeTask, FormalStocktakeScope, StocktakeRound, StocktakeRecountScopeAssignment
from .audit_chain import append_audit_event
from .formal_files import is_available_formal_file_for_purpose
from .opening_count_import_document import load_import_binding
from .opening_count_import_intake import _request_id, opening_import_request_key
from .opening_count_import_jobs import (
    OpeningCountImportJobError, OpeningCountImportJobStatus, _clean, _current_actor, _lock_owned_import_job,
)
from .opening_count_import_source import AuthorizedOpeningCountSource
from .opening_count_import_workbook import OpeningCountImportFormatError, prevalidate_opening_count_workbook
from .opening_observation_disposition import _task_principal_user_ids
from .opening_stocktake_count import lock_opening_count_coordinates


ACTIVE = frozenset({"queued", "prevalidating", "awaiting_confirmation"})
FAILURE_CODES = frozenset({"opening_import_context_changed", "opening_import_source_invalid",
                           "opening_import_cancelled", "opening_import_prevalidation_failed"})


@dataclass(frozen=True, slots=True)
class OpeningImportContextSweep:
    checked: int
    next_after_id: UUID | None
    failed_ids: tuple[UUID, ...] = ()


def sweep_awaiting_opening_imports(session_factory, *, after_id=None, limit=100):
    """Check bounded UUID pages, including jobs waiting for human confirmation.

    A rotating cursor avoids repeatedly checking only the oldest healthy jobs.
    This worker maintenance path exposes no file content and performs no I/O.
    """
    if type(limit) is not int or not 1 <= limit <= 100 or (after_id is not None and not isinstance(after_id, UUID)):
        raise OpeningCountImportJobError("opening_import_sweep_invalid", 422, "导入巡检参数无效")
    with session_factory() as db:
        query = select(FileJob.id).where(FileJob.job_type == "import", FileJob.status == "awaiting_confirmation")
        if after_id is not None:
            query = query.where(FileJob.id > after_id)
        identifiers = list(db.scalars(query.order_by(FileJob.id).limit(limit)))
    failed = []
    for identifier in identifiers:
        try:
            with session_factory() as db:
                terminate_ineligible_opening_import(db, job_id=identifier,
                    request_id=f"opening-import-context-sweep:{identifier}")
                db.commit()
        except Exception:
            # A failed read/commit proves no terminal fact. Preserve its job
            # and let the next original-ID pass re-read authoritative state.
            failed.append(identifier)
    return OpeningImportContextSweep(len(identifiers),
        identifiers[-1] if len(identifiers) == limit else None, tuple(failed))


def _status(job):
    return OpeningCountImportJobStatus(job.id, job.status, job.import_completion_id)


def _finish(db, *, job, status, reason, actor_id, request_id):
    before = job.status
    now = db.scalar(text("SELECT clock_timestamp()"))
    job.status, job.error_detail, job.completed_at = status, reason, now
    # Keep prepared preview/hash/size even if the original PUT may be in flight.
    # A terminal import can neither execute a count nor publish that object.
    db.flush()
    append_audit_event(db, stream_key="inventory", actor_user_id=actor_id,
        action="opening_count_import_terminated", aggregate_type="file_job", aggregate_id=str(job.id),
        before_jsonb={"status": before}, after_jsonb={"status": status, "reason": reason,
            "source_file_id": job.import_binding_jsonb["source_file_id"],
            "error_sha256": job.import_error_sha256,
            "error_object_state": "unknown_unpublished" if job.import_error_sha256 else "not_prepared"},
        request_id=request_id, occurred_at=now, created_at=now)
    return _status(job)


def cancel_opening_count_import(
    db: Session, *, actor: FormalPrincipal, job_id: UUID, idempotency_key: str, request_id: str,
) -> tuple[OpeningCountImportJobStatus, bool]:
    _request_id(request_id)
    job, _, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
    if job.idempotency_key != opening_import_request_key(current.user_id, idempotency_key):
        raise OpeningCountImportJobError("opening_import_idempotency_conflict", 409, "请使用原导入请求键")
    if job.status == "cancelled":
        return _status(job), True
    if job.status not in ACTIVE:
        raise OpeningCountImportJobError("opening_import_cannot_cancel", 409, "任务已结束，请读取原任务结果")
    return _finish(db, job=job, status="cancelled", reason="opening_import_cancelled",
        actor_id=current.user_id, request_id=request_id), False


def _lock_for_worker(db, job_id):
    """Internal worker only: locking is possible even after the requester is disabled."""
    _clean(db)
    if not isinstance(job_id, UUID):
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    query = select(FileJob).where(FileJob.id == job_id, FileJob.job_type == "import")
    job = db.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    binding = load_import_binding(job.import_binding_jsonb)
    lock_opening_count_coordinates(db, task_id=binding.task_id, round_id=binding.round_id,
                                  idempotency_key=job.idempotency_key)
    db.scalar(select(FormalStocktakeTask).where(FormalStocktakeTask.id == binding.task_id).with_for_update())
    users = _task_principal_user_ids(db, task_id=binding.task_id, supplied_user_ids=(job.requested_by,))
    lock_formal_principal_graph(db, tuple(sorted(users)))
    return db.scalar(query.with_for_update().execution_options(populate_existing=True)), binding


def _current_context(db, job, binding):
    current = load_formal_principal(db, job.requested_by)
    _current_actor(db, current, binding)
    source = db.scalar(select(FileObject).where(FileObject.id == binding.source_file_id)
                       .with_for_update().execution_options(populate_existing=True))
    task = db.get(FormalStocktakeTask, binding.task_id, populate_existing=True)
    scope = db.scalar(select(FormalStocktakeScope).where(FormalStocktakeScope.id == binding.scope_id,
        FormalStocktakeScope.task_id == binding.task_id).execution_options(populate_existing=True))
    round_row = db.scalar(select(StocktakeRound).where(StocktakeRound.id == binding.round_id,
        StocktakeRound.task_id == binding.task_id).execution_options(populate_existing=True))
    if (not is_available_formal_file_for_purpose(source, purpose="opening_count_import", uploader_user_id=current.user_id)
            or source.sha256 != binding.source_sha256
            or source.metadata_jsonb.get("uploader_person_id") != str(current.person_id)
            or source.metadata_jsonb.get("authorization_version") != current.authorization_version
            or task is None or task.task_type != "opening" or task.status != "counting"
            or round_row is None or round_row.status != "counting"
            or round_row.round_no != task.current_round_no or scope is None):
        raise OpeningCountImportJobError("opening_import_context_changed", 412, "原任务、范围或来源已不再接受导入")
    assigned = (scope.assignee_user_id == current.user_id if round_row.round_no == 1 else
        bool(db.scalar(select(StocktakeRecountScopeAssignment.id).where(
            StocktakeRecountScopeAssignment.recount_case_id == round_row.recount_case_id,
            StocktakeRecountScopeAssignment.task_id == binding.task_id,
            StocktakeRecountScopeAssignment.scope_id == binding.scope_id,
            StocktakeRecountScopeAssignment.assignee_user_id == current.user_id))))
    target = (("person", str(scope.custodian_person_id_snapshot)) if scope.custodian_person_id_snapshot
              else ("organization", str(scope.owner_org_id)))
    if not assigned or not current.allows(db, "stocktake", "count", target_scope_type=target[0], target_scope_id=target[1]):
        raise OpeningCountImportJobError("opening_import_scope_forbidden", 403, "当前账号不再有原范围的盘点权限")
    return current


def terminate_ineligible_opening_import(
    db: Session, *, job_id: UUID, request_id: str,
) -> OpeningCountImportJobStatus | None:
    """Worker rechecks authoritative context; transport errors are not a failure reason."""
    _request_id(request_id)
    job, binding = _lock_for_worker(db, job_id)
    if job.status in {"succeeded", "failed", "cancelled"}:
        return _status(job)
    if job.status not in ACTIVE:
        raise OpeningCountImportJobError("opening_import_worker_state_invalid", 409, "导入正在确认，请读取原结果")
    try:
        _current_context(db, job, binding)
    except (FormalAccessError, OpeningCountImportJobError):
        return _finish(db, job=job, status="failed", reason="opening_import_context_changed",
                       actor_id=None, request_id=request_id)
    return None


def fail_invalid_opening_import_source(
    db: Session, *, job_id: UUID, source: AuthorizedOpeningCountSource, request_id: str,
) -> OpeningCountImportJobStatus:
    """Reproduce a definite template/container failure from the exact verified bytes."""
    _request_id(request_id)
    job, binding = _lock_for_worker(db, job_id)
    if job.status in {"succeeded", "failed", "cancelled"}:
        return _status(job)
    _current_context(db, job, binding)
    if (job.status != "prevalidating" or job.import_error_sha256 is not None
            or type(source) is not AuthorizedOpeningCountSource or not isinstance(source.data, bytes)
            or source.file_id != binding.source_file_id or source.source_sha256 != binding.source_sha256
            or source.size_bytes != len(source.data) or sha256(source.data).hexdigest() != binding.source_sha256):
        raise OpeningCountImportJobError("opening_import_source_failure_unproved", 412, "源文件失败原因尚未得到核实")
    try:
        prevalidate_opening_count_workbook(source.data)
    except OpeningCountImportFormatError:
        return _finish(db, job=job, status="failed", reason="opening_import_source_invalid",
                       actor_id=None, request_id=request_id)
    raise OpeningCountImportJobError("opening_import_source_failure_unproved", 412, "源文件并非格式不可读取")
