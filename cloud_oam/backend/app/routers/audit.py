from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..dependencies import require_roles
from ..models import AuditLog, User


router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    rows = list(
        db.scalars(
            select(AuditLog)
            .options(joinedload(AuditLog.actor))
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": row.id,
            "actor": row.actor.name if row.actor else "系统",
            "action": row.action,
            "entityType": row.entity_type,
            "entityId": row.entity_id,
            "detail": row.detail,
            "ipAddress": row.ip_address,
            "createdAt": row.created_at,
        }
        for row in rows
    ]
