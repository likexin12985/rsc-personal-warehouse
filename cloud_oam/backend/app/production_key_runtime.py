"""Startup-authorized, ciphertext-only routing for both key providers.

Build only after the production DB identity/immutable catalog gate. All DB
reads finish at startup; no Session, request cipher, DEK or attestation boolean
is retained. Immutable claims identify historical keys; Settings alone chooses
active writes. Changing a registry, binding or active configuration requires a
controlled restart. Readiness re-decrypts exact bindings without a key cache.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from .config import Settings
from .formal_services.authentication_idempotency import (
    AuthenticationEncryptionKeyUnavailable, create_authentication_response_cipher,
)
from .formal_services.authentication_key_claims import (
    AliyunAuthenticationKeyBinding, ClaimRoutedAuthenticationKeyProvider,
    OpenBaoAuthenticationKeyBinding,
)
from .openbao_registry_candidate import load_openbao_registry_candidate
from .openbao_runtime_transport import OpenBaoUnixDecryptTransport
from .openbao_transit_candidate import OpenBaoTransitCandidate
from .persisted_key_references import (
    AUTH, CONTACT, ALIYUN, OPENBAO, AliyunPersistedKeyReference,
    read_required_key_claims, scan_persisted_key_references,
)
from .production_openbao_composition import (
    OpenBaoProductionSettings, create_openbao_authentication_composition,
    create_openbao_contact_composition,
)

_CONFIG_FIELDS = (
    'environment', 'auth_idempotency_encryption_provider', 'auth_idempotency_kms_key_id',
    'auth_idempotency_encryption_key_version', 'material_request_writes_enabled',
    'material_request_contact_encryption_provider', 'material_request_contact_kms_key_id',
    'material_request_contact_encryption_key_version', 'kms_endpoint', 'kms_region',
    'kms_encrypted_data_key_registry_path', 'openbao_provider_instance_id',
    'openbao_encrypted_data_key_registry_path', 'openbao_socket_path', 'openbao_token_file',
    'openbao_api_uid', 'openbao_bao_uid', 'openbao_bao_gid', 'openbao_shared_gid',
    'openbao_token_projector_uid',
)


def _configuration(settings):
    return tuple(getattr(settings, name) for name in _CONFIG_FIELDS)


def _identity(pin):
    if type(pin) is AliyunPersistedKeyReference:
        return pin.purpose, pin.application_key_version
    return pin.coordinate.purpose, pin.coordinate.application_key_version


def _probe_coordinate(pin):
    # An opaque full-binding identity, not a provider key ID. The runtime must
    # admit this token before any provider call; different provider/instance,
    # Transit version or any pin hash yields a different readiness fingerprint.
    payload = {'provider': ALIYUN if type(pin) is AliyunPersistedKeyReference else OPENBAO,
               'pin': asdict(pin)}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    purpose, version = _identity(pin)
    return purpose, digest, version


def _bao_settings(settings, purpose, versions, active):
    return OpenBaoProductionSettings(
        enabled=True, environment=settings.environment, purpose=purpose,
        provider_instance_id=settings.openbao_provider_instance_id,
        registry_path=settings.openbao_encrypted_data_key_registry_path,
        socket_path=settings.openbao_socket_path, token_file=settings.openbao_token_file,
        api_uid=settings.openbao_api_uid, bao_uid=settings.openbao_bao_uid,
        bao_gid=settings.openbao_bao_gid, shared_gid=settings.openbao_shared_gid,
        token_projector_uid=settings.openbao_token_projector_uid,
        application_key_versions=tuple(sorted(versions)), active_application_key_version=active,
    )


@dataclass(frozen=True, slots=True, repr=False)
class ProviderKeyRuntime:
    _configuration: tuple
    _pins: tuple
    _wrapped_entries: tuple
    _transport: object | None
    _aliyun_loader: object | None

    @property
    def required_coordinates(self):
        return frozenset(_probe_coordinate(pin) for pin in self._pins)

    def _pin(self, purpose, version):
        matches = [pin for pin in self._pins if _identity(pin) == (purpose, version)]
        if len(matches) != 1:
            raise ValueError('key binding unavailable')
        return matches[0]

    def _check_settings(self, settings):
        if self._configuration != _configuration(settings):
            raise ValueError('runtime configuration mismatch')

    def _candidate(self, purpose):
        entries = tuple(entry for entry in self._wrapped_entries if entry.coordinate.purpose == purpose)
        pins = tuple(pin for pin in self._pins if type(pin) is not AliyunPersistedKeyReference
                     and pin.coordinate.purpose == purpose)
        if not pins:
            raise ValueError('key binding unavailable')
        first = pins[0].coordinate
        return OpenBaoTransitCandidate(
            environment=first.environment, provider_instance_id=first.provider_instance_id,
            entries=entries, reviewed_pins=pins, transport=self._transport,
        )

    def _resolve(self, pin, *, probe=False):
        material = None
        try:
            material = self._resolve_checked(pin, probe=probe)
        except Exception:
            pass
        if material is None:
            # The legacy contact ring retains its loader's cause. Keep raw
            # SDK/transport exceptions out of that chain as well as auth/probes.
            raise AuthenticationEncryptionKeyUnavailable('key provider is unavailable')
        return material

    def _resolve_checked(self, pin, *, probe=False):
        if self._pin(*_identity(pin)) != pin:
            raise ValueError('key binding unavailable')
        if type(pin) is AliyunPersistedKeyReference:
            loader = self._aliyun_loader
            coordinate = (pin.purpose, pin.kms_key_id, pin.application_key_version)
            mounted = loader.pin_manifest().get(coordinate) if loader else None
            if (mounted is None or mounted.kms_key_version_id != pin.kms_key_version_id
                    or mounted.ciphertext_sha256 != pin.ciphertext_sha256):
                raise ValueError('key binding unavailable')
            key = (loader.probe if probe else loader)(*coordinate)
        else:
            key = self._candidate(pin.coordinate.purpose).resolve(*_identity(pin))
        if type(key) is not bytes or len(key) != 32:
            raise ValueError('key binding unavailable')
        return key

    def probe(self, purpose, binding_digest, version):
        material = None
        try:
            pin = self._pin(purpose, version)
            if _probe_coordinate(pin) != (purpose, binding_digest, version):
                raise ValueError('key binding unavailable')
            material = self._resolve(pin, probe=True)
        except Exception:
            pass
        if material is None:
            raise AuthenticationEncryptionKeyUnavailable('key provider is unavailable')
        return material

    def authentication_cipher(self, settings):
        result = None
        try:
            self._check_settings(settings)
            def reader(purpose, version):
                if purpose != AUTH:
                    raise ValueError('key binding unavailable')
                pin = self._pin(purpose, version)
                if type(pin) is AliyunPersistedKeyReference:
                    return AliyunAuthenticationKeyBinding(version, pin.kms_key_id,
                                                         pin.kms_key_version_id, pin.ciphertext_sha256)
                return OpenBaoAuthenticationKeyBinding(pin)
            def ali_loader(binding):
                pin = self._pin(AUTH, binding.application_key_version)
                if reader(AUTH, binding.application_key_version) != binding:
                    raise ValueError('key binding unavailable')
                return self._resolve(pin)
            active = reader(AUTH, settings.auth_idempotency_encryption_key_version)
            if active.provider != settings.auth_idempotency_encryption_provider:
                raise ValueError('active provider mismatch')
            if active.provider == OPENBAO:
                composition = create_openbao_authentication_composition(
                    _bao_settings(settings, AUTH,
                                  [_identity(pin)[1] for pin in self._pins if _identity(pin)[0] == AUTH],
                                  active.application_key_version),
                    claim_reader=reader, candidate=self._candidate(AUTH), aliyun_key_loader=ali_loader,
                )
                if composition is None:
                    raise ValueError('key binding unavailable')
                provider = composition.key_provider
            else:
                provider = ClaimRoutedAuthenticationKeyProvider(
                    active_binding=active, claim_reader=reader, aliyun_key_loader=ali_loader,
                    openbao_key_loader=lambda binding: self._resolve(binding.reviewed_pin),
                )
            result = create_authentication_response_cipher(environment=settings.environment, key_provider=provider)
        except Exception:
            pass
        if result is None:
            raise AuthenticationEncryptionKeyUnavailable('authentication encryption key is unavailable')
        return result

    def contact_cipher(self, settings):
        from .production_adapters import _MaterialRequestContactCipherRing
        result = None
        try:
            self._check_settings(settings)
            active = self._pin(CONTACT, settings.material_request_contact_encryption_key_version)
            legacy = tuple(pin for pin in self._pins if type(pin) is AliyunPersistedKeyReference and pin.purpose == CONTACT)
            ring = None
            legacy_id = None
            if legacy:
                # This anchor is for the legacy reader interface only. Every
                # envelope still selects an exact independently pinned CMK/version.
                anchor = active if type(active) is AliyunPersistedKeyReference else legacy[0]
                legacy_id = anchor.kms_key_id
                def legacy_loader(purpose, key_id, version):
                    pin = self._pin(purpose, version)
                    if type(pin) is not AliyunPersistedKeyReference or pin.kms_key_id != key_id:
                        raise ValueError('key binding unavailable')
                    return self._resolve(pin)
                ring = _MaterialRequestContactCipherRing(
                    environment=settings.environment, loader=legacy_loader,
                    active_kms_key_id=legacy_id, active_version=anchor.application_key_version,
                )
            if settings.material_request_contact_encryption_provider == ALIYUN:
                if type(active) is not AliyunPersistedKeyReference:
                    raise ValueError('active provider mismatch')
                # Reverse contact migration is not implicit: startup rejects
                # v2 history until a reader supporting v1 writes/v2 reads exists.
                result = ring
            elif settings.material_request_contact_encryption_provider == OPENBAO:
                pins = tuple(pin for pin in self._pins if type(pin) is not AliyunPersistedKeyReference
                             and pin.coordinate.purpose == CONTACT)
                composition = create_openbao_contact_composition(
                    _bao_settings(settings, CONTACT, [pin.coordinate.application_key_version for pin in pins],
                                  settings.material_request_contact_encryption_key_version),
                    reviewed_pins=pins, candidate=self._candidate(CONTACT), transport=self._transport,
                    legacy_cipher=ring, legacy_kms_key_id=legacy_id,
                )
                result = composition.cipher if composition else None
        except Exception:
            pass
        if result is None:
            raise AuthenticationEncryptionKeyUnavailable('material request contact encryption key is unavailable')
        return result


def build_production_key_runtime(db: Session, settings: Settings, *, now: datetime | None = None):
    """Bind stored+active references after the caller's DB identity/ACL proof.

    Construction is SELECT-only, no provider decrypt or writes. Readiness is a
    separate bounded live probe and never an assertion of release approval.
    """
    from .production_adapters import (
        ProductionAdapterConfigurationError, get_configured_kms_loader, _validate_database_kms_pins,
    )
    result = None
    try:
        instance = settings.openbao_provider_instance_id or None
        stored = scan_persisted_key_references(db, environment=settings.environment,
                                              openbao_provider_instance_id=instance, now=now)
        required = {_identity(pin) for pin in (*stored.aliyun, *stored.openbao)}
        required.add((AUTH, settings.auth_idempotency_encryption_key_version))
        contact_configured = settings.material_request_contact_encryption_provider != 'disabled'
        if contact_configured or settings.material_request_writes_enabled:
            required.add((CONTACT, settings.material_request_contact_encryption_key_version))
        catalog = read_required_key_claims(db, environment=settings.environment,
                                          required=frozenset(required), openbao_provider_instance_id=instance)
        pins = (*catalog.aliyun, *catalog.openbao)
        if not set((*stored.aliyun, *stored.openbao)).issubset(pins):
            raise ValueError('stored key binding changed')
        for purpose, provider, version, key_id in (
            (AUTH, settings.auth_idempotency_encryption_provider, settings.auth_idempotency_encryption_key_version,
             settings.auth_idempotency_kms_key_id),
            *(([(CONTACT, settings.material_request_contact_encryption_provider,
                 settings.material_request_contact_encryption_key_version, settings.material_request_contact_kms_key_id)])
              if contact_configured or settings.material_request_writes_enabled else []),
        ):
            pin = next(pin for pin in pins if _identity(pin) == (purpose, version))
            if provider == ALIYUN:
                if type(pin) is not AliyunPersistedKeyReference or pin.kms_key_id != key_id:
                    raise ValueError('active key mismatch')
            elif provider != OPENBAO or type(pin) is AliyunPersistedKeyReference:
                raise ValueError('active key mismatch')
        if any(pin.coordinate.purpose == CONTACT for pin in catalog.openbao) and settings.material_request_contact_encryption_provider != OPENBAO:
            raise ValueError('contact v2 reader unavailable')
        loader = None
        if catalog.aliyun:
            loader = get_configured_kms_loader(settings)
            with db.no_autoflush:
                _validate_database_kms_pins(db, loader=loader, required_coordinates={
                    (pin.purpose, pin.kms_key_id, pin.application_key_version) for pin in catalog.aliyun})
            mounted = loader.pin_manifest()
            for pin in catalog.aliyun:
                actual = mounted[(pin.purpose, pin.kms_key_id, pin.application_key_version)]
                if (actual.kms_key_version_id != pin.kms_key_version_id or actual.ciphertext_sha256 != pin.ciphertext_sha256):
                    raise ValueError('mounted pin mismatch')
        entries, transport = (), None
        if catalog.openbao:
            if not settings.openbao_configuration_ready():
                raise ValueError('OpenBao configuration unavailable')
            transport = OpenBaoUnixDecryptTransport(
                socket_path=settings.openbao_socket_path, token_file=settings.openbao_token_file,
                api_uid=settings.openbao_api_uid, bao_uid=settings.openbao_bao_uid,
                bao_gid=settings.openbao_bao_gid, shared_gid=settings.openbao_shared_gid,
                token_projector_uid=settings.openbao_token_projector_uid,
            )
            candidate = load_openbao_registry_candidate(
                registry_path=settings.openbao_encrypted_data_key_registry_path,
                environment=settings.environment, provider_instance_id=instance,
                reviewed_pins=catalog.openbao, transport=transport,
            )
            entries = tuple(candidate._entries[key] for key in sorted(candidate._entries))
        runtime = ProviderKeyRuntime(_configuration(settings), tuple(pins), entries, transport, loader)
        runtime.authentication_cipher(settings)
        if contact_configured:
            runtime.contact_cipher(settings)
        result = runtime
    except Exception:
        pass
    if result is None:
        raise ProductionAdapterConfigurationError('key provider runtime is unavailable')
    return result
