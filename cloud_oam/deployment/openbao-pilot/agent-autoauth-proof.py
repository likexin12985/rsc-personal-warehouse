"""New synthetic Agent proof only; never enables a service or reads credentials.

Reuses the already pinned LocalBao server lifecycle and official Agent binary.
Only ephemeral AppRole bootstrap and emitted runtime token files are written in
the owned temporary experiment directory; no raw logs or secrets are emitted.
This proves native Agent behavior, not production Linux UID/mount isolation.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from isolated_openbao_harness import KEYS, LocalBao


POLICY = "rsc-agent-proof-decrypt"
AUTH = "rsc-agent-proof"
ROLE = "synthetic"
TOKEN_TTL_SECONDS = 12


class ProofFailure(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise ProofFailure(code)


def accepted(server, method, path, body=None):
    status, data = server.root(method, path, body)
    require(status in (200, 204), "fixture_operation")
    return data


def read_token(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o440,
            "sink_mode")
    require(info.st_uid == os.geteuid() and info.st_gid == os.getegid()
            and info.st_nlink == 1, "sink_owner")
    value = path.read_bytes()
    require(re.fullmatch(rb"[A-Za-z0-9._-]{10,4096}", value) is not None,
            "sink_token_shape")
    return value.decode("ascii"), info.st_ino


def wait_token(process, path, previous=None, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raw = (process.stderr.read(8192) + process.stdout.read(8192)).lower()
            # Only a fixed word vocabulary crosses the diagnostic boundary.
            # Raw output (including any unexpected credentials) is discarded.
            vocabulary = ("config", "sink", "mode", "integer", "unknown", "invalid",
                          "method", "required", "permission", "denied", "mlock",
                          "sinks", "unmarshal", "parse", "address", "lookup", "auto_auth",
                          "error", "home", "cache", "token", "failed", "flag", "not defined")
            hints = [word for word in vocabulary if word.encode() in raw]
            raise ProofFailure("agent_exited_" + str(process.returncode) + ":" + ",".join(hints))
        if path.exists():
            value, inode = read_token(path)
            if previous is None or value != previous:
                return value, inode
        time.sleep(0.1)
    raise ProofFailure("sink_update_deadline")


def stop_agent(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_proof(binary):
    checks = []
    process = None
    holder = None
    directory = None
    result = None
    try:
        with ExitStack() as stack:
            server = stack.enter_context(LocalBao(binary))
            directory = server.directory
            # Every server object and key below is newly created in this fixture.
            policy = Path(__file__).with_name("api-decrypt-policy.json.example").read_text()
            accepted(server, "PUT", "/v1/sys/policies/acl/" + POLICY, {"policy": policy})
            accepted(server, "POST", "/v1/sys/mounts/transit", {"type": "transit"})
            wrapped = {}
            for key in KEYS:
                accepted(server, "POST", "/v1/transit/keys/" + key, {
                    "type": "aes256-gcm96", "derived": True,
                    "exportable": False, "allow_plaintext_backup": False,
                })
                context = base64.b64encode(("synthetic-agent:" + key).encode()).decode()
                aad = base64.b64encode(b"synthetic-agent-proof").decode()
                data = accepted(server, "POST", "/v1/transit/datakey/wrapped/" + key, {
                    "bits": 256, "context": context, "associated_data": aad,
                })
                wrapped[key] = {"ciphertext": data["data"]["ciphertext"],
                                "context": context, "associated_data": aad}
            accepted(server, "POST", "/v1/sys/auth/" + AUTH, {"type": "approle"})
            accepted(server, "POST", f"/v1/auth/{AUTH}/role/{ROLE}", {
                "bind_secret_id": True, "secret_id_num_uses": 20,
                "secret_id_ttl": "3m", "token_type": "batch",
                "token_ttl": str(TOKEN_TTL_SECONDS) + "s",
                "token_max_ttl": str(TOKEN_TTL_SECONDS) + "s",
                "token_num_uses": 0, "token_no_default_policy": True,
                "token_policies": [POLICY],
            })
            role = accepted(server, "GET", f"/v1/auth/{AUTH}/role/{ROLE}/role-id")["data"]["role_id"]
            secret = accepted(server, "POST", f"/v1/auth/{AUTH}/role/{ROLE}/secret-id", {})["data"]
            bootstrap = directory / "bootstrap"
            bootstrap.mkdir(mode=0o700)
            projected = directory / "projected"
            projected.mkdir(mode=0o750)
            # macOS inherits the parent's group, including /tmp's wheel group.
            # Explicitly bind this fixture directory to our own primary group;
            # production must provision its distinct shared group separately.
            os.chown(projected, -1, os.getegid())
            for name, value in (("role-id", role), ("secret-id", secret["secret_id"])):
                path = bootstrap / name
                path.write_text(value)
                path.chmod(0o600)
            config = json.loads(Path(__file__).with_name("agent-autoauth.json.example").read_text())
            require(set(config) == {"exit_after_auth", "vault", "auto_auth"}
                    and config["exit_after_auth"] is False, "template_surface")
            config["vault"]["address"] = "unix://" + str(server.socket_path)
            method = config["auto_auth"]["method"][0]
            method["mount_path"] = "auth/" + AUTH
            method["config"]["role_id_file_path"] = str(bootstrap / "role-id")
            method["config"]["secret_id_file_path"] = str(bootstrap / "secret-id")
            token_path = projected / "api.token"
            config["auto_auth"]["sinks"][0]["sink"]["config"]["path"] = str(token_path)
            config_path = directory / "agent.json"
            config_path.write_text(json.dumps(config))
            config_path.chmod(0o600)
            process = subprocess.Popen(
                [str(server.binary), "agent", "-log-level=error", "-config=" + str(config_path)],
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                     "TMPDIR": str(directory), "GOMAXPROCS": "2"},
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True, umask=0o027,
            )
            stack.callback(stop_agent, process)
            first, first_inode = wait_token(process, token_path)
            checks.extend(["official_agent_uds_login", "sink_mode_0440",
                           "sink_native_owner_group", "sink_regular_single_link"])
            require(not (bootstrap / "secret-id").exists(), "secret_id_removed")
            checks.append("secret_id_consumed_from_file")
            info = accepted(server, "POST", "/v1/auth/token/lookup", {"token": first})["data"]
            require(info["type"] == "batch" and info["renewable"] is False,
                    "batch_nonrenewable")
            require(info["policies"] == [POLICY] and not info.get("identity_policies"),
                    "exact_token_policy")
            require(0 < info["ttl"] <= TOKEN_TTL_SECONDS, "bounded_token_ttl")
            checks.extend(["batch_nonrenewable", "short_token_ttl", "exact_decrypt_policy_no_default"])
            for key in KEYS:
                status, data = server.client.request("POST", "/v1/transit/decrypt/" + key,
                                                     wrapped[key], token=first)
                require(status == 200 and len(base64.b64decode(data["data"]["plaintext"], validate=True)) == 32,
                        "projected_token_decrypt")
            checks.append("projected_token_two_exact_decrypt_paths")
            denied = [
                ("POST", "/v1/auth/token/renew-self", {}),
                ("GET", "/v1/transit/keys/" + KEYS[0], None),
                ("POST", "/v1/transit/datakey/wrapped/" + KEYS[0], {}),
                ("POST", "/v1/transit/keys/" + KEYS[0] + "/rotate", {}),
                ("POST", "/v1/auth/token/create", {}),
                ("GET", "/v1/sys/mounts", None),
                ("POST", "/v1/transit/decrypt/unrelated", {}),
            ]
            for method_name, path, body in denied:
                status, _ = server.client.request(method_name, path, body, token=first)
                require(status == 403, "token_unexpected_privilege")
            require(server.client.request("POST", "/v1/transit/decrypt/" + KEYS[0],
                                          wrapped[KEYS[0]], token=first)[0] == 200,
                    "negative_checks_token_expired")
            checks.append("seven_privilege_escalations_denied_including_renew_self")
            checks.append("same_token_positive_control_after_seven_denials")
            holder = token_path.open("rb")
            second, second_inode = wait_token(process, token_path, previous=first)
            require(second_inode != first_inode and holder.read().decode("ascii") == first,
                    "atomic_token_replacement")
            holder.close()
            holder = None
            checks.extend(["natural_batch_reauthentication", "atomic_inode_replacement",
                           "old_descriptor_retains_previous_token"])
            status, _ = server.client.request("POST", "/v1/transit/decrypt/" + KEYS[0],
                                              wrapped[KEYS[0]], token=second)
            require(status == 200, "new_token_usable")
            checks.append("replacement_token_usable")

            # The batch token remains individually non-revocable. Test precise
            # server-side policy removal as the separate emergency denial path.
            accepted(server, "DELETE", "/v1/sys/policies/acl/" + POLICY)
            require(server.root("GET", "/v1/sys/policies/acl/" + POLICY)[0] == 404,
                    "policy_delete_readback")
            for key in KEYS:
                require(server.client.request("POST", "/v1/transit/decrypt/" + key,
                                              wrapped[key], token=second)[0] == 403,
                        "removed_policy_still_authorized")
            second_info = accepted(server, "POST", "/v1/auth/token/lookup", {"token": second})["data"]
            require(second_info["ttl"] > 0, "policy_denial_token_expired")
            checks.append("emergency_exact_policy_removal_denies_existing_batch")
            accepted(server, "POST", f"/v1/auth/{AUTH}/role/{ROLE}/secret-id-accessor/destroy",
                     {"secret_id_accessor": secret["secret_id_accessor"]})
            denied_login, _ = server.client.request("POST", f"/v1/auth/{AUTH}/login",
                                                    {"role_id": role, "secret_id": secret["secret_id"]})
            require(denied_login in (400, 403), "revoked_secret_id_login")
            checks.append("revoked_secret_id_blocks_new_login")
            # Restore only this new fixture's policy before waiting for expiry,
            # so an expiry failure cannot be confused with policy denial.
            accepted(server, "PUT", "/v1/sys/policies/acl/" + POLICY, {"policy": policy})
            latest, _ = read_token(token_path)
            require(latest == second, "policy_control_token_changed")
            require(server.client.request("POST", "/v1/transit/decrypt/" + KEYS[0],
                                          wrapped[KEYS[0]], token=latest)[0] == 200,
                    "existing_token_not_revoked_with_secret_id")
            checks.append("secret_id_revocation_does_not_revoke_existing_batch")
            checks.append("same_token_positive_control_after_policy_restore")
            deadline = time.monotonic() + TOKEN_TTL_SECONDS + 2
            while time.monotonic() < deadline:
                require(process.poll() is None, "agent_stopped_after_revocation")
                current, _ = read_token(token_path)
                require(current == latest, "new_token_after_secret_id_revocation")
                time.sleep(0.2)
            require(server.client.request("POST", "/v1/transit/decrypt/" + KEYS[0],
                                          wrapped[KEYS[0]], token=latest)[0] == 403,
                    "expired_batch_accepted")
            checks.extend(["no_projection_after_secret_id_revocation", "batch_natural_expiry_denied"])
            result = {"status": "passed", "checks": checks, "checkCount": len(checks),
                      "openbaoVersion": "2.7.1", "binarySha256": server.binary_sha256,
                      "fixtureOnly": True, "productionReady": False,
                      "linuxDistinctUidVerified": False, "readOnlyMountVerified": False,
                      "tmpfsVerified": False, "individualBatchRevocationSupported": False,
                      "existingTransitSuiteRerun": False,
                      "templateSha256": hashlib.sha256(Path(__file__).with_name("agent-autoauth.json.example").read_bytes()).hexdigest(),
                      "decryptPolicySha256": hashlib.sha256(policy.encode()).hexdigest()}
            stop_agent(process)
            process = None
        require(directory is not None and not directory.exists(), "fixture_cleanup")
        result["cleanupCompleted"] = True
        return result
    finally:
        if holder is not None:
            holder.close()
        stop_agent(process)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    args = parser.parse_args()
    inputs = [Path(__file__), Path(__file__).with_name("agent-autoauth.json.example"),
              Path(__file__).with_name("api-decrypt-policy.json.example")]
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    try:
        result = run_proof(args.binary)
        require(all(hashlib.sha256(p.read_bytes()).hexdigest() == source_hashes[p.name]
                    for p in inputs), "proof_inputs_changed_during_run")
    except ProofFailure as error:
        print(json.dumps({"status": "failed", "safeCode": str(error), "productionReady": False}))
        return 1
    except Exception:
        print(json.dumps({"status": "failed", "safeCode": "unexpected_fixture_failure",
                          "productionReady": False}))
        return 1
    result["sourceSha256"] = source_hashes[Path(__file__).name]
    result["sourceInputsStable"] = True
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
