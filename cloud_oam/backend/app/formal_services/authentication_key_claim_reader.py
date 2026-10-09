"""SELECT-only independent C1 claim/pin source for authentication replay.

No registry, wrapped ciphertext, response ciphertext, tokens or DEKs are read.
The production composition root must already have validated the API database
identity, immutable C1 catalog and deployment coordinates. This reader neither
repairs drift nor substitutes a registry when database evidence is unavailable.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..foundation_models import KmsDataKeyPin
from ..key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
from ..openbao_transit_candidate import OpenBaoKeyCoordinate, OpenBaoReviewedPin
from .authentication_idempotency import AuthenticationEncryptionKeyUnavailable
from .authentication_key_claims import (
    AUTHENTICATION_PURPOSE, AliyunAuthenticationKeyBinding,
    AuthenticationKeyBinding, OpenBaoAuthenticationKeyBinding,
)


class DatabaseAuthenticationClaimReader:
    """One bound query and detached typed result, with no ORM autoflush."""

    def __init__(
        self, db: Session, *, environment: str,
        openbao_provider_instance_id: str | None = None,
    ) -> None:
        valid = False
        try:
            if type(environment) is not str or environment not in {"development", "test", "staging", "production"}:
                raise ValueError("invalid environment")
            if openbao_provider_instance_id is not None:
                OpenBaoKeyCoordinate(AUTHENTICATION_PURPOSE, environment, openbao_provider_instance_id, 1)
            valid = isinstance(db, Session)
        except Exception:
            pass
        if not valid:
            raise AuthenticationEncryptionKeyUnavailable("authentication claim reader is unavailable")
        self._db = db
        self._environment = environment
        self._instance = openbao_provider_instance_id

    def __call__(self, purpose: str, application_key_version: int) -> AuthenticationKeyBinding:
        binding = None
        try:
            if (
                type(purpose) is not str or purpose != AUTHENTICATION_PURPOSE
                or type(application_key_version) is not int
                or not 1 <= application_key_version <= 2_147_483_647
            ):
                raise ValueError("invalid coordinate")
            claim, aliyun, bao = (
                ApplicationKeyVersionClaim.__table__, KmsDataKeyPin.__table__,
                OpenBaoDataKeyPin.__table__,
            )
            statement = select(
                claim.c.provider.label("provider"),
                claim.c.ciphertext_sha256.label("claim_sha"),
                claim.c.created_at.label("claim_created"),
                aliyun.c.kms_key_id.label("ali_key_id"),
                aliyun.c.kms_key_version_id.label("ali_version_id"),
                aliyun.c.ciphertext_sha256.label("ali_sha"),
                aliyun.c.created_at.label("ali_created"),
                bao.c.environment.label("bao_environment"),
                bao.c.provider_instance_id.label("bao_instance"),
                bao.c.key_path.label("bao_key_path"),
                bao.c.transit_key_version.label("bao_transit_version"),
                bao.c.ciphertext_sha256.label("bao_sha"),
                bao.c.context_sha256.label("bao_context_sha"),
                bao.c.associated_data_sha256.label("bao_aad_sha"),
                bao.c.created_at.label("bao_created"),
            ).select_from(claim.outerjoin(aliyun, and_(
                aliyun.c.purpose == claim.c.purpose,
                aliyun.c.application_key_version == claim.c.application_key_version,
            )).outerjoin(bao, and_(
                bao.c.purpose == claim.c.purpose,
                bao.c.application_key_version == claim.c.application_key_version,
            ))).where(
                claim.c.purpose == purpose,
                claim.c.application_key_version == application_key_version,
            ).limit(2)
            with self._db.no_autoflush:
                rows = self._db.execute(statement).mappings().all()
            if len(rows) != 1:
                raise ValueError("claim missing or ambiguous")
            row = rows[0]
            ali_fields = ("ali_key_id", "ali_version_id", "ali_sha", "ali_created")
            bao_fields = (
                "bao_environment", "bao_instance", "bao_key_path", "bao_transit_version",
                "bao_sha", "bao_context_sha", "bao_aad_sha", "bao_created",
            )
            if not isinstance(row["claim_created"], datetime):
                raise ValueError("claim time invalid")
            if row["provider"] == "aliyun_kms":
                if (
                    any(row[name] is not None for name in bao_fields)
                    or any(row[name] is None for name in ali_fields)
                    or row["claim_sha"] != row["ali_sha"]
                    or row["claim_created"] != row["ali_created"]
                ):
                    raise ValueError("legacy pin mismatch")
                binding = AliyunAuthenticationKeyBinding(
                    application_key_version, row["ali_key_id"], row["ali_version_id"], row["ali_sha"],
                )
            elif row["provider"] == "openbao_transit_v1":
                if (
                    any(row[name] is not None for name in ali_fields)
                    or any(row[name] is None for name in bao_fields)
                    or self._instance is None
                    or row["bao_environment"] != self._environment
                    or row["bao_instance"] != self._instance
                    or row["bao_key_path"] != "transit/keys/rsc-authentication-idempotency"
                    or row["claim_sha"] != row["bao_sha"]
                    or row["claim_created"] != row["bao_created"]
                ):
                    raise ValueError("OpenBao pin mismatch")
                binding = OpenBaoAuthenticationKeyBinding(OpenBaoReviewedPin(
                    OpenBaoKeyCoordinate(purpose, row["bao_environment"], row["bao_instance"], application_key_version),
                    row["bao_transit_version"], row["bao_sha"], row["bao_context_sha"], row["bao_aad_sha"],
                ))
        except Exception:
            binding = None
        if binding is None:
            raise AuthenticationEncryptionKeyUnavailable("authentication key claim is unavailable")
        return binding
