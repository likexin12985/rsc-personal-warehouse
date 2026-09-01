from __future__ import annotations

import json
from types import SimpleNamespace

import app.kms_pin_gate as gate


def test_plan_is_deterministic_non_secret_and_never_opens_database(
    monkeypatch,
    capsys,
) -> None:
    database_calls = 0

    class _Loader:
        def pin_manifest(self):
            return {
                ("material_request_contact", "kms-contact", 2): SimpleNamespace(
                    kms_key_version_id="kms-version-contact",
                    ciphertext_sha256="b" * 64,
                ),
                ("authentication_idempotency", "kms-auth", 1): SimpleNamespace(
                    kms_key_version_id="kms-version-auth",
                    ciphertext_sha256="a" * 64,
                ),
            }

    class _Settings:
        environment = "production"

    def forbidden_database():
        nonlocal database_calls
        database_calls += 1
        raise AssertionError("plan must not open database")

    settings = _Settings()
    structural_calls: list[object] = []
    monkeypatch.setattr(gate, "get_settings", lambda: settings)
    monkeypatch.setattr(
        gate,
        "validate_production_adapter_installation",
        lambda actual: structural_calls.append(actual),
    )
    monkeypatch.setattr(gate, "get_configured_kms_loader", lambda _settings: _Loader())
    monkeypatch.setattr(gate, "SessionLocal", forbidden_database)

    assert gate.main(["--plan"]) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["schema"] == "rsc.kms.data-key-pin-plan.v1"
    assert len(document["manifest_sha256"]) == 64
    assert [entry["purpose"] for entry in document["entries"]] == [
        "authentication_idempotency",
        "material_request_contact",
    ]
    assert "ciphertext_blob" not in json.dumps(document)
    assert database_calls == 0
    assert structural_calls == [settings]


def test_plan_rejects_nonproduction_before_registry_or_database(
    monkeypatch,
    capsys,
) -> None:
    class _Settings:
        environment = "test"

    monkeypatch.setattr(gate, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        gate,
        "get_configured_kms_loader",
        lambda _settings: (_ for _ in ()).throw(
            AssertionError("registry must not be opened")
        ),
    )
    monkeypatch.setattr(
        gate,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not open")),
    )

    assert gate.main(["--plan"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "kms-pin-gate: not ready"


def test_verify_reuses_exact_read_only_runtime_proof(monkeypatch, capsys) -> None:
    calls: list[object] = []

    class _Settings:
        environment = "production"

    class _Session:
        def __enter__(self):
            calls.append("database-open")
            return self

        def __exit__(self, *_args):
            calls.append("database-close")

    settings = _Settings()
    monkeypatch.setattr(gate, "get_settings", lambda: settings)
    monkeypatch.setattr(gate, "SessionLocal", _Session)
    monkeypatch.setattr(
        gate,
        "validate_production_adapter_installation",
        lambda actual: calls.append(("adapter", actual)),
    )
    monkeypatch.setattr(
        gate,
        "validate_persisted_kms_key_references",
        lambda db, actual: calls.append(("pins", db, actual)),
    )

    assert gate.main([]) == 0
    output = capsys.readouterr()
    assert output.out.strip() == "kms-pin-gate: ready"
    assert output.err == ""
    assert calls[0:2] == [
        ("adapter", settings),
        "database-open",
    ]
    assert calls[-1] == "database-close"


def test_verify_failure_is_fixed_and_desensitized(monkeypatch, capsys) -> None:
    class _Settings:
        environment = "production"

    monkeypatch.setattr(gate, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        gate,
        "validate_production_adapter_installation",
        lambda _settings: (_ for _ in ()).throw(
            RuntimeError("secret key id database url ciphertext")
        ),
    )

    assert gate.main([]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "kms-pin-gate: not ready"
