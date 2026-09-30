"""The checked-in browser fixtures must remain valid real backend contracts."""
import json
from pathlib import Path

import pytest

from app.formal_services import stock_loss_headquarters_reviews as headquarters
from app.formal_services import stock_loss_regional_reviews as regional
from app.formal_services.stock_loss_sources import _hash
from app.stock_loss_review_query_schemas import LossReviewQueueOut, LossReviewQueryOut
from app.stock_loss_review_seal_schemas import (
    StockLossHeadquartersReviewCommandIn, StockLossRegionalReviewCommandIn,
)
from app.stock_loss_schemas import (
    StockLossHeadquartersReviewIn, StockLossHeadquartersReviewFoundOut,
    StockLossRegionalReviewIn, StockLossRegionalReviewFoundOut,
)

FIXTURES = Path(__file__).resolve().parents[2] / 'frontend/src/test-fixtures/loss-review'


@pytest.mark.parametrize('tracking', ['quantity', 'serial'])
@pytest.mark.parametrize('stage,command_type,result_type,service', [
    ('regional', StockLossRegionalReviewIn, StockLossRegionalReviewFoundOut, regional),
    ('headquarters', StockLossHeadquartersReviewIn, StockLossHeadquartersReviewFoundOut, headquarters),
])
def test_browser_fixture_matches_current_backend(tracking, stage, command_type, result_type, service):
    data = json.loads((FIXTURES / f'contract-{tracking}-{stage}.json').read_text())
    # Exact roundtrips also catch silently ignored old fields and newly added defaults.
    for key, schema in [('queue', LossReviewQueueOut), ('detail', LossReviewQueryOut), ('found', result_type)]:
        assert schema.model_validate(data[key]).model_dump(mode='json') == data[key]
    stored = data['pending']['command']
    envelope = StockLossRegionalReviewCommandIn if stage == 'regional' else StockLossHeadquartersReviewCommandIn
    assert envelope.model_validate(stored).model_dump(mode='json') == stored
    original = command_type.model_validate(stored['original'])
    assert original.model_dump(mode='json') == stored['original']
    assert _hash(service.intent(original)) == stored['request_hash'] == data['found']['review']['request_hash']
    assert stored['operator_person_id'] == data['identity']['person_id'] == data['found']['review']['reviewer_person_id']
    assert data['detail']['report']['operation_id'] == str(original.operation_id)
    assert data['found']['review']['stock_effect'] == 'none'
    if stage == 'headquarters':
        assert data['found']['review']['disposition_stage'] == 'pending'
    serials = [s for line in data['detail']['report']['lines'] for s in line['serials']]
    assert bool(serials) == (tracking == 'serial')
