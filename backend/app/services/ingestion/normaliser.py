"""Shared normaliser — the single commit path for every ingestion channel.

Commit rules (ARCHITECTURE.md §1, IMPLEMENTATION_GUIDE.md):
  1. Idempotent by ``(location_id, date)`` — reconciliation, not duplicate.
  2. No future dates relative to the service day.
  3. Every row keeps its source for traceability.
  4. Uploads are staged until confirmed (handled by routers).
  5. The quantity is inside the range the platform can serve back (``0`` to
     :data:`MAX_DEMAND_QTY`), so a forecast can always be stored for it.

This module enforces 1-3 and 5. Callers must supply the validated ``workspace_id``
so that cross-workspace writes are impossible even if the caller is confused.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import threading
import uuid
import zoneinfo
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.location import DemandObservation, Location
from app.models.workspace import Workspace

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

# The largest single-day demand the platform will accept.
#
# This is a *sanity* bound, not a storage one. It used to be both: the columns
# that hold a forecast were ``numeric(10, 2)``, so a larger entry was accepted,
# stored, and then detonated every later ``POST /forecasts`` for that location as
# a 500 ``NumericValueOutOfRange`` — the worst possible place to discover bad
# input, because nothing the operator typed looked wrong until an unrelated page
# broke. Migration ``015_demand_storage_range`` widened those columns to hold the
# whole ``integer`` range, so storage no longer constrains this number.
#
# What the bound is for now is a typo: 222,000,100 is not a busy café, it is a
# slipped keyboard, and the useful moment to say so is while the operator is
# still looking at the field. Ten million a day is already two orders of
# magnitude above any real location's visitor count, so nothing legitimate is
# turned away. Test ``test_demand_ceiling_fits_the_declared_storage_width``
# keeps the cap and the column widths from drifting apart in either direction.
MAX_DEMAND_QTY = 10_000_000

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Outcome of a single :func:`commit_demand` call."""

    observation: DemandObservation
    created: bool
    previous_qty: int | None


