from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.kms_readiness import KmsReadinessGate
from app.database import (
    HEALTH_DATABASE_CONNECT_TIMEOUT_SECONDS,
    HEALTH_DATABASE_TCP_TIMEOUT_MILLISECONDS,
)
from app.kms_readiness import DEFAULT_PROBE_BUDGET_SECONDS
import app.main as main


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
    monkeypatch,
) -> None:
    kms_calls = 0

    def kms_loader(*_coordinate):
        nonlocal kms_calls
        kms_calls += 1
        return bytes(range(32))

    def database_failure():
        raise RuntimeError("postgresql hostname and credentials")

    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(
        main,
        "health_engine",
        SimpleNamespace(connect=database_failure),
    )
    main.app.state.kms_readiness_gate = KmsReadinessGate(loader=kms_loader)
    main.app.state.required_kms_coordinates = frozenset({AUTH_COORDINATE})

    response = main._readiness_response()

    assert response.status_code == 503
    assert _body(response) == {
        "ok": False,
        "status": "not_ready",
        "service": "star-oam-cloud",
        "version": main.APP_VERSION,
    }
    assert kms_calls == 0


def test_production_readiness_uses_ttl_kms_gate_after_database(
    monkeypatch,
) -> None:
    kms_calls = 0

    def kms_loader(*coordinate):
        nonlocal kms_calls
        kms_calls += 1
        assert coordinate == AUTH_COORDINATE
        return bytes(range(32))

    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(
        main,
        "health_engine",
        SimpleNamespace(connect=lambda: _HealthyConnection()),
    )
    main.app.state.kms_readiness_gate = KmsReadinessGate(loader=kms_loader)
    main.app.state.required_kms_coordinates = frozenset({AUTH_COORDINATE})

    first = main._readiness_response()
    second = main._readiness_response()

    assert first.status_code == second.status_code == 200
    assert _body(first)["status"] == "ready"
    assert kms_calls == 1


def test_production_readiness_never_exposes_kms_failure(monkeypatch) -> None:
    def kms_failure(*_coordinate):
        raise RuntimeError("secret endpoint key-id ciphertext sdk detail")

    monkeypatch.setattr(main.settings, "environment", "production")
    monkeypatch.setattr(
        main,
        "health_engine",
        SimpleNamespace(connect=lambda: _HealthyConnection()),
    )
    main.app.state.kms_readiness_gate = KmsReadinessGate(loader=kms_failure)
    main.app.state.required_kms_coordinates = frozenset({AUTH_COORDINATE})

    response = main._readiness_response()
    serialized = response.body.decode("utf-8")

    assert response.status_code == 503
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
