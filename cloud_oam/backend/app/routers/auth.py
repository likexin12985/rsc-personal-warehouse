from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
import uuid

from fastapi import APIRouter, Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..authorization import FORMAL_ROLE_CODES
from ..config import get_settings
from ..database import get_db
from ..dependencies import client_ip, get_current_user, require_roles
from ..auth_sessions import (
    RefreshTokenReplayError,
    SessionError,
    SessionTokens,
    create_session,
    hash_login_challenge_ip_address,
    protect_session_ip_address,
    require_current_session_ip_evidence,
    revoke_by_refresh_token,
    revoke_by_refresh_token_with_result,
    revoke_session,
    rotate_session,
    session_is_active,
)
from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..formal_services import (
    AuditChainError,
    AuthenticationEvidenceError,
    authentication_challenge,
)
from ..formal_services.authentication_challenge import AuthenticationChallengeError
from ..formal_services.authentication_audit import append_authentication_event
from ..formal_services.authentication_idempotency import (
    Aes256GcmAuthenticationResponseCipher,
    AuthenticationEncryptionKeyUnavailable,
    AuthenticationIdempotencyBeginResult,
    AuthenticationIdempotencyError,
    begin_authentication_operation,
    complete_authentication_failure,
    complete_authentication_success,
    create_configured_authentication_response_cipher,
    probe_existing_authentication_operation,
)
from ..formal_services.authentication_rate_limit import (
    AuthenticationLoginRateLimitError,
    consume_authentication_login_rate_limits,
)
from ..formal_services.authentication_identity import (
    AuthenticationIdentityError,
    AuthenticationIdentityUnavailable,
    compute_identity_hash,
    find_active_formal_identity,
    normalize_mobile,
    resolve_active_formal_identity,
)
from ..formal_services.authentication_response_replay import (
    AuthenticationResponseReplayError,
    error_response_payload,
    logout_response_payload,
    restore_error_response,
    restore_logout_response,
    restore_session_response,
    session_response_payload,
)
from ..formal_services.authentication_session_admin import (
    AuthenticationSessionAdminError,
    force_revoke_session,
)
from ..formal_services.authentication_session import (
    record_session_created,
    record_session_replay_revocation,
    record_session_revoked,
    record_session_rotated,
)
from ..foundation_models import (
    AuditChainHead,
    AuthIdempotencyOperation,
    AuthRefreshToken,
    Organization,
    Person,
)
from ..models import AuthSession, SmsLoginChallenge, User, WechatIdentity
from ..schemas import (
    AuthSessionOut,
    FormalAuthSessionOut,
    FormalSelfOut,
    FormalSessionRevokeOut,
    FormalTokenSessionOut,
    LoginIn,
    LoginOptionsOut,
    MiniProgramPasswordLoginIn,
    MiniProgramSmsLoginIn,
    MiniProgramWechatLoginIn,
    PasswordChangeIn,
    SessionLogoutIn,
    SessionRefreshIn,
    SmsCodeLoginIn,
    SmsCodeRequestIn,
    SmsCodeRequestOut,
    TokenSessionOut,
    UserCreateIn,
    UserOut,
)
from ..security import hash_password, hash_refresh_token, verify_password
from ..services import audit
from ..sms import SmsProviderError, get_sms_provider
from ..wechat import WechatLoginIdentity, WechatProviderError, get_wechat_provider


router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
ALLOWED_ROLES = set(FORMAL_ROLE_CODES)

SMS_REQUEST_MESSAGE = "如账号已开通，验证码将发送至该手机号"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{7,159}$", re.ASCII)
_SAFE_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{16,128}$", re.ASCII)
_SAFE_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$", re.ASCII)


class FormalAuthenticationHTTPException(HTTPException):
    """Formal auth error whose response must clear local Web credentials."""


async def formal_authentication_http_exception_handler(
    _request: Request,
    exc: FormalAuthenticationHTTPException,
) -> JSONResponse:
    """Render an auth error with two independent cookie-deletion headers.

    ``HTTPException.headers`` is a mapping and cannot represent repeated
    ``Set-Cookie`` fields.  A dedicated handler is therefore required; joining
    access and refresh cookies into one header is not valid Set-Cookie syntax.
    """

    response = JSONResponse(
        status_code=exc.status_code,
        content={"detail": jsonable_encoder(exc.detail)},
        headers=exc.headers,
    )
    _clear_session_cookies(response)
    # Set-Cookie is intentionally hidden from browser JavaScript.  This
    # non-sensitive acknowledgement lets the Web client distinguish an
    # application response that cleared both HttpOnly credentials from a proxy
    # or transport failure that may have left them intact.
    response.headers["X-Auth-Credentials-Cleared"] = "true"
    return response


def install_formal_authentication_exception_handler(app: FastAPI) -> None:
    app.add_exception_handler(
        FormalAuthenticationHTTPException,
        formal_authentication_http_exception_handler,
    )


@dataclass(frozen=True, slots=True)
class _FormalAuthenticationWrite:
    operation_type: str
    client_type: str
    idempotency_key: str
    scope: dict[str, object]
    request_document: dict[str, object]
    auth_session_id: str | None
    input_refresh_token_id: uuid.UUID | None
    cipher: Aes256GcmAuthenticationResponseCipher
    begin: AuthenticationIdempotencyBeginResult


def _production() -> bool:
    return settings.environment == "production"


def _formal_request_id(request: Request) -> str:
    value = request.headers.get("x-request-id", "")
    if _SAFE_REQUEST_ID.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="缺少有效的请求标识")
    return value


def _formal_idempotency_key(request: Request) -> str:
    value = request.headers.get("idempotency-key", "")
    if _SAFE_IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="缺少有效的幂等键")
    return value


def _probe_existing_formal_login_operation(
    db: Session,
    request: Request,
    *,
    operation_type: str,
    client_type: str,
    scope: dict[str, object],
    request_document: dict[str, object],
) -> bool:
    """Return whether this exact key already owns terminal replay evidence.

    The probe is read-only and KMS-free.  An absent result rolls back its read
    transaction before the independent limiter opens a second connection.
    Existing mismatched/pending/expired evidence keeps the normal 409/503
    contract and never reaches a provider.
    """

    try:
        existing = probe_existing_authentication_operation(
            db,
            operation_type=operation_type,
            client_type=client_type,
            idempotency_key=_formal_idempotency_key(request),
            scope=scope,
            request=request_document,
            hmac_secret=settings.auth_idempotency_hmac_secret,
        )
    except AuthenticationIdempotencyError as exc:
        _raise_authentication_idempotency_error(db, exc)
    if existing is None:
        db.rollback()
        return False
    return True


def _consume_formal_login_rate_limits(
    db: Session,
    request: Request,
    *,
    operation_type: str,
    include_global_and_ip: bool,
    identity_identifier: str | None = None,
) -> None:
    """Consume login admission counters outside the authentication transaction."""

    if db.in_transaction():
        # Never pretend a second Session is independent while the request
        # Session already owns business/idempotency/audit mutations.
        db.rollback()
        raise HTTPException(status_code=503, detail="登录保护服务暂时不可用")
    try:
        decision = consume_authentication_login_rate_limits(
            db.get_bind(),
            operation_type=operation_type,
            hmac_secret=settings.auth_login_rate_limit_hmac_secret,
            hash_version=settings.auth_login_rate_limit_hash_version,
            window_seconds=settings.auth_login_rate_limit_window_seconds,
            global_limit=(
                settings.auth_login_rate_limit_global_limit
                if include_global_and_ip
                else None
            ),
            ip_address_value=(
                client_ip(request) if include_global_and_ip else None
            ),
            ip_limit=(
                settings.auth_login_rate_limit_ip_limit
                if include_global_and_ip
                else None
            ),
            identity_identifier=identity_identifier,
            identity_limit=(
                settings.auth_login_rate_limit_identity_limit
                if identity_identifier is not None
                else None
            ),
        )
    except AuthenticationLoginRateLimitError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": exc.code,
                "category": "service_unavailable",
                "message": exc.public_message,
            },
        ) from exc
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail={
            "code": "authentication_login_rate_limited",
            "category": "rate_limited",
            "message": "登录请求过于频繁，请稍后再试",
        },
        headers={"Retry-After": str(decision.retry_after or 1)},
    )


def _configured_authentication_response_cipher(
) -> Aes256GcmAuthenticationResponseCipher:
    """Return the reviewed runtime cipher or fail closed.

    A deployment-owned Aliyun KMS/envelope-key adapter is intentionally not
    emulated by a plaintext environment secret.  Until that adapter is wired at
    the composition boundary, formal authentication mutations return 503
    before a challenge, provider call, session or refresh token is mutated.
    Tests replace this function with an explicit static test-key cipher.
    """

    return create_configured_authentication_response_cipher(
        settings=settings,
        kms_key_loader=None,
    )


def _raise_authentication_idempotency_error(
    db: Session,
    exc: AuthenticationIdempotencyError,
) -> None:
    db.rollback()
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
    ) from exc


