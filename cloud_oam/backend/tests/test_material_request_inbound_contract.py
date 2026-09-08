from uuid import UUID
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
    order = SimpleNamespace(id=ID, receipt_id=ID, target_location_id=ID, target_person_id=ID, created_at=None)
    receipt = SimpleNamespace(id=ID, shipment_id=ID, status="exception")
    accepted = SimpleNamespace(id=UUID("22222222-2222-2222-2222-222222222222"), shipment_line_id=ID, accepted_qty=Decimal("2.000"))
    rejected = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), shipment_line_id=UUID("44444444-4444-4444-4444-444444444444"), accepted_qty=Decimal("0.000"))

    class ScalarResult:
        def __init__(self, value): self.value = value
        def first(self): return self.value

    class FakeDb:
        def __init__(self): self.added = []
        def get(self, model, key):
            if model is inbound_service.InboundOrder: return order
            if model is inbound_service.Receipt: return receipt
            if model is inbound_service.MaterialRequest: return SimpleNamespace(id=ID)
            if model is inbound_service.Shipment: return SimpleNamespace(target_location_id=ID, target_person_id=ID)
            return None
        def scalar(self, statement):
            if "FROM inbound_orders" in str(statement): return order
            if "FROM material_requests" in str(statement): return SimpleNamespace(id=ID)
            return None
        def scalars(self, statement):
            return SimpleNamespace(all=lambda: [ID] if "FROM outbound_postings" in str(statement) else [accepted, rejected])
        def add(self, value): self.added.append(value)

    db = FakeDb()
    monkeypatch.setattr(inbound_service, "resolve_personal_target_account", lambda db, **kwargs: SimpleNamespace(id=ID))
    monkeypatch.setattr(inbound_service, "build_inbound_posting_command", lambda db, **kwargs: SimpleNamespace(movements=(SimpleNamespace(quantity=Decimal("2.000")),)))
    monkeypatch.setattr(inbound_service, "post_inventory_transaction", lambda *args, **kwargs: SimpleNamespace(transaction_id=ID, replayed=False))

    result = inbound_service.post_inbound_order(db, actor=SimpleNamespace(user_id=ID), inbound_order_id=ID, material_request_id=ID, idempotency_key="idem", request_id="trace")
    assert result["inventory_transaction_id"] == ID
    assert len(db.added) == 1


def test_inbound_posting_rejects_receipt_with_no_accepted_quantity(monkeypatch):
    order = SimpleNamespace(id=ID, receipt_id=ID, target_location_id=ID, target_person_id=ID, created_at=None)
    receipt = SimpleNamespace(id=ID, shipment_id=ID, status="exception")
    rejected = SimpleNamespace(id=UUID("33333333-3333-3333-3333-333333333333"), shipment_line_id=ID, accepted_qty=Decimal("0.000"))

    class FakeDb:
        def get(self, model, key):
            if model is inbound_service.InboundOrder: return order
            if model is inbound_service.Receipt: return receipt
            if model is inbound_service.MaterialRequest: return SimpleNamespace(id=ID)
            if model is inbound_service.Shipment: return SimpleNamespace(target_location_id=ID, target_person_id=ID)
            return None
        def scalar(self, statement):
            if "FROM inbound_orders" in str(statement): return order
            if "FROM material_requests" in str(statement): return SimpleNamespace(id=ID)
            return None
        def scalars(self, statement): return SimpleNamespace(all=lambda: [ID] if "FROM outbound_postings" in str(statement) else [rejected])

    with pytest.raises(inbound_service.InboundError, match="合格数量"):
        inbound_service.post_inbound_order(FakeDb(), actor=SimpleNamespace(user_id=ID), inbound_order_id=ID, material_request_id=ID, idempotency_key="idem", request_id="trace")
