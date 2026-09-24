"""Shared recovery proof on migrated SQLite and the parent's disposable PG16.

Only the explicit fixture engine creates synthetic identities. Runtime recovery
uses the supplied API engine and seeded permissions; no provider is constructed.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import (AuditEvent, AuthIdentity, NotificationDelivery, NotificationEvent,
    NotificationPersonTarget, NotificationRecipient, NotificationTargetBinding, Organization, Person, Role, RoleAssignment)
from app.models import User
from app.formal_services import notification_target_operations as operations
from app.formal_services.audit_chain import verify_audit_event_in_read_snapshot
from app.formal_services.authentication_identity import compute_identity_hash
from app.formal_services.notification_events import record_business_notification
from app.formal_services.notification_expansion import expand_notification_event

POLICY = operations.IdentityPolicy("synthetic-migrated-notification-target-proof-key", 1, "target-gate-test", None)


def prepare_people(fixture_engine):
    now = datetime.now(timezone.utc)
    with Session(fixture_engine) as db:
        org = Organization(id=uuid4(),code=f"TG-{uuid4().hex}",name="通知目标合成测试总部",org_type="headquarters",status="active")
        db.add(org); db.flush()
        people = [Person(id=uuid4(),organization_id=org.id,employee_no=f"TG-{uuid4().hex}",
            name=name,employment_status="active") for name in ("测试通知管理员","测试原目标")]
        db.add_all(people); db.flush()
        users = [User(id=str(uuid4()),person_id=person.id,account_status="active",mobile=f"139{uuid4().int % 10**8:08d}",
            name=person.name,password_hash="unused-test-identity",role="technician",is_active=index==0)
            for index,person in enumerate(people)]
        db.add_all(users); db.flush()
        for index,user in enumerate(users):
            role=db.scalars(select(Role).where(Role.code==("admin" if index==0 else "technician"))).one()
            db.add(RoleAssignment(id=uuid4(),user_id=user.id,role_id=role.id,scope_type="national" if index==0 else "person",
                scope_id="*" if index==0 else str(user.person_id),valid_from=now-timedelta(days=1),status="active",
                assigned_by=users[0].id,reason="synthetic notification recovery gate"))
            db.add(AuthIdentity(id=uuid4(),user_id=user.id,identity_type="mobile",provider_key=POLICY.sms_provider_key,
                identifier_hash=compute_identity_hash(secret=POLICY.secret,hash_version=1,identity_type="mobile",
                    provider_key=POLICY.sms_provider_key,identifier=user.mobile),hash_version=1,
                verified_at=now-timedelta(hours=1),status="active"))
        db.commit()
        return SimpleNamespace(actor_id=users[0].id,person_id=people[1].id,user_id=users[1].id)


def create_target(api_engine, people):
    now=datetime.now(timezone.utc)
    with Session(api_engine) as db:
        business_id=uuid4()
        event=record_business_notification(db,event_type="target_gate",business_type="target_gate",business_id=business_id,
            dedup_key=f"target-gate:{business_id}",payload={},recipient_person_id=people.person_id,occurred_at=now,now=now)
        assert event.recipient_count==0
        assert expand_notification_event(db,event_id=event.event.id).created_delivery_count==0
        db.commit()
        return db.scalars(select(NotificationPersonTarget.id).where(NotificationPersonTarget.event_id==event.event.id)).one()


def observation(db, actor, target_id):
    after=None
    while True:
        page=operations.list_notification_targets(db,actor=actor,policy=POLICY,unbound_only=False,limit=100,after_id=after)
        found=next((item for item in page.items if item.target_id==target_id),None)
        if found is not None:return found
        assert page.next_after_id is not None, "exact retained target missing"
        after=page.next_after_id


def assert_recovery_on_migrated_database(api_engine, fixture_engine):
    people=prepare_people(fixture_engine)
    target_id=create_target(api_engine,people)
    with Session(fixture_engine) as db:
        db.get(User,people.user_id).is_active=True; db.commit()
    with Session(api_engine) as db:
        actor=load_formal_principal(db,people.actor_id)
        item=observation(db,actor,target_id)
        assert item.state=="ready" and item.available_channels==("sms",)
        command=dict(actor=actor,policy=POLICY,target_id=target_id,expected_snapshot_sha256=item.snapshot_sha256,
            channel="sms",reason="迁移后核实原目标身份",idempotency_key=f"gate-recover-{uuid4()}",request_id=f"gate-trace-{uuid4()}")
        audit_count=db.scalar(select(func.count()).select_from(AuditEvent))
        first=operations.recover_notification_target(db,**command)
        assert first.outcome=="bound"
        db.flush();db.rollback()
        assert db.get(NotificationRecipient,first.recipient_id) is None
        assert db.get(NotificationTargetBinding,(target_id,first.recipient_id)) is None
        assert db.scalar(select(func.count()).select_from(AuditEvent))==audit_count
        assert db.get(NotificationEvent,item.event_id).status=="expanded"
        restored=operations.recover_notification_target(db,**command);db.commit()
        assert restored.outcome=="bound" and restored.audit_id!=first.audit_id
        assert db.get(NotificationEvent,item.event_id).status=="pending"
        assert db.get(NotificationTargetBinding,(target_id,restored.recipient_id)) is not None
        assert db.get(NotificationRecipient,restored.recipient_id).user_id==people.user_id
        assert db.scalar(select(func.count()).select_from(NotificationDelivery).where(
            NotificationDelivery.recipient_id==restored.recipient_id))==0
        verified=verify_audit_event_in_read_snapshot(db,stream_key=operations.STREAM,event_id=restored.audit_id)
        assert verified.after_jsonb["outcome"]=="bound"
        replay=operations.recover_notification_target(db,**command)
        assert replay.replayed and replace(replay,replayed=False)==restored
        assert expand_notification_event(db,event_id=item.event_id).created_delivery_count==1
        db.commit()
        assert expand_notification_event(db,event_id=item.event_id).created_delivery_count==0
        delivery=db.scalars(select(NotificationDelivery).where(NotificationDelivery.recipient_id==restored.recipient_id)).one()
        assert delivery.status=="queued" and delivery.attempts==0
        db.rollback()
    return SimpleNamespace(people=people,target_id=target_id,event_id=item.event_id,result=restored,command=command)


def assert_pg16_recovery_gate(api_engine, fixture_engine):
    for engine,role in ((api_engine,"star_oam_api"),(fixture_engine,"star_oam_migrator")):
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT current_user"))==role
            assert conn.scalar(text("SELECT current_database()"))=="rsc_pg16_release_gate"
            assert int(conn.scalar(text("SHOW server_version_num")))//10000==16
    proof=assert_recovery_on_migrated_database(api_engine,fixture_engine)
    # An expander owns this event in a separate real transaction. Recovery must
    # report a conflict and leave even an idempotent replay unmodified.
    with Session(api_engine) as owner:
        owner.execute(select(NotificationEvent).where(NotificationEvent.id==proof.event_id).with_for_update()).one()
        with Session(api_engine) as contender:
            contender.execute(text("SET LOCAL statement_timeout = '5s'"))
            with pytest.raises(operations.OperationsError) as error:
                operations.recover_notification_target(contender,**proof.command)
            assert error.value.http_status_code==409
            contender.rollback()
        owner.rollback()
    # Normal API recovery must not require identity writes. Prove those ACLs
    # remain SELECT-only and that the immutable result survives denial.
    with Session(api_engine) as db:
        with pytest.raises(DBAPIError) as error:
            db.execute(text("UPDATE auth_identities SET provider_key='forbidden' WHERE user_id=:id"),{"id":proof.people.user_id})
        assert error.value.orig.sqlstate=="42501";db.rollback()
        assert operations.recover_notification_target(db,**proof.command).audit_id==proof.result.audit_id
        db.rollback()
    print("PG16 target recovery: seeded permissions, exact HMAC identity, API insert ACL, atomic rollback, "
        "immutable audit, replay, event ownership and separate expansion PASS; no provider call",flush=True)
