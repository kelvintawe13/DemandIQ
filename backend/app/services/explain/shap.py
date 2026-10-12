"""Predictive attribution for a forecast (ARCHITECTURE.md §5).

Two paths, in this order:

1. **The served booster's own TreeSHAP.** With a serving bundle loaded, the
   contributions are LightGBM's exact ``pred_contrib`` values for the very
   vector the point was scored from — the same model, the same row, no
   approximation and no second explainer to drift from it.
2. **The labelled stub.** With no bundle there is no model to attribute to, and
   the numbers are a deterministic function of the feature *name*
   (:func:`_stub_impact_for_feature`). They are not evidence about demand and
   are marked as such here, in that function, and in the response
   (``attribution_type``).

Either way the persisted rows satisfy ``Σ drivers + residual == point``, with the
residual holding the model's **base value** — the level it starts from before
this row's features — so an operator can read the attribution as "base + what
this day did". A booster fitted on ``log1p`` demand reports its contributions in
log space; they are restated in demand units by splitting the movement from the
base to the point in proportion to each feature's log-space value, which keeps
their signs and ratios and makes the rows add up to the demand being explained.

SHAP values are computed per forecast and stored alongside it so that the
explanation shown months later is the one that was actually generated — not
re-derived against a newer model. Responses are labelled as predictive
attribution, not causal effect.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.forecast import ATTRIBUTION_BOOSTER, ATTRIBUTION_STUB, ForecastExplanation
from app.services.feature_build import FeatureVector
from app.services.forecasting.bundle import get_bundle

logger = logging.getLogger(__name__)

#: Row name for the part of the point the named drivers do not account for.
#: Shared with the forecast router, which reports it separately from the
#: drivers rather than counting it as one of them.
RESIDUAL_FEATURE = "_residual"


@dataclass(frozen=True, slots=True)
class ShapContribution:
    """Single SHAP contribution (visitor units)."""

    feature: str
    value: str | None
    impact: float
    attribution_type: str = "predictive"


def _stub_impact_for_feature(name: str, raw: object, point: float) -> float:
    """Fallback impact: a deterministic function of the feature *name*.

    Used only when no serving bundle is loaded, i.e. when there is no model to
    attribute the forecast to. It is a placeholder that keeps the endpoint's
    shape and its ordering stable in development and CI; it carries no
    information about demand, and nothing should be read into its magnitude or
    sign. Magnitudes are scaled with the point so that the rows stay plausible
    next to a large forecast.
    """
    if raw is None:
        return 0.0
    h = sum(ord(c) for c in name) % 100
    sign = 1 if h % 2 == 0 else -1
    mag = (h % 20 + 1) * 0.3
    scale = max(1.0, point / 50.0)
    return sign * mag * scale


def _ordered_feature_names(feature_vector: FeatureVector) -> list[str]:
    """Stub-path order: the headline features first, then anything else present.

    Only names actually present in the vector are returned, so the stub never
    invents a driver the forecast did not have.
    """
    headline = [
        "roll_mean_7",
        "roll_mean_28",
        "same_weekday_mean",
        "lag_1",
        "lag_7",
        "volatility_28",
        "dow",
        "is_weekend",
        "month",
        "is_public_holiday",
        "reservation_count",
    ]
    present = feature_vector.features
    return [n for n in headline if n in present] + [n for n in present if n not in headline]


def _in_demand_units(
    model: dict[str, Any], names: list[str], contribs: list[float], bias: float, point: float
) -> tuple[list[tuple[str, float]], float]:
    """Restate the booster's contributions in demand units, exactly additively.

    ``contribs`` and ``bias`` are the booster's own values in its output space.
    For a booster fitted on ``log1p`` demand that space is log space, so the base
    value is ``expm1(bias)`` and the features move the prediction from there to
    ``point``; each feature takes the share of that movement given by its
    log-space contribution. Signs and ratios are the model's — only the scale is
    restated — and ``Σ named + base == point`` holds exactly (up to the 3-decimal
    rounding of the stored rows). A booster fitted on demand directly needs no
    conversion: its contributions are already demand and its bias is the base.
    """
    if not model.get("log_target", False):
        return list(zip(names, contribs, strict=True)), bias

    base = math.expm1(bias)
    spread = math.fsum(contribs)
    if math.isclose(spread, 0.0, abs_tol=1e-12):
        # No feature moved the prediction: the point is the base value.
        return [(name, 0.0) for name in names], base
    movement = point - base
    return (
        [(name, movement * c / spread) for name, c in zip(names, contribs, strict=True)],
        base,
    )


def _tree_shap_from_booster(
    bundle: dict[str, Any], features: dict[str, Any], point: float
) -> tuple[list[tuple[str, float]], float] | None:
    """Exact TreeSHAP from the served booster, restated in demand units.

    Returns ``(named, base)`` — one ``(feature, contribution)`` pair per trained
    column, plus the model's base value in the same units — or ``None`` when the
    bundle cannot answer: serving deps missing, the vector does not satisfy the
    bundle's column contract, or the booster declines ``pred_contrib``. ``None``
    means "there is no model here to attribute to", which is the caller's cue to
    use the stub.
    """
    try:
        import numpy as np
        import pandas as pd
    except ImportError:  # pragma: no cover - depends on extras
        logger.warning("numpy/pandas not installed; explanation falls back to the stub")
        return None

    model = bundle.get("model") or {}
    cols = list(model.get("feature_columns") or [])
    if not cols:
        logger.warning("serving bundle has no feature columns; explanation falls back to the stub")
        return None
    missing = [c for c in cols if c not in features]
    if missing:
        logger.warning(
            "cannot attribute from the bundle; vector is missing %d columns, e.g. %s",
            len(missing),
            missing[:3],
        )
        return None
    try:
        X = pd.DataFrame([{c: features[c] for c in cols}], columns=cols).astype("float64")
        raw = np.asarray(
            model["booster"].predict(
                X, num_iteration=model.get("best_iteration"), pred_contrib=True
            )
        ).ravel()
    except Exception as exc:  # noqa: BLE001 - attribution must degrade, never 500
        logger.warning("bundle attribution failed: %s", exc)
        return None
    # LightGBM returns one contribution per column plus the expected value.
    if raw.size != len(cols) + 1:
        logger.warning(
            "bundle returned %d contributions for %d columns; not using them",
            raw.size,
            len(cols),
        )
        return None

    values = [float(v) for v in raw[:-1]]
    bias = float(raw[-1])
    return _in_demand_units(model, cols, values, bias, point)


def build_explanation(
    db: Session,
    *,
    forecast_id: uuid.UUID,
    feature_vector: FeatureVector,
    point: float,
) -> list[ForecastExplanation]:
    """Compute and persist the attribution for a forecast.

    Uses the served booster's own TreeSHAP contributions when a bundle is
    loaded, and the labelled stub when there is no model to attribute to. See
    the module docstring for what separates the two and why the rows always sum
    to the point they explain.

    Args:
        db: Database session.
        forecast_id: Forecast to explain.
        feature_vector: Feature vector the forecast was scored from.
        point: Point forecast value, in demand units.

    Returns:
        List of persisted :class:`ForecastExplanation` rows (flushed).
    """
    # Remove any prior explanations for idempotency (batch re-run).
    prior = list(
        db.scalars(
            select(ForecastExplanation).where(ForecastExplanation.forecast_id == forecast_id)
        ).all()
    )
    for row in prior:
        db.delete(row)
    if prior:
        db.flush()

    bundle = get_bundle()
    from_booster = (
        _tree_shap_from_booster(bundle, feature_vector.features, point)
        if bundle is not None
        else None
    )
    # Stored on every row: the two sources are indistinguishable from the
    # numbers, and a deployment with no bundle would otherwise leave placeholder
    # attributions looking exactly like model ones.
    source = ATTRIBUTION_BOOSTER if from_booster is not None else ATTRIBUTION_STUB

    impacts: list[tuple[str, object, float]] = []
    if from_booster is not None:
        named, _base = from_booster
        # Every column the model was served, in the model's own order.
        impacts = [(name, feature_vector.features.get(name), value) for name, value in named]
    else:
        impacts = [
            (
                name,
                feature_vector.features[name],
                _stub_impact_for_feature(name, feature_vector.features[name], point),
            )
            for name in _ordered_feature_names(feature_vector)
        ]

    contributions: list[ForecastExplanation] = []
    named_total = 0.0
    for name, raw, impact in impacts:
        rounded = float(round(impact, 3))
        named_total += rounded
        row = ForecastExplanation(
            forecast_id=forecast_id,
            feature_name=name,
            feature_value=None if raw is None else str(raw),
            shap_impact=rounded,  # type: ignore[assignment]
            attribution_source=source,
        )
        db.add(row)
        contributions.append(row)

    # The model's base value: the level it starts from before this row's
    # features. On the booster path it is the booster's own expectation (in
    # demand units); on the stub path it is whatever the placeholder drivers did
    # not account for. Either way the explanation adds up to the point.
    residual_row = ForecastExplanation(
        forecast_id=forecast_id,
        feature_name=RESIDUAL_FEATURE,
        feature_value=None,
        shap_impact=float(round(point - named_total, 3)),  # type: ignore[assignment]
        attribution_source=source,
    )
    db.add(residual_row)
    contributions.append(residual_row)
    db.flush()
    return contributions


def attribution_source_of(rows: list[ForecastExplanation]) -> str | None:
    """Which path wrote ``rows``, for a reader that must not have to guess.

    ``None`` when there is nothing to report; ``"mixed"`` when a set holds rows
    from both paths — reachable after a partial back-fill of rows written before
    the column existed — because that is worth surfacing rather than picking one
    source and presenting the set as uniform.
    """
    # Only real values count: a row object that never carried the column (a
    # double handed a bare mock, say) must read as "unrecorded" rather than
    # putting a repr in the response.
    sources = {
        s for s in (getattr(row, "attribution_source", None) for row in rows) if isinstance(s, str)
    }
    if not sources:
        return None
    if len(sources) == 1:
        return next(iter(sources))
    return "mixed"


def get_explanation(db: Session, forecast_id: uuid.UUID) -> list[ForecastExplanation]:
    """Load persisted explanation rows for a forecast.

    Args:
        db: Database session.
        forecast_id: Forecast id.

    Returns:
        List of :class:`ForecastExplanation` ordered by impact magnitude desc.
    """
    rows = list(
        db.scalars(
            select(ForecastExplanation)
            .where(ForecastExplanation.forecast_id == forecast_id)
            .order_by(ForecastExplanation.shap_impact.desc())
        ).all()
    )
    return rows
