"""Real accepted-return facts drive a separate, atomic inventory posting."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text

from app.database import get_db
from app.foundation_models import NotificationEvent, NotificationPersonTarget, OutboxEvent
from app.inventory_models import (StockAccount, StockBalance, SerialCurrentPosition,
    InventoryTransaction, InventoryMovement, Receipt, Shipment, CustodyAssignment)
from app.stock_operation_models import (StockOperationReturnInbound,
    StockOperationReturnInboundPosting, StockOperationReceipt)
from app.formal_services import stock_return_inbound_commands as commands
from app.formal_services import stock_return_inbound_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import inventory_posting as posting
from app.formal_services import stock_return_inbound_plan as planning
from app.routers import formal_stock_return_inbounds as router
from test_stock_return_receipt import (db, world, stock, recovered, destination, prepared,
    parcel, incoming, acceptance, submit, execute, snapshot as receipt_snapshot)
from test_inventory_posting import establish_account_for_posting, NOW


@pytest.fixture
def inbound_accounts(db, stock, acceptance, parcel, destination):
    source = db.get(StockAccount, parcel.line.transit_stock_account_id)
    target = StockAccount(id=uuid4(), owner_org_id=source.owner_org_id,
        custodian_person_id=acceptance.actor.person_id, location_id=destination[0].id,
        material_id=source.material_id, condition_code=source.condition_code,
        availability_bucket="available", lot_id=source.lot_id,
        created_at=NOW - timedelta(days=1), updated_at=NOW - timedelta(days=1))
    db.add(target); db.flush()
    establish_account_for_posting(db, stock.world, target)
    stock.world.current_principal = acceptance.actor
    db.commit()
    return source.id, target.id


@pytest.fixture
def accepted(db, acceptance, inbound_accounts):
    receipt = execute(db, acceptance, submit(db, acceptance)); db.commit()
    return SimpleNamespace(receipt=receipt, actor=acceptance.actor,
        source_id=inbound_accounts[0], target_id=inbound_accounts[1])


def snapshot(db):
    return receipt_snapshot(db), tuple(tuple(db.execute(text(f"SELECT * FROM {name} ORDER BY id")))
        for name in ("stock_operation_return_inbounds", "stock_operation_return_inbound_lines",
            "stock_operation_return_inbound_serials", "stock_operation_return_inbound_postings",
            "stock_operation_command_seals", "stock_operation_return_inbound_seals", "notification_events", "notification_person_targets"))


def command(db, context):
    checked = commands.preview_return_inbound(db, actor=context.actor, receipt_id=context.receipt.receipt_id)
    return dict(actor=context.actor, receipt_id=context.receipt.receipt_id,
        expected_plan_hash=checked["plan_hash"], request_id=uuid4().hex, idempotency_key=uuid4().hex)


def application(db, actor):
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: actor
    return app


def prefix(context):
    return f"/api/v1/stock-returns/my-receiving/{context.receipt.receipt_id}/inbound"


def assert_mini_contract(view, original, actor):
    """Validate actual HTTP data with the shipped mini-program parser."""
    script = """const fs = require('node:fs');
const c = require('./utils/stock-return-inbound-contract');
const { view, original, person, version } = JSON.parse(fs.readFileSync(0, 'utf8'));
c.validatePreview(view, { receiptId: view.receipt_id, shipmentId: view.shipment_id, personId: person, authorizationVersion: version });
const marker = { receipt_id: view.receipt_id, shipment_id: view.shipment_id,
  trace_request_id: original.request_id, plan_hash: original.plan_hash,
  request_hash: c.requestHash(view.receipt_id, original.plan_hash, original.request_id) };
