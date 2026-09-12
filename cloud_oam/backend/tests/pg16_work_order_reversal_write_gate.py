"""Real API-role compensation; synthetic stock is always fully rolled back."""
from dataclasses import replace
from uuid import uuid4
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material, work_order_replacements as paired
from app.formal_services import work_order_removed_registration as registration
from app.formal_services.work_order_reversal_plan import preview_reversal
from app.formal_services.work_order_reversal_write import execute_reversal
from app.formal_services.work_order_reservations import read_work_order_reservations
from app.formal_services.serial_ledger import rebuild_serial_states
from app.inventory_models import InventoryMovement, SerialCurrentPosition, StockAccount
from app.work_order_reversal_schemas import WorkOrderReversalPreviewIn, WorkOrderReversalIn
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_removed_registration_gate import case, recovered, snapshot as original_snapshot


def snapshot(engine):
    with engine.connect() as connection:
        reversals = tuple(tuple(connection.execute(text(f"SELECT * FROM {table} ORDER BY id")))
            for table in ("work_order_reversals", "work_order_reversal_items"))
    return original_snapshot(engine), reversals


def reverse(db, actor, order_id, *, original=None, parent=None):
    selection = WorkOrderReversalPreviewIn(operator_person_id=actor.person_id, reason="核对原实物后撤回🔧",
        original_operation_id=original.id if original else None, original_replacement_id=parent.id if parent else None)
    plan = preview_reversal(db, actor=actor, work_order_id=order_id, request=selection)
    value = WorkOrderReversalIn(**selection.model_dump(), expected_plan_hash=plan.plan_hash, idempotency_key=uuid4().hex, request_id=uuid4().hex)
    result = execute_reversal(db, actor=actor, work_order_id=order_id, request=value)
    _checkpoint(db)
    assert execute_reversal(db, actor=actor, work_order_id=order_id, request=value) == result
    _checkpoint(db)
    return result


