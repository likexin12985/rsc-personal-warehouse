"""Exact identity recovery, transaction ownership and private operator routes."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import get_db
from app.dependencies import get_formal_principal
from app.foundation_models import AuditEvent, AuthIdentity, NotificationDelivery, NotificationEvent, NotificationPersonTarget, NotificationRecipient, NotificationTargetBinding, RoleAssignment
from app.models import WechatIdentity
from app.formal_services import notification_target_operations as service
from app.formal_services.authentication_identity import compute_identity_hash
from app.formal_services.notification_events import record_business_notification
from app.formal_services.notification_expansion import expand_notification_event, expand_pending_notification_events
from app.main import block_legacy_prototype_writes
from app.routers import formal_notifications
from test_inventory_notification_operations import db, fact, world, operator, count, stock_snapshot  # noqa: F401

SECRET = "local-notification-target-test-hmac-key-only"
POLICY = service.IdentityPolicy(SECRET, 1, "test", "wx-target-test")


def make_target(fact):
    fact.other_user.is_active=False
    fact.db.commit()
    result=record_business_notification(fact.db,event_type="inventory_transaction_changed",business_type="inventory_transaction",
        business_id=fact.tx.id,dedup_key=f"unmapped-test:{uuid4()}",payload={"transaction_id":str(fact.tx.id)},
        recipient_person_id=fact.other.id,occurred_at=fact.tx.posted_at,now=fact.now)
    assert result.recipient_count==0
    expand_notification_event(fact.db,event_id=result.event.id)
    fact.db.commit()
    target=fact.db.scalars(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id==result.event.id)).one()
    return target


def add_identity(fact, channel="sms", **changes):
    user=fact.other_user
    user.is_active=True
    provider=POLICY.sms_provider_key if channel=="sms" else POLICY.wechat_app_id
    identity_type="mobile" if channel=="sms" else "wechat_openid"
    coordinate=user.mobile if channel=="sms" else "openid-exact-target"
    if channel=="wechat":
        fact.db.add(WechatIdentity(id=str(uuid4()),user_id=user.id,app_id=provider,openid=coordinate,last_login_at=fact.now))
    identity=AuthIdentity(id=uuid4(),user_id=user.id,identity_type=identity_type,provider_key=provider,
        identifier_hash=compute_identity_hash(secret=SECRET,hash_version=1,identity_type=identity_type,provider_key=provider,identifier=coordinate),
        hash_version=1,verified_at=fact.now-timedelta(days=1),status="active",revoked_at=None)
    for key,value in changes.items():setattr(identity,key,value)
    fact.db.add(identity)
    fact.db.add(RoleAssignment(id=uuid4(),user_id=user.id,role_id=fact.world.regional_role.id,scope_type="organization",
        scope_id=str(fact.world.organization.id),valid_from=fact.now-timedelta(days=2),status="active",
        assigned_by=fact.world.headquarters_reviewer_user.id,reason="notification recovery test"))
    fact.db.commit()
    return identity


def row(fact,operator,target):
    return next(item for item in service.list_notification_targets(fact.db,actor=operator,policy=POLICY,unbound_only=False).items if item.target_id==target.id)


def args(fact,operator,target,channel="sms"):
    return dict(actor=operator,policy=POLICY,target_id=target.id,expected_snapshot_sha256=row(fact,operator,target).snapshot_sha256,
        channel=channel,reason="原目标身份已经核实",idempotency_key="target-recovery-command-001",request_id="target-recovery-trace-001")


@pytest.mark.parametrize("channel",["sms","wechat"])
def test_recovery_binds_exact_verified_person_then_expands_separately(fact,operator,channel):
    target=make_target(fact);proof=add_identity(fact,channel)
    before=stock_snapshot(fact)
    original_manifest=fact.db.get(NotificationEvent,target.event_id).target_manifest_sha256
    command=args(fact,operator,target,channel)
    first=service.recover_notification_target(fact.db,**command);fact.db.commit()
    assert first.outcome=="bound" and first.code is None and not first.replayed
    recipient=fact.db.get(NotificationRecipient,first.recipient_id)
    assert recipient.user_id==fact.other_user.id and recipient.channel==channel
    assert fact.db.get(NotificationTargetBinding,(target.id,recipient.id)) is not None
    event=fact.db.get(NotificationEvent,target.event_id)
    assert event.status=="pending" and event.target_manifest_sha256==original_manifest
    assert count(fact.db,NotificationDelivery)==0 and stock_snapshot(fact)==before and fact.outbox.status=="pending"
    audit=fact.db.get(AuditEvent,first.audit_id)
    assert audit.after_jsonb["identity_id"]==str(proof.id) and audit.after_jsonb["identity_provider"]==proof.provider_key
    assert fact.other_user.mobile not in str(audit.after_jsonb) and "openid-exact-target" not in str(audit.after_jsonb)
    expanded=expand_pending_notification_events(fact.db);fact.db.commit()
    assert len(expanded)==1 and expanded[0].created_delivery_count==1
    delivery=fact.db.scalars(select(NotificationDelivery)).one()
    delivery.status="failed";delivery.last_error="provider_result_unknown";fact.db.commit()
    replay=service.recover_notification_target(fact.db,**command)
    assert replace(replay,replayed=False)==first and replay.replayed
    assert delivery.status=="failed" and count(fact.db,NotificationDelivery)==1
    fresh=args(fact,operator,target,"wechat" if channel=="sms" else "sms")
    fresh.update(idempotency_key="target-recovery-command-002",request_id="target-recovery-trace-002")
    refused=service.recover_notification_target(fact.db,**fresh);fact.db.commit()
    assert refused.outcome=="blocked" and refused.code=="bound"
    assert count(fact.db,NotificationRecipient)==count(fact.db,NotificationTargetBinding)==1


@pytest.mark.parametrize("change",["wrong_hash","wrong_provider","wrong_version","revoked","pending","future","other_user"])
def test_legacy_coordinate_does_not_substitute_for_current_verified_identity(fact,operator,change):
    target=make_target(fact);proof=add_identity(fact)
    if change=="wrong_hash":proof.identifier_hash="e"*64
    elif change=="wrong_provider":proof.provider_key="other-provider"
    elif change=="wrong_version":proof.hash_version=2
    elif change=="revoked":proof.status="revoked";proof.revoked_at=fact.now
    elif change=="pending":proof.status="pending";proof.verified_at=None
    elif change=="future":proof.verified_at=fact.now+timedelta(days=1)
    else:proof.user_id=fact.world.user.id
    fact.db.commit()
    assert row(fact,operator,target).state=="needs_verified_channel"
    result=service.recover_notification_target(fact.db,**args(fact,operator,target));fact.db.commit()
    assert result.outcome=="blocked" and result.code=="needs_verified_channel"
    assert count(fact.db,NotificationRecipient)==count(fact.db,NotificationDelivery)==0


def test_wechat_other_app_even_if_most_recent_cannot_be_used(fact,operator):
    target=make_target(fact);add_identity(fact,"wechat")
    fact.db.add(WechatIdentity(id=str(uuid4()),user_id=fact.other_user.id,app_id="unrelated-app",openid="unrelated-openid",last_login_at=fact.now+timedelta(days=1)))
    fact.db.commit()
    result=service.recover_notification_target(fact.db,**args(fact,operator,target,"wechat"));fact.db.commit()
    assert fact.db.get(NotificationRecipient,result.recipient_id).recipient_key=="openid-exact-target"


@pytest.mark.parametrize("state",["needs_account","account_inactive","configuration_unavailable","cancelled","evidence_invalid"])
def test_nonrecoverable_states_remain_audited_without_guessing(fact,operator,state):
    target=make_target(fact);add_identity(fact)
    policy=POLICY
    if state=="needs_account":fact.other_user.person_id=None
    elif state=="account_inactive":fact.other.employment_status="inactive"
    elif state=="configuration_unavailable":policy=replace(POLICY,secret="")
    elif state=="cancelled":fact.db.get(NotificationEvent,target.event_id).status="cancelled"
    else:fact.db.get(NotificationEvent,target.event_id).target_manifest_sha256="c"*64
    fact.db.commit()
    item=service.list_notification_targets(fact.db,actor=operator,policy=policy).items[0]
    assert item.state==state and not item.available_channels
    command=args(fact,operator,target);command.update(policy=policy,expected_snapshot_sha256=item.snapshot_sha256)
    result=service.recover_notification_target(fact.db,**command);fact.db.commit()
    assert result.outcome=="blocked" and result.code==state and count(fact.db,NotificationRecipient)==0


@pytest.mark.parametrize("change",["coordinate","reverified_coordinate","revocation","payload","account_status"])
def test_stale_observation_cannot_recover_after_identity_or_event_change(fact,operator,change):
    target=make_target(fact);proof=add_identity(fact)
    command=args(fact,operator,target)
    if change=="coordinate":fact.other_user.mobile="13900000002"
    elif change=="reverified_coordinate":
        fact.other_user.mobile="13900000002"
        proof.identifier_hash=compute_identity_hash(secret=SECRET,hash_version=1,identity_type="mobile",
            provider_key=POLICY.sms_provider_key,identifier=fact.other_user.mobile)
    elif change=="revocation":proof.status="revoked";proof.revoked_at=fact.now
    elif change=="payload":fact.db.get(NotificationEvent,target.event_id).payload_jsonb={"changed":True}
    else:fact.other_user.account_status="suspended"
    fact.db.commit()
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**command)
    assert exc.value.http_status_code==412 and count(fact.db,NotificationRecipient)==0


@pytest.mark.parametrize("change",["reason","channel","request_reused","snapshot"])
def test_same_key_or_request_cannot_bind_different_command(fact,operator,change):
    target=make_target(fact);add_identity(fact)
    command=args(fact,operator,target)
    first=service.recover_notification_target(fact.db,**command);fact.db.commit()
    different=dict(command)
    if change=="reason":different["reason"]="different reason"
    elif change=="channel":different["channel"]="wechat"
    elif change=="request_reused":different.update(idempotency_key="target-other-command-0001",reason="changed")
    else:different["expected_snapshot_sha256"]="c"*64
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**different)
    assert exc.value.http_status_code==409 and count(fact.db,NotificationRecipient)==1
    assert fact.db.get(AuditEvent,first.audit_id) is not None


@pytest.mark.parametrize("fault",["caller_rollback","audit_failure"])
def test_recipient_binding_pending_state_and_audit_are_atomic(fact,operator,monkeypatch,fault):
    target=make_target(fact);add_identity(fact);command=args(fact,operator,target)
    before=count(fact.db,AuditEvent)
    if fault=="audit_failure":
        def fail(*a,**k):raise RuntimeError("test audit unavailable")
        monkeypatch.setattr(service,"append_audit_event",fail)
        with pytest.raises(RuntimeError,match="test audit unavailable"):service.recover_notification_target(fact.db,**command)
    else:service.recover_notification_target(fact.db,**command)
    fact.db.rollback()
    assert count(fact.db,NotificationRecipient)==count(fact.db,NotificationTargetBinding)==0
    assert count(fact.db,AuditEvent)==before and fact.db.get(NotificationEvent,target.event_id).status=="expanded"
    assert row(fact,operator,target).state=="ready"


@pytest.mark.parametrize("actor_kind",["no_permission","regional","restricted","test_double","read_only"])
def test_only_current_national_notification_operator_can_recover(fact,operator,actor_kind):
    target=make_target(fact);add_identity(fact);command=args(fact,operator,target)
    if actor_kind=="no_permission":actor=replace(operator,entitlements=())
    elif actor_kind=="regional":actor=replace(operator,assignments=tuple(replace(a,scope_type="organization",scope_id=str(fact.world.organization.id)) for a in operator.assignments))
    elif actor_kind=="restricted":actor=replace(operator,access_mode="restricted_handover")
    elif actor_kind=="test_double":actor=SimpleNamespace(user_id=operator.user_id,allows=lambda *a,**k:True)
    else:actor=replace(operator,entitlements=tuple(e for e in operator.entitlements if e.action=="read"))
    if actor_kind!="read_only":
        with pytest.raises(service.OperationsError):service.list_notification_targets(fact.db,actor=actor,policy=POLICY)
        with pytest.raises(service.OperationsError):service.list_legacy_notification_events(fact.db,actor=actor)
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**dict(command,actor=actor))
    assert exc.value.http_status_code==403 and count(fact.db,NotificationRecipient)==0


@pytest.mark.parametrize("change",["role_revoked","account_inactive"])
def test_current_actor_is_reloaded_after_graph_lock(fact,operator,change):
    target=make_target(fact);add_identity(fact);command=args(fact,operator,target)
    if change=="role_revoked":
        fact.world.headquarters_assignment.status="revoked";fact.world.headquarters_assignment.revoked_at=fact.now
        fact.world.headquarters_assignment.revoked_by=operator.user_id
    else:fact.world.headquarters_reviewer_user.is_active=False
    fact.db.commit()
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**command)
    assert exc.value.http_status_code==403


def test_unlinked_recipient_and_held_target_never_create_an_alternative(fact,operator,monkeypatch):
    target=make_target(fact);add_identity(fact);command=args(fact,operator,target)
    monkeypatch.setattr(service,"_try_target_lock",lambda *a:False)
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**command)
    assert exc.value.http_status_code==409
    fact.db.rollback();monkeypatch.undo()
    fact.db.add(NotificationRecipient(event_id=target.event_id,user_id=fact.other_user.id,channel="sms",recipient_key="old-original-coordinate",status="suppressed"))
    fact.db.commit()
    assert row(fact,operator,target).state=="bound"
    result=service.recover_notification_target(fact.db,**args(fact,operator,target));fact.db.commit()
    assert result.code=="bound" and count(fact.db,NotificationRecipient)==1


def test_legacy_events_remain_unknown_and_cannot_be_recovered_as_targets(fact,operator):
    event=NotificationEvent(event_type="legacy",business_type="legacy",business_id=str(uuid4()),dedup_key=f"legacy:{uuid4()}",payload_jsonb={},status="expanded",occurred_at=fact.now)
    fact.db.add(event);fact.db.commit()
    page=service.list_legacy_notification_events(fact.db,actor=operator)
    assert len(page.items)==1 and page.items[0].event_id==event.id and page.items[0].recipient_count==0
    assert not service.list_notification_targets(fact.db,actor=operator,policy=POLICY).items
    with pytest.raises(service.OperationsError) as exc:
        service.recover_notification_target(fact.db,actor=operator,policy=POLICY,target_id=event.id,
            expected_snapshot_sha256="c"*64,channel="sms",reason="不得猜测",idempotency_key="legacy-target-recovery-01",request_id="legacy-target-trace-01")
    assert exc.value.http_status_code==404


def test_http_private_surface_rejects_recipient_override_and_returns_immutable_result(fact,operator):
    target=make_target(fact);add_identity(fact)
    app=FastAPI();app.middleware("http")(block_legacy_prototype_writes)
    app.include_router(formal_notifications.router,prefix="/api")
    app.dependency_overrides[get_db]=lambda:fact.db
    app.dependency_overrides[get_formal_principal]=lambda:operator
    app.dependency_overrides[service.identity_policy]=lambda:POLICY
    path="/api/v1/notifications/person-targets"
    with TestClient(app) as client:
        response=client.get(path);assert response.status_code==200
        assert response.headers["Cache-Control"]=="private, no-store, max-age=0"
        item=response.json()["items"][0]
        assert item["state"]=="ready" and fact.other_user.mobile not in response.text
        body={"expected_snapshot_sha256":item["snapshot_sha256"],"channel":"sms","reason":"核对当前正式身份"}
        headers={"Idempotency-Key":"http-target-recovery-0001","X-Request-ID":"http-target-trace-0001"}
        url=f"{path}/{target.id}/recover"
        assert client.post(url,json=body).status_code==400
        assert client.post(url,json=dict(body,recipient_key="arbitrary"),headers=headers).status_code==422
        first=client.post(url,json=body,headers=headers);assert first.status_code==200 and first.json()["outcome"]=="bound"
        replay=client.post(url,json=body,headers=headers)
        assert replay.status_code==200 and replay.headers["Idempotency-Replayed"]=="true"
        assert replay.json()["audit_id"]==first.json()["audit_id"]
        assert count(fact.db,NotificationDelivery)==0
        app.dependency_overrides[get_formal_principal]=lambda:replace(operator,entitlements=())
        for response in (client.get(path),client.get(path+"/legacy-events"),client.post(url,json=body,headers=headers)):
            assert response.status_code==403 and response.headers["Cache-Control"]=="private, no-store, max-age=0"


def test_target_pagination_survives_resolution_of_the_cursor(fact,operator):
    targets=[make_target(fact) for _ in range(3)]
    add_identity(fact)
    first=service.list_notification_targets(fact.db,actor=operator,policy=POLICY,limit=1)
    assert len(first.items)==1 and first.next_after_id==first.items[0].target_id
    selected=fact.db.get(NotificationPersonTarget,first.next_after_id)
    service.recover_notification_target(fact.db,**args(fact,operator,selected));fact.db.commit()
    second=service.list_notification_targets(fact.db,actor=operator,policy=POLICY,limit=1,after_id=first.next_after_id)
    third=service.list_notification_targets(fact.db,actor=operator,policy=POLICY,limit=1,after_id=second.next_after_id)
    assert {page.items[0].target_id for page in (first,second,third)}=={target.id for target in targets}
    assert third.next_after_id is None


def test_mapping_change_during_graph_lock_requires_new_observation(fact,operator,monkeypatch):
    target=make_target(fact);add_identity(fact);command=args(fact,operator,target)
    original=service.lock_formal_principal_graph
    def change_mapping(db,users):
        original(db,users)
        fact.other_user.person_id=None
        db.flush()
    monkeypatch.setattr(service,"lock_formal_principal_graph",change_mapping)
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**command)
    assert exc.value.http_status_code==412 and count(fact.db,NotificationRecipient)==0
    fact.db.rollback()


def test_read_surfaces_do_not_flush_unrelated_caller_changes(fact,operator):
    make_target(fact)
    fact.outbox.status="invalid-unflushed-status"
    assert service.list_notification_targets(fact.db,actor=operator,policy=POLICY).items
    service.list_legacy_notification_events(fact.db,actor=operator)
    assert fact.outbox in fact.db.dirty
    fact.db.rollback()


def test_blocked_audit_replays_original_after_identity_repair_and_versions_next_attempt(fact,operator):
    target=make_target(fact);command=args(fact,operator,target)
    blocked=service.recover_notification_target(fact.db,**command);fact.db.commit()
    assert blocked.code=="account_inactive"
    another=dict(command,idempotency_key="target-recovery-command-002",request_id="target-recovery-trace-002")
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**another)
    assert exc.value.http_status_code==412
    fact.db.rollback();add_identity(fact)
    replay=service.recover_notification_target(fact.db,**command)
    assert replay.replayed and replay.audit_id==blocked.audit_id and replay.code=="account_inactive"
    fresh=dict(args(fact,operator,target),idempotency_key=another["idempotency_key"],request_id=another["request_id"])
    restored=service.recover_notification_target(fact.db,**fresh);fact.db.commit()
    assert restored.outcome=="bound" and restored.audit_id!=blocked.audit_id


def test_malformed_original_audit_cannot_be_reinterpreted_as_success(fact,operator):
    target=make_target(fact);add_identity(fact);command=args(fact,operator,target)
    first=service.recover_notification_target(fact.db,**command);fact.db.commit()
    audit=fact.db.get(AuditEvent,first.audit_id)
    # The fast metadata fixture permits corruption; migrated tests separately
    # prove the real API/owner immutable audit boundary.
    audit.after_jsonb=dict(audit.after_jsonb,outcome="delivered")
    fact.db.commit()
    with pytest.raises(service.OperationsError) as exc:service.recover_notification_target(fact.db,**command)
    assert exc.value.http_status_code==503 and count(fact.db,NotificationRecipient)==1
