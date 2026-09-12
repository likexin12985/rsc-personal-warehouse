from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from app.formal_access import load_formal_principal
from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent, FileObject, OutboxEvent, Permission, Role, RolePermission
from app.inventory_models import InventoryTransaction, Receipt, ReceiptLine, ReceiptSerial, ShipmentSerial, StockBalance
from app.formal_services import material_request_my_receipt as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.material_request_my_receipt_schemas import MyReceiptIn
from test_material_request_my_receiving import receiving_world, outbound_world
from test_material_request_draft_service import SECRET
from test_formal_material_request_api import api_client
from test_formal_files_service import FakeStorage

pytest_plugins = ("test_material_request_picking",)


@pytest.fixture
def world(receiving_world):
    db, actor, request, location, shipment, line = receiving_world
    permission = Permission(id=uuid4(), resource="material_request", action="receive", field_code="", description="test recipient grant")
    db.add(permission)
    db.flush()
    db.add(RolePermission(role_id=db.scalar(select(Role.id).where(Role.code == "technician")), permission_id=permission.id, effect="allow"))
    db.flush()
    return db, load_formal_principal(db, actor.user_id), request, location, shipment, line


def payload(world, **changes):
    db, _, request, _, shipment, line = world
    serials = tuple(db.scalars(select(ShipmentSerial.serial_id).where(ShipmentSerial.shipment_line_id == line.id)).all())
    value = dict(expected_request_version=request.version, shipment_id=shipment.id, received_at="2026-09-10T10:00:00+08:00",
                 lines=[dict(shipment_line_id=line.id, accepted_qty=str(line.shipped_qty), rejected_qty="0.000", condition="normal", accepted_serial_ids=serials)])
    value.update(changes)
    return MyReceiptIn(**value)


def create(world, value=None, key="my-receipt-test-command-001", actor=None):
    db, current, request, *_ = world
    return service.create_my_receipt(db, actor=actor or current, request_id=request.id, payload=value or payload(world), idempotency_key=key, secret=SECRET, trace_request_id=f"trace-{key}")


def recover(world, key="my-receipt-test-command-001", actor=None):
    db, current, request, *_ = world
    return service.my_receipt_command_status(db, actor=actor or current, request_id=request.id, idempotency_key=key, secret=SECRET)


def facts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (Receipt, ReceiptLine, ReceiptSerial, AuditEvent, OutboxEvent, InventoryTransaction, MaterialRequestCommand))


def evidence(world, key="receipt-exception-file-test-001", *, purpose="receipt_exception_evidence", complete=True):
    from app.formal_services import formal_files
    db, actor, *_ = world
    storage = FakeStorage()
    created = formal_files.create_file_upload_intent(db, actor=actor,
        command=formal_files.FileUploadIntentInput(purpose=purpose, original_filename="exception.png", size_bytes=10, mime_type="image/png", sha256="a"*64),
        idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id=f"trace-{key}", storage=storage, upload_ttl_seconds=600)
    file = db.get(FileObject, created.file_id)
    if complete:
        storage.materialize(file)
        formal_files.complete_file_upload(db, actor=actor, file_id=file.id, trace_request_id=f"complete-{key}", storage=storage)
    return file, storage


def test_recipient_accepts_once_without_stock_posting_or_source_permissions(world):
    db, actor, request, *_ = world
    value = payload(world)
    balances = tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity).order_by(StockBalance.stock_account_id)))
    transactions = db.scalar(select(func.count()).select_from(InventoryTransaction))
    result = create(world, value)
    assert result.person_id == actor.person_id and not result.idempotency_replayed
    assert result.request_hash == service.request_hash(request.id, actor.person_id, value)
    versions = tuple(db.scalars(select(MaterialRequestCommand.target_version).where(MaterialRequestCommand.request_id == request.id).order_by(MaterialRequestCommand.target_version)).all())
    assert versions == tuple(range(request.version + 1))
    stable = facts(db)
    assert create(world, value).idempotency_replayed
    assert recover(world).receipt_id == result.receipt_id
    assert facts(db) == stable
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == transactions
    assert tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity).order_by(StockBalance.stock_account_id))) == balances
    with pytest.raises(MaterialRequestReadError):
        create(world, value, key="my-receipt-second-command-001")
    with pytest.raises(MaterialRequestReadError, match="其他验收"):
        create(world, value.model_copy(update={"received_at": "2026-09-11T10:00:00+08:00"}))


