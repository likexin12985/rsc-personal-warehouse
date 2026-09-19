"""Operator-facing notification delivery records and explicit retry commands.

The provider worker owns the external send boundary.  This module only exposes
redacted delivery evidence to a nationally scoped administrator and requeues a
single *definitively* retryable failure.  A missing response code is treated as
an unknown provider outcome and can never be replayed here.

Retry commands are recorded in the existing ``material_request`` audit stream.
The stream head is locked before the delivery row, so the audit row is the
idempotency fence shared by concurrent operators.  The raw idempotency key and
recipient key are never persisted or returned.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal
from ..foundation_models import (
    AuditEvent,
    NotificationAttempt,
    NotificationDelivery,
    NotificationEvent,
    NotificationRecipient,
)
from .audit_chain import (
    AuditChainError,
    append_audit_event,
    lock_audit_chain_head,
)
from .notification_delivery import (
    NotificationDeliveryError,
    retry_failed_notification_delivery,
)


NotificationStatus = Literal[
    "queued",
    "sending",
    "sent",
    "delivered",
    "read",
    "failed",
    "cancelled",
]
NotificationChannel = Literal["wechat", "sms", "feishu"]

_STATUSES = frozenset(
    {"queued", "sending", "sent", "delivered", "read", "failed", "cancelled"}
)
_CHANNELS = frozenset({"wechat", "sms", "feishu"})
_SAFE_HEADER = re.compile(r"^[A-Za-z0-9._:-]+$", re.ASCII)
_MAX_ATTEMPTS = 5
NOTIFICATION_AUDIT_STREAM_KEY = "material_request"
NOTIFICATION_RETRY_ACTION = "notification_delivery.retry"


class NotificationDeliveryOperationsError(RuntimeError):
    """A delivery operator query/command failed closed."""

    _STATUS_BY_CATEGORY = {
        "invalid_request": 400,
        "forbidden": 403,
        "not_found": 404,
        "conflict": 409,
        "precondition_failed": 412,
        "service_unavailable": 503,
    }

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in self._STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported notification operation category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.http_status_code = self._STATUS_BY_CATEGORY[category]

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class NotificationDeliveryRecord:
    delivery_id: UUID
    event_id: UUID
    event_type: str
    business_type: str
    business_id: str
    recipient_user_id: str | None
    channel: str
    status: str
    attempts: int
    provider_message_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    read_at: datetime | None
    latest_attempt_no: int | None
    latest_response_code: str | None
    latest_error: str | None
    latest_attempted_at: datetime | None
    retryable: bool


@dataclass(frozen=True, slots=True)
class NotificationDeliveryPage:
    items: tuple[NotificationDeliveryRecord, ...]
    next_after_id: UUID | None


@dataclass(frozen=True, slots=True)
class NotificationDeliveryRetryResult:
    delivery_id: UUID
    retry_attempt_no: int
    status: Literal["queued"]
    queued_at: datetime
    replayed: bool


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_operator(db: Session, actor: FormalPrincipal, action: str) -> None:
    """Re-check the national operator boundary inside the service.

    Routes already use ``require_permission``.  Keeping this check here makes
    direct service callers and future background entry points fail closed too.
    Test doubles which only expose ``allows`` remain supported, while a real
    formal principal must carry the explicit national ``admin`` assignment.
    """

    user_id = getattr(actor, "user_id", None)
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 36:
        raise NotificationDeliveryOperationsError(
            "notification_operator_invalid",
            "forbidden",
            "当前账号没有有效的通知运维身份",
        )
    allows = getattr(actor, "allows", None)
    if callable(allows) and not allows(db, "notification_delivery", action):
        raise NotificationDeliveryOperationsError(
            "notification_delivery_permission_denied",
            "forbidden",
            "当前账号没有通知投递运维权限",
        )

    # A populated real principal must be a national admin.  Do not infer a
    # national scope from a missing/empty assignment; that is an authorization
    # failure.  Minimal test doubles intentionally omit these attributes.
    assignments = getattr(actor, "assignments", None)
    role_codes = getattr(actor, "role_codes", None)
    if assignments is not None:
        role_codes_set = set(role_codes or ())
        national_admin = any(
            getattr(row, "role_code", None) == "admin"
            and getattr(row, "scope_type", None) == "national"
            and getattr(row, "scope_id", None) == "*"
            for row in tuple(assignments)
        )
        if "admin" not in role_codes_set or not national_admin:
            raise NotificationDeliveryOperationsError(
                "notification_delivery_national_scope_required",
                "forbidden",
                "通知投递运维仅允许总部管理员查询或重试",
            )
    elif role_codes is not None and "admin" not in set(role_codes):
        raise NotificationDeliveryOperationsError(
            "notification_delivery_national_scope_required",
            "forbidden",
            "通知投递运维仅允许总部管理员查询或重试",
        )


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise NotificationDeliveryOperationsError(
            "notification_delivery_limit_invalid",
            "invalid_request",
            "通知投递查询条数必须在1到100之间",
        )
    return limit


def _validate_filter(value: str | None, allowed: frozenset[str], name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in allowed:
        raise NotificationDeliveryOperationsError(
            f"notification_delivery_{name}_invalid",
            "invalid_request",
            f"通知投递{name}无效",
        )
    return value


def _latest_attempts(
    db: Session, delivery_ids: tuple[UUID, ...]
) -> dict[UUID, NotificationAttempt]:
    if not delivery_ids:
        return {}
    rows = tuple(
        db.scalars(
            select(NotificationAttempt)
            .where(NotificationAttempt.delivery_id.in_(delivery_ids))
            .order_by(NotificationAttempt.delivery_id, NotificationAttempt.attempt_no.desc())
        ).all()
    )
    latest: dict[UUID, NotificationAttempt] = {}
    for row in rows:
        latest.setdefault(row.delivery_id, row)
    return latest


def _is_retryable(delivery: NotificationDelivery, attempt: NotificationAttempt | None) -> bool:
    if (
        delivery.status != "failed"
        or attempt is None
        or attempt.attempt_no != delivery.attempts
        or not attempt.response_code
    ):
        return False
    try:
        response_code = int(attempt.response_code.strip())
    except (AttributeError, ValueError):
        return False
    return delivery.attempts < _MAX_ATTEMPTS and (
        response_code == 429 or 500 <= response_code <= 599
    )


def _record(
    delivery: NotificationDelivery,
    recipient: NotificationRecipient,
    event: NotificationEvent,
    attempt: NotificationAttempt | None,
) -> NotificationDeliveryRecord:
    retryable = _is_retryable(delivery, attempt)
    return NotificationDeliveryRecord(
        delivery_id=delivery.id,
        event_id=event.id,
        event_type=event.event_type,
        business_type=event.business_type,
        business_id=event.business_id,
        recipient_user_id=recipient.user_id,
        channel=recipient.channel,
        status=delivery.status,
        attempts=delivery.attempts,
        provider_message_id=delivery.provider_message_id,
        last_error=delivery.last_error,
        created_at=_utc(delivery.created_at) or _now(),
        updated_at=_utc(delivery.updated_at) or _now(),
        sent_at=_utc(delivery.sent_at),
        delivered_at=_utc(delivery.delivered_at),
        read_at=_utc(delivery.read_at),
        latest_attempt_no=attempt.attempt_no if attempt else None,
        latest_response_code=attempt.response_code if attempt else None,
        latest_error=attempt.error if attempt else None,
        latest_attempted_at=_utc(attempt.attempted_at) if attempt else None,
        retryable=retryable,
    )


def list_notification_delivery_records(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int = 50,
    after_id: UUID | None = None,
    status: str | None = None,
    channel: str | None = None,
) -> NotificationDeliveryPage:
    """List redacted delivery evidence for a national operator."""

    _require_operator(db, actor, "read")
    checked_limit = _validate_limit(limit)
    checked_status = _validate_filter(status, _STATUSES, "status")
    checked_channel = _validate_filter(channel, _CHANNELS, "channel")

    cursor: NotificationDelivery | None = None
    if after_id is not None:
        cursor = db.get(NotificationDelivery, after_id)
        if cursor is None:
            raise NotificationDeliveryOperationsError(
                "notification_delivery_cursor_not_found",
                "not_found",
                "通知投递分页游标不存在",
            )

    query = (
        select(NotificationDelivery, NotificationRecipient, NotificationEvent)
        .join(NotificationRecipient, NotificationRecipient.id == NotificationDelivery.recipient_id)
        .join(NotificationEvent, NotificationEvent.id == NotificationRecipient.event_id)
    )
    conditions = []
    if checked_status is not None:
        conditions.append(NotificationDelivery.status == checked_status)
    if checked_channel is not None:
        conditions.append(NotificationRecipient.channel == checked_channel)
    if cursor is not None:
        conditions.append(
            or_(
                NotificationDelivery.created_at < cursor.created_at,
                and_(
                    NotificationDelivery.created_at == cursor.created_at,
                    NotificationDelivery.id < cursor.id,
                ),
            )
        )
    if conditions:
        query = query.where(*conditions)
    rows = tuple(
        db.execute(
            query.order_by(
                NotificationDelivery.created_at.desc(),
                NotificationDelivery.id.desc(),
            ).limit(checked_limit + 1)
        ).all()
    )
    has_next = len(rows) > checked_limit
    page_rows = rows[:checked_limit]
    delivery_ids = tuple(delivery.id for delivery, _recipient, _event in page_rows)
    attempts = _latest_attempts(db, delivery_ids)
    items = tuple(
        _record(delivery, recipient, event, attempts.get(delivery.id))
        for delivery, recipient, event in page_rows
    )
    return NotificationDeliveryPage(
        items=items,
        next_after_id=items[-1].delivery_id if has_next and items else None,
    )


def _safe_header(name: str, value: str, *, minimum: int, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not minimum <= len(value) <= maximum
        or _SAFE_HEADER.fullmatch(value) is None
    ):
        raise NotificationDeliveryOperationsError(
            f"{name.lower().replace('-', '_')}_invalid",
            "invalid_request",
            f"{name}必须是{minimum}-{maximum}位安全字符",
        )
    return value


def _command_hash(
    *,
    delivery_id: UUID,
    expected_attempt_no: int,
    reason: str,
    actor_user_id: str,
    idempotency_key_hash: str,
) -> str:
    material = {
        "actor_user_id": actor_user_id,
        "delivery_id": str(delivery_id),
        "expected_attempt_no": expected_attempt_no,
        "idempotency_key_sha256": idempotency_key_hash,
        "reason": reason,
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _audit_after(event: AuditEvent) -> dict[str, Any]:
    value = event.after_jsonb
    return value if isinstance(value, dict) else {}


def _replay_result(event: AuditEvent) -> NotificationDeliveryRetryResult:
    after = _audit_after(event)
    attempt_no = after.get("retry_attempt_no")
    if isinstance(attempt_no, bool) or not isinstance(attempt_no, int) or attempt_no < 1:
        raise NotificationDeliveryOperationsError(
            "notification_retry_audit_invalid",
            "service_unavailable",
            "通知重试审计证据不完整",
        )
    if after.get("status") != "queued":
        raise NotificationDeliveryOperationsError(
            "notification_retry_audit_invalid",
            "service_unavailable",
            "通知重试审计状态不完整",
        )
    occurred_at = _utc(event.occurred_at)
    if occurred_at is None:
        raise NotificationDeliveryOperationsError(
            "notification_retry_audit_invalid",
            "service_unavailable",
            "通知重试审计时间不完整",
        )
    try:
        delivery_id = UUID(event.aggregate_id)
    except (TypeError, ValueError) as exc:
        raise NotificationDeliveryOperationsError(
            "notification_retry_audit_invalid",
            "service_unavailable",
            "通知重试审计坐标不完整",
        ) from exc
    return NotificationDeliveryRetryResult(
        delivery_id=delivery_id,
        retry_attempt_no=attempt_no,
        status="queued",
        queued_at=occurred_at,
        replayed=True,
    )


def _find_retry_audits(
    db: Session,
    *,
    actor_user_id: str,
    delivery_id: UUID,
    idempotency_key_hash: str,
    request_id: str,
) -> tuple[AuditEvent, ...]:
    rows = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.stream_key == NOTIFICATION_AUDIT_STREAM_KEY,
                AuditEvent.action == NOTIFICATION_RETRY_ACTION,
                AuditEvent.aggregate_type == "notification_delivery",
            )
            .order_by(AuditEvent.stream_version)
        ).all()
    )
    matching: list[AuditEvent] = []
    for row in rows:
        after = _audit_after(row)
        same_delivery = row.aggregate_id == str(delivery_id)
        same_key = (
            after.get("idempotency_key_sha256", after.get("idempotency_key_hash"))
            == idempotency_key_hash
        )
        same_request = row.request_id == request_id
        same_actor = row.actor_user_id == actor_user_id
        if same_actor and same_delivery and (same_key or same_request):
            matching.append(row)
        elif same_key or same_request:
            raise NotificationDeliveryOperationsError(
                "notification_retry_idempotency_conflict",
                "conflict",
                "幂等键或请求标识已用于其他通知投递重试",
            )
    return tuple(matching)


def retry_notification_delivery(
    db: Session,
    *,
    actor: FormalPrincipal,
    delivery_id: UUID,
    expected_attempt_no: int,
    reason: str,
    idempotency_key: str,
    request_id: str,
    now: datetime | None = None,
) -> NotificationDeliveryRetryResult:
    """Explicitly queue one retry with audit-backed idempotency.

    Only HTTP 429/5xx failures with matching attempt evidence are accepted by
    the lower-level delivery service.  No provider call is made here.
    """

    _require_operator(db, actor, "retry")
    if (
        isinstance(expected_attempt_no, bool)
        or not isinstance(expected_attempt_no, int)
        or expected_attempt_no < 1
        or expected_attempt_no > 100
    ):
        raise NotificationDeliveryOperationsError(
            "notification_retry_attempt_invalid",
            "invalid_request",
            "通知重试尝试次数无效",
        )
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
        raise NotificationDeliveryOperationsError(
            "notification_retry_reason_invalid",
            "invalid_request",
            "通知重试原因必须为1到500个字符",
        )
    checked_reason = reason.strip()
    checked_key = _safe_header("Idempotency-Key", idempotency_key, minimum=16, maximum=128)
    checked_request_id = _safe_header("X-Request-ID", request_id, minimum=8, maximum=160)
    key_hash = hashlib.sha256(checked_key.encode()).hexdigest()
    command_hash = _command_hash(
        delivery_id=delivery_id,
        expected_attempt_no=expected_attempt_no,
        reason=checked_reason,
        actor_user_id=actor.user_id,
        idempotency_key_hash=key_hash,
    )
    effective_at = _utc(now) or _now()

    try:
        # Serialize all retry idempotency decisions on the pre-seeded stream.
        lock_audit_chain_head(db, stream_key=NOTIFICATION_AUDIT_STREAM_KEY)
        existing = _find_retry_audits(
            db,
            actor_user_id=actor.user_id,
            delivery_id=delivery_id,
            idempotency_key_hash=key_hash,
            request_id=checked_request_id,
        )
        if existing:
            if len(existing) != 1:
                raise NotificationDeliveryOperationsError(
                    "notification_retry_audit_ambiguous",
                    "service_unavailable",
                    "通知重试幂等审计事实不唯一",
                )
            prior = existing[0]
            after = _audit_after(prior)
            if (
                after.get("command_hash", after.get("request_hash")) != command_hash
                or after.get("expected_attempt_no") != expected_attempt_no
                or after.get("reason") != checked_reason
            ):
                raise NotificationDeliveryOperationsError(
                    "notification_retry_idempotency_conflict",
                    "conflict",
                    "幂等键已用于不同的通知重试命令",
                )
            return _replay_result(prior)

        delivery = db.scalar(
            select(NotificationDelivery)
            .where(NotificationDelivery.id == delivery_id)
            .with_for_update()
        )
        if delivery is None:
            raise NotificationDeliveryOperationsError(
                "notification_delivery_not_found",
                "not_found",
                "通知投递记录不存在",
            )
        # The audit head lock serializes normal writers, but re-read the
        # idempotency fence after taking the delivery lock as well.  This
        # protects deployments whose audit index is added online and makes the
        # exact owner-lock order explicit for reviewers.
        existing_after_delivery_lock = _find_retry_audits(
            db,
            actor_user_id=actor.user_id,
            delivery_id=delivery_id,
            idempotency_key_hash=key_hash,
            request_id=checked_request_id,
        )
        if existing_after_delivery_lock:
            if len(existing_after_delivery_lock) != 1:
                raise NotificationDeliveryOperationsError(
                    "notification_retry_audit_ambiguous",
                    "service_unavailable",
                    "通知重试幂等审计事实不唯一",
                )
            prior = existing_after_delivery_lock[0]
            after = _audit_after(prior)
            if (
                after.get("command_hash", after.get("request_hash")) != command_hash
                or after.get("expected_attempt_no") != expected_attempt_no
                or after.get("reason") != checked_reason
            ):
                raise NotificationDeliveryOperationsError(
                    "notification_retry_idempotency_conflict",
                    "conflict",
                    "幂等键已用于不同的通知重试命令",
                )
            return _replay_result(prior)
        recipient = db.get(NotificationRecipient, delivery.recipient_id)
        event = db.get(NotificationEvent, recipient.event_id) if recipient else None
        if recipient is None or event is None:
            raise NotificationDeliveryOperationsError(
                "notification_delivery_evidence_missing",
                "service_unavailable",
                "通知投递关联事实不完整",
            )
        before_attempt = db.scalar(
            select(NotificationAttempt)
            .where(
                NotificationAttempt.delivery_id == delivery.id,
                NotificationAttempt.attempt_no == expected_attempt_no,
            )
        )
        before_json = {
            "status": delivery.status,
            "attempts": delivery.attempts,
            "latest_response_code": before_attempt.response_code if before_attempt else None,
        }
        try:
            queued = retry_failed_notification_delivery(
                db,
                delivery_id=delivery.id,
                expected_attempt_no=expected_attempt_no,
                now=effective_at,
                max_attempts=_MAX_ATTEMPTS,
            )
        except NotificationDeliveryError as exc:
            message = str(exc)
            if "does not exist" in message:
                code, category, text = (
                    "notification_delivery_not_found",
                    "not_found",
                    "通知投递记录不存在",
                )
            elif "attempt has changed" in message:
                code, category, text = (
                    "notification_retry_precondition_failed",
                    "precondition_failed",
                    "通知投递尝试次数已变化，请重新查询",
                )
            elif "unknown" in message:
                code, category, text = (
                    "notification_retry_outcome_unknown",
                    "conflict",
                    "通知投递结果未知，禁止自动重试",
                )
            else:
                code, category, text = (
                    "notification_retry_not_allowed",
                    "conflict",
                    "通知投递当前不可重试",
                )
            raise NotificationDeliveryOperationsError(code, category, text) from exc

        append_audit_event(
            db,
            stream_key=NOTIFICATION_AUDIT_STREAM_KEY,
            actor_user_id=actor.user_id,
            action=NOTIFICATION_RETRY_ACTION,
            aggregate_type="notification_delivery",
            aggregate_id=str(delivery.id),
            before_jsonb=before_json,
            after_jsonb={
                "status": "queued",
                "retry_attempt_no": queued.attempts,
                "expected_attempt_no": expected_attempt_no,
                "reason": checked_reason,
                # Keep descriptive aliases so audit readers can distinguish
                # the one-way idempotency-key digest from the full command
                # request hash.  Neither value reveals the raw key.
                "idempotency_key_sha256": key_hash,
                "idempotency_key_hash": key_hash,
                "command_hash": command_hash,
                "request_hash": command_hash,
                "request_id": checked_request_id,
            },
            request_id=checked_request_id,
            occurred_at=effective_at,
            created_at=effective_at,
        )
        return NotificationDeliveryRetryResult(
            delivery_id=queued.id,
            retry_attempt_no=queued.attempts,
            status="queued",
            queued_at=effective_at,
            replayed=False,
        )
    except NotificationDeliveryOperationsError:
        raise
    except AuditChainError as exc:
        raise NotificationDeliveryOperationsError(
            "notification_retry_audit_unavailable",
            "service_unavailable",
            "通知重试审计链不可用",
        ) from exc


# Stable service names used by operational callers.  Keep the HTTP-oriented
# names above readable while exposing the command terminology used in runbooks.
list_notification_deliveries = list_notification_delivery_records
queue_notification_delivery_retry = retry_notification_delivery


__all__ = [
    "NOTIFICATION_AUDIT_STREAM_KEY",
    "NOTIFICATION_RETRY_ACTION",
    "NotificationDeliveryOperationsError",
    "NotificationDeliveryPage",
    "NotificationDeliveryRecord",
    "NotificationDeliveryRetryResult",
    "list_notification_deliveries",
    "list_notification_delivery_records",
    "queue_notification_delivery_retry",
    "retry_notification_delivery",
]
