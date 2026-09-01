from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_user
from ..models import (
    InventoryBalance,
    StocktakeTask,
    Transfer,
    User,
    Warehouse,
    WorkOrderMaterial,
)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    province_filter = user.province if user.role != "admin" else None
    warehouse_ids = select(Warehouse.id).where(Warehouse.is_active.is_(True))
    if province_filter:
        warehouse_ids = warehouse_ids.where(Warehouse.province == province_filter)

    inventory_stmt = select(
        func.coalesce(func.sum(InventoryBalance.quantity_on_hand), 0),
        func.coalesce(func.sum(InventoryBalance.quantity_occupied), 0),
        func.coalesce(func.sum(InventoryBalance.quantity_in_transit), 0),
    ).where(InventoryBalance.warehouse_id.in_(warehouse_ids))
    transfer_scope = select(Transfer.id).where(
        Transfer.target_warehouse_id.in_(warehouse_ids)
    )
    stocktake_scope = select(StocktakeTask.id).where(
        StocktakeTask.warehouse_id.in_(warehouse_ids)
    )
    if user.role == "technician":
        inventory_stmt = inventory_stmt.where(InventoryBalance.holder_user_id == user.id)
        transfer_scope = transfer_scope.where(
            or_(
                Transfer.recipient_user_id == user.id,
                Transfer.requester_user_id == user.id,
                Transfer.source_holder_user_id == user.id,
                Transfer.created_by_id == user.id,
            )
        )
        stocktake_scope = stocktake_scope.where(StocktakeTask.assignee_id == user.id)

    inventory = db.execute(inventory_stmt).one()
    pending_transfers = db.scalar(
        select(func.count(Transfer.id)).where(
            Transfer.id.in_(transfer_scope),
            Transfer.target_warehouse_id.in_(warehouse_ids),
            Transfer.status.in_(["pending_approval", "draft", "dispatched"]),
        )
    )
    work_material_stmt = select(func.count(WorkOrderMaterial.id)).where(
        WorkOrderMaterial.warehouse_id.in_(warehouse_ids),
        WorkOrderMaterial.status == "occupied",
    )
    if user.role == "technician":
        work_material_stmt = work_material_stmt.where(WorkOrderMaterial.user_id == user.id)
    pending_stocktakes = db.scalar(
        select(func.count(StocktakeTask.id)).where(
            StocktakeTask.id.in_(stocktake_scope),
            StocktakeTask.status.in_(["pending", "in_progress", "submitted"]),
        )
    )
    recent_since = datetime.now(timezone.utc) - timedelta(days=7)
    recent_stmt = select(Transfer).where(
        Transfer.target_warehouse_id.in_(warehouse_ids),
        Transfer.created_at >= recent_since,
    )
    if user.role == "technician":
        recent_stmt = recent_stmt.where(
            or_(
                Transfer.recipient_user_id == user.id,
                Transfer.requester_user_id == user.id,
                Transfer.source_holder_user_id == user.id,
                Transfer.created_by_id == user.id,
            )
        )
    recent_transfers = list(
        db.scalars(recent_stmt.order_by(Transfer.created_at.desc()).limit(8))
    )
    return {
        "inventory": {
            "onHand": inventory[0],
            "occupied": inventory[1],
            "inTransit": inventory[2],
            "available": max(0, inventory[0] - inventory[1]),
        },
        "pendingTransfers": pending_transfers or 0,
        "activeWorkOrderMaterials": db.scalar(work_material_stmt) or 0,
        "pendingStocktakes": pending_stocktakes or 0,
        "recentTransfers": [
            {
                "id": row.id,
                "number": row.number,
                "status": row.status,
                "targetWarehouse": row.target_warehouse.name,
                "createdAt": row.created_at,
                "itemCount": len(row.items),
            }
            for row in recent_transfers
        ],
    }
