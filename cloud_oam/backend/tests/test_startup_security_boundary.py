import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "database_url": (
            "postgresql+psycopg://star_oam_api:test@db:5432/test"
        ),
        "database_schema_mode": "alembic",
        "legacy_prototype_writes_enabled": False,
        "jwt_secret": "production-test-secret-with-at-least-32-characters",
        "identity_hash_secret": "production-identity-hash-secret-at-least-32-characters",
        "identity_hash_version": 1,
        "auth_idempotency_hmac_secret": (
            "production-auth-idempotency-hmac-secret-at-least-32-characters"
        ),
        "auth_idempotency_encryption_provider": "aliyun_kms",
        "auth_idempotency_kms_key_id": "kms-production-auth-idempotency",
        "auth_idempotency_encryption_key_version": 1,
        "auth_login_rate_limit_hmac_secret": (
            "production-auth-login-rate-limit-secret-at-least-32-characters"
        ),
        "auth_login_rate_limit_hash_version": 1,
        "admin_mobile": "",
        "admin_name": "",
        "admin_initial_password": None,
        "password_login_enabled": False,
        "sms_login_enabled": True,
        "sms_provider": "aliyun_pnvs",
        "sms_access_key_id": "test-access-key-id",
        "sms_access_key_secret": "test-access-key-secret",
        "sms_sign_name": "test-sign",
        "sms_template_code": "SMS_TEST",
        "sms_scheme_name": "test-scheme",
        "wechat_login_enabled": False,
        "wechat_provider": "disabled",
        "wechat_app_id": "",
        "wechat_app_secret": "",
        "edge_sync_enabled": False,
        "edge_sync_secret": "",
        "edge_sync_allowed_sources": "",
        "edge_sync_legacy_batches_enabled": False,
        "edge_sync_legacy_personnel_projection_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_security_sensitive_defaults_are_fail_closed_and_identity_neutral():
    fields = Settings.model_fields
    assert fields["environment"].default == "production"
    assert fields["database_schema_mode"].default == "alembic"
    assert fields["database_url"].default == ""
    assert fields["database_expected_runtime_role"].default == "star_oam_api"
    assert (
        fields["database_expected_migration_role"].default
        == "star_oam_migrator"
    )
    assert fields["password_login_enabled"].default is False
    assert fields["identity_hash_secret"].default == ""
    assert fields["identity_hash_version"].default == 1
    assert fields["auth_idempotency_ttl_seconds"].default == 90
    assert fields["auth_idempotency_hmac_secret"].default == ""
    assert fields["auth_idempotency_encryption_provider"].default == "disabled"
    assert fields["auth_idempotency_kms_key_id"].default == ""
    assert fields["kms_readiness_success_ttl_seconds"].default == 60
    assert fields["kms_readiness_failure_ttl_seconds"].default == 10
    assert fields["kms_readiness_wait_budget_seconds"].default == 4
    assert fields["kms_readiness_probe_budget_seconds"].default == 4
    assert fields["auth_login_rate_limit_hmac_secret"].default == ""
    assert fields["auth_login_rate_limit_hash_version"].default == 1
    assert fields["sms_dispatch_lease_seconds"].default == 30
    assert fields["sms_provider_max_concurrency"].default == 2
    assert fields["admin_mobile"].default == ""
    assert fields["admin_name"].default == ""
    assert fields["admin_initial_password"].default is None
    assert fields["edge_sync_legacy_batches_enabled"].default is False
    assert fields["edge_sync_legacy_personnel_projection_enabled"].default is False


def test_production_requires_a_complete_passwordless_login_channel():
    with pytest.raises(ValueError, match="passwordless login channel"):
        production_settings(
            sms_login_enabled=False,
            sms_provider="disabled",
        ).validate_api_startup()

    with pytest.raises(ValueError, match="SMS login is not fully configured"):
        production_settings(sms_access_key_secret="").validate_api_startup()

    settings = production_settings(
        sms_login_enabled=False,
        sms_provider="disabled",
        wechat_login_enabled=True,
        wechat_provider="wechat",
        wechat_app_id="wx-production-test",
        wechat_app_secret="wechat-production-test-secret",
    )
    settings.validate_api_startup()
    assert settings.wechat_configuration_ready() is True


