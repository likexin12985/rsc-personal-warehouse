from datetime import datetime, timezone
from ipaddress import ip_address

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from .authorization import is_formal_role
from .auth_sessions import SessionError, require_current_session_ip_evidence
from .config import get_settings
from .database import get_db
from .formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from .models import AuthSession, User
from .security import decode_access_token


SELF_SERVICE_PATHS = frozenset({"/api/auth/me", "/api/access/context"})


def enforce_legacy_route_scope(user: User, path: str) -> None:
    """Fail closed before the formal RBAC context is evaluated.

    This compatibility gate prevents an external approval identity, retired
    role, or unscoped regional account from falling through legacy query code
    that historically interpreted an empty province as nationwide access.
    """

    if path in SELF_SERVICE_PATHS:
        return
    if not is_formal_role(user.role):
        raise HTTPException(status_code=403, detail="账号角色已停用")
    if user.role == "star_headquarters_approver":
        raise HTTPException(status_code=403, detail="外部审批身份仅允许访问受限审批页面")
    if user.role in {"provincial_manager", "technician"} and not (
        user.province or ""
    ).strip():
        raise HTTPException(status_code=403, detail="账号缺少有效数据范围")


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> User:
    bearer_token = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            bearer_token = value.strip()
    token = bearer_token or access_token
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    try:
        user_id, session_id = decode_access_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效"
        ) from exc
    auth_session = db.get(AuthSession, session_id)
    if (
        not auth_session
        or auth_session.user_id != user_id
        or auth_session.revoked_at is not None
        or _as_utc(auth_session.expires_at) <= datetime.now(timezone.utc)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已失效")
    runtime_settings = get_settings()
    if runtime_settings.environment == "production":
        try:
            require_current_session_ip_evidence(
                auth_session,
                hash_version=runtime_settings.identity_hash_version,
            )
        except SessionError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录已失效",
            ) from None
    request.state.auth_session_id = auth_session.id
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    if runtime_settings.environment == "production":
        try:
            request.state.formal_principal = load_formal_principal(db, user.id)
        except FormalAccessError:
            # Do not disclose whether the failed edge was the person binding,
            # login identity, account state, role assignment, or scope graph.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号当前没有有效的正式访问权限",
            ) from None
    else:
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="账号不可用",
            )
        enforce_legacy_route_scope(user, request.url.path)
    return user


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def require_roles(*roles: str):
    def dependency(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        principal = getattr(request.state, "formal_principal", None)
        effective_roles = (
            set(principal.role_codes)
            if isinstance(principal, FormalPrincipal)
            else {user.role}
        )
        if not effective_roles.intersection(roles):
            raise HTTPException(status_code=403, detail="没有此操作权限")
        return user

    return dependency


def get_formal_principal(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FormalPrincipal:
    principal = getattr(request.state, "formal_principal", None)
    if isinstance(principal, FormalPrincipal):
        return principal
    try:
        return load_formal_principal(db, user.id)
    except FormalAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_permission(resource: str, action: str, *, field_code: str = ""):
    def dependency(
        principal: FormalPrincipal = Depends(get_formal_principal),
        db: Session = Depends(get_db),
    ) -> FormalPrincipal:
        if not principal.allows(
            db,
            resource,
            action,
            field_code=field_code,
        ):
            raise HTTPException(status_code=403, detail="没有此操作权限")
        return principal

    return dependency


def client_ip(request: Request) -> str:
    peer_host = request.client.host.strip() if request.client else ""
    if not peer_host:
        return ""

    try:
        normalized_peer = ip_address(peer_host).compressed
    except ValueError:
        # A non-IP ASGI peer cannot match the explicit proxy IP allowlist.
        return peer_host

    if normalized_peer not in get_settings().trusted_proxy_ip_set():
        return peer_host

    forwarded_first = request.headers.get("x-forwarded-for", "").split(",", 1)[0]
    try:
        return ip_address(forwarded_first.strip()).compressed
    except ValueError:
        # Missing or malformed proxy metadata must fail closed to the real peer.
        return peer_host
