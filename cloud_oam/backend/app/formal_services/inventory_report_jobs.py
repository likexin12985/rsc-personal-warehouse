"""Create and finish inventory exports from a current, proved formal snapshot.

The caller owns each transaction. The report routes remain disabled by default
until their worker and private object-storage deployment are verified.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import json
import re
import uuid
from uuid import UUID

from formal_file_integrity import (
    FILE_METADATA_SCHEMA,
    FileUploadIntentInput,
    StoredObjectHead,
    _head_manifest_sha256,
    _prepare_upload,
    _storage_key,
    _upload_request_hash,
    _validate_intent_metadata,
    _validate_object_head,
)

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal, lock_formal_principal_graph
from ..foundation_models import FileJob, FileObject
from ..models import User
from .audit_chain import append_audit_event
from .inventory_query import InventoryReadError
from .inventory_report_snapshot import InventoryReportSnapshot, capture_inventory_report_snapshot
from .inventory_report_workbook import MIME_TYPE, InventoryWorkbookError, render_inventory_workbook
from .file_storage import FileStorageAdapter


_IDEMPOTENCY = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_REQUEST_ID = re.compile(r"^[\x21-\x7e]{1,160}$", re.ASCII)
_REPORT_PARAMETERS = {"report": "inventory_balances", "filters": {}}
_RESULT_NAMESPACE = UUID("68286019-5c9e-4990-9447-9c3355c0e2fd")


@dataclass(frozen=True, slots=True)
class InventoryReportClaim:
    job_id: UUID
    status: str
    failure_code: str | None
    snapshot: InventoryReportSnapshot | None


def create_inventory_report_job(
    db: Session,
    *,
    actor: FormalPrincipal,
    idempotency_key: str,
    request_id: str,
) -> tuple[FileJob, bool]:
    """Return (job, replayed), flushing the job and audit in one transaction."""

    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY.fullmatch(idempotency_key):
        _fail("inventory_report_idempotency_invalid", 422, "幂等键无效")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        _fail("inventory_report_request_id_invalid", 422, "请求标识无效")
    lock_formal_principal_graph(db, (actor.user_id,))
    try:
        current = load_formal_principal(db, actor.user_id)
    except FormalAccessError:
        _fail("inventory_report_actor_not_current", 403, "当前账号没有有效的正式访问权限")
    user = db.scalar(select(User).where(User.id == current.user_id).execution_options(populate_existing=True))
    if (
        user is None or not user.is_active
        or user.authorization_version != current.authorization_version
        or current.person_id != actor.person_id
        or current.authorization_version != actor.authorization_version
    ):
        _fail("inventory_report_actor_stale", 409, "人员或权限版本已变化，请重新读取")
    if current.access_mode != "active":
        _fail("inventory_report_actor_inactive", 403, "当前账号不能申请报表")

    snapshot = capture_inventory_report_snapshot(db, actor=current)
    parameters = {"report": _REPORT_PARAMETERS["report"], "filters": {}}
    parameters_hash = _hash(parameters)
    frozen_scope = _frozen_scope(snapshot)
    storage_key = report_request_key(current.user_id, idempotency_key)
    existing = db.scalar(
        select(FileJob).where(FileJob.idempotency_key == storage_key).with_for_update()
    )
    if existing is not None:
        if (
            existing.job_type != "export"
            or existing.requested_by != current.user_id
            or existing.parameters_jsonb != parameters
            or existing.parameters_hash != parameters_hash
            or existing.export_authorization_version != current.authorization_version
            or existing.export_ledger_cursor != snapshot.ledger_cursor
            or existing.export_scope_jsonb != frozen_scope
        ):
            _fail("inventory_report_idempotency_conflict", 409, "幂等键已绑定另一份报表快照")
        return existing, True

    job = FileJob(
        job_type="export",
        requested_by=current.user_id,
        parameters_jsonb=parameters,
        parameters_hash=parameters_hash,
        idempotency_key=storage_key,
        status="queued",
        export_authorization_version=current.authorization_version,
        export_scope_jsonb=frozen_scope,
        export_ledger_cursor=snapshot.ledger_cursor,
        download_count=0,
    )
    db.add(job)
    db.flush()
    append_audit_event(
        db,
        stream_key="inventory",
        actor_user_id=current.user_id,
        action="inventory_report_export_requested",
        aggregate_type="file_job",
        aggregate_id=str(job.id),
        before_jsonb=None,
        after_jsonb={
            "report": "inventory_balances",
            "parameters_hash": parameters_hash,
            "authorization_version": current.authorization_version,
            "ledger_cursor": snapshot.ledger_cursor,
            "account_count": len(snapshot.account_ids),
        },
        request_id=request_id,
        occurred_at=datetime.now(timezone.utc),
    )
    return job, False


def report_request_key(user_id: str, idempotency_key: str) -> str:
    """Bind a recovery coordinate to its exact requester without exposing it."""

    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY.fullmatch(idempotency_key):
        _fail("inventory_report_idempotency_invalid", 422, "幂等键无效")
    return hashlib.sha256(
        f"inventory-report-export\0{user_id}\0{idempotency_key}".encode("utf-8")
    ).hexdigest()


def revalidate_inventory_report_job(
    db: Session,
    *,
    job: FileJob,
) -> tuple[FormalPrincipal, InventoryReportSnapshot]:
    """Recheck the frozen scope before a worker generates the result.

    A caller must lock the exact job first. This performs no state transition
    or object write, so a failed proof leaves the queue record unchanged.
    """

    if job.job_type != "export" or job.parameters_jsonb != _REPORT_PARAMETERS:
        _fail("inventory_report_job_shape_invalid", 503, "报表任务内容无效")
    if job.parameters_hash != _hash(_REPORT_PARAMETERS):
        _fail("inventory_report_job_hash_invalid", 503, "报表任务参数摘要不一致")
    scope = job.export_scope_jsonb
    if (
        not isinstance(scope, dict)
        or set(scope) != {"version", "account_ids", "assignment_ids"}
        or type(scope["version"]) is not int
        or scope["version"] != 1
    ):
        _fail("inventory_report_job_scope_invalid", 503, "报表任务范围无效")
    account_ids = _scope_ids(scope["account_ids"])
    assignment_ids = _scope_ids(scope["assignment_ids"])
    if type(job.export_ledger_cursor) is not int or job.export_ledger_cursor < 0:
        _fail("inventory_report_job_cursor_invalid", 503, "报表任务账本游标无效")
    if type(job.export_authorization_version) is not int or job.export_authorization_version <= 0:
        _fail("inventory_report_job_authorization_invalid", 503, "报表任务授权版本无效")
    lock_formal_principal_graph(db, (job.requested_by,))
    try:
        current = load_formal_principal(db, job.requested_by)
    except FormalAccessError:
        _fail("inventory_report_actor_not_current", 403, "申请人当前没有有效的正式访问权限")
    user = db.scalar(select(User).where(User.id == current.user_id).execution_options(populate_existing=True))
    if (
        user is None or not user.is_active
        or current.access_mode != "active"
        or current.authorization_version != job.export_authorization_version
        or user.authorization_version != current.authorization_version
    ):
        _fail("inventory_report_actor_stale", 409, "申请人的人员或权限版本已变化")
    snapshot = capture_inventory_report_snapshot(
        db,
        actor=current,
        expected_cursor=job.export_ledger_cursor,
        expected_account_ids=account_ids,
    )
    if snapshot.assignment_ids != assignment_ids:
        _fail("inventory_report_assignment_changed", 409, "申请人的角色授权已变化")
    return current, snapshot


def claim_inventory_report_job(
    db: Session,
    *,
    job_id: UUID,
    request_id: str,
) -> InventoryReportClaim:
    """Claim one queued job or terminally reject a stale request.

    The caller commits before any object write. The later result transaction
    must run revalidation again; this claim alone does not authorize export.
    """

    if not isinstance(job_id, UUID) or not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        _fail("inventory_report_claim_invalid", 422, "报表任务领取参数无效")
    preview = db.scalar(select(FileJob).where(FileJob.id == job_id))
    if preview is None or preview.job_type != "export":
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    requester_id = preview.requested_by
    # Match the request path's principal-then-job lock order.
    lock_formal_principal_graph(db, (requester_id,))
    job = db.scalar(
        select(FileJob).where(FileJob.id == job_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.status != "queued" or job.requested_by != requester_id:
        _fail("inventory_report_job_not_queued", 409, "报表任务已被领取")
    failure_code = None
    snapshot = None
    try:
        _, snapshot = revalidate_inventory_report_job(db, job=job)
    except InventoryReadError as exc:
        failure_code = exc.code
    db_time = db.scalar(select(func.current_timestamp()))
    if not isinstance(db_time, datetime):
        _fail("inventory_report_clock_unavailable", 503, "数据库时间不可用")
    job.status = "running"
    job.started_at = db_time
    db.flush()
    if failure_code is not None:
        job.status = "failed"
        job.completed_at = db_time
        job.error_detail = failure_code
        db.flush()
    append_audit_event(
        db,
        stream_key="inventory",
        actor_user_id=job.requested_by,
        action="inventory_report_export_claimed" if failure_code is None else "inventory_report_export_rejected",
        aggregate_type="file_job",
        aggregate_id=str(job.id),
        before_jsonb={"status": "queued"},
        after_jsonb={"status": job.status, "failure_code": failure_code},
        request_id=request_id,
        occurred_at=datetime.now(timezone.utc),
    )
    return InventoryReportClaim(
        job_id=job.id,
        status=job.status,
        failure_code=failure_code,
        snapshot=snapshot,
    )


def finish_inventory_report_job(
    db: Session,
    *,
    job_id: UUID,
    storage: FileStorageAdapter,
    request_id: str,
    allow_new_put: bool,
) -> tuple[FileJob, bool]:
    """Bind one verified, write-once private XLSX to the exact running job.

    A lost database acknowledgement may be retried: the object key is derived
    from the job ID and the storage adapter accepts only an exact HEAD match.
    The caller commits the file, result and audit together.
    """

    if (not isinstance(job_id, UUID) or not isinstance(request_id, str)
        or not _REQUEST_ID.fullmatch(request_id) or type(allow_new_put) is not bool):
        _fail("inventory_report_finish_invalid", 422, "报表完成参数无效")
    if storage.provider_code != "aliyun_oss_v2":
        _fail("inventory_report_storage_invalid", 503, "报表私有存储不可用")
    preview = db.scalar(select(FileJob).where(FileJob.id == job_id))
    if preview is None or preview.job_type != "export":
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    requester_id = preview.requested_by
    lock_formal_principal_graph(db, (requester_id,))
    job = db.scalar(
        select(FileJob).where(FileJob.id == job_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if job is None or job.requested_by != requester_id:
        _fail("inventory_report_job_not_found", 404, "报表任务不存在")
    if job.status == "succeeded":
        _validate_existing_result(db, job, storage=storage)
        return job, True
    if job.status != "running":
        _fail("inventory_report_job_not_running", 409, "报表任务未处于生成状态")
    try:
        actor, snapshot = revalidate_inventory_report_job(db, job=job)
        payload = render_inventory_workbook(snapshot.lines, ledger_cursor=snapshot.ledger_cursor)
    except (InventoryReadError, InventoryWorkbookError) as exc:
        _fail_running_job(db, job, exc, request_id=request_id)
        return job, False
    digest = hashlib.sha256(payload).hexdigest()
    file_id = uuid.uuid5(_RESULT_NAMESPACE, str(job.id))
    key = _storage_key("inventory_report_export", file_id)
    if allow_new_put:
        head = storage.put_report_object(
            storage_key=key, file_id=str(file_id), sha256=digest, payload=payload,
        )
    else:
        # A prior PUT acknowledgement was lost or its result commit is
        # uncertain. Read the exact object first; never issue a second PUT.
        head = storage.head_object(storage_key=key)
        if (not isinstance(head, StoredObjectHead) or not isinstance(head.etag, str)
            or head.etag.strip('"').lower() != hashlib.md5(payload).hexdigest()):
            _fail("inventory_report_result_unknown", 503, "报表对象回读与原结果不一致")
    try:
        _, final_snapshot = revalidate_inventory_report_job(db, job=job)
        if final_snapshot != snapshot:
            _fail("inventory_report_snapshot_changed", 409, "报表生成期间库存快照已变化")
    except InventoryReadError as exc:
        _fail_running_job(db, job, exc, request_id=request_id)
        return job, False
    db_time = db.scalar(select(func.current_timestamp()))
    if not isinstance(db_time, datetime):
        _fail("inventory_report_clock_unavailable", 503, "数据库时间不可用")
    filename = f"RSC库存余额_{job.export_ledger_cursor}_{job.id.hex[:8]}.xlsx"
    prepared = _prepare_upload(
        FileUploadIntentInput(
            purpose="inventory_report_export",
            original_filename=filename,
            size_bytes=len(payload),
            mime_type=MIME_TYPE,
            sha256=digest,
        ),
        maximum_size_bytes=20 * 1024 * 1024,
    )
    metadata = {
        "authorization_version": actor.authorization_version,
        "file_id": str(file_id),
        "idempotency_key_hash": job.idempotency_key,
        "provider": storage.provider_code,
        "purpose": "inventory_report_export",
        "request_sha256": _upload_request_hash(prepared),
        "schema": FILE_METADATA_SCHEMA,
        "storage_key": key,
        "uploader_person_id": str(actor.person_id),
        "uploader_user_id": actor.user_id,
    }
    file = FileObject(
        id=file_id,
        storage_key=key,
        sha256=digest,
        size_bytes=len(payload),
        mime_type=MIME_TYPE,
        original_filename=filename,
        uploaded_by=actor.user_id,
        status="pending",
        metadata_jsonb=metadata,
        created_at=db_time,
    )
    _validate_object_head(file, head)
    db.add(file)
    db.flush()
    checked_time = db_time if db_time.tzinfo is not None else db_time.replace(tzinfo=timezone.utc)
    file.status = "available"
    file.metadata_jsonb = {
        **metadata,
        "completion": {
            "etag_sha256": hashlib.sha256(head.etag.encode("utf-8")).hexdigest(),
            "head_manifest_sha256": _head_manifest_sha256(head),
            "verified_at": checked_time.isoformat(),
        },
    }
    db.flush()
    job.status = "succeeded"
    job.completed_at = db_time
    job.result_file_id = file.id
    job.result_sha256 = digest
    job.result_size_bytes = len(payload)
    db.flush()
    append_audit_event(
        db,
        stream_key="inventory",
        actor_user_id=actor.user_id,
        action="inventory_report_export_succeeded",
        aggregate_type="file_job",
        aggregate_id=str(job.id),
        before_jsonb={"status": "running"},
        after_jsonb={"status": "succeeded", "file_id": str(file.id),
                     "sha256": digest, "size_bytes": len(payload)},
        request_id=request_id,
        occurred_at=datetime.now(timezone.utc),
    )
    return job, False


def _validate_existing_result(db: Session, job: FileJob, *, storage: FileStorageAdapter) -> FileObject:
    file = db.scalar(select(FileObject).where(FileObject.id == job.result_file_id))
    if (
        file is None or file.status != "available"
        or file.uploaded_by != job.requested_by
        or file.metadata_jsonb.get("purpose") != "inventory_report_export"
        or file.metadata_jsonb.get("idempotency_key_hash") != job.idempotency_key
        or file.metadata_jsonb.get("authorization_version") != job.export_authorization_version
        or file.sha256 != job.result_sha256
        or file.size_bytes != job.result_size_bytes
        or file.id != uuid.uuid5(_RESULT_NAMESPACE, str(job.id))
    ):
        _fail("inventory_report_result_invalid", 503, "报表结果绑定无效")
    metadata = _validate_intent_metadata(file, allow_completed=True)
    head = storage.head_object(storage_key=file.storage_key)
    _validate_object_head(file, head)
    if metadata["completion"]["head_manifest_sha256"] != _head_manifest_sha256(head):
        _fail("inventory_report_result_invalid", 503, "报表结果对象已变化")
    return file


def _fail_running_job(db: Session, job: FileJob, exc: Exception, *, request_id: str) -> None:
    db_time = db.scalar(select(func.current_timestamp()))
    if not isinstance(db_time, datetime):
        _fail("inventory_report_clock_unavailable", 503, "数据库时间不可用")
    job.status = "failed"
    job.completed_at = db_time
    job.error_detail = exc.code if isinstance(exc, InventoryReadError) else "inventory_report_render_invalid"
    db.flush()
    append_audit_event(
        db,
        stream_key="inventory",
        actor_user_id=job.requested_by,
        action="inventory_report_export_failed",
        aggregate_type="file_job",
        aggregate_id=str(job.id),
        before_jsonb={"status": "running"},
        after_jsonb={"status": "failed", "failure_code": job.error_detail},
        request_id=request_id,
        occurred_at=datetime.now(timezone.utc),
    )


def _frozen_scope(snapshot: InventoryReportSnapshot) -> dict[str, object]:
    return {
        "version": 1,
        "account_ids": [str(value) for value in snapshot.account_ids],
        "assignment_ids": [str(value) for value in snapshot.assignment_ids],
    }


def _hash(document: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _scope_ids(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list) or len(value) > 20000:
        _fail("inventory_report_job_scope_invalid", 503, "报表任务范围无效")
    try:
        parsed = tuple(UUID(item) for item in value if isinstance(item, str))
    except ValueError:
        _fail("inventory_report_job_scope_invalid", 503, "报表任务范围无效")
    if len(parsed) != len(value) or len(set(parsed)) != len(parsed) or parsed != tuple(sorted(parsed, key=str)):
        _fail("inventory_report_job_scope_invalid", 503, "报表任务范围无效")
    return parsed


def _fail(code: str, status_code: int, message: str) -> None:
    raise InventoryReadError(code=code, status_code=status_code, message=message)
