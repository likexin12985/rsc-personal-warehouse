from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.foundation_models import AuditChainHead, AuditEvent, NotificationDelivery, NotificationEvent, NotificationPersonTarget, NotificationRecipient, OutboxEvent, Person
from app.inventory_models import InventoryMovement, InventoryTransaction, StockBalance
from app.models import User
from app.formal_services import inventory_notifications as service
from app.formal_services import inventory_notification_failures as failures
from app.formal_services.audit_chain import AuditChainHeadNotFound
from app.formal_services.notification_events import NotificationEventError
from app.formal_services.notification_events import record_business_notification
from app.formal_services.notification_expansion import expand_notification_event
from test_inventory_posting import db, world, make_account, post, command, InventoryMovementCommand  # noqa: F401
from notification_identity_fixtures import install_policy, verify_user_channels


@pytest.fixture
def fact(db, world, monkeypatch):
    install_policy(monkeypatch)
    db.add(AuditChainHead(id=uuid4(), stream_key="material_request", version=0))
    other = Person(id=uuid4(), organization_id=world.organization.id, employee_no="OTHER", name="测试接收人", employment_status="active")
    db.add(other)
    db.flush()
    other_user = User(id=str(uuid4()), person_id=other.id, account_status="active", mobile="13900000001",
                      name="测试接收人", password_hash="unused", role="technician", is_active=True)
    db.add(other_user)
    db.flush()
    world.user.mobile="13900000003"
    db.flush()
    identity_time=datetime.now(timezone.utc)-timedelta(days=1)
    verify_user_channels(db,world.user,identity_time)
    verify_user_channels(db,other_user,identity_time)
    source = make_account(db, organization=world.organization, material=world.material, custodian=world.person, initial=Decimal("10"))
    target = make_account(db, organization=world.organization, material=world.material, custodian=other, initial=Decimal("0"))
    result = post(db, world, command("transfer", (InventoryMovementCommand(
        from_account_id=source.id, to_account_id=target.id, quantity=Decimal("3")),)))
    db.commit()
    tx = db.get(InventoryTransaction, result.transaction_id)
    outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == "inventory_transaction", OutboxEvent.aggregate_id == str(tx.id)))
    return SimpleNamespace(db=db, world=world, source=source, target=target, tx=tx, outbox=outbox,
                           other=other, other_user=other_user, now=datetime.now(timezone.utc) + timedelta(seconds=1))


def project(fact):
    return service.project_inventory_notification(fact.db, outbox_id=fact.outbox.id, now=fact.now)


def recipients(fact, event_id):
    return tuple(fact.db.scalars(select(NotificationRecipient).where(NotificationRecipient.event_id == event_id)))


def stock_snapshot(fact):
    return [(str(row.stock_account_id), row.quantity, row.version, row.ledger_cursor)
            for row in fact.db.scalars(select(StockBalance).order_by(StockBalance.stock_account_id))]


def test_real_posting_notifies_both_exact_custodians_once_without_stock_or_outbox_changes(fact):
    before = stock_snapshot(fact)
    first = project(fact)
    replay = project(fact)
    assert first.created and not replay.created
    assert first.event_id == replay.event_id
    assert first.affected_person_count == first.recipient_count == 2
    assert {row.user_id for row in recipients(fact, first.event_id)} == {fact.world.user.id, fact.other_user.id}
    event = fact.db.get(NotificationEvent, first.event_id)
    assert event.payload_jsonb == fact.outbox.payload_jsonb
    assert not any(key in event.payload_jsonb for key in ("accounts", "people", "recipient_key", "quantity"))
    expanded = expand_notification_event(fact.db, event_id=first.event_id)
    assert expanded.created_delivery_count == 2
    assert {row.status for row in fact.db.scalars(select(NotificationDelivery))} == {"queued"}
    assert stock_snapshot(fact) == before
    assert fact.outbox.status == "pending" and fact.outbox.attempts == 0
    assert fact.tx.status == "posted"


