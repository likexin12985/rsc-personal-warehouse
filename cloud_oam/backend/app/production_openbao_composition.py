"""Fail-closed production composition for the OpenBao C2 candidate.

The production key runtime invokes these factories only after startup has
validated independent immutable claims/pins and the complete registry. Provider
selection remains disabled by default. Local composition does not supply the
external runtime or release evidence required by the readiness assessment.

The factory accepts an explicit database claim reader, an exact pin reader,
and a reviewed registry/transport.  It never chooses a latest key, treats an
OpenBao failure as a reason to try Aliyun, or derives a pin from a registry or
decrypt response.  Construction performs no provider decrypt call; the
transport is used only when the returned request-scoped key provider resolves
an exact application version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from sqlalchemy.orm import Session

from .formal_services.authentication_key_claim_reader import (
    DatabaseAuthenticationClaimReader,
)
from .formal_services.authentication_key_claims import (
    AUTHENTICATION_PURPOSE,
    AliyunAuthenticationKeyBinding,
    AuthenticationClaimReader,
    AuthenticationKeyBinding,
    ClaimRoutedAuthenticationKeyProvider,
    OpenBaoAuthenticationKeyBinding,
)
from .formal_services.material_request_contact_openbao import (
    OpenBaoContactBinding,
    OpenBaoMaterialRequestContactCipher,
)
from .openbao_pin_reader_candidate import read_openbao_reviewed_pins_candidate
from .openbao_registry_candidate import load_openbao_registry_candidate
from .openbao_runtime_transport import OpenBaoUnixDecryptTransport
from .openbao_settings import OpenBaoProductionSettings
from .openbao_transit_candidate import (
    OpenBaoDecryptTransport,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    OpenBaoTransitCandidate,
)


PROVIDER = "openbao_transit_v1"
CONTACT_PURPOSE = "material_request_contact"


class OpenBaoProductionUnavailable(RuntimeError):
    """Static safe error for an unconfigured or unproven C2 composition."""


@dataclass(frozen=True, slots=True)
class OpenBaoRuntimeAttestation:
    """External deployment evidence required before a release can be ready."""

    database_identity_verified: bool = False
    database_acl_verified: bool = False
    registry_mount_verified: bool = False
    token_projection_verified: bool = False
    transport_identity_verified: bool = False
    decrypt_verified: bool = False
    independent_pin_reviewed: bool = False

    def complete(self) -> bool:
        return all(
            type(value) is bool and value
            for value in (
                self.database_identity_verified,
                self.database_acl_verified,
                self.registry_mount_verified,
                self.token_projection_verified,
                self.transport_identity_verified,
                self.decrypt_verified,
                self.independent_pin_reviewed,
            )
        )


@dataclass(frozen=True, slots=True)
class OpenBaoReadiness:
    release_decision: Literal["ready", "not_ready"]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OpenBaoAuthenticationComposition:
    """Exact provider route and candidate retained by a caller-owned process."""

    key_provider: ClaimRoutedAuthenticationKeyProvider
    candidate: OpenBaoTransitCandidate
    active_binding: OpenBaoAuthenticationKeyBinding
    application_key_versions: tuple[int, ...]
    bindings: tuple[AuthenticationKeyBinding, ...]


@dataclass(frozen=True, slots=True)
class OpenBaoContactComposition:
    """Exact OpenBao contact-v2 cipher and its reviewed candidate."""

    cipher: OpenBaoMaterialRequestContactCipher
    candidate: OpenBaoTransitCandidate
    active_binding: OpenBaoContactBinding
    application_key_versions: tuple[int, ...]


def assess_openbao_readiness(
    settings: OpenBaoProductionSettings,
    *,
    composition: OpenBaoAuthenticationComposition | None = None,
    attestation: OpenBaoRuntimeAttestation | None = None,
) -> OpenBaoReadiness:
    """Produce a conservative, side-effect-free readiness result."""

    reasons = list(settings.validation_errors())
    if not reasons and settings.purpose != AUTHENTICATION_PURPOSE:
        reasons.append("wrong_composition_purpose")
    if reasons:
        return OpenBaoReadiness("not_ready", tuple(dict.fromkeys(reasons)))
    if composition is None:
        reasons.append("composition_not_attested")
    elif not _composition_matches_settings(settings, composition):
        reasons.append("composition_coordinate_mismatch")
    if attestation is None:
        reasons.append("runtime_evidence_missing")
    elif not attestation.complete():
        reasons.append("runtime_evidence_incomplete")
    if reasons:
        return OpenBaoReadiness("not_ready", tuple(dict.fromkeys(reasons)))
    return OpenBaoReadiness("ready", ())


def assess_openbao_contact_readiness(
    settings: OpenBaoProductionSettings,
    *,
    composition: OpenBaoContactComposition | None = None,
    attestation: OpenBaoRuntimeAttestation | None = None,
) -> OpenBaoReadiness:
    """Side-effect-free contact-v2 readiness; disabled by default."""

    reasons = list(settings.validation_errors())
    if not reasons and settings.purpose != CONTACT_PURPOSE:
        reasons.append("wrong_composition_purpose")
    if not reasons and composition is None:
        reasons.append("composition_not_attested")
    elif not reasons and not _contact_composition_matches_settings(settings, composition):
        reasons.append("composition_coordinate_mismatch")
    if attestation is None:
        reasons.append("runtime_evidence_missing")
    elif not attestation.complete():
        reasons.append("runtime_evidence_incomplete")
    if reasons:
        return OpenBaoReadiness("not_ready", tuple(dict.fromkeys(reasons)))
    return OpenBaoReadiness("ready", ())


def _binding_matches_settings(
    settings: OpenBaoProductionSettings,
    binding: AuthenticationKeyBinding,
    version: int,
) -> bool:
    if type(binding) is AliyunAuthenticationKeyBinding:
        return binding.application_key_version == version
    if type(binding) is not OpenBaoAuthenticationKeyBinding:
        return False
    coordinate = binding.reviewed_pin.coordinate
    return (
        coordinate.purpose == settings.purpose
        and coordinate.environment == settings.environment
        and coordinate.provider_instance_id == settings.provider_instance_id
        and coordinate.key_path == "transit/keys/rsc-authentication-idempotency"
        and coordinate.application_key_version == version
    )


def _verified_authentication_candidate(
    candidate: OpenBaoTransitCandidate,
    bindings: tuple[AuthenticationKeyBinding, ...],
) -> OpenBaoTransitCandidate | None:
    """Snapshot entries and revalidate every pin against the frozen DB claims.

    Reuse the candidate constructor's coordinate, Transit version and digest
    checks without decrypting.  Callers must resolve this verified snapshot,
    rather than checking one mutable entry map and then resolving another.
    """

    if type(candidate) is not OpenBaoTransitCandidate or type(bindings) is not tuple:
        return None
    try:
        pins = tuple(
            binding.reviewed_pin for binding in bindings
            if type(binding) is OpenBaoAuthenticationKeyBinding
        )
        if not pins:
            return None
        expected_keys = {
            (pin.coordinate.purpose, pin.coordinate.application_key_version)
            for pin in pins
        }
        entries = getattr(candidate, "_entries", None)
        if not isinstance(entries, dict):
            return None
        snapshot = entries.copy()
        if len(expected_keys) != len(pins) or set(snapshot) != expected_keys:
            return None
        scope = pins[0].coordinate
        return OpenBaoTransitCandidate(
            environment=scope.environment,
            provider_instance_id=scope.provider_instance_id,
            entries=tuple(snapshot[key] for key in sorted(expected_keys)),
            reviewed_pins=pins,
            transport=getattr(candidate, "_transport", None),
        )
    except Exception:
        return None


def _composition_matches_settings(
    settings: OpenBaoProductionSettings,
    composition: OpenBaoAuthenticationComposition,
) -> bool:
    if type(composition) is not OpenBaoAuthenticationComposition:
        return False
    if (
        composition.application_key_versions != settings.application_key_versions
        or type(composition.bindings) is not tuple
        or len(composition.bindings) != len(settings.application_key_versions)
        or any(
            not _binding_matches_settings(settings, binding, version)
            for version, binding in zip(settings.application_key_versions, composition.bindings)
        )
        or type(composition.active_binding) is not OpenBaoAuthenticationKeyBinding
        or not _binding_matches_settings(
            settings, composition.active_binding,
            settings.active_application_key_version,
        )
        or type(composition.candidate) is not OpenBaoTransitCandidate
        or type(composition.key_provider) is not ClaimRoutedAuthenticationKeyProvider
    ):
        return False
    return (
        composition.active_binding in composition.bindings
        and _verified_authentication_candidate(
            composition.candidate, composition.bindings,
        ) is not None
    )


def _contact_composition_matches_settings(
    settings: OpenBaoProductionSettings,
    composition: OpenBaoContactComposition | None,
) -> bool:
    if (
        type(composition) is not OpenBaoContactComposition
        or type(composition.cipher) is not OpenBaoMaterialRequestContactCipher
        or type(composition.candidate) is not OpenBaoTransitCandidate
        or composition.application_key_versions != settings.application_key_versions
        or type(composition.active_binding) is not OpenBaoContactBinding
    ):
        return False
    coordinate = composition.active_binding.coordinate
    entries = getattr(composition.candidate, "_entries", None)
    if not isinstance(entries, dict):
        return False
    expected = {
        (settings.purpose, version) for version in settings.application_key_versions
    }
    if set(entries) != expected:
        return False
    active = entries.get((settings.purpose, settings.active_application_key_version))
    return (
        coordinate == getattr(active, "coordinate", None)
        and composition.active_binding.transit_key_version == getattr(active, "transit_key_version", None)
        and coordinate.environment == settings.environment
        and coordinate.provider_instance_id == settings.provider_instance_id
        and coordinate.purpose == CONTACT_PURPOSE
    )


def create_openbao_authentication_composition(
    settings: OpenBaoProductionSettings,
    *,
    db: Session | None = None,
    claim_reader: AuthenticationClaimReader | None = None,
    pin_reader: Callable[..., tuple[OpenBaoReviewedPin, ...]] = read_openbao_reviewed_pins_candidate,
    registry_loader: Callable[..., OpenBaoTransitCandidate] = load_openbao_registry_candidate,
    transport: OpenBaoDecryptTransport | None = None,
    transport_factory: Callable[..., OpenBaoDecryptTransport] = OpenBaoUnixDecryptTransport,
    candidate: OpenBaoTransitCandidate | None = None,
    aliyun_key_loader: Callable[[AliyunAuthenticationKeyBinding], bytes] | None = None,
) -> OpenBaoAuthenticationComposition | None:
    """Build the explicit C2 route, or ``None`` while the pilot is disabled.

    All application versions are supplied by the caller.  The factory reads
    exactly those claim rows and asks the OpenBao pin reader for exactly the
    OpenBao subset.  An Aliyun historical row is accepted only when an explicit
    Aliyun loader is supplied; it is never used as fallback for OpenBao.
    """

    if not isinstance(settings, OpenBaoProductionSettings):
        return None
    if not settings.is_complete() or settings.purpose != AUTHENTICATION_PURPOSE:
        return None
    result: OpenBaoAuthenticationComposition | None = None
    try:
        reader = claim_reader
        if reader is None:
            if not isinstance(db, Session):
                raise ValueError("database claim reader is required")
            reader = DatabaseAuthenticationClaimReader(
                db,
                environment=settings.environment,
                openbao_provider_instance_id=settings.provider_instance_id,
            )
        bindings: dict[int, AuthenticationKeyBinding] = {}
        for version in settings.application_key_versions:
            binding = reader(AUTHENTICATION_PURPOSE, version)
            if (
                type(binding) not in (AliyunAuthenticationKeyBinding, OpenBaoAuthenticationKeyBinding)
                or binding.application_key_version != version
                or not _binding_matches_settings(settings, binding, version)
            ):
                raise ValueError("claim coordinate mismatch")
            bindings[version] = binding
        active = bindings[settings.active_application_key_version]
        if type(active) is not OpenBaoAuthenticationKeyBinding:
            raise ValueError("active provider must be OpenBao")
        bao_bindings = tuple(
            binding for binding in bindings.values()
            if type(binding) is OpenBaoAuthenticationKeyBinding
        )
        expected_coordinates = tuple(binding.reviewed_pin.coordinate for binding in bao_bindings)
        if not expected_coordinates:
            raise ValueError("OpenBao coordinates are missing")
        pins = None
        if candidate is None:
            if pin_reader is None:
                raise ValueError("independent pin reader is required")
            if db is None:
                raise ValueError("database is required for independent pins")
            pins = pin_reader(
                db,
                environment=settings.environment,
                provider_instance_id=settings.provider_instance_id,
                expected_coordinates=expected_coordinates,
            )
            if transport is None:
                transport = transport_factory(
                    socket_path=settings.socket_path,
                    token_file=settings.token_file,
                    api_uid=settings.api_uid,
                    bao_uid=settings.bao_uid,
                    bao_gid=settings.bao_gid,
                    shared_gid=settings.shared_gid,
                    token_projector_uid=settings.token_projector_uid,
                )
            candidate = registry_loader(
                registry_path=settings.registry_path,
                environment=settings.environment,
                provider_instance_id=settings.provider_instance_id,
                reviewed_pins=pins,
                transport=transport,
            )
        if type(candidate) is not OpenBaoTransitCandidate:
            raise ValueError("OpenBao candidate is unavailable")
        checked_bindings = tuple(
            bindings[version] for version in settings.application_key_versions
        )
        candidate = _verified_authentication_candidate(candidate, checked_bindings)
        if candidate is None:
            raise ValueError("OpenBao independent pin binding is unavailable")
        if any(type(binding) is AliyunAuthenticationKeyBinding for binding in bindings.values()) and not callable(aliyun_key_loader):
            raise ValueError("historical Aliyun loader is required")

        def checked_reader(purpose: str, version: int) -> AuthenticationKeyBinding:
            if (
                purpose != AUTHENTICATION_PURPOSE
                or type(version) is not int
                or version not in bindings
            ):
                raise OpenBaoProductionUnavailable("OpenBao key is unavailable")
            binding = reader(purpose, version)
            if (
                type(binding) not in (AliyunAuthenticationKeyBinding, OpenBaoAuthenticationKeyBinding)
                or binding != bindings[version]
            ):
                raise OpenBaoProductionUnavailable("OpenBao key is unavailable")
            return binding

        def openbao_loader(binding: OpenBaoAuthenticationKeyBinding) -> bytes:
            if (
                type(binding) is not OpenBaoAuthenticationKeyBinding
                or binding != bindings.get(binding.application_key_version)
            ):
                raise OpenBaoProductionUnavailable("OpenBao key is unavailable")
            verified = _verified_authentication_candidate(candidate, checked_bindings)
            if verified is None:
                raise OpenBaoProductionUnavailable("OpenBao key is unavailable")
            coordinate = binding.reviewed_pin.coordinate
            return verified.resolve(coordinate.purpose, coordinate.application_key_version)

        result = OpenBaoAuthenticationComposition(
            key_provider=ClaimRoutedAuthenticationKeyProvider(
                active_binding=active,
                claim_reader=checked_reader,
                aliyun_key_loader=aliyun_key_loader,
                openbao_key_loader=openbao_loader,
            ),
            candidate=candidate,
            active_binding=active,
            application_key_versions=settings.application_key_versions,
            bindings=checked_bindings,
        )
    except Exception:
        pass
    return result


def create_openbao_contact_composition(
    settings: OpenBaoProductionSettings,
    *,
    db: Session | None = None,
    reviewed_pins: tuple[OpenBaoReviewedPin, ...] | None = None,
    pin_reader: Callable[..., tuple[OpenBaoReviewedPin, ...]] = read_openbao_reviewed_pins_candidate,
    registry_loader: Callable[..., OpenBaoTransitCandidate] = load_openbao_registry_candidate,
    transport: OpenBaoDecryptTransport | None = None,
    transport_factory: Callable[..., OpenBaoDecryptTransport] = OpenBaoUnixDecryptTransport,
    candidate: OpenBaoTransitCandidate | None = None,
    legacy_cipher: object | None = None,
    legacy_kms_key_id: str | None = None,
) -> OpenBaoContactComposition | None:
    """Build the explicit contact-v2 route, without a legacy fallback.

    The caller supplies the exact application versions.  A candidate supplied
    by a reviewed loader is accepted only when its complete entry set matches
    those versions and the deployment scope.  If no candidate is supplied, the
    independent DB pin reader and explicit registry/transport factories are
    invoked in that order.  No decrypt request is made during construction.
    """

    if not isinstance(settings, OpenBaoProductionSettings):
        return None
    if not settings.is_complete() or settings.purpose != CONTACT_PURPOSE:
        return None
    if (legacy_cipher is None) != (legacy_kms_key_id is None):
        return None
    result: OpenBaoContactComposition | None = None
    try:
        pins = reviewed_pins
        if candidate is None:
            if db is None:
                raise ValueError("database is required for independent pins")
            coordinates = tuple(
                OpenBaoKeyCoordinate(
                    CONTACT_PURPOSE,
                    settings.environment,
                    settings.provider_instance_id,
                    version,
                )
                for version in settings.application_key_versions
            )
            pins = pin_reader(
                db,
                environment=settings.environment,
                provider_instance_id=settings.provider_instance_id,
                expected_coordinates=coordinates,
            )
            if transport is None:
                transport = transport_factory(
                    socket_path=settings.socket_path,
                    token_file=settings.token_file,
                    api_uid=settings.api_uid,
                    bao_uid=settings.bao_uid,
                    bao_gid=settings.bao_gid,
                    shared_gid=settings.shared_gid,
                    token_projector_uid=settings.token_projector_uid,
                )
            candidate = registry_loader(
                registry_path=settings.registry_path,
                environment=settings.environment,
                provider_instance_id=settings.provider_instance_id,
                reviewed_pins=pins,
                transport=transport,
            )
        if type(candidate) is not OpenBaoTransitCandidate:
            raise ValueError("OpenBao candidate is unavailable")
        if (
            type(pins) is not tuple
            or len(pins) != len(settings.application_key_versions)
            or any(type(pin) is not OpenBaoReviewedPin for pin in pins)
        ):
            raise ValueError("independent contact pins are required")
        entries = getattr(candidate, "_entries", None)
        if not isinstance(entries, dict):
            raise ValueError("OpenBao candidate entries are unavailable")
        expected_keys = {
            (CONTACT_PURPOSE, version) for version in settings.application_key_versions
        }
        if set(entries) != expected_keys:
            raise ValueError("contact exact coordinate set is unavailable")
        checked_entries = tuple(entries[key] for key in sorted(expected_keys, key=lambda item: item[1]))
        for entry in checked_entries:
            coordinate = entry.coordinate
            if (
                coordinate.purpose != CONTACT_PURPOSE
                or coordinate.environment != settings.environment
                or coordinate.provider_instance_id != settings.provider_instance_id
                or coordinate.application_key_version not in settings.application_key_versions
            ):
                raise ValueError("contact scope mismatch")
        active_entry = entries[(CONTACT_PURPOSE, settings.active_application_key_version)]
        active = OpenBaoContactBinding(
            active_entry.coordinate,
            active_entry.transit_key_version,
        )
        result = OpenBaoContactComposition(
            cipher=OpenBaoMaterialRequestContactCipher(
                environment=settings.environment,
                provider_instance_id=settings.provider_instance_id,
                active_application_key_version=settings.active_application_key_version,
                entries=checked_entries,
                reviewed_pins=pins,
                transport=transport if transport is not None else getattr(candidate, "_transport", None),
                legacy_cipher=legacy_cipher,
                legacy_kms_key_id=legacy_kms_key_id,
            ),
            candidate=candidate,
            active_binding=active,
            application_key_versions=settings.application_key_versions,
        )
    except Exception:
        pass
    return result


__all__ = [
    "OpenBaoAuthenticationComposition",
    "OpenBaoContactComposition",
    "OpenBaoProductionSettings",
    "OpenBaoProductionUnavailable",
    "OpenBaoReadiness",
    "OpenBaoRuntimeAttestation",
    "assess_openbao_readiness",
    "assess_openbao_contact_readiness",
    "create_openbao_authentication_composition",
    "create_openbao_contact_composition",
]
