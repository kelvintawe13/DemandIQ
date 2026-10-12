"""One place that turns a (location, date) pair into a persisted forecast.

Three callers used to spell out the same four steps — ``predict_point`` →
``build_interval`` → ``persist_forecast`` → optional ``build_explanation``:
``POST /forecasts``, ``POST /forecasts/batch`` and the nightly
``forecast_publish`` job. Keeping them separate meant the batch endpoint
could drift from the nightly job (different coverage target, a missing SHAP
row, a different ``as_of``) without any test noticing.

Everything that produces a stored forecast now goes through
:func:`generate_forecast`, so a change to how a forecast is made — the interval
rule, the attribution row, the persisted provenance — lands in all three at
once.

The caller owns ``as_of``: it is the information boundary (leakage rule), so it
must be chosen deliberately (request time for on-demand, the job's run
timestamp for the nightly publish) and is persisted on the row for
reproducibility.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.forecast import Forecast
from app.services.explain.shap import build_explanation
from app.services.feature_build import FeatureVector
from app.services.forecasting.conformal import build_interval
from app.services.forecasting.loader import predict_point
from app.services.forecasting.persist import persist_forecast


@dataclass(frozen=True, slots=True)
class GeneratedForecast:
    """A persisted forecast plus the inputs that produced it.

    ``feature_vector`` is returned so callers do not have to rebuild it (the
    router needs it for the SHAP explanation and for the limited-history flag;
    rebuilding it was a second full feature build per request).
    """

    row: Forecast
    fallback: bool
    feature_vector: FeatureVector

    @property
    def id(self) -> uuid.UUID:
        """Id of the persisted ``forecasts`` row."""
        return self.row.id

    @property
    def point(self) -> float:
        """Point forecast."""
        return float(self.row.point_forecast)

    @property
    def lower(self) -> float:
        """Lower interval bound."""
        return float(self.row.lower_bound)

    @property
    def upper(self) -> float:
        """Upper interval bound."""
        return float(self.row.upper_bound)

    @property
    def coverage_target(self) -> float:
        """Coverage the interval was built for."""
        return float(self.row.coverage_target)


def generate_forecast(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    target_date: datetime.date,
    as_of: datetime.datetime,
    horizon_days: int = 1,
    coverage_target: float | None = None,
    explain: bool = False,
) -> GeneratedForecast:
    """Forecast one location/date, persist it, and report what was written.

    Args:
        db: Database session.
        workspace_id: Owning workspace.
        location_id: Location being forecast.
        target_date: Day being forecast (must be after ``as_of``'s date).
        as_of: Information boundary — no data after this instant is used.
        horizon_days: Horizon the model is scored at.
        coverage_target: Interval coverage; ``None`` uses the configured default.
        explain: Also persist the SHAP attribution row for this forecast.

    Returns:
        The :class:`GeneratedForecast` (row flushed, with id).

    Raises:
        Exception: Whatever ``predict_point``/``build_features`` raise — callers
            decide whether a single failure is fatal (on-demand) or skipped
            (batch, nightly publish).
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=datetime.UTC)

    result = predict_point(
        db,
        workspace_id=workspace_id,
        location_id=location_id,
        target_date=target_date,
        as_of=as_of,
        horizon_days=horizon_days,
    )
    # The vector comes back with the point: it is the one the model was served
    # on (full trained feature set + the bundle's holiday calendar when a
    # bundle exists), so the interval and the attribution below describe the
    # same row. Rebuilding it here — with the legacy builder — is what made the
    # point and its explanation describe different feature sets.
    feature_vector = result.feature_vector
    interval = build_interval(
        db,
        model_version_id=result.model_version.id,
        point=result.point,
        feature_vector=feature_vector,
        coverage_target=coverage_target,
    )
    row = persist_forecast(
        db,
        workspace_id=workspace_id,
        location_id=location_id,
        target_date=target_date,
        point=result.point,
        lower=interval.lower,
        upper=interval.upper,
        coverage_target=interval.coverage_target,
        model_version_id=result.model_version.id,
        feature_set_version=result.feature_set_version,
        as_of_timestamp=as_of,
        fallback=result.fallback,
    )
    if explain:
        # Persisted so GET /forecasts/{id}/explanation is reproducible rather
        # than recomputed against whatever the feature store looks like later.
        build_explanation(db, forecast_id=row.id, feature_vector=feature_vector, point=result.point)

    return GeneratedForecast(row=row, fallback=result.fallback, feature_vector=feature_vector)
