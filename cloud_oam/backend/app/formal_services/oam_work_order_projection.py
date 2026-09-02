"""Publish one validated OAM edge snapshot into the formal work-order mirror.

The edge receiver owns transport authentication and isolated staging only.
This module is the separate cloud-side projection boundary: it never calls
OAM, never infers a person from names or phone numbers, and never interprets a
rolling-window absence as a delete.  A run either refreshes every observed
work order in one transaction or leaves the prior formal projection untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Final
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..demand_models import OamWorkOrder
from ..external_sync_scope_lock import lock_external_sync_scope
from ..foundation_models import (
    ExternalObject,
    ExternalObjectMapping,
    ExternalObjectVersion,
    Organization,
    Person,
    SourceSystem,
    SyncBatch,
    SyncConflict,
    SyncInboxEvent,
    SyncRun,
)
from ..models import (
    ExternalSyncCurrentRecord,
    ExternalSyncSnapshot,
    ExternalSyncSnapshotBatch,
    ExternalSyncSnapshotRecord,
)
from ..schemas import EdgeSyncSnapshotCompleteIn


SOURCE_SYSTEM_CODE: Final[str] = "starcharge_oam"
SOURCE_SYSTEM_MODE: Final[str] = "read_only"
PROJECTION_SCHEMA: Final[str] = "rsc.oam_work_order_projection.v1"
WORK_ORDER_ENTITY: Final[str] = "work_order"
EMPLOYEE_ENTITY: Final[str] = "employee"
EXPECTED_SNAPSHOT_ENTITIES: Final[frozenset[str]] = frozenset({"work_order"})
WORK_ORDER_PAYLOAD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "id",
        "code",
        "statusCode",
        "executorId",
        "authCompanyId",
        "province",
        "updateTime",
    }
)
STATUS_BY_SOURCE: Final[dict[str, str]] = {
    "to_be_create": "pending",
    "create": "pending",
    "wait_receive": "pending",
    "wait_connect": "active",
    "wait_process": "active",
    "processing": "active",
    "transferring": "active",
    "wait_client_accept": "active",
    "wait_platform_accept": "active",
    "wait_source_accept": "active",
    "wait_install_command": "active",
    "process_finish": "completed",
    "client_accept_pass": "completed",
    "platform_accept_pass": "completed",
    "source_accept_pass": "completed",
    "end": "completed",
    "closed": "closed",
    "stopping": "cancelled",
    "stopped": "cancelled",
    "rejected": "cancelled",
    "transfer_reject": "cancelled",
    "hang": "inactive",
}
SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
OAM_SOURCE_DATETIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
    r"(?:[Zz]|[+-]\d{2}:\d{2})?$"
)
SOURCE_VERSION_PREFIX: Final[str] = "wo-v1:"
PROJECTION_SOURCE_VERSION_PREFIX: Final[str] = "wo-v2:"
PROJECTION_SOURCE_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^wo-v2:([0-9a-f]{64}):([0-9a-f]{64})$"
)
OAM_SOURCE_TIMEZONE: Final[timezone] = timezone(timedelta(hours=8))


class OamWorkOrderProjectionError(RuntimeError):
    """Stable, payload-free failure from the formal projection boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class OamWorkOrderProjectionResult:
    sync_run_id: uuid.UUID
    snapshot_id: str
    status: str
    projected_records: int
    created_records: int
    updated_records: int
    unchanged_records: int
    conflict_records: int
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class _IncomingWorkOrder:
    business_key: str
    external_id: str
    work_order_no: str
    source_status: str
    projected_status: str
    executor_external_id: str
    source_updated_at: datetime
    source_version: str
    raw_payload: dict[str, Any]
    raw_payload_sha256: str


@dataclass(frozen=True, slots=True)
class _ProjectionPlan:
    incoming: _IncomingWorkOrder
    inbox_event: SyncInboxEvent
    person: Person
    organization: Organization
    external_object: ExternalObject | None
    current_version: ExternalObjectVersion | None
    work_order: OamWorkOrder | None
    projection_source_version: str
    projection_payload: dict[str, str]
    projection_sha256: str
    changed: bool


