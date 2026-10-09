"""The workspace profile — the tenant's own settings.

Surface:
  GET    /api/v1/workspace   the caller's workspace: identity, locale, footprint
  PATCH  /api/v1/workspace   rename / locale metadata  (owner, administrator)

This is deliberately *not* the Site Admin console. That one manages every
workspace on the platform and is gated on ``users.is_site_admin``; this manages
the workspace the caller already works in, and is gated on capabilities like
every other workspace screen:

* ``workspace:read``  — every seat. Which tenant you are in is context for the
  screen you are already on, not a privilege.
* ``workspace:manage`` — owner and administrator. Renames and locale changes
  are audited with a before/after snapshot.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.audit import AuditLog
from app.models.forecast import Forecast
from app.models.location import Location
from app.models.user import User
from app.models.workspace import Workspace
from app.security.deps import get_current_user
from app.security.permissions import WORKSPACE_MANAGE, WORKSPACE_READ, has_capability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/workspace", tags=["workspace"])


def _error(code_int: int, code: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code_int,
        detail={"error": {"code": code, "message": msg, "field": field, "reference": None}},
    )


def _require(user: User, capability: str, message: str) -> None:
    if not has_capability(user, capability):
        raise _error(403, "forbidden", message)


def _client(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    return "mobile" if "mobile" in ua else "web"


def _count(db: Session, model: Any, workspace_id: Any) -> int:
    try:
        return int(
            db.scalar(
                select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
            )
            or 0
        )
    except Exception:  # a footprint read must never fail the profile
        logger.warning("workspace footprint count failed", exc_info=True)
        return 0


@router.get("", response_model=dict)
def get_workspace(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """The caller's workspace, with its footprint and the caller's own reach."""
    _require(current_user, WORKSPACE_READ, "Workspace read not permitted")
    ws = db.get(Workspace, current_user.workspace_id)
    if ws is None:
        raise _error(404, "not_found", "Workspace not found")
    return {
        "id": str(ws.id),
        "name": ws.name,
        "business_type": ws.business_type,
        "country": ws.country,
        "timezone": ws.timezone,
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
        "counts": {
            "users": _count(db, User, ws.id),
            "locations": _count(db, Location, ws.id),
            "forecasts": _count(db, Forecast, ws.id),
        },
        # Lets the UI show a read-only profile to a seat that cannot edit it,
        # and say why — the same capability the API enforces below.
        "can_manage": has_capability(current_user, WORKSPACE_MANAGE),
    }


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    business_type: str | None = Field(default=None, max_length=60)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str | None = Field(default=None, max_length=64)


@router.patch("", response_model=dict)
def patch_workspace(
    payload: WorkspacePatch,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Rename the workspace or change its locale metadata. Audited."""
    _require(current_user, WORKSPACE_MANAGE, "Your seat cannot change workspace settings")
    ws = db.get(Workspace, current_user.workspace_id)
    if ws is None:
        raise _error(404, "not_found", "Workspace not found")

    before: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    for attr in ("name", "business_type", "country", "timezone"):
        value = getattr(payload, attr)
        if value is not None:
            before[attr] = getattr(ws, attr)
            setattr(ws, attr, value)
            changes[attr] = value
    if not changes:
        raise _error(422, "validation_error", "No changes supplied")

    db.flush()
    db.add(
        AuditLog(
            workspace_id=current_user.workspace_id,
            actor_id=current_user.id,
            action="workspace.update",
            target=str(ws.id),
            client=_client(request),
            meta_data={"before": before, "after": changes},
            created_at=datetime.now(UTC),
        )
    )
    db.flush()
    return {"id": str(ws.id), **{attr: getattr(ws, attr) for attr in changes}}
