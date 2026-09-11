"""Project one staged OAM receipt into immutable local evidence.

This boundary consumes only the already completed edge mirror. It never calls
OAM and never changes local receipt or personal-warehouse state. A receipt is
accepted only when an operator-approved external-object-to-shipment mapping
already exists; transport identifiers and human-readable addresses are not
matching keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..foundation_models import ExternalObject, ExternalObjectMapping, SourceSystem
from ..inventory_models import Shipment
from ..models import ExternalSyncCurrentRecord, ExternalSyncSnapshot
from ..schemas import EdgeSyncSnapshotCompleteIn
from .material_request_oam_receipt import (
    OamReceiptEvidenceError,
    record_oam_receipt_evidence,
)


OAM_RECEIPT_ENTITY = "oam_receipt"
OAM_RECEIPT_SCOPE_PREFIX = "oam-receipts:"
PAYLOAD_FIELDS = frozenset({"id", "status", "sourceTime", "sourceVersion"})
STATUSES = frozenset({"synced", "exception"})
SOURCE_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class OamReceiptProjectionError(RuntimeError):
    """Stable, payload-free failure from the receipt projection boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class OamReceiptProjectionResult:
    evidence_id: uuid.UUID
    external_object_id: uuid.UUID
    shipment_id: uuid.UUID
    duplicate: bool


@dataclass(frozen=True, slots=True)
class OamReceiptSnapshotProjectionResult:
    snapshot_id: str
    projected_records: int
    duplicate_records: int


def _fail(code: str, message: str) -> None:
    raise OamReceiptProjectionError(code, message)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _fail("oam_receipt_source_time_invalid", "OAM收货来源时间无效")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise OamReceiptProjectionError(
            "oam_receipt_source_time_invalid", "OAM收货来源时间无效"
        ) from exc
    if parsed.tzinfo is None:
        _fail("oam_receipt_source_time_invalid", "OAM收货来源时间必须带时区")
    return _aware(parsed)


