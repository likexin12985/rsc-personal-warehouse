"""Redacted append-only evidence for formal authentication state.

Authentication inputs contain credentials and stable identifiers.  This
adapter intentionally exposes only a closed set of non-secret fields to the
generic audit-chain writer so a caller cannot accidentally persist a mobile
number, verification code, provider subject, raw IP address or token.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import re

from sqlalchemy.orm import Session

from ..foundation_models import AuditEvent, StateTransitionEvent
from .audit_chain import append_audit_event


AUTHENTICATION_AUDIT_STREAM_KEY = "authentication"
AUTHENTICATION_AGGREGATE_TYPES = frozenset(
    {
        "authentication_attempt",
        "login_challenge",
        "sms_dispatch",
        "auth_session",
    }
)
AUTHENTICATION_CLIENT_TYPES = frozenset({"web", "miniprogram"})
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class AuthenticationEvidenceError(ValueError):
    """Authentication evidence contains a value outside the safe vocabulary."""


def append_authentication_event(
    db: Session,
    *,
    actor_user_id: str | None,
    action: str,
    aggregate_type: str,
    aggregate_id: str,
    request_id: str,
    client_type: str,
    outcome: str,
    reason_code: str,
    occurred_at: datetime,
    before_status: str | None = None,
    after_status: str | None = None,
) -> AuditEvent:
    """Append one redacted event to the pre-provisioned authentication chain."""

    checked_action = _safe_code("action", action)
    checked_outcome = _safe_code("outcome", outcome)
    checked_reason = _safe_code("reason_code", reason_code)
    checked_before = _optional_safe_code("before_status", before_status)
    checked_after = _optional_safe_code("after_status", after_status)
    request_reference = _request_reference(request_id)
    if aggregate_type not in AUTHENTICATION_AGGREGATE_TYPES:
        raise AuthenticationEvidenceError("unsupported authentication aggregate")
    if client_type not in AUTHENTICATION_CLIENT_TYPES:
        raise AuthenticationEvidenceError("unsupported authentication client type")

    before = {"status": checked_before} if checked_before is not None else None
    after = {
        "client_type": client_type,
        "outcome": checked_outcome,
        "reason_code": checked_reason,
    }
    if checked_after is not None:
        after["status"] = checked_after
    return append_audit_event(
        db,
        stream_key=AUTHENTICATION_AUDIT_STREAM_KEY,
        actor_user_id=actor_user_id,
        action=checked_action,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        before_jsonb=before,
        after_jsonb=after,
        request_id=request_reference,
        occurred_at=occurred_at,
    )


def add_authentication_state_transition(
    db: Session,
    *,
    aggregate_type: str,
    aggregate_id: str,
    actor_user_id: str | None,
    from_status: str | None,
    to_status: str,
    reason_code: str,
    request_id: str,
    occurred_at: datetime,
) -> StateTransitionEvent:
    """Add one lifecycle transition without storing authentication inputs."""

    if aggregate_type not in AUTHENTICATION_AGGREGATE_TYPES:
        raise AuthenticationEvidenceError("unsupported authentication aggregate")
    checked_from = _optional_safe_code("from_status", from_status)
    checked_to = _safe_code("to_status", to_status)
    checked_reason = _safe_code("reason_code", reason_code)
    request_reference = _request_reference(request_id)
    if checked_from == checked_to:
        raise AuthenticationEvidenceError("authentication state must change")
    event = StateTransitionEvent(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        from_status=checked_from,
        to_status=checked_to,
        reason=checked_reason,
        actor_id=actor_user_id,
        idempotency_key=_transition_key(
            request_id=request_reference,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            from_status=checked_from,
            to_status=checked_to,
            reason_code=checked_reason,
        ),
        occurred_at=occurred_at,
        metadata_jsonb={
            "operation": "formal_authentication_state_transition",
            "reason_code": checked_reason,
            "request_id": request_reference,
        },
    )
    db.add(event)
    db.flush()
    return event


def _transition_key(
    *,
    request_id: str,
    aggregate_type: str,
    aggregate_id: str,
    from_status: str | None,
    to_status: str,
    reason_code: str,
) -> str:
    document = "|".join(
        (
            "formal-authentication-state-v1",
            request_id,
            aggregate_type,
            aggregate_id,
            from_status or "",
            to_status,
            reason_code,
        )
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def _safe_code(field: str, value: str) -> str:
    if not isinstance(value, str) or _SAFE_CODE.fullmatch(value) is None:
        raise AuthenticationEvidenceError(f"{field} is not a safe authentication code")
    return value


def _optional_safe_code(field: str, value: str | None) -> str | None:
    return None if value is None else _safe_code(field, value)


def _request_reference(value: str) -> str:
    """Persist only a correlation digest, never caller-controlled header text."""

    if not isinstance(value, str) or not value or len(value) > 160:
        raise AuthenticationEvidenceError("request_id is invalid")
    return "authreq-" + hashlib.sha256(value.encode("utf-8")).hexdigest()
