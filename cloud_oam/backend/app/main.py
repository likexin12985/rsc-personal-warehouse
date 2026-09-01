from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .auth_sessions import validate_active_production_session_ip_evidence
from .database import Base, SessionLocal, engine
from .database_security import validate_production_database_security
from .routers import (
    access,
    audit,
    auth,
    dashboard,
    formal_files,
    formal_material_catalog,
    formal_material_requests,
    formal_opening_stocktake,
    formal_opening_stocktake_read,
    formal_stocktake_options,
    formal_stocktakes,
    formal_inventory,
    formal_reconciliation,
    integrations,
    inventory,
    master,
    media,
    oam_data,
    stocktakes,
    transfers,
    work_order_materials,
    work_orders,
)
from .seed import seed_initial_data


settings = get_settings()
APP_VERSION = "0.9.0"
LEGACY_PROTOTYPE_WRITE_PREFIXES = (
    "/api/auth/login",
    "/api/auth/miniprogram/password-login",
    "/api/auth/change-password",
    "/api/inventory/adjust",
    "/api/materials",
    "/api/warehouses",
    "/api/transfers",
    "/api/work-order-materials",
    "/api/stocktakes",
    "/api/media",
    "/api/auth/users",
    "/api/integrations/oam/personnel",
)
PRODUCTION_AUTH_PATHS = frozenset(
    {
        "/api/auth/login-options",
        "/api/auth/sms/request",
        "/api/auth/sms/login",
        "/api/auth/miniprogram/sms-login",
        "/api/auth/miniprogram/wechat-login",
        "/api/auth/refresh",
        "/api/auth/miniprogram/refresh",
        "/api/auth/logout",
        "/api/auth/miniprogram/logout",
        "/api/auth/me",
        "/api/auth/sessions",
    }
)
PRIVATE_IDENTITY_READ_PATHS = frozenset(
    {
        "/api/auth/me",
        "/api/access/context",
    }
)
PRIVATE_COMMAND_RECOVERY_PATHS = frozenset(
    {"/api/v1/material-request-lifecycle-command-status"}
)


def is_production_auth_path(method: str, path: str) -> bool:
    if path in PRODUCTION_AUTH_PATHS:
        return True
    parts = path.split("/")
    return bool(
        method.upper() == "POST"
        and len(parts) == 6
        and parts[:4] == ["", "api", "auth", "sessions"]
        and parts[4]
        and parts[5] == "revoke"
    )


def is_legacy_prototype_write(method: str, path: str) -> bool:
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return False
    return any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in LEGACY_PROTOTYPE_WRITE_PREFIXES
    )


def _run_startup_database_boundary(
    *,
    schema_mode: str,
    create_schema: Callable[[], None],
    seed_data: Callable[[], None],
) -> None:
    """Run the explicit non-production bootstrap path.

    Production uses the default ``alembic`` mode, which deliberately performs no
    application-startup DDL. Versioned migrations are run as a separate deploy
    step before the API starts.
    """

    if schema_mode == "alembic":
        return
    if schema_mode not in {"bootstrap", "bootstrap_with_seed"}:
        raise RuntimeError(f"unsupported database schema mode: {schema_mode}")
    create_schema()
    if schema_mode == "bootstrap_with_seed":
        seed_data()


def _create_bootstrap_schema() -> None:
    Base.metadata.create_all(engine)