def publish_completed_work_order_snapshot(
    db: Session,
    *,
    snapshot_id: str,
    now: datetime | None = None,
) -> OamWorkOrderProjectionResult:
    """Validate and publish one completed edge snapshot in the caller transaction."""

    checked_snapshot_id = _required_text(snapshot_id, "snapshot_id", 64)
    snapshot = db.scalar(
        select(ExternalSyncSnapshot).where(
            ExternalSyncSnapshot.id == checked_snapshot_id
        )
    )
    if snapshot is None:
        _fail("oam_work_order_snapshot_not_found", "OAM工单暂存快照不存在")
    assert snapshot is not None
    _lock_projection_scope(db, snapshot)
    # Read transaction time only after the scope lock.  A worker that waited
    # behind a newer publisher must never close a version with an earlier time.
    published_at = _aware(now) if now is not None else _database_now(db)
    _validate_snapshot_header(snapshot, published_at)
    manifest = _validate_snapshot_manifest(snapshot)
    work_order_manifest = next(
        entity
        for entity in manifest.entities
        if entity.entity_type == WORK_ORDER_ENTITY
    )
    source = _require_source_system(db, snapshot)
    run_key = _run_key(snapshot)
    formal_scope_key = _formal_scope_key(snapshot)
    run = db.scalar(select(SyncRun).where(SyncRun.run_key == run_key))
    if run is not None:
        _validate_existing_run(run, source, snapshot, formal_scope_key)
        if run.status == "completed":
            _validate_completed_duplicate(
                db,
                source=source,
                snapshot=snapshot,
                run=run,
                record_count=work_order_manifest.final_record_count,
                body_sha256=work_order_manifest.final_sha256,
            )
            return OamWorkOrderProjectionResult(
                sync_run_id=run.id,
                snapshot_id=snapshot.snapshot_id,
                status="completed",
                projected_records=work_order_manifest.final_record_count,
                created_records=0,
                updated_records=0,
                unchanged_records=work_order_manifest.final_record_count,
                conflict_records=0,
                duplicate=True,
            )
        _reject_older_completed_run(db, source, snapshot, formal_scope_key)
    else:
        _reject_older_completed_run(db, source, snapshot, formal_scope_key)
        run = SyncRun(
            source_system_id=source.id,
            run_key=run_key,
            scope_key=formal_scope_key,
            mode=snapshot.sync_mode,
            watermark_from=None,
            watermark_to=_iso(snapshot.snapshot_at),
            status="validating",
            manifest_sha256=snapshot.manifest_sha256,
            started_at=published_at,
            completed_at=None,
            failure_code=None,
            failure_detail=None,
        )
        db.add(run)
        db.flush()

    _validate_staged_snapshot(db, snapshot, manifest)
    staged_rows = tuple(
        db.scalars(
            select(ExternalSyncCurrentRecord)
            .where(
                ExternalSyncCurrentRecord.source_instance
                == snapshot.source_instance,
                ExternalSyncCurrentRecord.scope_key == snapshot.scope_key,
                ExternalSyncCurrentRecord.entity_type == WORK_ORDER_ENTITY,
                ExternalSyncCurrentRecord.last_snapshot_id == snapshot.id,
            )
            .order_by(ExternalSyncCurrentRecord.business_key)
        ).all()
    )
    incoming = tuple(
        _incoming_work_order(row, snapshot=snapshot) for row in staged_rows
    )

    run.status = "validating"
    run.manifest_sha256 = snapshot.manifest_sha256
    run.completed_at = None
    run.failure_code = None
    run.failure_detail = None
    formal_batch = _upsert_formal_batch(
        db,
        run=run,
        record_count=work_order_manifest.final_record_count,
        body_sha256=work_order_manifest.final_sha256,
        received_at=snapshot.completed_at,
        validated_at=published_at,
    )
    events, event_replay_conflict_indexes = _upsert_inbox_events(
        db,
        source=source,
        run=run,
        batch=formal_batch,
        snapshot=snapshot,
        incoming=incoming,
    )

    event_replay_conflict_index_set = set(event_replay_conflict_indexes)
    event_replay_conflicts = [
        (
            incoming[index],
            events[index],
            "oam_work_order_event_replay_conflict",
            "正式工单同步事件重放证据冲突",
        )
        for index in event_replay_conflict_indexes
    ]
    conflicts: list[tuple[_IncomingWorkOrder, SyncInboxEvent, str, str]] = []
    plans: list[_ProjectionPlan] = []
    seen_external_ids: set[str] = set()
    seen_order_numbers: set[str] = set()
    for index, (item, event) in enumerate(zip(incoming, events, strict=True)):
        if index in event_replay_conflict_index_set:
            continue
        if item.external_id in seen_external_ids:
            conflicts.append((item, event, "duplicate_external_id", "工单来源ID重复"))
            continue
        if item.work_order_no in seen_order_numbers:
            conflicts.append((item, event, "duplicate_work_order_no", "工单编号重复"))
            continue
        seen_external_ids.add(item.external_id)
        seen_order_numbers.add(item.work_order_no)
        try:
            plans.append(
                _build_projection_plan(
                    db,
                    source=source,
                    snapshot=snapshot,
                    incoming=item,
                    event=event,
                    published_at=published_at,
                )
            )
        except OamWorkOrderProjectionError as exc:
            conflicts.append((item, event, exc.code, exc.message))

    if event_replay_conflicts or conflicts:
        _record_event_replay_conflicts(
            db,
            run=run,
            conflicts=event_replay_conflicts,
            now=published_at,
        )
        _record_conflicts(db, run=run, conflicts=conflicts, now=published_at)
        for index, event in enumerate(events):
            # A replay mismatch is evidence about an already-persisted event.
            # Never rewrite that prior event while recording the new conflict.
            if index in event_replay_conflict_index_set:
                continue
            event.status = "conflict"
            event.error_code = "oam_work_order_projection_conflict"
            event.error_detail = "工单投影存在未解决冲突"
            event.processed_at = published_at
        formal_batch.status = "validated"
        run.status = "conflict"
        run.completed_at = published_at
        run.failure_code = "oam_work_order_projection_conflict"
        run.failure_detail = (
            f"conflict_records={len(event_replay_conflicts) + len(conflicts)}"
        )
        return OamWorkOrderProjectionResult(
            sync_run_id=run.id,
            snapshot_id=snapshot.snapshot_id,
            status="conflict",
            projected_records=0,
            created_records=0,
            updated_records=0,
            unchanged_records=0,
            conflict_records=len(event_replay_conflicts) + len(conflicts),
        )

    run.status = "projecting"
    # The 0044 external-object/version RLS predicates prove every write
    # against a run that is already in ``projecting`` state.  ``SyncRun`` and
    # a newly inserted ``ExternalObject`` have no ORM dependency edge, so a
    # later broad flush may emit the object INSERT before the run UPDATE.
    # Persist this authorization state explicitly before entering the plan
    # loop; the enclosing transaction still keeps the publication atomic.
    db.flush()
    created = 0
    updated = 0
    unchanged = 0
    for plan in sorted(plans, key=lambda value: value.incoming.external_id):
        outcome = _apply_plan(db, plan=plan, published_at=published_at)
        if outcome == "created":
            created += 1
        elif outcome == "updated":
            updated += 1
        else:
            unchanged += 1
        plan.inbox_event.status = "applied"
        plan.inbox_event.error_code = None
        plan.inbox_event.error_detail = None
        plan.inbox_event.processed_at = published_at

    _resolve_run_conflicts(db, run=run, now=published_at)
    formal_batch.status = "applied"
    formal_batch.validated_at = published_at
    run.status = "completed"
    run.completed_at = published_at
    run.failure_code = None
    run.failure_detail = None
    return OamWorkOrderProjectionResult(
        sync_run_id=run.id,
        snapshot_id=snapshot.snapshot_id,
        status="completed",
        projected_records=len(plans),
        created_records=created,
        updated_records=updated,
        unchanged_records=unchanged,
        conflict_records=0,
    )


def next_unpublished_work_order_snapshot_id(db: Session) -> str | None:
    """Return the oldest completed work-order snapshot without any formal run."""

    statement = (
        select(ExternalSyncSnapshot)
        .where(
            ExternalSyncSnapshot.status == "complete",
            ExternalSyncSnapshot.source_system == SOURCE_SYSTEM_CODE,
            ExternalSyncSnapshot.scope_key.like("work-orders:recent-%"),
        )
        .order_by(
            ExternalSyncSnapshot.snapshot_at,
            ExternalSyncSnapshot.completed_at,
            ExternalSyncSnapshot.id,
        )
        .execution_options(yield_per=200)
    )
    for snapshot in db.scalars(statement):
        run = db.scalar(select(SyncRun).where(SyncRun.run_key == _run_key(snapshot)))
        # Conflict/failed runs require an operator to correct the mapping or
        # source condition and retry the exact snapshot explicitly.  The idle
        # poller must not turn one conflict into a hot retry loop.  Do not parse
        # the manifest here: every completed matching scope must reach the
        # publisher so invalid evidence is quarantined as a failed SyncRun
        # instead of silently disappearing from the queue.
        if run is None:
            return snapshot.id
    return None


def work_order_snapshot_lock_coordinates(
    db: Session,
    *,
    snapshot_id: str,
) -> tuple[str, str] | None:
    """Read the immutable edge scope needed before a worker starts RR."""

    checked_snapshot_id = _required_text(snapshot_id, "snapshot_id", 64)
    row = db.execute(
        select(
            ExternalSyncSnapshot.source_instance,
            ExternalSyncSnapshot.scope_key,
        ).where(ExternalSyncSnapshot.id == checked_snapshot_id)
    ).one_or_none()
    if row is None:
        return None
    return str(row.source_instance), str(row.scope_key)


