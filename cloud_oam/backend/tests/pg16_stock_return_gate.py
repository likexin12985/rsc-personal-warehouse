"""Real API-role return reservations/cancellations on synthetic stock only."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.demand_models import WorkOrderReplacement, WorkOrderMaterialLine, WorkOrderMaterialSerial
from app.formal_access import load_formal_principal
from app.inventory_models import InventorySerial, StockAccount, StockLocation, CustodyAssignment, FormalMaterial, StockBalance
from app.stock_operation_models import StockOperationOrder, StockOperationCancellation
from app.stock_return_schemas import StockReturnPreviewIn, StockReturnSubmitIn, StockReturnCancelIn
from app.formal_services.stock_return_plan import preview_return
from app.formal_services.stock_return_commands import submit_return, cancel_return
from app.formal_services.stock_return_facts import order_result, cancellation_result
from app.formal_services import inventory_posting as posting, stock_return_commands as commands, stock_return_facts as facts
from app.formal_services.work_order_return_sources import return_sources
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_reversal_submit_gate import _original
from pg16_work_order_reversal_write_gate import snapshot as earlier_snapshot
from pg16_work_order_material_gate import _checkpoint


TABLES = ("stock_operation_orders", "stock_operation_lines", "stock_operation_serials", "stock_operation_cancellations")


def snapshot(engine):
    with engine.connect() as connection:
        returns = tuple(tuple(connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))) for table in TABLES)
    return earlier_snapshot(engine), returns


def _destination(fixture_engine, account_id):
    # Fixture-only warehouse master setup is committed before business snapshots.
    with Session(fixture_engine) as db:
        source = db.get(StockLocation, db.get(StockAccount, account_id).location_id)
        target = db.get(StockLocation, source.parent_id)
        assert target is not None and target.location_type == "region"
        if target.custodian_person_id is None:
            target.custodian_person_id = source.custodian_person_id
        at = datetime.now(timezone.utc)
        assignments = tuple(db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == target.id,
            CustodyAssignment.valid_to.is_(None))))
        if not assignments:
            db.add(CustodyAssignment(location_id=target.id, custodian_person_id=target.custodian_person_id,
                valid_from=at - timedelta(minutes=1)))
        else:
            assert len(assignments) == 1 and assignments[0].custodian_person_id == target.custodian_person_id
        transit = StockLocation(code="RETURN-TRANSIT-" + uuid4().hex, name="合成退回在途", location_type="transit",
            owner_org_id=target.owner_org_id, parent_id=target.id, status="active")
        db.add(transit); db.flush()
        result = target.id, transit.id
        db.commit()
    return result


def assert_stock_return_gate(api_engine, fixture_engine):
    for kind, world in query_worlds(fixture_engine).items():
        account_id, user_id, orders, _ = world
        target_id, transit_id = _destination(fixture_engine, account_id)
        baseline = snapshot(api_engine)
        with Session(api_engine) as db:
            selection = _original(db, world, "replace")
            parent = db.get(WorkOrderReplacement, UUID(selection["original_replacement_id"]))
            origin = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.recover_operation_id))
            account = db.get(StockAccount, origin.stock_account_id)
            sku = db.get(FormalMaterial, account.material_id)
            serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id)
                .where(WorkOrderMaterialSerial.operation_line_id == origin.id)))))
            actor = load_formal_principal(db, user_id)
            request = StockReturnPreviewIn(operator_person_id=actor.person_id, target_location_id=target_id,
                transit_location_id=transit_id, reason="合成旧件区域退回", lines=[dict(source_recovery_line_id=origin.id,
                    stock_account_id=account.id, quantity="1", serial_verifications=[dict(serial_id=sn.id,
                        sku_code=sku.sku_code, serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in serials])])
            local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
            before = snapshot(local)
            preview, _ = preview_return(db, actor=actor, work_order_id=orders[0], request=request)
            assert snapshot(local) == before
            value = StockReturnSubmitIn(**request.model_dump(), expected_plan_hash=preview.plan_hash,
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            posted = submit_return(db, actor=actor, work_order_id=orders[0], request=value)
            _checkpoint(db)
            order = db.get(StockOperationOrder, posted.operation_id)
            assert order_result(db, actor=actor, order=order) == posted
            assert submit_return(db, actor=actor, work_order_id=orders[0], request=value) == posted
            _checkpoint(db)
            source = next(row for row in return_sources(db, actor=actor, work_order_id=orders[0]).items
                if row.source_recovery_line_id == origin.id)
            assert source.owed_quantity == source.committed_quantity == "1.000" and source.selectable_quantity == "0.000"
            held_id = db.execute(text("SELECT reserved_account_id FROM stock_operation_lines WHERE operation_id=:id"),
                {"id": order.id}).scalar_one()
            held_before = snapshot(local)
            try:
                with db.begin_nested():
                    posting.post_inventory_transaction(db, actor=actor,
                        command=posting.InventoryPostingCommand(transaction_no="DETACHED-" + uuid4().hex,
                            movement_type="release", source_document_type="renamed_adjustment", source_document_id=str(order.id),
                            posting_key=uuid4().hex, effective_at=datetime.now(timezone.utc), movements=(posting.InventoryMovementCommand(
                                from_account_id=held_id, to_account_id=account.id, quantity=origin.quantity,
                                serial_ids=tuple(sorted((sn.id for sn in serials), key=str))),)),
                        idempotency_key=uuid4().hex, request_id=uuid4().hex,
                        permission_resource="stock_operation", permission_action="cancel_return")
                    _checkpoint(db)
            except DBAPIError as exc:
                assert "0100 held return stock" in str(exc.orig)
            else:
                raise AssertionError("Database allowed a generic movement to release another return's custody")
            db.expire_all()
            assert snapshot(local) == held_before
            cancellation = StockReturnCancelIn(operator_person_id=actor.person_id, reason="合成未发出取消",
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            cancelled = cancel_return(db, actor=actor, operation_id=order.id, request=cancellation)
            _checkpoint(db)
            assert cancellation_result(db, actor=actor, order=order,
                cancellation=db.get(StockOperationCancellation, cancelled.cancellation_id)) == cancelled
            assert cancel_return(db, actor=actor, operation_id=order.id, request=cancellation) == cancelled
            _checkpoint(db)
            after = next(row for row in return_sources(db, actor=actor, work_order_id=orders[0]).items
                if row.source_recovery_line_id == origin.id)
            assert after.owed_quantity == after.selectable_quantity == "1.000" and after.committed_quantity == "0.000"
            assert order_result(db, actor=actor, order=order) == posted
            before_missing = snapshot(local)
            next_preview, _ = preview_return(db, actor=actor, work_order_id=orders[0], request=request)
            next_value = StockReturnSubmitIn(**request.model_dump(), expected_plan_hash=next_preview.plan_hash,
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            try:
                with db.begin_nested(), patch.object(commands, "_record", return_value=None), patch.object(facts, "order_result", return_value=None):
                    submit_return(db, actor=actor, work_order_id=orders[0], request=next_value)
                    _checkpoint(db)
            except DBAPIError as exc:
                assert "0100 original audit, state or outbox evidence missing" in str(exc.orig)
            else:
                raise AssertionError("Database accepted return stock without its whole business evidence")
            db.expire_all()
            assert snapshot(local) == before_missing
            db.rollback()
        assert snapshot(api_engine) == baseline
        print(f"PG16 {kind} return reserve/cancel, exact replay, SQL bypass rejection, retained obligation and full rollback PASS", flush=True)


def assert_stock_return_concurrent_commit_gate(api_engine, fixture_engine):
    """Two API connections may create only one reservation and one cancellation."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.formal_services.inventory_query import InventoryReadError

    for kind, world in query_worlds(fixture_engine).items():
        account_id, user_id, orders, _ = world
        target_id, transit_id = _destination(fixture_engine, account_id)
        with Session(api_engine) as db:
            selection = _original(db, world, "replace")
            parent = db.get(WorkOrderReplacement, UUID(selection["original_replacement_id"]))
            origin = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.recover_operation_id))
            account = db.get(StockAccount, origin.stock_account_id)
            sku = db.get(FormalMaterial, account.material_id)
            serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id)
                .where(WorkOrderMaterialSerial.operation_line_id == origin.id)))))
            actor = load_formal_principal(db, user_id)
            request = StockReturnPreviewIn(operator_person_id=actor.person_id, target_location_id=target_id,
                transit_location_id=transit_id, reason="合成并发退回", lines=[dict(source_recovery_line_id=origin.id,
                    stock_account_id=account.id, quantity="1", serial_verifications=[dict(serial_id=sn.id,
                        sku_code=sku.sku_code, serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in serials])])
            _checkpoint(db); db.commit()
        for same_key in (True, False):
            with Session(api_engine) as db:
                actor = load_formal_principal(db, user_id)
                checked, _ = preview_return(db, actor=actor, work_order_id=orders[0], request=request)
            value = StockReturnSubmitIn(**request.model_dump(), expected_plan_hash=checked.plan_hash,
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            values = (value, value if same_key else value.model_copy(update={"idempotency_key": uuid4().hex, "request_id": uuid4().hex}))
            barrier = Barrier(2)
            def submit_worker(item):
                with Session(api_engine) as db:
                    current = load_formal_principal(db, user_id)
                    barrier.wait(timeout=30)
                    try:
                        result = submit_return(db, actor=current, work_order_id=orders[0], request=item)
                        _checkpoint(db); db.commit()
                        return result
                    except InventoryReadError as exc:
                        db.rollback(); return exc.code
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(submit_worker, item) for item in values]
                results = [future.result(timeout=180) for future in futures]
            successful = [result for result in results if not isinstance(result, str)]
            if same_key: assert len(successful) == 2 and successful[0] == successful[1]
            else: assert len(successful) == 1 and "work_order_return_quantity_insufficient" in results
            posted = successful[0]
            cancel = StockReturnCancelIn(operator_person_id=request.operator_person_id, reason="合成并发取消未发出退回",
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            barrier = Barrier(2)
            def cancel_worker():
                with Session(api_engine) as db:
                    current = load_formal_principal(db, user_id)
                    barrier.wait(timeout=30)
                    result = cancel_return(db, actor=current, operation_id=posted.operation_id, request=cancel)
                    _checkpoint(db); db.commit()
                    return result
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(cancel_worker) for _ in range(2)]
                cancellations = [future.result(timeout=180) for future in futures]
            assert cancellations[0] == cancellations[1]
            with Session(api_engine) as db:
                current = load_formal_principal(db, user_id)
                original = db.get(StockOperationOrder, posted.operation_id)
                assert order_result(db, actor=current, order=original) == posted
                rows = tuple(db.scalars(select(StockOperationCancellation).where(StockOperationCancellation.operation_id == original.id)))
                assert len(rows) == 1 and cancellation_result(db, actor=current, order=original, cancellation=rows[0]) == cancellations[0]
            print(f"PG16 {kind} concurrent {'same' if same_key else 'different'} submit keys, concurrent cancellation and fresh-session readback PASS", flush=True)
