"""Production-only composition for KMS-backed application data keys.

The application stores AES-GCM ciphertext locally, but it never accepts an AES
key through settings or environment variables.  Each logical key version is
provisioned out of band with Alibaba Cloud KMS ``GenerateDataKey`` and only the
returned ``CiphertextBlob`` is mounted into the API container.  This module
parses that immutable registry, asks KMS to unwrap one exact entry per request,
and keeps the plaintext data key only inside the request-scoped cipher.

No function in this module writes business data or calls OAM/RSC/Workflow/
Feishu.  Construction is network-free; the first key resolution is the KMS
availability/permission preflight and fails closed before an SMS or business
write side effect.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
from typing import Any, Callable, Final, Mapping, Protocol

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from .config import Settings
from .demand_models import MaterialRequest, MaterialRequestRevision
from .formal_services.authentication_idempotency import (
    Aes256GcmAuthenticationResponseCipher,
    AuthenticationEncryptionKeyUnavailable,
    KmsAuthenticationKeyProvider,
    create_authentication_response_cipher,
    create_configured_authentication_response_cipher,
)
from .foundation_models import AuthIdempotencyOperation, KmsDataKeyPin


REGISTRY_SCHEMA: Final[str] = "rsc.kms.encrypted-data-key-registry.v1"
AUTHENTICATION_PURPOSE: Final[str] = "authentication_idempotency"
MATERIAL_REQUEST_CONTACT_PURPOSE: Final[str] = "material_request_contact"
_ALLOWED_PURPOSES = frozenset(
    {AUTHENTICATION_PURPOSE, MATERIAL_REQUEST_CONTACT_PURPOSE}
)
_SAFE_ENDPOINT = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{1,251}[a-z0-9])?\.aliyuncs\.com$",
    re.ASCII,
)
_SAFE_REGION = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", re.ASCII)
_SAFE_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+-]{2,255}$", re.ASCII)
_SAFE_KMS_VERSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,127}$", re.ASCII)
_SAFE_CIPHERTEXT_BLOB = re.compile(r"^[A-Za-z0-9+/=_-]{16,8192}$", re.ASCII)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")
_MAX_REGISTRY_BYTES = 1024 * 1024


class ProductionAdapterConfigurationError(RuntimeError):
    """A production adapter cannot be constructed from reviewed coordinates."""


class ProductionAdapterUnavailable(RuntimeError):
    """A configured production provider cannot resolve the requested key."""


class KmsDecryptClient(Protocol):
    """Small SDK seam used by the envelope loader and deterministic tests."""

    def decrypt(
        self,
        *,
        ciphertext_blob: str,
        encryption_context: Mapping[str, str],
    ) -> object: ...


@dataclass(frozen=True, slots=True, repr=False)
class KmsDecryptResult:
    plaintext_b64: str
    key_id: str
    key_version_id: str


@dataclass(frozen=True, slots=True, repr=False)
class _RegistryEntry:
    purpose: str
    kms_key_id: str
    application_key_version: int
    kms_key_version_id: str
    ciphertext_blob: str
    encryption_context: Mapping[str, str]


@dataclass(frozen=True, slots=True, repr=False)
class _RegistryPin:
    kms_key_version_id: str
    ciphertext_sha256: str


class _AliyunOpenApiKmsDecryptClient:
    """Alibaba Cloud KMS V2 client using the default credential chain."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        credential: object | None = None,
    ) -> None:
        # Tea installs a DEBUG stream handler at import time.  Establish the
        # deny-by-default boundary both before and after importing the SDK so
        # an inherited DEBUG=sdk value can never print signed request headers.
        _silence_aliyun_sdk_loggers()
        try:
            from alibabacloud_credentials.client import Client as CredentialClient
            from alibabacloud_kms20160120.client import Client as KmsClient
            from alibabacloud_tea_openapi import models as open_api_models

            _silence_aliyun_sdk_loggers()
            credentials = credential if credential is not None else CredentialClient()
            config = open_api_models.Config(
                credential=credentials,
                endpoint=endpoint,
                region_id=region,
                protocol="https",
                connect_timeout=1000,
                read_timeout=2500,
            )
            self._client = KmsClient(config)
        except Exception as exc:  # pragma: no cover - deployment SDK boundary
            raise ProductionAdapterConfigurationError(
                "KMS client construction failed"
            ) from exc

    def decrypt(
        self,
        *,
        ciphertext_blob: str,
        encryption_context: Mapping[str, str],
    ) -> KmsDecryptResult:
        _silence_aliyun_sdk_loggers()
        try:
            from alibabacloud_kms20160120 import models as kms_models
            from alibabacloud_tea_util import models as util_models

            _silence_aliyun_sdk_loggers()
            response = self._client.decrypt_with_options(
                kms_models.DecryptRequest(
                    ciphertext_blob=ciphertext_blob,
                    encryption_context=dict(encryption_context),
                ),
                util_models.RuntimeOptions(
                    autoretry=False,
                    max_attempts=1,
                    connect_timeout=1000,
                    read_timeout=2500,
                ),
            )
            body = response.body if response is not None else None
            return KmsDecryptResult(
                plaintext_b64=str(getattr(body, "plaintext", "") or ""),
                key_id=str(getattr(body, "key_id", "") or ""),
                key_version_id=str(
                    getattr(body, "key_version_id", "") or ""
                ),
            )
        except Exception as exc:  # pragma: no cover - deployment SDK boundary
            raise ProductionAdapterUnavailable("KMS decrypt failed") from exc