def _raise_authentication_encryption_unavailable(
    db: Session,
    exc: Exception,
) -> None:
    db.rollback()
    raise HTTPException(
        status_code=503,
        detail={
            "code": "authentication_idempotency_encryption_unavailable",
            "category": "service_unavailable",
            "message": "认证幂等加密服务不可用",
        },
    ) from exc


def _begin_formal_authentication_write(
    db: Session,
    request: Request,
    *,
    operation_type: str,
    client_type: str,
    scope: dict[str, object],
    request_document: dict[str, object],
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | None = None,
) -> _FormalAuthenticationWrite:
    idempotency_key = _formal_idempotency_key(request)
    now = datetime.now(timezone.utc)
    try:
        cipher = _configured_authentication_response_cipher()
        # A fresh begin does not encrypt yet.  Resolve the active version before
        # any challenge/provider/session mutation so KMS loss fails closed.
        cipher.active_key_version()
        begin = begin_authentication_operation(
            db,
            operation_type=operation_type,
            client_type=client_type,
            idempotency_key=idempotency_key,
            scope=scope,
            request=request_document,
            hmac_secret=settings.auth_idempotency_hmac_secret,
            cipher=cipher,
            expires_at=now
            + timedelta(seconds=settings.auth_idempotency_ttl_seconds),
            now=now,
            auth_session_id=auth_session_id,
            input_refresh_token_id=input_refresh_token_id,
        )
    except AuthenticationIdempotencyError as exc:
        _raise_authentication_idempotency_error(db, exc)
    except AuthenticationEncryptionKeyUnavailable as exc:
        _raise_authentication_encryption_unavailable(db, exc)
    return _FormalAuthenticationWrite(
        operation_type=operation_type,
        client_type=client_type,
        idempotency_key=idempotency_key,
        scope=scope,
        request_document=request_document,
        auth_session_id=auth_session_id,
        input_refresh_token_id=input_refresh_token_id,
        cipher=cipher,
        begin=begin,
    )


def _restart_formal_authentication_begin(
    db: Session,
    write: _FormalAuthenticationWrite,
) -> AuthenticationIdempotencyBeginResult:
    now = datetime.now(timezone.utc)
    return begin_authentication_operation(
        db,
        operation_type=write.operation_type,
        client_type=write.client_type,
        idempotency_key=write.idempotency_key,
        scope=write.scope,
        request=write.request_document,
        hmac_secret=settings.auth_idempotency_hmac_secret,
        cipher=write.cipher,
        expires_at=now + timedelta(seconds=settings.auth_idempotency_ttl_seconds),
        now=now,
        auth_session_id=write.auth_session_id,
        input_refresh_token_id=write.input_refresh_token_id,
    )


def _current_formal_authentication_begin(
    db: Session,
    write: _FormalAuthenticationWrite,
) -> AuthenticationIdempotencyBeginResult:
    operation = db.get(AuthIdempotencyOperation, write.begin.operation.id)
    if operation is None or operation.status != "pending":
        return _restart_formal_authentication_begin(db, write)
    return AuthenticationIdempotencyBeginResult(
        operation=operation,
        disposition="execute",
    )


def _retry_after_from_http_exception(exc: HTTPException) -> int | None:
    raw_value = (exc.headers or {}).get("Retry-After")
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 3600 else None


def _idempotency_response_headers(
    *,
    replayed: bool,
    retry_after: int | None = None,
) -> dict[str, str]:
    headers = {"Idempotency-Replayed": str(replayed).lower()}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return headers


def _authentication_http_exception(
    *,
    status_code: int,
    detail: object,
    headers: dict[str, str] | None = None,
    clear_session_cookies: bool = False,
) -> HTTPException:
    exception_type = (
        FormalAuthenticationHTTPException
        if clear_session_cookies
        else HTTPException
    )
    return exception_type(
        status_code=status_code,
        detail=detail,
        headers=headers,
    )


def _is_web_refresh_write(write: _FormalAuthenticationWrite) -> bool:
    return (
        write.operation_type == "session_refresh"
        and write.client_type == "web"
    )


def _raise_cached_formal_authentication_failure(
    db: Session,
    write: _FormalAuthenticationWrite,
    *,
    begin: AuthenticationIdempotencyBeginResult | None = None,
) -> None:
    effective_begin = begin or write.begin
    cached = effective_begin.response
    if cached is None or cached.status != "failed":
        db.rollback()
        raise HTTPException(status_code=503, detail="认证幂等证据不可用")
    try:
        restored = restore_error_response(
            operation=effective_begin.operation,
            payload=cached.payload,
        )
        db.commit()
    except AuthenticationResponseReplayError as exc:
        db.rollback()
        raise _authentication_http_exception(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
            headers=_idempotency_response_headers(replayed=True),
            clear_session_cookies=(
                _is_web_refresh_write(write) and exc.http_status_code == 401
            ),
        ) from exc
    raise _authentication_http_exception(
        status_code=cached.http_status,
        detail=restored.detail,
        headers=_idempotency_response_headers(
            replayed=True,
            retry_after=restored.retry_after,
        ),
        clear_session_cookies=(
            _is_web_refresh_write(write) and cached.http_status == 401
        ),
    )


def _complete_formal_authentication_failure(
    db: Session,
    write: _FormalAuthenticationWrite,
    exc: HTTPException,
) -> None:
    retry_after = _retry_after_from_http_exception(exc)
    try:
        begin = _current_formal_authentication_begin(db, write)
        if begin.replayed:
            _raise_cached_formal_authentication_failure(db, write, begin=begin)
        complete_authentication_failure(
            db,
            begin=begin,
            payload=error_response_payload(
                detail=exc.detail,
                retry_after=retry_after,
            ),
            http_status=exc.status_code,
            cipher=write.cipher,
            auth_session_id=write.auth_session_id,
            input_refresh_token_id=write.input_refresh_token_id,
        )
        db.commit()
    except AuthenticationIdempotencyError as idempotency_error:
        _raise_authentication_idempotency_error(db, idempotency_error)
    except AuthenticationEncryptionKeyUnavailable as encryption_error:
        _raise_authentication_encryption_unavailable(db, encryption_error)
    raise _authentication_http_exception(
        status_code=exc.status_code,
        detail=exc.detail,
        headers=_idempotency_response_headers(
            replayed=False,
            retry_after=retry_after,
        ),
        clear_session_cookies=(
            _is_web_refresh_write(write) and exc.status_code == 401
        ),
    ) from exc


def _refresh_token_reference(
    db: Session,
    refresh_token: str | None,
) -> tuple[str | None, uuid.UUID | None]:
    if not refresh_token:
        return None, None
    rows = list(
        db.scalars(
            select(AuthRefreshToken)
            .where(AuthRefreshToken.token_hash == hash_refresh_token(refresh_token))
            .limit(2)
        )
    )
    if len(rows) > 1:
        db.rollback()
        raise HTTPException(status_code=503, detail="认证令牌证据不可用")
    if not rows:
        return None, None
    return rows[0].session_id, rows[0].id


def _output_refresh_token_id(db: Session, refresh_token: str) -> uuid.UUID:
    rows = list(
        db.scalars(
            select(AuthRefreshToken.id)
            .where(AuthRefreshToken.token_hash == hash_refresh_token(refresh_token))
            .limit(2)
        )
    )
    if len(rows) != 1:
        raise AuthenticationResponseReplayError(
            code="authentication_output_refresh_evidence_invalid",
            category="service_unavailable",
            public_message="认证幂等证据不可用",
        )
    return rows[0]


def _restore_cached_formal_session(
    db: Session,
    write: _FormalAuthenticationWrite,
):
    cached = write.begin.response
    if cached is None:
        db.rollback()
        raise HTTPException(status_code=503, detail="认证幂等证据不可用")
    if cached.status == "failed":
        _raise_cached_formal_authentication_failure(db, write)
    try:
        restored = restore_session_response(
            db,
            operation=write.begin.operation,
            payload=cached.payload,
        )
    except AuthenticationResponseReplayError as exc:
        db.rollback()
        raise _authentication_http_exception(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
            headers=_idempotency_response_headers(replayed=True),
            clear_session_cookies=(
                _is_web_refresh_write(write) and exc.http_status_code == 401
            ),
        ) from exc
    return restored


def _complete_formal_session_success(
    db: Session,
    write: _FormalAuthenticationWrite,
    *,
    user: FormalSelfOut,
    tokens: SessionTokens,
) -> None:
    try:
        complete_authentication_success(
            db,
            begin=write.begin,
            payload=session_response_payload(user=user, tokens=tokens),
            http_status=200,
            cipher=write.cipher,
            auth_session_id=tokens.auth_session.id,
            input_refresh_token_id=write.input_refresh_token_id,
            output_refresh_token_id=_output_refresh_token_id(
                db, tokens.refresh_token
            ),
        )
        db.commit()
    except AuthenticationIdempotencyError as exc:
        _raise_authentication_idempotency_error(db, exc)
    except AuthenticationEncryptionKeyUnavailable as exc:
        _raise_authentication_encryption_unavailable(db, exc)
    except AuthenticationResponseReplayError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
        ) from exc


