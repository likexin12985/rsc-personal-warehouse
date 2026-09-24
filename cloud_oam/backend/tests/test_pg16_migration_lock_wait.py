from concurrent.futures import Future

import pytest

import pg16_migration_lock_wait as probe


@pytest.fixture
def clock(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    return elapsed


def test_slow_process_startup_waits_for_real_lock_and_returns_immediately(clock):
    future = Future()
    observed = {"pid": 123, "blocked_by": 456}
    result = probe.wait_for_migration_lock(
        future, lambda: observed if clock[0] >= 65 else None,
    )
    assert result is observed
    assert 65 <= clock[0] < 65.1
    assert not future.done()


def test_finished_child_is_not_a_lock_proof_even_when_observer_reports_one(clock):
    future = Future()
    future.set_result("completed")
    with pytest.raises(AssertionError, match="completed without"):
        probe.wait_for_migration_lock(future, lambda: True)


def test_child_failure_is_propagated_without_waiting_out_deadline(clock):
    future = Future()
    def observe():
        future.set_exception(RuntimeError("synthetic child failure"))
        return False
    with pytest.raises(RuntimeError, match="synthetic child failure"):
        probe.wait_for_migration_lock(future, observe)
    assert clock[0] < 0.1


def test_missing_lock_fails_at_bound_without_marking_child_terminal(clock):
    future = Future()
    with pytest.raises(TimeoutError, match="required database lock"):
        probe.wait_for_migration_lock(future, lambda: False, timeout_seconds=1)
    assert clock[0] == 1
    assert not future.done()


def test_observation_failure_is_not_masked_as_process_completion(clock):
    def observe():
        raise RuntimeError("synthetic observation failure")
    with pytest.raises(RuntimeError, match="observation failure"):
        probe.wait_for_migration_lock(Future(), observe)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_unbounded_or_nonpositive_wait_is_rejected(timeout, clock):
    with pytest.raises(ValueError, match="positive and finite"):
        probe.wait_for_migration_lock(Future(), lambda: True, timeout_seconds=timeout)
