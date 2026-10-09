"""Provider-aware deployment admission: configuration is not runtime proof."""
import base64
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from test_pilot_preflight import _document
from scripts.pilot_preflight import checks_for, OPENBAO_ENV_FIELDS
from app.openbao_transit_candidate import OpenBaoKeyCoordinate, associated_data_b64, context_b64


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("preflight must not contact providers or DB")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)


@pytest.fixture
def candidate(tmp_path):
    document, compose = _document(tmp_path)
    api = document["services"]["api"]
    gate = document["services"]["kms-pin-gate"]
    environment = api["environment"]
    api_uid = os.geteuid() or 10001
    identities = [api_uid + 100, api_uid + 101, api_uid + 102, api_uid + 103]
    registry_dir = tmp_path / "wrapped"
    registry_dir.mkdir(mode=0o700)
    registry = registry_dir / "registry.json"
    entries = []
    for index, purpose in enumerate(("authentication_idempotency", "material_request_contact")):
        coordinate = OpenBaoKeyCoordinate(purpose, "production", "pilot-reviewed-instance", 2)
        entries.append(dict(purpose=purpose, environment="production",
            provider_instance_id=coordinate.provider_instance_id, application_key_version=2,
            key_path=coordinate.key_path, transit_key_version=1,
            ciphertext="vault:v1:" + base64.b64encode(bytes([index + 1]) * 60).decode(),
            context_b64=context_b64(coordinate), associated_data_b64=associated_data_b64(coordinate)))
    registry.write_text(json.dumps(dict(schema="rsc.openbao.wrapped-data-key-registry.v1",
        provider="openbao_transit_v1", entries=entries)))
    registry.chmod(0o600)
    if os.geteuid() == 0:
        os.chown(registry, api_uid, -1)
        os.chown(registry_dir, api_uid, -1)
    environment.update(dict(zip(OPENBAO_ENV_FIELDS, (
        "pilot-reviewed-instance", "/run/rsc-bao/registry/registry.json", "/run/rsc-bao/socket/api.sock",
        "/run/rsc-bao/token/token", str(api_uid), *(str(value) for value in identities),
    ))))
    environment["OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER"] = "openbao_transit_v1"
    environment["OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION"] = "2"
    environment["OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER"] = "openbao_transit_v1"
    environment["OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION"] = "2"
    for key in ("OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_PATH", "OAM_KMS_ENDPOINT", "OAM_KMS_REGION",
                "OAM_AUTH_IDEMPOTENCY_KMS_KEY_ID", "OAM_MATERIAL_REQUEST_CONTACT_KMS_KEY_ID"):
        environment[key] = ""
    mounts = []
    for name, source in (("registry", registry_dir), ("socket", tmp_path / "socket"), ("token", tmp_path / "token")):
        source.mkdir(exist_ok=True)
        mounts.append(dict(type="bind", source=str(source), target=f"/run/rsc-bao/{name}",
                           read_only=True, bind={"create_host_path": False}))
    api["volumes"] = [api["volumes"][1], *copy.deepcopy(mounts)]
    gate["volumes"] = copy.deepcopy(mounts)
    gate["environment"].update(environment)
    for service in (api, gate):
        service["user"] = f"{api_uid}:{identities[1]}"
        service["group_add"] = [str(identities[2])]
        service.update(pid='container:' + 'b' * 64, cap_drop=['ALL'], cap_add=[], privileged=False,
                       security_opt=['no-new-privileges:true'])
        service['environment'].update(RSC_OPENBAO_CONTAINER_ID='b' * 64, RSC_OPENBAO_IMAGE_ID='sha256:' + 'c' * 64)
    return document, compose, registry


def _checks(candidate):
    document, compose, _ = candidate
    return {item["name"]: item["ok"] for item in checks_for(document, compose)}


def test_both_openbao_purposes_admit_without_aliyun_coordinates_or_token_reads(candidate, monkeypatch):
    import app.openbao_registry_candidate as registry_module
    import app.openbao_runtime_transport as transport_module
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not construct a runtime or read a token")
    monkeypatch.setattr(transport_module, "OpenBaoUnixDecryptTransport", forbidden)
    monkeypatch.setattr(registry_module, "load_openbao_registry_candidate", forbidden)
    checks = _checks(candidate)
    assert all(checks.values()), [key for key, value in checks.items() if not value]
    assert "kms_registry_structure_and_active_keys" not in checks
    assert not (candidate[2].parent.parent / "token" / "token").exists()


