"""Background jobs — scheduled work per ``IMPLEMENTATION_GUIDE.md``.

Cadences (see :data:`app.jobs.runner.JOB_SCHEDULE`):

- Connector sync (every 15 min)
- Feature materialisation (03:20 daily)
- Recalibration (03:40 daily)
- Forecast publish (04:00 daily)
- Full retrain (Sundays 02:00)
- Report delivery (07:00 local)
- Drift check (hourly)

:mod:`app.jobs.runner` implements the work; :mod:`app.jobs.scheduler` is the
tick — it reaps abandoned runs, drains the queue and runs the due jobs.
:mod:`app.jobs.worker` runs that tick in its own process (``python -m
app.jobs.worker``) for deployments that prefer not to schedule work inside the
API; the in-process loop is used instead when ``JOBS_ENABLED`` is set.

Importing this package never requires Redis or a live database.
"""

from __future__ import annotations

from app.jobs.runner import (
    JOB_SCHEDULE,
    JOBS,
    JobOutcome,
    enqueue_run,
    execute_queued,
    recover_stale_runs,
    run_job,
)
from app.jobs.scheduler import cron_matches, due_jobs, next_run_after, run_tick, scheduler_loop

__all__ = [
    "JOBS",
    "JOB_SCHEDULE",
    "JobOutcome",
    "cron_matches",
    "due_jobs",
    "enqueue_run",
    "execute_queued",
    "next_run_after",
    "recover_stale_runs",
    "run_job",
    "run_tick",
    "scheduler_loop",
]
