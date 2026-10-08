from uuid import uuid4
import pytest
from pydantic import ValidationError
from app.return_condition_requests import ConditionSubmit


def test_generic_condition_entry_refuses_before_even_looking_up_replay():
    from datetime import datetime, timezone
    from decimal import Decimal
    from app.formal_access import FormalPrincipal
    from app.formal_services import inventory_posting as posting
    class NoDatabase:
        def __getattr__(self,name):
            raise AssertionError('generic condition request reached database: '+name)
    actor=FormalPrincipal(str(uuid4()),uuid4(),'active','active',1,'active',(),())
    command=posting.InventoryPostingCommand(transaction_no='COND-GENERIC',movement_type='freeze',
        source_document_type='stock_condition_event',source_document_id=str(uuid4()),posting_key=uuid4().hex,
        effective_at=datetime.now(timezone.utc),movements=(
            posting.InventoryMovementCommand(uuid4(),uuid4(),Decimal(1)),))
    with pytest.raises(posting.InventoryPostingError) as error:
        posting.post_inventory_transaction(NoDatabase(),actor=actor,command=command,
            idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert error.value.code=='condition_posting_authority_invalid'

@pytest.mark.parametrize('bad',[True,0.125,'NaN','0','-1','0.0001'])
def test_quantity_contract_rejects_inexact_or_invalid_input(bad):
    with pytest.raises(ValidationError):
        ConditionSubmit(action='submit_return_condition',inbound_line_id=uuid4(),expected_source_hash='a'*64,
            quantity=bad,evidence_file_ids=(uuid4(),),reason='核实',request_id=uuid4().hex,idempotency_key=uuid4().hex)
