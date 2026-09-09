"""ORM checks for the request-level personal-inbound state projection."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.demand_models import MaterialRequestLine
from app.inventory_models import (
    FormalMaterial,
    InboundOrder,
    InboundPosting,
    InventoryTransaction,
    OutboundPosting,
    Receipt,
    ReceiptLine,
    Shipment,
    ShipmentLine,
)
from app.formal_services.material_request_inbound_state import personal_inbound_state
from test_material_request_outbound import outbound_world, _create as create_outbound
# Import the transitive fixture names into this module as well.  Pytest scopes
# fixtures declared in test modules to the module that imports them.
from test_material_request_picking import pick_world  # noqa: F401
from test_material_request_reservation_release import release_world  # noqa: F401
from test_material_request_approval_service import approval_db  # noqa: F401


def _current_line(db, request):
    return db.scalar(
        select(MaterialRequestLine).where(
            MaterialRequestLine.request_id == request.id,
            MaterialRequestLine.revision_no == request.revision_no,
        )
    )


def _transaction(db, actor, suffix, *, source_document_id=None):
    tx = InventoryTransaction(
        id=uuid4(), transaction_no=f"INV-STATE-{suffix}", movement_type="transfer",
        source_document_type="personal_inbound",
        source_document_id=source_document_id or str(uuid4()),
        posting_key=f"state-posting-{suffix}", idempotency_key_hash=(f"{suffix:0>64}")[-64:],
        request_hash="a" * 64, status="posted", effective_at=datetime.now(timezone.utc),
        posted_at=datetime.now(timezone.utc), ledger_cursor=900000 + int(suffix),
        actor_user_id=actor.user_id,
    )
    db.add(tx)
    db.flush()
    return tx


def _receipt(db, actor, request, posting, *, accepted, rejected="0.000", posted=False, suffix="1"):
    now = datetime.now(timezone.utc)
    shipment = Shipment(
        id=uuid4(), shipment_no=f"SHP-STATE-{suffix}", source_location_id=uuid4(),
        target_location_id=uuid4(), target_person_id=actor.person_id, carrier="test",
        tracking_no=f"TRACK-{suffix}", status="shipped", shipped_at=now,
        idempotency_key_hash=(f"{suffix:0>64}")[-64:], request_hash="b" * 64,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version,
    )
    db.add(shipment)
    db.flush()
    shipment_line = ShipmentLine(
        id=uuid4(), shipment_id=shipment.id, outbound_posting_id=posting.id,
        outbound_line_id=posting.outbound_line_id, shipped_qty=Decimal(accepted) + Decimal(rejected),
    )
    db.add(shipment_line)
    db.flush()
    receipt = Receipt(
        id=uuid4(), receipt_no=f"RCT-STATE-{suffix}", shipment_id=shipment.id,
        status="accepted" if Decimal(rejected) == 0 else "exception", received_at=now,
        receiver_person_id=actor.person_id, request_hash="c" * 64,
        idempotency_key_hash=(f"{int(suffix) + 100:0>64}")[-64:],
    )
    db.add(receipt)
    db.flush()
    line = ReceiptLine(
        id=uuid4(), receipt_id=receipt.id, shipment_line_id=shipment_line.id,
        accepted_qty=Decimal(accepted), rejected_qty=Decimal(rejected),
        condition="normal" if Decimal(rejected) == 0 else "rejected",
    )
    db.add(line)
    db.flush()
    if posted:
        order = InboundOrder(
            id=uuid4(), inbound_no=f"INB-STATE-{suffix}", receipt_id=receipt.id,
            target_location_id=shipment.target_location_id, target_person_id=actor.person_id,
            status="posted", posting_transaction_id=None, created_at=now,
        )
        db.add(order)
        db.flush()
        tx = _transaction(
            db, actor, int(suffix) + 500, source_document_id=str(order.id)
        )
        order.posting_transaction_id = tx.id
        db.add(InboundPosting(id=uuid4(), inbound_order_id=order.id, inventory_transaction_id=tx.id))
        db.flush()
    return receipt


def _state_world(outbound_world):
    db, actor, request, *_ = outbound_world
    result = create_outbound(outbound_world)
    posting = db.get(OutboundPosting, UUID(result["posting_id"]))
    line = _current_line(db, request)
    line.final_approved_qty = Decimal("1.000")
    line.cancelled_qty = Decimal("0.000")
    db.flush()
    return db, actor, request, posting, line


def test_split_receipts_progress_pending_partial_accepted_then_posted(outbound_world):
    db, actor, request, posting, _ = _state_world(outbound_world)
    _receipt(db, actor, request, posting, accepted="0.400", suffix="1")
    assert personal_inbound_state(db, request) == "partially_accepted"
    _receipt(db, actor, request, posting, accepted="0.600", suffix="2")
    assert personal_inbound_state(db, request) == "accepted"

    # Each receipt is independently posted; the second one alone cannot make
    # the whole request posted.
    receipts = tuple(db.scalars(select(Receipt).order_by(Receipt.receipt_no)).all())
    for index, receipt in enumerate(receipts, start=3):
        # Reuse the receipt's shipment posting through a tiny accepted line
        # fact; this keeps the test focused on the state aggregate.
        shipment_line = db.scalar(select(ShipmentLine).where(ShipmentLine.shipment_id == receipt.shipment_id))
        order = InboundOrder(id=uuid4(), inbound_no=f"INB-STATE-POST-{index}", receipt_id=receipt.id,
            target_location_id=uuid4(), target_person_id=actor.person_id, status="posted",
            posting_transaction_id=None, created_at=datetime.now(timezone.utc))
        db.add(order); db.flush()
        tx = _transaction(
            db, actor, index + 500, source_document_id=str(order.id)
        )
        order.posting_transaction_id = tx.id
        db.add(InboundPosting(id=uuid4(), inbound_order_id=order.id, inventory_transaction_id=tx.id)); db.flush()
        if index == 3:
            assert personal_inbound_state(db, request) == "accepted"
    assert personal_inbound_state(db, request) == "posted"


def test_rejected_only_receipt_is_pending_acceptance(outbound_world):
    db, actor, request, posting, _ = _state_world(outbound_world)
    _receipt(db, actor, request, posting, accepted="0.000", rejected="1.000", suffix="11")
    assert personal_inbound_state(db, request) == "pending_acceptance"


def test_different_material_lines_cannot_cancel_each_other(outbound_world):
    db, actor, request, posting, first = _state_world(outbound_world)
    material_ids = tuple(db.scalars(select(FormalMaterial.id).limit(2)).all())
    second = MaterialRequestLine(
        id=uuid4(), request_id=request.id, revision_id=first.revision_id,
        revision_no=request.revision_no, line_no=first.line_no + 1, client_line_key=uuid4(),
        material_id=next(value for value in material_ids if value != first.material_id),
        requested_qty=Decimal("1.000"), status="approved", final_approved_qty=Decimal("1.000"),
        cancelled_qty=Decimal("0.000"), note="",
    )
    db.add(second); db.flush()
    # The first line is over-accepted while the other material has no receipt.
    # A request-level sum would incorrectly report all quantity accepted.
    _receipt(db, actor, request, posting, accepted="2.000", suffix="21")
    assert personal_inbound_state(db, request) == "partially_accepted"


def test_all_cancelled_lines_are_not_vacuously_posted(outbound_world):
    db, _, request, _, line = _state_world(outbound_world)
    line.cancelled_qty = line.final_approved_qty
    line.status = "cancelled"
    db.flush()
    assert personal_inbound_state(db, request) == "not_started"