def record_failed_work_order_snapshot(
    db: Session,
    *,
    snapshot_id: str,
    failure_code: str,
) -> uuid.UUID | None:
    """Persist payload-free quarantine evidence after a rolled-back validation.

    A deterministic poison snapshot must not be selected forever and starve
    newer healthy snapshots.  Existing completed evidence is never rewritten;
    an operator may explicitly retry a failed run after correcting its cause.
    """

    checked_snapshot_id = _required_text(snapshot_id, "snapshot_id", 64)
    checked_failure_code = _required_text(failure_code, "failure_code", 80)
    snapshot = db.scalar(
        select(ExternalSyncSnapshot).where(
            ExternalSyncSnapshot.id == checked_snapshot_id
        )
    )
    if (
        snapshot is None
        or snapshot.status != "complete"
        or snapshot.source_system != SOURCE_SYSTEM_CODE
        or not snapshot.scope_key.startswith("work-orders:recent-")
        or snapshot.sync_mode not in {"full", "incremental"}
    ):
        return None
    source = db.scalar(
        select(SourceSystem).where(SourceSystem.code == SOURCE_SYSTEM_CODE)
    )
    if source is None:
        return None
    _lock_projection_scope(db, snapshot)
    failed_at = _database_now(db)
    run_key = _run_key(snapshot)
    formal_scope_key = _formal_scope_key(snapshot)
    run = db.scalar(select(SyncRun).where(SyncRun.run_key == run_key))
    if run is not None:
        if run.status == "completed":
            return None
        if (
            run.source_system_id != source.id
            or run.scope_key != formal_scope_key
            or run.mode != snapshot.sync_mode
        ):
            return None
    else:
        run = SyncRun(
            source_system_id=source.id,
            run_key=run_key,
            scope_key=formal_scope_key,
            mode=snapshot.sync_mode,
            watermark_from=None,
            watermark_to=_iso(snapshot.snapshot_at),
            status="failed",
            manifest_sha256=(
                snapshot.manifest_sha256
                if SHA256_PATTERN.fullmatch(snapshot.manifest_sha256 or "")
                else None
            ),
            started_at=failed_at,
            completed_at=failed_at,
            failure_code=checked_failure_code,
            failure_detail="deterministic staging validation failed; retry is explicit",
        )
        db.add(run)
        db.flush()
        return run.id
    run.status = "failed"
    run.completed_at = failed_at
    run.failure_code = checked_failure_code
    run.failure_detail = "deterministic staging validation failed; retry is explicit"
    return run.id


def _validate_snapshot_header(
    snapshot: ExternalSyncSnapshot,
    published_at: datetime,
) -> None:
    if (
        snapshot.status != "complete"
        or snapshot.source_system != SOURCE_SYSTEM_CODE
        or not snapshot.source_instance
        or not snapshot.scope_key.startswith("work-orders:recent-")
        or snapshot.sync_mode not in {"full", "incremental"}
        or not snapshot.company_id
        or not snapshot.org_code
        or snapshot.completed_at is None
        or not snapshot.manifest_json
        or not SHA256_PATTERN.fullmatch(snapshot.manifest_sha256 or "")
    ):
        _fail("oam_work_order_snapshot_invalid", "OAM工单暂存快照头无效")
    snapshot_at = _aware(snapshot.snapshot_at)
    completed_at = _aware(snapshot.completed_at)
    if snapshot_at > completed_at or completed_at > published_at:
        _fail("oam_work_order_snapshot_time_invalid", "OAM工单暂存快照时间无效")


def _validate_snapshot_manifest(
    snapshot: ExternalSyncSnapshot,
) -> EdgeSyncSnapshotCompleteIn:
    try:
        manifest = EdgeSyncSnapshotCompleteIn.model_validate_json(
            snapshot.manifest_json
        )
    except Exception as exc:
        raise OamWorkOrderProjectionError(
            "oam_work_order_manifest_invalid",
            "OAM工单完成清单无效",
        ) from exc
    canonical_manifest = _canonical_json(manifest.model_dump(mode="json"))
    if (
        snapshot.manifest_json != canonical_manifest
        or hashlib.sha256(canonical_manifest.encode("utf-8")).hexdigest()
        != snapshot.manifest_sha256
    ):
        _fail("oam_work_order_manifest_hash_mismatch", "OAM工单完成清单哈希不一致")
    if (
        manifest.source_system != snapshot.source_system
        or manifest.snapshot_id != snapshot.snapshot_id
        or manifest.scope_key != snapshot.scope_key
        or manifest.sync_mode != snapshot.sync_mode
        or manifest.company_id != snapshot.company_id
        or manifest.org_code != snapshot.org_code
        or _aware(manifest.snapshot_at) != _aware(snapshot.snapshot_at)
        or {entity.entity_type for entity in manifest.entities}
        != set(EXPECTED_SNAPSHOT_ENTITIES)
    ):
        _fail("oam_work_order_manifest_mismatch", "OAM工单完成清单范围不一致")
    return manifest


def _validate_staged_snapshot(
    db: Session,
    snapshot: ExternalSyncSnapshot,
    manifest: EdgeSyncSnapshotCompleteIn,
) -> None:

    for entity in manifest.entities:
        batches = tuple(
            db.scalars(
                select(ExternalSyncSnapshotBatch)
                .where(
                    ExternalSyncSnapshotBatch.snapshot_ref_id == snapshot.id,
                    ExternalSyncSnapshotBatch.entity_type == entity.entity_type,
                )
                .order_by(ExternalSyncSnapshotBatch.sequence)
            ).all()
        )
        if (
            len(batches) != entity.batch_count
            or [batch.sequence for batch in batches]
            != list(range(1, entity.batch_count + 1))
            or any(batch.total_sequences != entity.batch_count for batch in batches)
            or sum(batch.record_count for batch in batches)
            != entity.delta_record_count
        ):
            _fail("oam_work_order_batch_incomplete", "OAM工单暂存批次不完整")
        delta_rows = tuple(
            db.scalars(
                select(ExternalSyncSnapshotRecord)
                .where(
                    ExternalSyncSnapshotRecord.snapshot_ref_id == snapshot.id,
                    ExternalSyncSnapshotRecord.entity_type == entity.entity_type,
                )
                .order_by(ExternalSyncSnapshotRecord.business_key)
            ).all()
        )
        delta_wire = [_wire_delta(row) for row in delta_rows]
        if (
            len(delta_wire) != entity.delta_record_count
            or _records_sha256(delta_wire) != entity.delta_sha256
        ):
            _fail("oam_work_order_delta_hash_mismatch", "OAM工单暂存增量哈希不一致")
        current_rows = tuple(
            db.scalars(
                select(ExternalSyncCurrentRecord)
                .where(
                    ExternalSyncCurrentRecord.source_instance
                    == snapshot.source_instance,
                    ExternalSyncCurrentRecord.scope_key == snapshot.scope_key,
                    ExternalSyncCurrentRecord.entity_type == entity.entity_type,
                )
                .order_by(ExternalSyncCurrentRecord.business_key)
            ).all()
        )
        if any(row.last_snapshot_id != snapshot.id for row in current_rows):
            _fail("oam_work_order_current_snapshot_mismatch", "OAM工单当前镜像未由该快照完整确认")
        final_wire = [_wire_current(row) for row in current_rows]
        if (
            len(final_wire) != entity.final_record_count
            or _records_sha256(final_wire) != entity.final_sha256
        ):
            _fail("oam_work_order_final_hash_mismatch", "OAM工单最终镜像哈希不一致")


def _require_source_system(
    db: Session,
    snapshot: ExternalSyncSnapshot,
) -> SourceSystem:
    source = db.scalar(
        select(SourceSystem).where(SourceSystem.code == SOURCE_SYSTEM_CODE)
    )
    if source is None:
        _fail("oam_work_order_source_not_provisioned", "正式OAM来源系统尚未配置")
    assert source is not None
    expected = {
        "projection_schema": PROJECTION_SCHEMA,
        "edge_source_instance": snapshot.source_instance,
        "work_order_company_id": snapshot.company_id,
        "work_order_org_code": snapshot.org_code,
        "work_order_scope_key": snapshot.scope_key,
    }
    if (
        source.mode != SOURCE_SYSTEM_MODE
        or source.enabled is not True
        or source.configuration_jsonb != expected
    ):
        _fail("oam_work_order_source_scope_mismatch", "正式OAM来源配置与快照范围不一致")
    return source


