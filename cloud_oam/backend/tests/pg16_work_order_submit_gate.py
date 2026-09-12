"""Actual mini-program SDK -> HTTP DTOs -> API-role ledger on disposable PG16.

Each endpoint flushes all deferred constraints in one rollback-only outer
transaction. Committed, fresh-session recovery is covered separately by the
ordinary recovery gate; this contract gate preserves its fixture stock.
"""
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.demand_models import WorkOrderMaterialOperation
from app.formal_access import load_formal_principal
from app.inventory_models import InventoryTransaction
from app.routers import formal_work_order_material, formal_work_order_query
from pg16_work_order_material_gate import _checkpoint, _snapshot
from pg16_work_order_query_gate import query_worlds


def assert_work_order_submit_gate(api_engine, fixture_engine):
    node = shutil.which("node")
    assert node, "Node is required for the mini-program/PG16 command contract"
    script = Path(__file__).resolve().parents[2] / "miniprogram/tests/fixtures/work-order-submit-pg16.cjs"
    for kind, (_account, user_id, orders, line) in query_worlds(fixture_engine).items():
        baseline = _snapshot(api_engine)
        with Session(api_engine) as db:
            actor = load_formal_principal(db, user_id)
            initial_transactions = db.scalar(select(func.count()).select_from(InventoryTransaction))
            initial_operations = db.scalar(select(func.count()).select_from(WorkOrderMaterialOperation))
            app = FastAPI()

            def request_db():
                yield db

            def principal():
                return load_formal_principal(db, user_id)

            for router in (formal_work_order_material.router, formal_work_order_query.router):
                app.include_router(router, prefix="/api")
                for route in router.routes:
                    for dependency in route.dependant.dependencies:
                        if dependency.name == "principal":
                            app.dependency_overrides[dependency.call] = principal
            app.dependency_overrides[get_db] = request_db
            fixture = {
                "workOrderId": str(orders[0]), "personId": str(actor.person_id),
                "authorizationVersion": actor.authorization_version,
                "materialId": str(line.material_id), "condition": line.condition_before,
                "proofs": [{"serial_id": str(row.serial_id), "sku_code": row.sku_code,
                            "serial_no": row.serial_no, "qr_code": row.qr_code}
                           for row in line.serial_verifications],
            }
            process = subprocess.Popen([node, str(script)], text=True, bufsize=1,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", "")})
            try:
                process.stdin.write(json.dumps(fixture) + "\n"); process.stdin.flush()
                with selectors.DefaultSelector() as ready, TestClient(app) as client, patch.object(
                        db, "commit", side_effect=lambda: _checkpoint(db)):
                    ready.register(process.stdout, selectors.EVENT_READ)
                    complete = False
                    for _ in range(32):
                        assert ready.select(timeout=30), "mini-program contract request timed out"
                        message = process.stdout.readline()
                        assert message, "mini-program contract exited before completion: " + process.stderr.read()
                        request = json.loads(message)
                        if request.get("complete"):
                            assert request == {"complete": True, "posts": 4, "previews": 4, "reads": 4, "confirmations": 4}
                            complete = True
                            break
                        data = request["request"]
                        assert data["path"].startswith(f"/api/v1/work-orders/{orders[0]}/")
                        assert data["method"] in {"GET", "POST"}
                        response = client.request(data["method"], data["path"],
                            json=data.get("data"), headers=data["headers"])
                        assert response.status_code == 200, (data["path"], response.text)
                        if data["method"] == "GET" or data["path"].endswith("/preview"):
                            assert response.headers["cache-control"] == "private, no-store"
                        process.stdin.write(json.dumps({"status": response.status_code, "body": response.json()}) + "\n")
                        process.stdin.flush()
                    assert complete, "mini-program made unexpected repeated requests"
                process.stdin.close()
                assert process.wait(timeout=10) == 0, process.stderr.read()
                assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == initial_transactions + 4
                assert db.scalar(select(func.count()).select_from(WorkOrderMaterialOperation)) == initial_operations + 4
            finally:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
                db.rollback()
        assert _snapshot(api_engine) == baseline
        print(f"PG16 {kind} mini SDK confirmation, single POST, original HTTP proof and complete rollback PASS", flush=True)
