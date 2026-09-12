"""Actual mini SDK: identity-only registration, recovery and separate paired stock.

Synthetic master fixtures use the migrator. All API identity, audit and inventory
writes force deferred constraints inside one rollback-only outer transaction.
"""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select, func, event
from sqlalchemy.orm import Session

from app.demand_models import WorkOrderReplacement, WorkOrderMaterialOperation, WorkOrderCommandSeal
from app.foundation_models import SourceSystem
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material, work_order_removed_registration as registration
from app.formal_services.work_order_replacements import _hash
from app.inventory_models import InventoryLot, InventorySerial, InventoryTransaction, SerialCurrentPosition, StockAccount
from app.routers import formal_work_order_query
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_query_gate import query_worlds
from pg16_work_order_removed_registration_gate import case, app_for, snapshot, stock_facts, _counts
from test_inventory_posting import make_material


def assert_removed_registration_submit_gate(api_engine, fixture_engine):
    node = shutil.which("node")
    assert node, "Node is required for the removed SN mini submission gate"
    script = Path(__file__).resolve().parents[2] / "miniprogram/tests/fixtures/work-order-removed-registration-pg16.cjs"
    world = query_worlds(fixture_engine)["serial"]
    with Session(fixture_engine, expire_on_commit=False) as db:
        source = db.scalar(select(SourceSystem).where(SourceSystem.code == "starcharge_oam"))
        sku = make_material(db, source, tracking_mode="lot_and_serial", quantity_scale=0, allow_fraction=False)
        lot = InventoryLot(id=uuid4(), material_id=sku.id, lot_no="拆回批次-" + uuid4().hex)
        db.add(lot); db.commit()
    for mode in ("direct", "lost_response", "sealed"):
        baseline = snapshot(api_engine)
        with Session(api_engine) as db:
            args, line = case(db, world)
            edits = {"serial_no": "未知拆回-SN-" + uuid4().hex, "qr_code": "三码-🔧-" + uuid4().hex}
            if mode == "lost_response":
                edits.update(sku_code=sku.sku_code, lot_no=lot.lot_no)
            args["scan"] = args["scan"].model_copy(update=edits)
            consume = material.operation_request_payload(operation_type="consume", work_order_id=args["work_order_id"],
                operator_person_id=args["actor"].person_id, lines=(line,))["lines"][0]
            consume.pop("target_stock_account_id")
            fixture = {"mode": mode, "workOrderId": str(args["work_order_id"]), "personId": str(args["actor"].person_id),
                "authorizationVersion": args["actor"].authorization_version, "consumeLine": consume,
                "scan": args["scan"].model_dump(mode="json"),
                "expectedHash": _hash(registration.command_payload(args["work_order_id"], args["scan"]))}
            local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
            before = snapshot(local); stock_before = stock_facts(db); identities_before = _counts(db)
            models = (WorkOrderReplacement, WorkOrderMaterialOperation, InventoryTransaction, WorkOrderCommandSeal)
            count = lambda: tuple(db.scalar(select(func.count()).select_from(model)) for model in models)
            counts_before = count()
            app = app_for(db, world[1])
            app.include_router(formal_work_order_query.router, prefix="/api")
            for route in formal_work_order_query.router.routes:
                for dependency in route.dependant.dependencies:
                    if dependency.name == "principal":
                        app.dependency_overrides[dependency.call] = lambda: load_formal_principal(db, world[1])
            process = subprocess.Popen([node, str(script)], text=True, bufsize=1,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", "")})
            registered = None; posted = False; sealed = False; complete = False
            try:
                process.stdin.write(json.dumps(fixture) + "\n"); process.stdin.flush()
                with selectors.DefaultSelector() as ready, TestClient(app) as client, patch.object(db, "commit", side_effect=lambda: _checkpoint(db)):
                    ready.register(process.stdout, selectors.EVENT_READ)
                    for _ in range(30):
                        assert ready.select(timeout=30), "removed SN mini request timed out"
                        message = process.stdout.readline()
                        assert message, "removed SN mini exited before completion: " + process.stderr.read()
                        request = json.loads(message)
                        if request.get("complete"):
                            assert request == {"complete": True, "registrationPosts": 1,
                                "pairedPosts": int(mode != "sealed"), "seals": int(mode == "sealed"),
                                "reviews": 1 if mode == "sealed" else 2}
                            complete = True; break
                        data = request["request"]
                        path = data["path"]
                        assert path.startswith(f'/api/v1/work-orders/{args["work_order_id"]}/')
                        admitting = data["method"] == "POST" and path.endswith("/removed-registrations")
                        posting = data["method"] == "POST" and path.endswith("/material-replacements")
                        sealing = data["method"] == "POST" and path.endswith("/seal")
                        if admitting:
                            assert registered is None and mode != "sealed" and snapshot(local) == before
                        if posting:
                            assert registered is not None and not posted and stock_facts(db) == stock_before
                        if sealing:
                            assert mode == "sealed" and not sealed and snapshot(local) == before
                        request_before = snapshot(local)
                        response = client.request(data["method"], path, json=data.get("data"), headers=data["headers"])
                        payload = response.json()
                        if path.endswith("/removed-part") and registered is None:
                            assert response.status_code == 412 and payload["detail"]["code"] == "removed_serial_not_found"
                        elif data["method"] == "GET" and "/by-request/" in path and mode == "sealed" and not sealed:
                            assert response.status_code == 404 and payload["detail"]["code"] == "removed_registration_not_found"
                        else:
                            assert response.status_code == 200, (path, response.text)
                            if not posting:
                                assert response.headers.get("cache-control") == "private, no-store", path
                        if admitting:
                            registered = payload
                            assert _counts(db) == tuple(n + 1 for n in identities_before)
                            assert db.get(SerialCurrentPosition, UUID(registered["serial_id"])) is None
                            assert db.get(InventorySerial, UUID(registered["serial_id"])).lifecycle_status == "active"
                            assert stock_facts(db) == stock_before
                        elif posting:
                            posted = True
                            position = db.get(SerialCurrentPosition, UUID(registered["serial_id"]))
                            account = db.get(StockAccount, position.stock_account_id)
                            assert account.condition_code == "damaged" and account.custodian_person_id == args["actor"].person_id
                            assert account.material_id == UUID(registered["material_id"])
                            assert account.lot_id == (UUID(registered["lot_id"]) if registered["lot_id"] else None)
                        elif sealing:
                            sealed = True
                            assert stock_facts(db) == stock_before
                            assert _counts(db) == (*identities_before[:3], identities_before[3] + 1)
                        else:
                            assert snapshot(local) == request_before
                        if "/removed-registrations" in path and response.status_code == 200:
                            assert "qr_code" not in response.text
                            digest = payload["seal"]["request_hash"] if payload.get("lookup_status") == "sealed_not_executed" else payload["request_hash"]
                            assert digest == fixture["expectedHash"]
                        process.stdin.write(json.dumps({"status": response.status_code, "body": payload}) + "\n"); process.stdin.flush()
                    assert complete
                process.stdin.close()
                assert process.wait(timeout=10) == 0, process.stderr.read()
                delta = (0, 0, 0, 1) if mode == "sealed" else (1, 2, 2, 0)
                assert count() == tuple(n + added for n, added in zip(counts_before, delta))
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr): stream.close()
                db.rollback()
        assert snapshot(api_engine) == baseline
        print(f"PG16 removed SN mini {mode}: original proof, exact completion obligations and full rollback PASS", flush=True)
    assert_completion_no_write_gate(api_engine, world)


def assert_completion_no_write_gate(api_engine, world):
    from app.formal_services.work_order_completion import completion_check
    before = snapshot(api_engine)
    selects = []
    def require_select(connection, cursor, statement, parameters, context, executemany):
        # Opening integrity currently acquires evidence/ledger row locks. Keep
        # that proof intact while rejecting any data mutation by the read path.
        assert statement.lstrip().upper().startswith("SELECT"), "completion attempted a non-SELECT statement"
        selects.append(statement)
    event.listen(api_engine, "before_cursor_execute", require_select)
    try:
        with Session(api_engine) as db:
            actor = load_formal_principal(db, world[1])
            result = completion_check(db, actor=actor, work_order_id=world[2][0])
            assert result.material_check_status == "clear" and not result.blockers and result.issue_count == 0
            db.rollback()
    finally:
        event.remove(api_engine, "before_cursor_execute", require_select)
    assert selects and any("FOR UPDATE" in statement for statement in selects)
    assert snapshot(api_engine) == before
    print("PG16 completion clear: SELECT-only, original opening proof locks retained, complete state unchanged PASS", flush=True)
