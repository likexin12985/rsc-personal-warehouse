"""Database-backed tests for exact historical work-order evidence.

SQLite verifies service/HTTP contracts; it is not a PG16 concurrency claim.
"""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from test_inventory_posting import world, NOW, make_account
from app.demand_models import OamWorkOrder, WorkOrderMaterialOperation, WorkOrderMaterialSerial
from app.foundation_models import AuditChainHead, AuditEvent, ExternalObject, OutboxEvent
from app.inventory_models import (InventoryTransaction, InventoryMovement, InventoryMovementSerial,
                                  InventorySerial, StockLocation)
from app.formal_services import work_order_material as service
from app.routers import formal_work_order_material as api
from app.database import Base, get_db


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def enable_fks(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def evidence(db, world):
    world.current_principal = replace(world.principal, entitlements=world.principal.entitlements + tuple(
        replace(world.principal.entitlements[0], resource="work_order_material", action=action)
        for action in ("read", "operate")))
    db.add(AuditChainHead(id=uuid4(), stream_key="material_request",
                          last_event_id=None, last_hash=None, version=0))
    db.flush()
    account = make_account(db, organization=world.organization, material=world.material,
                           custodian=world.person, established=False)
    account.availability_bucket = "reserved"
    location = db.get(StockLocation, account.location_id)
    parent = StockLocation(id=uuid4(), code=str(uuid4()), name="区域仓",
                           location_type="region", owner_org_id=world.organization.id, status="active")
    db.add(parent)
    db.flush()
    location.parent_id = parent.id
    location.location_type = "personal"
    db.flush()
    external = ExternalObject(id=uuid4(), source_system_id=world.source.id,
                              entity_type="work_order", external_id=str(uuid4()))
    db.add(external)
    db.flush()
    order = OamWorkOrder(id=uuid4(), external_object_id=external.id, work_order_no=str(uuid4()),
                         organization_id=world.organization.id, engineer_person_id=world.person.id,
                         status="active", source_updated_at=NOW)
    db.add(order)
    transaction = InventoryTransaction(
        id=uuid4(), transaction_no=str(uuid4()), movement_type="consume",
        source_document_type="work_order_material", source_document_id=str(order.id),
        posting_key=str(uuid4()), idempotency_key_hash="e" * 64, request_hash="d" * 64,
        status="posted", effective_at=NOW, posted_at=NOW, ledger_cursor=100,
        actor_user_id=world.user.id)
    db.add(transaction)
    db.flush()
    movement = InventoryMovement(id=uuid4(), transaction_id=transaction.id, line_no=1,
                                 from_account_id=account.id, to_account_id=None,
                                 external_boundary_code="consumed", quantity=Decimal("1"))
    db.add(movement)
    db.commit()
    return SimpleNamespace(world=world, order=order, transaction=transaction, movement=movement,
                           account=account, location=location,
                           line=service.WorkOrderMaterialLineInput(world.material.id, account.id, Decimal("1")))


def record(db, evidence, **overrides):
    args = dict(actor=evidence.world.current_principal, operation_type="consume",
                work_order_id=evidence.order.id, operator_person_id=evidence.world.person.id,
                lines=(evidence.line,), posting_transaction_id=evidence.transaction.id,
                idempotency_key="work-order-evidence-1")
    args.update(overrides)
    return service.record_posted_operation(db, **args)


def count_operations(db):
    return db.scalar(select(func.count()).select_from(WorkOrderMaterialOperation))


@pytest.mark.parametrize("field,value", [
    ("source_document_type", "unrelated_transfer"),
    ("source_document_id", str(uuid4())),
])
def test_rejects_unrelated_posted_transaction(db, evidence, field, value):
    setattr(evidence.transaction, field, value)
    db.flush()
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence)
    assert exc.value.code == "posting_origin_mismatch"
    assert count_operations(db) == 0


def test_rejects_transaction_posted_by_other_actor(db, evidence):
    evidence.transaction.actor_user_id = evidence.world.headquarters_reviewer_user.id
    db.flush()
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence)
    assert exc.value.code == "posting_origin_mismatch"
    assert count_operations(db) == 0