@pytest.mark.parametrize("key,value,failed", [
    ("OAM_OPENBAO_API_UID", "0", "openbao_coordinates"),
    ("OAM_OPENBAO_BAO_UID", "0", "openbao_coordinates"),
    ("OAM_OPENBAO_PROVIDER_INSTANCE_ID", "replace-with-instance", "openbao_coordinates"),
    ("OAM_OPENBAO_TOKEN_FILE", "/run/../token", "openbao_coordinates"),
    ("OAM_OPENBAO_SOCKET_PATH", "/run/bad socket", "openbao_coordinates"),
    ("OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION", "true", "openbao_coordinates"),
    ("OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION", "3", "openbao_registry_structure_and_active_keys"),
])
def test_openbao_invalid_or_unavailable_active_coordinates_fail(candidate, key, value, failed):
    candidate[0]["services"]["api"]["environment"][key] = value
    assert not _checks(candidate)[failed]


@pytest.mark.parametrize("change", ["rw", "auto-create", "file-token", "overlap", "missing-parent"])
def test_openbao_rejects_unsafe_or_missing_directory_mount(candidate, change):
    api = candidate[0]["services"]["api"]
    mount = api["volumes"][-1]
    if change == "rw": mount["read_only"] = False
    elif change == "auto-create": mount["bind"]["create_host_path"] = True
    elif change == "file-token": mount["target"] += "/token"
    elif change == "overlap": api["volumes"].append(dict(type="bind", target="/run", source="/run", read_only=True))
    else: mount["source"] += "/missing"
    assert not _checks(candidate)["openbao_readonly_mounts"]


@pytest.mark.parametrize("change", ["user", "group", "gate-source", "gate-config"])
def test_openbao_identity_and_gate_binding_do_not_accept_drift(candidate, change):
    gate = candidate[0]["services"]["kms-pin-gate"]
    if change == "user": gate["user"] = "0:0"
    elif change == "group": gate["group_add"] = []
    elif change == "gate-source": gate["volumes"][-1]["source"] = str(candidate[2].parent)
    else: gate["environment"]["OAM_OPENBAO_PROVIDER_INSTANCE_ID"] = "another-instance"
    key = "openbao_runtime_identity_declarations" if change in ("user", "group") else "openbao_gate_same_configuration"
    assert not _checks(candidate)[key]


@pytest.mark.parametrize("change", ["missing", "permissions", "extra-field", "wrong-context", "wrong-instance", "duplicate", "symlink"])
def test_openbao_registry_is_actually_read_and_invalid_wrapped_binding_blocks(candidate, change):
    path = candidate[2]
    if change == "missing": path.unlink()
    elif change == "permissions": path.chmod(0o640)
    elif change == "symlink":
        target = path.with_suffix(".original")
        path.rename(target)
        path.symlink_to(target)
    else:
        value = json.loads(path.read_text())
        if change == "extra-field": value["entries"][0]["plaintext"] = "must-never-be-accepted"
        elif change == "wrong-context": value["entries"][0]["context_b64"] = "wrong"
        elif change == "wrong-instance": value["entries"][0]["provider_instance_id"] = "wrong-instance"
        else: value["entries"].append(copy.deepcopy(value["entries"][0]))
        path.write_text(json.dumps(value))
    assert not _checks(candidate)["openbao_registry_structure_and_active_keys"]


def test_mixed_active_provider_keeps_aliyun_registry_checks(candidate, tmp_path):
    legacy, _ = _document(tmp_path)
    environment = candidate[0]["services"]["api"]["environment"]
    for key in ("OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER", "OAM_AUTH_IDEMPOTENCY_KMS_KEY_ID",
                "OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION", "OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_PATH",
                "OAM_KMS_ENDPOINT", "OAM_KMS_REGION"):
        environment[key] = legacy["services"]["api"]["environment"][key]
    for name in ("api", "kms-pin-gate"):
        candidate[0]["services"][name]["volumes"].append(copy.deepcopy(legacy["services"][name]["volumes"][0]))
    candidate[0]["services"]["kms-pin-gate"]["environment"].update(environment)
    assert all(_checks(candidate).values())
    Path(environment["OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_PATH"]).unlink()
    assert not _checks(candidate)["kms_registry_structure_and_active_keys"]


def test_preflight_help_is_cold_without_database_sdk_or_runtime_imports():
    script = Path(__file__).resolve().parents[2] / "scripts" / "pilot_preflight.py"
    program = """import runpy,sys
def guard(event,args):
 if event=='import' and (str(args[0]).startswith(('sqlalchemy','alibabacloud','app.'))):
  raise AssertionError('unexpected app/SDK import')
sys.addaudithook(guard)
sys.argv=[sys.argv[1],'--help']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", program, str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "--env-file" in result.stdout