def _restore_cached_formal_logout(
    db: Session,
    write: _FormalAuthenticationWrite,
) -> dict[str, bool]:
    cached = write.begin.response
    if cached is None:
        db.rollback()
        raise HTTPException(status_code=503, detail="认证幂等证据不可用")
    if cached.status == "failed":
        _raise_cached_formal_authentication_failure(db, write)
    try:
        result = restore_logout_response(
            operation=write.begin.operation,
            payload=cached.payload,
        )
        db.commit()
        return result
    except AuthenticationResponseReplayError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
            headers=_idempotency_response_headers(replayed=True),
        ) from exc


def _complete_formal_logout_success(
    db: Session,
    write: _FormalAuthenticationWrite,
) -> None:
    try:
        begin = _current_formal_authentication_begin(db, write)
        if begin.replayed:
            cached = begin.response
            if cached is not None and cached.status == "failed":
                _raise_cached_formal_authentication_failure(db, write, begin=begin)
            # A concurrent same-key caller completed the same logout.  Validate
            # its terminal envelope rather than appending another audit event.
            if cached is None:
                raise AuthenticationResponseReplayError(
                    code="cached_logout_response_missing",
                    category="service_unavailable",
                    public_message="认证幂等证据不可用",
                )
            restore_logout_response(
                operation=begin.operation,
                payload=cached.payload,
            )
            db.commit()
            return
        complete_authentication_success(
            db,
            begin=begin,
            payload=logout_response_payload(),
            http_status=200,
            cipher=write.cipher,
            auth_session_id=write.auth_session_id,
            input_refresh_token_id=write.input_refresh_token_id,
        )
        db.commit()
    except AuthenticationIdempotencyError as exc:
        _raise_authentication_idempotency_error(db, exc)
    except AuthenticationEncryptionKeyUnavailable as exc:
        _raise_authentication_encryption_unavailable(db, exc)
    except AuthenticationResponseReplayError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.http_status_code,
            detail=exc.as_detail(),
        ) from exc


def _formal_mobile(value: str) -> str:
    try:
        return normalize_mobile(value)
    except AuthenticationIdentityError as exc:
        raise HTTPException(status_code=422, detail="手机号格式无效") from exc


def _formal_mobile_hash(mobile: str) -> str:
    try:
        return compute_identity_hash(
            secret=settings.identity_hash_secret,
            hash_version=settings.identity_hash_version,
            identity_type="mobile",
            provider_key=settings.sms_provider,
            identifier=mobile,
        )
    except AuthenticationIdentityError as exc:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc


def _formal_ip_hash(ip_address: str) -> str:
    """Return a challenge-only digest, isolated from session IP evidence."""

    try:
        return hash_login_challenge_ip_address(ip_address)
    except SessionError as exc:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc


def _require_formal_session_ip(request: Request) -> None:
    """Reject an unusable source address before a formal write is opened."""

    try:
        protect_session_ip_address(client_ip(request))
    except SessionError as exc:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc


def _formal_mobile_identity(db: Session, mobile: str, *, required: bool):
    arguments = {
        "secret": settings.identity_hash_secret,
        "hash_version": settings.identity_hash_version,
        "identity_type": "mobile",
        "provider_key": settings.sms_provider,
        "identifier": mobile,
    }
    try:
        if required:
            return resolve_active_formal_identity(db, **arguments)
        return find_active_formal_identity(db, **arguments)
    except AuthenticationIdentityUnavailable:
        if required:
            raise HTTPException(status_code=401, detail="验证码不正确或已失效") from None
        return None
    except AuthenticationIdentityError as exc:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc


def _formal_self(
    db: Session,
    user: User,
    *,
    principal: FormalPrincipal | None = None,
) -> FormalSelfOut:
    try:
        resolved = principal or load_formal_principal(db, user.id)
    except FormalAccessError as exc:
        raise SessionError("账号当前不可建立正式会话") from exc
    person = db.get(Person, resolved.person_id)
    organization = db.get(Organization, person.organization_id) if person else None
    if person is None or organization is None:
        raise SessionError("账号当前不可建立正式会话")
    return FormalSelfOut(
        person_id=person.id,
        name=person.name,
        employee_no=person.employee_no,
        organization_code=organization.code,
        organization_name=organization.name,
        account_status=resolved.account_status,
        employment_status=resolved.employment_status,
        access_mode=resolved.access_mode,
        authorization_version=resolved.authorization_version,
        role_codes=list(resolved.role_codes),
    )


def _formal_token_session(
    db: Session,
    user: User,
    tokens: SessionTokens,
    *,
    principal: FormalPrincipal | None = None,
) -> FormalTokenSessionOut:
    return FormalTokenSessionOut(
        access_token=tokens.access_token,
        expires_in=tokens.access_expires_in,
        refresh_token=tokens.refresh_token,
        refresh_expires_in=tokens.refresh_expires_in,
        session_id=tokens.auth_session.id,
        user=_formal_self(db, user, principal=principal),
    )


def _raise_challenge_error(
    db: Session,
    exc: AuthenticationChallengeError,
    *,
    request_id: str | None = None,
    client_type: str | None = None,
    transaction_owned: bool = False,
) -> None:
    if transaction_owned:
        if (
            not exc.mutation_persisted
            and request_id is not None
            and client_type is not None
            and exc.code != "authentication_audit_unavailable"
        ):
            _append_owned_failed_authentication_attempt(
                db,
                request_id=request_id,
                client_type=client_type,
                action="authentication.sms.login_failed",
                reason_code=exc.code,
                outcome=("failed" if exc.category == "service_unavailable" else "rejected"),
            )
    elif exc.mutation_persisted:
        db.commit()
    else:
        db.rollback()
        if (
            request_id is not None
            and client_type is not None
            and exc.code != "authentication_audit_unavailable"
        ):
            _record_failed_authentication_attempt(
                db,
                request_id=request_id,
                client_type=client_type,
                action="authentication.sms.login_failed",
                reason_code=exc.code,
                outcome=("failed" if exc.category == "service_unavailable" else "rejected"),
            )
    headers = (
        {"Retry-After": str(exc.retry_after)}
        if exc.retry_after is not None
        else None
    )
    raise HTTPException(
        status_code=exc.http_status_code,
        detail=exc.as_detail(),
        headers=headers,
    ) from exc


def _require_authentication_audit_head(db: Session) -> None:
    head = db.scalar(
        select(AuditChainHead).where(AuditChainHead.stream_key == "authentication")
    )
    valid = bool(
        head
        and head.version >= 0
        and ((head.last_event_id is None) == (head.last_hash is None))
        and ((head.version == 0) == (head.last_event_id is None))
    )
    if not valid:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用")


def _raise_authentication_evidence_unavailable(
    db: Session,
    exc: Exception,
) -> None:
    db.rollback()
    raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc


def _record_failed_authentication_attempt(
    db: Session,
    *,
    request_id: str,
    client_type: str,
    action: str,
    reason_code: str,
    outcome: str = "rejected",
) -> None:
    try:
        _append_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type=client_type,
            action=action,
            reason_code=reason_code,
            outcome=outcome,
        )
        db.commit()
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        _raise_authentication_evidence_unavailable(db, exc)


def _append_failed_authentication_attempt(
    db: Session,
    *,
    request_id: str,
    client_type: str,
    action: str,
    reason_code: str,
    outcome: str = "rejected",
) -> None:
    aggregate_id = "attempt-" + hashlib.sha256(
        request_id.encode("utf-8")
    ).hexdigest()[:48]
    append_authentication_event(
        db,
        actor_user_id=None,
        action=action,
        aggregate_type="authentication_attempt",
        aggregate_id=aggregate_id,
        request_id=request_id,
        client_type=client_type,
        outcome=outcome,
        reason_code=reason_code,
        occurred_at=datetime.now(timezone.utc),
    )


def _append_owned_failed_authentication_attempt(
    db: Session,
    *,
    request_id: str,
    client_type: str,
    action: str,
    reason_code: str,
    outcome: str = "rejected",
) -> None:
    """Append failure evidence without committing the caller-owned operation."""

    try:
        _append_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type=client_type,
            action=action,
            reason_code=reason_code,
            outcome=outcome,
        )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc


def _require_session_manager(
    request: Request,
    actor: User,
    db: Session,
) -> FormalPrincipal | None:
    if not _production():
        if actor.role != "admin":
            raise HTTPException(status_code=403, detail="没有此操作权限")
        return None
    principal = getattr(request.state, "formal_principal", None)
    if not isinstance(principal, FormalPrincipal) or not (
        principal.access_mode == "active"
        and any(
            assignment.role_code == "admin"
            and assignment.scope_type == "national"
            and assignment.scope_id == "*"
            for assignment in principal.assignments
        )
        and principal.allows(db, "auth_session", "manage")
    ):
        raise HTTPException(status_code=403, detail="没有此操作权限")
    return principal


