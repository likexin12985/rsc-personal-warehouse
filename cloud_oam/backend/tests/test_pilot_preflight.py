"""Pure configuration contract checks for the /xx pilot preflight."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import sys
from urllib.parse import quote
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.pilot_preflight import checks_for, sms_sts_configured  # noqa: E402


def test_pilot_preflight_rejects_partial_or_expiring_pnvs_sts():
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    token = "synthetic-pnvs-token-" + "x" * 32
    assert sms_sts_configured({"OAM_SMS_SECURITY_TOKEN":token,
                               "OAM_SMS_SECURITY_TOKEN_EXPIRES_AT":future})
    for values in (
        {"OAM_SMS_SECURITY_TOKEN":token},
        {"OAM_SMS_SECURITY_TOKEN_EXPIRES_AT":future},
        {"OAM_SMS_SECURITY_TOKEN":token, "OAM_SMS_SECURITY_TOKEN_EXPIRES_AT":"invalid"},
        {"OAM_SMS_SECURITY_TOKEN":token,
         "OAM_SMS_SECURITY_TOKEN_EXPIRES_AT":datetime.now(timezone.utc).isoformat()},
    ):
        assert not sms_sts_configured(values)


def _document(tmp_path: Path) -> tuple[dict, Path]:
    registry = tmp_path / "rsc-kms-data-keys.json"
    registry.write_text(json.dumps({
        "schema": "rsc.kms.encrypted-data-key-registry.v1",
        "entries": [
            {
                "purpose": "authentication_idempotency",
                "kms_key_id": "kms-auth-key",
                "application_key_version": 1,
                "kms_key_version_id": "auth-version-1",
                "ciphertext_blob": "YXV0aC1jaXBoZXJ0ZXh0",
                "encryption_context": {
                    "application": "cloud_oam",
                    "purpose": "authentication_idempotency",
                    "kms_key_id": "kms-auth-key",
                    "application_key_version": "1",
                },
            },
            {
                "purpose": "material_request_contact",
                "kms_key_id": "kms-contact-key",
                "application_key_version": 1,
                "kms_key_version_id": "contact-version-1",
                "ciphertext_blob": "Y29udGFjdC1jaXBoZXJ0ZXh0",
                "encryption_context": {
                    "application": "cloud_oam",
                    "purpose": "material_request_contact",
                    "kms_key_id": "kms-contact-key",
                    "application_key_version": "1",
                },
            },
        ],
    }), encoding="utf-8")
    registry.chmod(0o600)
    secrets = {
        "POSTGRES_PASSWORD": "postgres-" + "a" * 32,
        "OAM_DB_MIGRATOR_PASSWORD": "migrator-" + "b" * 32,
        "OAM_DB_API_PASSWORD": "api-" + "c" * 32,
        "OAM_DB_BACKUP_PASSWORD": "backup-" + "d" * 32,
        "OAM_DB_PROJECTOR_PASSWORD": "projector-" + "e" * 32,
        "OAM_JWT_SECRET": "jwt-" + "f" * 32,
        "OAM_IDENTITY_HASH_SECRET": "identity-" + "g" * 32,
        "OAM_AUTH_IDEMPOTENCY_HMAC_SECRET": "auth-" + "h" * 32,
        "OAM_AUTH_LOGIN_RATE_LIMIT_HMAC_SECRET": "rate-" + "i" * 32,
        "OAM_MATERIAL_REQUEST_IDEMPOTENCY_HMAC_SECRET": "request-" + "j" * 32,
        "OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HMAC_SECRET": "mobile-" + "k" * 32,
        "OAM_FILE_IDEMPOTENCY_HMAC_SECRET": "file-" + "l" * 32,
    }
    api = {
        "environment": {
            **{key: secrets[key] for key in list(secrets)[5:]},
            "OAM_APP_ORIGIN": "https://rscwz.cn",
            "OAM_ENVIRONMENT": "production",
            "OAM_DATABASE_SCHEMA_MODE": "alembic",
            "OAM_PASSWORD_LOGIN_ENABLED": "false",
            "OAM_COOKIE_SECURE": "true",
            "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "false",
            "OAM_DATABASE_URL": "postgresql+psycopg://star_oam_api:api-" + "c" * 32 + "@db:5432/star_oam",
            "OAM_SMS_LOGIN_ENABLED": "true",
            "OAM_SMS_PROVIDER": "aliyun_pnvs",
            "OAM_SMS_ACCESS_KEY_ID": "sms-access-id",
            "OAM_SMS_ACCESS_KEY_SECRET": "sms-secret-value",
            "OAM_SMS_SIGN_NAME": "RSC",
            "OAM_SMS_TEMPLATE_CODE": "SMS_123456",
            "OAM_SMS_SCHEME_NAME": "RSC登录",
            "OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER": "aliyun_kms",
            "OAM_AUTH_IDEMPOTENCY_KMS_KEY_ID": "kms-auth-key",
            "OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION": "1",
            "OAM_MATERIAL_REQUEST_WRITES_ENABLED": "true",
            "OAM_MATERIAL_REQUEST_IDEMPOTENCY_HMAC_SECRET": secrets["OAM_MATERIAL_REQUEST_IDEMPOTENCY_HMAC_SECRET"],
            "OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HMAC_SECRET": secrets["OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HMAC_SECRET"],
            "OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER": "aliyun_kms",
            "OAM_MATERIAL_REQUEST_CONTACT_KMS_KEY_ID": "kms-contact-key",
            "OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION": "1",
            "OAM_FILE_STORAGE_ENABLED": "true",
            "OAM_FILE_STORAGE_PROVIDER": "aliyun_oss_v2",
            "OAM_FILE_STORAGE_REGION": "cn-shanghai",
            "OAM_FILE_STORAGE_BUCKET": "rsc-private-pilot",
            "OAM_TRUSTED_PROXY_IPS": "172.18.0.4",
            "OAM_KMS_ENDPOINT": "kms.cn-shanghai.aliyuncs.com",
            "OAM_KMS_REGION": "cn-shanghai",
            "OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_PATH": str(registry),
        },
        "volumes": [{"type": "bind", "source": str(registry), "target": str(registry), "read_only": True}],
        "networks": {"backend": {"ipv4_address": "172.18.0.4"}},
    }
    document = {
        "services": {
            "api": api,
            "web": {"environment": {"APP_DOMAIN": "rscwz.cn"}, "build": {"args": {"RELEASE_PROFILE": "pilot"}}, "networks": {"backend": {"ipv4_address": "172.18.0.4"}}},
            "db": {"environment": {"POSTGRES_DB": "star_oam", **{key: secrets[key] for key in list(secrets)[:5]}}},
            "migrate": {"environment": {
                "OAM_DATABASE_URL": f"postgresql+psycopg://star_oam_migrator:{secrets['OAM_DB_MIGRATOR_PASSWORD']}@db:5432/star_oam",
            }},
            "kms-pin-gate": {
                "environment": {"OAM_DATABASE_URL": api["environment"]["OAM_DATABASE_URL"]},
                "volumes": [{"type": "bind", "source": str(registry), "target": str(registry), "read_only": True}],
            },
        },
        "networks": {"backend": {"ipam": {"config": [{"subnet": "172.18.0.0/16"}]}}},
    }
    return document, tmp_path / "docker-compose.yml"


def test_valid_pilot_configuration_passes_without_external_io(tmp_path):
    document, compose_file = _document(tmp_path)
    checks = checks_for(document, compose_file)
    assert checks and all(item["ok"] for item in checks), checks


def test_pilot_preflight_binds_pnvs_sts_expiry_to_login_gate(tmp_path):
    document, compose_file = _document(tmp_path)
    environment = document["services"]["api"]["environment"]
    environment["OAM_SMS_SECURITY_TOKEN"] = "synthetic-pnvs-token-" + "x" * 32
    environment["OAM_SMS_SECURITY_TOKEN_EXPIRES_AT"] = (
        datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    assert {item["name"]: item["ok"] for item in checks_for(document, compose_file)}["h5_sms_login"]
    environment["OAM_SMS_SECURITY_TOKEN_EXPIRES_AT"] = datetime.now(timezone.utc).isoformat()
    assert not {item["name"]: item["ok"] for item in checks_for(document, compose_file)}["h5_sms_login"]


def test_pilot_preflight_rejects_password_login_and_writable_registry(tmp_path):
    document, compose_file = _document(tmp_path)
    document["services"]["api"]["environment"]["OAM_PASSWORD_LOGIN_ENABLED"] = "yes"
    document["services"]["api"]["volumes"][0]["read_only"] = False
    checks = {item["name"]: item["ok"] for item in checks_for(document, compose_file)}
    assert checks["password_login_disabled"] is False
    assert checks["kms_registry_readonly_mount"] is False
    assert checks["kms_registry_structure_and_active_keys"] is False


def test_pilot_preflight_rejects_short_database_url_password(tmp_path):
    document, compose_file = _document(tmp_path)
    document["services"]["api"]["environment"]["OAM_DATABASE_URL"] = (
        "postgresql+psycopg://star_oam_api:too-short@db:5432/star_oam"
    )
    checks = {item["name"]: item["ok"] for item in checks_for(document, compose_file)}
    assert checks["api_database_role"] is False


def deployment_document(tmp_path, image_tag="test-candidate"):
    document, compose_file = _document(tmp_path)
    project = "rsc-pilot-test"
    document["name"] = project
    document["volumes"] = {"postgres_data": {"name": project + "_postgres_data"}}
    document["networks"]["backend"]["name"] = project + "_backend"
    document["services"]["db"]["volumes"] = [
        {"type": "volume", "source": "postgres_data", "target": "/var/lib/postgresql/data"}]
    for name, image in (
        ("db", "rsc-pilot-db"), ("web", "rsc-pilot-web"), ("api", "rsc-pilot-api"),
        ("migrate", "rsc-pilot-api"), ("kms-pin-gate", "rsc-pilot-api"),
    ):
        document["services"][name]["image"] = f"{image}:{image_tag}"
    return document, compose_file


@pytest.mark.parametrize("url,path,valid", [
    ("https://rscwz.cn", "/xx/", True), ("https://rscwz.cn/", "/xx/", True),
    ("https://another.invalid", "/xx/", False), ("http://rscwz.cn", "/xx/", False),
    ("https://rscwz.cn/xx", "/xx/", False), ("https://rscwz.cn", "/", False),
])
def test_deployment_smoke_is_bound_to_the_private_entry(tmp_path, url, path, valid):
    document, compose_file = deployment_document(tmp_path)
    checks = {item["name"]: item["ok"] for item in checks_for(
        document, compose_file, project_name="rsc-pilot-test", image_tag="test-candidate",
        smoke_base_url=url, smoke_private_path=path)}
    assert checks["smoke_target_matches_origin"] is valid
    assert checks["isolated_pilot_project"]
    assert checks["pilot_resource_isolation"]


@pytest.mark.parametrize("change", [
    lambda d: d["volumes"]["postgres_data"].update(name="star-oam_postgres_data"),
    lambda d: d["volumes"]["postgres_data"].update(external=True),
    lambda d: d["networks"]["backend"].update(name="star-oam_backend"),
    lambda d: d["services"]["api"].update(container_name="star-oam-api"),
    lambda d: d["services"]["db"]["volumes"][0].update(type="bind", source="/legacy/data"),
])
def test_project_name_cannot_hide_shared_or_legacy_resources(tmp_path, change):
    document, compose_file = deployment_document(tmp_path)
    change(document)
    checks = {item["name"]: item["ok"] for item in checks_for(
        document, compose_file, project_name="rsc-pilot-test", image_tag="test-candidate")}
    assert checks["pilot_resource_isolation"] is False


@pytest.mark.parametrize("project", ["star-oam", "rsc-pilot-", "RSC-pilot-test", "rsc-pilot-../legacy"])
def test_non_pilot_project_is_rejected(tmp_path, project):
    document, compose_file = deployment_document(tmp_path)
    document["name"] = project
    checks = {item["name"]: item["ok"] for item in checks_for(
        document, compose_file, project_name=project, image_tag="test-candidate")}
    assert checks["isolated_pilot_project"] is False


def test_deployment_configuration_binds_all_images_and_database_services(tmp_path):
    document, compose_file = deployment_document(tmp_path)
    checks = checks_for(document, compose_file, project_name="rsc-pilot-test", image_tag="test-candidate")
    assert checks and all(item["ok"] for item in checks), checks


@pytest.mark.parametrize("image_tag", [None, "", ".candidate", "-candidate", "x" * 129, "candidate/old"])
def test_deployment_requires_a_valid_explicit_image_tag(tmp_path, image_tag):
    document, compose_file = deployment_document(tmp_path)
    checks = {item["name"]: item["ok"] for item in checks_for(
        document, compose_file, project_name="rsc-pilot-test", image_tag=image_tag)}
    assert checks["pilot_image_tag"] is False
    assert checks["pilot_service_images"] is False


@pytest.mark.parametrize("service", ["db", "api", "web", "migrate", "kms-pin-gate"])
def test_deployment_rejects_shared_or_different_candidate_images(tmp_path, service):
    document, compose_file = deployment_document(tmp_path)
    for wrong_image in ("star-oam-api:0.9.0", "rsc-pilot-api:another-candidate", ""):
        document["services"][service]["image"] = wrong_image
        checks = {item["name"]: item["ok"] for item in checks_for(
            document, compose_file, project_name="rsc-pilot-test", image_tag="test-candidate")}
        assert checks["pilot_service_images"] is False


DATABASE_SERVICES = [
    ("api", "api_database_role", "star_oam_api", "OAM_DB_API_PASSWORD"),
    ("migrate", "migration_database_binding", "star_oam_migrator", "OAM_DB_MIGRATOR_PASSWORD"),
    ("kms-pin-gate", "kms_pin_database_binding", "star_oam_api", "OAM_DB_API_PASSWORD"),
]


@pytest.mark.parametrize("service,check_name,role,password_key", DATABASE_SERVICES)
@pytest.mark.parametrize("change", [
    lambda value: value.replace("@db:", "@production-db.invalid:"),
    lambda value: value.replace(":5432/", ":5433/"),
    lambda value: value.replace("/star_oam", "/unrelated_db"),
    lambda value: value.replace("postgresql+psycopg://", "postgresql+psycopg://wrong_role", 1),
    lambda value: value.replace("@db:", "different-password@db:"),
    lambda value: value + "?host=production-db.invalid",
    lambda value: value + "?",
    lambda value: value + "#fragment",
    lambda value: value + "#",
])
def test_database_services_cannot_target_another_database_or_credential(
    tmp_path, service, check_name, role, password_key, change,
):
    document, compose_file = _document(tmp_path)
    environment = document["services"][service]["environment"]
    environment["OAM_DATABASE_URL"] = change(environment["OAM_DATABASE_URL"])
    checks = {item["name"]: item["ok"] for item in checks_for(document, compose_file)}
    assert checks[check_name] is False


@pytest.mark.parametrize("service,check_name,role,password_key", DATABASE_SERVICES)
def test_database_binding_decodes_password_once_and_preserves_plus(
    tmp_path, service, check_name, role, password_key,
):
    document, compose_file = _document(tmp_path)
    password = "distinct-strong-password-" + "a" * 32 + "@:/?#%+"
    document["services"]["db"]["environment"][password_key] = password
    document["services"][service]["environment"]["OAM_DATABASE_URL"] = (
        f"postgresql+psycopg://{role}:{quote(password, safe='')}@db:5432/star_oam"
    )
    checks = {item["name"]: item["ok"] for item in checks_for(document, compose_file)}
    assert checks[check_name] is True
    document["services"]["db"]["environment"][password_key] = quote(password, safe="")
    checks = {item["name"]: item["ok"] for item in checks_for(document, compose_file)}
    assert checks[check_name] is False
