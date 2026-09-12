from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .auth_sessions import validate_active_production_session_ip_evidence
from .database import Base, SessionLocal, engine, health_engine
from .database_security import validate_production_database_security
from .kms_readiness import KmsReadinessGate
from .production_adapters import (
    get_configured_kms_loader,
    validate_persisted_kms_key_references,
    validate_production_adapter_installation,
)
from .routers import (
    access,
    audit,
    auth,
    dashboard,
    formal_files,
    formal_material_catalog,
    formal_material_request_options,
    formal_material_requests,
    formal_work_order_material,
    formal_work_order_query,
    formal_opening_start_options,
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
    {
        "/api/v1/material-request-lifecycle-command-status",
        "/api/v1/stocktakes/post-differences-command-status",
    }
)
PRIVATE_MATERIAL_REQUEST_OPTION_PREFIX = "/api/v1/material-request-options"
PRIVATE_OPENING_START_OPTION_PREFIX = "/api/v1/stocktakes/opening/start-options"


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
async def lifespan(application: FastAPI):
    settings.validate_api_startup()
    runtime_app = application or app
    runtime_app.state.kms_readiness_gate = None
    runtime_app.state.required_kms_coordinates = frozenset()
    if settings.environment == "production":
        # Structural, network-free proof: the ciphertext-only key registry and
        # every enabled logical key coordinate must exist before the process can
        # advertise health.  Online KMS availability is re-proved before each
        # paid SMS or protected write.
        validate_production_adapter_installation(settings)
        validate_production_database_security(
            engine,
            expected_runtime_role=settings.database_expected_runtime_role,
            expected_migration_role=settings.database_expected_migration_role,
        )
        # Read-only fail-closed gate.  Legacy revoked/expired rows are retained,
        # but no active session with plaintext or a stale HMAC version may be
        # accepted by the formal API.
        with SessionLocal() as db:
            required_kms_coordinates = validate_persisted_kms_key_references(
                db,
                settings,
            )
            validate_active_production_session_ip_evidence(
                db,
                hash_version=settings.identity_hash_version,
            )
        runtime_app.state.required_kms_coordinates = required_kms_coordinates
        runtime_app.state.kms_readiness_gate = KmsReadinessGate(
            loader=get_configured_kms_loader(settings).probe,
            success_ttl_seconds=settings.kms_readiness_success_ttl_seconds,
            failure_ttl_seconds=settings.kms_readiness_failure_ttl_seconds,
            wait_budget_seconds=settings.kms_readiness_wait_budget_seconds,
            probe_budget_seconds=settings.kms_readiness_probe_budget_seconds,
        )
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    _run_startup_database_boundary(
        schema_mode=settings.database_schema_mode,
        create_schema=_create_bootstrap_schema,
        seed_data=_seed_bootstrap_data,
    )
    try:
        yield
    finally:
        readiness_gate = getattr(runtime_app.state, "kms_readiness_gate", None)
        if isinstance(readiness_gate, KmsReadinessGate):
            readiness_gate.close()


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
    if request.url.path.startswith((
        PRIVATE_MATERIAL_REQUEST_OPTION_PREFIX,
        PRIVATE_OPENING_START_OPTION_PREFIX,
    )):
        # Picker rows are live authorization decisions. Apply this to
        # framework and service failures too so a cached 404/403 cannot hide a
        # later source refresh or permission change.
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    if (request.url.path.startswith("/api/v1/stocktakes/")
        and request.url.path.endswith("/count-command-status")):
        # Dynamic recovery coordinates require the same privacy boundary on
        # authentication/validation/route failures as on successful reads.
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    if (request.url.path.startswith("/api/v1/stocktakes/")
        and request.url.path.endswith("/post-differences-command-status")):
        # Keep framework-level 401/404/405/422 responses on the historical
        # posting lookup inside the same private, no-store boundary as the
        # successful route response.  This endpoint carries identity and
        # immutable inventory evidence and must never be cacheable.
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
    if (request.url.path.startswith("/api/v1/stocktakes/")
        and "/reviews/" in request.url.path
        and request.url.path.endswith("/command-status")):
        # Review recovery coordinates contain identity and immutable audit
        # evidence; framework-level failures must receive the same private,
        # no-store boundary as successful reads.
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
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
# Request creation reads requester-scoped work-order labels from the validated
# local OAM projection. It never refreshes OAM or accepts a client-typed UUID.
app.include_router(formal_material_request_options.router, prefix="/api")
# Formal demand reads are mounted in every environment.  Commands remain
# independently fail-closed behind their explicit feature/configuration gate
# and request-scoped KMS cipher dependency.
app.include_router(formal_material_requests.router, prefix="/api")
app.include_router(formal_work_order_material.router, prefix="/api")
app.include_router(formal_work_order_query.router, prefix="/api")
app.include_router(formal_material_requests.command_status_router, prefix="/api")
# Formal attachments use only private-object-store presigned intents.  The
# adapter is disabled by default and never shares the quarantined legacy
# ``/media`` filesystem route.
app.include_router(formal_files.router, prefix="/api")
# The formal opening workflow exposes start, round-aware count, observation
# disposition, independent regional/headquarters review, recount, post and
# close commands.  Each route commits one transition only, revalidates its
# complete evidence graph and owns no external-system side effect.
# Literal preparation routes precede the UUID task routes. They never start
# a task or attest that OAM control evidence is ready.
app.include_router(formal_opening_start_options.router, prefix="/api")
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


def _health_response(*, ready: bool, status: str) -> JSONResponse:
    response = JSONResponse(
        status_code=200 if ready else 503,
        content={
            "ok": ready,
            "status": status,
            "service": "star-oam-cloud",
            "version": APP_VERSION,
        },
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _readiness_response() -> JSONResponse:
    try:
        with health_engine.connect() as connection:
            connection.execute(text("select 1"))
    except Exception:
        return _health_response(ready=False, status="not_ready")

    if settings.environment == "production":
        gate = getattr(app.state, "kms_readiness_gate", None)
        coordinates = getattr(app.state, "required_kms_coordinates", ())
        if not isinstance(gate, KmsReadinessGate) or not gate.is_ready(coordinates):
            return _health_response(ready=False, status="not_ready")
    return _health_response(ready=True, status="ready")


@app.get("/api/health/live")
def health_live():
    return _health_response(ready=True, status="live")


@app.get("/api/health/ready")
def health_ready():
    return _readiness_response()


@app.get("/api/health")
def health():
    """Backward-compatible readiness alias for existing deployment monitors."""

    return _readiness_response()