def _seed_bootstrap_data() -> None:
    with SessionLocal() as db:
        seed_initial_data(db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_api_startup()
    if settings.environment == "production":
        validate_production_database_security(
            engine,
            expected_runtime_role=settings.database_expected_runtime_role,
            expected_migration_role=settings.database_expected_migration_role,
        )
        # Read-only fail-closed gate.  Legacy revoked/expired rows are retained,
        # but no active session with plaintext or a stale HMAC version may be
        # accepted by the formal API.
        with SessionLocal() as db:
            validate_active_production_session_ip_evidence(
                db,
                hash_version=settings.identity_hash_version,
            )
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    _run_startup_database_boundary(
        schema_mode=settings.database_schema_mode,
        create_schema=_create_bootstrap_schema,
        seed_data=_seed_bootstrap_data,
    )
    yield


app = FastAPI(
    title=settings.app_name,
    version=APP_VERSION,
    docs_url="/api/docs" if settings.environment != "production" else None,
    openapi_url="/api/openapi.json" if settings.environment != "production" else None,
    lifespan=lifespan,
)
auth.install_formal_authentication_exception_handler(app)
formal_material_requests.install_formal_material_request_validation_exception_handler(
    app
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def block_legacy_prototype_writes(request, call_next):
    if (
        settings.environment == "production"
        and request.url.path.startswith("/api/auth")
        and not is_production_auth_path(request.method, request.url.path)
    ):
        return JSONResponse(
            status_code=410,
            content={"detail": "v0.9账号管理接口未进入正式身份与RBAC白名单"},
        )
    if (
        not settings.legacy_prototype_writes_enabled
        and is_legacy_prototype_write(request.method, request.url.path)
    ):
        return JSONResponse(
            status_code=410,
            content={
                "detail": (
                    "v0.9原型写接口已冻结；请使用V1.0正式业务接口，"
                    "不得直接修改库存或合并履约状态"
                )
            },
        )
    response = await call_next(request)
    if request.url.path in (
        PRIVATE_IDENTITY_READ_PATHS | PRIVATE_COMMAND_RECOVERY_PATHS
    ):
        # Identity/effective-RBAC documents and command-recovery evidence are
        # authorization decisions, not cacheable application data. Apply this
        # after ``call_next`` so framework-generated failures receive the same
        # boundary as successful reads.
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/v1/files"):
        # Cover framework-level 404/405/422 responses as well as successful
        # signed-intent responses; presigned URLs must never be cached or sent
        # in a referrer.
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response

app.include_router(auth.router, prefix="/api")
app.include_router(access.router, prefix="/api")
# The V1 inventory surface is formal-RBAC and read-only.  It is intentionally
# mounted in production, unlike every quarantined v0.9 inventory route below.
app.include_router(formal_inventory.router, prefix="/api")
# The active formal material catalog is a local, read-only picker. It never
# refreshes OAM data and omits materials without a current inventory policy.
app.include_router(formal_material_catalog.router, prefix="/api")
# Formal demand reads are mounted in every environment.  Commands remain
# independently fail-closed behind their explicit feature/configuration gate
# and request-scoped KMS cipher dependency.
app.include_router(formal_material_requests.router, prefix="/api")
app.include_router(formal_material_requests.command_status_router, prefix="/api")
# Formal attachments use only private-object-store presigned intents.  The
# adapter is disabled by default and never shares the quarantined legacy
# ``/media`` filesystem route.
app.include_router(formal_files.router, prefix="/api")
# The formal opening workflow exposes start, round-aware count, observation
# disposition, independent regional/headquarters review, recount, post and
# close commands.  Each route commits one transition only, revalidates its
# complete evidence graph and owns no external-system side effect.
app.include_router(formal_opening_stocktake.router, prefix="/api")
app.include_router(formal_opening_stocktake_read.router, prefix="/api")
# Managed task creation uses separately scoped read-only pickers so clients do
# not ask managers to type raw organization, location, or person identifiers.
app.include_router(formal_stocktake_options.router, prefix="/api")
# Register the generic UUID route after the literal ``/opening`` routes so the
# dedicated opening workflow can never be shadowed by ``/{task_id}``.
# Non-opening commands never advance notification or reconciliation.
app.include_router(formal_stocktakes.router, prefix="/api")
# OAM control totals remain a read-only comparison source. This router owns
# only local reconciliation explanations and approvals; it cannot mutate OAM
# data or inventory ledger facts.
app.include_router(formal_reconciliation.router, prefix="/api")
# All v0.9 business routers are compatibility-only.  Production exposes no
# legacy read surface because empty/ambiguous legacy scopes must never degrade
# to nationwide access.  Each V1 module is mounted only after it uses the
# formal permission and object-scope evaluator.
if settings.environment != "production":
    app.include_router(master.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")
    app.include_router(integrations.ingress_router, prefix="/api")
    app.include_router(integrations.management_router, prefix="/api")
    app.include_router(oam_data.router, prefix="/api")
    app.include_router(inventory.router, prefix="/api")
    app.include_router(transfers.router, prefix="/api")
    app.include_router(work_order_materials.router, prefix="/api")
    app.include_router(work_orders.router, prefix="/api")
    app.include_router(stocktakes.router, prefix="/api")
    app.include_router(media.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")


@app.get("/api/health")
def health():
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    return {"ok": True, "service": "star-oam-cloud", "version": APP_VERSION}