def test_sms_dispatch_lease_preserves_provider_call_safety_window() -> None:
    with pytest.raises(ValidationError, match="sms_dispatch_lease_seconds"):
        production_settings(sms_dispatch_lease_seconds=29)

    assert production_settings(sms_dispatch_lease_seconds=30).sms_dispatch_lease_seconds == 30


@pytest.mark.parametrize("value", [0, 6])
def test_sms_provider_concurrency_is_strictly_bounded(value: int) -> None:
    with pytest.raises(ValidationError, match="sms_provider_max_concurrency"):
        production_settings(sms_provider_max_concurrency=value)


def test_production_api_database_url_and_roles_are_separated() -> None:
    with pytest.raises(ValueError, match="expected runtime role"):
        production_settings(
            database_url=(
                "postgresql+psycopg://star_oam_migrator:test@db:5432/test"
            )
        ).validate_api_startup()
    with pytest.raises(ValidationError, match="roles must differ"):
        production_settings(
            database_expected_migration_role="star_oam_api"
        )
    with pytest.raises(ValidationError, match="not canonical"):
        production_settings(database_expected_runtime_role="Star-OAM-API")


def test_production_api_requires_jwt_but_edge_settings_do_not():
    settings = production_settings(jwt_secret="")
    with pytest.raises(ValueError, match="JWT secret of at least 32 characters"):
        settings.validate_api_startup()

    edge_settings = production_settings(
        jwt_secret="",
        edge_sync_enabled=True,
        edge_sync_secret="edge-sync-secret-with-at-least-32-characters",
        edge_sync_allowed_sources="edge-production-01",
    )
    assert edge_settings.edge_sync_configuration_ready() is True

    placeholder = production_settings(
        jwt_secret="replace-with-at-least-32-random-characters"
    )
    with pytest.raises(ValueError, match="JWT secret of at least 32 characters"):
        placeholder.validate_api_startup()


def test_production_api_requires_a_separate_versioned_identity_hash_secret():
    with pytest.raises(ValueError, match="dedicated identity hash secret"):
        production_settings(identity_hash_secret="").validate_api_startup()

    with pytest.raises(ValueError, match="dedicated identity hash secret"):
        production_settings(
            identity_hash_secret="replace-with-a-random-identity-secret"
        ).validate_api_startup()

    settings = production_settings(identity_hash_version=2)
    settings.validate_api_startup()
    assert settings.identity_hash_version == 2


def test_production_api_requires_kms_encrypted_authentication_idempotency():
    with pytest.raises(ValueError, match="idempotency HMAC secret"):
        production_settings(auth_idempotency_hmac_secret="").validate_api_startup()

    with pytest.raises(ValueError, match="aliyun_kms authentication"):
        production_settings(
            auth_idempotency_encryption_provider="disabled",
            auth_idempotency_kms_key_id="",
        ).validate_api_startup()

    with pytest.raises(ValueError, match="aliyun_kms authentication"):
        production_settings(
            auth_idempotency_encryption_provider="aliyun_kms",
            auth_idempotency_kms_key_id="replace-with-kms-key-id",
        ).validate_api_startup()

    settings = production_settings(auth_idempotency_ttl_seconds=120)
    settings.validate_api_startup()
    assert settings.authentication_idempotency_kms_configuration_ready() is True


def test_production_api_rejects_sdk_wire_debug_and_oversized_readiness_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEBUG", "sdk")
    with pytest.raises(ValueError, match="forbids Alibaba Cloud SDK wire debug"):
        production_settings().validate_api_startup()
    monkeypatch.delenv("DEBUG")

    for field in (
        "kms_readiness_wait_budget_seconds",
        "kms_readiness_probe_budget_seconds",
    ):
        with pytest.raises(ValidationError, match=field):
            production_settings(**{field: 5})


