from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..dependencies import client_ip, get_current_user, require_roles
from ..models import (
    InventoryBalance,
    MediaAttachment,
    StocktakeItem,
    StocktakeTask,
    User,
    Warehouse,
)
from ..schemas import StocktakeCountIn, StocktakeCreateIn
from ..services import audit, get_or_create_balance, make_number


router = APIRouter(prefix="/stocktakes", tags=["stocktakes"])
MANAGER_ROLES = ("admin", "provincial_manager")


def ensure_task_access(row: StocktakeTask, user: User) -> None:
    if user.role == "technician" and row.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="不能查看或处理他人的盘点任务")
    if (
        user.role not in {"admin", "technician"}
        and user.province
        and row.warehouse.province != user.province
    ):
        raise HTTPException(status_code=403, detail="不能查看或处理其他省份盘点任务")


def task_query():
    return select(StocktakeTask).options(
        joinedload(StocktakeTask.warehouse),
        joinedload(StocktakeTask.assignee),
        joinedload(StocktakeTask.created_by),
        selectinload(StocktakeTask.items).joinedload(StocktakeItem.material),
    )


def serialize_task(row: StocktakeTask, attachment_count: int = 0) -> dict:
    counted = sum(1 for item in row.items if item.counted_quantity is not None)
    difference = sum(
        (item.counted_quantity or 0) - item.expected_quantity
        for item in row.items
        if item.counted_quantity is not None
    )
    return {
        "id": row.id,
        "number": row.number,
        "status": row.status,
        "warehouse": {
            "id": row.warehouse.id,
            "name": row.warehouse.name,
            "province": row.warehouse.province,
        },
        "assignee": {"id": row.assignee.id, "name": row.assignee.name},
        "deadline": row.deadline,
        "note": row.note,
        "createdAt": row.created_at,
        "submittedAt": row.submitted_at,
        "closedAt": row.closed_at,
        "progress": {"counted": counted, "total": len(row.items)},
        "difference": difference,
        "attachmentCount": attachment_count,
        "items": [
            {
                "id": item.id,
                "materialId": item.material_id,
                "code": item.material.code,
                "name": item.material.name,
                "unit": item.material.unit,
                "condition": item.condition,
                "expected": item.expected_quantity,
                "counted": item.counted_quantity,
                "difference": (
                    None
                    if item.counted_quantity is None
                    else item.counted_quantity - item.expected_quantity
                ),
                "remark": item.remark,
            }
            for item in row.items
        ],
    }