@pytest.mark.parametrize("quantity", [Decimal("2"), Decimal("0.5")])
def test_requested_quantity_must_match_posted_movement(db, evidence, quantity):
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence, lines=(replace(evidence.line, quantity=quantity),))
    assert exc.value.code == "posting_lines_mismatch"
    assert count_operations(db) == 0


def test_personal_custodian_on_regional_location_is_not_a_personal_warehouse(db, evidence):
    evidence.location.location_type = "region"
    db.flush()
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence)
    assert exc.value.code == "posting_account_mismatch"


def test_replay_after_order_closes_and_new_key_cannot_duplicate_fact(db, evidence):
    first = record(db, evidence)
    db.commit()
    evidence.order.status = "closed"
    db.commit()
    second = record(db, evidence)
    assert first.id == second.id
    assert count_operations(db) == 1
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence, idempotency_key="different-key")
    assert exc.value.code == "posting_already_bound"
    assert count_operations(db) == 1


def serial_evidence(db, evidence):
    serial = InventorySerial(id=uuid4(), material_id=evidence.world.material.id,
                             serial_no="SN-EXACT-1", qr_code="QR-EXACT-1", lifecycle_status="consumed")
    db.add(serial)
    db.flush()
    db.add(InventoryMovementSerial(movement_id=evidence.movement.id,
                                  transaction_id=evidence.transaction.id, serial_id=serial.id))
    db.commit()
    proof = service.SerialVerificationInput(serial.id, evidence.world.material.sku_code,
                                            serial.serial_no, serial.qr_code)
    return serial, replace(evidence.line, serial_ids=(serial.id,), serial_verifications=(proof,))


def test_serial_history_survives_consumption_without_current_position(db, evidence):
    serial, line = serial_evidence(db, evidence)
    operation = record(db, evidence, lines=(line,))
    db.commit()
    proof = db.scalar(select(WorkOrderMaterialSerial))
    assert proof.serial_id == serial.id
    assert proof.sku_verified and proof.qr_verified
    assert record(db, evidence, lines=(line,)).id == operation.id


@pytest.mark.parametrize("changed", ["sku_code", "serial_no", "qr_code"])
def test_rejects_incorrect_scan_without_writing_true_flags(db, evidence, changed):
    _, line = serial_evidence(db, evidence)
    line = replace(line, serial_verifications=(replace(line.serial_verifications[0], **{changed: "wrong"}),))
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence, lines=(line,))
    assert exc.value.code == "serial_verification_mismatch"
    assert count_operations(db) == 0


def test_uuid_without_scan_is_not_three_code_verification(db, evidence):
    _, line = serial_evidence(db, evidence)
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence, lines=(replace(line, serial_verifications=()),))
    assert exc.value.code == "serial_verification_missing"
    assert count_operations(db) == 0


def test_arbitrary_replacement_uuids_do_not_create_replacement_fact(db, evidence):
    with pytest.raises(service.WorkOrderMaterialPreflightError) as exc:
        record(db, evidence, replacement_pairs=(service.WorkOrderReplacementPairInput(uuid4(), uuid4()),))
    assert exc.value.code == "replacement_evidence_required"
    assert count_operations(db) == 0


def client_for(db, evidence):
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal":
                app.dependency_overrides[dependency.call] = lambda: evidence.world.current_principal
    return TestClient(app)


def test_history_checks_object_scope_even_with_route_permission(db, evidence):
    evidence.world.current_principal = replace(evidence.world.current_principal,
        entitlements=tuple(replace(e, scope_type="person", scope_id=str(uuid4()))
                           for e in evidence.world.current_principal.entitlements))
    with client_for(db, evidence) as client:
        response = client.get(f"/api/v1/work-orders/{evidence.order.id}/material-operations")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "work_order_forbidden"


def test_authorized_history_missing_order_and_cache_policy(db, evidence):
    with client_for(db, evidence) as client:
        path = f"/api/v1/work-orders/{evidence.order.id}/material-operations"
        response = client.get(path)
        missing = client.get(f"/api/v1/work-orders/{uuid4()}/material-operations")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.headers["cache-control"] == "private, no-store"
    assert missing.status_code == 404


