from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.inventory_models import StockLocation, CustodyAssignment, InventoryLedgerHead
from app.stock_return_schemas import StockReturnCancelIn
from app.formal_services import stock_return_options as options
from app.formal_services.stock_return_history import return_history
from app.formal_services.stock_return_commands import submit_return, cancel_return
from app.formal_services.inventory_query import InventoryReadError
from test_stock_return_commands import db, world, stock, recovered, destination, submission, _snapshot, change_status
from test_formal_stock_return_routes import client


def test_only_exact_regional_transit_bindings_are_read_without_writes(db, stock, recovered, destination, client):
    target, transit = destination
    duplicate = StockLocation(code=uuid4().hex, name="另一个明确在途位置", location_type="transit",
        owner_org_id=target.owner_org_id, parent_id=target.id, status="active")
    unrelated = StockLocation(code=uuid4().hex, name="其他个人位置", location_type="transit",
        owner_org_id=target.owner_org_id, parent_id=None, status="active")
    db.add_all((duplicate, unrelated)); db.commit()
    change_status(db, stock.orders[0], "closed")
    before = _snapshot(db)
    db.execute(text("PRAGMA query_only=ON"))
    response = client.get(f"/api/v1/work-orders/{stock.orders[0].id}/returns/options")
    assert response.status_code == 200 and "no-store" in response.headers["cache-control"], response.text
    result = response.json()
    assert result["sources"]["work_order"]["can_operate"] is False
    assert {row["transit_location_id"] for row in result["destinations"]} == {str(transit.id), str(duplicate.id)}
    assert all(row["target_location_id"] == str(target.id) and row["source_location_id"] == str(recovered.account.location_id)
        for row in result["destinations"])
    assert "qr_code" not in response.text and _snapshot(db) == before


def test_missing_transit_is_empty_and_ambiguous_receiver_is_rejected(db, stock, recovered, destination):
    target, transit = destination
    transit.status = "inactive"; db.commit()
    assert not options.return_options(db, actor=stock.actor, work_order_id=stock.orders[0].id).destinations
    transit.status = "active"
    db.add(CustodyAssignment(location_id=target.id, custodian_person_id=stock.actor.person_id,
        valid_from=recovered.parent.created_at, valid_to=datetime.now(timezone.utc) + timedelta(hours=1))); db.commit()
    with pytest.raises(InventoryReadError) as exc:
        options.return_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert exc.value.code == "stock_return_receiver_unresolved"


def test_routing_change_during_read_discards_stale_options(db, stock, recovered, destination, monkeypatch):
    original = options._destinations
    calls = 0
    def changing(*args):
        nonlocal calls
        rows = original(*args); calls += 1
        if calls == 1:
            destination[1].status = "inactive"; db.commit()
        return rows
    monkeypatch.setattr(options, "_destinations", changing)
    with pytest.raises(InventoryReadError) as exc:
        options.return_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert exc.value.code == "stock_return_options_changed"


def test_read_and_new_return_selection_permissions_remain_separate(db, stock, recovered, destination):
    original = stock.actor
    stock.world.current_principal = replace(original, entitlements=tuple(row for row in original.entitlements
        if not (row.resource == "stock_operation" and row.action == "submit_return")))
    with pytest.raises(InventoryReadError): options.return_options(db, actor=original, work_order_id=stock.orders[0].id)
    assert return_history(db, actor=original, work_order_id=stock.orders[0].id).items == ()
    stock.world.current_principal = replace(original, entitlements=tuple(row for row in original.entitlements
        if not (row.resource == "stock_operation" and row.action == "read")))
    with pytest.raises(InventoryReadError): return_history(db, actor=original, work_order_id=stock.orders[0].id)


def test_inventory_change_during_final_route_read_discards_source_quantities(db, stock, recovered, destination, monkeypatch):
    original = options._destinations
    calls = 0
    def changing(*args):
        nonlocal calls
        rows = original(*args); calls += 1
        if calls == 2:
            head = db.scalar(select(InventoryLedgerHead).where(InventoryLedgerHead.stream_key == "inventory"))
            head.next_cursor += 1; db.commit()
        return rows
    monkeypatch.setattr(options, "_destinations", changing)
    with pytest.raises(InventoryReadError) as exc:
        options.return_options(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert exc.value.code == "inventory_projection_changed"


def test_history_keeps_original_submission_and_cancellation_separate(db, stock, recovered, destination, client):
    value = submission(db, stock, recovered, destination)
    posted = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    first = return_history(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert first.items[0].original == posted and first.items[0].cancellation is None
    cancelled = cancel_return(db, actor=stock.actor, operation_id=posted.operation_id,
        request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="尚未寄出",
            request_id=uuid4().hex, idempotency_key=uuid4().hex)); db.commit()
    change_status(db, stock.orders[0], "closed")
    before = _snapshot(db); db.execute(text("PRAGMA query_only=ON"))
    response = client.get(f"/api/v1/work-orders/{stock.orders[0].id}/returns")
    assert response.status_code == 200 and "no-store" in response.headers["cache-control"]
    row = response.json()["items"][0]
    assert row["original"]["status"] == "submitted" and row["original"]["operation_id"] == str(posted.operation_id)
    assert row["cancellation"]["status"] == "cancelled" and row["cancellation"]["cancellation_id"] == str(cancelled.cancellation_id)
    assert "qr_code" not in response.text and _snapshot(db) == before
    assert not return_history(db, actor=stock.actor, work_order_id=stock.orders[1].id).items
