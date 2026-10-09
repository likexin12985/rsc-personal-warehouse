"""Check the actual deployment mapping without running Compose or containers."""

from __future__ import annotations

import os
from pathlib import Path
import re

import pytest
import yaml

from app.config import Settings


ROOT = Path(__file__).resolve().parents[2]
COORDINATES = {
    "OAM_OPENBAO_PROVIDER_INSTANCE_ID": "pilot-bao-a1",
    "OAM_OPENBAO_ENCRYPTED_DATA_KEY_REGISTRY_PATH": "/run/keys/registry.json",
    "OAM_OPENBAO_SOCKET_PATH": "/run/bao/api.sock",
    "OAM_OPENBAO_TOKEN_FILE": "/run/identity/bao/token",
    "OAM_OPENBAO_API_UID": "41001",
    "OAM_OPENBAO_BAO_UID": "41002",
    "OAM_OPENBAO_BAO_GID": "41003",
    "OAM_OPENBAO_SHARED_GID": "41004",
    "OAM_OPENBAO_TOKEN_PROJECTOR_UID": "41005",
}


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("OAM_"):
            monkeypatch.delenv(name)


@pytest.fixture
def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def _mapped_coordinates(compose, supplied, service="api"):
    """Evaluate only the known, explicit ${NAME:-default} coordinate values."""
    environment = compose["services"][service]["environment"]
    mapped = {}
    for name in COORDINATES:
        match = re.fullmatch(r"\$\{([A-Z_]+):-([^}]*)\}", environment[name])
        assert match is not None, f"unsupported coordinate mapping: {name}"
        variable, default = match.groups()
        mapped[name.removeprefix("OAM_").lower()] = supplied.get(variable) or default
    return mapped


@pytest.mark.parametrize("name", tuple(COORDINATES))
@pytest.mark.parametrize("service", ["api", "kms-pin-gate"])
def test_consumers_forward_each_nonsecret_coordinate_from_its_own_variable(compose, name, service):
    defaults = "0" if name.endswith(("_UID", "_GID")) else ""
    expression = compose["services"][service]["environment"][name]
    assert expression == "${" + name + ":-" + defaults + "}"
    mapped = _mapped_coordinates(compose, COORDINATES, service)
    assert mapped[name.removeprefix("OAM_").lower()] == COORDINATES[name]


@pytest.mark.parametrize("supplied", [{}, dict.fromkeys(COORDINATES, "")])
def test_absent_or_empty_coordinates_remain_unconfigured_without_changing_providers(compose, supplied):
    settings = Settings(_env_file=None, environment="test", **_mapped_coordinates(compose, supplied))
    assert not settings.openbao_configuration_ready()
    assert settings.auth_idempotency_encryption_provider == "disabled"
    assert settings.material_request_contact_encryption_provider == "disabled"
    # The older explicit Aliyun deployment route and disabled contact default
    # remain unchanged; neither default implicitly selects OpenBao.
    environment = compose["services"]["api"]["environment"]
    assert environment["OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER"] == "${OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER:-aliyun_kms}"
    assert environment["OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER"] == "${OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER:-disabled}"


def test_reviewed_coordinates_reach_settings_but_do_not_enable_a_provider(compose):
    settings = Settings(_env_file=None, environment="test", **_mapped_coordinates(compose, COORDINATES))
    assert settings.openbao_configuration_ready()
    assert not settings.authentication_idempotency_kms_configuration_ready()
    assert not settings.material_request_contact_kms_configuration_ready()


@pytest.mark.parametrize("missing", tuple(COORDINATES))
def test_incomplete_deployment_cannot_pass_selected_openbao_configuration(compose, missing):
    supplied = {key: value for key, value in COORDINATES.items() if key != missing}
    settings = Settings(
        _env_file=None,
        environment="test",
        auth_idempotency_encryption_provider="openbao_transit_v1",
        material_request_contact_encryption_provider="openbao_transit_v1",
        **_mapped_coordinates(compose, supplied),
    )
    assert not settings.authentication_idempotency_kms_configuration_ready()
    assert not settings.material_request_contact_kms_configuration_ready()


@pytest.mark.parametrize("service", ["api", "kms-pin-gate"])
def test_default_compose_does_not_invent_openbao_mounts_or_identity_proof(compose, service):
    services = compose["services"]
    api = services[service]
    assert services["api"]["depends_on"]["kms-pin-gate"]["condition"] == "service_completed_successfully"
    assert all("openbao" not in name.lower() for name in services)
    assert "user" not in api and "group_add" not in api
    for volume in api["volumes"]:
        text = str(volume).lower()
        assert "openbao" not in text
        assert "/run/bao" not in text
        assert "/run/identity/bao" not in text
    forbidden_names = {"OAM_OPENBAO_TOKEN", "OAM_OPENBAO_PLAINTEXT_KEY", "OAM_OPENBAO_RUNTIME_VERIFIED"}
    assert not forbidden_names.intersection(api["environment"])


def test_openbao_coordinates_are_not_injected_into_unrelated_services(compose):
    for service_name, service in compose["services"].items():
        if service_name not in {"api", "kms-pin-gate"}:
            assert not set(COORDINATES).intersection(service.get("environment", {}))


def test_api_and_read_only_gate_share_exact_provider_configuration(compose):
    api = compose["services"]["api"]["environment"]
    gate = compose["services"]["kms-pin-gate"]["environment"]
    shared = compose["x-openbao-runtime-environment"]
    assert set(shared) == set(COORDINATES)
    for field in COORDINATES:
        assert api[field] == gate[field] == shared[field]
    for field in (
        "OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER",
        "OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION",
        "OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER",
        "OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION",
        "OAM_AUTH_IDEMPOTENCY_KMS_KEY_ID",
        "OAM_MATERIAL_REQUEST_CONTACT_KMS_KEY_ID",
        "OAM_MATERIAL_REQUEST_WRITES_ENABLED",
    ):
        assert api[field] == gate[field]


def test_env_example_and_compose_agree_on_disabled_openbao_coordinates(compose):
    example = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            example[name] = value
    values = {name: example[name] for name in COORDINATES}
    mapped = _mapped_coordinates(compose, values)
    settings = Settings(_env_file=None, environment="test", **mapped)
    assert not settings.openbao_configuration_ready()
    for name, value in values.items():
        assert value == ("0" if name.endswith(("_UID", "_GID")) else "")
