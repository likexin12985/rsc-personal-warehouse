from dataclasses import replace
from decimal import Decimal
from uuid import uuid4
from unittest.mock import Mock

import pytest
from sqlalchemy import event, select

from app.formal_services import material_request_my_receipt_candidates as candidates
from app.formal_services.material_request_query import MaterialRequestReadError
from app.foundation_models import Permission, RolePermission
from app.inventory_models import CustodyAssignment, InventorySerial, MaterialInventoryPolicy, ReceiptSerial, ShipmentSerial
from app.material_request_my_receipt_candidate_schemas import MyReceiptCandidateOut
from test_material_request_my_receipt import world, receiving_world, outbound_world, create, payload
from test_formal_material_request_api import api_client

pytest_plugins = ("test_material_request_picking",)


def read(world, **kwargs):
    db, actor, request, _, shipment, _ = world
    return candidates.my_receipt_candidates(db, actor=actor, request_id=request.id, shipment_id=kwargs.get("shipment_id", shipment.id))


def test_candidates_are_select_only_and_omit_source_dimensions(world):
    db, actor, request, _, shipment, line = world
    serials = tuple(db.scalars(select(ShipmentSerial.serial_id).where(ShipmentSerial.shipment_line_id == line.id)).all())
    statements = []
    def record(_conn, _cursor, statement, *_args): statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", record)
    # Pending unrelated data must not be flushed by a read.
    request.purpose = "unsaved caller change"
    try:
        result = read(world)
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert result.can_receive and result.blocked_reason is None
    assert result.shipment_id == shipment.id and result.person_id == actor.person_id
    assert result.lines[0].unconfirmed_qty == format(line.shipped_qty, ".3f")
    assert {s.serial_id for s in result.lines[0].remaining_serials} == set(serials)
    assert result.shipped_at.tzinfo is not None and result.checked_at.tzinfo is not None
    output = result.model_dump_json()
    for private in ("source_stock_account", "source_location", "owner_org", "outbound_posting", "balance"):
        assert private not in output


def test_completed_acceptance_removes_candidates_without_claiming_inbound(world):
    before = read(world)
    create(world)
    after = read(world)
    assert after.request_version == before.request_version + 1
    assert after.blocked_reason == "complete" and not after.can_receive
    assert after.lines[0].unconfirmed_qty == "0.000"
    assert after.lines[0].remaining_serials == ()
    assert "posted" not in after.model_dump_json()


def test_partial_decimal_acceptance_keeps_exact_remaining_quantity(world):
    if read(world).lines[0].remaining_serials:
        return  # A one-SN package cannot be fractionally accepted.
    value = payload(world).model_dump()
    value["lines"][0]["accepted_qty"] = "0.025"
    create(world, value=type(payload(world))(**value))
    result = read(world)
    assert result.can_receive
    assert result.lines[0].accepted_qty == "0.025"
    assert result.lines[0].unconfirmed_qty == format(world[-1].shipped_qty - Decimal("0.025"), ".3f")


def test_read_remains_available_when_receive_is_denied(world):
    db, *_ = world
    permission = db.scalar(select(Permission).where(Permission.resource == "material_request", Permission.action == "receive"))
    db.query(RolePermission).filter(RolePermission.permission_id == permission.id).update({"effect": "deny"})
    db.flush()
    result = read(world)
    assert result.blocked_reason == "permission_required" and not result.can_receive
    assert result.lines


@pytest.mark.parametrize("change", ["recipient", "location", "unknown_package", "stale_identity", "policy", "serial_material"])
def test_crossed_context_or_unverifiable_candidates_are_rejected(world, change, monkeypatch):
    db, actor, request, location, shipment, _ = world
    if change == "recipient": shipment.target_person_id = uuid4()
    if change == "location": location.status = "inactive"
    if change == "unknown_package":
        with pytest.raises(MaterialRequestReadError): read(world, shipment_id=uuid4())
        return
    if change == "stale_identity":
        with pytest.raises(MaterialRequestReadError):
            candidates.my_receipt_candidates(db, actor=replace(actor, authorization_version=actor.authorization_version + 1), request_id=request.id, shipment_id=shipment.id)
        return
    if change == "policy": db.query(MaterialInventoryPolicy).delete()
    if change == "serial_material":
        serials = read(world).lines[0].remaining_serials
        if not serials: return
        real = candidates.receipt_service._package
        def corrupted_projection(*args):
            result = real(*args)
            db.get(InventorySerial, serials[0].serial_id).material_id = uuid4()
            return result
        monkeypatch.setattr(candidates.receipt_service, "_package", corrupted_projection)
    db.flush()
    with pytest.raises(MaterialRequestReadError): read(world)


@pytest.mark.parametrize("change", ["result", "missing"])
def test_accepted_rejected_serial_mismatch_never_becomes_available(world, change):
    if not read(world).lines[0].remaining_serials: return
    create(world)
    serial = world[0].scalar(select(ReceiptSerial))
    if change == "result": serial.accepted = False
    else: world[0].delete(serial)
    world[0].flush()
    with pytest.raises(MaterialRequestReadError, match="数量与 SN"):
        read(world)


def test_candidate_query_rechecks_request_version(world, monkeypatch):
    real = candidates.receipt_service._context
    calls = 0
    def drift(db, *args, **kwargs):
        nonlocal calls
        context, request = real(db, *args, **kwargs)
        calls += 1
        if calls == 2: request.version += 1
        return context, request
    monkeypatch.setattr(candidates.receipt_service, "_context", drift)
    with pytest.raises(MaterialRequestReadError, match="已变化"):
        read(world)


def test_candidate_query_rechecks_custody_before_returning(world, monkeypatch):
    real = candidates.receipt_service._context
    calls = 0
    def drift(db, *args, **kwargs):
        nonlocal calls
        result = real(db, *args, **kwargs)
        calls += 1
        if calls == 2:
            db.query(CustodyAssignment).filter(CustodyAssignment.location_id == world[3].id).delete()
            db.flush()
        return result
    monkeypatch.setattr(candidates.receipt_service, "_context", drift)
    with pytest.raises(MaterialRequestReadError, match="保管责任"):
        read(world)


def test_route_is_readonly_no_store_with_writes_disabled(api_client, monkeypatch):
    from datetime import datetime, timezone
    client, db, principals, _, settings = api_client
    settings.material_request_writes_enabled = False
    request_id, shipment_id = uuid4(), uuid4()
    result = MyReceiptCandidateOut(request_id=request_id, request_no="TEST", request_version=4,
        person_id=principals["value"].person_id, shipment_id=shipment_id, shipment_no="SHP-TEST",
        shipped_at=datetime.now(timezone.utc), target_location_name="个人仓", checked_at=datetime.now(timezone.utc),
        can_receive=False, blocked_reason="complete", lines=())
    mocked = Mock(return_value=result)
    monkeypatch.setattr(candidates, "my_receipt_candidates", mocked)
    response = client.get(f"/api/v1/material-requests/{request_id}/my-receiving/{shipment_id}/candidates")
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert mocked.call_args.kwargs["shipment_id"] == shipment_id
    db.commit.assert_not_called()
    mocked.side_effect = MaterialRequestReadError("blocked", "not_found", "包裹不存在")
    response = client.get(f"/api/v1/material-requests/{request_id}/my-receiving/{shipment_id}/candidates")
    assert response.status_code == 404 and "no-store" in response.headers["Cache-Control"]
    assert "lines" not in response.json()
