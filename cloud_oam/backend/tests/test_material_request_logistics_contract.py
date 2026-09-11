from datetime import datetime, timezone
import pytest

from app.formal_services.material_request_logistics import LogisticsEventError, _ensure_after_shipping, _validate_evidence_file
from app.formal_services import material_request_logistics as logistics
from test_formal_material_request_api import api_client
from unittest.mock import Mock


def test_logistics_event_cannot_predate_shipment():
    shipped = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    with pytest.raises(LogisticsEventError, match="交运时间"):
        _ensure_after_shipping(shipped, datetime(2026, 9, 9, 9, 59, tzinfo=timezone.utc))


def test_logistics_event_accepts_same_or_later_time():
    shipped = datetime(2026, 9, 9, 10, tzinfo=timezone.utc)
    _ensure_after_shipping(shipped, shipped)
    _ensure_after_shipping(shipped, datetime(2026, 9, 9, 10, 1, tzinfo=timezone.utc))


def test_logistics_evidence_must_be_an_available_file():
    class Db:
        def get(self, model, key):
            return None

    with pytest.raises(LogisticsEventError, match="证据文件"):
        _validate_evidence_file(Db(), "11111111-1111-4111-8111-000000000001")

def test_logistics_command_status_http_is_read_only_when_writes_disabled(api_client, monkeypatch):
    client, db, principal_box, _cipher, settings = api_client
    settings.material_request_writes_enabled = False
    service = Mock(return_value=None)
    monkeypatch.setattr(logistics, "logistics_command_status", service)
    request_id = "11111111-1111-1111-1111-111111111111"
    shipment_id = "22222222-2222-2222-2222-222222222222"
    response = client.get(f"/api/v1/material-requests/{request_id}/shipments/{shipment_id}/logistics-command-status",
                          headers={"Idempotency-Key": "logistics-http-recovery-001"})
    assert response.status_code == 200
    assert response.json() == {"schema_version": "1.0", "lookup_status": "not_observed", "command": None}
    assert "no-store" in response.headers["Cache-Control"]
    service.assert_called_once()
    assert service.call_args.kwargs["actor"] is principal_box["value"]
    db.commit.assert_not_called()
