from uuid import UUID
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
import app.formal_services.material_request_inbound as inbound_service
from app.formal_services.material_request_inbound import InboundError, _validate_inbound_target
from app.material_request_inbound_schemas import InboundOrderIn, InboundOrderOut, InboundPostingOut

ID = UUID("11111111-1111-1111-1111-111111111111")

def test_inbound_order_input_is_strict():
    value = InboundOrderIn(expected_request_version=2, receipt_id=ID, target_location_id=ID, target_person_id=ID)
    assert value.expected_request_version == 2
    with pytest.raises(Exception):
        InboundOrderIn(expected_request_version=2, receipt_id=ID, target_location_id=ID, target_person_id=ID, extra=True)

def test_inbound_order_input_rejects_zero_version():
    with pytest.raises(Exception):
        InboundOrderIn(expected_request_version=0, receipt_id=ID, target_location_id=ID, target_person_id=ID)

def test_inbound_outputs_keep_schema_version():
    order = InboundOrderOut(inbound_order_id=ID, inbound_no="INB-1", receipt_id=ID, target_location_id=ID, target_person_id=ID, status="pending")
    posted = InboundPostingOut(inbound_order_id=ID, inventory_transaction_id=ID)
    assert order.schema_version == posted.schema_version == "1.0"

def test_inbound_history_allows_derived_posted_status():
    order = InboundOrderOut(inbound_order_id=ID, inbound_no="INB-1", receipt_id=ID, target_location_id=ID, target_person_id=ID, status="posted")
    assert order.status == "posted"

def test_inbound_target_must_match_shipment_destination():
    shipment = SimpleNamespace(target_location_id=ID, target_person_id=ID)
    with pytest.raises(InboundError, match="发运事实"):
        _validate_inbound_target(shipment, UUID("22222222-2222-2222-2222-222222222222"), ID)


def test_inbound_posting_skips_rejected_only_receipt_lines(monkeypatch):
    # Exercise the command builder, where accepted quantities are selected.
    # Full transaction locking, posting, audit and outbox are covered by the
    # orchestration suite and actual PG16 gate, not a fake SQLAlchemy Session.
    order = SimpleNamespace(id=ID, receipt_id=ID, target_location_id=ID, target_person_id=ID, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    accepted = SimpleNamespace(id=UUID("22222222-2222-2222-2222-222222222222"), shipment_line_id=ID, accepted_qty=Decimal("2.000"))
    rejected = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), shipment_line_id=UUID("44444444-4444-4444-4444-444444444444"), accepted_qty=Decimal("0.000"))
    db = SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: [accepted, rejected]))
    resolved, built = [], []
    target = SimpleNamespace(id=ID)
    movement = SimpleNamespace(quantity=accepted.accepted_qty)
    def resolve(db, **kwargs):
        resolved.append(kwargs)
        return target
    def build(db, **kwargs):
        built.append(kwargs)
        return SimpleNamespace(movements=(movement,))
    monkeypatch.setattr(inbound_service, "resolve_personal_target_account", resolve)
    monkeypatch.setattr(inbound_service, "build_inbound_posting_command", build)

    command = inbound_service._order_posting_command(db, order, create_missing=True)

    assert command.movements == (movement,)
    assert command.source_document_type == "personal_inbound"
    assert command.source_document_id == str(order.id)
    assert resolved == [dict(receipt_id=order.receipt_id, target_location_id=order.target_location_id,
        target_person_id=order.target_person_id, shipment_line_id=accepted.shipment_line_id, create=True)]
    assert built == [dict(inbound_order=order, receipt_line_id=accepted.id, target_account=target)]


def test_inbound_posting_rejects_receipt_with_no_accepted_quantity(monkeypatch):
    order = SimpleNamespace(id=ID, receipt_id=ID, target_location_id=ID, target_person_id=ID, created_at=datetime(2026, 9, 1, tzinfo=timezone.utc))
    rejected = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), shipment_line_id=ID, accepted_qty=Decimal("0.000"))
    db = SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: [rejected]))
    def forbidden(*args, **kwargs):
        raise AssertionError("Rejected-only receipt must not resolve/create accounts or build movements")
    monkeypatch.setattr(inbound_service, "resolve_personal_target_account", forbidden)
    monkeypatch.setattr(inbound_service, "build_inbound_posting_command", forbidden)

    with pytest.raises(inbound_service.InboundError, match="合格数量"):
        inbound_service._order_posting_command(db, order, create_missing=True)
