"""Actual PostgreSQL role proves return recovery and both seal directions."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.demand_models import WorkOrderReplacement, WorkOrderMaterialLine, WorkOrderMaterialSerial
from app.formal_access import load_formal_principal
from app.inventory_models import StockAccount, InventorySerial, FormalMaterial
from app.stock_return_schemas import StockReturnPreviewIn, StockReturnSubmitIn, StockReturnCancelIn
from app.formal_services import stock_return_recovery as recovery, stock_return_commands as commands, stock_return_facts as facts
from app.formal_services.stock_return_plan import preview_return
from app.formal_services.work_order_return_sources import _hash
from app.formal_services.inventory_query import InventoryReadError
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_reversal_submit_gate import _original
from pg16_stock_return_gate import _destination, snapshot as previous_snapshot
from pg16_work_order_material_gate import _checkpoint


def snapshot(engine):
    with engine.connect() as connection:
        seals = tuple(connection.execute(text("SELECT * FROM stock_operation_command_seals ORDER BY id")))
    return previous_snapshot(engine), seals


def assert_stock_return_recovery_gate(api_engine, fixture_engine):
    for kind, world in query_worlds(fixture_engine).items():
        account_id, user_id, orders, _ = world
        target, transit = _destination(fixture_engine, account_id)
        baseline = snapshot(api_engine)
        with Session(api_engine) as db:
            selected = _original(db, world, "replace")
            parent = db.get(WorkOrderReplacement, UUID(selected["original_replacement_id"]))
            origin = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.recover_operation_id))
            account = db.get(StockAccount, origin.stock_account_id)
            sku = db.get(FormalMaterial, account.material_id)
            serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id)
                .where(WorkOrderMaterialSerial.operation_line_id == origin.id)))))
            actor = load_formal_principal(db, user_id)
            value = StockReturnPreviewIn(operator_person_id=actor.person_id, target_location_id=target, transit_location_id=transit,
                reason="合成退回恢复", lines=[dict(source_recovery_line_id=origin.id, stock_account_id=account.id, quantity="1",
                    serial_verifications=[dict(serial_id=sn.id, sku_code=sku.sku_code, serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in serials])])
            checked, _ = preview_return(db, actor=actor, work_order_id=orders[0], request=value)
            submit = StockReturnSubmitIn(**value.model_dump(), expected_plan_hash=checked.plan_hash, idempotency_key=uuid4().hex, request_id=uuid4().hex)
            local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))

            def read(coordinates):
                before = snapshot(local); statements = []
                def capture(_c, _cu, statement, _p, _ctx, _m): statements.append(statement)
                connection = db.connection(); event.listen(connection, "before_cursor_execute", capture)
                try: result = recovery.lookup_return_request(db, **coordinates)
                finally: event.remove(connection, "before_cursor_execute", capture)
                assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
                assert snapshot(local) == before and not db.new and not db.dirty and not db.deleted
                return result

            def prove_seal(coordinates, digest, execute):
                before = snapshot(local)
                with db.begin_nested() as boundary:
                    assert read(coordinates) is None
                    seal = recovery.seal_return_request(db, **coordinates, request_hash=digest); _checkpoint(db)
                    assert read(coordinates) == seal
                    sealed = snapshot(local)
                    try: execute()
                    except InventoryReadError as exc: assert exc.code == "stock_return_request_sealed"
                    else: raise AssertionError("Service replayed a sealed stock request")
                    assert snapshot(local) == sealed
                    try:
                        with db.begin_nested(), patch.object(commands, "_fresh_request", return_value=None), patch.object(facts, "order_result", return_value=None), patch.object(facts, "cancellation_result", return_value=None):
                            execute(); _checkpoint(db)
                    except DBAPIError as exc: assert "0101 sealed return request cannot execute" in str(exc.orig)
                    else: raise AssertionError("SQL allowed a late return command")
                    db.expire_all(); assert snapshot(local) == sealed
                    boundary.rollback()
                db.expire_all(); assert snapshot(local) == before

            def prove_executed(coordinates, digest, result):
                assert read(coordinates) == result
                before = snapshot(local)
                assert recovery.seal_return_request(db, **coordinates, request_hash=digest) == result
                assert snapshot(local) == before
                try:
                    with db.begin_nested(), patch.object(recovery, "lookup_return_request", return_value=None):
                        recovery.seal_return_request(db, **coordinates, request_hash=digest); _checkpoint(db)
                except DBAPIError as exc: assert "0101 executed return request cannot be sealed" in str(exc.orig)
                else: raise AssertionError("SQL sealed an executed request")
                db.expire_all(); assert snapshot(local) == before

            coordinates = dict(actor=actor, work_order_id=orders[0], operation_type="submit_return", request_id=submit.request_id)
            execute = lambda: commands.submit_return(db, actor=actor, work_order_id=orders[0], request=submit)
            prove_seal(coordinates, checked.request_hash, execute)
            posted = execute(); _checkpoint(db)
            prove_executed(coordinates, checked.request_hash, posted)
            cancel = StockReturnCancelIn(operator_person_id=actor.person_id, reason="合成取消恢复", request_id=uuid4().hex, idempotency_key=uuid4().hex)
            digest = _hash({"operation_id": str(posted.operation_id), "operator_person_id": str(actor.person_id), "reason": cancel.reason})
            coordinates = dict(actor=actor, work_order_id=orders[0], operation_id=posted.operation_id, operation_type="cancel_return", request_id=cancel.request_id)
            execute = lambda: commands.cancel_return(db, actor=actor, operation_id=posted.operation_id, request=cancel)
            prove_seal(coordinates, digest, execute)
            cancelled = execute(); _checkpoint(db)
            prove_executed(coordinates, digest, cancelled)
            db.rollback()
        assert snapshot(api_engine) == baseline
        print(f"PG16 {kind} submit/cancel lookup, seal and SQL mutual exclusion; SELECT-only recovery and full rollback PASS", flush=True)
