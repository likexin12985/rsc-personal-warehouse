"""Pure non-secret OpenBao coordinates; no models, database or runtime I/O."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .openbao_transit_candidate import OpenBaoKeyCoordinate


_ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})
_PATH_PART = re.compile(r"^[^/\x00]+$", re.ASCII)


def _absolute_path(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 4096 or not value.startswith("/"):
        return False
    parts = value.split("/")[1:]
    return bool(parts) and all(
        part not in {".", ".."} and _PATH_PART.fullmatch(part) for part in parts
    )


@dataclass(frozen=True, slots=True)
class OpenBaoProductionSettings:
    """Non-secret OpenBao deployment coordinates.

    ``enabled`` defaults to false intentionally. The token itself is never a
    field here; configuration completeness does not establish runtime identity,
    independent pin review, successful decryption or release readiness.
    """

    enabled: bool = False
    environment: str = "production"
    purpose: str = "authentication_idempotency"
    provider_instance_id: str = ""
    registry_path: str = ""
    socket_path: str = ""
    token_file: str = ""
    api_uid: int = 0
    bao_uid: int = 0
    bao_gid: int = 0
    shared_gid: int = 0
    token_projector_uid: int = 0
    application_key_versions: tuple[int, ...] = ()
    active_application_key_version: int = 0

    def validation_errors(self) -> tuple[str, ...]:
        """Return stable reason codes without touching files or the network."""

        if self.enabled is not True:
            return ("disabled",)
        errors: list[str] = []
        if self.environment not in _ENVIRONMENTS:
            errors.append("invalid_environment")
        try:
            OpenBaoKeyCoordinate(
                self.purpose,
                self.environment,
                self.provider_instance_id,
                1,
            )
        except Exception:
            errors.append("invalid_provider_instance")
        if not all(_absolute_path(item) for item in (
            self.registry_path, self.socket_path, self.token_file,
        )):
            errors.append("invalid_runtime_path")
        elif len({self.registry_path, self.socket_path, self.token_file}) != 3:
            errors.append("runtime_paths_must_be_distinct")
        identities = (
            self.api_uid, self.bao_uid, self.bao_gid,
            self.shared_gid, self.token_projector_uid,
        )
        if any(type(value) is not int or value <= 0 for value in identities):
            errors.append("invalid_runtime_identity")
        if len({self.api_uid, self.bao_uid, self.token_projector_uid}) != 3:
            errors.append("runtime_uids_must_be_distinct")
        versions = self.application_key_versions
        if (
            type(versions) is not tuple or not 1 <= len(versions) <= 64
            or any(type(value) is not int or isinstance(value, bool) or not 1 <= value <= 2_147_483_647 for value in versions)
            or tuple(sorted(set(versions))) != versions
            or self.active_application_key_version not in versions
        ):
            errors.append("invalid_exact_application_versions")
        return tuple(errors)

    def is_complete(self) -> bool:
        return not self.validation_errors()
