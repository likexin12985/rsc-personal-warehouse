"""Provider-scoped, read-only channel resolution for notification creation.

Legacy coordinates are candidates only. A current, exact formal identity must
prove each one. Missing configuration or identity leaves the original person
target unresolved; this module never binds accounts, sends, flushes or commits.
Sending still needs an independently verified current provider/identity boundary.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..foundation_models import AuthIdentity, Organization, Person, Permission, Role, RoleAssignment, RolePermission
from ..models import User, WechatIdentity
from .authentication_identity import AuthenticationIdentityError, compute_identity_hash, find_active_formal_identity, normalize_mobile


@dataclass(frozen=True)
class IdentityPolicy:
    secret: str = field(repr=False)
    hash_version: int
    sms_provider_key: str | None
    wechat_app_id: str | None


def identity_policy() -> IdentityPolicy:
    settings=get_settings()
    return IdentityPolicy(settings.identity_hash_secret,settings.identity_hash_version,
        settings.sms_provider if settings.sms_provider=="aliyun_pnvs" else None,
        settings.wechat_app_id if settings.wechat_provider=="wechat" and settings.wechat_app_id else None)


def policy_available(policy: IdentityPolicy) -> bool:
    return (isinstance(policy,IdentityPolicy) and isinstance(policy.secret,str) and len(policy.secret)>=32
        and bool(policy.secret.strip()) and type(policy.hash_version) is int and 0<policy.hash_version<=2147483647
        and bool(policy.sms_provider_key or policy.wechat_app_id))


@dataclass(frozen=True)
class VerifiedNotificationChannel:
    channel: str
    user_id: str
    identity_id: UUID
    provider_key: str
    hash_version: int
    verified_at: datetime
    identifier_hash: str = field(repr=False)
    recipient_key: str = field(repr=False)


_GRAPH_TYPES=(User,Person,Organization,AuthIdentity,WechatIdentity,RoleAssignment,Role,RolePermission,Permission)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def resolve_verified_channels(db: Session, *, person_id: UUID, policy: IdentityPolicy,
    now: datetime) -> tuple[VerifiedNotificationChannel, ...]:
    if not isinstance(person_id,UUID) or not policy_available(policy):return ()
    with db.no_autoflush:
        # Formal-principal loading refreshes ORM rows. A notification must not
        # overwrite or prematurely persist the caller's staged identity graph.
        # Defer resolution until those changes have an authoritative checkpoint.
        if any(isinstance(row,_GRAPH_TYPES) for row in (*db.new,*db.dirty,*db.deleted)):
            return ()
        users=tuple(db.scalars(select(User).join(Person,Person.id==User.person_id)
            .where(User.person_id==person_id,User.account_status=="active",User.is_active.is_(True),
                Person.employment_status=="active").limit(2).execution_options(populate_existing=True)))
        if len(users)!=1:return ()
        user=users[0]
        coordinates=[]
        if policy.sms_provider_key:
            try:coordinates.append(("sms","mobile",policy.sms_provider_key,normalize_mobile(user.mobile)))
            except AuthenticationIdentityError:pass
        if policy.wechat_app_id:
            identities=tuple(db.scalars(select(WechatIdentity).where(WechatIdentity.user_id==user.id,
                WechatIdentity.app_id==policy.wechat_app_id).limit(2).execution_options(populate_existing=True)))
            if len(identities)==1:coordinates.append(("wechat","wechat_openid",policy.wechat_app_id,identities[0].openid))
        result=[]
        for channel,identity_type,provider,coordinate in coordinates:
            if not isinstance(coordinate,str) or not 1<=len(coordinate.strip())<=200:continue
            try:
                expected_hash=compute_identity_hash(secret=policy.secret,hash_version=policy.hash_version,
                    identity_type=identity_type,provider_key=provider,identifier=coordinate)
                resolved=find_active_formal_identity(db,secret=policy.secret,hash_version=policy.hash_version,
                    identity_type=identity_type,provider_key=provider,identifier=coordinate,now=now)
            except AuthenticationIdentityError:continue
            if (resolved is None or resolved.user_id!=user.id or resolved.person_id!=person_id
                or resolved.principal.access_mode!="active"):continue
            proof=db.get(AuthIdentity,resolved.identity_id,populate_existing=True)
            if (proof is None or proof.user_id!=user.id or proof.identity_type!=identity_type
                or proof.provider_key!=provider or proof.hash_version!=policy.hash_version
                or proof.identifier_hash!=expected_hash or proof.status!="active" or proof.revoked_at is not None
                or proof.verified_at is None or _utc(proof.verified_at)>_utc(now)):continue
            result.append(VerifiedNotificationChannel(channel,user.id,proof.id,provider,policy.hash_version,
                _utc(proof.verified_at),proof.identifier_hash,coordinate.strip()))
        return tuple(result)