def test_trace_recovers_original_receipt_readonly_without_write_key(world):
    db, actor, request, *_ = world
    result = create(world)
    statements = []
    def record(_conn, _cursor, statement, *_args): statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        recovered = service.my_receipt_trace_status(db, actor=actor, request_id=request.id,
            trace_request_id="trace-my-receipt-test-command-001")
        assert recovered.receipt_id == result.receipt_id and recovered.request_hash == result.request_hash
        assert service.my_receipt_trace_status(db, actor=actor, request_id=request.id,
            trace_request_id="trace-not-observed-001") is None
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_trace_lookup_rejects_broken_original_audit(world):
    db, actor, request, *_ = world
    result = create(world)
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "my_receipt_registered"))
    original_id = audit.aggregate_id
    audit.aggregate_id = str(uuid4()); db.flush()
    with pytest.raises(MaterialRequestReadError, match="事实不存在"):
        service.my_receipt_trace_status(db, actor=actor, request_id=request.id, trace_request_id=audit.request_id)
    audit.aggregate_id = original_id
    audit.after_jsonb = {**audit.after_jsonb, "request_hash": "0" * 64}; db.flush()
    with pytest.raises(MaterialRequestReadError):
        service.my_receipt_trace_status(db, actor=actor, request_id=request.id, trace_request_id=audit.request_id)


def test_trace_lookup_rejects_multiple_matching_audits(world):
    from app.formal_services.audit_chain import append_audit_event
    db, actor, request, *_ = world
    create(world)
    original = db.scalar(select(AuditEvent).where(AuditEvent.action == "my_receipt_registered"))
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id,
        action=original.action, aggregate_type=original.aggregate_type, aggregate_id=str(uuid4()),
        before_jsonb={}, after_jsonb=original.after_jsonb, request_id=original.request_id,
        occurred_at=datetime.now(timezone.utc))
    before = facts(db)
    with pytest.raises(MaterialRequestReadError, match="多笔验收"):
        service.my_receipt_trace_status(db, actor=actor, request_id=request.id, trace_request_id=original.request_id)
    assert facts(db) == before


def test_new_write_cannot_reuse_a_trace_already_bound_to_receipt(world):
    db, actor, request, *_ = world
    value = payload(world)
    create(world, value)
    before = facts(db)
    with pytest.raises(MaterialRequestReadError, match="原请求标识"):
        service.create_my_receipt(db, actor=actor, request_id=request.id, payload=value,
            idempotency_key="different-write-key-0001", secret=SECRET,
            trace_request_id="trace-my-receipt-test-command-001")
    assert facts(db) == before


def test_trace_http_is_readonly_and_requires_original_coordinate(api_client, monkeypatch):
    client, db, principals, *_ = api_client
    mocked = Mock(return_value=None)
    monkeypatch.setattr(service, "my_receipt_trace_status", mocked)
    path = f"/api/v1/material-requests/{uuid4()}/my-receipts/trace-status"
    assert client.get(path).status_code == 400
    mocked.assert_not_called()
    response = client.get(path, headers={"X-Original-Request-ID": "original-receipt-trace-001"})
    assert response.status_code == 200 and response.json()["lookup_status"] == "not_observed"
    assert "no-store" in response.headers["Cache-Control"]
    assert mocked.call_args.kwargs["trace_request_id"] == "original-receipt-trace-001"
    db.commit.assert_not_called()


def test_rejected_serials_are_never_accepted_for_inbound(world):
    db, actor, _, _, _, _ = world
    value = payload(world).model_dump()
    line = value["lines"][0]
    file, _ = evidence(world)
    line.update(condition="rejected", rejected_qty=line["accepted_qty"], accepted_qty="0.000", rejected_serial_ids=line["accepted_serial_ids"], accepted_serial_ids=(), exception_evidence_file_id=file.id)
    result = create(world, MyReceiptIn(**value))
    assert result.status == "exception" and result.lines[0].accepted_qty == 0
    assert result.lines[0].accepted_serial_ids == ()
    assert all(not x.accepted for x in db.scalars(select(ReceiptSerial)).all())
    assert recover(world).lines[0].rejected_serial_ids == result.lines[0].rejected_serial_ids


