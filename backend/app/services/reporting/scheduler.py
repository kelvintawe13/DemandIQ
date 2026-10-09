"""Report schedule management."""

from __future__ import annotations

import datetime
import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.report import ReportSchedule

ALLOWED_CADENCES = {"daily", "weekly", "monthly"}
ALLOWED_FORMATS = {"pdf", "csv", "xlsx"}


def compute_next_run(cadence: str, now: datetime.datetime | None = None) -> datetime.datetime:
    """Compute ``next_run_at`` for a cadence.

    * daily  — tomorrow at 07:00 UTC
    * weekly — next Monday at 07:00 UTC
    * monthly — 1st of next month at 07:00 UTC

    Args:
        cadence: One of ``daily`` / ``weekly`` / ``monthly`` (case-insensitive).
        now: Reference time (defaults to ``now()`` in UTC).

    Returns:
        UTC datetime for the next run.
    """
    cad = cadence.lower()
    if cad not in ALLOWED_CADENCES:
        raise ValueError(f"cadence must be one of {sorted(ALLOWED_CADENCES)}")
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)

    base = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if cad == "daily":
        nxt = base + datetime.timedelta(days=1)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        return nxt
    if cad == "weekly":
        # Next Monday
        days_ahead = (7 - base.weekday()) % 7
        if days_ahead == 0 and base <= now:
            days_ahead = 7
        # Ensure strictly future
        nxt = base + datetime.timedelta(days=days_ahead if days_ahead else 7)
        if nxt <= now:
            nxt += datetime.timedelta(days=7)
        return nxt
    # monthly
    year = base.year
    month = base.month + 1
    if month > 12:
        month = 1
        year += 1
    nxt = datetime.datetime(year, month, 1, 7, 0, tzinfo=datetime.UTC)
    if nxt <= now:
        # Advance one more month
        year2 = nxt.year
        month2 = nxt.month + 1
        if month2 > 12:
            month2 = 1
            year2 += 1
        nxt = datetime.datetime(year2, month2, 1, 7, 0, tzinfo=datetime.UTC)
    return nxt


def list_schedules(db: Session, workspace_id: uuid.UUID) -> list[ReportSchedule]:
    """List all schedules for a workspace."""
    return list(
        db.scalars(
            select(ReportSchedule)
            .where(ReportSchedule.workspace_id == workspace_id)
            .order_by(ReportSchedule.next_run_at.asc().nulls_last(), ReportSchedule.id.asc())
        ).all()
    )


def create_schedule(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    template: str,
    cadence: str,
    recipients: list[str],
    fmt: str,
    enabled: bool = True,
) -> ReportSchedule:
    """Create a schedule and compute ``next_run_at``."""
    cad_norm = cadence.lower()
    if cad_norm not in ALLOWED_CADENCES:
        raise ValueError(f"cadence must be one of {sorted(ALLOWED_CADENCES)}")
    fmt_norm = fmt.lower()
    if fmt_norm not in ALLOWED_FORMATS:
        raise ValueError(f"format must be one of {sorted(ALLOWED_FORMATS)}")
    if not recipients:
        raise ValueError("recipients must not be empty")

    sched = ReportSchedule(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        template=template,
        cadence=cad_norm,
        recipients=recipients,
        format=fmt_norm,
        enabled=enabled,
        next_run_at=compute_next_run(cad_norm) if enabled else None,
    )
    db.add(sched)
    db.flush()
    return sched


def update_schedule(
    db: Session,
    schedule: ReportSchedule,
    *,
    cadence: str | None = None,
    recipients: list[str] | None = None,
    fmt: str | None = None,
    enabled: bool | None = None,
    template: str | None = None,
) -> ReportSchedule:
    """Patch a schedule; recompute ``next_run_at`` when cadence/enabled changes."""
    if template is not None:
        schedule.template = template
    if cadence is not None:
        cad_norm = cadence.lower()
        if cad_norm not in ALLOWED_CADENCES:
            raise ValueError(f"cadence must be one of {sorted(ALLOWED_CADENCES)}")
        schedule.cadence = cad_norm
    if recipients is not None:
        if not recipients:
            raise ValueError("recipients must not be empty")
        schedule.recipients = recipients
    if fmt is not None:
        fmt_norm = fmt.lower()
        if fmt_norm not in ALLOWED_FORMATS:
            raise ValueError(f"format must be one of {sorted(ALLOWED_FORMATS)}")
        schedule.format = fmt_norm
    if enabled is not None:
        schedule.enabled = enabled

    # Recompute next_run_at if relevant fields changed
    if cadence is not None or enabled is not None:
        if schedule.enabled:
            schedule.next_run_at = compute_next_run(schedule.cadence)
        else:
            schedule.next_run_at = None

    db.flush()
    return schedule


def delete_schedule(db: Session, schedule: ReportSchedule) -> None:
    """Delete a schedule."""
    db.delete(schedule)
    db.flush()
    _ = hashlib.sha256  # keep import used for mypy strict
