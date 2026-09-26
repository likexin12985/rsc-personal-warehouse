"""Scoped management reads of original imports after requester authority changes.

This is an inspection path, never a substitute principal for confirmation or
downloads. Missing/active jobs grant no retry or local-record deletion rights.
"""

from uuid import UUID

from sqlalchemy import select

from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, FileJob, FileObject
from ..inventory_models import StockLocation
from ..stocktake_models import FormalStocktakeTask, FormalStocktakeScope, StocktakeScopeCountCompletion
from . import opening_stocktake as opening
from .audit_chain import verify_audit_event_in_read_snapshot
from .formal_files import is_available_formal_file_for_purpose
from .opening_count_import_document import load_import_binding, load_import_preview
from .opening_count_import_intake import opening_import_request_key
from .opening_count_import_jobs import OpeningCountImportJobError, _clean
from .opening_observation_disposition import _task_principal_user_ids
from .opening_stocktake_count import lock_opening_count_coordinates


def _unavailable():
    raise OpeningCountImportJobError("opening_import_management_evidence_unavailable", 412,
                                    "原导入证据不完整，请保留恢复记录")


def _terminal_audit(db, job, binding):
    if job.status in {"queued", "prevalidating", "awaiting_confirmation"}:
        return None
    if job.status not in {"succeeded", "failed", "cancelled"} or job.completed_at is None:
        _unavailable()
    if job.status == "succeeded":
        action = "opening_count_import_confirmed"
    elif job.error_detail == "opening_import_prevalidation_failed":
        action = "opening_count_import_error_published"
    elif job.error_detail in {"opening_import_cancelled", "opening_import_context_changed", "opening_import_source_invalid"}:
        action = "opening_count_import_terminated"
    else:
        _unavailable()
    events = list(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == "file_job",
        AuditEvent.aggregate_id == str(job.id), AuditEvent.action == action).limit(2)))
    if len(events) != 1:
        _unavailable()
    event = events[0]
    detail = event.after_jsonb
    if event.stream_key != "inventory" or not isinstance(detail, dict):
        _unavailable()
    if action == "opening_count_import_confirmed":
        preview = load_import_preview(job.import_preview_jsonb, binding=binding)
        completion = db.get(StocktakeScopeCountCompletion, job.import_completion_id)
        if (preview.count is None or completion is None or event.actor_user_id != job.requested_by
                or completion.task_id != binding.task_id or completion.round_id != binding.round_id
                or completion.scope_id != binding.scope_id or completion.completed_by_user_id != job.requested_by
                or completion.authorization_version != binding.authorization_version
                or completion.idempotency_key_hash != binding.count_key_sha256
                or completion.request_sha256 != preview.count.request_sha256
                or not event.occurred_at <= completion.completed_at <= job.completed_at
                or detail != {"confirmed_by": job.requested_by, "source_sha256": binding.source_sha256,
                    "count_key_sha256": binding.count_key_sha256, "request_sha256": preview.count.request_sha256}):
            _unavailable()
    elif action == "opening_count_import_terminated":
        expected_actor = job.requested_by if job.status == "cancelled" else None
        if (job.import_completion_id is not None or event.actor_user_id != expected_actor
                or event.occurred_at != job.completed_at
                or detail != {"status": job.status, "reason": job.error_detail,
                    "source_file_id": str(binding.source_file_id), "error_sha256": job.import_error_sha256,
                    "error_object_state": "unknown_unpublished" if job.import_error_sha256 else "not_prepared"}):
            _unavailable()
    else:
        error_file = db.get(FileObject, job.error_file_id)
        if (job.status != "failed" or job.import_completion_id is not None or event.actor_user_id != job.requested_by
                or event.occurred_at < job.completed_at
                or not is_available_formal_file_for_purpose(error_file, purpose="opening_count_import_error", uploader_user_id=job.requested_by)
                or error_file.sha256 != job.import_error_sha256 or error_file.size_bytes != job.import_error_size_bytes
                or error_file.metadata_jsonb.get("idempotency_key_hash") != job.idempotency_key
                or error_file.metadata_jsonb.get("authorization_version") != binding.authorization_version
                or detail != {"status": "failed", "error_file_id": str(error_file.id), "error_sha256": error_file.sha256}):
            _unavailable()
    verify_audit_event_in_read_snapshot(db, stream_key="inventory", event_id=event.id)
    return event.id


