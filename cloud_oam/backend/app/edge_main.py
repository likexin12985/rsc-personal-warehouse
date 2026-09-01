from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

from .config import get_settings
from .database import engine
from .routers import integrations


settings = get_settings()
APP_VERSION = "0.9.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The receiver only stages authenticated batches. Schema changes are owned by
    # the separate Alembic deployment step and never run in this process.
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
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    return {
        "ok": True,
        "service": "rsc-oam-edge-receiver",
        "version": APP_VERSION,
        "configured": settings.edge_sync_configuration_ready(),
        "mode": "staging_only",
    }
