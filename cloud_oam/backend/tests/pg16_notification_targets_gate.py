"""0109 audience retention on the parent's verified disposable PG16 cluster.

This helper never provisions a database or calls a notification provider.
The caller supplies the already validated API and migrator connections.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import (
    NotificationEvent, NotificationPersonTarget, NotificationRecipient,
    NotificationTargetBinding, Person,
    AuthIdentity,
)
from app.models import User
from app.formal_services.notification_events import (
    NotificationEventError, record_business_notification, target_manifest_hash,
)
from app.formal_services.notification_expansion import expand_notification_event
from notification_identity_fixtures import POLICY


def _rejected(engine, action, *, state, message=None):
    with Session(engine) as db:
        try:
            with pytest.raises(DBAPIError) as error:
                action(db)
                db.commit()  # Deferred aggregate proof must fail at this boundary.
            assert error.value.orig.sqlstate == state
            if message is not None:
                assert message in str(error.value.orig)
        finally:
            db.rollback()


def _args(*people):
    business_id = uuid4()
    now = datetime.now(timezone.utc)
    return dict(event_type="pg16_target_proof", business_type="pg16_target_proof",
        business_id=business_id, dedup_key=f"pg16-target:{business_id}", payload={},
        recipient_person_id=None, recipient_person_ids=tuple(people), occurred_at=now, now=now)


def assert_notification_targets_gate(api_engine, migrator_engine):
    for engine, role in ((api_engine, "star_oam_api"), (migrator_engine, "star_oam_migrator")):
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_user")) == role
            assert connection.scalar(text("SELECT current_database()")) == "rsc_pg16_release_gate"
            assert int(connection.scalar(text("SHOW server_version_num"))) // 10000 == 16

    with Session(migrator_engine) as db:
        # Reuse one exact synthetic account from the parent's business proof;
        # create a distinct real person with no account, never a dangling UUID.
        ready, user = db.execute(select(Person, User).join(User, User.person_id == Person.id)
            .where(Person.employment_status == "active", User.is_active.is_(True),
                   User.account_status == "active", User.mobile.is_not(None), User.mobile != "")
            .where(select(AuthIdentity.id).where(AuthIdentity.user_id==User.id,
                AuthIdentity.provider_key==POLICY.wechat_app_id,AuthIdentity.status=="active").exists())
            .order_by(User.id).limit(1)).one()
        missing = Person(id=uuid4(), organization_id=ready.organization_id,
            employee_no=f"PG16-TARGET-{uuid4().hex}", name="PG16 无账号通知目标", employment_status="active")
        db.add(missing); db.commit()
        missing_id, ready_id, user_id = missing.id, ready.id, user.id

    args = _args(missing_id, ready_id)
    with Session(api_engine) as db:
        result = record_business_notification(db, **args)
        event_id = result.event.id
        assert result.recipient_count > 0
        expand_notification_event(db, event_id=event_id)
        db.commit()
        targets = dict(db.execute(select(NotificationPersonTarget.person_id, NotificationPersonTarget.id)
            .where(NotificationPersonTarget.event_id == event_id)).all())
        assert set(targets) == {missing_id, ready_id}
        bindings = tuple(db.execute(select(NotificationTargetBinding.target_id, NotificationTargetBinding.recipient_id)
            .where(NotificationTargetBinding.target_id.in_(targets.values()))))
        assert bindings and {row.target_id for row in bindings} == {targets[ready_id]}
        assert result.event.target_manifest_sha256 == target_manifest_hash((missing_id, ready_id))
        assert record_business_notification(db, **args).event.id == event_id
        with pytest.raises(NotificationEventError, match="different target people"):
            record_business_notification(db, **dict(args, recipient_person_ids=(ready_id,)))
        db.rollback()

    # A zero-channel expanded event still retains the intended person after a
    # restart; merely recording or expanding it does not claim delivery.
    with Session(api_engine) as db:
        zero = record_business_notification(db, **_args(missing_id))
        zero_id = zero.event.id
        assert zero.recipient_count == 0
        assert expand_notification_event(db, event_id=zero_id).created_delivery_count == 0
        db.commit()
    with Session(api_engine) as db:
        assert db.get(NotificationEvent, zero_id).status == "expanded"
        assert db.scalars(select(NotificationPersonTarget.person_id)
            .where(NotificationPersonTarget.event_id == zero_id)).one() == missing_id
        assert db.scalar(select(func.count()).select_from(NotificationRecipient)
            .where(NotificationRecipient.event_id == zero_id)) == 0
        rollback = record_business_notification(db, **_args(missing_id, ready_id))
        rollback_id = rollback.event.id
        db.flush(); db.rollback()
        assert db.get(NotificationEvent, rollback_id) is None
        assert db.scalar(select(func.count()).select_from(NotificationPersonTarget)
            .where(NotificationPersonTarget.event_id == rollback_id)) == 0

    target_id, recipient_id = bindings[0]
    mutations = (
        ("UPDATE notification_person_targets SET person_id=:person WHERE id=:target", {"person":missing_id,"target":target_id}),
        ("DELETE FROM notification_person_targets WHERE id=:target", {"target":target_id}),
        ("UPDATE notification_target_bindings SET target_id=:target WHERE recipient_id=:recipient", {"target":target_id,"recipient":recipient_id}),
        ("DELETE FROM notification_target_bindings WHERE recipient_id=:recipient", {"recipient":recipient_id}),
        ("TRUNCATE notification_person_targets CASCADE", {}),
        ("TRUNCATE notification_target_bindings", {}),
        ("DELETE FROM notification_events WHERE id=:id", {"id":event_id}),
        ("TRUNCATE notification_events CASCADE", {}),
    )
    for statement, values in mutations:
        for engine, state in ((api_engine, "42501"), (migrator_engine, "23514")):
            _rejected(engine, lambda db: db.execute(text(statement), values), state=state)
    _rejected(api_engine, lambda db: db.execute(text(
        "UPDATE notification_events SET target_manifest_sha256=:hash WHERE id=:id"),
        {"hash":"f" * 64, "id":event_id}), state="23514", message="immutable")

    # New event without its sealed audience and late extra targets must be
    # rejected by the real initially deferred PostgreSQL constraint triggers.
    absent_id = uuid4()
    def missing_targets(db):
        db.add(NotificationEvent(id=absent_id, event_type="pg16", business_type="pg16", business_id="pg16",
            dedup_key=f"pg16-missing:{absent_id}", payload_jsonb={}, status="pending",
            occurred_at=datetime.now(timezone.utc), target_manifest_sha256=target_manifest_hash((missing_id,))))
        db.flush()  # The missing target is allowed only until commit.
    _rejected(api_engine, missing_targets, state="23514", message="manifest mismatch")
    _rejected(api_engine, lambda db: db.add(NotificationPersonTarget(id=uuid4(),
        event_id=zero_id, person_id=ready_id)), state="23514", message="manifest mismatch")

    # Same-event wrong-person and cross-event bindings use newly inserted
    # recipients, so a unique constraint cannot mask the identity guard.
    for recipient_event in (event_id, zero_id):
        def mismatched_binding(db):
            recipient = NotificationRecipient(id=uuid4(), event_id=recipient_event, user_id=user_id,
                channel="sms", recipient_key=f"pg16-unsent-{uuid4().hex}", status="active")
            db.add(recipient); db.flush()
            db.add(NotificationTargetBinding(target_id=targets[missing_id], recipient_id=recipient.id))
        _rejected(api_engine, mismatched_binding, state="23514", message="binding mismatch")

    legacy_id = uuid4()
    with Session(api_engine) as db:
        db.add(NotificationEvent(id=legacy_id, event_type="pg16-legacy", business_type="pg16-legacy",
            business_id="pg16", dedup_key=f"pg16-legacy:{legacy_id}", payload_jsonb={}, status="expanded",
            occurred_at=datetime.now(timezone.utc), target_manifest_sha256=None))
        db.commit()
    _rejected(api_engine, lambda db: db.add(NotificationPersonTarget(id=uuid4(),
        event_id=legacy_id, person_id=missing_id)), state="23514", message="legacy notification audience")
    with Session(api_engine) as db:
        assert db.get(NotificationEvent, absent_id) is None
        assert db.get(NotificationEvent, event_id).target_manifest_sha256 == target_manifest_hash((missing_id,ready_id))
        assert db.scalar(select(func.count()).select_from(NotificationPersonTarget)
            .where(NotificationPersonTarget.event_id == event_id)) == 2
        assert db.scalar(select(func.count()).select_from(NotificationTargetBinding)
            .where(NotificationTargetBinding.target_id.in_(targets.values()))) == len(bindings)
    print("PG16 notification targets: exact people, zero-channel retention, atomic rollback, replay, "
          "API ACL, owner immutability, deferred manifest and binding guards PASS; no provider call", flush=True)
