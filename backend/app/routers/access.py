"""Access-log and audit-log read endpoints."""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.audit import AuditLog
from app.models.location_access import LocationAccessLog
from app.models.user import User
from app.schemas.access import AuditLogResponse, LocationAccessLogResponse
from app.schemas.common import PaginatedResponse
from app.security.permissions import ACCESS_READ, AUDIT_READ, has_capability

logger = logging.getLogger(__name__)

router = APIRouter(tags=["governance"])


def _error(code: int, err: str, msg: str) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": None, "reference": None}},
    )


def _encode(val: datetime, pk: str | int) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"ts": val.isoformat(), "id": str(pk)}).encode()
    ).decode()


def _decode(cursor: str) -> tuple[datetime, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return datetime.fromisoformat(data["ts"]), data["id"]
    except Exception as exc:
        raise _error(400, "invalid_cursor", "Invalid pagination cursor") from exc


@router.get(
    "/api/v1/locations/access-logs", response_model=PaginatedResponse[LocationAccessLogResponse]
)
def list_location_access_logs(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    location_id: uuid.UUID | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[LocationAccessLogResponse]:
    """List location access logs — admin only, workspace-scoped, cursor-paginated."""
    if not has_capability(current_user, ACCESS_READ):
        raise _error(403, "forbidden", "Administrator role required")

    q = select(LocationAccessLog).where(LocationAccessLog.workspace_id == current_user.workspace_id)

    if location_id is not None:
        q = q.where(LocationAccessLog.location_id == location_id)
    if user_id is not None:
        q = q.where(LocationAccessLog.user_id == user_id)
    if action is not None:
        q = q.where(LocationAccessLog.action == action)

    if cursor:
        ts, _ = _decode(cursor)
        q = q.where(LocationAccessLog.created_at < ts)

    q = q.order_by(LocationAccessLog.created_at.desc(), LocationAccessLog.id.desc()).limit(
        limit + 1
    )

    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode(page[-1].created_at, str(page[-1].id)) if has_more and page else None

    return PaginatedResponse[LocationAccessLogResponse](
        data=[LocationAccessLogResponse.model_validate(r) for r in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/api/v1/audit-log", response_model=PaginatedResponse[AuditLogResponse])
def list_audit_log(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    client: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[AuditLogResponse]:
    """List audit log — admin only, workspace-scoped, cursor-paginated."""
    if not has_capability(current_user, AUDIT_READ):
        raise _error(403, "forbidden", "Administrator role required")

    q = select(AuditLog).where(AuditLog.workspace_id == current_user.workspace_id)

    if actor_id is not None:
        q = q.where(AuditLog.actor_id == actor_id)
    if action is not None:
        q = q.where(AuditLog.action == action)
    if client is not None:
        q = q.where(AuditLog.client == client)

    if cursor:
        ts, _ = _decode(cursor)
        q = q.where(AuditLog.created_at < ts)

    q = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)

    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode(page[-1].created_at, page[-1].id) if has_more and page else None

    # Resolve the actor's email in one lookup, the same way the change-requests
    # list resolves names. The audit rows store only `actor_id`; a client that
    # renders the id shows an opaque reference, and one that renders nothing
    # shows a blank where a person belongs. Best-effort, like the other reads.
    actor_ids = {r.actor_id for r in page if r.actor_id is not None}
    emails: dict[uuid.UUID, str] = {}
    if actor_ids:
        try:
            emails = {
                row[0]: str(row[1])
                for row in db.execute(
                    select(User.id, User.email).where(User.id.in_(actor_ids))
                ).all()
            }
        except Exception:
            logger.debug("could not resolve audit-log actor emails", exc_info=True)

    return PaginatedResponse[AuditLogResponse](
        data=[
            AuditLogResponse(
                id=r.id,
                workspace_id=r.workspace_id,
                actor_id=r.actor_id,
                actor_email=emails.get(r.actor_id) if r.actor_id is not None else None,
                action=r.action,
                target=r.target,
                client=r.client,
                meta_data=r.meta_data,
                created_at=r.created_at,
            )
            for r in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )
