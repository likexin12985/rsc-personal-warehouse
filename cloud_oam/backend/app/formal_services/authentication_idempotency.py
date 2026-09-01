"""Encrypted, transaction-owned idempotency for formal authentication writes.

The formal login and session endpoints return credentials that cannot be
reconstructed from their persisted hashes after a network timeout.  This
service stores a short-lived, AES-256-GCM encrypted response alongside one
globally unique HMAC of the caller's idempotency key.  A matching retry can
therefore return the exact original credentials without consuming a login
challenge twice or treating a legitimate refresh retry as token theft.

Security and transaction boundaries:

* raw idempotency keys, request documents, tokens, mobile numbers, codes,
  device identifiers and addresses are never assigned to an ORM field;
* request and scope documents are canonical-JSON HMACed with a server secret;
* the encrypted response uses a random 96-bit nonce and AAD that binds the
  operation row, operation/client types, all stored request digests, key
  version and expiry;
* this module flushes but never commits or rolls back the caller's transaction;
* production construction accepts only the explicit KMS-backed provider
  adapter.  There is deliberately no environment-variable/plaintext-master-key
  factory path.

PostgreSQL is the production concurrency authority: ``FOR UPDATE`` serializes
an existing key and the global unique constraint arbitrates first insertion.
SQLite exercises the state contract but does not reproduce PostgreSQL row-lock
or unique-index wait semantics; PostgreSQL 16 concurrency tests remain a
separate release gate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any, Final, Literal, Protocol
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..foundation_models import AuthIdempotencyOperation


AuthenticationOperationType = Literal[
    "sms_login",
    "wechat_login",
    "session_refresh",
    "session_logout",
]
AuthenticationClientType = Literal["web", "miniprogram"]
AuthenticationOperationStatus = Literal["pending", "completed", "failed"]
AuthenticationCompletionOutcome = Literal["success", "controlled_failure"]

SUPPORTED_OPERATION_TYPES: Final[frozenset[str]] = frozenset(
    {"sms_login", "wechat_login", "session_refresh", "session_logout"}
)
SUPPORTED_CLIENT_TYPES: Final[frozenset[str]] = frozenset({"web", "miniprogram"})
FINAL_STATUSES: Final[frozenset[str]] = frozenset({"completed", "failed"})

_VISIBLE_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{16,128}$", re.ASCII)
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_KMS_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+-]{2,255}$", re.ASCII)
_SECRET_PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "change-me",
    "changeme",
)
_AES_256_KEY_BYTES: Final[int] = 32
_GCM_NONCE_BYTES: Final[int] = 12
_GCM_TAG_BYTES: Final[int] = 16
_MAX_JSON_BYTES: Final[int] = 64 * 1024
_MAX_JSON_DEPTH: Final[int] = 32
_MIN_HMAC_SECRET_BYTES: Final[int] = 32
_MIN_REPLAY_TTL_SECONDS: Final[int] = 30
_MAX_REPLAY_TTL_SECONDS: Final[int] = 120
_RESPONSE_SCHEMA: Final[str] = "formal-auth-idempotency-response-v1"
_AAD_SCHEMA: Final[str] = "formal-auth-idempotency-aad-v1"

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "conflict": 409,
    "service_unavailable": 503,
}


class AuthenticationIdempotencyError(RuntimeError):
    """Stable, non-sensitive failure for an API boundary."""

    def __init__(self, *, code: str, category: str, public_message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported authentication idempotency category: {category}")
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


class AuthenticationCipherConfigurationError(AuthenticationIdempotencyError):
    """The response-key boundary is absent or unsafe."""


class AuthenticationEncryptionKeyUnavailable(RuntimeError):
    """The active or historical data-encryption key cannot be resolved."""


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticationEncryptionKey:
    """One versioned AES-256 data key; repr intentionally hides key bytes."""

    version: int
    key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise ValueError("encryption key version must be an integer")
        if self.version <= 0:
            raise ValueError("encryption key version must be positive")
        if not isinstance(self.key, bytes) or len(self.key) != _AES_256_KEY_BYTES:
            raise ValueError("authentication response encryption requires 32-byte keys")


class AuthenticationEncryptionKeyProvider(Protocol):
    """Version-aware source of AES data keys."""

    def current_key(self) -> AuthenticationEncryptionKey:
        """Return the current encryption key and its immutable version."""

    def key_for_version(self, version: int) -> AuthenticationEncryptionKey:
        """Resolve an active or retained historical key by exact version."""


class StaticAuthenticationKeyProvider:
    """Single static key provider for local unit tests only.

    The production cipher factory rejects this exact type and every other
    non-KMS provider.  Keeping it explicit avoids a generic environment-key
    implementation being accidentally promoted to production configuration.
    """

    def __init__(self, *, key: bytes, version: int = 1) -> None:
        self._material = AuthenticationEncryptionKey(version=version, key=key)

    def current_key(self) -> AuthenticationEncryptionKey:
        return self._material

    def key_for_version(self, version: int) -> AuthenticationEncryptionKey:
        if version != self._material.version:
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key version is unavailable"
            )
        return self._material


class KmsAuthenticationKeyProvider:
    """Adapter for KMS-unwrapped, versioned authentication response data keys.

    ``key_loader`` is an application-owned KMS adapter.  It receives the
    reviewed KMS key identifier and an exact key version, and must obtain the
    corresponding 32-byte data key through KMS/envelope-key infrastructure.
    This module never reads an encryption master key from settings or the
    environment.  Merely labelling another provider as KMS cannot pass the
    production factory because the factory requires this exact class.
    """

    def __init__(
        self,
        *,
        kms_key_id: str,
        active_version: int,
        key_loader: Callable[[str, int], bytes],
    ) -> None:
        normalized_key_id = kms_key_id.strip() if isinstance(kms_key_id, str) else ""
        if (
            not _SAFE_KMS_KEY_ID.fullmatch(normalized_key_id)
            or any(marker in normalized_key_id.lower() for marker in _SECRET_PLACEHOLDER_MARKERS)
        ):
            raise ValueError("a non-placeholder KMS key identifier is required")
        if not isinstance(active_version, int) or isinstance(active_version, bool):
            raise ValueError("active KMS key version must be an integer")
        if active_version <= 0:
            raise ValueError("active KMS key version must be positive")
        if not callable(key_loader):
            raise ValueError("KMS key loader must be callable")
        self._kms_key_id = normalized_key_id
        self._active_version = active_version
        self._key_loader = key_loader

    @property
    def kms_key_id(self) -> str:
        return self._kms_key_id

    def current_key(self) -> AuthenticationEncryptionKey:
        return self.key_for_version(self._active_version)

    def key_for_version(self, version: int) -> AuthenticationEncryptionKey:
        if not isinstance(version, int) or isinstance(version, bool) or version <= 0:
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key version is unavailable"
            )
        try:
            key = self._key_loader(self._kms_key_id, version)
            return AuthenticationEncryptionKey(version=version, key=key)
        except AuthenticationEncryptionKeyUnavailable:
            raise
        except Exception as exc:
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key version is unavailable"
            ) from exc


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedAuthenticationResponse:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class Aes256GcmAuthenticationResponseCipher:
    """AES-256-GCM response cipher with fresh 96-bit nonces.

    One cipher instance belongs to one authentication request.  Versioned data
    keys are therefore resolved at most once per instance and retained only in
    request memory.  Besides avoiding redundant KMS traffic, this guarantees
    that the key preflight performed before a state mutation is the exact key
    later used to seal its terminal response; a transient second KMS lookup
    cannot roll back an already-detected refresh-token replay revocation.
    """

    def __init__(
        self,
        *,
        key_provider: AuthenticationEncryptionKeyProvider,
        random_bytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self._key_provider = key_provider
        self._random_bytes = random_bytes
        self._resolved_keys: dict[int, AuthenticationEncryptionKey] = {}
        self._active_key_version: int | None = None

    def active_key_version(self) -> int:
        if self._active_key_version is not None:
            return self._active_key_version
        try:
            material = self._key_provider.current_key()
        except AuthenticationEncryptionKeyUnavailable:
            raise
        except Exception as exc:
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key is unavailable"
            ) from exc
        if not isinstance(material, AuthenticationEncryptionKey):
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key is unavailable"
            )
        cached = self._resolved_keys.get(material.version)
        if cached is not None and not hmac.compare_digest(cached.key, material.key):
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key version changed within request"
            )
        self._resolved_keys[material.version] = material
        self._active_key_version = material.version
        return material.version

    def encrypt(
        self,
        plaintext: bytes,
        *,
        aad: bytes,
        key_version: int,
    ) -> EncryptedAuthenticationResponse:
        material = self._resolve_key(key_version)
        try:
            nonce = self._random_bytes(_GCM_NONCE_BYTES)
        except Exception as exc:
            raise AuthenticationEncryptionKeyUnavailable(
                "secure nonce generation failed"
            ) from exc
        if not isinstance(nonce, bytes) or len(nonce) != _GCM_NONCE_BYTES:
            raise AuthenticationEncryptionKeyUnavailable(
                "secure nonce generation returned an invalid nonce"
            )
        ciphertext = AESGCM(material.key).encrypt(nonce, plaintext, aad)
        return EncryptedAuthenticationResponse(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=material.version,
        )

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes:
        material = self._resolve_key(key_version)
        return AESGCM(material.key).decrypt(nonce, ciphertext, aad)

    def _resolve_key(self, version: int) -> AuthenticationEncryptionKey:
        cached = self._resolved_keys.get(version)
        if cached is not None:
            return cached
        try:
            material = self._key_provider.key_for_version(version)
        except AuthenticationEncryptionKeyUnavailable:
            raise
        except Exception as exc:
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key is unavailable"
            ) from exc
        if material.version != version:
            raise AuthenticationEncryptionKeyUnavailable(
                "authentication encryption key version mismatch"
            )
        self._resolved_keys[version] = material
        return material


def create_authentication_response_cipher(
    *,
    environment: str,
    key_provider: AuthenticationEncryptionKeyProvider,
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> Aes256GcmAuthenticationResponseCipher:
    """Create the response cipher while rejecting plaintext production modes.

    Production accepts exactly :class:`KmsAuthenticationKeyProvider`.  Static
    keys can be injected only in development/test/staging and there is no
    parameter that accepts a plaintext key or environment variable value.
    """

    if environment not in {"development", "test", "staging", "production"}:
        _fail(
            "authentication_cipher_environment_invalid",
            "invalid_request",
            "认证幂等加密配置无效",
            error_type=AuthenticationCipherConfigurationError,
        )
    if environment == "production" and type(key_provider) is not KmsAuthenticationKeyProvider:
        _fail(
            "authentication_kms_required",
            "service_unavailable",
            "认证幂等加密服务不可用",
            error_type=AuthenticationCipherConfigurationError,
        )
    return Aes256GcmAuthenticationResponseCipher(
        key_provider=key_provider,
        random_bytes=random_bytes,
    )


def create_configured_authentication_response_cipher(
    *,
    settings: Any,
    kms_key_loader: Callable[[str, int], bytes] | None,
    random_bytes: Callable[[int], bytes] = os.urandom,
) -> Aes256GcmAuthenticationResponseCipher:
    """Build the runtime cipher from reviewed KMS coordinates.

    The application composition root must inject a real KMS/envelope-key
    loader.  Settings provide only a KMS key identifier and version; there is
    no plaintext encryption-key setting.  ``None`` or a non-KMS provider fails
    closed in every environment so a disabled mode cannot masquerade as the
    formal runtime.
    """

    environment = getattr(settings, "environment", None)
    provider = getattr(settings, "auth_idempotency_encryption_provider", None)
    kms_key_id = getattr(settings, "auth_idempotency_kms_key_id", None)
    key_version = getattr(
        settings, "auth_idempotency_encryption_key_version", None
    )
    if provider != "aliyun_kms" or kms_key_loader is None:
        _fail(
            "authentication_kms_required",
            "service_unavailable",
            "认证幂等加密服务不可用",
            error_type=AuthenticationCipherConfigurationError,
        )
    try:
        key_provider = KmsAuthenticationKeyProvider(
            kms_key_id=kms_key_id,
            active_version=key_version,
            key_loader=kms_key_loader,
        )
    except (TypeError, ValueError) as exc:
        _fail(
            "authentication_kms_configuration_invalid",
            "service_unavailable",
            "认证幂等加密服务不可用",
            cause=exc,
            error_type=AuthenticationCipherConfigurationError,
        )
    return create_authentication_response_cipher(
        environment=environment,
        key_provider=key_provider,
        random_bytes=random_bytes,
    )


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticationIdempotentResponse:
    operation_id: uuid.UUID
    status: Literal["completed", "failed"]
    http_status: int
    payload: dict[str, Any]
    replayed: bool


@dataclass(frozen=True, slots=True)
class AuthenticationIdempotencyBeginResult:
    operation: AuthIdempotencyOperation
    disposition: Literal["execute", "replay"]
    response: AuthenticationIdempotentResponse | None = None

    @property
    def replayed(self) -> bool:
        return self.disposition == "replay"


def probe_existing_authentication_operation(
    db: Session,
    *,
    operation_type: str,
    client_type: str,
    idempotency_key: str,
    scope: Mapping[str, Any],
    request: Mapping[str, Any],
    hmac_secret: bytes | str,
    now: datetime | None = None,
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | str | None = None,
) -> AuthIdempotencyOperation | None:
    """Read-only probe used before a new login consumes rate-limit budget.

    An exact, unexpired terminal operation may proceed to the normal encrypted
    replay path without being changed into a 429 when a bucket is full.  A
    reused key with a different request, a pending operation, or expired/broken
    evidence fails with the same public contract as ``begin``.  This function
    never inserts, locks, decrypts, resolves KMS keys, commits or rolls back.
    """

    checked_operation_type = _require_operation_type(operation_type)
    checked_client_type = _require_client_type(client_type)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_secret = _require_hmac_secret(hmac_secret)
    checked_now = _require_aware_datetime("now", now or datetime.now(timezone.utc))
    scope_document = _canonical_json_bytes("scope", scope)
    request_document = _canonical_json_bytes("request", request)
    key_hash = _hmac_digest(
        checked_secret,
        b"formal-auth-idempotency-key-v1",
        checked_key.encode("ascii"),
    )
    scope_hash = _hmac_digest(
        checked_secret,
        b"formal-auth-idempotency-scope-v1",
        scope_document,
    )
    request_hmac = _hmac_digest(
        checked_secret,
        b"formal-auth-idempotency-request-v1",
        _canonical_json_bytes(
            "request envelope",
            {
                "client_type": checked_client_type,
                "operation_type": checked_operation_type,
                "request": json.loads(request_document),
                "scope_hash": scope_hash,
            },
        ),
    )
    checked_auth_session_id = _optional_auth_session_id(auth_session_id)
    checked_input_token_id = _optional_uuid(
        "input_refresh_token_id", input_refresh_token_id
    )
    existing = _find_by_key_hash(db, key_hash)
    if existing is None:
        return None
    for persisted, supplied in (
        (existing.operation_type, checked_operation_type),
        (existing.client_type, checked_client_type),
        (existing.scope_hash, scope_hash),
        (existing.request_hmac, request_hmac),
    ):
        if not isinstance(persisted, str) or not hmac.compare_digest(
            persisted, supplied
        ):
            _idempotency_conflict()
    if checked_operation_type in {"sms_login", "wechat_login"}:
        if checked_auth_session_id is not None:
            _idempotency_conflict()
    elif existing.auth_session_id != checked_auth_session_id:
        _idempotency_conflict()
    if existing.input_refresh_token_id != checked_input_token_id:
        _idempotency_conflict()
    if _as_utc(existing.expires_at) <= checked_now:
        _fail(
            "authentication_idempotency_expired",
            "conflict",
            "认证请求结果已过期，请重新登录",
        )
    if existing.status == "pending":
        _fail(
            "authentication_idempotency_pending",
            "conflict",
            "相同认证请求正在处理中",
        )
    if existing.status not in FINAL_STATUSES:
        _fail(
            "authentication_idempotency_state_invalid",
            "service_unavailable",
            "认证幂等证据不可用",
        )
    return existing


def begin_authentication_operation(
    db: Session,
    *,
    operation_type: str,
    client_type: str,
    idempotency_key: str,
    scope: Mapping[str, Any],
    request: Mapping[str, Any],
    hmac_secret: bytes | str,
    cipher: Aes256GcmAuthenticationResponseCipher,
    expires_at: datetime,
    now: datetime | None = None,
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | str | None = None,
) -> AuthenticationIdempotencyBeginResult:
    """Begin or replay one formal authentication write.

    A newly inserted row is returned with ``disposition == 'execute'``.
    Matching completed or controlled-failure rows are decrypted and returned
    with ``disposition == 'replay'``.  Pending, expired, mismatched or
    undecryptable evidence fails closed and never starts another operation.
    """

    checked_operation_type = _require_operation_type(operation_type)
    checked_client_type = _require_client_type(client_type)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_secret = _require_hmac_secret(hmac_secret)
    checked_now = _require_aware_datetime("now", now or datetime.now(timezone.utc))
    checked_expires_at = _require_aware_datetime("expires_at", expires_at)
    ttl_seconds = (checked_expires_at - checked_now).total_seconds()
    if not _MIN_REPLAY_TTL_SECONDS <= ttl_seconds <= _MAX_REPLAY_TTL_SECONDS:
        _fail(
            "authentication_idempotency_expiry_invalid",
            "invalid_request",
            "认证幂等有效期无效",
        )

    scope_document = _canonical_json_bytes("scope", scope)
    request_document = _canonical_json_bytes("request", request)
    key_hash = _hmac_digest(
        checked_secret,
        b"formal-auth-idempotency-key-v1",
        checked_key.encode("ascii"),
    )
    scope_hash = _hmac_digest(
        checked_secret,
        b"formal-auth-idempotency-scope-v1",
        scope_document,
    )
    request_hmac = _hmac_digest(
        checked_secret,
        b"formal-auth-idempotency-request-v1",
        _canonical_json_bytes(
            "request envelope",
            {
                "client_type": checked_client_type,
                "operation_type": checked_operation_type,
                "request": json.loads(request_document),
                "scope_hash": scope_hash,
            },
        ),
    )
    checked_auth_session_id = _optional_auth_session_id(auth_session_id)
    checked_input_token_id = _optional_uuid(
        "input_refresh_token_id", input_refresh_token_id
    )
    if (
        checked_operation_type in {"sms_login", "wechat_login"}
        and checked_auth_session_id is not None
    ):
        _fail(
            "authentication_login_input_session_forbidden",
            "invalid_request",
            "登录幂等请求不能预先绑定输出会话",
        )

    existing = _lock_by_key_hash(db, key_hash)
    if existing is not None:
        return _evaluate_existing_operation(
            existing,
            operation_type=checked_operation_type,
            client_type=checked_client_type,
            scope_hash=scope_hash,
            request_hmac=request_hmac,
            auth_session_id=checked_auth_session_id,
            input_refresh_token_id=checked_input_token_id,
            cipher=cipher,
            now=checked_now,
        )

    operation = AuthIdempotencyOperation(
        operation_type=checked_operation_type,
        client_type=checked_client_type,
        idempotency_key_hash=key_hash,
        scope_hash=scope_hash,
        request_hmac=request_hmac,
        status="pending",
        auth_session_id=checked_auth_session_id,
        input_refresh_token_id=checked_input_token_id,
        expires_at=checked_expires_at,
    )
    _ensure_outer_transaction(db)
    try:
        # The savepoint contains only arbitration of the unique key.  It never
        # commits the caller's surrounding authentication transaction.
        with db.begin_nested():
            db.add(operation)
            db.flush()
    except IntegrityError as exc:
        existing = _lock_by_key_hash(db, key_hash)
        if existing is None:
            _fail(
                "authentication_idempotency_arbitration_failed",
                "service_unavailable",
                "认证幂等协调不可用",
                cause=exc,
            )
        return _evaluate_existing_operation(
            existing,
            operation_type=checked_operation_type,
            client_type=checked_client_type,
            scope_hash=scope_hash,
            request_hmac=request_hmac,
            auth_session_id=checked_auth_session_id,
            input_refresh_token_id=checked_input_token_id,
            cipher=cipher,
            now=checked_now,
        )
    return AuthenticationIdempotencyBeginResult(
        operation=operation,
        disposition="execute",
    )


def complete_authentication_operation(
    db: Session,
    *,
    begin: AuthenticationIdempotencyBeginResult,
    payload: Mapping[str, Any],
    http_status: int,
    outcome: AuthenticationCompletionOutcome,
    cipher: Aes256GcmAuthenticationResponseCipher,
    now: datetime | None = None,
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | str | None = None,
    output_refresh_token_id: uuid.UUID | str | None = None,
) -> AuthenticationIdempotentResponse:
    """Encrypt and persist one successful or controlled-failure response."""

    if not isinstance(begin, AuthenticationIdempotencyBeginResult):
        _fail(
            "authentication_idempotency_begin_required",
            "invalid_request",
            "认证幂等操作无效",
        )
    if begin.disposition != "execute" or begin.response is not None:
        _fail(
            "authentication_idempotency_already_replayed",
            "conflict",
            "认证幂等操作已完成",
        )
    if outcome not in {"success", "controlled_failure"}:
        _fail(
            "authentication_idempotency_outcome_invalid",
            "invalid_request",
            "认证幂等完成结果无效",
        )
    if (
        not isinstance(http_status, int)
        or isinstance(http_status, bool)
        or http_status < 200
        or http_status > 599
        or (outcome == "success" and http_status >= 300)
        or (outcome == "controlled_failure" and http_status < 400)
    ):
        _fail(
            "authentication_idempotency_http_status_invalid",
            "invalid_request",
            "认证幂等响应状态无效",
        )

    checked_now = _require_aware_datetime("now", now or datetime.now(timezone.utc))
    checked_auth_session_id = _optional_auth_session_id(auth_session_id)
    checked_input_token_id = _optional_uuid(
        "input_refresh_token_id", input_refresh_token_id
    )
    checked_output_token_id = _optional_uuid(
        "output_refresh_token_id", output_refresh_token_id
    )
    if outcome == "controlled_failure" and checked_output_token_id is not None:
        _fail(
            "authentication_idempotency_failed_output_token_forbidden",
            "invalid_request",
            "认证失败结果不能关联输出令牌",
        )

    operation_id = _require_uuid("operation_id", begin.operation.id)
    operation = db.scalar(
        select(AuthIdempotencyOperation)
        .where(AuthIdempotencyOperation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if operation is None:
        _fail(
            "authentication_idempotency_operation_missing",
            "service_unavailable",
            "认证幂等协调不可用",
        )
    _require_same_begin_identity(operation, begin.operation)
    if _as_utc(operation.expires_at) <= checked_now:
        _fail(
            "authentication_idempotency_expired",
            "conflict",
            "认证请求结果已过期，请重新登录",
        )
    if operation.status in FINAL_STATUSES:
        return _decrypt_response(operation, cipher=cipher, replayed=True)
    if operation.status != "pending":
        _fail(
            "authentication_idempotency_state_invalid",
            "service_unavailable",
            "认证幂等证据不可用",
        )

    _merge_reference(
        operation,
        "auth_session_id",
        checked_auth_session_id,
    )
    _merge_reference(
        operation,
        "input_refresh_token_id",
        checked_input_token_id,
    )
    if outcome == "success":
        _merge_reference(
            operation,
            "output_refresh_token_id",
            checked_output_token_id,
        )

    terminal_status: Literal["completed", "failed"] = (
        "completed" if outcome == "success" else "failed"
    )
    response_payload = _canonical_json_bytes("response payload", payload)
    response_document = _canonical_json_bytes(
        "response",
        {
            "http_status": http_status,
            "payload": json.loads(response_payload),
            "schema": _RESPONSE_SCHEMA,
            "status": terminal_status,
        },
    )
    try:
        key_version = cipher.active_key_version()
        aad = _operation_aad(operation, key_version=key_version)
        envelope = cipher.encrypt(
            response_document,
            aad=aad,
            key_version=key_version,
        )
    except (AuthenticationEncryptionKeyUnavailable, InvalidTag) as exc:
        _fail(
            "authentication_idempotency_encryption_unavailable",
            "service_unavailable",
            "认证幂等加密服务不可用",
            cause=exc,
        )

    operation.status = terminal_status
    operation.response_ciphertext = envelope.ciphertext
    operation.response_nonce = envelope.nonce
    operation.response_sha256 = _response_envelope_sha256(
        envelope.nonce, envelope.ciphertext
    )
    operation.encryption_key_version = envelope.key_version
    operation.http_status = http_status
    operation.completed_at = checked_now
    try:
        db.flush()
    except IntegrityError as exc:
        _fail(
            "authentication_idempotency_completion_conflict",
            "conflict",
            "认证幂等操作状态已变化",
            cause=exc,
        )
    return AuthenticationIdempotentResponse(
        operation_id=operation.id,
        status=terminal_status,
        http_status=http_status,
        payload=json.loads(response_payload),
        replayed=False,
    )


def complete_authentication_success(
    db: Session,
    *,
    begin: AuthenticationIdempotencyBeginResult,
    payload: Mapping[str, Any],
    http_status: int,
    cipher: Aes256GcmAuthenticationResponseCipher,
    now: datetime | None = None,
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | str | None = None,
    output_refresh_token_id: uuid.UUID | str | None = None,
) -> AuthenticationIdempotentResponse:
    """Convenience wrapper for a successful terminal result."""

    return complete_authentication_operation(
        db,
        begin=begin,
        payload=payload,
        http_status=http_status,
        outcome="success",
        cipher=cipher,
        now=now,
        auth_session_id=auth_session_id,
        input_refresh_token_id=input_refresh_token_id,
        output_refresh_token_id=output_refresh_token_id,
    )


def complete_authentication_failure(
    db: Session,
    *,
    begin: AuthenticationIdempotencyBeginResult,
    payload: Mapping[str, Any],
    http_status: int,
    cipher: Aes256GcmAuthenticationResponseCipher,
    now: datetime | None = None,
    auth_session_id: str | None = None,
    input_refresh_token_id: uuid.UUID | str | None = None,
) -> AuthenticationIdempotentResponse:
    """Convenience wrapper for a controlled, replayable failure result."""

    return complete_authentication_operation(
        db,
        begin=begin,
        payload=payload,
        http_status=http_status,
        outcome="controlled_failure",
        cipher=cipher,
        now=now,
        auth_session_id=auth_session_id,
        input_refresh_token_id=input_refresh_token_id,
    )


def _evaluate_existing_operation(
    operation: AuthIdempotencyOperation,
    *,
    operation_type: str,
    client_type: str,
    scope_hash: str,
    request_hmac: str,
    auth_session_id: str | None,
    input_refresh_token_id: uuid.UUID | None,
    cipher: Aes256GcmAuthenticationResponseCipher,
    now: datetime,
) -> AuthenticationIdempotencyBeginResult:
    for persisted, supplied in (
        (operation.operation_type, operation_type),
        (operation.client_type, client_type),
        (operation.scope_hash, scope_hash),
        (operation.request_hmac, request_hmac),
    ):
        if not isinstance(persisted, str):
            _response_unavailable()
        if not hmac.compare_digest(persisted, supplied):
            _idempotency_conflict()
    # Login creates the session, so its row reference is an output populated by
    # complete_success.  A network retry necessarily begins with ``None`` and
    # must still be able to decrypt that terminal result.  Refresh/logout bind
    # an input session and therefore continue to require an exact match.
    if operation_type in {"sms_login", "wechat_login"}:
        if auth_session_id is not None:
            _idempotency_conflict()
    elif operation.auth_session_id != auth_session_id:
        _idempotency_conflict()
    if operation.input_refresh_token_id != input_refresh_token_id:
        _idempotency_conflict()
    if _as_utc(operation.expires_at) <= now:
        _fail(
            "authentication_idempotency_expired",
            "conflict",
            "认证请求结果已过期，请重新登录",
        )
    if operation.status == "pending":
        _fail(
            "authentication_idempotency_pending",
            "conflict",
            "相同认证请求正在处理中",
        )
    if operation.status not in FINAL_STATUSES:
        _fail(
            "authentication_idempotency_state_invalid",
            "service_unavailable",
            "认证幂等证据不可用",
        )
    return AuthenticationIdempotencyBeginResult(
        operation=operation,
        disposition="replay",
        response=_decrypt_response(operation, cipher=cipher, replayed=True),
    )


def _decrypt_response(
    operation: AuthIdempotencyOperation,
    *,
    cipher: Aes256GcmAuthenticationResponseCipher,
    replayed: bool,
) -> AuthenticationIdempotentResponse:
    _validate_terminal_evidence(operation)
    nonce = bytes(operation.response_nonce)
    ciphertext = bytes(operation.response_ciphertext)
    expected_hash = _response_envelope_sha256(nonce, ciphertext)
    if not hmac.compare_digest(expected_hash, operation.response_sha256):
        _response_unavailable()
    aad = _operation_aad(
        operation,
        key_version=operation.encryption_key_version,
    )
    try:
        plaintext = cipher.decrypt(
            ciphertext,
            nonce=nonce,
            aad=aad,
            key_version=operation.encryption_key_version,
        )
    except (AuthenticationEncryptionKeyUnavailable, InvalidTag, ValueError):
        _response_unavailable()
    document = _parse_canonical_json_document(plaintext)
    if set(document) != {"http_status", "payload", "schema", "status"}:
        _response_unavailable()
    if document.get("schema") != _RESPONSE_SCHEMA:
        _response_unavailable()
    if document.get("status") != operation.status:
        _response_unavailable()
    if document.get("http_status") != operation.http_status:
        _response_unavailable()
    payload = document.get("payload")
    if not isinstance(payload, dict):
        _response_unavailable()
    return AuthenticationIdempotentResponse(
        operation_id=operation.id,
        status=operation.status,
        http_status=operation.http_status,
        payload=payload,
        replayed=replayed,
    )


def _validate_terminal_evidence(operation: AuthIdempotencyOperation) -> None:
    if operation.status not in FINAL_STATUSES:
        _response_unavailable()
    if not isinstance(operation.response_ciphertext, bytes):
        _response_unavailable()
    if len(operation.response_ciphertext) < _GCM_TAG_BYTES:
        _response_unavailable()
    if not isinstance(operation.response_nonce, bytes):
        _response_unavailable()
    if len(operation.response_nonce) != _GCM_NONCE_BYTES:
        _response_unavailable()
    if (
        not isinstance(operation.response_sha256, str)
        or not _LOWERCASE_SHA256.fullmatch(operation.response_sha256)
    ):
        _response_unavailable()
    if (
        not isinstance(operation.encryption_key_version, int)
        or isinstance(operation.encryption_key_version, bool)
        or operation.encryption_key_version <= 0
    ):
        _response_unavailable()
    if (
        not isinstance(operation.http_status, int)
        or isinstance(operation.http_status, bool)
        or operation.http_status < 200
        or operation.http_status > 599
    ):
        _response_unavailable()


def _operation_aad(operation: AuthIdempotencyOperation, *, key_version: int) -> bytes:
    return _canonical_json_bytes(
        "authentication idempotency AAD",
        {
            "client_type": operation.client_type,
            "encryption_key_version": key_version,
            "expires_at": _canonical_timestamp(operation.expires_at),
            "idempotency_key_hash": operation.idempotency_key_hash,
            "operation_id": str(operation.id),
            "operation_type": operation.operation_type,
            "request_hmac": operation.request_hmac,
            "schema": _AAD_SCHEMA,
            "scope_hash": operation.scope_hash,
        },
    )


def _lock_by_key_hash(
    db: Session, idempotency_key_hash: str
) -> AuthIdempotencyOperation | None:
    return db.scalar(
        select(AuthIdempotencyOperation)
        .where(
            AuthIdempotencyOperation.idempotency_key_hash == idempotency_key_hash
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _find_by_key_hash(
    db: Session, idempotency_key_hash: str
) -> AuthIdempotencyOperation | None:
    return db.scalar(
        select(AuthIdempotencyOperation).where(
            AuthIdempotencyOperation.idempotency_key_hash == idempotency_key_hash
        )
    )


def _ensure_outer_transaction(db: Session) -> None:
    """Make SQLite savepoint behavior match the caller-owned transaction.

    SQLAlchemy's SQLite autobegin can otherwise emit ``SAVEPOINT`` before the
    driver emits ``BEGIN``; releasing that first savepoint would persist the
    insert even if the caller later rolls back.  PostgreSQL begins the outer
    transaction normally and needs no special handling.
    """

    connection = db.connection()
    if connection.dialect.name != "sqlite":
        return
    driver_connection = connection.connection.driver_connection
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")


def _require_same_begin_identity(
    persisted: AuthIdempotencyOperation,
    supplied: AuthIdempotencyOperation,
) -> None:
    for field in (
        "operation_type",
        "client_type",
        "idempotency_key_hash",
        "scope_hash",
        "request_hmac",
    ):
        persisted_value = getattr(persisted, field)
        supplied_value = getattr(supplied, field)
        if not isinstance(persisted_value, str) or not isinstance(supplied_value, str):
            _response_unavailable()
        if not hmac.compare_digest(persisted_value, supplied_value):
            _idempotency_conflict()


def _merge_reference(
    operation: AuthIdempotencyOperation,
    field: str,
    proposed: str | uuid.UUID | None,
) -> None:
    current = getattr(operation, field)
    if current is not None and proposed is not None and current != proposed:
        _idempotency_conflict()
    if current is None and proposed is not None:
        setattr(operation, field, proposed)


def _require_operation_type(value: str) -> str:
    if value not in SUPPORTED_OPERATION_TYPES:
        _fail(
            "authentication_operation_type_invalid",
            "invalid_request",
            "认证操作类型无效",
        )
    return value


def _require_client_type(value: str) -> str:
    if value not in SUPPORTED_CLIENT_TYPES:
        _fail(
            "authentication_client_type_invalid",
            "invalid_request",
            "认证客户端类型无效",
        )
    return value


def _require_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not _VISIBLE_IDEMPOTENCY_KEY.fullmatch(value):
        _fail(
            "authentication_idempotency_key_invalid",
            "invalid_request",
            "Idempotency-Key 格式无效",
        )
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    elif isinstance(value, bytes):
        encoded = value
    else:
        encoded = b""
    if len(encoded) < _MIN_HMAC_SECRET_BYTES:
        _fail(
            "authentication_idempotency_hmac_secret_invalid",
            "service_unavailable",
            "认证幂等摘要服务不可用",
        )
    return encoded


def _canonical_json_bytes(field: str, value: Mapping[str, Any]) -> bytes:
    if not isinstance(value, Mapping):
        _fail(
            "authentication_idempotency_document_invalid",
            "invalid_request",
            f"{field} 必须是 JSON 对象",
        )
    _validate_json_tree(value, field=field, depth=0)
    try:
        serialized = json.dumps(
            dict(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(
            "authentication_idempotency_document_invalid",
            "invalid_request",
            f"{field} 不是规范 JSON",
            cause=exc,
        )
    if len(serialized) > _MAX_JSON_BYTES:
        _fail(
            "authentication_idempotency_document_too_large",
            "invalid_request",
            f"{field} 超出大小限制",
        )
    return serialized


def _validate_json_tree(value: Any, *, field: str, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        _fail(
            "authentication_idempotency_document_invalid",
            "invalid_request",
            f"{field} 不是规范 JSON",
        )
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        # json.dumps(..., allow_nan=False) performs the finite-value check.
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(
                    "authentication_idempotency_document_invalid",
                    "invalid_request",
                    f"{field} 不是规范 JSON",
                )
            _validate_json_tree(item, field=field, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_tree(item, field=field, depth=depth + 1)
        return
    _fail(
        "authentication_idempotency_document_invalid",
        "invalid_request",
        f"{field} 不是规范 JSON",
    )


def _parse_canonical_json_document(value: bytes) -> dict[str, Any]:
    duplicate = False

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate
        document: dict[str, Any] = {}
        for key, item in pairs:
            if key in document:
                duplicate = True
            document[key] = item
        return document

    try:
        decoded = value.decode("utf-8")
        document = json.loads(decoded, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _response_unavailable()
    if duplicate or not isinstance(document, dict):
        _response_unavailable()
    if _canonical_json_bytes("decrypted response", document) != value:
        _response_unavailable()
    return document


def _hmac_digest(secret: bytes, domain: bytes, document: bytes) -> str:
    digest = hmac.new(secret, domain + b"\x00" + document, hashlib.sha256).hexdigest()
    if not _LOWERCASE_SHA256.fullmatch(digest):  # pragma: no cover - stdlib invariant
        _fail(
            "authentication_idempotency_digest_failed",
            "service_unavailable",
            "认证幂等摘要服务不可用",
        )
    return digest


def _response_envelope_sha256(nonce: bytes, ciphertext: bytes) -> str:
    return hashlib.sha256(
        b"formal-auth-idempotency-response-envelope-v1\x00" + nonce + ciphertext
    ).hexdigest()


def _optional_auth_session_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(
            "authentication_session_reference_invalid",
            "invalid_request",
            "认证会话引用无效",
        )
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        _fail(
            "authentication_session_reference_invalid",
            "invalid_request",
            "认证会话引用无效",
        )
    if normalized != value.lower():
        _fail(
            "authentication_session_reference_invalid",
            "invalid_request",
            "认证会话引用无效",
        )
    return normalized


def _optional_uuid(field: str, value: uuid.UUID | str | None) -> uuid.UUID | None:
    if value is None:
        return None
    return _require_uuid(field, value)


def _require_uuid(field: str, value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            parsed = uuid.UUID(value)
        except ValueError:
            pass
        else:
            if str(parsed) == value.lower():
                return parsed
    _fail(
        "authentication_idempotency_reference_invalid",
        "invalid_request",
        f"{field} 无效",
    )


def _require_aware_datetime(field: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(
            "authentication_idempotency_time_invalid",
            "invalid_request",
            f"{field} 必须包含时区",
        )
    return value.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    # SQLite loses the timezone marker even for DateTime(timezone=True).
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _idempotency_conflict() -> None:
    _fail(
        "authentication_idempotency_key_conflict",
        "conflict",
        "Idempotency-Key 已用于不同认证请求",
    )


def _response_unavailable() -> None:
    _fail(
        "authentication_idempotency_response_unavailable",
        "service_unavailable",
        "认证幂等证据不可用，请重新登录",
    )


def _fail(
    code: str,
    category: str,
    public_message: str,
    *,
    cause: BaseException | None = None,
    error_type: type[AuthenticationIdempotencyError] = AuthenticationIdempotencyError,
) -> None:
    error = error_type(
        code=code,
        category=category,
        public_message=public_message,
    )
    if cause is None:
        raise error
    raise error from cause
