from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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
        database_expected_runtime_role = "synthetic_api"
        database_expected_migration_role = "synthetic_migrator"

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
        "validate_production_database_security",
        lambda actual_engine, **roles: calls.append(("boundary", actual_engine, roles)),
    )
    monkeypatch.setattr(
        gate,
        "build_production_key_runtime",
        lambda db, actual: calls.append(("runtime", db, actual)) or SimpleNamespace(
            probe=lambda *_args: pytest.fail("structural proof must not decrypt"),
        ),
    )

    assert gate.main([]) == 0
    output = capsys.readouterr()
    assert output.out.strip() == "kms-pin-gate: structural ready; provider decrypt and release approval not verified"
    assert output.err == ""
    assert calls[0:2] == [
        ("boundary", gate.engine, {
            "expected_runtime_role": "synthetic_api",
            "expected_migration_role": "synthetic_migrator",
        }),
        "database-open",
    ]
    assert calls[2][0] == "runtime"
    assert calls[2][2] is settings
    assert calls[-1] == "database-close"


def test_verify_failure_is_fixed_and_desensitized(monkeypatch, capsys) -> None:
    class _Settings:
        environment = "production"
        database_expected_runtime_role = "synthetic_api"
        database_expected_migration_role = "synthetic_migrator"

    monkeypatch.setattr(gate, "get_settings", lambda: _Settings())
    monkeypatch.setattr(
        gate,
        "validate_production_database_security",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secret key id database url ciphertext")
        ),
    )
    monkeypatch.setattr(gate, "SessionLocal", lambda: pytest.fail("boundary failure must precede Session"))
    monkeypatch.setattr(gate, "build_production_key_runtime", lambda *_args: pytest.fail("boundary failure must precede pin scan"))

    assert gate.main([]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "kms-pin-gate: not ready"


@pytest.mark.parametrize("field,value", [
    ("auth_idempotency_encryption_provider", "openbao_transit_v1"),
    ("material_request_contact_encryption_provider", "openbao_transit_v1"),
    ("openbao_provider_instance_id", "synthetic-instance"),
    ("openbao_encrypted_data_key_registry_path", "/private/registry.json"),
    ("openbao_socket_path", "/run/bao/api.sock"),
    ("openbao_token_file", "/run/projected/api-token"),
    ("openbao_api_uid", 11001),
    ("openbao_bao_uid", 11002),
    ("openbao_bao_gid", 11002),
    ("openbao_shared_gid", 11004),
    ("openbao_token_projector_uid", 11003),
])
def test_legacy_plan_rejects_every_openbao_declaration_before_io(monkeypatch, capsys, field, value):
    settings = SimpleNamespace(environment="production", **{field: value})
    monkeypatch.setattr(gate, "get_settings", lambda: settings)
    def forbidden(*_args, **_kwargs):
        pytest.fail("unsupported plan must fail before registry, database or runtime access")
    for target in ("validate_production_adapter_installation", "get_configured_kms_loader",
                   "validate_production_database_security", "SessionLocal", "build_production_key_runtime"):
        monkeypatch.setattr(gate, target, forbidden)
    assert gate.main(["--plan"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "kms-pin-gate: not ready"


def test_verify_runtime_failure_closes_session_without_success_or_provider_call(monkeypatch, capsys):
    settings = SimpleNamespace(environment="production", database_expected_runtime_role="synthetic_api",
                               database_expected_migration_role="synthetic_migrator")
    calls = []
    class _Session:
        def __enter__(self):
            calls.append("open")
            return self
        def __exit__(self, *_args):
            calls.append("close")
    def unavailable(*_args):
        calls.append("runtime")
        raise RuntimeError("secret registry token database url")
    monkeypatch.setattr(gate, "get_settings", lambda: settings)
    monkeypatch.setattr(gate, "validate_production_database_security", lambda *_args, **_kwargs: calls.append("boundary"))
    monkeypatch.setattr(gate, "SessionLocal", _Session)
    monkeypatch.setattr(gate, "build_production_key_runtime", unavailable)
    assert gate.main([]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "kms-pin-gate: not ready"
    assert calls == ["boundary", "open", "runtime", "close"]


def test_verify_nonproduction_rejected_before_database_boundary(monkeypatch, capsys):
    monkeypatch.setattr(gate, "get_settings", lambda: SimpleNamespace(environment="test"))
    monkeypatch.setattr(gate, "validate_production_database_security", lambda *_args, **_kwargs: pytest.fail("no database in nonproduction gate"))
    assert gate.main([]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.strip() == "kms-pin-gate: not ready"
