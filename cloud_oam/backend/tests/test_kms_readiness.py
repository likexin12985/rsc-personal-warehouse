from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app.kms_readiness import (
    DEFAULT_FAILURE_TTL_SECONDS,
    DEFAULT_PROBE_BUDGET_SECONDS,
    DEFAULT_SUCCESS_TTL_SECONDS,
    DEFAULT_WAIT_BUDGET_SECONDS,
    KmsReadinessGate,
)


AUTH = ("authentication_idempotency", "kms-auth-key", 1)
CONTACT = ("material_request_contact", "kms-contact-key", 1)
ROTATED_CONTACT = ("material_request_contact", "kms-contact-key", 2)
KEY_BYTES = bytes(range(32))


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_defaults_and_duplicate_coordinates_probe_once() -> None:
    calls: list[tuple[str, str, int]] = []

    def loader(*coordinate):
        calls.append(coordinate)
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader)

    assert DEFAULT_SUCCESS_TTL_SECONDS == 60.0
    assert DEFAULT_FAILURE_TTL_SECONDS == 10.0
    assert DEFAULT_WAIT_BUDGET_SECONDS == 4.0
    assert DEFAULT_PROBE_BUDGET_SECONDS == 4.0
    assert gate.is_ready([CONTACT, AUTH, AUTH]) is True
    assert sorted(calls) == [AUTH, CONTACT]


def test_success_is_cached_until_ttl_then_reprobed() -> None:
    clock = FakeClock()
    calls: list[tuple[str, str, int]] = []

    def loader(*coordinate):
        calls.append(coordinate)
        return KEY_BYTES

    gate = KmsReadinessGate(
        loader=loader,
        monotonic=clock,
        success_ttl_seconds=5,
    )

    assert gate.is_ready([AUTH]) is True
    clock.advance(4.999)
    assert gate.is_ready([AUTH]) is True
    assert calls == [AUTH]
    clock.advance(0.001)
    assert gate.is_ready([AUTH]) is True
    assert calls == [AUTH, AUTH]


def test_failure_is_short_cached_and_recovers_after_ttl() -> None:
    clock = FakeClock()
    calls = 0

    def loader(*_coordinate):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("provider details must not escape")
        return KEY_BYTES

    gate = KmsReadinessGate(
        loader=loader,
        monotonic=clock,
        failure_ttl_seconds=2,
    )

    assert gate.is_ready([AUTH]) is False
    clock.advance(1.999)
    assert gate.is_ready([AUTH]) is False
    assert calls == 1
    clock.advance(0.001)
    assert gate.is_ready([AUTH]) is True
    assert calls == 2


def test_changed_coordinate_set_never_reuses_success_cache() -> None:
    clock = FakeClock()
    calls: list[tuple[str, str, int]] = []

    def loader(*coordinate):
        calls.append(coordinate)
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader, monotonic=clock)

    assert gate.is_ready([AUTH, CONTACT]) is True
    assert gate.is_ready([AUTH, CONTACT]) is True
    assert gate.is_ready([AUTH, CONTACT, ROTATED_CONTACT]) is True
    assert calls.count(AUTH) == 2
    assert calls.count(CONTACT) == 2
    assert calls.count(ROTATED_CONTACT) == 1


def test_twenty_concurrent_callers_share_one_probe_round() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, str, int]] = []
    calls_lock = threading.Lock()

    def loader(*coordinate):
        with calls_lock:
            calls.append(coordinate)
            first_call = len(calls) == 1
        if first_call:
            entered.set()
            assert release.wait(timeout=2)
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader, wait_budget_seconds=1)
    start = threading.Barrier(20)

    def check() -> bool:
        start.wait(timeout=2)
        return gate.is_ready([AUTH, CONTACT])

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(check) for _ in range(20)]
        assert entered.wait(timeout=2)
        release.set()
        assert [future.result(timeout=2) for future in futures] == [True] * 20

    assert sorted(calls) == [AUTH, CONTACT]


def test_many_coordinates_probe_in_parallel_within_total_budget() -> None:
    calls: list[tuple[str, str, int]] = []
    lock = threading.Lock()
    coordinates = [
        ("material_request_contact", f"kms-contact-{index}", 1)
        for index in range(8)
    ]

    def loader(*coordinate):
        time.sleep(0.05)
        with lock:
            calls.append(coordinate)
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader, probe_budget_seconds=0.2)
    started_at = time.monotonic()

    assert gate.is_ready(coordinates) is True
    assert time.monotonic() - started_at < 0.18
    assert sorted(calls) == sorted(coordinates)


