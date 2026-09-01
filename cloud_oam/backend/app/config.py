from functools import lru_cache
from ipaddress import ip_address
import os
import re
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


SECRET_PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "change-me",
    "changeme",
)
DATABASE_ROLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
OSS_REGION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
OSS_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")


def _is_configured_secret(value: str, *, min_length: int = 1) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    return len(normalized) >= min_length and not any(
        marker in lowered for marker in SECRET_PLACEHOLDER_MARKERS
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OAM_", case_sensitive=False)

    app_name: str = "RSC个人仓"
    environment: Literal["development", "test", "staging", "production"] = (
        "production"
    )
    database_url: str = ""
    database_expected_runtime_role: str = "star_oam_api"
    database_expected_migration_role: str = "star_oam_migrator"
    database_schema_mode: Literal["alembic", "bootstrap", "bootstrap_with_seed"] = (
        "alembic"
    )
    legacy_prototype_writes_enabled: bool = False
    # The edge receiver shares this settings object but never issues or verifies
    # user sessions.  Keep the field optional at parse time and enforce it at
    # the user-facing API startup boundary instead.
    jwt_secret: str = ""
    jwt_ttl_minutes: int = Field(default=15, ge=5, le=1440)
    session_ttl_days: int = Field(default=30, ge=1, le=90)
    # Login identifiers are pseudonymized with a dedicated, versioned HMAC
    # key.  Do not reuse the JWT signing key: the two secrets have independent
    # rotation and exposure boundaries.
    identity_hash_secret: str = ""
    identity_hash_version: int = Field(default=1, ge=1, le=2_147_483_647)
    # Authentication writes return non-reconstructible credentials.  Their
    # short idempotency replay window therefore uses a dedicated HMAC secret
    # and KMS-resolved AES data key.  There is intentionally no setting for a
    # plaintext AES key or master key.
    auth_idempotency_ttl_seconds: int = Field(default=90, ge=30, le=120)
    auth_idempotency_hmac_secret: str = ""
    auth_idempotency_encryption_provider: Literal[
        "disabled", "aliyun_kms"
    ] = "disabled"
    auth_idempotency_kms_key_id: str = ""
    auth_idempotency_encryption_key_version: int = Field(
        default=1, ge=1, le=2_147_483_647
    )
    # KMS unwraps versioned AES-256 data keys from a ciphertext-only registry.
    # Credentials come from Alibaba Cloud's default provider chain; plaintext
    # application keys are never accepted as settings.
    kms_endpoint: str = ""
    kms_region: str = ""
    kms_encrypted_data_key_registry_path: str = ""
    kms_readiness_success_ttl_seconds: int = Field(default=60, ge=30, le=300)
    kms_readiness_failure_ttl_seconds: int = Field(default=10, ge=1, le=30)
    # Health first spends at most 2.5s on its isolated PostgreSQL probe.  Keep
    # either KMS path within the remaining Docker/ALB 8s readiness budget.
    kms_readiness_wait_budget_seconds: int = Field(default=4, ge=1, le=4)
    kms_readiness_probe_budget_seconds: int = Field(default=4, ge=1, le=4)
    # Formal login admission uses its own domain-separated HMAC key and short
    # PostgreSQL counter windows.  It must not reuse identity or idempotency
    # secrets because all three have different rotation and exposure scopes.
    auth_login_rate_limit_hmac_secret: str = ""
    auth_login_rate_limit_hash_version: int = Field(
        default=1, ge=1, le=2_147_483_647
    )
    auth_login_rate_limit_window_seconds: int = Field(
        default=60, ge=10, le=3600
    )
    auth_login_rate_limit_global_limit: int = Field(
        default=300, ge=1, le=1_000_000
    )
    auth_login_rate_limit_ip_limit: int = Field(
        default=20, ge=1, le=1_000_000
    )
    auth_login_rate_limit_identity_limit: int = Field(
        default=8, ge=1, le=1_000_000
    )
    # Formal material-request commands are independently gated.  Read routes
    # remain available while this switch is off or while the write-only KMS
    # adapter is not installed by the deployment composition root.
    material_request_writes_enabled: bool = False
    material_request_idempotency_hmac_secret: str = ""
    material_request_contact_mobile_hmac_secret: str = ""
    material_request_contact_mobile_hash_version: int = Field(
        default=1, ge=1, le=2_147_483_647
    )
    material_request_contact_encryption_provider: Literal[
        "disabled", "aliyun_kms"
    ] = "disabled"
    material_request_contact_kms_key_id: str = ""
    material_request_contact_encryption_key_version: int = Field(
        default=1, ge=1, le=2_147_483_647
    )
    # Non-opening stocktake commands are mounted behind their own explicit
    # production gate.  Their replay coordinates must not share an HMAC key
    # with authentication or material-request commands.
    stocktake_writes_enabled: bool = False
    stocktake_idempotency_hmac_secret: str = ""
    # Formal attachments use private OSS through short V4 presigned requests.
    # Credentials are resolved only by the SDK environment provider; there are
    # deliberately no OSS access-key settings in this application model.
    file_storage_enabled: bool = False
    file_storage_provider: Literal["disabled", "aliyun_oss_v2"] = "disabled"
    file_storage_region: str = ""
    file_storage_bucket: str = ""
    file_upload_intent_ttl_seconds: int = Field(default=600, ge=60, le=900)
    file_download_intent_ttl_seconds: int = Field(default=300, ge=30, le=600)
    file_idempotency_hmac_secret: str = ""
    cookie_secure: bool = True
    upload_dir: str = "/data/uploads"
    max_upload_bytes: int = Field(
        default=120 * 1024 * 1024,
        ge=1,
        le=120 * 1024 * 1024,
    )
    admin_mobile: str = ""
    admin_name: str = ""
    admin_initial_password: str | None = None
    app_origin: str = ""
    # Only these direct network peers may supply the client address through
    # X-Forwarded-For.  Keep this separate from the ASGI server's own proxy
    # settings so application-level rate limits have an explicit trust boundary.
    trusted_proxy_ips: str = "127.0.0.1,::1"

    password_login_enabled: bool = False
    sms_login_enabled: bool = False
    sms_provider: str = "disabled"
    sms_access_key_id: str = ""
    sms_access_key_secret: str = ""
    sms_sign_name: str = ""
    sms_template_code: str = ""
    sms_scheme_name: str = "RSC个人仓登录"
    sms_code_length: int = Field(default=6, ge=4, le=8)
    sms_valid_seconds: int = Field(default=300, ge=60, le=1800)
    sms_interval_seconds: int = Field(default=60, ge=30, le=600)
    sms_dispatch_lease_seconds: int = Field(default=30, ge=30, le=120)
    sms_provider_max_concurrency: int = Field(default=2, ge=1, le=5)
    sms_max_per_mobile_hour: int = Field(default=5, ge=1, le=30)
    sms_max_per_ip_hour: int = Field(default=20, ge=1, le=200)
    sms_max_verify_attempts: int = Field(default=5, ge=1, le=10)
    sms_test_code: str = ""

    wechat_login_enabled: bool = False
    wechat_provider: str = "disabled"
    wechat_app_id: str = ""
    wechat_app_secret: str = ""
    wechat_test_mobile: str = ""

    edge_sync_enabled: bool = False
    edge_sync_secret: str = ""
    edge_sync_allowed_sources: str = ""
    edge_sync_legacy_batches_enabled: bool = False
    edge_sync_legacy_personnel_projection_enabled: bool = False
    edge_sync_max_clock_skew_seconds: int = Field(default=300, ge=60, le=3600)
    edge_sync_max_body_bytes: int = Field(
        default=8 * 1024 * 1024,
        ge=1024,
        le=32 * 1024 * 1024,
    )
    edge_sync_max_records_per_batch: int = Field(default=500, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_runtime_security_boundary(self) -> "Settings":
        errors: list[str] = []

        try:
            self.trusted_proxy_ip_set()
        except ValueError as exc:
            errors.append(str(exc))

        if self.database_schema_mode == "bootstrap_with_seed" and not all(
            (
                self.admin_mobile.strip(),
                self.admin_name.strip(),
                self.admin_initial_password,
            )
        ):
            errors.append(
                "bootstrap_with_seed requires explicit admin_mobile, admin_name, "
                "and admin_initial_password"
            )

        if self.environment == "production":
            if not self.database_url.startswith("postgresql+psycopg://") or any(
                marker in self.database_url.lower()
                for marker in SECRET_PLACEHOLDER_MARKERS
            ):
                errors.append(
                    "production requires an explicit non-placeholder "
                    "postgresql+psycopg database URL"
                )
            if self.legacy_prototype_writes_enabled:
                errors.append("legacy prototype writes are forbidden in production")
            if self.password_login_enabled:
                errors.append("password login is forbidden in production")
            if self.database_schema_mode != "alembic":
                errors.append(
                    "production schema changes must use Alembic; startup bootstrap "
                    "is forbidden"
                )
            if not DATABASE_ROLE_PATTERN.fullmatch(
                self.database_expected_runtime_role
            ):
                errors.append("production runtime database role is not canonical")
            if not DATABASE_ROLE_PATTERN.fullmatch(
                self.database_expected_migration_role
            ):
                errors.append("production migration database role is not canonical")
            if (
                self.database_expected_runtime_role
                == self.database_expected_migration_role
            ):
                errors.append(
                    "production runtime and migration database roles must differ"
                )
            if any(
                (
                    self.admin_mobile.strip(),
                    self.admin_name.strip(),
                    self.admin_initial_password,
                )
            ):
                errors.append(
                    "production bootstrap administrator credentials are forbidden"
                )

            if self.edge_sync_legacy_batches_enabled:
                errors.append("legacy edge-sync batches are forbidden in production")
            if self.edge_sync_legacy_personnel_projection_enabled:
                errors.append(
                    "legacy edge-sync personnel projection is forbidden in production"
                )
            if self.edge_sync_enabled:
                if not _is_configured_secret(self.edge_sync_secret, min_length=32):
                    errors.append(
                        "production edge sync requires a secret of at least 32 characters"
                    )
                if not self.edge_sync_allowed_source_set():
                    errors.append(
                        "production edge sync requires a non-empty source allowlist"
                    )

        if errors:
            raise ValueError("; ".join(errors))
        return self

    def validate_api_startup(self) -> None:
        """Fail closed when the production user-facing API cannot authenticate."""

        if self.environment != "production":
            return
        errors: list[str] = []
        if os.getenv("DEBUG", "").strip().lower() == "sdk":
            errors.append(
                "production API forbids Alibaba Cloud SDK wire debug logging"
            )
        try:
            database_username = make_url(self.database_url).username
        except Exception:
            database_username = None
        if database_username != self.database_expected_runtime_role:
            errors.append(
                "production API database URL must use the expected runtime role"
            )
        if not _is_configured_secret(self.jwt_secret, min_length=32):
            errors.append(
                "production API requires a JWT secret of at least 32 characters"
            )
        if not _is_configured_secret(self.identity_hash_secret, min_length=32):
            errors.append(
                "production API requires a dedicated identity hash secret "
                "of at least 32 characters"
            )
        if not _is_configured_secret(
            self.auth_idempotency_hmac_secret, min_length=32
        ):
            errors.append(
                "production API requires a dedicated authentication idempotency "
                "HMAC secret of at least 32 characters"
            )
        if not _is_configured_secret(
            self.auth_login_rate_limit_hmac_secret, min_length=32
        ):
            errors.append(
                "production API requires a dedicated authentication login "
                "rate-limit HMAC secret of at least 32 characters"
            )
        configured_authentication_secrets = [
            value.strip()
            for value in (
                self.jwt_secret,
                self.identity_hash_secret,
                self.auth_idempotency_hmac_secret,
                self.auth_login_rate_limit_hmac_secret,
                *(
                    (
                        self.material_request_idempotency_hmac_secret,
                        self.material_request_contact_mobile_hmac_secret,
                    )
                    if self.material_request_writes_enabled
                    else ()
                ),
                *(
                    (self.stocktake_idempotency_hmac_secret,)
                    if self.stocktake_writes_enabled
                    else ()
                ),
                *(
                    (self.file_idempotency_hmac_secret,)
                    if self.file_storage_enabled
                    else ()
                ),
                *((self.edge_sync_secret,) if self.edge_sync_enabled else ()),
            )
            if _is_configured_secret(value, min_length=32)
        ]
        if len(configured_authentication_secrets) != len(
            set(configured_authentication_secrets)
        ):
            errors.append(
                "production JWT, identity hash, authentication idempotency, "
                "authentication login rate-limit, material-request idempotency, "
                "contact mobile hash, stocktake idempotency, file idempotency, "
                "and edge-sync secrets must be "
                "pairwise distinct"
            )
        if not self.authentication_idempotency_kms_configuration_ready():
            errors.append(
                "production API requires the aliyun_kms authentication "
                "idempotency encryption provider and a non-placeholder KMS key ID"
            )
        if self.material_request_writes_enabled:
            if not _is_configured_secret(
                self.material_request_idempotency_hmac_secret,
                min_length=32,
            ):
                errors.append(
                    "enabled production material-request writes require a dedicated "
                    "idempotency HMAC secret of at least 32 characters"
                )
            if not _is_configured_secret(
                self.material_request_contact_mobile_hmac_secret,
                min_length=32,
            ):
                errors.append(
                    "enabled production material-request writes require a dedicated "
                    "contact mobile HMAC secret of at least 32 characters"
                )
            if not self.material_request_contact_kms_configuration_ready():
                errors.append(
                    "enabled production material-request writes require the "
                    "aliyun_kms contact encryption provider and a non-placeholder "
                    "KMS key ID"
                )
            if (
                self.material_request_contact_kms_key_id.strip()
                and self.material_request_contact_kms_key_id.strip()
                == self.auth_idempotency_kms_key_id.strip()
            ):
                errors.append(
                    "material-request contact and authentication idempotency KMS "
                    "key IDs must be distinct"
                )
        if self.stocktake_writes_enabled and not _is_configured_secret(
            self.stocktake_idempotency_hmac_secret,
            min_length=32,
        ):
            errors.append(
                "enabled production stocktake writes require a dedicated "
                "idempotency HMAC secret of at least 32 characters"
            )
        if self.file_storage_enabled:
            if not self.file_storage_configuration_ready():
                errors.append(
                    "enabled production file storage requires the aliyun_oss_v2 "
                    "provider, canonical region and private bucket coordinates"
                )
            if not _is_configured_secret(
                self.file_idempotency_hmac_secret,
                min_length=32,
            ):
                errors.append(
                    "enabled production file storage requires a dedicated "
                    "idempotency HMAC secret of at least 32 characters"
                )
        sms_ready = self.sms_configuration_ready()
        wechat_ready = self.wechat_configuration_ready()
        if self.sms_login_enabled and not sms_ready:
            errors.append("enabled production SMS login is not fully configured")
        if self.wechat_login_enabled and not wechat_ready:
            errors.append("enabled production WeChat login is not fully configured")
        if not (sms_ready or wechat_ready):
            errors.append(
                "production requires at least one fully configured passwordless "
                "login channel (SMS or WeChat)"
            )
        if errors:
            raise ValueError("; ".join(errors))

    def sms_configuration_ready(self) -> bool:
        if not self.sms_login_enabled:
            return False
        if self.sms_provider == "mock":
            return self.environment != "production" and bool(self.sms_test_code)
        if self.sms_provider != "aliyun_pnvs":
            return False
        return all(
            value.strip()
            for value in (
                self.sms_access_key_id,
                self.sms_access_key_secret
                if _is_configured_secret(self.sms_access_key_secret)
                else "",
                self.sms_sign_name,
                self.sms_template_code,
                self.sms_scheme_name,
            )
        )

    def wechat_configuration_ready(self) -> bool:
        if not self.wechat_login_enabled:
            return False
        if self.wechat_provider == "mock":
            return self.environment != "production" and bool(self.wechat_test_mobile)
        return self.wechat_provider == "wechat" and bool(
            self.wechat_app_id.strip()
            and _is_configured_secret(self.wechat_app_secret)
        )

    def authentication_idempotency_kms_configuration_ready(self) -> bool:
        """Return whether formal authentication replay encryption is KMS-bound."""

        return (
            self.auth_idempotency_encryption_provider == "aliyun_kms"
            and _is_configured_secret(self.auth_idempotency_kms_key_id, min_length=3)
        )

    def material_request_contact_kms_configuration_ready(self) -> bool:
        """Return whether the write-only contact envelope is KMS-bound."""

        return (
            self.material_request_contact_encryption_provider == "aliyun_kms"
            and _is_configured_secret(
                self.material_request_contact_kms_key_id,
                min_length=3,
            )
        )

    def kms_envelope_configuration_ready(self) -> bool:
        """Return whether ciphertext-only KMS runtime coordinates are present."""

        endpoint = self.kms_endpoint.strip().lower()
        region = self.kms_region.strip().lower()
        registry_path = self.kms_encrypted_data_key_registry_path.strip()
        return bool(
            endpoint.endswith(".aliyuncs.com")
            and "://" not in endpoint
            and "/" not in endpoint
            and region
            and registry_path.startswith("/")
            and not any(
                marker in f"{endpoint}\0{region}\0{registry_path}".lower()
                for marker in SECRET_PLACEHOLDER_MARKERS
            )
        )

    def file_storage_configuration_ready(self) -> bool:
        """Return whether private OSS coordinates are canonical and enabled."""

        region = self.file_storage_region.strip()
        bucket = self.file_storage_bucket.strip()
        return (
            self.file_storage_enabled
            and self.file_storage_provider == "aliyun_oss_v2"
            and OSS_REGION_PATTERN.fullmatch(region) is not None
            and OSS_BUCKET_PATTERN.fullmatch(bucket) is not None
            and _is_configured_secret(
                self.file_idempotency_hmac_secret,
                min_length=32,
            )
        )

    def edge_sync_allowed_source_set(self) -> set[str]:
        return {
            source.strip()
            for source in self.edge_sync_allowed_sources.split(",")
            if source.strip()
        }

    def trusted_proxy_ip_set(self) -> set[str]:
        """Return canonical, exact proxy IPs allowed to supply forwarded IPs."""

        trusted: set[str] = set()
        for raw_value in self.trusted_proxy_ips.split(","):
            value = raw_value.strip()
            if not value:
                continue
            try:
                parsed = ip_address(value)
            except ValueError as exc:
                raise ValueError(
                    "trusted_proxy_ips must contain only explicit IP addresses"
                ) from exc
            if parsed.is_unspecified or parsed.is_multicast:
                raise ValueError(
                    "trusted_proxy_ips cannot contain unspecified or multicast addresses"
                )
            trusted.add(parsed.compressed)
        return trusted

    def edge_sync_configuration_ready(self) -> bool:
        if not self.edge_sync_enabled or not _is_configured_secret(
            self.edge_sync_secret,
            min_length=32,
        ):
            return False
        if self.environment == "production":
            return bool(self.edge_sync_allowed_source_set()) and not (
                self.edge_sync_legacy_batches_enabled
            )
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
