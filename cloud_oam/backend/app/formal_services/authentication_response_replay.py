"""Strict reconstruction of one encrypted formal-authentication response.

The idempotency ledger stores an AES-GCM envelope because a successful login or
refresh response contains bearer credentials that cannot be recovered from the
one-way token history.  Decryption alone is never enough to return those
credentials: every replay re-locks the referenced session and output refresh
token, revalidates the current formal principal, and verifies the cached JWT and
token hashes against database facts.

This module never commits.  Callers own the surrounding transaction and decide
whether the restored response is rendered as secure web cookies or a mini-
program token body.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth_sessions import (
    SessionError,
    SessionTokens,
    require_current_session_ip_evidence,
    session_is_active,
)
from ..config import get_settings
from ..formal_access import FormalAccessError, load_formal_principal
from ..foundation_models import (
    AuthIdempotencyOperation,
    AuthRefreshToken,
    Organization,
    Person,
)
from ..models import AuthSession, User
from ..schemas import FormalSelfOut, FormalTokenSessionOut
from ..security import decode_access_token, hash_refresh_token


SESSION_RESPONSE_SCHEMA = "formal_auth_session_response_v1"
ERROR_RESPONSE_SCHEMA = "formal_auth_error_response_v1"
LOGOUT_RESPONSE_SCHEMA = "formal_auth_logout_response_v1"

_ACCESS_TOKEN = re.compile(
    r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$", re.ASCII
)
_REFRESH_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,512}$", re.ASCII)
_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$", re.ASCII)
_ALLOWED_ERROR_DETAIL_KEYS = frozenset(
    {"code", "category", "message", "retry_after"}
)
_FORMAL_SELF_KEYS = frozenset(FormalSelfOut.model_fields)
_SESSION_OPERATION_TYPES = frozenset(
    {"sms_login", "wechat_login", "session_refresh"}
)
_ALLOWED_CLIENTS_BY_OPERATION = {
    "sms_login": frozenset({"web", "miniprogram"}),
    "wechat_login": frozenset({"miniprogram"}),
    "session_refresh": frozenset({"web", "miniprogram"}),
    "session_logout": frozenset({"web", "miniprogram"}),
}


def _access_token_has_canonical_base64url_segments(token: str) -> bool:
    """Reject alternate JWT spellings that decode to the same signed bytes."""

    for segment in token.split("."):
        try:
            decoded = base64.urlsafe_b64decode(
                segment + ("=" * (-len(segment) % 4))
            )
        except (binascii.Error, ValueError):
            return False
        canonical = (
            base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        )
        if canonical != segment:
            return False
    return True


class AuthenticationResponseReplayError(RuntimeError):
    """Non-sensitive failure raised before cached credentials are returned."""

    def __init__(
        self,
        *,
        code: str,
        category: str,
        public_message: str,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.category = category
        self.public_message = public_message

    @property
    def http_status_code(self) -> int:
        return 401 if self.category == "unauthorized" else 503

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.public_message,
        }


@dataclass(frozen=True, slots=True)
class RestoredFormalSessionResponse:
    user: FormalSelfOut
    tokens: SessionTokens

    def token_body(self) -> FormalTokenSessionOut:
        return FormalTokenSessionOut(
            access_token=self.tokens.access_token,
            expires_in=self.tokens.access_expires_in,
            refresh_token=self.tokens.refresh_token,
            refresh_expires_in=self.tokens.refresh_expires_in,
            session_id=self.tokens.auth_session.id,
            user=self.user,
        )


@dataclass(frozen=True, slots=True)
class RestoredFormalErrorResponse:
    detail: str | dict[str, str | int]
    retry_after: int | None
    http_status: int


def session_response_payload(
    *,
    user: FormalSelfOut,
    tokens: SessionTokens,
) -> dict[str, object]:
    """Return the exact credential envelope that is encrypted before commit."""

    return {
        "schema": SESSION_RESPONSE_SCHEMA,
        "user": user.model_dump(mode="json"),
        "session_id": tokens.auth_session.id,
        "access_token": tokens.access_token,
        "expires_in": tokens.access_expires_in,
        "refresh_token": tokens.refresh_token,
        "refresh_expires_in": tokens.refresh_expires_in,
    }


def restore_session_response(
    db: Session,
    *,
    operation: AuthIdempotencyOperation,
    payload: dict[str, object],
    now: datetime | None = None,
) -> RestoredFormalSessionResponse:
    """Validate decrypted credentials against current locked database facts."""

    effective_now = _effective_now(now)
    _validate_terminal_operation(
        operation,
        expected_status="completed",
        expected_http_status=200,
        allowed_operation_types=_SESSION_OPERATION_TYPES,
        now=effective_now,
    )
    expected_keys = {
        "schema",
        "user",
        "session_id",
        "access_token",
        "expires_in",
        "refresh_token",
        "refresh_expires_in",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema") != SESSION_RESPONSE_SCHEMA
    ):
        _evidence_failure("cached_response_schema_invalid")

    session_id = payload.get("session_id")
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    access_expires_in = payload.get("expires_in")
    refresh_expires_in = payload.get("refresh_expires_in")
    if (
        not isinstance(session_id, str)
        or not isinstance(access_token, str)
        or _ACCESS_TOKEN.fullmatch(access_token) is None
        or not isinstance(refresh_token, str)
        or _REFRESH_TOKEN.fullmatch(refresh_token) is None
        or not isinstance(access_expires_in, int)
        or isinstance(access_expires_in, bool)
        or access_expires_in <= 0
        or access_expires_in > 24 * 60 * 60
        or not isinstance(refresh_expires_in, int)
        or isinstance(refresh_expires_in, bool)
        or refresh_expires_in <= 0
        or refresh_expires_in > 90 * 24 * 60 * 60
        or operation.auth_session_id != session_id
        or operation.output_refresh_token_id is None
    ):
        _evidence_failure("cached_response_fields_invalid")

    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if auth_session is None or not session_is_active(auth_session, effective_now):
        _authorization_failure("cached_session_inactive")
    runtime_settings = get_settings()
    if runtime_settings.environment == "production":
        try:
            require_current_session_ip_evidence(
                auth_session,
                hash_version=runtime_settings.identity_hash_version,
            )
        except SessionError:
            # A decrypted cached response must not bypass the same current-IP
            # evidence gate used by normal access-token and refresh paths.
            # This is deliberately read-only: legacy evidence is neither
            # rewritten nor guessed during credential replay.
            _authorization_failure("cached_session_ip_evidence_invalid")
    if auth_session.client_type != operation.client_type:
        _evidence_failure("cached_session_client_mismatch")

    output_token = db.scalar(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.id == operation.output_refresh_token_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        output_token is None
        or output_token.session_id != auth_session.id
        or output_token.consumed_at is not None
        or output_token.revoked_at is not None
        or output_token.token_hash != hash_refresh_token(refresh_token)
    ):
        _authorization_failure("cached_refresh_token_inactive")
    if auth_session.refresh_token_hash != output_token.token_hash:
        _evidence_failure("cached_refresh_projection_invalid")

    active_token_ids = tuple(
        db.scalars(
            select(AuthRefreshToken.id)
            .where(
                AuthRefreshToken.session_id == auth_session.id,
                AuthRefreshToken.consumed_at.is_(None),
                AuthRefreshToken.revoked_at.is_(None),
            )
            .with_for_update()
        )
    )
    if active_token_ids != (output_token.id,):
        _evidence_failure("cached_active_refresh_set_invalid")

    operation_created_at = _as_utc(operation.created_at)
    operation_completed_at = _as_utc(operation.completed_at)
    output_issued_at = _as_utc(output_token.issued_at)
    if not operation_created_at <= output_issued_at <= operation_completed_at:
        _evidence_failure("cached_refresh_issue_time_invalid")

    if operation.operation_type == "session_refresh":
        input_token = db.scalar(
            select(AuthRefreshToken)
            .where(AuthRefreshToken.id == operation.input_refresh_token_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            input_token is None
            or input_token.session_id != auth_session.id
            or input_token.consumed_at is None
            or input_token.revoked_at is not None
            or input_token.replaced_by_id != output_token.id
            or _as_utc(input_token.issued_at) > _as_utc(input_token.consumed_at)
            or _as_utc(input_token.consumed_at) > output_issued_at
        ):
            _evidence_failure("cached_refresh_chain_invalid")
    elif operation.operation_type in {"sms_login", "wechat_login"}:
        if operation.input_refresh_token_id is not None:
            _evidence_failure("cached_login_input_token_invalid")
        predecessor_id = db.scalar(
            select(AuthRefreshToken.id)
            .where(AuthRefreshToken.replaced_by_id == output_token.id)
            .limit(1)
        )
        if predecessor_id is not None:
            _evidence_failure("cached_login_refresh_chain_invalid")

    try:
        if not _access_token_has_canonical_base64url_segments(access_token):
            raise ValueError("access token uses non-canonical base64url encoding")
        token_user_id, token_session_id = decode_access_token(access_token)
    except Exception as exc:  # PyJWT exposes several verification subclasses.
        raise AuthenticationResponseReplayError(
            code="cached_access_token_invalid",
            category="unauthorized",
            public_message="登录已失效，请重新登录",
        ) from exc
    if token_user_id != auth_session.user_id or token_session_id != auth_session.id:
        _evidence_failure("cached_access_token_binding_invalid")

    user = db.get(User, auth_session.user_id)
    if user is None:
        _authorization_failure("cached_account_unavailable")
    current_user = _formal_self(db, user, now=effective_now)
    cached_user_document = payload.get("user")
    if (
        not isinstance(cached_user_document, dict)
        or set(cached_user_document) != _FORMAL_SELF_KEYS
    ):
        _evidence_failure("cached_user_schema_invalid")
    try:
        cached_user = FormalSelfOut.model_validate(cached_user_document)
    except ValidationError as exc:
        raise AuthenticationResponseReplayError(
            code="cached_user_schema_invalid",
            category="service_unavailable",
            public_message="认证幂等证据不可用",
        ) from exc
    if cached_user != current_user:
        _authorization_failure("cached_authorization_changed")

    return RestoredFormalSessionResponse(
        user=current_user,
        tokens=SessionTokens(
            auth_session=auth_session,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_in=access_expires_in,
            refresh_expires_in=refresh_expires_in,
        ),
    )


def error_response_payload(
    *,
    detail: str | dict[str, object],
    retry_after: int | None = None,
) -> dict[str, object]:
    checked_detail = _safe_error_detail(detail)
    checked_retry = _safe_retry_after(retry_after)
    detail_retry = (
        checked_detail.get("retry_after")
        if isinstance(checked_detail, dict)
        else None
    )
    if detail_retry is not None:
        if checked_retry is None:
            checked_retry = detail_retry
        elif checked_retry != detail_retry:
            _evidence_failure("cached_retry_after_mismatch")
    return {
        "schema": ERROR_RESPONSE_SCHEMA,
        "detail": checked_detail,
        "retry_after": checked_retry,
    }


def restore_error_response(
    *,
    operation: AuthIdempotencyOperation,
    payload: dict[str, object],
    now: datetime | None = None,
) -> RestoredFormalErrorResponse:
    _validate_terminal_operation(
        operation,
        expected_status="failed",
        expected_http_status=None,
        allowed_operation_types=frozenset(
            {"sms_login", "wechat_login", "session_refresh", "session_logout"}
        ),
        now=_effective_now(now),
    )
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "detail", "retry_after"}
        or payload.get("schema") != ERROR_RESPONSE_SCHEMA
    ):
        _evidence_failure("cached_error_schema_invalid")
    checked_detail = _safe_error_detail(payload.get("detail"))
    checked_retry = _safe_retry_after(payload.get("retry_after"))
    detail_retry = (
        checked_detail.get("retry_after")
        if isinstance(checked_detail, dict)
        else None
    )
    if detail_retry is not None and detail_retry != checked_retry:
        _evidence_failure("cached_retry_after_mismatch")
    return RestoredFormalErrorResponse(
        detail=checked_detail,
        retry_after=checked_retry,
        http_status=operation.http_status,
    )


def logout_response_payload() -> dict[str, object]:
    return {"schema": LOGOUT_RESPONSE_SCHEMA, "ok": True}


def restore_logout_response(
    *,
    operation: AuthIdempotencyOperation,
    payload: dict[str, object],
    now: datetime | None = None,
) -> dict[str, bool]:
    _validate_terminal_operation(
        operation,
        expected_status="completed",
        expected_http_status=200,
        allowed_operation_types=frozenset({"session_logout"}),
        now=_effective_now(now),
    )
    if operation.output_refresh_token_id is not None:
        _evidence_failure("cached_logout_output_token_invalid")
    if payload != {"schema": LOGOUT_RESPONSE_SCHEMA, "ok": True}:
        _evidence_failure("cached_logout_schema_invalid")
    return {"ok": True}


def _formal_self(
    db: Session,
    user: User,
    *,
    now: datetime,
) -> FormalSelfOut:
    try:
        principal = load_formal_principal(db, user.id, now=now)
    except FormalAccessError as exc:
        raise AuthenticationResponseReplayError(
            code="cached_account_authorization_invalid",
            category="unauthorized",
            public_message="登录已失效，请重新登录",
        ) from exc
    person = db.get(Person, principal.person_id)
    organization = db.get(Organization, person.organization_id) if person else None
    if person is None or organization is None:
        _authorization_failure("cached_person_mapping_invalid")
    return FormalSelfOut(
        person_id=person.id,
        name=person.name,
        employee_no=person.employee_no,
        organization_code=organization.code,
        organization_name=organization.name,
        account_status=principal.account_status,
        employment_status=principal.employment_status,
        access_mode=principal.access_mode,
        authorization_version=principal.authorization_version,
        role_codes=list(principal.role_codes),
    )


def _safe_error_detail(value: object) -> str | dict[str, str | int]:
    if isinstance(value, str):
        if not value or len(value) > 160 or any(char in value for char in "\r\n"):
            _evidence_failure("cached_error_detail_invalid")
        return value
    if (
        not isinstance(value, dict)
        or not {"code", "category", "message"}.issubset(value)
        or not set(value).issubset(_ALLOWED_ERROR_DETAIL_KEYS)
    ):
        _evidence_failure("cached_error_detail_invalid")
    checked: dict[str, str | int] = {}
    for name, item in value.items():
        if name == "retry_after":
            checked_retry = _safe_retry_after(item)
            if checked_retry is None:
                _evidence_failure("cached_error_detail_invalid")
            checked[name] = checked_retry
            continue
        if not isinstance(item, str) or not item or len(item) > 160:
            _evidence_failure("cached_error_detail_invalid")
        if name in {"code", "category"} and _SAFE_ERROR_CODE.fullmatch(item) is None:
            _evidence_failure("cached_error_detail_invalid")
        if any(char in item for char in "\r\n"):
            _evidence_failure("cached_error_detail_invalid")
        checked[name] = item
    return checked


def _safe_retry_after(value: object) -> int | None:
    if value is None:
        return None
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > 3600
    ):
        _evidence_failure("cached_retry_after_invalid")
    return value


def _validate_terminal_operation(
    operation: AuthIdempotencyOperation,
    *,
    expected_status: str,
    expected_http_status: int | None,
    allowed_operation_types: frozenset[str],
    now: datetime,
) -> None:
    if not isinstance(operation, AuthIdempotencyOperation):
        _evidence_failure("cached_operation_invalid")
    if operation.status != expected_status:
        _evidence_failure("cached_operation_status_invalid")
    if operation.operation_type not in allowed_operation_types:
        _evidence_failure("cached_operation_type_invalid")
    if operation.client_type not in _ALLOWED_CLIENTS_BY_OPERATION.get(
        operation.operation_type,
        frozenset(),
    ):
        _evidence_failure("cached_operation_client_invalid")
    if (
        not isinstance(operation.http_status, int)
        or isinstance(operation.http_status, bool)
        or operation.http_status < 200
        or operation.http_status > 599
        or (expected_status == "completed" and operation.http_status >= 300)
        or (expected_status == "failed" and operation.http_status < 400)
        or (
            expected_http_status is not None
            and operation.http_status != expected_http_status
        )
    ):
        _evidence_failure("cached_operation_http_status_invalid")
    if expected_status == "failed" and operation.output_refresh_token_id is not None:
        _evidence_failure("cached_failed_operation_output_token_invalid")
    if (
        operation.created_at is None
        or operation.completed_at is None
        or operation.expires_at is None
    ):
        _evidence_failure("cached_operation_time_invalid")
    try:
        created_at = _as_utc(operation.created_at)
        completed_at = _as_utc(operation.completed_at)
        expires_at = _as_utc(operation.expires_at)
    except (AttributeError, TypeError, ValueError) as exc:
        raise AuthenticationResponseReplayError(
            code="cached_operation_time_invalid",
            category="service_unavailable",
            public_message="认证幂等证据不可用",
        ) from exc
    if not created_at <= completed_at < expires_at:
        _evidence_failure("cached_operation_time_invalid")
    if expires_at <= now:
        _evidence_failure("cached_operation_expired")


def _effective_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        _evidence_failure("cached_replay_time_invalid")
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _authorization_failure(code: str) -> None:
    raise AuthenticationResponseReplayError(
        code=code,
        category="unauthorized",
        public_message="登录已失效，请重新登录",
    )


def _evidence_failure(code: str) -> None:
    raise AuthenticationResponseReplayError(
        code=code,
        category="service_unavailable",
        public_message="认证幂等证据不可用",
    )


__all__ = [
    "AuthenticationResponseReplayError",
    "RestoredFormalErrorResponse",
    "RestoredFormalSessionResponse",
    "error_response_payload",
    "logout_response_payload",
    "restore_error_response",
    "restore_logout_response",
    "restore_session_response",
    "session_response_payload",
]
