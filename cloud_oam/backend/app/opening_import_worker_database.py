"""Bound SQL waits only for opening-import worker transactions.

These are SQL and lock budgets, not a wall-clock deadline for OSS or parsing.
The caller retains the original job and recovers uncertain commits by reread.
"""
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker

OPENING_IMPORT_LOCK_TIMEOUT_MS = 5_000
OPENING_IMPORT_STATEMENT_TIMEOUT_MS = 30_000


def opening_import_worker_session_factory(
    engine, *, lock_timeout_ms=OPENING_IMPORT_LOCK_TIMEOUT_MS,
    statement_timeout_ms=OPENING_IMPORT_STATEMENT_TIMEOUT_MS,
):
    """Give each worker transaction local limits without altering API sessions."""
    if (type(lock_timeout_ms) is not int or type(statement_timeout_ms) is not int
            or not 1 <= lock_timeout_ms <= statement_timeout_ms <= 120_000
            or engine.dialect.name != "postgresql"):
        raise ValueError("opening_import_database_budget_invalid")
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @event.listens_for(factory, "after_begin")
    def apply_sql_budgets(session, transaction, connection):
        # SET LOCAL expires on commit/rollback, even when the physical pooled
        # connection is subsequently borrowed by another subsystem. The maker
        # owns its Session subclass, so no global Session listener is installed.
        connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'")
        connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'")

    return factory