class _SynchronizedCredentialClient:
    """Serialize one SDK default-chain client shared by bounded KMS clients."""

    def __init__(self, delegate: object) -> None:
        self._delegate = delegate
        self._lock = threading.Lock()

    def get_credential(self):
        with self._lock:
            return self._delegate.get_credential()

    def get_type(self):
        with self._lock:
            return self._delegate.get_type()

    async def get_credential_async(self):  # pragma: no cover - sync KMS client only
        return await self._delegate.get_credential_async()


class AliyunKmsEnvelopeKeyLoader:
    """Resolve exact versioned 32-byte data keys from a ciphertext registry."""

    def __init__(
        self,
        *,
        endpoint: str,
        region: str,
        registry_path: str,
        client_factory: Callable[..., KmsDecryptClient] = (
            _AliyunOpenApiKmsDecryptClient
        ),
    ) -> None:
        checked_endpoint = _endpoint(endpoint)
        checked_region = _region(region)
        checked_path = _registry_path(registry_path)
        self._entries = _load_registry(checked_path)
        self._endpoint = checked_endpoint
        self._region = checked_region
        self._client_factory = client_factory
        self._uses_default_client_factory = (
            client_factory is _AliyunOpenApiKmsDecryptClient
        )
        self._client: KmsDecryptClient | None = None
        self._client_lock = threading.Lock()
        self._credential_client: object | None = None
        self._credential_lock = threading.Lock()

    def has_entry(self, purpose: str, kms_key_id: str, version: int) -> bool:
        try:
            coordinate = _coordinate(purpose, kms_key_id, version)
        except ProductionAdapterConfigurationError:
            return False
        return coordinate in self._entries

    def pin_manifest(self) -> Mapping[tuple[str, str, int], _RegistryPin]:
        """Return non-secret immutable identities for mounted registry rows."""

        return {
            coordinate: _RegistryPin(
                kms_key_version_id=entry.kms_key_version_id,
                ciphertext_sha256=hashlib.sha256(
                    entry.ciphertext_blob.encode("ascii")
                ).hexdigest(),
            )
            for coordinate, entry in self._entries.items()
        }

    def __call__(self, purpose: str, kms_key_id: str, version: int) -> bytes:
        entry = self._entry(purpose, kms_key_id, version)
        return self._resolve(entry, client=self._decrypt_client())

    def probe(self, purpose: str, kms_key_id: str, version: int) -> bytes:
        """Decrypt one readiness coordinate with an isolated SDK client."""

        entry = self._entry(purpose, kms_key_id, version)
        return self._resolve(entry, client=self._new_decrypt_client())

    def _entry(
        self,
        purpose: str,
        kms_key_id: str,
        version: int,
    ) -> _RegistryEntry:
        coordinate = _coordinate(purpose, kms_key_id, version)
        entry = self._entries.get(coordinate)
        if entry is None:
            raise ProductionAdapterUnavailable(
                "KMS encrypted data-key entry is unavailable"
            )
        return entry

    def _resolve(
        self,
        entry: _RegistryEntry,
        *,
        client: KmsDecryptClient,
    ) -> bytes:
        try:
            raw_result = client.decrypt(
                ciphertext_blob=entry.ciphertext_blob,
                encryption_context=entry.encryption_context,
            )
            result = _decrypt_result(raw_result)
            if (
                result.key_id != entry.kms_key_id
                or result.key_version_id != entry.kms_key_version_id
            ):
                raise ProductionAdapterUnavailable(
                    "KMS decrypt identity does not match the registry"
                )
            plaintext = base64.b64decode(result.plaintext_b64, validate=True)
            if (
                len(plaintext) != 32
                or base64.b64encode(plaintext).decode("ascii")
                != result.plaintext_b64
            ):
                raise ProductionAdapterUnavailable(
                    "KMS data key is not canonical AES-256 material"
                )
            return plaintext
        except ProductionAdapterUnavailable:
            raise
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProductionAdapterUnavailable(
                "KMS data key could not be decoded"
            ) from exc
        except Exception as exc:
            raise ProductionAdapterUnavailable("KMS decrypt failed") from exc

    def _decrypt_client(self) -> KmsDecryptClient:
        client = self._client
        if client is not None:
            return client
        with self._client_lock:
            if self._client is not None:
                return self._client
            client = self._new_decrypt_client()
            self._client = client
            return client

    def _new_decrypt_client(self) -> KmsDecryptClient:
        try:
            arguments: dict[str, object] = {
                "endpoint": self._endpoint,
                "region": self._region,
            }
            if self._uses_default_client_factory:
                arguments["credential"] = self._shared_credential_client()
            return self._client_factory(**arguments)
        except ProductionAdapterConfigurationError:
            raise
        except Exception as exc:
            raise ProductionAdapterConfigurationError(
                "KMS client construction failed"
            ) from exc

    def _shared_credential_client(self) -> object:
        credential = self._credential_client
        if credential is not None:
            return credential
        with self._credential_lock:
            if self._credential_client is not None:
                return self._credential_client
            try:
                _silence_aliyun_sdk_loggers()
                from alibabacloud_credentials.client import (
                    Client as CredentialClient,
                )

                _silence_aliyun_sdk_loggers()
                credential = _SynchronizedCredentialClient(CredentialClient())
            except Exception as exc:
                raise ProductionAdapterConfigurationError(
                    "KMS credential provider construction failed"
                ) from exc
            self._credential_client = credential
            return credential


