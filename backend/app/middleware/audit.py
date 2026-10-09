"""Audit middleware — logs privileged requests to ``audit_log`` and location access."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from fastapi import Request, Response
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.database import get_session_factory
from app.models.audit import AuditLog
from app.models.location_access import LocationAccessLog
from app.security.jwt import decode_token_unsafe

logger = logging.getLogger(__name__)

# Paths that are considered privileged (write) and should be audited.
_PRIVILEGED_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# Location reads are always tracked (even GET) per access-tracking requirement.
_LOCATION_PREFIX = "/api/v1/locations"


def _client_from_request(request: Request) -> str:
    header_client = request.headers.get("x-client")
    if header_client in ("web", "mobile", "api"):
        return header_client
    # A device-authenticated call is an API client whatever its user-agent says
    # — a heartbeat from a till is not a "web" request, and filing it under
    # "web" would make the trail misdescribe where a write came from.
    if request.headers.get("x-device-key"):
        return "api"
    ua = (request.headers.get("user-agent") or "").lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    return "web"


def _actor_from_request(request: Request) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Extract (user_id, workspace_id) from JWT if present."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return None, None
    token = auth.split(" ", 1)[1].strip()
    payload = decode_token_unsafe(token)
    if not payload:
        return None, None
    try:
        uid = uuid.UUID(str(payload.get("sub"))) if payload.get("sub") else None
        wid = uuid.UUID(str(payload.get("workspace_id"))) if payload.get("workspace_id") else None
        return uid, wid
    except ValueError:
        return None, None


class AuditMiddleware(BaseHTTPMiddleware):
    """Middleware that writes audit and location access rows.

    - For every non-GET privileged request, writes to ``audit_log``.
    - For every request whose path contains ``/locations``, writes to
      ``location_access_logs`` (captures actor, action, client, ip, UA).

    The middleware never fails the request — DB errors are logged only.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:  # type: ignore[override]
        start = time.monotonic()
        # Assign request id for error correlation.
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id  # type: ignore[attr-defined]

        response: Response = await call_next(request)

        # Add request id header to every response.
        response.headers["x-request-id"] = request_id

        # Skip health and docs.
        path = request.url.path
        if path in ("/health", "/api/v1/health", "/docs", "/openapi.json", "/redoc"):
            return response

        method = request.method.upper()
        is_privileged = method in _PRIVILEGED_METHODS
        is_location = _LOCATION_PREFIX in path

        if not is_privileged and not is_location:
            return response

        # Fire-and-forget DB write — best-effort.
        try:
            factory = get_session_factory()
            # Only write if we can obtain a session without creating engine
            # failures. If no DB is configured, this is a no-op.
            db: Session = factory()
            try:
                actor_id, workspace_id = _actor_from_request(request)

                # We need workspace_id; if not in JWT, skip (unauthenticated).
                if workspace_id is None:
                    # Try to resolve from current_user if set by deps — but
                    # middleware runs before deps for some paths, so best effort.
                    workspace_id = getattr(request.state, "workspace_id", None)  # type: ignore[attr-defined]

                if workspace_id is None:
                    db.close()
                    return response

                duration_ms = int((time.monotonic() - start) * 1000)
                client = _client_from_request(request)

                if is_privileged:
                    # A device is not a user, so it cannot be ``actor_id`` (an
                    # FK to ``users``). The route that authenticated it sets
                    # ``request.state.device_id``; recording that here is what
                    # keeps device writes — heartbeat above all — in the trail
                    # instead of being dropped for want of a JWT.
                    device_id = getattr(request.state, "device_id", None)  # type: ignore[attr-defined]
                    audit = AuditLog(
                        workspace_id=workspace_id,
                        actor_id=actor_id,
                        action=f"{method} {path}",
                        target=path,
                        client=client,
                        meta_data={
                            "method": method,
                            "path": path,
                            "status_code": response.status_code,
                            "duration_ms": duration_ms,
                            "request_id": request_id,
                            **({"device_id": str(device_id)} if device_id else {}),
                        },
                    )
                    db.add(audit)

                if is_location:
                    # Derive action from method+path
                    if method == "GET" and path.rstrip("/").endswith("/locations"):
                        action = "list"
                    elif method == "GET":
                        action = "detail"
                    elif method in ("POST", "PATCH", "DELETE"):
                        action = "view"
                    else:
                        action = "view"

                    # Try to extract location_id from path
                    loc_id = None
                    parts = path.split("/")
                    for i, part in enumerate(parts):
                        if part == "locations" and i + 1 < len(parts):
                            cand = parts[i + 1]
                            try:
                                loc_id = uuid.UUID(cand)
                            except ValueError:
                                loc_id = None
                            break

                    access = LocationAccessLog(
                        workspace_id=workspace_id,
                        location_id=loc_id,
                        user_id=actor_id,
                        action=action,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent"),
                        client=client,
                    )
                    db.add(access)

                db.commit()
            except Exception as exc:  # pragma: no cover
                db.rollback()
                logger.warning("Audit middleware DB write failed: %s", exc)
            finally:
                db.close()
        except Exception as exc:  # pragma: no cover
            logger.debug("Audit middleware skipped (no DB): %s", exc)

        return response
