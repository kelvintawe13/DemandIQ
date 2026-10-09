"""Quality endpoints (ARCHITECTURE.md §6).

* GET  /api/v1/quality/score
* GET  /api/v1/quality/issues
* POST /api/v1/quality/issues/{id}/apply-fix
* POST /api/v1/quality/issues/{id}/dismiss
* GET  /api/v1/quality/rules
* PATCH /api/v1/quality/rules/{id}
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.quality import QualityIssue, QualityRule
from app.models.user import User
from app.security.permissions import QUALITY_CONFIGURE, QUALITY_FIX, has_capability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/quality", tags=["quality"])


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


class QualityRuleUpdate(BaseModel):
    """Body for PATCH /quality/rules/{id}."""

    enabled: bool | None = Field(default=None)
    name: str | None = Field(default=None)
    description: str | None = Field(default=None)


@router.get("/score", response_model=dict)
def get_score(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Overall and per-dimension quality scores."""
    # Compute score from open issues: more critical issues lowers score.
    open_issues = list(
        db.scalars(
            select(QualityIssue).where(
                QualityIssue.workspace_id == current_user.workspace_id,
                QualityIssue.resolved_at.is_(None),
                QualityIssue.dismissed_at.is_(None),
            )
        ).all()
    )
    # Simple scoring: start at 100, subtract per issue severity.
    score = 100
    for iss in open_issues:
        if iss.severity == "critical":
            score -= 10
        elif iss.severity == "warning":
            score -= 5
        else:
            score -= 2
    score = max(0, score)

    # Per-dimension stubs — in real impl these are completeness, timeliness, etc.
    dimensions = {
        "completeness": max(0, score - 2),
        "timeliness": max(0, score - 1),
        "consistency": score,
        "accuracy": max(0, score - 3),
    }

    return {"overall": score, "dimensions": dimensions, "open_issues": len(open_issues)}


@router.get("/issues", response_model=dict)
def list_issues(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: str = Query(
        default="open",
        description="open (default) | resolved | all",
    ),
    severity: str | None = Query(default=None),
    location_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """List quality issues with suggested fixes.

    Defaults to **open** issues only. This is the same population
    ``GET /quality/score`` counts as ``open_issues``, and it is what makes
    "Apply fix" observably work: a resolved issue leaves the list instead of
    reappearing unchanged, which is how this endpoint used to behave (it
    filtered dismissals but not resolutions, so the row stayed put and the
    action looked like a no-op). Pass ``status=resolved`` for the handled
    history or ``status=all`` for everything not dismissed.
    """
    if status not in ("open", "resolved", "all"):
        raise _error(400, "invalid_status", "status must be open, resolved or all", field="status")

    q = select(QualityIssue).where(
        QualityIssue.workspace_id == current_user.workspace_id, QualityIssue.dismissed_at.is_(None)
    )
    if status == "open":
        q = q.where(QualityIssue.resolved_at.is_(None))
    elif status == "resolved":
        q = q.where(QualityIssue.resolved_at.is_not(None))
    if severity is not None:
        q = q.where(QualityIssue.severity == severity)
    if location_id is not None:
        q = q.where(QualityIssue.location_id == location_id)
    if cursor:
        try:
            ts = datetime.datetime.fromisoformat(cursor)
            q = q.where(QualityIssue.created_at < ts)
        except ValueError:
            raise _error(400, "invalid_cursor", "Invalid cursor") from None
    q = q.order_by(QualityIssue.created_at.desc()).limit(limit + 1)
    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].created_at.isoformat() if has_more and page else None
    return {
        "data": [
            {
                "id": str(r.id),
                "rule_id": str(r.rule_id) if r.rule_id else None,
                "location_id": str(r.location_id) if r.location_id else None,
                "severity": r.severity,
                "summary": r.summary,
                "suggested_fix": r.suggested_fix,
                "created_at": r.created_at.isoformat(),
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
                "dismissed_at": r.dismissed_at.isoformat() if r.dismissed_at else None,
            }
            for r in page
        ],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.post("/issues/{issue_id}/apply-fix", response_model=dict)
def apply_fix(
    issue_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Apply the suggested fix for an issue (marks resolved)."""
    if not has_capability(current_user, QUALITY_FIX):
        raise _error(403, "forbidden", "Insufficient permissions")
    row: QualityIssue | None = db.get(QualityIssue, issue_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Issue not found")
    if row.resolved_at is not None:
        raise _error(409, "already_resolved", "Issue already resolved")
    if row.dismissed_at is not None:
        raise _error(409, "dismissed", "Issue was dismissed")
    row.resolved_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    logger.info("quality issue fix applied id=%s by=%s", row.id, current_user.id)
    return {"id": str(row.id), "status": "resolved", "resolved_at": row.resolved_at.isoformat()}


@router.post("/issues/{issue_id}/dismiss", response_model=dict)
def dismiss_issue(
    issue_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Dismiss an issue with a reason (marks dismissed)."""
    if not has_capability(current_user, QUALITY_FIX):
        raise _error(403, "forbidden", "Insufficient permissions")
    row: QualityIssue | None = db.get(QualityIssue, issue_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Issue not found")
    if row.dismissed_at is not None:
        raise _error(409, "already_dismissed", "Issue already dismissed")
    if row.resolved_at is not None:
        raise _error(409, "already_resolved", "Issue already resolved")
    row.dismissed_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    logger.info("quality issue dismissed id=%s by=%s", row.id, current_user.id)
    return {"id": str(row.id), "status": "dismissed", "dismissed_at": row.dismissed_at.isoformat()}


@router.get("/rules", response_model=dict)
def list_rules(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List quality validation rules and their enabled state."""
    rows = list(
        db.scalars(
            select(QualityRule).where(QualityRule.workspace_id == current_user.workspace_id)
        ).all()
    )
    if not rows:
        # Seed default rules so the UI has something to show.
        defaults = [
            {"name": "gap_detection", "description": "Detects gaps in the demand series"},
            {"name": "spike_detection", "description": "Flags implausible spikes"},
            {
                "name": "duplicate_conflict",
                "description": "Duplicate-day conflicts across channels",
            },
            {"name": "stale_connector", "description": "Connector freshness"},
            {"name": "schema_drift", "description": "Schema drift in uploads"},
        ]
        seeded: list[QualityRule] = []
        for d in defaults:
            r = QualityRule(
                id=uuid.uuid4(),
                workspace_id=current_user.workspace_id,
                name=d["name"],
                description=d["description"],
                enabled=True,
            )
            db.add(r)
            seeded.append(r)
        db.flush()
        rows = seeded
    return {
        "data": [
            {"id": str(r.id), "name": r.name, "description": r.description, "enabled": r.enabled}
            for r in rows
        ]
    }


@router.patch("/rules/{rule_id}", response_model=dict)
def patch_rule(
    rule_id: uuid.UUID,
    payload: QualityRuleUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a quality rule (admin only)."""
    if not has_capability(current_user, QUALITY_CONFIGURE):
        raise _error(403, "forbidden", "Administrator role required")
    row: QualityRule | None = db.get(QualityRule, rule_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Rule not found")
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.name is not None:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    db.flush()
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "enabled": row.enabled,
    }
