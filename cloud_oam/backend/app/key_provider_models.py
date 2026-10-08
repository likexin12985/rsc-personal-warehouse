"""Migration-owned provider bindings; no production provider selection.

Only non-secret, reviewed identities live here. Application versions are
shared across providers so historical envelopes cannot silently change key.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Integer, PrimaryKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def _remainder(expression: str, alphabet: str) -> str:
    for character in alphabet:
        expression = f"replace({expression}, '{character}', '')"
    return expression


def _sha_check(column: str, *, sqlite: bool = False) -> str:
    if sqlite:
        return (f"length({column}) = 64 AND {column} NOT GLOB '*[^0123456789abcdef]*'"
                f" AND instr({column}, char(0)) = 0")
    return f"length({column}) = 64 AND length({_remainder(column, '0123456789abcdef')}) = 0"


def _instance_check(*, sqlite: bool = False) -> str:
    prefix = "length(provider_instance_id) BETWEEN 3 AND 63 AND substr(provider_instance_id, 1, 1) <> '-' AND "
    if sqlite:
        return (prefix + "provider_instance_id NOT GLOB '*[^abcdefghijklmnopqrstuvwxyz0123456789-]*'"
                " AND instr(provider_instance_id, char(0)) = 0")
    return prefix + "length(" + _remainder("provider_instance_id", "abcdefghijklmnopqrstuvwxyz0123456789-") + ") = 0"


def _dialect_checks(postgresql: str, sqlite: str, *, name: str) -> tuple[CheckConstraint, CheckConstraint]:
    # Keep reviewed PG SQL intact. SQLite builds have different parser stack
    # limits; shallow GLOB checks also explicitly reject its NUL terminator.
    return (CheckConstraint(postgresql, name=name).ddl_if(dialect="postgresql"),
            CheckConstraint(sqlite, name=name).ddl_if(dialect="sqlite"))


class OpenBaoDataKeyPin(Base):
    __tablename__ = "openbao_data_key_pins"
    __table_args__ = (
        PrimaryKeyConstraint("purpose", "application_key_version", name="pk_openbao_data_key_pins_0180"),
        UniqueConstraint("ciphertext_sha256", name="uq_openbao_data_key_pins_ciphertext_0180"),
        CheckConstraint("purpose IN ('authentication_idempotency', 'material_request_contact')", name="ck_openbao_data_key_pins_purpose_0180"),
        CheckConstraint("environment IN ('development', 'test', 'staging', 'production')", name="ck_openbao_data_key_pins_environment_0180"),
        CheckConstraint("application_key_version BETWEEN 1 AND 2147483647 AND transit_key_version BETWEEN 1 AND 2147483647", name="ck_openbao_data_key_pins_versions_0180"),
        *_dialect_checks(_instance_check(), _instance_check(sqlite=True), name="ck_openbao_data_key_pins_instance_0180"),
        CheckConstraint("(purpose = 'authentication_idempotency' AND key_path = 'transit/keys/rsc-authentication-idempotency') OR (purpose = 'material_request_contact' AND key_path = 'transit/keys/rsc-material-request-contact')", name="ck_openbao_data_key_pins_key_path_0180"),
        *_dialect_checks(
            " AND ".join(_sha_check(column) for column in ("ciphertext_sha256", "context_sha256", "associated_data_sha256")),
            " AND ".join(_sha_check(column, sqlite=True) for column in ("ciphertext_sha256", "context_sha256", "associated_data_sha256")),
            name="ck_openbao_data_key_pins_hashes_0180"),
    )

    purpose: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16))
    provider_instance_id: Mapped[str] = mapped_column(String(63))
    key_path: Mapped[str] = mapped_column(String(128))
    application_key_version: Mapped[int] = mapped_column(Integer)
    transit_key_version: Mapped[int] = mapped_column(Integer)
    ciphertext_sha256: Mapped[str] = mapped_column(String(64))
    context_sha256: Mapped[str] = mapped_column(String(64))
    associated_data_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ApplicationKeyVersionClaim(Base):
    __tablename__ = "application_key_version_claims"
    __table_args__ = (
        PrimaryKeyConstraint("purpose", "application_key_version", name="pk_application_key_version_claims_0180"),
        CheckConstraint("purpose IN ('authentication_idempotency', 'material_request_contact')", name="ck_application_key_version_claims_purpose_0180"),
        CheckConstraint("provider IN ('aliyun_kms', 'openbao_transit_v1')", name="ck_application_key_version_claims_provider_0180"),
        CheckConstraint("application_key_version BETWEEN 1 AND 2147483647", name="ck_application_key_version_claims_version_0180"),
        *_dialect_checks(_sha_check("ciphertext_sha256"), _sha_check("ciphertext_sha256", sqlite=True), name="ck_application_key_version_claims_hash_0180"),
    )

    purpose: Mapped[str] = mapped_column(String(64))
    application_key_version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    ciphertext_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