def test_http_rejects_unrelated_transaction_and_rolls_back(db, evidence):
    evidence.transaction.source_document_type = "unrelated"
    db.commit()
    with client_for(db, evidence) as client:
        response = client.post(f"/api/v1/work-orders/{evidence.order.id}/material-operations", json={
            "operator_person_id": str(evidence.world.person.id), "operation_type": "consume",
            "posting_transaction_id": str(evidence.transaction.id), "idempotency_key": "test-key",
            "lines": [{"material_id": str(evidence.world.material.id), "stock_account_id": str(evidence.account.id),
                       "quantity": "1"}]})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "posting_origin_mismatch"
    assert count_operations(db) == 0


def payload_for(evidence):
    return {
        "operator_person_id": str(evidence.world.person.id), "operation_type": "consume",
        "posting_transaction_id": str(evidence.transaction.id), "idempotency_key": "http-key",
        "lines": [{"material_id": str(evidence.world.material.id), "stock_account_id": str(evidence.account.id),
                   "quantity": "1"}],
    }


def assert_history_head_matches(history_item, operation):
    assert {key: value for key, value in history_item.items() if key != "lines"} == operation


def test_http_successful_scan_and_replay(db, evidence):
    serial, line = serial_evidence(db, evidence)
    payload = payload_for(evidence)
    payload["lines"][0].update(serial_ids=[str(serial.id)], serial_verifications=[{
        "serial_id": str(serial.id), "sku_code": line.serial_verifications[0].sku_code,
        "serial_no": serial.serial_no, "qr_code": serial.qr_code}])
    with client_for(db, evidence) as client:
        path = f"/api/v1/work-orders/{evidence.order.id}/material-operations"
        first = client.post(path, json=payload)
        second = client.post(path, json=payload)
        history = client.get(path)
    assert first.status_code == 200, first.text
    assert second.json() == first.json()
    history_item = history.json()["items"][0]
    assert_history_head_matches(history_item, first.json())
    assert history_item["lines"][0]["material_id"] == str(evidence.world.material.id)
    assert history_item["lines"][0]["serial_ids"] == [str(serial.id)]
    assert count_operations(db) == 1


def test_posted_operation_appends_audit_and_outbox_once(db, evidence):
    first = record(db, evidence)
    db.commit()
    audit_rows = db.scalars(select(AuditEvent).where(
        AuditEvent.aggregate_type == "work_order_material_operation",
        AuditEvent.aggregate_id == str(first.id),
    )).all()
    outbox_rows = db.scalars(select(OutboxEvent).where(
        OutboxEvent.aggregate_type == "work_order_material_operation",
        OutboxEvent.aggregate_id == str(first.id),
    )).all()
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "work_order_material.consume"
    assert audit_rows[0].after_jsonb["posting_transaction_id"] == str(evidence.transaction.id)
    assert len(outbox_rows) == 1
    assert outbox_rows[0].event_type == "work_order_material_operation_posted"
    replay = record(db, evidence)
    db.commit()
    assert replay.id == first.id
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1


