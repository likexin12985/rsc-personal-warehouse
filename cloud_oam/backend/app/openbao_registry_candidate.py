"""Strict file registry loader for the unregistered OpenBao candidate only.

No Settings/factory/readiness/database/credential integration exists here.
Callers supply a physical absolute path (for example resolve their OWN trusted
temporary directory before calling; this loader never resolves input symlinks),
independently reviewed pins, expected scope and an explicit transport.

Schema rsc.openbao.wrapped-data-key-registry.v1:
  root: schema, provider='openbao_transit_v1', entries
  entry: purpose, environment, provider_instance_id, application_key_version,
         key_path, transit_key_version, ciphertext, context_b64,
         associated_data_b64
Only original wrapped ciphertext and public binding metadata are accepted.
Pins are not stored in, generated from, or inferred from this registry.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Final

from .openbao_transit_candidate import (
    PROVIDER,
    OpenBaoCandidateConfigurationError,
    OpenBaoDecryptTransport,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    OpenBaoTransitCandidate,
    OpenBaoWrappedKey,
    associated_data_b64,
    context_b64,
)


REGISTRY_SCHEMA: Final = "rsc.openbao.wrapped-data-key-registry.v1"
MAX_REGISTRY_BYTES: Final = 1024 * 1024
_MAX_DEPTH = 8
_ENTRY_FIELDS = frozenset({
    "purpose", "environment", "provider_instance_id", "application_key_version",
    "key_path", "transit_key_version", "ciphertext", "context_b64",
    "associated_data_b64",
})


class OpenBaoRegistryCandidateError(OpenBaoCandidateConfigurationError):
    """Fixed safe failure, never a path, JSON excerpt or chained I/O error."""


def _reject() -> None:
    raise ValueError("invalid OpenBao candidate registry")


def _directory_metadata(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def _file_metadata(info):
    return (*_directory_metadata(info), info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _safe_ancestor(info, owner):
    # Root-owned sticky temp ancestors are acceptable only before the final
    # private parent; a sibling user cannot rename another user's 0700 child.
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner)
        or (info.st_mode & 0o022 and not sticky_root)
    ):
        _reject()


def _safe_file(info, owner):
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != owner
        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
        or not 1 <= info.st_size <= MAX_REGISTRY_BYTES
    ):
        _reject()


def _read_stable_file(registry_path: str | Path) -> bytes:
    if not isinstance(registry_path, (str, Path)):
        _reject()
    raw = os.fspath(registry_path)
    if (
        type(raw) is not str or not 1 <= len(raw) <= 4096
        or not raw.startswith("/") or "\x00" in raw
    ):
        _reject()
    parts = raw.split("/")[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        _reject()
    # Do not silently weaken the guard on a platform lacking required flags.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    owner = os.geteuid()
    fds = []
    chain = []
    try:
        root_fd = os.open("/", directory_flags)
        fds.append(root_fd)
        root_before = os.fstat(root_fd)
        _safe_ancestor(root_before, owner)
        parent_fd = root_fd
        for name in parts[:-1]:
            current_fd = os.open(name, directory_flags, dir_fd=parent_fd)
            fds.append(current_fd)
            info = os.fstat(current_fd)
            _safe_ancestor(info, owner)
            chain.append((parent_fd, name, current_fd, _directory_metadata(info)))
            parent_fd = current_fd
        parent = os.fstat(parent_fd)
        if parent.st_uid != owner or stat.S_IMODE(parent.st_mode) != 0o700:
            _reject()
        file_fd = os.open(parts[-1], file_flags, dir_fd=parent_fd)
        fds.append(file_fd)
        before = os.fstat(file_fd)
        _safe_file(before, owner)
        content = bytearray()
        # Read at most one byte beyond the original size; growing files cannot
        # force unbounded allocation, and shortened/grown input never parses.
        while len(content) <= before.st_size:
            chunk = os.read(file_fd, min(64 * 1024, before.st_size + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(file_fd)
        _safe_file(after, owner)
        pathname = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        if (
            len(content) != before.st_size or _file_metadata(before) != _file_metadata(after)
            or _file_metadata(after) != _file_metadata(pathname)
            or _directory_metadata(os.fstat(root_fd)) != _directory_metadata(root_before)
        ):
            _reject()
        # The open dirfds anchor traversal. Recheck every pathname so replacing
        # an ancestor/parent while reading cannot validate an orphaned file.
        for previous_fd, name, current_fd, expected in chain:
            current = os.fstat(current_fd)
            named = os.stat(name, dir_fd=previous_fd, follow_symlinks=False)
            if _directory_metadata(current) != expected or _directory_metadata(named) != expected:
                _reject()
        return bytes(content)
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject()
        result[key] = value
    return result


def _integer(value):
    if len(value) > 10:
        _reject()
    return int(value)


def _not_integer(_value):
    _reject()


def _check_depth(raw: bytes) -> None:
    # Bound parser recursion before json.loads. Braces inside escaped strings
    # do not count; complete JSON validity is still checked by json.loads.
    depth = 0
    in_string = escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                in_string = False
        elif byte == 34:
            in_string = True
        elif byte in (91, 123):
            depth += 1
            if depth > _MAX_DEPTH:
                _reject()
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                _reject()


def _parse_entries(raw: bytes) -> tuple[OpenBaoWrappedKey, ...]:
    _check_depth(raw)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_int=_integer, parse_float=_not_integer,
                          parse_constant=_not_integer)
    if (
        type(document) is not dict or set(document) != {"schema", "provider", "entries"}
        or document["schema"] != REGISTRY_SCHEMA or document["provider"] != PROVIDER
        or type(document["entries"]) is not list or not 1 <= len(document["entries"]) <= 64
    ):
        _reject()
    entries = []
    for item in document["entries"]:
        if type(item) is not dict or set(item) != _ENTRY_FIELDS:
            _reject()
        coordinate = OpenBaoKeyCoordinate(
            purpose=item["purpose"], environment=item["environment"],
            provider_instance_id=item["provider_instance_id"],
            application_key_version=item["application_key_version"],
        )
        if (
            item["key_path"] != coordinate.key_path
            or item["context_b64"] != context_b64(coordinate)
            or item["associated_data_b64"] != associated_data_b64(coordinate)
        ):
            _reject()
        entries.append(OpenBaoWrappedKey(coordinate, item["ciphertext"], item["transit_key_version"]))
    return tuple(entries)


def load_openbao_registry_candidate(
    *,
    registry_path: str | Path,
    environment: str,
    provider_instance_id: str,
    reviewed_pins: tuple[OpenBaoReviewedPin, ...],
    transport: OpenBaoDecryptTransport,
) -> OpenBaoTransitCandidate:
    """Load one stable registry; validate externally supplied pins, no decrypt.

    The resulting candidate retains exact historical application versions in
    memory. Later file changes require a new explicit load, never auto-reload.
    POSIX owner/mode/inode checks do not claim ACL, mount or production readiness.
    """
    result = None
    try:
        # An absent/replaced pin source is rejected before any filesystem read.
        if (
            type(reviewed_pins) is not tuple or not 1 <= len(reviewed_pins) <= 64
            or any(type(pin) is not OpenBaoReviewedPin for pin in reviewed_pins)
        ):
            _reject()
        OpenBaoKeyCoordinate("authentication_idempotency", environment, provider_instance_id, 1)
        entries = _parse_entries(_read_stable_file(registry_path))
        result = OpenBaoTransitCandidate(
            environment=environment, provider_instance_id=provider_instance_id,
            entries=entries, reviewed_pins=reviewed_pins, transport=transport,
        )
    except Exception:
        pass
    if result is None:
        # Do not retain JSONDecodeError.doc, I/O paths or supplied material in
        # exception __context__/__cause__ for downstream exception collectors.
        raise OpenBaoRegistryCandidateError("OpenBao candidate registry is unavailable")
    return result
