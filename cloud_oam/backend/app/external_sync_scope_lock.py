"""One transaction lock shared by edge completion and formal projection."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session


def external_sync_scope_lock_key(source_instance: str, scope_key: str) -> str:
    """Return the stable lock coordinate for one isolated mirror scope."""

    # Both coordinates may themselves contain ``:``.  Length-prefixing keeps
    # the pair injective before PostgreSQL reduces it to one 64-bit advisory
    # key (for example, ("a:b", "c") must not alias ("a", "b:c")).
    return (
        f"external-sync-scope:{len(source_instance)}:{source_instance}:"
        f"{len(scope_key)}:{scope_key}"
    )


def lock_external_sync_scope(
    db: Session,
    *,
    source_instance: str,
    scope_key: str,
) -> None:
    """Serialize completion and publication for exactly one source scope."""

    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text(
            "SELECT pg_catalog.pg_advisory_xact_lock("
            "pg_catalog.hashtextextended(:lock_key, 0))"
        ),
        {
            "lock_key": external_sync_scope_lock_key(
                source_instance,
                scope_key,
            )
        },
    )


def acquire_external_sync_scope_session_lock(
    connection: Connection,
    *,
    source_instance: str,
    scope_key: str,
) -> None:
    """Acquire the worker's cross-transaction PostgreSQL scope lock.

    The caller must finish the short acquisition transaction before starting
    its repeatable-read publication transaction.  Unlike the transaction lock
    above, this lock survives that boundary and therefore prevents a waiting
    worker from keeping a stale MVCC snapshot.
    """

    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        text(
            "SELECT pg_catalog.pg_advisory_lock("
            "pg_catalog.hashtextextended(:lock_key, 0))"
        ),
        {
            "lock_key": external_sync_scope_lock_key(
                source_instance,
                scope_key,
            )
        },
    )


def release_external_sync_scope_session_lock(
    connection: Connection,
    *,
    source_instance: str,
    scope_key: str,
) -> bool:
    """Release exactly one worker session lock and report ownership."""

    if connection.dialect.name != "postgresql":
        return True
    released = connection.scalar(
        text(
            "SELECT pg_catalog.pg_advisory_unlock("
            "pg_catalog.hashtextextended(:lock_key, 0))"
        ),
        {
            "lock_key": external_sync_scope_lock_key(
                source_instance,
                scope_key,
            )
        },
    )
    return released is True


__all__ = [
    "acquire_external_sync_scope_session_lock",
    "external_sync_scope_lock_key",
    "lock_external_sync_scope",
    "release_external_sync_scope_session_lock",
]
