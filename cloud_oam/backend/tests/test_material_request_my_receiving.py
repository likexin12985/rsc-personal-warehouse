from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select

from app.formal_access import load_formal_principal
from app.formal_services import material_request_my_receiving as receiving
from app.formal_services import material_request_shipment as shipping
from app.formal_services.material_request_query import MaterialRequestReadError
from app.foundation_models import Permission, Person, Role, RolePermission
from app.inventory_models import CustodyAssignment, Receipt, ReceiptLine, Shipment, ShipmentLine, StockAccount, StockLocation
from app.models import User
from test_material_request_draft_service import SECRET
from test_material_request_outbound import outbound_world, _create as create_outbound, _input as outbound_input
from test_formal_material_request_api import api_client

pytest_plugins = ("test_material_request_picking",)


@pytest.fixture
def receiving_world(outbound_world):
    db, source_actor, request, *_ = outbound_world
    recipient_user = db.scalar(select(User).where(User.person_id == request.requester_person_id))
    person = db.get(Person, request.requester_person_id)
    permission = db.scalar(select(Permission).where(Permission.resource == "inventory", Permission.action == "read", Permission.field_code == ""))
    role_id = db.scalar(select(Role.id).where(Role.code == "technician"))
    db.add(RolePermission(role_id=role_id, permission_id=permission.id, effect="allow"))
    parent = db.scalar(select(StockLocation).where(StockLocation.location_type != "personal"))
    location = StockLocation(id=uuid4(), code=f"RECEIVER-{uuid4().hex}", name="测试收货个人仓", location_type="personal", owner_org_id=person.organization_id, parent_id=parent.id, custodian_person_id=person.id, status="active")
    db.add(location)
    db.flush()
    db.add(CustodyAssignment(id=uuid4(), location_id=location.id, custodian_person_id=person.id, valid_from=datetime(2026, 9, 1, tzinfo=timezone.utc)))
    db.flush()
    result = create_outbound(outbound_world)
    shipped = shipping.create_shipment(
        db, actor=source_actor, request_id=request.id, expected_version=request.version,
        target_location_id=location.id, target_person_id=person.id,
        carrier="人工承运", tracking_no="TEST-PACKAGE-001", shipped_at="2026-09-09T10:00:00+08:00",
        lines=(SimpleNamespace(outbound_posting_id=UUID(result["posting_id"]), shipped_qty=Decimal(result["outbound_qty"]), serial_ids=tuple(outbound_world[4][:1])),),
        idempotency_key="my-receiving-shipment-test-0001", secret=SECRET, trace_request_id="trace-my-receiving-shipment-test-0001",
    )
    db.flush()
    recipient = load_formal_principal(db, recipient_user.id)
    return db, recipient, request, location, db.get(Shipment, shipped["shipment_id"]), db.get(ShipmentLine, shipped["lines"][0]["shipment_line_id"])


def read(world, **kwargs):
    db, actor, request, *_ = world
    return receiving.list_my_receiving(db, actor=actor, request_id=request.id, **kwargs)


def test_recipient_reads_own_package_without_source_permissions_or_writes(receiving_world):
    db, actor, request, location, shipment, line = receiving_world
    source = db.scalar(select(StockAccount).join(receiving.OutboundPosting, receiving.OutboundPosting.source_stock_account_id == StockAccount.id).where(receiving.OutboundPosting.id == line.outbound_posting_id))
    assert not actor.allows(db, "inventory", "read", target_scope_type="organization", target_scope_id=str(source.owner_org_id))
    statements = []
    def record(_conn, _cursor, statement, _params, _context, _many): statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", record)
    try:
        result = read(receiving_world)
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert result.person_id == actor.person_id
    assert result.packages[0].shipment_id == shipment.id
    assert result.packages[0].lines[0].unconfirmed_qty == format(line.shipped_qty, ".3f")
    assert result.packages[0].lines[0].accepted_qty == "0.000"
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    payload = result.model_dump_json()
    assert str(source.id) not in payload
    assert "source_stock_account_id" not in payload and "balance" not in payload