def _incoming_work_order(
    row: ExternalSyncCurrentRecord,
    *,
    snapshot: ExternalSyncSnapshot,
) -> _IncomingWorkOrder:
    try:
        payload = json.loads(row.payload_json)
    except json.JSONDecodeError as exc:
        raise OamWorkOrderProjectionError(
            "oam_work_order_payload_invalid",
            "OAM工单暂存载荷不是有效JSON",
        ) from exc
    if not isinstance(payload, dict):
        _fail("oam_work_order_payload_not_minimal", "OAM工单暂存载荷不符合最小白名单")
    return _incoming_payload(
        payload,
        business_key=row.business_key,
        source_updated_at=row.source_updated_at,
        supplied_payload_sha256=row.payload_sha256,
        snapshot=snapshot,
    )


def _incoming_event(
    event: SyncInboxEvent,
    *,
    snapshot: ExternalSyncSnapshot,
) -> _IncomingWorkOrder:
    payload = event.payload_jsonb
    if not isinstance(payload, dict):
        _fail("oam_work_order_payload_not_minimal", "OAM工单同步事件载荷无效")
    code = _required_text(payload.get("code"), "code", 100)
    return _incoming_payload(
        payload,
        business_key=f"work-order:{code}",
        source_updated_at=event.source_updated_at,
        supplied_payload_sha256=event.payload_sha256,
        snapshot=snapshot,
    )


def _incoming_payload(
    payload: dict[str, Any],
    *,
    business_key: str,
    source_updated_at: datetime | None,
    supplied_payload_sha256: str,
    snapshot: ExternalSyncSnapshot,
) -> _IncomingWorkOrder:
    if set(payload) != set(WORK_ORDER_PAYLOAD_FIELDS):
        _fail("oam_work_order_payload_not_minimal", "OAM工单暂存载荷不符合最小白名单")
    canonical_raw = _canonical_json(payload)
    raw_hash = hashlib.sha256(canonical_raw.encode("utf-8")).hexdigest()
    if raw_hash != supplied_payload_sha256:
        _fail("oam_work_order_payload_hash_mismatch", "OAM工单暂存载荷哈希不一致")
    external_id = _required_text(payload.get("id"), "id", 250)
    work_order_no = _required_text(payload.get("code"), "code", 100)
    status_code = _required_text(payload.get("statusCode"), "statusCode", 64)
    projected_status = STATUS_BY_SOURCE.get(status_code)
    if projected_status is None:
        _fail("oam_work_order_status_unknown", "OAM工单状态未纳入正式映射")
    executor_id = _required_text(payload.get("executorId"), "executorId", 250)
    if payload.get("authCompanyId") != snapshot.company_id:
        _fail("oam_work_order_company_mismatch", "OAM工单企业范围与快照不一致")
    if business_key != f"work-order:{work_order_no}":
        _fail("oam_work_order_business_key_mismatch", "OAM工单业务键与编号不一致")
    if source_updated_at is None:
        _fail("oam_work_order_source_time_missing", "OAM工单缺少来源更新时间")
    checked_source_updated_at = _aware(source_updated_at)
    payload_source_time = _parse_oam_source_time(payload.get("updateTime"))
    if (
        payload_source_time != checked_source_updated_at
        or checked_source_updated_at > _aware(snapshot.snapshot_at)
    ):
        _fail("oam_work_order_source_time_mismatch", "OAM工单来源时间不一致")
    return _IncomingWorkOrder(
        business_key=business_key,
        external_id=external_id,
        work_order_no=work_order_no,
        source_status=status_code,
        projected_status=projected_status,
        executor_external_id=executor_id,
        source_updated_at=checked_source_updated_at,
        source_version=(
            f"{SOURCE_VERSION_PREFIX}{_iso(checked_source_updated_at)}:{raw_hash}"
        ),
        raw_payload=payload,
        raw_payload_sha256=raw_hash,
    )


def _build_projection_plan(
    db: Session,
    *,
    source: SourceSystem,
    snapshot: ExternalSyncSnapshot,
    incoming: _IncomingWorkOrder,
    event: SyncInboxEvent,
    published_at: datetime,
) -> _ProjectionPlan:
    person, organization, mapping_sha256 = _resolve_person_and_organization(
        db,
        source=source,
        snapshot=snapshot,
        executor_external_id=incoming.executor_external_id,
        published_at=published_at,
    )
    external = db.scalar(
        select(ExternalObject).where(
            ExternalObject.source_system_id == source.id,
            ExternalObject.entity_type == WORK_ORDER_ENTITY,
            ExternalObject.external_id == incoming.external_id,
        )
    )
    current_version: ExternalObjectVersion | None = None
    work_order: OamWorkOrder | None = None
    if external is not None:
        if external.deleted_at is not None:
            _fail("oam_work_order_external_deleted", "OAM工单来源对象已删除")
        versions = tuple(
            db.scalars(
                select(ExternalObjectVersion).where(
                    ExternalObjectVersion.external_object_id == external.id,
                    ExternalObjectVersion.is_current.is_(True),
                )
            ).all()
        )
        if len(versions) != 1 or external.current_version_id != versions[0].id:
            _fail("oam_work_order_current_version_invalid", "OAM工单当前版本链无效")
        current_version = versions[0]
        work_order = db.scalar(
            select(OamWorkOrder).where(OamWorkOrder.external_object_id == external.id)
        )
        if work_order is None:
            _fail("oam_work_order_projection_missing", "OAM工单正式投影缺失")
        _validate_current_projection_evidence(
            external=external,
            current_version=current_version,
            work_order=work_order,
        )
        if _aware(current_version.source_updated_at) > incoming.source_updated_at:
            _fail("oam_work_order_source_time_regressed", "OAM工单来源版本发生时间回退")
    conflicting_number = db.scalar(
        select(OamWorkOrder).where(OamWorkOrder.work_order_no == incoming.work_order_no)
    )
    if conflicting_number is not None and (
        work_order is None or conflicting_number.id != work_order.id
    ):
        _fail("oam_work_order_number_conflict", "OAM工单编号已绑定其他来源对象")
    projection_source_version = _projection_source_version(
        incoming.raw_payload_sha256,
        mapping_sha256,
    )
    payload = {
        "work_order_no": incoming.work_order_no,
        "organization_id": str(organization.id),
        "engineer_person_id": str(person.id),
        "status": incoming.projected_status,
    }
    payload_hash = _sha256(payload)
    if current_version is not None:
        current_source_coordinates = _projection_source_coordinates(
            current_version.source_version,
            source_updated_at=_aware(current_version.source_updated_at),
        )
        if current_source_coordinates is None:
            # The current evidence was validated immediately above, so this is
            # an internal invariant rather than recoverable source input.
            raise AssertionError("validated projection source version is unreadable")
        current_raw_sha256, current_mapping_sha256 = current_source_coordinates
        same_raw_source = current_raw_sha256 == incoming.raw_payload_sha256
        same_mapping = current_mapping_sha256 == mapping_sha256
        if (
            same_raw_source
            and same_mapping
            and current_version.payload_sha256 != payload_hash
        ):
            _fail("oam_work_order_same_version_hash_conflict", "OAM工单同一来源版本载荷冲突")
        if not same_raw_source and _aware(
            current_version.source_updated_at
        ) == incoming.source_updated_at:
            _fail("oam_work_order_same_time_version_conflict", "OAM工单同一来源时间版本冲突")
    changed = (
        current_version is None
        or current_version.source_version != projection_source_version
        or current_version.payload_sha256 != payload_hash
    )
    return _ProjectionPlan(
        incoming=incoming,
        inbox_event=event,
        person=person,
        organization=organization,
        external_object=external,
        current_version=current_version,
        work_order=work_order,
        projection_source_version=projection_source_version,
        projection_payload=payload,
        projection_sha256=payload_hash,
        changed=changed,
    )