def _set_session_cookies(response: Response, tokens: SessionTokens) -> None:
    response.set_cookie(
        "access_token",
        tokens.access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=tokens.access_expires_in,
        path="/",
    )
    response.set_cookie(
        "refresh_token",
        tokens.refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=tokens.refresh_expires_in,
        path="/",
    )
    response.set_cookie(
        "auth_device_id",
        tokens.auth_session.device_id,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    for cookie_name in ("access_token", "refresh_token"):
        response.delete_cookie(
            cookie_name,
            path="/",
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )


def _token_session(user: User, tokens: SessionTokens) -> TokenSessionOut:
    return TokenSessionOut(
        access_token=tokens.access_token,
        expires_in=tokens.access_expires_in,
        refresh_token=tokens.refresh_token,
        refresh_expires_in=tokens.refresh_expires_in,
        session_id=tokens.auth_session.id,
        user=UserOut.model_validate(user),
    )


def _request_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:500]


def _create_web_session(db: Session, user: User, request: Request) -> SessionTokens:
    presented_device_id = request.cookies.get("auth_device_id", "")
    stable_device_id = (
        presented_device_id
        if _SAFE_DEVICE_ID.fullmatch(presented_device_id) is not None
        else ""
    )
    return create_session(
        db,
        user,
        client_type="web",
        device_id=stable_device_id,
        device_name="PC网页",
        ip_address=client_ip(request),
        user_agent=_request_user_agent(request),
    )


def _create_miniprogram_session(
    db: Session,
    user: User,
    request: Request,
    *,
    device_id: str,
    device_name: str,
) -> SessionTokens:
    return create_session(
        db,
        user,
        client_type="miniprogram",
        device_id=device_id,
        device_name=device_name,
        ip_address=client_ip(request),
        user_agent=_request_user_agent(request),
    )


def _authenticate_password(payload: LoginIn, db: Session) -> User:
    if not settings.password_login_enabled:
        raise HTTPException(status_code=403, detail="密码登录已关闭")
    user = db.scalar(select(User).where(User.mobile == payload.mobile.strip()))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="手机号或密码错误")
    return user


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _masked_mobile(mobile: str) -> str:
    return f"{mobile[:3]}****{mobile[-4:]}"


def _require_sms_configuration() -> None:
    if not settings.sms_configuration_ready():
        raise HTTPException(status_code=503, detail="短信验证码登录尚未启用")


def _request_formal_sms_code(
    payload: SmsCodeRequestIn,
    request: Request,
    db: Session,
    *,
    client_type: str,
) -> SmsCodeRequestOut:
    _require_sms_configuration()
    request_id = _formal_request_id(request)
    idempotency_key = _formal_idempotency_key(request)
    mobile = _formal_mobile(payload.mobile)
    mobile_hash = _formal_mobile_hash(mobile)
    ip_hash = _formal_ip_hash(client_ip(request))
    identity = _formal_mobile_identity(db, mobile, required=False)
    user = db.get(User, identity.user_id) if identity is not None else None
    try:
        prepared = authentication_challenge.prepare(
            db,
            mobile_hash=mobile_hash,
            requested_ip_hash=ip_hash,
            user=user,
            provider=settings.sms_provider,
            hash_version=settings.identity_hash_version,
            client_type=client_type,
            idempotency_key=idempotency_key,
            request_id=request_id,
            mobile_hour_limit=settings.sms_max_per_mobile_hour,
            ip_hour_limit=settings.sms_max_per_ip_hour,
            send_interval_seconds=settings.sms_interval_seconds,
            ttl_seconds=settings.sms_valid_seconds,
            max_attempts=settings.sms_max_verify_attempts,
        )
    except AuthenticationChallengeError as exc:
        _raise_challenge_error(db, exc)

    # Persist the prepared challenge and its append-only evidence before the
    # provider boundary.  A retry with the same key reuses the challenge id,
    # which is also the provider out_id.
    db.commit()
    if not prepared.dispatch_required:
        return SmsCodeRequestOut(
            ok=True,
            message=prepared.public_message,
            retry_after=prepared.retry_after,
            expires_in=prepared.expires_in,
        )

    try:
        sent = get_sms_provider().send(mobile, str(prepared.challenge_id))
    except SmsProviderError as exc:
        try:
            authentication_challenge.mark_send_failed(
                db,
                challenge_id=prepared.challenge_id,
                request_id=request_id,
            )
            db.commit()
        except AuthenticationChallengeError as evidence_error:
            _raise_challenge_error(db, evidence_error)
        raise HTTPException(status_code=502, detail="短信服务暂时不可用") from exc

    if not sent.biz_id:
        try:
            authentication_challenge.mark_send_failed(
                db,
                challenge_id=prepared.challenge_id,
                request_id=request_id,
            )
            db.commit()
        except AuthenticationChallengeError as evidence_error:
            _raise_challenge_error(db, evidence_error)
        raise HTTPException(status_code=502, detail="短信服务暂时不可用")

    provider_reference = "biz-" + hashlib.sha256(
        sent.biz_id.encode("utf-8")
    ).hexdigest()
    try:
        authentication_challenge.mark_sent(
            db,
            challenge_id=prepared.challenge_id,
            provider_reference=provider_reference,
            request_id=request_id,
        )
    except AuthenticationChallengeError as exc:
        _raise_challenge_error(db, exc)
    db.commit()
    return SmsCodeRequestOut(
        ok=True,
        message=prepared.public_message,
        retry_after=prepared.retry_after,
        expires_in=prepared.expires_in,
    )


@router.get("/login-options", response_model=LoginOptionsOut)
def login_options():
    return LoginOptionsOut(
        sms_enabled=settings.sms_configuration_ready(),
        password_enabled=settings.password_login_enabled,
        wechat_enabled=settings.wechat_configuration_ready(),
        sms_valid_seconds=settings.sms_valid_seconds,
        sms_interval_seconds=settings.sms_interval_seconds,
        session_ttl_days=settings.session_ttl_days,
    )


@router.post("/login", response_model=UserOut)
def login(payload: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)):
    user = _authenticate_password(payload, db)
    tokens = _create_web_session(db, user, request)
    _set_session_cookies(response, tokens)
    audit(
        db,
        actor=user,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        detail="登录成功",
        ip_address=client_ip(request),
    )
    db.commit()
    return user


@router.post("/miniprogram/password-login", response_model=TokenSessionOut)
def miniprogram_password_login(
    payload: MiniProgramPasswordLoginIn,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _authenticate_password(payload, db)
    tokens = _create_miniprogram_session(
        db,
        user,
        request,
        device_id=payload.device_id,
        device_name=payload.device_name,
    )
    audit(
        db,
        actor=user,
        action="auth.miniprogram_login",
        entity_type="user",
        entity_id=user.id,
        detail="微信小程序密码登录成功",
        ip_address=client_ip(request),
    )
    db.commit()
    return _token_session(user, tokens)


@router.post("/sms/request", response_model=SmsCodeRequestOut)
def request_sms_code(
    payload: SmsCodeRequestIn,
    request: Request,
    db: Session = Depends(get_db),
):
    if _production():
        client_type = request.headers.get("x-auth-client", "web")
        if client_type not in {"web", "miniprogram"}:
            raise HTTPException(status_code=422, detail="认证客户端类型无效")
        return _request_formal_sms_code(
            payload,
            request,
            db,
            client_type=client_type,
        )
    _require_sms_configuration()
    mobile = payload.mobile.strip()
    ip_address = client_ip(request)
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)

    mobile_count = db.scalar(
        select(func.count(SmsLoginChallenge.id)).where(
            SmsLoginChallenge.mobile == mobile,
            SmsLoginChallenge.created_at >= hour_ago,
        )
    ) or 0
    if mobile_count >= settings.sms_max_per_mobile_hour:
        raise HTTPException(status_code=429, detail="验证码请求过于频繁，请稍后再试")

    ip_count = db.scalar(
        select(func.count(SmsLoginChallenge.id)).where(
            SmsLoginChallenge.requested_ip == ip_address,
            SmsLoginChallenge.created_at >= hour_ago,
        )
    ) or 0
    if ip_count >= settings.sms_max_per_ip_hour:
        raise HTTPException(status_code=429, detail="验证码请求过于频繁，请稍后再试")

    last_challenge = db.scalar(
        select(SmsLoginChallenge)
        .where(SmsLoginChallenge.mobile == mobile)
        .order_by(SmsLoginChallenge.created_at.desc())
        .limit(1)
    )
    if last_challenge:
        elapsed = (now - _as_utc(last_challenge.created_at)).total_seconds()
        if elapsed < settings.sms_interval_seconds:
            retry_after = max(1, int(settings.sms_interval_seconds - elapsed) + 1)
            raise HTTPException(
                status_code=429,
                detail=f"请在{retry_after}秒后重新获取验证码",
                headers={"Retry-After": str(retry_after)},
            )

    user = db.scalar(
        select(User)
        .where(User.mobile == mobile, User.is_active.is_(True))
        .with_for_update()
    )
    expires_at = now + timedelta(seconds=settings.sms_valid_seconds)
    if not user:
        db.add(
            SmsLoginChallenge(
                mobile=mobile,
                provider=settings.sms_provider,
                requested_ip=ip_address,
                status="ignored",
                expires_at=expires_at,
            )
        )
        db.commit()
        return SmsCodeRequestOut(
            ok=True,
            message=SMS_REQUEST_MESSAGE,
            retry_after=settings.sms_interval_seconds,
            expires_in=settings.sms_valid_seconds,
        )

    for challenge in db.scalars(
        select(SmsLoginChallenge).where(
            SmsLoginChallenge.mobile == mobile,
            SmsLoginChallenge.status == "pending",
        )
    ):
        challenge.status = "superseded"

    challenge = SmsLoginChallenge(
        user_id=user.id,
        mobile=mobile,
        provider=settings.sms_provider,
        requested_ip=ip_address,
        expires_at=expires_at,
    )
    db.add(challenge)
    db.flush()
    try:
        result = get_sms_provider().send(mobile, challenge.id)
    except SmsProviderError as exc:
        challenge.status = "failed"
        audit(
            db,
            actor=user,
            action="auth.sms_send_failed",
            entity_type="user",
            entity_id=user.id,
            detail={"mobile": _masked_mobile(mobile)},
            ip_address=ip_address,
        )
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    challenge.provider_biz_id = result.biz_id
    audit(
        db,
        actor=user,
        action="auth.sms_requested",
        entity_type="user",
        entity_id=user.id,
        detail={"mobile": _masked_mobile(mobile)},
        ip_address=ip_address,
    )
    db.commit()
    return SmsCodeRequestOut(
        ok=True,
        message=SMS_REQUEST_MESSAGE,
        retry_after=settings.sms_interval_seconds,
        expires_in=settings.sms_valid_seconds,
    )