class NormaliserError(ValueError):
    """Domain error raised when a row violates commit rules."""

    def __init__(self, code: str, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field
        self.message = message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_in(tz_name: str | None) -> datetime.date:
    """Return today's date in the workspace's own timezone.

    The service-day ceiling is a *local* business question — "has tomorrow
    started for this restaurant?" — so it cannot be answered in UTC. A
    workspace on ``Africa/Kigali`` (UTC+3, and the default here) crosses into
    the next service day three hours before UTC does; asking in UTC rejects
    that restaurant's legitimate "today" between 21:00 and 24:00 UTC, which is
    the early part of its working evening.

    An unknown or missing timezone falls back to UTC rather than refusing the
    write: a bad tz string in a workspace row should not cost a location its
    ability to ingest.
    """
    if not tz_name:
        return datetime.datetime.now(datetime.UTC).date()
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("unknown workspace timezone %r; using UTC ceiling", tz_name)
        return datetime.datetime.now(datetime.UTC).date()
    return datetime.datetime.now(tz).date()


def _ensure_no_future(target: datetime.date, ceiling: datetime.date) -> None:
    if target > ceiling:
        raise NormaliserError(
            "future_date",
            f"date {target.isoformat()} is in the future",
            field="date",
        )


def validate_demand_qty(qty: int) -> None:
    """Reject a demand quantity the platform could never store or forecast.

    Public because the ingest router's PATCH path edits an observation in place
    instead of going through :func:`commit_demand`, and must apply one rule, not
    a second half-remembered copy of it.
    """
    if qty < 0:
        raise NormaliserError("invalid_demand", "demand_qty must be >= 0", field="demand_qty")
    if qty > MAX_DEMAND_QTY:
        raise NormaliserError(
            "invalid_demand",
            f"demand_qty must be <= {MAX_DEMAND_QTY:,}",
            field="demand_qty",
        )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def validate_demand_row(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    date: datetime.date,
    demand_qty: int,
) -> Location:
    """Apply the commit rules to one row **without writing it**.

    Public so that a caller wanting to *preview* an ingestion — the connector
    pull's dry run — applies exactly the rules :func:`commit_demand` will apply,
    rather than a second, drifting copy of them. ``commit_demand`` calls this
    directly, so the two cannot disagree.

    Args:
        db: Active SQLAlchemy session.
        workspace_id: Owning workspace.
        location_id: Target location.
        date: Service day.
        demand_qty: Visitor-equivalent demand.

    Returns:
        The validated :class:`Location`.

    Raises:
        NormaliserError: If the row violates a commit rule.
    """
    validate_demand_qty(demand_qty)

    # The ceiling is the workspace's local service day, so the workspace is read
    # first. Within one request ``db.get`` hits the identity map after the first
    # call, so a 1000-row batch still costs one query.
    ws = db.get(Workspace, workspace_id)
    _ensure_no_future(date, _today_in(getattr(ws, "timezone", None)))

    loc: Location | None = db.get(Location, location_id)
    if loc is None:
        raise NormaliserError("not_found", "Location not found", field="location_id")
    if loc.workspace_id != workspace_id:
        raise NormaliserError("not_found", "Location not found", field="location_id")
    if loc.archived_at is not None:
        raise NormaliserError("archived", "Location is archived", field="location_id")
    return loc


def commit_demand(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    date: datetime.date,
    demand_qty: int,
    source: str | None = None,
    source_id: uuid.UUID | None = None,
    upload_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> CommitResult:
    """Commit a single canonical demand row with idempotency and auditing.

    The function is the *only* place where ``demand_observations`` are written
    outside of migrations. Every ingestion adapter must call it.

    Args:
        db: Active SQLAlchemy session (caller owns commit/rollback).
        workspace_id: Owning workspace — every write is scoped.
        location_id: Target location.
        date: Service day (not a timestamp).
        demand_qty: Visitor-equivalent demand for the day.
        source: Human-readable source tag (``manual``, ``file``, ``image``, …).
        source_id: Optional connector FK when the row came via a connector.
        upload_id: Optional staged upload that produced the row.
        notes: Free-form notes from the operator.

    Returns:
        :class:`CommitResult` with the persisted observation and whether it was
        created or reconciled.

    Raises:
        NormaliserError: If the row violates a commit rule (future date, out-of-range
            qty, unknown location, workspace mismatch).
    """
    # --- Validation --------------------------------------------------------
    # One shared implementation, so a dry-run preview and a real commit cannot
    # disagree about what is acceptable.
    validate_demand_row(
        db,
        workspace_id=workspace_id,
        location_id=location_id,
        date=date,
        demand_qty=demand_qty,
    )

    # Source tagging — at least one provenance signal should survive.
    # ``source`` is kept in ``notes`` when no connector/upload is present so
    # that the quality monitor can still attribute the row without a JOIN.
    #
    # One format, always bracketed. The unbracketed ``source:file`` used when the
    # row carried no operator note was not what the demand read parsed, so
    # `source` came back null for most rows in the workspace — the write and the
    # read disagreed about their own tag. The `source not in notes` guard is gone
    # with it: a note that happened to contain the word "manual" used to drop the
    # tag entirely.
    tagged_notes = notes
    if source:
        tagged_notes = f"{notes} [source:{source}]" if notes else f"[source:{source}]"

    # --- Idempotent upsert on (location_id, date) --------------------------
    existing: DemandObservation | None = db.scalar(
        select(DemandObservation).where(
            DemandObservation.location_id == location_id,
            DemandObservation.date == date,
        )
    )

    now = datetime.datetime.now(datetime.UTC)

    if existing is not None:
        # Workspace guard on the existing row (defence in depth).
        if existing.workspace_id != workspace_id:
            raise NormaliserError("conflict", "Existing row belongs to another workspace")

        prev = existing.demand_qty
        existing.demand_qty = demand_qty
        existing.notes = tagged_notes
        existing.recorded_at = now
        if source_id is not None:
            existing.source_id = source_id
        if upload_id is not None:
            existing.upload_id = upload_id
        db.flush()
        logger.info(
            "demand reconciled location=%s date=%s %s -> %s source=%s",
            location_id,
            date,
            prev,
            demand_qty,
            source,
        )
        _publish_demand_change(
            workspace_id=workspace_id,
            location_id=location_id,
            date=date,
            demand_qty=demand_qty,
            created=False,
            previous_qty=prev,
            source=source,
        )
        return CommitResult(observation=existing, created=False, previous_qty=prev)

    obs = DemandObservation(
        workspace_id=workspace_id,
        location_id=location_id,
        date=date,
        demand_qty=demand_qty,
        source_id=source_id,
        upload_id=upload_id,
        notes=tagged_notes,
        recorded_at=now,
    )
    db.add(obs)
    db.flush()
    logger.info(
        "demand committed location=%s date=%s qty=%s source=%s",
        location_id,
        date,
        demand_qty,
        source,
    )
    # First realisation of the target day — feed the online-ACI state so the
    # serving intervals keep adapting (best-effort; never blocks ingestion).
    _maybe_update_conformal_state(
        db,
        workspace_id=workspace_id,
        location_id=location_id,
        target_date=date,
        actual=float(demand_qty),
    )
    _publish_demand_change(
        workspace_id=workspace_id,
        location_id=location_id,
        date=date,
        demand_qty=demand_qty,
        created=True,
        previous_qty=None,
        source=source,
    )
    return CommitResult(observation=obs, created=True, previous_qty=None)


def _run_without_blocking_the_caller(coro: Coroutine[Any, Any, Any]) -> None:
    """Run ``coro`` to completion whether or not this thread has a loop.

    Every ingest route is a sync ``def``, so FastAPI runs it in a threadpool
    with no loop of its own and ``asyncio.run`` is correct. ``commit_demand`` is
    a public service though, so an async caller is possible later: ``asyncio.run``
    inside a running loop raises, and the failure would surface as a broken
    ingestion. In that case the coroutine gets a thread that owns its loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()


def _publish_demand_change(
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    date: datetime.date,
    demand_qty: int,
    created: bool,
    previous_qty: int | None,
    source: str | None,
) -> None:
    """Fan a committed demand row out to connected devices.

    Lives here, at the only place ``demand_observations`` are written, so every
    adapter — manual, file, image, connector — reaches connected tills without
    each one having to remember to publish.

    The payload carries exactly what a device needs to apply the *same*
    reconciliation rule the server just applied: last write wins on
    ``(location_id, date)``. ``created``/``previous_qty`` are advisory — a
    device that has never seen the row treats it as a create, and one that has
    treats it as an overwrite; neither needs the distinction to stay correct.

    Best-effort, like the conformal update below: a realtime fan-out failure
    must never fail an ingestion that has already committed to the database.
    The durable record is the row itself, not this message.
    """
    from app.services import realtime

    try:
        payload: dict[str, Any] = {
            "location_id": str(location_id),
            "date": date.isoformat(),
            "demand_qty": demand_qty,
            "created": created,
            "previous_qty": previous_qty,
            "source": source,
            "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        _run_without_blocking_the_caller(
            realtime.publish(
                workspace_id,
                realtime.TOPIC_DEMAND,
                payload,
                origin=realtime.PublishOrigin.SERVER,
            )
        )
    except Exception:
        logger.warning(
            "demand realtime publish failed location=%s date=%s",
            location_id,
            date,
            exc_info=True,
        )


def _maybe_update_conformal_state(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    target_date: datetime.date,
    actual: float,
) -> None:
    """Best-effort online-ACI update when a realised observation first lands.

    Fires on CREATE only — reconciliations re-fire the same day's outcome and
    would double-count it in the adaptation state. Never raises: ingestion
    must not break because the adaptation side-path failed (no persisted
    forecast for that day, no serving bundle, ...).
    """
    try:
        from app.services.forecasting.conformal import record_conformal_outcome

        outcome = record_conformal_outcome(
            db,
            workspace_id=workspace_id,
            location_id=location_id,
            target_date=target_date,
            actual=actual,
        )
        if outcome is not None:
            logger.info(
                "conformal state updated location=%s date=%s missed=%s alpha %.4f -> %.4f",
                location_id,
                target_date,
                outcome["missed"],
                outcome["alpha_before"],
                outcome["alpha_after"],
            )
    except Exception:
        logger.warning(
            "conformal state update failed location=%s date=%s",
            location_id,
            target_date,
            exc_info=True,
        )
