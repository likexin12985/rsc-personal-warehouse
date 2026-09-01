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

from dataclasses import dataclass, field
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
from ..foundation_models import (
    AuthIdentity,
    LoginChallenge,
    SmsChallengeDispatch,
    StateTransitionEvent,
)
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
    dispatch_status: str | None
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
class DispatchClaim:
    challenge_id: uuid.UUID
    dispatch_status: str
    owner_token: str | None = field(repr=False)
    replayed: bool = False

    @property
    def acquired(self) -> bool:
        return self.owner_token is not None and self.dispatch_status == "sending"


@dataclass(frozen=True, slots=True)
class DispatchLifecycleResult:
    challenge_id: uuid.UUID
    dispatch_status: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DeferredChallengeAuditEvent:
    """One redacted challenge event held only in request memory until flush."""

    actor_user_id: str | None
    action: str
    aggregate_type: str
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
    dispatch_request_profile_sha256: str,
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
    checked_dispatch_profile = _require_hash(
        "dispatch_request_profile_sha256",
        dispatch_request_profile_sha256,
    )
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
        dispatch_request_profile_sha256=checked_dispatch_profile,
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
                    db,
                    existing,
                    replayed=True,
                    retry_after=limits.send_interval_seconds,
                    expires_in=limits.ttl_seconds,
                )

            if effective_user_id is not None:
                unresolved_dispatch = _unresolved_dispatch(
                    db,
                    mobile_hash=checked_mobile_hash,
                    provider=checked_provider,
                    now=effective_at,
                    request_id=checked_request_id,
                )
                if unresolved_dispatch is not None:
                    unresolved_challenge = db.get(
                        LoginChallenge,
                        unresolved_dispatch.challenge_id,
                    )
                    if unresolved_challenge is None:
                        raise _error(
                            "dispatch_evidence_invalid",
                            "conflict",
                            "请求状态冲突",
                        )
                    # A new idempotency key cannot create rows while one paid
                    # side effect remains unresolved.  Reuse the existing
                    # generic response without another challenge/audit write;
                    # this prevents both resend and database write amplification.
                    return _preparation_result(
                        db,
                        unresolved_challenge,
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
                if _active_rate_denial(
                    db,
                    reason_code=reason_code,
                    mobile_hash=checked_mobile_hash,
                    requested_ip_hash=checked_ip_hash,
                    now=effective_at,
                    send_interval_seconds=limits.send_interval_seconds,
                ) is not None:
                    # Preserve one durable denial/audit fact for the active
                    # scope, but never append one row per attacker-controlled
                    # idempotency key while the same limit remains active.
                    raise AuthenticationChallengeError(
                        code=reason_code,
                        category="rate_limited",
                        public_message=PUBLIC_RATE_LIMIT_MESSAGE,
                        retry_after=retry_after,
                    )
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
                    db,
                    challenge,
                    replayed=False,
                    retry_after=retry_after,
                    expires_in=limits.ttl_seconds,
                )
            else:
                status = "pending" if effective_user_id is not None else "cancelled"
                reason_code = (
                    "challenge_created"
                    if status == "pending"
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
                if status == "pending":
                    dispatch = _new_dispatch(
                        challenge=challenge,
                        provider=checked_provider,
                        request_profile_sha256=checked_dispatch_profile,
                        created_at=effective_at,
                    )
                    db.add(dispatch)
                _record_initial_state(
                    db,
                    challenge=challenge,
                    actor_user_id=effective_user_id,
                    reason_code=reason_code,
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                    outcome="accepted" if status == "pending" else "ignored",
                )
                if status == "pending":
                    _record_initial_dispatch_state(
                        db,
                        challenge=challenge,
                        dispatch=dispatch,
                        actor_user_id=effective_user_id,
                        request_id=checked_request_id,
                        occurred_at=effective_at,
                    )
                result = _preparation_result(
                    db,
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


def claim_dispatch(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    request_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> DispatchClaim:
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_request_id = _require_request_id(request_id)
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, int)
        or not 5 <= lease_seconds <= 300
    ):
        raise _error("invalid_dispatch_lease", "invalid_request", "请求参数无效")
    owner_token = uuid.uuid4().hex
    owner_token_hash = _dispatch_owner_hash(owner_token)
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            dispatch = _locked_dispatch(db, checked_id)
            if dispatch is None:
                raise _error("dispatch_not_prepared", "conflict", "请求状态冲突")
            if dispatch.status == "sending":
                if _as_utc(dispatch.lease_expires_at) <= effective_at:
                    dispatch.uncertain_at = effective_at
                    _transition_dispatch(
                        db,
                        challenge=challenge,
                        dispatch=dispatch,
                        to_status="uncertain",
                        action="authentication.sms.dispatch_uncertain",
                        outcome="uncertain",
                        reason_code="dispatch_lease_expired",
                        request_id=checked_request_id,
                        occurred_at=effective_at,
                    )
                    return _dispatch_claim_result(dispatch, replayed=True)
                return _dispatch_claim_result(dispatch, replayed=True)
            if dispatch.status in {"accepted", "uncertain"}:
                return _dispatch_claim_result(dispatch, replayed=True)
            if dispatch.status != "prepared" or challenge.status != "pending":
                raise _error("dispatch_not_prepared", "conflict", "请求状态冲突")
            dispatch.owner_token_hash = owner_token_hash
            dispatch.claimed_at = effective_at
            dispatch.lease_expires_at = effective_at + timedelta(
                seconds=lease_seconds
            )
            _transition_dispatch(
                db,
                challenge=challenge,
                dispatch=dispatch,
                to_status="sending",
                action="authentication.sms.dispatch_claimed",
                outcome="accepted",
                reason_code="single_owner_claimed",
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            return DispatchClaim(
                challenge_id=dispatch.challenge_id,
                dispatch_status=dispatch.status,
                owner_token=owner_token,
            )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc


def mark_sent(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    owner_token: str,
    provider_reference: str,
    request_id: str,
    now: datetime | None = None,
) -> DispatchLifecycleResult:
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_owner_hash = _require_dispatch_owner(owner_token)
    checked_reference = _require_provider_reference(provider_reference)
    checked_request_id = _require_request_id(request_id)
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            dispatch = _locked_dispatch(db, checked_id)
            if dispatch is None:
                raise _error("dispatch_not_prepared", "conflict", "请求状态冲突")
            if dispatch.owner_token_hash != checked_owner_hash:
                raise _error("dispatch_owner_conflict", "conflict", "请求状态冲突")
            if dispatch.status == "accepted":
                if (
                    dispatch.provider_reference == checked_reference
                    and challenge.provider_reference == checked_reference
                ):
                    return _dispatch_lifecycle_result(dispatch, replayed=True)
                raise _error(
                    "challenge_provider_reference_conflict",
                    "conflict",
                    "请求状态冲突",
                )
            if dispatch.status not in {"sending", "uncertain"}:
                raise _error("dispatch_not_sending", "conflict", "请求状态冲突")
            if challenge.status != "pending":
                raise _error("challenge_not_pending", "unauthorized", PUBLIC_INVALID_MESSAGE)
            if challenge.provider_reference is not None:
                raise _error(
                    "challenge_provider_reference_conflict",
                    "conflict",
                    "请求状态冲突",
                )
            dispatch.provider_reference = checked_reference
            dispatch.accepted_at = effective_at
            _transition_dispatch(
                db,
                challenge=challenge,
                dispatch=dispatch,
                to_status="accepted",
                action="authentication.sms.dispatch_accepted",
                outcome="accepted",
                reason_code="provider_accepted",
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            # The legacy column is retained as a compatibility projection.  It
            # is written only after the independent dispatch fact is accepted.
            challenge.provider_reference = checked_reference
            db.flush()
            return _dispatch_lifecycle_result(dispatch)
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc


def authorize_dispatch_provider_call(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    owner_token: str,
    request_id: str,
    minimum_remaining_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Lock and authorize one imminent provider call in the outer transaction.

    The caller must keep the transaction open across the provider call and its
    accepted/uncertain transition.  This prevents expiry/replacement from
    racing a delayed owner into an unaccounted late SMS.
    """

    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_owner_hash = _require_dispatch_owner(owner_token)
    checked_request_id = _require_request_id(request_id)
    if (
        isinstance(minimum_remaining_seconds, bool)
        or not isinstance(minimum_remaining_seconds, int)
        or not 1 <= minimum_remaining_seconds <= 60
    ):
        raise _error("invalid_dispatch_call_budget", "invalid_request", "请求参数无效")
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error(
                    "challenge_not_found",
                    "unauthorized",
                    PUBLIC_INVALID_MESSAGE,
                )
            dispatch = _locked_dispatch(db, checked_id)
            if dispatch is None:
                raise _error("dispatch_not_prepared", "conflict", "请求状态冲突")
            if dispatch.owner_token_hash != checked_owner_hash:
                raise _error("dispatch_owner_conflict", "conflict", "请求状态冲突")
            if dispatch.status != "sending" or challenge.status != "pending":
                return False
            if dispatch.lease_expires_at is None:
                raise _error("dispatch_evidence_invalid", "conflict", "请求状态冲突")
            start_deadline = min(
                _as_utc(dispatch.lease_expires_at),
                _as_utc(challenge.expires_at),
            )
            if start_deadline <= effective_at + timedelta(
                seconds=minimum_remaining_seconds
            ):
                dispatch.uncertain_at = effective_at
                _transition_dispatch(
                    db,
                    challenge=challenge,
                    dispatch=dispatch,
                    to_status="uncertain",
                    action="authentication.sms.dispatch_uncertain",
                    outcome="uncertain",
                    reason_code="dispatch_start_window_elapsed",
                    request_id=checked_request_id,
                    occurred_at=effective_at,
                )
                return False
            # Both SELECT FOR UPDATE locks survive this savepoint until the
            # caller commits or rolls back its outer transaction.
            return True
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise _audit_unavailable() from exc
    except IntegrityError as exc:
        raise _concurrent_conflict() from exc


def mark_send_uncertain(
    db: Session,
    *,
    challenge_id: uuid.UUID | str,
    owner_token: str,
    request_id: str,
    reason_code: str = "provider_outcome_unknown",
    now: datetime | None = None,
) -> DispatchLifecycleResult:
    effective_at = _aware_now(now)
    checked_id = _require_uuid(challenge_id)
    checked_owner_hash = _require_dispatch_owner(owner_token)
    checked_request_id = _require_request_id(request_id)
    checked_reason = _require_dispatch_uncertain_reason(reason_code)
    _ensure_outer_transaction(db)
    try:
        with db.begin_nested():
            challenge = _locked_challenge(db, checked_id)
            if challenge is None:
                raise _error("challenge_not_found", "unauthorized", PUBLIC_INVALID_MESSAGE)
            dispatch = _locked_dispatch(db, checked_id)
            if dispatch is None:
                raise _error("dispatch_not_prepared", "conflict", "请求状态冲突")
            if dispatch.owner_token_hash != checked_owner_hash:
                raise _error("dispatch_owner_conflict", "conflict", "请求状态冲突")
            if dispatch.status in {"accepted", "uncertain"}:
                return _dispatch_lifecycle_result(dispatch, replayed=True)
            if dispatch.status != "sending":
                raise _error("dispatch_not_sending", "conflict", "请求状态冲突")
            dispatch.uncertain_at = effective_at
            _transition_dispatch(
                db,
                challenge=challenge,
                dispatch=dispatch,
                to_status="uncertain",
                action="authentication.sms.dispatch_uncertain",
                outcome="uncertain",
                reason_code=checked_reason,
                request_id=checked_request_id,
                occurred_at=effective_at,
            )
            return _dispatch_lifecycle_result(dispatch)
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
            dispatch = (
                _locked_dispatch(db, challenge.id)
                if challenge.verification_mode == "provider_managed"
                else None
            )
            if _as_utc(challenge.expires_at) <= effective_at:
                if dispatch is not None and dispatch.status in {
                    "sending",
                    "uncertain",
                }:
                    if dispatch.uncertain_at is None:
                        dispatch.uncertain_at = effective_at
                    dispatch.expired_at = effective_at
                    _transition_dispatch(
                        db,
                        challenge=challenge,
                        dispatch=dispatch,
                        to_status="expired",
                        action="authentication.sms.dispatch_expired",
                        outcome="expired",
                        reason_code="challenge_expired",
                        request_id=checked_request_id,
                        occurred_at=effective_at,
                        deferred_events=deferred_events,
                    )
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
            else:
                if challenge.verification_mode == "provider_managed":
                    if dispatch is None or dispatch.status == "prepared":
                        raise _error(
                            "challenge_not_sent",
                            "conflict",
                            "请求状态冲突",
                        )
                    if dispatch.status == "sending":
                        if _as_utc(dispatch.lease_expires_at) > effective_at:
                            raise _error(
                                "challenge_not_sent",
                                "conflict",
                                "请求状态冲突",
                            )
                        dispatch.uncertain_at = effective_at
                        _transition_dispatch(
                            db,
                            challenge=challenge,
                            dispatch=dispatch,
                            to_status="uncertain",
                            action="authentication.sms.dispatch_uncertain",
                            outcome="uncertain",
                            reason_code="dispatch_lease_expired",
                            request_id=checked_request_id,
                            occurred_at=effective_at,
                            deferred_events=deferred_events,
                        )
                    elif dispatch.status not in {"accepted", "uncertain"}:
                        raise _error(
                            "challenge_not_sent",
                            "conflict",
                            "请求状态冲突",
                        )
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
                dispatch = _locked_dispatch(db, checked_id)
                if (
                    challenge.code_hash is not None
                    or dispatch is None
                    or dispatch.status not in {"accepted", "uncertain"}
                    or (
                        dispatch.status == "accepted"
                        and (
                            challenge.provider_reference is None
                            or challenge.provider_reference
                            != dispatch.provider_reference
                        )
                    )
                    or (
                        dispatch.status == "uncertain"
                        and challenge.provider_reference is not None
                    )
                ):
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
                aggregate_type=event.aggregate_type,
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
    dispatch_request_profile_sha256: str,
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
        "dispatch_request_profile_sha256": dispatch_request_profile_sha256,
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


def _active_rate_denial(
    db: Session,
    *,
    reason_code: str,
    mobile_hash: str,
    requested_ip_hash: str,
    now: datetime,
    send_interval_seconds: int,
) -> LoginChallenge | None:
    scope_filter = (
        LoginChallenge.requested_ip_hash == requested_ip_hash
        if reason_code == "ip_hour_limit"
        else LoginChallenge.mobile_hash == mobile_hash
    )
    active_since = now - timedelta(
        seconds=(
            send_interval_seconds
            if reason_code == "send_interval"
            else 60 * 60
        )
    )
    candidates = list(
        db.scalars(
            select(LoginChallenge)
            .where(
                LoginChallenge.status == "cancelled",
                LoginChallenge.created_at >= active_since,
                scope_filter,
            )
            .order_by(LoginChallenge.created_at.desc(), LoginChallenge.id.desc())
            .with_for_update()
            .limit(8)
        )
    )
    for candidate in candidates:
        if _initial_rate_denial_reason(db, candidate) == reason_code:
            return candidate
    return None


def _unresolved_dispatch(
    db: Session,
    *,
    mobile_hash: str,
    provider: str,
    now: datetime,
    request_id: str,
) -> SmsChallengeDispatch | None:
    candidate_id = db.scalar(
        select(SmsChallengeDispatch.challenge_id)
        .join(
            LoginChallenge,
            LoginChallenge.id == SmsChallengeDispatch.challenge_id,
        )
        .where(
            SmsChallengeDispatch.mobile_hash == mobile_hash,
            SmsChallengeDispatch.provider == provider,
            SmsChallengeDispatch.status.in_(("sending", "uncertain")),
        )
        .order_by(LoginChallenge.created_at.desc(), LoginChallenge.id.desc())
        .limit(1)
    )
    if candidate_id is None:
        return None
    # Use the same explicit challenge -> dispatch lock order as verification
    # and background completion.  The candidate lookup itself is read-only;
    # after both locks are held every predicate is revalidated.
    challenge = _locked_challenge(db, candidate_id)
    dispatch = _locked_dispatch(db, candidate_id)
    if (
        challenge is None
        or dispatch is None
        or challenge.status != "pending"
        or dispatch.mobile_hash != mobile_hash
        or dispatch.provider != provider
        or dispatch.status not in {"sending", "uncertain"}
    ):
        return None
    if _as_utc(challenge.expires_at) <= now:
        if dispatch.uncertain_at is None:
            dispatch.uncertain_at = now
        dispatch.expired_at = now
        _transition_dispatch(
            db,
            challenge=challenge,
            dispatch=dispatch,
            to_status="expired",
            action="authentication.sms.dispatch_expired",
            outcome="expired",
            reason_code="challenge_expired",
            request_id=request_id,
            occurred_at=now,
        )
        _transition(
            db,
            challenge=challenge,
            to_status="expired",
            actor_user_id=None,
            action="authentication.sms.challenge_expired",
            outcome="rejected",
            reason_code="challenge_expired",
            request_id=request_id,
            occurred_at=now,
        )
        return None
    if (
        dispatch.status == "sending"
        and _as_utc(dispatch.lease_expires_at) <= now
    ):
        dispatch.uncertain_at = now
        _transition_dispatch(
            db,
            challenge=challenge,
            dispatch=dispatch,
            to_status="uncertain",
            action="authentication.sms.dispatch_uncertain",
            outcome="uncertain",
            reason_code="dispatch_lease_expired",
            request_id=request_id,
            occurred_at=now,
        )
    return dispatch


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

    latest = mobile_rows[-1] if mobile_rows else None
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


def _new_dispatch(
    *,
    challenge: LoginChallenge,
    provider: str,
    request_profile_sha256: str,
    created_at: datetime,
) -> SmsChallengeDispatch:
    request_sha256 = hashlib.sha256(
        (
            "formal-sms-dispatch-request-v1|"
            f"{challenge.id}|{request_profile_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    return SmsChallengeDispatch(
        challenge_id=challenge.id,
        provider=provider,
        mobile_hash=challenge.mobile_hash,
        status="prepared",
        request_sha256=request_sha256,
        owner_token_hash=None,
        provider_reference=None,
        claimed_at=None,
        lease_expires_at=None,
        accepted_at=None,
        uncertain_at=None,
        expired_at=None,
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


def _record_initial_dispatch_state(
    db: Session,
    *,
    challenge: LoginChallenge,
    dispatch: SmsChallengeDispatch,
    actor_user_id: str | None,
    request_id: str,
    occurred_at: datetime,
) -> None:
    add_authentication_state_transition(
        db,
        aggregate_type="sms_dispatch",
        aggregate_id=str(dispatch.challenge_id),
        actor_user_id=actor_user_id,
        from_status=None,
        to_status=dispatch.status,
        reason_code="dispatch_prepared",
        request_id=request_id,
        occurred_at=occurred_at,
    )
    _append_dispatch_event(
        db,
        challenge=challenge,
        dispatch=dispatch,
        actor_user_id=actor_user_id,
        action="authentication.sms.dispatch_prepared",
        outcome="accepted",
        reason_code="dispatch_prepared",
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


def _transition_dispatch(
    db: Session,
    *,
    challenge: LoginChallenge,
    dispatch: SmsChallengeDispatch,
    to_status: str,
    action: str,
    outcome: str,
    reason_code: str,
    request_id: str,
    occurred_at: datetime,
    deferred_events: list[DeferredChallengeAuditEvent] | None = None,
) -> None:
    before_status = dispatch.status
    dispatch.status = to_status
    add_authentication_state_transition(
        db,
        aggregate_type="sms_dispatch",
        aggregate_id=str(dispatch.challenge_id),
        actor_user_id=None,
        from_status=before_status,
        to_status=to_status,
        reason_code=reason_code,
        request_id=request_id,
        occurred_at=occurred_at,
    )
    _append_dispatch_event(
        db,
        challenge=challenge,
        dispatch=dispatch,
        actor_user_id=None,
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


def _append_dispatch_event(
    db: Session,
    *,
    challenge: LoginChallenge,
    dispatch: SmsChallengeDispatch,
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
                aggregate_type="sms_dispatch",
                aggregate_id=str(dispatch.challenge_id),
                request_id=request_id,
                client_type=challenge.client_type,
                outcome=outcome,
                reason_code=reason_code,
                occurred_at=occurred_at,
                before_status=before_status,
                after_status=dispatch.status,
            )
        )
        return
    append_authentication_event(
        db,
        actor_user_id=actor_user_id,
        action=action,
        aggregate_type="sms_dispatch",
        aggregate_id=str(dispatch.challenge_id),
        request_id=request_id,
        client_type=challenge.client_type,
        outcome=outcome,
        reason_code=reason_code,
        occurred_at=occurred_at,
        before_status=before_status,
        after_status=dispatch.status,
    )


def _locked_challenge(db: Session, challenge_id: uuid.UUID) -> LoginChallenge | None:
    return db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.id == challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _locked_dispatch(
    db: Session,
    challenge_id: uuid.UUID,
) -> SmsChallengeDispatch | None:
    return db.scalar(
        select(SmsChallengeDispatch)
        .where(SmsChallengeDispatch.challenge_id == challenge_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _preparation_result(
    db: Session,
    challenge: LoginChallenge,
    *,
    replayed: bool,
    retry_after: int,
    expires_in: int,
) -> ChallengePreparation:
    dispatch = db.get(SmsChallengeDispatch, challenge.id)
    return ChallengePreparation(
        challenge_id=challenge.id,
        status=challenge.status,
        dispatch_status=dispatch.status if dispatch is not None else None,
        dispatch_required=(dispatch is not None and dispatch.status == "prepared"),
        replayed=replayed,
        public_message=PUBLIC_REQUEST_MESSAGE,
        retry_after=retry_after,
        expires_in=expires_in,
    )


def _dispatch_claim_result(
    dispatch: SmsChallengeDispatch,
    *,
    replayed: bool,
) -> DispatchClaim:
    return DispatchClaim(
        challenge_id=dispatch.challenge_id,
        dispatch_status=dispatch.status,
        owner_token=None,
        replayed=replayed,
    )


def _dispatch_lifecycle_result(
    dispatch: SmsChallengeDispatch,
    *,
    replayed: bool = False,
) -> DispatchLifecycleResult:
    return DispatchLifecycleResult(
        challenge_id=dispatch.challenge_id,
        dispatch_status=dispatch.status,
        replayed=replayed,
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


def _dispatch_owner_hash(owner_token: str) -> str:
    return _domain_hash("formal-sms-dispatch-owner-v1", owner_token)


def _require_dispatch_owner(owner_token: str) -> str:
    if (
        not isinstance(owner_token, str)
        or re.fullmatch(r"[0-9a-f]{32}", owner_token, re.ASCII) is None
    ):
        raise _error("invalid_dispatch_owner", "invalid_request", "请求参数无效")
    return _dispatch_owner_hash(owner_token)


def _require_dispatch_uncertain_reason(value: str) -> str:
    if value not in {
        "provider_outcome_unknown",
        "provider_response_invalid",
        "provider_out_id_mismatch",
    }:
        raise _error(
            "invalid_dispatch_uncertain_reason",
            "invalid_request",
            "请求参数无效",
        )
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
    "DispatchClaim",
    "DispatchLifecycleResult",
    "VerificationAttempt",
    "authorize_dispatch_provider_call",
    "begin_verify",
    "claim_dispatch",
    "consume_verified",
    "finish_verify",
    "flush_deferred_audit_events",
    "mark_send_uncertain",
    "mark_sent",
    "prepare",
]
