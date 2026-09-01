from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from ipaddress import ip_address as parse_ip_address
import json
import re
import secrets

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .formal_access import FormalAccessError, load_formal_principal
from .foundation_models import AuthRefreshToken
from .models import AuthSession, User
from .security import create_access_token, create_refresh_token, hash_refresh_token


settings = get_settings()
_MAX_HASH_VERSION = 2_147_483_647
_PROTECTED_IP = re.compile(
    r"^hmac:([1-9][0-9]{0,9}):([0-9a-f]{64})$",
    re.ASCII,
)


class SessionError(RuntimeError):
    pass


class RefreshTokenReplayError(SessionError):
    """A consumed or revoked refresh token was presented again.

    The service has already marked the entire session family revoked in the
    caller's transaction when this error is raised.  The caller must commit
    that transaction before returning the authentication failure; rolling it
    back would discard the replay response.
    """

    def __init__(self, session_id: str, *, previous_status: str):
        super().__init__("检测到刷新令牌重复使用，会话已撤销")
        self.session_id = session_id
        self.previous_status = previous_status
        self.state_changed = previous_status != "revoked"


@dataclass(frozen=True)
class SessionTokens:
    auth_session: AuthSession
    access_token: str
    refresh_token: str
    access_expires_in: int
    refresh_expires_in: int
    replaced_sessions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SessionRevocationResult:
    auth_session: AuthSession
    previous_status: str
    state_changed: bool


def create_session(
    db: Session,
    user: User,
    *,
    client_type: str,
    device_id: str = "",
    device_name: str = "",
    ip_address: str = "",
    user_agent: str = "",
) -> SessionTokens:
    # Validate and protect the address before loading the formal principal,
    # updating last_login_at, revoking a same-device family, or issuing a
    # refresh token.  A broken production hash configuration must therefore
    # leave the caller's authentication transaction untouched.
    protected_ip_address = protect_session_ip_address(ip_address)
    now = datetime.now(timezone.utc)
    if settings.environment == "production":
        _require_formal_principal(db, user, now)

    stable_device_id = device_id.strip() or secrets.token_urlsafe(18)
    _acquire_device_session_lock(
        db,
        user_id=user.id,
        client_type=client_type,
        device_id=stable_device_id,
    )
    replaced_sessions: list[tuple[str, str]] = []
    for existing in db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.client_type == client_type,
            AuthSession.device_id == stable_device_id,
            AuthSession.revoked_at.is_(None),
        ).with_for_update()
    ):
        if settings.environment == "production" and _is_active(existing, now):
            require_current_session_ip_evidence(existing)
        previous_status = _session_status(existing, now)
        _revoke_session_family(db, existing, now=now)
        replaced_sessions.append((existing.id, previous_status))

    if settings.environment == "production":
        user.last_login_at = now

    refresh_token = create_refresh_token()
    refresh_token_hash = hash_refresh_token(refresh_token)
    auth_session = AuthSession(
        user_id=user.id,
        # Retained as a compatibility projection.  AuthRefreshToken is the
        # production source of truth for issuance, rotation and replay.
        refresh_token_hash=refresh_token_hash,
        client_type=client_type,
        device_id=stable_device_id,
        device_name=(device_name.strip() or _default_device_name(client_type))[:160],
        ip_address=protected_ip_address,
        user_agent=user_agent[:500],
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.session_ttl_days),
    )
    db.add(auth_session)
    db.flush()
    db.add(
        AuthRefreshToken(
            session_id=auth_session.id,
            token_hash=refresh_token_hash,
            issued_at=now,
        )
    )
    db.flush()
    return _tokens(
        auth_session,
        refresh_token,
        now,
        replaced_sessions=tuple(replaced_sessions),
    )


