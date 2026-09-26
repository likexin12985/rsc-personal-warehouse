"""Public input isolation and disabled/unknown-result boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import Settings, get_settings
from app.dependencies import get_formal_principal
from app.formal_services.opening_count_import_intake import opening_import_request_key
from app.formal_services.opening_count_import_jobs import OpeningCountImportJobError
from app.routers import formal_opening_imports as route
from app import opening_count_import_worker as worker
from test_opening_count_import_source import _actor


BASE = "/api/v1/stocktakes/opening/imports/opening-count"


@pytest.fixture
def web():
    app = FastAPI()
    app.include_router(route.router, prefix="/api")
    settings = SimpleNamespace(opening_count_import_enabled=False,
        database_url="postgresql+psycopg://synthetic", file_storage_configuration_ready=lambda: True)
    storage = SimpleNamespace(provider_code="aliyun_oss_v2")
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_formal_principal] = _actor
    app.dependency_overrides[route.get_formal_file_storage_adapter] = lambda: storage

    def no_database():
        raise AssertionError("database must not be opened")

    app.dependency_overrides[route.get_import_session_factory] = lambda: no_database
    with TestClient(app) as client:
        yield client, app, settings


def coordinates():
    return {key: str(uuid4()) for key in ("source_file_id", "task_id", "round_id", "scope_id")}


@pytest.mark.parametrize("method,path,payload", [
    ("post", "/jobs", coordinates()), ("get", "/jobs/recovery", None),
    ("get", f"/jobs/{uuid4()}", None), ("post", f"/jobs/{uuid4()}/confirm", {}),
    ("get", f"/jobs/{uuid4()}/review", None),
    ("get", "/jobs/management-recovery?" + "&".join(f"{key}={value}" for key, value in coordinates().items()), None),
    ("post", "/source-upload-intents", {"original_filename": "import.xlsx", "size_bytes": 12, "sha256": "a" * 64}),
    ("post", f"/sources/{uuid4()}/complete", None),
    ("post", f"/jobs/{uuid4()}/error-download-intents", {}),
    ("post", f"/jobs/{uuid4()}/cancel", {}),
])
def test_disabled_import_routes_never_open_database(web, method, path, payload):
    client, _, _ = web
    response = client.request(method, BASE + path, json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "opening_import_disabled"
    assert "no-store" in response.headers["cache-control"]


def test_capability_and_default_configuration_remain_off(web):
    client, _, _ = web
    assert client.get(BASE + "/capabilities").json() == {"available": False}
    assert Settings.model_fields["opening_count_import_enabled"].default is False


@pytest.mark.parametrize("field", ["actor_id", "preview", "status", "source_sha256", "authorization_version"])
def test_intake_rejects_server_owned_proof_fields(web, field):
    client, _, _ = web
    response = client.post(BASE + "/jobs", json={**coordinates(), field: "forged"})
    assert response.status_code == 422


def test_source_request_cannot_choose_purpose_or_bypass_bound(web):
    client, _, _ = web
    payload = {"original_filename": "import.xlsx", "size_bytes": 100, "sha256": "a" * 64}
    for change in ({"purpose": "opening_count_import_error"}, {"size_bytes": True},
                   {"size_bytes": 8 * 1024 * 1024 + 1}):
        assert client.post(BASE + "/source-upload-intents", json={**payload, **change}).status_code == 422
    assert client.post(BASE + f"/jobs/{uuid4()}/confirm", json={"preview": {}}).status_code == 422
    assert client.post(BASE + f"/jobs/{uuid4()}/error-download-intents",
        json={"file_id": str(uuid4())}).status_code == 422
    assert client.post(BASE + f"/jobs/{uuid4()}/cancel",
        json={"reason": "make it succeeded"}).status_code == 422


def test_unknown_storage_or_commit_error_is_sanitized_and_points_to_recovery(web):
    client, app, settings = web
    settings.opening_count_import_enabled = True

    def failed_database():
        raise RuntimeError("private signed URL and raw source cell must not escape")

    app.dependency_overrides[route.get_import_session_factory] = lambda: failed_database
    response = client.post(BASE + "/jobs", json=coordinates())
    assert response.status_code == 503 and "signed URL" not in response.text
    assert response.json()["detail"]["code"] == "opening_import_result_unknown"


def test_source_intent_fixes_purpose_and_limit_then_commits_once(web, monkeypatch):
    client, app, settings = web
    settings.opening_count_import_enabled = True
    settings.file_idempotency_hmac_secret = "synthetic-file-key-at-least-32-characters"
    settings.file_upload_intent_ttl_seconds = 600
    calls = []
    identifier = uuid4()

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            calls.append("close")

        def commit(self):
            calls.append("commit")

    def intent(db, **arguments):
        command = arguments["command"]
        assert command.purpose == "opening_count_import"
        assert command.mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert arguments["maximum_size_bytes"] == 8 * 1024 * 1024
        assert arguments["idempotency_key"] == "original-source"
        calls.append("intent")
        return SimpleNamespace(file_id=identifier, status="available", upload=None, replayed=True)

    app.dependency_overrides[route.get_import_session_factory] = lambda: Database
    monkeypatch.setattr(route.formal_files, "create_file_upload_intent", intent)
    response = client.post(BASE + "/source-upload-intents",
        json={"original_filename": "import.xlsx", "size_bytes": 100, "sha256": "a" * 64},
        headers={"Idempotency-Key": "original-source", "X-Request-ID": "source-request"})
    assert response.status_code == 201 and response.json()["file_id"] == str(identifier)
    assert response.json()["replayed"] and calls == ["intent", "commit", "close"]


def test_request_keys_are_requester_bound_and_domain_separated():
    first = opening_import_request_key("one", "same")
    assert first == opening_import_request_key("one", "same")
    assert first != opening_import_request_key("two", "same")
    from app.formal_services.inventory_report_jobs import report_request_key
    assert first != report_request_key("one", "same")


def test_management_recovery_is_read_only_and_has_no_file_capabilities(web, monkeypatch):
    client, app, settings = web
    settings.opening_count_import_enabled = True
    calls, params = [], coordinates()

    class Database:
        def __enter__(self):
            calls.append("open")
            return self

        def __exit__(self, *_):
            calls.append("close")

        def commit(self):
            pytest.fail("management inspection must not commit")

    def inspect(db, **arguments):
        assert {key: str(arguments[key]) for key in params} == params
        assert arguments["idempotency_key"] == "original-import-key"
        actor = arguments["actor"]
        return dict(schema_version="rsc.opening_import_management_recovery.v1", **params,
            reviewer_person_id=actor.person_id, reviewer_authorization_version=actor.authorization_version,
            actor_person_id=uuid4(), authorization_version=1, source_sha256="a" * 64,
            size_bytes=12, job_id=uuid4(), status="cancelled", completion_id=None,
            terminal_audit_id=uuid4(), terminal_verified=True, automatic_retry_allowed=False)

    app.dependency_overrides[route.get_import_session_factory] = lambda: Database
    monkeypatch.setattr(route, "read_opening_import_management_recovery", inspect)
    response = client.get(BASE + "/jobs/management-recovery", params=params,
                          headers={"Idempotency-Key": "original-import-key"})
    assert response.status_code == 200, response.text
    assert calls == ["open", "close"]
    assert response.json()["terminal_verified"] and not response.json()["automatic_retry_allowed"]
    assert "no-store" in response.headers["cache-control"]
    assert not set(response.json()) & {"download", "original_filename", "storage_key", "preview"}


def test_management_evidence_failure_keeps_the_result_unknown_and_redacted(web, monkeypatch):
    client, app, settings = web
    settings.opening_count_import_enabled = True

    class Database:
        def __enter__(self): return self
        def __exit__(self, *_): pass

    def broken(*_args, **_kwargs):
        raise ValueError("private source cell and audit payload must not escape")

    app.dependency_overrides[route.get_import_session_factory] = lambda: Database
    monkeypatch.setattr(route, "read_opening_import_management_recovery", broken)
    response = client.get(BASE + "/jobs/management-recovery", params=coordinates(),
                          headers={"Idempotency-Key": "original-import-key"})
    assert response.status_code == 503 and "private source" not in response.text
    assert "terminal_verified" not in response.text


@pytest.mark.parametrize("key", [None, "", "with space", "line\nbreak", "x" * 201, True])
def test_invalid_original_request_keys_are_refused(key):
    with pytest.raises(OpeningCountImportJobError) as error:
        opening_import_request_key("one", key)
    assert error.value.code == "opening_import_idempotency_invalid"


def test_worker_configuration_is_checked_before_runtime_imports(monkeypatch, capsys):
    monkeypatch.setattr(worker, "get_settings", lambda: SimpleNamespace(environment="production",
        database_url="postgresql+psycopg://synthetic", opening_count_import_enabled=False,
        file_storage_configuration_ready=lambda: False))
    monkeypatch.setattr(worker, "process_one_opening_count_import",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("database touched")))
    assert worker.main([]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "opening_import_worker_not_configured"
    assert worker.main(["--poll-seconds", "1"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "opening_import_worker_poll_invalid"


def test_worker_missing_environment_reports_controlled_error_without_traceback():
    root = Path(__file__).parents[2]
    environment = dict(os.environ, OAM_ENVIRONMENT="production", OAM_DATABASE_URL="", PYTHONPATH=str(root / "backend"))
    result = subprocess.run([sys.executable, "-m", "app.opening_count_import_worker"],
        cwd=root, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 2 and not result.stderr
    assert json.loads(result.stdout)["code"] == "opening_import_worker_not_configured"


def test_import_worker_compose_uses_explicit_disabled_profile_and_api_role():
    import yaml
    root = Path(__file__).parents[2]
    services = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    worker_config = services["opening-count-import-worker"]
    assert worker_config["profiles"] == ["imports"]
    assert worker_config["command"][:3] == ["python", "-m", "app.opening_count_import_worker"]
    environment = worker_config["environment"]
    assert environment["OAM_DATABASE_EXPECTED_RUNTIME_ROLE"] == "star_oam_api"
    assert environment["OAM_OPENING_COUNT_IMPORT_ENABLED"] == "${OAM_OPENING_COUNT_IMPORT_ENABLED:-false}"
    assert services["api"]["environment"]["OAM_OPENING_COUNT_IMPORT_ENABLED"] == environment["OAM_OPENING_COUNT_IMPORT_ENABLED"]
    assert "ports" not in worker_config and "volumes" not in worker_config
    assert worker_config["read_only"] and worker_config["cap_drop"] == ["ALL"]
    assert worker_config["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_polling_import_worker_sweeps_waiting_jobs_and_waits_when_idle(monkeypatch):
    from app.formal_services import opening_count_import_termination as termination
    from app.opening_count_import_worker import OpeningImportWorkerResult
    import time
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    outputs, sweeps = [], []
    identifier = uuid4()
    pages = iter([(identifier,), ()])
    monkeypatch.setattr(worker, "_opening_import_queue_page", lambda *a, **k: next(pages))
    def process(*args, job_id, **kwargs):
        assert job_id == identifier
        return OpeningImportWorkerResult(identifier, "failed")
    monkeypatch.setattr(worker, "process_one_opening_count_import", process)

    def sweep(factory, *, after_id):
        sweeps.append(after_id)
        return termination.OpeningImportContextSweep(1, uuid4())

    monkeypatch.setattr(termination, "sweep_awaiting_opening_imports", sweep)

    class Stop:
        waits = 0

        def is_set(self):
            return self.waits == 2

        def wait(self, seconds):
            assert seconds == 5
            self.waits += 1

    stop = Stop()
    worker._poll_opening_imports(lambda: None, storage=object(), poll_seconds=5, stop_event=stop,
                                emit=outputs.append)
    assert [row["status"] for row in outputs] == ["failed", "idle"]
    assert sweeps == [None] and stop.waits == 2


@pytest.mark.parametrize("limit,cursor", [(0, None), (101, None), (True, None), (1, "invalid")])
def test_awaiting_import_sweep_is_bounded_before_opening_database(limit, cursor):
    from app.formal_services.opening_count_import_termination import sweep_awaiting_opening_imports
    with pytest.raises(OpeningCountImportJobError, match="巡检参数"):
        sweep_awaiting_opening_imports(lambda: pytest.fail("database touched"), after_id=cursor, limit=limit)


def test_import_seal_routes_disabled_before_database(web):
    client,_,_=web
    payload={**coordinates(),'actor_person_id':str(uuid4()),'authorization_version':1,'source_sha256':'a'*64,'size_bytes':12}
    for response in [client.post(BASE+'/command-seals',json=payload),client.get(BASE+'/command-seals/recovery',params=payload)]:
        assert response.status_code==503
        assert response.json()['detail']['code']=='opening_import_disabled'
        assert 'no-store' in response.headers['cache-control']


@pytest.mark.parametrize('field,value',[('authorization_version',True),('size_bytes',True),('size_bytes',8388609),('source_sha256','bad'),('permanent_nonexecution',True)])
def test_import_seal_never_accepts_client_terminal_proof_or_malformed_binding(web,field,value):
    client,_,_=web
    payload={**coordinates(),'actor_person_id':str(uuid4()),'authorization_version':1,'source_sha256':'a'*64,'size_bytes':12}
    response=client.post(BASE+'/command-seals',json={**payload,field:value})
    assert response.status_code==422
