"""Read the exact owned import binding for human review, without object I/O.

Only source metadata and prevalidation counts are exposed. Blind-count book
quantities, observation rows, storage keys and persisted proof hashes stay out.
The ready flag is advisory; confirmation always revalidates the real workbook.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from ..foundation_models import FileObject
from ..stocktake_models import FormalStocktakeTask, FormalStocktakeScope, StocktakeRound, StocktakeRecountScopeAssignment
from .formal_files import is_available_formal_file_for_purpose
from .opening_count_import_document import load_import_preview
from .opening_count_import_jobs import OpeningCountImportJobError, _lock_owned_import_job
from .opening_count_import_termination import _current_context


@dataclass(frozen=True, slots=True)
class OpeningCountImportReview:
    job_id: UUID
    task_id: UUID
    round_id: UUID
    scope_id: UUID
    actor_person_id: UUID
    authorization_version: int
    source_file_id: UUID
    source_sha256: str
    size_bytes: int
    original_filename: str
    can_confirm: bool


def read_opening_count_import_review(db, *, actor, job_id):
    job, binding, current = _lock_owned_import_job(db, actor=actor, job_id=job_id)
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
        raise OpeningCountImportJobError("opening_import_scope_forbidden", 403, "当前账号不再是原范围的执行人")
    target = (("person", str(scope.custodian_person_id_snapshot)) if scope.custodian_person_id_snapshot
              else ("organization", str(scope.owner_org_id)))
    if not current.allows(db, "stocktake", "count", target_scope_type=target[0], target_scope_id=target[1]):
        raise OpeningCountImportJobError("opening_import_scope_forbidden", 403, "当前账号没有原范围的盘点权限")
    source = db.scalar(select(FileObject).where(FileObject.id == binding.source_file_id)
                       .with_for_update().execution_options(populate_existing=True))
    if (not is_available_formal_file_for_purpose(source, purpose="opening_count_import", uploader_user_id=current.user_id)
            or source.sha256 != binding.source_sha256
            or source.metadata_jsonb.get("uploader_person_id") != str(current.person_id)
            or source.metadata_jsonb.get("authorization_version") != current.authorization_version
            or not source.original_filename):
        raise OpeningCountImportJobError("opening_import_source_binding_changed", 412, "源文件与原任务不一致")
    can_confirm = False
    if job.status == "awaiting_confirmation":
        preview = load_import_preview(job.import_preview_jsonb, binding=binding)
        try:
            _current_context(db, job, binding)
        except OpeningCountImportJobError:
            pass
        else:
            task = db.get(FormalStocktakeTask, binding.task_id)
            can_confirm = bool(preview.count is not None and not preview.errors
                               and preview.count.task_version == task.version)
    return OpeningCountImportReview(job.id, binding.task_id, binding.round_id, binding.scope_id,
        current.person_id, current.authorization_version, source.id, source.sha256,
        source.size_bytes, source.original_filename, can_confirm)