def _validate_current_projection_evidence(
    *,
    external: ExternalObject,
    current_version: ExternalObjectVersion,
    work_order: OamWorkOrder,
) -> None:
    source_updated_at = _aware(work_order.source_updated_at)
    synced_at = _aware(work_order.updated_at)
    payload = {
        "work_order_no": work_order.work_order_no,
        "organization_id": str(work_order.organization_id),
        "engineer_person_id": (
            str(work_order.engineer_person_id)
            if work_order.engineer_person_id is not None
            else None
        ),
        "status": work_order.status,
    }
    source_coordinates = _projection_source_coordinates(
        current_version.source_version,
        source_updated_at=source_updated_at,
    )
    if (
        external.current_version_id != current_version.id
        or current_version.is_current is not True
        or current_version.valid_to is not None
        or _aware(current_version.source_updated_at) != source_updated_at
        or source_coordinates is None
        or not isinstance(current_version.payload_jsonb, dict)
        or current_version.payload_jsonb != payload
        or current_version.payload_sha256 != _sha256(payload)
        or source_updated_at > synced_at
        or _aware(current_version.valid_from) > synced_at
        or _aware(current_version.created_at) > synced_at
    ):
        _fail(
            "oam_work_order_existing_projection_invalid",
            "既有OAM工单正式投影证据无效",
        )


def _validate_completed_duplicate(
    db: Session,
    *,
    source: SourceSystem,
    snapshot: ExternalSyncSnapshot,
    run: SyncRun,
    record_count: int,
    body_sha256: str,
) -> None:
    batches = tuple(
        db.scalars(
            select(SyncBatch).where(
                SyncBatch.run_id == run.id,
                SyncBatch.entity_type == WORK_ORDER_ENTITY,
            )
        ).all()
    )
    if (
        len(batches) != 1
        or batches[0].sequence != 1
        or batches[0].record_count != record_count
        or batches[0].body_sha256 != body_sha256
        or batches[0].status != "applied"
    ):
        _fail(
            "oam_work_order_duplicate_batch_invalid",
            "既有OAM工单同步批次证据无效",
        )
    batch = batches[0]
    events = tuple(
        db.scalars(
            select(SyncInboxEvent)
            .where(SyncInboxEvent.batch_id == batch.id)
            .order_by(SyncInboxEvent.external_event_id)
        ).all()
    )
    if len({event.external_event_id for event in events}) != len(events):
        _fail(
            "oam_work_order_duplicate_events_invalid",
            "既有OAM工单同步事件证据无效",
        )
    incoming_events = tuple(
        sorted(
            (
                (_incoming_event(event, snapshot=snapshot), event)
                for event in events
            ),
            key=lambda value: value[0].business_key,
        )
    )
    final_wire = [
        {
            "business_key": item.business_key,
            "source_updated_at": _iso(item.source_updated_at),
            "data": item.raw_payload,
        }
        for item, _event in incoming_events
    ]
    if (
        len(incoming_events) != record_count
        or _records_sha256(final_wire) != body_sha256
    ):
        _fail(
            "oam_work_order_duplicate_events_invalid",
            "既有OAM工单同步事件数量或哈希无效",
        )
    seen_external_ids: set[str] = set()
    seen_order_numbers: set[str] = set()
    has_newer_run = _has_newer_completed_run(db, run=run)
    for item, event in incoming_events:
        if (
            item.external_id in seen_external_ids
            or item.work_order_no in seen_order_numbers
        ):
            _fail(
                "oam_work_order_duplicate_identity_invalid",
                "既有OAM工单同步身份重复",
            )
        seen_external_ids.add(item.external_id)
        seen_order_numbers.add(item.work_order_no)
        if any(
            (
                event.batch_id != batch.id,
                event.source_system_id != source.id,
                event.external_event_id != _event_id(snapshot, item.business_key),
                event.entity_type != WORK_ORDER_ENTITY,
                event.external_id != item.external_id,
                event.source_version != item.source_version,
                _aware(event.source_updated_at) != item.source_updated_at,
                event.payload_jsonb != item.raw_payload,
                event.payload_sha256 != item.raw_payload_sha256,
                event.status != "applied",
                event.error_code is not None,
                event.error_detail is not None,
                event.processed_at is None,
            )
        ):
            _fail(
                "oam_work_order_duplicate_event_invalid",
                "既有OAM工单同步事件证据无效",
            )
        if not has_newer_run:
            _validate_duplicate_projection_evidence(
                db,
                source=source,
                incoming=item,
            )


def _validate_duplicate_projection_evidence(
    db: Session,
    *,
    source: SourceSystem,
    incoming: _IncomingWorkOrder,
) -> None:
    """Validate the immutable result of a completed run without replaying policy.

    A completed delivery is idempotent evidence.  Re-evaluating it against the
    *current* person mapping would make a harmless later approval edit change
    the meaning of that historical delivery.  The current projection must
    instead prove that it was derived from the exact raw OAM version recorded
    by the completed inbox event.  Both the legacy v1 coordinate and the v2
    mapping-aware coordinate remain readable here; a later, distinct snapshot
    performs any required v1-to-v2 sealing.
    """

    external = db.scalar(
        select(ExternalObject).where(
            ExternalObject.source_system_id == source.id,
            ExternalObject.entity_type == WORK_ORDER_ENTITY,
            ExternalObject.external_id == incoming.external_id,
        )
    )
    if external is None or external.deleted_at is not None:
        _fail(
            "oam_work_order_duplicate_projection_drift",
            "既有OAM工单正式投影已漂移",
        )
    assert external is not None
    versions = tuple(
        db.scalars(
            select(ExternalObjectVersion).where(
                ExternalObjectVersion.external_object_id == external.id,
                ExternalObjectVersion.is_current.is_(True),
            )
        ).all()
    )
    work_order = db.scalar(
        select(OamWorkOrder).where(OamWorkOrder.external_object_id == external.id)
    )
    if (
        len(versions) != 1
        or external.current_version_id != versions[0].id
        or work_order is None
    ):
        _fail(
            "oam_work_order_duplicate_projection_drift",
            "既有OAM工单正式投影已漂移",
        )
    current_version = versions[0]
    assert work_order is not None
    _validate_current_projection_evidence(
        external=external,
        current_version=current_version,
        work_order=work_order,
    )
    coordinates = _projection_source_coordinates(
        current_version.source_version,
        source_updated_at=_aware(current_version.source_updated_at),
    )
    if (
        coordinates is None
        or coordinates[0] != incoming.raw_payload_sha256
        or _aware(current_version.source_updated_at) != incoming.source_updated_at
        or _aware(work_order.source_updated_at) != incoming.source_updated_at
        or work_order.work_order_no != incoming.work_order_no
        or work_order.status != incoming.projected_status
    ):
        _fail(
            "oam_work_order_duplicate_projection_drift",
            "既有OAM工单正式投影已漂移",
        )


