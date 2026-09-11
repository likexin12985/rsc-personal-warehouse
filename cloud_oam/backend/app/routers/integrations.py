import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import client_ip, require_roles
from ..external_sync_scope_lock import lock_external_sync_scope
from ..foundation_models import SourceSystem, SyncConflict, SyncRun
from ..models import (
    ExternalSyncBatch,
    ExternalSyncCurrentRecord,
    ExternalSyncRecord,
    ExternalSyncSnapshot,
    ExternalSyncSnapshotBatch,
    ExternalSyncSnapshotRecord,
    User,
)
from ..schemas import (
    EdgeSyncBatchIn,
    EdgeSyncSnapshotBatchIn,
    EdgeSyncSnapshotCompleteIn,
)
from ..services import audit
from ..oam_directory import OamDirectoryError, reconcile_personnel_snapshot


ingress_router = APIRouter(prefix="/integrations/oam/edge", tags=["integrations"])
management_router = APIRouter(prefix="/integrations/oam/edge", tags=["integrations"])
settings = get_settings()
HEADER_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")
OAM_SOURCE_SYSTEM_CODE = "starcharge_oam"
OAM_WORK_ORDER_RUN_PREFIX = "oam-work-order:%"
OAM_WORK_ORDER_SCOPE_PREFIX = "oam-work-order-scope:%"
SYNC_FRESHNESS_SECONDS = 45 * 60
WORK_ORDER_SCOPE_PREFIX = "work-orders:recent-"
WORK_ORDER_ENTITY = "work_order"
OAM_RECEIPT_SCOPE_PREFIX = "oam-receipts:"
OAM_RECEIPT_ENTITY = "oam_receipt"
RETIRED_PLAINTEXT_WORK_ORDER_ENTITIES = frozenset(
    {"work_order_detail", "work_order_relation"}
)
WORK_ORDER_STAGING_FIELDS = frozenset(
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


@dataclass(frozen=True)
class VerifiedEdgeRequest:
    source_instance: str
    batch_id: str
    body: bytes
    body_sha256: str


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _database_utc_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        raise RuntimeError("数据库未返回有效UTC当前时间")
    return _aware_utc(value)


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return round((_aware_utc(now) - _aware_utc(value)).total_seconds(), 3)


def _fresh_age(age_seconds: float | None) -> bool:
    return (
        age_seconds is not None
        and 0 <= age_seconds <= SYNC_FRESHNESS_SECONDS
    )


def _snapshot_metadata_matches(
    snapshot: ExternalSyncSnapshot,
    payload: EdgeSyncSnapshotBatchIn | EdgeSyncSnapshotCompleteIn,
    source_instance: str,
) -> bool:
    return all(
        (
            snapshot.source_system == payload.source_system,
            snapshot.source_instance == source_instance,
            snapshot.snapshot_id == payload.snapshot_id,
            snapshot.scope_key == payload.scope_key,
            snapshot.sync_mode == payload.sync_mode,
            snapshot.company_id == payload.company_id,
            snapshot.org_code == payload.org_code,
            _utc_iso(snapshot.snapshot_at) == _utc_iso(payload.snapshot_at),
        )
    )


def _validate_snapshot_entity_boundary(
    payload: EdgeSyncSnapshotBatchIn,
) -> None:
    """Reject retired plaintext details and enforce the seven-field feed."""

    if payload.entity_type in RETIRED_PLAINTEXT_WORK_ORDER_ENTITIES:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="明文工单详情暂存协议已停用",
        )
    is_work_order_scope = payload.scope_key.startswith(WORK_ORDER_SCOPE_PREFIX)
    if is_work_order_scope != (payload.entity_type == WORK_ORDER_ENTITY):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="工单实体与专用同步范围不一致",
        )
    is_oam_receipt_scope = payload.scope_key.startswith(OAM_RECEIPT_SCOPE_PREFIX)
    if is_oam_receipt_scope != (payload.entity_type == OAM_RECEIPT_ENTITY):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OAM收货实体与专用同步范围不一致",
        )
    if not is_work_order_scope:
        return
    for record in payload.records:
        if record.operation == "delete":
            if record.data != {} or record.source_updated_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="工单删除增量不得携带业务载荷",
                )
            continue
        if set(record.data) != WORK_ORDER_STAGING_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="工单暂存载荷超出七字段白名单",
            )
        if record.data.get("authCompanyId") != payload.company_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="工单企业范围与快照头不一致",
            )


