"""Startup publication and live readiness for the reviewed provider runtime.

The real builder, runtime, authentication cipher and readiness gate are used;
database proof inputs and provider transports are deterministic local doubles.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import json
from types import SimpleNamespace

from fastapi import FastAPI
import pytest

from app.config import Settings
from app.kms_readiness import KmsReadinessGate
from app.persisted_key_references import (
    AUTH,
    AliyunPersistedKeyReference,
    PersistedKeyReferenceCatalog,
)
import app.main as main
import app.production_adapters as adapters
import app.production_key_runtime as wiring


class _Loader:
    def __init__(self, pins, events):
        self.pins = pins
        self.events = events
        self.request_calls = []
        self.probe_calls = []
        self.fail = False

    def pin_manifest(self):
        self.events.append("registry-read")
        return {
            (pin.purpose, pin.kms_key_id, pin.application_key_version): pin
            for pin in self.pins
        }

    def __call__(self, *coordinate):
        self.request_calls.append(coordinate)
        if self.fail:
            raise RuntimeError("private provider detail")
        return b"r" * 32

    def probe(self, *coordinate):
        self.probe_calls.append(coordinate)
        if self.fail:
            raise RuntimeError("private provider detail")
        return b"r" * 32


def _state_is_empty(app):
    assert app.state.provider_key_runtime is None
    assert app.state.kms_readiness_gate is None
    assert app.state.required_kms_coordinates == frozenset()


def _during_lifespan(app, body):
    async def run():
        async with main.lifespan(app):
            return body()

    return asyncio.run(run())


@pytest.fixture
def startup_world(monkeypatch, tmp_path):
    events = []
    settings = main.settings.model_copy(update={
        "environment": "production",
        "database_schema_mode": "alembic",
        "upload_dir": str(tmp_path / "uploads"),
        "auth_idempotency_encryption_provider": "aliyun_kms",
        "auth_idempotency_kms_key_id": "kms-startup-active",
        "auth_idempotency_encryption_key_version": 1,
        "material_request_contact_encryption_provider": "disabled",
        "material_request_writes_enabled": False,
    })
    pins = (
        AliyunPersistedKeyReference(AUTH, 1, "kms-startup-active", "provider-version-1", "a" * 64),
        AliyunPersistedKeyReference(AUTH, 2, "kms-startup-history", "provider-version-2", "b" * 64),
    )
    catalog = PersistedKeyReferenceCatalog(aliyun=pins)
    loader = _Loader(pins, events)
    app = FastAPI()
    db = SimpleNamespace(no_autoflush=nullcontext())
    built = []
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "app", app)
    monkeypatch.setattr(Settings, "validate_api_startup", lambda _self: events.append("settings"))

    def security(*_args, **_kwargs):
        _state_is_empty(app)
        events.append("database-security")

    @contextmanager
    def session():
        assert events[-1] == "database-security"
        events.append("session-enter")
        yield db
        events.append("session-exit")

    def scan(actual_db, **_kwargs):
        assert actual_db is db
        assert "database-security" in events
        _state_is_empty(app)
        events.append("stored-scan")
        return catalog

    def claims(actual_db, **_kwargs):
        assert actual_db is db
        assert "stored-scan" in events
        events.append("independent-claims")
        return catalog

    def build(actual_db, actual_settings):
        events.append("build-runtime")
        runtime = wiring.build_production_key_runtime(actual_db, actual_settings)
        built.append(runtime)
        return runtime

    def session_evidence(actual_db, **_kwargs):
        assert actual_db is db
        _state_is_empty(app)
        events.append("session-evidence")

    def boundary(**_kwargs):
        _state_is_empty(app)
        events.append("schema-boundary")

    monkeypatch.setattr(main, "validate_production_database_security", security)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(main, "build_production_key_runtime", build)
    monkeypatch.setattr(main, "validate_active_production_session_ip_evidence", session_evidence)
    monkeypatch.setattr(main, "_run_startup_database_boundary", boundary)
    monkeypatch.setattr(wiring, "scan_persisted_key_references", scan)
    monkeypatch.setattr(wiring, "read_required_key_claims", claims)
    monkeypatch.setattr(adapters, "get_configured_kms_loader", lambda _settings: loader)
    monkeypatch.setattr(adapters, "_validate_database_kms_pins", lambda *_args, **_kwargs: events.append("immutable-db-pins"))

    class HealthyConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, _statement):
            events.append("health-database")

    monkeypatch.setattr(main, "health_engine", SimpleNamespace(connect=HealthyConnection))
    return SimpleNamespace(app=app, settings=settings, events=events, loader=loader, pins=pins, built=built)


def test_runtime_publishes_after_all_startup_boundaries_and_shutdown_clears_it(startup_world):
    world = startup_world
    retained = []

    def body():
        assert world.events == [
            "settings", "database-security", "session-enter", "build-runtime",
            "stored-scan", "independent-claims", "immutable-db-pins",
            "registry-read", "session-evidence", "session-exit", "schema-boundary",
        ]
        runtime = world.app.state.provider_key_runtime
        assert type(runtime) is wiring.ProviderKeyRuntime
        assert runtime is world.built[0]
        assert world.app.state.required_kms_coordinates == runtime.required_coordinates
        gate = world.app.state.kms_readiness_gate
        assert isinstance(gate, KmsReadinessGate)
        retained.append((runtime, gate))
        assert not world.loader.probe_calls and not world.loader.request_calls

    _during_lifespan(world.app, body)
    _state_is_empty(world.app)
    runtime, gate = retained[0]
    assert gate.is_ready(runtime.required_coordinates) is False
    assert not world.loader.probe_calls and not world.loader.request_calls


@pytest.mark.parametrize("failure", ["database", "scan", "session-evidence", "schema-boundary"])
def test_failed_startup_never_publishes_a_provider_runtime(startup_world, monkeypatch, failure):
    world = startup_world

    def reject(*_args, **_kwargs):
        _state_is_empty(world.app)
        world.events.append("rejected-" + failure)
        raise RuntimeError("synthetic failed startup boundary")

    target, name = {
        "database": (main, "validate_production_database_security"),
        "scan": (wiring, "scan_persisted_key_references"),
        "session-evidence": (main, "validate_active_production_session_ip_evidence"),
        "schema-boundary": (main, "_run_startup_database_boundary"),
    }[failure]
    monkeypatch.setattr(target, name, reject)
    with pytest.raises(RuntimeError):
        _during_lifespan(world.app, lambda: pytest.fail("failed startup must never yield"))
    _state_is_empty(world.app)
    assert not world.loader.request_calls and not world.loader.probe_calls
    if failure == "database":
        assert world.events == ["settings", "rejected-database"]
        assert not world.built
    elif failure == "scan":
        assert "independent-claims" not in world.events
        assert "registry-read" not in world.events
        assert not world.built


def test_ready_uses_full_binding_coordinates_and_provider_probe_not_request_loader(
    startup_world, monkeypatch
):
    world = startup_world
    actual_probe = wiring.ProviderKeyRuntime.probe
    observed_coordinates = []

    def probe(runtime, *coordinate):
        observed_coordinates.append(coordinate)
        return actual_probe(runtime, *coordinate)

    monkeypatch.setattr(wiring.ProviderKeyRuntime, "probe", probe)

    def body():
        runtime = world.app.state.provider_key_runtime
        expected = runtime.required_coordinates
        assert len(expected) == 2
        for purpose, digest, version in expected:
            assert purpose == AUTH and version in {1, 2}
            assert len(digest) == 64
            assert digest not in {pin.kms_key_id for pin in world.pins}
        first = main._readiness_response()
        second = main._readiness_response()
        assert first.status_code == second.status_code == 200
        assert json.loads(first.body)["status"] == "ready"
        assert set(observed_coordinates) == expected
        assert len(observed_coordinates) == 2
        assert set(world.loader.probe_calls) == {
            (pin.purpose, pin.kms_key_id, pin.application_key_version) for pin in world.pins
        }
        assert not world.loader.request_calls
        assert world.events.count("health-database") == 2

    _during_lifespan(world.app, body)


def test_cached_request_dek_cannot_make_readiness_pass_after_provider_loss(startup_world):
    world = startup_world

    def body():
        runtime = world.app.state.provider_key_runtime
        cipher = runtime.authentication_cipher(world.settings)
        assert cipher.active_key_version() == 1
        assert len(world.loader.request_calls) == 1
        world.loader.fail = True
        # The request still owns its resolved key; readiness must do a live
        # independent provider call rather than inspecting that cache.
        assert cipher.active_key_version() == 1
        response = main._readiness_response()
        assert response.status_code == 503
        assert json.loads(response.body)["status"] == "not_ready"
        assert "private provider detail" not in response.body.decode()
        assert len(world.loader.request_calls) == 1
        assert len(world.loader.probe_calls) == 2

    _during_lifespan(world.app, body)


@pytest.mark.parametrize("bad_state", ["missing", "wrong-type", "empty", "partial", "extra", "changed-pin"])
def test_readiness_rejects_runtime_or_coordinate_mismatch_before_provider_call(startup_world, bad_state):
    world = startup_world

    def body():
        runtime = world.app.state.provider_key_runtime
        coordinates = runtime.required_coordinates
        if bad_state == "missing":
            world.app.state.provider_key_runtime = None
        elif bad_state == "wrong-type":
            world.app.state.provider_key_runtime = SimpleNamespace(required_coordinates=coordinates)
        elif bad_state == "empty":
            world.app.state.required_kms_coordinates = frozenset()
        elif bad_state == "partial":
            world.app.state.required_kms_coordinates = frozenset({next(iter(coordinates))})
        elif bad_state == "extra":
            world.app.state.required_kms_coordinates = coordinates | {(AUTH, "e" * 64, 100)}
        else:
            changed = replace(world.pins[0], ciphertext_sha256="c" * 64)
            altered = replace(runtime, _pins=(changed, world.pins[1]))
            assert altered.required_coordinates != coordinates
            world.app.state.provider_key_runtime = altered
        response = main._readiness_response()
        assert response.status_code == 503
        assert json.loads(response.body)["status"] == "not_ready"
        assert not world.loader.probe_calls and not world.loader.request_calls

    _during_lifespan(world.app, body)


@pytest.mark.parametrize("field,value", [
    ("auth_idempotency_encryption_key_version", 2),
    ("auth_idempotency_kms_key_id", "kms-unreviewed-key"),
    ("kms_encrypted_data_key_registry_path", "/run/unreviewed/keys.json"),
    ("openbao_provider_instance_id", "unreviewed-instance"),
])
def test_readiness_rejects_settings_drift_before_provider_call(startup_world, monkeypatch, field, value):
    world = startup_world

    def body():
        monkeypatch.setattr(world.settings, field, value)
        response = main._readiness_response()
        assert response.status_code == 503
        assert json.loads(response.body)["status"] == "not_ready"
        assert not world.loader.probe_calls and not world.loader.request_calls

    _during_lifespan(world.app, body)


def test_cached_healthy_gate_cannot_hide_later_configuration_change(startup_world, monkeypatch):
    world = startup_world

    def body():
        assert main._readiness_response().status_code == 200
        assert len(world.loader.probe_calls) == 2
        monkeypatch.setattr(world.settings, "auth_idempotency_encryption_key_version", 2)
        response = main._readiness_response()
        assert response.status_code == 503
        assert json.loads(response.body)["status"] == "not_ready"
        assert len(world.loader.probe_calls) == 2

    _during_lifespan(world.app, body)
