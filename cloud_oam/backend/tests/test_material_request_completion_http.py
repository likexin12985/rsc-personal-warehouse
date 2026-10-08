from unittest.mock import Mock

from app.formal_services import material_request_completion as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_formal_material_request_api import api_client, REQUEST_ID, REVISION_ID, LINE_ID


def test_read_only_http_uses_current_permission_and_no_store(api_client, monkeypatch):
    client, db, principal_box, _, settings = api_client
    settings.material_request_writes_enabled = False
    action = Mock(return_value=dict(request_id=REQUEST_ID, request_version=9, revision_id=REVISION_ID,
        quantity_coverage_complete=False, pending_inbound_orders=1,
        lines=[dict(request_line_id=LINE_ID, approved_qty='3.000', cancelled_qty='0.000',
                    posted_qty='1.000', remaining_qty='2.000')]))
    monkeypatch.setattr(service, 'completion_quantities', action)
    response = client.get(f'/api/v1/material-requests/{REQUEST_ID}/completion-quantities')
    assert response.status_code == 200, response.text
    assert 'no-store' in response.headers['cache-control']
    assert response.json()['quantity_coverage_complete'] is False
    assert action.call_args.kwargs['actor'] is principal_box['value']
    assert action.call_args.kwargs['_include_returns'] is True
    db.commit.assert_not_called()


def test_read_failure_not_returned_as_zero_or_complete(api_client, monkeypatch):
    client, db, *_ = api_client
    monkeypatch.setattr(service, 'completion_quantities', Mock(side_effect=MaterialRequestReadError(
        'closure_cancellation_history_invalid', 'service_unavailable', '取消证据无法核验')))
    response = client.get(f'/api/v1/material-requests/{REQUEST_ID}/completion-quantities')
    assert response.status_code == 503
    assert 'no-store' in response.headers['cache-control']
    assert 'lines' not in response.json()
    db.commit.assert_not_called()
