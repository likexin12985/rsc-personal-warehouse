from __future__ import annotations

import json
import secrets
from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth_sessions import revoke_session
from ..database import get_db
from ..dependencies import client_ip, require_roles
from ..models import (
    AuthSession,
    ExternalSyncCurrentRecord,
    ExternalSyncSnapshot,
    OamPersonnelBinding,
    User,
)
from ..schemas import OamPersonnelEnableIn
from ..security import hash_password
from ..services import audit


router = APIRouter(prefix="/integrations/oam", tags=["oam-data"])
PROVINCE_ROLES = {"provincial_manager", "technician"}


def _latest_source(db: Session) -> tuple[str, str] | None:
    snapshot = db.scalar(
        select(ExternalSyncSnapshot)
        .where(
            ExternalSyncSnapshot.scope_key == "all",
            ExternalSyncSnapshot.status == "complete",
        )
        .order_by(
            ExternalSyncSnapshot.snapshot_at.desc(),
            ExternalSyncSnapshot.completed_at.desc(),
        )
        .limit(1)
    )
    return (snapshot.source_instance, snapshot.scope_key) if snapshot else None


def _load_current_payloads(db: Session, entity_type: str) -> list[dict]:
    source = _latest_source(db)
    if not source:
        return []
    rows = db.scalars(
        select(ExternalSyncCurrentRecord).where(
            ExternalSyncCurrentRecord.source_instance == source[0],
            ExternalSyncCurrentRecord.scope_key == source[1],
            ExternalSyncCurrentRecord.entity_type == entity_type,
        )
    )
    return [json.loads(row.payload_json) for row in rows]


def _binding_out(row: OamPersonnelBinding) -> dict:
    return {
        "id": row.id,
        "oamAccountId": row.oam_account_id,
        "oamEmployeeId": row.oam_employee_id,
        "account": row.account,
        "jobNo": row.job_no,
        "name": row.name,
        "mobile": row.mobile,
        "sourcePresent": row.source_present,
        "sourceActive": row.source_active,
        "loginEligible": row.login_eligible,
        "eligibilityReason": row.eligibility_reason,
        "loginEnabled": row.login_enabled,
        "user": None
        if row.user is None
        else {
            "id": row.user.id,
            "role": row.user.role,
            "province": row.user.province,
            "isActive": row.user.is_active,
        },
        "updatedAt": row.updated_at,
    }


