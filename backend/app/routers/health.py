"""Health check routers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas.common import HealthResponse
from app.services.forecasting.bundle import serving_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


def _db_status(db: Session) -> str:
    try:
        db.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # pragma: no cover
        logger.warning("Health DB check failed: %s", exc)
        return "unavailable"


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Unauthenticated liveness probe (no DB check)."""
    cfg = get_settings()
    return HealthResponse(
        status="ok",
        version=cfg.app_version,
        timestamp=datetime.now(UTC),
        database="unknown",
        redis="unknown",
    )


@router.get("/api/v1/health", response_model=HealthResponse, summary="Detailed health")
def health_v1(
    response: Response,
    db: Session = Depends(get_db),  # type: ignore[no-untyped-def]
) -> HealthResponse:
    """Versioned health endpoint with DB check and serving-mode assertion.

    A configured-but-unloadable ``MODEL_BUNDLE_PATH`` is reported as
    ``status: degraded`` with **503**, so a monitor (or the compose healthcheck)
    fails on it. The startup check refuses to boot in that case; this covers a
    process that started before the bundle was removed, and any deployment that
    did not run the startup check.
    """
    cfg = get_settings()
    serving = serving_status()
    if serving["mode"] == "error":
        response.status_code = 503
    return HealthResponse(
        status="degraded" if serving["mode"] == "error" else "ok",
        version=cfg.app_version,
        timestamp=datetime.now(UTC),
        database=_db_status(db),
        redis="unknown",
        serving=serving,
    )
