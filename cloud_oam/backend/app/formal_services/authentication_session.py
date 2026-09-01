"""Formal session lifecycle evidence owned by the caller transaction."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..auth_sessions import SessionRevocationResult, SessionTokens
from ..models import AuthSession
from .authentication_audit import (
    add_authentication_state_transition,
    append_authentication_event,
)


def record_session_created(
    db: Session,
    *,
    tokens: SessionTokens,
    actor_user_id: str,
    request_id: str,
    reason_code: str,
    occurred_at: datetime | None = None,
) -> None:
    """Record same-device replacement and the new active session atomically."""

    effective_at = _aware(occurred_at)
    for replaced_id, previous_status in tokens.replaced_sessions:
        _record_revocation(
            db,
            session_id=replaced_id,
            client_type=tokens.auth_session.client_type,
            actor_user_id=actor_user_id,
            previous_status=previous_status,
            request_id=request_id,
            reason_code="same_device_replaced",
            action="authentication.session.replaced",
            occurred_at=effective_at,
        )

    add_authentication_state_transition(
        db,
        aggregate_type="auth_session",
        aggregate_id=tokens.auth_session.id,
        actor_user_id=actor_user_id,
        from_status=None,
        to_status="active",
        reason_code=reason_code,
        request_id=request_id,
        occurred_at=effective_at,
    )
    append_authentication_event(
        db,
        actor_user_id=actor_user_id,
        action="authentication.session.created",
        aggregate_type="auth_session",
        aggregate_id=tokens.auth_session.id,
        request_id=request_id,
        client_type=tokens.auth_session.client_type,
        outcome="authenticated",
        reason_code=reason_code,
        occurred_at=effective_at,
        before_status=None,
        after_status="active",
    )


def record_session_rotated(
    db: Session,
    *,
    auth_session: AuthSession,
    actor_user_id: str,
    request_id: str,
    occurred_at: datetime | None = None,
) -> None:
    append_authentication_event(
        db,
        actor_user_id=actor_user_id,
        action="authentication.session.rotated",
        aggregate_type="auth_session",
        aggregate_id=auth_session.id,
        request_id=request_id,
        client_type=auth_session.client_type,
        outcome="accepted",
        reason_code="refresh_rotated",
        occurred_at=_aware(occurred_at),
        before_status="active",
        after_status="active",
    )


def record_session_replay_revocation(
    db: Session,
    *,
    auth_session: AuthSession,
    previous_status: str,
    request_id: str,
    occurred_at: datetime | None = None,
) -> None:
    _record_revocation(
        db,
        session_id=auth_session.id,
        client_type=auth_session.client_type,
        # Possession of an old bearer token does not establish who presented
        # it.  Attribute the security event to the session aggregate without
        # falsely naming the legitimate account holder as the actor.
        actor_user_id=None,
        previous_status=previous_status,
        request_id=request_id,
        reason_code="refresh_replay",
        action="authentication.session.refresh_replay",
        occurred_at=_aware(occurred_at),
    )


def record_session_revoked(
    db: Session,
    *,
    result: SessionRevocationResult,
    request_id: str,
    reason_code: str = "user_logout",
    occurred_at: datetime | None = None,
) -> None:
    _record_revocation(
        db,
        session_id=result.auth_session.id,
        client_type=result.auth_session.client_type,
        actor_user_id=result.auth_session.user_id,
        previous_status=result.previous_status,
        request_id=request_id,
        reason_code=reason_code,
        action="authentication.session.revoked",
        occurred_at=_aware(occurred_at),
    )


def _record_revocation(
    db: Session,
    *,
    session_id: str,
    client_type: str,
    actor_user_id: str | None,
    previous_status: str,
    request_id: str,
    reason_code: str,
    action: str,
    occurred_at: datetime,
) -> None:
    if previous_status != "revoked":
        add_authentication_state_transition(
            db,
            aggregate_type="auth_session",
            aggregate_id=session_id,
            actor_user_id=actor_user_id,
            from_status=previous_status,
            to_status="revoked",
            reason_code=reason_code,
            request_id=request_id,
            occurred_at=occurred_at,
        )
    append_authentication_event(
        db,
        actor_user_id=actor_user_id,
        action=action,
        aggregate_type="auth_session",
        aggregate_id=session_id,
        request_id=request_id,
        client_type=client_type,
        outcome="revoked",
        reason_code=reason_code,
        occurred_at=occurred_at,
        before_status=previous_status,
        after_status="revoked",
    )


def _aware(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("authentication evidence timestamp must be timezone aware")
    return current.astimezone(timezone.utc)


__all__ = [
    "record_session_created",
    "record_session_replay_revocation",
    "record_session_revoked",
    "record_session_rotated",
]