def _validate_snapshot_manifest_boundary(
    payload: EdgeSyncSnapshotCompleteIn,
) -> None:
    entity_types = {entity.entity_type for entity in payload.entities}
    if entity_types & RETIRED_PLAINTEXT_WORK_ORDER_ENTITIES:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="明文工单详情暂存协议已停用",
        )
    is_work_order_scope = payload.scope_key.startswith(WORK_ORDER_SCOPE_PREFIX)
    if is_work_order_scope and entity_types != {WORK_ORDER_ENTITY}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="正式工单完成清单只能包含七字段工单实体",
        )
    if not is_work_order_scope and WORK_ORDER_ENTITY in entity_types:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="工单实体与专用同步范围不一致",
        )
    is_oam_receipt_scope = payload.scope_key.startswith(OAM_RECEIPT_SCOPE_PREFIX)
    if is_oam_receipt_scope and entity_types != {OAM_RECEIPT_ENTITY}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OAM收货完成清单只能包含收货实体",
        )
    if not is_oam_receipt_scope and OAM_RECEIPT_ENTITY in entity_types:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="OAM收货实体与专用同步范围不一致",
        )


def _get_or_create_snapshot(
    db: Session,
    payload: EdgeSyncSnapshotBatchIn | EdgeSyncSnapshotCompleteIn,
    source_instance: str,
    *,
    for_update: bool = False,
) -> ExternalSyncSnapshot:
    if for_update:
        # The current mirror namespace is source+scope.  Serialize the whole
        # coordinate before the first snapshot insert, and permanently bind it
        # to one source system, company and organization.  A reinstalled edge
        # state must never relabel an existing cloud namespace and replace its
        # current rows under a different tenant header.
        lock_external_sync_scope(
            db,
            source_instance=source_instance,
            scope_key=payload.scope_key,
        )
        scope_binding = db.scalar(
            select(ExternalSyncSnapshot)
            .where(
                ExternalSyncSnapshot.source_instance == source_instance,
                ExternalSyncSnapshot.scope_key == payload.scope_key,
            )
            .order_by(ExternalSyncSnapshot.received_at, ExternalSyncSnapshot.id)
            .limit(1)
        )
        if scope_binding is not None and any(
            (
                scope_binding.source_system != payload.source_system,
                scope_binding.company_id != payload.company_id,
                scope_binding.org_code != payload.org_code,
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="同步范围已绑定其他企业或组织坐标",
            )
    query = select(ExternalSyncSnapshot).where(
        ExternalSyncSnapshot.source_instance == source_instance,
        ExternalSyncSnapshot.snapshot_id == payload.snapshot_id,
    )
    if for_update:
        query = query.with_for_update()
    snapshot = db.scalar(query)
    if snapshot is None:
        snapshot = ExternalSyncSnapshot(
            source_system=payload.source_system,
            source_instance=source_instance,
            snapshot_id=payload.snapshot_id,
            scope_key=payload.scope_key,
            sync_mode=payload.sync_mode,
            company_id=payload.company_id,
            org_code=payload.org_code,
            snapshot_at=payload.snapshot_at,
        )
        db.add(snapshot)
        db.flush()
        return snapshot
    if not _snapshot_metadata_matches(snapshot, payload, source_instance):
        raise HTTPException(status_code=409, detail="快照元数据与既有批次不一致")
    return snapshot


def _wire_record(
    *,
    business_key: str,
    source_updated_at: datetime | None,
    payload_json: str,
) -> dict[str, Any]:
    return {
        "business_key": business_key,
        "source_updated_at": _utc_iso(source_updated_at),
        "data": json.loads(payload_json),
    }


def _wire_delta(record: ExternalSyncSnapshotRecord) -> dict[str, Any]:
    return {
        **_wire_record(
            business_key=record.business_key,
            source_updated_at=record.source_updated_at,
            payload_json=record.payload_json,
        ),
        "operation": record.operation,
    }


def _records_sha256(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_canonical_json(records).encode("utf-8")).hexdigest()


def _signing_message(
    timestamp: str,
    source_instance: str,
    batch_id: str,
    body: bytes,
) -> bytes:
    return b"\n".join(
        (
            timestamp.encode("ascii"),
            source_instance.encode("utf-8"),
            batch_id.encode("utf-8"),
            body,
        )
    )


async def verify_edge_request(
    request: Request,
    source_instance: str = Header(alias="X-RSC-Edge-Source"),
    timestamp_value: str = Header(alias="X-RSC-Edge-Timestamp"),
    batch_id: str = Header(alias="X-RSC-Edge-Batch"),
    signature: str = Header(alias="X-RSC-Edge-Signature"),
) -> VerifiedEdgeRequest:
    if not settings.edge_sync_configuration_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAM边缘同步尚未启用",
        )
    if (
        not 1 <= len(source_instance) <= 128
        or not 1 <= len(batch_id) <= 128
        or not HEADER_VALUE_PATTERN.fullmatch(source_instance)
        or not HEADER_VALUE_PATTERN.fullmatch(batch_id)
    ):
        raise HTTPException(status_code=400, detail="同步请求标识格式无效")
    allowed_sources = settings.edge_sync_allowed_source_set()
    if allowed_sources and source_instance not in allowed_sources:
        raise HTTPException(status_code=401, detail="同步来源未获授权")
    try:
        timestamp = int(timestamp_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="同步时间戳格式无效") from exc
    if abs(int(time.time()) - timestamp) > settings.edge_sync_max_clock_skew_seconds:
        raise HTTPException(status_code=401, detail="同步请求已过期")

    body = await request.body()
    if len(body) > settings.edge_sync_max_body_bytes:
        raise HTTPException(status_code=413, detail="同步批次超过大小限制")
    expected = hmac.new(
        settings.edge_sync_secret.encode("utf-8"),
        _signing_message(timestamp_value, source_instance, batch_id, body),
        hashlib.sha256,
    ).hexdigest()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", signature) or not hmac.compare_digest(
        expected,
        signature.lower(),
    ):
        raise HTTPException(status_code=401, detail="同步签名无效")
    return VerifiedEdgeRequest(
        source_instance=source_instance,
        batch_id=batch_id,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
    )


