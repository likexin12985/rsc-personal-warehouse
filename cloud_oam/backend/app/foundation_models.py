"""V1.0 stage-one foundation models.

The v0.9 ``users`` table retains its legacy fields for a later reviewed
compatibility cutover, while revision 0003 appends the formal person/account
boundary without inferring any historical identity mapping.  References to
legacy table identifiers therefore continue to use their current
``VARCHAR(36)`` representation.  Tables owned by this module use SQLAlchemy's
portable UUID type (native UUID on PostgreSQL and a SQLite-compatible
representation in tests).

These tables are infrastructure only.  Registering them in ORM metadata does
not authorize synchronization, migration, notification, or any external write.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


UUID_TYPE = Uuid(as_uuid=True)
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")
QUANTITY = Numeric(18, 3)


def _lower_hex_remainder(expression: str) -> str:
    for character in "0123456789abcdef":
        expression = f"replace({expression}, '{character}', '')"
    return expression


def uuid4_value() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SourceSystem(TimestampMixin, Base):
    __tablename__ = "source_systems"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('read_only', 'mirror_only')",
            name="ck_source_systems_mode",
        ),
        CheckConstraint("length(code) > 0", name="ck_source_systems_code_nonempty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(24), default="read_only")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    configuration_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict
    )


class SyncRun(TimestampMixin, Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_sync_runs_run_key"),
        CheckConstraint(
            "mode IN ('full', 'incremental', 'replay')",
            name="ck_sync_runs_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'receiving', 'validating', 'validated', "
            "'projecting', 'completed', 'failed', 'conflict', 'cancelled')",
            name="ck_sync_runs_status",
        ),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="ck_sync_runs_time_order",
        ),
        Index("ix_sync_runs_source_status", "source_system_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("source_systems.id"), index=True
    )
    run_key: Mapped[str] = mapped_column(String(160))
    scope_key: Mapped[str] = mapped_column(String(200), index=True)
    mode: Mapped[str] = mapped_column(String(24))
    watermark_from: Mapped[str | None] = mapped_column(String(250), nullable=True)
    watermark_to: Mapped[str | None] = mapped_column(String(250), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class SyncBatch(CreatedAtMixin, Base):
    __tablename__ = "sync_batches"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "entity_type", "sequence", name="uq_sync_batches_sequence"
        ),
        CheckConstraint("sequence > 0", name="ck_sync_batches_sequence_positive"),
        CheckConstraint(
            "record_count >= 0", name="ck_sync_batches_record_count_nonnegative"
        ),
        CheckConstraint(
            "status IN ('receiving', 'received', 'validated', 'rejected', 'applied')",
            name="ck_sync_batches_status",
        ),
        Index("ix_sync_batches_run_status", "run_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    record_count: Mapped[int] = mapped_column(Integer)
    body_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="receiving", index=True)
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SyncInboxEvent(CreatedAtMixin, Base):
    __tablename__ = "sync_inbox_events"
    __table_args__ = (
        UniqueConstraint(
            "source_system_id",
            "external_event_id",
            "payload_sha256",
            name="uq_sync_inbox_events_id_hash",
        ),
        CheckConstraint(
            "status IN ('staged', 'validated', 'applied', 'conflict', 'rejected')",
            name="ck_sync_inbox_events_status",
        ),
        Index(
            "ix_sync_inbox_events_external_object",
            "source_system_id",
            "entity_type",
            "external_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("sync_batches.id", ondelete="CASCADE"), index=True
    )
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("source_systems.id"), index=True
    )
    external_event_id: Mapped[str] = mapped_column(String(200), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str] = mapped_column(String(250))
    source_version: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="staged", index=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExternalObject(TimestampMixin, Base):
    __tablename__ = "external_objects"
    __table_args__ = (
        UniqueConstraint(
            "source_system_id",
            "entity_type",
            "external_id",
            name="uq_external_objects_source_identity",
        ),
        Index("ix_external_objects_entity_type", "entity_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("source_systems.id"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str] = mapped_column(String(250))
    # Deliberately no FK: adding one would create a DDL cycle with versions and
    # make a portable SQLite migration impossible. The version row owns the FK.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExternalObjectVersion(CreatedAtMixin, Base):
    __tablename__ = "external_object_versions"
    __table_args__ = (
        UniqueConstraint(
            "external_object_id",
            "source_version",
            "payload_sha256",
            name="uq_external_object_versions_version_hash",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_external_object_versions_validity",
        ),
        Index(
            "uq_external_object_versions_current",
            "external_object_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
        Index(
            "ix_external_object_versions_source_time",
            "external_object_id",
            "source_updated_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    external_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("external_objects.id", ondelete="CASCADE"), index=True
    )
    source_version: Mapped[str] = mapped_column(String(160))
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "org_type IN ('headquarters', 'region_company', 'department', "
            "'external_approval_org')",
            name="ck_organizations_org_type",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_organizations_status"
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_organizations_parent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    external_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("external_objects.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id"), nullable=True, index=True
    )
    org_type: Mapped[str] = mapped_column(String(32), index=True)
    province_code: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)


class Person(TimestampMixin, Base):
    __tablename__ = "people"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "employee_no", name="uq_people_org_employee_no"
        ),
        CheckConstraint(
            "employment_status IN ('active', 'inactive', 'left', 'restricted')",
            name="ck_people_employment_status",
        ),
        Index("ix_people_org_status", "organization_id", "employment_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    external_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("external_objects.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id"), index=True
    )
    employee_no: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    mobile_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    mobile_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    employment_status: Mapped[str] = mapped_column(
        String(24), default="active", index=True
    )
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileObject(CreatedAtMixin, Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_files_size_nonnegative"),
        CheckConstraint(
            "status IN ('pending', 'available', 'quarantined', 'deleted')",
            name="ck_files_status",
        ),
        Index("ix_files_sha256", "sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(160))
    original_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)


class AuthIdentity(TimestampMixin, Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint(
            "identity_type",
            "provider_key",
            "identifier_hash",
            name="uq_auth_identities_identifier",
        ),
        UniqueConstraint(
            "user_id",
            "identity_type",
            "provider_key",
            name="uq_auth_identities_user_type_provider",
        ),
        CheckConstraint(
            "identity_type IN ('mobile', 'wechat_openid', 'wechat_unionid')",
            name="ck_auth_identities_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'revoked')",
            name="ck_auth_identities_status",
        ),
        CheckConstraint(
            "hash_version > 0",
            name="ck_auth_identities_hash_version_positive",
        ),
        CheckConstraint(
            "(status = 'pending' AND verified_at IS NULL AND revoked_at IS NULL) "
            "OR (status = 'active' AND verified_at IS NOT NULL AND revoked_at IS NULL) "
            "OR (status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_auth_identities_status_timestamps",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    identity_type: Mapped[str] = mapped_column(String(24), index=True)
    provider_key: Mapped[str] = mapped_column(String(100), default="")
    identifier_hash: Mapped[str] = mapped_column(String(64), index=True)
    hash_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LoginChallenge(CreatedAtMixin, Base):
    __tablename__ = "login_challenges"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_login_challenges_idempotency"),
        CheckConstraint(
            "purpose IN ('login', 'bind_identity', 'step_up')",
            name="ck_login_challenges_purpose",
        ),
        CheckConstraint(
            "status IN ('pending', 'verified', 'consumed', 'expired', 'locked', 'cancelled')",
            name="ck_login_challenges_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="ck_login_challenges_attempts",
        ),
        CheckConstraint(
            "client_type IN ('web', 'miniprogram', 'legacy_unknown')",
            name="ck_login_challenges_client_type",
        ),
        CheckConstraint(
            "(verification_mode = 'local_hash' AND code_hash IS NOT NULL "
            "AND length(code_hash) > 0) OR "
            "(verification_mode = 'provider_managed' AND code_hash IS NULL) OR "
            "verification_mode = 'legacy_unknown'",
            name="ck_login_challenges_verification_material",
        ),
        Index("ix_login_challenges_mobile_created", "mobile_hash", "created_at"),
        Index(
            "ix_login_challenges_requested_ip_created",
            "requested_ip_hash",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    mobile_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verification_mode: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(
        String(40), default="legacy_unknown", server_default="legacy_unknown"
    )
    provider_reference: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    client_type: Mapped[str] = mapped_column(
        String(24), default="legacy_unknown", server_default="legacy_unknown"
    )
    purpose: Mapped[str] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    requested_ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthRefreshToken(CreatedAtMixin, Base):
    __tablename__ = "auth_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_refresh_tokens_hash"),
        UniqueConstraint(
            "replaced_by_id", name="uq_auth_refresh_tokens_replaced_by_id"
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at",
            name="ck_auth_refresh_tokens_consumed_order",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_auth_refresh_tokens_revoked_order",
        ),
        CheckConstraint(
            "replaced_by_id IS NULL OR (replaced_by_id <> id AND consumed_at IS NOT NULL)",
            name="ck_auth_refresh_tokens_replacement",
        ),
        Index(
            "ix_auth_refresh_tokens_session_active",
            "session_id",
            "consumed_at",
            "revoked_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("auth_refresh_tokens.id"),
        nullable=True,
    )


class AuthIdempotencyOperation(TimestampMixin, Base):
    """Encrypted replay evidence for formal passwordless authentication writes.

    The ledger stores only domain-separated hashes/HMACs and an authenticated
    ciphertext envelope.  It deliberately has no plaintext response or request
    payload column.  Expiration is evaluated from ``expires_at``; an expired key
    is not silently recycled into a new operation.
    """

    __tablename__ = "auth_idempotency_operations"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_auth_idempotency_operations_key_hash",
        ),
        CheckConstraint(
            "operation_type IN ('sms_login', 'wechat_login', "
            "'session_refresh', 'session_logout')",
            name="ck_auth_idempotency_operations_type",
        ),
        CheckConstraint(
            "client_type IN ('web', 'miniprogram')",
            name="ck_auth_idempotency_operations_client_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_auth_idempotency_operations_status",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND "
            "length(scope_hash) = 64 AND length(request_hmac) = 64",
            name="ck_auth_idempotency_operations_request_hashes",
        ),
        CheckConstraint(
            "(status = 'pending' AND response_ciphertext IS NULL AND "
            "response_nonce IS NULL AND response_sha256 IS NULL AND "
            "encryption_key_version IS NULL AND http_status IS NULL AND "
            "completed_at IS NULL) OR "
            "(status IN ('completed', 'failed') AND "
            "response_ciphertext IS NOT NULL AND "
            "length(response_ciphertext) > 0 AND response_nonce IS NOT NULL "
            "AND length(response_nonce) = 12 AND response_sha256 IS NOT NULL "
            "AND length(response_sha256) = 64 AND "
            "encryption_key_version IS NOT NULL AND "
            "encryption_key_version > 0 AND http_status IS NOT NULL AND "
            "((status = 'completed' AND http_status BETWEEN 200 AND 299) OR "
            "(status = 'failed' AND http_status BETWEEN 400 AND 599)) AND "
            "completed_at IS NOT NULL)",
            name="ck_auth_idempotency_operations_response_evidence",
        ),
        CheckConstraint(
            "status = 'completed' OR output_refresh_token_id IS NULL",
            name="ck_auth_idempotency_operations_output_token_status",
        ),
        CheckConstraint(
            "expires_at > created_at AND "
            "(completed_at IS NULL OR completed_at >= created_at)",
            name="ck_auth_idempotency_operations_time_order",
        ),
        Index(
            "ix_auth_idempotency_operations_scope_status_expires",
            "scope_hash",
            "status",
            "expires_at",
        ),
        Index(
            "ix_auth_idempotency_operations_status_expires",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    operation_type: Mapped[str] = mapped_column(String(32))
    client_type: Mapped[str] = mapped_column(String(24))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    scope_hash: Mapped[str] = mapped_column(String(64))
    request_hmac: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    response_ciphertext: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    response_nonce: Mapped[bytes | None] = mapped_column(
        LargeBinary(12), nullable=True
    )
    response_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    encryption_key_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auth_session_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("auth_sessions.id"), nullable=True
    )
    input_refresh_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("auth_refresh_tokens.id"), nullable=True
    )
    output_refresh_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("auth_refresh_tokens.id"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthLoginRateLimitBucket(TimestampMixin, Base):
    """Short-lived, pseudonymous counters for formal login admission.

    ``scope_hmac`` is produced with a dedicated, domain-separated server HMAC
    key.  The table never stores an IP address, mobile number, WeChat code,
    openid, unionid, or any other provider credential in plaintext.
    """

    __tablename__ = "auth_login_rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "operation_type",
            "scope_type",
            "hash_version",
            "scope_hmac",
            "window_started_at",
            "window_seconds",
            name="uq_auth_login_rate_limit_bucket_window",
        ),
        CheckConstraint(
            "operation_type IN ('sms_login', 'wechat_login')",
            name="ck_auth_login_rate_limit_buckets_operation",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'ip', 'identity')",
            name="ck_auth_login_rate_limit_buckets_scope",
        ),
        CheckConstraint(
            "length(scope_hmac) = 64",
            name="ck_auth_login_rate_limit_buckets_scope_hmac",
        ),
        CheckConstraint(
            "hash_version > 0",
            name="ck_auth_login_rate_limit_buckets_hash_version",
        ),
        CheckConstraint(
            "window_seconds BETWEEN 10 AND 3600",
            name="ck_auth_login_rate_limit_buckets_window_seconds",
        ),
        CheckConstraint(
            "request_count > 0",
            name="ck_auth_login_rate_limit_buckets_request_count",
        ),
        CheckConstraint(
            "expires_at > window_started_at AND cleanup_after > expires_at",
            name="ck_auth_login_rate_limit_buckets_expiry",
        ),
        Index(
            "ix_auth_login_rate_limit_buckets_cleanup_after",
            "cleanup_after",
        ),
        Index(
            "ix_auth_login_rate_limit_buckets_lookup",
            "operation_type",
            "scope_type",
            "hash_version",
            "scope_hmac",
            "window_started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    operation_type: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str] = mapped_column(String(24))
    hash_version: Mapped[int] = mapped_column(Integer)
    scope_hmac: Mapped[str] = mapped_column(String(64))
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_seconds: Mapped[int] = mapped_column(Integer)
    request_count: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleanup_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Role(TimestampMixin, Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint(
            "code IN ('admin', 'provincial_manager', 'technician', "
            "'star_headquarters_approver')",
            name="ck_roles_fixed_code",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_roles_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="active")


class Permission(TimestampMixin, Base):
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint(
            "resource", "action", "field_code", name="uq_permissions_definition"
        ),
        CheckConstraint("length(resource) > 0", name="ck_permissions_resource_nonempty"),
        CheckConstraint("length(action) > 0", name="ck_permissions_action_nonempty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    resource: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    field_code: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(String(300), default="")


class RolePermission(CreatedAtMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permissions_pair"
        ),
        CheckConstraint(
            "effect IN ('allow', 'deny')", name="ck_role_permissions_effect"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("roles.id", ondelete="CASCADE"), index=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("permissions.id", ondelete="CASCADE"), index=True
    )
    effect: Mapped[str] = mapped_column(String(12), default="allow")


class RoleAssignment(TimestampMixin, Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "scope_type",
            "scope_id",
            "valid_from",
            name="uq_role_assignments_identity_scope_start",
        ),
        CheckConstraint(
            "scope_type IN ('national', 'organization', 'warehouse', 'person', 'document')",
            name="ck_role_assignments_scope_type",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'active', 'revoked', 'expired')",
            name="ck_role_assignments_status",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_role_assignments_validity",
        ),
        CheckConstraint(
            "(scope_type = 'national' AND scope_id = '*') OR "
            "(scope_type <> 'national' AND length(scope_id) > 0)",
            name="ck_role_assignments_scope_binding",
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL) "
            "OR (status <> 'revoked' AND revoked_at IS NULL AND revoked_by IS NULL)",
            name="ck_role_assignments_revocation_state",
        ),
        Index("ix_role_assignments_user_active", "user_id", "status", "valid_to"),
        Index(
            "uq_role_assignments_current_scope",
            "user_id",
            "role_id",
            "scope_type",
            "scope_id",
            unique=True,
            postgresql_where=text("status IN ('scheduled', 'active')"),
            sqlite_where=text("status IN ('scheduled', 'active')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("roles.id"), index=True
    )
    scope_type: Mapped[str] = mapped_column(String(24))
    scope_id: Mapped[str] = mapped_column(String(80), default="*")
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)
    assigned_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")


class ApprovalDelegation(TimestampMixin, Base):
    __tablename__ = "approval_delegations"
    __table_args__ = (
        UniqueConstraint(
            "from_user_id",
            "to_user_id",
            "scope_hash",
            "valid_from",
            name="uq_approval_delegations_scope_start",
        ),
        CheckConstraint(
            "from_user_id <> to_user_id", name="ck_approval_delegations_distinct_users"
        ),
        CheckConstraint(
            "valid_to > valid_from", name="ck_approval_delegations_validity"
        ),
        CheckConstraint(
            "status IN ('scheduled', 'active', 'revoked', 'expired')",
            name="ck_approval_delegations_status",
        ),
        Index(
            "ix_approval_delegations_from_active",
            "from_user_id",
            "status",
            "valid_to",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    from_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    to_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    scope_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    scope_hash: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("files.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="scheduled", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    revoked_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExternalObjectMapping(TimestampMixin, Base):
    __tablename__ = "external_object_mappings"
    __table_args__ = (
        UniqueConstraint(
            "external_object_id",
            "local_object_type",
            "local_object_id",
            name="uq_external_object_mappings_target",
        ),
        CheckConstraint(
            "status IN ('proposed', 'approved', 'rejected', 'superseded')",
            name="ck_external_object_mappings_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    external_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("external_objects.id", ondelete="CASCADE"), index=True
    )
    local_object_type: Mapped[str] = mapped_column(String(80))
    local_object_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, default="")


class SyncConflict(TimestampMixin, Base):
    __tablename__ = "sync_conflicts"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_sync_conflicts_dedup_key"),
        CheckConstraint(
            "inbox_event_id IS NOT NULL OR external_object_id IS NOT NULL",
            name="ck_sync_conflicts_object_reference",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'ignored')",
            name="ck_sync_conflicts_status",
        ),
        Index("ix_sync_conflicts_status_type", "status", "conflict_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("sync_runs.id"), nullable=True, index=True
    )
    inbox_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("sync_inbox_events.id"), nullable=True, index=True
    )
    external_object_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("external_objects.id"), nullable=True, index=True
    )
    dedup_key: Mapped[str] = mapped_column(String(160))
    conflict_type: Mapped[str] = mapped_column(String(80), index=True)
    external_value_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    local_value_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    resolution_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReconciliationRun(TimestampMixin, Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        UniqueConstraint("run_key", name="uq_reconciliation_runs_run_key"),
        CheckConstraint(
            "status IN ('pending', 'running', 'matched', 'differences', 'failed', 'approved')",
            name="ck_reconciliation_runs_status",
        ),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="ck_reconciliation_runs_time_order",
        ),
        Index(
            "ix_reconciliation_runs_source_status", "source_system_id", "status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    run_key: Mapped[str] = mapped_column(String(160))
    source_system_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("source_systems.id"), index=True
    )
    scope: Mapped[str] = mapped_column(String(200))
    external_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    local_ledger_cursor: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    summary_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReconciliationItem(TimestampMixin, Base):
    __tablename__ = "reconciliation_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "business_key", name="uq_reconciliation_items_business_key"
        ),
        CheckConstraint(
            "external_qty >= 0 AND local_qty >= 0",
            name="ck_reconciliation_items_quantities_nonnegative",
        ),
        CheckConstraint(
            "difference = external_qty - local_qty",
            name="ck_reconciliation_items_difference",
        ),
        CheckConstraint(
            "status IN ('matched', 'difference', 'explained', 'resolved')",
            name="ck_reconciliation_items_status",
        ),
        Index("ix_reconciliation_items_run_status", "run_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True
    )
    business_key: Mapped[str] = mapped_column(String(300))
    external_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    local_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    difference: Mapped[Decimal] = mapped_column(QUANTITY)
    status: Mapped[str] = mapped_column(String(20), index=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    evidence_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("files.id"), nullable=True
    )


class OpeningControlReconciliationRun(TimestampMixin, Base):
    """Formal opening-control binding for a generic reconciliation run.

    The generic run remains a mutable state projection.  This extension keeps
    the immutable stocktake/source coordinates and the authorization snapshots
    required to prove that an approved run covers one exact posted opening
    task.  It never changes an opening establishment or inventory fact.
    """

    __tablename__ = "opening_control_reconciliation_runs"
    __table_args__ = (
        PrimaryKeyConstraint(
            "run_id", name="pk_opening_control_reconciliation_runs"
        ),
        UniqueConstraint(
            "task_id", name="uq_opening_control_reconciliation_runs_task"
        ),
        UniqueConstraint(
            "run_id",
            "task_id",
            "round_id",
            name="uq_opening_control_reconciliation_runs_binding",
        ),
        ForeignKeyConstraint(
            ["run_id"],
            ["reconciliation_runs.id"],
            name="fk_opening_control_reconciliation_runs_run",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["create_command_id", "run_id"],
            ["reconciliation_commands.id", "reconciliation_commands.run_id"],
            name="fk_opening_control_reconciliation_runs_create_command",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["task_id"],
            ["stocktake_tasks.id"],
            name="fk_opening_control_reconciliation_runs_task",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["round_id", "task_id"],
            ["stocktake_rounds.id", "stocktake_rounds.task_id"],
            name="fk_opening_control_reconciliation_runs_round",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["posting_id", "task_id", "round_id"],
            [
                "stocktake_postings.id",
                "stocktake_postings.task_id",
                "stocktake_postings.round_id",
            ],
            name="fk_opening_control_reconciliation_runs_posting",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version >= 0 AND item_count > 0",
            name="ck_opening_control_reconciliation_runs_counts",
        ),
        CheckConstraint(
            "length(item_manifest_sha256) = 64 AND "
            f"length({_lower_hex_remainder('item_manifest_sha256')}) = 0",
            name="ck_opening_control_reconciliation_runs_manifest",
        ),
        CheckConstraint(
            "created_authorization_version > 0 AND "
            "(approved_authorization_version IS NULL OR "
            "approved_authorization_version > 0)",
            name="ck_opening_control_reconciliation_runs_auth_versions",
        ),
        CheckConstraint(
            "(approved_by_user_id IS NULL AND approved_by_person_id IS NULL "
            "AND approved_role_assignment_id IS NULL "
            "AND approved_authorization_version IS NULL "
            "AND approved_at IS NULL) OR "
            "(approved_by_user_id IS NOT NULL "
            "AND approved_by_person_id IS NOT NULL "
            "AND approved_role_assignment_id IS NOT NULL "
            "AND approved_authorization_version IS NOT NULL "
            "AND approved_at IS NOT NULL)",
            name="ck_opening_control_reconciliation_runs_approval_group",
        ),
        CheckConstraint(
            "approved_at IS NULL OR approved_at >= created_at",
            name="ck_opening_control_reconciliation_runs_time_order",
        ),
        Index(
            "ix_opening_control_reconciliation_runs_region",
            "region_org_id",
            "created_at",
        ),
        Index(
            "ix_opening_control_reconciliation_runs_posting", "posting_id"
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    create_command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    region_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    posting_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    control_sync_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("sync_runs.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    item_count: Mapped[int] = mapped_column(Integer)
    item_manifest_sha256: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    created_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    created_authorization_version: Mapped[int] = mapped_column(BigInteger)
    approved_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    approved_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    approved_role_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("role_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approved_authorization_version: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_comment: Mapped[str] = mapped_column(Text, default="")


class OpeningControlReconciliationItem(TimestampMixin, Base):
    """Exact immutable-source binding for one control-only difference item."""

    __tablename__ = "opening_control_reconciliation_items"
    __table_args__ = (
        PrimaryKeyConstraint(
            "item_id", name="pk_opening_control_reconciliation_items"
        ),
        UniqueConstraint(
            "difference_id",
            name="uq_opening_control_reconciliation_items_difference",
        ),
        ForeignKeyConstraint(
            ["item_id"],
            ["reconciliation_items.id"],
            name="fk_opening_control_reconciliation_items_item",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["run_id", "task_id", "round_id"],
            [
                "opening_control_reconciliation_runs.run_id",
                "opening_control_reconciliation_runs.task_id",
                "opening_control_reconciliation_runs.round_id",
            ],
            name="fk_opening_control_reconciliation_items_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["difference_id", "task_id", "round_id"],
            [
                "stocktake_differences.id",
                "stocktake_differences.task_id",
                "stocktake_differences.round_id",
            ],
            name="fk_opening_control_reconciliation_items_difference",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["control_snapshot_line_id", "task_id"],
            [
                "stocktake_control_snapshot_lines.id",
                "stocktake_control_snapshot_lines.task_id",
            ],
            name="fk_opening_control_reconciliation_items_control",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version >= 0",
            name="ck_opening_control_reconciliation_items_version",
        ),
        CheckConstraint(
            "length(evidence_reference) <= 1000",
            name="ck_opening_control_reconciliation_items_evidence_length",
        ),
        CheckConstraint(
            "(evidence_file_sha256 IS NULL "
            "AND evidence_file_size_bytes IS NULL "
            "AND evidence_file_mime_type IS NULL) OR "
            "(evidence_file_sha256 IS NOT NULL "
            "AND length(evidence_file_sha256) = 64 "
            "AND evidence_file_size_bytes IS NOT NULL "
            "AND evidence_file_size_bytes >= 0 "
            "AND evidence_file_mime_type IS NOT NULL "
            "AND length(trim(evidence_file_mime_type)) > 0)",
            name="ck_opening_control_reconciliation_items_file_snapshot",
        ),
        CheckConstraint(
            "explanation_authorization_version IS NULL OR "
            "explanation_authorization_version > 0",
            name="ck_opening_control_reconciliation_items_auth_version",
        ),
        CheckConstraint(
            "(explained_by_user_id IS NULL AND explained_by_person_id IS NULL "
            "AND explained_role_assignment_id IS NULL "
            "AND explanation_authorization_version IS NULL "
            "AND explained_at IS NULL) OR "
            "(explained_by_user_id IS NOT NULL "
            "AND explained_by_person_id IS NOT NULL "
            "AND explained_role_assignment_id IS NOT NULL "
            "AND explanation_authorization_version IS NOT NULL "
            "AND explained_at IS NOT NULL)",
            name="ck_opening_control_reconciliation_items_explanation_group",
        ),
        CheckConstraint(
            "explained_at IS NULL OR explained_at >= created_at",
            name="ck_opening_control_reconciliation_items_time_order",
        ),
        Index(
            "ix_opening_control_reconciliation_items_run", "run_id", "item_id"
        ),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    round_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    difference_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    control_snapshot_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    version: Mapped[int] = mapped_column(BigInteger, default=0)
    evidence_reference: Mapped[str] = mapped_column(String(1000), default="")
    evidence_file_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    evidence_file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    evidence_file_mime_type: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    explained_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    explained_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    explained_role_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("role_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    explanation_authorization_version: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    explained_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OpeningControlReconciliationCommandConsumption(Base):
    """Append-only commit seal proving one command was consumed exactly once."""

    __tablename__ = "opening_control_reconciliation_command_consumptions"
    __table_args__ = (
        PrimaryKeyConstraint(
            "command_id",
            name="pk_opening_control_reconciliation_command_consumptions",
        ),
        UniqueConstraint(
            "command_id",
            "run_id",
            "operation",
            "target_version",
            name="uq_opening_control_reconciliation_consumptions_command",
        ),
        UniqueConstraint(
            "run_id",
            "target_version",
            name="uq_opening_control_reconciliation_consumptions_target",
        ),
        CheckConstraint(
            "operation IN ('create_opening', 'explain_opening', "
            "'approve_opening')",
            name="ck_opening_control_reconciliation_consumptions_operation",
        ),
        CheckConstraint(
            "target_version >= 0",
            name="ck_opening_control_reconciliation_consumptions_target_version",
        ),
    )

    command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    operation: Mapped[str] = mapped_column(String(32))
    target_version: Mapped[int] = mapped_column(BigInteger)
    consumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReconciliationCommand(CreatedAtMixin, Base):
    """Append-only accepted command and exact replay evidence."""

    __tablename__ = "reconciliation_commands"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_reconciliation_commands"),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_reconciliation_commands_idempotency",
        ),
        UniqueConstraint(
            "id", "run_id", name="uq_reconciliation_commands_id_run"
        ),
        UniqueConstraint(
            "id",
            "run_id",
            "operation",
            "target_version",
            name="uq_reconciliation_commands_consumption_binding",
        ),
        UniqueConstraint(
            "run_id",
            "target_version",
            name="uq_reconciliation_commands_target",
        ),
        ForeignKeyConstraint(
            ["id", "run_id", "operation", "target_version"],
            [
                "opening_control_reconciliation_command_consumptions.command_id",
                "opening_control_reconciliation_command_consumptions.run_id",
                "opening_control_reconciliation_command_consumptions.operation",
                "opening_control_reconciliation_command_consumptions.target_version",
            ],
            name="fk_reconciliation_commands_consumption",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(
            "operation IN ('create_opening', 'explain_opening', "
            "'approve_opening')",
            name="ck_reconciliation_commands_operation",
        ),
        CheckConstraint(
            "length(request_reference) = "
            f"{len('opening-reconciliation-request-') + 64} AND "
            "substr(request_reference, 1, "
            f"{len('opening-reconciliation-request-')}) = "
            "'opening-reconciliation-request-' AND "
            f"length({_lower_hex_remainder('substr(request_reference, ' + str(len('opening-reconciliation-request-') + 1) + ')')}) = 0",
            name="ck_reconciliation_commands_request_reference",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND "
            "length(request_hash) = 64 AND length(result_hash) = 64 AND "
            f"length({_lower_hex_remainder('idempotency_key_hash')}) = 0 AND "
            f"length({_lower_hex_remainder('request_hash')}) = 0 AND "
            f"length({_lower_hex_remainder('result_hash')}) = 0",
            name="ck_reconciliation_commands_hashes",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_reconciliation_commands_authorization_version",
        ),
        CheckConstraint(
            "target_version >= 0",
            name="ck_reconciliation_commands_target_version",
        ),
        CheckConstraint(
            "occurred_at = created_at",
            name="ck_reconciliation_commands_time_binding",
        ),
        Index(
            "uq_reconciliation_commands_create_run",
            "run_id",
            unique=True,
            postgresql_where=text("operation = 'create_opening'"),
            sqlite_where=text("operation = 'create_opening'"),
        ),
        Index(
            "uq_reconciliation_commands_approve_run",
            "run_id",
            unique=True,
            postgresql_where=text("operation = 'approve_opening'"),
            sqlite_where=text("operation = 'approve_opening'"),
        ),
        Index(
            "ix_reconciliation_commands_run_time", "run_id", "occurred_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, default=uuid4_value
    )
    operation: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("reconciliation_runs.id", ondelete="RESTRICT"),
    )
    target_version: Mapped[int] = mapped_column(BigInteger)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_reference: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_hash: Mapped[str] = mapped_column(String(64))
    request_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    result_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    actor_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MigrationBatch(TimestampMixin, Base):
    __tablename__ = "migration_batches"
    __table_args__ = (
        UniqueConstraint("batch_no", name="uq_migration_batches_batch_no"),
        CheckConstraint(
            "source_count >= 0 AND target_count >= 0",
            name="ck_migration_batches_counts_nonnegative",
        ),
        CheckConstraint(
            "cutoff_to IS NULL OR cutoff_from IS NULL OR cutoff_to >= cutoff_from",
            name="ck_migration_batches_cutoff_order",
        ),
        CheckConstraint(
            "status IN ('planned', 'validating', 'validated', 'importing', "
            "'completed', 'failed', 'rolled_back')",
            name="ck_migration_batches_status",
        ),
        CheckConstraint("attempt_no > 0", name="ck_migration_batches_attempt_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    batch_no: Mapped[str] = mapped_column(String(100), index=True)
    cutoff_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cutoff_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    source_count: Mapped[int] = mapped_column(BigInteger, default=0)
    target_count: Mapped[int] = mapped_column(BigInteger, default=0)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MigrationError(TimestampMixin, Base):
    __tablename__ = "migration_errors"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "source_key",
            "error_code",
            name="uq_migration_errors_source_error",
        ),
        CheckConstraint(
            "resolution_status IN ('open', 'resolved', 'ignored')",
            name="ck_migration_errors_resolution_status",
        ),
        Index(
            "ix_migration_errors_batch_status", "batch_id", "resolution_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("migration_batches.id", ondelete="CASCADE"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(300))
    error_code: Mapped[str] = mapped_column(String(80), index=True)
    message: Mapped[str] = mapped_column(Text)
    source_payload_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    resolution_status: Mapped[str] = mapped_column(
        String(20), default="open", index=True
    )
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DocumentAttachment(CreatedAtMixin, Base):
    __tablename__ = "document_attachments"
    __table_args__ = (
        UniqueConstraint(
            "document_type",
            "document_id",
            "file_id",
            "attachment_type",
            name="uq_document_attachments_binding",
        ),
        CheckConstraint(
            "status IN ('active', 'removed')",
            name="ck_document_attachments_status",
        ),
        Index(
            "ix_document_attachments_document", "document_type", "document_id"
        ),
        Index(
            "uq_document_attachments_stocktake_evidence_file_0036",
            "file_id",
            unique=True,
            postgresql_where=text(
                "document_type = 'stocktake_scope_count_completion' AND "
                "attachment_type = 'stocktake_evidence'"
            ),
            sqlite_where=text(
                "document_type = 'stocktake_scope_count_completion' AND "
                "attachment_type = 'stocktake_evidence'"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    document_type: Mapped[str] = mapped_column(String(80))
    document_id: Mapped[str] = mapped_column(String(64))
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("files.id"), index=True
    )
    attachment_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="active")
    uploaded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )


class NotificationEvent(CreatedAtMixin, Base):
    __tablename__ = "notification_events"
    __table_args__ = (
        UniqueConstraint("dedup_key", name="uq_notification_events_dedup_key"),
        CheckConstraint(
            "status IN ('pending', 'expanded', 'cancelled')",
            name="ck_notification_events_status",
        ),
        Index(
            "ix_notification_events_business", "business_type", "business_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    business_type: Mapped[str] = mapped_column(String(80))
    business_id: Mapped[str] = mapped_column(String(64))
    dedup_key: Mapped[str] = mapped_column(String(200))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationRecipient(CreatedAtMixin, Base):
    __tablename__ = "notification_recipients"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "channel",
            "recipient_key",
            name="uq_notification_recipients_event_channel_key",
        ),
        CheckConstraint(
            "channel IN ('wechat', 'sms', 'feishu')",
            name="ck_notification_recipients_channel",
        ),
        CheckConstraint(
            "status IN ('active', 'suppressed')",
            name="ck_notification_recipients_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("notification_events.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), index=True)
    recipient_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("recipient_id", name="uq_notification_deliveries_recipient"),
        UniqueConstraint("delivery_key", name="uq_notification_deliveries_key"),
        CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'delivered', 'read', "
            "'failed', 'cancelled')",
            name="ck_notification_deliveries_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_deliveries_attempts"),
        Index(
            "ix_notification_deliveries_status_created", "status", "created_at"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("notification_recipients.id", ondelete="CASCADE")
    )
    delivery_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(
        String(250), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationAttempt(CreatedAtMixin, Base):
    __tablename__ = "notification_attempts"
    __table_args__ = (
        UniqueConstraint(
            "delivery_id", "attempt_no", name="uq_notification_attempts_number"
        ),
        CheckConstraint("attempt_no > 0", name="ck_notification_attempts_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("notification_deliveries.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    response_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RobotInboundEvent(CreatedAtMixin, Base):
    __tablename__ = "robot_inbound_events"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_robot_inbound_events_provider_id"
        ),
        CheckConstraint(
            "status IN ('received', 'verified', 'processed', 'rejected', 'failed')",
            name="ck_robot_inbound_events_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    provider: Mapped[str] = mapped_column(String(40), default="feishu")
    provider_event_id: Mapped[str] = mapped_column(String(250), index=True)
    sender_id: Mapped[str] = mapped_column(String(250), index=True)
    action: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    response_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OutboxEvent(TimestampMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_events_idempotency"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'published', 'failed', 'dead_letter')",
            name="ck_outbox_events_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts"),
        Index("ix_outbox_events_dispatch", "status", "available_at", "created_at"),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEvent(CreatedAtMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("event_hash", name="uq_audit_events_event_hash"),
        UniqueConstraint(
            "stream_key",
            "stream_version",
            name="uq_audit_events_stream_version_0017",
        ),
        CheckConstraint(
            "stream_key IN ('authorization', 'authentication', 'inventory', "
            "'material_request')",
            name="ck_audit_events_stream_key_0017",
        ),
        CheckConstraint(
            "stream_version > 0",
            name="ck_audit_events_stream_version_0017",
        ),
        Index("ix_audit_events_aggregate", "aggregate_type", "aggregate_id"),
        Index("ix_audit_events_request_id", "request_id"),
        Index(
            "uq_audit_events_material_request_request_id_0039",
            "request_id",
            unique=True,
            postgresql_where=text(
                "stream_key = 'material_request' AND action IN "
                "('material_request.withdraw', 'material_request.cancel')"
            ),
            sqlite_where=text(
                "stream_key = 'material_request' AND action IN "
                "('material_request.withdraw', 'material_request.cancel')"
            ),
        ),
        Index("ix_audit_events_occurred_at", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    stream_key: Mapped[str] = mapped_column(
        String(160),
        ForeignKey(
            "audit_chain_heads.stream_key",
            name="fk_audit_events_stream_key_0017",
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        ),
    )
    stream_version: Mapped[int] = mapped_column(BigInteger)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    before_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    after_jsonb: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT, nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(160))
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditChainHead(TimestampMixin, Base):
    __tablename__ = "audit_chain_heads"
    __table_args__ = (
        UniqueConstraint(
            "last_event_id", name="uq_audit_chain_heads_last_event_id"
        ),
        CheckConstraint("version >= 0", name="ck_audit_chain_heads_version"),
        CheckConstraint(
            "(last_event_id IS NULL AND last_hash IS NULL) OR "
            "(last_event_id IS NOT NULL AND last_hash IS NOT NULL)",
            name="ck_audit_chain_heads_last_event_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    stream_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    last_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("audit_events.id"), nullable=True
    )
    last_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(BigInteger, default=0)


class StateTransitionEvent(CreatedAtMixin, Base):
    __tablename__ = "state_transition_events"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_state_transition_events_idempotency"
        ),
        CheckConstraint(
            "from_status IS NULL OR from_status <> to_status",
            name="ck_state_transition_events_changed",
        ),
        Index(
            "ix_state_transition_events_aggregate",
            "aggregate_type",
            "aggregate_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT, default=dict)


class FileJob(TimestampMixin, Base):
    __tablename__ = "file_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_file_jobs_idempotency"),
        CheckConstraint(
            "job_type IN ('import', 'export', 'print')", name="ck_file_jobs_type"
        ),
        CheckConstraint(
            "status IN ('queued', 'prevalidating', 'awaiting_confirmation', "
            "'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_file_jobs_status",
        ),
        Index("ix_file_jobs_requester_status", "requested_by", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    job_type: Mapped[str] = mapped_column(String(20), index=True)
    requested_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    parameters_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    parameters_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(28), default="queued", index=True)
    result_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("files.id"), nullable=True
    )
    error_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("files.id"), nullable=True
    )
    confirmed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemParameter(TimestampMixin, Base):
    __tablename__ = "system_parameters"
    __table_args__ = (
        UniqueConstraint(
            "parameter_key", "version", name="uq_system_parameters_key_version"
        ),
        CheckConstraint("version > 0", name="ck_system_parameters_version_positive"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_system_parameters_validity",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'superseded', 'revoked')",
            name="ck_system_parameters_status",
        ),
        Index(
            "uq_system_parameters_one_active",
            "parameter_key",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    parameter_key: Mapped[str] = mapped_column(String(160), index=True)
    value_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
