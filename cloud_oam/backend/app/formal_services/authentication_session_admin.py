"""Formal administrator revocation of one authentication session family.

The service re-authorizes the supplied formal principal, locks exactly one
session, revokes every unfinished refresh token through the shared session
lifecycle, and records both authentication audit and idempotent state evidence
in the caller's transaction.  It never commits and never exposes the target
user, device address, mobile identity or token material.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_sessions import SessionError, revoke_session_family
from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..models import AuthSession
from .audit_chain import AuditChainError
from .authentication_audit import (
    AuthenticationEvidenceError,
    append_authentication_event,
)


OPERATION = "admin_force_revoke_session"
AGGREGATE_TYPE = "auth_session"
PUBLIC_FORBIDDEN_MESSAGE = "仅当前有效的蔚来总部全国管理员可强制下线会话"
PUBLIC_CONFLICT_MESSAGE = "会话状态已变化，请重新读取后再操作"
PUBLIC_SERVICE_MESSAGE = "会话下线审计不可用，操作未执行"

_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{16,128}$", re.ASCII)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{7,159}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class AuthenticationSessionAdminError(RuntimeError):
    """Stable non-sensitive domain failure for a router boundary."""

    def __init__(self, *, code: str, category: str, public_message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported session-admin category: {category}")
        super().__init__(public_message)
        self.code = code
        self.category = category
        self.public_message = public_message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.public_message,
        }


@dataclass(frozen=True, slots=True)
class AuthenticationSessionRevocationResult:
    session_id: str
    status: str
    revoked_at: datetime
    audit_event_id: uuid.UUID
    state_transition_event_id: uuid.UUID
    replayed: bool = False


def force_revoke_session(
    db: Session,
    *,
    actor: FormalPrincipal,
    session_id: str | uuid.UUID,
    idempotency_key: str,
    request_id: str,
) -> AuthenticationSessionRevocationResult:
    """Revoke one active formal session and its unfinished refresh family."""

    effective_at = datetime.now(timezone.utc)
    current_actor = _require_current_hq_national_admin(db, actor, effective_at)
    checked_session_id = _require_session_id(session_id)
    checked_raw_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    storage_key = idempotency_storage_key(current_actor.user_id, checked_raw_key)
    request_hash = _request_hash(
        {
            "operation": OPERATION,
            "actor_user_id": current_actor.user_id,
            "session_id": checked_session_id,
        }
    )

    replay = _load_idempotent_result(
        db,
        actor_user_id=current_actor.user_id,
        storage_key=storage_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replace(replay, replayed=True)

    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == checked_session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if auth_session is None:
        _fail("session_not_found", "not_found", "会话不存在")
    # A same-key peer may have committed while this request waited for the
    # target session row lock.  Re-read the idempotency evidence after acquiring
    # that lock and before evaluating the now-revoked precondition, otherwise a
    # legitimate concurrent replay is incorrectly returned as 412.
    replay = _load_idempotent_result(
        db,
        actor_user_id=current_actor.user_id,
        storage_key=storage_key,
        request_hash=request_hash,
    )
    if replay is not None:
        return replace(replay, replayed=True)
    if auth_session.revoked_at is not None:
        _fail(
            "session_already_revoked",
            "precondition_failed",
            PUBLIC_CONFLICT_MESSAGE,
        )
    if _as_utc(auth_session.expires_at) <= effective_at:
        _fail("session_expired", "precondition_failed", PUBLIC_CONFLICT_MESSAGE)

    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            revoked_session = revoke_session_family(
                db,
                auth_session,
                revoked_by_id=current_actor.user_id,
            )
            if revoked_session.revoked_at is None:
                _fail(
                    "session_revocation_incomplete",
                    "service_unavailable",
                    PUBLIC_SERVICE_MESSAGE,
                )
            revoked_at = _as_utc(revoked_session.revoked_at)
            audit = append_authentication_event(
                db,
                actor_user_id=current_actor.user_id,
                action="authentication.session.admin_revoked",
                aggregate_type=AGGREGATE_TYPE,
                aggregate_id=revoked_session.id,
                request_id=checked_request_id,
                client_type=revoked_session.client_type,
                outcome="revoked",
                reason_code="admin_force_logout",
                occurred_at=effective_at,
                before_status="active",
                after_status="revoked",
            )
            transition_id = uuid.uuid4()
            result = AuthenticationSessionRevocationResult(
                session_id=revoked_session.id,
                status="revoked",
                revoked_at=revoked_at,
                audit_event_id=audit.id,
                state_transition_event_id=transition_id,
            )
            db.add(
                _transition_event(
                    event_id=transition_id,
                    result=result,
                    actor_user_id=current_actor.user_id,
                    storage_key=storage_key,
                    request_hash=request_hash,
                    occurred_at=effective_at,
                )
            )
            db.flush()
        return result
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        _fail(
            "authentication_audit_unavailable",
            "service_unavailable",
            PUBLIC_SERVICE_MESSAGE,
            cause=exc,
        )
    except SessionError as exc:
        _fail(
            "session_concurrent_conflict",
            "conflict",
            PUBLIC_CONFLICT_MESSAGE,
            cause=exc,
        )
    except IntegrityError as exc:
        replay = _load_idempotent_result(
            db,
            actor_user_id=current_actor.user_id,
            storage_key=storage_key,
            request_hash=request_hash,
        )
        if replay is not None:
            return replace(replay, replayed=True)
        _fail(
            "session_concurrent_conflict",
            "conflict",
            PUBLIC_CONFLICT_MESSAGE,
            cause=exc,
        )


def idempotency_storage_key(actor_user_id: str, raw_key: str) -> str:
    """Return the actor-scoped digest that is the only stored key form."""

    document = json.dumps(
        ["formal-auth-session-admin-idempotency-v1", actor_user_id, raw_key],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _require_current_hq_national_admin(
    db: Session,
    supplied: FormalPrincipal,
    now: datetime,
) -> FormalPrincipal:
    if not isinstance(supplied, FormalPrincipal):
        _fail("actor_principal_required", "forbidden", PUBLIC_FORBIDDEN_MESSAGE)
    if (
        supplied.access_mode != "active"
        or supplied.account_status != "active"
        or supplied.employment_status != "active"
        or not _has_national_admin_assignment(supplied)
    ):
        _fail("actor_admin_required", "forbidden", PUBLIC_FORBIDDEN_MESSAGE)
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "actor_not_current",
            "forbidden",
            PUBLIC_FORBIDDEN_MESSAGE,
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "actor_principal_stale",
            "precondition_failed",
            "管理员权限版本已变化，请重新读取权限上下文",
        )
    if (
        current.access_mode != "active"
        or current.account_status != "active"
        or current.employment_status != "active"
        or not _has_national_admin_assignment(current)
    ):
        _fail("actor_admin_required", "forbidden", PUBLIC_FORBIDDEN_MESSAGE)
    if not current.allows(db, "auth_session", "manage"):
        _fail("actor_permission_required", "forbidden", PUBLIC_FORBIDDEN_MESSAGE)
    return current


def _has_national_admin_assignment(principal: FormalPrincipal) -> bool:
    return any(
        assignment.role_code == "admin"
        and assignment.scope_type == "national"
        and assignment.scope_id == "*"
        for assignment in principal.assignments
    )


def _transition_event(
    *,
    event_id: uuid.UUID,
    result: AuthenticationSessionRevocationResult,
    actor_user_id: str,
    storage_key: str,
    request_hash: str,
    occurred_at: datetime,
) -> StateTransitionEvent:
    return StateTransitionEvent(
        id=event_id,
        aggregate_type=AGGREGATE_TYPE,
        aggregate_id=result.session_id,
        from_status="active",
        to_status="revoked",
        reason="admin_force_logout",
        actor_id=actor_user_id,
        idempotency_key=storage_key,
        occurred_at=occurred_at,
        metadata_jsonb={
            "operation": OPERATION,
            "request_hash": request_hash,
            "result": _result_to_json(result),
        },
    )


def _load_idempotent_result(
    db: Session,
    *,
    actor_user_id: str,
    storage_key: str,
    request_hash: str,
) -> AuthenticationSessionRevocationResult | None:
    event = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.idempotency_key == storage_key
        )
    )
    if event is None:
        return None
    metadata = event.metadata_jsonb
    if (
        event.aggregate_type != AGGREGATE_TYPE
        or event.from_status != "active"
        or event.to_status != "revoked"
        or event.actor_id != actor_user_id
        or not isinstance(metadata, dict)
        or metadata.get("operation") != OPERATION
        or metadata.get("request_hash") != request_hash
    ):
        _fail(
            "idempotency_key_conflict",
            "conflict",
            "该幂等键已用于不同的会话操作",
        )
    try:
        result = _result_from_json(metadata["result"])
    except (KeyError, TypeError, ValueError) as exc:
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "会话幂等记录不完整，已失败关闭",
            cause=exc,
        )
    if (
        result.session_id != event.aggregate_id
        or result.state_transition_event_id != event.id
    ):
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "会话幂等记录与状态事件不一致，已失败关闭",
        )
    audit = db.get(AuditEvent, result.audit_event_id)
    if (
        audit is None
        or audit.aggregate_type != AGGREGATE_TYPE
        or audit.aggregate_id != result.session_id
        or audit.actor_user_id != actor_user_id
        or audit.action != "authentication.session.admin_revoked"
    ):
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "会话幂等记录缺少认证审计，已失败关闭",
        )
    return result


def _result_to_json(result: AuthenticationSessionRevocationResult) -> dict[str, str]:
    return {
        "session_id": result.session_id,
        "status": result.status,
        "revoked_at": _canonical_timestamp(result.revoked_at),
        "audit_event_id": str(result.audit_event_id),
        "state_transition_event_id": str(result.state_transition_event_id),
    }


def _result_from_json(value: object) -> AuthenticationSessionRevocationResult:
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    if set(value) != {
        "session_id",
        "status",
        "revoked_at",
        "audit_event_id",
        "state_transition_event_id",
    }:
        raise ValueError("result fields do not match the reviewed schema")
    session_id = _require_session_id(value["session_id"])
    if value["status"] != "revoked":
        raise ValueError("result status is invalid")
    revoked_at = _parse_timestamp(value["revoked_at"])
    audit_event_id = uuid.UUID(str(value["audit_event_id"]))
    state_event_id = uuid.UUID(str(value["state_transition_event_id"]))
    return AuthenticationSessionRevocationResult(
        session_id=session_id,
        status="revoked",
        revoked_at=revoked_at,
        audit_event_id=audit_event_id,
        state_transition_event_id=state_event_id,
    )


def _request_hash(document: dict[str, str]) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _require_session_id(value: object) -> str:
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        _fail("invalid_session_id", "invalid_request", "会话标识无效")


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _SAFE_IDEMPOTENCY_KEY.fullmatch(value) is None:
        _fail("invalid_idempotency_key", "invalid_request", "幂等键无效")
    return value


def _require_request_id(value: object) -> str:
    if not isinstance(value, str) or _SAFE_REQUEST_ID.fullmatch(value) is None:
        _fail("invalid_request_id", "invalid_request", "请求标识无效")
    return value


def _ensure_outer_transaction(db: Session) -> None:
    connection = db.connection()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail(
    code: str,
    category: str,
    public_message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = AuthenticationSessionAdminError(
        code=code,
        category=category,
        public_message=public_message,
    )
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "AuthenticationSessionAdminError",
    "AuthenticationSessionRevocationResult",
    "force_revoke_session",
    "idempotency_storage_key",
]
