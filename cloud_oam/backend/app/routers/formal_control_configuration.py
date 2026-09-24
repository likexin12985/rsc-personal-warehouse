"""Formal PC handoff boundary; no owner DSN or private control table access."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import require_permission
from ..formal_access import FormalAccessError, FormalPrincipal
from ..formal_services.audit_chain import AuditChainError
from ..inventory_control_authority import AuthorityCommand, ControlAuthorityError
from ..inventory_control_configuration import ControlConfigurationError
from ..inventory_control_handoff import ControlHandoffError, inspect_owner_response, issue_control_handoff

router = APIRouter(prefix='/v1/inventory-control/configuration', tags=['inventory-control-configuration'])


def install_validation_handler(app):
    previous = app.exception_handlers.get(RequestValidationError, request_validation_exception_handler)
    async def handler(request, exc):
        if request.url.path.startswith('/api/v1/inventory-control/configuration'):
            return JSONResponse(status_code=422, content={'detail': {'code': 'control_configuration_invalid_request',
                'message': '配置请求格式无效，请核对原命令和回执'}})
        return await previous(request, exc)
    app.add_exception_handler(RequestValidationError, handler)


class HandoffInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command: AuthorityCommand
    expected_authorization_version: int = Field(strict=True, ge=1)
    purpose: Literal['preview', 'execute', 'status']
    owner_response: dict | None = None


class InspectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command: AuthorityCommand
    expected_authorization_version: int = Field(strict=True, ge=1)
    owner_response: dict


def _token(request):
    value = request.headers.get('authorization', '')
    scheme, _, content = value.partition(' ')
    return content.strip() if scheme.lower() == 'bearer' and content.strip() else request.cookies.get('access_token', '')


def _error(exc):
    value = str(exc)
    if value in ('control_handoff_disabled', 'control_handoff_unconfigured'):
        status, message = 503, '配置交接服务尚未就绪'
    elif isinstance(exc, FormalAccessError) or value.endswith(('authentication_required', 'operator_forbidden', 'authorization_changed', 'actor_changed')):
        status, message = 403, '当前登录或总部配置权限已变化，请重新核查'
    else:
        status, message = 409, '配置命令或回执未通过核验，请保留原请求并重新核查'
    # Do not echo uploaded command content, keys, credentials, or SQL errors.
    raise HTTPException(status_code=status, detail={'code': 'control_configuration_unavailable' if status == 503 else 'control_configuration_verification_failed', 'message': message}) from None


@router.post('/handoffs')
def create_handoff(payload: HandoffInput, request: Request, response: Response,
                   principal: FormalPrincipal = Depends(require_permission('inventory_control', 'authorize')),
                   db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        result = issue_control_handoff(db, access_token=_token(request), actor=principal,
            expected_version=payload.expected_authorization_version, command=payload.command,
            purpose=payload.purpose, owner_response=payload.owner_response)
        db.commit()
        return {'handoff': result, 'decision_recorded': False, 'projection_published': False, 'start_ready': False}
    except (ControlHandoffError, ControlConfigurationError, ControlAuthorityError, FormalAccessError, AuditChainError) as exc:
        db.rollback(); _error(exc)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail={'code':'control_configuration_issue_unknown',
            'message':'交接包签发结果未确认；此接口不执行配置决策'}) from None


@router.post('/inspect-response')
def inspect_response(payload: InspectInput, request: Request, response: Response,
                     principal: FormalPrincipal = Depends(require_permission('inventory_control', 'authorize')),
                     db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        result = inspect_owner_response(db, access_token=_token(request), actor=principal,
            expected_version=payload.expected_authorization_version, command=payload.command, envelope=payload.owner_response)
        db.rollback()
        return result
    except (ControlHandoffError, ControlConfigurationError, ControlAuthorityError, FormalAccessError, AuditChainError) as exc:
        db.rollback(); _error(exc)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=503, detail={'code':'control_configuration_response_unavailable',
            'message':'回执核验暂不可用，请保留原请求'}) from None