@pytest.mark.parametrize("kind,business,key", service.DEDICATED_EVENTS)
def test_exact_existing_business_notification_prevents_duplicate_delivery(fact, kind, business, key):
    prior = record_business_notification(fact.db, event_type=kind, business_type=business, business_id=uuid4(),
        dedup_key=f"test-prior:{uuid4()}", payload={key: str(fact.tx.id)}, recipient_person_id=fact.other.id,
        occurred_at=fact.tx.posted_at, now=fact.now)
    expand_notification_event(fact.db, event_id=prior.event.id)
    delivery = fact.db.scalar(select(NotificationDelivery))
    delivery.status = "failed"
    delivery.last_error = "provider_result_unknown"
    fact.db.flush()
    result = project(fact)
    assert result.already_notified_person_count == 1
    assert [row.user_id for row in recipients(fact, result.event_id)] == [fact.world.user.id]
    assert delivery.status == "failed"  # No new route around the original retry boundary.


@pytest.mark.parametrize("changes", [
    {"event_type": "unrelated_event"}, {"business_type": "unrelated_business"},
    {"payload": {"inventory_transaction_id": str(uuid4())}},
])
def test_unrelated_notification_cannot_suppress_an_affected_custodian(fact, changes):
    args = dict(event_type="personal_inbound_posted", business_type="inbound_order", business_id=uuid4(),
        dedup_key=f"unrelated:{uuid4()}", payload={"inventory_transaction_id": str(fact.tx.id)},
        recipient_person_id=fact.other.id, occurred_at=fact.tx.posted_at, now=fact.now)
    record_business_notification(fact.db, **dict(args, **changes))
    assert project(fact).recipient_count == 2


def test_zero_channel_dedicated_target_prevents_a_second_recovery_route(fact):
    fact.other_user.is_active = False
    fact.db.flush()
    prior = record_business_notification(fact.db, event_type="personal_inbound_posted",
        business_type="inbound_order", business_id=uuid4(), dedup_key=f"unmapped:{uuid4()}",
        payload={"inventory_transaction_id":str(fact.tx.id)}, recipient_person_id=fact.other.id,
        occurred_at=fact.tx.posted_at, now=fact.now)
    assert prior.recipient_count == 0
    expand_notification_event(fact.db, event_id=prior.event.id)
    first = project(fact)
    assert first.already_notified_person_count == 1 and first.recipient_count == 1
    assert list(fact.db.scalars(select(NotificationPersonTarget.person_id)
        .where(NotificationPersonTarget.event_id == first.event_id))) == [fact.world.person.id]
    fact.other_user.is_active = True
    fact.db.commit()
    replay = project(fact)
    assert not replay.created and replay.event_id == first.event_id
    assert [row.user_id for row in recipients(fact, first.event_id)] == [fact.world.user.id]
    assert not recipients(fact, prior.event.id)  # No implicit identity recovery/send.


def test_later_dedicated_notification_cannot_change_the_original_generic_audience(fact):
    first = project(fact)
    fact.db.commit()
    record_business_notification(fact.db, event_type="personal_inbound_posted",
        business_type="inbound_order", business_id=uuid4(), dedup_key=f"later:{uuid4()}",
        payload={"inventory_transaction_id":str(fact.tx.id)}, recipient_person_id=fact.other.id,
        occurred_at=fact.tx.posted_at, now=fact.now)
    fact.db.commit()
    replay = project(fact)
    assert not replay.created and replay.event_id == first.event_id
    assert replay.recipient_count == first.recipient_count == 2
    assert replay.already_notified_person_count == first.already_notified_person_count == 0


@pytest.mark.parametrize("break_source", [
    lambda f: setattr(f.outbox, "payload_jsonb", dict(f.outbox.payload_jsonb, ledger_cursor=999999)),
    lambda f: setattr(f.outbox, "event_type", "inventory.transaction.reversed"),
    lambda f: setattr(f.outbox, "available_at", f.now + timedelta(days=1)),
    lambda f: setattr(f.db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id == str(f.tx.id))), "aggregate_id", str(uuid4())),
    lambda f: f.db.delete(f.db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == f.tx.id))),
])
def test_incomplete_or_conflicting_source_never_creates_notification(fact, break_source):
    break_source(fact)
    fact.db.flush()
    with pytest.raises(service.InventoryNotificationError):
        project(fact)
    assert fact.db.scalar(select(func.count()).select_from(NotificationEvent)) == 0


def test_worker_rollback_preserves_committed_stock_and_can_recover_once(fact):
    before = stock_snapshot(fact)
    project(fact)
    fact.db.rollback()
    assert fact.db.scalar(select(func.count()).select_from(NotificationEvent)) == 0
    assert stock_snapshot(fact) == before
    assert project(fact).created
    fact.db.commit()
    assert not project(fact).created


