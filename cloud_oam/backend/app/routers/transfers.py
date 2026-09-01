from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..dependencies import client_ip, get_current_user, require_roles
from ..models import Material, Transfer, TransferItem, User, Warehouse
from ..schemas import (
    DispatchIn,
    TransferApproveIn,
    TransferCreateIn,
    TransferRejectIn,
)
from ..services import audit, get_or_create_balance, make_number


router = APIRouter(prefix="/transfers", tags=["transfers"])
MANAGER_ROLES = ("admin", "provincial_manager")
PERSONAL_REQUEST_TYPE = "personal_request"
REQUEST_TYPES = {PERSONAL_REQUEST_TYPE, "regional_request", "provider_request"}
RETURN_TYPES = {"bad_return", "stagnant_return"}
TRANSFER_TYPES = {
    "internal",
    "standard_transfer",
    "external_inbound",
    "oam_inbound",
    *REQUEST_TYPES,
    *RETURN_TYPES,
}
SOURCE_REQUIRED_TYPES = {"internal", "standard_transfer", *RETURN_TYPES}
CONDITIONS = {"good", "old", "bad"}


def ensure_transfer_access(row: Transfer, user: User) -> None:
    if user.role == "technician" and user.id not in {
        row.recipient_user_id,
        row.requester_user_id,
        row.source_holder_user_id,
        row.created_by_id,
    }:
        raise HTTPException(status_code=403, detail="不能查看或处理他人的物料单")
    if (
        user.role not in {"admin", "technician"}
        and user.province
        and row.target_warehouse.province != user.province
    ):
        raise HTTPException(status_code=403, detail="不能查看或处理其他省份物料单")


def _condition_allowed(warehouse: Warehouse, condition: str) -> bool:
    return warehouse.condition_scope == "mixed" or warehouse.condition_scope == condition


def _validate_path(
    *,
    transfer_type: str,
    source: Warehouse | None,
    target: Warehouse,
    source_holder: User | None,
    recipient: User | None,
    requester: User | None,
    items: list[TransferItem],
) -> None:
    if source and source.ownership_type != target.ownership_type:
        raise HTTPException(status_code=400, detail="调出与调入仓库的物权属性必须一致")
    source_owner = source_holder.id if source_holder else "WAREHOUSE"
    target_owner = recipient.id if recipient else "WAREHOUSE"
    if source and source.id == target.id and source_owner == target_owner:
        raise HTTPException(status_code=400, detail="来源与目标库存不能相同")
    if transfer_type == "provider_request" and target.position_scope not in {
        "service_provider",
        "unrestricted",
    }:
        raise HTTPException(status_code=400, detail="服务商申请必须调入服务商仓位")
    if transfer_type in {PERSONAL_REQUEST_TYPE, "regional_request"} and target.position_scope not in {
        "employee",
        "unrestricted",
    }:
        raise HTTPException(status_code=400, detail="个人或区域申请必须调入员工仓位或通用仓位")
    if transfer_type == PERSONAL_REQUEST_TYPE:
        if not requester or not recipient:
            raise HTTPException(status_code=400, detail="个人需求必须绑定申请人和接收人")
        if requester.id != recipient.id:
            raise HTTPException(status_code=400, detail="个人需求的申请人和接收人必须一致")
    for item in items:
        if item.condition not in CONDITIONS:
            raise HTTPException(status_code=400, detail="物料状态不正确")
        if transfer_type == "bad_return" and item.condition != "bad":
            raise HTTPException(status_code=400, detail="坏件退回只能选择坏件")
        if transfer_type == PERSONAL_REQUEST_TYPE and item.condition == "bad":
            raise HTTPException(status_code=400, detail="个人需求不能申请坏件")
        if source and not source_holder and not _condition_allowed(source, item.condition):
            raise HTTPException(
                status_code=400,
                detail=f"{item.material.code} 状态与来源仓库库存范围不一致",
            )
        if not _condition_allowed(target, item.condition):
            raise HTTPException(
                status_code=400,
                detail=f"{item.material.code} 状态与目标仓库库存范围不一致",
            )


