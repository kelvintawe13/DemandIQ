"""Reporting endpoints per ARCHITECTURE.md §6 and API_ENDPOINTS.md analytics & reporting.

* GET  /api/v1/reports/templates
* POST /api/v1/reports/generate
* GET  /api/v1/reports/{id}
* GET  /api/v1/reports
* GET  /api/v1/reports/schedules
* POST /api/v1/reports/schedules
* PATCH /api/v1/reports/schedules/{id}
* DELETE /api/v1/reports/schedules/{id}
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.security.api_key_auth import get_principal, require_write_scope
from app.security.permissions import REPORTS_GENERATE, REPORTS_READ, has_capability
from app.services.reporting.builder import (
    REPORT_TEMPLATES,
    build_signed_url,
    generate_report,
    get_report,
    list_reports,
)
from app.services.reporting.scheduler import (
    create_schedule,
    delete_schedule,
    list_schedules,
    update_schedule,
)

logger = logging.getLogger(__name__)


def require_reports_read(user: Annotated[User, Depends(get_principal)]) -> User:
    """Reading reports and templates."""
    if not has_capability(user, REPORTS_READ):
        raise _error(403, "forbidden", "Report read permission required")
    return user


def require_reports_generate(user: Annotated[User, Depends(get_principal)]) -> User:
    """Generating a report or editing its schedule is a write."""
    if not has_capability(user, REPORTS_GENERATE):
        raise _error(403, "forbidden", "Report generate permission required")
    return user


router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
    dependencies=[Depends(require_reports_read)],
)


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Body for POST /reports/generate."""

    template_id: str = Field(description="Template id, e.g. demand_summary")
    period: dict[str, str] | None = Field(default=None, description="{start, end} ISO dates")
    period_start: datetime.date | None = Field(default=None)
    period_end: datetime.date | None = Field(default=None)
    format: str = Field(default="pdf", description="pdf|csv|xlsx")
    location_ids: list[uuid.UUID] | None = Field(default=None)


class ScheduleCreateRequest(BaseModel):
    """Body for POST /reports/schedules."""

    template: str | None = Field(default=None, description="Alias for template_id")
    template_id: str | None = Field(default=None)
    cadence: str = Field(description="daily|weekly|monthly")
    recipients: list[str] = Field(description="Email recipients")
    format: str = Field(default="pdf", description="pdf|csv|xlsx")
    enabled: bool = Field(default=True)


class SchedulePatchRequest(BaseModel):
    """Body for PATCH /reports/schedules/{id}."""

    cadence: str | None = Field(default=None)
    recipients: list[str] | None = Field(default=None)
    format: str | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    template: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# GET /templates
# ---------------------------------------------------------------------------