def _payload(record: ExternalSyncCurrentRecord) -> dict[str, Any]:
    if record.entity_type != OAM_RECEIPT_ENTITY:
        _fail("oam_receipt_entity_invalid", "暂存记录不是OAM收货实体")
    if not record.business_key.startswith("oam-receipt:"):
        _fail("oam_receipt_business_key_invalid", "OAM收货业务键无效")
    try:
        payload = json.loads(record.payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise OamReceiptProjectionError(
            "oam_receipt_payload_invalid", "OAM收货暂存载荷不是有效JSON"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_FIELDS:
        _fail("oam_receipt_payload_not_minimal", "OAM收货暂存载荷超出白名单")
    payload_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if payload_hash != record.payload_sha256 or not SHA256_PATTERN.fullmatch(record.payload_sha256 or ""):
        _fail("oam_receipt_payload_hash_mismatch", "OAM收货暂存载荷哈希不一致")
    external_id = payload.get("id")
    if not isinstance(external_id, str) or not external_id.strip() or len(external_id) > 250:
        _fail("oam_receipt_external_id_invalid", "OAM收货外部编号无效")
    if record.business_key != f"oam-receipt:{external_id}":
        _fail("oam_receipt_business_key_mismatch", "OAM收货业务键与外部编号不一致")
    if payload.get("status") not in STATUSES:
        _fail("oam_receipt_status_invalid", "OAM收货状态未纳入正式映射")
    source_version = payload.get("sourceVersion")
    if not isinstance(source_version, str) or not SOURCE_VERSION_PATTERN.fullmatch(source_version):
        _fail("oam_receipt_source_version_invalid", "OAM收货来源版本无效")
    source_time = _source_time(payload.get("sourceTime"))
    if record.source_updated_at is None or _aware(record.source_updated_at) != source_time:
        _fail("oam_receipt_source_time_mismatch", "OAM收货来源时间与暂存坐标不一致")
    return payload


def project_oam_receipt_record(
    db: Session,
    *,
    source: SourceSystem,
    record: ExternalSyncCurrentRecord,
) -> OamReceiptProjectionResult:
    """Project one current mirror row after exact approved mapping checks."""

    if source.code != "starcharge_oam" or source.mode != "read_only" or source.enabled is not True:
        _fail("oam_receipt_source_invalid", "OAM来源系统未处于只读启用状态")
    if not record.scope_key.startswith(OAM_RECEIPT_SCOPE_PREFIX):
        _fail("oam_receipt_scope_invalid", "OAM收货记录不在专用同步范围")
    if record.source_system != source.code:
        _fail("oam_receipt_source_mismatch", "OAM收货来源系统不一致")
    payload = _payload(record)
    external = db.scalar(
        select(ExternalObject).where(
            ExternalObject.source_system_id == source.id,
            ExternalObject.entity_type == OAM_RECEIPT_ENTITY,
            ExternalObject.external_id == payload["id"],
        )
    )
    if external is None:
        _fail("oam_receipt_external_object_unmapped", "OAM收货外部对象尚未建立正式坐标")
    mappings = tuple(
        db.scalars(
            select(ExternalObjectMapping).where(
                ExternalObjectMapping.external_object_id == external.id,
                ExternalObjectMapping.local_object_type == "shipment",
                ExternalObjectMapping.status == "approved",
            )
        ).all()
    )
    if len(mappings) != 1:
        _fail("oam_receipt_shipment_mapping_ambiguous", "OAM收货发运绑定缺失或不唯一")
    try:
        shipment_id = uuid.UUID(mappings[0].local_object_id)
    except (ValueError, TypeError) as exc:
        raise OamReceiptProjectionError(
            "oam_receipt_shipment_mapping_invalid", "OAM收货发运绑定坐标无效"
        ) from exc
    if db.get(Shipment, shipment_id) is None:
        _fail("oam_receipt_shipment_not_found", "OAM收货绑定的发运单不存在")
    try:
        evidence, duplicate = record_oam_receipt_evidence(
            db,
            external_object_id=external.id,
            shipment_id=shipment_id,
            status=payload["status"],
            source_time=_source_time(payload["sourceTime"]),
            source_version=payload["sourceVersion"],
            payload_sha256=record.payload_sha256,
        )
    except OamReceiptEvidenceError as exc:
        raise OamReceiptProjectionError(exc.code, exc.message) from exc
    return OamReceiptProjectionResult(
        evidence_id=evidence.id,
        external_object_id=external.id,
        shipment_id=shipment_id,
        duplicate=duplicate,
    )


def _wire_current(row: ExternalSyncCurrentRecord) -> dict[str, Any]:
    source_time = row.source_updated_at
    if source_time is not None:
        source_time = _aware(source_time).isoformat()
    return {
        "business_key": row.business_key,
        "source_updated_at": source_time,
        "data": json.loads(row.payload_json),
    }


def publish_completed_oam_receipt_snapshot(
    db: Session,
    *,
    snapshot_id: str,
    source: SourceSystem,
) -> OamReceiptSnapshotProjectionResult:
    """Publish one complete receipt mirror without changing local fulfillment."""

    snapshot = db.scalar(
        select(ExternalSyncSnapshot).where(ExternalSyncSnapshot.id == snapshot_id)
    )
    if snapshot is None or snapshot.status != "complete":
        _fail("oam_receipt_snapshot_invalid", "OAM收货暂存快照不存在或未完成")
    if snapshot.source_system != source.code or not snapshot.scope_key.startswith(OAM_RECEIPT_SCOPE_PREFIX):
        _fail("oam_receipt_snapshot_scope_invalid", "OAM收货暂存快照范围无效")
    try:
        manifest = EdgeSyncSnapshotCompleteIn.model_validate_json(snapshot.manifest_json)
    except Exception as exc:
        raise OamReceiptProjectionError(
            "oam_receipt_manifest_invalid", "OAM收货完成清单无效"
        ) from exc
    if (
        manifest.source_system != snapshot.source_system
        or manifest.snapshot_id != snapshot.snapshot_id
        or manifest.scope_key != snapshot.scope_key
        or manifest.sync_mode != snapshot.sync_mode
        or manifest.company_id != snapshot.company_id
        or manifest.org_code != snapshot.org_code
        or _aware(manifest.snapshot_at) != _aware(snapshot.snapshot_at)
        or {entity.entity_type for entity in manifest.entities} != {OAM_RECEIPT_ENTITY}
    ):
        _fail("oam_receipt_manifest_mismatch", "OAM收货完成清单范围不一致")
    canonical_manifest = _canonical(manifest.model_dump(mode="json"))
    if hashlib.sha256(canonical_manifest.encode("utf-8")).hexdigest() != snapshot.manifest_sha256:
        _fail("oam_receipt_manifest_hash_mismatch", "OAM收货完成清单哈希不一致")
    entity = manifest.entities[0]
    rows = tuple(
        db.scalars(
            select(ExternalSyncCurrentRecord)
            .where(
                ExternalSyncCurrentRecord.source_instance == snapshot.source_instance,
                ExternalSyncCurrentRecord.scope_key == snapshot.scope_key,
                ExternalSyncCurrentRecord.entity_type == OAM_RECEIPT_ENTITY,
            )
            .order_by(ExternalSyncCurrentRecord.business_key)
        ).all()
    )
    if any(row.last_snapshot_id != snapshot.id for row in rows):
        _fail("oam_receipt_current_snapshot_mismatch", "OAM收货当前镜像未由该快照确认")
    final_wire = [_wire_current(row) for row in rows]
    if len(final_wire) != entity.final_record_count:
        _fail("oam_receipt_final_count_mismatch", "OAM收货最终记录数校验失败")
    final_hash = hashlib.sha256(_canonical(final_wire).encode("utf-8")).hexdigest()
    if final_hash != entity.final_sha256:
        _fail("oam_receipt_final_hash_mismatch", "OAM收货最终镜像哈希校验失败")
    projected = 0
    duplicates = 0
    for row in rows:
        result = project_oam_receipt_record(db, source=source, record=row)
        projected += 1
        duplicates += int(result.duplicate)
    return OamReceiptSnapshotProjectionResult(
        snapshot_id=snapshot.snapshot_id,
        projected_records=projected,
        duplicate_records=duplicates,
    )


__all__ = [
    "OamReceiptProjectionError",
    "OamReceiptProjectionResult",
    "OamReceiptSnapshotProjectionResult",
    "project_oam_receipt_record",
    "publish_completed_oam_receipt_snapshot",
]
