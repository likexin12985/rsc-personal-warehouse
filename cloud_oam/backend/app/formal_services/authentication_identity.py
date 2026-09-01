"""Pure formal-identity hashing and exact authentication lookup.

This module is deliberately isolated from the v0.9 authentication tables.  It
does not create, bind, verify, revoke or otherwise mutate an identity, and it
never commits.  Callers must supply the secret and hash version explicitly so
that configuration ownership and key rotation stay outside the domain layer.

An identifier is always domain-separated by hash version, identity type and
provider key before HMAC-SHA256 is applied.  Lookup never falls back between
mobile, WeChat OpenID and WeChat UnionID, nor to any legacy user field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
import json
import re
import uuid
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..foundation_models import AuthIdentity
from ..models import User


IdentityType = Literal["mobile", "wechat_openid", "wechat_unionid"]

SUPPORTED_IDENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {"mobile", "wechat_openid", "wechat_unionid"}
)
MIN_SECRET_CHARACTERS: Final[int] = 32
MAX_PROVIDER_KEY_CHARACTERS: Final[int] = 100
MAX_WECHAT_IDENTIFIER_CHARACTERS: Final[int] = 512
MAX_HASH_VERSION: Final[int] = 2_147_483_647
PUBLIC_UNAVAILABLE_MESSAGE: Final[str] = "登录身份无效或账号不可用"

_MAINLAND_MOBILE = re.compile(r"^1[3-9][0-9]{9}$", re.ASCII)
_LOWERCASE_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class AuthenticationIdentityError(RuntimeError):
    """Base failure for the formal authentication-identity boundary."""

    code = "authentication_identity_error"


class AuthenticationIdentityValidationError(AuthenticationIdentityError, ValueError):
    """A caller supplied an invalid type, provider, version or secret."""

    code = "authentication_identity_invalid_input"


class AuthenticationIdentityUnavailable(AuthenticationIdentityError):
    """A credential cannot produce a safe formal principal.

    The error intentionally has one stable code and one stable public message
    for missing identities, broken user bindings and invalid formal access
    graphs.  No identifier, hash, user id or internal failure reason is stored
    on the exception.
    """

    code = "authentication_identity_unavailable"

    def __init__(self) -> None:
        super().__init__(PUBLIC_UNAVAILABLE_MESSAGE)


@dataclass(frozen=True, slots=True)
class ResolvedAuthenticationIdentity:
    """Non-secret result of an exact formal identity resolution.

    This object is an internal domain value and must not be serialized as an API
    response.  In particular, it carries neither the identifier nor its hash.
    """

    identity_id: uuid.UUID
    user_id: str
    person_id: uuid.UUID
    identity_type: IdentityType
    provider_key: str
    hash_version: int
    principal: FormalPrincipal = field(repr=False)


def normalize_mobile(value: str) -> str:
    """Return the canonical mainland-China mobile number.

    Only an eleven-digit number beginning with ``13`` through ``19`` is valid.
    Outer whitespace is normalized; country prefixes, punctuation, internal
    whitespace and non-ASCII digits are rejected rather than guessed.
    """

    if not isinstance(value, str):
        raise AuthenticationIdentityValidationError("手机号必须是字符串")
    normalized = value.strip()
    if not _MAINLAND_MOBILE.fullmatch(normalized):
        raise AuthenticationIdentityValidationError("手机号格式无效")
    return normalized


def compute_identity_hash(
    *,
    secret: str,
    hash_version: int,
    identity_type: str,
    provider_key: str,
    identifier: str,
) -> str:
    """Calculate a versioned, domain-separated HMAC-SHA256 identifier hash."""

    secret_bytes = _validated_secret(secret)
    checked_version = _validated_hash_version(hash_version)
    checked_type = _validated_identity_type(identity_type)
    checked_provider = _validated_provider_key(provider_key)
    checked_identifier = _normalized_identifier(checked_type, identifier)
    canonical = _canonical_identity_bytes(
        checked_version,
        checked_type,
        checked_provider,
        checked_identifier,
    )
    digest = hmac.new(secret_bytes, canonical, hashlib.sha256).hexdigest()
    if not _LOWERCASE_SHA256.fullmatch(digest):  # pragma: no cover - stdlib invariant
        raise AuthenticationIdentityError("身份摘要计算失败")
    return digest


def find_active_formal_identity(
    db: Session,
    *,
    secret: str,
    hash_version: int,
    identity_type: str,
    provider_key: str,
    identifier: str,
    now: datetime | None = None,
) -> ResolvedAuthenticationIdentity | None:
    """Return one exact active formal identity, or ``None`` when it is unknown.

    Pending, revoked, unverified and differently versioned/provider-scoped
    identities are indistinguishable from an unknown identifier.  A duplicate
    exact match or any broken user/formal-principal graph fails closed with the
    same non-disclosing :class:`AuthenticationIdentityUnavailable` error.
    """

    checked_version = _validated_hash_version(hash_version)
    checked_type = _validated_identity_type(identity_type)
    checked_provider = _validated_provider_key(provider_key)
    checked_identifier = _normalized_identifier(checked_type, identifier)
    identifier_hash = compute_identity_hash(
        secret=secret,
        hash_version=checked_version,
        identity_type=checked_type,
        provider_key=checked_provider,
        identifier=checked_identifier,
    )

    # ``no_autoflush`` ensures this read-only service cannot accidentally flush
    # unrelated pending mutations owned by its caller while resolving a login.
    with db.no_autoflush:
        identity_rows = db.execute(
            select(AuthIdentity.id, AuthIdentity.user_id)
            .where(
                AuthIdentity.identity_type == checked_type,
                AuthIdentity.provider_key == checked_provider,
                AuthIdentity.hash_version == checked_version,
                AuthIdentity.identifier_hash == identifier_hash,
                AuthIdentity.status == "active",
                AuthIdentity.verified_at.is_not(None),
                AuthIdentity.revoked_at.is_(None),
            )
            .limit(2)
        ).all()
        if not identity_rows:
            return None
        if len(identity_rows) != 1:
            raise AuthenticationIdentityUnavailable()

        identity_id, user_id = identity_rows[0]
        user_row = db.execute(
            select(User.id, User.person_id, User.account_status).where(User.id == user_id)
        ).one_or_none()
        if user_row is None:
            raise AuthenticationIdentityUnavailable()
        resolved_user_id, person_id, account_status = user_row
        if (
            not person_id
            or account_status not in {"active", "restricted_handover"}
        ):
            raise AuthenticationIdentityUnavailable()

        try:
            principal = load_formal_principal(db, resolved_user_id, now=now)
        except FormalAccessError:
            raise AuthenticationIdentityUnavailable() from None
        if (
            principal.user_id != resolved_user_id
            or principal.person_id != person_id
            or principal.account_status != account_status
        ):
            raise AuthenticationIdentityUnavailable()

    return ResolvedAuthenticationIdentity(
        identity_id=identity_id,
        user_id=resolved_user_id,
        person_id=person_id,
        identity_type=checked_type,
        provider_key=checked_provider,
        hash_version=checked_version,
        principal=principal,
    )


def resolve_active_formal_identity(
    db: Session,
    *,
    secret: str,
    hash_version: int,
    identity_type: str,
    provider_key: str,
    identifier: str,
    now: datetime | None = None,
) -> ResolvedAuthenticationIdentity:
    """Require one exact formal identity using a non-enumerating failure."""

    resolved = find_active_formal_identity(
        db,
        secret=secret,
        hash_version=hash_version,
        identity_type=identity_type,
        provider_key=provider_key,
        identifier=identifier,
        now=now,
    )
    if resolved is None:
        raise AuthenticationIdentityUnavailable()
    return resolved


def _validated_secret(secret: str) -> bytes:
    if not isinstance(secret, str):
        raise AuthenticationIdentityValidationError("身份哈希密钥必须是字符串")
    if len(secret) < MIN_SECRET_CHARACTERS or not secret.strip():
        raise AuthenticationIdentityValidationError(
            f"身份哈希密钥至少需要{MIN_SECRET_CHARACTERS}个字符"
        )
    return secret.encode("utf-8")


def _validated_hash_version(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AuthenticationIdentityValidationError("身份哈希版本必须是正整数")
    if value <= 0 or value > MAX_HASH_VERSION:
        raise AuthenticationIdentityValidationError("身份哈希版本必须是正整数")
    return value


def _validated_identity_type(value: str) -> IdentityType:
    if not isinstance(value, str) or value not in SUPPORTED_IDENTITY_TYPES:
        raise AuthenticationIdentityValidationError("登录身份类型不受支持")
    return value  # type: ignore[return-value]


def _validated_provider_key(value: str) -> str:
    if not isinstance(value, str):
        raise AuthenticationIdentityValidationError("身份提供方标识必须是字符串")
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_PROVIDER_KEY_CHARACTERS
        or _contains_control_character(value)
    ):
        raise AuthenticationIdentityValidationError("身份提供方标识无效")
    return value


def _normalized_identifier(identity_type: IdentityType, value: str) -> str:
    if identity_type == "mobile":
        return normalize_mobile(value)
    if not isinstance(value, str):
        raise AuthenticationIdentityValidationError("微信身份标识必须是字符串")
    if (
        not value
        or value != value.strip()
        or len(value) > MAX_WECHAT_IDENTIFIER_CHARACTERS
        or _contains_control_character(value)
    ):
        raise AuthenticationIdentityValidationError("微信身份标识无效")
    if identity_type == "wechat_openid" and _MAINLAND_MOBILE.fullmatch(value):
        raise AuthenticationIdentityValidationError("微信OpenID不能使用手机号替代")
    return value


def _canonical_identity_bytes(
    hash_version: int,
    identity_type: IdentityType,
    provider_key: str,
    identifier: str,
) -> bytes:
    # JSON array order is fixed to the reviewed tuple and prevents delimiter
    # injection without storing or logging any of its plaintext components.
    return json.dumps(
        [hash_version, identity_type, provider_key, identifier],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


__all__ = [
    "AuthenticationIdentityError",
    "AuthenticationIdentityUnavailable",
    "AuthenticationIdentityValidationError",
    "IdentityType",
    "ResolvedAuthenticationIdentity",
    "SUPPORTED_IDENTITY_TYPES",
    "compute_identity_hash",
    "find_active_formal_identity",
    "normalize_mobile",
    "resolve_active_formal_identity",
]