@router.get("/templates", response_model=dict)
def list_templates(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> dict[str, Any]:
    """Return available report templates."""
    _ = db
    _ = current_user
    return {"data": REPORT_TEMPLATES}


# ---------------------------------------------------------------------------
# POST /generate
# ---------------------------------------------------------------------------


@router.post(
    "/generate",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_reports_generate)],
)
def generate(
    payload: GenerateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> dict[str, Any]:
    """Queue a report generation (async stub). Returns ``report_id``."""
    require_write_scope(current_user)
    # Normalise period
    p_start = payload.period_start
    p_end = payload.period_end
    if payload.period is not None:
        try:
            if "start" in payload.period:
                p_start = datetime.date.fromisoformat(payload.period["start"])
            if "end" in payload.period:
                p_end = datetime.date.fromisoformat(payload.period["end"])
        except ValueError as exc:
            raise _error(
                400, "invalid_period", "Invalid period date format", field="period"
            ) from exc

    try:
        report = generate_report(
            db,
            workspace_id=current_user.workspace_id,
            template_id=payload.template_id,
            period_start=p_start,
            period_end=p_end,
            fmt=payload.format,
            location_ids=payload.location_ids,
            generated_by=current_user.id,
        )
    except ValueError as exc:
        # Distinguish format vs template errors for field
        msg = str(exc)
        if "format" in msg.lower():
            raise _error(400, "invalid_format", msg, field="format") from exc
        raise _error(400, "invalid_template", msg, field="template_id") from exc

    return {"report_id": str(report.id), "status": report.status, "id": str(report.id)}


# ---------------------------------------------------------------------------
# Schedules — placed BEFORE /{report_id} to avoid UUID shadowing
# ---------------------------------------------------------------------------


@router.get("/schedules", response_model=dict)
def list_all_schedules(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> dict[str, Any]:
    """List scheduled deliveries."""
    rows = list_schedules(db, current_user.workspace_id)
    return {
        "data": [
            {
                "id": str(r.id),
                "template": r.template,
                "cadence": r.cadence,
                "recipients": r.recipients,
                "format": r.format,
                "enabled": r.enabled,
                "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
            }
            for r in rows
        ]
    }


# ---------------------------------------------------------------------------
# GET /  (list)
# ---------------------------------------------------------------------------


@router.get("", response_model=dict)
def list_all(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """List recently generated reports (cursor-paginated)."""
    try:
        page, next_cursor, has_more = list_reports(
            db, current_user.workspace_id, cursor=cursor, limit=limit
        )
    except ValueError as exc:
        raise _error(400, "invalid_cursor", "Invalid pagination cursor") from exc

    return {
        "data": [
            {
                "id": str(r.id),
                "template": r.template,
                "period_start": r.period_start.isoformat() if r.period_start else None,
                "period_end": r.period_end.isoformat() if r.period_end else None,
                "format": r.format,
                "status": r.status,
                "file_uri": r.file_uri,
                "size_bytes": r.size_bytes,
                "created_at": r.created_at.isoformat(),
            }
            for r in page
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


# ---------------------------------------------------------------------------
# GET /{id}
# ---------------------------------------------------------------------------


@router.get("/{report_id}", response_model=dict)
def get_one(
    report_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> dict[str, Any]:
    """Get report status; when ``completed`` includes a signed download URL."""
    row = get_report(db, report_id, current_user.workspace_id)
    if row is None:
        raise _error(404, "not_found", "Report not found")

    signed = build_signed_url(row)
    return {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "template": row.template,
        "period_start": row.period_start.isoformat() if row.period_start else None,
        "period_end": row.period_end.isoformat() if row.period_end else None,
        "format": row.format,
        "status": row.status,
        "file_uri": row.file_uri,
        "signed_url": signed,
        "download_url": signed,
        "size_bytes": row.size_bytes,
        "created_at": row.created_at.isoformat(),
    }


@router.post(
    "/schedules",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_reports_generate)],
)
def create_one_schedule(
    payload: ScheduleCreateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> dict[str, Any]:
    """Create a report schedule."""
    require_write_scope(current_user)
    template = payload.template or payload.template_id
    if not template:
        raise _error(422, "validation_error", "template is required", field="template")
    try:
        row = create_schedule(
            db,
            workspace_id=current_user.workspace_id,
            template=template,
            cadence=payload.cadence,
            recipients=payload.recipients,
            fmt=payload.format,
            enabled=payload.enabled,
        )
    except ValueError as exc:
        msg = str(exc)
        field = (
            "cadence"
            if "cadence" in msg.lower()
            else ("format" if "format" in msg.lower() else None)
        )
        raise _error(400, "invalid_request", msg, field=field) from exc

    return {
        "id": str(row.id),
        "template": row.template,
        "cadence": row.cadence,
        "recipients": row.recipients,
        "format": row.format,
        "enabled": row.enabled,
        "next_run_at": row.next_run_at.isoformat() if row.next_run_at else None,
    }


@router.patch(
    "/schedules/{schedule_id}",
    response_model=dict,
    dependencies=[Depends(require_reports_generate)],
)
def patch_schedule(
    schedule_id: uuid.UUID,
    payload: SchedulePatchRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> dict[str, Any]:
    """Update a report schedule."""
    require_write_scope(current_user)
    from app.models.report import ReportSchedule

    row: ReportSchedule | None = db.get(ReportSchedule, schedule_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Schedule not found")
    try:
        updated = update_schedule(
            db,
            row,
            cadence=payload.cadence,
            recipients=payload.recipients,
            fmt=payload.format,
            enabled=payload.enabled,
            template=payload.template,
        )
    except ValueError as exc:
        raise _error(400, "invalid_request", str(exc)) from exc

    return {
        "id": str(updated.id),
        "template": updated.template,
        "cadence": updated.cadence,
        "recipients": updated.recipients,
        "format": updated.format,
        "enabled": updated.enabled,
        "next_run_at": updated.next_run_at.isoformat() if updated.next_run_at else None,
    }


@router.delete(
    "/schedules/{schedule_id}",
    status_code=204,
    dependencies=[Depends(require_reports_generate)],
)
def delete_one_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> None:
    """Delete a report schedule."""
    require_write_scope(current_user)
    from app.models.report import ReportSchedule

    row: ReportSchedule | None = db.get(ReportSchedule, schedule_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Schedule not found")
    delete_schedule(db, row)
