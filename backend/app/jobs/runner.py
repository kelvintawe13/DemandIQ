"""Background job runner — the scheduled jobs from ``IMPLEMENTATION_GUIDE.md``.

Each job is a function ``(db, workspace_id) -> JobOutcome`` that does real work
against the configured tables and reports ``(rows_processed, detail)``.
:func:`run_job` wraps a call so that **every** execution writes exactly one
``pipeline_runs`` row — ``running`` then ``success``/``failed`` — which is what
``GET /pipeline/runs`` shows and what the admin console reads.

Execution is in-process and synchronous here. Production hands the same
functions to a Redis/RQ worker (:mod:`app.jobs.scheduler` drives the cadence);
because the functions only touch the DB session they are given, the executor
can be swapped without changing behaviour.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.forecast import Calibration, Forecast, ModelVersion
from app.models.location import Connector, DemandObservation
from app.models.pipeline import PipelineRun
from app.models.report import ReportSchedule
from app.services.alerting import raise_alert, record_job_failure, record_job_success
from app.services.drift import compute_drift
from app.services.forecasting.generate import generate_forecast
from app.services.ingestion.connector_pull import pull_connector

logger = logging.getLogger(__name__)

DEFAULT_FEATURE_SET_VERSION = "fs-2026.09.2"

# A ``running`` row older than this was abandoned by a dead worker/process.
STALE_RUN_MINUTES = 15

# Reports rendered per tick. Rendering is CPU-bound (a PDF build), so the batch is
# small and the next tick picks up the rest rather than one workspace's backlog
# starving every other job in the tick.
REPORT_BATCH = 5

# Cadence table — kept beside the jobs so GET /pipeline/schedule and the
# scheduler agree on one source of truth.
JOB_SCHEDULE: dict[str, dict[str, str]] = {
    "connector_sync": {"cadence": "Every 15 min", "cron": "*/15 * * * *"},
    "feature_materialisation": {"cadence": "03:20 daily", "cron": "20 3 * * *"},
    "recalibration": {"cadence": "03:40 daily", "cron": "40 3 * * *"},
    "forecast_publish": {"cadence": "04:00 daily", "cron": "0 4 * * *"},
    "full_retrain": {"cadence": "Sundays 02:00", "cron": "0 2 * * 0"},
    "report_delivery": {"cadence": "07:00 local", "cron": "0 7 * * *"},
    "report_generation": {"cadence": "Every minute", "cron": "* * * * *"},
    "drift_check": {"cadence": "Hourly", "cron": "0 * * * *"},
}


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """What a job did: rows touched (or ``None``) plus a human-readable note."""

    rows_processed: int | None
    detail: str


def _orm(rows: Iterable[object], cls: type) -> list[Any]:
    """Keep only real ORM instances (mocked sessions return MagicMocks)."""
    try:
        return [r for r in rows if isinstance(r, cls)]
    except TypeError:
        return []


# ---------------------------------------------------------------------------
# Job implementations
# ---------------------------------------------------------------------------


def _job_connector_sync(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Pull each active connector's demand from its provider and commit it.

    This is a real pull. For every ``status == "active"`` connector the job
    fetches the provider endpoint configured in the connector's credentials and
    writes the returned rows through :func:`commit_demand`, tagged with the
    connector's ``source_id``.

    Three consequences the callers rely on:

    * Only ``status == "active"`` is touched. A connector is created
      ``available``, so a sync for one would pull nothing; ``POST
      /sources/{id}/sync`` therefore refuses it rather than queueing a run that
      cannot do anything.
    * A connector with no pull endpoint is *not* treated as a completed sync:
      it is fetched from nowhere, so ``last_sync_at`` is left alone and the run
      detail says so. Stamping freshness here is what made an unconfigured
      channel look like it had pulled.
    * For a configured connector, ``last_sync_at`` is stamped *here*, on
      completion, so freshness in ``GET /sources`` reports a finished pull and
      never a queued one.
    """
    now = datetime.datetime.now(datetime.UTC)
    conns = _orm(
        db.scalars(
            select(Connector).where(
                Connector.workspace_id == workspace_id, Connector.status == "active"
            )
        ).all(),
        Connector,
    )
    committed = 0
    parts: list[str] = []
    for conn in conns:
        outcome = pull_connector(db, conn)
        committed += outcome.committed
        parts.append(f"{conn.name}: {outcome.detail}")
        # Record the outcome on the channel itself; `last_sync_at` is the
        # timestamp, these are what happened (see migration 016).
        conn.last_pull_detail = outcome.persisted_detail
        if outcome.configured:
            conn.last_sync_at = now
            conn.last_pull_pulled = outcome.pulled
            conn.last_pull_committed = outcome.committed
            conn.last_pull_skipped = outcome.skipped
    if conns:
        db.flush()
    detail = "; ".join(parts) if parts else "no active connectors to sync"
    return JobOutcome(committed, detail)


