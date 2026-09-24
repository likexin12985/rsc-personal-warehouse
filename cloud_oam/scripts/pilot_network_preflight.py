#!/usr/bin/env python3
"""Inspect Docker network allocation before pilot writes; never create/delete a network."""
import argparse
import ipaddress
import json
from pathlib import Path
import subprocess


def check_networks(document, existing, project):
    """Return non-sensitive errors; permit reuse only of this project's exact allocation."""
    errors = []
    desired = {}
    for key, network in document["networks"].items():
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
    for network in existing:
        actual = {ipaddress.ip_network(item["Subnet"]) for item in network["IPAM"]["Config"] or []
                  if item.get("Subnet")}
        name = network["Name"]
        if name in desired:
            key, expected = desired[name]
            labels = network.get("Labels") or {}
            if (labels.get("com.docker.compose.project") != project
                    or labels.get("com.docker.compose.network") != key
                    or (expected and actual != expected)):
                errors.append("existing_pilot_network_mismatch")
            continue
        if any(a.version == b.version and a.overlaps(b)
               for _, subnets in desired.values() for a in subnets for b in actual):
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