c.validateLookup(original, marker);
"""
    checked = subprocess.run([shutil.which("node") or "node", "-e", script],
        input=json.dumps(dict(view=view, original=original, person=str(actor.person_id), version=actor.authorization_version)),
        text=True, capture_output=True, timeout=30,
        cwd=Path(__file__).resolve().parents[2] / "miniprogram")
    assert checked.returncode == 0, checked.stderr


def test_preview_http_is_read_only_and_posting_moves_only_accepted_inventory(db, stock, accepted):
    before = snapshot(db)
    with TestClient(application(db, accepted.actor), raise_server_exceptions=False) as client:
        statements = []
        def capture(_c, _cu, sql, _p, _ctx, _many): statements.append(sql.strip().split()[0].upper())
        c = db.connection(); event.listen(c, "before_cursor_execute", capture)
        try: response = client.post(prefix(accepted) + "/preview")
        finally: event.remove(c, "before_cursor_execute", capture)
        assert response.status_code == 200, response.text
        assert response.json()["reason"] == "逐件验收退回物料，尚未入账"
        assert statements and set(statements) == {"SELECT"}
        assert snapshot(db) == before
    value = command(db, accepted)
    result = commands.execute_return_inbound(db, **value); db.commit()
    fact = db.get(StockOperationReturnInbound, result["inbound_id"])
    assert fact.receipt_id == accepted.receipt.receipt_id
    assert db.get(StockBalance, accepted.source_id).quantity == 0
    assert db.get(StockBalance, accepted.target_id).quantity == 1
    tx = db.get(InventoryTransaction, fact.posting_transaction_id)
    assert tx.source_document_type == "stock_return_receipt_inbound"
    assert tx.source_document_id == str(fact.id) and tx.status == "posted"
    move = db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id))
    assert (move.from_account_id, move.to_account_id, move.quantity) == (accepted.source_id, accepted.target_id, Decimal(1))
    assert db.scalar(select(StockOperationReturnInboundPosting.inventory_transaction_id)) == tx.id
    if stock.tracked:
        assert db.get(SerialCurrentPosition, stock.serials[0].id).stock_account_id == accepted.target_id
    assert db.get(Receipt, fact.receipt_id).status == "accepted"
    assert db.get(Shipment, fact.shipment_id).status == "shipped"
    notice = db.scalar(select(NotificationEvent).where(NotificationEvent.business_id == str(fact.id)))
    assert notice is not None
    assert db.scalar(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == notice.id)) == accepted.actor.person_id
    after = snapshot(db)
    assert commands.execute_return_inbound(db, **value) == {**result, "replayed": True}
    assert snapshot(db) == after


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
@pytest.mark.parametrize("change", ["receipt", "request", "key", "plan"])
def test_replay_requires_exact_receipt_request_key_and_plan(db, accepted, change):
    value = command(db, accepted)
    commands.execute_return_inbound(db, **value); db.commit(); before = snapshot(db)
    other = {**value, {"receipt": "receipt_id", "request": "request_id", "key": "idempotency_key", "plan": "expected_plan_hash"}[change]:
        uuid4() if change == "receipt" else "0" * 64 if change == "plan" else uuid4().hex}
    with pytest.raises(InventoryReadError) as error: commands.execute_return_inbound(db, **other)
    assert isinstance(error.value.status_code, int)
    assert snapshot(db) == before


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_completed_inbound_seal_returns_exact_original_http_result(db, accepted):
    view = commands.preview_return_inbound(db, actor=accepted.actor, receipt_id=accepted.receipt.receipt_id)
    from app.stock_return_inbound_schemas import StockReturnInboundPreviewOut
    view = StockReturnInboundPreviewOut.model_validate(view).model_dump(mode="json")
    value = command(db, accepted)
    result = commands.execute_return_inbound(db, **value); db.commit(); before = snapshot(db)
    with TestClient(application(db, accepted.actor), raise_server_exceptions=False) as client:
        url = prefix(accepted) + "/by-request/" + value["request_id"]
        original = client.get(url)
        assert original.status_code == 200, original.text
        response = client.post(url + "/seal", json={"request_hash": result["request_hash"]})
        assert response.status_code == 200, response.text
        assert response.json() == original.json()
        assert "no-store" in response.headers["cache-control"]
        assert_mini_contract(view, original.json(), accepted.actor)
    assert snapshot(db) == before


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_missing_receipt_http_error_is_private_and_readable(db, accepted):
    before = snapshot(db)
    with TestClient(application(db, accepted.actor), raise_server_exceptions=False) as client:
        response = client.post(f"/api/v1/stock-returns/my-receiving/{uuid4()}/inbound/preview")
        assert response.status_code == 404, response.text
        assert "no-store" in response.headers["cache-control"]
        assert response.json()["detail"]["message"] == "退回验收事实不存在"
    assert snapshot(db) == before


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_changed_plan_and_late_domain_failure_roll_back_every_posting_effect(db, accepted, monkeypatch):
    value = command(db, accepted); before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.execute_return_inbound(db, **{**value, "expected_plan_hash": "0" * 64})
    assert error.value.status_code == 409 and snapshot(db) == before
    def broken(*args, **kwargs): raise RuntimeError("synthetic inbound notification failure")
    monkeypatch.setattr(commands, "record_stock_return_notification", broken)
    with pytest.raises(RuntimeError, match="synthetic inbound notification failure"):
        commands.execute_return_inbound(db, **value)
    db.rollback(); db.expire_all()
    assert snapshot(db) == before
    assert recovery.lookup_return_inbound_request(db, actor=accepted.actor,
        receipt_id=accepted.receipt.receipt_id, request_id=value["request_id"]) is None


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
@pytest.mark.parametrize("change", ["read_only", "out_of_scope", "custody"])
def test_preview_replay_and_seal_recheck_current_write_authority(db, stock, accepted, change):
    value = command(db, accepted)
    original = commands.execute_return_inbound(db, **value); db.commit()
    actor = accepted.actor
    if change == "read_only":
        actor = replace(actor, entitlements=tuple(row for row in actor.entitlements if row.action != "receive_return"))
    elif change == "out_of_scope":
        actor = replace(actor, entitlements=tuple(replace(row, scope_type="organization", scope_id=str(uuid4())) for row in actor.entitlements))
    else:
        from datetime import datetime, timezone
        db.get(CustodyAssignment, original["target_custody_assignment_id"]).valid_to = datetime.now(timezone.utc)
        db.commit()
    stock.world.current_principal = actor; before = snapshot(db)
    for call in (
        lambda: commands.preview_return_inbound(db, actor=actor, receipt_id=accepted.receipt.receipt_id),
        lambda: commands.execute_return_inbound(db, **{**value, "actor": actor}),
        lambda: recovery.seal_return_inbound_request(db, actor=actor, receipt_id=accepted.receipt.receipt_id,
            request_id=value["request_id"], request_hash=original["request_hash"]),
    ):
        with pytest.raises((InventoryReadError, posting.InventoryPostingError)): call()
        assert snapshot(db) == before
    if change == "read_only":
        assert recovery.lookup_return_inbound_request(db, actor=actor,
            receipt_id=accepted.receipt.receipt_id, request_id=value["request_id"]) == original


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_generic_reversal_cannot_unpost_an_accepted_return_by_renaming_source(db, accepted):
    result = commands.execute_return_inbound(db, **command(db, accepted)); db.commit(); before = snapshot(db)
    with pytest.raises(posting.InventoryPostingError) as error:
        posting._require_generic_reversal_origin(db, SimpleNamespace(original_transaction_id=result["posting_transaction_id"],
            source_document_type="renamed"))
    assert error.value.code == "stock_return_reversal_requires_command" and snapshot(db) == before


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_preview_includes_reason_in_one_canonical_plan_and_preserves_pending_outbox(db, accepted):
    pending = OutboxEvent(event_type="synthetic.not_flushed", aggregate_type="synthetic", aggregate_id="synthetic",
        payload_jsonb={}, idempotency_key="synthetic-inbound-preview-" + uuid4().hex)
    db.add(pending)
    plan = planning.plan_return_inbound(db, actor=accepted.actor, receipt_id=accepted.receipt.receipt_id)
    view = commands.preview_return_inbound(db, actor=accepted.actor, receipt_id=accepted.receipt.receipt_id)
    assert view["reason"] == plan["reason"]
    assert view["plan_hash"] == plan["plan_hash"]
    assert pending in db.new
    db.rollback()


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_two_receipts_on_one_parcel_post_independently_and_never_recover_each_other(db, acceptance, inbound_accounts):
    contexts = []
    for qty in (".375", ".625"):
        request = acceptance.request.model_copy(update={"lines": (acceptance.request.lines[0].model_copy(update={"accepted_qty": Decimal(qty)}),)})
        receipt = execute(db, acceptance, submit(db, acceptance, request)); db.commit()
        contexts.append(SimpleNamespace(receipt=receipt, actor=acceptance.actor))
    first, second = contexts
    value = command(db, first)
    result = commands.execute_return_inbound(db, **value); db.commit()
    assert db.get(StockBalance, inbound_accounts[0]).quantity == Decimal(".625")
    assert db.get(StockBalance, inbound_accounts[1]).quantity == Decimal(".375")
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.execute_return_inbound(db, **{**value, "receipt_id": second.receipt.receipt_id})
    assert error.value.code == "stock_return_inbound_idempotency_conflict"
    for call in (
        lambda: recovery.lookup_return_inbound_request(db, actor=acceptance.actor,
            receipt_id=second.receipt.receipt_id, request_id=value["request_id"]),
        lambda: recovery.seal_return_inbound_request(db, actor=acceptance.actor,
            receipt_id=second.receipt.receipt_id, request_id=value["request_id"], request_hash=result["request_hash"]),
    ):
        with pytest.raises(InventoryReadError) as error: call()
        assert error.value.code == "stock_return_inbound_request_conflict"
    assert snapshot(db) == before
    other = commands.execute_return_inbound(db, **command(db, second)); db.commit()
    assert other["inbound_id"] != result["inbound_id"]
    assert db.get(StockBalance, inbound_accounts[0]).quantity == 0
    assert db.get(StockBalance, inbound_accounts[1]).quantity == 1
    assert len(tuple(db.scalars(select(StockOperationReturnInbound.id)))) == 2


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_acceptance_request_and_seal_cannot_be_reused_for_inbound(db, accepted):
    from app.formal_services.stock_return_receipt_recovery import seal_receipt_request
    value = command(db, accepted); before = snapshot(db)
    original = db.get(StockOperationReceipt, accepted.receipt.receipt_id)
    with pytest.raises(InventoryReadError) as error:
        commands.execute_return_inbound(db, **{**value, "request_id": original.request_id})
    assert error.value.code == "stock_return_request_conflict" and snapshot(db) == before
    seal_receipt_request(db, actor=accepted.actor, shipment_id=accepted.receipt.shipment_id,
        request_id=value["request_id"], request_hash="a" * 64)
    db.commit(); before = snapshot(db)
    with pytest.raises(InventoryReadError) as error: commands.execute_return_inbound(db, **value)
    assert error.value.code == "stock_return_request_sealed" and snapshot(db) == before


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_http_submit_headers_and_commit_failure_preserve_original_request(db, accepted, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    value = command(db, accepted)
    body = {key: value[key] for key in ("expected_plan_hash", "request_id", "idempotency_key")}
    body["operator_person_id"] = str(accepted.actor.person_id)
    before = snapshot(db)
    with TestClient(application(db, accepted.actor), raise_server_exceptions=False) as client:
        for header in ({"X-Request-ID": "other-request"}, {"Idempotency-Key": "other-key"}):
            response = client.post(prefix(accepted), json=body, headers=header)
            assert response.status_code == 400 and "no-store" in response.headers["cache-control"]
        assert client.post(prefix(accepted), json={**body, "operator_person_id": str(uuid4())}).status_code == 403
        assert snapshot(db) == before
        def broken(): raise SQLAlchemyError("synthetic inbound commit failure")
        monkeypatch.setattr(db, "commit", broken)
        response = client.post(prefix(accepted), json=body)
        assert response.status_code == 503 and "no-store" in response.headers["cache-control"]
        assert "synthetic" not in response.text
        assert snapshot(db) == before
        original = client.get(prefix(accepted) + "/by-request/" + value["request_id"])
        assert original.status_code == 404


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_missing_target_is_an_explicit_precondition_error(db, acceptance):
    receipt = execute(db, acceptance, submit(db, acceptance)); db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.preview_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id)
    assert error.value.code == "stock_return_inbound_target_missing" and error.value.status_code == 412
    assert snapshot(db) == before


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_rejected_only_receipt_cannot_generate_an_empty_inbound(db, stock, acceptance, monkeypatch):
    from test_stock_return_receipt import evidence, abnormal
    file, _ = evidence(db, stock, acceptance, monkeypatch)
    rejected = abnormal(acceptance, file.id, "rejected")
    receipt = execute(db, acceptance, submit(db, acceptance, rejected)); db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.preview_return_inbound(db, actor=acceptance.actor, receipt_id=receipt.receipt_id)
    assert error.value.code == "stock_return_inbound_no_accepted_lines" and error.value.status_code == 409
    assert snapshot(db) == before