def serialize_transfer(row: Transfer) -> dict:
    return {
        "id": row.id,
        "number": row.number,
        "transferType": row.transfer_type,
        "status": row.status,
        "sourceWarehouse": (
            {"id": row.source_warehouse.id, "name": row.source_warehouse.name}
            if row.source_warehouse
            else None
        ),
        "sourceHolder": (
            {"id": row.source_holder.id, "name": row.source_holder.name}
            if row.source_holder
            else None
        ),
        "targetWarehouse": {
            "id": row.target_warehouse.id,
            "name": row.target_warehouse.name,
            "province": row.target_warehouse.province,
        },
        "recipient": (
            {"id": row.recipient.id, "name": row.recipient.name} if row.recipient else None
        ),
        "requester": (
            {"id": row.requester.id, "name": row.requester.name} if row.requester else None
        ),
        "approvedBy": row.approved_by.name if row.approved_by else None,
        "workOrderNumber": row.work_order_number,
        "logisticsCompany": row.logistics_company,
        "trackingNumber": row.tracking_number,
        "externalReference": row.external_reference,
        "note": row.note,
        "createdBy": row.created_by.name,
        "createdById": row.created_by_id,
        "createdAt": row.created_at,
        "approvedAt": row.approved_at,
        "dispatchedAt": row.dispatched_at,
        "receivedAt": row.received_at,
        "items": [
            {
                "id": item.id,
                "materialId": item.material_id,
                "code": item.material.code,
                "name": item.material.name,
                "quantity": item.quantity,
                "condition": item.condition,
                "remark": item.remark,
                "unit": item.material.unit,
            }
            for item in row.items
        ],
    }


def transfer_query():
    return select(Transfer).options(
        joinedload(Transfer.source_warehouse),
        joinedload(Transfer.source_holder),
        joinedload(Transfer.target_warehouse),
        joinedload(Transfer.recipient),
        joinedload(Transfer.requester),
        joinedload(Transfer.approved_by),
        joinedload(Transfer.created_by),
        selectinload(Transfer.items).joinedload(TransferItem.material),
    )


def locked_transfer_query(transfer_id: str):
    return transfer_query().where(Transfer.id == transfer_id).with_for_update(of=Transfer)


