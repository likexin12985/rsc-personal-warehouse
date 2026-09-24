"""Durable intended audience, migrated FK/immutability and legacy preservation."""
import hashlib
import io
import runpy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
import pytest
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.foundation_models import NotificationEvent, NotificationPersonTarget, NotificationRecipient, NotificationTargetBinding, Organization, Person
from app.models import User
from app.formal_services.notification_events import NotificationEventError, record_business_notification, target_manifest_hash
from app.formal_services.notification_expansion import expand_notification_event
from test_alembic_migrations import _config
from notification_identity_fixtures import install_policy, verify_user_channels

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)
MIGRATION = Path(__file__).parents[1] / "alembic/versions/20261019_0109_notification_person_targets.py"


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    install_policy(monkeypatch)
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    url = f"sqlite+pysqlite:///{tmp_path / 'target-migration.db'}"
    config = _config(url)
    command.upgrade(config, "20261018_0108")
    engine = create_engine(url)
    @event.listens_for(engine, "connect")
    def fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    legacy_id, recipient_id = uuid4().hex, uuid4().hex
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO notification_events(id,event_type,business_type,business_id,dedup_key,payload_jsonb,status,occurred_at,created_at) VALUES(:id,'legacy','legacy','legacy','legacy','{}','expanded',:time,:time)"), {"id": legacy_id, "time": NOW.isoformat()})
        connection.execute(text("INSERT INTO notification_recipients(id,event_id,user_id,channel,recipient_key,status,created_at) VALUES(:id,:event,NULL,'sms','legacy-coordinate','active',:time)"), {"id":recipient_id,"event":legacy_id,"time":NOW.isoformat()})
    command.upgrade(config, "head")
    yield engine, config, legacy_id, recipient_id
    engine.dispose()


def people(db):
    organization = Organization(id=uuid4(), code=f"ORG-{uuid4()}", name="测试组织", org_type="region_company")
    db.add(organization)
    db.flush()
    first = Person(id=uuid4(), organization_id=organization.id, employee_no="ONE", name="无账号目标", employment_status="active")
    second = Person(id=uuid4(), organization_id=organization.id, employee_no="TWO", name="已绑定目标", employment_status="active")
    db.add_all([first, second]); db.flush()
    user = User(id=str(uuid4()), person_id=second.id, account_status="active", mobile="13900000009", name="测试账号", password_hash="unused", role="technician", is_active=True)
    db.add(user); db.flush()
    verify_user_channels(db,user,NOW)
    db.commit()
    return first, second, user


def arguments(*people):
    business_id = uuid4()
    return dict(event_type="target_test", business_type="target_test", business_id=business_id,
        dedup_key=f"target-test:{business_id}", payload={}, recipient_person_id=None,
        recipient_person_ids=tuple(row.id for row in people), occurred_at=NOW, now=NOW)


def test_real_migration_keeps_legacy_recipients_across_empty_down_and_up(migrated):
    engine, config, legacy_id, recipient_id = migrated
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT target_manifest_sha256 FROM notification_events WHERE id=:id"), {"id":legacy_id}) is None
        assert connection.scalar(text("SELECT count(*) FROM notification_person_targets")) == 0
    command.downgrade(config, "20261018_0108")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM notification_recipients WHERE id=:id"), {"id":recipient_id}) == 1
        assert "target_manifest_sha256" not in {col["name"] for col in inspect(connection).get_columns("notification_events")}
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM notification_recipients WHERE id=:id"), {"id":recipient_id}) == 1


