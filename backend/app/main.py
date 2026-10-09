"""DemandIQ FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.config import get_settings
from app.middleware.audit import AuditMiddleware
from app.middleware.maintenance import MaintenanceModeMiddleware
from app.routers import (
    access,
    agent,
    alerts,
    analytics,
    apikeys,
    auth,
    billing,
    connectors,
    datasets,
    device_grants,
    device_mirror,
    devices,
    experiments,
    features,
    forecasts,
    governance,
    health,
    ingest,
    locations,
    me,
    menu,
    models,
    policies,
    quality,
    reports,
    site_admin,
    sources,
    stream,
    webhooks,
    workspace,
)

logger = logging.getLogger(__name__)


def _wants_html(scope: dict[str, Any]) -> bool:
    """True when the request is a browser navigation rather than an asset fetch."""
    for name, value in scope.get("headers") or []:
        if name.lower() == b"accept":
            return b"text/html" in value.lower()
    return False


class _SpaStaticFiles(StaticFiles):
    """Static files with a single-page-app fallback.

    The shell owns its own routes (``/app/login``, ``/app/reports``), so a
    navigation to a path that is not a file on disk has to answer with
    ``index.html`` and let the client router decide. Plain
    ``StaticFiles(html=True)`` only does that for directories, so every deep link
    into the built app returned 404 — the app could only be entered at ``/app/``.

    Only navigations get the shell. An asset request keeps its 404, because
    answering HTML with a 200 would surface as a MIME error in the console
    instead of the missing file it actually is.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or not _wants_html(scope):
                raise
            return await super().get_response("index.html", scope)


def _configure_logging(level_name: str) -> None:
    """Apply ``LOG_LEVEL`` to the application's own loggers.

    uvicorn configures only its own loggers (with ``propagate=False``), leaving
    the root logger handler-less. Without this the app's INFO records — including
    the boot-time serving mode — are discarded by the last-resort handler, and
    the documented ``LOG_LEVEL`` setting has no effect on the API at all.
    Mirrors ``app/jobs/worker.py``, which already did this for the worker.
    """
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        # Something already owns output (uvicorn config, pytest's capture).
        root.setLevel(level)
    else:
        logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # type: ignore[no-untyped-def]
    """Application lifespan — validate settings at boot, run jobs when enabled."""
    cfg = get_settings()
    _configure_logging(cfg.log_level)
    logger.info("Starting %s v%s env=%s", cfg.app_name, cfg.app_version, cfg.app_env)
    # Report the serving mode once, at boot. Whether forecasts come from the
    # trained bundle or the degraded stub is the most consequential fact about a
    # running deployment, and it used to be visible only in a per-request log.
    from app.services.forecasting.bundle import (
        assert_serving_configuration,
        describe_serving_mode,
    )

    for line in describe_serving_mode():
        logger.info("Serving: %s", line)
    # An explicitly configured but unloadable bundle must stop the boot: the
    # alternative is an API that answers every forecast from the stub while
    # looking perfectly healthy. An unset path (the documented stub) is fine.
    assert_serving_configuration()
    stop_event: asyncio.Event | None = None
    scheduler_task: asyncio.Task[None] | None = None
    if cfg.jobs_enabled:
        from app.jobs.scheduler import scheduler_loop

        stop_event = asyncio.Event()
        scheduler_task = asyncio.create_task(scheduler_loop(stop_event))
    try:
        yield
    finally:
        if stop_event is not None and scheduler_task is not None:
            stop_event.set()
            await scheduler_task
        logger.info("Shutting down %s", cfg.app_name)


