"""The shipped mini SDK crosses real HTTP and API-role PostgreSQL boundaries."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from pg16_work_order_query_gate import query_worlds
from pg16_work_order_material_gate import _checkpoint
from pg16_stock_return_gate import _destination
from pg16_stock_return_recovery_gate import snapshot
from pg16_stock_return_transport_gate import _selection, _command, _app
from app.formal_access import load_formal_principal


def assert_stock_return_mini_gate(api_engine, fixture_engine):
    node = shutil.which("node"); assert node, "Node is required for the stock return mini SDK gate"
    script = Path(__file__).resolve().parents[2] / "miniprogram/tests/fixtures/stock-return-submit-pg16.cjs"
    for kind, world in query_worlds(fixture_engine).items():
        target, transit = _destination(fixture_engine, world[0])
        baseline = snapshot(api_engine)
        for operation in ("submit_return", "cancel_return"):
            for mode in ("post", "lost_after", "lost_before"):
                with Session(api_engine) as db:
                    selection = _selection(db, world, target, transit)
                    actor = load_formal_principal(db, world[1])
                    operation_id = None
                    if operation == "cancel_return":
                        _, coordinates, _ = _command(db, world, selection, operation)
                        operation_id = str(coordinates["operation_id"])
                    fixture = dict(workOrderId=str(world[2][0]), personId=str(actor.person_id), authorizationVersion=actor.authorization_version,
                        targetLocationId=str(target), transitLocationId=str(transit), operationId=operation_id,
                        operationType=operation, reason="核验实物后退回🔧\n保管责任独立处理", lines=selection.model_dump(mode="json")["lines"], tracked=kind == "serial", mode=mode)
                    local = SimpleNamespace(connect=lambda: nullcontext(db.connection()))
                    process = subprocess.Popen([node, str(script)], text=True, bufsize=1, stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "")})
                    try:
                        process.stdin.write(json.dumps(fixture) + "\n"); process.stdin.flush()
                        with selectors.DefaultSelector() as ready, TestClient(_app(db, world[1])) as client, patch.object(db, "commit", side_effect=lambda: _checkpoint(db)):
                            ready.register(process.stdout, selectors.EVENT_READ)
                            complete = False; posts = 0
                            for _ in range(15):
                                assert ready.select(timeout=30), "Stock return SDK stopped producing requests"
                                message = process.stdout.readline()
                                assert message, "Stock return SDK exited: " + process.stderr.read()
                                data = json.loads(message)
                                if data.get("complete"):
                                    assert data == dict(complete=True, posts=1, previews=1 if operation == "submit_return" else 0,
                                        reads=3 if mode == "lost_before" else 1, seals=1 if mode == "lost_before" else 0,
                                        confirmations=1, status="sealed" if mode == "lost_before" else "confirmed")
                                    complete = True; break
                                request = data["request"]
                                write = request["method"] == "POST" and not request["path"].endswith("/preview")
                                command = write and not request["path"].endswith("/seal")
                                if command: posts += 1; assert posts == 1
                                before = snapshot(local); statements = []
                                def capture(_c, _cu, statement, _p, _ctx, _m): statements.append(statement)
                                connection = db.connection(); event.listen(connection, "before_cursor_execute", capture)
                                try:
                                    if command and mode == "lost_before": reply = {"transportLost": True}
                                    else:
                                        response = client.request(request["method"], request["path"], json=request.get("data"), headers=request["headers"])
                                        assert response.status_code in (200, 404), response.text
                                        assert "no-store" in response.headers["cache-control"]
                                        reply = {"transportLost": True} if command and mode == "lost_after" else {"status": response.status_code, "body": response.json()}
                                finally: event.remove(connection, "before_cursor_execute", capture)
                                if not write:
                                    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
                                    assert snapshot(local) == before
                                process.stdin.write(json.dumps(reply) + "\n"); process.stdin.flush()
                            assert complete
                        process.stdin.close()
                        process.wait(timeout=10); assert process.returncode == 0, process.stderr.read()
                    finally:
                        if process.poll() is None: process.kill(); process.wait(timeout=10)
                        for stream in (process.stdin, process.stdout, process.stderr): stream.close()
                        db.rollback()
                assert snapshot(api_engine) == baseline
                print(f"PG16 {kind} mini {operation} {mode}: one POST, original GET, exact digest, SELECT-only reads and full rollback PASS", flush=True)
