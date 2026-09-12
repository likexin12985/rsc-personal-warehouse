from decimal import Decimal
from uuid import UUID
import pytest
from app.material_request_receipt_schemas import ReceiptIn, ReceiptOut
from app.formal_services.material_request_receipt import ReceiptError, _receipt_status, _validate_evidence_file, _validate_serial_receipt_quantity

ID = UUID("11111111-1111-1111-1111-111111111111")

def test_receipt_input_is_strict():
    value = ReceiptIn(expected_request_version=2, receiver_person_id=ID, received_at="2026-09-09T10:00:00Z", lines=[{"shipment_line_id": ID, "accepted_qty": "1.000", "rejected_qty": "0.000", "condition": "normal", "exception_evidence_file_id": ID}])
    assert value.lines[0].accepted_qty == 1
    assert value.lines[0].exception_evidence_file_id == ID
    with pytest.raises(Exception):
        ReceiptIn(expected_request_version=2, receiver_person_id=ID, received_at="2026-09-09T10:00:00Z", lines=[{"shipment_line_id": ID, "accepted_qty": "1.000", "rejected_qty": "0.000", "condition": "normal", "extra": True}])

def test_receipt_output_preserves_exception_facts():
    value = ReceiptOut(receipt_id=ID, receipt_no="RCT-1", shipment_id=ID, status="exception", lines=(), exceptions=({"exception_type": "damaged", "detail": "破损"},))
    assert value.schema_version == "1.0"
    assert value.exceptions[0]["exception_type"] == "damaged"

def test_receipt_condition_is_an_explicit_code():
    with pytest.raises(Exception):
        ReceiptIn(expected_request_version=2, receiver_person_id=ID, received_at="2026-09-09T10:00:00Z", lines=[{"shipment_line_id": ID, "accepted_qty": "1.000", "rejected_qty": "0.000", "condition": "free_text"}])

def test_serial_receipt_quantity_must_be_whole_physical_units():
    with pytest.raises(ReceiptError, match="SN"):
        _validate_serial_receipt_quantity(Decimal("1.500"), {ID}, {ID, UUID("22222222-2222-2222-2222-222222222222")}, set())

def test_non_normal_condition_is_exception_even_when_quantity_is_accepted():
    line = type("Line", (), {"accepted_qty": Decimal("1.000"), "rejected_qty": Decimal("0.000"), "condition": "damaged"})()
    assert _receipt_status([(None, line, Decimal("1.000"), set())]) == "exception"

def test_receipt_evidence_must_be_an_available_file():
    class Db:
        def scalar(self, query):
            return None

    with pytest.raises(ReceiptError, match="证据文件"):
        _validate_evidence_file(Db(), ID)
