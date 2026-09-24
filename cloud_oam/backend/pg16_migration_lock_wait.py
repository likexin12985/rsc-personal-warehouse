"""Synchronize migration probes on an observed database lock, not startup time."""

from concurrent.futures import Future
import math
import time
from typing import Callable, TypeVar


Observation = TypeVar("Observation")
ALEMBIC_COMMAND_TIMEOUT_SECONDS = 180


def observe_projector_preflight_lock(connection, *, blocker_pid: int) -> bool:
    return connection.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_stat_activity AS activity
            JOIN pg_catalog.pg_locks AS waiting USING (pid)
            WHERE activity.datname = current_database()
              AND activity.usename = 'star_oam_migrator'
              AND activity.wait_event_type = 'Lock'
              AND waiting.relation = 'public.sync_runs'::regclass
              AND waiting.mode = 'ShareRowExclusiveLock' AND NOT waiting.granted
              AND %s = ANY(pg_catalog.pg_blocking_pids(activity.pid))
              AND activity.query LIKE 'LOCK TABLE public.external_sync_snapshots,%%'
        )
        """, (blocker_pid,),
    ).fetchone()[0]


def observe_version_maintenance_lock(connection, *, blocker_pid: int):
    return connection.execute(
        "SELECT activity.query, ARRAY(SELECT DISTINCT held.mode "
        "FROM pg_catalog.pg_locks AS held WHERE held.pid = activity.pid "
        "AND held.locktype = 'relation' AND held.granted "
        "AND held.mode <> 'AccessShareLock' ORDER BY held.mode) "
        "FROM pg_catalog.pg_locks AS lock_row "
        "JOIN pg_catalog.pg_stat_activity AS activity USING (pid) "
        "WHERE lock_row.relation = 'public.alembic_version'::regclass "
        "AND lock_row.mode = 'AccessExclusiveLock' AND NOT lock_row.granted "
        "AND activity.usename = 'star_oam_migrator' "
        "AND activity.datname = current_database() "
        "AND %s = ANY(pg_catalog.pg_blocking_pids(activity.pid))",
        (blocker_pid,),
    ).fetchall()


def wait_for_migration_lock(
    future: Future,
    observe: Callable[[], Observation],
    *,
    timeout_seconds: float = ALEMBIC_COMMAND_TIMEOUT_SECONDS,
) -> Observation:
    """Return the first truthy lock proof while the migration is still live.

    Callers retain their conflicting transaction until this returns. They
    must release it in a finally block before joining the worker, including
    on observation failures. A completed migration is never a lock proof.
    The subprocess itself must also enforce its own bounded lifetime.
    """

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("migration lock observation timeout must be positive and finite")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if future.done():
            future.result()  # Preserve a specific child-process failure.
            raise AssertionError("migration completed without the required lock observation")
        proof = observe()
        if proof and not future.done():
            return proof
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("migration did not reach the required database lock before its deadline")
        time.sleep(min(0.05, remaining))
