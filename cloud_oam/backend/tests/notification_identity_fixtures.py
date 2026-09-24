"""Explicit synthetic identity proof; never bypass the production resolver."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from app.foundation_models import AuthIdentity, Role, RoleAssignment
from app.models import WechatIdentity
from app.formal_services import notification_identities
from app.formal_services.authentication_identity import compute_identity_hash

POLICY=notification_identities.IdentityPolicy("synthetic-notification-identity-fixture-hmac-key",1,"notification-fixture","wx-notification-fixture")


def install_policy(monkeypatch):
    monkeypatch.setattr(notification_identities,"identity_policy",lambda:POLICY)


def verify_user_channels(db,user,now,*,include_sms=True):
    role=db.scalar(select(Role).where(Role.code=="technician"))
    if role is None:
        role=Role(id=uuid4(),code="technician",name="测试工程师",status="active",is_external=False)
        db.add(role);db.flush()
    assignment=db.scalar(select(RoleAssignment.id).where(RoleAssignment.user_id==user.id,RoleAssignment.role_id==role.id,
        RoleAssignment.scope_type=="person",RoleAssignment.scope_id==str(user.person_id),RoleAssignment.status=="active"))
    if assignment is None:
        db.add(RoleAssignment(id=uuid4(),user_id=user.id,role_id=role.id,scope_type="person",scope_id=str(user.person_id),
            valid_from=now-timedelta(days=1),status="active",assigned_by=user.id,reason="synthetic notification identity fixture"))
    coordinates=[("mobile",POLICY.sms_provider_key,user.mobile)] if include_sms else []
    wechat=db.scalar(select(WechatIdentity).where(WechatIdentity.user_id==user.id,WechatIdentity.app_id==POLICY.wechat_app_id))
    if wechat is not None:coordinates.append(("wechat_openid",POLICY.wechat_app_id,wechat.openid))
    for kind,provider,coordinate in coordinates:
        expected=compute_identity_hash(secret=POLICY.secret,hash_version=1,identity_type=kind,provider_key=provider,identifier=coordinate)
        existing=db.scalar(select(AuthIdentity).where(AuthIdentity.user_id==user.id,AuthIdentity.identity_type==kind,AuthIdentity.provider_key==provider))
        if existing is not None:
            assert existing.identifier_hash==expected
            continue
        db.add(AuthIdentity(id=uuid4(),user_id=user.id,identity_type=kind,provider_key=provider,identifier_hash=expected,
            hash_version=1,verified_at=now-timedelta(hours=1),status="active"))
    db.flush()


def prepare_pg16_notification_identities(fixture_engine):
    """Add test-app proofs to exact internal accounts on the protected PG16 only."""
    from datetime import datetime,timezone
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from app.models import User
    from app.foundation_models import Person,Organization
    with Session(fixture_engine) as db:
        assert db.scalar(text("SELECT current_user"))=="star_oam_migrator"
        assert db.scalar(text("SELECT current_database()"))=="rsc_pg16_release_gate"
        assert int(db.scalar(text("SHOW server_version_num")))//10000==16
        users=tuple(db.scalars(select(User).join(Person,Person.id==User.person_id).join(Organization,Organization.id==Person.organization_id)
            .where(User.is_active.is_(True),User.account_status=="active",Person.employment_status=="active",Organization.status=="active",
                Organization.org_type.in_(("headquarters","region_company","department"))).order_by(User.id)))
        assert users, "protected PG16 synthetic accounts missing"
        now=datetime.now(timezone.utc)
        for user in users:
            db.add(WechatIdentity(id=str(uuid4()),user_id=user.id,app_id=POLICY.wechat_app_id,
                openid=f"pg16-synthetic-openid-{uuid4().hex}",last_login_at=now))
            db.flush()
            verify_user_channels(db,user,now,include_sms=False)
        db.commit()
