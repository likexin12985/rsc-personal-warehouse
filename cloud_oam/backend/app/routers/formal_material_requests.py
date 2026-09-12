"""Formal V1.0 material-request HTTP composition boundary.

The router owns only authentication/permission composition, strict public-to-
domain DTO mapping, contact protection and one-transaction commit/rollback.
It does not duplicate demand policy and it never advances allocation,
reservation, outbound, shipment, signature, OAM receipt, personal inbound,
notification or reconciliation state.

The production composition root supplies one request-scoped
:class:`MaterialRequestContactCipher` from a ciphertext-only KMS data-key
registry.  Missing or unavailable KMS material still fails closed while read
routes remain usable.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..demand_models import ApprovalInstance
from ..demand_schemas import (
    ExternalApprovalRegistrationIn,
    ExternalApprovalVerificationIn,
    MaterialRequestAmendIn,
    MaterialRequestApprovalDecisionIn,
    MaterialRequestCancelIn,
    MaterialRequestCreateIn,
    MaterialRequestSubmitIn,
    MaterialRequestWithdrawIn,
    SupplyTaskCreateIn,
    SupplyTaskUpdateIn,
)
from ..dependencies import get_formal_principal, require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import material_request_approval as approval_service
from ..formal_services import material_request_allocation as allocation_service
from ..formal_services import material_request_allocation_options as allocation_options_service
from ..formal_services import material_request_command_status as command_status_service
from ..formal_services import material_request_draft as draft_service
from ..formal_services import material_request_edit as edit_service
from ..formal_services import material_request_lifecycle as lifecycle_service
from ..formal_services import material_request_query as query_service
from ..formal_services import material_request_supply as supply_service
from ..formal_services import material_request_supply_command_status as supply_status_service
from ..formal_services import material_request_reservation as reservation_service
from ..formal_services import material_request_reservation_release as release_service
from ..formal_services import material_request_outbound as outbound_service
from ..formal_services import material_request_outbound_options as outbound_options_service
from ..formal_services import material_request_shipment as shipment_service
from ..formal_services import material_request_receipt as receipt_service
from ..formal_services import material_request_inbound as inbound_service
from ..formal_services.inventory_posting import InventoryPostingError
from ..formal_services import material_request_logistics as logistics_service
from ..formal_services import material_request_oam_receipt as oam_receipt_service
from ..material_request_outbound_schemas import OutboundOptionsOut, OutboundIn, OutboundOut, OutboundStatusOut
from ..material_request_shipment_schemas import ShipmentIn, ShipmentOut, ShipmentOptionsOut, ShipmentCommandStatusOut
from ..material_request_receipt_schemas import ReceiptIn, ReceiptOut, ReceiptCommandStatusOut
from ..material_request_inbound_schemas import InboundOrderIn, InboundOrderOut, InboundPostingOut
from ..formal_services import material_request_my_receiving as my_receiving_service
from ..material_request_my_receiving_schemas import MyReceivingOut
from ..formal_services import material_request_my_receipt as my_receipt_service
from ..material_request_my_receipt_schemas import MyReceiptIn, MyReceiptOut, MyReceiptCommandStatusOut
from ..formal_services import material_request_my_receipt_candidates as my_receipt_candidates_service
from ..material_request_my_receipt_candidate_schemas import MyReceiptCandidateOut
from ..material_request_logistics_schemas import LogisticsEventIn, LogisticsEventOut, LogisticsEventCommandStatusOut
from ..material_request_oam_receipt_schemas import OamReceiptEvidenceOut
from ..formal_services import material_request_picking as picking_service
from ..formal_services import material_request_picking_options as picking_options_service
from ..material_request_picking_schemas import PickOptionsOut
from ..material_request_picking_schemas import ReservationPickIn, ReservationPickOut, ReservationPickStatusOut
from ..formal_services import material_request_reservation_release_options as release_options_service
from ..formal_services import material_request_fulfillment_preparation as preparation_service
from ..material_request_fulfillment_preparation_schemas import FulfillmentPreparationOut
from ..material_request_reservation_release_schemas import (
    ReservationReleaseIn, ReservationReleaseOut, ReservationReleaseStatusOut,
    ReservationReleaseOptionsOut,
)
from ..formal_services import material_request_reservation_options as reservation_options_service
from ..formal_services.material_request_contact import (
    MaterialRequestContactCipher,
    MaterialRequestContactProtectionError,
    protect_material_request_contact,
)
from ..formal_services.material_request_policy import (
    ApprovalLineDecision,
    ApprovalReturnInstruction,
)
from ..material_request_read_schemas import (
    MaterialRequestCreateOut,
    MaterialRequestDetailOut,
    MaterialRequestLifecycleCommandOut,
    MaterialRequestLifecycleCommandStatusOut,
    MaterialRequestMutationOut,
    MaterialRequestPageOut,
    MaterialRequestSupplyTaskMutationOut,
    MaterialRequestSupplyCommandOut,
    MaterialRequestSupplyCommandStatusOut,
)
from ..material_request_allocation_option_schemas import (
    MaterialRequestAllocationOptionPageOut,
)
from ..material_request_allocation_schemas import (
    AllocationCommandStatusOut,
    AllocationCreateIn,
    AllocationMutationOut,
)
from ..material_request_reservation_schemas import (
    ReservationCommandStatusOut,
    ReservationCreateIn,
    ReservationMutationOut,
)
from ..material_request_reservation_option_schemas import MaterialRequestReservationOptionPageOut
from ..production_adapters import create_production_material_request_contact_cipher


router = APIRouter(
    prefix="/v1/material-requests",
    tags=["formal-material-requests"],
)
command_status_router = APIRouter(
    prefix="/v1",
    tags=["formal-material-requests"],
)

_SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._:-]+$")
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")


def _set_read_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"


@command_status_router.get(
    "/material-request-allocation-command-status",
    response_model=AllocationCommandStatusOut,
)
def formal_material_request_allocation_command_status(
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_request_id = _required_safe_header(
        "X-Request-ID", request_id, minimum=8, maximum=160
    )
    _set_read_no_store(response)
    try:
        result = allocation_service.allocation_command_status(
            db, actor=principal, trace_request_id=checked_request_id
        )
        output = AllocationCommandStatusOut(
            lookup_status="confirmed" if result is not None else "not_observed",
            command=None
            if result is None
            else AllocationMutationOut(
                request_id=result.request_id,
                allocation_id=result.allocation_id,
                allocation_no=result.allocation_no,
                request_version=result.request_version,
                current_request_version=result.current_request_version,
                revision_id=result.revision_id,
                revision_no=result.revision_no,
                request_line_id=result.request_line_id,
                source_stock_account_id=result.source_stock_account_id,
                source_balance_version=result.source_balance_version,
                source_ledger_cursor=result.source_ledger_cursor,
                allocated_qty=f"{result.allocated_qty:.3f}",
                allocation_status="allocated",
                request_status=result.request_status,
                state_axes=dict(result.state_axes),
                idempotency_replayed=True,
            ),
        )
    except allocation_service.MaterialRequestAllocationError as exc:
        _raise_service_error(exc, no_store=True)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "material_request_allocation_response_invalid",
                "category": "service_unavailable",
                "message": "分配命令状态响应无效，保持结果待核验",
            },
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        ) from None
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    return output


@command_status_router.get(
    "/material-request-reservation-command-status",
    response_model=ReservationCommandStatusOut,
)
def formal_material_request_reservation_command_status(
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "read")
    ),
    db: Session = Depends(get_db),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_request_id = _required_safe_header(
        "X-Request-ID", request_id, minimum=8, maximum=160
    )
    _set_read_no_store(response)
    try:
        result = reservation_service.reservation_command_status(
            db, actor=principal, trace_request_id=checked_request_id
        )
        output = ReservationCommandStatusOut(
            lookup_status="confirmed" if result is not None else "not_observed",
            command=(
                None
                if result is None
                else ReservationMutationOut(
                    request_id=result.request_id,
                    reservation_id=result.reservation_id,
                    reservation_no=result.reservation_no,
                    request_version=result.request_version,
                    current_request_version=result.current_request_version,
                    revision_id=result.revision_id,
                    revision_no=result.revision_no,
                    request_line_id=result.request_line_id,
                    allocation_id=result.allocation_id,
                    source_stock_account_id=result.source_stock_account_id,
                    stock_account_id=result.stock_account_id,
                    reserve_transaction_id=result.reserve_transaction_id,
                    reserve_transaction_no=result.reserve_transaction_no,
                    source_balance_version=result.source_balance_version,
                    source_ledger_cursor=result.source_ledger_cursor,
                    serial_ids=result.serial_ids,
                    reserved_qty=f"{result.reserved_qty:.3f}",
                    reservation_status="reserved",
                    request_status=result.request_status,
                    state_axes=dict(result.state_axes),
                    idempotency_replayed=True,
                )
            ),
        )
    except reservation_service.MaterialRequestReservationError as exc:
        _raise_service_error(exc, no_store=True)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "material_request_reservation_response_invalid",
                "category": "service_unavailable",
                "message": "预约命令状态响应无效，保持结果待核验",
            },
            headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
        ) from None
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    return output


@command_status_router.get(
    "/material-request-lifecycle-command-status",
    response_model=MaterialRequestLifecycleCommandStatusOut,
)
def formal_material_request_lifecycle_command_status(
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "read")
    ),
    db: Session = Depends(get_db),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_request_id = _required_safe_header(
        "X-Request-ID",
        request_id,
        minimum=8,
        maximum=160,
    )
    try:
        result = command_status_service.material_request_lifecycle_command_status(
            db,
            actor=principal,
            trace_request_id=checked_request_id,
        )
        command = (
            None
            if result.command is None
            else MaterialRequestLifecycleCommandOut(
                action=result.command.action,
                request_id=result.command.request_id,
                request_version=result.command.request_version,
                revision_id=result.command.revision_id,
                revision_no=result.command.revision_no,
                approval_instance_id=result.command.approval_instance_id,
                approval_attempt_no=result.command.approval_attempt_no,
                current_step_id=None,
                states=dict(result.command.states),
                occurred_at=result.command.occurred_at,
            )
        )
        output = MaterialRequestLifecycleCommandStatusOut(
            lookup_status=result.lookup_status,
            command=command,
        )
    except lifecycle_service.MaterialRequestLifecycleError as exc:
        _raise_service_error(exc)
    except ValidationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "material_request_response_projection_invalid",
                "category": "service_unavailable",
                "message": "生命周期命令状态响应投影无效",
            },
        ) from None
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True)
    _set_read_no_store(response)
    return output


@command_status_router.get(
    "/material-request-supply-command-status",
    response_model=MaterialRequestSupplyCommandStatusOut,
)
def formal_supply_command_status(
    response: Response,
    trace_request_id: str = Query(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$"),
    principal: FormalPrincipal = Depends(require_permission("supply_task", "manage")),
    db: Session = Depends(get_db),
):
    try:
        result = supply_status_service.material_request_supply_command_status(
            db, actor=principal, trace_request_id=trace_request_id,
        )
        command = None
        if result.command is not None:
            values = _supply_mutation_output(result.command).model_dump(
                exclude={"schema_version", "idempotency_replayed"},
            )
            command = MaterialRequestSupplyCommandOut(
                **values, occurred_at=result.occurred_at,
            )
        output = MaterialRequestSupplyCommandStatusOut(
            lookup_status=result.lookup_status, command=command,
        )
    except supply_service.MaterialRequestSupplyError as exc:
        _raise_service_error(exc)
    except SQLAlchemyError:
        _raise_database_unavailable(read_only=True)
    except ValidationError:
        raise HTTPException(status_code=503, detail={
            "code": "material_request_supply_command_projection_invalid",
            "category": "service_unavailable", "message": "供给历史命令投影无效，保持结果待核验",
        }) from None
    _set_read_no_store(response)
    return output


class _MaterialRequestAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        category: str,
        message: str,
        http_status_code: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.http_status_code = http_status_code

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


def get_material_request_contact_cipher(
    runtime_settings: Settings = Depends(get_settings),
) -> MaterialRequestContactCipher | None:
    """Return one request-scoped KMS cipher with no plaintext-key fallback."""

    try:
        return create_production_material_request_contact_cipher(runtime_settings)
    except Exception:
        # The formal write adapter maps absence/unavailability to its stable,
        # non-sensitive 503 response before any business mutation.
        return None


async def formal_material_request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    """Remove request-body values from validation responses for this PII API."""

    if request.url.path.startswith("/api/v1/material-requests"):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": {
                    "code": "material_request_request_invalid",
                    "category": "invalid_request",
                    "message": "需求单请求字段无效",
                }
            },
        )
    return await request_validation_exception_handler(request, exc)


def install_formal_material_request_validation_exception_handler(
    app: FastAPI,
) -> None:
    app.add_exception_handler(
        RequestValidationError,
        formal_material_request_validation_exception_handler,
    )


def _require_internal_approval_permission(
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
) -> FormalPrincipal:
    allowed = any(
        principal.allows(
            db,
            "material_request",
            action,
            field_code="approval_decision",
        )
        for action in ("approve_region", "approve_headquarters")
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "material_request_approval_permission_denied",
                "category": "forbidden",
                "message": "没有当前内部审批操作权限",
            },
        )
    return principal


@router.get("", response_model=MaterialRequestPageOut)
def list_formal_material_requests(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    after_id: Annotated[UUID | None, Query()] = None,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "read")
    ),
    db: Session = Depends(get_db),
):
    try:
        output = query_service.list_material_requests(
            db,
            actor=principal,
            limit=limit,
            after_id=after_id,
        )
    except query_service.MaterialRequestReadError as exc:
        _raise_service_error(exc)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True)
    _set_read_no_store(response)
    return output


@router.get(
    "/{material_request_id}/allocation-options",
    response_model=MaterialRequestAllocationOptionPageOut,
)
def formal_material_request_allocation_options(
    material_request_id: UUID,
    response: Response,
    request_line_id: Annotated[UUID, Query(...)],
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "read")
    ),
    db: Session = Depends(get_db),
):
    """Return source stock candidates without creating allocation facts."""

    # Set before service evaluation so fail-closed errors are non-cacheable too.
    _set_read_no_store(response)
    try:
        output = allocation_options_service.list_allocation_options(
            db,
            actor=principal,
            material_request_id=material_request_id,
            request_line_id=request_line_id,
        )
    except allocation_options_service.MaterialRequestAllocationOptionError as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    return output


@router.get(
    "/{material_request_id}/reservation-options",
    response_model=MaterialRequestReservationOptionPageOut,
)
def formal_material_request_reservation_options(
    material_request_id: UUID,
    response: Response,
    request_line_id: Annotated[UUID, Query(...)],
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    """Return actual allocation candidates without creating inventory facts."""
    _set_read_no_store(response)
    try:
        return reservation_options_service.list_reservation_options(
            db, actor=principal, material_request_id=material_request_id,
            request_line_id=request_line_id,
        )
    except reservation_options_service.MaterialRequestReservationOptionError as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.post(
    "/{material_request_id}/allocations",
    response_model=AllocationMutationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_formal_material_request_allocation(
    material_request_id: UUID,
    payload: AllocationCreateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key, request_id=request_id,
    )
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = allocation_service.create_allocation(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_request_version=payload.expected_request_version,
            allocation=allocation_service.AllocationCreateInput(
                request_line_id=payload.request_line_id,
                source_stock_account_id=payload.source_stock_account_id,
                allocated_qty=payload.allocated_qty,
                source_balance_version=payload.source_balance_version,
                source_ledger_cursor=payload.source_ledger_cursor,
                serial_ids=payload.serial_ids,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = AllocationMutationOut(
            request_id=result.request_id,
            allocation_id=result.allocation_id,
            allocation_no=result.allocation_no,
            request_version=result.request_version,
            current_request_version=result.current_request_version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            request_line_id=result.request_line_id,
            source_stock_account_id=result.source_stock_account_id,
            source_balance_version=result.source_balance_version,
            source_ledger_cursor=result.source_ledger_cursor,
            allocated_qty=f"{result.allocated_qty:.3f}",
            allocation_status="allocated",
            request_status=result.request_status,
            state_axes=dict(result.state_axes),
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/reservations",
    response_model=ReservationMutationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_formal_material_request_reservation(
    material_request_id: UUID,
    payload: ReservationCreateIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "read")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key, request_id=request_id
    )
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = reservation_service.create_reservation(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_request_version=payload.expected_request_version,
            reservation=reservation_service.ReservationCreateInput(
                request_line_id=payload.request_line_id,
                allocation_id=payload.allocation_id,
                reserved_qty=payload.reserved_qty,
                source_balance_version=payload.source_balance_version,
                source_ledger_cursor=payload.source_ledger_cursor,
                serial_ids=payload.serial_ids,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = ReservationMutationOut(
            request_id=result.request_id,
            reservation_id=result.reservation_id,
            reservation_no=result.reservation_no,
            request_version=result.request_version,
            current_request_version=result.current_request_version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            request_line_id=result.request_line_id,
            allocation_id=result.allocation_id,
            source_stock_account_id=result.source_stock_account_id,
            stock_account_id=result.stock_account_id,
            reserve_transaction_id=result.reserve_transaction_id,
            reserve_transaction_no=result.reserve_transaction_no,
            source_balance_version=result.source_balance_version,
            source_ledger_cursor=result.source_ledger_cursor,
            serial_ids=result.serial_ids,
            reserved_qty=f"{result.reserved_qty:.3f}",
            reservation_status="reserved",
            request_status=result.request_status,
            state_axes=dict(result.state_axes),
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@command_status_router.get("/material-request-reservation-release-command-status", response_model=ReservationReleaseStatusOut)
def formal_material_request_release_status(
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    trace = _required_safe_header("X-Request-ID", request_id, minimum=8, maximum=160)
    _set_read_no_store(response)
    try:
        result = release_service.release_command_status(db, actor=principal, trace_request_id=trace)
        return ReservationReleaseStatusOut(lookup_status="confirmed" if result else "not_observed", command=result)
    except reservation_service.MaterialRequestReservationError as exc:
        _raise_service_error(exc, no_store=True)
    except (ValidationError, DBAPIError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.get("/{material_request_id}/reservation-release-options", response_model=ReservationReleaseOptionsOut)
def formal_material_request_release_options(
    material_request_id: UUID, request_line_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return release_options_service.list_release_options(db, actor=principal,
            material_request_id=material_request_id, request_line_id=request_line_id)
    except reservation_service.MaterialRequestReservationError as exc:
        _raise_service_error(exc, no_store=True)
    except (DBAPIError, ValidationError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.get("/{material_request_id}/fulfillment-preparation", response_model=FulfillmentPreparationOut)
def formal_material_request_fulfillment_preparation(
    material_request_id: UUID, request_line_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return preparation_service.list_fulfillment_preparation(db, actor=principal,
            material_request_id=material_request_id, request_line_id=request_line_id)
    except (reservation_service.MaterialRequestReservationError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except (DBAPIError, ValidationError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.post("/{material_request_id}/reservation-releases", response_model=ReservationReleaseOut, status_code=201)
def create_formal_material_request_release(
    material_request_id: UUID, payload: ReservationReleaseIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = release_service.create_release(db, actor=principal, material_request_id=material_request_id,
            expected_request_version=payload.expected_request_version,
            release=release_service.ReservationReleaseInput(**payload.model_dump(exclude={"expected_request_version"})),
            idempotency_key=key, idempotency_hmac_secret=secret, trace_request_id=trace)
        output = ReservationReleaseOut(**result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.get("/{material_request_id}/reservation-pick-options", response_model=PickOptionsOut)
def formal_material_request_picking_options(
    material_request_id: UUID, request_line_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return picking_options_service.list_picking_options(db, actor=principal,
            material_request_id=material_request_id, request_line_id=request_line_id)
    except (reservation_service.MaterialRequestReservationError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except (DBAPIError, ValidationError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.post("/{material_request_id}/reservation-picks", response_model=ReservationPickOut, status_code=201)
def create_formal_material_request_pick(
    material_request_id: UUID, payload: ReservationPickIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = picking_service.create_pick(db, actor=principal, material_request_id=material_request_id,
            expected_request_version=payload.expected_request_version,
            pick=picking_service.ReservationPickInput(**payload.model_dump(exclude={"expected_request_version"})),
            idempotency_key=key, idempotency_hmac_secret=secret, trace_request_id=trace)
        output = ReservationPickOut(**result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@command_status_router.get("/material-request-reservation-pick-command-status", response_model=ReservationPickStatusOut)
def formal_material_request_pick_status(
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    trace = _required_safe_header("X-Request-ID", request_id, minimum=8, maximum=160)
    _set_read_no_store(response)
    try:
        result = picking_service.pick_command_status(db, actor=principal, trace_request_id=trace)
        return ReservationPickStatusOut(lookup_status="confirmed" if result else "not_observed", command=result)
    except reservation_service.MaterialRequestReservationError as exc:
        _raise_service_error(exc, no_store=True)
    except (ValidationError, DBAPIError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.get("/{material_request_id}/outbound-options", response_model=OutboundOptionsOut)
def formal_material_request_outbound_options(
    material_request_id: UUID, request_line_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return outbound_options_service.list_outbound_options(db, actor=principal,
            material_request_id=material_request_id, request_line_id=request_line_id)
    except (reservation_service.MaterialRequestReservationError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except (DBAPIError, ValidationError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.post("/{material_request_id}/outbounds", response_model=OutboundOut, status_code=201)
def create_formal_material_request_outbound(
    material_request_id: UUID, payload: OutboundIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = outbound_service.create_outbound(db, actor=principal, material_request_id=material_request_id,
            expected_request_version=payload.expected_request_version,
            outbound=outbound_service.OutboundInput(**payload.model_dump(exclude={"expected_request_version"})),
            idempotency_key=key, idempotency_hmac_secret=secret, trace_request_id=trace)
        output = OutboundOut(**result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@command_status_router.get("/material-request-outbound-command-status", response_model=OutboundStatusOut)
def formal_material_request_outbound_status(
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    trace = _required_safe_header("X-Request-ID", request_id, minimum=8, maximum=160)
    _set_read_no_store(response)
    try:
        result = outbound_service.outbound_command_status(db, actor=principal, trace_request_id=trace)
        return OutboundStatusOut(lookup_status="confirmed" if result else "not_observed", command=result)
    except reservation_service.MaterialRequestReservationError as exc:
        _raise_service_error(exc, no_store=True)
    except (ValidationError, DBAPIError):
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)



@router.post("/{material_request_id}/shipments", response_model=ShipmentOut, status_code=201)
def create_formal_material_request_shipment(
    material_request_id: UUID, payload: ShipmentIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "fulfill")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = shipment_service.create_shipment(db, actor=principal, request_id=material_request_id,
            expected_version=payload.expected_request_version, target_location_id=payload.target_location_id,
            target_person_id=payload.target_person_id, carrier=payload.carrier, tracking_no=payload.tracking_no,
            shipped_at=payload.shipped_at, lines=payload.lines, idempotency_key=key, secret=secret,
            trace_request_id=trace)
        output = ShipmentOut(**result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output

@router.get("/{material_request_id}/shipments", response_model=list[ShipmentOut])
def list_formal_material_request_shipments(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return [ShipmentOut(**row) for row in shipment_service.list_shipments(db, actor=principal, request_id=material_request_id)]
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/shipment-options", response_model=ShipmentOptionsOut)
def list_formal_material_request_shipment_options(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return shipment_service.list_shipment_options(db, actor=principal, request_id=material_request_id)
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/shipment-command-status", response_model=ShipmentCommandStatusOut)
def shipment_command_status(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Read-only recovery probe for a timed-out shipment submission."""
    key = _required_safe_header("Idempotency-Key", idempotency_key, minimum=16, maximum=128)
    _set_read_no_store(response)
    try:
        secret = _require_lifecycle_idempotency_secret(runtime_settings)
        result = shipment_service.shipment_command_status(
            db, actor=principal, request_id=material_request_id,
            idempotency_key=key, secret=secret,
        )
        return ShipmentCommandStatusOut(
            lookup_status="confirmed" if result is not None else "not_observed",
            request_hash=None if result is None else result["request_hash"],
            command=None if result is None else ShipmentOut(**result["command"]),
        )
    except (shipment_service.ShipmentError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.post("/{material_request_id}/shipments/{shipment_id}/logistics-events", response_model=LogisticsEventOut, status_code=201)
def create_formal_material_request_logistics_event(
    material_request_id: UUID, shipment_id: UUID, payload: LogisticsEventIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "fulfill")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = logistics_service.create_event(db, actor=principal, request_id=material_request_id,
            shipment_id=shipment_id, event_type=payload.event_type, event_at=payload.event_at,
            source=payload.source, evidence_file_id=payload.evidence_file_id, external_ref=payload.external_ref,
            idempotency_key=key, secret=secret, trace_request_id=trace)
        output = LogisticsEventOut(**result); db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response); _set_replay_header(response, output.idempotency_replayed)
    return output

