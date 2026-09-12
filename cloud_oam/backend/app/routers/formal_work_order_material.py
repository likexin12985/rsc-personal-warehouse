from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, Path
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy import select

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import work_order_material as service
from ..formal_services import work_order_replacements as replacements
from ..formal_services.work_order_replacement_read import replacement_result
from ..demand_models import (
    WorkOrderMaterialLine,
    WorkOrderMaterialOperation,
    WorkOrderMaterialSerial,
    WorkOrderReplacement,
)
from ..work_order_material_schemas import (
    WorkOrderMaterialPreflightIn,
    WorkOrderMaterialPreflightOut, WorkOrderMaterialOperationIn,
    WorkOrderMaterialOperationOut,
    WorkOrderMaterialConsumeIn,
    WorkOrderMaterialReleaseIn,
    WorkOrderMaterialOccupyIn,
    WorkOrderMaterialRecoverIn,
    WorkOrderMaterialOperationHistoryItemOut,
    WorkOrderMaterialOperationHistoryOut,
    WorkOrderReplacementIn,
    WorkOrderReplacementOut,
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


def _require_operator(payload, principal):
    if payload.operator_person_id != principal.person_id:
        raise HTTPException(status_code=403, detail={"code": "operator_mismatch", "message": "操作人必须是当前登录人员"})


@router.post("/{work_order_id}/material-replacements", response_model=WorkOrderReplacementOut)
def execute_material_replacement(
    work_order_id: UUID, payload: WorkOrderReplacementIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db), request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    _require_operator(payload, principal)
    if request_id is not None and request_id != payload.request_id:
        raise HTTPException(status_code=400, detail={"code": "request_id_mismatch", "message": "请求头与原请求标识不一致"})
    def proofs(row):
        return tuple(service.SerialVerificationInput(**proof.model_dump()) for proof in row.serial_verifications)
    consumed = tuple(service.WorkOrderMaterialLineInput(
        material_id=row.material_id, stock_account_id=row.stock_account_id, quantity=row.quantity,
        condition_before=row.condition_before, serial_ids=row.serial_ids, serial_verifications=proofs(row),
    ) for row in payload.consume_lines)
    recovered = tuple(replacements.RecoveryLineInput(
        basis_stock_account_id=row.basis_stock_account_id, material_id=row.material_id,
        lot_id=row.lot_id, target_stock_account_id=row.target_stock_account_id, quantity=row.quantity,
        condition_before=row.condition_before, serial_ids=row.serial_ids, serial_verifications=proofs(row),
    ) for row in payload.recover_lines)
    pairs = tuple(service.WorkOrderReplacementPairInput(**row.model_dump()) for row in payload.replacement_pairs)
    try:
        replacement = replacements.execute_replacement(db, actor=principal, work_order_id=work_order_id,
            consume_lines=consumed, recover_lines=recovered, pairs=pairs,
            idempotency_key=payload.idempotency_key, request_id=payload.request_id)
        output = replacement_result(db, replacement=replacement, actor=principal)
        db.commit()
    except service.InventoryPostingError as exc:
        db.rollback(); _raise(exc)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "work_order_storage_unavailable", "message": "替换结果未确认，请按原请求标识回读"}) from None
    return output


@router.get("/{work_order_id}/material-replacements/by-request/{request_id}", response_model=WorkOrderReplacementOut)
def read_material_replacement(
    work_order_id: UUID, response: Response,
    request_id: str = Path(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "read")),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        _order, current = service.authorize_work_order(db, actor=principal, work_order_id=work_order_id, action="read")
        replacement = db.scalar(select(WorkOrderReplacement).where(
            WorkOrderReplacement.oam_work_order_id == work_order_id,
            WorkOrderReplacement.operator_person_id == current.person_id,
            WorkOrderReplacement.request_id == request_id))
        if replacement is None:
            raise HTTPException(status_code=404, detail={"code": "replacement_not_found", "message": "尚未查到该请求的已提交替换记录"})
        return replacement_result(db, replacement=replacement, actor=current)
    except service.InventoryPostingError as exc:
        _raise(exc)
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail={"code": "work_order_storage_unavailable", "message": "原替换记录暂时无法核验，请保留原请求稍后查询"}) from None


@router.post("/{work_order_id}/material-operations/consume", response_model=WorkOrderMaterialOperationOut)
def execute_material_consume(
    work_order_id: UUID,
    payload: WorkOrderMaterialConsumeIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    _require_operator(payload, principal)
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


@router.post("/{work_order_id}/material-operations/release", response_model=WorkOrderMaterialOperationOut)
def execute_material_release(
    work_order_id: UUID, payload: WorkOrderMaterialReleaseIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db), request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    _require_operator(payload, principal)
    values = tuple(service.WorkOrderMaterialLineInput(
        material_id=row.material_id, stock_account_id=row.stock_account_id,
        target_stock_account_id=row.target_stock_account_id, quantity=row.quantity,
        serial_ids=row.serial_ids, condition_before=row.condition_before,
        serial_verifications=tuple(service.SerialVerificationInput(**v.model_dump()) for v in row.serial_verifications),
    ) for row in payload.lines)
    try:
        operation, _posted = service.execute_release_operation(
            db, actor=principal, work_order_id=work_order_id, lines=values,
            idempotency_key=payload.idempotency_key, request_id=request_id or payload.request_id,
        )
        output = WorkOrderMaterialOperationOut(
            operation_id=operation.id, operation_no=operation.operation_no,
            work_order_id=operation.oam_work_order_id, posting_transaction_id=operation.posting_transaction_id,
            operation_type=operation.operation_type, status=operation.status,
        )
        db.commit()
    except service.InventoryPostingError as exc:
        db.rollback(); _raise(exc)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "work_order_storage_unavailable", "message": "写入未确认，请回读原操作"}) from None
    return output


