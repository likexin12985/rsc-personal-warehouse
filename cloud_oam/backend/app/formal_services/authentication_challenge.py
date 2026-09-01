"""Provider-independent formal SMS challenge lifecycle.

The service accepts only precomputed SHA-256-shaped identifier/IP hashes and an
already exact-matched user candidate.  It never accepts or stores a plaintext
mobile number, IP address, verification code or provider credential; it never
calls an SMS provider, creates a session or commits a transaction.

Every persisted lifecycle change is paired with a redacted state transition and
the pre-provisioned ``authentication`` audit chain.  Formal login callers may
defer only the audit-chain append until all per-object session/token rows are
locked, preventing an audit-head/session lock inversion with concurrent refresh.
The deferred evidence remains caller-transaction-owned and must be flushed
before commit.  Missing or invalid audit state fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import math
import re
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, load_formal_principal
from ..foundation_models import AuthIdentity, LoginChallenge, StateTransitionEvent
from ..models import User
from .audit_chain import AuditChainError
from .authentication_audit import (
    AuthenticationEvidenceError,
    add_authentication_state_transition,
    append_authentication_event,
)


PUBLIC_REQUEST_MESSAGE = "如账号已开通，验证码将发送至该手机号"
PUBLIC_INVALID_MESSAGE = "验证码无效或已失效"
PUBLIC_RATE_LIMIT_MESSAGE = "验证码请求过于频繁，请稍后再试"
PUBLIC_SERVICE_MESSAGE = "认证服务暂时不可用"

_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_PROVIDER = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$", re.ASCII)
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$", re.ASCII)
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{16,128}$", re.ASCII)
_SAFE_REQUEST_ID = re.compile(r"^[\x21-\x7e]{8,160}$", re.ASCII)
_OBVIOUS_MOBILE = re.compile(r"^1[3-9][0-9]{9}$", re.ASCII)
_OBVIOUS_CODE = re.compile(r"^[0-9]{4,8}$", re.ASCII)

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "unauthorized": 401,
    "rate_limited": 429,
    "conflict": 409,
    "service_unavailable": 503,
}


class AuthenticationChallengeError(RuntimeError):
    """Stable, non-disclosing domain failure for a router boundary."""

    def __init__(
        self,
        *,
        code: str,
        category: str,
        public_message: str,
        retry_after: int | None = None,
        mutation_persisted: bool = False,
    ) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported authentication error category: {category}")
        super().__init__(public_message)
        self.code = code
        self.category = category
        self.public_message = public_message
        self.retry_after = retry_after
        # True means state/audit evidence has been flushed in the caller's
        # healthy transaction and should normally be committed before response.
        self.mutation_persisted = mutation_persisted

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str | int]:
        detail: dict[str, str | int] = {
            "code": self.code,
            "category": self.category,
            "message": self.public_message,
        }
        if self.retry_after is not None:
            detail["retry_after"] = self.retry_after
        return detail


@dataclass(frozen=True, slots=True)
class ChallengePreparation:
    challenge_id: uuid.UUID
    status: str
    dispatch_required: bool
    replayed: bool
    public_message: str
    retry_after: int
    expires_in: int


@dataclass(frozen=True, slots=True)
class VerificationAttempt:
    challenge_id: uuid.UUID
    attempt_no: int
    max_attempts: int
    verification_mode: str
    provider_reference: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ChallengeLifecycleResult:
    challenge_id: uuid.UUID
    status: str
    attempts: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DeferredChallengeAuditEvent:
    """One redacted challenge event held only in request memory until flush."""

    actor_user_id: str | None
    action: str
    aggregate_id: str
    request_id: str
    client_type: str
    outcome: str
    reason_code: str
    occurred_at: datetime
    before_status: str | None
    after_status: str


def prepare(
    db: Session,
    *,
    mobile_hash: str,
    requested_ip_hash: str,
    user: User | None,
    provider: str,
    client_type: str,
    idempotency_key: str,
    request_id: str,
    hash_version: int,
    mobile_hour_limit: int,
    ip_hour_limit: int,
    send_interval_seconds: int,
    ttl_seconds: int,
    max_attempts: int,
    now: datetime | None = None,
) -> ChallengePreparation:
    """Prepare one provider-managed SMS challenge without sending it."""

    effective_at = _aware_now(now)
    checked_mobile_hash = _require_hash("mobile_hash", mobile_hash)
    checked_ip_hash = _require_hash("requested_ip_hash", requested_ip_hash)
    checked_provider = _require_provider(provider)
    checked_client = _require_client_type(client_type)
    checked_request_id = _require_request_id(request_id)
    checked_hash_version = _require_hash_version(hash_version)
    raw_key = _require_idempotency_key(idempotency_key)
    limits = _validated_limits(
        mobile_hour_limit=mobile_hour_limit,
        ip_hour_limit=ip_hour_limit,
        send_interval_seconds=send_interval_seconds,
        ttl_seconds=ttl_seconds,
        max_attempts=max_attempts,
    )
    effective_user_id = _formal_login_user_id(
        db,
        user,
        effective_at,
        mobile_hash=checked_mobile_hash,
        provider=checked_provider,
        hash_version=checked_hash_version,
    )
    key_hash = _domain_hash("authentication-challenge-idempotency-v1", raw_key)
    fingerprint = _request_fingerprint(
        mobile_hash=checked_mobile_hash,
        requested_ip_hash=checked_ip_hash,
        user_id=effective_user_id,
        provider=checked_provider,
        client_type=checked_client,
        hash_version=checked_hash_version,
        limits=limits,
    )
    stored_key = f"{key_hash}:{fingerprint}"

    pending_error: AuthenticationChallengeError | None = None
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            _acquire_postgresql_locks(
                db,
                key_hash=key_hash,
                mobile_hash=checked_mobile_hash,
                requested_ip_hash=checked_ip_hash,
            )
            existing = _idempotent_challenge(db, key_hash)
            if existing is not None:
                if existing.idempotency_key != stored_key:
                    raise _error(
                        "challenge_idempotency_conflict",
                        "conflict",
                        "请求状态冲突",
                    )
                denial_reason = _initial_rate_denial_reason(db, existing)
                if denial_reason is not None:
                    current_rate_error = _rate_limit_error(
                        db,
                        mobile_hash=checked_mobile_hash,
                        requested_ip_hash=checked_ip_hash,
                        now=effective_at,
                        limits=limits,
                    )
                    raise AuthenticationChallengeError(
                        code=denial_reason,
                        category="rate_limited",
                        public_message=PUBLIC_RATE_LIMIT_MESSAGE,
                        retry_after=(
                            current_rate_error[1]
                            if current_rate_error is not None
                            else 1
                        ),
                    )
                return _preparation_result(
                    existing,
                    replayed=True,
                    retry_after=limits.send_interval_seconds,
                    expires_in=limits.ttl_seconds,
                )

            rate_error = _rate_limit_error(
                db,
                mobile_hash=checked_mobile_hash,
                requested_ip_hash=checked_ip_hash,
                now=effective_at,
                limits=limits,
            )
            if rate_error is not None:
                reason_code, retry_after = rate_error
                challenge = _new_challenge(
                    mobile_hash=checked_mobile_hash,
                    requested_ip_hash=checked_ip_hash,
                    provider=checked_provider,
                    client_type=checked_client,
                    stored_key=stored_key,
                    status="cancelled",
                    max_attempts=limits.max_attempts,
                    expires_at=effective_at + timedelta(seconds=limits.ttl_seconds),
                    created_at=effective_at,
                )
                db.add(challenge)
                _record_initial_state(
                    db,
                    challenge=challenge,
                    actor_user_id=effective_user_id,
                    reason_code=reason_code,
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    outcome="rate_limited",
                )
                pending_error = AuthenticationChallengeError(
                    code=reason_code,
                    category="rate_limited",
                    public_message=PUBLIC_RATE_LIMIT_MESSAGE,
                    retry_after=retry_after,
                    mutation_persisted=True,
                )
                result = _preparation_result(
                    challenge,
                    replayed=False,
                    retry_after=retry_after,
                    expires_in=limits.ttl_seconds,
                )
            else:
                status = "pending" if effective_user_id is not None else "cancelled"
                reason_code = (
                    "challenge_created"
                    if effective_user_id is not None
                    else "identity_unavailable"
                )
                challenge = _new_challenge(
                    mobile_hash=checked_mobile_hash,
                    requested_ip_hash=checked_ip_hash,
                    provider=checked_provider,
                    client_type=checked_client,
                    stored_key=stored_key,
                    status=status,
                    max_attempts=limits.max_attempts,
                    expires_at=effective_at + timedelta(seconds=limits.ttl_seconds),
                    created_at=effective_at,
                )
                db.add(challenge)
                _record_initial_state(
                    db,
                    challenge=challenge,
                    actor_user_id=effective_user_id,
                    reason_code=reason_code,
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    outcome="accepted" if status == "pending" else "ignored",
                )
                result = _preparation_result(
                    challenge,
                    replayed=False,
                    retry_after=limits.send_interval_seconds,
                    expires_in=limits.ttl_seconds,
                )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc

    if pending_error is not None:
        raise pending_error
    return result


def mark_sent(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    provider_reference: str,
    request_id: str,
    now: datetime | None = None,
) -> ChallengeLifecycleResult:
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_reference = _require_provider_reference(provider_reference)
    checked_request_id = _require_request_id(request_id)
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if challenge.provider_reference is not None:
                if challenge.provider_reference == checked_reference:
                    return _lifecycle_result(challenge, replayed=True)
                raise _error(
                    "challenge_provider_reference_conflict",
                    "conflict",
                    "请求状态冲突",
                )
            if challenge.status != "pending":
                raise _error("challenge_not_pending", "unauthorized", PUBLIC_INVALID_MESSAGE)
            challenge.provider_reference = checked_reference
            _append_event(
                db,
                challenge=challenge,
                actor_user_id=None,
                action="authentication.sms.challenge_sent",
                outcome="accepted",
                reason_code="provider_accepted",
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            db.flush()
            return _lifecycle_result(challenge)
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc


def mark_send_failed(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    request_id: str,
    now: datetime | None = None,
) -> ChallengeLifecycleResult:
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_request_id = _require_request_id(request_id)
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if challenge.status == "cancelled":
                return _lifecycle_result(challenge, replayed=True)
            if challenge.status != "pending":
                raise _error("challenge_not_pending", "unauthorized", PUBLIC_INVALID_MESSAGE)
            _transition(
                db,
                challenge=challenge,
                to_status="cancelled",
                actor_user_id=None,
                action="authentication.sms.send_failed",
                outcome="failed",
                reason_code="provider_send_failed",
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            return _lifecycle_result(challenge)
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc


def begin_verify(
    db: Session,
    *,
    mobile_hash: str,
    provider: str,
    client_type: str,
    request_id: str,
    now: datetime | None = None,
    deferred_events: list[DeferredChallengeAuditEvent] | None = None,
) -> VerificationAttempt:
    effective_at = _aware_now(now)
    checked_hash = _require_hash("mobile_hash", mobile_hash)
    checked_provider = _require_provider(provider)
    checked_client = _require_client_type(client_type)
    checked_request_id = _require_request_id(request_id)
    pending_error: AuthenticationChallengeError | None = None
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = db.scalar(
                select(LoginChallenge)
                .where(
                    LoginChallenge.mobile_hash == checked_hash,
                    LoginChallenge.provider == checked_provider,
                    LoginChallenge.client_type == checked_client,
                    LoginChallenge.status == "pending",
                )
                .order_by(LoginChallenge.created_at.desc(), LoginChallenge.id.desc())
                .with_for_update()
                .limit(1)
            )
            if challenge is None:
                raise _error("challenge_unavailable", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if _as_utc(challenge.expires_at) <= effective_at:
                _transition(
                    db,
                    challenge=challenge,
                    to_status="expired",
                    actor_user_id=None,
                    action="authentication.sms.challenge_expired",
                    outcome="rejected",
                    reason_code="challenge_expired",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    deferred_events=deferred_events,
                )
                pending_error = AuthenticationChallengeError(
                    code="challenge_expired",
                    category="unauthorized",
                    public_message=PUBLIC_INVALID_MESSAGE,
                    mutation_persisted=True,
                )
            elif challenge.attempts >= challenge.max_attempts:
                _transition(
                    db,
                    challenge=challenge,
                    to_status="locked",
                    actor_user_id=None,
                    action="authentication.sms.challenge_locked",
                    outcome="rejected",
                    reason_code="attempts_exhausted",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    deferred_events=deferred_events,
                )
                pending_error = AuthenticationChallengeError(
                    code="attempts_exhausted",
                    category="rate_limited",
                    public_message=PUBLIC_INVALID_MESSAGE,
                    mutation_persisted=True,
                )
            elif (
                challenge.verification_mode == "provider_managed"
                and challenge.provider_reference is None
            ):
                raise _error(
                    "challenge_not_sent",
                    "conflict",
                    "请求状态冲突",
                )
            else:
                challenge.attempts += 1
                _append_event(
                    db,
                    challenge=challenge,
                    actor_user_id=None,
                    action="authentication.sms.verification_started",
                    outcome="accepted",
                    reason_code="attempt_started",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    deferred_events=deferred_events,
                )
                db.flush()
                result = VerificationAttempt(
                    challenge_id=challenge.id,
                    attempt_no=challenge.attempts,
                    max_attempts=challenge.max_attempts,
                    verification_mode=challenge.verification_mode,
                    provider_reference=challenge.provider_reference or "",
                )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc
    if pending_error is not None:
        raise pending_error
    return result


def finish_verify(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    verified: bool,
    request_id: str,
    now: datetime | None = None,
    deferred_events: list[DeferredChallengeAuditEvent] | None = None,
) -> ChallengeLifecycleResult:
    if not isinstance(verified, bool):
        raise _error("invalid_verification_result", "invalid_request", "请求参数无效")
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_request_id = _require_request_id(request_id)
    pending_error: AuthenticationChallengeError | None = None
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if verified and challenge.status == "verified":
                return _lifecycle_result(challenge, replayed=True)
            if challenge.status != "pending" or challenge.attempts <= 0:
                raise _error("challenge_not_pending", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if challenge.verification_mode == "provider_managed":
                if challenge.code_hash is not None or challenge.provider_reference is None:
                    raise _error(
                        "challenge_verification_material_invalid",
                        "conflict",
                        "请求状态冲突",
                    )

            if verified:
                challenge.verified_at = effective_at
                _transition(
                    db,
                    challenge=challenge,
                    to_status="verified",
                    actor_user_id=None,
                    action="authentication.sms.verification_succeeded",
                    outcome="verified",
                    reason_code="provider_verified",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    deferred_events=deferred_events,
                )
                result = _lifecycle_result(challenge)
            elif challenge.attempts >= challenge.max_attempts:
                _transition(
                    db,
                    challenge=challenge,
                    to_status="locked",
                    actor_user_id=None,
                    action="authentication.sms.verification_failed",
                    outcome="rejected",
                    reason_code="attempts_exhausted",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    deferred_events=deferred_events,
                )
                pending_error = AuthenticationChallengeError(
                    code="attempts_exhausted",
                    category="unauthorized",
                    public_message=PUBLIC_INVALID_MESSAGE,
                    mutation_persisted=True,
                )
                result = _lifecycle_result(challenge)
            else:
                _append_event(
                    db,
                    challenge=challenge,
                    actor_user_id=None,
                    action="authentication.sms.verification_failed",
                    outcome="rejected",
                    reason_code="code_rejected",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    deferred_events=deferred_events,
                )
                db.flush()
                pending_error = AuthenticationChallengeError(
                    code="verification_failed",
                    category="unauthorized",
                    public_message=PUBLIC_INVALID_MESSAGE,
                    mutation_persisted=True,
                )
                result = _lifecycle_result(challenge)
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc
    if pending_error is not None:
        raise pending_error
    return result


def consume_verified(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    request_id: str,
    actor_user_id: str | None = None,
    now: datetime | None = None,
    deferred_events: list[DeferredChallengeAuditEvent] | None = None,
) -> ChallengeLifecycleResult:
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_request_id = _require_request_id(request_id)
    checked_actor = _optional_user_id(actor_user_id)
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if challenge.status == "consumed":
                return _lifecycle_result(challenge, replayed=True)
            if challenge.status != "verified" or challenge.verified_at is None:
                raise _error("challenge_not_verified", "unauthorized", PUBLIC_INVALID_MESSAGE)
            challenge.consumed_at = effective_at
            _transition(
                db,
                challenge=challenge,
                to_status="consumed",
                actor_user_id=checked_actor,
                action="authentication.sms.challenge_consumed",
                outcome="consumed",
                reason_code="session_issue_authorized",
                request_id=checked_request_id,
                occurred_at=effective_at,
                deferred_events=deferred_events,
            )
            return _lifecycle_result(challenge)
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc


def flush_deferred_audit_events(
    db: Session,
    deferred_events: list[DeferredChallengeAuditEvent],
) -> None:
    """Append deferred events in fact order, then consume the in-memory queue.

    The caller must invoke this only after its session, refresh-token and state
    rows have been locked/written.  This function never commits or rolls back.
    On any failure the queue is kept intact and the caller must roll back its
    transaction; a second flush is safe only after the first completed and
    emptied the list.
    """

    if not isinstance(deferred_events, list) or any(
        not isinstance(event, DeferredChallengeAuditEvent)
        for event in deferred_events
    ):
        raise AuthenticationChallengeError(
            code="authentication_deferred_audit_invalid",
            category="service_unavailable",
            public_message=PUBLIC_SERVICE_MESSAGE,
        )
    try:
        for event in deferred_events:
            append_authentication_event(
                db,
                actor_user_id=event.actor_user_id,
                action=event.action,
                aggregate_type="login_challenge",
                aggregate_id=event.aggregate_id,
                request_id=event.request_id,
                client_type=event.client_type,
                outcome=event.outcome,
                reason_code=event.reason_code,
                occurred_at=event.occurred_at,
                before_status=event.before_status,
                after_status=event.after_status,
            )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    deferred_events.clear()


@dataclass(frozen=True, slots=True)
class _Limits:
    mobile_hour_limit: int
    ip_hour_limit: int
    send_interval_seconds: int
    ttl_seconds: int
    max_attempts: int


def _validated_limits(**values: int) -> _Limits:
    checked: dict[str, int] = {}
    bounds = {
        "mobile_hour_limit": (1, 1000),
        "ip_hour_limit": (1, 10000),
        "send_interval_seconds": (1, 3600),
        "ttl_seconds": (30, 3600),
        "max_attempts": (1, 20),
    }
    for field, value in values.items():
        minimum, maximum = bounds[field]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise _error("invalid_challenge_limits", "invalid_request", "请求参数无效")
        checked[field] = value
    return _Limits(**checked)


def _formal_login_user_id(
    db: Session,
    user: User | None,
    now: datetime,
    *,
    mobile_hash: str,
    provider: str,
    hash_version: int,
) -> str | None:
    if user is None:
        return None
    if not isinstance(user, User) or not isinstance(user.id, str) or not user.id:
        raise _error("invalid_user_candidate", "invalid_request", "请求参数无效")
    try:
        with db.no_autoflush:
            exact_identity_ids = list(
                db.scalars(
                    select(AuthIdentity.id)
                    .where(
                        AuthIdentity.user_id == user.id,
                        AuthIdentity.identity_type == "mobile",
                        AuthIdentity.provider_key == provider,
                        AuthIdentity.identifier_hash == mobile_hash,
                        AuthIdentity.hash_version == hash_version,
                        AuthIdentity.status == "active",
                        AuthIdentity.verified_at.is_not(None),
                        AuthIdentity.revoked_at.is_(None),
                    )
                    .limit(2)
                )
            )
            if len(exact_identity_ids) != 1:
                return None
            principal = load_formal_principal(db, user.id, now=now)
    except FormalAccessError:
        return None
    if principal.user_id != user.id or principal.account_status not in {
        "active",
        "restricted_handover",
    }:
        return None
    return user.id


def _request_fingerprint(
    *,
    mobile_hash: str,
    requested_ip_hash: str,
    user_id: str | None,
    provider: str,
    client_type: str,
    hash_version: int,
    limits: _Limits,
) -> str:
    document = {
        "client_type": client_type,
        "hash_version": hash_version,
        "ip_hash": requested_ip_hash,
        "limits": {
            "ip_hour": limits.ip_hour_limit,
            "max_attempts": limits.max_attempts,
            "mobile_hour": limits.mobile_hour_limit,
            "send_interval": limits.send_interval_seconds,
            "ttl": limits.ttl_seconds,
        },
        "mobile_hash": mobile_hash,
        "provider": provider,
        "user_id": user_id,
        "verification_mode": "provider_managed",
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _idempotent_challenge(db: Session, key_hash: str) -> LoginChallenge | None:
    return db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.idempotency_key.like(f"{key_hash}:%"))
        .order_by(LoginChallenge.created_at.desc(), LoginChallenge.id.desc())
        .with_for_update()
        .limit(1)
    )


def _initial_rate_denial_reason(
    db: Session,
    challenge: LoginChallenge,
) -> str | None:
    reason = db.scalar(
        select(StateTransitionEvent.reason)
        .where(
            StateTransitionEvent.aggregate_type == "login_challenge",
            StateTransitionEvent.aggregate_id == str(challenge.id),
            StateTransitionEvent.from_status.is_(None),
            StateTransitionEvent.to_status == "cancelled",
        )
        .order_by(StateTransitionEvent.occurred_at)
        .limit(1)
    )
    return reason if reason in {"mobile_hour_limit", "ip_hour_limit", "send_interval"} else None


def _rate_limit_error(
    db: Session,
    *,
    mobile_hash: str,
    requested_ip_hash: str,
    now: datetime,
    limits: _Limits,
) -> tuple[str, int] | None:
    cutoff = now - timedelta(hours=1)
    mobile_rows = list(
        db.scalars(
            select(LoginChallenge.created_at)
            .where(
                LoginChallenge.mobile_hash == mobile_hash,
                LoginChallenge.created_at >= cutoff,
            )
            .order_by(LoginChallenge.created_at)
        )
    )
    if len(mobile_rows) >= limits.mobile_hour_limit:
        return "mobile_hour_limit", _hour_retry_after(mobile_rows[0], now)

    ip_rows = list(
        db.scalars(
            select(LoginChallenge.created_at)
            .where(
                LoginChallenge.requested_ip_hash == requested_ip_hash,
                LoginChallenge.created_at >= cutoff,
            )
            .order_by(LoginChallenge.created_at)
        )
    )
    if len(ip_rows) >= limits.ip_hour_limit:
        return "ip_hour_limit", _hour_retry_after(ip_rows[0], now)

    latest = db.scalar(
        select(LoginChallenge.created_at)
        .where(LoginChallenge.mobile_hash == mobile_hash)
        .order_by(LoginChallenge.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        elapsed = (now - _as_utc(latest)).total_seconds()
        if elapsed < limits.send_interval_seconds:
            return "send_interval", max(
                1,
                math.ceil(limits.send_interval_seconds - elapsed),
            )
    return None


def _hour_retry_after(oldest: datetime, now: datetime) -> int:
    return max(1, math.ceil((_as_utc(oldest) + timedelta(hours=1) - now).total_seconds()))


def _new_challenge(
    *,
    mobile_hash: str,
    requested_ip_hash: str,
    provider: str,
    client_type: str,
    stored_key: str,
    status: str,
    max_attempts: int,
    expires_at: datetime,
    created_at: datetime,
) -> LoginChallenge:
    return LoginChallenge(
        id=uuid.uuid4(),
        mobile_hash=mobile_hash,
        code_hash=None,
        verification_mode="provider_managed",
        provider=provider,
        provider_reference=None,
        client_type=client_type,
        purpose="login",
        attempts=0,
        max_attempts=max_attempts,
        expires_at=expires_at,
        status=status,
        idempotency_key=stored_key,
        requested_ip_hash=requested_ip_hash,
        verified_at=None,
        consumed_at=None,
        created_at=created_at,
    )


def _record_initial_state(
    db: Session,
    *,
    challenge: LoginChallenge,
    actor_user_id: str | None,
    reason_code: str,
    request_id: str,
    occurred_at: datetime,
    outcome: str,
) -> None:
    add_authentication_state_transition(
        db,
        aggregate_type="login_challenge",
        aggregate_id=str(challenge.id),
        actor_user_id=actor_user_id,
        from_status=None,
        to_status=challenge.status,
        reason_code=reason_code,
        request_id=request_id,
        occurred_at=occurred_at,
    )
    _append_event(
        db,
        challenge=challenge,
        actor_user_id=actor_user_id,
        action="authentication.sms.challenge_prepared",
        outcome=outcome,
        reason_code=reason_code,
        request_id=request_id,
        occurred_at=occurred_at,
        before_status=None,
    )


def _transition(
    db: Session,
    *,
    challenge: LoginChallenge,
    to_status: str,
    actor_user_id: str | None,
    action: str,
    outcome: str,
    reason_code: str,
    request_id: str,
    occurred_at: datetime,
    deferred_events: list[DeferredChallengeAuditEvent] | None = None,
) -> None:
    before_status = challenge.status
    challenge.status = to_status
    add_authentication_state_transition(
        db,
        aggregate_type="login_challenge",
        aggregate_id=str(challenge.id),
        actor_user_id=actor_user_id,
        from_status=before_status,
        to_status=to_status,
        reason_code=reason_code,
        request_id=request_id,
        occurred_at=occurred_at,
    )
    _append_event(
        db,
        challenge=challenge,
        actor_user_id=actor_user_id,
        action=action,
        outcome=outcome,
        reason_code=reason_code,
        request_id=request_id,
        occurred_at=occurred_at,
        before_status=before_status,
        deferred_events=deferred_events,
    )


def _append_event(
    db: Session,
    *,
    challenge: LoginChallenge,
    actor_user_id: str | None,
    action: str,
    outcome: str,
    reason_code: str,
    request_id: str,
    occurred_at: datetime,
    before_status: str | None = None,
    deferred_events: list[DeferredChallengeAuditEvent] | None = None,
) -> None:
    if deferred_events is not None:
        deferred_events.append(
            DeferredChallengeAuditEvent(
                actor_user_id=actor_user_id,
                action=action,
                aggregate_id=str(challenge.id),
                request_id=request_id,
                client_type=challenge.client_type,
                outcome=outcome,
                reason_code=reason_code,
                occurred_at=occurred_at,
                before_status=before_status,
                after_status=challenge.status,
            )
        )
        return
    append_authentication_event(
        db,
        actor_user_id=actor_user_id,
        action=action,
        aggregate_type="login_challenge",
        aggregate_id=str(challenge.id),
        request_id=request_id,
        client_type=challenge.client_type,
        outcome=outcome,
        reason_code=reason_code,
        occurred_at=occurred_at,
        before_status=before_status,
        after_status=challenge.status,
    )


def _locked_challenge(db: Session, challenge_id: uuid.UUID) -> LoginChallenge | None:
    return db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.id == challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _preparation_result(
    challenge: LoginChallenge,
    *,
    replayed: bool,
    retry_after: int,
    expires_in: int,
) -> ChallengePreparation:
    return ChallengePreparation(
        challenge_id=challenge.id,
        status=challenge.status,
        dispatch_required=(
            challenge.status == "pending" and challenge.provider_reference is None
        ),
        replayed=replayed,
        public_message=PUBLIC_REQUEST_MESSAGE,
        retry_after=retry_after,
        expires_in=expires_in,
    )


def _lifecycle_result(
    challenge: LoginChallenge,
    *,
    replayed: bool = False,
) -> ChallengeLifecycleResult:
    return ChallengeLifecycleResult(
        challenge_id=challenge.id,
        status=challenge.status,
        attempts=challenge.attempts,
        replayed=replayed,
    )


def _acquire_postgresql_locks(
    db: Session,
    *,
    key_hash: str,
    mobile_hash: str,
    requested_ip_hash: str,
) -> None:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    lock_keys = sorted(
        {
            _advisory_lock_key("idempotency", key_hash),
            _advisory_lock_key("mobile", mobile_hash),
            _advisory_lock_key("requested_ip", requested_ip_hash),
        }
    )
    for lock_key in lock_keys:
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def _ensure_outer_transaction(db: Session) -> None:
    """Ensure a SAVEPOINT never becomes SQLite's effective outer transaction.

    SQLite defers the physical ``BEGIN`` through read-only ORM work.  Releasing
    the first SAVEPOINT in that state commits it at the driver level, defeating
    the service contract that only the caller may commit.  PostgreSQL starts a
    physical transaction on the preceding reads and needs no compatibility
    action.
    """

    connection = db.connection()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _advisory_lock_key(namespace: str, value: str) -> int:
    digest = hashlib.sha256(f"formal-auth:{namespace}:{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _domain_hash(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}:{value}".encode()).hexdigest()


def _require_hash(field: str, value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _error(f"invalid_{field}", "invalid_request", "请求参数无效")
    return value


def _require_provider(value: str) -> str:
    if not isinstance(value, str) or _SAFE_PROVIDER.fullmatch(value) is None:
        raise _error("invalid_provider", "invalid_request", "请求参数无效")
    return value


def _require_hash_version(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > 2_147_483_647
    ):
        raise _error("invalid_hash_version", "invalid_request", "请求参数无效")
    return value


def _require_client_type(value: str) -> str:
    if value not in {"web", "miniprogram"}:
        raise _error("invalid_client_type", "invalid_request", "请求参数无效")
    return value


def _require_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_IDEMPOTENCY_KEY.fullmatch(value) is None
        or _looks_like_plaintext_auth_value(value)
    ):
        raise _error("invalid_idempotency_key", "invalid_request", "请求参数无效")
    return value


def _require_request_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_REQUEST_ID.fullmatch(value) is None
        or _looks_like_plaintext_auth_value(value)
    ):
        raise _error("invalid_request_id", "invalid_request", "请求参数无效")
    return value


def _require_provider_reference(value: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_REFERENCE.fullmatch(value) is None
        or _looks_like_plaintext_auth_value(value)
    ):
        raise _error("invalid_provider_reference", "invalid_request", "请求参数无效")
    return value


def _looks_like_plaintext_auth_value(value: str) -> bool:
    if (
        _OBVIOUS_MOBILE.fullmatch(value) is not None
        or _OBVIOUS_CODE.fullmatch(value) is not None
    ):
        return True
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _require_uuid(value: uuid.UUID | str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise _error("invalid_challenge_id", "invalid_request", "请求参数无效") from None


def _optional_user_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 36:
        raise _error("invalid_actor", "invalid_request", "请求参数无效")
    return value


def _aware_now(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        raise _error("invalid_timestamp", "invalid_request", "请求参数无效")
    return current.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _error(
    code: str,
    category: str,
    public_message: str,
    *,
    retry_after: int | None = None,
) -> AuthenticationChallengeError:
    return AuthenticationChallengeError(
        code=code,
        category=category,
        public_message=public_message,
        retry_after=retry_after,
    )


def _audit_unavailable() -> AuthenticationChallengeError:
    return AuthenticationChallengeError(
        code="authentication_audit_unavailable",
        category="service_unavailable",
        public_message=PUBLIC_SERVICE_MESSAGE,
    )


def _concurrent_conflict() -> AuthenticationChallengeError:
    return _error(
        "challenge_concurrent_conflict",
        "conflict",
        "请求状态冲突",
    )


__all__ = [
    "AuthenticationChallengeError",
    "ChallengeLifecycleResult",
    "ChallengePreparation",
    "DeferredChallengeAuditEvent",
    "VerificationAttempt",
    "begin_verify",
    "consume_verified",
    "finish_verify",
    "flush_deferred_audit_events",
    "mark_send_failed",
    "mark_sent",
    "prepare",
]
