from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalPrincipal
from ..formal_services import provincial_role_assignment as provincial_roles
from ..schemas import (
    AccessContextOut,
    ProvincialManagerAssignmentOut,
    ProvincialManagerCandidateOut,
    ProvincialManagerGrantIn,
    ProvincialManagerMutationOut,
    ProvincialManagerRevokeIn,
    ProvincialRegionOptionOut,
)


router = APIRouter(prefix="/access", tags=["access"])
_SAFE_HEADER_VALUE = re.compile(r"^[A-Za-z0-9._:-]+$")


@router.get("/context", response_model=AccessContextOut)
def access_context(
    principal: FormalPrincipal = Depends(
        require_permission("access_context", "read")
    ),
):
    return AccessContextOut(
        person_id=str(principal.person_id),
        account_status=principal.account_status,
        employment_status=principal.employment_status,
        authorization_version=principal.authorization_version,
        access_mode=principal.access_mode,
        role_codes=list(principal.role_codes),
        assignments=[
            {
                "assignment_id": str(row.assignment_id),
                "role_code": row.role_code,
                "scope_type": row.scope_type,
                "scope_id": row.scope_id,
                "valid_from": row.valid_from,
                "valid_to": row.valid_to,
            }
            for row in principal.assignments
        ],
        permissions=[
            {
                "resource": resource,
                "action": action,
                "field_code": field_code,
            }
            for resource, action, field_code in principal.permission_keys()
        ],
    )


@router.get(
    "/provincial-managers/regions",
    response_model=list[ProvincialRegionOptionOut],
)
def provincial_manager_regions(
    principal: FormalPrincipal = Depends(
        require_permission("people", "read_minimal")
    ),
    db: Session = Depends(get_db),
):
    try:
        rows = provincial_roles.list_provincial_manager_regions(
            db,
            actor=principal,
        )
    except provincial_roles.ProvincialRoleAssignmentError as exc:
        _raise_role_assignment_http_error(exc)
    return [
        ProvincialRegionOptionOut(
            organization_id=row.organization_id,
            organization_code=row.organization_code,
            organization_name=row.organization_name,
            province_code=row.province_code,
        )
        for row in rows
    ]


@router.get(
    "/provincial-managers/candidates",
    response_model=list[ProvincialManagerCandidateOut],
)
def provincial_manager_candidates(
    organization_id: Annotated[UUID, Query()],
    principal: FormalPrincipal = Depends(
        require_permission("people", "read_minimal")
    ),
    db: Session = Depends(get_db),
):
    try:
        rows = provincial_roles.list_provincial_manager_candidates(
            db,
            actor=principal,
            organization_id=organization_id,
        )
    except provincial_roles.ProvincialRoleAssignmentError as exc:
        _raise_role_assignment_http_error(exc)
    return [
        ProvincialManagerCandidateOut(
            person_id=row.person_id,
            person_name=row.person_name,
            employee_no=row.employee_no,
            organization_id=row.organization_id,
            organization_code=row.organization_code,
            organization_name=row.organization_name,
            authorization_version=row.authorization_version,
        )
        for row in rows
    ]


@router.get(
    "/provincial-managers/assignments",
    response_model=list[ProvincialManagerAssignmentOut],
)
def provincial_manager_assignments(
    organization_id: Annotated[UUID, Query()],
    principal: FormalPrincipal = Depends(
        require_permission("people", "read_minimal")
    ),
    db: Session = Depends(get_db),
):
    try:
        rows = provincial_roles.list_provincial_manager_assignments(
            db,
            actor=principal,
            organization_id=organization_id,
        )
    except provincial_roles.ProvincialRoleAssignmentError as exc:
        _raise_role_assignment_http_error(exc)
    return [
        ProvincialManagerAssignmentOut(
            assignment_id=row.assignment_id,
            person_id=row.person_id,
            person_name=row.person_name,
            employee_no=row.employee_no,
            organization_id=row.organization_id,
            organization_code=row.organization_code,
            organization_name=row.organization_name,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            status=row.status,
            authorization_version=row.authorization_version,
        )
        for row in rows
    ]


@router.post(
    "/provincial-managers/assignments",
    response_model=ProvincialManagerMutationOut,
    status_code=status.HTTP_201_CREATED,
)
def grant_provincial_manager(
    payload: ProvincialManagerGrantIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("role_assignment", "manage_provincial")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key = _required_safe_header(
        "Idempotency-Key",
        idempotency_key,
        minimum=16,
        maximum=128,
    )
    checked_request_id = _required_safe_header(
        "X-Request-ID",
        request_id,
        minimum=8,
        maximum=160,
    )
    try:
        result = provincial_roles.grant_provincial_manager(
            db,
            actor=principal,
            target_person_id=payload.person_id,
            organization_id=payload.organization_id,
            expected_authorization_version=payload.expected_authorization_version,
            valid_to=payload.valid_to,
            reason=payload.reason,
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        db.commit()
    except provincial_roles.ProvincialRoleAssignmentError as exc:
        db.rollback()
        _raise_role_assignment_http_error(exc)
    except Exception:
        db.rollback()
        raise
    response.headers["Idempotency-Replayed"] = (
        "true" if result.replayed else "false"
    )
    return _mutation_output(result)


@router.post(
    "/provincial-managers/assignments/{assignment_id}/revoke",
    response_model=ProvincialManagerMutationOut,
)
def revoke_provincial_manager(
    assignment_id: UUID,
    payload: ProvincialManagerRevokeIn,
    response: Response,
    principal: FormalPrincipal = Depends(
        require_permission("role_assignment", "manage_provincial")
    ),
    db: Session = Depends(get_db),
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
    request_id: Annotated[
        str | None,
        Header(alias="X-Request-ID"),
    ] = None,
):
    checked_key = _required_safe_header(
        "Idempotency-Key",
        idempotency_key,
        minimum=16,
        maximum=128,
    )
    checked_request_id = _required_safe_header(
        "X-Request-ID",
        request_id,
        minimum=8,
        maximum=160,
    )
    try:
        result = provincial_roles.revoke_provincial_manager(
            db,
            actor=principal,
            assignment_id=assignment_id,
            expected_authorization_version=payload.expected_authorization_version,
            reason=payload.reason,
            idempotency_key=checked_key,
            request_id=checked_request_id,
        )
        db.commit()
    except provincial_roles.ProvincialRoleAssignmentError as exc:
        db.rollback()
        _raise_role_assignment_http_error(exc)
    except Exception:
        db.rollback()
        raise
    response.headers["Idempotency-Replayed"] = (
        "true" if result.replayed else "false"
    )
    return _mutation_output(result)


def _mutation_output(
    result: provincial_roles.ProvincialRoleAssignmentResult,
) -> ProvincialManagerMutationOut:
    return ProvincialManagerMutationOut(
        assignment_id=result.assignment_id,
        person_id=result.target_person_id,
        organization_id=result.organization_id,
        role_code=result.role_code,
        scope_type=result.scope_type,
        status=result.status,
        valid_from=result.valid_from,
        valid_to=result.valid_to,
        authorization_version=result.authorization_version,
        audit_event_id=result.audit_event_id,
        state_transition_event_id=result.state_transition_event_id,
        replayed=result.replayed,
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
                "message": (
                    f"{name} 必须是 {minimum}-{maximum} 位安全字符"
                ),
            },
        )
    return value


def _raise_role_assignment_http_error(
    exc: provincial_roles.ProvincialRoleAssignmentError,
) -> None:
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from None
