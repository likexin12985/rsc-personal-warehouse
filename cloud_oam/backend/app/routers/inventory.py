from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..dependencies import client_ip, get_current_user, require_roles
from ..models import InventoryBalance, Material, User, Warehouse
from ..schemas import InventoryAdjustIn
from ..services import audit, get_or_create_balance


router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
def list_inventory(
    q: str = Query(default="", max_length=100),
    province: str = "",
    warehouse_id: str = "",
    condition: str = "",
    mine: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(InventoryBalance)
        .options(
            joinedload(InventoryBalance.warehouse),
            joinedload(InventoryBalance.material),
            joinedload(InventoryBalance.holder),
        )
        .join(InventoryBalance.warehouse)
        .join(InventoryBalance.material)
    )
    effective_province = province.strip()
    if user.role != "admin" and user.province:
        effective_province = user.province
    if effective_province:
        stmt = stmt.where(Warehouse.province == effective_province)
    if warehouse_id:
        stmt = stmt.where(InventoryBalance.warehouse_id == warehouse_id)
    if condition:
        stmt = stmt.where(InventoryBalance.condition == condition)
    # Engineers are always limited to their own custody.  ``mine=false`` is a
    # presentation hint, never an authorization override.
    if user.role == "technician" or mine:
        stmt = stmt.where(InventoryBalance.holder_user_id == user.id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Material.code.ilike(like), Material.name.ilike(like)))
    rows = list(
        db.scalars(
            stmt.order_by(Warehouse.province, Warehouse.name, Material.code).limit(2000)
        ).unique()
    )
    return [
        {
            "id": row.id,
            "warehouse": {
                "id": row.warehouse.id,
                "name": row.warehouse.name,
                "province": row.warehouse.province,
            },
            "holder": {"id": row.holder.id, "name": row.holder.name} if row.holder else None,
            "material": {
                "id": row.material.id,
                "code": row.material.code,
                "name": row.material.name,
                "unit": row.material.unit,
            },
            "condition": row.condition,
            "onHand": row.quantity_on_hand,
            "occupied": row.quantity_occupied,
            "inTransit": row.quantity_in_transit,
            "available": max(0, row.quantity_on_hand - row.quantity_occupied),
            "updatedAt": row.updated_at,
        }
        for row in rows
    ]


@router.post("/adjust")
def adjust_inventory(
    payload: InventoryAdjustIn,
    request: Request,
    user: User = Depends(require_roles("admin", "provincial_manager")),
    db: Session = Depends(get_db),
):
    if payload.delta == 0:
        raise HTTPException(status_code=400, detail="调整数量不能为0")
    warehouse = db.get(Warehouse, payload.warehouse_id)
    if not warehouse:
        raise HTTPException(status_code=404, detail="仓库不存在")
    if user.role != "admin" and user.province and warehouse.province != user.province:
        raise HTTPException(status_code=403, detail="不能调整其他省份库存")
    balance = get_or_create_balance(
        db,
        warehouse_id=payload.warehouse_id,
        material_id=payload.material_id,
        condition=payload.condition,
        for_update=True,
    )
    after = balance.quantity_on_hand + payload.delta
    if after < 0:
        raise HTTPException(status_code=409, detail="调整后库存不能小于0")
    before = balance.quantity_on_hand
    balance.quantity_on_hand = after
    balance.version += 1
    audit(
        db,
        actor=user,
        action="inventory.adjust",
        entity_type="inventory_balance",
        entity_id=balance.id,
        detail={"before": before, "after": after, "reason": payload.reason},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "before": before, "after": after}
