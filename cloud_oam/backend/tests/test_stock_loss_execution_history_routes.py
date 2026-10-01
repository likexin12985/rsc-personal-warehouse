"""Run complete original-history scenarios through the registered HTTP read.

SQLite bindings are explicit fixtures, not native registration evidence.
Only transport is replaced; real approvals, postings and corruptions remain
those of the original service scenario, including its exact snapshots.
"""
import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services.inventory_query import InventoryReadError
from app.main import app
import test_stock_loss_original_history_recovery as scenarios
from test_stock_loss_original_history_recovery import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready,
)
from test_stock_loss_source_routes import PATH, private


pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)


@pytest.mark.parametrize('kind', ['restore_available', 'convert_used', 'convert_damaged'])
def test_registered_http_preserves_original_through_corrected_history(
        db, inverse_ready, execution, kind, monkeypatch):
    observations = []
    monkeypatch.setattr(app, 'dependency_overrides', {get_db: lambda: db})
    client = TestClient(app, raise_server_exceptions=False)
    try:
        def read(session, world, original, command=None):
            assert session is db and original.flow == 'disposition'
            actor = world.actor
            app.dependency_overrides[get_formal_principal] = lambda: actor
            request = command or original.command
            response = client.post(PATH + '/dispositions/request-lookup',
                json=request.model_dump(mode='json'))
            private(response)
            assert request.idempotency_key not in response.text
            assert response.status_code in (200, 403, 409, 503), response.text
            observations.append(response.status_code)
            answer = response.json()
            if response.status_code != 200:
                # The service scenario asserts statuses and preserved facts.
                # Convert only the checked public error; never invoke the service
                # directly as a fallback when the HTTP response fails.
                detail = answer['detail']
                if response.status_code == 403 and isinstance(detail, str):
                    assert detail == '没有此操作权限'
                    raise InventoryReadError(code='forbidden', status_code=403, message=detail)
                assert isinstance(detail, dict) and isinstance(detail['code'], str)
                raise InventoryReadError(code=detail['code'],
                    status_code=response.status_code, message=detail['message'])
            assert answer['result_scope'] == 'original_command'
            assert answer['lookup_status'] == 'found' and answer['retry_permitted'] is False
            return {key: value for key, value in answer.items() if key != 'result_scope'}

        monkeypatch.setattr(scenarios, 'read', read)
        scenarios.test_original_outcome_survives_inverse_approval_and_correction(
            db, inverse_ready, execution, kind, monkeypatch)
    finally:
        client.close()
    assert observations.count(200) == 3
    assert observations.count(409) == 3
    assert observations.count(503) == 7
    assert observations.count(403) == 1
