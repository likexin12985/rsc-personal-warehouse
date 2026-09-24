"""Operator boundaries, immutable recheck evidence and caller atomicity."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import AuditEvent, NotificationDelivery, NotificationEvent, Permission, RolePermission
from app.formal_services import inventory_notification_failures as failures
from app.formal_services import inventory_notification_operations as service
from app.formal_services.audit_chain import AuditChainHeadNotFound
from app.main import block_legacy_prototype_writes
from app.routers import formal_notifications
from test_inventory_notifications import fact, world, broken_source, stock_snapshot  # noqa: F401


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def operator(fact):
    for action in ("read", "retry"):
        permission = Permission(resource="notification_delivery", action=action, field_code="", description="test operator")
        fact.db.add(permission)
        fact.db.flush()
        fact.db.add(RolePermission(role_id=fact.world.headquarters_assignment.role_id, permission_id=permission.id, effect="allow"))
    fact.db.commit()
    return load_formal_principal(fact.db, fact.world.headquarters_reviewer_user.id)


def isolate(fact, outbox_id=None):
    outbox_id = outbox_id or fact.outbox.id
    assert failures.try_source_lock(fact.db, outbox_id)
    failed = failures.record_source_failure(fact.db, outbox_id=outbox_id, code=failures.SOURCE_INVALID, now=fact.now)
    fact.db.commit()
    return failed


def command(fact, operator, **changes):
    item = service.list_inventory_notification_sources(fact.db, actor=operator).items[0]
    return dict(actor=operator, outbox_id=item.outbox_id, expected_audit_id=item.latest_audit_id,
        expected_source_sha256=item.source_sha256, reason="按原来源复核", idempotency_key="source-recheck-test-0001",
        request_id="source-recheck-trace-0001", **changes)


def count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_recheck_projects_once_without_delivery_or_source_changes_and_replay_is_original(fact, operator):
    blocked = isolate(fact)
    original_hash = fact.db.get(AuditEvent, blocked.audit_id).event_hash
    initial_audit_count = count(fact.db, AuditEvent)
    before = stock_snapshot(fact)
    args = command(fact, operator)
    first = service.recheck_inventory_notification_source(fact.db, **args)
    fact.db.commit()
    assert first.item.status == "projected" and first.item.recipient_count == 2 and not first.replayed
    assert count(fact.db, NotificationEvent) == 1 and count(fact.db, NotificationDelivery) == 0
    assert fact.outbox.status == "pending" and stock_snapshot(fact) == before
    assert fact.db.get(AuditEvent, blocked.audit_id).event_hash == original_hash
    replay = service.recheck_inventory_notification_source(fact.db, **args)
    assert replay.replayed and replay.item == first.item
    assert count(fact.db, AuditEvent) == initial_audit_count + 1
    assert service.list_inventory_notification_sources(fact.db, actor=operator).items == (first.item,)
    audit = fact.db.get(AuditEvent, first.item.latest_audit_id)
    assert audit.actor_user_id == operator.user_id and audit.after_jsonb["reason"] == args["reason"]
    assert audit.after_jsonb["observed_source_sha256"] == args["expected_source_sha256"]
    assert args["idempotency_key"] not in str(audit.after_jsonb)
    with pytest.raises(service.OperationsError) as exc:
        service.recheck_inventory_notification_source(fact.db, **dict(args, reason="changed command"))
    assert exc.value.http_status_code == 409


def test_still_bad_source_is_audited_and_second_explicit_check_requires_latest_version(fact, operator):
    bad = broken_source(fact)
    isolate(fact, bad.id)
    args = command(fact, operator)
    first = service.recheck_inventory_notification_source(fact.db, **args)
    fact.db.commit()
    assert first.item.status == "blocked" and first.item.code == failures.SOURCE_INVALID
    assert first.item.event_id is None and count(fact.db, NotificationEvent) == 0
    assert "do-not-copy-source-content" not in str(service.list_inventory_notification_sources(fact.db, actor=operator))
    newer = dict(args, idempotency_key="source-recheck-test-0002", request_id="source-recheck-trace-0002")
    with pytest.raises(service.OperationsError) as exc:
        service.recheck_inventory_notification_source(fact.db, **newer)
    assert exc.value.http_status_code == 412
    second = service.recheck_inventory_notification_source(fact.db, **dict(newer, expected_audit_id=first.item.latest_audit_id))
    fact.db.commit()
    assert second.item.latest_audit_id != first.item.latest_audit_id
    assert service.recheck_inventory_notification_source(fact.db, **args).item == first.item


def test_source_changed_after_quarantine_cannot_be_replaced_under_same_object(fact, operator, monkeypatch):
    isolate(fact)
    args = command(fact, operator)
    fact.outbox.payload_jsonb = dict(fact.outbox.payload_jsonb, private_note="do-not-expose")
    fact.db.commit()
    monkeypatch.setattr(service, "project_inventory_notification", lambda *a, **k: pytest.fail("changed source projected"))
    result = service.recheck_inventory_notification_source(fact.db, **args)
    assert result.item.status == "blocked" and result.item.code == service.SOURCE_CHANGED
    audit = fact.db.get(AuditEvent, result.item.latest_audit_id).after_jsonb
    assert audit["observed_source_sha256"] != audit["source_sha256"]
    assert "do-not-expose" not in str(audit)


@pytest.mark.parametrize("mode", ["caller_rollback", "audit_failure"])
def test_projection_and_result_audit_rollback_together(fact, operator, monkeypatch, mode):
    isolate(fact)
    args = command(fact, operator)
    initial = count(fact.db, AuditEvent)
    before = stock_snapshot(fact)
    if mode == "audit_failure":
        def fail(*a, **k):
            raise AuditChainHeadNotFound("test audit failure")
        monkeypatch.setattr(service, "append_audit_event", fail)
        with pytest.raises(AuditChainHeadNotFound):
            service.recheck_inventory_notification_source(fact.db, **args)
    else:
        assert service.recheck_inventory_notification_source(fact.db, **args).item.status == "projected"
    fact.db.rollback()
    assert count(fact.db, NotificationEvent) == 0 and count(fact.db, AuditEvent) == initial
    assert service.list_inventory_notification_sources(fact.db, actor=operator).items[0].status == "blocked"
    assert stock_snapshot(fact) == before


@pytest.mark.parametrize("actor_kind", ["missing_permission", "regional_admin", "restricted", "test_double"])
def test_both_services_require_real_national_operator(fact, operator, actor_kind):
    isolate(fact)
    args = command(fact, operator)
    if actor_kind == "missing_permission":
        actor = replace(operator, entitlements=())
    elif actor_kind == "regional_admin":
        actor = replace(operator, assignments=tuple(replace(row, scope_type="organization", scope_id=str(fact.world.organization.id)) for row in operator.assignments))
    elif actor_kind == "restricted":
        actor = replace(operator, access_mode="restricted_handover")
    else:
        actor = SimpleNamespace(user_id=operator.user_id, allows=lambda *a, **k: True)
    with pytest.raises(service.OperationsError) as read:
        service.list_inventory_notification_sources(fact.db, actor=actor)
    with pytest.raises(service.OperationsError) as write:
        service.recheck_inventory_notification_source(fact.db, **dict(args, actor=actor))
    assert read.value.http_status_code == write.value.http_status_code == 403
    assert count(fact.db, NotificationEvent) == 0


def test_write_reloads_revoked_authority_despite_old_request_principal(fact, operator):
    isolate(fact)
    args = command(fact, operator)
    fact.world.headquarters_assignment.status = "revoked"
    fact.world.headquarters_assignment.revoked_at = fact.now
    fact.world.headquarters_assignment.revoked_by = operator.user_id
    fact.db.commit()
    with pytest.raises(service.OperationsError) as exc:
        service.recheck_inventory_notification_source(fact.db, **args)
    assert exc.value.http_status_code == 403 and count(fact.db, NotificationEvent) == 0


def test_failed_projection_savepoint_keeps_only_redacted_conflict_audit(fact, operator, monkeypatch):
    isolate(fact)
    args = command(fact, operator)
    def partially_project(db, **kwargs):
        db.add(NotificationEvent(event_type="test", business_type="test", business_id=str(uuid4()),
            dedup_key=f"partial-{uuid4()}", payload_jsonb={"private": "do-not-copy"},
            status="pending", occurred_at=fact.now))
        db.flush()
        raise service.NotificationEventError("do-not-copy")
    monkeypatch.setattr(service, "project_inventory_notification", partially_project)
    result = service.recheck_inventory_notification_source(fact.db, **args)
    fact.db.commit()
    assert result.item.code == failures.EVENT_CONFLICT and result.item.status == "blocked"
    assert count(fact.db, NotificationEvent) == 0
    assert "do-not-copy" not in str(fact.db.get(AuditEvent, result.item.latest_audit_id).after_jsonb)


def test_read_permission_does_not_authorize_recheck(fact, operator):
    isolate(fact)
    reader = replace(operator, entitlements=tuple(row for row in operator.entitlements if row.action == "read"))
    assert len(service.list_inventory_notification_sources(fact.db, actor=reader).items) == 1
    with pytest.raises(service.OperationsError) as exc:
        service.recheck_inventory_notification_source(fact.db, **command(fact, reader))
    assert exc.value.http_status_code == 403 and count(fact.db, NotificationEvent) == 0


@pytest.mark.parametrize("lock", ["source", "transaction"])
def test_owned_objects_defer_without_result_audit_or_notification(fact, operator, monkeypatch, lock):
    isolate(fact)
    args = command(fact, operator)
    before = count(fact.db, AuditEvent)
    if lock == "source":
        monkeypatch.setattr(failures, "try_source_lock", lambda *a: False)
    else:
        monkeypatch.setattr(service, "project_inventory_notification", lambda *a, **k: None)
    with pytest.raises(service.OperationsError) as exc:
        service.recheck_inventory_notification_source(fact.db, **args)
    assert exc.value.http_status_code == 409
    assert count(fact.db, AuditEvent) == before and count(fact.db, NotificationEvent) == 0


def test_list_keyset_pagination_survives_later_rechecks(fact, operator):
    older = isolate(fact)
    bad = broken_source(fact)
    newer = isolate(fact, bad.id)
    page = service.list_inventory_notification_sources(fact.db, actor=operator, limit=1)
    assert page.items[0].failure_audit_id == newer.audit_id and page.next_after_id == newer.audit_id
    service.recheck_inventory_notification_source(fact.db, **command(fact, operator))
    fact.db.commit()
    next_page = service.list_inventory_notification_sources(fact.db, actor=operator, limit=1, after_id=page.next_after_id)
    assert next_page.items[0].failure_audit_id == older.audit_id and next_page.next_after_id is None
    with pytest.raises(service.OperationsError):
        service.list_inventory_notification_sources(fact.db, actor=operator, after_id=uuid4())


def test_http_routes_keep_private_headers_validate_commands_and_replay(fact, operator):
    isolate(fact)
    app = FastAPI()
    app.middleware("http")(block_legacy_prototype_writes)
    app.include_router(formal_notifications.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: fact.db
    app.dependency_overrides[get_formal_principal] = lambda: operator
    path = "/api/v1/notifications/inventory-sources"
    with TestClient(app) as client:
        listed = client.get(path)
        assert listed.status_code == 200 and listed.headers["Cache-Control"] == "private, no-store, max-age=0"
        item = listed.json()["items"][0]
        body = {"expected_audit_id": item["latest_audit_id"], "expected_source_sha256": item["source_sha256"], "reason": "原对象复核"}
        target = f"{path}/{item['outbox_id']}/recheck"
        missing = client.post(target, json=body)
        assert missing.status_code == 400 and missing.headers["Cache-Control"] == "private, no-store, max-age=0"
        headers = {"Idempotency-Key": "http-source-recheck-0001", "X-Request-ID": "http-source-trace-0001"}
        rejected = client.post(target, json=dict(body, force=True), headers=headers)
        assert rejected.status_code == 422
        first = client.post(target, json=body, headers=headers)
        assert first.status_code == 200 and first.json()["item"]["status"] == "projected"
        second = client.post(target, json=body, headers=headers)
        assert second.status_code == 200 and second.headers["Idempotency-Replayed"] == "true"
        assert second.json()["item"] == first.json()["item"]
        assert count(fact.db, NotificationDelivery) == 0
        app.dependency_overrides[get_formal_principal] = lambda: replace(operator, entitlements=())
        for response in (client.get(path), client.post(target, json=body, headers=headers)):
            assert response.status_code == 403 and response.headers["Cache-Control"] == "private, no-store, max-age=0"
