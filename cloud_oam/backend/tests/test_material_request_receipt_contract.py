from uuid import UUID
import pytest
from app.material_request_receipt_schemas import ReceiptIn, ReceiptOut

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
