from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuthSession, ExternalSyncSnapshot, OamPersonnelBinding, User


MOBILE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


class OamDirectoryError(RuntimeError):
    pass


def _as_text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _source_is_active(data: dict[str, Any]) -> bool:
    return _as_text(data.get("status")) == "1" and _as_text(data.get("isDelete")) in {
        "",
        "0",
        "False",
        "false",
    }


def _eligibility(data: dict[str, Any]) -> tuple[bool, str]:
    if not _source_is_active(data):
        return False, "OAM账号已停用或删除"
    mobile = _as_text(data.get("mobile"))
    if not MOBILE_PATTERN.fullmatch(mobile):
        return False, "OAM手机号缺失或格式不正确"
    if not _as_text(data.get("accountId")):
        return False, "OAM账号标识缺失"
    return True, "可由管理员开通"


def _revoke_user_sessions(db: Session, user_id: str) -> None:
    now = datetime.now(timezone.utc)
    for session in db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ):
        session.revoked_at = now


def reconcile_personnel_snapshot(
    db: Session,
    *,
    snapshot: ExternalSyncSnapshot,
    final_state: dict[str, dict[str, Any]],
) -> dict[str, int]:
    parsed: list[dict[str, Any]] = []
    account_ids: set[str] = set()
    eligible_mobiles: dict[str, str] = {}
    for state in final_state.values():
        try:
            data = json.loads(state["payload_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise OamDirectoryError("OAM人员快照包含无效记录") from exc
        account_id = _as_text(data.get("accountId"))
        if not account_id:
            raise OamDirectoryError("OAM人员快照存在缺少账号标识的记录")
        if account_id in account_ids:
            raise OamDirectoryError(f"OAM人员账号标识重复：{account_id}")
        account_ids.add(account_id)
        eligible, reason = _eligibility(data)
        mobile = _as_text(data.get("mobile"))
        if eligible:
            previous_account = eligible_mobiles.get(mobile)
            if previous_account and previous_account != account_id:
                raise OamDirectoryError(f"OAM在职人员手机号重复：{mobile}")
            eligible_mobiles[mobile] = account_id
        parsed.append(
            {
                "data": data,
                "account_id": account_id,
                "mobile": mobile,
                "eligible": eligible,
                "reason": reason,
            }
        )

    existing = list(
        db.scalars(
            select(OamPersonnelBinding).where(
                OamPersonnelBinding.source_instance == snapshot.source_instance
            )
        )
    )
    by_account = {row.oam_account_id: row for row in existing}
    seen: set[str] = set()
    disabled_users: set[str] = set()
    for item in parsed:
        data = item["data"]
        account_id = item["account_id"]
        binding = by_account.get(account_id)
        if binding is None:
            binding = OamPersonnelBinding(
                source_instance=snapshot.source_instance,
                oam_account_id=account_id,
                name=_as_text(data.get("name")) or account_id,
                last_seen_snapshot_id=snapshot.id,
            )
            db.add(binding)
        seen.add(account_id)
        binding.oam_employee_id = _as_text(data.get("employeeId"))
        binding.account = _as_text(data.get("account"))
        binding.job_no = _as_text(data.get("jobNo"))
        binding.name = _as_text(data.get("name")) or account_id
        binding.mobile = item["mobile"]
        binding.oam_status = _as_text(data.get("status"))
        binding.source_present = True
        binding.source_active = _source_is_active(data)
        binding.login_eligible = item["eligible"]
        binding.eligibility_reason = item["reason"]
        binding.last_seen_snapshot_id = snapshot.id

        user = db.get(User, binding.user_id) if binding.user_id else None
        binding_valid = bool(
            binding.login_enabled
            and binding.login_eligible
            and user
            and user.mobile == binding.mobile
        )
        if user and not binding_valid:
            user.is_active = False
            disabled_users.add(user.id)
            _revoke_user_sessions(db, user.id)
        elif user and binding_valid:
            user.is_active = True
            user.name = binding.name

    for binding in existing:
        if binding.oam_account_id in seen:
            continue
        binding.source_present = False
        binding.source_active = False
        binding.login_eligible = False
        binding.eligibility_reason = "最新OAM人员目录中已不存在"
        if binding.user_id:
            user = db.get(User, binding.user_id)
            if user:
                user.is_active = False
                disabled_users.add(user.id)
                _revoke_user_sessions(db, user.id)

    return {
        "records": len(parsed),
        "eligible": len(eligible_mobiles),
        "disabledUsers": len(disabled_users),
    }