def _consume_sms_code(
    payload: SmsCodeLoginIn,
    request: Request,
    db: Session,
) -> User:
    _require_sms_configuration()
    mobile = payload.mobile.strip()
    now = datetime.now(timezone.utc)
    user = db.scalar(
        select(User)
        .where(User.mobile == mobile, User.is_active.is_(True))
        .with_for_update()
    )
    if not user:
        raise HTTPException(status_code=401, detail="验证码不正确或已失效")

    challenge = db.scalar(
        select(SmsLoginChallenge)
        .where(
            SmsLoginChallenge.user_id == user.id,
            SmsLoginChallenge.mobile == mobile,
            SmsLoginChallenge.status == "pending",
        )
        .order_by(SmsLoginChallenge.created_at.desc())
        .with_for_update()
        .limit(1)
    )
    if not challenge:
        raise HTTPException(status_code=401, detail="验证码不正确或已失效")
    if _as_utc(challenge.expires_at) <= now:
        challenge.status = "expired"
        db.commit()
        raise HTTPException(status_code=401, detail="验证码不正确或已失效")
    if challenge.attempt_count >= settings.sms_max_verify_attempts:
        challenge.status = "exhausted"
        db.commit()
        raise HTTPException(status_code=429, detail="验证码尝试次数过多，请重新获取")

    challenge.attempt_count += 1
    try:
        verified = get_sms_provider().verify(mobile, payload.code, challenge.id)
    except SmsProviderError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not verified:
        if challenge.attempt_count >= settings.sms_max_verify_attempts:
            challenge.status = "exhausted"
        audit(
            db,
            actor=user,
            action="auth.sms_login_failed",
            entity_type="user",
            entity_id=user.id,
            detail={"attempt": challenge.attempt_count},
            ip_address=client_ip(request),
        )
        db.commit()
        raise HTTPException(status_code=401, detail="验证码不正确或已失效")

    challenge.status = "used"
    challenge.consumed_at = now
    user.require_password_change = False
    audit(
        db,
        actor=user,
        action="auth.sms_login",
        entity_type="user",
        entity_id=user.id,
        detail="短信验证码登录成功",
        ip_address=client_ip(request),
    )
    return user


def _consume_formal_sms_code(
    payload: SmsCodeLoginIn,
    request: Request,
    db: Session,
    *,
    client_type: str,
    device_id: str = "",
    device_name: str = "",
) -> tuple[User, SessionTokens, FormalPrincipal]:
    _require_sms_configuration()
    request_id = _formal_request_id(request)
    mobile = _formal_mobile(payload.mobile)
    mobile_hash = _formal_mobile_hash(mobile)
    try:
        identity = _formal_mobile_identity(db, mobile, required=True)
    except HTTPException as exc:
        if exc.status_code == 401:
            _append_owned_failed_authentication_attempt(
                db,
                request_id=request_id,
                client_type=client_type,
                action="authentication.sms.login_failed",
                reason_code="identity_unavailable",
            )
        raise
    user = db.get(User, identity.user_id)
    if user is None:
        _append_owned_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type=client_type,
            action="authentication.sms.login_failed",
            reason_code="identity_unavailable",
        )
        raise HTTPException(status_code=401, detail="验证码不正确或已失效")

    deferred_challenge_events: list[
        authentication_challenge.DeferredChallengeAuditEvent
    ] = []

    def flush_challenge_events() -> None:
        try:
            authentication_challenge.flush_deferred_audit_events(
                db,
                deferred_events=deferred_challenge_events,
            )
        except AuthenticationChallengeError as exc:
            # Never commit challenge/session state without its chained evidence.
            # The endpoint can restart only the caller-owned idempotency failure
            # envelope after this full transaction rollback.
            db.rollback()
            raise HTTPException(
                status_code=exc.http_status_code,
                detail=exc.as_detail(),
            ) from exc

    verification_savepoint = db.begin_nested()
    try:
        attempt = authentication_challenge.begin_verify(
            db,
            mobile_hash=mobile_hash,
            provider=settings.sms_provider,
            client_type=client_type,
            request_id=request_id,
            deferred_events=deferred_challenge_events,
        )
    except AuthenticationChallengeError as exc:
        if exc.mutation_persisted:
            verification_savepoint.commit()
            flush_challenge_events()
        else:
            verification_savepoint.rollback()
            deferred_challenge_events.clear()
        _raise_challenge_error(
            db,
            exc,
            request_id=request_id,
            client_type=client_type,
            transaction_owned=True,
        )

    try:
        verified = get_sms_provider().verify(
            mobile,
            payload.code,
            str(attempt.challenge_id),
        )
    except SmsProviderError as exc:
        # Provider availability must not consume an attempt.  Roll back only
        # the verification savepoint; keep the caller-owned pending operation
        # so audit + failed envelope can be committed once by the endpoint.
        verification_savepoint.rollback()
        deferred_challenge_events.clear()
        _append_owned_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type=client_type,
            action="authentication.sms.login_failed",
            reason_code="provider_unavailable",
            outcome="failed",
        )
        raise HTTPException(status_code=502, detail="短信服务暂时不可用") from exc
    else:
        verification_savepoint.commit()

    try:
        authentication_challenge.finish_verify(
            db,
            challenge_id=attempt.challenge_id,
            verified=verified,
            request_id=request_id,
            deferred_events=deferred_challenge_events,
        )
    except AuthenticationChallengeError as exc:
        flush_challenge_events()
        _raise_challenge_error(
            db,
            exc,
            request_id=request_id,
            client_type=client_type,
            transaction_owned=True,
        )

    session_savepoint = db.begin_nested()
    pre_session_event_count = len(deferred_challenge_events)
    try:
        if client_type == "web":
            tokens = _create_web_session(db, user, request)
        else:
            tokens = _create_miniprogram_session(
                db,
                user,
                request,
                device_id=device_id,
                device_name=device_name,
            )
        authentication_challenge.consume_verified(
            db,
            challenge_id=attempt.challenge_id,
            request_id=request_id,
            actor_user_id=user.id,
            deferred_events=deferred_challenge_events,
        )
        # Lock ordering is deliberately business rows first, global audit head
        # last.  Refresh/logout use the same session -> token -> audit order.
        flush_challenge_events()
        record_session_created(
            db,
            tokens=tokens,
            actor_user_id=user.id,
            request_id=request_id,
            reason_code="sms_login",
        )
    except AuthenticationChallengeError as exc:
        session_savepoint.rollback()
        del deferred_challenge_events[pre_session_event_count:]
        flush_challenge_events()
        _raise_challenge_error(
            db,
            exc,
            request_id=request_id,
            client_type=client_type,
            transaction_owned=True,
        )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        # Deferred challenge events may already have advanced the chain inside
        # this savepoint.  Roll back the entire transaction so no challenge or
        # session fact survives without the complete audit sequence.
        db.rollback()
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc
    except SessionError as exc:
        session_savepoint.rollback()
        del deferred_challenge_events[pre_session_event_count:]
        flush_challenge_events()
        _append_owned_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type=client_type,
            action="authentication.sms.login_failed",
            reason_code="session_rejected",
        )
        raise HTTPException(status_code=401, detail="账号当前不可建立正式会话") from exc
    except HTTPException:
        # ``flush_challenge_events`` has already rolled back the outer
        # transaction on an audit-chain failure.
        raise
    except Exception:
        session_savepoint.rollback()
        raise
    else:
        session_savepoint.commit()
    return user, tokens, identity.principal


