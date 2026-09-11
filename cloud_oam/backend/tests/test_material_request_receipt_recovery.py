from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from app.formal_services import material_request_receipt as receipt
from app.formal_services import material_request_shipment as shipment
from app.formal_services.material_request_query import MaterialRequestReadError
from app.inventory_models import Receipt, ReceiptException, ReceiptLine, ReceiptSerial, Shipment, ShipmentLine
from test_formal_material_request_api import api_client
from test_material_request_draft_service import SECRET
from test_material_request_outbound import outbound_world, _create as create_outbound

pytest_plugins = ("test_material_request_picking",)


def _create_receipt(world, key="receipt-recovery-command-001"):
    db, actor, request, _fact, serials, _calls, _target_id = world
    outbound_result = create_outbound(world, key=f"outbound-for-{key}")
    shipment_result = shipment.create_shipment(
        db, actor=actor, request_id=request.id, expected_version=request.version,
        target_location_id=uuid4(), target_person_id=actor.person_id,
        carrier="人工承运", tracking_no=f"REC-{key[-6:]}",
        shipped_at="2026-09-09T10:00:00+08:00",
        lines=(SimpleNamespace(
            outbound_posting_id=UUID(outbound_result["posting_id"]),
            shipped_qty=Decimal(outbound_result["outbound_qty"]),
            serial_ids=tuple(serials[:1]),
        ),), idempotency_key=f"ship-{key}", secret=SECRET,
        trace_request_id=f"trace-ship-{key}",
    )
    # SQLite test sessions drop timezone metadata from DateTime columns.
    db.get(Shipment, shipment_result["shipment_id"]).shipped_at = datetime(
        2026, 9, 9, 2, tzinfo=timezone.utc
    )
    line = shipment_result["lines"][0]
    result = receipt.create_receipt(
        db, actor=actor, request_id=request.id, expected_version=request.version,
        receiver_person_id=actor.person_id,
        received_at="2026-09-10T10:00:00+08:00",
        lines=(SimpleNamespace(
            shipment_line_id=line["shipment_line_id"],
            accepted_qty=Decimal(line["shipped_qty"]), rejected_qty=Decimal("0.000"),
            condition="normal", serial_ids=tuple(serials[:1]),
            exception_evidence_file_id=None,
        ),), idempotency_key=key, secret=SECRET,
        trace_request_id=f"trace-{key}",
    )
    db.flush()
    return result


def test_receipt_command_status_recovers_exact_result(outbound_world):
    db, actor, request, *_ = outbound_world
    result = _create_receipt(outbound_world)
    recovered = receipt.receipt_command_status(
        db, actor=actor, request_id=request.id,
        idempotency_key="receipt-recovery-command-001", secret=SECRET,
    )
    assert recovered["request_hash"] == db.get(Receipt, result["receipt_id"]).request_hash
    assert recovered["command"]["receipt_id"] == result["receipt_id"]
    assert recovered["command"]["idempotency_replayed"] is True


def test_receipt_command_status_missing_is_not_observed(outbound_world):
    db, actor, request, *_ = outbound_world
    assert receipt.receipt_command_status(
        db, actor=actor, request_id=request.id,
        idempotency_key="receipt-recovery-command-missing", secret=SECRET,
    ) is None


def test_receipt_command_status_rejects_tampered_hash(outbound_world):
    db, actor, request, *_ = outbound_world
    result = _create_receipt(outbound_world)
    db.get(Receipt, result["receipt_id"]).request_hash = "f" * 63 + "x"
    with pytest.raises(receipt.ReceiptError) as caught:
        receipt.receipt_command_status(
            db, actor=actor, request_id=request.id,
            idempotency_key="receipt-recovery-command-001", secret=SECRET,
        )
    assert caught.value.code == "history_invalid"


