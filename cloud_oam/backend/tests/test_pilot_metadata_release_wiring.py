"""Focused metadata-public edge wiring checks; no Docker or network I/O."""
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest

from scripts import pilot_network_preflight as network
from scripts import pilot_preflight as preflight
from scripts import pilot_release as release


def metadata_document(tmp_path):
    content = b"site /xx /srv/warehouse /srv/public /api/"
    digest = hashlib.sha256(content).hexdigest()
    source = f"/etc/rsc-pilot-public/{digest}/Caddyfile"
    return {
        "services": {
            "web": {
                "labels": {
                    "rsc.pilot.metadata.network-id": "a" * 64,
                    "rsc.pilot.metadata.container-id": "b" * 64,
                    "rsc.pilot.metadata.image-id": "sha256:" + "c" * 64,
                    "rsc.pilot.metadata.started-at": "2026-10-09T01:00:00.123456789Z",
                    "rsc.pilot.metadata.gateway-sha256": "d" * 64,
                    "rsc.pilot.metadata.binary-sha256": "e" * 64,
                },
                "volumes": [{"type": "bind", "source": source, "target": "/etc/caddy/Caddyfile",
                             "read_only": True, "bind": {"create_host_path": False,
                                                           "propagation": "rprivate"}}],
                "networks": {"metadata_public": {}},
            },
            "api": {"networks": {"backend": {}}},
        },
        "networks": {
            "backend": {"name": "rsc-pilot-test_backend", "ipam": {"config": [{"subnet": "172.30.0.0/24"}]}},
            "metadata_public": {"name": "rsc-openbao-metadata", "external": True},
        },
    }


def test_metadata_declaration_is_closed_and_membership_is_phase_specific(tmp_path):
    document = metadata_document(tmp_path)
    declaration = preflight.metadata_peer_declaration(document)
    assert declaration["network_name"] == "rsc-openbao-metadata"
    assert release.Release.metadata_expected_members(document, "beforeStart") == {"b" * 64}
    assert release.Release.metadata_expected_members(document, "afterWebCreate", candidate_web_id="f" * 64) == {
        "b" * 64, "f" * 64
    }
    with pytest.raises(ValueError, match="metadata"):
        broken = copy.deepcopy(document)
        broken["services"]["web"]["labels"]["rsc.pilot.metadata.container-id"] = "metadata"
        preflight.metadata_peer_declaration(broken)


def test_metadata_runtime_network_inspect_uses_supported_full_id_command(monkeypatch, tmp_path):
    """The runtime gate must not pass an ``ls``-only flag to network inspect."""
    document = metadata_document(tmp_path)
    metadata_id = "b" * 64
    network_id = "a" * 64
    calls = []
    started_at = document["services"]["web"]["labels"]["rsc.pilot.metadata.started-at"]

    container = {
        "Id": metadata_id,
        "Image": "sha256:" + "c" * 64,
        "State": {"Running": True, "Status": "running", "Paused": False,
                   "Restarting": False, "OOMKilled": False, "Pid": 123,
                   "StartedAt": started_at},
        "Config": {"User": "23205:23205"},
        "HostConfig": {"GroupAdd": ["23110"], "CapDrop": ["ALL"], "CapAdd": [],
                        "Privileged": False, "SecurityOpt": ["no-new-privileges:true"],
                        "ReadonlyRootfs": True, "NetworkMode": "rsc-openbao-metadata",
                        "PidMode": "private", "IpcMode": "private", "UsernsMode": "",
                        "Devices": None, "PortBindings": {}},
        "Mounts": [
            {"Destination": "/runtime/caddy", "Source": "/opt/rsc-openbao/caddy/caddy",
             "Type": "bind", "RW": False, "Propagation": "rprivate"},
            {"Destination": "/etc/caddy/Caddyfile", "Source": "/etc/rsc-openbao/gateway.Caddyfile",
             "Type": "bind", "RW": False, "Propagation": "rprivate"},
            {"Destination": "/run/rsc-bao", "Source": "/run/rsc-openbao/socket",
             "Type": "bind", "RW": False, "Propagation": "rprivate"},
        ],
        "NetworkSettings": {"Networks": {"rsc-openbao-metadata": {
            "NetworkID": network_id, "Aliases": ["rsc-bao-metadata", "metadata"]
        }}},
    }
    network_row = [{"Id": network_id, "Name": "rsc-openbao-metadata", "Driver": "bridge",
                    "Internal": True, "Labels": {"com.docker.compose.project": "rsc-openbao",
                    "com.docker.compose.network": "metadata"}, "IPAM": {"Config": []},
                    "Options": {}}]

    def run(args, *, diagnostic=False):
        calls.append(args)
        if args[:3] == ["docker", "inspect", "--type"]:
            return json.dumps([container])
        if args[:3] == ["docker", "network", "inspect"]:
            assert "--no-trunc" not in args
            return json.dumps(network_row)
        raise AssertionError(args)

    fake = object.__new__(release.Release)
    fake.run = run
    monkeypatch.setattr(release.Release, "_metadata_file_metadata",
                        staticmethod(lambda path, expected, *, label: {"path": path, "sha256": expected}))
    result = release.Release.metadata_peer_metadata(fake, document)
    assert result["network"]["Id"] == network_id
    assert calls[-1] == ["docker", "network", "inspect", network_id]