def test_other_recipient_and_unbound_packages_are_not_returned(receiving_world):
    db, _, _, _, shipment, _ = receiving_world
    for person_id in (uuid4(), None):
        shipment.target_person_id = person_id
        db.flush()
        assert read(receiving_world).packages == ()


@pytest.mark.parametrize("change", ["location", "inactive", "custody", "duplicate", "child"])
def test_target_and_custody_must_remain_exact(receiving_world, change):
    db, actor, _, location, shipment, _ = receiving_world
    if change == "location": shipment.target_location_id = uuid4()
    if change == "inactive": location.status = "inactive"
    if change == "custody": db.query(CustodyAssignment).filter(CustodyAssignment.location_id == location.id).delete()
    if change == "duplicate": db.add(StockLocation(id=uuid4(), code=f"OTHER-{uuid4().hex}", name="另一库位", location_type="personal", owner_org_id=location.owner_org_id, parent_id=location.parent_id, custodian_person_id=actor.person_id, status="active"))
    if change == "child": db.add(StockLocation(id=uuid4(), code=f"CHILD-{uuid4().hex}", name="错误子库位", location_type="region", owner_org_id=location.owner_org_id, parent_id=location.id, status="active"))
    db.flush()
    with pytest.raises(MaterialRequestReadError) as error:
        read(receiving_world)
    assert error.value.category == "precondition_failed"


def test_receipt_quantities_stay_separate_from_inbound_and_signature(receiving_world):
    db, actor, _, _, shipment, line = receiving_world
    accepted = Decimal("1.000") if line.shipped_qty == Decimal("1.000") else Decimal("0.025")
    receipt = Receipt(id=uuid4(), receipt_no="TEST-RCT", shipment_id=shipment.id, status="exception", received_at=datetime.now(timezone.utc), receiver_person_id=actor.person_id, request_hash="a" * 64, idempotency_key_hash="b" * 64)
    db.add(receipt)
    db.flush()
    db.add(ReceiptLine(id=uuid4(), receipt_id=receipt.id, shipment_line_id=line.id, accepted_qty=accepted, rejected_qty=Decimal("0.000"), condition="shortage"))
    db.flush()
    row = read(receiving_world).packages[0].lines[0]
    assert row.accepted_qty == format(accepted, ".3f")
    assert row.unconfirmed_qty == format(line.shipped_qty - accepted, ".3f")
    assert row.has_exception is True
    assert "inbound" not in row.model_dump_json() and "signature" not in row.model_dump_json()
    receipt.receiver_person_id = uuid4()
    db.flush()
    with pytest.raises(MaterialRequestReadError, match="收货记录"):
        read(receiving_world)


def test_quantity_overrun_or_broken_outbound_evidence_is_not_displayed(receiving_world, monkeypatch):
    db, _, _, _, _, line = receiving_world
    old = line.shipped_qty
    line.shipped_qty += Decimal("1.000")
    db.flush()
    with pytest.raises(MaterialRequestReadError, match="数量"):
        read(receiving_world)
    line.shipped_qty = old
    db.flush()
    def broken(*args, **kwargs): raise receiving.outbound.MaterialRequestOutboundError("bad", "service_unavailable", "bad")
    monkeypatch.setattr(receiving.outbound, "verified_outbound_history", broken)
    with pytest.raises(MaterialRequestReadError, match="出库证据"):
        read(receiving_world)


def test_revoked_or_stale_identity_cannot_read(receiving_world):
    db, actor, request, *_ = receiving_world
    with pytest.raises(MaterialRequestReadError):
        receiving.list_my_receiving(db, actor=replace(actor, authorization_version=actor.authorization_version + 1), request_id=request.id)
    role_id = db.scalar(select(Role.id).where(Role.code == "technician"))
    permission_id = db.scalar(select(Permission.id).where(Permission.resource == "inventory", Permission.action == "read"))
    db.query(RolePermission).filter(RolePermission.role_id == role_id, RolePermission.permission_id == permission_id).update({"effect": "deny"})
    db.flush()
    with pytest.raises(MaterialRequestReadError, match="本人库存"):
        read(receiving_world)


