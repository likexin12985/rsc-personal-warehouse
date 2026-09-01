from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
HEALTH_DATABASE_CONNECT_TIMEOUT_SECONDS = 2
HEALTH_DATABASE_TCP_TIMEOUT_MILLISECONDS = 1000
HEALTH_DATABASE_STATEMENT_TIMEOUT_MILLISECONDS = 500
_engine_options: dict[str, object] = {"pool_pre_ping": True, "future": True}
if settings.database_url.startswith("postgresql+psycopg://"):
    # Readiness must fail within the orchestrator budget when PostgreSQL is
    # unreachable; an unbounded pool/driver wait would make liveness ambiguous.
    _engine_options.update(
        pool_timeout=5,
        connect_args={"connect_timeout": 5},
    )
engine = create_engine(settings.database_url, **_engine_options)

if settings.database_url.startswith("postgresql+psycopg://"):
    # Health uses an independent one-shot connection so API pool saturation and
    # long business-query timeouts cannot consume the orchestrator's 8s budget.
    health_engine = create_engine(
        settings.database_url,
        future=True,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": HEALTH_DATABASE_CONNECT_TIMEOUT_SECONDS,
            "tcp_user_timeout": HEALTH_DATABASE_TCP_TIMEOUT_MILLISECONDS,
            "options": (
                "-c statement_timeout="
                f"{HEALTH_DATABASE_STATEMENT_TIMEOUT_MILLISECONDS}"
            ),
        },
    )
else:
    health_engine = engine


if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
