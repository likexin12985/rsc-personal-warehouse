#!/usr/bin/env python3
"""Read-only /xx pilot configuration checks.

Compose interpolation is resolved before checks. This command never starts a
container, contacts PostgreSQL/KMS/OSS/SMS, or prints secret values. A pass is
only a configuration preflight; it is not deployment or business acceptance.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit


PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme", "your-")
SECRET_NAMES = (
    "POSTGRES_PASSWORD", "OAM_DB_MIGRATOR_PASSWORD", "OAM_DB_API_PASSWORD",
    "OAM_DB_BACKUP_PASSWORD", "OAM_DB_PROJECTOR_PASSWORD", "OAM_JWT_SECRET",
    "OAM_IDENTITY_HASH_SECRET", "OAM_AUTH_IDEMPOTENCY_HMAC_SECRET",
    "OAM_AUTH_LOGIN_RATE_LIMIT_HMAC_SECRET", "OAM_MATERIAL_REQUEST_IDEMPOTENCY_HMAC_SECRET",
    "OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HMAC_SECRET", "OAM_FILE_IDEMPOTENCY_HMAC_SECRET",
)
REGISTRY_SCHEMA = "rsc.kms.encrypted-data-key-registry.v1"
AUTH_PURPOSE = "authentication_idempotency"
CONTACT_PURPOSE = "material_request_contact"
REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
KMS_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+-]{2,255}$")
KMS_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,127}$")
CIPHERTEXT_RE = re.compile(r"^[A-Za-z0-9+/=_-]{16,8192}$")
MAX_APPLICATION_KEY_VERSION = 2_147_483_647
DOMAIN_RE = re.compile(
    r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)
KMS_ENDPOINT_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{1,251}[a-z0-9])?\.aliyuncs\.com$"
)
PILOT_PROJECT_RE = re.compile(r"^rsc-pilot-[a-z0-9][a-z0-9_-]{0,47}$")
IMAGE_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
DATABASE_URL_RE = re.compile(
    r"postgresql\+psycopg://(?P<role>[^:/?#@\s]+):"
    r"(?P<password>[^@/?#\s]+)@db:5432/(?P<database>[^/?#\s]+)"
)


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def bool_value(value: object) -> bool | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return None


def configured(value: object, minimum: int = 1) -> bool:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        return False
    lowered = value.strip().lower()
    return not any(marker in lowered for marker in PLACEHOLDERS)


def sms_sts_configured(environment: dict[str, str]) -> bool:
    token = environment.get("OAM_SMS_SECURITY_TOKEN", "").strip()
    expiry = environment.get("OAM_SMS_SECURITY_TOKEN_EXPIRES_AT", "").strip()
    if not token and not expiry:
        return True
    if not configured(token, 32) or not expiry:
        return False
    try:
        instant = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return False
    return instant.tzinfo is not None and instant.astimezone(timezone.utc) > (
        datetime.now(timezone.utc) + timedelta(seconds=60)
    )


def service_env(service: dict[str, object]) -> dict[str, str]:
    raw = service.get("environment", {})
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    if isinstance(raw, list):
        return dict(item.split("=", 1) for item in raw if isinstance(item, str) and "=" in item)
    raise ValueError("invalid service environment")


def database_binding(service: object, database_name: str, role: str, password: str) -> bool:
    """Bind a service to this Compose database without exposing its credential."""
    if not isinstance(service, dict) or not configured(database_name):
        return False
    try:
        value = service_env(service).get("OAM_DATABASE_URL", "")
        match = DATABASE_URL_RE.fullmatch(value)
        if match is None:
            return False
        # SQLAlchemy decodes user-info passwords but not the database path.
        # Require reserved password characters to be percent encoded so its
        # parser cannot select a different host than this standard-library gate.
        decoded_password = unquote(match["password"], errors="strict")
        return (match["role"] == role and match["database"] == database_name
                and configured(decoded_password, 32) and decoded_password == password)
    except (TypeError, ValueError):
        return False


def registry_entries(path: Path) -> set[tuple[str, str, int]]:
    metadata = path.stat()
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()
            or metadata.st_size <= 0 or metadata.st_size > 1024 * 1024
            or metadata.st_mode & 0o022):
        raise ValueError("invalid KMS registry path")
    document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    if (not isinstance(document, dict)
            or set(document) != {"schema", "entries"}
            or document.get("schema") != REGISTRY_SCHEMA):
        raise ValueError("invalid KMS registry schema")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > 64:
        raise ValueError("invalid KMS registry entries")
    coordinates: set[tuple[str, str, int]] = set()
    purpose_versions: set[tuple[str, int]] = set()
    ciphertexts: set[str] = set()
    for entry in raw_entries:
        if not isinstance(entry, dict) or set(entry) != {
            "purpose", "kms_key_id", "application_key_version", "kms_key_version_id",
            "ciphertext_blob", "encryption_context",
        }:
            raise ValueError("invalid KMS registry row")
        purpose = entry.get("purpose")
        key_id = entry.get("kms_key_id")
        version = entry.get("application_key_version")
        ciphertext = entry.get("ciphertext_blob")
        context = entry.get("encryption_context")
        if (purpose not in {AUTH_PURPOSE, CONTACT_PURPOSE}
                or not isinstance(key_id, str) or KMS_KEY_RE.fullmatch(key_id) is None
                or any(marker in key_id.lower() for marker in PLACEHOLDERS)):
            raise ValueError("invalid KMS registry coordinate")
        key_version_id = entry.get("kms_key_version_id")
        if (not isinstance(version, int) or isinstance(version, bool) or version < 1
                or version > MAX_APPLICATION_KEY_VERSION
                or not isinstance(key_version_id, str)
                or KMS_VERSION_RE.fullmatch(key_version_id) is None
                or not isinstance(ciphertext, str)):
            raise ValueError("invalid KMS registry version")
        if CIPHERTEXT_RE.fullmatch(ciphertext) is None or ciphertext in ciphertexts:
            raise ValueError("invalid or reused KMS ciphertext")
        expected = {
            "application": "cloud_oam", "purpose": purpose,
            "kms_key_id": key_id, "application_key_version": str(version),
        }
        if context != expected:
            raise ValueError("KMS encryption context mismatch")
        coordinate = (purpose, key_id, version)
        purpose_version = (purpose, version)
        if coordinate in coordinates or purpose_version in purpose_versions:
            raise ValueError("duplicate KMS coordinate")
        if any(existing_key == key_id and existing_purpose != purpose
               for existing_purpose, existing_key, _ in coordinates):
            raise ValueError("KMS key crosses purpose boundary")
        ciphertexts.add(ciphertext)
        coordinates.add(coordinate)
        purpose_versions.add(purpose_version)
    return coordinates


def checks_for(document: dict[str, object], compose_file: Path, *,
               project_name: str | None = None, smoke_base_url: str | None = None,
               smoke_private_path: str = "/xx/", image_tag: str | None = None) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool) -> None:
        checks.append({"name": name, "ok": bool(ok)})

    services = document.get("services")
    if not isinstance(services, dict):
        return [{"name": "resolved_compose_shape", "ok": False}]
    try:
        api = services["api"]
        web = services["web"]
        db = services["db"]
        if not all(isinstance(item, dict) for item in (api, web, db)):
            raise ValueError
        api_env, web_env, db_env = service_env(api), service_env(web), service_env(db)
        build = web.get("build")
        if not isinstance(build, dict):
            raise ValueError
        build_args = build.get("args")
        if not isinstance(build_args, dict):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return [{"name": "resolved_compose_shape", "ok": False}]

    check("pilot_build_profile", build_args.get("RELEASE_PROFILE") == "pilot")
    if project_name is not None or image_tag is not None:
        tag_ok = isinstance(image_tag, str) and IMAGE_TAG_RE.fullmatch(image_tag) is not None
        check("pilot_image_tag", tag_ok)
        expected_images = {
            "db": f"rsc-pilot-db:{image_tag}",
            "api": f"rsc-pilot-api:{image_tag}",
            "web": f"rsc-pilot-web:{image_tag}",
            "migrate": f"rsc-pilot-api:{image_tag}",
            "kms-pin-gate": f"rsc-pilot-api:{image_tag}",
        }
        check("pilot_service_images", tag_ok and all(
            isinstance(services.get(name), dict) and services[name].get("image") == expected
            for name, expected in expected_images.items()
        ))
    if project_name is not None:
        check("isolated_pilot_project", PILOT_PROJECT_RE.fullmatch(project_name) is not None
              and document.get("name") == project_name)
        resources_ok = True
        for kind in ("volumes", "networks"):
            resources = document.get(kind)
            if not isinstance(resources, dict) or not resources:
                resources_ok = False
                continue
            resources_ok = resources_ok and all(
                isinstance(value, dict) and not value.get("external")
                and value.get("name") == f"{project_name}_{key}"
                for key, value in resources.items()
            )
        resources_ok = resources_ok and all(
            isinstance(service, dict) and not service.get("container_name")
            for service in services.values()
        )
        database_mounts = [item for item in db.get("volumes", [])
                           if isinstance(item, dict) and item.get("target") == "/var/lib/postgresql/data"]
        check("pilot_resource_isolation", resources_ok and len(database_mounts) == 1
              and database_mounts[0].get("type") == "volume"
              and database_mounts[0].get("source") == "postgres_data"
              and "postgres_data" in document.get("volumes", {}))
    domain = web_env.get("APP_DOMAIN", "")
    origin = api_env.get("OAM_APP_ORIGIN", "")
    try:
        parsed_origin = urlsplit(origin)
    except ValueError:
        parsed_origin = urlsplit("")
    check("canonical_https_origin", DOMAIN_RE.fullmatch(domain) is not None
          and ".example.com" not in domain.lower()
          and origin == f"https://{domain}"
          and parsed_origin.scheme == "https" and parsed_origin.netloc == domain
          and parsed_origin.path in {"", "/"} and not parsed_origin.query
          and not parsed_origin.fragment)
    if smoke_base_url is not None:
        check("smoke_target_matches_origin", smoke_base_url in {origin, origin + "/"}
              and smoke_private_path == "/xx/")
    check("production_environment", api_env.get("OAM_ENVIRONMENT") == "production")
    check("alembic_schema", api_env.get("OAM_DATABASE_SCHEMA_MODE") == "alembic")
    check("password_login_disabled", bool_value(api_env.get("OAM_PASSWORD_LOGIN_ENABLED")) is False)
    check("secure_cookie", bool_value(api_env.get("OAM_COOKIE_SECURE")) is True)
    check("legacy_writes_disabled", bool_value(api_env.get("OAM_LEGACY_PROTOTYPE_WRITES_ENABLED")) is False)
    check("bootstrap_credentials_empty", not any(api_env.get(key, "").strip() for key in (
        "OAM_ADMIN_MOBILE", "OAM_ADMIN_NAME", "OAM_ADMIN_INITIAL_PASSWORD")))
    database_name = db_env.get("POSTGRES_DB", "")
    runtime_password = db_env.get("OAM_DB_API_PASSWORD", "")
    check("api_database_role", database_binding(api, database_name, "star_oam_api", runtime_password))
    check("migration_database_binding", database_binding(
        services.get("migrate"), database_name, "star_oam_migrator", db_env.get("OAM_DB_MIGRATOR_PASSWORD", "")))
    check("kms_pin_database_binding", database_binding(
        services.get("kms-pin-gate"), database_name, "star_oam_api", runtime_password))

    check("h5_sms_login", bool_value(api_env.get("OAM_SMS_LOGIN_ENABLED")) is True
          and api_env.get("OAM_SMS_PROVIDER") == "aliyun_pnvs"
          and all(configured(api_env.get(key)) for key in (
              "OAM_SMS_ACCESS_KEY_ID", "OAM_SMS_ACCESS_KEY_SECRET", "OAM_SMS_SIGN_NAME",
              "OAM_SMS_TEMPLATE_CODE", "OAM_SMS_SCHEME_NAME"))
          and sms_sts_configured(api_env))

    all_secrets = [db_env.get(key, "") for key in SECRET_NAMES[:5]] + [api_env.get(key, "") for key in SECRET_NAMES[5:]]
    check("required_secrets", all(configured(value, 32) for value in all_secrets))
    check("secret_separation", all(configured(value, 32) for value in all_secrets)
          and len(all_secrets) == len(set(all_secrets)))

    auth_key = api_env.get("OAM_AUTH_IDEMPOTENCY_KMS_KEY_ID", "")
    contact_key = api_env.get("OAM_MATERIAL_REQUEST_CONTACT_KMS_KEY_ID", "")
    endpoint = api_env.get("OAM_KMS_ENDPOINT", "").lower()
    check("authentication_kms_coordinates", api_env.get("OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER") == "aliyun_kms"
          and KMS_KEY_RE.fullmatch(auth_key) is not None
          and not any(marker in auth_key.lower() for marker in PLACEHOLDERS)
          and KMS_ENDPOINT_RE.fullmatch(endpoint) is not None
          and ".." not in endpoint
          and REGION_RE.fullmatch(api_env.get("OAM_KMS_REGION", "")) is not None)
    check("material_request_writes", bool_value(api_env.get("OAM_MATERIAL_REQUEST_WRITES_ENABLED")) is True
          and configured(api_env.get("OAM_MATERIAL_REQUEST_IDEMPOTENCY_HMAC_SECRET"), 32)
          and configured(api_env.get("OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HMAC_SECRET"), 32)
          and api_env.get("OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER") == "aliyun_kms"
          and KMS_KEY_RE.fullmatch(contact_key) is not None
          and not any(marker in contact_key.lower() for marker in PLACEHOLDERS)
          and contact_key != auth_key)
    check("private_oss", bool_value(api_env.get("OAM_FILE_STORAGE_ENABLED")) is True
          and api_env.get("OAM_FILE_STORAGE_PROVIDER") == "aliyun_oss_v2"
          and REGION_RE.fullmatch(api_env.get("OAM_FILE_STORAGE_REGION", "")) is not None
          and BUCKET_RE.fullmatch(api_env.get("OAM_FILE_STORAGE_BUCKET", "")) is not None
          and configured(api_env.get("OAM_FILE_IDEMPOTENCY_HMAC_SECRET"), 32))

    proxy = api_env.get("OAM_TRUSTED_PROXY_IPS", "")
    try:
        proxy_values = [ipaddress.ip_address(value.strip()) for value in proxy.split(",") if value.strip()]
        proxy_ok = bool(proxy_values) and all(not value.is_unspecified and not value.is_multicast for value in proxy_values)
        proxy_ok = proxy_ok and proxy.strip() not in {"127.0.0.1", "127.0.0.1,::1"}
    except ValueError:
        proxy_ok = False
    web_networks = web.get("networks", {})
    web_backend = web_networks.get("backend") if isinstance(web_networks, dict) else None
    web_peer = web_backend.get("ipv4_address") if isinstance(web_backend, dict) else None
    try:
        web_peer_ip = ipaddress.ip_address(str(web_peer))
        web_peer_ok = web_peer_ip.version == 4 and web_peer_ip in ipaddress.ip_network(
            str(document.get("networks", {}).get("backend", {}).get("ipam", {}).get("config", [{}])[0].get("subnet")),
            strict=False,
        )
    except (ValueError, TypeError, AttributeError, IndexError):
        web_peer_ok = False
    check("trusted_proxy_peer", proxy_ok and web_peer_ok and web_peer in {str(value) for value in proxy_values})

    target = api_env.get("OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_PATH", "")
    mounts = [item for item in api.get("volumes", []) if isinstance(item, dict) and item.get("target") == target]
    mount_ok = len(mounts) == 1 and mounts[0].get("type") == "bind" and mounts[0].get("read_only") is True
    check("kms_registry_readonly_mount", mount_ok)
    registry_ok = False
    registry_source = None
    if mount_ok:
        source = Path(str(mounts[0].get("source", "")))
        if not source.is_absolute():
            source = compose_file.parent / source
        registry_source = source
        try:
            coordinates = registry_entries(source)
            required = {(AUTH_PURPOSE, auth_key, int(api_env.get("OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION", "1")))}
            if bool_value(api_env.get("OAM_MATERIAL_REQUEST_WRITES_ENABLED")) is True:
                required.add((CONTACT_PURPOSE, contact_key, int(api_env.get("OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION", "1"))))
            registry_ok = required.issubset(coordinates)
        except Exception:
            registry_ok = False
    check("kms_registry_structure_and_active_keys", registry_ok)

    pin_gate = services.get("kms-pin-gate")
    gate_ok = False
    if isinstance(pin_gate, dict):
        gate_mounts = [item for item in pin_gate.get("volumes", [])
                       if isinstance(item, dict) and item.get("target") == target]
        if len(gate_mounts) == 1 and registry_source is not None:
            gate_source = Path(str(gate_mounts[0].get("source", "")))
            if not gate_source.is_absolute():
                gate_source = compose_file.parent / gate_source
            gate_ok = gate_mounts[0].get("type") == "bind" and gate_mounts[0].get("read_only") is True and gate_source == registry_source
    check("kms_pin_gate_same_readonly_registry", gate_ok)
    return checks


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    parser.add_argument("--compose-file", type=Path, default=root / "docker-compose.yml")
    parser.add_argument("--project-name", help="Explicit isolated rsc-pilot-* Compose project")
    parser.add_argument("--image-tag", help="Reviewed pilot image tag; required with --project-name")
    parser.add_argument("--smoke-base-url", help="Bind deployment smoke to the resolved HTTPS origin")
    parser.add_argument("--smoke-private-path", default="/xx/")
    args = parser.parse_args(argv)
    checks = [{"name": name, "ok": path.is_file()} for name, path in (("env_file", args.env_file), ("compose_file", args.compose_file))]
    docker = shutil.which("docker")
    checks.append({"name": "docker_cli", "ok": docker is not None})
    if all(item["ok"] for item in checks):
        try:
            command = [docker, "compose", "--env-file", str(args.env_file.resolve()), "-f", str(args.compose_file.resolve())]
            if args.project_name is not None:
                command.extend(["--project-name", args.project_name])
            result = subprocess.run(command + ["config", "--format", "json"], capture_output=True, text=True, timeout=15, check=False)
            if result.returncode != 0 or result.stderr.strip():
                raise ValueError
            document = json.loads(result.stdout)
            checks.append({"name": "compose_resolution", "ok": True})
            checks.extend(checks_for(document, args.compose_file.resolve(),
                                     project_name=args.project_name, smoke_base_url=args.smoke_base_url,
                                     smoke_private_path=args.smoke_private_path, image_tag=args.image_tag))
        except Exception:
            checks.append({"name": "compose_resolution", "ok": False})
    errors = [item["name"] for item in checks if not item["ok"]]
    output = {"check": "pilot-preflight", "status": "fail" if errors else "pass", "deploymentReady": False, "errors": errors, "checks": checks, "unverified": ["target engine/images", "HTTPS certificate and proxy peer", "SMS send/verify", "KMS decrypt and persisted pins", "OSS policy/access", "database migration/roles", "identity/opening data", "backup restore and business UAT"], "scope": "configuration only; no container, network or business writes"}
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
