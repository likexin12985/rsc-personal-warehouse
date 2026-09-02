from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from .config import get_settings
from .database import engine, health_engine
from .edge_database_security import (
    EdgeDatabaseBoundaryError,
    verify_edge_database_boundary,
)
from .routers import integrations


settings = get_settings()
APP_VERSION = "0.9.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The receiver only stages authenticated batches. Schema changes are owned by
    # the separate Alembic deployment step and never run in this process.
    settings.validate_edge_receiver_startup()
    verify_edge_database_boundary(
        engine,
        expected_role=settings.database_expected_edge_role,
        expected_migration_role=settings.database_expected_migration_role,
    )
    yield


app = FastAPI(
    title="RSC OAM Edge Receiver",
    version=APP_VERSION,
    docs_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.include_router(integrations.ingress_router, prefix="/api")


@app.get("/api/health")
def health():
    try:
        settings.validate_edge_receiver_startup()
        verify_edge_database_boundary(
            health_engine,
            expected_role=settings.database_expected_edge_role,
            expected_migration_role=settings.database_expected_migration_role,
        )
    except (EdgeDatabaseBoundaryError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="边缘接收器数据库安全边界未就绪",
        ) from None
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="边缘接收器数据库不可用",
        ) from None
    return {
        "ok": True,
        "service": "rsc-oam-edge-receiver",
        "version": APP_VERSION,
        "configured": settings.edge_sync_configuration_ready(),
        "mode": "staging_only",
    }
