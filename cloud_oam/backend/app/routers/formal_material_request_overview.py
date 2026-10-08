"""Read-only current demand cohort report; no contact or storage dependency."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import FormalPrincipal
from app.formal_services.material_request_overview import material_request_overview
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_overview_schemas import MaterialRequestOverviewOut

router = APIRouter(prefix='/v1/reports/material-requests', tags=['formal-reports'])
HEADERS = {'Cache-Control': 'private, no-store, max-age=0', 'Pragma': 'no-cache',
           'Referrer-Policy': 'no-referrer', 'X-Content-Type-Options': 'nosniff'}
_BACKEND_REPORT_ROLES = frozenset({'admin', 'provincial_manager'})


def _require_backend_report(principal: FormalPrincipal) -> None:
    roles = getattr(principal, 'role_codes', None)
    if roles is not None and not set(roles).intersection(_BACKEND_REPORT_ROLES):
        raise HTTPException(
            status_code=403,
            detail={
                'code': 'report_forbidden',
                'category': 'forbidden',
                'message': '当前账号没有后台需求概览权限',
            },
            headers=HEADERS,
        )


@router.get('', response_model=MaterialRequestOverviewOut)
def read_overview(
    response: Response, organization_id: UUID | None = None,
    created_from: AwareDatetime | None = None, created_before: AwareDatetime | None = None,
    principal: FormalPrincipal = Depends(get_formal_principal),
    db: Session = Depends(get_db),
):
    response.headers.update(HEADERS)
    _require_backend_report(principal)
    try:
        output = material_request_overview(db, actor=principal, organization_id=organization_id,
            created_from=created_from, created_before=created_before)
    except MaterialRequestReadError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail={
            'code': error.code, 'category': error.category, 'message': str(error),
        }, headers=HEADERS) from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail={
            'code': 'material_request_overview_unavailable',
            'message': '需求概览暂时无法读取，请稍后刷新',
        }, headers=HEADERS) from None
    db.rollback()
    return output
