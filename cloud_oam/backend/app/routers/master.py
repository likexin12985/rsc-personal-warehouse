from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import client_ip, get_current_user, require_roles
from ..models import Material, User, Warehouse
from ..schemas import MaterialCreateIn, MaterialOut, WarehouseCreateIn, WarehouseOut
from ..services import audit


router = APIRouter(tags=["master"])
WAREHOUSE_LEVELS = {"headquarters", "network"}
OWNERSHIP_TYPES = {"regular", "customer_supplied"}
POSITION_SCOPES = {"unrestricted", "employee", "service_provider"}
CONDITION_SCOPES = {"good", "old", "bad", "mixed"}


@router.get("/materials", response_model=list[MaterialOut])
def list_materials(
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Material).where(Material.is_active.is_(True))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Material.code.ilike(like),
                Material.name.ilike(like),
                Material.aliases.ilike(like),
            )
        )
    return list(db.scalars(stmt.order_by(Material.code).limit(limit)))


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(
    province: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Warehouse).where(Warehouse.is_active.is_(True))
    effective_province = province.strip()
    if user.role != "admin" and user.province:
        effective_province = user.province
    if effective_province:
        stmt = stmt.where(
            or_(
                Warehouse.province == effective_province,
                Warehouse.warehouse_level == "headquarters",
            )
        )
    return list(db.scalars(stmt.order_by(Warehouse.province, Warehouse.name)))


@router.post("/materials", response_model=MaterialOut)
def create_material(
    payload: MaterialCreateIn,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    code = payload.code.strip().upper()
    if db.scalar(select(Material).where(Material.code == code)):
        raise HTTPException(status_code=409, detail="物料编码已存在")
    material = Material(
        code=code,
        name=payload.name.strip(),
        specification=payload.specification.strip(),
        category=payload.category.strip() or "其他",
        aliases=payload.aliases.strip(),
        unit=payload.unit.strip(),
    )
    db.add(material)
    db.flush()
    audit(
        db,
        actor=actor,
        action="material.created",
        entity_type="material",
        entity_id=material.id,
        detail={"code": material.code, "name": material.name},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(material)
    return material


@router.post("/warehouses", response_model=WarehouseOut)
def create_warehouse(
    payload: WarehouseCreateIn,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    code = payload.code.strip().upper()
    if db.scalar(select(Warehouse).where(Warehouse.code == code)):
        raise HTTPException(status_code=409, detail="仓库编码已存在")
    if payload.warehouse_level not in WAREHOUSE_LEVELS:
        raise HTTPException(status_code=400, detail="仓库层级不正确")
    if payload.ownership_type not in OWNERSHIP_TYPES:
        raise HTTPException(status_code=400, detail="仓库物权属性不正确")
    if payload.position_scope not in POSITION_SCOPES:
        raise HTTPException(status_code=400, detail="仓位属性不正确")
    if payload.condition_scope not in CONDITION_SCOPES:
        raise HTTPException(status_code=400, detail="库存范围不正确")
    parent = (
        db.get(Warehouse, payload.parent_warehouse_id)
        if payload.parent_warehouse_id
        else None
    )
    if payload.parent_warehouse_id and not parent:
        raise HTTPException(status_code=404, detail="上级仓库不存在")
    if payload.warehouse_level == "headquarters" and parent:
        raise HTTPException(status_code=400, detail="总部仓库不能设置上级仓库")
    if parent and parent.warehouse_level != "headquarters":
        raise HTTPException(status_code=400, detail="网点仓库的上级必须是总部仓库")
    warehouse = Warehouse(
        code=code,
        name=payload.name.strip(),
        province=payload.province.strip(),
        city=payload.city.strip(),
        warehouse_type=payload.warehouse_type,
        condition_scope=payload.condition_scope,
        warehouse_level=payload.warehouse_level,
        ownership_type=payload.ownership_type,
        position_scope=payload.position_scope,
        parent_warehouse_id=payload.parent_warehouse_id,
    )
    db.add(warehouse)
    db.flush()
    audit(
        db,
        actor=actor,
        action="warehouse.created",
        entity_type="warehouse",
        entity_id=warehouse.id,
        detail={
            "code": warehouse.code,
            "name": warehouse.name,
            "province": warehouse.province,
            "city": warehouse.city,
            "warehouseLevel": warehouse.warehouse_level,
            "ownershipType": warehouse.ownership_type,
            "positionScope": warehouse.position_scope,
            "parentWarehouseId": warehouse.parent_warehouse_id,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(warehouse)
    return warehouse
