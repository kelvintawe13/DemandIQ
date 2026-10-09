"""Persist forecasts to ``forecasts`` with as_of and feature-set versioning."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy.orm import Session

from app.models.forecast import Forecast


def persist_forecast(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    location_id: uuid.UUID,
    target_date: datetime.date,
    point: float,
    lower: float,
    upper: float,
    coverage_target: float,
    model_version_id: uuid.UUID,
    feature_set_version: str,
    as_of_timestamp: datetime.datetime,
    fallback: bool = False,
) -> Forecast:
    """Persist a forecast row.

    The row records ``model_version_id``, ``feature_set_version`` and
    ``as_of_timestamp`` so that any number on screen can be reproduced exactly
    (ARCHITECTURE.md §3). ``fallback`` records whether the point is a historical
    average rather than a model output, so every read path reports the same
    answer instead of recomputing one.

    Args:
        db: Database session.
        workspace_id: Owning workspace.
        location_id: Location being forecast.
        target_date: Day being forecast.
        point: Point forecast (visitor units).
        lower: Lower bound of interval.
        upper: Upper bound of interval.
        coverage_target: Coverage target (e.g. 0.90).
        model_version_id: Model version FK.
        feature_set_version: Feature-set version string.
        as_of_timestamp: Information-boundary timestamp (must be ``< target_date``).
        fallback: True when the point is a historical average, not a model output.

    Returns:
        Persisted :class:`Forecast` (flushed, with id).
    """
    if as_of_timestamp.tzinfo is None:
        as_of_timestamp = as_of_timestamp.replace(tzinfo=datetime.UTC)

    row = Forecast(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        location_id=location_id,
        model_version_id=model_version_id,
        target_date=target_date,
        point_forecast=point,  # type: ignore[assignment]
        lower_bound=lower,  # type: ignore[assignment]
        upper_bound=upper,  # type: ignore[assignment]
        coverage_target=coverage_target,  # type: ignore[assignment]
        feature_set_version=feature_set_version,
        as_of_timestamp=as_of_timestamp,
        fallback=fallback,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(row)
    db.flush()
    return row