def _job_feature_materialisation(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Row count of the demand history the feature store is built from."""
    rows = _orm(
        db.scalars(
            select(DemandObservation).where(DemandObservation.workspace_id == workspace_id)
        ).all(),
        DemandObservation,
    )
    return JobOutcome(len(rows), f"materialised features over {len(rows)} observation(s)")


def _job_recalibration(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Recompute realised interval coverage and publish it on the calibration row."""
    now = datetime.datetime.now(datetime.UTC)
    cutoff = now.date() - datetime.timedelta(days=30)
    prods = _orm(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.workspace_id == workspace_id, ModelVersion.status == "production"
            )
        ).all(),
        ModelVersion,
    )
    if not prods:
        return JobOutcome(None, "no production model to recalibrate")
    prod = prods[0]

    forecasts = _orm(
        db.scalars(
            select(Forecast).where(
                Forecast.workspace_id == workspace_id, Forecast.target_date >= cutoff
            )
        ).all(),
        Forecast,
    )
    demand = {
        (o.location_id, o.date): float(o.demand_qty)
        for o in _orm(
            db.scalars(
                select(DemandObservation).where(
                    DemandObservation.workspace_id == workspace_id,
                    DemandObservation.date >= cutoff,
                )
            ).all(),
            DemandObservation,
        )
    }
    pairs = [
        (f, demand[(f.location_id, f.target_date)])
        for f in forecasts
        if (f.location_id, f.target_date) in demand
    ]
    if not pairs:
        return JobOutcome(0, "no realised forecasts in the last 30 days to calibrate on")

    covered = sum(
        1 for f, actual in pairs if float(f.lower_bound) <= actual <= float(f.upper_bound)
    )
    coverage = covered / len(pairs) * 100.0
    width = sum(float(f.upper_bound) - float(f.lower_bound) for f, _ in pairs) / len(pairs)

    calib = None
    try:
        calib = db.scalar(
            select(Calibration)
            .where(Calibration.model_version_id == prod.id)
            .order_by(Calibration.evaluated_at.desc())
            .limit(1)
        )
    except Exception:
        calib = None
    if isinstance(calib, Calibration):
        calib.empirical_coverage = coverage  # type: ignore[assignment]
        calib.mean_interval_width = width  # type: ignore[assignment]
        calib.evaluated_at = now
    else:
        calib = Calibration(
            id=uuid.uuid4(),
            model_version_id=prod.id,
            method="adaptive_conformal",
            target_coverage=90.0,
            empirical_coverage=coverage,  # type: ignore[arg-type]
            mean_interval_width=width,  # type: ignore[arg-type]
            evaluated_at=now,
        )
        db.add(calib)
    db.flush()
    return JobOutcome(
        len(pairs), f"realised coverage {coverage:.1f}% over {len(pairs)} forecast(s)"
    )


