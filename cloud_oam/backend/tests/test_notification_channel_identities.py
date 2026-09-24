"""Legacy coordinates cannot create recipients without exact formal proof."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.foundation_models import AuthIdentity, NotificationEvent, NotificationPersonTarget, NotificationRecipient, OutboxEvent, Organization, Person
from app.models import User, WechatIdentity
from app.formal_services import notification_identities as identities
from app.formal_services.notification_events import record_business_notification
from notification_identity_fixtures import POLICY, install_policy, verify_user_channels
from test_notification_events import _db, NOW


@pytest.fixture
def subject(monkeypatch):
    install_policy(monkeypatch)
    db=_db()
    org=Organization(id=uuid4(),code=f"CH-{uuid4().hex}",name="身份测试组织",org_type="region_company")
    db.add(org);db.flush()
    person=Person(id=uuid4(),organization_id=org.id,employee_no="CHANNEL",name="原目标",employment_status="active")
    db.add(person);db.flush()
    user=User(id=str(uuid4()),person_id=person.id,account_status="active",mobile="13900000008",name="原目标",
        password_hash="unused",role="technician",is_active=True)
    db.add(user);db.flush()
    wechat=WechatIdentity(id=str(uuid4()),user_id=user.id,app_id=POLICY.wechat_app_id,openid="original-scoped-openid",last_login_at=NOW)
    db.add(wechat);db.commit()
    yield SimpleNamespace(db=db,org=org,person=person,person_id=person.id,user=user,wechat=wechat)
    engine=db.get_bind();db.close();engine.dispose()


def record(subject,**changes):
    business_id=uuid4()
    arguments=dict(event_type="test",business_type="test",business_id=business_id,dedup_key=f"channels:{business_id}",
        payload={},recipient_person_id=subject.person_id,occurred_at=NOW,now=NOW)
    return record_business_notification(subject.db,**dict(arguments,**changes))


def verify(subject):
    verify_user_channels(subject.db,subject.user,NOW);subject.db.commit()


def mobile_identity(subject):
    return subject.db.scalars(select(AuthIdentity).where(AuthIdentity.user_id==subject.user.id,AuthIdentity.identity_type=="mobile")).one()


def test_unverified_legacy_coordinates_retain_original_target_without_recipients(subject):
    first=record(subject);subject.db.commit()
    assert first.recipient_count==0
    assert subject.db.scalars(select(NotificationPersonTarget.person_id)).one()==subject.person.id
    assert not tuple(subject.db.scalars(select(NotificationRecipient)))
    verify(subject)
    replay=record_business_notification(subject.db,event_type=first.event.event_type,
        business_type=first.event.business_type,business_id=UUID(first.event.business_id),dedup_key=first.event.dedup_key,
        payload={},recipient_person_id=subject.person.id,occurred_at=NOW,now=NOW)
    assert replay.event.id==first.event.id and replay.recipient_count==0
    assert record(subject).recipient_count==2  # Only a new fact resolves current identity.


def test_initial_creation_uses_configured_app_even_when_another_app_logged_in_later(subject):
    verify(subject)
    subject.db.add(WechatIdentity(id=str(uuid4()),user_id=subject.user.id,app_id="unrelated-app",openid="unrelated-openid",last_login_at=NOW+timedelta(days=1)))
    subject.db.commit()
    result=record(subject);subject.db.commit()
    assert result.recipient_count==2
    assert {(r.channel,r.recipient_key) for r in subject.db.scalars(select(NotificationRecipient))}=={
        ("sms",subject.user.mobile),("wechat",subject.wechat.openid)}


@pytest.mark.parametrize("change",["hash","provider","version","revoked","pending","future","user_inactive","person_inactive","account_suspended","missing_person","changed_mobile","missing_role"])
def test_invalid_or_unverified_mobile_never_becomes_a_recipient(subject,monkeypatch,change):
    verify(subject)
    monkeypatch.setattr(identities,"identity_policy",lambda:replace(POLICY,wechat_app_id=None))
    proof=mobile_identity(subject)
    if change=="hash":proof.identifier_hash="f"*64
    elif change=="provider":proof.provider_key="unrelated-provider"
    elif change=="version":proof.hash_version=2
    elif change=="revoked":proof.status="revoked";proof.revoked_at=NOW
    elif change=="pending":proof.status="pending";proof.verified_at=None
    elif change=="future":proof.verified_at=NOW+timedelta(days=1)
    elif change=="user_inactive":subject.user.is_active=False
    elif change=="person_inactive":subject.person.employment_status="inactive"
    elif change=="account_suspended":subject.user.account_status="suspended"
    elif change=="missing_person":subject.user.person_id=None
    elif change=="changed_mobile":subject.user.mobile="13900000009"
    else:
        from app.foundation_models import RoleAssignment
        subject.db.scalars(select(RoleAssignment)).one().status="expired"
    subject.db.commit()
    first=record(subject);subject.db.commit()
    assert first.recipient_count==0 and subject.db.scalars(select(NotificationPersonTarget.person_id)).one()==subject.person.id


@pytest.mark.parametrize("change",["secret","hash_version","wechat_app_id","disabled"])
def test_unavailable_configuration_defers_resolution_without_losing_business_event(subject,monkeypatch,change):
    verify(subject)
    policy={"secret":replace(POLICY,secret=""),"hash_version":replace(POLICY,hash_version=0),
        "wechat_app_id":replace(POLICY,sms_provider_key=None,wechat_app_id="unrelated-app"),
        "disabled":replace(POLICY,sms_provider_key=None,wechat_app_id=None)}[change]
    monkeypatch.setattr(identities,"identity_policy",lambda:policy)
    first=record(subject);subject.db.commit()
    assert first.recipient_count==0 and subject.db.get(NotificationEvent,first.event.id) is not None


def test_staged_graph_change_is_not_overwritten_or_flushed_by_recipient_resolution(subject):
    verify(subject)
    subject.user.mobile="13900000009"
    outbox=OutboxEvent(event_type="pending",aggregate_type="test",aggregate_id=str(uuid4()),payload_jsonb={},
        idempotency_key=f"pending:{uuid4()}",available_at=NOW)
    subject.db.add(outbox)
    first=record(subject)
    assert first.recipient_count==0 and subject.user.mobile=="13900000009" and subject.user in subject.db.dirty
    assert outbox in subject.db.new
    subject.db.rollback()


def test_valid_identity_reads_preserve_unrelated_pending_outbox(subject):
    verify(subject)
    outbox=OutboxEvent(event_type="pending",aggregate_type="test",aggregate_id=str(uuid4()),payload_jsonb={},
        idempotency_key=f"pending:{uuid4()}",available_at=NOW)
    subject.db.add(outbox)
    assert record(subject).recipient_count==2 and outbox in subject.db.new
    subject.db.rollback()


def test_revocation_between_exact_lookup_and_proof_read_is_not_accepted(subject,monkeypatch):
    verify(subject)
    original=identities.find_active_formal_identity
    def revoke(db,**args):
        result=original(db,**args)
        if result:
            db.execute(update(AuthIdentity).where(AuthIdentity.id==result.identity_id).values(status="revoked",revoked_at=NOW))
        return result
    monkeypatch.setattr(identities,"find_active_formal_identity",revoke)
    assert identities.resolve_verified_channels(subject.db,person_id=subject.person.id,policy=POLICY,now=NOW)==()
    subject.db.rollback()


def test_only_enabled_real_login_identity_domains_are_configured(monkeypatch):
    monkeypatch.setattr(identities,"get_settings",lambda:SimpleNamespace(identity_hash_secret=POLICY.secret,
        identity_hash_version=1,sms_provider="mock",wechat_provider="mock",wechat_app_id="mock-app"))
    assert identities.identity_policy()==replace(POLICY,sms_provider_key=None,wechat_app_id=None)
    monkeypatch.setattr(identities,"get_settings",lambda:SimpleNamespace(identity_hash_secret=POLICY.secret,
        identity_hash_version=1,sms_provider="aliyun_pnvs",wechat_provider="wechat",wechat_app_id="real-app-domain"))
    assert identities.identity_policy()==replace(POLICY,sms_provider_key="aliyun_pnvs",wechat_app_id="real-app-domain")
