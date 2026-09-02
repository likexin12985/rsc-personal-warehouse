import os
import stat
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


def test_oam_token_is_not_exposed_in_curl_process_arguments():
    token = "sensitive-oam-token-for-test"
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        header_argument = command[command.index("--header") + 1]
        header_path = Path(header_argument.removeprefix("@"))
        captured["header_path"] = header_path
        captured["header_mode"] = stat.S_IMODE(header_path.stat().st_mode)
        captured["headers"] = header_path.read_text(encoding="utf-8")
        assert kwargs["input"] == b'{"page": 1}'
        return SimpleNamespace(
            returncode=0,
            stdout=b'{"success":true,"model":{}}\n__OAM_HTTP_CODE__:200',
            stderr=b"",
        )

    with (
        patch.object(oam_read_client, "get_oam_token", return_value=token),
        patch.object(oam_read_client.subprocess, "run", side_effect=fake_run),
    ):
        response = oam_read_client.post_json("/readonly", {"page": 1}, retries=1)

    assert response["success"] is True
    assert token not in " ".join(captured["command"])
    assert captured["header_mode"] == 0o600
    assert f"Oam-Token: {token}" in captured["headers"]
    assert not captured["header_path"].exists()


def test_oam_application_failure_is_not_blindly_retried():
    calls = 0

    def rejected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            returncode=0,
            stdout=(
                b'{"success":false,"code":"auth_required",'
                b'"message":"session invalid"}'
                b"\n__OAM_HTTP_CODE__:200"
            ),
            stderr=b"",
        )

    with (
        patch.object(oam_read_client, "get_oam_token", return_value="test-token"),
        patch.object(oam_read_client.subprocess, "run", side_effect=rejected),
        patch.object(oam_read_client.time, "sleep") as sleep,
        pytest.raises(oam_read_client.OamNonRetryableReadError),
    ):
        oam_read_client.post_json("/readonly", {}, retries=3)

    assert calls == 1
    sleep.assert_not_called()