def create_app(*, frontend_dist: Path | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = get_settings()

    app = FastAPI(
        title=cfg.app_name,
        version=cfg.app_version,
        description="DemandIQ API: forecasting, policy simulation and governance.",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "ops", "description": "Operational: health and root"},
            {"name": "health", "description": "Health checks and readiness"},
            {"name": "auth", "description": "Authentication and identity"},
            {"name": "me", "description": "Current user profile and preferences"},
            {"name": "locations", "description": "Locations and menu"},
            {"name": "menu", "description": "Menu groups and perishability"},
            {"name": "forecasts", "description": "Forecasting with intervals"},
            {"name": "policies", "description": "Preparation policy simulation"},
            {"name": "quality", "description": "Data quality & issues"},
            {"name": "alerts", "description": "Alerts & rules"},
            {"name": "ingestion", "description": "Demand ingestion"},
            {"name": "governance", "description": "Audit and access tracking"},
            {"name": "reports", "description": "Analytics & reporting"},
            {"name": "analytics", "description": "Analytics aggregations and insights"},
            {"name": "webhooks", "description": "Webhook delivery"},
            {"name": "billing", "description": "Workspace billing and invoices"},
            {"name": "agent", "description": "AI assistant sessions and answers"},
            {"name": "agent-admin", "description": "Agent administration and quotas"},
            {"name": "features", "description": "Feature definitions and coverage"},
            {"name": "models", "description": "Model registry and versions"},
            {"name": "experiments", "description": "Experiments and evaluation"},
            {"name": "sources", "description": "Data sources aggregation"},
            {"name": "connectors", "description": "External data connectors"},
            {"name": "api-keys", "description": "API keys and scopes"},
            {"name": "apikeys", "description": "API keys and scopes (alias of api-keys)"},
            {"name": "datasets", "description": "Datasets and canonical tables"},
            {"name": "waste", "description": "Independent waste evidence (no FK to demand)"},
            {"name": "devices", "description": "Device fleet, grants and consent-gated mirroring"},
            {"name": "stream", "description": "Realtime device sync (WebSocket) and its health"},
        ],
    )

    # --- Middleware (order matters: outermost first) ---
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])  # type: ignore[arg-type]
    app.add_middleware(  # type: ignore[arg-type]
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.add_middleware(AuditMiddleware)  # type: ignore[arg-type]
    # Maintenance write-freeze (innermost; reads platform setting per request)
    app.add_middleware(MaintenanceModeMiddleware)  # type: ignore[arg-type]

    # --- Exception handlers: unified error shape per IMPLEMENTATION_GUIDE.md ---
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:  # type: ignore[no-untyped-def]
        # Preserve already-shaped errors (dict with ``error`` key).
        detail = exc.detail  # type: ignore[assignment]
        if isinstance(detail, dict) and "error" in detail:
            err = detail["error"]  # type: ignore[assignment]
            # Inject reference if missing.
            if not err.get("reference"):  # type: ignore[union-attr]
                err["reference"] = getattr(request.state, "request_id", str(uuid.uuid4()))  # type: ignore[union-attr]
            return JSONResponse(status_code=exc.status_code, content={"error": err})
        # Fallback shape.
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "http_error",
                    "message": str(detail),
                    "field": None,
                    "reference": getattr(request.state, "request_id", str(uuid.uuid4())),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:  # type: ignore[no-untyped-def]
        # Map Pydantic validation to the unified error shape.
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(x) for x in first.get("loc", []) if x != "body") or None
        code = "validation_error"
        msg = first.get("msg", "Validation failed")
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": code,
                    "message": msg,
                    "field": field,
                    "reference": getattr(request.state, "request_id", str(uuid.uuid4())),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:  # type: ignore[no-untyped-def]
        logger.exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred",
                    "field": None,
                    "reference": getattr(request.state, "request_id", str(uuid.uuid4())),
                }
            },
        )

    # --- Routers ---
    # Health (no prefix, unauthenticated)
    app.include_router(health.router)
    # Auth
    app.include_router(auth.router)
    # Access / audit reads BEFORE locations to avoid /locations/{id} shadowing
    app.include_router(access.router)
    # Ingestion (manual / file / image) — before locations to keep /ingest prefix clear
    app.include_router(ingest.router)
    # Forecasting & explainability
    app.include_router(forecasts.router)
    # Policies / simulation
    app.include_router(policies.router)
    # Quality & alerts
    app.include_router(quality.router)
    app.include_router(alerts.router)
    # Reports (must be before generic governance pipeline routes)
    app.include_router(reports.router)
    # Webhooks (admin)
    app.include_router(webhooks.router)
    # Sources (aggregated connectors) & connectors & api-keys
    app.include_router(sources.router)
    app.include_router(connectors.router)
    app.include_router(apikeys.router)
    # Governance — change requests, members, roles, pipeline
    app.include_router(governance.router)
    # Me — account & security (profile, preferences, mfa, sessions, identities)
    app.include_router(me.router)
    # Billing — workspace subscription (must be before locations catch-all)
    app.include_router(billing.router)
    # Agent — assistant (must be before locations catch-all)
    app.include_router(agent.router)
    app.include_router(agent.admin_router)
    # Models / experiments / features — all admin (must be before locations catch-all)
    app.include_router(models.router)
    app.include_router(experiments.router)
    app.include_router(features.router)
    # Analytics (summary, trend, by-location, breakdown, insights)
    app.include_router(analytics.router)
    # Datasets & waste evidence — admin governance (must be before locations)
    app.include_router(datasets.router)
    app.include_router(datasets.waste_router)
    # Device fleet (before locations, so no device path can be shadowed by the
    # locations catch-all). Order within the group is not significant: the four
    # prefixes are disjoint.
    app.include_router(devices.router)
    app.include_router(device_grants.router)
    app.include_router(device_mirror.router)
    app.include_router(stream.router)
    # Locations (CRUD)
    app.include_router(locations.router)
    # Menu groups
    app.include_router(menu.router)
    # The workspace's own profile (distinct from the platform-wide Site Admin)
    app.include_router(workspace.router)
    app.include_router(site_admin.router)

    # --- Console & SPA (must be after API routers to avoid shadowing) ---
    _console_path = Path(__file__).resolve().parent.parent / "api-console.html"
    _frontend_dist = frontend_dist or Path(__file__).resolve().parents[2] / "frontend" / "dist"

    @app.get("/console", include_in_schema=False, response_model=None)
    def console() -> FileResponse | JSONResponse:  # type: ignore[no-untyped-def]
        """Serve the standalone Gateway Console (api-console.html)."""
        if _console_path.exists():
            return FileResponse(str(_console_path), media_type="text/html")
        return JSONResponse(  # fallback if file missing in slim image
            {
                "detail": "Console not found",
                "hint": "mount api-console.html at backend/api-console.html",
            },
            status_code=404,
        )

    @app.get("/", tags=["ops"], include_in_schema=False, response_model=None)
    def root(request: Request) -> JSONResponse | FileResponse:  # type: ignore[no-untyped-def]
        """Root — JSON for API clients, HTML console for browsers."""
        accept = request.headers.get("accept", "")
        # Browser navigation prefers text/html; return console if available
        if "text/html" in accept and _console_path.exists():
            return FileResponse(str(_console_path), media_type="text/html")
        return JSONResponse(
            {
                "name": cfg.app_name,
                "version": cfg.app_version,
                "docs": "/docs",
                "health": "/health",
                "console": "/console",
                "contract": "API_ENDPOINTS.md",
            }
        )

    # Serve built frontend SPA at /app/* if dist exists (production)
    # Mounted last so /api/* and /console remain prioritized
    if _frontend_dist.exists() and (_frontend_dist / "index.html").exists():
        app.mount(
            "/app",
            _SpaStaticFiles(directory=str(_frontend_dist), html=True),
            name="frontend",
        )
        logger.info("Mounted frontend dist at /app from %s", _frontend_dist)

    return app


# ASGI app instance for ``uvicorn app.main:app``.
app = create_app()