def test_unknown_request_and_limit_are_rejected(receiving_world):
    db, actor, *_ = receiving_world
    with pytest.raises(MaterialRequestReadError) as error:
        receiving.list_my_receiving(db, actor=actor, request_id=uuid4())
    assert error.value.status_code == 404
    with pytest.raises(MaterialRequestReadError): read(receiving_world, limit=21)
    assert read(receiving_world, after_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")).packages == ()


def test_packages_page_without_duplicates(receiving_world, outbound_world):
    db, recipient, request, location, first, _ = receiving_world
    result = create_outbound(outbound_world, outbound_input(outbound_world, serials=outbound_world[4][1:2]), key="receiving-outbound-second-0001")
    second = shipping.create_shipment(
        db, actor=outbound_world[1], request_id=request.id, expected_version=request.version,
        target_location_id=location.id, target_person_id=recipient.person_id,
        carrier="人工承运", tracking_no="TEST-PACKAGE-002", shipped_at="2026-09-09T10:00:00+08:00",
        lines=(SimpleNamespace(outbound_posting_id=UUID(result["posting_id"]), shipped_qty=Decimal(result["outbound_qty"]), serial_ids=tuple(outbound_world[4][1:2])),),
        idempotency_key="my-receiving-shipment-test-0002", secret=SECRET, trace_request_id="trace-my-receiving-shipment-test-0002",
    )
    db.flush()
    one = read(receiving_world, limit=1)
    two = read(receiving_world, limit=1, after_id=one.next_after_id)
    assert {one.packages[0].shipment_id, two.packages[0].shipment_id} == {first.id, second["shipment_id"]}
    assert two.next_after_id is None


def test_query_rechecks_identity_before_returning(receiving_world, monkeypatch):
    real = receiving.query._load_read_context
    calls = 0
    def drift(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            user = db.get(User, kwargs["actor"].user_id)
            user.authorization_version += 1
            db.flush()
        return real(db, **kwargs)
    monkeypatch.setattr(receiving.query, "_load_read_context", drift)
    with pytest.raises(MaterialRequestReadError):
        read(receiving_world)


def test_http_read_is_no_store_even_when_business_writes_are_disabled(api_client, monkeypatch):
    client, db, principal_box, _, settings = api_client
    settings.material_request_writes_enabled = False
    request_id = uuid4()
    result = receiving.MyReceivingOut(request_id=request_id, request_no="REQ-TEST", request_version=1, person_id=principal_box["value"].person_id, packages=(), next_after_id=None)
    service = Mock(return_value=result)
    monkeypatch.setattr(receiving, "list_my_receiving", service)
    response = client.get(f"/api/v1/material-requests/{request_id}/my-receiving?limit=5")
    assert response.status_code == 200
    assert response.json()["packages"] == []
    assert "no-store" in response.headers["Cache-Control"]
    assert service.call_args.kwargs["limit"] == 5
    db.commit.assert_not_called()
    for invalid in ("0", "21", "invalid"):
        assert client.get(f"/api/v1/material-requests/{request_id}/my-receiving?limit={invalid}").status_code == 422
    assert service.call_count == 1


def test_http_failure_never_falls_back_to_source_history(api_client, monkeypatch):
    client, db, _, _, _ = api_client
    service = Mock(side_effect=MaterialRequestReadError("my_receiving_forbidden", "forbidden", "无本人权限"))
    monkeypatch.setattr(receiving, "list_my_receiving", service)
    response = client.get(f"/api/v1/material-requests/{uuid4()}/my-receiving")
    assert response.status_code == 403
    assert "no-store" in response.headers["Cache-Control"]
    assert "packages" not in response.json()
    db.commit.assert_not_called()