def _exchange_formal_wechat_identity(
    payload: MiniProgramWechatLoginIn,
    request: Request,
    db: Session,
) -> WechatLoginIdentity:
    """Exchange one provider code after pre-provider rate admission."""

    if not settings.wechat_configuration_ready():
        raise HTTPException(status_code=503, detail="微信快捷登录尚未启用")
    request_id = _formal_request_id(request)
    try:
        wechat_user = get_wechat_provider().exchange_login_code(payload.login_code)
    except WechatProviderError as exc:
        _record_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type="miniprogram",
            action="authentication.wechat.login_failed",
            reason_code="provider_unavailable",
            outcome="failed",
        )
        raise HTTPException(status_code=502, detail="微信接口暂时不可用") from exc
    if wechat_user.app_id != settings.wechat_app_id:
        _record_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type="miniprogram",
            action="authentication.wechat.login_failed",
            reason_code="provider_mismatch",
        )
        raise HTTPException(status_code=502, detail="微信身份提供方响应无效")
    return wechat_user


def _login_formal_wechat(
    payload: MiniProgramWechatLoginIn,
    request: Request,
    db: Session,
    *,
    wechat_user: WechatLoginIdentity,
) -> tuple[User, SessionTokens, FormalPrincipal]:
    request_id = _formal_request_id(request)
    _require_authentication_audit_head(db)
    try:
        identity = resolve_active_formal_identity(
            db,
            secret=settings.identity_hash_secret,
            hash_version=settings.identity_hash_version,
            identity_type="wechat_openid",
            provider_key=wechat_user.app_id,
            identifier=wechat_user.openid,
        )
    except AuthenticationIdentityUnavailable:
        _append_owned_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type="miniprogram",
            action="authentication.wechat.login_failed",
            reason_code="identity_unavailable",
        )
        raise HTTPException(status_code=401, detail="登录身份无效或账号不可用") from None
    except AuthenticationIdentityError as exc:
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc

    user = db.get(User, identity.user_id)
    if user is None:
        _append_owned_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type="miniprogram",
            action="authentication.wechat.login_failed",
            reason_code="identity_unavailable",
        )
        raise HTTPException(status_code=401, detail="登录身份无效或账号不可用")
    session_savepoint = db.begin_nested()
    try:
        tokens = _create_miniprogram_session(
            db,
            user,
            request,
            device_id=payload.device_id,
            device_name=payload.device_name,
        )
        record_session_created(
            db,
            tokens=tokens,
            actor_user_id=user.id,
            request_id=request_id,
            reason_code="wechat_login",
        )
    except (AuditChainError, AuthenticationEvidenceError) as exc:
        session_savepoint.rollback()
        raise HTTPException(status_code=503, detail="认证服务暂时不可用") from exc
    except SessionError as exc:
        session_savepoint.rollback()
        _append_owned_failed_authentication_attempt(
            db,
            request_id=request_id,
            client_type="miniprogram",
            action="authentication.wechat.login_failed",
            reason_code="session_rejected",
        )
        raise HTTPException(status_code=401, detail="账号当前不可建立正式会话") from exc
    except Exception:
        session_savepoint.rollback()
        raise
    else:
        session_savepoint.commit()
    return user, tokens, identity.principal


@router.post("/sms/login", response_model=UserOut | FormalSelfOut)
def login_with_sms_code(
    payload: SmsCodeLoginIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    if _production():
        _require_formal_session_ip(request)
        _formal_request_id(request)
        _formal_idempotency_key(request)
        mobile = _formal_mobile(payload.mobile)
        scope = {"identity_type": "mobile", "mobile": mobile}
        request_document = {"mobile": mobile, "code": payload.code}
        existing_operation = _probe_existing_formal_login_operation(
            db,
            request,
            operation_type="sms_login",
            client_type="web",
            scope=scope,
            request_document=request_document,
        )
        if not existing_operation:
            _consume_formal_login_rate_limits(
                db,
                request,
                operation_type="sms_login",
                include_global_and_ip=True,
                identity_identifier=mobile,
            )
        write = _begin_formal_authentication_write(
            db,
            request,
            operation_type="sms_login",
            client_type="web",
            scope=scope,
            request_document=request_document,
        )
        if write.begin.replayed:
            restored = _restore_cached_formal_session(db, write)
            _set_session_cookies(response, restored.tokens)
            response.headers.update(_idempotency_response_headers(replayed=True))
            db.commit()
            return restored.user
        try:
            user, tokens, principal = _consume_formal_sms_code(
                payload,
                request,
                db,
                client_type="web",
            )
            result = _formal_self(db, user, principal=principal)
        except HTTPException as exc:
            _complete_formal_authentication_failure(db, write, exc)
        except Exception:
            db.rollback()
            raise
        _complete_formal_session_success(
            db,
            write,
            user=result,
            tokens=tokens,
        )
        _set_session_cookies(response, tokens)
        response.headers.update(_idempotency_response_headers(replayed=False))
        return result
    user = _consume_sms_code(payload, request, db)
    try:
        tokens = _create_web_session(db, user, request)
    except SessionError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _set_session_cookies(response, tokens)
    db.commit()
    return user


@router.post(
    "/miniprogram/sms-login",
    response_model=TokenSessionOut | FormalTokenSessionOut,
)
def miniprogram_sms_login(
    payload: MiniProgramSmsLoginIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    if _production():
        _require_formal_session_ip(request)
        _formal_request_id(request)
        _formal_idempotency_key(request)
        mobile = _formal_mobile(payload.mobile)
        scope = {"identity_type": "mobile", "mobile": mobile}
        request_document = {
            "mobile": mobile,
            "code": payload.code,
            "device_id": payload.device_id,
            "device_name": payload.device_name,
        }
        existing_operation = _probe_existing_formal_login_operation(
            db,
            request,
            operation_type="sms_login",
            client_type="miniprogram",
            scope=scope,
            request_document=request_document,
        )
        if not existing_operation:
            _consume_formal_login_rate_limits(
                db,
                request,
                operation_type="sms_login",
                include_global_and_ip=True,
                identity_identifier=mobile,
            )
        write = _begin_formal_authentication_write(
            db,
            request,
            operation_type="sms_login",
            client_type="miniprogram",
            scope=scope,
            request_document=request_document,
        )
        if write.begin.replayed:
            restored = _restore_cached_formal_session(db, write)
            response.headers.update(_idempotency_response_headers(replayed=True))
            db.commit()
            return restored.token_body()
        try:
            user, tokens, principal = _consume_formal_sms_code(
                payload,
                request,
                db,
                client_type="miniprogram",
                device_id=payload.device_id,
                device_name=payload.device_name,
            )
            formal_user = _formal_self(db, user, principal=principal)
            result = _formal_token_session(db, user, tokens, principal=principal)
        except HTTPException as exc:
            _complete_formal_authentication_failure(db, write, exc)
        except Exception:
            db.rollback()
            raise
        _complete_formal_session_success(
            db,
            write,
            user=formal_user,
            tokens=tokens,
        )
        response.headers.update(_idempotency_response_headers(replayed=False))
        return result
    user = _consume_sms_code(payload, request, db)
    try:
        tokens = _create_miniprogram_session(
            db,
            user,
            request,
            device_id=payload.device_id,
            device_name=payload.device_name,
        )
    except SessionError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    db.commit()
    return _token_session(user, tokens)


@router.post(
    "/miniprogram/wechat-login",
    response_model=TokenSessionOut | FormalTokenSessionOut,
)
def miniprogram_wechat_login(
    payload: MiniProgramWechatLoginIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    if _production():
        _require_formal_session_ip(request)
        _formal_request_id(request)
        _formal_idempotency_key(request)
        scope = {
            "identity_type": "wechat_openid",
            "provider_key": settings.wechat_app_id,
        }
        request_document = {
            "login_code": payload.login_code,
            "phone_code": payload.phone_code or "",
            "device_id": payload.device_id,
            "device_name": payload.device_name,
        }
        existing_operation = _probe_existing_formal_login_operation(
            db,
            request,
            operation_type="wechat_login",
            client_type="miniprogram",
            scope=scope,
            request_document=request_document,
        )
        wechat_user: WechatLoginIdentity | None = None
        if not existing_operation:
            _consume_formal_login_rate_limits(
                db,
                request,
                operation_type="wechat_login",
                include_global_and_ip=True,
            )
            wechat_user = _exchange_formal_wechat_identity(payload, request, db)
            _consume_formal_login_rate_limits(
                db,
                request,
                operation_type="wechat_login",
                include_global_and_ip=False,
                identity_identifier=(
                    f"{wechat_user.app_id}\x00{wechat_user.openid}"
                ),
            )
        write = _begin_formal_authentication_write(
            db,
            request,
            operation_type="wechat_login",
            client_type="miniprogram",
            scope=scope,
            request_document=request_document,
        )
        if write.begin.replayed:
            restored = _restore_cached_formal_session(db, write)
            response.headers.update(_idempotency_response_headers(replayed=True))
            db.commit()
            return restored.token_body()
        if wechat_user is None:
            db.rollback()
            raise HTTPException(status_code=503, detail="认证幂等协调不可用")
        try:
            user, tokens, principal = _login_formal_wechat(
                payload,
                request,
                db,
                wechat_user=wechat_user,
            )
            formal_user = _formal_self(db, user, principal=principal)
            result = _formal_token_session(db, user, tokens, principal=principal)
        except HTTPException as exc:
            _complete_formal_authentication_failure(db, write, exc)
        except Exception:
            db.rollback()
            raise
        _complete_formal_session_success(
            db,
            write,
            user=formal_user,
            tokens=tokens,
        )
        response.headers.update(_idempotency_response_headers(replayed=False))
        return result
    if not settings.wechat_configuration_ready():
        raise HTTPException(status_code=503, detail="微信快捷登录尚未启用")
    try:
        wechat_user = get_wechat_provider().exchange_login_code(payload.login_code)
    except WechatProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    identity = db.scalar(
        select(WechatIdentity).where(
            WechatIdentity.app_id == wechat_user.app_id,
            WechatIdentity.openid == wechat_user.openid,
        )
    )
    if identity:
        user = db.get(User, identity.user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="账号不可用")
    else:
        if not payload.phone_code:
            raise HTTPException(status_code=428, detail="请授权手机号完成首次绑定")
        try:
            mobile = get_wechat_provider().exchange_phone_code(payload.phone_code)
        except WechatProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        user = db.scalar(
            select(User).where(User.mobile == mobile, User.is_active.is_(True))
        )
        if not user:
            raise HTTPException(status_code=403, detail="该手机号尚未开通账号")
        existing_binding = db.scalar(
            select(WechatIdentity).where(
                WechatIdentity.app_id == wechat_user.app_id,
                WechatIdentity.user_id == user.id,
            )
        )
        if existing_binding and existing_binding.openid != wechat_user.openid:
            raise HTTPException(status_code=409, detail="该账号已绑定其他微信，请联系管理员")
        identity = existing_binding or WechatIdentity(
            user_id=user.id,
            app_id=wechat_user.app_id,
            openid=wechat_user.openid,
        )
        db.add(identity)

    now = datetime.now(timezone.utc)
    identity.unionid = wechat_user.unionid or identity.unionid
    identity.last_login_at = now
    user.require_password_change = False
    try:
        tokens = _create_miniprogram_session(
            db,
            user,
            request,
            device_id=payload.device_id,
            device_name=payload.device_name,
        )
    except SessionError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    audit(
        db,
        actor=user,
        action="auth.wechat_login",
        entity_type="user",
        entity_id=user.id,
        detail="微信小程序快捷登录成功",
        ip_address=client_ip(request),
    )
    db.commit()
    return _token_session(user, tokens)


@router.post("/refresh", response_model=UserOut | FormalSelfOut)
def refresh_web_session(
    response: Response,
    request: Request,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    request_id = _formal_request_id(request) if _production() else ""
    write: _FormalAuthenticationWrite | None = None
    if _production():
        _require_formal_session_ip(request)
        _formal_idempotency_key(request)
        auth_session_id, input_refresh_token_id = _refresh_token_reference(
            db, refresh_token
        )
        write = _begin_formal_authentication_write(
            db,
            request,
            operation_type="session_refresh",
            client_type="web",
            scope={
                "credential_type": "refresh_token",
                "refresh_token": refresh_token or "",
            },
            request_document={
                "refresh_token": refresh_token or "",
                "refresh_token_present": refresh_token is not None,
            },
            auth_session_id=auth_session_id,
            input_refresh_token_id=input_refresh_token_id,
        )
        if write.begin.replayed:
            restored = _restore_cached_formal_session(db, write)
            _set_session_cookies(response, restored.tokens)
            response.headers.update(_idempotency_response_headers(replayed=True))
            db.commit()
            return restored.user
    if not refresh_token:
        if _production():
            _clear_session_cookies(response)
            try:
                _append_failed_authentication_attempt(
                    db,
                    request_id=request_id,
                    client_type="web",
                    action="authentication.session.refresh_failed",
                    reason_code="refresh_missing",
                )
            except (AuditChainError, AuthenticationEvidenceError):
                db.rollback()
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(status_code=503, detail="认证服务暂时不可用"),
                )
            _complete_formal_authentication_failure(
                db,
                write,
                HTTPException(status_code=401, detail="登录已失效"),
            )
        raise HTTPException(status_code=401, detail="登录已失效")
    try:
        user, tokens = rotate_session(
            db,
            refresh_token,
            ip_address=client_ip(request),
            user_agent=_request_user_agent(request),
        )
    except RefreshTokenReplayError as exc:
        if _production():
            auth_session = db.get(AuthSession, exc.session_id)
            try:
                if auth_session is not None:
                    record_session_replay_revocation(
                        db,
                        auth_session=auth_session,
                        previous_status=exc.previous_status,
                        request_id=request_id,
                    )
            except (AuditChainError, AuthenticationEvidenceError) as evidence_error:
                db.rollback()
                _clear_session_cookies(response)
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(status_code=503, detail="认证服务暂时不可用"),
                )
            _clear_session_cookies(response)
            _complete_formal_authentication_failure(
                db,
                write,
                HTTPException(status_code=401, detail="登录已失效"),
            )
        else:
            db.rollback()
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="登录已失效") from exc
    except SessionError as exc:
        _clear_session_cookies(response)
        if _production():
            try:
                _append_failed_authentication_attempt(
                    db,
                    request_id=request_id,
                    client_type="web",
                    action="authentication.session.refresh_failed",
                    reason_code="refresh_rejected",
                )
            except (AuditChainError, AuthenticationEvidenceError):
                db.rollback()
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(status_code=503, detail="认证服务暂时不可用"),
                )
            _complete_formal_authentication_failure(
                db,
                write,
                HTTPException(status_code=401, detail="登录已失效"),
            )
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if _production():
        try:
            record_session_rotated(
                db,
                auth_session=tokens.auth_session,
                actor_user_id=user.id,
                request_id=request_id,
            )
            result = _formal_self(db, user)
        except (AuditChainError, AuthenticationEvidenceError) as exc:
            _raise_authentication_evidence_unavailable(db, exc)
        _complete_formal_session_success(
            db,
            write,
            user=result,
            tokens=tokens,
        )
        _set_session_cookies(response, tokens)
        response.headers.update(_idempotency_response_headers(replayed=False))
        return result
    _set_session_cookies(response, tokens)
    db.commit()
    return user