def test_unmapped_person_survives_zero_channel_expansion_and_exact_replay(migrated):
    engine, config, _, _ = migrated
    with Session(engine) as db:
        missing, ready, _ = people(db)
        args = arguments(missing, ready)
        first = record_business_notification(db, **args)
        assert first.recipient_count == 1
        result = expand_notification_event(db, event_id=first.event.id, now=NOW)
        db.commit()
        assert result.created_delivery_count == 1
        targets = tuple(db.scalars(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id == first.event.id)))
        assert {row.person_id for row in targets} == {missing.id, ready.id}
        assert first.event.target_manifest_sha256 == target_manifest_hash((missing.id,ready.id))
        bound = select(NotificationTargetBinding.target_id)
        assert list(db.scalars(select(NotificationPersonTarget.person_id).where(
            NotificationPersonTarget.event_id == first.event.id, ~NotificationPersonTarget.id.in_(bound)))) == [missing.id]
        assert record_business_notification(db, **args).event.id == first.event.id
        with pytest.raises(NotificationEventError, match="different target people"):
            record_business_notification(db, **dict(args, recipient_person_ids=(ready.id,)))
    with pytest.raises(RuntimeError, match="evidence must be retained"):
        command.downgrade(config, "20261018_0108")


def test_target_facts_and_manifest_cannot_be_rewritten_or_deleted(migrated):
    engine, _, _, _ = migrated
    with Session(engine) as db:
        missing, ready, _ = people(db)
        result = record_business_notification(db, **arguments(missing,ready)); db.commit()
        target = db.scalars(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id==result.event.id,NotificationPersonTarget.person_id==ready.id)).one()
        statements = [
            ("UPDATE notification_events SET target_manifest_sha256=:value WHERE id=:id", {"value":"b"*64,"id":result.event.id.hex}),
            ("UPDATE notification_person_targets SET person_id=:value WHERE id=:id", {"value":missing.id.hex,"id":target.id.hex}),
            ("DELETE FROM notification_person_targets WHERE id=:id", {"id":target.id.hex}),
            ("DELETE FROM notification_target_bindings WHERE target_id=:id", {"id":target.id.hex}),
            ("DELETE FROM notification_events WHERE id=:id", {"id":result.event.id.hex}),
        ]
        for statement, values in statements:
            with pytest.raises(IntegrityError):
                db.execute(text(statement), values); db.flush()
            db.rollback()


def test_cross_person_and_cross_event_bindings_are_rejected(migrated):
    engine, _, _, _ = migrated
    with Session(engine) as db:
        missing, ready, user = people(db)
        result = record_business_notification(db, **arguments(missing)); db.commit()
        target = db.scalars(select(NotificationPersonTarget).where(NotificationPersonTarget.event_id==result.event.id)).one()
        for event_id in (result.event.id, uuid4()):
            if event_id != result.event.id:
                other = NotificationEvent(id=event_id,event_type="test",business_type="test",business_id="test",dedup_key=f"other:{event_id}",payload_jsonb={},status="pending",occurred_at=NOW)
                db.add(other); db.commit()
            recipient = NotificationRecipient(id=uuid4(),event_id=event_id,user_id=user.id,channel="sms",recipient_key=f"test-{uuid4()}",status="active",created_at=NOW)
            db.add(recipient);db.flush()
            db.add(NotificationTargetBinding(target_id=target.id,recipient_id=recipient.id,created_at=NOW))
            with pytest.raises(IntegrityError,match="binding mismatch"):
                db.commit()
            db.rollback()


def test_caller_rollback_removes_targets_and_bindings_but_not_legacy(migrated):
    engine, _, _, _ = migrated
    with Session(engine) as db:
        missing, ready, _ = people(db)
        record_business_notification(db, **arguments(missing,ready)); db.flush(); db.rollback()
        assert db.scalar(select(func.count()).select_from(NotificationPersonTarget)) == 0
        assert db.scalar(select(func.count()).select_from(NotificationTargetBinding)) == 0
        assert db.scalar(select(func.count()).select_from(NotificationEvent)) == 1


