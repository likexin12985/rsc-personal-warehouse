"""A generic inventory reversal must not detach the original work-order facts."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.inventory_models import InventoryTransaction
from app.formal_services import inventory_posting as posting
from app.formal_services import work_order_material as material, work_order_replacements as paired
from test_work_order_material_options import db, world, stock


@pytest.mark.parametrize("source_type", ["reversal_case", "work_order_material"])
@pytest.mark.parametrize("original_type", ["reserve", "consume"])
def test_generic_reversal_requires_the_work_order_command(db, stock, source_type, original_type):
    original = db.scalar(select(InventoryTransaction).where(
        InventoryTransaction.source_document_type == "work_order_material",
        InventoryTransaction.source_document_id == str(stock.orders[0].id),
        InventoryTransaction.movement_type == original_type))
    assert original is not None
    with pytest.raises(posting.InventoryPostingError) as caught:
        posting.reverse_inventory_transaction(db, actor=stock.actor,
            command=posting.InventoryReversalCommand(
                original_transaction_id=original.id, transaction_no="RV-" + uuid4().hex,
                source_document_type=source_type, source_document_id=str(stock.orders[0].id),
                posting_key="work-order-reversal:" + uuid4().hex,
                effective_at=datetime.now(timezone.utc)),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
    assert caught.value.code == "work_order_reversal_requires_command"
    assert db.scalar(select(InventoryTransaction.id).where(
        InventoryTransaction.reversed_transaction_id == original.id)) is None


@pytest.mark.parametrize("child", ["consume", "recover"])
def test_neither_child_of_a_paired_replacement_can_be_generically_reversed(db, stock, child):
    consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
    removed = stock.serials[:1]
    recovered = paired.RecoveryLineInput(stock.reserved.id, stock.world.material.id,
        consumed.quantity, "used", serial_ids=tuple(sn.id for sn in removed),
        serial_verifications=tuple(material.SerialVerificationInput(sn.id, stock.world.material.sku_code,
            sn.serial_no, sn.qr_code) for sn in removed))
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        consume_lines=(consumed,), recover_lines=(recovered,),
        pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], removed[0].id),) if removed else (),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    from app.demand_models import WorkOrderMaterialOperation
    fact = db.get(WorkOrderMaterialOperation, getattr(parent, child + "_operation_id"))
    with pytest.raises(posting.InventoryPostingError) as caught:
        posting.reverse_inventory_transaction(db, actor=stock.actor,
            command=posting.InventoryReversalCommand(original_transaction_id=fact.posting_transaction_id,
                transaction_no="RV-" + uuid4().hex, source_document_type="renamed_inverse",
                source_document_id=str(uuid4()), posting_key="reverse:" + uuid4().hex,
                effective_at=datetime.now(timezone.utc)),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
    assert caught.value.code == "work_order_reversal_requires_command"
    assert db.scalar(select(InventoryTransaction.id).where(
        InventoryTransaction.reversed_transaction_id == fact.posting_transaction_id)) is None