def test_probe_owner_returns_false_at_total_budget() -> None:
    release = threading.Event()

    def loader(*_coordinate):
        release.wait(timeout=1)
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader, probe_budget_seconds=0.05)
    started_at = time.monotonic()
    try:
        assert gate.is_ready([AUTH, CONTACT]) is False
        elapsed = time.monotonic() - started_at
        assert 0.04 <= elapsed < 0.3
    finally:
        release.set()


def test_repeated_timeouts_never_start_an_unbounded_provider_wave() -> None:
    clock = FakeClock()
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum_active = 0
    calls = 0
    coordinates = [
        ("material_request_contact", f"kms-timeout-{index}", 1)
        for index in range(8)
    ]

    def loader(*_coordinate):
        nonlocal active, maximum_active, calls
        with lock:
            active += 1
            calls += 1
            maximum_active = max(maximum_active, active)
        try:
            release.wait(timeout=1)
            return KEY_BYTES
        finally:
            with lock:
                active -= 1

    gate = KmsReadinessGate(
        loader=loader,
        monotonic=clock,
        failure_ttl_seconds=1,
        probe_budget_seconds=0.03,
    )
    try:
        assert gate.is_ready(coordinates) is False
        first_wave_calls = calls
        assert 1 <= first_wave_calls <= 4
        for _ in range(5):
            clock.advance(2)
            assert gate.is_ready(coordinates) is False
        assert calls == first_wave_calls
        assert maximum_active <= 4
    finally:
        release.set()
        gate.close()


def test_closed_gate_rejects_without_calling_loader() -> None:
    calls = 0

    def loader(*_coordinate):
        nonlocal calls
        calls += 1
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader)
    gate.close()

    assert gate.is_ready([AUTH]) is False
    assert calls == 0


def test_probe_infrastructure_failure_releases_single_flight(monkeypatch) -> None:
    clock = FakeClock()
    calls = 0

    def loader(*_coordinate):
        nonlocal calls
        calls += 1
        return KEY_BYTES

    gate = KmsReadinessGate(
        loader=loader,
        monotonic=clock,
        failure_ttl_seconds=1,
    )
    original_probe = gate._probe
    monkeypatch.setattr(
        gate,
        "_probe",
        lambda _coordinates: (_ for _ in ()).throw(
            RuntimeError("executor submission failed")
        ),
    )

    assert gate.is_ready([AUTH]) is False
    clock.advance(1)
    monkeypatch.setattr(gate, "_probe", original_probe)

    assert gate.is_ready([AUTH]) is True
    assert calls == 1


def test_waiter_times_out_without_starting_second_probe() -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def loader(*_coordinate):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader, wait_budget_seconds=0.05)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(gate.is_ready, [AUTH])
        assert entered.wait(timeout=1)
        started_at = time.monotonic()
        assert gate.is_ready([AUTH]) is False
        elapsed = time.monotonic() - started_at
        assert elapsed >= 0.04
        assert elapsed < 0.5
        assert calls == 1
        release.set()
        assert owner.result(timeout=1) is True

    assert gate.is_ready([AUTH]) is True
    assert calls == 1


@pytest.mark.parametrize(
    "coordinates",
    [
        [],
        [("authentication_idempotency", "", 1)],
        [("authentication_idempotency", "kms-auth-key", True)],
        [("authentication_idempotency", "kms-auth-key", 0)],
    ],
)
def test_invalid_or_empty_coordinates_fail_closed_without_loader_call(
    coordinates,
) -> None:
    calls = 0

    def loader(*_coordinate):
        nonlocal calls
        calls += 1
        return KEY_BYTES

    gate = KmsReadinessGate(loader=loader)

    assert gate.is_ready(coordinates) is False
    assert calls == 0


def test_non_bytes_loader_result_fails_closed_and_is_failure_cached() -> None:
    clock = FakeClock()
    calls = 0

    def loader(*_coordinate):
        nonlocal calls
        calls += 1
        return None

    gate = KmsReadinessGate(loader=loader, monotonic=clock)

    assert gate.is_ready([AUTH]) is False
    assert gate.is_ready([AUTH]) is False
    assert calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("success_ttl_seconds", 0),
        ("failure_ttl_seconds", -1),
        ("wait_budget_seconds", float("inf")),
        ("probe_budget_seconds", 0),
    ],
)
def test_invalid_gate_timing_is_rejected(field, value) -> None:
    with pytest.raises((TypeError, ValueError)):
        KmsReadinessGate(loader=lambda *_: KEY_BYTES, **{field: value})
