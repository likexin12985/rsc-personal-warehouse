from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.database import Base
from app.foundation_models import (
    NotificationEvent,
    NotificationRecipient,
    Organization,
    OutboxEvent,
    Person,
)
from app.formal_services.notification_events import (
    NotificationEventError,
    record_business_notification,
    record_shipment_handover_notification,
    record_stock_return_notification,
)
from app.models import User, WechatIdentity
from notification_identity_fixtures import POLICY, install_policy, verify_user_channels


NOW = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)


def test_deduplication_rejects_conflicting_business_facts_and_keeps_pending_outbox():
    db = _db()
    try:
        business_id = uuid4()
        args = dict(event_type="test_fact", business_type="test", business_id=business_id,
                    dedup_key=f"test:{business_id}", payload={"quantity": "1.000"},
                    recipient_person_id=None, occurred_at=NOW, now=NOW)
        first = record_business_notification(db, **args)
        pending = OutboxEvent(event_type="test_pending", aggregate_type="test", aggregate_id=str(business_id),
                              payload_jsonb={}, idempotency_key=f"pending:{business_id}", available_at=NOW)
        db.add(pending)
        assert record_business_notification(db, **args).event.id == first.event.id
        assert pending in db.new
        for changed in ({"payload": {"quantity": "2.000"}}, {"business_id": uuid4()}, {"event_type": "other_fact"}):
            with pytest.raises(NotificationEventError, match="different facts"):
                record_business_notification(db, **dict(args, **changed))
        assert pending in db.new
    finally:
        engine = db.get_bind()
        db.close()
        engine.dispose()


def _db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = Session(engine)
    return session


def _shipment(person_id):
    return SimpleNamespace(
        id=uuid4(),
        shipment_no="SHP-20260915-TEST",
        carrier="测试承运商",
        tracking_no="TEST-001",
        shipped_at=NOW,
        target_location_id=uuid4(),
        target_person_id=person_id,
    )


def test_shipment_handover_creates_event_and_bound_channels(monkeypatch):
    install_policy(monkeypatch)
    db = _db()
    try:
        organization = Organization(
            id=uuid4(), code="ORG-TEST", name="测试组织", org_type="region_company"
        )
        person = Person(
            id=uuid4(),
            organization_id=organization.id,
            employee_no="E-001",
            name="测试工程师",
            employment_status="active",
        )
        user = User(
            id=str(uuid4()),
            person_id=person.id,
            account_status="active",
            mobile="13800000000",
            name="测试工程师",
            password_hash="unused",
            role="technician",
            is_active=True,
        )
        db.add_all(
            [
                organization,
                person,
                user,
                WechatIdentity(
                    id=str(uuid4()),
                    user_id=user.id,
                    app_id=POLICY.wechat_app_id,
                    openid="openid-test",
                ),
            ]
        )
        db.flush()
        verify_user_channels(db,user,NOW)
        shipment = _shipment(person.id)

        result = record_shipment_handover_notification(
            db, shipment=shipment, request_id=uuid4(), now=NOW
        )

        assert result.recipient_count == 2
        event_row = db.get(NotificationEvent, result.event.id)
        assert event_row is not None
        assert event_row.status == "pending"
        assert event_row.event_type == "shipment_handover_registered"
        assert event_row.payload_jsonb["tracking_no"] == "TEST-001"
        recipients = tuple(
            db.scalars(
                select(NotificationRecipient)
                .where(NotificationRecipient.event_id == event_row.id)
                .order_by(NotificationRecipient.channel)
            ).all()
        )
        assert [(row.channel, row.recipient_key, row.user_id) for row in recipients] == [
            ("sms", "13800000000", user.id),
            ("wechat", "openid-test", user.id),
        ]
    finally:
        engine = db.get_bind()
        db.close()
        engine.dispose()


def test_shipment_handover_notification_is_idempotent_and_missing_mapping_is_safe():
    db = _db()
    try:
        person_id = uuid4()
        organization = Organization(id=uuid4(), code="ORG-NO-LOGIN", name="无登录映射", org_type="region_company")
        db.add(organization)
        db.flush()
        db.add(Person(id=person_id, organization_id=organization.id, employee_no="NO-LOGIN", name="无登录账号人员", employment_status="active"))
        db.flush()
        shipment = _shipment(person_id)
        request_id = uuid4()
        first = record_shipment_handover_notification(
            db, shipment=shipment, request_id=request_id, now=NOW
        )
        second = record_shipment_handover_notification(
            db, shipment=shipment, request_id=request_id, now=NOW
        )

        assert second.event.id == first.event.id
        assert first.recipient_count == second.recipient_count == 0
        assert len(
            tuple(db.scalars(select(NotificationEvent)).all())
        ) == 1
        assert len(tuple(db.scalars(select(NotificationRecipient)).all())) == 0
    finally:
        engine = db.get_bind()
        db.close()
        engine.dispose()


def test_notification_lookup_does_not_flush_callers_outbox_boundary():
    db = _db()
    try:
        shipment = _shipment(None)
        pending_outbox = OutboxEvent(
            event_type="business_fact",
            aggregate_type="shipment",
            aggregate_id=str(shipment.id),
            payload_jsonb={},
            status="pending",
            attempts=0,
            idempotency_key=f"test-outbox:{shipment.id}",
            available_at=NOW,
        )
        db.add(pending_outbox)

        result = record_shipment_handover_notification(
            db, shipment=shipment, request_id=uuid4(), now=NOW
        )

        assert pending_outbox in db.new
        assert result.event.id is not None
    finally:
        engine = db.get_bind()
        db.close()
        engine.dispose()


def test_stock_return_notification_keeps_fact_identity_and_bound_recipient(monkeypatch):
    install_policy(monkeypatch)
    db = _db()
    try:
        organization = Organization(
            id=uuid4(), code="ORG-RETURN", name="退回测试组织", org_type="region_company"
        )
        person = Person(
            id=uuid4(),
            organization_id=organization.id,
            employee_no="E-RETURN",
            name="退回保管人",
            employment_status="active",
        )
        user = User(
            id=str(uuid4()),
            person_id=person.id,
            account_status="active",
            mobile="13900000000",
            name="退回保管人",
            password_hash="unused",
            role="technician",
            is_active=True,
        )
        db.add_all([organization, person, user])
        db.flush()
        verify_user_channels(db,user,NOW)
        business_id = uuid4()
        first = record_stock_return_notification(
            db,
            event_type="stock_return_received",
            business_type="stock_operation_receipt",
            business_id=business_id,
            payload={"receipt_id": str(business_id)},
            recipient_person_id=person.id,
            occurred_at=NOW,
            now=NOW,
        )
        second = record_stock_return_notification(
            db,
            event_type="stock_return_received",
            business_type="stock_operation_receipt",
            business_id=business_id,
            payload={"receipt_id": str(business_id)},
            recipient_person_id=person.id,
            occurred_at=NOW,
            now=NOW,
        )
        assert first.event.id == second.event.id
        assert first.recipient_count == second.recipient_count == 1
        assert first.event.dedup_key == f"stock-return-notification:stock_return_received:{business_id}"
        recipients = tuple(db.scalars(select(NotificationRecipient)).all())
        assert [(row.channel, row.recipient_key, row.user_id) for row in recipients] == [
            ("sms", "13900000000", user.id)
        ]
    finally:
        engine = db.get_bind()
        db.close()
        engine.dispose()
