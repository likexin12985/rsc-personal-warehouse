"""Untrusted selection cannot override persisted condition-correction sources."""
from uuid import UUID, uuid4
import pytest
from pydantic import ValidationError
from app.formal_services.stock_loss_corrections import return_condition_source as subject


@pytest.mark.parametrize('extra', ['quantity', 'source_account_id', 'serial_ids', 'target_condition'])
def test_client_cannot_supply_stock_dimensions(extra):
    with pytest.raises(ValidationError):
        subject.ConditionSourceSelection.model_validate(dict(root_disposition_id=uuid4(), inbound_line_id=uuid4(),
            expected_history_fingerprint='a'*64, **{extra: 'untrusted'}))


def test_model_copy_does_not_bypass_identifier_validation():
    value = subject.ConditionSourceSelection(root_disposition_id=uuid4(), inbound_line_id=uuid4(),
        expected_history_fingerprint='a'*64).model_copy(update={'inbound_line_id': UUID(int=0)})
    with pytest.raises(ValidationError):
        subject.inspect_source(None, actor=None, selection=value)