def test_active_worker_owner_defers_the_object_without_side_effects(fact, monkeypatch):
    monkeypatch.setattr(service, "_try_transaction_lock", lambda *_: False)
    assert project(fact) is None
    assert fact.db.scalar(select(func.count()).select_from(NotificationEvent)) == 0


def test_inactive_identity_is_not_replaced_by_a_guessed_user(fact):
    fact.other.employment_status = "inactive"
    fact.db.flush()
    result = project(fact)
    assert result.affected_person_count == 2 and result.recipient_count == 1
    assert [row.user_id for row in recipients(fact, result.event_id)] == [fact.world.user.id]


def test_batch_uses_its_own_checkpoint_and_leaves_outbox_state_owned_by_other_consumers(fact):
    for source in fact.db.scalars(select(OutboxEvent)):
        if source.id != fact.outbox.id:
            source.available_at = fact.now + timedelta(days=1)
    fact.outbox.status = "published"
    fact.db.flush()
    batch = service.project_pending_inventory_notifications(fact.db, limit=1, now=fact.now)
    assert len(batch.projected) == 1 and batch.projected[0].transaction_id == fact.tx.id
    assert not batch.blocked
    assert service.project_pending_inventory_notifications(fact.db, limit=1, now=fact.now).projected == ()
    assert fact.outbox.status == "published"


@pytest.mark.parametrize("limit", [0, 501, True, "10"])
def test_bad_batch_limit_is_rejected_before_database_access(limit):
    with pytest.raises(service.InventoryNotificationError):
        service.project_pending_inventory_notifications(None, limit=limit)


def broken_source(fact, *, first=True):
    for source in fact.db.scalars(select(OutboxEvent)):
        if source.id != fact.outbox.id:
            source.available_at = fact.now + timedelta(days=1)
    row = OutboxEvent(event_type="inventory.transaction.posted", aggregate_type="inventory_transaction",
        aggregate_id=str(uuid4()), payload_jsonb={"private_note": "do-not-copy-source-content"},
        idempotency_key=f"isolated-broken-source:{uuid4()}", status="pending", attempts=0,
        available_at=fact.now - timedelta(seconds=1),
        created_at=fact.outbox.created_at + timedelta(seconds=-1 if first else 1))
    fact.db.add(row)
    fact.db.commit()
    return row


@pytest.mark.parametrize("bad_first", [False, True])
def test_one_invalid_source_is_durably_isolated_without_rolling_back_other_notifications(fact, bad_first):
    bad = broken_source(fact, first=bad_first)
    before = stock_snapshot(fact)
    batch = service.project_pending_inventory_notifications(fact.db, limit=2, now=fact.now)
    assert len(batch.projected) == len(batch.blocked) == 1
    assert batch.projected[0].transaction_id == fact.tx.id
    assert batch.blocked[0].outbox_id == bad.id and batch.blocked[0].created
    fact.db.commit()
    audit = fact.db.get(AuditEvent, batch.blocked[0].audit_id)
    assert audit.action == failures.BLOCKED_ACTION
    assert audit.after_jsonb["code"] == failures.SOURCE_INVALID
    assert "do-not-copy-source-content" not in str(audit.after_jsonb)
    assert set(audit.after_jsonb) == {"schema", "outbox_id", "code", "source_sha256"}
    assert len(audit.after_jsonb["source_sha256"]) == 64
    assert bad.status == "pending" and bad.attempts == 0
    assert stock_snapshot(fact) == before
    assert len(recipients(fact, batch.projected[0].event_id)) == 2


def test_restart_skips_quarantined_source_and_limit_one_reaches_later_work(fact):
    broken_source(fact)
    first = service.project_pending_inventory_notifications(fact.db, limit=1, now=fact.now)
    assert len(first.blocked) == 1 and not first.projected
    fact.db.commit()
    expected_tx = fact.tx.id
    engine, now = fact.db.get_bind(), fact.now
    fact.db.close()
    with Session(engine) as restarted:
        second = service.project_pending_inventory_notifications(restarted, limit=1, now=now)
        assert len(second.projected) == 1 and second.projected[0].transaction_id == expected_tx
        assert not second.blocked
        restarted.commit()
        third = service.project_pending_inventory_notifications(restarted, limit=1, now=now)
        assert not third.projected and not third.blocked
        assert restarted.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == failures.BLOCKED_ACTION)) == 1


