"""Realised forecast accuracy — the served forecast against demand that landed.

The registry records what a training run measured on its *held-out window*
(``model_versions.mae`` / ``bundle_evaluation``). This module answers the other
question an operator asks every morning: how is the forecast we actually served
doing against the demand that has since been recorded?

For every ``(location, day)`` where a forecast was published **before the day
began** and an actual observation now exists, the signed error is
``point - actual`` — positive means the forecast ran high.
Those pairs are aggregated into MAE, WAPE (``sum |error| / sum actual``, in
percent), bias (the mean *signed* error, so over- and under-forecasting cannot
cancel out invisibly) and interval coverage — for a trailing window and for the
window immediately before it. The pair is the point: one
number cannot show *drift*, and the daily question is "is this getting worse?".

Degradation is reported as severities, never as a pass/fail invented threshold
on its own:

* live MAE against the same model's held-out MAE (the honest reference),
* WAPE this window against the previous one,
* interval coverage against its own target,
* days that were served by the rolling-mean fallback instead of the model.

Points served by the fallback are **excluded** from the error statistics — they
are not model output (see :func:`app.services.forecasting.loader.predict_point`)
— but counted in ``fallback_days``, so a stretch of fallback serving can never
masquerade as a degraded model.

Nothing is invented: with no overlapping (forecast, actual) pair the result is
``status="unknown"`` and ``reason`` names what is missing.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.forecast import Forecast, ModelVersion
from app.models.location import DemandObservation, Location

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 28

# Below this many scored store-days the figures are indicative, not evidence.
MIN_SCORED_DAYS = 7

# A location whose absolute error is a quarter of its demand is worth a look;
# above 40% the forecast is not steering anything a manager would trust.
WAPE_WATCH_PCT = 25.0
WAPE_DEGRADED_PCT = 40.0

# Live error relative to the same model's held-out error. 1.25 is roughly the
# noise band a 28-day window can produce on its own; 1.5 is a real shift.
MAE_RATIO_WATCH = 1.25
MAE_RATIO_DEGRADED = 1.5

# Window-over-window move in WAPE that counts as drift rather than noise.
DRIFT_WATCH_REL = 0.10
DRIFT_DEGRADED_REL = 0.25

# Interval coverage falling this far below its target means the ranges no
# longer hold — the uncertainty-aware policy reasons on them.
COVERAGE_WATCH_POINTS = 5.0
COVERAGE_DEGRADED_POINTS = 10.0

# Bias, as a share of a typical day, that is worth naming.
BIAS_SHARE_WATCH = 0.10

DEFAULT_COVERAGE_TARGET = 90.0

_SEVERITY_RANK = {"healthy": 0, "watch": 1, "degraded": 2}


def _metrics(
    pairs: list[tuple[float, float]],
    covered: int,
    fallback_days: int,
) -> dict[str, Any] | None:
    """MAE/WAPE/bias/coverage for ``(predicted, actual)`` pairs, or None.

    ``wape_pct`` is ``None`` rather than 0 when the actuals sum to zero — a
    location with no demand recorded says nothing about percentage error, and a
    zero there would read as a perfect score.
    """
    if not pairs:
        return None
    n = len(pairs)
    signed = [point - actual for point, actual in pairs]
    abs_total = sum(abs(e) for e in signed)
    actual_total = sum(actual for _, actual in pairs)
    return {
        "n": n,
        "mae": round(abs_total / n, 3),
        "wape_pct": round(abs_total / actual_total * 100.0, 2) if actual_total else None,
        "bias": round(sum(signed) / n, 3),
        "coverage_pct": round(covered / n * 100.0, 1),
        "mean_actual": round(actual_total / n, 2),
        "fallback_days": fallback_days,
    }


def _band(value: float, watch: float, degraded: float) -> str:
    if value >= degraded:
        return "degraded"
    if value >= watch:
        return "watch"
    return "healthy"


def _signal(code: str, label: str, severity: str, detail: str) -> dict[str, Any]:
    return {"code": code, "label": label, "severity": severity, "detail": detail}


def _location_status(metrics: dict[str, Any], test_mae: float | None) -> str:
    """A location's own severity: its WAPE band, escalated by live-vs-held-out."""
    wape = metrics["wape_pct"]
    rank = _SEVERITY_RANK[_band(wape, WAPE_WATCH_PCT, WAPE_DEGRADED_PCT)] if wape else 0
    if test_mae:
        rank = max(
            rank,
            _SEVERITY_RANK[_band(metrics["mae"] / test_mae, MAE_RATIO_WATCH, MAE_RATIO_DEGRADED)],
        )
    return next(label for label, value in _SEVERITY_RANK.items() if value == rank)