@router.get("")
def list_transfers(
    status: str = "",
    transfer_type: str = "",
    province: str = "",
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = transfer_query().join(Transfer.target_warehouse)
    effective_province = province.strip()
    if user.role != "admin" and user.province:
        effective_province = user.province
    if effective_province:
        stmt = stmt.where(Warehouse.province == effective_province)
    if user.role == "technician":
        stmt = stmt.where(
            or_(
                Transfer.recipient_user_id == user.id,
                Transfer.requester_user_id == user.id,
                Transfer.source_holder_user_id == user.id,
                Transfer.created_by_id == user.id,
            )
        )
    if status:
        stmt = stmt.where(Transfer.status == status)
    if transfer_type:
        stmt = stmt.where(Transfer.transfer_type == transfer_type)
    rows = list(db.scalars(stmt.order_by(Transfer.created_at.desc()).limit(limit)).unique())
    return [serialize_transfer(row) for row in rows]


@router.get("/{transfer_id}")
def transfer_detail(
    transfer_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.scalar(transfer_query().where(Transfer.id == transfer_id))
    if not row:
        raise HTTPException(status_code=404, detail="物料单不存在")
    ensure_transfer_access(row, user)
    return serialize_transfer(row)


@router.post("")
def create_transfer(
    payload: TransferCreateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.transfer_type not in TRANSFER_TYPES:
        raise HTTPException(status_code=400, detail="业务类型不正确")
    if user.role not in {*MANAGER_ROLES, "technician"}:
        raise HTTPException(status_code=403, detail="没有此操作权限")
    if user.role == "technician" and payload.transfer_type not in {
        *REQUEST_TYPES,
        *RETURN_TYPES,
    }:
        raise HTTPException(status_code=403, detail="现场人员只能发起申请或退回")

    target = db.get(Warehouse, payload.target_warehouse_id)
    if not target or not target.is_active:
        raise HTTPException(status_code=404, detail="目标仓库不存在")
    if user.role != "admin" and user.province and target.province != user.province:
        raise HTTPException(status_code=403, detail="不能创建其他省份物料单")

    source = db.get(Warehouse, payload.source_warehouse_id) if payload.source_warehouse_id else None
    if payload.source_warehouse_id and (not source or not source.is_active):
        raise HTTPException(status_code=404, detail="来源仓库不存在")
    if payload.transfer_type in SOURCE_REQUIRED_TYPES and not source:
        raise HTTPException(status_code=400, detail="当前业务类型必须选择来源仓库")
    if payload.transfer_type in REQUEST_TYPES and source:
        raise HTTPException(status_code=400, detail="申请单由审批人确定调出仓库")

    requester_id = payload.requester_user_id
    if payload.transfer_type in REQUEST_TYPES and not requester_id:
        requester_id = user.id
    requester = db.get(User, requester_id) if requester_id else None
    if requester_id and not requester:
        raise HTTPException(status_code=404, detail="申请人不存在")
    if user.role == "technician" and requester_id not in {None, user.id}:
        raise HTTPException(status_code=403, detail="只能为自己发起申请")

    recipient_id = payload.recipient_user_id
    if payload.transfer_type == PERSONAL_REQUEST_TYPE:
        if recipient_id and recipient_id != requester_id:
            raise HTTPException(status_code=400, detail="个人需求的申请人和接收人必须一致")
        recipient_id = requester_id
    recipient = db.get(User, recipient_id) if recipient_id else None
    if recipient_id and not recipient:
        raise HTTPException(status_code=404, detail="接收人不存在")
    if recipient and recipient.province and recipient.province != target.province:
        raise HTTPException(status_code=400, detail="接收人与目标仓库省份不一致")

    source_holder_id = payload.source_holder_user_id
    if user.role == "technician" and payload.transfer_type in RETURN_TYPES:
        source_holder_id = user.id
    source_holder = db.get(User, source_holder_id) if source_holder_id else None
    if source_holder_id and not source_holder:
        raise HTTPException(status_code=404, detail="调出持有人不存在")
    if payload.transfer_type in RETURN_TYPES and not source_holder:
        raise HTTPException(status_code=400, detail="退回单必须指定调出持有人")
    if user.role == "technician" and source_holder_id not in {None, user.id}:
        raise HTTPException(status_code=403, detail="只能退回自己的物料")

    item_models: list[TransferItem] = []
    for item in payload.items:
        material = db.get(Material, item.material_id)
        if not material or not material.is_active:
            raise HTTPException(status_code=404, detail="物料不存在或已停用")
        item_row = TransferItem(**item.model_dump())
        item_row.material = material
        item_models.append(item_row)

    _validate_path(
        transfer_type=payload.transfer_type,
        source=source,
        target=target,
        source_holder=source_holder,
        recipient=recipient,
        requester=requester,
        items=item_models,
    )
    transfer = Transfer(
        number=make_number("TR"),
        transfer_type=payload.transfer_type,
        status="pending_approval" if payload.transfer_type in REQUEST_TYPES else "draft",
        source_warehouse_id=payload.source_warehouse_id,
        source_holder_user_id=source_holder_id,
        target_warehouse_id=payload.target_warehouse_id,
        recipient_user_id=recipient_id,
        requester_user_id=requester_id,
        work_order_number=payload.work_order_number.strip(),
        external_reference=payload.external_reference.strip(),
        note=payload.note.strip(),
        created_by_id=user.id,
    )
    transfer.items = item_models
    db.add(transfer)
    db.flush()
    audit(
        db,
        actor=user,
        action="transfer.created",
        entity_type="transfer",
        entity_id=transfer.id,
        detail={"number": transfer.number, "type": transfer.transfer_type, "status": transfer.status, "items": len(transfer.items)},
        ip_address=client_ip(request),
    )
    db.commit()
    row = db.scalar(transfer_query().where(Transfer.id == transfer.id))
    return serialize_transfer(row)


@router.post("/{transfer_id}/approve")
def approve_transfer(
    transfer_id: str,
    payload: TransferApproveIn,
    request: Request,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
):
    transfer = db.scalar(locked_transfer_query(transfer_id))
    if not transfer:
        raise HTTPException(status_code=404, detail="物料单不存在")
    ensure_transfer_access(transfer, user)
    if transfer.status != "pending_approval":
        raise HTTPException(status_code=409, detail="只有待审批申请可审批")
    source = db.get(Warehouse, payload.source_warehouse_id)
    if not source or not source.is_active:
        raise HTTPException(status_code=404, detail="调出仓库不存在")
    _validate_path(
        transfer_type=transfer.transfer_type,
        source=source,
        target=transfer.target_warehouse,
        source_holder=transfer.source_holder,
        recipient=transfer.recipient,
        requester=transfer.requester,
        items=transfer.items,
    )
    transfer.source_warehouse_id = source.id
    transfer.status = "draft"
    transfer.approved_by_id = user.id
    transfer.approved_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action="transfer.approved",
        entity_type="transfer",
        entity_id=transfer.id,
        detail={"number": transfer.number, "sourceWarehouseId": source.id},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": transfer.status}


@router.post("/{transfer_id}/reject")
def reject_transfer(
    transfer_id: str,
    payload: TransferRejectIn,
    request: Request,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
):
    transfer = db.scalar(locked_transfer_query(transfer_id))
    if not transfer:
        raise HTTPException(status_code=404, detail="物料单不存在")
    ensure_transfer_access(transfer, user)
    if transfer.status != "pending_approval":
        raise HTTPException(status_code=409, detail="只有待审批申请可驳回")
    transfer.status = "rejected"
    audit(
        db,
        actor=user,
        action="transfer.rejected",
        entity_type="transfer",
        entity_id=transfer.id,
        detail={"number": transfer.number, "reason": payload.reason.strip()},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": transfer.status}


@router.post("/{transfer_id}/dispatch")
def dispatch_transfer(
    transfer_id: str,
    payload: DispatchIn,
    request: Request,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
):
    transfer = db.scalar(locked_transfer_query(transfer_id))
    if not transfer:
        raise HTTPException(status_code=404, detail="物料单不存在")
    ensure_transfer_access(transfer, user)
    if transfer.status != "draft":
        raise HTTPException(status_code=409, detail="只有已通过或草稿单可派发")
    for item in transfer.items:
        if transfer.source_warehouse_id:
            source = get_or_create_balance(
                db,
                warehouse_id=transfer.source_warehouse_id,
                holder_user_id=transfer.source_holder_user_id,
                material_id=item.material_id,
                condition=item.condition,
                for_update=True,
            )
            available = source.quantity_on_hand - source.quantity_occupied
            if available < item.quantity:
                raise HTTPException(status_code=409, detail=f"{item.material.code} 可用库存不足，当前{available}")
            source.quantity_on_hand -= item.quantity
            source.version += 1
        target = get_or_create_balance(
            db,
            warehouse_id=transfer.target_warehouse_id,
            holder_user_id=transfer.recipient_user_id,
            material_id=item.material_id,
            condition=item.condition,
            for_update=True,
        )
        target.quantity_in_transit += item.quantity
        target.version += 1
    transfer.status = "dispatched"
    transfer.logistics_company = payload.logistics_company.strip()
    transfer.tracking_number = payload.tracking_number.strip()
    transfer.dispatched_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action="transfer.dispatched",
        entity_type="transfer",
        entity_id=transfer.id,
        detail={"trackingNumber": transfer.tracking_number},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": transfer.status}


@router.post("/{transfer_id}/receive")
def receive_transfer(
    transfer_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    transfer = db.scalar(locked_transfer_query(transfer_id))
    if not transfer:
        raise HTTPException(status_code=404, detail="物料单不存在")
    ensure_transfer_access(transfer, user)
    if transfer.status != "dispatched":
        raise HTTPException(status_code=409, detail="只有已派发单可入库")
    if user.role == "technician" and transfer.recipient_user_id != user.id:
        raise HTTPException(status_code=403, detail="只能签收派发给自己的物料")
    for item in transfer.items:
        target = get_or_create_balance(
            db,
            warehouse_id=transfer.target_warehouse_id,
            holder_user_id=transfer.recipient_user_id,
            material_id=item.material_id,
            condition=item.condition,
            for_update=True,
        )
        if target.quantity_in_transit < item.quantity:
            raise HTTPException(status_code=409, detail=f"{item.material.code} 在途数量异常")
        target.quantity_in_transit -= item.quantity
        target.quantity_on_hand += item.quantity
        target.version += 1
    transfer.status = "received"
    transfer.received_at = datetime.now(timezone.utc)
    audit(
        db,
        actor=user,
        action="transfer.received",
        entity_type="transfer",
        entity_id=transfer.id,
        detail={"number": transfer.number},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": transfer.status}


@router.post("/{transfer_id}/cancel")
def cancel_transfer(
    transfer_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    transfer = db.scalar(locked_transfer_query(transfer_id))
    if not transfer:
        raise HTTPException(status_code=404, detail="物料单不存在")
    ensure_transfer_access(transfer, user)
    if user.role not in MANAGER_ROLES and transfer.created_by_id != user.id:
        raise HTTPException(status_code=403, detail="只能撤销自己创建的申请")
    if transfer.status not in {"pending_approval", "draft", "dispatched"}:
        raise HTTPException(status_code=409, detail="当前状态不可撤销")
    if transfer.status == "dispatched":
        for item in transfer.items:
            target = get_or_create_balance(
                db,
                warehouse_id=transfer.target_warehouse_id,
                holder_user_id=transfer.recipient_user_id,
                material_id=item.material_id,
                condition=item.condition,
                for_update=True,
            )
            if target.quantity_in_transit < item.quantity:
                raise HTTPException(status_code=409, detail="在途库存已发生变化，禁止撤销")
            target.quantity_in_transit -= item.quantity
            if transfer.source_warehouse_id:
                source = get_or_create_balance(
                    db,
                    warehouse_id=transfer.source_warehouse_id,
                    holder_user_id=transfer.source_holder_user_id,
                    material_id=item.material_id,
                    condition=item.condition,
                    for_update=True,
                )
                source.quantity_on_hand += item.quantity
                source.version += 1
    transfer.status = "cancelled"
    audit(
        db,
        actor=user,
        action="transfer.cancelled",
        entity_type="transfer",
        entity_id=transfer.id,
        detail={"number": transfer.number},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True, "status": transfer.status}