@pytest.mark.parametrize("change", ["recipient", "location", "inactive", "version", "permission", "stale", "shipment_line", "time"])
def test_wrong_context_cannot_create_or_replay(world, change):
    db, actor, request, location, shipment, line = world
    value = payload(world)
    stable = facts(db)
    if change == "recipient": shipment.target_person_id = uuid4()
    if change == "location": shipment.target_location_id = uuid4()
    if change == "inactive": location.status = "inactive"
    if change == "version": value = value.model_copy(update={"expected_request_version": request.version + 1})
    if change == "permission":
        permission = db.scalar(select(Permission).where(Permission.resource == "material_request", Permission.action == "receive"))
        db.query(RolePermission).filter(RolePermission.permission_id == permission.id).update({"effect": "deny"})
    if change == "stale": actor = replace(actor, authorization_version=actor.authorization_version + 1)
    if change == "shipment_line": value = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"shipment_line_id": uuid4()}),)})
    if change == "time": value = value.model_copy(update={"received_at": "2026-09-08T00:00:00Z"})
    db.flush()
    with pytest.raises(MaterialRequestReadError): create(world, value, actor=actor)
    assert facts(db) == stable


def test_readonly_recovery_survives_receive_permission_removal(world):
    db, actor, *_ = world
    result = create(world)
    permission = db.scalar(select(Permission).where(Permission.resource == "material_request", Permission.action == "receive"))
    db.query(RolePermission).filter(RolePermission.permission_id == permission.id).update({"effect": "deny"}); db.flush()
    statements = []
    def record(_conn, _cursor, statement, *_args): statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        assert recover(world).receipt_id == result.receipt_id
        assert recover(world, key="not-observed-receipt-key-001") is None
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert all(x.lstrip().upper().startswith("SELECT") for x in statements)
    with pytest.raises(MaterialRequestReadError): create(world)


@pytest.mark.parametrize("change", ["quantity", "hash", "serial", "audit"])
def test_recovery_rejects_broken_original_evidence(world, change):
    db, _, _, _, _, _ = world
    result = create(world)
    if change == "quantity": db.query(ReceiptLine).filter(ReceiptLine.receipt_id == result.receipt_id).update({"accepted_qty": ReceiptLine.accepted_qty + Decimal("0.001")})
    if change == "hash": db.get(Receipt, result.receipt_id).request_hash = "b"*64
    if change == "serial":
        row = db.scalar(select(ReceiptSerial))
        if row is None:
            assert recover(world).lines[0].accepted_serial_ids == ()
            return
        row.accepted = False
    if change == "audit": db.query(AuditEvent).filter(AuditEvent.aggregate_id == str(result.receipt_id)).update({"after_jsonb": {}})
    db.flush()
    with pytest.raises((MaterialRequestReadError, ValidationError)): recover(world)


def test_late_audit_failure_rolls_back_all_acceptance_facts(world, monkeypatch):
    db, *_ = world
    db.commit()
    stable = facts(db)
    def fail(*args, **kwargs): raise RuntimeError("injected audit failure")
    monkeypatch.setattr(service, "append_audit_event", fail)
    with pytest.raises(RuntimeError): create(world)
    db.rollback()
    assert facts(db) == stable


def test_late_version_audit_failure_rolls_back_receipt_and_version(world, monkeypatch):
    from app.formal_services import material_request_fulfillment_command as commands
    db, _, request, *_ = world
    db.commit()
    stable, version = facts(db), request.version
    monkeypatch.setattr(commands, "append_audit_event", Mock(side_effect=RuntimeError("injected version audit failure")))
    with pytest.raises(RuntimeError, match="version audit"):
        create(world)
    db.rollback()
    assert facts(db) == stable and request.version == version


@pytest.mark.parametrize("change", ["missing", "body", "audit"])
def test_recovery_checks_the_original_version_command(world, change):
    from app.formal_services import material_request_fulfillment_command as commands
    db, *_ = world
    result = create(world)
    receipt = db.get(Receipt, result.receipt_id)
    command = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.idempotency_key_hash == receipt.idempotency_key_hash))
    if change == "missing":
        db.delete(command)
    elif change == "body":
        command.result_jsonb = {**command.result_jsonb, "personal_inbound_status": "posted"}
        command.result_hash = commands._hash(command.result_jsonb)
    else:
        db.query(AuditEvent).filter(AuditEvent.action == "fulfillment_version_recorded", AuditEvent.aggregate_id == str(result.receipt_id)).update({"after_jsonb": {}})
    db.flush()
    with pytest.raises(MaterialRequestReadError):
        recover(world)