def _has_newer_completed_run(db: Session, *, run: SyncRun) -> bool:
    if run.watermark_to is None:
        _fail("oam_work_order_previous_watermark_invalid", "既有正式工单同步水位无效")
    try:
        current_time = _aware(datetime.fromisoformat(run.watermark_to))
    except ValueError:
        _fail("oam_work_order_previous_watermark_invalid", "既有正式工单同步水位无效")
    rows = tuple(
        db.scalars(
            select(SyncRun).where(
                SyncRun.source_system_id == run.source_system_id,
                SyncRun.scope_key == run.scope_key,
                SyncRun.status == "completed",
                SyncRun.id != run.id,
            )
        ).all()
    )
    for other in rows:
        if other.watermark_to is None:
            _fail("oam_work_order_previous_watermark_invalid", "既有正式工单同步水位无效")
        try:
            other_time = _aware(datetime.fromisoformat(other.watermark_to))
        except ValueError:
            _fail("oam_work_order_previous_watermark_invalid", "既有正式工单同步水位无效")
        if other_time > current_time:
            return True
    return False


def _resolve_person_and_organization(
    db: Session,
    *,
    source: SourceSystem,
    snapshot: ExternalSyncSnapshot,
    executor_external_id: str,
    published_at: datetime,
) -> tuple[Person, Organization, str]:
    employee = db.scalar(
        select(ExternalObject).where(
            ExternalObject.source_system_id == source.id,
            ExternalObject.entity_type == EMPLOYEE_ENTITY,
            ExternalObject.external_id == executor_external_id,
        )
    )
    if employee is None or employee.deleted_at is not None:
        _fail("oam_work_order_person_mapping_missing", "OAM执行人尚未建立正式人员映射")
    assert employee is not None
    employee_versions = tuple(
        db.scalars(
            select(ExternalObjectVersion).where(
                ExternalObjectVersion.external_object_id == employee.id,
                ExternalObjectVersion.is_current.is_(True),
            )
        ).all()
    )
    if (
        len(employee_versions) != 1
        or employee.current_version_id != employee_versions[0].id
        or employee_versions[0].valid_to is not None
        or employee_versions[0].source_updated_at is None
        or _aware(employee_versions[0].source_updated_at) > _aware(snapshot.snapshot_at)
        or not isinstance(employee_versions[0].payload_jsonb, dict)
        or employee_versions[0].payload_jsonb.get("accountId")
        != executor_external_id
        or employee_versions[0].payload_jsonb.get("companyId")
        != snapshot.company_id
        or employee_versions[0].payload_jsonb.get("orgCode")
        != snapshot.org_code
        or employee_versions[0].payload_sha256
        != _sha256(employee_versions[0].payload_jsonb)
    ):
        _fail("oam_work_order_person_source_invalid", "OAM执行人正式来源版本无效")
    direct = db.scalar(select(Person).where(Person.external_object_id == employee.id))
    mappings = tuple(
        db.scalars(
            select(ExternalObjectMapping).where(
                ExternalObjectMapping.external_object_id == employee.id,
                ExternalObjectMapping.local_object_type == "person",
                ExternalObjectMapping.status == "approved",
            )
        ).all()
    )
    if not mappings:
        _fail("oam_work_order_person_mapping_missing", "OAM执行人尚未建立正式人员映射")
    if len(mappings) != 1:
        _fail("oam_work_order_person_mapping_ambiguous", "OAM执行人人员映射缺失或不唯一")
    mapping = mappings[0]
    approved_by = mapping.approved_by
    if (
        not isinstance(approved_by, str)
        or approved_by.strip() != approved_by
        or not 1 <= len(approved_by) <= 36
        or any(ord(character) < 32 or ord(character) == 127 for character in approved_by)
        or mapping.approved_at is None
        or mapping.updated_at is None
    ):
        _fail(
            "oam_work_order_person_mapping_approval_invalid",
            "OAM执行人人员映射缺少有效审批人或审批时间",
        )
    approved_at = _aware(mapping.approved_at)
    mapping_updated_at = _aware(mapping.updated_at)
    if (
        mapping_updated_at < approved_at
        or approved_at > published_at
        or mapping_updated_at > published_at
    ):
        _fail(
            "oam_work_order_person_mapping_approval_invalid",
            "OAM执行人人员映射审批时间证据无效",
        )
    try:
        person_id = uuid.UUID(mapping.local_object_id)
    except (TypeError, ValueError):
        _fail("oam_work_order_person_mapping_invalid", "OAM执行人人员映射坐标无效")
    if str(person_id) != mapping.local_object_id:
        _fail("oam_work_order_person_mapping_invalid", "OAM执行人人员映射坐标无效")
    if direct is not None and direct.id != person_id:
        _fail("oam_work_order_person_mapping_ambiguous", "OAM执行人人员映射缺失或不唯一")
    person = db.get(Person, person_id)
    if person is None or person.employment_status != "active":
        _fail("oam_work_order_person_inactive", "OAM执行人对应人员当前不可用")
    organization = db.get(Organization, person.organization_id)
    if organization is None or organization.status != "active":
        _fail("oam_work_order_organization_inactive", "OAM工单人员所属组织当前不可用")
    _require_organization_scope(
        db,
        organization=organization,
        required_org_code=snapshot.org_code,
    )
    mapping_sha256 = _sha256(
        {
            "mapping_id": str(mapping.id),
            "external_object_id": str(employee.id),
            "local_object_type": "person",
            "local_object_id": str(person.id),
            "approved_by_id": approved_by,
            "approved_at": _iso(approved_at),
            "mapping_updated_at": _iso(mapping_updated_at),
            "person_id": str(person.id),
            "organization_id": str(organization.id),
        }
    )
    return person, organization, mapping_sha256


def _require_organization_scope(
    db: Session,
    *,
    organization: Organization,
    required_org_code: str,
) -> None:
    """Require the mapped person to remain under the configured OAM org root."""

    current: Organization | None = organization
    visited: set[uuid.UUID] = set()
    for _depth in range(64):
        if current is None or current.id in visited or current.status != "active":
            break
        visited.add(current.id)
        if current.code == required_org_code:
            return
        if current.parent_id is None:
            break
        current = db.get(Organization, current.parent_id)
    _fail(
        "oam_work_order_person_scope_mismatch",
        "OAM执行人人员映射不属于配置的组织范围",
    )