def test_receipt_command_status_rejects_missing_lines(outbound_world):
    db, actor, request, *_ = outbound_world
    result = _create_receipt(outbound_world)
    db.query(ReceiptException).filter(ReceiptException.receipt_id == result["receipt_id"]).delete()
    db.query(ReceiptSerial).filter(ReceiptSerial.receipt_line_id.in_(
        db.query(ReceiptLine.id).filter(ReceiptLine.receipt_id == result["receipt_id"])
    )).delete(synchronize_session=False)
    db.query(ReceiptLine).filter(ReceiptLine.receipt_id == result["receipt_id"]).delete()
    db.flush()
    with pytest.raises(receipt.ReceiptError) as caught:
        receipt.receipt_command_status(
            db, actor=actor, request_id=request.id,
            idempotency_key="receipt-recovery-command-001", secret=SECRET,
        )
    assert caught.value.code == "history_invalid"


def test_receipt_command_status_revalidates_current_principal(outbound_world):
    db, actor, request, *_ = outbound_world
    _create_receipt(outbound_world)
    with pytest.raises(MaterialRequestReadError) as caught:
        receipt.receipt_command_status(
            db, actor=replace(actor, authorization_version=actor.authorization_version + 1),
            request_id=request.id, idempotency_key="receipt-recovery-command-001", secret=SECRET,
        )
    assert caught.value.code == "material_request_actor_principal_stale"


def test_receipt_command_status_validates_secret_and_key(outbound_world):
    db, actor, request, *_ = outbound_world
    with pytest.raises(receipt.ReceiptError) as secret_error:
        receipt.receipt_command_status(db, actor=actor, request_id=request.id,
                                       idempotency_key="receipt-recovery-command-001", secret="short")
    assert secret_error.value.code == "secret_invalid"
    with pytest.raises(receipt.ReceiptError) as key_error:
        receipt.receipt_command_status(db, actor=actor, request_id=request.id,
                                       idempotency_key="short", secret=SECRET)
    assert key_error.value.code == "idempotency_key_invalid"


def test_receipt_command_status_rechecks_source_inventory_permission(outbound_world, monkeypatch):
    db, actor, request, *_ = outbound_world
    _create_receipt(outbound_world)
    def deny(*_args, **_kwargs):
        raise receipt.ReceiptError("forbidden", "forbidden", "库存读取未授权")
    monkeypatch.setattr(receipt.outbound, "_authorize_account_ids", deny)
    with pytest.raises(receipt.ReceiptError, match="库存读取"):
        receipt.receipt_command_status(db, actor=actor, request_id=request.id,
                                       idempotency_key="receipt-recovery-command-001", secret=SECRET)


def test_receipt_command_status_http_is_readable_when_writes_disabled(api_client, monkeypatch):
    client, db, principal_box, _cipher, settings = api_client
    settings.material_request_writes_enabled = False
    service = Mock(return_value=None)
    monkeypatch.setattr(receipt, "receipt_command_status", service)
    request_id = "11111111-1111-1111-1111-111111111111"
    response = client.get(f"/api/v1/material-requests/{request_id}/receipt-command-status",
                          headers={"Idempotency-Key": "receipt-http-recovery-001"})
    assert response.status_code == 200
    assert response.json() == {"schema_version": "1.0", "lookup_status": "not_observed",
                               "request_hash": None, "command": None}
    assert "no-store" in response.headers["Cache-Control"]
    service.assert_called_once()
    assert service.call_args.kwargs["actor"] is principal_box["value"]
    db.commit.assert_not_called()


def test_receipt_command_status_http_rejects_short_key(api_client, monkeypatch):
    client, _db, _principal, _cipher, _settings = api_client
    service = Mock(return_value=None)
    monkeypatch.setattr(receipt, "receipt_command_status", service)
    request_id = "11111111-1111-1111-1111-111111111111"
    response = client.get(f"/api/v1/material-requests/{request_id}/receipt-command-status",
                          headers={"Idempotency-Key": "short"})
    assert response.status_code == 400
    service.assert_not_called()
