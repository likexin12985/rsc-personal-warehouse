"""Synthetic H5 loss-submission fixtures must track actual backend wire contracts."""
import json
from pathlib import Path

import pytest

from app.formal_services.stock_loss_plan import intent
from app.formal_services.stock_loss_sources import _hash, selection_hash
from app.stock_loss_schemas import (
    StockLossSelectionIn, StockLossSelectionOut, StockLossSourcesOut,
    StockLossPreviewIn, StockLossPreviewOut, StockLossSubmitIn,
    StockLossRequestLookupIn, StockLossRequestFoundOut, StockLossRequestMissingOut,
    StockLossRequestSealedOut, StockLossSubmittedOut,
)

FIXTURES = Path(__file__).resolve().parents[2] / 'frontend/src/test-fixtures/loss-submission'


@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
@pytest.mark.parametrize('outcome', ['found', 'sealed'])
def test_h5_loss_submission_fixture_matches_current_command_and_recovery(tracking, outcome):
    data = json.loads((FIXTURES / f'loss-submission-{tracking}-{outcome}.json').read_text())
    assert data['synthetic_only'] is True
    schema_pairs = [
        ('sources', StockLossSourcesOut), ('selection_input', StockLossSelectionIn),
        ('selection', StockLossSelectionOut), ('preview_input', StockLossPreviewIn),
        ('preview', StockLossPreviewOut), ('command', StockLossSubmitIn),
        ('lookup_input', StockLossRequestLookupIn), ('missing', StockLossRequestMissingOut),
        ('observed', StockLossRequestFoundOut if outcome == 'found' else StockLossRequestSealedOut),
        ('result', StockLossSubmittedOut if outcome == 'found' else StockLossRequestSealedOut),
    ]
    for key, schema in schema_pairs:
        assert schema.model_validate(data[key]).model_dump(mode='json') == data[key]
    command = StockLossSubmitIn.model_validate(data['command'])
    prepared = data['preview']
    assert _hash(intent(command)) == prepared['request_hash'] == data['lookup_input']['request_hash']
    assert command.expected_plan_hash == prepared['plan_hash'] == data['lookup_input']['expected_plan_hash']
    assert selection_hash(StockLossSelectionIn.model_validate(data['selection_input'])) == data['selection']['selection_hash']
    assert str(command.operator_person_id) == data['identity']['person_id'] == data['sources']['person_id']
    assert prepared['lines'] == data['selection']['lines']
    assert prepared['location_id'] == data['sources']['location_id']
    assert data['missing']['retry_permitted'] is False and data['observed']['retry_permitted'] is False
    assert bool(command.lines[0].serial_verifications) == (tracking == 'serial')
    if outcome == 'found':
        result = data['observed']['submission']
        assert result == data['result'] and result['status'] == 'submitted'
        assert result['posting_transaction_id'] and result['source_location_id'] == prepared['location_id']
        assert result['lines'] == prepared['lines'] and result['evidence'] == prepared['evidence']
    else:
        result = data['observed']['seal']
        assert data['result'] == data['observed'] and 'posting_transaction_id' not in result
    assert result['request_hash'] == prepared['request_hash']
    assert result['plan_hash'] == prepared['plan_hash']
    assert result['request_id'] == command.request_id


def test_h5_loss_serial_choices_match_current_personal_inventory_schema():
    from app.inventory_schemas import PersonalWarehouseSerialPageOut
    data = json.loads((FIXTURES / 'loss-source-serial-options.json').read_text())
    assert data['synthetic_only'] is True
    sources = StockLossSourcesOut.model_validate(data['sources'])
    page = PersonalWarehouseSerialPageOut.model_validate(data['page'])
    assert page.model_dump(mode='json') == data['page']
    assert sources.person_id == page.person_id and sources.location_id == page.location_id
    assert sources.ledger_cursor == page.ledger_cursor and sources.projected_at == page.projected_at
    account = next(row for row in sources.items if row.stock_account_id == page.stock_account_id)
    assert account.material_id == page.material_id and account.tracking_mode == page.tracking_mode
    assert all(row.lifecycle_status == 'active' for row in page.items)
    assert all('qr_code' not in row for row in data['page']['items'])
    assert page.next_after_id is None and len(page.items) == page.total_serials


@pytest.mark.parametrize('wire', [None, '2026-09-30T09:05:01.123456', '2026-09-30T09:05:01.123456+00:00', '2026-09-30T17:05:01.123456+08:00'])
def test_inventory_projection_timestamp_wire_is_utc_without_mutating_snapshot(wire):
    from datetime import datetime
    from app.inventory_schemas import InventoryProjectionOut
    original = datetime.fromisoformat(wire) if wire is not None else None
    model = InventoryProjectionOut(projection_status='ready', opening_balance_status='established', projected_at=original, ledger_cursor=4)
    assert model.projected_at == original
    assert model.model_dump()['projected_at'] == original
    assert model.model_dump(mode='json')['projected_at'] == (None if original is None else '2026-09-30T09:05:01.123456Z')