def test_metadata_runtime_rejects_a_second_network_attachment(monkeypatch, tmp_path):
    document = metadata_document(tmp_path)
    network_id = "a" * 64
    container_networks = {
        "rsc-openbao-metadata": {
            "NetworkID": network_id,
            "Aliases": ["rsc-bao-metadata", "metadata"],
        },
        "foreign-network": {
            "NetworkID": "f" * 64,
            "Aliases": ["metadata-foreign"],
        },
    }
    container = {
        "Id": "b" * 64,
        "Image": "sha256:" + "c" * 64,
        "State": {"Running": True, "Status": "running", "Paused": False,
                   "Restarting": False, "OOMKilled": False, "Pid": 123,
                   "StartedAt": document["services"]["web"]["labels"]["rsc.pilot.metadata.started-at"]},
        "Config": {"User": "23205:23205"},
        "HostConfig": {"GroupAdd": ["23110"], "CapDrop": ["ALL"], "CapAdd": [],
                        "Privileged": False, "SecurityOpt": ["no-new-privileges:true"],
                        "ReadonlyRootfs": True, "NetworkMode": "rsc-openbao-metadata",
                        "PidMode": "private", "IpcMode": "private", "UsernsMode": "",
                        "Devices": None, "PortBindings": {}},
        "Mounts": [
            {"Destination": "/runtime/caddy", "Source": "/opt/rsc-openbao/caddy/caddy",
             "Type": "bind", "RW": False, "Propagation": "rprivate"},
            {"Destination": "/etc/caddy/Caddyfile", "Source": "/etc/rsc-openbao/gateway.Caddyfile",
             "Type": "bind", "RW": False, "Propagation": "rprivate"},
            {"Destination": "/run/rsc-bao", "Source": "/run/rsc-openbao/socket",
             "Type": "bind", "RW": False, "Propagation": "rprivate"},
        ],
        "NetworkSettings": {"Networks": container_networks},
    }

    def run(args, *, diagnostic=False):
        if args[:3] == ["docker", "inspect", "--type"]:
            return json.dumps([container])
        if args[:3] == ["docker", "network", "inspect"]:
            return json.dumps([{
                "Id": network_id, "Name": "rsc-openbao-metadata", "Driver": "bridge",
                "Internal": True, "Labels": {"com.docker.compose.project": "rsc-openbao",
                "com.docker.compose.network": "metadata"}, "IPAM": {"Config": []}, "Options": {},
            }])
        raise AssertionError(args)

    fake = object.__new__(release.Release)
    fake.run = run
    monkeypatch.setattr(release.Release, "_metadata_file_metadata",
                        staticmethod(lambda path, expected, *, label: {"path": path, "sha256": expected}))
    with pytest.raises(release.Refused, match="metadata_peer_network_isolation_invalid"):
        release.Release.metadata_peer_metadata(fake, document)