def test_production_api_requires_dedicated_login_rate_limit_hmac_secret():
    with pytest.raises(ValueError, match="login rate-limit HMAC secret"):
        production_settings(auth_login_rate_limit_hmac_secret="").validate_api_startup()

    production_settings().validate_api_startup()


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "auth_idempotency_hmac_secret": (
                "production-test-secret-with-at-least-32-characters"
            )
        },
        {
            "auth_idempotency_hmac_secret": (
                "production-identity-hash-secret-at-least-32-characters"
            )
        },
        {
            "identity_hash_secret": (
                "production-test-secret-with-at-least-32-characters"
            )
        },
        {
            "auth_login_rate_limit_hmac_secret": (
                "production-test-secret-with-at-least-32-characters"
            )
        },
        {
            "auth_login_rate_limit_hmac_secret": (
                "production-auth-idempotency-hmac-secret-at-least-32-characters"
            )
        },
    ],
)
def test_production_authentication_secrets_must_be_pairwise_distinct(overrides):
    with pytest.raises(ValueError, match="must be pairwise distinct"):
        production_settings(**overrides).validate_api_startup()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"password_login_enabled": True}, "password login is forbidden"),
        ({"database_schema_mode": "bootstrap"}, "startup bootstrap is forbidden"),
        (
            {"database_url": "sqlite+pysqlite:///:memory:"},
            r"postgresql\+psycopg database URL",
        ),
        ({"admin_mobile": "13800000000"}, "administrator credentials are forbidden"),
        (
            {"edge_sync_legacy_personnel_projection_enabled": True},
            "personnel projection is forbidden",
        ),
    ],
)
def test_production_rejects_prototype_auth_and_bootstrap_paths(overrides, message):
    with pytest.raises(ValidationError, match=message):
        production_settings(**overrides)


def test_production_rejects_mock_login_provider_at_api_startup():
    settings = production_settings(
        sms_provider="mock",
        sms_test_code="123456",
    )
    with pytest.raises(ValueError, match="SMS login is not fully configured"):
        settings.validate_api_startup()


def test_non_production_seed_bootstrap_requires_explicit_identity():
    common = {
        "environment": "test",
        "database_url": "sqlite+pysqlite:///:memory:",
        "database_schema_mode": "bootstrap_with_seed",
        "legacy_prototype_writes_enabled": False,
        "jwt_secret": "non-production-test-secret-with-at-least-32-characters",
        "password_login_enabled": False,
        "admin_mobile": "",
        "admin_name": "",
        "admin_initial_password": None,
    }
    with pytest.raises(ValidationError, match="requires explicit admin_mobile"):
        Settings(_env_file=None, **common)

    settings = Settings(
        _env_file=None,
        **(
            common
            | {
                "admin_mobile": "13800000000",
                "admin_name": "Test Administrator",
                "admin_initial_password": "test-only-password",
            }
        ),
    )
    assert settings.database_schema_mode == "bootstrap_with_seed"


def test_production_edge_sync_requires_allowlist_and_forbids_legacy_batches():
    with pytest.raises(ValidationError, match="non-empty source allowlist"):
        production_settings(
            edge_sync_enabled=True,
            edge_sync_secret="edge-sync-secret-with-at-least-32-characters",
        )

    with pytest.raises(ValidationError, match="legacy edge-sync batches"):
        production_settings(edge_sync_legacy_batches_enabled=True)

    settings = production_settings(
        edge_sync_enabled=True,
        edge_sync_secret="edge-sync-secret-with-at-least-32-characters",
        edge_sync_allowed_sources=" edge-a, , edge-b,edge-a ",
    )
    assert settings.edge_sync_allowed_source_set() == {"edge-a", "edge-b"}
    assert settings.edge_sync_configuration_ready() is True