def read_opening_import_management_recovery(db, *, actor, task_id, round_id, scope_id, source_file_id, idempotency_key):
    _clean(db)
    if not isinstance(actor, FormalPrincipal) or any(type(v) is not UUID or not v.int for v in (task_id, round_id, scope_id, source_file_id)):
        raise OpeningCountImportJobError("opening_import_management_coordinates_invalid", 422, "需要准确的原任务与文件坐标")
    source = db.get(FileObject, source_file_id)
    if source is None:
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "原导入任务未找到，请保留恢复记录")
    key = opening_import_request_key(source.uploaded_by, idempotency_key)
    lock_opening_count_coordinates(db, task_id=task_id, round_id=round_id, idempotency_key=key)
    task = db.scalar(select(FormalStocktakeTask).where(FormalStocktakeTask.id == task_id)
                     .with_for_update().execution_options(populate_existing=True))
    if task is None or task.task_type != "opening":
        _unavailable()
    users = _task_principal_user_ids(db, task_id=task_id, supplied_user_ids=(actor.user_id, source.uploaded_by))
    lock_formal_principal_graph(db, tuple(sorted(users)))
    scope = db.scalar(select(FormalStocktakeScope).where(FormalStocktakeScope.id == scope_id,
                                                       FormalStocktakeScope.task_id == task_id))
    location = db.get(StockLocation, scope.location_id) if scope is not None else None
    if location is None:
        _unavailable()
    try:
        current = opening._require_current_actor(db, actor, now=opening._database_now(db))
        grant = opening._authorize_scope_dimensions(db, actor=current, task_region_org_id=task.region_org_id,
            owner_org_id=scope.owner_org_id, location_owner_org_id=location.owner_org_id)
        if not all(opening._grant_allows(db, current, grant, "stocktake", "read",
                target_scope_type="organization", target_scope_id=str(org_id))
                for org_id in {task.region_org_id, scope.owner_org_id, location.owner_org_id}):
            raise OpeningCountImportJobError("opening_import_management_forbidden", 403,
                                            "当前管理身份没有原范围的盘点读取权限")
    except opening.OpeningStocktakeError as exc:
        raise OpeningCountImportJobError("opening_import_management_forbidden", exc.http_status_code,
                                        "只有当前有权的总部或本区域负责人可以核验原导入") from None
    query = select(FileJob).where(FileJob.job_type == "import", FileJob.idempotency_key == key,
                                  FileJob.requested_by == source.uploaded_by)
    job = db.scalar(query.execution_options(populate_existing=True))
    if job is None:
        raise OpeningCountImportJobError("opening_import_job_not_found", 404, "原导入任务未找到，请保留恢复记录")
    binding = load_import_binding(job.import_binding_jsonb)
    if (binding.task_id, binding.round_id, binding.scope_id, binding.source_file_id) != (task_id, round_id, scope_id, source_file_id):
        _unavailable()
    # Never take another task's job lock for conflicting caller coordinates.
    job = db.scalar(query.with_for_update().execution_options(populate_existing=True))
    source = db.scalar(select(FileObject).where(FileObject.id == source_file_id).with_for_update().execution_options(populate_existing=True))
    if (not is_available_formal_file_for_purpose(source, purpose="opening_count_import", uploader_user_id=job.requested_by)
            or source.sha256 != binding.source_sha256
            or source.metadata_jsonb.get("authorization_version") != binding.authorization_version):
        _unavailable()
    original_person = UUID(source.metadata_jsonb["uploader_person_id"])
    if not original_person.int:
        _unavailable()
    terminal_audit_id = _terminal_audit(db, job, binding)
    return dict(schema_version="rsc.opening_import_management_recovery.v1",
        reviewer_person_id=current.person_id, reviewer_authorization_version=current.authorization_version,
        actor_person_id=original_person, authorization_version=binding.authorization_version,
        task_id=task_id, round_id=round_id, scope_id=scope_id, source_file_id=source_file_id,
        source_sha256=source.sha256, size_bytes=source.size_bytes, job_id=job.id,
        status=job.status, completion_id=job.import_completion_id,
        terminal_audit_id=terminal_audit_id, terminal_verified=terminal_audit_id is not None,
        automatic_retry_allowed=False)