def _silence_aliyun_sdk_loggers() -> None:
    """Prevent provider secrets and wire diagnostics reaching process stderr.

    The pinned credential and Tea SDKs install their own stream handlers.  Tea
    can render complete signed request/response headers when ``DEBUG=sdk`` is
    inherited.  KMS availability is already represented by fixed
    readiness/business failures, so raw SDK diagnostics must not bypass the
    application's desensitized logging boundary.
    """

    for logger_name in ("credentials", "alibabacloud-tea"):
        sdk_logger = logging.getLogger(logger_name)
        sdk_logger.handlers.clear()
        sdk_logger.addHandler(logging.NullHandler())
        sdk_logger.propagate = False
        sdk_logger.disabled = True


@lru_cache(maxsize=4)
def _configured_loader(
    endpoint: str,
    region: str,
    registry_path: str,
) -> AliyunKmsEnvelopeKeyLoader:
    return AliyunKmsEnvelopeKeyLoader(
        endpoint=endpoint,
        region=region,
        registry_path=registry_path,
    )


def get_configured_kms_loader(settings: Settings) -> AliyunKmsEnvelopeKeyLoader:
    """Return the ciphertext-only loader without resolving plaintext keys."""

    if not settings.kms_envelope_configuration_ready():
        raise ProductionAdapterConfigurationError(
            "KMS envelope-key configuration is incomplete"
        )
    return _configured_loader(
        settings.kms_endpoint.strip().lower(),
        settings.kms_region.strip().lower(),
        settings.kms_encrypted_data_key_registry_path.strip(),
    )


def create_production_authentication_cipher(
    settings: Settings,
) -> Aes256GcmAuthenticationResponseCipher:
    """Create one request-scoped authentication cipher."""

    try:
        loader = get_configured_kms_loader(settings)
        return create_configured_authentication_response_cipher(
            settings=settings,
            kms_key_loader=lambda key_id, version: loader(
                AUTHENTICATION_PURPOSE,
                key_id,
                version,
            ),
        )
    except AuthenticationEncryptionKeyUnavailable:
        raise
    except Exception as exc:
        raise AuthenticationEncryptionKeyUnavailable(
            "authentication encryption key is unavailable"
        ) from exc