def _overall_signals(
    overall: dict[str, Any],
    prior: dict[str, Any] | None,
    test_mae: float | None,
    target_coverage: float,
    fallback_days: int,
) -> list[dict[str, Any]]:
    """What is worth telling an operator about the window as a whole."""
    out: list[dict[str, Any]] = []
    n = overall["n"]
    if n < MIN_SCORED_DAYS:
        out.append(
            _signal(
                "low_sample",
                "Too few days to judge",
                "watch",
                f"Only {n} store-day(s) of published forecast can be checked against recorded "
                "demand so far; read this as indicative, not as the model's error.",
            )
        )

    if test_mae:
        ratio = overall["mae"] / test_mae
        if ratio >= MAE_RATIO_WATCH:
            out.append(
                _signal(
                    "live_vs_test",
                    "Worse than the held-out window",
                    _band(ratio, MAE_RATIO_WATCH, MAE_RATIO_DEGRADED),
                    f"Real demand is off by {overall['mae']:.2f} a day on average, against "
                    f"{test_mae:.2f} measured when the model was trained "
                    f"({(ratio - 1) * 100:.0f}% higher).",
                )
            )

    if prior is not None and prior["wape_pct"] and overall["wape_pct"] is not None:
        rel = (overall["wape_pct"] - prior["wape_pct"]) / prior["wape_pct"]
        if rel >= DRIFT_WATCH_REL:
            out.append(
                _signal(
                    "window_drift",
                    "Accuracy is worsening",
                    _band(rel, DRIFT_WATCH_REL, DRIFT_DEGRADED_REL),
                    f"Error is {overall['wape_pct']:.1f}% of demand this window against "
                    f"{prior['wape_pct']:.1f}% in the one before it ({rel * 100:+.0f}%).",
                )
            )

    shortfall = target_coverage - overall["coverage_pct"]
    if shortfall >= COVERAGE_WATCH_POINTS:
        out.append(
            _signal(
                "coverage",
                "Ranges are too narrow",
                _band(shortfall, COVERAGE_WATCH_POINTS, COVERAGE_DEGRADED_POINTS),
                f"Real demand fell inside the published range {overall['coverage_pct']:.1f}% of the "
                f"time against a {target_coverage:.0f}% target.",
            )
        )

    if overall["mean_actual"]:
        share = abs(overall["bias"]) / overall["mean_actual"]
        if share >= BIAS_SHARE_WATCH:
            # ``bias`` is ``point - actual``: positive means the forecast ran high.
            over = overall["bias"] > 0
            direction = "over" if over else "under"
            out.append(
                _signal(
                    "bias",
                    f"Forecasts run {direction}",
                    "watch",
                    f"On average the forecast was {abs(overall['bias']):.1f} visitors "
                    f"{'above' if over else 'below'} a typical day of "
                    f"{overall['mean_actual']:.0f}, {direction}-forecasting "
                    f"{share * 100:.0f}% of a day's demand.",
                )
            )

    if fallback_days:
        out.append(
            _signal(
                "fallback",
                "Some days served by an average",
                "watch",
                f"{fallback_days} scored day(s) were answered with a rolling-mean average instead "
                "of the model (too little history for the features). Those days are left out of "
                "the error figures above.",
            )
        )
    return out


def _production_model(db: Session, workspace_id: uuid.UUID) -> ModelVersion | None:
    """The registered production row, if any — the error's reference point."""
    try:
        rows = [
            r
            for r in db.scalars(
                select(ModelVersion)
                .where(ModelVersion.workspace_id == workspace_id)
                .order_by(ModelVersion.promoted_at.desc().nulls_last())
            ).all()
            if isinstance(r, ModelVersion)
        ]
    except Exception:
        return None
    return next((r for r in rows if r.status == "production"), None)


