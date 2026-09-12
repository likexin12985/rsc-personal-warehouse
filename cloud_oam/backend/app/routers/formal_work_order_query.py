"""Formal own-work-order query for PC and mini-program material workflows."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services.inventory_query import InventoryReadError
from ..formal_services.work_order_material_options import material_options
from ..formal_services.work_order_query import list_my_work_orders
from ..work_order_query_schemas import MyWorkOrdersOut, WorkOrderStatus, WorkOrderMaterialOptionsOut
from ..work_order_material_schemas import WorkOrderMaterialPreviewIn, WorkOrderMaterialPreviewOut
from ..formal_services.work_order_preview import preview_batch
from ..formal_services.work_order_material import WorkOrderMaterialLineInput, SerialVerificationInput

router = APIRouter(prefix="/v1/work-orders", tags=["formal-work-order-query"])


@router.post("/{work_order_id}/material-operations/{operation_type}/preview", response_model=WorkOrderMaterialPreviewOut)
def preview_my_materials(
    work_order_id: UUID, operation_type: Literal["occupy", "consume", "release"],
    payload: WorkOrderMaterialPreviewIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code": "operator_mismatch", "message": "操作人必须是当前登录人员"})
    lines = tuple(WorkOrderMaterialLineInput(material_id=row.material_id, stock_account_id=row.stock_account_id,
        quantity=row.quantity, serial_ids=row.serial_ids, condition_before=row.condition_before,
        target_stock_account_id=row.target_stock_account_id,
        serial_verifications=tuple(SerialVerificationInput(**proof.model_dump()) for proof in row.serial_verifications)) for row in payload.lines)
    try:
        return preview_batch(db, actor=principal, work_order_id=work_order_id, operation_type=operation_type, lines=lines)
    except InventoryReadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None
    except InventoryPostingError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "work_order_preview_unavailable", "message": "整批预检暂时无法完成，请稍后重新核验"}) from None


@router.get("/mine", response_model=MyWorkOrdersOut)
def my_work_orders(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: UUID | None = None,
    search: Annotated[str, Query(max_length=100)] = "",
    status: WorkOrderStatus | Literal["all"] = "active",
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return list_my_work_orders(db, actor=principal, limit=limit, after_id=after_id, search=search, status=None if status == "all" else status)
    except InventoryPostingError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "work_order_query_unavailable", "message": "本人工单暂时无法读取，请稍后刷新"}) from None


@router.get("/{work_order_id}/material-options", response_model=WorkOrderMaterialOptionsOut)
def my_material_options(
    work_order_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return material_options(db, actor=principal, work_order_id=work_order_id)
    except InventoryReadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None
    except InventoryPostingError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "work_order_material_options_unavailable", "message": "工单可用物料暂时无法读取，请稍后刷新"}) from None