def test_exception_file_must_be_available_and_owned_by_recipient(world):
    db, actor, *_ = world
    value = payload(world).model_dump()
    line = value["lines"][0]
    file = FileObject(id=uuid4(), storage_key=f"test/{uuid4()}", sha256="a"*64, size_bytes=10, mime_type="image/png", uploaded_by=actor.user_id, status="pending")
    db.add(file); db.flush()
    line.update(condition="shortage", exception_evidence_file_id=file.id)
    value = MyReceiptIn(**value)
    stable = facts(db)
    with pytest.raises(MaterialRequestReadError, match="证据"): create(world, value)
    file.status = "available"; file.uploaded_by = None; db.flush()
    with pytest.raises(MaterialRequestReadError, match="证据"): create(world, value)
    assert facts(db) == stable


def test_missing_or_foreign_shipment_serial_evidence_fails_closed(world):
    db, _, _, _, _, line = world
    value = payload(world)
    bound = tuple(db.scalars(select(ShipmentSerial).where(ShipmentSerial.shipment_line_id == line.id)).all())
    if bound:
        db.delete(bound[0]); db.flush()
        with pytest.raises(MaterialRequestReadError, match="SN"): create(world, value)
    else:
        value = value.model_copy(update={"lines": (value.lines[0].model_copy(update={"accepted_serial_ids": (uuid4(),)}),)})
        with pytest.raises(MaterialRequestReadError, match="SN"): create(world, value)


def test_posted_receipt_remains_recipient_bound_on_recovery(world):
    db, _, _, _, shipment, _ = world
    value = payload(world)
    create(world, value)
    shipment.target_person_id = uuid4(); db.flush()
    with pytest.raises(MaterialRequestReadError): recover(world)
    with pytest.raises(MaterialRequestReadError): create(world, value)


@pytest.mark.parametrize("qty", ["abc", "NaN", "Infinity", "-1.000", "0.0001", "1000000000000000", 1.25, True])
def test_invalid_quantities_are_rejected(world, qty):
    value = payload(world).model_dump()
    value["lines"][0]["accepted_qty"] = qty
    with pytest.raises((ValueError, ValidationError)): MyReceiptIn(**value)


def test_http_requires_dedicated_receive_permission_and_never_accepts_receiver(api_client, monkeypatch):
    client, db, principals, _, _ = api_client
    request_id, shipment_id, line_id = uuid4(), uuid4(), uuid4()
    body = dict(expected_request_version=1, shipment_id=str(shipment_id), received_at="2026-09-10T10:00:00Z", lines=[dict(shipment_line_id=str(line_id), accepted_qty="1.000", rejected_qty="0.000", condition="normal")])
    headers = {"Idempotency-Key": "my-receipt-http-0001", "X-Request-ID": "my-receipt-trace-0001"}
    mocked = Mock(side_effect=MaterialRequestReadError("my_receipt_forbidden", "forbidden", "测试拒绝"))
    monkeypatch.setattr(service, "create_my_receipt", mocked)
    assert client.post(f"/api/v1/material-requests/{request_id}/my-receipts", json=body, headers=headers).status_code == 403
    mocked.assert_not_called()
    principals["value"].permissions.add(("material_request", "receive", ""))
    assert client.post(f"/api/v1/material-requests/{request_id}/my-receipts", json={**body, "receiver_person_id": str(uuid4())}, headers=headers).status_code == 422
    result = client.post(f"/api/v1/material-requests/{request_id}/my-receipts", json=body, headers=headers)
    assert result.status_code == 403 and "no-store" in result.headers["Cache-Control"]
    db.rollback.assert_called_once(); db.commit.assert_not_called()


def test_http_recovery_stays_readonly_when_write_switch_is_off(api_client, monkeypatch):
    client, db, _, _, settings = api_client
    settings.material_request_writes_enabled = False
    mocked = Mock(return_value=None)
    monkeypatch.setattr(service, "my_receipt_command_status", mocked)
    response = client.get(f"/api/v1/material-requests/{uuid4()}/my-receipts/command-status", headers={"Idempotency-Key": "my-receipt-http-recovery-001"})
    assert response.status_code == 200 and response.json()["lookup_status"] == "not_observed"
    assert "no-store" in response.headers["Cache-Control"]
    mocked.assert_called_once(); db.commit.assert_not_called()
