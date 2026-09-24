"""Private, requester-only inventory report download intent and audit boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, _scope_covers, load_formal_principal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, FileJob, FileObject
from ..models import User
from . import inventory_query
from .audit_chain import append_audit_event
from .file_storage import FileStorageAdapter
from .formal_files import _validate_download_intent
from .inventory_query import InventoryReadError
from .inventory_report_jobs import _scope_ids, _validate_existing_result, report_request_key
from .inventory_report_snapshot import qualified_report_grants


_REQUEST_ID = re.compile(r"^[\x21-\x7e]{1,160}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class InventoryReportDownload:
    job_id: UUID
    file_id: UUID
    url: str
    expires_at: datetime
    download_count: int


@dataclass(frozen=True, slots=True)
class InventoryReportJobStatus:
    job_id: UUID
    status: str
    created_at: datetime
    completed_at: datetime | None
    download_count: int
    file_available: bool


def get_inventory_report_job_status(
    db: Session, *, actor: FormalPrincipal, job_id: UUID,
) -> InventoryReportJobStatus:
    _, job = _authorized_report_job(db, actor=actor, job_id=job_id, for_update=False)
    file_available = bool(
        job.status == "succeeded"
        and job.result_file_id is not None
        and db.scalar(
            select(FileObject.id).where(
                FileObject.id == job.result_file_id,
                FileObject.status == "available",
            )
        ) is not None
    )
    return InventoryReportJobStatus(
        job_id=job.id,
        status=job.status,
        created_at=job.created_at,
        completed_at=job.completed_at,
        download_count=job.download_count,
        file_available=file_available,
    )


def recover_inventory_report_job_status(
    db: Session, *, actor: FormalPrincipal, idempotency_key: str,
) -> InventoryReportJobStatus:
    """Reread a lost application response by its original requester-bound key."""

    request_key = report_request_key(actor.user_id, idempotency_key)
    job_id = db.scalar(
        select(FileJob.id).where(
            FileJob.idempotency_key == request_key,
            FileJob.job_type == "export",
            FileJob.requested_by == actor.user_id,
        )
    )
    if job_id is None:
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    return get_inventory_report_job_status(db, actor=actor, job_id=job_id)


def create_inventory_report_download_intent(
    db: Session,
    *,
    actor: FormalPrincipal,
    job_id: UUID,
    storage: FileStorageAdapter,
    request_id: str,
    ttl_seconds: int,
) -> InventoryReportDownload:
    """Sign one private URL; increment intent count and audit in one transaction.

    Stock movements after report generation do not invalidate a historical
    file. The requester must still hold the exact frozen role assignments and
    be authorized for every account captured by that file.
    """

    if not isinstance(job_id, UUID) or not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        _fail("inventory_report_download_request_invalid", 422, "下载请求标识无效")
    if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 600:
        _fail("inventory_report_download_ttl_invalid", 422, "下载有效期无效")
    if storage.provider_code != "aliyun_oss_v2":
        _fail("inventory_report_storage_invalid", 503, "报表私有存储不可用")
    current, job = _authorized_report_job(db, actor=actor, job_id=job_id, for_update=True)
    if job.status != "succeeded":
        _fail("inventory_report_download_not_available", 404, "报表结果尚不可下载")
    duplicate = db.scalar(
        select(AuditEvent.id).where(
            AuditEvent.stream_key == "inventory",
            AuditEvent.action == "inventory_report_download_intent",
            AuditEvent.aggregate_type == "file_job",
            AuditEvent.aggregate_id == str(job.id),
            AuditEvent.request_id == request_id,
        )
    )
    if duplicate is not None:
        _fail("inventory_report_download_request_replayed", 409, "该下载请求已处理，请使用新的请求标识")
    file = _validate_existing_result(db, job, storage=storage)
    now = datetime.now(timezone.utc)
    intent = storage.create_download_intent(
        storage_key=file.storage_key, ttl_seconds=ttl_seconds,
    )
    _validate_download_intent(intent, row=file, now=now, ttl_seconds=ttl_seconds)
    job.download_count += 1
    db.flush()
    append_audit_event(
        db,
        stream_key="inventory",
        actor_user_id=current.user_id,
        action="inventory_report_download_intent",
        aggregate_type="file_job",
        aggregate_id=str(job.id),
        before_jsonb={"download_count": job.download_count - 1},
        after_jsonb={"download_count": job.download_count, "file_id": str(file.id)},
        request_id=request_id,
        occurred_at=now,
    )
    return InventoryReportDownload(
        job_id=job.id,
        file_id=file.id,
        url=intent.url,
        expires_at=intent.expires_at,
        download_count=job.download_count,
    )


def _authorized_report_job(
    db: Session,
    *,
    actor: FormalPrincipal,
    job_id: UUID,
    for_update: bool,
) -> tuple[FormalPrincipal, FileJob]:
    if not isinstance(job_id, UUID):
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    preview = db.scalar(select(FileJob).where(FileJob.id == job_id))
    if preview is None or preview.job_type != "export" or preview.requested_by != actor.user_id:
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    lock_formal_principal_graph(db, (actor.user_id,))
    try:
        current = load_formal_principal(db, actor.user_id)
    except FormalAccessError:
        _fail("inventory_report_actor_not_current", 403, "当前账号没有有效的正式访问权限")
    user = db.scalar(select(User).where(User.id == current.user_id).execution_options(populate_existing=True))
    statement = select(FileJob).where(FileJob.id == job_id)
    if for_update:
        statement = statement.with_for_update()
    job = db.scalar(statement.execution_options(populate_existing=True))
    if job is None or job.requested_by != actor.user_id:
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    if (
        user is None or not user.is_active
        or current.access_mode != "active"
        or current.person_id != actor.person_id
        or current.authorization_version != actor.authorization_version
        or current.authorization_version != job.export_authorization_version
        or user.authorization_version != current.authorization_version
    ):
        _fail("inventory_report_actor_stale", 403, "当前人员或权限已变化")
    scope = job.export_scope_jsonb
    if (
        not isinstance(scope, dict)
        or set(scope) != {"version", "account_ids", "assignment_ids"}
        or type(scope["version"]) is not int
        or scope["version"] != 1
    ):
        _fail("inventory_report_job_scope_invalid", 503, "报表任务范围无效")
    account_ids = set(_scope_ids(scope["account_ids"]))
    assignment_ids = _scope_ids(scope["assignment_ids"])
    if assignment_ids != tuple(sorted((grant.assignment_id for grant in current.assignments), key=str)):
        _fail("inventory_report_assignment_changed", 403, "角色授权已变化")
    inventory_query._require_inventory_read(db, current)
    report_grants = qualified_report_grants(db, current)
    try:
        permitted = bool(report_grants) and current.allows(db, "report", "export")
    except FormalAccessError:
        permitted = False
    if not permitted:
        _fail("inventory_report_export_denied", 403, "当前没有库存报表导出权限")
    visible = {row.account.id: row for row in inventory_query._authorized_account_rows(db, actor=current)}
    if not account_ids.issubset(visible):
        _fail("inventory_report_scope_changed", 403, "报表中的库存账户已不在当前授权范围")
    for account_id in account_ids:
        target_id = str(visible[account_id].location_owner_org.id)
        try:
            covered = any(
                _scope_covers(db, grant.scope_type, grant.scope_id, "organization", target_id)
                for grant in report_grants
            ) and current.allows(
                db, "report", "export",
                target_scope_type="organization", target_scope_id=target_id,
            )
        except FormalAccessError:
            covered = False
        if not covered:
            _fail("inventory_report_scope_changed", 403, "报表中的库存账户已不在当前授权范围")
    return current, job


def _fail(code: str, status_code: int, message: str) -> None:
    raise InventoryReadError(code=code, status_code=status_code, message=message)
