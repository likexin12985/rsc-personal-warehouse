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
from ..formal_services import material_request_command_status as command_status_service
from ..formal_services import material_request_draft as draft_service
from ..formal_services import material_request_edit as edit_service
from ..formal_services import material_request_lifecycle as lifecycle_service
from ..formal_services import material_request_query as query_service
from ..formal_services import material_request_supply as supply_service
from ..formal_services import material_request_supply_command_status as supply_status_service
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
            lifecycle_service.MaterialRequestLifecycleError,
            supply_service.MaterialRequestSupplyError,
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


def _raise_database_unavailable(*, read_only: bool) -> None:
    message = "需求单查询暂时不可用" if read_only else "数据库暂时不可用，本次需求单操作未完成"
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "material_request_database_unavailable",
            "category": "service_unavailable",
            "message": message,
        },
    ) from None


def _raise_service_error(exc: Any) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None


def _set_replay_header(response: Response, replayed: bool) -> None:
    response.headers["Idempotency-Replayed"] = "true" if replayed else "false"


__all__ = [
    "command_status_router",
    "get_material_request_contact_cipher",
    "install_formal_material_request_validation_exception_handler",
    "router",
]
