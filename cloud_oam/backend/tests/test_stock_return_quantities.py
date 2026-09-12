"""Several partial return orders must retain each original recovery's budget."""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.formal_services.stock_return_plan import preview_return
from app.formal_services.stock_return_commands import submit_return, cancel_return
from app.formal_services.stock_return_facts import order_result
from app.formal_services.inventory_query import InventoryReadError
from app.stock_operation_models import StockOperationOrder
from app.stock_return_schemas import StockReturnSubmitIn, StockReturnCancelIn
from test_stock_return_commands import db, world, stock, recovered, destination, submission, sources, _snapshot


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_partial_returns_share_original_allowance_and_cancel_only_their_own_quantity(db, stock, recovered, destination):
    template = submission(db, stock, recovered, destination)
    def submit(quantity):
        value = template.model_copy(update={"lines": (template.lines[0].model_copy(update={"quantity": Decimal(quantity)}),),
            "request_id": uuid4().hex, "idempotency_key": uuid4().hex})
        checked, _ = preview_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
        value = value.model_copy(update={"expected_plan_hash": checked.plan_hash})
        result = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
        return result
    first = submit("0.400")
    row = sources(db, stock).items[0]
    assert (row.owed_quantity, row.committed_quantity, row.selectable_quantity) == ("1.000", "0.400", "0.600")
    second = submit("0.600")
    before = _snapshot(db)
    with pytest.raises(InventoryReadError) as exc: submit("0.001")
    assert exc.value.code == "work_order_return_quantity_insufficient" and _snapshot(db) == before
    cancelled = cancel_return(db, actor=stock.actor, operation_id=first.operation_id,
        request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="只取消第一笔未发出退回",
            idempotency_key=uuid4().hex, request_id=uuid4().hex)); db.commit()
    assert cancelled.operation_id == first.operation_id
    row = sources(db, stock).items[0]
    assert (row.owed_quantity, row.committed_quantity, row.selectable_quantity) == ("1.000", "0.600", "0.400")
    assert order_result(db, actor=stock.actor, order=db.get(StockOperationOrder, second.operation_id)) == second
    third = submit("0.400")
    assert len(tuple(db.scalars(select(StockOperationOrder)))) == 3
    row = sources(db, stock).items[0]
    assert row.owed_quantity == row.committed_quantity == "1.000" and row.selectable_quantity == "0.000"
    assert third.operation_id not in {first.operation_id, second.operation_id}
