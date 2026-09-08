from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import work_order_material as service
from ..work_order_material_schemas import (
    WorkOrderMaterialLineIn, WorkOrderMaterialPreflightIn,
    WorkOrderMaterialPreflightOut, WorkOrderMaterialOperationIn,
    WorkOrderMaterialOperationOut,
)

router = APIRouter(prefix="/v1/work-orders", tags=["formal-work-order-material"])


@router.post("/{work_order_id}/material-preflight", response_model=WorkOrderMaterialPreflightOut)
def preflight_material_operation(
    work_order_id: UUID,
    payload: WorkOrderMaterialPreflightIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code": "operator_mismatch", "message": "操作人必须是当前登录人员"})
    lines = tuple(WorkOrderMaterialLineIn.model_validate(row).model_dump() for row in payload.lines)
    values = tuple(service.WorkOrderMaterialLineInput(**row) for row in lines)
    try:
        result = service.preflight_work_order_material_batch(
            db, work_order_id=work_order_id,
            operator_person_id=principal.person_id, lines=values,
        )
    except service.WorkOrderMaterialPreflightError as exc:
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from None
    return WorkOrderMaterialPreflightOut(
        work_order_id=result.work_order_id,
        operator_person_id=result.operator_person_id,
        line_count=len(result.lines),
    )


__all__ = ["router"]


@router.post("/{work_order_id}/material-operations", response_model=WorkOrderMaterialOperationOut)
def post_material_operation(
    work_order_id: UUID,
    payload: WorkOrderMaterialOperationIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code": "operator_mismatch", "message": "操作人必须是当前登录人员"})
    values = tuple(service.WorkOrderMaterialLineInput(**row.model_dump()) for row in payload.lines)
    pairs = tuple(service.WorkOrderReplacementPairInput(**row.model_dump()) for row in payload.replacement_pairs)
    try:
        operation = service.record_posted_operation(
            db, operation_type=payload.operation_type, work_order_id=work_order_id,
            operator_person_id=principal.person_id, lines=values,
            posting_transaction_id=payload.posting_transaction_id,
            idempotency_key=payload.idempotency_key,
            replacement_pairs=pairs,
        )
        db.commit()
    except service.WorkOrderMaterialPreflightError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": exc.message}) from None
    return WorkOrderMaterialOperationOut(
        operation_id=operation.id, operation_no=operation.operation_no,
        work_order_id=operation.oam_work_order_id,
        posting_transaction_id=operation.posting_transaction_id,
        operation_type=operation.operation_type, status=operation.status,
    )