@router.get("")
def list_tasks(
    status: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = task_query()
    if user.role == "technician":
        stmt = stmt.where(StocktakeTask.assignee_id == user.id)
    elif user.role != "admin" and user.province:
        stmt = stmt.join(StocktakeTask.warehouse).where(Warehouse.province == user.province)
    if status:
        stmt = stmt.where(StocktakeTask.status == status)
    rows = list(db.scalars(stmt.order_by(StocktakeTask.created_at.desc()).limit(300)).unique())
    return [serialize_task(row) for row in rows]


@router.get("/{task_id}")
def task_detail(
    task_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(task_query().where(StocktakeTask.id == task_id))
    if not row:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    ensure_task_access(row, user)
    attachment_count = len(
        list(
            db.scalars(
                select(MediaAttachment).where(
                    MediaAttachment.entity_type == "stocktake",
                    MediaAttachment.entity_id == row.id,
                )
            )
        )
    )
    return serialize_task(row, attachment_count)


@router.post("")
def create_task(
    payload: StocktakeCreateIn,
    request: Request,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
):
    warehouse = db.get(Warehouse, payload.warehouse_id)
    assignee = db.get(User, payload.assignee_id)
    if not warehouse or not assignee:
        raise HTTPException(status_code=404, detail="仓库或盘点人不存在")
    if assignee.province and assignee.province != warehouse.province:
        raise HTTPException(status_code=400, detail="盘点人与仓库省份不一致")
    if user.role != "admin" and user.province and warehouse.province != user.province:
        raise HTTPException(status_code=403, detail="不能创建其他省份盘点任务")
    balances = list(
        db.scalars(
            select(InventoryBalance)
            .where(
                InventoryBalance.warehouse_id == warehouse.id,
                InventoryBalance.holder_user_id.is_(None),
                InventoryBalance.quantity_on_hand > 0,
            )
            .order_by(InventoryBalance.material_id)
        )
    )
    if not balances:
        raise HTTPException(status_code=409, detail="该仓库暂无可盘点库存")
    task = StocktakeTask(
        number=make_number("ST"),
        warehouse_id=warehouse.id,
        assignee_id=assignee.id,
        deadline=payload.deadline,
        note=payload.note,
        created_by_id=user.id,
    )
    task.items = [
        StocktakeItem(
            material_id=row.material_id,
            condition=row.condition,
            expected_quantity=row.quantity_on_hand,
        )
        for row in balances
    ]
    db.add(task)
    db.flush()
    audit(
        db,
        actor=user,
        action="stocktake.created",
        entity_type="stocktake",
        entity_id=task.id,
        detail={"number": task.number, "items": len(task.items)},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": task.id, "number": task.number}


@router.put("/{task_id}/items/{item_id}")
def count_item(
    task_id: str,
    item_id: str,
    payload: StocktakeCountIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.scalar(task_query().where(StocktakeTask.id == task_id))
    if not task:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    if task.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=409, detail="当前任务不可录入")
    ensure_task_access(task, user)
    item = next((row for row in task.items if row.id == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="盘点明细不存在")
    item.counted_quantity = payload.counted_quantity
    item.remark = payload.remark.strip()
    task.status = "in_progress"
    audit(
        db,
        actor=user,
        action="stocktake.item_counted",
        entity_type="stocktake_item",
        entity_id=item.id,
        detail={"expected": item.expected_quantity, "counted": item.counted_quantity},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True}


@router.post("/{task_id}/submit")
def submit_task(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.scalar(task_query().where(StocktakeTask.id == task_id))
    if not task:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    if task.status not in {"pending", "in_progress"}:
        raise HTTPException(status_code=409, detail="当前任务不可提交")
    ensure_task_access(task, user)
    missing = [item.material.code for item in task.items if item.counted_quantity is None]
    if missing:
        raise HTTPException(status_code=409, detail=f"仍有{len(missing)}项未盘点")
    has_difference = any(
        item.counted_quantity != item.expected_quantity for item in task.items
    )
    if has_difference:
        attachment = db.scalar(
            select(MediaAttachment).where(
                MediaAttachment.entity_type == "stocktake",
                MediaAttachment.entity_id == task.id,
            )
        )
        if not attachment:
            raise HTTPException(status_code=409, detail="有差异时必须上传照片或视频凭证")
        empty_remarks = [
            item.material.code
            for item in task.items
            if item.counted_quantity != item.expected_quantity and not item.remark.strip()
        ]
        if empty_remarks:
            raise HTTPException(status_code=409, detail="差异项必须填写原因")
    task.status = "submitted"
    task.submitted_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action="stocktake.submitted",
        entity_type="stocktake",
        entity_id=task.id,
        detail={"number": task.number, "hasDifference": has_difference},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": task.status}


@router.post("/{task_id}/close")
def close_task(
    task_id: str,
    request: Request,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
):
    task = db.scalar(
        select(StocktakeTask)
        .options(selectinload(StocktakeTask.items).joinedload(StocktakeItem.material))
        .where(StocktakeTask.id == task_id)
        .with_for_update(of=StocktakeTask)
    )
    if not task:
        raise HTTPException(status_code=404, detail="盘点任务不存在")
    ensure_task_access(task, user)
    if task.status != "submitted":
        raise HTTPException(status_code=409, detail="只有已提交任务可关闭")
    for item in task.items:
        balance = get_or_create_balance(
            db,
            warehouse_id=task.warehouse_id,
            material_id=item.material_id,
            condition=item.condition,
            for_update=True,
        )
        balance.quantity_on_hand = int(item.counted_quantity or 0)
        balance.version += 1
    task.status = "closed"
    task.closed_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action="stocktake.closed",
        entity_type="stocktake",
        entity_id=task.id,
        detail={"number": task.number},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": task.status}
