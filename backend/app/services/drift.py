"""Distribution drift monitoring (ARCHITECTURE.md §6, model/MODEL_LIFECYCLE.md).

Compares the demand distribution the production model was trained on against
recent live demand, using the Population Stability Index (PSI) — the monitor
``model/EVALUATION.md`` names as the retrain trigger::

    PSI < 0.10   -> stable
    0.10 – 0.20  -> watch
    PSI > 0.20   -> drifting  (recalibrate; retrain if persistent)

The baseline is the production model's recorded training window
(``model_versions.train_start`` / ``train_end``). Without a dated production
model, or with too few observations to be meaningful, the result is
``unknown`` — a real "not enough evidence" state rather than an invented score.
"""

from __future__ import annotations

import datetime
import math
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.forecast import ModelVersion
from app.models.location import DemandObservation

# Minimum sample sizes before a PSI is meaningful rather than noise.
MIN_BASELINE_OBS = 30
MIN_LIVE_OBS = 10
DEFAULT_WINDOW_DAYS = 14


def population_stability_index(
    expected: list[float], actual: list[float], bins: int = 10
) -> float | None:
    """PSI between a baseline and a live sample using equal-width bins.

    Args:
        expected: Baseline (training-window) values.
        actual: Live values to compare against the baseline.
        bins: Number of equal-width bins spanning the observed range.

    Returns:
        The PSI score, or ``None`` when either sample is empty.
    """
    if not expected or not actual:
        return None
    lo = min(min(expected), min(actual))
    hi = max(max(expected), max(actual))
    if hi <= lo:
        return 0.0
    width = (hi - lo) / bins
    eps = 1e-6
    n_e, n_a = len(expected), len(actual)
    psi = 0.0
    for b in range(bins):
        low = lo + b * width
        high = lo + (b + 1) * width
        last = b == bins - 1
        e = (
            sum(1 for v in expected if low <= v <= high)
            if last
            else sum(1 for v in expected if low <= v < high)
        )
        a = (
            sum(1 for v in actual if low <= v <= high)
            if last
            else sum(1 for v in actual if low <= v < high)
        )
        e_pct = max(e / n_e, eps)
        a_pct = max(a / n_a, eps)
        psi += (a_pct - e_pct) * math.log(a_pct / e_pct)
    return psi


def _status_for(psi: float) -> str:
    if psi < 0.10:
        return "stable"
    if psi < 0.20:
        return "watch"
    return "drifting"


def compute_drift(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict[str, Any]:
    """Compute live-vs-training drift signals for a workspace.

    Args:
        db: Database session.
        workspace_id: Workspace scope.
        window_days: Length of the "live" comparison window ending today.

    Returns:
        ``{"signals": [...], "overall": str, "baseline": {...} | None,
        "evaluated_at": iso, "reason": str | None}``.
    """
    now = datetime.datetime.now(datetime.UTC)
    result: dict[str, Any] = {
        "signals": [],
        "overall": "unknown",
        "baseline": None,
        "reason": None,
        "evaluated_at": now.isoformat(),
    }

    # --- Resolve the production model that defines the baseline window ---
    prod: ModelVersion | None = None
    try:
        rows = [
            r
            for r in db.scalars(
                select(ModelVersion)
                .where(ModelVersion.workspace_id == workspace_id)
                .order_by(
                    ModelVersion.promoted_at.desc().nulls_last(),
                    ModelVersion.trained_at.desc().nulls_last(),
                )
            ).all()
            if isinstance(r, ModelVersion)
        ]
        prod = next((r for r in rows if r.status == "production"), None)
    except Exception:
        prod = None

    if prod is None:
        result["reason"] = "No production model. Nothing to compare live data against."
        return result
    if prod.train_start is None or prod.train_end is None:
        result["reason"] = "Production model has no recorded training window."
        return result

    # --- Load both populations ---
    try:
        baseline_rows = [
            r
            for r in db.scalars(
                select(DemandObservation).where(
                    DemandObservation.workspace_id == workspace_id,
                    DemandObservation.date >= prod.train_start,
                    DemandObservation.date <= prod.train_end,
                )
            ).all()
            if isinstance(r, DemandObservation)
        ]
        live_rows = [
            r
            for r in db.scalars(
                select(DemandObservation).where(
                    DemandObservation.workspace_id == workspace_id,
                    DemandObservation.date > prod.train_end,
                    DemandObservation.date >= (now.date() - datetime.timedelta(days=window_days)),
                )
            ).all()
            if isinstance(r, DemandObservation)
        ]
    except Exception:
        result["reason"] = "Could not read demand observations for the drift comparison."
        return result

    baseline = [float(r.demand_qty) for r in baseline_rows]
    live = [float(r.demand_qty) for r in live_rows]

    result["baseline"] = {
        "model_version_id": str(prod.id),
        "algorithm": prod.algorithm,
        "version": prod.version,
        "train_start": prod.train_start.isoformat(),
        "train_end": prod.train_end.isoformat(),
        "n": len(baseline),
        "window_days": window_days,
        "n_live": len(live),
    }

    if len(baseline) < MIN_BASELINE_OBS or len(live) < MIN_LIVE_OBS:
        result["reason"] = (
            f"Not enough observations yet (baseline {len(baseline)}, live {len(live)}) "
            "for a meaningful drift score."
        )
        return result

    psi = population_stability_index(baseline, live)
    if psi is None:
        result["reason"] = "Drift score could not be computed."
        return result

    mean_e = sum(baseline) / len(baseline)
    mean_a = sum(live) / len(live)
    delta = ((mean_a - mean_e) / mean_e * 100.0) if mean_e else None
    status = _status_for(psi)

    detail = (
        f"PSI {psi:.3f} over the last {window_days} days "
        f"(n={len(live)}) vs the training window (n={len(baseline)})."
    )
    result["signals"] = [
        {
            "id": "demand_qty",
            "feature": "demand_qty",
            "label": "Daily demand",
            "detail": detail,
            "description": detail,
            "drift_score": round(psi, 4),
            "p_value": None,
            "delta": round(delta, 1) if delta is not None else None,
            "status": status,
            "severity": status,
        }
    ]
    result["overall"] = status
    return result
