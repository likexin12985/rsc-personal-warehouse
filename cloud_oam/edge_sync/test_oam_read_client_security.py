import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
READ_CLIENT = WORK / "inventory_query_portal" / "oam_read_client.py"
if os.getenv("RSC_EDGE_TEST_FORCE_IMPORT_STUBS") == "1" or not READ_CLIENT.is_file():
    pytest.skip(
        "local OAM read client is intentionally absent from the hosted repository",
        allow_module_level=True,
    )
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))

from inventory_query_portal import oam_read_client


def _edge_response(payload, *, status=200):
    return SimpleNamespace(
        returncode=0,
        stdout=json.dumps({
            "status": status,
            "headers": {"content-type": "application/json"},
            "bodyBase64": base64.b64encode(json.dumps(payload).encode()).decode(),
        }).encode(),
        stderr=b"",
    )


def test_oam_token_is_not_exposed_in_edge_process_arguments():
    token = "sensitive-oam-token-for-test"
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        # Exercise the real shared Python adapter up to its subprocess boundary;
        # never start Node or send a business request from this contract test.
        assert len(command) == 2 and Path(command[0]).name == "node"
        assert Path(command[1]) == Path.home() / "Library/Application Support/CodexLocalEdge/edge_http.mjs"
        request = json.loads(kwargs["input"])
        assert request["method"] == "POST"
        assert request["url"] == oam_read_client.BASE_URL + "/readonly"
        assert base64.b64decode(request["bodyBase64"]) == b'{"page": 1}'
        captured["headers"] = request["headers"]
        assert token not in json.dumps(kwargs.get("env", {}))
        return _edge_response({"success": True, "model": {}})

    with (
        patch.object(oam_read_client, "get_oam_token", return_value=token),
        patch.object(oam_read_client.subprocess, "run", side_effect=fake_run),
    ):
        response = oam_read_client.post_json("/readonly", {"page": 1}, retries=1)

    assert response["success"] is True
    assert token not in " ".join(captured["command"])
    assert captured["headers"]["Oam-Token"] == token


def test_oam_application_failure_is_not_blindly_retried():
    calls = 0

    def rejected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _edge_response({"success": False, "code": "auth_required", "message": "session invalid"})

    with (
        patch.object(oam_read_client, "get_oam_token", return_value="test-token"),
        patch.object(oam_read_client.subprocess, "run", side_effect=rejected),
        patch.object(oam_read_client.time, "sleep") as sleep,
        pytest.raises(oam_read_client.OamNonRetryableReadError),
    ):
        oam_read_client.post_json("/readonly", {}, retries=3)

    assert calls == 1
    sleep.assert_not_called()


def test_edge_timeout_is_not_replayed_or_sent_through_another_transport():
    with (
        patch.object(oam_read_client, "get_oam_token", return_value="test-token"),
        patch.object(oam_read_client.subprocess, "run", side_effect=subprocess.TimeoutExpired("edge-adapter", 90)) as run,
        patch.object(oam_read_client.time, "sleep") as sleep,
        pytest.raises(RuntimeError, match="outcome unknown"),
    ):
        oam_read_client.post_json("/readonly", {}, retries=3)
    run.assert_called_once()
    sleep.assert_not_called()
