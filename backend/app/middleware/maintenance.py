"""Maintenance-mode middleware — platform-wide write freeze.

When the ``maintenance_mode`` platform setting is on, all state-changing
API requests (POST/PATCH/PUT/DELETE) are rejected with 503 except for:

* the site admins themselves (they need access to turn it off),
* authentication reads (``/auth/me``, ``/auth/refresh``) so sessions can
  be validated, and
* health/docs paths.

Reads stay open — maintenance shows a banner, not a blackout. The setting
is read per-request (single small query) so toggling it in the Site Admin
console takes effect immediately, no restart.
"""

from __future__ import annotations

import json
import logging
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.database import get_session_factory
from app.routers.site_admin import get_platform_setting

logger = logging.getLogger(__name__)

_WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

# Prefixes exempt from the write freeze.
_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/console",
    "/app",
    # Auth surface: login/refresh must work so admins can sign in; me/refresh
    # must work so already-signed-in sessions stay valid.
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/me",
    # Site-admin surface (flag-gated inside the router) and its meta.
    "/api/v1/admin/site",
)

_MAINTENANCE_BODY = json.dumps(
    {
        "error": {
            "code": "maintenance_mode",
            "message": "DemandIQ is in maintenance mode. Writing is temporarily disabled.",
            "field": None,
            "reference": None,
        }
    }
).encode()


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """Reject write requests while the platform is in maintenance mode."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if request.method.upper() not in _WRITE_METHODS:
            return await call_next(request)
        if any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            return await call_next(request)

        try:
            factory = get_session_factory()
            db = factory()
            try:
                maintenance = bool(get_platform_setting(db, "maintenance_mode"))
            finally:
                db.close()
        except Exception:  # noqa: BLE001 — DB down must not block the request
            logger.warning("maintenance check failed; allowing request")
            return await call_next(request)

        if not maintenance:
            return await call_next(request)

        # Site admins are exempt — they manage the flag itself.
        if _is_site_admin(request):
            return await call_next(request)

        return Response(
            content=_MAINTENANCE_BODY,
            status_code=503,
            media_type="application/json",
            headers={"x-request-id": getattr(request.state, "request_id", str(uuid.uuid4()))},
        )


def _is_site_admin(request: Request) -> bool:
    """Resolve the bearer token to an ``is_site_admin`` flag (best effort)."""
    from app.security.jwt import decode_token_unsafe

    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return False
    payload = decode_token_unsafe(auth.split(" ", 1)[1].strip())
    if not payload:
        return False
    sub = payload.get("sub")
    if not sub:
        return False
    try:
        factory = get_session_factory()
        db = factory()
        try:
            from sqlalchemy import text

            row = db.execute(
                text("SELECT is_site_admin FROM users WHERE id::text = :id"), {"id": str(sub)}
            ).first()
            return bool(row and row[0])
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return False
