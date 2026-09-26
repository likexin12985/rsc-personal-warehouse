"""Requester-bound import creation and original-request recovery, without I/O."""

from hashlib import sha256
import json
import re
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import FileJob, FileObject
from ..stocktake_models import (
    FormalStocktakeTask, FormalStocktakeScope, StocktakeRound, StocktakeRecountScopeAssignment,
)
from .audit_chain import append_audit_event
from .formal_files import is_available_formal_file_for_purpose
from .opening_count_import_document import OpeningCountImportBinding
from .opening_count_import_jobs import (
    OpeningCountImportJobError, OpeningCountImportJobStatus, _clean, _current_actor,
    _lock_owned_import_job, read_opening_count_import_job,
)
from .opening_observation_disposition import _task_principal_user_ids
from .opening_stocktake_count import _storage_hash, lock_opening_count_coordinates


_KEY = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_TRACE = re.compile(r"^[\x21-\x7e]{1,160}$", re.ASCII)
PARAMETERS = {"import": "opening_count", "template_version": 1}
PARAMETERS_HASH = sha256(json.dumps(PARAMETERS, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def opening_import_request_key(user_id: str, idempotency_key: str) -> str:
    if (not isinstance(user_id, str) or not user_id or "\0" in user_id
            or not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key)):
        raise OpeningCountImportJobError("opening_import_idempotency_invalid", 422, "幂等键无效")
    return sha256(f"opening-count-import-v1\0{user_id}\0{idempotency_key}".encode()).hexdigest()


def _request_id(value):
    if not isinstance(value, str) or not _TRACE.fullmatch(value):
        raise OpeningCountImportJobError("opening_import_request_id_invalid", 422, "请求标识无效")
    return value


def create_opening_count_import_job(
    db: Session, *, actor: FormalPrincipal, source_file_id: UUID,
    task_id: UUID, round_id: UUID, scope_id: UUID, idempotency_key: str, request_id: str,
) -> tuple[UUID, bool]:
    """Persist one authorized queue record and audit in the caller's transaction."""
    _clean(db)
    _request_id(request_id)
    if not isinstance(actor, FormalPrincipal) or any(
        not isinstance(value, UUID) or value.int == 0
        for value in (source_file_id, task_id, round_id, scope_id)
    ):
        raise OpeningCountImportJobError("opening_import_request_invalid", 422, "导入任务坐标无效")
    key = opening_import_request_key(actor.user_id, idempotency_key)
    from .opening_import_admission import lock_import_admission, require_unsealed_import
    lock_import_admission(db, source_file_id, key)
    require_unsealed_import(db, source_file_id, key)
    lock_opening_count_coordinates(db, task_id=task_id, round_id=round_id, idempotency_key=key)
    existing = db.scalar(select(FileJob).where(FileJob.idempotency_key == key))
    if existing is not None:
        # A changed coordinate must not take a second task lock in an inverse
        # order. Reject it before locking the original job's task graph.
        expected = {"source_file_id": str(source_file_id), "task_id": str(task_id),
                    "round_id": str(round_id), "scope_id": str(scope_id)}
        if (existing.job_type != "import" or existing.requested_by != actor.user_id
                or any(existing.import_binding_jsonb.get(k) != v for k, v in expected.items())):
            raise OpeningCountImportJobError("opening_import_idempotency_conflict", 409,
                                            "原请求键已绑定其他导入，请恢复原任务")
        job, _, _ = _lock_owned_import_job(db, actor=actor, job_id=existing.id)
        return job.id, True

    task = db.scalar(select(FormalStocktakeTask).where(FormalStocktakeTask.id == task_id)
                     .with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise OpeningCountImportJobError("opening_import_task_missing", 404, "盘点任务不存在")
    users = _task_principal_user_ids(db, task_id=task_id, supplied_user_ids=(actor.user_id,))
    lock_formal_principal_graph(db, tuple(sorted(users)))
    source = db.scalar(select(FileObject).where(FileObject.id == source_file_id)
                       .with_for_update().execution_options(populate_existing=True))
    if not is_available_formal_file_for_purpose(source, purpose="opening_count_import",
                                               uploader_user_id=actor.user_id):
        raise OpeningCountImportJobError("opening_import_source_unavailable", 404,
                                        "源文件不存在或未完成核验")
    binding = OpeningCountImportBinding(source_file_id=source.id, source_sha256=source.sha256,
        task_id=task_id, round_id=round_id, scope_id=scope_id,
        authorization_version=actor.authorization_version, count_key_sha256=_storage_hash(key))
    current = _current_actor(db, actor, binding)
    if (source.metadata_jsonb.get("authorization_version") != current.authorization_version
            or source.metadata_jsonb.get("uploader_person_id") != str(current.person_id)):
        raise OpeningCountImportJobError("opening_import_source_binding_changed", 412,
                                        "源文件上传身份或授权版本已变化")
    round_row = db.scalar(select(StocktakeRound).where(StocktakeRound.id == round_id,
        StocktakeRound.task_id == task_id).execution_options(populate_existing=True))
    scope = db.scalar(select(FormalStocktakeScope).where(FormalStocktakeScope.id == scope_id,
        FormalStocktakeScope.task_id == task_id).execution_options(populate_existing=True))
    if (task.task_type != "opening" or task.status != "counting" or round_row is None
            or round_row.status != "counting" or round_row.round_no != task.current_round_no
            or scope is None):
        raise OpeningCountImportJobError("opening_import_scope_not_counting", 409,
                                        "当前任务、轮次或范围不接受盘点导入")
    assigned = scope.assignee_user_id == current.user_id if round_row.round_no == 1 else bool(
        db.scalar(select(StocktakeRecountScopeAssignment.id).where(
            StocktakeRecountScopeAssignment.recount_case_id == round_row.recount_case_id,
            StocktakeRecountScopeAssignment.task_id == task_id,
            StocktakeRecountScopeAssignment.scope_id == scope_id,
            StocktakeRecountScopeAssignment.assignee_user_id == current.user_id)))
    target = (("person", str(scope.custodian_person_id_snapshot)) if scope.custodian_person_id_snapshot
              else ("organization", str(scope.owner_org_id)))
    if not assigned or not current.allows(db, "stocktake", "count",
        target_scope_type=target[0], target_scope_id=target[1]):
        raise OpeningCountImportJobError("opening_import_scope_forbidden", 403,
                                        "只有当前范围指定且有权限的执行人可以导入")
    job = FileJob(job_type="import", requested_by=current.user_id, parameters_jsonb=dict(PARAMETERS),
        parameters_hash=PARAMETERS_HASH, idempotency_key=key, status="queued",
        import_binding_jsonb=binding.model_dump(mode="json"))
    db.add(job)
    db.flush()
    now = db.scalar(text("SELECT clock_timestamp()"))
    append_audit_event(db, stream_key="inventory", actor_user_id=current.user_id,
        action="opening_count_import_requested", aggregate_type="file_job", aggregate_id=str(job.id),
        before_jsonb=None, after_jsonb={"status": "queued", "source_file_id": str(source.id),
            "source_sha256": source.sha256, "task_id": str(task_id), "round_id": str(round_id),
            "scope_id": str(scope_id), "authorization_version": current.authorization_version},
        request_id=request_id, occurred_at=now, created_at=now)
    return job.id, False


def recover_opening_count_import_job(
    db: Session, *, actor: FormalPrincipal, idempotency_key: str,
) -> OpeningCountImportJobStatus:
    _clean(db)
    if not isinstance(actor, FormalPrincipal):
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    key = opening_import_request_key(actor.user_id, idempotency_key)
    job_id = db.scalar(select(FileJob.id).where(FileJob.job_type == "import",
        FileJob.requested_by == actor.user_id, FileJob.idempotency_key == key))
    if job_id is None:
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "导入任务不存在")
    return read_opening_count_import_job(db, actor=actor, job_id=job_id)