def _apply_plan(
    db: Session,
    *,
    plan: _ProjectionPlan,
    published_at: datetime,
) -> str:
    incoming = plan.incoming
    external = plan.external_object
    work_order = plan.work_order
    current_version = plan.current_version
    created = external is None
    if external is None:
        source_system_id = plan.inbox_event.source_system_id
        external = ExternalObject(
            source_system_id=source_system_id,
            entity_type=WORK_ORDER_ENTITY,
            external_id=incoming.external_id,
            current_version_id=None,
            deleted_at=None,
            created_at=published_at,
            updated_at=published_at,
        )
        db.add(external)
        db.flush()

    if plan.changed:
        if current_version is not None:
            current_version.is_current = False
            current_version.valid_to = published_at
        version = ExternalObjectVersion(
            external_object_id=external.id,
            source_version=plan.projection_source_version,
            source_updated_at=incoming.source_updated_at,
            valid_from=published_at,
            valid_to=None,
            payload_jsonb=plan.projection_payload,
            payload_sha256=plan.projection_sha256,
            is_current=True,
            created_at=published_at,
        )
        db.add(version)
        db.flush()
        external.current_version_id = version.id
        external.updated_at = published_at
        # The work-order RLS predicate proves the formal row against the
        # external object's current-version pointer.  Persist that pointer
        # before the dependent INSERT/UPDATE instead of relying on implicit
        # unit-of-work ordering at commit time.
        db.flush()
    elif current_version is None:
        raise AssertionError("unchanged work-order plan requires a current version")

    if work_order is None:
        work_order = OamWorkOrder(
            external_object_id=external.id,
            work_order_no=incoming.work_order_no,
            organization_id=plan.organization.id,
            engineer_person_id=plan.person.id,
            status=incoming.projected_status,
            source_updated_at=incoming.source_updated_at,
            created_at=published_at,
            updated_at=published_at,
        )
        db.add(work_order)
        return "created"

    if plan.changed:
        work_order.work_order_no = incoming.work_order_no
        work_order.organization_id = plan.organization.id
        work_order.engineer_person_id = plan.person.id
        work_order.status = incoming.projected_status
        work_order.source_updated_at = incoming.source_updated_at
    # A complete successful observation refreshes selection freshness even if
    # OAM content did not change.  Window absence never reaches this branch.
    work_order.updated_at = published_at
    return "updated" if plan.changed else "unchanged"


def _upsert_formal_batch(
    db: Session,
    *,
    run: SyncRun,
    record_count: int,
    body_sha256: str,
    received_at: datetime | None,
    validated_at: datetime,
) -> SyncBatch:
    batch = db.scalar(
        select(SyncBatch).where(
            SyncBatch.run_id == run.id,
            SyncBatch.entity_type == WORK_ORDER_ENTITY,
            SyncBatch.sequence == 1,
        )
    )
    if batch is None:
        batch = SyncBatch(
            run_id=run.id,
            entity_type=WORK_ORDER_ENTITY,
            sequence=1,
            record_count=record_count,
            body_sha256=body_sha256,
            status="validated",
            received_at=received_at,
            validated_at=validated_at,
        )
        db.add(batch)
        db.flush()
        return batch
    if batch.record_count != record_count or batch.body_sha256 != body_sha256:
        _fail("oam_work_order_formal_batch_conflict", "正式工单同步批次证据冲突")
    batch.status = "validated"
    batch.validated_at = validated_at
    return batch


def _upsert_inbox_events(
    db: Session,
    *,
    source: SourceSystem,
    run: SyncRun,
    batch: SyncBatch,
    snapshot: ExternalSyncSnapshot,
    incoming: tuple[_IncomingWorkOrder, ...],
) -> tuple[tuple[SyncInboxEvent, ...], tuple[int, ...]]:
    events: list[SyncInboxEvent] = []
    replay_conflict_indexes: list[int] = []
    for index, item in enumerate(incoming):
        external_event_id = _event_id(snapshot, item.business_key)
        event = db.scalar(
            select(SyncInboxEvent).where(
                SyncInboxEvent.source_system_id == source.id,
                SyncInboxEvent.external_event_id == external_event_id,
            )
        )
        if event is None:
            event = SyncInboxEvent(
                batch_id=batch.id,
                source_system_id=source.id,
                external_event_id=external_event_id,
                entity_type=WORK_ORDER_ENTITY,
                external_id=item.external_id,
                source_version=item.source_version,
                source_updated_at=item.source_updated_at,
                payload_jsonb=item.raw_payload,
                payload_sha256=item.raw_payload_sha256,
                status="validated",
                error_code=None,
                error_detail=None,
                processed_at=None,
            )
            db.add(event)
            db.flush()
        elif any(
            (
                event.batch_id != batch.id,
                event.entity_type != WORK_ORDER_ENTITY,
                event.external_id != item.external_id,
                event.source_version != item.source_version,
                _aware(event.source_updated_at) != item.source_updated_at,
                event.payload_jsonb != item.raw_payload,
                event.payload_sha256 != item.raw_payload_sha256,
            )
        ):
            # Preserve the first immutable inbox evidence.  The caller records
            # a formal SyncConflict for the mismatching replay in the same
            # transaction instead of rolling everything back into a
            # payload-free failed run.
            replay_conflict_indexes.append(index)
        else:
            event.status = "validated"
            event.error_code = None
            event.error_detail = None
            event.processed_at = None
        events.append(event)
    return tuple(events), tuple(replay_conflict_indexes)


