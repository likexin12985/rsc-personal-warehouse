"""Mini confirmation uses actual SDK, paired HTTP routes and both PG16 facts.

All real stock operations flush deferred guards inside one rollback-only outer
transaction. Committed parent recovery/seal proofs are separate gate stages.
"""
from contextlib import nullcontext
from dataclasses import replace
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.demand_models import WorkOrderReplacement, WorkOrderMaterialOperation
from app.formal_access import load_formal_principal
from app.formal_services import work_order_material as material, work_order_replacements as replacements
from app.inventory_models import InventoryMovement, InventorySerial, FormalMaterial, StockAccount, InventoryTransaction
from app.routers import formal_work_order_query, formal_work_order_material
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_replacements_gate import replacement_snapshot
from pg16_work_order_query_gate import query_worlds


def assert_replacement_submit_gate(api_engine, fixture_engine):
    node = shutil.which("node")
    assert node, "Node is required for the paired mini submission gate"
    script = Path(__file__).resolve().parents[2] / "miniprogram/tests/fixtures/work-order-replacement-submit-pg16.cjs"
    for kind, (_account, user_id, orders, line) in query_worlds(fixture_engine).items():
        baseline = replacement_snapshot(api_engine)
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            occupied, _ = material.execute_occupy_operation(db, actor=actor, work_order_id=orders[0], lines=(line,),
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            _checkpoint(db)
            reserved = db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id == occupied.posting_transaction_id))
            account = db.get(StockAccount, reserved)
            sku = db.get(FormalMaterial, line.material_id)
            removed = db.scalar(select(InventorySerial).where(InventorySerial.material_id == line.material_id,
                InventorySerial.lot_id == account.lot_id, InventorySerial.lifecycle_status == "consumed")
                .order_by(InventorySerial.id).limit(1)) if line.serial_ids else None
            assert removed is not None or not line.serial_ids
            recovered = replacements.RecoveryLineInput(reserved, line.material_id, line.quantity, "damaged", lot_id=account.lot_id,
                serial_ids=(removed.id,) if removed else (), serial_verifications=(material.SerialVerificationInput(
                    removed.id, sku.sku_code, removed.serial_no, removed.qr_code),) if removed else ())
            pairs = (material.WorkOrderReplacementPairInput(line.serial_ids[0], removed.id),) if removed else ()
            command = replacements.replacement_request_payload(work_order_id=orders[0], operator_person_id=actor.person_id,
                consume_lines=(replace(line, stock_account_id=reserved),), recover_lines=(recovered,), pairs=pairs)
            fixture = {"workOrderId": str(orders[0]), "personId": str(actor.person_id), "authorizationVersion": actor.authorization_version,
                "expectedHash": replacements._hash(command), "pairs": command["replacement_pairs"], "recoverLines": command["recover_lines"],
                "consumeLines": [{key: value for key, value in row.items() if key != "target_stock_account_id"} for row in command["consume_lines"]],
                "scan": {"sku_code": sku.sku_code, "lot_no": None, "serial_no": removed.serial_no if removed else None,
                         "qr_code": removed.qr_code if removed else None}}
            local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
            before_post = replacement_snapshot(local)
            initial_counts = [db.scalar(select(func.count()).select_from(model)) for model in
                (WorkOrderReplacement, WorkOrderMaterialOperation, InventoryTransaction)]
            app = FastAPI()
            app.dependency_overrides[get_db] = lambda: db
            for router in (formal_work_order_query.router, formal_work_order_material.router):
                app.include_router(router, prefix="/api")
                for route in router.routes:
                    for dependency in route.dependant.dependencies:
                        if dependency.name == "principal":
                            app.dependency_overrides[dependency.call] = lambda: load_formal_principal(db, user_id)
            process = subprocess.Popen([node, str(script)], text=True, bufsize=1,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", "")})
            try:
                process.stdin.write(json.dumps(fixture) + "\n"); process.stdin.flush()
                with selectors.DefaultSelector() as ready, TestClient(app) as client, patch.object(db, "commit", side_effect=lambda: _checkpoint(db)):
                    ready.register(process.stdout, selectors.EVENT_READ)
                    complete = False
                    posted = False
                    for _ in range(16):
                        assert ready.select(timeout=30), "paired mini request timed out"
                        message = process.stdout.readline()
                        assert message, "paired mini exited before completion: " + process.stderr.read()
                        request = json.loads(message)
                        if request.get("complete"):
                            assert request == {"complete": True, "posts": 1, "scans": 2, "previews": 1, "reads": 1, "confirmations": 1}
                            complete = True
                            break
                        data = request["request"]
                        assert data["path"].startswith(f"/api/v1/work-orders/{orders[0]}/")
                        stock_post = data["method"] == "POST" and data["path"].endswith("/material-replacements")
                        if stock_post:
                            assert not posted and replacement_snapshot(local) == before_post
                            posted = True
                        snapshot = replacement_snapshot(local)
                        response = client.request(data["method"], data["path"], json=data.get("data"), headers=data["headers"])
                        assert response.status_code == 200, (data["path"], response.text)
                        if not stock_post:
                            assert response.headers["cache-control"] == "private, no-store"
                            assert replacement_snapshot(local) == snapshot
                        if data["path"].endswith("/preview") or "/by-request/" in data["path"]:
                            assert response.json()["request_hash"] == fixture["expectedHash"]
                        process.stdin.write(json.dumps({"status": response.status_code, "body": response.json()}) + "\n")
                        process.stdin.flush()
                    assert complete
                process.stdin.close()
                assert process.wait(timeout=10) == 0, process.stderr.read()
                actual_counts = [db.scalar(select(func.count()).select_from(model)) for model in
                    (WorkOrderReplacement, WorkOrderMaterialOperation, InventoryTransaction)]
                assert actual_counts == [initial_counts[0] + 1, initial_counts[1] + 2, initial_counts[2] + 2]
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
                db.rollback()
        assert replacement_snapshot(api_engine) == baseline
        print(f"PG16 {kind} paired mini SDK full review, one durable POST, both original facts and complete rollback PASS", flush=True)