def test_caller_rollback_removes_success_and_quarantine_checkpoint_together(fact):
    broken_source(fact)
    before = stock_snapshot(fact)
    result = service.project_pending_inventory_notifications(fact.db, limit=2, now=fact.now)
    assert result.projected and result.blocked
    fact.db.rollback()
    assert fact.db.scalar(select(func.count()).select_from(NotificationEvent)) == 0
    assert fact.db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == failures.BLOCKED_ACTION)) == 0
    assert stock_snapshot(fact) == before
    retried = service.project_pending_inventory_notifications(fact.db, limit=2, now=fact.now)
    assert len(retried.projected) == len(retried.blocked) == 1


def test_failure_checkpoint_cannot_be_skipped_when_the_audit_chain_is_unavailable(fact, monkeypatch):
    broken_source(fact, first=False)
    before = stock_snapshot(fact)

    def unavailable(*args, **kwargs):
        raise AuditChainHeadNotFound("isolated audit failure")

    monkeypatch.setattr(failures, "append_audit_event", unavailable)
    with pytest.raises(AuditChainHeadNotFound):
        service.project_pending_inventory_notifications(fact.db, limit=2, now=fact.now)
    fact.db.rollback()
    assert fact.db.scalar(select(func.count()).select_from(NotificationEvent)) == 0
    assert stock_snapshot(fact) == before


def test_partial_notification_in_failed_savepoint_is_removed_before_quarantine(fact, monkeypatch):
    bad = broken_source(fact, first=False)
    bad_id = bad.id
    original = service.project_inventory_notification

    def fail_after_partial_write(db, *, outbox_id, now):
        if outbox_id != bad_id:
            return original(db, outbox_id=outbox_id, now=now)
        record_business_notification(db, event_type="isolated_partial_event", business_type="inventory_transaction",
            business_id=uuid4(), dedup_key="isolated-partial-failure", payload={}, recipient_person_id=None,
            occurred_at=now, now=now)
        raise NotificationEventError("isolated event conflict")

    monkeypatch.setattr(service, "project_inventory_notification", fail_after_partial_write)
    batch = service.project_pending_inventory_notifications(fact.db, limit=2, now=fact.now)
    fact.db.commit()
    assert len(batch.projected) == len(batch.blocked) == 1
    assert batch.blocked[0].code == failures.EVENT_CONFLICT
    assert fact.db.scalar(select(NotificationEvent.id).where(
        NotificationEvent.dedup_key == "isolated-partial-failure")) is None
    assert fact.db.scalar(select(func.count()).select_from(NotificationEvent)) == 1


def test_real_worker_expands_existing_business_notice_despite_bad_inventory_source(fact, monkeypatch):
    from app import notification_expander as worker
    from app.formal_services.notification_expansion import expand_pending_notification_events

    broken_source(fact)
    prior = record_business_notification(fact.db, event_type="shipment_handover_registered",
        business_type="shipment", business_id=uuid4(), dedup_key=f"existing-shipment:{uuid4()}",
        payload={"title": "发运已登记"}, recipient_person_id=fact.other.id, occurred_at=fact.now)
    prior_id = prior.event.id
    fact.db.commit()
    engine = fact.db.get_bind()
    fact.db.close()
    monkeypatch.setattr(worker, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(worker, "project_pending_inventory_notifications", service.project_pending_inventory_notifications)
    monkeypatch.setattr(worker, "expand_pending_notification_events", expand_pending_notification_events)
    code, result = worker._run_once(limit=2)
    assert code == 0 and result["ok"]
    assert len(result["blockedInventorySources"]) == 1
    assert "do-not-copy-source-content" not in str(result)
    with Session(engine) as verified:
        delivery = verified.scalars(select(NotificationDelivery).join(NotificationRecipient).where(
            NotificationRecipient.event_id == prior_id)).one()
        assert delivery.status == "queued" and delivery.attempts == 0
        assert verified.scalar(select(func.count()).select_from(NotificationDelivery)) == 3
        assert verified.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == failures.BLOCKED_ACTION)) == 1