@ingress_router.post("/batches")
def receive_edge_batch(
    payload: EdgeSyncBatchIn,
    request: Request,
    verified: VerifiedEdgeRequest = Depends(verify_edge_request),
    db: Session = Depends(get_db),
):
    if not settings.edge_sync_legacy_batches_enabled:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="旧版逐批覆盖接口已停用，请使用完整快照协议",
        )
    if len(payload.records) > settings.edge_sync_max_records_per_batch:
        raise HTTPException(status_code=413, detail="同步记录数超过批次限制")

    existing_batch = db.scalar(
        select(ExternalSyncBatch).where(
            ExternalSyncBatch.source_instance == verified.source_instance,
            ExternalSyncBatch.batch_id == verified.batch_id,
        )
    )
    if existing_batch:
        if existing_batch.body_sha256 != verified.body_sha256:
            raise HTTPException(status_code=409, detail="批次编号已被不同内容占用")
        return {
            "ok": True,
            "duplicate": True,
            "batch_id": existing_batch.batch_id,
            "accepted_records": existing_batch.record_count,
        }

    batch = ExternalSyncBatch(
        source_system=payload.source_system,
        source_instance=verified.source_instance,
        batch_id=verified.batch_id,
        entity_type=payload.entity_type,
        snapshot_at=payload.snapshot_at,
        record_count=len(payload.records),
        body_sha256=verified.body_sha256,
    )
    db.add(batch)
    db.flush()

    business_keys = [record.business_key for record in payload.records]
    current_records = {
        record.business_key: record
        for record in db.scalars(
            select(ExternalSyncRecord).where(
                ExternalSyncRecord.source_instance == verified.source_instance,
                ExternalSyncRecord.entity_type == payload.entity_type,
                ExternalSyncRecord.business_key.in_(business_keys),
            )
        )
    } if business_keys else {}

    created = 0
    updated = 0
    unchanged = 0
    for incoming in payload.records:
        payload_json = json.dumps(
            incoming.data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        current = current_records.get(incoming.business_key)
        if current is None:
            db.add(
                ExternalSyncRecord(
                    source_system=payload.source_system,
                    source_instance=verified.source_instance,
                    entity_type=payload.entity_type,
                    business_key=incoming.business_key,
                    source_updated_at=incoming.source_updated_at,
                    payload_json=payload_json,
                    payload_sha256=payload_sha256,
                    last_batch_id=batch.id,
                )
            )
            created += 1
            continue
        if current.payload_sha256 == payload_sha256:
            unchanged += 1
        else:
            current.payload_json = payload_json
            current.payload_sha256 = payload_sha256
            updated += 1
        current.source_updated_at = incoming.source_updated_at
        current.last_batch_id = batch.id

    audit(
        db,
        actor=None,
        action="edge_sync.receive",
        entity_type="external_sync_batch",
        entity_id=batch.id,
        detail={
            "sourceSystem": payload.source_system,
            "sourceInstance": verified.source_instance,
            "batchId": verified.batch_id,
            "entityType": payload.entity_type,
            "recordCount": len(payload.records),
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "mode": "staging_only",
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "ok": True,
        "duplicate": False,
        "batch_id": batch.batch_id,
        "accepted_records": batch.record_count,
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "mode": "staging_only",
    }


@ingress_router.post("/snapshots/batches")
def receive_snapshot_batch(
    payload: EdgeSyncSnapshotBatchIn,
    request: Request,
    verified: VerifiedEdgeRequest = Depends(verify_edge_request),
    db: Session = Depends(get_db),
):
    if len(payload.records) > settings.edge_sync_max_records_per_batch:
        raise HTTPException(status_code=413, detail="同步记录数超过批次限制")
    _validate_snapshot_entity_boundary(payload)

    snapshot = _get_or_create_snapshot(
        db,
        payload,
        verified.source_instance,
        for_update=True,
    )

    # Recheck after the snapshot-scoped lock: a concurrent retry may have
    # committed between request verification and lock acquisition.
    existing_batch = db.scalar(
        select(ExternalSyncSnapshotBatch).where(
            ExternalSyncSnapshotBatch.source_instance == verified.source_instance,
            ExternalSyncSnapshotBatch.batch_id == verified.batch_id,
        )
    )
    if existing_batch:
        if existing_batch.snapshot_ref_id != snapshot.id:
            raise HTTPException(status_code=409, detail="批次编号已绑定其他快照")
        if existing_batch.body_sha256 != verified.body_sha256:
            raise HTTPException(status_code=409, detail="批次编号已被不同内容占用")
        return {
            "ok": True,
            "duplicate": True,
            "snapshot_id": payload.snapshot_id,
            "batch_id": existing_batch.batch_id,
            "accepted_records": existing_batch.record_count,
            "mode": "staging_only",
        }
    if snapshot.status != "receiving":
        raise HTTPException(status_code=409, detail="已完成快照不得追加批次")

    occupied_sequence = db.scalar(
        select(ExternalSyncSnapshotBatch).where(
            ExternalSyncSnapshotBatch.snapshot_ref_id == snapshot.id,
            ExternalSyncSnapshotBatch.entity_type == payload.entity_type,
            ExternalSyncSnapshotBatch.sequence == payload.sequence,
        )
    )
    if occupied_sequence is not None:
        raise HTTPException(status_code=409, detail="快照批次序号已被其他批次占用")

    business_keys = [record.business_key for record in payload.records]
    duplicate_key = db.scalar(
        select(ExternalSyncSnapshotRecord.business_key).where(
            ExternalSyncSnapshotRecord.snapshot_ref_id == snapshot.id,
            ExternalSyncSnapshotRecord.entity_type == payload.entity_type,
            ExternalSyncSnapshotRecord.business_key.in_(business_keys),
        ).limit(1)
    )
    if duplicate_key:
        raise HTTPException(status_code=409, detail="快照内业务键跨批次重复")

    batch = ExternalSyncSnapshotBatch(
        snapshot_ref_id=snapshot.id,
        source_instance=verified.source_instance,
        batch_id=verified.batch_id,
        entity_type=payload.entity_type,
        sequence=payload.sequence,
        total_sequences=payload.total_sequences,
        record_count=len(payload.records),
        body_sha256=verified.body_sha256,
    )
    db.add(batch)
    for incoming in payload.records:
        payload_json = _canonical_json(incoming.data)
        db.add(
            ExternalSyncSnapshotRecord(
                snapshot_ref_id=snapshot.id,
                entity_type=payload.entity_type,
                business_key=incoming.business_key,
                operation=incoming.operation,
                source_updated_at=incoming.source_updated_at,
                payload_json=payload_json,
                payload_sha256=hashlib.sha256(
                    payload_json.encode("utf-8")
                ).hexdigest(),
            )
        )

    # The 0044 audit RLS predicate rereads the receiving snapshot and staged
    # facts.  Make those facts visible inside this transaction before the
    # append-only transport audit is emitted.
    db.flush()
    audit(
        db,
        actor=None,
        action="edge_sync.snapshot_batch.receive",
        entity_type="external_sync_snapshot",
        entity_id=snapshot.id,
        detail={
            "sourceInstance": verified.source_instance,
            "snapshotId": snapshot.snapshot_id,
            "scopeKey": snapshot.scope_key,
            "entityType": payload.entity_type,
            "sequence": payload.sequence,
            "totalSequences": payload.total_sequences,
            "recordCount": len(payload.records),
            "mode": "staging_only",
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "ok": True,
        "duplicate": False,
        "snapshot_id": snapshot.snapshot_id,
        "batch_id": batch.batch_id,
        "accepted_records": batch.record_count,
        "mode": "staging_only",
    }


@ingress_router.post("/snapshots/complete")
def complete_snapshot(
    payload: EdgeSyncSnapshotCompleteIn,
    request: Request,
    verified: VerifiedEdgeRequest = Depends(verify_edge_request),
    db: Session = Depends(get_db),
):
    _validate_snapshot_manifest_boundary(payload)
    snapshot = _get_or_create_snapshot(
        db,
        payload,
        verified.source_instance,
        for_update=True,
    )
    if snapshot.status == "complete":
        if snapshot.manifest_sha256 != verified.body_sha256:
            raise HTTPException(status_code=409, detail="快照已由不同完成清单封存")
        return {
            "ok": True,
            "duplicate": True,
            "snapshot_id": snapshot.snapshot_id,
            "status": snapshot.status,
            "mode": "staging_only",
        }
    if snapshot.status != "receiving":
        raise HTTPException(status_code=409, detail="快照状态不允许完成")

    # Completing a snapshot replaces the isolated current mirror.  Use the
    # same exact source/scope lock as the formal projector so a newly completed
    # snapshot cannot race an older snapshot into the formal projection.
    lock_external_sync_scope(
        db,
        source_instance=verified.source_instance,
        scope_key=payload.scope_key,
    )

    newer_snapshot = db.scalar(
        select(ExternalSyncSnapshot)
        .where(
            ExternalSyncSnapshot.source_system == payload.source_system,
            ExternalSyncSnapshot.source_instance == verified.source_instance,
            ExternalSyncSnapshot.scope_key == payload.scope_key,
            ExternalSyncSnapshot.status == "complete",
            ExternalSyncSnapshot.snapshot_id != payload.snapshot_id,
            ExternalSyncSnapshot.snapshot_at >= payload.snapshot_at,
        )
        .order_by(
            ExternalSyncSnapshot.snapshot_at.desc(),
            ExternalSyncSnapshot.completed_at.desc(),
        )
        .limit(1)
    )
    if newer_snapshot is not None:
        snapshot.status = "rejected_stale"
        db.flush()
        audit(
            db,
            actor=None,
            action="edge_sync.snapshot.reject_stale",
            entity_type="external_sync_snapshot",
            entity_id=snapshot.id,
            detail={
                "sourceInstance": verified.source_instance,
                "snapshotId": snapshot.snapshot_id,
                "scopeKey": snapshot.scope_key,
                "snapshotAt": _utc_iso(snapshot.snapshot_at),
                "newerSnapshotId": newer_snapshot.snapshot_id,
                "newerSnapshotAt": _utc_iso(newer_snapshot.snapshot_at),
                "mode": "staging_only",
            },
            ip_address=client_ip(request),
        )
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="快照来源时间不晚于当前有效快照，已隔离且未更新投影",
        )

    prepared_states: dict[str, dict[str, dict[str, Any]]] = {}
    entity_results: dict[str, dict[str, int | str]] = {}
    for manifest in payload.entities:
        batches = list(
            db.scalars(
                select(ExternalSyncSnapshotBatch)
                .where(
                    ExternalSyncSnapshotBatch.snapshot_ref_id == snapshot.id,
                    ExternalSyncSnapshotBatch.entity_type == manifest.entity_type,
                )
                .order_by(ExternalSyncSnapshotBatch.sequence)
            )
        )
        if len(batches) != manifest.batch_count:
            raise HTTPException(status_code=409, detail="接收批次数与完成清单不一致")
        if batches:
            expected_sequences = list(range(1, manifest.batch_count + 1))
            if [batch.sequence for batch in batches] != expected_sequences or any(
                batch.total_sequences != manifest.batch_count for batch in batches
            ):
                raise HTTPException(status_code=409, detail="同步批次序列不完整")
        if sum(batch.record_count for batch in batches) != manifest.delta_record_count:
            raise HTTPException(status_code=409, detail="增量记录数与完成清单不一致")

        delta_rows = list(
            db.scalars(
                select(ExternalSyncSnapshotRecord)
                .where(
                    ExternalSyncSnapshotRecord.snapshot_ref_id == snapshot.id,
                    ExternalSyncSnapshotRecord.entity_type == manifest.entity_type,
                )
                .order_by(ExternalSyncSnapshotRecord.business_key)
            )
        )
        delta_wire = [_wire_delta(record) for record in delta_rows]
        if len(delta_wire) != manifest.delta_record_count:
            raise HTTPException(status_code=409, detail="暂存增量记录数不完整")
        if _records_sha256(delta_wire) != manifest.delta_sha256:
            raise HTTPException(status_code=409, detail="增量记录哈希校验失败")

        current_rows = list(
            db.scalars(
                select(ExternalSyncCurrentRecord).where(
                    ExternalSyncCurrentRecord.source_instance
                    == verified.source_instance,
                    ExternalSyncCurrentRecord.scope_key == payload.scope_key,
                    ExternalSyncCurrentRecord.entity_type == manifest.entity_type,
                ).with_for_update()
            )
        )
        final_state: dict[str, dict[str, Any]] = {}
        if payload.sync_mode == "incremental":
            final_state = {
                row.business_key: {
                    "source_updated_at": row.source_updated_at,
                    "payload_json": row.payload_json,
                    "payload_sha256": row.payload_sha256,
                }
                for row in current_rows
            }
        for delta in delta_rows:
            if delta.operation == "delete":
                final_state.pop(delta.business_key, None)
                continue
            final_state[delta.business_key] = {
                "source_updated_at": delta.source_updated_at,
                "payload_json": delta.payload_json,
                "payload_sha256": delta.payload_sha256,
            }

        final_wire = [
            _wire_record(
                business_key=business_key,
                source_updated_at=state["source_updated_at"],
                payload_json=state["payload_json"],
            )
            for business_key, state in sorted(final_state.items())
        ]
        if len(final_wire) != manifest.final_record_count:
            raise HTTPException(status_code=409, detail="最终记录数校验失败")
        if _records_sha256(final_wire) != manifest.final_sha256:
            raise HTTPException(status_code=409, detail="最终快照哈希校验失败")

        prepared_states[manifest.entity_type] = final_state
        entity_results[manifest.entity_type] = {
            "records": len(final_wire),
            "sha256": manifest.final_sha256,
            "deltaRecords": len(delta_wire),
        }

    for entity_type, final_state in prepared_states.items():
        existing_rows = list(
            db.scalars(
                select(ExternalSyncCurrentRecord).where(
                    ExternalSyncCurrentRecord.source_instance
                    == verified.source_instance,
                    ExternalSyncCurrentRecord.scope_key == payload.scope_key,
                    ExternalSyncCurrentRecord.entity_type == entity_type,
                ).with_for_update()
            )
        )
        existing_by_key = {row.business_key: row for row in existing_rows}
        for business_key, current in existing_by_key.items():
            if business_key not in final_state:
                db.delete(current)
        for business_key, state in final_state.items():
            current = existing_by_key.get(business_key)
            if current is None:
                db.add(
                    ExternalSyncCurrentRecord(
                        source_system=payload.source_system,
                        source_instance=verified.source_instance,
                        scope_key=payload.scope_key,
                        entity_type=entity_type,
                        business_key=business_key,
                        source_updated_at=state["source_updated_at"],
                        payload_json=state["payload_json"],
                        payload_sha256=state["payload_sha256"],
                        last_snapshot_id=snapshot.id,
                    )
                )
                continue
            current.source_updated_at = state["source_updated_at"]
            current.payload_json = state["payload_json"]
            current.payload_sha256 = state["payload_sha256"]
            current.last_snapshot_id = snapshot.id

    # Current-record policies prove the replacement against a receiving
    # snapshot.  Flush the complete mirror while the parent is still in that
    # state, then seal the parent in a separate statement below.
    db.flush()
    personnel_result: dict[str, Any] | None = None
    if (
        "employee" in prepared_states
        and settings.edge_sync_legacy_personnel_projection_enabled
    ):
        try:
            personnel_result = reconcile_personnel_snapshot(
                db,
                snapshot=snapshot,
                final_state=prepared_states["employee"],
            )
        except OamDirectoryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    elif "employee" in prepared_states:
        personnel_result = {
            "status": "deferred",
            "reason": "人员投影须由云端受控任务校验后执行",
        }

    snapshot.status = "complete"
    snapshot.manifest_json = _canonical_json(payload.model_dump(mode="json"))
    snapshot.manifest_sha256 = verified.body_sha256
    snapshot.completed_at = datetime.now(timezone.utc)
    # Persist current-mirror replacement and the terminal one-way seal before
    # the audit predicate proves that this is a completed snapshot.
    db.flush()
    audit(
        db,
        actor=None,
        action="edge_sync.snapshot.complete",
        entity_type="external_sync_snapshot",
        entity_id=snapshot.id,
        detail={
            "sourceInstance": verified.source_instance,
            "snapshotId": snapshot.snapshot_id,
            "scopeKey": snapshot.scope_key,
            "syncMode": snapshot.sync_mode,
            "entities": entity_results,
            "mode": "staging_only",
            "personnel": personnel_result,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "ok": True,
        "duplicate": False,
        "snapshot_id": snapshot.snapshot_id,
        "status": snapshot.status,
        "entities": entity_results,
        "mode": "staging_only",
        "personnel": personnel_result,
    }


def _completed_scope_health(
    db: Session,
    *,
    now: datetime,
) -> tuple[list[dict[str, Any]], ExternalSyncSnapshot | None]:
    latest_order = (
        ExternalSyncSnapshot.snapshot_at.desc(),
        ExternalSyncSnapshot.completed_at.desc().nulls_last(),
        ExternalSyncSnapshot.received_at.desc(),
        ExternalSyncSnapshot.id.desc(),
    )
    ranked = (
        select(
            ExternalSyncSnapshot.id.label("snapshot_ref_id"),
            func.row_number()
            .over(
                partition_by=(
                    ExternalSyncSnapshot.source_instance,
                    ExternalSyncSnapshot.scope_key,
                ),
                order_by=latest_order,
            )
            .label("scope_rank"),
        )
        .where(
            ExternalSyncSnapshot.source_system == OAM_SOURCE_SYSTEM_CODE,
            ExternalSyncSnapshot.status == "complete",
        )
        .subquery()
    )
    completed_snapshots = tuple(
        db.scalars(
            select(ExternalSyncSnapshot)
            .join(
                ranked,
                ranked.c.snapshot_ref_id == ExternalSyncSnapshot.id,
            )
            .where(ranked.c.scope_rank == 1)
            .order_by(*latest_order)
        ).all()
    )

    scopes: list[dict[str, Any]] = []
    for snapshot in sorted(
        completed_snapshots,
        key=lambda item: (item.source_instance, item.scope_key),
    ):
        snapshot_age = _age_seconds(now, snapshot.snapshot_at)
        completed_age = _age_seconds(now, snapshot.completed_at)
        ordered_times = (
            snapshot.completed_at is not None
            and _aware_utc(snapshot.snapshot_at)
            <= _aware_utc(snapshot.completed_at)
        )
        fresh = (
            ordered_times
            and _fresh_age(snapshot_age)
            and _fresh_age(completed_age)
        )
        scopes.append(
            {
                "source_system": snapshot.source_system,
                "source_instance": snapshot.source_instance,
                "scope_key": snapshot.scope_key,
                "company_id": snapshot.company_id,
                "org_code": snapshot.org_code,
                "snapshot_id": snapshot.snapshot_id,
                "sync_mode": snapshot.sync_mode,
                "snapshot_at": _aware_utc(snapshot.snapshot_at),
                "completed_at": (
                    _aware_utc(snapshot.completed_at)
                    if snapshot.completed_at is not None
                    else None
                ),
                "age_seconds": snapshot_age,
                "completed_age_seconds": completed_age,
                "fresh": fresh,
                "freshness_status": "fresh" if fresh else "stale",
                "healthy": fresh,
            }
        )
    return scopes, completed_snapshots[0] if completed_snapshots else None


def _work_order_projection_health(
    db: Session,
    *,
    now: datetime,
) -> dict[str, Any]:
    runs = tuple(
        db.scalars(
            select(SyncRun)
            .join(SourceSystem, SourceSystem.id == SyncRun.source_system_id)
            .where(
                SourceSystem.code == OAM_SOURCE_SYSTEM_CODE,
                SyncRun.run_key.like(OAM_WORK_ORDER_RUN_PREFIX),
                SyncRun.scope_key.like(OAM_WORK_ORDER_SCOPE_PREFIX),
            )
        ).all()
    )
    ordered_runs = sorted(
        runs,
        key=lambda run: (
            _aware_utc(run.completed_at or run.started_at or run.created_at),
            _aware_utc(run.created_at),
            str(run.id),
        ),
        reverse=True,
    )
    latest = ordered_runs[0] if ordered_runs else None
    failed_run_count = sum(run.status == "failed" for run in runs)
    conflict_run_count = sum(run.status == "conflict" for run in runs)
    open_conflict_count = (
        db.scalar(
            select(func.count())
            .select_from(SyncConflict)
            .join(SyncRun, SyncRun.id == SyncConflict.run_id)
            .join(SourceSystem, SourceSystem.id == SyncRun.source_system_id)
            .where(
                SourceSystem.code == OAM_SOURCE_SYSTEM_CODE,
                SyncRun.run_key.like(OAM_WORK_ORDER_RUN_PREFIX),
                SyncRun.scope_key.like(OAM_WORK_ORDER_SCOPE_PREFIX),
                SyncConflict.status == "open",
            )
        )
        or 0
    )
    latest_at = (
        latest.completed_at or latest.started_at or latest.created_at
        if latest is not None
        else None
    )
    latest_age = _age_seconds(now, latest_at)
    fresh = _fresh_age(latest_age)
    healthy = (
        latest is not None
        and latest.status == "completed"
        and latest.completed_at is not None
        and fresh
        and failed_run_count == 0
        and conflict_run_count == 0
        and open_conflict_count == 0
    )
    return {
        "healthy": healthy,
        "fresh": fresh,
        "freshness_status": "fresh" if fresh else "stale",
        "run_count": len(runs),
        "failed_run_count": failed_run_count,
        "conflict_run_count": conflict_run_count,
        "open_conflict_count": open_conflict_count,
        "latest_run": None
        if latest is None
        else {
            "run_id": str(latest.id),
            "run_key": latest.run_key,
            "scope_key": latest.scope_key,
            "sync_mode": latest.mode,
            "status": latest.status,
            "started_at": (
                _aware_utc(latest.started_at)
                if latest.started_at is not None
                else None
            ),
            "completed_at": (
                _aware_utc(latest.completed_at)
                if latest.completed_at is not None
                else None
            ),
            "updated_at": _aware_utc(latest.updated_at),
            "run_at": _aware_utc(latest_at),
            "age_seconds": latest_age,
            "failure_code": latest.failure_code,
        },
    }


@management_router.get("/status")
def edge_sync_status(
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    database_now = _database_utc_now(db)
    last_batch = db.scalar(
        select(ExternalSyncBatch).order_by(ExternalSyncBatch.received_at.desc()).limit(1)
    )
    scopes, last_completed_snapshot = _completed_scope_health(
        db,
        now=database_now,
    )
    work_order_projection = _work_order_projection_health(
        db,
        now=database_now,
    )
    healthy = (
        bool(scopes)
        and all(scope["fresh"] for scope in scopes)
        and work_order_projection["healthy"]
    )
    return {
        "healthy": healthy,
        "database_now": database_now,
        "freshness_threshold_seconds": SYNC_FRESHNESS_SECONDS,
        "scopes": scopes,
        "work_order_projection": work_order_projection,
        # Preserve the existing management contract below.  The global latest
        # snapshot remains informational only and never substitutes for the
        # independent source+scope freshness decisions above.
        # Production receiver secrets intentionally do not exist in the main
        # API process, so its management view must not infer receiver health
        # from local credentials. Persisted batch evidence is authoritative.
        "enabled": None
        if settings.environment == "production"
        else settings.edge_sync_enabled,
        "configured": None
        if settings.environment == "production"
        else settings.edge_sync_configuration_ready(),
        "receiver_configuration": "isolated",
        "mode": "staging_only",
        "batch_count": db.scalar(select(func.count()).select_from(ExternalSyncBatch)) or 0,
        "record_count": db.scalar(select(func.count()).select_from(ExternalSyncRecord)) or 0,
        "snapshot_count": db.scalar(
            select(func.count()).select_from(ExternalSyncSnapshot)
        )
        or 0,
        "current_record_count": db.scalar(
            select(func.count()).select_from(ExternalSyncCurrentRecord)
        )
        or 0,
        "last_batch": None
        if last_batch is None
        else {
            "batch_id": last_batch.batch_id,
            "source_system": last_batch.source_system,
            "source_instance": last_batch.source_instance,
            "entity_type": last_batch.entity_type,
            "record_count": last_batch.record_count,
            "snapshot_at": last_batch.snapshot_at,
            "received_at": last_batch.received_at,
        },
        "last_completed_snapshot": None
        if last_completed_snapshot is None
        else {
            "snapshot_id": last_completed_snapshot.snapshot_id,
            "source_instance": last_completed_snapshot.source_instance,
            "scope_key": last_completed_snapshot.scope_key,
            "sync_mode": last_completed_snapshot.sync_mode,
            "snapshot_at": last_completed_snapshot.snapshot_at,
            "completed_at": last_completed_snapshot.completed_at,
        },
    }