@router.post("/{work_order_id}/material-operations/occupy", response_model=WorkOrderMaterialOperationOut)
def execute_material_occupy(
    work_order_id: UUID, payload: WorkOrderMaterialOccupyIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db), request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    _require_operator(payload, principal)
    values = tuple(service.WorkOrderMaterialLineInput(
        material_id=row.material_id, stock_account_id=row.stock_account_id,
        target_stock_account_id=row.target_stock_account_id, quantity=row.quantity,
        serial_ids=row.serial_ids, condition_before=row.condition_before,
        serial_verifications=tuple(service.SerialVerificationInput(**v.model_dump()) for v in row.serial_verifications),
    ) for row in payload.lines)
    try:
        operation, _posted = service.execute_occupy_operation(
            db, actor=principal, work_order_id=work_order_id, lines=values,
            idempotency_key=payload.idempotency_key, request_id=request_id or payload.request_id)
        output = WorkOrderMaterialOperationOut(
            operation_id=operation.id, operation_no=operation.operation_no,
            work_order_id=operation.oam_work_order_id, posting_transaction_id=operation.posting_transaction_id,
            operation_type=operation.operation_type, status=operation.status)
        db.commit()
    except service.InventoryPostingError as exc:
        db.rollback(); _raise(exc)
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={"code": "work_order_storage_unavailable", "message": "写入未确认，请回读原操作"}) from None
    return output


@router.post("/{work_order_id}/material-operations/recover", response_model=WorkOrderMaterialOperationOut)
def execute_material_recover(
    work_order_id: UUID, payload: WorkOrderMaterialRecoverIn,
    principal: FormalPrincipal = Depends(require_permission("work_order_material", "operate")),
    db: Session = Depends(get_db), request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    _require_operator(payload, principal)
    values = tuple(service.WorkOrderMaterialLineInput(
        material_id=row.material_id, stock_account_id=row.target_stock_account_id,
        target_stock_account_id=row.target_stock_account_id, quantity=row.quantity,
        serial_ids=row.serial_ids, condition_before=row.condition_before,
        serial_verifications=tuple(service.SerialVerificationInput(**v.model_dump()) for v in row.serial_verifications),
    ) for row in payload.lines)
    try:
        operation, _posted = service.execute_recover_operation(
            db, actor=principal, work_order_id=work_order_id, lines=values,
            idempotency_key=payload.idempotency_key, request_id=request_id or payload.request_id)
        output = WorkOrderMaterialOperationOut(
            operation_id=operation.id, operation_no=operation.operation_no,
            work_order_id=operation.oam_work_order_id, posting_transaction_id=operation.posting_transaction_id,
            operation_type=operation.operation_type, status=operation.status)
        db.commit()
    except service.InventoryPostingError as exc:
        db.rollback(); _raise(exc)
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
    operation_ids = tuple(row.id for row in rows)
    lines_by_operation: dict[UUID, list[WorkOrderMaterialLine]] = {
        operation_id: [] for operation_id in operation_ids
    }
    if operation_ids:
        for line in db.scalars(
            select(WorkOrderMaterialLine)
            .where(WorkOrderMaterialLine.operation_id.in_(operation_ids))
            .order_by(WorkOrderMaterialLine.operation_id, WorkOrderMaterialLine.line_no)
        ).all():
            lines_by_operation[line.operation_id].append(line)
    line_ids = tuple(line.id for lines in lines_by_operation.values() for line in lines)
    serials_by_line: dict[UUID, list[tuple[UUID, bool, bool]]] = {
        line_id: [] for line_id in line_ids
    }
    if line_ids:
        for serial_id, line_id, sku_verified, qr_verified in db.execute(
            select(
                WorkOrderMaterialSerial.serial_id,
                WorkOrderMaterialSerial.operation_line_id,
                WorkOrderMaterialSerial.sku_verified,
                WorkOrderMaterialSerial.qr_verified,
            )
            .where(WorkOrderMaterialSerial.operation_line_id.in_(line_ids))
            .order_by(WorkOrderMaterialSerial.operation_line_id, WorkOrderMaterialSerial.serial_id)
        ).all():
            serials_by_line[line_id].append((serial_id, sku_verified, qr_verified))
    return WorkOrderMaterialOperationHistoryOut(items=tuple(
        WorkOrderMaterialOperationHistoryItemOut(
            operation_id=row.id, operation_no=row.operation_no,
            work_order_id=row.oam_work_order_id,
            posting_transaction_id=row.posting_transaction_id,
            operation_type=row.operation_type, status=row.status,
            lines=tuple(
                {
                    "line_no": line.line_no,
                    "material_id": line.material_id,
                    "stock_account_id": line.stock_account_id,
                    "quantity": line.quantity,
                    "condition_before": line.condition_before,
                    "condition_after": line.condition_after,
                    "serial_ids": tuple(serial_id for serial_id, _, _ in serials_by_line[line.id]),
                    "serials": tuple(
                        {
                            "serial_id": serial_id,
                            "sku_verified": sku_verified,
                            "qr_verified": qr_verified,
                        }
                        for serial_id, sku_verified, qr_verified in serials_by_line[line.id]
                    ),
                }
                for line in lines_by_operation[row.id]
            ),
        )
        for row in rows
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