def test_http_late_database_failure_rolls_back_operation_fact(db, evidence, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    original = service.record_posted_operation
    def fail_after_append(*args, **kwargs):
        original(*args, **kwargs)
        raise SQLAlchemyError("synthetic late database failure")
    monkeypatch.setattr(service, "record_posted_operation", fail_after_append)
    with client_for(db, evidence) as client:
        response = client.post(f"/api/v1/work-orders/{evidence.order.id}/material-operations",
                               json=payload_for(evidence))
    assert response.status_code == 503
    assert "synthetic" not in response.text
    assert count_operations(db) == 0


def test_revoked_or_stale_actor_cannot_replay_existing_evidence(db, evidence):
    first = record(db, evidence)
    db.commit()
    actor = evidence.world.current_principal
    evidence.world.current_principal = replace(actor, authorization_version=actor.authorization_version + 1)
    with pytest.raises(service.InventoryPostingError) as exc:
        record(db, evidence, actor=actor)
    assert exc.value.code == "actor_principal_stale"
    assert count_operations(db) == 1


def test_atomic_consume_composes_posting_and_fact_in_one_session(db, evidence, monkeypatch):
    calls = []
    posted = SimpleNamespace(transaction_id=uuid4())
    operation = SimpleNamespace(id=uuid4())
    def fake_post(db, **kwargs):
        calls.append(("inventory", kwargs["command"]))
        return posted
    def fake_record(db, **kwargs):
        calls.append(("fact", kwargs["posting_transaction_id"]))
        assert kwargs["posting_transaction_id"] == posted.transaction_id
        return operation
    monkeypatch.setattr(service, "post_inventory_transaction", fake_post)
    monkeypatch.setattr(service, "record_posted_operation", fake_record)
    result, transaction = service.execute_consume_operation(
        db, actor=evidence.world.current_principal, work_order_id=evidence.order.id,
        lines=(evidence.line,), idempotency_key="atomic-consume-1", request_id="consume-request-1",
    )
    assert result is operation and transaction is posted
    assert calls[0][1].movement_type == "consume"
    assert calls == [("inventory", calls[0][1]), ("fact", posted.transaction_id)]


def test_atomic_recover_posts_external_to_personal_available(db, evidence, monkeypatch):
    target = SimpleNamespace(id=uuid4(), material_id=evidence.world.material.id,
                             custodian_person_id=evidence.world.person.id,
                             availability_bucket="available", condition_code="used", location_id=uuid4())
    location = SimpleNamespace(id=target.location_id, location_type="personal",
                               custodian_person_id=evidence.world.person.id, status="active")
    monkeypatch.setattr(service, "authorize_work_order",
                        lambda *args, **kwargs: (evidence.order, evidence.world.current_principal))
    monkeypatch.setattr(db, "get", lambda model, key, **kwargs: target if model.__name__ == "StockAccount" else location)
    posted = SimpleNamespace(transaction_id=uuid4())
    operation = SimpleNamespace(id=uuid4())
    calls = []
    monkeypatch.setattr(service, "post_inventory_transaction",
                        lambda db, **kwargs: (calls.append(kwargs["command"]) or posted))
    def fake_record(db, **kwargs):
        calls.append(kwargs["operation_type"])
        assert kwargs["lines"][0].stock_account_id == target.id
        assert kwargs["lines"][0].target_stock_account_id is None
        return operation
    monkeypatch.setattr(service, "record_posted_operation", fake_record)
    line = replace(evidence.line, target_stock_account_id=target.id, condition_before="used")
    result, transaction = service.execute_recover_operation(
        db, actor=evidence.world.current_principal, work_order_id=evidence.order.id,
        lines=(line,), idempotency_key="atomic-recover-1", request_id="recover-request-1")
    assert result is operation and transaction is posted
    assert calls[0].movement_type == "inbound"
    assert calls[0].movements[0].from_account_id is None
    assert calls[0].movements[0].to_account_id == target.id
    assert calls[1] == "recover"


@pytest.mark.parametrize("case,code", [
    ("new", "recover_condition_invalid"),
    ("scrapped", "recover_condition_invalid"),
    ("mismatched_condition", "recover_target_invalid"),
    ("inactive_location", "recover_target_invalid"),
    ("another_custodian", "recover_target_invalid"),
    ("reserved_target", "recover_target_invalid"),
])
def test_recover_rejects_invalid_destination_before_posting(db, evidence, monkeypatch, case, code):
    evidence.account.condition_code = "used"
    evidence.account.availability_bucket = "available"
    condition = "used"
    if case in {"new", "scrapped"}:
        condition = case
    elif case == "mismatched_condition":
        evidence.account.condition_code = "damaged"
    elif case == "inactive_location":
        evidence.location.status = "inactive"
    elif case == "another_custodian":
        evidence.account.custodian_person_id = None
    else:
        evidence.account.availability_bucket = "reserved"
    db.flush()
    def must_not_post(*args, **kwargs):
        pytest.fail("invalid return destination reached inventory posting")
    monkeypatch.setattr(service, "post_inventory_transaction", must_not_post)
    line = replace(evidence.line, condition_before=condition,
                   target_stock_account_id=evidence.account.id)
    with pytest.raises(service.WorkOrderMaterialPreflightError) as error:
        service.execute_recover_operation(
            db, actor=evidence.world.current_principal, work_order_id=evidence.order.id,
            lines=(line,), idempotency_key="invalid-recover", request_id="invalid-recover-trace")
    assert error.value.code == code
    assert count_operations(db) == 0


@pytest.mark.parametrize("condition", ["used", "damaged"])
def test_http_recover_binds_real_fact_to_seeded_return_evidence(db, evidence, monkeypatch, condition):
    # Only inventory posting is stubbed with persisted evidence. Authorization,
    # account validation, fact recording, audit/outbox and HTTP commit are real.
    evidence.account.condition_code = condition
    evidence.account.availability_bucket = "available"
    evidence.transaction.movement_type = "inbound"
    evidence.movement.from_account_id = None
    evidence.movement.to_account_id = evidence.account.id
    db.commit()
    def seeded_post(db, **kwargs):
        command = kwargs["command"]
        assert command.movement_type == "inbound"
        assert command.movements[0].from_account_id is None
        assert command.movements[0].to_account_id == evidence.account.id
        return SimpleNamespace(transaction_id=evidence.transaction.id)
    monkeypatch.setattr(service, "post_inventory_transaction", seeded_post)
    payload = {
        "operator_person_id": str(evidence.world.person.id),
        "idempotency_key": "recover-http-fact", "request_id": "recover-http-fact-trace",
        "lines": [{"material_id": str(evidence.world.material.id),
                   "target_stock_account_id": str(evidence.account.id),
                   "quantity": "1", "condition_before": condition}],
    }
    with client_for(db, evidence) as client:
        path = f"/api/v1/work-orders/{evidence.order.id}/material-operations/recover"
        first = client.post(path, json=payload)
        replay = client.post(path, json=payload)
        history = client.get(f"/api/v1/work-orders/{evidence.order.id}/material-operations")
    assert first.status_code == 200, first.text
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    history_item = history.json()["items"][0]
    assert_history_head_matches(history_item, first.json())
    assert history_item["lines"][0]["condition_before"] == condition
    assert first.json()["operation_type"] == "recover"
    assert first.json()["posting_transaction_id"] == str(evidence.transaction.id)
    assert count_operations(db) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "work_order_material.recover")) == 1
    assert db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.event_type == "work_order_material_operation_posted")) == 1


