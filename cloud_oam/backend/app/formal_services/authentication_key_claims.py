"""Explicit C1-claim routing for authentication response keys.

This module does not read Settings, databases, registries or credentials. The
composition root supplies an independent SELECT-only claims/pins reader and
bounded provider loaders. A registry is never evidence of provider ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Protocol, TypeAlias

from ..openbao_transit_candidate import OpenBaoReviewedPin
from .authentication_idempotency import (
    AuthenticationEncryptionKey, AuthenticationEncryptionKeyUnavailable,
)


AUTHENTICATION_PURPOSE = "authentication_idempotency"
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{2,255}", re.ASCII)
_KMS_VERSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,127}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")


def _version(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


@dataclass(frozen=True, slots=True)
class AliyunAuthenticationKeyBinding:
    """Exact non-secret immutable pin returned by the independent DB reader."""

    application_key_version: int
    kms_key_id: str
    kms_key_version_id: str
    ciphertext_sha256: str

    def __post_init__(self) -> None:
        if (
            not _version(self.application_key_version)
            or type(self.kms_key_id) is not str or _KEY_ID.fullmatch(self.kms_key_id) is None
            or any(marker in self.kms_key_id.lower() for marker in _PLACEHOLDERS)
            or type(self.kms_key_version_id) is not str
            or _KMS_VERSION_ID.fullmatch(self.kms_key_version_id) is None
            or type(self.ciphertext_sha256) is not str
            or _SHA256.fullmatch(self.ciphertext_sha256) is None
        ):
            raise AuthenticationEncryptionKeyUnavailable("authentication key binding is invalid")

    @property
    def provider(self) -> str:
        return "aliyun_kms"

    @property
    def purpose(self) -> str:
        return AUTHENTICATION_PURPOSE


@dataclass(frozen=True, slots=True)
class OpenBaoAuthenticationKeyBinding:
    reviewed_pin: OpenBaoReviewedPin

    def __post_init__(self) -> None:
        if (
            type(self.reviewed_pin) is not OpenBaoReviewedPin
            or self.reviewed_pin.coordinate.purpose != AUTHENTICATION_PURPOSE
        ):
            raise AuthenticationEncryptionKeyUnavailable("authentication key binding is invalid")

    @property
    def application_key_version(self) -> int:
        return self.reviewed_pin.coordinate.application_key_version

    @property
    def provider(self) -> str:
        return "openbao_transit_v1"

    @property
    def purpose(self) -> str:
        return AUTHENTICATION_PURPOSE


AuthenticationKeyBinding: TypeAlias = AliyunAuthenticationKeyBinding | OpenBaoAuthenticationKeyBinding


class AuthenticationClaimReader(Protocol):
    """SELECT claim by exact purpose/version and JOIN its immutable pin.

    Reject zero/multiple rows, provider mismatch, cross-provider ambiguity,
    orphan claims, ciphertext-hash/created_at mismatch and wrong coordinates.
    Return a detached typed binding. Read no expected values from the registry.
    Database runtime role/catalog validation remains the composition root's
    responsibility; a callback type cannot attest deployment provenance.
    """

    def __call__(self, purpose: str, application_key_version: int) -> AuthenticationKeyBinding | None: ...


class ClaimRoutedAuthenticationKeyProvider:
    """Exact provider selection before any DEK lookup, with no fallback.

    ``active_binding`` must match configured active provider/version plus its
    independent DB pin. Each loader receives that full expected binding and
    must compare its registry entry against it before decrypting. OpenBao's
    real Transit version is in its pin; it is not the application version.

    The existing AES cipher retains resolved keys only for one request. Its
    version-only cache is unambiguous because C1 globally owns each
    (authentication_idempotency, application_version) across both providers.
    """

    def __init__(
        self, *, active_binding: AuthenticationKeyBinding,
        claim_reader: AuthenticationClaimReader,
        aliyun_key_loader: Callable[[AliyunAuthenticationKeyBinding], bytes] | None = None,
        openbao_key_loader: Callable[[OpenBaoAuthenticationKeyBinding], bytes] | None = None,
    ) -> None:
        if (
            type(active_binding) not in (AliyunAuthenticationKeyBinding, OpenBaoAuthenticationKeyBinding)
            or not callable(claim_reader)
            or (aliyun_key_loader is not None and not callable(aliyun_key_loader))
            or (openbao_key_loader is not None and not callable(openbao_key_loader))
        ):
            raise AuthenticationEncryptionKeyUnavailable("authentication key routing is invalid")
        self._active = active_binding
        self._reader = claim_reader
        self._aliyun_loader = aliyun_key_loader
        self._openbao_loader = openbao_key_loader

    def current_key(self) -> AuthenticationEncryptionKey:
        return self.key_for_version(self._active.application_key_version)

    def key_for_version(self, version: int) -> AuthenticationEncryptionKey:
        material = None
        try:
            if not _version(version):
                raise ValueError("unavailable")
            binding = self._reader(AUTHENTICATION_PURPOSE, version)
            if (
                type(binding) not in (AliyunAuthenticationKeyBinding, OpenBaoAuthenticationKeyBinding)
                or binding.application_key_version != version
                or (version == self._active.application_key_version and binding != self._active)
            ):
                raise ValueError("unavailable")
            if type(binding) is AliyunAuthenticationKeyBinding:
                loader = self._aliyun_loader
            else:
                loader = self._openbao_loader
            if loader is None:
                raise ValueError("unavailable")
            key = loader(binding)
            if type(key) is not bytes or len(key) != 32:
                raise ValueError("unavailable")
            material = AuthenticationEncryptionKey(version=version, key=key)
        except Exception:
            pass
        # Outside the except block: neither reader/transport errors nor secret
        # arguments remain in an ordinary exception cause/context chain.
        if material is None:
            raise AuthenticationEncryptionKeyUnavailable("authentication encryption key is unavailable")
        return material