def _record_event_replay_conflicts(
    db: Session,
    *,
    run: SyncRun,
    conflicts: list[tuple[_IncomingWorkOrder, SyncInboxEvent, str, str]],
    now: datetime,
) -> None:
    for incoming, event, code, _message in conflicts:
        digest = hashlib.sha256(
            f"{run.id}\0{incoming.business_key}\0{code}".encode("utf-8")
        ).hexdigest()
        dedup_key = f"oam-wo:{digest}"
        existing = db.scalar(
            select(SyncConflict).where(SyncConflict.dedup_key == dedup_key)
        )
        external_value = {
            "business_key": incoming.business_key,
            "external_id": incoming.external_id,
            "work_order_no": incoming.work_order_no,
            "source_version": incoming.source_version,
            "payload_sha256": incoming.raw_payload_sha256,
        }
        local_value = {
            "inbox_event_id": str(event.id),
            "batch_id": str(event.batch_id),
            "source_version": event.source_version,
            "payload_sha256": event.payload_sha256,
        }
        if existing is None:
            db.add(
                SyncConflict(
                    run_id=run.id,
                    inbox_event_id=event.id,
                    external_object_id=None,
                    dedup_key=dedup_key,
                    conflict_type=code,
                    external_value_jsonb=external_value,
                    local_value_jsonb=local_value,
                    status="open",
                    resolution_jsonb=None,
                    resolved_by=None,
                    resolved_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            continue
        existing.external_value_jsonb = external_value
        existing.local_value_jsonb = local_value
        if existing.status != "open":
            existing.status = "open"
            existing.resolution_jsonb = None
            existing.resolved_by = None
            existing.resolved_at = None
        existing.updated_at = now


def _record_conflicts(
    db: Session,
    *,
    run: SyncRun,
    conflicts: list[tuple[_IncomingWorkOrder, SyncInboxEvent, str, str]],
    now: datetime,
) -> None:
    for incoming, event, code, message in conflicts:
        digest = hashlib.sha256(
            f"{run.id}\0{incoming.business_key}\0{code}".encode("utf-8")
        ).hexdigest()
        dedup_key = f"oam-wo:{digest}"
        existing = db.scalar(
            select(SyncConflict).where(SyncConflict.dedup_key == dedup_key)
        )
        if existing is None:
            db.add(
                SyncConflict(
                    run_id=run.id,
                    inbox_event_id=event.id,
                    external_object_id=None,
                    dedup_key=dedup_key,
                    conflict_type=code,
                    external_value_jsonb={
                        "business_key": incoming.business_key,
                        "external_id": incoming.external_id,
                        "work_order_no": incoming.work_order_no,
                        "source_version": incoming.source_version,
                    },
                    local_value_jsonb={},
                    status="open",
                    resolution_jsonb=None,
                    resolved_by=None,
                    resolved_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            existing.external_value_jsonb = {
                "business_key": incoming.business_key,
                "external_id": incoming.external_id,
                "work_order_no": incoming.work_order_no,
                "source_version": incoming.source_version,
            }
            existing.local_value_jsonb = {}
            if existing.status != "open":
                existing.status = "open"
                existing.resolution_jsonb = None
                existing.resolved_by = None
                existing.resolved_at = None
            existing.updated_at = now
        event.error_code = code
        event.error_detail = message


def _resolve_run_conflicts(db: Session, *, run: SyncRun, now: datetime) -> None:
    rows = tuple(
        db.scalars(
            select(SyncConflict).where(
                SyncConflict.run_id == run.id,
                SyncConflict.status == "open",
            )
        ).all()
    )
    for row in rows:
        row.status = "resolved"
        row.resolution_jsonb = {
            "resolution": "projection_revalidated",
            "resolved_at": _iso(now),
        }
        row.resolved_at = now
        row.updated_at = now


def _validate_existing_run(
    run: SyncRun,
    source: SourceSystem,
    snapshot: ExternalSyncSnapshot,
    formal_scope_key: str,
) -> None:
    if any(
        (
            run.source_system_id != source.id,
            run.scope_key != formal_scope_key,
            run.mode != snapshot.sync_mode,
            run.watermark_to != _iso(snapshot.snapshot_at),
            run.manifest_sha256 != snapshot.manifest_sha256,
        )
    ):
        _fail("oam_work_order_run_replay_conflict", "正式工单同步运行坐标冲突")


def _reject_older_completed_run(
    db: Session,
    source: SourceSystem,
    snapshot: ExternalSyncSnapshot,
    formal_scope_key: str,
) -> None:
    runs = tuple(
        db.scalars(
            select(SyncRun).where(
                SyncRun.source_system_id == source.id,
                SyncRun.scope_key == formal_scope_key,
                SyncRun.status == "completed",
            )
        ).all()
    )
    incoming_time = _aware(snapshot.snapshot_at)
    for run in runs:
        if run.watermark_to is None:
            _fail("oam_work_order_previous_watermark_invalid", "既有正式工单同步水位无效")
        try:
            previous_time = _aware(datetime.fromisoformat(run.watermark_to))
        except ValueError:
            _fail("oam_work_order_previous_watermark_invalid", "既有正式工单同步水位无效")
        if previous_time >= incoming_time:
            _fail("oam_work_order_snapshot_stale", "旧OAM工单快照不得覆盖正式投影")


def _lock_projection_scope(db: Session, snapshot: ExternalSyncSnapshot) -> None:
    lock_external_sync_scope(
        db,
        source_instance=snapshot.source_instance,
        scope_key=snapshot.scope_key,
    )


def _wire_delta(row: ExternalSyncSnapshotRecord) -> dict[str, Any]:
    return {
        **_wire_record(
            business_key=row.business_key,
            source_updated_at=row.source_updated_at,
            payload_json=row.payload_json,
        ),
        "operation": row.operation,
    }


def _wire_current(row: ExternalSyncCurrentRecord) -> dict[str, Any]:
    return _wire_record(
        business_key=row.business_key,
        source_updated_at=row.source_updated_at,
        payload_json=row.payload_json,
    )


def _wire_record(
    *,
    business_key: str,
    source_updated_at: datetime | None,
    payload_json: str,
) -> dict[str, Any]:
    try:
        data = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OamWorkOrderProjectionError(
            "oam_work_order_staging_json_invalid",
            "OAM工单暂存记录不是有效JSON",
        ) from exc
    return {
        "business_key": business_key,
        "source_updated_at": _iso(source_updated_at) if source_updated_at else None,
        "data": data,
    }


def _records_sha256(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _projection_source_version(
    raw_payload_sha256: str,
    mapping_sha256: str,
) -> str:
    if (
        SHA256_PATTERN.fullmatch(raw_payload_sha256) is None
        or SHA256_PATTERN.fullmatch(mapping_sha256) is None
    ):
        raise AssertionError("projection source coordinates must be sha256 values")
    return (
        f"{PROJECTION_SOURCE_VERSION_PREFIX}"
        f"{raw_payload_sha256}:{mapping_sha256}"
    )


def _projection_source_coordinates(
    source_version: str,
    *,
    source_updated_at: datetime,
) -> tuple[str, str | None] | None:
    match = PROJECTION_SOURCE_VERSION_PATTERN.fullmatch(source_version)
    if match is not None:
        return match.group(1), match.group(2)
    # Read the unreleased v1 evidence format so the first post-upgrade
    # observation can seal it into a mapping-aware v2 version.
    legacy_prefix = f"{SOURCE_VERSION_PREFIX}{_iso(source_updated_at)}:"
    if source_version.startswith(legacy_prefix):
        raw_payload_sha256 = source_version.removeprefix(legacy_prefix)
        if SHA256_PATTERN.fullmatch(raw_payload_sha256) is not None:
            return raw_payload_sha256, None
    return None


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(text("SELECT clock_timestamp()"))
        if not isinstance(value, datetime):
            _fail("oam_work_order_database_time_invalid", "数据库时间不可用")
        return _aware(value)
    return datetime.now(timezone.utc)


def _parse_oam_source_time(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        _fail("oam_work_order_source_time_missing", "OAM工单缺少来源更新时间")
    if OAM_SOURCE_DATETIME_PATTERN.fullmatch(raw) is None:
        _fail("oam_work_order_source_time_invalid", "OAM工单来源更新时间格式无效")
    normalized = f"{raw[:-1]}+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OamWorkOrderProjectionError(
            "oam_work_order_source_time_invalid",
            "OAM工单来源更新时间格式无效",
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=OAM_SOURCE_TIMEZONE)
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime | None) -> datetime:
    if value is None:
        _fail("oam_work_order_time_missing", "工单投影时间坐标缺失")
    assert value is not None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _aware(value).isoformat()


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        _fail("oam_work_order_field_invalid", f"OAM工单字段无效：{field}")
    normalized = value.strip()
    if (
        normalized != value
        or not 1 <= len(normalized) <= maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        _fail("oam_work_order_field_invalid", f"OAM工单字段无效：{field}")
    return normalized


def _run_key(snapshot: ExternalSyncSnapshot) -> str:
    digest = hashlib.sha256(
        f"{snapshot.source_instance}\0{snapshot.snapshot_id}".encode("utf-8")
    ).hexdigest()
    return f"oam-work-order:{digest}"


def _formal_scope_key(snapshot: ExternalSyncSnapshot) -> str:
    digest = hashlib.sha256(
        f"{snapshot.source_instance}\0{snapshot.scope_key}".encode("utf-8")
    ).hexdigest()
    return f"oam-work-order-scope:{digest}"


def _event_id(snapshot: ExternalSyncSnapshot, business_key: str) -> str:
    digest = hashlib.sha256(business_key.encode("utf-8")).hexdigest()
    return f"{snapshot.snapshot_id}:wo:{digest}"


def _fail(code: str, message: str) -> None:
    raise OamWorkOrderProjectionError(code, message)


__all__ = [
    "OamWorkOrderProjectionError",
    "OamWorkOrderProjectionResult",
    "next_unpublished_work_order_snapshot_id",
    "publish_completed_work_order_snapshot",
    "record_failed_work_order_snapshot",
    "work_order_snapshot_lock_coordinates",
]