@router.get("/{material_request_id}/shipments/{shipment_id}/logistics-events", response_model=list[LogisticsEventOut])
def list_formal_material_request_logistics_events(
    material_request_id: UUID, shipment_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return [LogisticsEventOut(**row) for row in logistics_service.list_events(db, actor=principal, request_id=material_request_id, shipment_id=shipment_id)]
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/shipments/{shipment_id}/logistics-command-status", response_model=LogisticsEventCommandStatusOut)
def logistics_command_status(
    material_request_id: UUID, shipment_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    key = _required_safe_header("Idempotency-Key", idempotency_key, minimum=16, maximum=128)
    _set_read_no_store(response)
    try:
        result = logistics_service.logistics_command_status(
            db, actor=principal, request_id=material_request_id, shipment_id=shipment_id,
            idempotency_key=key, secret=_require_lifecycle_idempotency_secret(runtime_settings),
        )
        return LogisticsEventCommandStatusOut(
            lookup_status="confirmed" if result is not None else "not_observed",
            command=None if result is None else LogisticsEventOut(**result),
        )
    except (logistics_service.LogisticsEventError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/oam-receipt-evidence", response_model=list[OamReceiptEvidenceOut])
def list_formal_material_request_oam_receipt_evidence(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    """Read-only OAM receipt evidence; it never changes local receipt or inbound state."""
    _set_read_no_store(response)
    try:
        return [OamReceiptEvidenceOut(**row) for row in oam_receipt_service.list_oam_receipt_evidence(
            db, actor=principal, request_id=material_request_id,
        )]
    except (oam_receipt_service.OamReceiptEvidenceError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/my-receiving", response_model=MyReceivingOut)
def list_formal_material_request_my_receiving(
    material_request_id: UUID, response: Response,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
    after_id: UUID | None = None,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return my_receiving_service.list_my_receiving(
            db, actor=principal, request_id=material_request_id, limit=limit, after_id=after_id,
        )
    except query_service.MaterialRequestReadError as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.get("/{material_request_id}/my-receiving/{shipment_id}/candidates", response_model=MyReceiptCandidateOut)
def read_my_receipt_candidates(
    material_request_id: UUID, shipment_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return my_receipt_candidates_service.my_receipt_candidates(
            db, actor=principal, request_id=material_request_id, shipment_id=shipment_id,
        )
    except query_service.MaterialRequestReadError as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.post("/{material_request_id}/my-receipts", response_model=MyReceiptOut, status_code=201)
def create_my_material_request_receipt(
    material_request_id: UUID, payload: MyReceiptIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "receive")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    _set_read_no_store(response)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = my_receipt_service.create_my_receipt(db, actor=principal, request_id=material_request_id,
            payload=payload, idempotency_key=key, secret=secret, trace_request_id=trace)
        output = MyReceiptOut.model_validate(result)
        db.commit()
    except query_service.MaterialRequestReadError as exc:
        db.rollback()
        _raise_service_error(exc, no_store=True)
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.get("/{material_request_id}/my-receipts/command-status", response_model=MyReceiptCommandStatusOut)
def my_material_request_receipt_command_status(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    key = _required_safe_header("Idempotency-Key", idempotency_key, minimum=16, maximum=128)
    _set_read_no_store(response)
    try:
        result = my_receipt_service.my_receipt_command_status(db, actor=principal, request_id=material_request_id,
            idempotency_key=key, secret=_require_lifecycle_idempotency_secret(runtime_settings))
        return MyReceiptCommandStatusOut(lookup_status="confirmed" if result is not None else "not_observed", command=result)
    except query_service.MaterialRequestReadError as exc:
        _raise_service_error(exc, no_store=True)
    except ValidationError:
        _raise_service_error(query_service.MaterialRequestReadError(
            "my_receipt_history_invalid", "service_unavailable", "原验收结果证据不完整，请保留原请求继续核验",
        ), no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)


@router.post("/{material_request_id}/receipts", response_model=ReceiptOut, status_code=201)
def create_formal_material_request_receipt(
    material_request_id: UUID, payload: ReceiptIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "fulfill")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = receipt_service.create_receipt(db, actor=principal, request_id=material_request_id,
            expected_version=payload.expected_request_version, receiver_person_id=payload.receiver_person_id,
            received_at=payload.received_at, lines=payload.lines, idempotency_key=key, secret=secret,
            trace_request_id=trace)
        output = ReceiptOut(**result); db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response); _set_replay_header(response, output.idempotency_replayed)
    return output

@router.get("/{material_request_id}/receipts", response_model=list[ReceiptOut])
def list_formal_material_request_receipts(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return [ReceiptOut(**row) for row in receipt_service.list_receipts(db, actor=principal, request_id=material_request_id)]
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/receipt-command-status", response_model=ReceiptCommandStatusOut)
def receipt_command_status(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db), runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """Read-only recovery probe for a timed-out receipt submission."""
    key = _required_safe_header("Idempotency-Key", idempotency_key, minimum=16, maximum=128)
    _set_read_no_store(response)
    try:
        secret = _require_lifecycle_idempotency_secret(runtime_settings)
        result = receipt_service.receipt_command_status(
            db, actor=principal, request_id=material_request_id,
            idempotency_key=key, secret=secret,
        )
        return ReceiptCommandStatusOut(
            lookup_status="confirmed" if result is not None else "not_observed",
            request_hash=None if result is None else result["request_hash"],
            command=None if result is None else ReceiptOut(**result["command"]),
        )
    except (receipt_service.ReceiptError, query_service.MaterialRequestReadError) as exc:
        _raise_service_error(exc, no_store=True)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True, no_store=True)
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.post("/{material_request_id}/inbound-orders", response_model=InboundOrderOut, status_code=201)
def create_formal_material_request_inbound_order(
    material_request_id: UUID, payload: InboundOrderIn, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "fulfill")),
    db: Session = Depends(get_db), request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    trace = _required_safe_header("X-Request-ID", request_id, minimum=8, maximum=160)
    try:
        output = InboundOrderOut(**inbound_service.create_inbound_order(db, actor=principal, request_id=material_request_id, expected_version=payload.expected_request_version, receipt_id=payload.receipt_id, target_location_id=payload.target_location_id, target_person_id=payload.target_person_id, trace_request_id=trace)); db.commit(); _set_read_no_store(response); return output
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}/inbound-orders", response_model=list[InboundOrderOut])
def list_formal_material_request_inbound_orders(
    material_request_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "read")),
    db: Session = Depends(get_db),
):
    _set_read_no_store(response)
    try:
        return [InboundOrderOut(**row) for row in inbound_service.list_inbound_orders(db, actor=principal, request_id=material_request_id)]
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.post("/{material_request_id}/inbound-orders/{inbound_order_id}/post", response_model=InboundPostingOut)
def post_formal_material_request_inbound_order(
    material_request_id: UUID, inbound_order_id: UUID, response: Response,
    principal: FormalPrincipal = Depends(require_permission("material_request", "fulfill")),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    key, trace = _required_write_headers(idempotency_key=idempotency_key, request_id=request_id)
    try:
        result = inbound_service.post_inbound_order(db, actor=principal, inbound_order_id=inbound_order_id, material_request_id=material_request_id, idempotency_key=key, request_id=trace)
        output = InboundPostingOut(**result); db.commit(); _set_read_no_store(response); _set_replay_header(response, output.replayed); return output
    except Exception as exc:
        _rollback_and_raise(db, exc)

@router.get("/{material_request_id}", response_model=MaterialRequestDetailOut)
def formal_material_request_detail(
    material_request_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "read")
    ),
    db: Session = Depends(get_db),
):
    try:
        output = query_service.material_request_detail(
            db,
            actor=principal,
            request_id=material_request_id,
        )
    except query_service.MaterialRequestReadError as exc:
        _raise_service_error(exc)
    except DBAPIError:
        db.rollback()
        _raise_database_unavailable(read_only=True)
    _set_read_no_store(response)
    return output


@router.get(
    "/{material_request_id}/editable-draft",
    response_model=edit_service.MaterialRequestEditableDraftOut,
)
def formal_material_request_editable_draft(
    material_request_id: UUID,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "update_draft")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
):
    """Return plaintext only to the exact current requester for active edit."""

    try:
        _require_write_runtime(runtime_settings, contact_cipher)
        output = edit_service.material_request_editable_draft(
            db,
            actor=principal,
            request_id=material_request_id,
            cipher=contact_cipher,
            kms_key_id=runtime_settings.material_request_contact_kms_key_id,
            mobile_hmac_secret=(
                runtime_settings.material_request_contact_mobile_hmac_secret
            ),
            mobile_hash_version=(
                runtime_settings.material_request_contact_mobile_hash_version
            ),
        )
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    return output


@router.post("", response_model=MaterialRequestCreateOut)
def create_formal_material_request(
    payload: MaterialRequestCreateIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "create")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_write_runtime(runtime_settings, contact_cipher)
        material_request_id = draft_service.derive_material_request_create_id(
            actor=principal,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
        )
        result = draft_service.create_material_request_draft(
            db,
            actor=principal,
            material_request_id=material_request_id,
            draft=_draft_input(
                payload,
                material_request_id=material_request_id,
                principal=principal,
                settings=runtime_settings,
                cipher=contact_cipher,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = MaterialRequestCreateOut(
            request_id=result.request_id,
            request_version=result.request_version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            states=result.state_axes,
            idempotency_replayed=result.idempotency_replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.put("/{material_request_id}", response_model=MaterialRequestMutationOut)
def replace_formal_material_request_draft(
    material_request_id: UUID,
    payload: MaterialRequestAmendIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "update_draft")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_write_runtime(runtime_settings, contact_cipher)
        result = draft_service.amend_material_request_draft(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_version=payload.expected_version,
            draft=_draft_input(
                payload,
                material_request_id=material_request_id,
                principal=principal,
                settings=runtime_settings,
                cipher=contact_cipher,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = MaterialRequestMutationOut(
            request_id=result.request_id,
            action="update",
            request_version=result.version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            approval_instance_id=None,
            approval_attempt_no=None,
            current_step_id=None,
            states=result.state_axes,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/submit",
    response_model=MaterialRequestMutationOut,
)
def submit_formal_material_request(
    material_request_id: UUID,
    payload: MaterialRequestSubmitIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "submit")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_write_runtime(runtime_settings, contact_cipher)
        result = draft_service.submit_material_request(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_version=payload.expected_version,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = MaterialRequestMutationOut(
            request_id=result.request_id,
            action="submit",
            request_version=result.version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            approval_instance_id=result.approval_instance_id,
            approval_attempt_no=result.approval_attempt_no,
            current_step_id=result.approval_step_ids[0],
            states=result.state_axes,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/withdraw",
    response_model=MaterialRequestMutationOut,
)
def withdraw_formal_material_request(
    material_request_id: UUID,
    payload: MaterialRequestWithdrawIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "withdraw")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = lifecycle_service.withdraw_material_request(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = _lifecycle_mutation_output(result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/cancel",
    response_model=MaterialRequestMutationOut,
)
def cancel_formal_material_request(
    material_request_id: UUID,
    payload: MaterialRequestCancelIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("material_request", "cancel")
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = lifecycle_service.cancel_material_request(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_version=payload.expected_version,
            cancellation=lifecycle_service.MaterialRequestCancelInput(
                reason=payload.reason,
                lines=tuple(
                    lifecycle_service.MaterialRequestCancellationLineInput(
                        request_line_id=row.request_line_id,
                        cancelled_qty=row.cancelled_qty,
                        reason=row.reason,
                    )
                    for row in payload.lines
                ),
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = _lifecycle_mutation_output(result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/approval-steps/{approval_step_id}/decision",
    response_model=MaterialRequestMutationOut,
)
def decide_formal_material_request_approval(
    material_request_id: UUID,
    approval_step_id: UUID,
    payload: MaterialRequestApprovalDecisionIn,
    response: Response,
    principal: FormalPrincipal = Depends(_require_internal_approval_permission),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_write_runtime(runtime_settings, contact_cipher)
        result = approval_service.decide_material_request_approval(
            db,
            actor=principal,
            material_request_id=material_request_id,
            approval_step_id=approval_step_id,
            expected_request_version=payload.expected_request_version,
            expected_step_version=payload.expected_step_version,
            decision=approval_service.MaterialRequestApprovalInput(
                action=payload.action,
                lines=_approval_lines(payload.lines),
                return_lines=_return_lines(payload.return_lines),
                comment=payload.comment,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = _approval_mutation_output(
            db,
            result=result,
            action=payload.action,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/approval-steps/{approval_step_id}/external-evidence",
    response_model=MaterialRequestMutationOut,
)
def register_formal_material_request_external_evidence(
    material_request_id: UUID,
    approval_step_id: UUID,
    payload: ExternalApprovalRegistrationIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission(
            "material_request",
            "register_external",
            field_code="approval_evidence",
        )
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_write_runtime(runtime_settings, contact_cipher)
        result = approval_service.register_external_approval_evidence(
            db,
            actor=principal,
            material_request_id=material_request_id,
            approval_step_id=approval_step_id,
            expected_request_version=payload.expected_request_version,
            expected_step_version=payload.expected_step_version,
            registration=approval_service.ExternalApprovalRegistrationInput(
                evidence_file_id=payload.evidence_file_id,
                external_approver_name=payload.external_approver_name,
                external_reference_no=payload.external_reference_no,
                external_decided_at=payload.external_decided_at,
                action=payload.action,
                lines=_approval_lines(payload.lines),
                return_lines=_return_lines(payload.return_lines),
                comment=payload.comment,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = MaterialRequestMutationOut(
            request_id=result.request_id,
            action="register_external_approval",
            request_version=result.request_version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            approval_instance_id=result.instance_id,
            approval_attempt_no=_approval_attempt_no(db, result.instance_id),
            current_step_id=result.step_id,
            states=result.state_axes,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/approval-steps/{approval_step_id}/external-evidence/"
    "{registration_id}/verification",
    response_model=MaterialRequestMutationOut,
)
def verify_formal_material_request_external_evidence(
    material_request_id: UUID,
    approval_step_id: UUID,
    registration_id: UUID,
    payload: ExternalApprovalVerificationIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission(
            "material_request",
            "verify_external",
            field_code="approval_evidence",
        )
    ),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    contact_cipher=Depends(get_material_request_contact_cipher),
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key,
        request_id=request_id,
    )
    try:
        secret = _require_write_runtime(runtime_settings, contact_cipher)
        result = approval_service.verify_external_approval_evidence(
            db,
            actor=principal,
            material_request_id=material_request_id,
            approval_step_id=approval_step_id,
            registration_id=registration_id,
            expected_request_version=payload.expected_request_version,
            expected_step_version=payload.expected_step_version,
            verification_decision=payload.decision,
            comment=payload.comment,
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = MaterialRequestMutationOut(
            request_id=result.request_id,
            action="verify_external_approval",
            request_version=result.request_version,
            revision_id=result.revision_id,
            revision_no=result.revision_no,
            approval_instance_id=result.instance_id,
            approval_attempt_no=_approval_attempt_no(db, result.instance_id),
            current_step_id=result.current_step_id,
            states=result.state_axes,
            idempotency_replayed=result.replayed,
        )
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/supply-tasks",
    response_model=MaterialRequestSupplyTaskMutationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_formal_supply_task(
    material_request_id: UUID,
    payload: SupplyTaskCreateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("supply_task", "manage")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key, request_id=request_id,
    )
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = supply_service.create_supply_task(
            db,
            actor=principal,
            material_request_id=material_request_id,
            expected_request_version=payload.expected_request_version,
            plan=supply_service.SupplyTaskCreateInput(
                request_line_id=payload.request_line_id,
                supply_type=payload.supply_type,
                reference_no=payload.reference_no,
                expected_qty=payload.expected_qty,
                expected_date=payload.expected_date,
                note=payload.note,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = _supply_mutation_output(result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


@router.post(
    "/{material_request_id}/supply-tasks/{supply_task_id}",
    response_model=MaterialRequestSupplyTaskMutationOut,
)
def update_formal_supply_task(
    material_request_id: UUID,
    supply_task_id: UUID,
    payload: SupplyTaskUpdateIn,
    response: Response,
    principal: FormalPrincipal = Depends(require_permission("supply_task", "manage")),
    db: Session = Depends(get_db),
    runtime_settings: Settings = Depends(get_settings),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
):
    checked_key, checked_request_id = _required_write_headers(
        idempotency_key=idempotency_key, request_id=request_id,
    )
    try:
        secret = _require_lifecycle_write_runtime(runtime_settings)
        result = supply_service.update_supply_task(
            db,
            actor=principal,
            material_request_id=material_request_id,
            supply_task_id=supply_task_id,
            expected_request_version=payload.expected_request_version,
            expected_task_version=payload.expected_task_version,
            update=supply_service.SupplyTaskUpdateInput(
                status=payload.status,
                reference_no=payload.reference_no,
                expected_date=payload.expected_date,
                comment=payload.comment,
            ),
            idempotency_key=checked_key,
            idempotency_hmac_secret=secret,
            trace_request_id=checked_request_id,
        )
        output = _supply_mutation_output(result)
        db.commit()
    except Exception as exc:
        _rollback_and_raise(db, exc)
    _set_read_no_store(response)
    _set_replay_header(response, output.idempotency_replayed)
    return output


def _supply_mutation_output(result) -> MaterialRequestSupplyTaskMutationOut:
    return MaterialRequestSupplyTaskMutationOut(
        request_id=result.request_id,
        action=result.action,
        request_version=result.request_version,
        revision_id=result.revision_id,
        revision_no=result.revision_no,
        approval_instance_id=result.approval_instance_id,
        approval_attempt_no=result.approval_attempt_no,
        current_step_id=result.current_step_id,
        states=result.state_axes,
        supply_task_id=result.supply_task_id,
        task_no=result.task_no,
        task_status=result.task_status,
        task_version=result.task_version,
        idempotency_replayed=result.replayed,
    )


def _draft_input(
    payload: MaterialRequestCreateIn | MaterialRequestAmendIn,
    *,
    material_request_id: UUID,
    principal: FormalPrincipal,
    settings: Settings,
    cipher: MaterialRequestContactCipher,
) -> draft_service.MaterialRequestDraftInput:
    envelope = protect_material_request_contact(
        cipher=cipher,
        kms_key_id=settings.material_request_contact_kms_key_id,
        mobile_hmac_secret=settings.material_request_contact_mobile_hmac_secret,
        mobile_hash_version=settings.material_request_contact_mobile_hash_version,
        request_id=material_request_id,
        requester_person_id=principal.person_id,
        name=payload.contact.name,
        mobile=payload.contact.mobile,
    )
    return draft_service.MaterialRequestDraftInput(
        work_order_id=payload.work_order_id,
        purpose=payload.purpose,
        urgency=payload.urgency,
        expected_date=payload.expected_date,
        address_snapshot=payload.address.model_dump(mode="python"),
        contact_envelope=envelope,
        contact_masked=draft_service.mask_material_request_contact(
            name=payload.contact.name,
            mobile=payload.contact.mobile,
        ),
        attachment_file_ids=payload.attachment_file_ids,
        lines=tuple(
            draft_service.MaterialRequestDraftLineInput(
                material_id=row.material_id,
                requested_qty=row.requested_qty,
                required_date=row.required_date,
                suggested_substitute_material_id=(
                    row.suggested_substitute_material_id
                ),
                note=row.note,
            )
            for row in payload.lines
        ),
        note=payload.note,
    )


def _approval_lines(rows) -> tuple[ApprovalLineDecision, ...]:
    return tuple(
        ApprovalLineDecision(
            request_line_id=row.request_line_id,
            approved_qty=row.approved_qty,
            reason=row.reason,
        )
        for row in rows
    )


def _return_lines(rows) -> tuple[ApprovalReturnInstruction, ...]:
    return tuple(
        ApprovalReturnInstruction(
            request_line_id=row.request_line_id,
            required_review_qty=row.requested_reapproval_qty,
            reason=row.reason,
        )
        for row in rows
    )


def _approval_mutation_output(
    db: Session,
    *,
    result: approval_service.ApprovalCommandResult,
    action: str,
) -> MaterialRequestMutationOut:
    return MaterialRequestMutationOut(
        request_id=result.request_id,
        action=action,
        request_version=result.request_version,
        revision_id=result.revision_id,
        revision_no=result.revision_no,
        approval_instance_id=result.instance_id,
        approval_attempt_no=_approval_attempt_no(db, result.instance_id),
        current_step_id=result.current_step_id,
        states=result.state_axes,
        idempotency_replayed=result.replayed,
    )


def _lifecycle_mutation_output(
    result: lifecycle_service.MaterialRequestLifecycleResult,
) -> MaterialRequestMutationOut:
    return MaterialRequestMutationOut(
        request_id=result.request_id,
        action=result.action,
        request_version=result.request_version,
        revision_id=result.revision_id,
        revision_no=result.revision_no,
        approval_instance_id=result.approval_instance_id,
        approval_attempt_no=result.approval_attempt_no,
        current_step_id=result.current_step_id,
        states={
            "request_status": result.request_status,
            **dict(result.state_axes),
        },
        idempotency_replayed=result.replayed,
    )


def _approval_attempt_no(db: Session, instance_id: UUID) -> int:
    row = db.get(ApprovalInstance, instance_id)
    value = getattr(row, "attempt_no", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _MaterialRequestAdapterError(
            "material_request_approval_instance_projection_invalid",
            "service_unavailable",
            "审批实例投影无效，本次操作未完成",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return value


def _require_write_runtime(
    settings: Settings,
    cipher: MaterialRequestContactCipher | None,
) -> str:
    if not settings.material_request_writes_enabled:
        raise _MaterialRequestAdapterError(
            "material_request_writes_disabled",
            "service_unavailable",
            "正式需求单写功能尚未启用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    secrets = (
        settings.material_request_idempotency_hmac_secret,
        settings.material_request_contact_mobile_hmac_secret,
    )
    if any(not _configured_secret(value) for value in secrets):
        raise _MaterialRequestAdapterError(
            "material_request_write_secrets_unavailable",
            "service_unavailable",
            "正式需求单写入密钥配置不可用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    other_secrets = tuple(
        value.strip()
        for value in (
            settings.jwt_secret,
            settings.identity_hash_secret,
            settings.auth_idempotency_hmac_secret,
            settings.auth_login_rate_limit_hmac_secret,
        )
        if value.strip()
    )
    checked = tuple(value.strip() for value in secrets)
    if len(set(checked)) != len(checked) or set(checked).intersection(other_secrets):
        raise _MaterialRequestAdapterError(
            "material_request_write_secrets_not_distinct",
            "service_unavailable",
            "正式需求单写入密钥未完成独立配置",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if (
        not settings.material_request_contact_kms_configuration_ready()
        or settings.material_request_contact_kms_key_id.strip()
        == settings.auth_idempotency_kms_key_id.strip()
    ):
        raise _MaterialRequestAdapterError(
            "material_request_contact_kms_unavailable",
            "service_unavailable",
            "联系人 KMS 加密配置不可用，本次操作未完成",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if cipher is None:
        raise _MaterialRequestAdapterError(
            "material_request_contact_kms_unavailable",
            "service_unavailable",
            "联系人 KMS 加密服务不可用，本次操作未完成",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        active_version = cipher.active_key_version()
    except Exception:
        raise _MaterialRequestAdapterError(
            "material_request_contact_kms_unavailable",
            "service_unavailable",
            "联系人 KMS 加密服务不可用，本次操作未完成",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from None
    if isinstance(active_version, bool) or not isinstance(active_version, int) or active_version < 1:
        raise _MaterialRequestAdapterError(
            "material_request_contact_kms_unavailable",
            "service_unavailable",
            "联系人 KMS 密钥版本不可用，本次操作未完成",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return checked[0]


def _require_lifecycle_write_runtime(settings: Settings) -> str:
    """Enable lifecycle writes without requiring contact decryption/KMS use."""

    if not settings.material_request_writes_enabled:
        raise _MaterialRequestAdapterError(
            "material_request_writes_disabled",
            "service_unavailable",
            "正式需求单写功能尚未启用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return _require_lifecycle_idempotency_secret(settings)


def _require_lifecycle_idempotency_secret(settings: Settings) -> str:
    """Read the shipment/lifecycle idempotency secret without enabling writes."""

    secret = settings.material_request_idempotency_hmac_secret.strip()
    if not _configured_secret(secret):
        raise _MaterialRequestAdapterError(
            "material_request_write_secrets_unavailable",
            "service_unavailable",
            "正式需求单写入密钥配置不可用",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    other_secrets = {
        value.strip()
        for value in (
            settings.material_request_contact_mobile_hmac_secret,
            settings.jwt_secret,
            settings.identity_hash_secret,
            settings.auth_idempotency_hmac_secret,
            settings.auth_login_rate_limit_hmac_secret,
        )
        if value.strip()
    }
    if secret in other_secrets:
        raise _MaterialRequestAdapterError(
            "material_request_write_secrets_not_distinct",
            "service_unavailable",
            "正式需求单写入密钥未完成独立配置",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return secret


def _configured_secret(value: str) -> bool:
    checked = value.strip()
    return len(checked) >= 32 and not any(
        marker in checked.lower() for marker in _PLACEHOLDERS
    )


def _required_write_headers(
    *, idempotency_key: str | None, request_id: str | None
) -> tuple[str, str]:
    return (
        _required_safe_header(
            "Idempotency-Key",
            idempotency_key,
            minimum=16,
            maximum=128,
        ),
        _required_safe_header(
            "X-Request-ID",
            request_id,
            minimum=8,
            maximum=160,
        ),
    )


def _required_safe_header(
    name: str,
    value: str | None,
    *,
    minimum: int,
    maximum: int,
) -> str:
    if (
        value is None
        or not minimum <= len(value) <= maximum
        or _SAFE_HEADER_VALUE.fullmatch(value) is None
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": f"{name.lower().replace('-', '_')}_invalid",
                "category": "invalid_request",
                "message": f"{name} 必须是 {minimum}-{maximum} 位安全字符",
            },
        )
    return value


def _rollback_and_raise(db: Session, exc: Exception) -> None:
    db.rollback()
    if isinstance(
        exc,
        (
            draft_service.MaterialRequestDraftError,
            edit_service.MaterialRequestEditableDraftError,
            approval_service.MaterialRequestApprovalError,
            allocation_service.MaterialRequestAllocationError,
            reservation_service.MaterialRequestReservationError,
            lifecycle_service.MaterialRequestLifecycleError,
            supply_service.MaterialRequestSupplyError,
            InventoryPostingError,
            _MaterialRequestAdapterError,
        ),
    ):
        _raise_service_error(exc)
    if isinstance(exc, MaterialRequestContactProtectionError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "material_request_contact_protection_unavailable",
                "category": "service_unavailable",
                "message": "联系人加密保护不可用，本次操作未完成",
            },
        ) from None
    if isinstance(exc, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "material_request_response_projection_invalid",
                "category": "service_unavailable",
                "message": "需求单响应投影无效，本次操作未完成",
            },
        ) from None
    if isinstance(exc, SQLAlchemyError):
        _raise_database_unavailable(read_only=False)
    raise exc


def _raise_database_unavailable(*, read_only: bool, no_store: bool = False) -> None:
    message = "需求单查询暂时不可用" if read_only else "数据库暂时不可用，本次需求单操作未完成"
    headers = (
        {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        }
        if no_store
        else None
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "material_request_database_unavailable",
            "category": "service_unavailable",
            "message": message,
        },
        headers=headers,
    ) from None


def _raise_service_error(exc: Any, *, no_store: bool = False) -> None:
    headers = None
    if no_store:
        headers = {
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        }
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
        headers=headers,
    ) from None


def _set_replay_header(response: Response, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"


__all__ = [
    "command_status_router",
    "get_material_request_contact_cipher",
    "install_formal_material_request_validation_exception_handler",
    "router",
]