def test_lifespans_do_not_touch_schema_without_explicit_test_bootstrap(tmp_path):
    database_path = tmp_path / "must-not-be-created.db"
    upload_path = tmp_path / "uploads"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(BACKEND),
            "OAM_ENVIRONMENT": "test",
            "OAM_DATABASE_URL": f"sqlite+pysqlite:///{database_path}",
            "OAM_DATABASE_SCHEMA_MODE": "alembic",
            "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "false",
            "OAM_JWT_SECRET": "lifecycle-test-secret-with-at-least-32-characters",
            "OAM_UPLOAD_DIR": str(upload_path),
            "OAM_ADMIN_MOBILE": "",
            "OAM_ADMIN_NAME": "",
            "OAM_ADMIN_INITIAL_PASSWORD": "",
            "OAM_PASSWORD_LOGIN_ENABLED": "false",
            "OAM_SMS_LOGIN_ENABLED": "false",
            "OAM_SMS_PROVIDER": "disabled",
            "OAM_WECHAT_LOGIN_ENABLED": "false",
            "OAM_WECHAT_PROVIDER": "disabled",
            "OAM_EDGE_SYNC_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED": "false",
        }
    )
    script = """
import asyncio

from app.main import _run_startup_database_boundary, lifespan


async def run_main_lifespan():
    async with lifespan(None):
        pass


asyncio.run(run_main_lifespan())
events = []
_run_startup_database_boundary(
    schema_mode="alembic",
    create_schema=lambda: events.append("schema"),
    seed_data=lambda: events.append("seed"),
)
assert events == []
_run_startup_database_boundary(
    schema_mode="bootstrap",
    create_schema=lambda: events.append("schema"),
    seed_data=lambda: events.append("seed"),
)
assert events == ["schema"]
_run_startup_database_boundary(
    schema_mode="bootstrap_with_seed",
    create_schema=lambda: events.append("schema"),
    seed_data=lambda: events.append("seed"),
)
assert events == ["schema", "schema", "seed"]
try:
    _run_startup_database_boundary(
        schema_mode="unknown",
        create_schema=lambda: events.append("unexpected-schema"),
        seed_data=lambda: events.append("unexpected-seed"),
    )
except RuntimeError:
    pass
else:
    raise AssertionError("unknown schema mode did not fail closed")
assert events == ["schema", "schema", "seed"]

from app import edge_main


async def run_edge_lifespan():
    async with edge_main.lifespan(None):
        pass


asyncio.run(run_edge_lifespan())
assert not hasattr(edge_main, "Base")
assert not hasattr(edge_main, "run_compatibility_migrations")
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert upload_path.is_dir()
    assert not database_path.exists()


def test_production_api_rejects_missing_login_channel_before_startup_side_effects(
    tmp_path,
):
    database_path = tmp_path / "must-not-be-created.db"
    upload_path = tmp_path / "must-not-be-created-uploads"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(BACKEND),
            "OAM_ENVIRONMENT": "production",
            "OAM_DATABASE_URL": "postgresql+psycopg://test:test@db:5432/test",
            "OAM_DATABASE_SCHEMA_MODE": "alembic",
            "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "false",
            "OAM_JWT_SECRET": "production-lifecycle-secret-with-at-least-32-characters",
            "OAM_UPLOAD_DIR": str(upload_path),
            "OAM_ADMIN_MOBILE": "",
            "OAM_ADMIN_NAME": "",
            "OAM_ADMIN_INITIAL_PASSWORD": "",
            "OAM_PASSWORD_LOGIN_ENABLED": "false",
            "OAM_SMS_LOGIN_ENABLED": "false",
            "OAM_SMS_PROVIDER": "disabled",
            "OAM_WECHAT_LOGIN_ENABLED": "false",
            "OAM_WECHAT_PROVIDER": "disabled",
            "OAM_EDGE_SYNC_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED": "false",
        }
    )
    script = """
import asyncio

from app.main import lifespan


async def run():
    try:
        async with lifespan(None):
            pass
    except ValueError as error:
        assert "passwordless login channel" in str(error)
        return
    raise AssertionError("production API startup unexpectedly succeeded")


asyncio.run(run())
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not upload_path.exists()
    assert not database_path.exists()


