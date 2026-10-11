#!/usr/bin/env python3
"""Inspect Docker network allocation before pilot writes; never create/delete a network."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess

METADATA_NETWORK_KEY = "metadata_public"
METADATA_NETWORK_NAME = "rsc-openbao-metadata"
METADATA_LABEL_PREFIX = "rsc.pilot.metadata."
FULL_ID = __import__("re").compile(r"^[0-9a-f]{64}$")


def check_networks(document, existing, project):
    """Return non-sensitive errors; permit reuse only of this project's exact allocation."""
    errors = []
    desired = {}
    metadata_declared = document.get("networks", {}).get(METADATA_NETWORK_KEY)
    if metadata_declared is not None and (
            not isinstance(metadata_declared, dict)
            or metadata_declared.get("external") is not True
            or metadata_declared.get("name") != METADATA_NETWORK_NAME):
        errors.append("metadata_network_declaration_invalid")
    for key, network in document["networks"].items():
        if key == METADATA_NETWORK_KEY:
            continue
        if network.get("external") or network.get("name") != f"{project}_{key}":
            errors.append("network_not_isolated")
        desired[network["name"]] = (key, {
            ipaddress.ip_network(item["subnet"]) for item in network.get("ipam", {}).get("config", [])
            if item.get("subnet")
        })
    if not desired or not any(subnets for _, subnets in desired.values()):
        errors.append("explicit_backend_subnet_required")
    if not isinstance(existing, list):
        raise ValueError("invalid network inspection")
    metadata_networks = []
    seen_desired = set()
    for network in existing:
        actual = {ipaddress.ip_network(item["Subnet"]) for item in network.get("IPAM", {}).get("Config", []) or []
                  if item.get("Subnet")}
        name = network["Name"]
        if name == METADATA_NETWORK_NAME:
            metadata_networks.append(network)
            continue
        if name in desired:
            seen_desired.add(name)
            key, expected = desired[name]
            labels = network.get("Labels") or {}
            declaration = document["networks"][key]
            expected_driver = declaration.get("driver", "bridge")
            expected_internal = declaration.get("internal", False) is True
            expected_scope = declaration.get("scope", "local")
            if (labels.get("com.docker.compose.project") != project
                    or labels.get("com.docker.compose.network") != key
                    or (expected and actual != expected)
                    or network.get("Driver") != expected_driver
                    or network.get("Internal") is not expected_internal
                    or network.get("Scope") != expected_scope):
                errors.append("existing_pilot_network_mismatch")
            continue
        if any(a.version == b.version and a.overlaps(b)
               for _, subnets in desired.values() for a in subnets for b in actual):
            errors.append("docker_subnet_overlap")
    if set(desired) - seen_desired:
        errors.append("existing_pilot_network_missing")
    if metadata_declared is None and metadata_networks:
        # A same-name external network is not owned merely because its name
        # resembles the reviewed declaration.
        errors.append("metadata_network_unowned")
    if metadata_declared is not None:
        # The declaration carries the full ID in the web labels.  The network
        # must be the one exact bridge/internal object; name-only matches are
        # never accepted and its mutable Containers member is intentionally
        # excluded from any durable fingerprint.
        web = document.get("services", {}).get("web", {})
        labels = web.get("labels", {}) if isinstance(web, dict) else {}
        if isinstance(labels, list):
            labels = dict(item.split("=", 1) for item in labels if isinstance(item, str) and "=" in item)
        network_id = labels.get(METADATA_LABEL_PREFIX + "network-id") if isinstance(labels, dict) else None
        if not isinstance(network_id, str) or FULL_ID.fullmatch(network_id) is None:
            errors.append("metadata_network_id_invalid")
        if len(metadata_networks) != 1 or not any(item.get("Id") == network_id for item in metadata_networks):
            errors.append("metadata_network_identity_mismatch")
        else:
            row = metadata_networks[0]
            labels_actual = row.get("Labels") or {}
            if (row.get("Id") != network_id or row.get("Driver") != "bridge"
                    or row.get("Internal") is not True
                    or labels_actual.get("com.docker.compose.project") != "rsc-openbao"
                    or labels_actual.get("com.docker.compose.network") != "metadata"):
                errors.append("metadata_network_trust_labels_invalid")
            metadata_subnets = {ipaddress.ip_network(item["Subnet"]) for item in row.get("IPAM", {}).get("Config", []) or []
                                if item.get("Subnet")}
            if any(a.version == b.version and a.overlaps(b)
                   for _, subnets in desired.values() for a in subnets for b in metadata_subnets):
                errors.append("docker_subnet_overlap")
    return sorted(set(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--project-name", required=True)
    args = parser.parse_args()

    def read(command):
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        if result.stderr.strip():
            raise ValueError("unexpected diagnostic")
        return result.stdout

    try:
        document = json.loads(read([
            "docker", "compose", "--env-file", str(args.env_file), "-f", str(args.compose_file),
            "--project-name", args.project_name, "config", "--format", "json",
        ]))
        ids = read(["docker", "network", "ls", "--quiet", "--no-trunc"]).split()
        # An empty successful listing is valid; an inspection must return every requested ID.
        existing = json.loads(read(["docker", "network", "inspect", *ids])) if ids else []
        if len(existing) != len(ids) or {item["Id"] for item in existing} != set(ids):
            raise ValueError("incomplete network inspection")
        errors = check_networks(document, existing, args.project_name)
    except Exception:
        errors = ["network_inspection_failed"]
    print(json.dumps({"check": "pilot-network-preflight", "status": "fail" if errors else "pass",
                      "errors": errors, "scope": "Docker allocation only; host routes and deployment unverified"}))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
