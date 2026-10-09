"""In-process scheduler for the job cadence in :mod:`app.jobs.runner`.

Opt-in via ``JOBS_ENABLED=true``: the app lifespan starts one asyncio task that
wakes every ``JOBS_TICK_SECONDS`` and runs any job whose cron field matches the
current minute, once per workspace. Failures are logged and contained so one
bad job never stops the loop.

A tick does three things, in this order: reap ``running`` rows abandoned by a
dead process (:func:`run_job` writes one before the work starts), drain the
``queued`` runs (``POST /sources/{id}/sync``), and run the jobs whose cron
matches. :mod:`app.jobs.worker` runs the very same :func:`run_tick` in its own
process, so a deployment that would rather not schedule work inside the API can
set ``JOBS_ENABLED=false`` and run the worker instead — behaviour is identical,
the cadence just lives somewhere else.

Production would run the same ``JOBS`` functions on a Redis/RQ worker; this
scheduler exists so a single-node deployment still gets the documented
cadences (``IMPLEMENTATION_GUIDE.md`` "Job cadences") without extra services.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from sqlalchemy import select

from app.config import get_settings
from app.database import get_session_factory
from app.jobs.runner import (
    JOB_SCHEDULE,
    JOBS,
    execute_queued,
    recover_stale_runs,
    run_job,
)

logger = logging.getLogger(__name__)

# Minute-keys already executed this process, so a 60s tick cannot double-run.
_executed: set[str] = set()


def _field_matches(field: str, value: int, *, low: int = 0, high: int = 59) -> bool:
    """Whether one cron field (`*`, ``*/n``, ``a-b``, ``a,b`` or a number) matches."""
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            return True
        step = 1
        if "/" in part:
            part, _, step_raw = part.partition("/")
            step = int(step_raw)
        if part in ("*", ""):
            if value % step == 0:
                return True
            continue
        if "-" in part:
            start_raw, _, end_raw = part.partition("-")
            start, end = int(start_raw), int(end_raw)
            if start <= value <= end and (value - start) % step == 0:
                return True
            continue
        if value == int(part):
            return True
    return False


def cron_matches(expr: str, when: datetime.datetime) -> bool:
    """Whether a 5-field cron expression is true at ``when`` (UTC).

    Fields: minute hour day-of-month month day-of-week. Day-of-week follows
    cron convention (0 = Sunday); Python's ``weekday()`` is converted.
    """
    fields = expr.split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    cron_dow = (when.weekday() + 1) % 7  # Mon=1..Sun=0
    return (
        _field_matches(minute, when.minute)
        and _field_matches(hour, when.hour, low=0, high=23)
        and _field_matches(dom, when.day, low=1, high=31)
        and _field_matches(month, when.month, low=1, high=12)
        and _field_matches(dow, cron_dow, low=0, high=6)
    )


def due_jobs(when: datetime.datetime) -> list[str]:
    """Job types whose schedule matches ``when`` (and are registered)."""
    return [
        job_type
        for job_type, sched in JOB_SCHEDULE.items()
        if job_type in JOBS and cron_matches(sched["cron"], when)
    ]


def next_run_after(expr: str, when: datetime.datetime) -> datetime.datetime | None:
    """The first instant strictly after ``when`` that ``expr`` matches (UTC).

    Scans forward day by day (up to roughly a year, enough for any monthly or
    day-of-week cadence in :data:`JOB_SCHEDULE`) and, within a matching day,
    hour by minute. Minute-by-minute scanning of a whole year would be ~500k
    comparisons per job; this is a few hundred.

    Args:
        expr: 5-field cron expression.
        when: Reference instant (naive values are treated as UTC).

    Returns:
        The next matching datetime, or ``None`` for an unparseable expression.
    """
    fields = expr.split()
    if len(fields) != 5:
        return None
    minute, hour, dom, month, dow = fields
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.UTC)
    base = when.astimezone(datetime.UTC).replace(second=0, microsecond=0)
    start = base + datetime.timedelta(minutes=1)

    for day_offset in range(0, 400):
        day = (start + datetime.timedelta(days=day_offset)).date()
        cron_dow = (day.weekday() + 1) % 7  # cron convention: Sunday = 0
        if not _field_matches(dom, day.day, low=1, high=31):
            continue
        if not _field_matches(month, day.month, low=1, high=12):
            continue
        if not _field_matches(dow, cron_dow, low=0, high=6):
            continue
        for h in range(24):
            if not _field_matches(hour, h, low=0, high=23):
                continue
            for m in range(60):
                if not _field_matches(minute, m):
                    continue
                candidate = datetime.datetime.combine(day, datetime.time(h, m), tzinfo=datetime.UTC)
                if candidate >= start:
                    return candidate
    return None


def run_tick(now: datetime.datetime | None = None) -> dict[str, int]:
    """One scheduler pass: reap dead runs, drain the queue, run due jobs.

    Shared by the in-process loop and :mod:`app.jobs.worker` so both processes
    behave identically. Every failure is contained — a bad job must never stop
    the loop.

    Args:
        now: Instant to evaluate the cadence at (defaults to now, UTC).

    Returns:
        ``{"recovered": n, "drained": n, "scheduled": n}`` counts.
    """
    from app.models.workspace import Workspace

    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    counts = {"recovered": 0, "drained": 0, "scheduled": 0}

    factory = get_session_factory()
    db = factory()
    try:
        # 1. A ``running`` row whose process died would otherwise never settle.
        try:
            counts["recovered"] = recover_stale_runs(db)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("reaping stale runs failed")

        # 2. Drain anything queued by hand (e.g. POST /sources/{id}/sync).
        try:
            counts["drained"] = execute_queued(db)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("draining queued runs failed")

        # 3. Run the jobs whose cron matches this minute, once per workspace.
        try:
            workspaces = [
                w for w in db.scalars(select(Workspace)).all() if isinstance(w, Workspace)
            ]
        except Exception:
            db.rollback()
            logger.exception("could not list workspaces for the scheduler tick")
            return counts
        for job_type in due_jobs(now):
            key = f"{job_type}:{now.strftime('%Y-%m-%dT%H:%M')}"
            if key in _executed:
                continue
            _executed.add(key)
            for ws in workspaces:
                try:
                    run_job(db, workspace_id=ws.id, job_type=job_type)
                    db.commit()
                    counts["scheduled"] += 1
                except Exception:
                    db.rollback()
                    logger.exception("scheduled %s failed for workspace %s", job_type, ws.id)
            if len(_executed) > 5000:  # bound the de-dupe memory
                _executed.clear()
    finally:
        db.close()
    return counts


async def scheduler_loop(stop: asyncio.Event) -> None:
    """Tick loop; sleeps ``JOBS_TICK_SECONDS`` or until ``stop`` is set."""
    tick = max(5, get_settings().jobs_tick_seconds)
    logger.info("job scheduler started (tick=%ss, jobs=%s)", tick, sorted(JOB_SCHEDULE))
    while not stop.is_set():
        try:
            run_tick(datetime.datetime.now(datetime.UTC))
        except Exception:  # pragma: no cover - defensive
            logger.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick)
        except TimeoutError:
            continue
    logger.info("job scheduler stopped")
