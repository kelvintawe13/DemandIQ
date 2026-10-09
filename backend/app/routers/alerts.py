"""Alert endpoints (ARCHITECTURE.md §6).

* GET  /api/v1/alerts
* POST /api/v1/alerts/{id}/read
* POST /api/v1/alerts/read-all
* POST /api/v1/alerts/{id}/resolve
* GET  /api/v1/alerts/rules
* PATCH /api/v1/alerts/rules/{id}
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.quality import Alert, AlertRule
from app.models.user import User
from app.security.permissions import (
    ALERTS_CONFIGURE,
    ALERTS_READ,
    ALERTS_WRITE,
    has_capability,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


def _require_alerts_read(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Reading the alert inbox and its rules."""
    if not has_capability(user, ALERTS_READ):
        raise _error(403, "forbidden", "Alert read permission required")
    return user


def _require_alerts_write(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Acknowledging or resolving an alert."""
    if not has_capability(user, ALERTS_WRITE):
        raise _error(403, "forbidden", "Alert write permission required")
    return user


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


class AlertRuleCreate(BaseModel):
    """Body for POST /alerts/rules."""

    name: str = Field(min_length=1, max_length=100)
    threshold: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = Field(default=True)


class AlertRuleUpdate(BaseModel):
    """Body for PATCH /alerts/rules/{id}."""

    enabled: bool | None = Field(default=None)
    name: str | None = Field(default=None)
    threshold: dict[str, Any] | None = Field(default=None)


@router.post("/read-all", response_model=dict, dependencies=[Depends(_require_alerts_write)])
def mark_all_read(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Mark all unread alerts as read."""
    rows = list(
        db.scalars(
            select(Alert).where(
                Alert.workspace_id == current_user.workspace_id,
                Alert.read_at.is_(None),
                Alert.resolved_at.is_(None),
            )
        ).all()
    )
    now = datetime.datetime.now(datetime.UTC)
    for r in rows:
        r.read_at = now
    db.flush()
    return {"updated": len(rows)}


@router.get("", response_model=dict, dependencies=[Depends(_require_alerts_read)])
def list_alerts(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: str | None = Query(default=None, description="unread|critical|resolved"),
    severity: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """List alerts, filter by status."""
    q = select(Alert).where(Alert.workspace_id == current_user.workspace_id)
    # Exact unread total for the workspace, not just this page: the sidebar
    # badge must not depend on the requested limit. Same rule as the
    # status="unread" filter below.
    if status == "unread":
        q = q.where(Alert.read_at.is_(None), Alert.resolved_at.is_(None))
    elif status == "resolved":
        q = q.where(Alert.resolved_at.is_not(None))
    elif status == "critical":
        q = q.where(Alert.severity == "critical", Alert.resolved_at.is_(None))
    if severity is not None:
        q = q.where(Alert.severity == severity)
    if cursor:
        try:
            ts = datetime.datetime.fromisoformat(cursor)
            q = q.where(Alert.created_at < ts)
        except ValueError:
            raise _error(400, "invalid_cursor", "Invalid cursor") from None
    q = q.order_by(Alert.created_at.desc()).limit(limit + 1)
    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].created_at.isoformat() if has_more and page else None
    unread_count = (
        db.scalar(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.workspace_id == current_user.workspace_id,
                Alert.read_at.is_(None),
                Alert.resolved_at.is_(None),
            )
        )
        or 0
    )
    return {
        "unread_count": unread_count,
        "data": [
            {
                "id": str(r.id),
                "rule_id": str(r.rule_id) if r.rule_id else None,
                "location_id": str(r.location_id) if r.location_id else None,
                "severity": r.severity,
                "title": r.title,
                "body": r.body,
                "read_at": r.read_at.isoformat() if r.read_at else None,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in page
        ],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.post("/{alert_id}/read", response_model=dict, dependencies=[Depends(_require_alerts_write)])
def mark_read(
    alert_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Mark a single alert as read."""
    row: Alert | None = db.get(Alert, alert_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Alert not found")
    if row.read_at is None:
        row.read_at = datetime.datetime.now(datetime.UTC)
        db.flush()
    return {"id": str(row.id), "read_at": row.read_at.isoformat() if row.read_at else None}


@router.post(
    "/{alert_id}/resolve", response_model=dict, dependencies=[Depends(_require_alerts_write)]
)
def resolve_alert(
    alert_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Resolve an alert with optional note."""
    row: Alert | None = db.get(Alert, alert_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Alert not found")
    if row.resolved_at is not None:
        raise _error(409, "already_resolved", "Alert already resolved")
    row.resolved_at = datetime.datetime.now(datetime.UTC)
    # Also mark read if not already.
    if row.read_at is None:
        row.read_at = row.resolved_at
    db.flush()
    logger.info("alert resolved id=%s by=%s", row.id, current_user.id)
    return {"id": str(row.id), "resolved_at": row.resolved_at.isoformat()}


@router.get("/rules", response_model=dict, dependencies=[Depends(_require_alerts_read)])
def list_alert_rules(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List alert rules and thresholds."""
    rows = list(
        db.scalars(
            select(AlertRule).where(AlertRule.workspace_id == current_user.workspace_id)
        ).all()
    )
    if not rows:
        defaults = [
            {"name": "forecast_error", "threshold": {"mae_gt": 20}},
            {"name": "coverage_breach", "threshold": {"coverage_lt": 0.85}},
            {"name": "drift", "threshold": {"drift_p_gt": 0.05}},
            {"name": "connector_health", "threshold": {"stale_hours_gt": 24}},
        ]
        seeded: list[AlertRule] = []
        for d in defaults:
            r = AlertRule(
                id=uuid.uuid4(),
                workspace_id=current_user.workspace_id,
                name=d["name"],
                threshold=d["threshold"],
                enabled=True,
            )
            db.add(r)
            seeded.append(r)
        db.flush()
        rows = seeded
    return {
        "data": [
            {"id": str(r.id), "name": r.name, "threshold": r.threshold, "enabled": r.enabled}
            for r in rows
        ]
    }


@router.post("/rules", response_model=dict, status_code=201)
def create_alert_rule(
    payload: AlertRuleCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a new alert rule (admin only)."""
    if not has_capability(current_user, ALERTS_CONFIGURE):
        raise _error(403, "forbidden", "Administrator role required")
    row = AlertRule(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        name=payload.name,
        threshold=payload.threshold,
        enabled=payload.enabled,
    )
    db.add(row)
    db.flush()
    return {"id": str(row.id), "name": row.name, "threshold": row.threshold, "enabled": row.enabled}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_alert_rule(
    rule_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete an alert rule (admin only)."""
    if not has_capability(current_user, ALERTS_CONFIGURE):
        raise _error(403, "forbidden", "Administrator role required")
    row: AlertRule | None = db.get(AlertRule, rule_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Rule not found")
    db.delete(row)
    db.flush()


@router.patch("/rules/{rule_id}", response_model=dict)
def patch_alert_rule(
    rule_id: uuid.UUID,
    payload: AlertRuleUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update an alert rule (admin only)."""
    if not has_capability(current_user, ALERTS_CONFIGURE):
        raise _error(403, "forbidden", "Administrator role required")
    row: AlertRule | None = db.get(AlertRule, rule_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Rule not found")
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.name is not None:
        row.name = payload.name
    if payload.threshold is not None:
        row.threshold = payload.threshold
    db.flush()
    return {"id": str(row.id), "name": row.name, "threshold": row.threshold, "enabled": row.enabled}
