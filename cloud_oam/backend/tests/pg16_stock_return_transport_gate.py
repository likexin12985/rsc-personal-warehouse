"""Real API-role HTTP and two-connection return request recovery proofs."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.demand_models import WorkOrderReplacement, WorkOrderMaterialLine, WorkOrderMaterialSerial
from app.formal_access import load_formal_principal
from app.formal_services import stock_return_commands as commands, stock_return_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_return_plan import preview_return
from app.formal_services.work_order_return_sources import _hash
from app.inventory_models import StockAccount, InventorySerial, FormalMaterial, StockBalance
from app.routers import formal_stock_returns as router
from app.stock_return_schemas import StockReturnPreviewIn, StockReturnSubmitIn, StockReturnCancelIn
from pg16_stock_return_gate import _destination
from pg16_stock_return_recovery_gate import snapshot
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_reversal_submit_gate import _original


def _selection(db, world, target, transit):
    selected = _original(db, world, "replace")
    parent = db.get(WorkOrderReplacement, UUID(selected["original_replacement_id"]))
    origin = db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == parent.recover_operation_id))
    account = db.get(StockAccount, origin.stock_account_id)
    sku = db.get(FormalMaterial, account.material_id)
    serials = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id)
        .where(WorkOrderMaterialSerial.operation_line_id == origin.id)))))
    actor = load_formal_principal(db, world[1])
    return StockReturnPreviewIn(operator_person_id=actor.person_id, target_location_id=target, transit_location_id=transit,
        reason="合成退回接口与竞争", lines=[dict(source_recovery_line_id=origin.id, stock_account_id=account.id, quantity="1",
            serial_verifications=[dict(serial_id=sn.id, sku_code=sku.sku_code, serial_no=sn.serial_no, qr_code=sn.qr_code) for sn in serials])])


def _command(db, world, selection, operation_type):
    actor = load_formal_principal(db, world[1])
    checked, _ = preview_return(db, actor=actor, work_order_id=world[2][0], request=selection)
    request = StockReturnSubmitIn(**selection.model_dump(), expected_plan_hash=checked.plan_hash,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    coordinates = dict(work_order_id=world[2][0], operation_type=operation_type, request_id=request.request_id)
    digest = checked.request_hash
    if operation_type == "cancel_return":
        original = commands.submit_return(db, actor=actor, work_order_id=world[2][0], request=request); _checkpoint(db)
        request = StockReturnCancelIn(operator_person_id=actor.person_id, reason="合成退回取消",
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        coordinates.update(operation_id=original.operation_id, request_id=request.request_id)
        digest = _hash(dict(operation_id=str(original.operation_id), operator_person_id=str(actor.person_id), reason=request.reason))
    return request, coordinates, digest


def _execute(db, user_id, coordinates, request):
    actor = load_formal_principal(db, user_id)
    if coordinates["operation_type"] == "submit_return":
        return commands.submit_return(db, actor=actor, work_order_id=coordinates["work_order_id"], request=request)
    return commands.cancel_return(db, actor=actor, work_order_id=coordinates["work_order_id"],
        operation_id=coordinates["operation_id"], request=request)


def _app(db, user_id):
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal":
                app.dependency_overrides[dependency.call] = lambda: load_formal_principal(db, user_id)
    return app


def assert_stock_return_http_gate(api_engine, fixture_engine):
    for kind, world in query_worlds(fixture_engine).items():
        target, transit = _destination(fixture_engine, world[0])
        baseline = snapshot(api_engine)
        for operation_type in ("submit_return", "cancel_return"):
            for action in ("post", "seal"):
                with Session(api_engine) as db:
                    selection = _selection(db, world, target, transit)
                    request, coordinates, digest = _command(db, world, selection, operation_type)
                    path = f"/api/v1/work-orders/{coordinates['work_order_id']}/returns"
                    if operation_type == "cancel_return": path += f"/{coordinates['operation_id']}/cancellations"
                    lookup = path + "/by-request/" + request.request_id
                    with TestClient(_app(db, world[1])) as client, patch.object(db, "commit", side_effect=lambda: _checkpoint(db)):
                        if operation_type == "submit_return":
                            local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
                            before_preview = snapshot(local)
                            prepared = client.post(path + "/preview", json=selection.model_dump(mode="json"))
                            assert prepared.status_code == 200, prepared.text
                            assert prepared.json()["plan_hash"] == request.expected_plan_hash
                            assert prepared.json()["request_hash"] == digest and "qr_code" not in prepared.text
                            assert snapshot(local) == before_preview
                        missing = client.get(lookup)
                        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "stock_return_not_observed"
                        result = client.post(path, json=request.model_dump(mode="json")) if action == "post" else client.post(
                            lookup + "/seal", json=dict(operator_person_id=str(request.operator_person_id), request_hash=digest))
                        assert result.status_code == 200, result.text
                        local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
                        before = snapshot(local); statements = []
                        def capture(_c, _cu, statement, _p, _ctx, _m): statements.append(statement)
                        connection = db.connection(); event.listen(connection, "before_cursor_execute", capture)
                        try: found = client.get(lookup)
                        finally: event.remove(connection, "before_cursor_execute", capture)
                        assert found.status_code == 200 and found.json() == result.json(), found.text
                        assert found.headers["cache-control"] == "private, no-store"
                        assert "qr_code" not in found.text
                        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
                        assert snapshot(local) == before and not db.new and not db.dirty and not db.deleted
                    db.rollback()
                assert snapshot(api_engine) == baseline
                print(f"PG16 {kind} {operation_type} HTTP {action}, exact SELECT-only GET and full rollback PASS", flush=True)


def assert_stock_return_seal_commit_gate(api_engine, fixture_engine):
    # Commit permanent 0101 seals only after all older downgrade proofs.
    def quantities():
        with Session(api_engine) as db:
            return dict(db.execute(select(StockBalance.stock_account_id, StockBalance.quantity).where(StockBalance.quantity != 0)).all())
    for kind, world in query_worlds(fixture_engine).items():
        target, transit = _destination(fixture_engine, world[0])
        with Session(api_engine) as db:
            selection = _selection(db, world, target, transit)
            _checkpoint(db); db.commit()
        for operation_type in ("submit_return", "cancel_return"):
            for first in ("seal", "post"):
                before = quantities()
                with Session(api_engine) as db:
                    request, coordinates, digest = _command(db, world, selection, operation_type)
                    _checkpoint(db); db.commit()
                started = Event(); identity = {}
                def seal(db):
                    return recovery.seal_return_request(db, actor=load_formal_principal(db, world[1]),
                        **coordinates, request_hash=digest)
                def late():
                    with Session(api_engine) as db:
                        db.execute(text("SET LOCAL statement_timeout='30s'"))
                        identity["pid"] = db.scalar(text("SELECT pg_backend_pid()")); started.set()
                        if first == "post":
                            result = seal(db); _checkpoint(db); db.commit(); return result
                        try:
                            _execute(db, world[1], coordinates, request.model_copy(update={"idempotency_key": uuid4().hex}))
                        except InventoryReadError as exc: return exc.code
                        raise AssertionError("A late return command executed after its seal")
                with Session(api_engine) as db, ThreadPoolExecutor(max_workers=1) as workers:
                    result = seal(db) if first == "seal" else _execute(db, world[1], coordinates, request)
                    _checkpoint(db)
                    future = workers.submit(late); assert started.wait(timeout=5)
                    try:
                        blocked = False; until = monotonic() + 5
                        with fixture_engine.connect() as probe:
                            while monotonic() < until:
                                blocked = bool(probe.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), identity))
                                if blocked: break
                                sleep(.02)
                        assert blocked, "Competing request did not reach the held ledger lock"
                        db.commit()
                    except BaseException:
                        db.rollback(); raise
                    assert future.result(timeout=35) == ("stock_return_request_sealed" if first == "seal" else result)
                with Session(api_engine) as db:
                    actor = load_formal_principal(db, world[1])
                    assert recovery.lookup_return_request(db, actor=actor, **coordinates) == result
                    assert seal(db) == result
                    db.rollback()
                # Undo only the synthetic unshipped return, retaining every fact.
                operation_id = (result.operation_id if first == "post" else None) if operation_type == "submit_return" else (
                    coordinates["operation_id"] if first == "seal" else None)
                if operation_id:
                    with Session(api_engine) as db:
                        actor = load_formal_principal(db, world[1])
                        cleanup = StockReturnCancelIn(operator_person_id=actor.person_id, reason="合成测试归还占用",
                            request_id=uuid4().hex, idempotency_key=uuid4().hex)
                        commands.cancel_return(db, actor=actor, operation_id=operation_id, request=cleanup)
                        _checkpoint(db); db.commit()
                assert quantities() == before
                print(f"PG16 {kind} {operation_type} {first}-first real concurrency, fresh recovery and zero net reserved stock PASS", flush=True)
