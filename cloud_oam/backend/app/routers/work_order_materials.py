from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..dependencies import client_ip, get_current_user
from ..models import Material, User, Warehouse, WorkOrderMaterial
from ..schemas import (
    WorkOrderMaterialBatchCreateIn,
    WorkOrderMaterialCreateIn,
    WorkOrderMaterialRecoverIn,
)
from ..services import audit, get_or_create_balance


router = APIRouter(prefix="/work-order-materials", tags=["work-order-materials"])
OPERATOR_ROLES = {"admin", "provincial_manager", "technician"}


def work_material_query():
    return select(WorkOrderMaterial).options(
        joinedload(WorkOrderMaterial.warehouse),
        joinedload(WorkOrderMaterial.user),
        joinedload(WorkOrderMaterial.material),
        joinedload(WorkOrderMaterial.created_by),
    )


def serialize_row(row: WorkOrderMaterial) -> dict:
    return {
        "id": row.id,
        "workOrderNumber": row.work_order_number,
        "status": row.status,
        "warehouse": {
            "id": row.warehouse.id,
            "name": row.warehouse.name,
            "province": row.warehouse.province,
        },
        "user": {"id": row.user.id, "name": row.user.name},
        "material": {
            "id": row.material.id,
            "code": row.material.code,
            "name": row.material.name,
            "unit": row.material.unit,
        },
        "condition": row.condition,
        "quantity": row.quantity,
        "recoveryCondition": row.recovery_condition,
        "note": row.note,
        "createdBy": row.created_by.name,
        "createdAt": row.created_at,
        "settledAt": row.settled_at,
    }


def ensure_access(row: WorkOrderMaterial, user: User) -> None:
    if user.role == "technician" and row.user_id != user.id:
        raise HTTPException(status_code=403, detail="不能处理他人的工单物料")
    if (
        user.role not in {"admin", "technician"}
        and user.province
        and row.warehouse.province != user.province
    ):
        raise HTTPException(status_code=403, detail="不能处理其他省份工单物料")


