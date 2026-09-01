"""Database-backed admission control for formal passwordless login.

The limiter owns a deliberately separate, short database transaction.  Its
rows contain only dedicated, domain-separated HMACs.  Raw IP addresses, mobile
numbers, WeChat login/phone codes, openids and unionids are never persisted.

PostgreSQL transactions take advisory locks in one fixed order:
``global -> ip -> identity``.  SQLite is supported only for deterministic
sequential contract tests; production remains PostgreSQL 16 per the V1.0
baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from ipaddress import ip_address
import math
from typing import Final, Literal

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from ..foundation_models import AuthLoginRateLimitBucket


AuthenticationLoginOperation = Literal["sms_login", "wechat_login"]
AuthenticationRateLimitScope = Literal["global", "ip", "identity"]

_OPERATIONS: Final[frozenset[str]] = frozenset({"sms_login", "wechat_login"})
_MIN_SECRET_BYTES: Final[int] = 32
_MIN_WINDOW_SECONDS: Final[int] = 10
_MAX_WINDOW_SECONDS: Final[int] = 3600
_MAX_LIMIT: Final[int] = 1_000_000
_ADVISORY_LOCK_STATEMENT: Final[TextClause] = text(
    "SELECT pg_advisory_xact_lock(:lock_key)"
)


class AuthenticationLoginRateLimitError(RuntimeError):
    """Fail-closed configuration or persistence error."""

    def __init__(self, code: str, public_message: str) -> None:
        super().__init__(code)
        self.code = code
        self.public_message = public_message


@dataclass(frozen=True, slots=True)
class AuthenticationLoginRateLimitDecision:
    allowed: bool
    retry_after: int | None
    denied_scope: AuthenticationRateLimitScope | None
    consumed_scopes: tuple[AuthenticationRateLimitScope, ...]


@dataclass(frozen=True, slots=True)
class _ScopeSpec:
    scope_type: AuthenticationRateLimitScope
    scope_hmac: str
    limit: int


def postgresql_authentication_rate_limit_advisory_statement() -> TextClause:
    """Expose the fixed PostgreSQL lock statement for migration/test review."""

    return _ADVISORY_LOCK_STATEMENT


def consume_authentication_login_rate_limits(
    bind: Engine,
    *,
    operation_type: AuthenticationLoginOperation | str,
    hmac_secret: bytes | str,
    hash_version: int,
    window_seconds: int,
    global_limit: int | None = None,
    ip_address_value: str | None = None,
    ip_limit: int | None = None,
    identity_identifier: str | None = None,
    identity_limit: int | None = None,
    now: datetime | None = None,
) -> AuthenticationLoginRateLimitDecision:
    """Consume ordered login buckets in an independent short transaction.

    A denied scope is not incremented past its configured limit, but any
    earlier scope in the fixed order remains consumed.  That preserves the
    global attack budget even when a narrower IP or identity budget rejects.
    """

    if not isinstance(bind, Engine):
        _unavailable("authentication_login_rate_limit_independent_bind_required")
    checked_operation = _operation(operation_type)
    checked_secret = _secret(hmac_secret)
    checked_hash_version = _hash_version(hash_version)
    checked_window = _window_seconds(window_seconds)
    checked_now = _aware_utc(now or datetime.now(timezone.utc))
    window_started_at = _window_start(checked_now, checked_window)
    expires_at = window_started_at + timedelta(seconds=checked_window)
    cleanup_after = expires_at + timedelta(seconds=checked_window)
    retry_after = max(
        1,
        math.ceil((expires_at - checked_now).total_seconds()),
    )
    scopes = _scope_specs(
        operation_type=checked_operation,
        secret=checked_secret,
        hash_version=checked_hash_version,
        global_limit=global_limit,
        ip_address_value=ip_address_value,
        ip_limit=ip_limit,
        identity_identifier=identity_identifier,
        identity_limit=identity_limit,
    )
    if not scopes:
        _unavailable("authentication_login_rate_limit_scope_required")

    limiter_db = Session(bind=bind, autoflush=False, expire_on_commit=False)
    consumed: list[AuthenticationRateLimitScope] = []
    try:
        _acquire_hash_version_guard_lock(
            limiter_db,
            operation_type=checked_operation,
        )
        _assert_no_active_window_configuration_conflict(
            limiter_db,
            operation_type=checked_operation,
            window_seconds=checked_window,
            now=checked_now,
        )
        _assert_no_active_hash_version_conflict(
            limiter_db,
            operation_type=checked_operation,
            hash_version=checked_hash_version,
            now=checked_now,
        )
        _assert_no_active_global_hmac_conflict(
            limiter_db,
            operation_type=checked_operation,
            hash_version=checked_hash_version,
            scopes=scopes,
            now=checked_now,
        )
        for scope in scopes:
            _acquire_scope_lock(
                limiter_db,
                operation_type=checked_operation,
                scope=scope,
                hash_version=checked_hash_version,
                window_started_at=window_started_at,
                window_seconds=checked_window,
            )
            allowed = _consume_scope(
                limiter_db,
                operation_type=checked_operation,
                scope=scope,
                hash_version=checked_hash_version,
                window_started_at=window_started_at,
                window_seconds=checked_window,
                expires_at=expires_at,
                cleanup_after=cleanup_after,
                now=checked_now,
            )
            if not allowed:
                limiter_db.commit()
                return AuthenticationLoginRateLimitDecision(
                    allowed=False,
                    retry_after=retry_after,
                    denied_scope=scope.scope_type,
                    consumed_scopes=tuple(consumed),
                )
            consumed.append(scope.scope_type)
        limiter_db.commit()
    except AuthenticationLoginRateLimitError:
        limiter_db.rollback()
        raise
    except SQLAlchemyError as exc:
        limiter_db.rollback()
        raise AuthenticationLoginRateLimitError(
            "authentication_login_rate_limit_storage_unavailable",
            "登录保护服务暂时不可用",
        ) from exc
    finally:
        limiter_db.close()

    return AuthenticationLoginRateLimitDecision(
        allowed=True,
        retry_after=None,
        denied_scope=None,
        consumed_scopes=tuple(consumed),
    )


def cleanup_expired_authentication_login_rate_limit_buckets(
    bind: Engine,
    *,
    now: datetime | None = None,
    batch_size: int = 1000,
) -> int:
    """Delete only fully expired buckets in one bounded short transaction."""

    if not isinstance(bind, Engine):
        _unavailable("authentication_login_rate_limit_independent_bind_required")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not (
        1 <= batch_size <= 10_000
    ):
        _unavailable("authentication_login_rate_limit_cleanup_batch_invalid")
    checked_now = _aware_utc(now or datetime.now(timezone.utc))
    limiter_db = Session(bind=bind, autoflush=False, expire_on_commit=False)
    try:
        ids_statement = _expired_bucket_ids_statement(
            checked_now,
            batch_size=batch_size,
            postgresql_locking=(
                limiter_db.bind is not None
                and limiter_db.bind.dialect.name == "postgresql"
            ),
        )
        bucket_ids = list(limiter_db.scalars(ids_statement))
        if bucket_ids:
            limiter_db.execute(
                delete(AuthLoginRateLimitBucket).where(
                    AuthLoginRateLimitBucket.id.in_(bucket_ids)
                )
            )
        limiter_db.commit()
        return len(bucket_ids)
    except SQLAlchemyError as exc:
        limiter_db.rollback()
        raise AuthenticationLoginRateLimitError(
            "authentication_login_rate_limit_cleanup_unavailable",
            "登录保护清理服务暂时不可用",
        ) from exc
    finally:
        limiter_db.close()


def _expired_bucket_ids_statement(
    now: datetime,
    *,
    batch_size: int,
    postgresql_locking: bool,
):
    statement = (
        select(AuthLoginRateLimitBucket.id)
        .where(AuthLoginRateLimitBucket.cleanup_after <= now)
        .order_by(
            AuthLoginRateLimitBucket.cleanup_after,
            AuthLoginRateLimitBucket.id,
        )
        .limit(batch_size)
    )
    return (
        statement.with_for_update(skip_locked=True)
        if postgresql_locking
        else statement
    )


def _scope_specs(
    *,
    operation_type: AuthenticationLoginOperation,
    secret: bytes,
    hash_version: int,
    global_limit: int | None,
    ip_address_value: str | None,
    ip_limit: int | None,
    identity_identifier: str | None,
    identity_limit: int | None,
) -> tuple[_ScopeSpec, ...]:
    specs: list[_ScopeSpec] = []
    if global_limit is not None:
        specs.append(
            _ScopeSpec(
                scope_type="global",
                scope_hmac=_scope_hmac(
                    secret,
                    operation_type=operation_type,
                    scope_type="global",
                    hash_version=hash_version,
                    identifier=b"all",
                ),
                limit=_limit("global_limit", global_limit),
            )
        )
    if ip_address_value is not None or ip_limit is not None:
        if ip_address_value is None or ip_limit is None:
            _unavailable("authentication_login_rate_limit_ip_scope_incomplete")
        try:
            canonical_ip = ip_address(ip_address_value).compressed.encode("ascii")
        except (AttributeError, ValueError) as exc:
            raise AuthenticationLoginRateLimitError(
                "authentication_login_rate_limit_ip_invalid",
                "登录保护服务暂时不可用",
            ) from exc
        specs.append(
            _ScopeSpec(
                scope_type="ip",
                scope_hmac=_scope_hmac(
                    secret,
                    operation_type=operation_type,
                    scope_type="ip",
                    hash_version=hash_version,
                    identifier=canonical_ip,
                ),
                limit=_limit("ip_limit", ip_limit),
            )
        )
    if identity_identifier is not None or identity_limit is not None:
        if identity_identifier is None or identity_limit is None:
            _unavailable("authentication_login_rate_limit_identity_scope_incomplete")
        if not isinstance(identity_identifier, str) or not (
            1 <= len(identity_identifier.encode("utf-8")) <= 1024
        ):
            _unavailable("authentication_login_rate_limit_identity_invalid")
        specs.append(
            _ScopeSpec(
                scope_type="identity",
                scope_hmac=_scope_hmac(
                    secret,
                    operation_type=operation_type,
                    scope_type="identity",
                    hash_version=hash_version,
                    identifier=identity_identifier.encode("utf-8"),
                ),
                limit=_limit("identity_limit", identity_limit),
            )
        )
    return tuple(specs)


def _consume_scope(
    db: Session,
    *,
    operation_type: AuthenticationLoginOperation,
    scope: _ScopeSpec,
    hash_version: int,
    window_started_at: datetime,
    window_seconds: int,
    expires_at: datetime,
    cleanup_after: datetime,
    now: datetime,
) -> bool:
    statement = select(AuthLoginRateLimitBucket).where(
        AuthLoginRateLimitBucket.operation_type == operation_type,
        AuthLoginRateLimitBucket.scope_type == scope.scope_type,
        AuthLoginRateLimitBucket.hash_version == hash_version,
        AuthLoginRateLimitBucket.scope_hmac == scope.scope_hmac,
        AuthLoginRateLimitBucket.window_started_at == window_started_at,
        AuthLoginRateLimitBucket.window_seconds == window_seconds,
    )
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    bucket = db.scalar(statement)
    if bucket is None:
        db.add(
            AuthLoginRateLimitBucket(
                operation_type=operation_type,
                scope_type=scope.scope_type,
                hash_version=hash_version,
                scope_hmac=scope.scope_hmac,
                window_started_at=window_started_at,
                window_seconds=window_seconds,
                request_count=1,
                expires_at=expires_at,
                cleanup_after=cleanup_after,
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        return True
    if bucket.request_count >= scope.limit:
        return False
    bucket.request_count += 1
    bucket.updated_at = now
    db.flush()
    return True


def _acquire_scope_lock(
    db: Session,
    *,
    operation_type: AuthenticationLoginOperation,
    scope: _ScopeSpec,
    hash_version: int,
    window_started_at: datetime,
    window_seconds: int,
) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    lock_key = _advisory_lock_key(
        operation_type=operation_type,
        scope_type=scope.scope_type,
        hash_version=hash_version,
        scope_hmac=scope.scope_hmac,
        window_started_at=window_started_at,
        window_seconds=window_seconds,
    )
    db.execute(_ADVISORY_LOCK_STATEMENT, {"lock_key": lock_key})


def _acquire_hash_version_guard_lock(
    db: Session,
    *,
    operation_type: AuthenticationLoginOperation,
) -> None:
    """Serialize version admission before any versioned scope lock."""

    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    document = b"\x00".join(
        (
            b"rsc-auth-login-rate-version-guard-v1",
            operation_type.encode("ascii"),
        )
    )
    unsigned = int.from_bytes(hashlib.sha256(document).digest()[:8], "big")
    lock_key = unsigned if unsigned < 2**63 else unsigned - 2**64
    db.execute(_ADVISORY_LOCK_STATEMENT, {"lock_key": lock_key})


def _assert_no_active_hash_version_conflict(
    db: Session,
    *,
    operation_type: AuthenticationLoginOperation,
    hash_version: int,
    now: datetime,
) -> None:
    conflicting = db.scalar(
        select(AuthLoginRateLimitBucket.id)
        .where(
            AuthLoginRateLimitBucket.operation_type == operation_type,
            AuthLoginRateLimitBucket.hash_version != hash_version,
            AuthLoginRateLimitBucket.expires_at > now,
        )
        .limit(1)
    )
    if conflicting is not None:
        _unavailable("authentication_login_rate_limit_hash_rotation_in_progress")


def _assert_no_active_window_configuration_conflict(
    db: Session,
    *,
    operation_type: AuthenticationLoginOperation,
    window_seconds: int,
    now: datetime,
) -> None:
    conflicting = db.scalar(
        select(AuthLoginRateLimitBucket.id)
        .where(
            AuthLoginRateLimitBucket.operation_type == operation_type,
            AuthLoginRateLimitBucket.window_seconds != window_seconds,
            AuthLoginRateLimitBucket.expires_at > now,
        )
        .limit(1)
    )
    if conflicting is not None:
        _unavailable(
            "authentication_login_rate_limit_window_change_in_progress"
        )


def _assert_no_active_global_hmac_conflict(
    db: Session,
    *,
    operation_type: AuthenticationLoginOperation,
    hash_version: int,
    scopes: tuple[_ScopeSpec, ...],
    now: datetime,
) -> None:
    expected = next(
        (scope.scope_hmac for scope in scopes if scope.scope_type == "global"),
        None,
    )
    if expected is None:
        # WeChat's identity-only second stage is admitted only after its
        # global/IP stage has committed under the same settings snapshot.
        return
    active_hmacs = list(
        db.scalars(
            select(AuthLoginRateLimitBucket.scope_hmac).where(
                AuthLoginRateLimitBucket.operation_type == operation_type,
                AuthLoginRateLimitBucket.scope_type == "global",
                AuthLoginRateLimitBucket.hash_version == hash_version,
                AuthLoginRateLimitBucket.expires_at > now,
            )
        )
    )
    if any(not hmac.compare_digest(value, expected) for value in active_hmacs):
        _unavailable(
            "authentication_login_rate_limit_secret_changed_without_version"
        )


def _advisory_lock_key(
    *,
    operation_type: AuthenticationLoginOperation,
    scope_type: AuthenticationRateLimitScope,
    hash_version: int,
    scope_hmac: str,
    window_started_at: datetime,
    window_seconds: int,
) -> int:
    document = b"\x00".join(
        (
            b"rsc-auth-login-rate-advisory-v1",
            operation_type.encode("ascii"),
            scope_type.encode("ascii"),
            str(hash_version).encode("ascii"),
            scope_hmac.encode("ascii"),
            str(int(window_started_at.timestamp())).encode("ascii"),
            str(window_seconds).encode("ascii"),
        )
    )
    unsigned = int.from_bytes(hashlib.sha256(document).digest()[:8], "big")
    return unsigned if unsigned < 2**63 else unsigned - 2**64


def _scope_hmac(
    secret: bytes,
    *,
    operation_type: AuthenticationLoginOperation,
    scope_type: AuthenticationRateLimitScope,
    hash_version: int,
    identifier: bytes,
) -> str:
    document = b"\x00".join(
        (
            b"rsc-auth-login-rate-scope-v1",
            operation_type.encode("ascii"),
            scope_type.encode("ascii"),
            str(hash_version).encode("ascii"),
            identifier,
        )
    )
    return hmac.new(secret, document, hashlib.sha256).hexdigest()


def _operation(value: str) -> AuthenticationLoginOperation:
    if value not in _OPERATIONS:
        _unavailable("authentication_login_rate_limit_operation_invalid")
    return value  # type: ignore[return-value]


def _secret(value: bytes | str) -> bytes:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    elif isinstance(value, bytes):
        encoded = value
    else:
        encoded = b""
    if len(encoded) < _MIN_SECRET_BYTES:
        _unavailable("authentication_login_rate_limit_hmac_secret_invalid")
    return encoded


def _window_seconds(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (
        _MIN_WINDOW_SECONDS <= value <= _MAX_WINDOW_SECONDS
    ):
        _unavailable("authentication_login_rate_limit_window_invalid")
    return value


def _hash_version(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (
        1 <= value <= 2_147_483_647
    ):
        _unavailable("authentication_login_rate_limit_hash_version_invalid")
    return value


def _limit(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not (
        1 <= value <= _MAX_LIMIT
    ):
        _unavailable(f"authentication_login_rate_limit_{name}_invalid")
    return value


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _unavailable("authentication_login_rate_limit_time_invalid")
    return value.astimezone(timezone.utc)


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch_seconds = math.floor(now.timestamp())
    started_epoch = (epoch_seconds // window_seconds) * window_seconds
    return datetime.fromtimestamp(started_epoch, tz=timezone.utc)


def _unavailable(code: str) -> None:
    raise AuthenticationLoginRateLimitError(code, "登录保护服务暂时不可用")
