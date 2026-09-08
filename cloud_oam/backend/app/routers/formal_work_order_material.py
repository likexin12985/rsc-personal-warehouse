from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import work_order_material as service
from ..demand_models import WorkOrderMaterialOperation
from ..work_order_material_schemas import (
    WorkOrderMaterialPreflightIn,
    WorkOrderMaterialPreflightOut, WorkOrderMaterialOperationIn,
    WorkOrderMaterialOperationOut,
    WorkOrderMaterialConsumeIn,
    WorkOrderMaterialOperationHistoryOut,
)

router = APIRouter(prefix="/v1/work-orders", tags=["formal-work-order-material"])


def _lines(payload):
    return tuple(service.WorkOrderMaterialLineInput(
        material_id=row.material_id, stock_account_id=row.stock_account_id,
        quantity=row.quantity, serial_ids=row.serial_ids, condition_before=row.condition_before,
        serial_verifications=tuple(service.SerialVerificationInput(**v.model_dump()) for v in row.serial_verifications),
    ) for row in payload.lines)


def _raise(exc):
    raise HTTPException(status_code=exc.http_status_code, detail=exc.as_detail()) from None


@router.post("/{work_order_id}/material-operations/consume", response_model=WorkOrderMaterialOperationOut)
def execute_material_consume(
    work_order_id: UUID,
    payload: WorkOrderMaterialConsumeIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    trace = request_id or payload.request_id
    values = _lines(payload)
    try:
        operation, _posted = service.execute_consume_operation(
            db, actor=principal, work_order_id=work_order_id, lines=values,
            idempotency_key=payload.idempotency_key, request_id=trace,
        )
        output = WorkOrderMaterialOperationOut(
            operation_id=operation.id, operation_no=operation.operation_no,
            work_order_id=operation.oam_work_order_id,
            posting_transaction_id=operation.posting_transaction_id,
            operation_type=operation.operation_type, status=operation.status,
        )
        db.commit()
    except service.InventoryPostingError as exc:
        db.rollback()
        _raise(exc)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "work_order_storage_unavailable", "message": "写入未确认，请回读原操作"}) from None
    return output


@router.post("/{work_order_id}/material-preflight", response_model=WorkOrderMaterialPreflightOut)
def preflight_material_operation(
    work_order_id: UUID,
    payload: WorkOrderMaterialPreflightIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code": "operator_mismatch", "message": "操作人必须是当前登录人员"})
    values = _lines(payload)
    try:
        service.authorize_work_order(db, actor=principal, work_order_id=work_order_id, action="operate")
        result = service.preflight_work_order_material_batch(
            db, work_order_id=work_order_id,
            operator_person_id=principal.person_id, lines=values,
        )
    except service.InventoryPostingError as exc:
        _raise(exc)
    return WorkOrderMaterialPreflightOut(
        work_order_id=result.work_order_id,
        operator_person_id=result.operator_person_id,
        line_count=len(result.lines),
    )


__all__ = ["router"]


@router.get("/{work_order_id}/material-operations", response_model=WorkOrderMaterialOperationHistoryOut)
def list_material_operations(
    work_order_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")),
    db: Session = Depends(get_db),
):
    try:
        service.authorize_work_order(db, actor=principal, work_order_id=work_order_id, action="read")
    except service.InventoryPostingError as exc:
        _raise(exc)
    response.headers["Cache-Control"] = "private, no-store"
    rows = tuple(db.scalars(
        select(WorkOrderMaterialOperation)
        .where(WorkOrderMaterialOperation.oam_work_order_id == work_order_id)
        .order_by(WorkOrderMaterialOperation.created_at, WorkOrderMaterialOperation.operation_no)
    ).all())
    return WorkOrderMaterialOperationHistoryOut(items=tuple(
        WorkOrderMaterialOperationOut(
            operation_id=row.id, operation_no=row.operation_no,
            work_order_id=row.oam_work_order_id,
            posting_transaction_id=row.posting_transaction_id,
            operation_type=row.operation_type, status=row.status,
        ) for row in rows
    ))


@router.post("/{work_order_id}/material-operations", response_model=WorkOrderMaterialOperationOut)
def post_material_operation(
    work_order_id: UUID,
    payload: WorkOrderMaterialOperationIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code": "operator_mismatch", "message": "操作人必须是当前登录人员"})
    values = _lines(payload)
    pairs = tuple(service.WorkOrderReplacementPairInput(**row.model_dump()) for row in payload.replacement_pairs)
    try:
        operation = service.record_posted_operation(
            db, actor=principal, operation_type=payload.operation_type, work_order_id=work_order_id,
            operator_person_id=principal.person_id, lines=values,
            posting_transaction_id=payload.posting_transaction_id,
            idempotency_key=payload.idempotency_key,
            replacement_pairs=pairs,
        )
        output = WorkOrderMaterialOperationOut(
            operation_id=operation.id, operation_no=operation.operation_no,
            work_order_id=operation.oam_work_order_id,
            posting_transaction_id=operation.posting_transaction_id,
            operation_type=operation.operation_type, status=operation.status,
        )
        db.commit()
    except service.InventoryPostingError as exc:
        db.rollback()
        _raise(exc)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "work_order_storage_unavailable", "message": "写入未确认，请回读原操作"}) from None
    return output