def test_network_preflight_requires_exact_trusted_metadata_network(tmp_path):
    document = metadata_document(tmp_path)
    existing = [
        {"Id": "a" * 64, "Name": "rsc-openbao-metadata", "Driver": "bridge", "Internal": True,
         "Labels": {"com.docker.compose.project": "rsc-openbao", "com.docker.compose.network": "metadata"},
         "IPAM": {"Config": [{"Subnet": "172.31.0.0/24"}]}},
        {"Id": "9" * 64, "Name": "rsc-pilot-test_backend", "Driver": "bridge", "Scope": "local", "Internal": False,
         "Labels": {"com.docker.compose.project": "rsc-pilot-test", "com.docker.compose.network": "backend"},
         "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]}},
    ]
    assert network.check_networks(document, existing, "rsc-pilot-test") == []
    existing[0]["Labels"]["com.docker.compose.project"] = "foreign"
    assert "metadata_network_trust_labels_invalid" in network.check_networks(document, existing, "rsc-pilot-test")


def test_network_preflight_rejects_missing_declared_pilot_network(tmp_path):
    document = metadata_document(tmp_path)
    existing = [{
        "Id": "a" * 64, "Name": "rsc-openbao-metadata", "Driver": "bridge", "Scope": "local",
        "Internal": True,
        "Labels": {"com.docker.compose.project": "rsc-openbao", "com.docker.compose.network": "metadata"},
        "IPAM": {"Config": []},
    }]
    assert "existing_pilot_network_missing" in network.check_networks(document, existing, "rsc-pilot-test")


@pytest.mark.parametrize("field,value", [("Driver", "macvlan"), ("Scope", "swarm"), ("Internal", True)])
def test_network_preflight_rejects_declared_pilot_network_property_drift(tmp_path, field, value):
    document = metadata_document(tmp_path)
    existing = [
        {"Id": "a" * 64, "Name": "rsc-openbao-metadata", "Driver": "bridge", "Scope": "local", "Internal": True,
         "Labels": {"com.docker.compose.project": "rsc-openbao", "com.docker.compose.network": "metadata"},
         "IPAM": {"Config": []}},
        {"Id": "9" * 64, "Name": "rsc-pilot-test_backend", "Driver": "bridge", "Scope": "local", "Internal": False,
         "Labels": {"com.docker.compose.project": "rsc-pilot-test", "com.docker.compose.network": "backend"},
         "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]}},
    ]
    existing[1][field] = value
    assert "existing_pilot_network_mismatch" in network.check_networks(document, existing, "rsc-pilot-test")


def test_network_preflight_rejects_unknown_overlapping_network(tmp_path):
    document = metadata_document(tmp_path)
    existing = [
        {"Id": "a" * 64, "Name": "rsc-openbao-metadata", "Driver": "bridge", "Internal": True,
         "Labels": {"com.docker.compose.project": "rsc-openbao", "com.docker.compose.network": "metadata"},
         "IPAM": {"Config": [{"Subnet": "172.31.0.0/24"}]}},
        {"Id": "9" * 64, "Name": "foreign_overlap", "Driver": "bridge", "Internal": False,
         "Labels": {}, "IPAM": {"Config": [{"Subnet": "172.30.0.128/25"}]}},
    ]
    errors = network.check_networks(document, existing, "rsc-pilot-test")
    assert "docker_subnet_overlap" in errors


@pytest.mark.parametrize("mutation", ["numeric", "duplicate"])
def test_metadata_label_input_never_coerces_or_overwrites(mutation, tmp_path):
    document = metadata_document(tmp_path)
    labels = document["services"]["web"]["labels"]
    if mutation == "numeric":
        labels["rsc.pilot.metadata.container-id"] = 7
    else:
        document["services"]["web"]["labels"] = [
            f"rsc.pilot.metadata.container-id={'b' * 64}",
            f"rsc.pilot.metadata.container-id={'c' * 64}",
            *[f"{key}={value}" for key, value in labels.items() if key != "rsc.pilot.metadata.container-id"],
        ]
    with pytest.raises(ValueError, match="metadata_labels"):
        preflight.metadata_peer_declaration(document)


@pytest.mark.parametrize(
    ("source_state", "expected_ok"),
    [
        ("matching", True),
        ("missing", False),
        ("symlink", False),
        ("wrong_content", False),
        ("stat_error", False),
        ("read_error", False),
    ],
)
def test_metadata_caddy_preflight_requires_readable_matching_regular_file(
    source_state, expected_ok, tmp_path, monkeypatch
):
    """Exercise the actual gate without touching /etc or a remote runtime.

    Missing snapshots previously skipped this check altogether. The gate must
    emit a failing result for every unavailable or untrusted source, while a
    real regular file with the declared content hash remains accepted.
    """
    document = metadata_document(tmp_path)
    document["services"]["web"]["build"] = {"args": {"RELEASE_PROFILE": "pilot"}}
    document["services"]["db"] = {}
    source = document["services"]["web"]["volumes"][0]["source"]
    content = b"site /xx /srv/warehouse /srv/public /api/"
    digest = hashlib.sha256(content).hexdigest()
    caddy_file = tmp_path / digest / "Caddyfile"
    caddy_file.parent.mkdir()
    if source_state == "symlink":
        target = tmp_path / "symlink-target"
        target.write_bytes(content)
        caddy_file.symlink_to(target)
    elif source_state != "missing":
        caddy_file.write_bytes(b"unexpected ingress configuration" if source_state == "wrong_content" else content)

    mapped_sources = []

    def local_path(value):
        # Fail immediately if checks_for starts accessing any other host path.
        assert value == source
        mapped_sources.append(value)
        return caddy_file

    monkeypatch.setattr(preflight, "Path", local_path)
    original_read = Path.read_bytes
    original_is_file = Path.is_file
    error_observed = []
    reads = []

    def read_bytes(path):
        if path == caddy_file:
            reads.append(path)
            if source_state == "read_error":
                error_observed.append("read")
                raise PermissionError("simulated unreadable public snapshot")
        return original_read(path)

    def is_file(path):
        if path == caddy_file and source_state == "stat_error":
            error_observed.append("stat")
            raise OSError("simulated public snapshot stat failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "is_file", is_file)
    checks = preflight.checks_for(document, tmp_path / "compose.json")
    assert {item["name"]: item["ok"] for item in checks}["metadata_peer_declaration"] is True
    caddy_checks = [item for item in checks if item["name"] == "metadata_caddy_content_address"]
    assert caddy_checks == [{"name": "metadata_caddy_content_address", "ok": expected_ok}]
    assert mapped_sources == [source]
    assert error_observed == {"read_error": ["read"], "stat_error": ["stat"]}.get(source_state, [])
    if source_state in {"missing", "symlink", "stat_error"}:
        assert reads == []
    else:
        assert reads == [caddy_file]