def _job_forecast_publish(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Publish tomorrow's forecast for every location with recent demand."""
    now = datetime.datetime.now(datetime.UTC)
    target = now.date() + datetime.timedelta(days=1)
    cutoff = now.date() - datetime.timedelta(days=30)

    obs = _orm(
        db.scalars(
            select(DemandObservation).where(
                DemandObservation.workspace_id == workspace_id,
                DemandObservation.date >= cutoff,
            )
        ).all(),
        DemandObservation,
    )
    location_ids = {o.location_id for o in obs}
    if not location_ids:
        return JobOutcome(0, "no locations with recent demand, nothing to publish")

    existing = {
        (f.location_id, f.target_date)
        for f in _orm(
            db.scalars(
                select(Forecast).where(
                    Forecast.workspace_id == workspace_id, Forecast.target_date == target
                )
            ).all(),
            Forecast,
        )
    }
    published = 0
    for location_id in location_ids:
        if (location_id, target) in existing:
            continue
        try:
            # Same generation path as POST /forecasts, so the nightly publish
            # and the on-demand endpoint cannot disagree about how a forecast
            # is produced. as_of is the job's run timestamp (the boundary).
            generate_forecast(
                db,
                workspace_id=workspace_id,
                location_id=location_id,
                target_date=target,
                as_of=now,
                horizon_days=1,
                coverage_target=0.90,
            )
            published += 1
        except Exception:
            logger.warning("forecast publish failed for location %s", location_id, exc_info=True)
    return JobOutcome(published, f"published {published} forecast(s) for {target.isoformat()}")


def _job_full_retrain(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Queue a training run on the registry; the model package does the fit."""
    now = datetime.datetime.now(datetime.UTC)
    running = _orm(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.workspace_id == workspace_id, ModelVersion.status == "training"
            )
        ).all(),
        ModelVersion,
    )
    if running:
        return JobOutcome(None, "a training run is already queued")

    prod = _orm(
        db.scalars(
            select(ModelVersion).where(
                ModelVersion.workspace_id == workspace_id, ModelVersion.status == "production"
            )
        ).all(),
        ModelVersion,
    )
    algorithm = prod[0].algorithm if prod else "lightgbm"
    feature_set = prod[0].feature_set_version if prod else DEFAULT_FEATURE_SET_VERSION
    count = len(
        _orm(
            db.scalars(
                select(ModelVersion).where(
                    ModelVersion.workspace_id == workspace_id, ModelVersion.algorithm == algorithm
                )
            ).all(),
            ModelVersion,
        )
    )
    row = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        algorithm=algorithm,
        version=f"v{count + 1}",
        status="training",
        feature_set_version=feature_set,
        trained_at=now,
    )
    db.add(row)
    db.flush()
    return JobOutcome(None, f"queued {algorithm} {row.version} (feature set {feature_set})")


def _job_report_delivery(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Generate every report schedule whose next run is due."""
    from app.services.reporting.builder import generate_report
    from app.services.reporting.scheduler import compute_next_run

    now = datetime.datetime.now(datetime.UTC)
    schedules = _orm(
        db.scalars(
            select(ReportSchedule).where(
                ReportSchedule.workspace_id == workspace_id, ReportSchedule.enabled.is_(True)
            )
        ).all(),
        ReportSchedule,
    )
    delivered = 0
    for sched in schedules:
        if sched.next_run_at is not None and sched.next_run_at > now:
            continue
        try:
            generate_report(
                db,
                workspace_id=workspace_id,
                template_id=sched.template,
                period_start=None,
                period_end=None,
                fmt=sched.format,
                generated_by=None,
            )
            sched.next_run_at = compute_next_run(sched.cadence, now)  # type: ignore[assignment]
            delivered += 1
        except Exception:
            logger.warning("report delivery failed for schedule %s", sched.id, exc_info=True)
    if delivered:
        db.flush()
    return JobOutcome(delivered, f"delivered {delivered} scheduled report(s)")


def _job_report_generation(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Render every report this workspace has queued.

    ``POST /reports/generate`` only inserts the row, so this is the half that
    produces the file. A failure marks the row ``failed`` with the reason in
    ``reports.error`` (see ``render_stored_report``) and moves on: one template
    that cannot render must not stop the others from being delivered.
    """
    from app.models.report import Report
    from app.services.reporting.builder import render_stored_report

    queued = _orm(
        db.scalars(
            select(Report)
            .where(Report.workspace_id == workspace_id, Report.status == "queued")
            .order_by(Report.created_at)
            .limit(REPORT_BATCH)
        ).all(),
        Report,
    )
    if not queued:
        return JobOutcome(0, "no queued reports")

    rendered = 0
    failed = 0
    for report in queued:
        try:
            render_stored_report(db, report)
            rendered += 1
        except Exception:
            failed += 1
            logger.warning("report render failed id=%s", report.id, exc_info=True)
    db.flush()
    return JobOutcome(rendered, f"rendered {rendered} report(s), {failed} failed")


def _job_drift_check(db: Session, workspace_id: uuid.UUID) -> JobOutcome:
    """Compute drift and raise an alert when it crosses the retrain threshold."""
    result = compute_drift(db, workspace_id)
    signals = result.get("signals", [])
    overall = result.get("overall", "unknown")
    if overall in ("drifting", "watch"):
        severity = "critical" if overall == "drifting" else "warning"
        detail = signals[0]["detail"] if signals else "drift threshold crossed"
        # raise_alert de-dupes on the title, so the hourly check keeps a single
        # open alert and fires the alert.raised webhook only once per episode.
        raise_alert(
            db,
            workspace_id=workspace_id,
            severity=severity,
            title=f"Demand drift {overall}",
            body=detail,
            payload={
                "overall": overall,
                "signals": signals,
                "baseline": result.get("baseline"),
                "source": "drift_check",
            },
        )
    return JobOutcome(len(signals), f"drift {overall}")


JOBS: dict[str, Callable[[Session, uuid.UUID], JobOutcome]] = {
    "connector_sync": _job_connector_sync,
    "feature_materialisation": _job_feature_materialisation,
    "recalibration": _job_recalibration,
    "forecast_publish": _job_forecast_publish,
    "full_retrain": _job_full_retrain,
    "report_delivery": _job_report_delivery,
    "report_generation": _job_report_generation,
    "drift_check": _job_drift_check,
}


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def enqueue_run(
    db: Session, *, workspace_id: uuid.UUID, job_type: str, detail: str | None = None
) -> PipelineRun:
    """Create a ``queued`` ``pipeline_runs`` row without executing it."""
    if job_type not in JOBS:
        raise ValueError(f"unknown job_type '{job_type}'")
    run = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        job_type=job_type,
        status="queued",
        rows_processed=None,
        duration_ms=None,
        detail=detail or f"{job_type} queued",
        started_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(run)
    db.flush()
    return run


def record_manual_run(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    job_type: str,
    detail: str,
    rows_processed: int | None = None,
    started_at: datetime.datetime | None = None,
) -> PipelineRun:
    """Record a job that ran outside the scheduler (e.g. an on-demand pull).

    ``GET /pipeline/runs`` and the Sources history read one table, so a manual
    pull has to leave a row with the same shape :func:`run_job` writes —
    otherwise it is invisible to every run view. The success settles the same
    failure alert a scheduled success would.
    """
    if job_type not in JOBS:
        raise ValueError(f"unknown job_type '{job_type}'")
    finished = datetime.datetime.now(datetime.UTC)
    started = started_at or finished
    run = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        job_type=job_type,
        status="success",
        rows_processed=rows_processed,
        duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
        detail=detail,
        started_at=started,
    )
    db.add(run)
    db.flush()
    _settle_alerts(db, run)
    return run


def execute_queued(db: Session, *, limit: int = 25, workspace_id: uuid.UUID | None = None) -> int:
    """Run ``queued`` ``pipeline_runs`` rows — the worker side of :func:`enqueue_run`.

    Args:
        db: Database session.
        limit: Maximum runs to process in this pass.
        workspace_id: Restrict to one workspace; ``None`` drains every workspace
            (what the worker and scheduler pass).

    Returns:
        Count of runs processed this pass.
    """
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.status == "queued")
        .order_by(PipelineRun.started_at.asc())
        .limit(limit)
    )
    if workspace_id is not None:
        stmt = stmt.where(PipelineRun.workspace_id == workspace_id)
    try:
        queued = [r for r in db.scalars(stmt).all() if isinstance(r, PipelineRun)]
    except Exception:
        return 0
    processed = 0
    for run in queued:
        started = datetime.datetime.now(datetime.UTC)
        run.status = "running"
        db.flush()
        try:
            outcome = JOBS[run.job_type](db, run.workspace_id)
            run.status = "success"
            run.rows_processed = outcome.rows_processed
            run.detail = outcome.detail
            _settle_alerts(db, run)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("queued job %s failed", run.job_type)
            run.status = "failed"
            run.detail = f"{run.job_type} failed: {exc}"
            _fail_alerts(db, run, exc)
        finally:
            finished = datetime.datetime.now(datetime.UTC)
            run.duration_ms = int((finished - started).total_seconds() * 1000)
            db.flush()
        processed += 1
    return processed


def _settle_alerts(db: Session, run: PipelineRun) -> None:
    """Clear the open failure alert for a job that just succeeded."""
    try:
        record_job_success(db, workspace_id=run.workspace_id, job_type=run.job_type)
    except Exception:  # pragma: no cover - alerting must not fail the job
        logger.warning("could not resolve alert for %s", run.job_type, exc_info=True)


def _fail_alerts(db: Session, run: PipelineRun, exc: BaseException) -> None:
    """Alert on a failed run instead of only stamping the ``pipeline_runs`` row."""
    try:
        record_job_failure(
            db,
            workspace_id=run.workspace_id,
            job_type=run.job_type,
            run_id=run.id,
            error=str(exc) or exc.__class__.__name__,
        )
    except Exception:  # pragma: no cover - alerting must not mask the failure
        logger.warning("could not raise alert for %s", run.job_type, exc_info=True)


def recover_stale_runs(
    db: Session,
    *,
    older_than_minutes: int = STALE_RUN_MINUTES,
    workspace_id: uuid.UUID | None = None,
) -> int:
    """Mark runs abandoned by a dead process as ``failed``.

    A run is written ``running`` before the work starts (so a crash leaves
    evidence), but nothing else ever settles it: the row would show as in
    progress forever and block the console's view of pipeline health. A worker
    calls this on startup and on every tick.

    Without heartbeats a *slow* job is indistinguishable from a dead one, so
    this is a best-effort call: a job that outlives the threshold is marked
    failed, and then overwrites that itself when it finishes, resolving the
    alert it just raised (:func:`app.services.alerting.record_job_success`).
    The threshold is therefore a liveness guess, not a timeout.

    Args:
        db: Database session.
        older_than_minutes: Age past which a ``running`` row is considered dead.
        workspace_id: Restrict to one workspace; ``None`` reaps every workspace.

    Returns:
        Number of runs reaped.
    """
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=older_than_minutes)
    stmt = select(PipelineRun).where(
        PipelineRun.status == "running", PipelineRun.started_at < cutoff
    )
    if workspace_id is not None:
        stmt = stmt.where(PipelineRun.workspace_id == workspace_id)
    try:
        stuck = [r for r in db.scalars(stmt).all() if isinstance(r, PipelineRun)]
    except Exception:
        return 0
    for run in stuck:
        run.status = "failed"
        run.detail = f"{run.job_type} abandoned (no worker heartbeat), marked failed"
        if run.duration_ms is None:
            run.duration_ms = int(older_than_minutes * 60 * 1000)
        _fail_alerts(
            db,
            run,
            RuntimeError(f"stale run: no completion within {older_than_minutes} minutes"),
        )
    if stuck:
        db.flush()
    return len(stuck)


def run_job(
    db: Session, *, workspace_id: uuid.UUID, job_type: str, detail: str | None = None
) -> PipelineRun:
    """Execute a job and record it in ``pipeline_runs``.

    The run row is written *before* the work starts (status ``running``) so a
    crash leaves evidence, then updated to ``success`` or ``failed`` with the
    duration and rows processed.
    """
    if job_type not in JOBS:
        raise ValueError(f"unknown job_type '{job_type}'")
    started = datetime.datetime.now(datetime.UTC)
    run = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        job_type=job_type,
        status="running",
        rows_processed=None,
        duration_ms=None,
        detail=detail or f"{job_type} started",
        started_at=started,
    )
    db.add(run)
    db.flush()

    try:
        outcome = JOBS[job_type](db, workspace_id)
        run.status = "success"
        run.rows_processed = outcome.rows_processed
        run.detail = outcome.detail
        _settle_alerts(db, run)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("job %s failed", job_type)
        run.status = "failed"
        run.detail = f"{job_type} failed: {exc}"
        _fail_alerts(db, run, exc)
    finally:
        finished = datetime.datetime.now(datetime.UTC)
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        db.flush()
    return run