def assert_reversal_write_gate(api_engine, fixture_engine):
    worlds = query_worlds(fixture_engine)
    baseline = snapshot(api_engine)
    for kind, (_account_id, user_id, orders, line) in worlds.items():
        for action in ("occupy", "consume", "release"):
            with Session(api_engine) as db:
                actor = load_formal_principal(db, user_id)
                occupied, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0],
                    lines=(line,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
                _checkpoint(db)
                reserved = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id == occupied.posting_transaction_id))
                original = occupied
                if action in {"consume", "release"}:
                    selected = replace(line, stock_account_id=reserved, target_stock_account_id=line.stock_account_id if action == "release" else None)
                    original, _ = getattr(material, f"execute_{action}_operation")(db, actor=actor, work_order_id=orders[0],
                        lines=(selected,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
                    _checkpoint(db)
                reverse(db, actor, orders[0], original=original)
                remaining = read_work_order_reservations(db, work_order_id=orders[0], account_ids=(reserved,))[reserved]
                assert remaining.quantity == (0 if action == "occupy" else line.quantity)
                if line.serial_ids:
                    states = rebuild_serial_states(db, line.serial_ids)
                    assert all(s.lifecycle_status == "active" and s.stock_account_id == (line.stock_account_id if action == "occupy" else reserved) for s in states.values())
                db.rollback()
            assert snapshot(api_engine) == baseline
            print(f"PG16 {kind} {action} full inverse, exact reservation and replay with full rollback PASS", flush=True)
    with Session(api_engine) as db:
        args, line = case(db, worlds["serial"])
        identity = registration.register_removed_serial(db, **args); _checkpoint(db)
        parent = paired.execute_replacement(db, actor=args["actor"], work_order_id=args["work_order_id"],
            consume_lines=(line,), recover_lines=(recovered(db, args, identity),),
            pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0], identity.serial_id),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        result = reverse(db, args["actor"], args["work_order_id"], parent=parent)
        assert len(result.items) == 2
        state = rebuild_serial_states(db, (identity.serial_id,))[identity.serial_id]
        assert state.lifecycle_status == "active" and state.stock_account_id is None and state.admission_movement_id is None
        db.rollback()
    assert snapshot(api_engine) == baseline
    print("PG16 registered first-recovery pair compensated atomically; identity retained, effective admission cleared; full rollback PASS", flush=True)

    with Session(api_engine) as db:
        args, line = case(db, worlds["serial"])
        actor = args["actor"]
        identity = registration.register_removed_serial(db, **args); _checkpoint(db)
        first = paired.execute_replacement(db, actor=actor, work_order_id=args["work_order_id"],
            consume_lines=(line,), recover_lines=(recovered(db, args, identity),),
            pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0], identity.serial_id),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        account = db.get(StockAccount, db.get(SerialCurrentPosition, identity.serial_id).stock_account_id)
        order_id = worlds["serial"][2][1]
        reused = material.WorkOrderMaterialLineInput(account.material_id, account.id, Decimal(1), (identity.serial_id,),
            account.condition_code, recovered(db, args, identity).serial_verifications)
        occupied, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=order_id, lines=(reused,),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        reserved = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id == occupied.posting_transaction_id))
        second = paired.execute_replacement(db, actor=actor, work_order_id=order_id,
            consume_lines=(replace(reused, stock_account_id=reserved),),
            recover_lines=(paired.RecoveryLineInput(reserved, line.material_id, Decimal(1), "damaged",
                serial_ids=line.serial_ids, serial_verifications=line.serial_verifications),),
            pairs=(material.WorkOrderReplacementPairInput(identity.serial_id, line.serial_ids[0]),),
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
        _checkpoint(db)
        reverse(db, actor, order_id, parent=second)
        states = rebuild_serial_states(db, (identity.serial_id, line.serial_ids[0]))
        assert states[identity.serial_id].lifecycle_status == "active" and states[identity.serial_id].stock_account_id == reserved
        assert states[line.serial_ids[0]].lifecycle_status == "consumed" and states[line.serial_ids[0]].stock_account_id is None
        from app.formal_services.work_order_replacement_read import replacement_result
        replacement_result(db, actor=actor, replacement=first)
        db.rollback()
    assert snapshot(api_engine) == baseline
    print("PG16 later cross-order pair inverse restores earlier consumed SN and registered SN reservation; original history still readable PASS", flush=True)


def assert_reversal_database_proof_gate(api_engine, fixture_engine):
    """Bypass only application readback to exercise actual deferred SQL guards."""
    from sqlalchemy.exc import DBAPIError
    from app.formal_services import work_order_reversal_write as writer, work_order_reversal_read as reader
    worlds = query_worlds(fixture_engine)
    baseline = snapshot(api_engine)
    _, user_id, orders, line = worlds["quantity"]
    for corruption in ("missing_parent_audit", "forged_parent_payload", "missing_operation_lines"):
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            original, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0], lines=(line,),
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            selection = WorkOrderReversalPreviewIn(operator_person_id=actor.person_id, reason="synthetic proof rejection", original_operation_id=original.id)
            plan = preview_reversal(db, actor=actor, work_order_id=orders[0], request=selection)
            value = WorkOrderReversalIn(**selection.model_dump(), expected_plan_hash=plan.plan_hash, idempotency_key=uuid4().hex, request_id=uuid4().hex)
            append = writer.append_audit_event
            record = writer._record_child
            def audited(*args, **kwargs):
                if corruption == "missing_parent_audit" and kwargs.get("action") == "work_order_material.reversal": return
                return append(*args, **kwargs)
            def recorded(*args, **kwargs):
                if corruption == "missing_operation_lines":
                    # Do not create the inverse operation, its detail or audit.
                    return
                return record(*args, **kwargs)
            try:
                with patch.object(reader, "reversal_result", lambda *a, **k: None), \
                        patch.object(writer, "append_audit_event", audited), patch.object(writer, "_record_child", recorded):
                    if corruption == "forged_parent_payload":
                        with patch.object(writer, "parent_payload", lambda *a: {"forged": True}):
                            writer.execute_reversal(db, actor=actor, work_order_id=orders[0], request=value)
                    else: writer.execute_reversal(db, actor=actor, work_order_id=orders[0], request=value)
                    _checkpoint(db)
            except DBAPIError as error:
                assert getattr(error.orig, "sqlstate", None) in {"23514", "23503"}
            else: raise AssertionError("PG accepted incomplete reversal proof: " + corruption)
            db.rollback()
        assert snapshot(api_engine) == baseline
        print("PG16 rejects " + corruption + " without application readback; full rollback PASS", flush=True)


