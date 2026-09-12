"""Real API-role return-source HTTP reads cannot create or release stock facts."""
from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.demand_models import WorkOrderReplacement, WorkOrderMaterialLine, WorkOrderMaterialSerial
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material
from app.inventory_models import InventorySerial, StockAccount, FormalMaterial
from app.routers import formal_work_order_query as router
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_reversal_submit_gate import _original
from pg16_work_order_reversal_write_gate import snapshot


def assert_return_sources_gate(api_engine, fixture_engine):
    for kind, world in query_worlds(fixture_engine).items():
        _, user_id, orders, _ = world
        baseline = snapshot(api_engine)
        with Session(api_engine) as db:
            selection = _original(db, world, "replace")
            parent = db.get(WorkOrderReplacement, UUID(selection["original_replacement_id"]))
            origin = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.recover_operation_id))
            account = db.get(StockAccount, origin.stock_account_id)
            sku = db.get(FormalMaterial, account.material_id)
            serial_ids = tuple(db.scalars(select(WorkOrderMaterialSerial.serial_id).where(WorkOrderMaterialSerial.operation_line_id == origin.id)))
            serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(serial_ids))))
            actor = load_formal_principal(db, user_id)
            line = material.WorkOrderMaterialLineInput(account.material_id, account.id, Decimal(1), serial_ids,
                account.condition_code, tuple(material.SerialVerificationInput(sn.id, sku.sku_code, sn.serial_no, sn.qr_code) for sn in serials))
            payload = dict(operator_person_id=str(actor.person_id), lines=[dict(source_recovery_line_id=str(origin.id),
                stock_account_id=str(account.id), quantity="1", serial_verifications=[dict(serial_id=str(sn.id),
                    sku_code=sku.sku_code, serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in serials])])
            app = FastAPI(); app.include_router(router.router, prefix="/api")
            app.dependency_overrides[get_db] = lambda: db
            for route in router.router.routes:
                for dependency in route.dependant.dependencies:
                    if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: load_formal_principal(db, user_id)
            local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
            path = f"/api/v1/work-orders/{orders[0]}/return-sources"
            with TestClient(app) as client:
                def read(method, url, body=None, expected=200):
                    before = snapshot(local); statements = []
                    def capture(_c, _cu, statement, _p, _ctx, _m): statements.append(statement)
                    connection = db.connection()
                    event.listen(connection, "before_cursor_execute", capture)
                    try: response = client.request(method, url, json=body)
                    finally: event.remove(connection, "before_cursor_execute", capture)
                    assert response.status_code == expected, response.text
                    assert response.headers["cache-control"] == "private, no-store"
                    assert statements and all(row.lstrip().upper().startswith("SELECT") for row in statements), statements
                    assert snapshot(local) == before and not db.new and not db.dirty and not db.deleted
                    assert "qr_code" not in response.text and all(sn.qr_code not in response.text for sn in serials)
                    return response.json()
                sources = read("GET", path)
                row = next(row for row in sources["items"] if row["source_recovery_line_id"] == str(origin.id))
                assert row["owed_quantity"] == row["selectable_quantity"] == "1.000"
                original_available = Decimal(row["available_quantity"])
                checked = read("POST", path + "/preview", payload)
                assert checked["planning_status"] == "source_selection_only"
                assert checked["basis_hash"] == read("POST", path + "/preview", payload)["basis_hash"]
                db.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
                assert checked["basis_hash"] == read("POST", path + "/preview", payload)["basis_hash"]
                db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                invalid = {**payload, "lines": [{**payload["lines"][0], "source_recovery_line_id": str(uuid4())}]}
                read("POST", path + "/preview", invalid, 409)
                if serials:
                    invalid = {**payload, "lines": [{**payload["lines"][0], "serial_verifications": [
                        {**payload["lines"][0]["serial_verifications"][0], "qr_code": "WRONG-PHYSICAL-CODE"}]}]}
                    read("POST", path + "/preview", invalid, 409)
                else:
                    # Another real recovery shares the balance. Leave exactly
                    # one unit, including when an earlier fixture has stock.
                    second, _ = material.execute_recover_operation(db, actor=actor, work_order_id=orders[0],
                        lines=(replace(line, target_stock_account_id=account.id),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
                    _checkpoint(db)
                    second_line = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == second.id))
                    material.execute_occupy_operation(db, actor=actor, work_order_id=orders[1],
                        lines=(replace(line, quantity=original_available),),
                        idempotency_key=uuid4().hex, request_id=uuid4().hex)
                    _checkpoint(db)
                    batch = {**payload, "lines": payload["lines"] + [{**payload["lines"][0], "source_recovery_line_id": str(second_line.id)}]}
                    read("POST", path + "/preview", payload)
                    denied = read("POST", path + "/preview", batch, 409)
                    assert denied["detail"]["code"] == "work_order_return_batch_stock_insufficient"
                material.execute_occupy_operation(db, actor=actor, work_order_id=orders[1], lines=(line,),
                    idempotency_key=uuid4().hex, request_id=uuid4().hex)
                _checkpoint(db)
                moved = read("GET", path)
                row = next(row for row in moved["items"] if row["source_recovery_line_id"] == str(origin.id))
                assert row["owed_quantity"] == "1.000" and row["selectable_quantity"] == "0.000"
                denied = read("POST", path + "/preview", payload, 409)
                assert denied["detail"]["code"] == "work_order_return_quantity_insufficient"
            db.rollback()
        assert snapshot(api_engine) == baseline
        print(f"PG16 {kind} return sources, exact batch/physical codes, SELECT-only HTTP, retained custody and rollback PASS", flush=True)