@router.post(
    "/miniprogram/refresh",
    response_model=TokenSessionOut | FormalTokenSessionOut,
)
def refresh_miniprogram_session(
    payload: SessionRefreshIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    request_id = _formal_request_id(request) if _production() else ""
    write: _FormalAuthenticationWrite | None = None
    if _production():
        _require_formal_session_ip(request)
        _formal_idempotency_key(request)
        auth_session_id, input_refresh_token_id = _refresh_token_reference(
            db, payload.refresh_token
        )
        write = _begin_formal_authentication_write(
            db,
            request,
            operation_type="session_refresh",
            client_type="miniprogram",
            scope={
                "credential_type": "refresh_token",
                "refresh_token": payload.refresh_token,
            },
            request_document={
                "refresh_token": payload.refresh_token,
                "device_id": payload.device_id,
            },
            auth_session_id=auth_session_id,
            input_refresh_token_id=input_refresh_token_id,
        )
        if write.begin.replayed:
            restored = _restore_cached_formal_session(db, write)
            response.headers.update(_idempotency_response_headers(replayed=True))
            db.commit()
            return restored.token_body()
    try:
        user, tokens = rotate_session(
            db,
            payload.refresh_token,
            device_id=payload.device_id,
            ip_address=client_ip(request),
            user_agent=_request_user_agent(request),
        )
    except RefreshTokenReplayError as exc:
        if _production():
            auth_session = db.get(AuthSession, exc.session_id)
            try:
                if auth_session is not None:
                    record_session_replay_revocation(
                        db,
                        auth_session=auth_session,
                        previous_status=exc.previous_status,
                        request_id=request_id,
                    )
            except (AuditChainError, AuthenticationEvidenceError) as evidence_error:
                db.rollback()
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(status_code=503, detail="认证服务暂时不可用"),
                )
            _complete_formal_authentication_failure(
                db,
                write,
                HTTPException(status_code=401, detail="登录已失效"),
            )
        else:
            db.rollback()
        raise HTTPException(status_code=401, detail="登录已失效") from exc
    except SessionError as exc:
        if _production():
            try:
                _append_failed_authentication_attempt(
                    db,
                    request_id=request_id,
                    client_type="miniprogram",
                    action="authentication.session.refresh_failed",
                    reason_code="refresh_rejected",
                )
            except (AuditChainError, AuthenticationEvidenceError):
                db.rollback()
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(status_code=503, detail="认证服务暂时不可用"),
                )
            _complete_formal_authentication_failure(
                db,
                write,
                HTTPException(status_code=401, detail="登录已失效"),
            )
        db.rollback()
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if _production():
        try:
            record_session_rotated(
                db,
                auth_session=tokens.auth_session,
                actor_user_id=user.id,
                request_id=request_id,
            )
            result = _formal_token_session(db, user, tokens)
        except (AuditChainError, AuthenticationEvidenceError) as exc:
            _raise_authentication_evidence_unavailable(db, exc)
        _complete_formal_session_success(
            db,
            write,
            user=result.user,
            tokens=tokens,
        )
        response.headers.update(_idempotency_response_headers(replayed=False))
        return result
    db.commit()
    return _token_session(user, tokens)