def assert_reversal_concurrent_commit_gate(api_engine, fixture_engine):
    """Commit synthetic races with zero net stock; retain immutable 0098 history."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.demand_models import WorkOrderReversal, WorkOrderReversalItem
    from app.formal_services.work_order_reversal_read import reversal_result
    worlds = query_worlds(fixture_engine)
    _, user_id, orders, line = worlds["quantity"]
    for same_key, order_id in zip((True, False), orders):
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            original, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=order_id, lines=(line,),
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db); db.commit()
            selection = WorkOrderReversalPreviewIn(operator_person_id=actor.person_id, reason="synthetic concurrent compensation", original_operation_id=original.id)
            plan = preview_reversal(db, actor=actor, work_order_id=order_id, request=selection)
            value = WorkOrderReversalIn(**selection.model_dump(), expected_plan_hash=plan.plan_hash, idempotency_key=uuid4().hex, request_id=uuid4().hex)
        values = (value, value if same_key else value.model_copy(update={"idempotency_key":uuid4().hex,"request_id":uuid4().hex}))
        barrier = Barrier(2)
        def worker(request):
            with Session(api_engine) as db:
                current = load_formal_principal(db, user_id)
                barrier.wait(timeout=30)
                try:
                    result = execute_reversal(db, actor=current, work_order_id=order_id, request=request)
                    _checkpoint(db); db.commit()
                    return result
                except material.InventoryPostingError as error:
                    db.rollback()
                    return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker, request) for request in values]
            results = [future.result(timeout=60) for future in futures]
        if same_key:
            assert results[0] == results[1] and not isinstance(results[0], str)
        else:
            assert sum(not isinstance(result,str) for result in results) == 1
            assert "original_transaction_already_reversed" in results
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            rows = tuple(db.scalars(select(WorkOrderReversal).where(WorkOrderReversal.original_operation_id == original.id)))
            assert len(rows) == 1 and len(tuple(db.scalars(select(WorkOrderReversalItem).where(WorkOrderReversalItem.reversal_id == rows[0].id)))) == 1
            verified = reversal_result(db, actor=actor, parent=rows[0])
            assert verified in results
        print("PG16 concurrent " + ("same-key replay" if same_key else "different-key original conflict") + ": exactly one committed synthetic compensation, fresh-session readback PASS", flush=True)
    assert_reversal_history_downgrade_rejected(api_engine, fixture_engine)


def assert_reversal_history_downgrade_rejected(api_engine, fixture_engine):
    from pathlib import Path
    import runpy
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    baseline = snapshot(api_engine)
    migration = runpy.run_path(str(Path(__file__).parents[1] / "alembic/versions/20261008_0098_work_order_reversals.py"))
    with fixture_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Operations.context(MigrationContext.configure(connection)):
                try: migration["downgrade"]()
                except (RuntimeError, DBAPIError) as error:
                    assert "immutable reversal history must be retained" in str(error)
                else: raise AssertionError("Downgrade accepted loss of committed compensation")
        finally: transaction.rollback()
    assert snapshot(api_engine) == baseline
    print("PG16 committed reversal history blocks downgrade; full schema and business transaction rollback PASS", flush=True)
