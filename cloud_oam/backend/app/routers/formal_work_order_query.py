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
from ..formal_services.work_order_completion import completion_check
from ..work_order_completion_schemas import WorkOrderCompletionCheckOut
from ..work_order_return_schemas import WorkOrderReturnSourcesOut, WorkOrderReturnSelectionIn, WorkOrderReturnSelectionOut
from ..formal_services.work_order_return_sources import return_sources, preview_selection
from ..formal_services.work_order_query import list_my_work_orders
from ..work_order_query_schemas import MyWorkOrdersOut, WorkOrderStatus, WorkOrderMaterialOptionsOut
from ..work_order_material_schemas import WorkOrderMaterialPreviewIn, WorkOrderMaterialPreviewOut
from ..formal_services.work_order_preview import preview_batch
from ..formal_services.work_order_material import WorkOrderMaterialLineInput, SerialVerificationInput
from ..formal_services import work_order_replacement_preview as replacement_preview
from ..formal_services.work_order_replacements import RecoveryLineInput
from ..formal_services.work_order_material import WorkOrderReplacementPairInput
from ..work_order_material_schemas import (
    WorkOrderReplacementPreviewIn, WorkOrderReplacementPreviewOut,
    WorkOrderRemovedScanIn, WorkOrderRemovedScanOut,
)

router = APIRouter(prefix="/v1/work-orders", tags=["formal-work-order-query"])


def _return_query_error(exc):
    if isinstance(exc, InventoryReadError):
        status, detail = exc.status_code, exc.as_detail()
    elif isinstance(exc, InventoryPostingError):
        status, detail = exc.http_status_code, exc.as_detail()
    else:
        status, detail = 503, {"code": "work_order_return_sources_unavailable", "message": "退回来源暂时无法核验，请稍后重新读取"}
    return HTTPException(status_code=status, detail=detail, headers={"Cache-Control": "private, no-store"})


@router.get("/{work_order_id}/return-sources", response_model=WorkOrderReturnSourcesOut)
def my_return_sources(work_order_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return return_sources(db, actor=principal, work_order_id=work_order_id)
    except (InventoryReadError, InventoryPostingError, SQLAlchemyError) as exc:
        raise _return_query_error(exc) from None


@router.post("/{work_order_id}/return-sources/preview", response_model=WorkOrderReturnSelectionOut)
def preview_my_return_sources(work_order_id: UUID, payload: WorkOrderReturnSelectionIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return preview_selection(db, actor=principal, work_order_id=work_order_id, request=payload)
    except (InventoryReadError, InventoryPostingError, SQLAlchemyError) as exc:
        raise _return_query_error(exc) from None


@router.get("/{work_order_id}/material-completion-check", response_model=WorkOrderCompletionCheckOut)
def check_my_work_order_completion(work_order_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        return completion_check(db, actor=principal, work_order_id=work_order_id)
    except InventoryReadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None
    except InventoryPostingError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "work_order_completion_unavailable", "message": "工单物料结束检查暂不可用，请稍后重新检查"}) from None


@router.post("/{work_order_id}/material-replacements/preview", response_model=WorkOrderReplacementPreviewOut)
def preview_my_replacement(
    work_order_id: UUID, payload: WorkOrderReplacementPreviewIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code":"operator_mismatch", "message":"操作人必须是当前登录人员"})
    def line(row, model):
        values = row.model_dump()
        values["serial_verifications"] = tuple(SerialVerificationInput(**proof) for proof in values["serial_verifications"])
        return model(**values)
    try:
        return replacement_preview.preview_replacement(db, actor=principal, work_order_id=work_order_id,
            consume_lines=tuple(line(row, WorkOrderMaterialLineInput) for row in payload.consume_lines),
            recover_lines=tuple(line(row, RecoveryLineInput) for row in payload.recover_lines),
            pairs=tuple(WorkOrderReplacementPairInput(**row.model_dump()) for row in payload.replacement_pairs))
    except InventoryReadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None
    except InventoryPostingError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code":"replacement_preview_unavailable", "message":"替换整批预检暂时不可用，请重新核验"}) from None


@router.post("/{work_order_id}/material-replacements/removed-part", response_model=WorkOrderRemovedScanOut)
def resolve_my_removed_part(
    work_order_id: UUID, payload: WorkOrderRemovedScanIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code":"operator_mismatch", "message":"操作人必须是当前登录人员"})
    try:
        return replacement_preview.lookup_removed_part(db, actor=principal, work_order_id=work_order_id, scan=payload)
    except InventoryReadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None
    except InventoryPostingError as exc:
        raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code":"removed_part_unavailable", "message":"拆回件暂时无法核验，请重新扫描"}) from None


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
