"""Thread-safe, fail-closed KMS readiness probing.

The gate deliberately knows nothing about Alibaba Cloud, FastAPI, databases, or
business state.  Its caller supplies an exact set of already-authorized KMS
application-key coordinates and a loader that resolves one coordinate to bytes.

Only the boolean result, an opaque SHA-256 coordinate-set fingerprint, and
monotonic expiry times are cached.  Resolved key bytes and provider responses
are never retained.  Construction performs no I/O; callers can force the first
probe during application startup by calling :meth:`KmsReadinessGate.is_ready`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import threading
import time
from typing import Final, TypeAlias


KmsCoordinate: TypeAlias = tuple[str, str, int]
KmsKeyLoader: TypeAlias = Callable[[str, str, int], bytes]

DEFAULT_SUCCESS_TTL_SECONDS: Final[float] = 60.0
DEFAULT_FAILURE_TTL_SECONDS: Final[float] = 10.0
DEFAULT_WAIT_BUDGET_SECONDS: Final[float] = 4.0
DEFAULT_PROBE_BUDGET_SECONDS: Final[float] = 4.0
MAX_PARALLEL_PROBES: Final[int] = 4


class KmsReadinessGate:
    """Cache an exact-coordinate KMS Decrypt readiness result with single-flight.

    ``wait_budget_seconds`` bounds only how long a concurrent caller waits for
    the probe already owned by another thread.  The loader must enforce its own
    provider connect/read deadline; Python cannot safely cancel a blocking SDK
    call from this coordination primitive.
    """

    def __init__(
        self,
        *,
        loader: KmsKeyLoader,
        monotonic: Callable[[], float] = time.monotonic,
        success_ttl_seconds: float = DEFAULT_SUCCESS_TTL_SECONDS,
        failure_ttl_seconds: float = DEFAULT_FAILURE_TTL_SECONDS,
        wait_budget_seconds: float = DEFAULT_WAIT_BUDGET_SECONDS,
        probe_budget_seconds: float = DEFAULT_PROBE_BUDGET_SECONDS,
    ) -> None:
        if not callable(loader):
            raise TypeError("loader must be callable")
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")
        self._success_ttl_seconds = _positive_seconds(
            success_ttl_seconds,
            field="success_ttl_seconds",
        )
        self._failure_ttl_seconds = _positive_seconds(
            failure_ttl_seconds,
            field="failure_ttl_seconds",
        )
        self._wait_budget_seconds = _positive_seconds(
            wait_budget_seconds,
            field="wait_budget_seconds",
        )
        self._probe_budget_seconds = _positive_seconds(
            probe_budget_seconds,
            field="probe_budget_seconds",
        )
        self._loader = loader
        self._monotonic = monotonic
        self._condition = threading.Condition()
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_PARALLEL_PROBES,
            thread_name_prefix="kms-readiness",
        )
        self._cached_fingerprint: bytes | None = None
        self._cached_ready: bool | None = None
        self._cache_expires_at = 0.0
        self._inflight_fingerprint: bytes | None = None
        self._provider_pending = 0
        self._closed = False

    def close(self) -> None:
        """Reject new probes and cancel queued work during process shutdown."""

        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def is_ready(self, required_coordinates: Iterable[KmsCoordinate]) -> bool:
        """Return whether every exact coordinate can currently be decrypted.

        Invalid/empty coordinate sets, loader failures, clock failures, and a
        concurrent wait timeout all fail closed as ``False``.  Provider error
        details are intentionally not exposed through this interface.
        """

        try:
            coordinates, fingerprint = _prepare_coordinates(required_coordinates)
            if not coordinates:
                return False
            return self._check(coordinates, fingerprint)
        except Exception:
            return False

    def _check(
        self,
        coordinates: tuple[KmsCoordinate, ...],
        fingerprint: bytes,
    ) -> bool:
        wait_deadline: float | None = None
        while True:
            now = self._monotonic()
            with self._condition:
                if self._closed:
                    return False
                if (
                    self._cached_fingerprint == fingerprint
                    and self._cached_ready is not None
                    and now < self._cache_expires_at
                ):
                    return self._cached_ready

                if self._inflight_fingerprint is None:
                    # A timed-out provider generation may still own up to four
                    # bounded worker slots.  Never start another wave until all
                    # of those tasks have really ended or been cancelled.
                    if self._provider_pending:
                        return False
                    self._inflight_fingerprint = fingerprint
                    break

                if wait_deadline is None:
                    wait_deadline = now + self._wait_budget_seconds
                remaining = wait_deadline - now
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=remaining)

        try:
            ready = self._probe(coordinates)
        except Exception:
            # Executor shutdown, partial submission, and provider coordination
            # failures must release the single-flight owner just like an
            # ordinary failed probe.  Otherwise one infrastructure race could
            # leave every later readiness request waiting on a probe that no
            # longer exists.
            ready = False
        try:
            checked_at = self._monotonic()
        except Exception:
            checked_at = 0.0
            ready = False
        ttl = (
            self._success_ttl_seconds if ready else self._failure_ttl_seconds
        )
        with self._condition:
            # This thread is the sole probe owner.  Keep the defensive equality
            # check so a future cancellation API cannot publish another probe's
            # result accidentally.
            if self._inflight_fingerprint == fingerprint:
                self._cached_fingerprint = fingerprint
                self._cached_ready = ready
                self._cache_expires_at = checked_at + ttl
                self._inflight_fingerprint = None
                self._condition.notify_all()
        return ready

    def _probe(self, coordinates: tuple[KmsCoordinate, ...]) -> bool:
        futures = [
            self._executor.submit(self._probe_one, coordinate)
            for coordinate in coordinates
        ]
        with self._condition:
            self._provider_pending = len(futures)
        for future in futures:
            future.add_done_callback(self._provider_future_done)
        try:
            completed, pending = wait(
                futures,
                timeout=self._probe_budget_seconds,
                return_when=ALL_COMPLETED,
            )
            if pending:
                for future in pending:
                    future.cancel()
                return False
            for future in completed:
                try:
                    if future.result() is not True:
                        return False
                except Exception:
                    return False
            return True
        finally:
            futures.clear()

    def _probe_one(self, coordinate: KmsCoordinate) -> bool:
        key_material: bytes | None = None
        try:
            key_material = self._loader(*coordinate)
            return isinstance(key_material, bytes) and bool(key_material)
        except Exception:
            return False
        finally:
            key_material = None

    def _provider_future_done(self, _future: object) -> None:
        with self._condition:
            if self._provider_pending > 0:
                self._provider_pending -= 1
            self._condition.notify_all()


def _prepare_coordinates(
    required_coordinates: Iterable[KmsCoordinate],
) -> tuple[tuple[KmsCoordinate, ...], bytes]:
    normalized: set[KmsCoordinate] = set()
    for raw_coordinate in required_coordinates:
        if not isinstance(raw_coordinate, (tuple, list)) or len(raw_coordinate) != 3:
            raise ValueError("invalid KMS coordinate")
        purpose, key_id, version = raw_coordinate
        if (
            not isinstance(purpose, str)
            or not purpose
            or not isinstance(key_id, str)
            or not key_id
            or not isinstance(version, int)
            or isinstance(version, bool)
            or version <= 0
        ):
            raise ValueError("invalid KMS coordinate")
        normalized.add((purpose, key_id, version))
    coordinates = tuple(sorted(normalized))
    canonical = json.dumps(
        coordinates,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return coordinates, hashlib.sha256(canonical).digest()


def _positive_seconds(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a positive number")
    checked = float(value)
    if checked <= 0 or checked == float("inf") or checked != checked:
        raise ValueError(f"{field} must be a finite positive number")
    return checked


__all__ = [
    "DEFAULT_FAILURE_TTL_SECONDS",
    "DEFAULT_PROBE_BUDGET_SECONDS",
    "DEFAULT_SUCCESS_TTL_SECONDS",
    "DEFAULT_WAIT_BUDGET_SECONDS",
    "KmsCoordinate",
    "KmsReadinessGate",
    "MAX_PARALLEL_PROBES",
]