def _location_names(
    db: Session, workspace_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Names for the locations that appear, best-effort — never a hard failure."""
    if not ids:
        return {}
    try:
        rows = [
            r
            for r in db.scalars(
                select(Location).where(Location.workspace_id == workspace_id, Location.id.in_(ids))
            ).all()
            if isinstance(r, Location)
        ]
    except Exception:
        return {}
    return {r.id: r.name for r in rows}


def compute_accuracy(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Live forecast accuracy for a workspace, against demand already recorded.

    Args:
        db: Database session.
        workspace_id: Workspace scope.
        window_days: Length of the comparison window, and of the window before it.
        today: Override for "today" (tests); defaults to the current UTC date.

    Returns:
        The payload described in the module docstring: ``overall``/``prior``
        metrics, the production model's held-out figures as ``reference``,
        ``signals``, and a per-location table sorted worst-first.
    """
    now = datetime.datetime.now(datetime.UTC)
    today = today or now.date()
    recent_start = today - datetime.timedelta(days=window_days)
    prior_start = recent_start - datetime.timedelta(days=window_days)

    result: dict[str, Any] = {
        "evaluated_at": now.isoformat(),
        "window_days": window_days,
        "status": "unknown",
        "reason": None,
        "target_coverage": DEFAULT_COVERAGE_TARGET,
        "reference": None,
        "overall": None,
        "prior": None,
        "signals": [],
        "locations": [],
    }

    prod = _production_model(db, workspace_id)
    test_mae: float | None = None
    if prod is not None:
        test_mae = float(prod.mae) if prod.mae is not None else None
        result["reference"] = {
            "model_version_id": str(prod.id),
            "algorithm": prod.algorithm,
            "version": prod.version,
            "test_mae": test_mae,
            "test_end": prod.test_end.isoformat() if prod.test_end else None,
        }

    # --- Published forecasts for both windows -------------------------------
    try:
        forecasts = [
            f
            for f in db.scalars(
                select(Forecast).where(
                    Forecast.workspace_id == workspace_id,
                    Forecast.target_date >= prior_start,
                    Forecast.target_date <= today,
                )
            ).all()
            if isinstance(f, Forecast)
        ]
    except Exception:
        result["reason"] = "Could not read published forecasts."
        return result

    # One forecast per (location, day): the last one published *before* the day
    # it describes. A row written on or after its own target date is a record of
    # what happened, not a prediction, and scoring it would flatter the model.
    latest: dict[tuple[uuid.UUID, datetime.date], Forecast] = {}
    for f in forecasts:
        if f.as_of_timestamp is None or f.as_of_timestamp.date() >= f.target_date:
            continue
        key = (f.location_id, f.target_date)
        current = latest.get(key)
        if current is None or f.as_of_timestamp > current.as_of_timestamp:
            latest[key] = f

    # --- What actually landed ----------------------------------------------
    try:
        observations = [
            o
            for o in db.scalars(
                select(DemandObservation).where(
                    DemandObservation.workspace_id == workspace_id,
                    DemandObservation.date >= prior_start,
                    DemandObservation.date <= today,
                )
            ).all()
            if isinstance(o, DemandObservation)
        ]
    except Exception:
        result["reason"] = "Could not read recorded demand."
        return result
    actual = {(o.location_id, o.date): float(o.demand_qty) for o in observations}

    # --- Pair them up -------------------------------------------------------
    recent: list[tuple[float, float]] = []
    prior: list[tuple[float, float]] = []
    covered_recent = covered_prior = 0
    fallback_recent = 0
    per_location: dict[uuid.UUID, dict[str, Any]] = {}

    for (loc_id, day), f in sorted(latest.items(), key=lambda item: item[0][1]):
        landed = actual.get((loc_id, day))
        if landed is None:
            continue
        entry = per_location.setdefault(loc_id, {"pairs": [], "covered": 0, "fallback": 0})
        is_recent = day >= recent_start
        if f.fallback:
            # Not model output — counted, never scored.
            entry["fallback"] += 1
            if is_recent:
                fallback_recent += 1
            continue
        point = float(f.point_forecast)
        inside = float(f.lower_bound) <= landed <= float(f.upper_bound)
        if is_recent:
            recent.append((point, landed))
            entry["pairs"].append((point, landed))
            if inside:
                covered_recent += 1
                entry["covered"] += 1
            result["target_coverage"] = float(f.coverage_target)
        else:
            prior.append((point, landed))
            if inside:
                covered_prior += 1

    result["overall"] = _metrics(recent, covered_recent, fallback_recent)
    result["prior"] = _metrics(prior, covered_prior, 0)

    if result["overall"] is None:
        result["reason"] = (
            "No published forecast could be checked against recorded demand in this "
            "window yet. Accuracy appears once a forecast's day has passed and its "
            "demand has been recorded."
        )
        # A location that only ever got fallback answers is still worth naming.
        fallback_only = sum(1 for e in per_location.values() if e["fallback"] and not e["pairs"])
        if fallback_only:
            result["signals"] = _overall_signals(
                {
                    "n": 0,
                    "mae": 0.0,
                    "wape_pct": None,
                    "bias": 0.0,
                    "coverage_pct": 0.0,
                    "mean_actual": 0.0,
                    "fallback_days": fallback_recent,
                },
                None,
                test_mae,
                result["target_coverage"],
                fallback_recent,
            )
        return result

    signal_list = _overall_signals(
        result["overall"], result["prior"], test_mae, result["target_coverage"], fallback_recent
    )
    result["signals"] = signal_list
    result["status"] = (
        max((s["severity"] for s in signal_list), key=lambda s: _SEVERITY_RANK[s])
        if signal_list
        else "healthy"
    )

    names = _location_names(db, workspace_id, list(per_location))
    rows: list[dict[str, Any]] = []
    for loc_id, entry in per_location.items():
        metrics = _metrics(entry["pairs"], entry["covered"], entry["fallback"])
        if metrics is None:
            continue
        rows.append(
            {
                "location_id": str(loc_id),
                "name": names.get(loc_id),
                **metrics,
                "status": _location_status(metrics, test_mae),
            }
        )
    # Worst error first: the page exists to answer "where is it going wrong?".
    rows.sort(key=lambda r: (r["wape_pct"] is None, -(r["wape_pct"] or 0.0)))
    result["locations"] = rows
    return result
