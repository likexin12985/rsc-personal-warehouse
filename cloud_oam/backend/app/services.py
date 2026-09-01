import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, InventoryBalance, Material, User


def make_number(prefix: str) -> str:
    return f"{prefix}{datetime.now(timezone.utc).astimezone().strftime('%Y%m%d%H%M%S%f')[:17]}"


def audit(
    db: Session,
    *,
    actor: User | None,
    action: str,
    entity_type: str,
    entity_id: str,
    detail: dict | str,
    ip_address: str = "",
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False),
            ip_address=ip_address,
        )
    )


def get_or_create_balance(
    db: Session,
    *,
    warehouse_id: str,
    material_id: str,
    condition: str,
    holder_user_id: str | None = None,
    for_update: bool = False,
) -> InventoryBalance:
    holder_key = holder_user_id or "WAREHOUSE"
    query = select(InventoryBalance).where(
        InventoryBalance.warehouse_id == warehouse_id,
        InventoryBalance.material_id == material_id,
        InventoryBalance.condition == condition,
        InventoryBalance.holder_key == holder_key,
    )
    if for_update:
        query = query.with_for_update()
    balance = db.scalar(query)
    if balance:
        return balance
    if not db.get(Material, material_id):
        raise HTTPException(status_code=404, detail="物料不存在")
    balance = InventoryBalance(
        warehouse_id=warehouse_id,
        material_id=material_id,
        condition=condition,
        holder_user_id=holder_user_id,
        holder_key=holder_key,
    )
    db.add(balance)
    db.flush()
    return balance
