from unittest.mock import Mock
from uuid import UUID

from app.formal_services import material_request_oam_receipt as oam_receipt
from test_formal_material_request_api import api_client


def test_oam_receipt_evidence_http_is_read_only_when_writes_disabled(api_client, monkeypatch):
    client, db, principal_box, _cipher, settings = api_client
    settings.material_request_writes_enabled = False
    evidence_id = UUID("33333333-3333-4333-8333-333333333333")
    external_id = UUID("44444444-4444-4444-8444-444444444444")
    shipment_id = UUID("55555555-5555-4555-8555-555555555555")
    service = Mock(return_value=({
        "schema_version": "1.0",
        "evidence_id": evidence_id,
        "external_object_id": external_id,
        "shipment_id": shipment_id,
        "status": "synced",
        "source_time": "2026-09-11T08:00:00Z",
        "source_version": "oam-receipt-v1",
        "payload_sha256": "a" * 64,
    },))
    monkeypatch.setattr(oam_receipt, "list_oam_receipt_evidence", service)
    request_id = "11111111-1111-1111-1111-111111111111"
    response = client.get(f"/api/v1/material-requests/{request_id}/oam-receipt-evidence")
    assert response.status_code == 200
    assert response.json()[0]["evidence_id"] == str(evidence_id)
    assert response.json()[0]["status"] == "synced"
    assert "no-store" in response.headers["Cache-Control"]
    service.assert_called_once()
    assert service.call_args.kwargs["actor"] is principal_box["value"]
    db.commit.assert_not_called()
