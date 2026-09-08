from uuid import UUID
import pytest
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
