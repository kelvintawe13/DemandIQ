"""Standalone worker process — ``python -m app.jobs.worker``.

``POST /sources/{id}/sync`` (and ``POST /pipeline/runs/{id}/rerun``) write a
``queued`` ``pipeline_runs`` row. Something has to drain those rows, and the
in-process scheduler only exists when ``JOBS_ENABLED=true``. This module is the
"something": a process whose entire job is to call :func:`app.jobs.scheduler.run_tick`
on a fixed cadence, so a deployment can run the API without background work and
still have queued syncs complete.

It is deliberately thin — all the behaviour (reap dead runs, drain the queue,
run the due jobs) lives in ``run_tick``, shared with the in-process loop, so
there is one definition of what a tick means.

Usage::

    python -m app.jobs.worker              # loop every JOBS_TICK_SECONDS
    python -m app.jobs.worker --once       # one tick, for a k8s CronJob
    python -m app.jobs.worker --tick-seconds 15
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from typing import Any

from app.config import get_settings
from app.jobs.runner import JOB_SCHEDULE
from app.jobs.scheduler import run_tick

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Match the API's log level so worker output looks like the rest of the app."""
    level = getattr(logging, get_settings().log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _sleep_until(deadline: float, is_stopping: Any) -> None:
    """Sleep in short slices so SIGTERM is picked up promptly."""
    while not is_stopping() and time.monotonic() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def main(argv: list[str] | None = None) -> int:
    """Run the worker.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.worker",
        description="Drain queued pipeline runs and run due scheduled jobs.",
    )
    parser.add_argument(
        "--once", action="store_true", help="run a single tick and exit (for a CronJob)"
    )
    parser.add_argument(
        "--tick-seconds",
        type=int,
        default=None,
        help="override JOBS_TICK_SECONDS for this process",
    )
    args = parser.parse_args(argv)

    _configure_logging()
    # This process runs `forecast_publish` / `recalibration`, which read the
    # serving bundle, so an explicitly configured path that cannot load must stop
    # it here too — not surface later as stub numbers in the forecast table.
    from app.services.forecasting.bundle import (
        assert_serving_configuration,
        describe_serving_mode,
    )

    for line in describe_serving_mode():
        logger.info("Serving: %s", line)
    assert_serving_configuration()

    settings = get_settings()
    tick = max(5, args.tick_seconds or settings.jobs_tick_seconds)

    if args.once:
        counts = run_tick()
        logger.info("single tick complete: %s", counts)
        return 0

    stopping = False

    def _handle_signal(signum: int, frame: Any) -> None:
        nonlocal stopping
        stopping = True
        logger.info("worker received signal %s; finishing current tick", signum)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    logger.info(
        "job worker started (tick=%ss, jobs=%s, jobs_enabled=%s)",
        tick,
        sorted(JOB_SCHEDULE),
        settings.jobs_enabled,
    )
    while not stopping:
        try:
            counts = run_tick()
            if any(counts.values()):
                logger.info("tick: %s", counts)
        except Exception:  # pragma: no cover - defensive
            logger.exception("worker tick failed")
        _sleep_until(time.monotonic() + tick, lambda: stopping)
    logger.info("job worker stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