def test_generic_evidence_endpoint_cannot_bypass_recover_condition(db, evidence):
    evidence.transaction.movement_type = "inbound"
    evidence.movement.from_account_id = None
    evidence.movement.to_account_id = evidence.account.id
    evidence.account.availability_bucket = "available"
    db.commit()
    payload = payload_for(evidence)
    payload["operation_type"] = "recover"
    payload["lines"][0]["condition_before"] = "new"
    with client_for(db, evidence) as client:
        response = client.post(f"/api/v1/work-orders/{evidence.order.id}/material-operations", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "recover_condition_invalid"
    assert count_operations(db) == 0


@pytest.fixture(params=["consume", "occupy", "release", "recover"])
def replay_command(db, evidence, monkeypatch, request):
    """Seed the first ledger result; every subsequent replay uses the real ledger service."""
    from app.formal_services import inventory_posting as inventory
    from app.inventory_models import StockAccount

    operation_type = request.param
    account = evidence.account
    account.availability_bucket = "available" if operation_type in {"occupy", "recover"} else "reserved"
    account.condition_code = "used" if operation_type == "recover" else "new"
    target = None
    if operation_type in {"occupy", "release"}:
        target = StockAccount(
            id=uuid4(), owner_org_id=account.owner_org_id, location_id=account.location_id,
            custodian_person_id=account.custodian_person_id, material_id=account.material_id,
            condition_code=account.condition_code, lot_id=account.lot_id,
            availability_bucket="reserved" if operation_type == "occupy" else "available",
        )
        db.add(target)
    elif operation_type == "recover":
        target = account
    db.flush()
    line = replace(evidence.line, condition_before=account.condition_code,
                   target_stock_account_id=target.id if target else None)
    execute = getattr(service, f"execute_{operation_type}_operation")
    def seeded_post(db, *, actor, command, idempotency_key, request_id, **kwargs):
        command = inventory._validate_posting_command(command)
        row = evidence.transaction
        row.transaction_no = command.transaction_no
        row.movement_type = command.movement_type
        row.posting_key = command.posting_key
        row.idempotency_key_hash = inventory._storage_hash(idempotency_key)
        row.request_hash = inventory._posting_request_hash(actor, command)
        row.effective_at = command.effective_at
        movement = command.movements[0]
        evidence.movement.from_account_id = movement.from_account_id
        evidence.movement.to_account_id = movement.to_account_id
        evidence.movement.external_boundary_code = movement.external_boundary_code
        db.flush()
        return inventory.InventoryPostingResult(transaction_id=row.id,
            transaction_no=row.transaction_no, ledger_cursor=row.ledger_cursor)
    monkeypatch.setattr(service, "post_inventory_transaction", seeded_post)
    key = f"ledger-replay-{operation_type}"
    first, _ = execute(db, actor=evidence.world.current_principal, work_order_id=evidence.order.id,
                       lines=(line,), idempotency_key=key, request_id="ledger-first-request")
    db.commit()
    monkeypatch.setattr(service, "post_inventory_transaction", inventory.post_inventory_transaction)
    return SimpleNamespace(execute=execute, line=line, key=key, first=first,
                           evidence=evidence, operation_type=operation_type)


def run_replay(db, value, **overrides):
    params = dict(actor=value.evidence.world.current_principal, work_order_id=value.evidence.order.id,
                  lines=(value.line,), idempotency_key=value.key, request_id="ledger-replay-request")
    params.update(overrides)
    return value.execute(db, **params)


def test_real_ledger_replay_uses_original_time_even_after_work_order_closes(db, replay_command, monkeypatch):
    from datetime import datetime, timedelta, timezone
    value = replay_command
    original_time = value.evidence.transaction.effective_at
    value.evidence.order.status = "closed"
    db.commit()
    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (original_time.replace(tzinfo=timezone.utc) + timedelta(days=1)).astimezone(tz)
    monkeypatch.setattr(service, "datetime", LaterClock)
    fact, posting = run_replay(db, value)
    assert fact.id == value.first.id
    assert posting.replayed is True
    assert posting.transaction_id == value.evidence.transaction.id
    assert count_operations(db) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1


def test_real_ledger_replay_still_rejects_changed_quantity(db, replay_command):
    changed = replace(replay_command.line, quantity=Decimal("2"))
    with pytest.raises(service.InventoryPostingError) as error:
        run_replay(db, replay_command, lines=(changed,))
    assert error.value.code == "idempotency_key_conflict"
    assert count_operations(db) == 1


def test_real_ledger_replay_rechecks_work_order_permission(db, replay_command):
    world = replay_command.evidence.world
    world.current_principal = replace(world.current_principal, entitlements=tuple(
        entry for entry in world.current_principal.entitlements if entry.resource != "work_order_material"))
    with pytest.raises(service.InventoryPostingError) as error:
        run_replay(db, replay_command)
    assert error.value.http_status_code == 403
    assert count_operations(db) == 1


def test_work_order_replay_does_not_require_broad_inventory_post_permission(db, replay_command):
    world = replay_command.evidence.world
    world.current_principal = replace(world.current_principal, entitlements=tuple(
        entry for entry in world.current_principal.entitlements
        if not (entry.resource == "inventory_transaction" and entry.action == "post")))
    fact, posting = run_replay(db, replay_command)
    assert fact.id == replay_command.first.id
    assert posting.replayed is True


def test_closed_work_order_cannot_start_a_new_key(db, replay_command, monkeypatch):
    replay_command.evidence.order.status = "closed"
    db.commit()
    def must_not_post(*args, **kwargs):
        pytest.fail("new command on closed work order reached inventory posting")
    monkeypatch.setattr(service, "post_inventory_transaction", must_not_post)
    with pytest.raises(service.WorkOrderMaterialPreflightError) as error:
        run_replay(db, replay_command, idempotency_key="closed-order-new-key")
    assert error.value.code == "work_order_inactive"
    assert count_operations(db) == 1


@pytest.mark.parametrize("condition", ["used", "damaged"])
def test_recover_real_ledger_updates_balance_and_replays(db, world, monkeypatch, condition):
    """Real SQLite inventory posting from a reviewed opening graph, without posting stubs."""
    from app.inventory_models import StockBalance
    from test_formal_inventory_established_read import _make_personal_account
    from test_inventory_posting import establish_account_for_posting

    account, location, _ = _make_personal_account(db, world, established=False)
    account.condition_code = condition
    db.flush()
    establish_account_for_posting(db, world, account)
    world.current_principal = replace(world.principal, entitlements=world.principal.entitlements + tuple(
        replace(world.principal.entitlements[0], resource="work_order_material", action=action)
        for action in ("read", "operate")))
    db.add(AuditChainHead(id=uuid4(), stream_key="material_request",
                         last_event_id=None, last_hash=None, version=0))
    external = ExternalObject(id=uuid4(), source_system_id=world.source.id,
                              entity_type="work_order", external_id=str(uuid4()))
    db.add(external)
    db.flush()
    order = OamWorkOrder(id=uuid4(), external_object_id=external.id, work_order_no=str(uuid4()),
                         organization_id=world.organization.id, engineer_person_id=world.person.id,
                         status="active", source_updated_at=NOW)
    db.add(order)
    db.commit()
    args = dict(actor=world.current_principal, work_order_id=order.id,
                lines=(service.WorkOrderMaterialLineInput(
                    material_id=world.material.id, stock_account_id=account.id,
                    target_stock_account_id=account.id, condition_before=condition, quantity=Decimal("1")),),
                idempotency_key="real-recover-key", request_id="real-recover-trace")
    first, first_post = service.execute_recover_operation(db, **args)
    db.commit()
    assert db.get(StockBalance, account.id).quantity == Decimal("1")
    assert db.get(InventoryTransaction, first_post.transaction_id).movement_type == "inbound"
    replay, replay_post = service.execute_recover_operation(db, **args)
    db.commit()
    assert replay.id == first.id
    assert replay_post.transaction_id == first_post.transaction_id
    assert replay_post.replayed is True
    assert db.get(StockBalance, account.id).quantity == Decimal("1")
    assert count_operations(db) == 1

    # A late business-audit failure occurs AFTER real inventory posting; the
    # HTTP transaction must roll back the ledger, balance and all events.
    from sqlalchemy.exc import SQLAlchemyError
    counts_before = tuple(db.scalar(select(func.count()).select_from(model))
                          for model in (InventoryTransaction, InventoryMovement, AuditEvent, OutboxEvent))
    def fail_business_audit(*args, **kwargs):
        raise SQLAlchemyError("injected after inventory posting")
    monkeypatch.setattr(service, "append_audit_event", fail_business_audit)
    with client_for(db, SimpleNamespace(world=world)) as client:
        response = client.post(f"/api/v1/work-orders/{order.id}/material-operations/recover", json={
            "operator_person_id": str(world.person.id),
            "idempotency_key": "second-recover-key", "request_id": "second-recover-trace",
            "lines": [{"material_id": str(world.material.id), "target_stock_account_id": str(account.id),
                       "quantity": "1", "condition_before": condition}],
        })
    assert response.status_code == 503, response.text
    assert db.get(StockBalance, account.id).quantity == Decimal("1")
    assert count_operations(db) == 1
    assert tuple(db.scalar(select(func.count()).select_from(model))
                 for model in (InventoryTransaction, InventoryMovement, AuditEvent, OutboxEvent)) == counts_before
