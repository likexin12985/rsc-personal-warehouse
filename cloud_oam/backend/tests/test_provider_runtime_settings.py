"""Provider selection is explicit; valid coordinates are not runtime evidence."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


OPENBAO = {
    "openbao_provider_instance_id": "rsc-pilot-bao-01",
    "openbao_encrypted_data_key_registry_path": "/run/rsc-keys/data-keys.json",
    "openbao_socket_path": "/run/rsc-bao/api.sock",
    "openbao_token_file": "/run/rsc-identity/bao/token",
    "openbao_api_uid": 21001,
    "openbao_bao_uid": 21002,
    "openbao_bao_gid": 21002,
    "openbao_shared_gid": 21004,
    "openbao_token_projector_uid": 21003,
}


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("OAM_") or name == "DEBUG":
            monkeypatch.delenv(name)


def configured_settings(**overrides) -> Settings:
    values = {"environment": "test", **OPENBAO}
    values.update(overrides)
    return Settings(_env_file=None, **values)


def production_settings(**overrides) -> Settings:
    values = {
        **OPENBAO,
        "environment": "production",
        "database_url": "postgresql+psycopg://star_oam_api:test@db/test",
        "jwt_secret": "synthetic-jwt-secret-" + "j" * 32,
        "identity_hash_secret": "synthetic-identity-secret-" + "i" * 32,
        "auth_idempotency_hmac_secret": "synthetic-replay-secret-" + "r" * 32,
        "auth_login_rate_limit_hmac_secret": "synthetic-limit-secret-" + "l" * 32,
        "auth_idempotency_encryption_provider": "openbao_transit_v1",
        "material_request_writes_enabled": True,
        "material_request_idempotency_hmac_secret": "synthetic-request-secret-" + "q" * 32,
        "material_request_contact_mobile_hmac_secret": "synthetic-contact-secret-" + "c" * 32,
        "material_request_contact_encryption_provider": "openbao_transit_v1",
        "sms_login_enabled": True,
        "sms_provider": "aliyun_pnvs",
        "sms_sign_name": "test-sign",
        "sms_template_code": "SMS_TEST",
        "sms_scheme_name": "test-scheme",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_defaults_leave_both_providers_disabled_and_openbao_unconfigured():
    settings = Settings(_env_file=None, environment="test")
    assert settings.auth_idempotency_encryption_provider == "disabled"
    assert settings.material_request_contact_encryption_provider == "disabled"
    assert not settings.openbao_configuration_ready()
    assert not settings.authentication_idempotency_kms_configuration_ready()
    assert not settings.material_request_contact_kms_configuration_ready()
    for field, value in OPENBAO.items():
        assert getattr(settings, field) == (0 if type(value) is int else "")


def test_complete_coordinates_do_not_select_a_provider_or_attest_a_runtime():
    settings = configured_settings()
    assert settings.openbao_configuration_ready()
    assert not settings.authentication_idempotency_kms_configuration_ready()
    assert not settings.material_request_contact_kms_configuration_ready()
    assert not any("attestation" in field for field in Settings.model_fields)


@pytest.mark.parametrize("environment", ["development", "test", "staging", "production"])
def test_explicit_openbao_provider_accepts_canonical_configured_scope(environment):
    settings = production_settings(environment=environment)
    assert settings.openbao_configuration_ready()
    assert settings.authentication_idempotency_kms_configuration_ready()
    assert settings.material_request_contact_kms_configuration_ready()
    assert settings.auth_idempotency_kms_key_id == ""
    assert settings.material_request_contact_kms_key_id == ""


@pytest.mark.parametrize("field", tuple(OPENBAO))
def test_each_missing_coordinate_fails_closed(field):
    value = 0 if type(OPENBAO[field]) is int else ""
    settings = production_settings(**{field: value})
    assert not settings.openbao_configuration_ready()
    assert not settings.authentication_idempotency_kms_configuration_ready()
    assert not settings.material_request_contact_kms_configuration_ready()
    with pytest.raises(ValueError, match="openbao_transit_v1"):
        settings.validate_api_startup()


@pytest.mark.parametrize("field,value", [
    ("openbao_provider_instance_id", "other/instance"),
    ("openbao_provider_instance_id", "UPPER-case-instance"),
    ("openbao_provider_instance_id", "a"),
    ("openbao_provider_instance_id", "replace-with-instance-id"),
    ("openbao_encrypted_data_key_registry_path", "registry.json"),
    ("openbao_encrypted_data_key_registry_path", "/run/../registry.json"),
    ("openbao_encrypted_data_key_registry_path", "/run//registry.json"),
    ("openbao_encrypted_data_key_registry_path", "/run/replace-with-registry.json"),
    ("openbao_socket_path", "/run/./api.sock"),
    ("openbao_token_file", "/run/token\x00"),
    ("openbao_api_uid", -1),
    ("openbao_bao_uid", -1),
    ("openbao_bao_gid", -1),
    ("openbao_shared_gid", -1),
    ("openbao_token_projector_uid", -1),
])
def test_noncanonical_scope_path_or_identity_is_not_ready(field, value):
    assert not configured_settings(**{field: value}).openbao_configuration_ready()


@pytest.mark.parametrize("first,second", [
    ("openbao_api_uid", "openbao_bao_uid"),
    ("openbao_api_uid", "openbao_token_projector_uid"),
    ("openbao_bao_uid", "openbao_token_projector_uid"),
    ("openbao_encrypted_data_key_registry_path", "openbao_socket_path"),
    ("openbao_encrypted_data_key_registry_path", "openbao_token_file"),
    ("openbao_socket_path", "openbao_token_file"),
])
def test_conflicting_runtime_identities_or_paths_are_not_ready(first, second):
    assert not configured_settings(**{first: OPENBAO[second]}).openbao_configuration_ready()


@pytest.mark.parametrize("field", [field for field, value in OPENBAO.items() if type(value) is int])
@pytest.mark.parametrize("value", [True, 21001.0, "21001.0", " 21001", "+21001"])
def test_runtime_identity_does_not_coerce_boolean_or_noncanonical_numeric_input(field, value):
    with pytest.raises(ValidationError, match="OpenBao runtime identities must be integers"):
        configured_settings(**{field: value})


def test_canonical_identity_environment_strings_are_supported(monkeypatch):
    for field, value in OPENBAO.items():
        monkeypatch.setenv("OAM_" + field.upper(), str(value))
    settings = Settings(_env_file=None, environment="test")
    assert settings.openbao_configuration_ready()
    assert settings.openbao_api_uid == OPENBAO["openbao_api_uid"]


def test_configuration_probe_does_not_read_files_or_connect(monkeypatch):
    settings = configured_settings()

    def forbidden(*args, **kwargs):
        raise AssertionError("configuration validation performed I/O")

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", forbidden)
        patch.setattr(Path, "read_text", forbidden)
        patch.setattr(os, "stat", forbidden)
        patch.setattr(socket, "socket", forbidden)
        assert settings.openbao_configuration_ready()


def test_configuration_probe_cold_import_does_not_initialize_global_database():
    # No OAM_* environment is available to the subprocess. A regression that
    # imports app.database would try to build unrelated global production
    # settings and fail, even though this caller supplied its own test scope.
    code = "\n".join((
        "import sys",
        "from app.config import Settings",
        "assert 'app.database' not in sys.modules",
        "empty = Settings(_env_file=None, environment='test')",
        "assert not empty.openbao_configuration_ready()",
        f"configured = Settings(_env_file=None, environment='test', **{OPENBAO!r})",
        "assert configured.openbao_configuration_ready()",
        "assert 'app.database' not in sys.modules",
        "assert 'app.formal_services' not in sys.modules",
        "assert 'app.production_openbao_composition' not in sys.modules",
        "print('cold configuration validation passed')",
    ))
    env = {name: value for name, value in os.environ.items() if not name.startswith("OAM_")}
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True,
        text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "cold configuration validation passed"


def test_openbao_production_configuration_passes_without_enabling_other_features():
    settings = production_settings()
    settings.validate_api_startup()
    assert not settings.stocktake_writes_enabled
    assert not settings.file_storage_enabled
    assert not settings.wechat_login_enabled
    assert not settings.password_login_enabled


@pytest.mark.parametrize("auth_provider,contact_provider", [
    ("openbao_transit_v1", "openbao_transit_v1"),
    ("openbao_transit_v1", "aliyun_kms"),
    ("aliyun_kms", "openbao_transit_v1"),
])
def test_legacy_key_id_equality_only_matters_when_both_active_providers_are_aliyun(
    auth_provider, contact_provider,
):
    settings = production_settings(
        auth_idempotency_encryption_provider=auth_provider,
        material_request_contact_encryption_provider=contact_provider,
        auth_idempotency_kms_key_id="legacy-kms-key",
        material_request_contact_kms_key_id="legacy-kms-key",
    )
    settings.validate_api_startup()


def test_two_active_aliyun_purposes_still_require_distinct_key_ids():
    settings = production_settings(
        auth_idempotency_encryption_provider="aliyun_kms",
        material_request_contact_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id="legacy-kms-key",
        material_request_contact_kms_key_id="legacy-kms-key",
    )
    with pytest.raises(ValueError, match="KMS key IDs must be distinct"):
        settings.validate_api_startup()


def test_aliyun_selection_does_not_require_unused_openbao_coordinates():
    settings = production_settings(
        **{field: 0 if type(value) is int else "" for field, value in OPENBAO.items()},
        auth_idempotency_encryption_provider="aliyun_kms",
        material_request_contact_encryption_provider="aliyun_kms",
        auth_idempotency_kms_key_id="legacy-auth-key",
        material_request_contact_kms_key_id="legacy-contact-key",
    )
    assert not settings.openbao_configuration_ready()
    settings.validate_api_startup()


@pytest.mark.parametrize("purpose", ["authentication", "contact"])
def test_openbao_selection_never_falls_back_to_present_aliyun_coordinates(purpose):
    settings = production_settings(
        openbao_socket_path="",
        auth_idempotency_kms_key_id="legacy-auth-key",
        material_request_contact_kms_key_id="legacy-contact-key",
    )
    method = (
        settings.authentication_idempotency_kms_configuration_ready
        if purpose == "authentication"
        else settings.material_request_contact_kms_configuration_ready
    )
    assert not method()
    with pytest.raises(ValueError, match="openbao_transit_v1"):
        settings.validate_api_startup()


@pytest.mark.parametrize("field", [
    "auth_idempotency_encryption_provider",
    "material_request_contact_encryption_provider",
])
def test_unknown_provider_alias_does_not_enable_a_provider(field):
    with pytest.raises(ValidationError):
        configured_settings(**{field: "openbao"})


def test_disabled_contact_provider_still_blocks_enabled_production_writes():
    with pytest.raises(ValueError, match="contact encryption"):
        production_settings(material_request_contact_encryption_provider="disabled").validate_api_startup()


def test_contact_read_only_mode_does_not_require_an_active_write_provider():
    settings = production_settings(
        material_request_writes_enabled=False,
        material_request_contact_encryption_provider="disabled",
    )
    settings.validate_api_startup()
