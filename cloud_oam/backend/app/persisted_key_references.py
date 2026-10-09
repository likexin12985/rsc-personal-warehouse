"""SELECT-only provider-aware catalog of keys still needed by stored records.

Only reference metadata and independent immutable claims/pins cross this
boundary. No business ciphertext, identity HMAC, nonce or plaintext is read.
This is not a decrypt/registry/production-readiness proof: callers must verify
the runtime DB identity/catalog and compare these pins with mounted material.
Full envelope shape and business AAD remain enforced by the existing guards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from sqlalchemy import String, and_, cast, func, select, tuple_, union
from sqlalchemy.orm import Session

from .demand_models import MaterialRequest, MaterialRequestRevision
from .foundation_models import AuthIdempotencyOperation, KmsDataKeyPin
from .key_provider_models import ApplicationKeyVersionClaim, OpenBaoDataKeyPin
from .formal_services.authentication_key_claims import AliyunAuthenticationKeyBinding
from .openbao_transit_candidate import OpenBaoKeyCoordinate, OpenBaoReviewedPin


AUTH = "authentication_idempotency"
CONTACT = "material_request_contact"
ALIYUN = "aliyun_kms"
OPENBAO = "openbao_transit_v1"
MAX_REFERENCES = 128
_METADATA = (
    "schema", "provider", "kms_key_id", "key_version", "purpose", "environment",
    "provider_instance_id", "key_path", "application_key_version", "transit_key_version",
)
_V1_FIELDS = frozenset({"schema", "provider", "kms_key_id", "key_version"})
_V2_FIELDS = frozenset(_METADATA) - {"kms_key_id", "key_version"}
_DECIMAL = re.compile(r"[1-9][0-9]{0,9}", re.ASCII)


class PersistedKeyReferenceUnavailable(RuntimeError):
    """Static error: incomplete references are never silently skipped."""


@dataclass(frozen=True, slots=True)
class AliyunPersistedKeyReference:
    purpose: str
    application_key_version: int
    kms_key_id: str
    kms_key_version_id: str
    ciphertext_sha256: str

    def __post_init__(self) -> None:
        if type(self.purpose) is not str or self.purpose not in {AUTH, CONTACT}:
            raise ValueError("invalid key purpose")
        # Share the existing scalar validation, without changing its auth-only
        # route or pretending that a contact reference is an auth binding.
        AliyunAuthenticationKeyBinding(
            self.application_key_version, self.kms_key_id,
            self.kms_key_version_id, self.ciphertext_sha256,
        )


@dataclass(frozen=True, slots=True)
class PersistedKeyReferenceCatalog:
    aliyun: tuple[AliyunPersistedKeyReference, ...] = ()
    openbao: tuple[OpenBaoReviewedPin, ...] = ()


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise ValueError("invalid key version")
    return value


def _contact_projection(model, dialect: str):
    envelope = model.__table__.c.contact_snapshot_jsonb
    fields = []
    for name in _METADATA:
        # Keep the JSON type independently: casting alone would accept true,
        # numeric strings and fractions on some supported database dialects.
        json_type = (
            func.jsonb_typeof(envelope[name]) if dialect == "postgresql"
            else func.json_type(envelope, "$." + name)
        )
        fields.extend((
            cast(envelope[name].as_string(), String).label(name),
            json_type.label(name + "_type"),
        ))
    return select(*fields)


def _metadata_value(row, name: str, *, integer: bool = False):
    value, kind = row[name], row[name + "_type"]
    if integer:
        if kind not in {"number", "integer"} or type(value) is not str or not _DECIMAL.fullmatch(value):
            raise ValueError("invalid key metadata")
        return _version(int(value))
    if kind not in {"string", "text"} or type(value) is not str:
        raise ValueError("invalid key metadata")
    return value


def _contact_reference(row, environment: str, instance: str | None):
    schema = _metadata_value(row, "schema")
    provider = _metadata_value(row, "provider")
    if schema == "rsc.material_request_contact.v1" and provider == ALIYUN:
        allowed = _V1_FIELDS
        version = _metadata_value(row, "key_version", integer=True)
        reference = (ALIYUN, _metadata_value(row, "kms_key_id"))
    elif schema == "rsc.material_request_contact.v2" and provider == OPENBAO:
        allowed = _V2_FIELDS
        version = _metadata_value(row, "application_key_version", integer=True)
        coordinate = OpenBaoKeyCoordinate(
            _metadata_value(row, "purpose"), _metadata_value(row, "environment"),
            _metadata_value(row, "provider_instance_id"), version,
        )
        if (
            coordinate.purpose != CONTACT or coordinate.environment != environment
            or instance is None or coordinate.provider_instance_id != instance
            or _metadata_value(row, "key_path") != coordinate.key_path
        ):
            raise ValueError("invalid contact scope")
        reference = (OPENBAO, coordinate, _metadata_value(row, "transit_key_version", integer=True))
    else:
        raise ValueError("unknown contact reference")
    # Absent fields have SQL NULL type; an explicitly inserted JSON null in
    # the other provider's coordinate vocabulary is still a malformed binding.
    if any(row[name + "_type"] is not None for name in set(_METADATA) - allowed):
        raise ValueError("mixed contact metadata")
    return (CONTACT, version), reference


def _bounded_rows(db: Session, statement):
    result = db.execute(statement.limit(MAX_REFERENCES + 1))
    try:
        rows = result.mappings().fetchmany(MAX_REFERENCES + 1)
    finally:
        result.close()
    if len(rows) > MAX_REFERENCES:
        raise ValueError("too many distinct key references")
    return rows


def _claim_statement(required):
    claim = ApplicationKeyVersionClaim.__table__
    aliyun = KmsDataKeyPin.__table__
    bao = OpenBaoDataKeyPin.__table__
    return select(
        claim.c.purpose, claim.c.application_key_version, claim.c.provider,
        claim.c.ciphertext_sha256.label("claim_sha"), claim.c.created_at.label("claim_created"),
        aliyun.c.kms_key_id.label("ali_key_id"),
        aliyun.c.kms_key_version_id.label("ali_version_id"),
        aliyun.c.ciphertext_sha256.label("ali_sha"), aliyun.c.created_at.label("ali_created"),
        bao.c.environment.label("bao_environment"), bao.c.provider_instance_id.label("bao_instance"),
        bao.c.key_path.label("bao_key_path"), bao.c.transit_key_version.label("bao_transit_version"),
        bao.c.ciphertext_sha256.label("bao_sha"), bao.c.context_sha256.label("bao_context_sha"),
        bao.c.associated_data_sha256.label("bao_aad_sha"), bao.c.created_at.label("bao_created"),
    ).select_from(claim.outerjoin(aliyun, and_(
        aliyun.c.purpose == claim.c.purpose,
        aliyun.c.application_key_version == claim.c.application_key_version,
    )).outerjoin(bao, and_(
        bao.c.purpose == claim.c.purpose,
        bao.c.application_key_version == claim.c.application_key_version,
    ))).where(tuple_(claim.c.purpose, claim.c.application_key_version).in_(sorted(required)))


def _catalog(rows, required, contacts, environment, instance):
    found, fingerprints = set(), set()
    aliyun, openbao = [], []
    ali_fields = ("ali_key_id", "ali_version_id", "ali_sha", "ali_created")
    bao_fields = (
        "bao_environment", "bao_instance", "bao_key_path", "bao_transit_version",
        "bao_sha", "bao_context_sha", "bao_aad_sha", "bao_created",
    )
    for row in rows:
        purpose, version = row["purpose"], _version(row["application_key_version"])
        identity = (purpose, version)
        if identity not in required or identity in found or not isinstance(row["claim_created"], datetime):
            raise ValueError("invalid claim")
        if row["provider"] == ALIYUN:
            if (
                any(row[field] is not None for field in bao_fields)
                or any(row[field] is None for field in ali_fields)
                or row["claim_sha"] != row["ali_sha"]
                or row["claim_created"] != row["ali_created"]
            ):
                raise ValueError("invalid Aliyun pin")
            pin = AliyunPersistedKeyReference(
                purpose, version, row["ali_key_id"], row["ali_version_id"], row["ali_sha"],
            )
            reference = (ALIYUN, pin.kms_key_id)
            aliyun.append(pin)
        elif row["provider"] == OPENBAO:
            if (
                any(row[field] is not None for field in ali_fields)
                or any(row[field] is None for field in bao_fields)
                or instance is None or row["bao_environment"] != environment
                or row["bao_instance"] != instance
                or row["claim_sha"] != row["bao_sha"]
                or row["claim_created"] != row["bao_created"]
            ):
                raise ValueError("invalid OpenBao pin")
            coordinate = OpenBaoKeyCoordinate(purpose, environment, instance, version)
            if row["bao_key_path"] != coordinate.key_path:
                raise ValueError("invalid OpenBao path")
            pin = OpenBaoReviewedPin(
                coordinate, row["bao_transit_version"], row["bao_sha"],
                row["bao_context_sha"], row["bao_aad_sha"],
            )
            reference = (OPENBAO, coordinate, pin.transit_key_version)
            openbao.append(pin)
        else:
            raise ValueError("unknown provider")
        if purpose == CONTACT and identity in contacts and contacts[identity] != reference:
            raise ValueError("contact pin mismatch")
        if row["claim_sha"] in fingerprints:
            raise ValueError("reused key material")
        fingerprints.add(row["claim_sha"])
        found.add(identity)
    if found != required:
        raise ValueError("missing key claims")
    return PersistedKeyReferenceCatalog(
        tuple(sorted(aliyun, key=lambda pin: (pin.purpose, pin.application_key_version))),
        tuple(sorted(openbao, key=lambda pin: (pin.coordinate.purpose, pin.coordinate.application_key_version))),
    )


def read_required_key_claims(
    db: Session, *, environment: str,
    required: frozenset[tuple[str, int]],
    openbao_provider_instance_id: str | None = None,
) -> PersistedKeyReferenceCatalog:
    """Read independently pinned, explicitly requested active/runtime keys.

    This uses the same bounded claim join as the stored-reference scan, but
    does not infer an active key from database contents or read business data.
    The caller must compare provider and full active coordinates to Settings.
    """
    catalog = None
    try:
        if not isinstance(db, Session) or type(required) is not frozenset:
            raise ValueError("invalid claim request")
        if environment not in {"development", "test", "staging", "production"}:
            raise ValueError("invalid environment")
        if openbao_provider_instance_id is not None:
            OpenBaoKeyCoordinate(AUTH, environment, openbao_provider_instance_id, 1)
        if not 1 <= len(required) <= MAX_REFERENCES:
            raise ValueError("invalid claim count")
        for identity in required:
            if type(identity) is not tuple or len(identity) != 2 or identity[0] not in {AUTH, CONTACT}:
                raise ValueError("invalid claim identity")
            _version(identity[1])
        with db.no_autoflush:
            catalog = _catalog(_bounded_rows(db, _claim_statement(required)), required, {},
                               environment, openbao_provider_instance_id)
    except Exception:
        pass
    if catalog is None:
        raise PersistedKeyReferenceUnavailable("persisted key references are unavailable")
    return catalog


def scan_persisted_key_references(
    db: Session, *, environment: str,
    openbao_provider_instance_id: str | None = None,
    now: datetime | None = None,
) -> PersistedKeyReferenceCatalog:
    """Resolve all required historical keys without assuming the active provider.

    Live terminal auth rows require keys strictly before expiry; every contact
    current/revision reference is durable, including closed/disabled workflows.
    At most 128 distinct identities are admitted (the two 64-entry registries).
    Overflow fails closed rather than silently dropping retained history.
    The caller owns the session, transaction, permissions and query timeout.
    """
    catalog = None
    try:
        if not isinstance(db, Session):
            raise ValueError("invalid database session")
        if type(environment) is not str or environment not in {"development", "test", "staging", "production"}:
            raise ValueError("invalid environment")
        if openbao_provider_instance_id is not None:
            OpenBaoKeyCoordinate(AUTH, environment, openbao_provider_instance_id, 1)
        checked_now = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(checked_now, datetime) or checked_now.tzinfo is None or checked_now.utcoffset() is None:
            raise ValueError("invalid reference time")
        dialect = db.get_bind().dialect.name
        if dialect not in {"postgresql", "sqlite"}:
            raise ValueError("unsupported reference database")
        # Core projections plus no_autoflush keep unrelated pending ORM work
        # untouched. No flush/commit/rollback, locks, writes or decrypts occur.
        with db.no_autoflush:
            auth = AuthIdempotencyOperation.__table__
            auth_rows = _bounded_rows(db, select(auth.c.encryption_key_version).where(
                auth.c.status.in_(("completed", "failed")), auth.c.expires_at > checked_now,
            ).distinct())
            required = {(AUTH, _version(row["encryption_key_version"])) for row in auth_rows}
            contact_rows = _bounded_rows(db, union(
                _contact_projection(MaterialRequest, dialect),
                _contact_projection(MaterialRequestRevision, dialect),
            ))
            contacts = {}
            for row in contact_rows:
                identity, reference = _contact_reference(row, environment, openbao_provider_instance_id)
                if identity in contacts and contacts[identity] != reference:
                    raise ValueError("ambiguous contact identity")
                contacts[identity] = reference
                required.add(identity)
            if len(required) > MAX_REFERENCES:
                raise ValueError("too many key references")
            rows = _bounded_rows(db, _claim_statement(required)) if required else []
            catalog = _catalog(rows, required, contacts, environment, openbao_provider_instance_id)
    except Exception:
        pass
    if catalog is None:
        # Deliberately outside the handler: driver SQL/parameters must not be
        # retained in a public exception's cause or context chain.
        raise PersistedKeyReferenceUnavailable("persisted key references are unavailable")
    return catalog