def _acquire_device_session_lock(
    db: Session,
    *,
    user_id: str,
    client_type: str,
    device_id: str,
) -> None:
    """Serialize one PostgreSQL device family even when no row exists yet.

    ``SELECT .. FOR UPDATE`` cannot lock an absent row.  The transaction-scoped
    advisory lock closes that first-insert race; the database partial unique
    index remains the invariant backstop.  SQLite is used only for local tests
    and relies on the unique index rather than pretending to reproduce PG lock
    semantics.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    document = "\x00".join(
        (
            "formal-auth-device-session-v1",
            user_id,
            client_type,
            device_id,
        )
    ).encode("utf-8")
    lock_key = int.from_bytes(
        hashlib.sha256(document).digest()[:8],
        byteorder="big",
        signed=True,
    )
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


def rotate_session(
    db: Session,
    refresh_token: str,
    *,
    device_id: str | None = None,
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[User, SessionTokens]:
    # Do this before token lookup/locking so configuration or address failures
    # cannot consume a refresh token or mutate its session family.
    if not isinstance(ip_address, str):
        raise SessionError("会话来源地址无效")
    protected_ip_address = (
        protect_session_ip_address(ip_address)
        if settings.environment == "production" or ip_address.strip()
        else ""
    )
    now = datetime.now(timezone.utc)
    presented_hash = hash_refresh_token(refresh_token)
    refresh_history = db.scalar(
        select(AuthRefreshToken).where(AuthRefreshToken.token_hash == presented_hash)
    )
    if refresh_history is not None:
        return _rotate_with_history(
            db,
            refresh_history,
            now=now,
            device_id=device_id,
            ip_address=protected_ip_address,
            user_agent=user_agent,
        )

    if settings.environment == "production":
        # Never fall back to the v0.9 session hash in production.  A session
        # created before formal token history was activated must authenticate
        # again through a reviewed passwordless identity flow.
        raise SessionError("登录已失效")

    # Non-production compatibility for sessions created before the formal
    # history table was activated.  A successful rotation upgrades the session
    # by recording the newly issued token in AuthRefreshToken.
    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == presented_hash)
        .with_for_update()
    )
    if not auth_session or not _is_active(auth_session, now):
        raise SessionError("登录已失效")
    if device_id is not None and auth_session.device_id != device_id.strip():
        raise SessionError("登录设备不匹配")
    user = db.get(User, auth_session.user_id)
    if not user:
        raise SessionError("账号不可用")
    if settings.environment == "production":
        _require_formal_principal(db, user, now)
    elif not user.is_active:
        raise SessionError("账号不可用")

    next_refresh_token = create_refresh_token()
    next_refresh_hash = hash_refresh_token(next_refresh_token)
    auth_session.refresh_token_hash = next_refresh_hash
    auth_session.last_seen_at = now
    if protected_ip_address:
        auth_session.ip_address = protected_ip_address
    if user_agent:
        auth_session.user_agent = user_agent[:500]
    db.add(
        AuthRefreshToken(
            session_id=auth_session.id,
            token_hash=next_refresh_hash,
            issued_at=now,
        )
    )
    db.flush()
    return user, _tokens(auth_session, next_refresh_token, now)


def revoke_by_refresh_token(
    db: Session,
    refresh_token: str,
    *,
    revoked_by_id: str | None = None,
) -> AuthSession | None:
    result = revoke_by_refresh_token_with_result(
        db,
        refresh_token,
        revoked_by_id=revoked_by_id,
    )
    return result.auth_session if result is not None else None


def revoke_by_refresh_token_with_result(
    db: Session,
    refresh_token: str,
    *,
    revoked_by_id: str | None = None,
) -> SessionRevocationResult | None:
    now = datetime.now(timezone.utc)
    presented_hash = hash_refresh_token(refresh_token)
    refresh_history = db.scalar(
        select(AuthRefreshToken).where(AuthRefreshToken.token_hash == presented_hash)
    )
    if refresh_history is not None:
        auth_session = db.scalar(
            select(AuthSession)
            .where(AuthSession.id == refresh_history.session_id)
            .with_for_update()
        )
        if auth_session is None:
            return None
        # Lock ordering is session -> token everywhere.  Re-read the token only
        # after the session lock so a concurrent rotation/revocation cannot use
        # the opposite order and deadlock a same-device login.
        refresh_history = db.scalar(
            select(AuthRefreshToken)
            .where(AuthRefreshToken.id == refresh_history.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if refresh_history is None:
            return None
        previous_status = _session_status(auth_session, now)
        _revoke_session_family(
            db,
            auth_session,
            now=now,
            revoked_by_id=revoked_by_id,
        )
        db.flush()
        return SessionRevocationResult(
            auth_session=auth_session,
            previous_status=previous_status,
            state_changed=previous_status != "revoked",
        )

    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == presented_hash)
        .with_for_update()
    )
    if not auth_session:
        return None
    if settings.environment == "production":
        raise SessionError("登录已失效")
    previous_status = _session_status(auth_session, now)
    if auth_session.revoked_at is None:
        auth_session.revoked_at = now
        auth_session.revoked_by_id = revoked_by_id
    return SessionRevocationResult(
        auth_session=auth_session,
        previous_status=previous_status,
        state_changed=previous_status != "revoked",
    )


def revoke_session(
    auth_session: AuthSession,
    *,
    revoked_by_id: str,
) -> None:
    """Legacy compatibility helper that marks only the session row revoked.

    Formal administrator revocation must use ``revoke_session_family`` so all
    outstanding refresh tokens are terminated in the same transaction.
    """

    if auth_session.revoked_at is None:
        auth_session.revoked_at = datetime.now(timezone.utc)
        auth_session.revoked_by_id = revoked_by_id


def revoke_session_family(
    db: Session,
    auth_session: AuthSession,
    *,
    revoked_by_id: str | None = None,
) -> AuthSession:
    """Revoke one session and every unfinished formal refresh token.

    The caller owns commit/rollback.  The row is reloaded under a lock so an
    administrator force-revoke cannot race a refresh rotation and leave the new
    replacement token active.
    """

    locked_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == auth_session.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_session is None:
        raise SessionError("登录设备不存在")
    _revoke_session_family(
        db,
        locked_session,
        now=datetime.now(timezone.utc),
        revoked_by_id=revoked_by_id,
    )
    db.flush()
    return locked_session


def session_is_active(auth_session: AuthSession, now: datetime | None = None) -> bool:
    return _is_active(auth_session, now or datetime.now(timezone.utc))


def _tokens(
    auth_session: AuthSession,
    refresh_token: str,
    now: datetime,
    *,
    replaced_sessions: tuple[tuple[str, str], ...] = (),
) -> SessionTokens:
    refresh_expires_in = max(0, int((_as_utc(auth_session.expires_at) - now).total_seconds()))
    return SessionTokens(
        auth_session=auth_session,
        access_token=create_access_token(auth_session.user_id, auth_session.id),
        refresh_token=refresh_token,
        access_expires_in=settings.jwt_ttl_minutes * 60,
        refresh_expires_in=refresh_expires_in,
        replaced_sessions=replaced_sessions,
    )


def _is_active(auth_session: AuthSession, now: datetime) -> bool:
    return auth_session.revoked_at is None and _as_utc(auth_session.expires_at) > now


def _session_status(auth_session: AuthSession, now: datetime) -> str:
    if auth_session.revoked_at is not None:
        return "revoked"
    if _as_utc(auth_session.expires_at) <= now:
        return "expired"
    return "active"


def _rotate_with_history(
    db: Session,
    refresh_history: AuthRefreshToken,
    *,
    now: datetime,
    device_id: str | None,
    ip_address: str,
    user_agent: str,
) -> tuple[User, SessionTokens]:
    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == refresh_history.session_id)
        .with_for_update()
    )
    if auth_session is None:
        raise SessionError("登录已失效")

    # The initial hash lookup deliberately did not lock.  All lifecycle paths
    # lock the session first and the token second to avoid a token/session lock
    # inversion with same-device replacement and family revocation.
    refresh_history = db.scalar(
        select(AuthRefreshToken)
        .where(AuthRefreshToken.id == refresh_history.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if refresh_history is None:
        raise SessionError("登录已失效")

    if refresh_history.consumed_at is not None or refresh_history.revoked_at is not None:
        if settings.environment == "production" and _is_active(auth_session, now):
            require_current_session_ip_evidence(auth_session)
        previous_status = _session_status(auth_session, now)
        _revoke_session_family(db, auth_session, now=now)
        db.flush()
        raise RefreshTokenReplayError(
            auth_session.id,
            previous_status=previous_status,
        )

    if not _is_active(auth_session, now):
        raise SessionError("登录已失效")
    if settings.environment == "production":
        require_current_session_ip_evidence(auth_session)
    if device_id is not None and auth_session.device_id != device_id.strip():
        raise SessionError("登录设备不匹配")

    user = db.get(User, auth_session.user_id)
    if not user:
        raise SessionError("账号不可用")
    if settings.environment == "production":
        _require_formal_principal(db, user, now)
    elif not user.is_active:
        raise SessionError("账号不可用")

    next_refresh_token = create_refresh_token()
    next_refresh_hash = hash_refresh_token(next_refresh_token)
    next_history = AuthRefreshToken(
        session_id=auth_session.id,
        token_hash=next_refresh_hash,
        issued_at=now,
    )
    refresh_history.consumed_at = now
    db.add(next_history)
    db.flush()
    refresh_history.replaced_by_id = next_history.id

    # Keep the v0.9 column synchronized only as a compatibility projection.
    auth_session.refresh_token_hash = next_refresh_hash
    auth_session.last_seen_at = now
    if ip_address:
        # ``rotate_session`` already validated and protected this value before
        # looking up or locking the presented refresh token.
        auth_session.ip_address = ip_address
    if user_agent:
        auth_session.user_agent = user_agent[:500]
    db.flush()
    return user, _tokens(auth_session, next_refresh_token, now)


def _revoke_session_family(
    db: Session,
    auth_session: AuthSession,
    *,
    now: datetime,
    revoked_by_id: str | None = None,
) -> None:
    if auth_session.revoked_at is None:
        auth_session.revoked_at = now
    if revoked_by_id is not None and auth_session.revoked_by_id is None:
        auth_session.revoked_by_id = revoked_by_id

    unfinished_tokens = db.scalars(
        select(AuthRefreshToken)
        .where(
            AuthRefreshToken.session_id == auth_session.id,
            AuthRefreshToken.consumed_at.is_(None),
            AuthRefreshToken.revoked_at.is_(None),
        )
        .with_for_update()
    )
    for token in unfinished_tokens:
        token.revoked_at = now


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _default_device_name(client_type: str) -> str:
    return "微信小程序" if client_type == "miniprogram" else "PC网页"


def protect_session_ip_address(value: str) -> str:
    """Return the persisted session-address evidence for the active runtime.

    Development and test retain the v0.9 plaintext projection so prototype
    callers and fixtures remain compatible.  Production accepts only a real IP
    address and persists a versioned, domain-separated HMAC.  The hash version
    is both encoded in the canonical HMAC document and carried in the bounded
    80-character storage value; this makes key/version rotation explicit
    without a schema change.  Pre-hashed caller input is rejected because its
    provenance and key version cannot be established at this trust boundary.
    """

    if not isinstance(value, str):
        raise SessionError("会话来源地址无效")
    normalized = value.strip()
    if settings.environment != "production":
        return normalized[:80]
    hash_version, digest = _production_ip_hmac(
        normalized,
        domain="auth_session_ip",
    )
    protected = f"hmac:{hash_version}:{digest}"
    if len(protected) > 80:  # pragma: no cover - guarded by version bound
        raise SessionError("会话来源证据配置无效")
    return protected


def hash_login_challenge_ip_address(value: str) -> str:
    """Return a challenge-only IP HMAC that cannot correlate session rows."""

    _, digest = _production_ip_hmac(
        value,
        domain="login_challenge_ip",
    )
    return digest


def _production_ip_hmac(value: str, *, domain: str) -> tuple[int, str]:
    if not isinstance(value, str) or not value.strip():
        raise SessionError("会话来源地址无效")
    try:
        canonical_ip = parse_ip_address(value.strip()).compressed
    except ValueError:
        raise SessionError("会话来源地址无效") from None
    hash_version = _validated_hash_version(settings.identity_hash_version)
    secret = settings.identity_hash_secret
    if not isinstance(secret, str) or len(secret) < 32 or not secret.strip():
        raise SessionError("会话来源证据配置无效")
    canonical = json.dumps(
        [hash_version, domain, canonical_ip],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return hash_version, digest


def require_current_session_ip_evidence(
    auth_session: AuthSession,
    *,
    hash_version: int | None = None,
) -> None:
    """Reject an active formal session whose address evidence is not current.

    This check is read-only.  It never rewrites, clears, hashes, revokes or
    otherwise guesses at legacy values.  Callers deliberately apply it only to
    unrevoked, unexpired sessions that could still cross a formal authentication
    boundary; revoked and expired history remains preserved as-is.
    """

    current_version = _validated_hash_version(
        settings.identity_hash_version if hash_version is None else hash_version
    )
    value = auth_session.ip_address
    match = _PROTECTED_IP.fullmatch(value) if isinstance(value, str) else None
    if match is None or int(match.group(1)) != current_version:
        raise SessionError("登录已失效")


def validate_active_production_session_ip_evidence(
    db: Session,
    *,
    hash_version: int,
    now: datetime | None = None,
) -> None:
    """Read-only startup preflight for sessions the formal API could accept."""

    effective_at = now or datetime.now(timezone.utc)
    current_version = _validated_hash_version(hash_version)
    with db.no_autoflush:
        active_sessions = db.scalars(
            select(AuthSession).where(
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > effective_at,
            )
        )
        for auth_session in active_sessions:
            try:
                require_current_session_ip_evidence(
                    auth_session,
                    hash_version=current_version,
                )
            except SessionError:
                raise SessionError(
                    "存在不兼容的活跃正式会话证据"
                ) from None


def _validated_hash_version(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_HASH_VERSION
    ):
        raise SessionError("会话来源证据配置无效")
    return value


def _require_formal_principal(db: Session, user: User, now: datetime) -> None:
    """Fail closed before any production session token is created or rotated.

    The public error deliberately does not distinguish an unbound person, a
    revoked identity, or an expired/revoked role assignment.  The formal
    authorization graph remains the source of truth and is reloaded for every
    refresh, so authorization changes take effect without waiting for the
    existing refresh token to expire.

    A ``restricted_handover`` principal remains valid here by design: V1.0
    requires inactive/left personnel to retain access to the handover surface.
    Resource authorization, not token issuance, confines that session.
    """

    try:
        load_formal_principal(db, user.id, now=now)
    except FormalAccessError:
        raise SessionError("账号当前不可建立正式会话") from None
