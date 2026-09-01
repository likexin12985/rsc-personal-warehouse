from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_roles
from ..models import (
    ExternalSyncCurrentRecord,
    ExternalSyncSnapshot,
    OamPersonnelBinding,
    User,
)


router = APIRouter(prefix="/work-orders", tags=["work-orders"])
ALLOWED_ROLES = (
    "admin",
    "provincial_manager",
    "technician",
)
TERMINAL_STATUSES = {
    "end",
    "stopped",
    "closed",
    "rejected",
    "client_accept_pass",
    "platform_accept_pass",
    "source_accept_pass",
}


def _manifest_has_entity(snapshot: ExternalSyncSnapshot, entity_type: str) -> bool:
    try:
        manifest = json.loads(snapshot.manifest_json)
    except (TypeError, json.JSONDecodeError):
        return False
    entities = manifest.get("entities") if isinstance(manifest, dict) else None
    return isinstance(entities, list) and any(
        isinstance(entity, dict) and entity.get("entity_type") == entity_type
        for entity in entities
    )


def _latest_entity_snapshot(
    db: Session, entity_type: str
) -> ExternalSyncSnapshot | None:
    snapshots = db.scalars(
        select(ExternalSyncSnapshot)
        .where(ExternalSyncSnapshot.status == "complete")
        .order_by(
            ExternalSyncSnapshot.snapshot_at.desc(),
            ExternalSyncSnapshot.completed_at.desc(),
        )
        .limit(100)
    )
    return next(
        (snapshot for snapshot in snapshots if _manifest_has_entity(snapshot, entity_type)),
        None,
    )


def _load_entity(
    db: Session, entity_type: str
) -> tuple[ExternalSyncSnapshot | None, list[dict[str, Any]]]:
    snapshot = _latest_entity_snapshot(db, entity_type)
    if snapshot is None:
        return None, []
    rows = db.scalars(
        select(ExternalSyncCurrentRecord).where(
            ExternalSyncCurrentRecord.source_instance == snapshot.source_instance,
            ExternalSyncCurrentRecord.scope_key == snapshot.scope_key,
            ExternalSyncCurrentRecord.entity_type == entity_type,
        )
    )
    payloads = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=503, detail="工单镜像记录格式无效") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=503, detail="工单镜像记录不是对象")
        payloads.append(payload)
    return snapshot, payloads


