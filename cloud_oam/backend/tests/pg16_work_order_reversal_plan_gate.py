"""Read-only formal HTTP proposals over real PG16 stock and lifecycle history."""
from dataclasses import replace
from contextlib import nullcontext
from types import SimpleNamespace
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material, work_order_replacements as paired
from app.formal_services import work_order_removed_registration as registration
from app.inventory_models import InventoryMovement, SerialCurrentPosition, StockAccount
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_removed_registration_gate import app_for, case, recovered, snapshot


def readonly_preview(db, user_id, work_order_id, request):
    local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
    before = snapshot(local)
    def select_only(_conn, _cursor, statement, _parameters, _context, _many):
        assert statement.lstrip().upper().startswith("SELECT"), "Reversal preview attempted a non-SELECT statement"
    connection = db.connection()
    event.listen(connection, "before_cursor_execute", select_only)
    try:
        with TestClient(app_for(db, user_id)) as client:
            response = client.post(f"/api/v1/work-orders/{work_order_id}/material-reversals/preview", json=request)
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            body = response.json()
            assert body["planning_status"] == "preview_only" and "qr_code" not in response.text
    finally:
        event.remove(connection, "before_cursor_execute", select_only)
    assert snapshot(local) == before
    return body


def assert_reversal_plan_gate(api_engine, fixture_engine):
    worlds = query_worlds(fixture_engine)
    baseline = snapshot(api_engine)
    for kind, (_account_id, user_id, orders, line) in worlds.items():
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            occupied, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                lines=(line,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            payload = dict(operator_person_id=str(actor.person_id), reason="实物核对后撤回原操作🔧",
                original_operation_id=str(occupied.id))
            result = readonly_preview(db, user_id, orders[0], payload)
            assert result["children"][0]["movements"][0]["reservation_delta"] == "-1.000"
            reserved = db.scalar(select(InventoryMovement.to_account_id).where(
                InventoryMovement.transaction_id == occupied.posting_transaction_id))
            consumed, _ = material.execute_consume_operation(db, actor=actor, work_order_id=orders[0],
                lines=(replace(line, stock_account_id=reserved),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            payload["original_operation_id"] = str(consumed.id)
            result = readonly_preview(db, user_id, orders[0], payload)
            movement = result["children"][0]["movements"][0]
            assert movement["from_account_id"] is None and movement["to_account_id"] == str(reserved)
            assert movement["reservation_delta"] == "1.000"
            if kind == "serial":
                assert [(row["lifecycle_before"], row["lifecycle_after"]) for row in movement["serials"]] == [("consumed", "active")]
            assert readonly_preview(db, user_id, orders[0], payload)["plan_hash"] == result["plan_hash"]
            db.rollback()
        assert snapshot(api_engine) == baseline
        print(f"PG16 {kind} own original occupancy/consumption inverse previews are SELECT-only, exact and rollback-safe PASS", flush=True)

    with Session(api_engine) as db:
        args, line = case(db, worlds["serial"])
        actor, user_id = args["actor"], worlds["serial"][1]
        registered = registration.register_removed_serial(db, **args); _checkpoint(db)
        parent = paired.execute_replacement(db, actor=actor, work_order_id=args["work_order_id"],
            consume_lines=(line,), recover_lines=(recovered(db, args, registered),),
            pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0], registered.serial_id),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        payload = dict(operator_person_id=str(actor.person_id), reason="核对原新旧件与身份登记",
            original_replacement_id=str(parent.id))
        result = readonly_preview(db, user_id, args["work_order_id"], payload)
        assert [row["original_operation_type"] for row in result["children"]] == ["recover", "consume"]
        restored = result["children"][0]["movements"][0]["serials"][0]
        assert restored["previous_movement_id"] is None and restored["lifecycle_after"] == "active"
        assert restored["registration_id"] == str(registered.registration_id)

        # Reuse that registered SN in a second order; the original installed SN
        # now becomes the removed part, so undoing recovery must restore consumed.
        position = db.get(SerialCurrentPosition, registered.serial_id)
        account = db.get(StockAccount, position.stock_account_id)
        second_order = worlds["serial"][2][1]
        reused = material.WorkOrderMaterialLineInput(account.material_id, account.id, Decimal(1),
            (registered.serial_id,), account.condition_code, recovered(db, args, registered).serial_verifications)
        occupied, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=second_order, lines=(reused,),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        reserve = db.scalar(select(InventoryMovement.to_account_id).where(
            InventoryMovement.transaction_id == occupied.posting_transaction_id))
        returned = paired.RecoveryLineInput(reserve, line.material_id, Decimal(1), "damaged",
            lot_id=account.lot_id, serial_ids=line.serial_ids, serial_verifications=line.serial_verifications)
        second = paired.execute_replacement(db, actor=actor, work_order_id=second_order,
            consume_lines=(replace(reused, stock_account_id=reserve),), recover_lines=(returned,),
            pairs=(material.WorkOrderReplacementPairInput(registered.serial_id, line.serial_ids[0]),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        payload["original_replacement_id"] = str(second.id)
        result = readonly_preview(db, user_id, second_order, payload)
        restored = result["children"][0]["movements"][0]["serials"][0]
        assert restored["lifecycle_after"] == "consumed" and restored["previous_movement_id"] is not None
        db.rollback()
    assert snapshot(api_engine) == baseline
    print("PG16 both paired lifecycle origins and later cross-order registered SN reuse prove exact inverse plans; full rollback PASS", flush=True)