def _logout_formal_web(
    response: Response,
    request: Request,
    refresh_token: str | None,
    db: Session,
    *,
    request_id: str,
) -> dict[str, bool]:
    auth_session_id, input_refresh_token_id = _refresh_token_reference(
        db, refresh_token
    )
    write = _begin_formal_authentication_write(
        db,
        request,
        operation_type="session_logout",
        client_type="web",
        scope={
            "credential_type": "refresh_token",
            "refresh_token": refresh_token or "",
        },
        request_document={
            "refresh_token": refresh_token or "",
            "refresh_token_present": refresh_token is not None,
        },
        auth_session_id=auth_session_id,
        input_refresh_token_id=input_refresh_token_id,
    )
    if write.begin.replayed:
        result = _restore_cached_formal_logout(db, write)
        _clear_session_cookies(response)
        response.headers.update(_idempotency_response_headers(replayed=True))
        return result
    result = None
    if refresh_token:
        try:
            result = revoke_by_refresh_token_with_result(db, refresh_token)
        except SessionError:
            db.rollback()
            result = None
        if result is not None:
            try:
                record_session_revoked(
                    db,
                    result=result,
                    request_id=request_id,
                )
            except (AuditChainError, AuthenticationEvidenceError):
                db.rollback()
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(
                        status_code=503,
                        detail="认证服务暂时不可用",
                    ),
                )
    _complete_formal_logout_success(db, write)
    _clear_session_cookies(response)
    response.headers.update(_idempotency_response_headers(replayed=False))
    return {"ok": True}


@router.post("/logout")
def logout(
    response: Response,
    request: Request,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if _production():
        request_id = _formal_request_id(request)
        _formal_idempotency_key(request)
        try:
            return _logout_formal_web(
                response,
                request,
                refresh_token,
                db,
                request_id=request_id,
            )
        except HTTPException as exc:
            db.rollback()
            raise _authentication_http_exception(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=dict(exc.headers or {}),
                clear_session_cookies=True,
            ) from exc
    if refresh_token:
        revoke_by_refresh_token(db, refresh_token)
        db.commit()
    _clear_session_cookies(response)
    return {"ok": True}


@router.post("/miniprogram/logout")
def miniprogram_logout(
    payload: SessionLogoutIn,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    if _production():
        request_id = _formal_request_id(request)
        _formal_idempotency_key(request)
        auth_session_id, input_refresh_token_id = _refresh_token_reference(
            db, payload.refresh_token
        )
        write = _begin_formal_authentication_write(
            db,
            request,
            operation_type="session_logout",
            client_type="miniprogram",
            scope={
                "credential_type": "refresh_token",
                "refresh_token": payload.refresh_token,
            },
            request_document={"refresh_token": payload.refresh_token},
            auth_session_id=auth_session_id,
            input_refresh_token_id=input_refresh_token_id,
        )
        if write.begin.replayed:
            result = _restore_cached_formal_logout(db, write)
            response.headers.update(_idempotency_response_headers(replayed=True))
            return result
        try:
            result = revoke_by_refresh_token_with_result(db, payload.refresh_token)
        except SessionError:
            db.rollback()
            result = None
        if result is not None:
            try:
                record_session_revoked(
                    db,
                    result=result,
                    request_id=request_id,
                )
            except (AuditChainError, AuthenticationEvidenceError) as exc:
                db.rollback()
                _complete_formal_authentication_failure(
                    db,
                    write,
                    HTTPException(
                        status_code=503,
                        detail="认证服务暂时不可用",
                    ),
                )
        _complete_formal_logout_success(db, write)
        response.headers.update(_idempotency_response_headers(replayed=False))
        return {"ok": True}
    revoke_by_refresh_token(db, payload.refresh_token)
    db.commit()
    return {"ok": True}


@router.get(
    "/sessions",
    response_model=list[AuthSessionOut | FormalAuthSessionOut],
)
def list_auth_sessions(
    request: Request,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_session_manager(request, actor, db)
    current_session_id = getattr(request.state, "auth_session_id", "")
    rows = db.scalars(
        select(AuthSession)
        .where(AuthSession.revoked_at.is_(None))
        .order_by(AuthSession.last_seen_at.desc())
    )
    result: list[AuthSessionOut | FormalAuthSessionOut] = []
    for row in rows:
        if not session_is_active(row):
            continue
        if _production():
            try:
                require_current_session_ip_evidence(
                    row,
                    hash_version=settings.identity_hash_version,
                )
            except SessionError:
                raise HTTPException(
                    status_code=503,
                    detail="认证服务暂时不可用",
                ) from None
        user = db.get(User, row.user_id)
        if not user:
            continue
        if _production():
            person = db.get(Person, user.person_id) if user.person_id else None
            organization = (
                db.get(Organization, person.organization_id) if person else None
            )
            if person is None or organization is None:
                raise HTTPException(status_code=503, detail="会话人员映射不完整")
            result.append(
                FormalAuthSessionOut(
                    session_id=row.id,
                    person_id=person.id,
                    person_name=person.name,
                    employee_no=person.employee_no,
                    organization_code=organization.code,
                    organization_name=organization.name,
                    account_status=user.account_status,
                    client_type=row.client_type,
                    device_name=row.device_name,
                    created_at=row.created_at,
                    last_seen_at=row.last_seen_at,
                    expires_at=row.expires_at,
                    is_current=row.id == current_session_id,
                )
            )
            continue
        result.append(
            AuthSessionOut(
                id=row.id,
                user_id=user.id,
                user_name=user.name,
                mobile=user.mobile,
                client_type=row.client_type,
                device_name=row.device_name,
                ip_address=row.ip_address,
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                is_current=row.id == current_session_id,
            )
        )
    return result


@router.post(
    "/sessions/{session_id}/revoke",
    response_model=FormalSessionRevokeOut | dict[str, bool],
)
def revoke_auth_session(
    session_id: str,
    request: Request,
    http_response: Response,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _production():
        principal = _require_session_manager(request, actor, db)
        if principal is None:
            raise HTTPException(status_code=403, detail="没有此操作权限")
        request_id = _formal_request_id(request)
        idempotency_key = _formal_idempotency_key(request)
        try:
            result = force_revoke_session(
                db,
                actor=principal,
                session_id=session_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            payload = FormalSessionRevokeOut(
                session_id=result.session_id,
                status="revoked",
                revoked_at=result.revoked_at,
                replayed=result.replayed,
                audit_event_id=result.audit_event_id,
                state_transition_event_id=result.state_transition_event_id,
            )
            http_response.headers["Idempotency-Replayed"] = str(
                result.replayed
            ).lower()
            db.commit()
            return payload
        except AuthenticationSessionAdminError as exc:
            db.rollback()
            raise HTTPException(
                status_code=exc.http_status_code,
                detail=exc.as_detail(),
            ) from exc
    auth_session = db.get(AuthSession, session_id)
    if not auth_session:
        raise HTTPException(status_code=404, detail="登录设备不存在")
    revoke_session(auth_session, revoked_by_id=actor.id)
    audit(
        db,
        actor=actor,
        action="auth.session_revoked",
        entity_type="auth_session",
        entity_id=auth_session.id,
        detail={"user_id": auth_session.user_id, "device": auth_session.device_name},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut | FormalSelfOut)
def me(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if _production():
        principal = getattr(request.state, "formal_principal", None)
        if not isinstance(principal, FormalPrincipal):
            raise HTTPException(status_code=403, detail="账号当前没有有效的正式访问权限")
        try:
            return _formal_self(db, user, principal=principal)
        except SessionError as exc:
            raise HTTPException(
                status_code=403,
                detail="账号当前没有有效的正式访问权限",
            ) from exc
    return user


@router.post("/change-password")
def change_password(
    payload: PasswordChangeIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.require_password_change = False
    audit(
        db,
        actor=user,
        action="auth.password_changed",
        entity_type="user",
        entity_id=user.id,
        detail="用户修改密码",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"ok": True}


@router.get("/users", response_model=list[UserOut])
def list_users(
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    return list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name)))


@router.post("/users", response_model=UserOut)
def create_user(
    payload: UserCreateIn,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    mobile = payload.mobile.strip()
    name = payload.name.strip()
    province = payload.province.strip() if payload.province else None
    if payload.role not in ALLOWED_ROLES:
        raise HTTPException(status_code=400, detail="账号角色不合法")
    if payload.role in {"provincial_manager", "technician"} and not province:
        raise HTTPException(status_code=400, detail="该角色必须指定省份")
    if db.scalar(select(User).where(User.mobile == mobile)):
        raise HTTPException(status_code=409, detail="该手机号已存在")
    user = User(
        mobile=mobile,
        name=name,
        password_hash=hash_password(payload.temporary_password),
        role=payload.role,
        province=province,
        require_password_change=True,
    )
    db.add(user)
    db.flush()
    audit(
        db,
        actor=actor,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        detail={"mobile": mobile, "name": name, "role": payload.role, "province": province},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return user