def _province_key(value: Any) -> str:
    text = str(value or "").strip()
    for suffix in ("特别行政区", "自治区", "省", "市"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def _binding_for(db: Session, actor: User) -> OamPersonnelBinding | None:
    return db.scalar(
        select(OamPersonnelBinding).where(
            OamPersonnelBinding.user_id == actor.id,
            OamPersonnelBinding.login_enabled.is_(True),
            OamPersonnelBinding.source_active.is_(True),
        )
    )


def _can_view(
    row: dict[str, Any],
    actor: User,
    binding: OamPersonnelBinding | None,
) -> bool:
    if actor.role == "admin":
        return True
    if actor.role == "provincial_manager":
        return bool(actor.province) and _province_key(row.get("province")) == _province_key(
            actor.province
        )
    if binding is None:
        return False
    executor_id = str(row.get("executorId") or "").strip()
    executor_phone = str(row.get("executorPhone") or "").strip()
    executor = str(row.get("executor") or "").strip()
    ids = {binding.oam_account_id.strip(), binding.account.strip()}
    ids.discard("")
    return (
        (bool(executor_id) and executor_id in ids)
        or (bool(binding.mobile) and executor_phone == binding.mobile)
        or (
            bool(binding.name)
            and (executor == binding.name or executor.endswith(f"-{binding.name}"))
        )
    )


def _detail_by_code(
    details: list[dict[str, Any]],
    relation_chunks: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    result = {}
    for detail in details:
        summary = detail.get("summary")
        code = str(summary.get("code") or "").strip() if isinstance(summary, dict) else ""
        if not code or code in result:
            raise HTTPException(status_code=503, detail="工单详情镜像编号缺失或重复")
        result[code] = detail

    grouped: dict[str, dict[int, dict[str, Any]]] = {}
    expected_counts: dict[str, int] = {}
    for chunk in relation_chunks or []:
        code = str(chunk.get("workOrderCode") or "").strip()
        section = str(chunk.get("section") or "").strip()
        index = chunk.get("chunkIndex")
        count = chunk.get("chunkCount")
        items = chunk.get("items")
        if (
            not code
            or section != "relatedOrders"
            or not isinstance(index, int)
            or not isinstance(count, int)
            or count < 1
            or index < 0
            or index >= count
            or not isinstance(items, list)
        ):
            raise HTTPException(status_code=503, detail="工单关联镜像分块格式无效")
        if code not in result:
            raise HTTPException(status_code=503, detail="工单关联镜像存在孤立分块")
        if code in expected_counts and expected_counts[code] != count:
            raise HTTPException(status_code=503, detail="工单关联镜像分块总数不一致")
        expected_counts[code] = count
        by_index = grouped.setdefault(code, {})
        if index in by_index:
            raise HTTPException(status_code=503, detail="工单关联镜像分块重复")
        by_index[index] = chunk

    for code, by_index in grouped.items():
        count = expected_counts[code]
        if set(by_index) != set(range(count)):
            raise HTTPException(status_code=503, detail="工单关联镜像分块不完整")
        detail = result[code]
        existing = detail.get("relatedOrders")
        if existing not in (None, []):
            raise HTTPException(status_code=503, detail="工单关联镜像主子记录重复")
        merged = []
        for index in range(count):
            merged.extend(by_index[index]["items"])
        result[code] = {**detail, "relatedOrders": merged}
    return result


@router.get("")
def list_work_orders(
    search: str = Query(default="", max_length=100),
    status: str = Query(default="", max_length=50),
    order_type: str = Query(default="", max_length=40),
    province: str = Query(default="", max_length=40),
    warranty: str = Query(default="", max_length=20),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    actor: User = Depends(require_roles(*ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    snapshot, rows = _load_entity(db, "work_order")
    _, details = _load_entity(db, "work_order_detail")
    _, relation_chunks = _load_entity(db, "work_order_relation")
    details_by_code = _detail_by_code(details, relation_chunks)
    binding = _binding_for(db, actor) if actor.role == "technician" else None
    if actor.role == "technician" and binding is None:
        raise HTTPException(status_code=403, detail="当前账号尚未绑定有效OAM人员")
    rows = [row for row in rows if _can_view(row, actor, binding)]
    available = len(rows)
    for row in rows:
        code = str(row.get("code") or "").strip()
        detail = details_by_code.get(code)
        detail_summary = detail.get("summary") if isinstance(detail, dict) else None
        row["detailAvailable"] = detail is not None
        row["warranty"] = (
            str(detail_summary.get("warranty") or "")
            if isinstance(detail_summary, dict)
            else ""
        )
        row["deviceCodes"] = row.get("deviceCodes") or (
            [
                target.get("deviceCode")
                for target in detail.get("targets", [])
                if isinstance(target, dict) and target.get("deviceCode")
            ]
            if detail
            else []
        )
    status_counts = Counter(str(row.get("statusCode") or "") for row in rows)
    province_counts = Counter(str(row.get("province") or "") for row in rows if row.get("province"))
    type_counts = Counter(str(row.get("type") or "") for row in rows if row.get("type"))
    warranty_counts = Counter(str(row.get("warranty") or "") for row in rows if row.get("warranty"))
    active = sum(
        str(row.get("statusCode") or "") not in TERMINAL_STATUSES for row in rows
    )
    with_detail = sum(bool(row.get("detailAvailable")) for row in rows)
    term = search.strip().casefold()
    if term:
        rows = [
            row
            for row in rows
            if term
            in " ".join(
                (
                    str(row.get("code") or ""),
                    str(row.get("serviceCode") or ""),
                    str(row.get("title") or ""),
                    str(row.get("executor") or ""),
                    str(row.get("brandName") or ""),
                    " ".join(str(value) for value in row.get("deviceCodes") or []),
                )
            ).casefold()
        ]
    if status:
        rows = [row for row in rows if str(row.get("statusCode") or "") == status]
    if order_type:
        rows = [
            row
            for row in rows
            if order_type in {str(row.get("type") or ""), str(row.get("typeCode") or "")}
        ]
    if province:
        rows = [
            row
            for row in rows
            if _province_key(row.get("province")) == _province_key(province)
        ]
    if warranty:
        rows = [row for row in rows if str(row.get("warranty") or "") == warranty]
    rows.sort(
        key=lambda row: (
            str(row.get("updateTime") or ""),
            str(row.get("createTime") or ""),
            str(row.get("code") or ""),
        ),
        reverse=True,
    )
    total = len(rows)
    return {
        "summary": {
            "available": available,
            "active": active,
            "withDetail": with_detail,
            "statuses": dict(status_counts),
            "provinces": dict(province_counts),
            "types": dict(type_counts),
            "warranties": dict(warranty_counts),
        },
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": rows[offset : offset + limit],
        "snapshot": None
        if snapshot is None
        else {
            "id": snapshot.snapshot_id,
            "at": snapshot.snapshot_at,
            "completedAt": snapshot.completed_at,
            "scope": snapshot.scope_key,
        },
    }


@router.get("/{work_order_code}")
def get_work_order(
    work_order_code: str,
    actor: User = Depends(require_roles(*ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    code = work_order_code.strip()
    if not code or len(code) > 100:
        raise HTTPException(status_code=400, detail="工单编号无效")
    snapshot, rows = _load_entity(db, "work_order")
    row = next((item for item in rows if str(item.get("code") or "") == code), None)
    if row is None:
        raise HTTPException(status_code=404, detail="工单不存在或尚未同步")
    binding = _binding_for(db, actor) if actor.role == "technician" else None
    if actor.role == "technician" and binding is None:
        raise HTTPException(status_code=403, detail="当前账号尚未绑定有效OAM人员")
    if not _can_view(row, actor, binding):
        raise HTTPException(status_code=403, detail="无权查看该工单")
    _, details = _load_entity(db, "work_order_detail")
    _, relation_chunks = _load_entity(db, "work_order_relation")
    detail = _detail_by_code(details, relation_chunks).get(code)
    if detail is None:
        raise HTTPException(status_code=409, detail="该工单详情正在分批同步，请稍后刷新")
    summary = detail.get("summary")
    if not isinstance(summary, dict) or summary.get("code") != code:
        raise HTTPException(status_code=503, detail="工单详情镜像校验失败")
    return {
        **detail,
        "listRow": row,
        "snapshot": None
        if snapshot is None
        else {
            "id": snapshot.snapshot_id,
            "at": snapshot.snapshot_at,
            "completedAt": snapshot.completed_at,
            "scope": snapshot.scope_key,
        },
    }