def create_production_material_request_contact_cipher(
    settings: Settings,
) -> _MaterialRequestContactCipherRing:
    """Create one request-scoped contact cipher with its independent key."""

    try:
        loader = get_configured_kms_loader(settings)
        return _MaterialRequestContactCipherRing(
            environment=settings.environment,
            loader=loader,
            active_kms_key_id=settings.material_request_contact_kms_key_id,
            active_version=settings.material_request_contact_encryption_key_version,
        )
    except AuthenticationEncryptionKeyUnavailable:
        raise
    except Exception as exc:
        raise AuthenticationEncryptionKeyUnavailable(
            "material request contact encryption key is unavailable"
        ) from exc


class _MaterialRequestContactCipherRing:
    """Request-local exact-key ring for current writes and historical drafts."""

    def __init__(
        self,
        *,
        environment: str,
        loader: AliyunKmsEnvelopeKeyLoader,
        active_kms_key_id: str,
        active_version: int,
    ) -> None:
        self._environment = environment
        self._loader = loader
        self._active_kms_key_id = _key_id(active_kms_key_id)
        self._active_version = _version(active_version)
        self._ciphers: dict[
            tuple[str, int],
            Aes256GcmAuthenticationResponseCipher,
        ] = {}

    def active_key_version(self) -> int:
        return self._cipher(
            self._active_kms_key_id,
            self._active_version,
        ).active_key_version()

    def encrypt(self, plaintext: bytes, *, aad: bytes, key_version: int):
        return self._cipher(
            self._active_kms_key_id,
            self._active_version,
        ).encrypt(plaintext, aad=aad, key_version=key_version)

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes:
        return self._cipher(
            self._active_kms_key_id,
            self._active_version,
        ).decrypt(
            ciphertext,
            nonce=nonce,
            aad=aad,
            key_version=key_version,
        )

    def decrypt_for_kms_key_id(
        self,
        kms_key_id: str,
        ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes:
        checked_key_id = _key_id(kms_key_id)
        checked_version = _version(key_version)
        active_version = (
            self._active_version
            if checked_key_id == self._active_kms_key_id
            else checked_version
        )
        return self._cipher(checked_key_id, active_version).decrypt(
            ciphertext,
            nonce=nonce,
            aad=aad,
            key_version=checked_version,
        )

    def _cipher(
        self,
        kms_key_id: str,
        active_version: int,
    ) -> Aes256GcmAuthenticationResponseCipher:
        coordinate = (kms_key_id, active_version)
        cipher = self._ciphers.get(coordinate)
        if cipher is not None:
            return cipher
        provider = KmsAuthenticationKeyProvider(
            kms_key_id=kms_key_id,
            active_version=active_version,
            key_loader=lambda key_id, version: self._loader(
                MATERIAL_REQUEST_CONTACT_PURPOSE,
                key_id,
                version,
            ),
        )
        cipher = create_authentication_response_cipher(
            environment=self._environment,
            key_provider=provider,
        )
        self._ciphers[coordinate] = cipher
        return cipher


def validate_production_adapter_installation(settings: Settings) -> None:
    """Network-free startup proof for every enabled production adapter."""

    if settings.environment != "production":
        return
    loader = get_configured_kms_loader(settings)
    required = _active_coordinates(settings)
    missing = [coordinate for coordinate in required if not loader.has_entry(*coordinate)]
    if missing:
        raise ProductionAdapterConfigurationError(
            "required KMS encrypted data-key entries are unavailable"
        )


def validate_persisted_kms_key_references(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> frozenset[tuple[str, str, int]]:
    """Prove every still-decryptable database envelope has a registry key.

    Authentication replay evidence needs its historical data key only while
    its short replay window is live.  Material-request contact envelopes are
    durable business records, so every version referenced by either the
    current projection or an immutable revision remains required indefinitely
    until a separately audited re-encryption migration proves otherwise.
    Only key coordinates are selected; ciphertext and contact data never cross
    this startup boundary.
    """

    if settings.environment != "production":
        return frozenset()
    checked_now = now or datetime.now(timezone.utc)
    if checked_now.tzinfo is None or checked_now.utcoffset() is None:
        raise ProductionAdapterConfigurationError(
            "KMS persisted-reference validation time is invalid"
        )
    loader = get_configured_kms_loader(settings)
    auth_versions = set(
        db.scalars(
            select(AuthIdempotencyOperation.encryption_key_version)
            .where(
                AuthIdempotencyOperation.status.in_(("completed", "failed")),
                AuthIdempotencyOperation.expires_at > checked_now,
                AuthIdempotencyOperation.encryption_key_version.is_not(None),
            )
            .distinct()
        )
    )
    contact_coordinates = set(
        db.execute(
            union(
                select(
                    MaterialRequest.contact_snapshot_jsonb["kms_key_id"]
                    .as_string()
                    .label("kms_key_id"),
                    MaterialRequest.contact_snapshot_jsonb["key_version"]
                    .as_integer()
                    .label("key_version"),
                ),
                select(
                    MaterialRequestRevision.contact_snapshot_jsonb["kms_key_id"]
                    .as_string()
                    .label("kms_key_id"),
                    MaterialRequestRevision.contact_snapshot_jsonb["key_version"]
                    .as_integer()
                    .label("key_version"),
                ),
            )
        ).all()
    )

    missing: list[tuple[str, str, object]] = []
    required_coordinates = set(_active_coordinates(settings))
    for version in auth_versions:
        try:
            coordinate = _coordinate(
                AUTHENTICATION_PURPOSE,
                settings.auth_idempotency_kms_key_id,
                version,
            )
        except ProductionAdapterConfigurationError:
            missing.append(
                (
                    AUTHENTICATION_PURPOSE,
                    settings.auth_idempotency_kms_key_id,
                    version,
                )
            )
            continue
        if not loader.has_entry(*coordinate):
            missing.append(coordinate)
        else:
            required_coordinates.add(coordinate)

    for key_id, version in contact_coordinates:
        try:
            coordinate = _coordinate(
                MATERIAL_REQUEST_CONTACT_PURPOSE,
                key_id,
                version,
            )
        except ProductionAdapterConfigurationError:
            missing.append((MATERIAL_REQUEST_CONTACT_PURPOSE, str(key_id), version))
            continue
        if not loader.has_entry(*coordinate):
            missing.append(coordinate)
        else:
            required_coordinates.add(coordinate)
    if missing:
        raise ProductionAdapterConfigurationError(
            "persisted KMS key references are unavailable"
        )
    _validate_database_kms_pins(
        db,
        loader=loader,
        required_coordinates=required_coordinates,
    )
    return frozenset(required_coordinates)


def _active_coordinates(settings: Settings) -> tuple[tuple[str, str, int], ...]:
    coordinates = [
        _coordinate(
            AUTHENTICATION_PURPOSE,
            settings.auth_idempotency_kms_key_id,
            settings.auth_idempotency_encryption_key_version,
        )
    ]
    if settings.material_request_writes_enabled:
        coordinates.append(
            _coordinate(
                MATERIAL_REQUEST_CONTACT_PURPOSE,
                settings.material_request_contact_kms_key_id,
                settings.material_request_contact_encryption_key_version,
            )
        )
    return tuple(coordinates)


def _validate_database_kms_pins(
    db: Session,
    *,
    loader: AliyunKmsEnvelopeKeyLoader,
    required_coordinates: set[tuple[str, str, int]],
) -> None:
    rows = db.execute(
        select(
            KmsDataKeyPin.purpose,
            KmsDataKeyPin.kms_key_id,
            KmsDataKeyPin.application_key_version,
            KmsDataKeyPin.kms_key_version_id,
            KmsDataKeyPin.ciphertext_sha256,
        )
    ).all()
    actual: dict[tuple[str, str, int], _RegistryPin] = {}
    fingerprints: set[str] = set()
    purpose_versions: set[tuple[str, int]] = set()
    key_purposes: dict[str, str] = {}
    for purpose, key_id, version, kms_version_id, ciphertext_sha256 in rows:
        try:
            coordinate = _coordinate(purpose, key_id, version)
        except ProductionAdapterConfigurationError as exc:
            raise ProductionAdapterConfigurationError(
                "database KMS data-key pin is invalid"
            ) from exc
        purpose_version = (coordinate[0], coordinate[2])
        known_purpose = key_purposes.get(coordinate[1])
        if (
            not isinstance(kms_version_id, str)
            or _SAFE_KMS_VERSION_ID.fullmatch(kms_version_id) is None
            or not isinstance(ciphertext_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", ciphertext_sha256) is None
            or coordinate in actual
            or purpose_version in purpose_versions
            or ciphertext_sha256 in fingerprints
            or (
                known_purpose is not None
                and known_purpose != coordinate[0]
            )
        ):
            raise ProductionAdapterConfigurationError(
                "database KMS data-key pin is invalid"
            )
        actual[coordinate] = _RegistryPin(
            kms_key_version_id=kms_version_id,
            ciphertext_sha256=ciphertext_sha256,
        )
        purpose_versions.add(purpose_version)
        fingerprints.add(ciphertext_sha256)
        key_purposes[coordinate[1]] = coordinate[0]

    mounted = loader.pin_manifest()
    if not required_coordinates.issubset(mounted) or not set(mounted).issubset(actual):
        raise ProductionAdapterConfigurationError(
            "database KMS data-key pins are incomplete"
        )
    if any(actual[coordinate] != pin for coordinate, pin in mounted.items()):
        raise ProductionAdapterConfigurationError(
            "database KMS data-key pin does not match the mounted registry"
        )


def clear_production_adapter_caches_for_tests() -> None:
    """Clear ciphertext-only construction caches between isolated unit tests."""

    _configured_loader.cache_clear()


def _load_registry(path: Path) -> dict[tuple[str, str, int], _RegistryEntry]:
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionAdapterConfigurationError(
            "KMS encrypted data-key registry is unreadable"
        ) from exc
    if not isinstance(document, dict) or set(document) != {"schema", "entries"}:
        raise ProductionAdapterConfigurationError(
            "KMS encrypted data-key registry shape is invalid"
        )
    if document.get("schema") != REGISTRY_SCHEMA:
        raise ProductionAdapterConfigurationError(
            "KMS encrypted data-key registry schema is invalid"
        )
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries or len(raw_entries) > 64:
        raise ProductionAdapterConfigurationError(
            "KMS encrypted data-key registry entries are invalid"
        )
    entries: dict[tuple[str, str, int], _RegistryEntry] = {}
    ciphertexts: set[str] = set()
    # Authentication replay rows persist only the application version, not the
    # KMS Key ID.  A version must therefore remain globally unambiguous within
    # one purpose even when a CMK is changed after the replay window drains.
    purpose_versions: set[tuple[str, int]] = set()
    # One CMK may carry multiple retained application versions for the same
    # purpose, but it must never cross the authentication/contact isolation
    # boundary—even after either active key rotates.
    key_purposes: dict[str, str] = {}
    for raw in raw_entries:
        entry = _registry_entry(raw)
        coordinate = (
            entry.purpose,
            entry.kms_key_id,
            entry.application_key_version,
        )
        purpose_version = (entry.purpose, entry.application_key_version)
        known_purpose = key_purposes.get(entry.kms_key_id)
        if (
            coordinate in entries
            or purpose_version in purpose_versions
            or entry.ciphertext_blob in ciphertexts
        ):
            raise ProductionAdapterConfigurationError(
                "KMS encrypted data-key registry contains duplicate material"
            )
        if known_purpose is not None and known_purpose != entry.purpose:
            raise ProductionAdapterConfigurationError(
                "KMS encrypted data-key registry must use distinct KMS keys "
                "for each purpose"
            )
        entries[coordinate] = entry
        purpose_versions.add(purpose_version)
        ciphertexts.add(entry.ciphertext_blob)
        key_purposes[entry.kms_key_id] = entry.purpose
    return entries


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys at every registry nesting level."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate registry object key")
        result[key] = value
    return result


def _registry_entry(raw: object) -> _RegistryEntry:
    expected_fields = {
        "purpose",
        "kms_key_id",
        "application_key_version",
        "kms_key_version_id",
        "ciphertext_blob",
        "encryption_context",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ProductionAdapterConfigurationError("KMS registry entry is invalid")
    purpose = _purpose(raw.get("purpose"))
    key_id = _key_id(raw.get("kms_key_id"))
    version = _version(raw.get("application_key_version"))
    key_version_id = raw.get("kms_key_version_id")
    ciphertext_blob = raw.get("ciphertext_blob")
    if (
        not isinstance(key_version_id, str)
        or _SAFE_KMS_VERSION_ID.fullmatch(key_version_id) is None
        or not isinstance(ciphertext_blob, str)
        or _SAFE_CIPHERTEXT_BLOB.fullmatch(ciphertext_blob) is None
    ):
        raise ProductionAdapterConfigurationError("KMS registry entry is invalid")
    expected_context = _encryption_context(purpose, key_id, version)
    raw_context = raw.get("encryption_context")
    if not isinstance(raw_context, dict) or raw_context != expected_context:
        raise ProductionAdapterConfigurationError(
            "KMS registry encryption context is invalid"
        )
    return _RegistryEntry(
        purpose=purpose,
        kms_key_id=key_id,
        application_key_version=version,
        kms_key_version_id=key_version_id,
        ciphertext_blob=ciphertext_blob,
        encryption_context=expected_context,
    )


def _encryption_context(purpose: str, key_id: str, version: int) -> dict[str, str]:
    return {
        "application": "cloud_oam",
        "purpose": purpose,
        "kms_key_id": key_id,
        "application_key_version": str(version),
    }


def _coordinate(purpose: object, key_id: object, version: object) -> tuple[str, str, int]:
    return (_purpose(purpose), _key_id(key_id), _version(version))


def _purpose(value: object) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_PURPOSES:
        raise ProductionAdapterConfigurationError("KMS purpose is invalid")
    return value


def _key_id(value: object) -> str:
    checked = value.strip() if isinstance(value, str) else ""
    if (
        _SAFE_KEY_ID.fullmatch(checked) is None
        or any(marker in checked.lower() for marker in _PLACEHOLDERS)
    ):
        raise ProductionAdapterConfigurationError("KMS key identifier is invalid")
    return checked


def _version(value: object) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > 2_147_483_647
    ):
        raise ProductionAdapterConfigurationError("KMS key version is invalid")
    return value


def _endpoint(value: object) -> str:
    checked = value.strip().lower() if isinstance(value, str) else ""
    if (
        _SAFE_ENDPOINT.fullmatch(checked) is None
        or ".." in checked
        or any(marker in checked for marker in _PLACEHOLDERS)
    ):
        raise ProductionAdapterConfigurationError("KMS endpoint is invalid")
    return checked


def _region(value: object) -> str:
    checked = value.strip().lower() if isinstance(value, str) else ""
    if _SAFE_REGION.fullmatch(checked) is None:
        raise ProductionAdapterConfigurationError("KMS region is invalid")
    return checked


def _registry_path(value: object) -> Path:
    checked = value.strip() if isinstance(value, str) else ""
    if not checked or "\x00" in checked:
        raise ProductionAdapterConfigurationError("KMS registry path is invalid")
    path = Path(checked)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ProductionAdapterConfigurationError("KMS registry path is invalid")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ProductionAdapterConfigurationError(
            "KMS registry path is invalid"
        ) from exc
    if metadata.st_size <= 0 or metadata.st_size > _MAX_REGISTRY_BYTES:
        raise ProductionAdapterConfigurationError("KMS registry size is invalid")
    if metadata.st_mode & 0o022:
        raise ProductionAdapterConfigurationError(
            "KMS registry permissions are invalid"
        )
    return path


def _decrypt_result(value: object) -> KmsDecryptResult:
    if isinstance(value, KmsDecryptResult):
        return value
    if isinstance(value, Mapping) and set(value) == {
        "plaintext_b64",
        "key_id",
        "key_version_id",
    }:
        return KmsDecryptResult(
            plaintext_b64=str(value["plaintext_b64"]),
            key_id=str(value["key_id"]),
            key_version_id=str(value["key_version_id"]),
        )
    raise ProductionAdapterUnavailable("KMS decrypt response is invalid")


__all__ = [
    "AUTHENTICATION_PURPOSE",
    "AliyunKmsEnvelopeKeyLoader",
    "KmsDecryptResult",
    "MATERIAL_REQUEST_CONTACT_PURPOSE",
    "ProductionAdapterConfigurationError",
    "ProductionAdapterUnavailable",
    "REGISTRY_SCHEMA",
    "clear_production_adapter_caches_for_tests",
    "create_production_authentication_cipher",
    "create_production_material_request_contact_cipher",
    "get_configured_kms_loader",
    "validate_production_adapter_installation",
    "validate_persisted_kms_key_references",
]
