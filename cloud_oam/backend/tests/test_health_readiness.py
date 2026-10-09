from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.kms_readiness import KmsReadinessGate
from app.database import (
    HEALTH_DATABASE_CONNECT_TIMEOUT_SECONDS,
    HEALTH_DATABASE_TCP_TIMEOUT_MILLISECONDS,
)
from app.kms_readiness import DEFAULT_PROBE_BUDGET_SECONDS
import app.main as main
from app.config import Settings
from app.persisted_key_references import AliyunPersistedKeyReference
from app.production_key_runtime import ProviderKeyRuntime, _configuration


AUTH_COORDINATE = (
    "authentication_idempotency",
    "kms-production-auth-key",
    1,
)
ROOT = Path(__file__).resolve().parents[2]


class _HealthyConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, _statement):
        return None


def _body(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.fixture
def provider_runtime(monkeypatch):
    gates = []

    def install(probe):
        settings = main.settings.model_copy(update={
            "environment": "production",
            "auth_idempotency_encryption_provider": "aliyun_kms",
            "auth_idempotency_kms_key_id": AUTH_COORDINATE[1],
            "auth_idempotency_encryption_key_version": AUTH_COORDINATE[2],
            "material_request_contact_encryption_provider": "disabled",
            "material_request_writes_enabled": False,
        })
        pin = AliyunPersistedKeyReference(
            AUTH_COORDINATE[0], AUTH_COORDINATE[2], AUTH_COORDINATE[1],
            "synthetic-provider-version", "a" * 64,
        )
        loader = SimpleNamespace(
            pin_manifest=lambda: {AUTH_COORDINATE: pin}, probe=probe,
        )
        runtime = ProviderKeyRuntime(_configuration(settings), (pin,), (), None, loader)
        gate = KmsReadinessGate(loader=runtime.probe)
        gates.append(gate)
        monkeypatch.setattr(main, "settings", settings)
        monkeypatch.setattr(main, "app", SimpleNamespace(state=SimpleNamespace(
            provider_key_runtime=runtime,
            kms_readiness_gate=gate,
            required_kms_coordinates=runtime.required_coordinates,
        )))
        return runtime

    yield install
    for gate in gates:
        gate.close()


def test_liveness_never_calls_database_or_kms(monkeypatch) -> None:
    calls = 0

    def explode():
        nonlocal calls
        calls += 1
        raise RuntimeError("database details")

    monkeypatch.setattr(main, "health_engine", SimpleNamespace(connect=explode))
    response = main.health_live()

    assert response.status_code == 200
    assert _body(response)["status"] == "live"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert calls == 0


def test_readiness_database_failure_is_desensitized_and_skips_kms(
    monkeypatch, provider_runtime,
) -> None:
    kms_calls = 0

    def kms_loader(*_coordinate):
        nonlocal kms_calls
        kms_calls += 1
        return bytes(range(32))

    def database_failure():
        raise RuntimeError("postgresql hostname and credentials")

    monkeypatch.setattr(
        main,
        "health_engine",
        SimpleNamespace(connect=database_failure),
    )
    provider_runtime(kms_loader)

    response = main._readiness_response()

    assert response.status_code == 503
    assert _body(response) == {
        "ok": False,
        "status": "not_ready",
        "service": "star-oam-cloud",
        "version": main.APP_VERSION,
        "release_scope": main.settings.release_scope,
    }
    assert kms_calls == 0


def test_production_readiness_uses_ttl_kms_gate_after_database(
    monkeypatch, provider_runtime,
) -> None:
    kms_calls = 0
    events = []

    def kms_loader(*coordinate):
        nonlocal kms_calls
        kms_calls += 1
        events.append("provider")
        assert coordinate == AUTH_COORDINATE
        return bytes(range(32))

    def database_connection():
        events.append("database")
        return _HealthyConnection()

    monkeypatch.setattr(
        main,
        "health_engine",
        SimpleNamespace(connect=database_connection),
    )
    runtime = provider_runtime(kms_loader)
    assert main.app.state.required_kms_coordinates == runtime.required_coordinates

    first = main._readiness_response()
    second = main._readiness_response()

    assert first.status_code == second.status_code == 200
    assert _body(first)["status"] == "ready"
    assert _body(first)["release_scope"] == main.settings.release_scope
    assert kms_calls == 1
    assert events == ["database", "provider", "database"]


def test_health_reports_the_isolated_trial_scope_without_secrets(monkeypatch) -> None:
    monkeypatch.setattr(main.settings, "release_scope", "trial-mvp")
    response = main._health_response(ready=True, status="ready")

    assert _body(response) == {
        "ok": True,
        "status": "ready",
        "service": "star-oam-cloud",
        "version": main.APP_VERSION,
        "release_scope": "trial-mvp",
    }
    assert "secret" not in response.body.decode("utf-8").lower()


def test_settings_rejects_an_unreviewed_release_scope() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="test", database_url="sqlite+pysqlite:///:memory:", release_scope="formal-v1")


def test_production_readiness_never_exposes_kms_failure(monkeypatch, provider_runtime) -> None:
    kms_calls = 0

    def kms_failure(*_coordinate):
        nonlocal kms_calls
        kms_calls += 1
        raise RuntimeError("secret endpoint key-id ciphertext sdk detail")

    monkeypatch.setattr(
        main,
        "health_engine",
        SimpleNamespace(connect=lambda: _HealthyConnection()),
    )
    provider_runtime(kms_failure)

    response = main._readiness_response()
    serialized = response.body.decode("utf-8")

    assert response.status_code == 503
    assert kms_calls == 1
    assert _body(response)["status"] == "not_ready"
    for forbidden in ("endpoint", "key-id", "ciphertext", "sdk"):
        assert forbidden not in serialized


def test_combined_database_and_kms_budgets_fit_compose_timeout() -> None:
    combined_budget = (
        HEALTH_DATABASE_CONNECT_TIMEOUT_SECONDS
        + HEALTH_DATABASE_TCP_TIMEOUT_MILLISECONDS / 1000
        + DEFAULT_PROBE_BUDGET_SECONDS
    )
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert combined_budget == 7
    assert "urlopen('http://127.0.0.1:8000/api/health/ready')" in compose
    assert "timeout: 8s" in compose
    assert "start_period: 20s" in compose