def test_sealed_empty_audience_is_retained_and_cannot_be_reinterpreted(migrated):
    engine, config, _, _ = migrated
    with Session(engine) as db:
        missing, _, _ = people(db)
        args = arguments()
        result = record_business_notification(db, **args)
        db.commit()
        assert result.event.target_manifest_sha256 == target_manifest_hash(())
        event_id = result.event.id
        with pytest.raises(NotificationEventError, match="different target people"):
            record_business_notification(db, **dict(args, recipient_person_ids=(missing.id,)))
        # Both the original manifest guard and the current event-facts guard
        # reject deletion; SQLite does not promise which trigger fires first.
        with pytest.raises(IntegrityError, match="manifest is immutable|notification event facts cannot be removed"):
            db.delete(result.event); db.commit()
        db.rollback()
        assert db.get(NotificationEvent, event_id).target_manifest_sha256 == target_manifest_hash(())
        assert db.scalar(select(func.count()).select_from(NotificationPersonTarget).where(
            NotificationPersonTarget.event_id == event_id)) == 0
    with pytest.raises(RuntimeError, match="evidence must be retained"):
        command.downgrade(config, "20261018_0108")


def test_legacy_replay_does_not_backfill_a_guessed_audience(migrated):
    engine, _, _, _ = migrated
    with Session(engine) as db:
        missing, _, _ = people(db)
        args = arguments(missing)
        original = NotificationEvent(id=uuid4(), event_type=args["event_type"],
            business_type=args["business_type"], business_id=str(args["business_id"]),
            dedup_key=args["dedup_key"], payload_jsonb={}, status="expanded", occurred_at=NOW,
            target_manifest_sha256=None)
        db.add(original); db.commit()
        replay = record_business_notification(db, **args)
        db.commit()
        assert replay.event.id == original.id and replay.recipient_count == 0
        assert replay.event.target_manifest_sha256 is None
        assert db.scalar(select(func.count()).select_from(NotificationPersonTarget)) == 0


def test_postgresql_sql_pins_seal_guards_acl_and_readiness_in_both_directions(monkeypatch):
    from pglast import parse_sql
    from pglast.ast import CreateTrigStmt
    from app.database_security import EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS, MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256, RUNTIME_INSERT_TABLES, RUNTIME_UPDATE_TABLES
    from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0108, OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0109
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    migration = runpy.run_path(str(MIGRATION))
    output = io.StringIO(); config = _config("postgresql+psycopg://offline:offline@localhost/offline", output_buffer=output)
    command.upgrade(config, "20261018_0108:20261019_0109", sql=True)
    sql = output.getvalue()
    trigger_sql = {row.stmt.trigname: row.stmt for row in parse_sql(sql) if isinstance(row.stmt, CreateTrigStmt)}
    assert "GRANT SELECT, INSERT ON TABLE public.notification_person_targets, public.notification_target_bindings TO star_oam_api" in sql
    assert "GRANT UPDATE" not in sql and "GRANT DELETE" not in sql
    assert migration["OLD_HASH"] in sql and migration["NEW_HASH"] in sql
    for name,(table,events,kind,deferred) in migration["TRIGGERS"].items():
        assert EXPECTED_MATERIAL_REQUEST_APPROVAL_TRIGGERS[name] == (table,migration["FUNCTION_NAME"],"A",kind,deferred,deferred,deferred)
        assert f"ENABLE ALWAYS TRIGGER {name}" in sql
        # The runtime catalog rejects column filters and WHEN clauses. Check
        # emitted DDL, not just the hand-maintained trigger metadata tuple.
        assert not trigger_sql[name].columns and trigger_sql[name].whenClause is None
    assert MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256[(migration["FUNCTION_NAME"],"")] == hashlib.sha256(migration["GUARD_BODY"].encode()).hexdigest()
    assert {migration["TARGETS"],migration["BINDINGS"]} <= RUNTIME_INSERT_TABLES
    assert not ({migration["TARGETS"],migration["BINDINGS"]} & RUNTIME_UPDATE_TABLES)
    ready_signature = "rsc_oam_runtime_binding_ready_0044()"
    assert OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0108[ready_signature][6] == migration["OLD_HASH"]
    assert OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0109[ready_signature][6] == migration["NEW_HASH"]
    output.seek(0);output.truncate(0)
    command.downgrade(config,"20261019_0109:20261018_0108",sql=True)
    sql=output.getvalue();assert parse_sql(sql)
    assert sql.index("evidence must be retained") < sql.index("DROP TABLE notification_person_targets")
    assert migration["OLD_HASH"] in sql and migration["NEW_HASH"] in sql