def test_production_edge_receiver_needs_no_user_login_and_performs_no_ddl(tmp_path):
    database_path = tmp_path / "edge-must-not-be-created.db"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(BACKEND),
            "OAM_ENVIRONMENT": "production",
            "OAM_DATABASE_URL": "postgresql+psycopg://test:test@db:5432/test",
            "OAM_DATABASE_SCHEMA_MODE": "alembic",
            "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "false",
            "OAM_ADMIN_MOBILE": "",
            "OAM_ADMIN_NAME": "",
            "OAM_ADMIN_INITIAL_PASSWORD": "",
            "OAM_PASSWORD_LOGIN_ENABLED": "false",
            "OAM_SMS_LOGIN_ENABLED": "false",
            "OAM_SMS_PROVIDER": "disabled",
            "OAM_WECHAT_LOGIN_ENABLED": "false",
            "OAM_WECHAT_PROVIDER": "disabled",
            "OAM_EDGE_SYNC_ENABLED": "true",
            "OAM_EDGE_SYNC_SECRET": "edge-sync-secret-with-at-least-32-characters",
            "OAM_EDGE_SYNC_ALLOWED_SOURCES": "edge-production-01",
            "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED": "false",
        }
    )
    environment.pop("OAM_JWT_SECRET", None)
    script = """
import asyncio

from app import edge_main


async def run():
    async with edge_main.lifespan(None):
        pass


asyncio.run(run())
assert edge_main.settings.edge_sync_configuration_ready()
assert not hasattr(edge_main, "Base")
assert not hasattr(edge_main, "run_compatibility_migrations")
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert not database_path.exists()


def test_production_route_surfaces_separate_edge_ingress_from_main_api(tmp_path):
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(BACKEND),
            "OAM_ENVIRONMENT": "production",
            "OAM_DATABASE_URL": "postgresql+psycopg://test:test@db:5432/test",
            "OAM_DATABASE_SCHEMA_MODE": "alembic",
            "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "false",
            "OAM_JWT_SECRET": "route-surface-secret-with-at-least-32-characters",
            "OAM_ADMIN_MOBILE": "",
            "OAM_ADMIN_NAME": "",
            "OAM_ADMIN_INITIAL_PASSWORD": "",
            "OAM_PASSWORD_LOGIN_ENABLED": "false",
            "OAM_SMS_LOGIN_ENABLED": "true",
            "OAM_SMS_PROVIDER": "aliyun_pnvs",
            "OAM_SMS_ACCESS_KEY_ID": "test-key-id",
            "OAM_SMS_ACCESS_KEY_SECRET": "test-key-secret",
            "OAM_SMS_SIGN_NAME": "test-sign",
            "OAM_SMS_TEMPLATE_CODE": "SMS_TEST",
            "OAM_SMS_SCHEME_NAME": "test-scheme",
            "OAM_WECHAT_LOGIN_ENABLED": "false",
            "OAM_WECHAT_PROVIDER": "disabled",
            "OAM_EDGE_SYNC_ENABLED": "true",
            "OAM_EDGE_SYNC_SECRET": "edge-route-secret-with-at-least-32-characters",
            "OAM_EDGE_SYNC_ALLOWED_SOURCES": "edge-production-01",
            "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED": "false",
        }
    )
    script = """
from app import edge_main, main


def routes(app):
    return {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }


main_routes = routes(main.app)
edge_routes = routes(edge_main.app)
assert ("/api/integrations/oam/edge/snapshots/batches", "POST") not in main_routes
assert ("/api/integrations/oam/edge/snapshots/complete", "POST") not in main_routes
assert ("/api/integrations/oam/edge/status", "GET") not in main_routes
assert ("/api/access/context", "GET") in main_routes
for legacy_path in (
    "/api/dashboard",
    "/api/inventory",
    "/api/transfers",
    "/api/stocktakes",
    "/api/work-order-materials",
    "/api/warehouses",
    "/api/materials",
    "/api/oam",
):
    assert not any(path == legacy_path or path.startswith(legacy_path + "/") for path, _ in main_routes)
assert "/api/auth/users" not in main.PRODUCTION_AUTH_PATHS
assert "/api/auth/sessions" in main.PRODUCTION_AUTH_PATHS
assert main.is_production_auth_path("GET", "/api/auth/sessions")
assert main.is_production_auth_path("POST", "/api/auth/sessions/session-id/revoke")
assert not main.is_production_auth_path("GET", "/api/auth/sessions/session-id/revoke")
assert ("/api/integrations/oam/edge/snapshots/batches", "POST") in edge_routes
assert ("/api/integrations/oam/edge/snapshots/complete", "POST") in edge_routes
assert ("/api/integrations/oam/edge/status", "GET") not in edge_routes
assert not any(path.startswith("/api/auth") for path, _ in edge_routes)
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
