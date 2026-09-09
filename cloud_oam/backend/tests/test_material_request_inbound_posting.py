"""Inbound orchestration/replay tests; seeded posting is not PG16 acceptance."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.demand_models import MaterialRequest, MaterialRequestLine
from app.foundation_models import AuditEvent, OutboxEvent
from app.foundation_models import Permission, RolePermission
from app.models import User
from app.inventory_models import (
    InboundOrder, InboundPosting, InventoryTransaction, OutboundPosting,
    Receipt, ReceiptLine, ReceiptSerial, Shipment, ShipmentLine,
    StockAccount, StockBalance, StockLocation,
)
from app.formal_services import material_request_inbound as inbound
from app.formal_services.material_request_inbound_state import refresh_personal_inbound_status
from app.formal_services import inventory_posting as inventory
from test_material_request_outbound import outbound_world, _create as create_outbound
from test_material_request_picking import pick_world
from test_material_request_reservation_release import release_world
from test_material_request_fulfillment_preparation import approval_db

KEY = "inbound-posting-replay-test-0001"


@pytest.fixture
def inbound_world(outbound_world, monkeypatch):
    db, actor, request, _, serials, _, _ = outbound_world
    result = create_outbound(outbound_world)
    posting = db.get(OutboundPosting, UUID(result["posting_id"]))
    source = db.get(StockAccount, posting.target_stock_account_id)
    now = datetime.now(timezone.utc)
    location = StockLocation(
        id=uuid4(), code="INBOUND-PERSONAL", name="测试个人仓", location_type="personal",
        owner_org_id=source.owner_org_id, parent_id=source.location_id,
        custodian_person_id=actor.person_id, status="active",
    )
    db.add(location); db.flush()
    target = StockAccount(
        id=uuid4(), owner_org_id=source.owner_org_id, custodian_person_id=actor.person_id,
        location_id=location.id, material_id=source.material_id,
        condition_code=source.condition_code, lot_id=source.lot_id,
        availability_bucket="available",
    )
    db.add(target); db.flush()
    db.add(StockBalance(stock_account_id=target.id, quantity=Decimal("0"), ledger_cursor=0, version=0))
    shipment = Shipment(
        id=uuid4(), shipment_no="SHP-INBOUND-REPLAY", source_location_id=source.location_id,
        target_location_id=location.id, target_person_id=actor.person_id, carrier="测试",
        tracking_no="TEST-INBOUND", status="shipped", shipped_at=now,
        idempotency_key_hash="a" * 64, request_hash="b" * 64, actor_user_id=actor.user_id,
        actor_person_id=actor.person_id, authorization_version=actor.authorization_version,
    )
    db.add(shipment); db.flush()
    shipped_line = ShipmentLine(id=uuid4(), shipment_id=shipment.id,
        outbound_posting_id=posting.id, outbound_line_id=posting.outbound_line_id,
        shipped_qty=posting.outbound_qty)
    db.add(shipped_line); db.flush()
    receipt = Receipt(id=uuid4(), receipt_no="RCT-INBOUND-REPLAY", shipment_id=shipment.id,
        status="accepted", received_at=now, receiver_person_id=actor.person_id,
        request_hash="c" * 64, idempotency_key_hash="d" * 64)
    db.add(receipt); db.flush()
    line = ReceiptLine(id=uuid4(), receipt_id=receipt.id, shipment_line_id=shipped_line.id,
        accepted_qty=posting.outbound_qty, rejected_qty=Decimal("0"), condition="normal")
    db.add(line); db.flush()
    db.add_all(ReceiptSerial(receipt_line_id=line.id, serial_id=s, accepted=True) for s in serials[:1])
    order = InboundOrder(id=uuid4(), inbound_no="INB-REPLAY", receipt_id=receipt.id,
        target_location_id=location.id, target_person_id=actor.person_id,
        status="pending", created_at=now)
    db.add(order); db.flush()

    # Seed the first commit with the same canonical request hash as the ledger.
    # Replay below uses the REAL posting service and REAL current RBAC checks.
    def seed_post(db, *, actor, command, idempotency_key, request_id):
        command = inventory._validate_posting_command(command)
        transaction = InventoryTransaction(
            id=uuid4(), transaction_no=command.transaction_no, movement_type=command.movement_type,
            source_document_type=command.source_document_type, source_document_id=command.source_document_id,
            posting_key=command.posting_key, idempotency_key_hash=inventory._storage_hash(idempotency_key),
            request_hash=inventory._posting_request_hash(actor, command), status="posted",
            effective_at=command.effective_at, posted_at=now, ledger_cursor=10000,
            actor_user_id=actor.user_id,
        )
        db.add(transaction); db.flush()
        return inventory.InventoryPostingResult(transaction_id=transaction.id,
            transaction_no=transaction.transaction_no, ledger_cursor=transaction.ledger_cursor)
    monkeypatch.setattr(inbound, "post_inventory_transaction", seed_post)
    first = inbound.post_inbound_order(db, actor=actor, inbound_order_id=order.id,
        material_request_id=request.id, idempotency_key=KEY, request_id="trace-inbound-first-0001")
    db.flush()
    monkeypatch.setattr(inbound, "post_inventory_transaction", inventory.post_inventory_transaction)
    return SimpleNamespace(db=db, actor=actor, request=request, order=order, receipt=receipt,
        shipment=shipment, target=target, first=first)


def post(world, **overrides):
    return inbound.post_inbound_order(world.db, **{
        "actor": world.actor, "inbound_order_id": world.order.id,
        "material_request_id": world.request.id, "idempotency_key": KEY,
        "request_id": "trace-inbound-repeat-0001", **overrides,
    })


def snapshot(world):
    db = world.db
    return (
        db.scalar(select(func.count()).select_from(InboundPosting)),
        db.scalar(select(func.count()).select_from(InventoryTransaction)),
        tuple(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity,
            StockBalance.version, StockBalance.ledger_cursor).order_by(StockBalance.stock_account_id))),
    )


def test_same_command_replay_returns_same_transaction_without_duplicate_binding(inbound_world):
    world = inbound_world
    before = snapshot(world)
    result = post(world)
    assert result["inventory_transaction_id"] == world.first["inventory_transaction_id"]
    assert result["replayed"] is True
    assert world.order.posting_transaction_id == world.first["inventory_transaction_id"]
    assert world.order.status == "posted"
    # The fixture ships/receives one line while the approved request has a
    # larger net quantity; one posted slice must remain partial.
    assert world.request.personal_inbound_status == "partially_accepted"
    assert world.db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "personal_inbound_posted")) == 1
    assert world.db.scalar(select(func.count()).select_from(OutboxEvent).where(
        OutboxEvent.event_type == "personal_inbound_posted")) == 1
    assert snapshot(world) == before


def test_first_post_binds_order_to_inventory_transaction(inbound_world):
    world = inbound_world

    assert world.order.status == "posted"
    assert world.order.posting_transaction_id == world.first["inventory_transaction_id"]


def test_full_approved_line_reaches_posted_state(inbound_world):
    world = inbound_world
    posting = world.db.scalar(
        select(OutboundPosting).where(OutboundPosting.request_id == world.request.id)
    )
    line = world.db.get(MaterialRequestLine, posting.request_line_id)
    receipt_line = world.db.scalar(select(ReceiptLine).where(ReceiptLine.receipt_id == world.receipt.id))
    line.final_approved_qty = receipt_line.accepted_qty
    line.cancelled_qty = Decimal("0.000")
    world.db.flush()

    refresh_personal_inbound_status(world.db, world.request)

    assert world.request.personal_inbound_status == "posted"


def test_unposted_accepted_quantity_is_accepted_state(inbound_world):
    world = inbound_world
    posting = world.db.scalar(
        select(OutboundPosting).where(OutboundPosting.request_id == world.request.id)
    )
    line = world.db.get(MaterialRequestLine, posting.request_line_id)
    receipt_line = world.db.scalar(select(ReceiptLine).where(ReceiptLine.receipt_id == world.receipt.id))
    line.final_approved_qty = receipt_line.accepted_qty
    line.cancelled_qty = Decimal("0.000")
    inbound_posting = world.db.scalar(
        select(InboundPosting).where(InboundPosting.inbound_order_id == world.order.id)
    )
    world.db.delete(inbound_posting)
    world.order.status = "pending"
    world.order.posting_transaction_id = None
    world.db.flush()

    refresh_personal_inbound_status(world.db, world.request)

    assert world.request.personal_inbound_status == "accepted"


def test_failed_first_post_rolls_back_order_transaction_binding(inbound_world, monkeypatch):
    world = inbound_world
    existing = world.db.scalar(
        select(InboundPosting).where(InboundPosting.inbound_order_id == world.order.id)
    )
    world.db.delete(existing)
    world.order.status = "pending"
    world.order.posting_transaction_id = None
    world.db.flush()

    transaction_id = uuid4()
    monkeypatch.setattr(
        inbound,
        "post_inventory_transaction",
        lambda *args, **kwargs: SimpleNamespace(transaction_id=transaction_id, replayed=False),
    )

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(inbound, "append_audit_event", fail_audit)
    savepoint = world.db.begin_nested()
    with pytest.raises(RuntimeError, match="audit unavailable"):
        post(world, idempotency_key="inbound-first-failure-0002")
    savepoint.rollback()
    world.db.expire_all()
    refreshed = world.db.get(InboundOrder, world.order.id)
    assert refreshed.posting_transaction_id is None
    assert refreshed.status == "pending"
    assert world.db.scalar(
        select(func.count()).select_from(InboundPosting).where(
            InboundPosting.inbound_order_id == world.order.id
        )
    ) == 0


def test_posted_order_cannot_be_replayed_through_another_existing_request(inbound_world):
    world = inbound_world
    # Copy only a request row so the alternate URL refers to an existing object.
    values = {c.name: getattr(world.request, c.name) for c in MaterialRequest.__table__.columns}
    values.update(id=uuid4(), request_no="REQ-UNRELATED-INBOUND")
    world.db.add(MaterialRequest(**values)); world.db.flush()
    before = snapshot(world)
    with pytest.raises(inbound.InboundError) as error:
        post(world, material_request_id=values["id"])
    assert error.value.code == "request_mismatch"
    assert snapshot(world) == before


def test_posted_order_replay_rechecks_current_inventory_permission(inbound_world):
    world = inbound_world
    permission = world.db.scalar(select(Permission).where(
        Permission.resource == "inventory_transaction", Permission.action == "post"))
    grants = world.db.scalars(select(RolePermission).where(RolePermission.permission_id == permission.id)).all()
    for grant in grants: grant.effect = "deny"
    world.db.flush()
    before = snapshot(world)
    with pytest.raises(inventory.InventoryPostingError) as error:
        post(world)
    assert error.value.category == "forbidden"
    assert snapshot(world) == before


def test_posted_order_replay_rechecks_current_principal_version(inbound_world):
    world = inbound_world
    world.db.get(User, world.actor.user_id).authorization_version += 1
    world.db.flush()
    with pytest.raises(inventory.InventoryPostingError) as error:
        post(world)
    assert error.value.code == "actor_principal_stale"


def test_new_idempotency_key_cannot_bypass_original_posting_binding(inbound_world):
    world = inbound_world
    before = snapshot(world)
    with pytest.raises(inventory.InventoryPostingError) as error:
        post(world, idempotency_key="inbound-different-key-0002")
    assert error.value.code == "posting_key_conflict"
    assert snapshot(world) == before


def test_changed_destination_blocks_even_completed_orders(inbound_world):
    world = inbound_world
    world.order.target_person_id = uuid4()
    world.db.flush()
    with pytest.raises(inbound.InboundError) as error:
        post(world)
    assert error.value.code == "target_mismatch"


def test_available_destination_is_required_without_creating_an_account(inbound_world):
    world = inbound_world
    world.target.availability_bucket = "arrived_pending"
    world.db.flush()
    count = world.db.scalar(select(func.count()).select_from(StockAccount))
    with pytest.raises(inbound.InboundError) as error:
        post(world)
    assert error.value.code == "personal_target_missing"
    assert world.db.scalar(select(func.count()).select_from(StockAccount)) == count


@pytest.mark.parametrize("category,expected", [("forbidden", 403), ("conflict", 409), ("precondition_failed", 412)])
def test_inbound_http_errors_roll_back_and_preserve_stable_status(monkeypatch, category, expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.dependencies import get_formal_principal
    from app.routers import formal_material_requests

    calls = []
    db = SimpleNamespace(rollback=lambda: calls.append("rollback"), commit=lambda: calls.append("commit"))
    app = FastAPI()
    app.include_router(formal_material_requests.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_formal_principal] = lambda: SimpleNamespace(allows=lambda *a, **kw: True)
    def fail(*a, **kw):
        raise inbound.InboundError("inbound_test_boundary", category, "入账验证未通过")
    monkeypatch.setattr(inbound, "post_inbound_order", fail)
    with TestClient(app) as client:
        response = client.post(f"/api/v1/material-requests/{uuid4()}/inbound-orders/{uuid4()}/post",
            headers={"Idempotency-Key": KEY, "X-Request-ID": "inbound-http-test-trace"})
    assert response.status_code == expected
    assert response.json()["detail"]["code"] == "inbound_test_boundary"
    assert calls == ["rollback"]