@router.get("")
def list_work_order_materials(
    q: str = Query(default="", max_length=100),
    status: str = "",
    mine: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = work_material_query().join(WorkOrderMaterial.warehouse)
    if user.role != "admin" and user.province:
        stmt = stmt.where(Warehouse.province == user.province)
    if user.role == "technician" or mine:
        stmt = stmt.where(WorkOrderMaterial.user_id == user.id)
    if status:
        stmt = stmt.where(WorkOrderMaterial.status == status)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.join(WorkOrderMaterial.material).where(
            or_(
                WorkOrderMaterial.work_order_number.ilike(like),
                Material.code.ilike(like),
                Material.name.ilike(like),
            )
        )
    rows = list(db.scalars(stmt.order_by(WorkOrderMaterial.created_at.desc()).limit(limit)))
    return [serialize_row(row) for row in rows]


@router.post("")
def occupy_work_order_material(
    payload: WorkOrderMaterialCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role not in OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="没有此操作权限")
    subject_id = payload.user_id or user.id
    if user.role == "technician" and subject_id != user.id:
        raise HTTPException(status_code=403, detail="只能占用自己的物料")
    subject = db.get(User, subject_id)
    if not subject or not subject.is_active:
        raise HTTPException(status_code=404, detail="领用人不存在")
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not warehouse or not warehouse.is_active:
        raise HTTPException(status_code=404, detail="所属仓库不存在")
    if user.role != "admin" and user.province and warehouse.province != user.province:
        raise HTTPException(status_code=403, detail="不能处理其他省份库存")
    if subject.province and subject.province != warehouse.province:
        raise HTTPException(status_code=400, detail="领用人与所属仓库省份不一致")
    if payload.condition not in {"good", "old"}:
        raise HTTPException(status_code=400, detail="工单只能占用好件或旧件")
    material = db.get(Material, payload.material_id)
    if not material or not material.is_active:
        raise HTTPException(status_code=404, detail="物料不存在或已停用")

    duplicate = db.scalar(
        select(WorkOrderMaterial).where(
            WorkOrderMaterial.work_order_number == payload.work_order_number.strip(),
            WorkOrderMaterial.warehouse_id == warehouse.id,
            WorkOrderMaterial.user_id == subject.id,
            WorkOrderMaterial.material_id == material.id,
            WorkOrderMaterial.condition == payload.condition,
            WorkOrderMaterial.status == "occupied",
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="该工单物料已有未结清占用记录")

    balance = get_or_create_balance(
        db,
        warehouse_id=warehouse.id,
        holder_user_id=subject.id,
        material_id=material.id,
        condition=payload.condition,
        for_update=True,
    )
    available = balance.quantity_on_hand - balance.quantity_occupied
    if available < payload.quantity:
        raise HTTPException(status_code=409, detail=f"个人可用库存不足，当前{available}")
    balance.quantity_occupied += payload.quantity
    balance.version += 1
    row = WorkOrderMaterial(
        work_order_number=payload.work_order_number.strip(),
        warehouse_id=warehouse.id,
        user_id=subject.id,
        material_id=material.id,
        condition=payload.condition,
        quantity=payload.quantity,
        note=payload.note.strip(),
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit(
        db,
        actor=user,
        action="work_order_material.occupied",
        entity_type="work_order_material",
        entity_id=row.id,
        detail={
            "workOrderNumber": row.work_order_number,
            "materialCode": material.code,
            "quantity": row.quantity,
            "userId": subject.id,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return serialize_row(db.scalar(work_material_query().where(WorkOrderMaterial.id == row.id)))


@router.post("/batch")
def occupy_work_order_material_batch(
    payload: WorkOrderMaterialBatchCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role not in OPERATOR_ROLES:
        raise HTTPException(status_code=403, detail="没有此操作权限")
    subject_id = payload.user_id or user.id
    if user.role == "technician" and subject_id != user.id:
        raise HTTPException(status_code=403, detail="只能占用自己的物料")
    subject = db.get(User, subject_id)
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not subject or not subject.is_active:
        raise HTTPException(status_code=404, detail="领用人不存在")
    if not warehouse or not warehouse.is_active:
        raise HTTPException(status_code=404, detail="所属仓库不存在")
    if user.role != "admin" and user.province and warehouse.province != user.province:
        raise HTTPException(status_code=403, detail="不能处理其他省份库存")
    if subject.province and subject.province != warehouse.province:
        raise HTTPException(status_code=400, detail="领用人与所属仓库省份不一致")

    work_order_number = payload.work_order_number.strip()
    seen: set[tuple[str, str]] = set()
    prepared: list[tuple[object, Material, object]] = []
    for item in payload.items:
        key = (item.material_id, item.condition)
        if key in seen:
            raise HTTPException(status_code=400, detail="同一物料与状态不能重复")
        seen.add(key)
        if item.condition not in {"good", "old"}:
            raise HTTPException(status_code=400, detail="工单只能占用好件或旧件")
        material = db.get(Material, item.material_id)
        if not material or not material.is_active:
            raise HTTPException(status_code=404, detail="物料不存在或已停用")
        duplicate = db.scalar(
            select(WorkOrderMaterial).where(
                WorkOrderMaterial.work_order_number == work_order_number,
                WorkOrderMaterial.warehouse_id == warehouse.id,
                WorkOrderMaterial.user_id == subject.id,
                WorkOrderMaterial.material_id == material.id,
                WorkOrderMaterial.condition == item.condition,
                WorkOrderMaterial.status == "occupied",
            )
        )
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail=f"{material.code} 已有未结清占用记录",
            )
        balance = get_or_create_balance(
            db,
            warehouse_id=warehouse.id,
            holder_user_id=subject.id,
            material_id=material.id,
            condition=item.condition,
            for_update=True,
        )
        available = balance.quantity_on_hand - balance.quantity_occupied
        if available < item.quantity:
            raise HTTPException(
                status_code=409,
                detail=f"{material.code} 个人可用库存不足，当前{available}",
            )
        prepared.append((item, material, balance))

    rows: list[WorkOrderMaterial] = []
    for item, material, balance in prepared:
        balance.quantity_occupied += item.quantity
        balance.version += 1
        row = WorkOrderMaterial(
            work_order_number=work_order_number,
            warehouse_id=warehouse.id,
            user_id=subject.id,
            material_id=material.id,
            condition=item.condition,
            quantity=item.quantity,
            note=payload.note.strip(),
            created_by_id=user.id,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    audit(
        db,
        actor=user,
        action="work_order_material.batch_occupied",
        entity_type="work_order",
        entity_id=work_order_number,
        detail={"items": len(rows), "userId": subject.id},
        ip_address=client_ip(request),
    )
    row_ids = [row.id for row in rows]
    db.commit()
    saved = list(
        db.scalars(
            work_material_query()
            .where(WorkOrderMaterial.id.in_(row_ids))
            .order_by(WorkOrderMaterial.created_at)
        )
    )
    return [serialize_row(row) for row in saved]


def _locked_row(row_id: str, db: Session) -> WorkOrderMaterial:
    row = db.scalar(
        work_material_query()
        .where(WorkOrderMaterial.id == row_id)
        .with_for_update(of=WorkOrderMaterial)
    )
    if not row:
        raise HTTPException(status_code=404, detail="工单物料记录不存在")
    return row


def _settle_occupation(
    *,
    row: WorkOrderMaterial,
    action: str,
    request: Request,
    user: User,
    db: Session,
) -> dict:
    ensure_access(row, user)
    if row.status != "occupied":
        raise HTTPException(status_code=409, detail="只有占用中的物料可执行此操作")
    balance = get_or_create_balance(
        db,
        warehouse_id=row.warehouse_id,
        holder_user_id=row.user_id,
        material_id=row.material_id,
        condition=row.condition,
        for_update=True,
    )
    if balance.quantity_occupied < row.quantity:
        raise HTTPException(status_code=409, detail="占用库存数量异常")
    balance.quantity_occupied -= row.quantity
    if action == "consumed":
        if balance.quantity_on_hand < row.quantity:
            raise HTTPException(status_code=409, detail="个人现有库存数量异常")
        balance.quantity_on_hand -= row.quantity
    balance.version += 1
    row.status = action
    row.settled_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action=f"work_order_material.{action}",
        entity_type="work_order_material",
        entity_id=row.id,
        detail={"workOrderNumber": row.work_order_number, "quantity": row.quantity},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": row.status}


@router.post("/{row_id}/release")
def release_work_order_material(
    row_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _settle_occupation(
        row=_locked_row(row_id, db),
        action="released",
        request=request,
        user=user,
        db=db,
    )


@router.post("/{row_id}/consume")
def consume_work_order_material(
    row_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _settle_occupation(
        row=_locked_row(row_id, db),
        action="consumed",
        request=request,
        user=user,
        db=db,
    )


@router.post("/{row_id}/recover")
def recover_work_order_material(
    row_id: str,
    payload: WorkOrderMaterialRecoverIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _locked_row(row_id, db)
    ensure_access(row, user)
    if row.status != "consumed":
        raise HTTPException(status_code=409, detail="只有已消耗物料可登记回收")
    if payload.recovery_condition not in {"old", "bad"}:
        raise HTTPException(status_code=400, detail="回收件只能登记为旧件或坏件")
    balance = get_or_create_balance(
        db,
        warehouse_id=row.warehouse_id,
        holder_user_id=row.user_id,
        material_id=row.material_id,
        condition=payload.recovery_condition,
        for_update=True,
    )
    balance.quantity_on_hand += row.quantity
    balance.version += 1
    row.status = "recovered"
    row.recovery_condition = payload.recovery_condition
    if payload.note.strip():
        row.note = payload.note.strip()
    row.settled_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action="work_order_material.recovered",
        entity_type="work_order_material",
        entity_id=row.id,
        detail={
            "workOrderNumber": row.work_order_number,
            "quantity": row.quantity,
            "condition": row.recovery_condition,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": row.status}
