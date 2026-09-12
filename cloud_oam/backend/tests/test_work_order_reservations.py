"""Work-order custody is causal history, never the pooled account balance."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from test_work_order_material_evidence import db, evidence, world, serial_evidence
from app.formal_services import work_order_material as service
from app.formal_services.work_order_reservations import require_work_order_reservations


def test_own_remaining_quantity_excludes_consumed_history(db, evidence):
    require_work_order_reservations(db, work_order_id=evidence.order.id, lines=(evidence.line,))
    with pytest.raises(service.InventoryPostingError, match="剩余占用不足"):
        require_work_order_reservations(db, work_order_id=evidence.order.id,
            lines=(replace(evidence.line, quantity=Decimal("2")),))


def test_another_work_order_cannot_use_the_same_reserved_account(db, evidence):
    with pytest.raises(service.InventoryPostingError) as exc:
        require_work_order_reservations(db, work_order_id=uuid4(), lines=(evidence.line,))
    assert exc.value.code == "work_order_reservation_insufficient"


def test_historical_replay_checks_original_cursor_not_present_remaining(db, evidence):
    _, line = serial_evidence(db, evidence)
    require_work_order_reservations(db, work_order_id=evidence.order.id, lines=(line,),
                                    before_cursor=evidence.transaction.ledger_cursor)
    with pytest.raises(service.InventoryPostingError) as exc:
        require_work_order_reservations(db, work_order_id=evidence.order.id, lines=(line,))
    assert exc.value.code == "work_order_reservation_insufficient"


def test_future_occupy_does_not_repair_earlier_negative_prefix(db, evidence):
    evidence.reserved_transaction.ledger_cursor = 101
    db.flush()
    with pytest.raises(service.InventoryPostingError) as exc:
        require_work_order_reservations(db, work_order_id=evidence.order.id, lines=(evidence.line,))
    assert exc.value.code == "work_order_reservation_history_invalid"


def test_other_account_or_other_sn_is_not_reservation_evidence(db, evidence):
    with pytest.raises(service.InventoryPostingError) as exc:
        require_work_order_reservations(db, work_order_id=evidence.order.id,
            lines=(replace(evidence.line, stock_account_id=evidence.available.id),))
    assert exc.value.code == "work_order_reservation_insufficient"
    with pytest.raises(service.InventoryPostingError) as exc:
        require_work_order_reservations(db, work_order_id=evidence.order.id,
            lines=(replace(evidence.line, serial_ids=(uuid4(),)),))
    assert exc.value.code == "work_order_serial_not_reserved"


@pytest.mark.parametrize("operation", ["consume", "release"])
def test_insufficient_whole_batch_never_calls_posting(db, evidence, monkeypatch, operation):
    def unexpected(*args, **kwargs):
        raise AssertionError("no inventory command may post before the entire reservation preflight")
    monkeypatch.setattr(service, "post_inventory_transaction", unexpected)
    line = replace(evidence.line, quantity=Decimal("2"),
                   target_stock_account_id=evidence.available.id if operation == "release" else None)
    with pytest.raises(service.InventoryPostingError) as exc:
        getattr(service, f"execute_{operation}_operation")(db, actor=evidence.world.current_principal,
            work_order_id=evidence.order.id, lines=(line,), idempotency_key="insufficient",
            request_id="insufficient-trace")
    assert exc.value.code == "work_order_reservation_insufficient"


def test_generic_evidence_cannot_attach_other_work_orders_occupancy(db, evidence):
    evidence.reserved_transaction.source_document_id = str(uuid4())
    db.flush()
    with pytest.raises(service.InventoryPostingError) as exc:
        service.record_posted_operation(db, actor=evidence.world.current_principal,
            work_order_id=evidence.order.id, operator_person_id=evidence.world.person.id,
            operation_type="consume", lines=(evidence.line,),
            posting_transaction_id=evidence.transaction.id, idempotency_key="foreign-occupancy")
    assert exc.value.code == "work_order_reservation_insufficient"


@pytest.mark.parametrize("operation", ["occupy", "consume", "release", "recover"])
def test_every_inventory_command_locks_ledger_before_work_order(db, evidence, monkeypatch, operation):
    calls = []
    monkeypatch.setattr(service, "_lock_inventory_ledger_head_for_atomic_batch", lambda db: calls.append("ledger"))
    def authorize(*args, **kwargs):
        assert calls == ["ledger"]
        raise RuntimeError("lock order checked")
    monkeypatch.setattr(service, "authorize_work_order", authorize)
    with pytest.raises(RuntimeError, match="lock order checked"):
        getattr(service, f"execute_{operation}_operation")(db, actor=evidence.world.current_principal,
            work_order_id=evidence.order.id, lines=(evidence.line,), idempotency_key="lock-order", request_id="trace")