@router.get("/personnel")
def list_oam_personnel(
    search: str = Query(default="", max_length=80),
    state: str = Query(default="all", pattern=r"^(all|eligible|enabled|blocked)$"),
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    rows = list(
        db.scalars(
            select(OamPersonnelBinding)
            .order_by(OamPersonnelBinding.source_active.desc(), OamPersonnelBinding.name)
        )
    )
    summary = {
        "total": len(rows),
        "sourceActive": sum(row.source_active for row in rows),
        "loginEligible": sum(row.login_eligible for row in rows),
        "loginEnabled": sum(row.login_enabled for row in rows),
    }
    term = search.strip().lower()
    if term:
        rows = [
            row
            for row in rows
            if term
            in " ".join(
                (row.name, row.mobile, row.account, row.job_no, row.oam_account_id)
            ).lower()
        ]
    if state == "eligible":
        rows = [row for row in rows if row.login_eligible and not row.login_enabled]
    elif state == "enabled":
        rows = [row for row in rows if row.login_enabled]
    elif state == "blocked":
        rows = [row for row in rows if not row.login_eligible]
    return {"summary": summary, "items": [_binding_out(row) for row in rows]}


@router.post("/personnel/{binding_id}/enable")
def enable_oam_personnel_login(
    binding_id: str,
    payload: OamPersonnelEnableIn,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    binding = db.get(OamPersonnelBinding, binding_id)
    if not binding:
        raise HTTPException(status_code=404, detail="OAM人员不存在")
    if not binding.source_present or not binding.source_active or not binding.login_eligible:
        raise HTTPException(status_code=409, detail=binding.eligibility_reason or "该人员当前不可开通")
    province = payload.province.strip() if payload.province else None
    if payload.role in PROVINCE_ROLES and not province:
        raise HTTPException(status_code=400, detail="该角色必须指定省份")
    conflicting_binding = db.scalar(
        select(OamPersonnelBinding).where(
            OamPersonnelBinding.id != binding.id,
            OamPersonnelBinding.mobile == binding.mobile,
            OamPersonnelBinding.login_enabled.is_(True),
        )
    )
    if conflicting_binding:
        raise HTTPException(status_code=409, detail="该手机号已绑定其他OAM人员")

    user = db.get(User, binding.user_id) if binding.user_id else None
    mobile_user = db.scalar(select(User).where(User.mobile == binding.mobile))
    if user and mobile_user and mobile_user.id != user.id:
        raise HTTPException(status_code=409, detail="该手机号已属于其他系统账号")
    if user is None:
        user = mobile_user
    if user and user.id == actor.id and payload.role != "admin":
        raise HTTPException(status_code=409, detail="不能降低当前管理员自己的角色")
    if user is None:
        user = User(
            mobile=binding.mobile,
            name=binding.name,
            password_hash=hash_password(secrets.token_urlsafe(48)),
            role=payload.role,
            province=province,
            is_active=True,
            require_password_change=False,
        )
        db.add(user)
        db.flush()
    else:
        linked_elsewhere = db.scalar(
            select(OamPersonnelBinding).where(
                OamPersonnelBinding.id != binding.id,
                OamPersonnelBinding.user_id == user.id,
            )
        )
        if linked_elsewhere:
            raise HTTPException(status_code=409, detail="该系统账号已绑定其他OAM人员")
        user.mobile = binding.mobile
        user.name = binding.name
        user.role = payload.role
        user.province = province
        user.is_active = True

    binding.user_id = user.id
    binding.login_enabled = True
    audit(
        db,
        actor=actor,
        action="oam.personnel_login_enabled",
        entity_type="oam_personnel_binding",
        entity_id=binding.id,
        detail={"userId": user.id, "role": user.role, "province": user.province},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(binding)
    return _binding_out(binding)


@router.post("/personnel/{binding_id}/disable")
def disable_oam_personnel_login(
    binding_id: str,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    binding = db.get(OamPersonnelBinding, binding_id)
    if not binding:
        raise HTTPException(status_code=404, detail="OAM人员不存在")
    binding.login_enabled = False
    user = db.get(User, binding.user_id) if binding.user_id else None
    if user and user.id == actor.id:
        raise HTTPException(status_code=409, detail="不能停用当前管理员自己的登录")
    if user:
        user.is_active = False
        for session in db.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        ):
            revoke_session(session, revoked_by_id=actor.id)
    audit(
        db,
        actor=actor,
        action="oam.personnel_login_disabled",
        entity_type="oam_personnel_binding",
        entity_id=binding.id,
        detail={"userId": binding.user_id},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(binding)
    return _binding_out(binding)


@router.get("/orders")
def list_oam_orders(
    search: str = Query(default="", max_length=100),
    status: str = Query(default="", max_length=40),
    order_type: int | None = Query(default=None, ge=1, le=9),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    actor: User = Depends(
        require_roles(
            "admin",
            "provincial_manager",
            "technician",
        )
    ),
    db: Session = Depends(get_db),
):
    orders = _load_current_payloads(db, "material_application")
    lines = _load_current_payloads(db, "material_application_line")
    if actor.role != "admin":
        binding = db.scalar(
            select(OamPersonnelBinding).where(
                OamPersonnelBinding.user_id == actor.id,
                OamPersonnelBinding.login_enabled.is_(True),
                OamPersonnelBinding.source_active.is_(True),
            )
        )
        if not binding:
            raise HTTPException(status_code=403, detail="当前账号尚未绑定有效OAM人员")
        identity_values = {binding.oam_account_id, binding.account}
        identity_values.discard("")
        orders = [
            row
            for row in orders
            if any(
                str(row.get(key) or "").strip() in identity_values
                for key in (
                    "applicantId",
                    "principalId",
                    "createAccountId",
                    "updateAccountId",
                )
            )
        ]
    visible_order_ids = {
        str(row.get("materialApplyId") or "").strip() for row in orders
    }
    lines = [
        row
        for row in lines
        if str(row.get("materialApplyId") or "").strip() in visible_order_ids
    ]
    status_counts = Counter(
        str(row.get("transferStatus") or row.get("status") or "unknown")
        for row in orders
    )
    lines_by_order: dict[str, list[dict]] = defaultdict(list)
    for line in lines:
        lines_by_order[str(line.get("materialApplyId") or "")].append(line)
    term = search.strip().lower()
    if term:
        orders = [
            row
            for row in orders
            if term
            in " ".join(
                str(row.get(key) or "")
                for key in (
                    "materialApplyId",
                    "applicantName",
                    "principalName",
                    "warehouseLocationName",
                    "warehousePositionName",
                    "info",
                )
            ).lower()
        ]
    if status:
        orders = [
            row
            for row in orders
            if str(row.get("transferStatus") or row.get("status") or "") == status
        ]
    if order_type is not None:
        orders = [row for row in orders if int(row.get("type") or 0) == order_type]
    orders.sort(key=lambda row: str(row.get("createTime") or ""), reverse=True)
    total = len(orders)
    items = []
    for order in orders[offset : offset + limit]:
        apply_id = str(order.get("materialApplyId") or "")
        items.append({**order, "lines": lines_by_order.get(apply_id, [])})
    return {
        "summary": {
            "total": sum(status_counts.values()),
            "active": sum(
                count
                for key, count in status_counts.items()
                if key not in {"finished", "invalided", "refused"}
            ),
            "statuses": dict(status_counts),
            "activeLines": len(lines),
        },
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }
