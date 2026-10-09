"""Forecast, model version, calibration and explanation ORM models."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, MutableJSONB


class ModelVersion(Base):
    """Registered model artifact version."""

    __tablename__ = "model_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    mae: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)  # type: ignore[assignment]
    rmse: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)  # type: ignore[assignment]
    rmsle: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)  # type: ignore[assignment]
    hyperparameters: Mapped[dict | None] = mapped_column(MutableJSONB, nullable=True)
    feature_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    train_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    train_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    test_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    test_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    artifact_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    trained_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    promoted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Calibration(Base):
    """Adaptive conformal calibration record."""

    __tablename__ = "calibrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(Text, nullable=False, default="adaptive_conformal")
    target_coverage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=90.0)  # type: ignore[assignment]
    empirical_coverage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # type: ignore[assignment]
    # A width in demand units, so it scales with the demand it was built for.
    mean_interval_width: Mapped[float | None] = mapped_column(Numeric(14, 3), nullable=True)  # type: ignore[assignment]
    # Live online-ACI state (serving-calibrated alphas + trailing residual
    # windows per stratum, plus the global fallback). Seeded from the serving
    # bundle on first use; updated as realised demand arrives so the intervals
    # keep adapting (Gibbs & Candès, mirrored from the training loop).
    aci_state: Mapped[dict | None] = mapped_column(MutableJSONB, nullable=True)
    evaluated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class Forecast(Base):
    """Persisted forecast with interval."""

    __tablename__ = "forecasts"
    __table_args__ = (Index("forecasts_lookup", "location_id", "target_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_versions.id"), nullable=False
    )
    target_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    # Sized to the range demand can take (``demand_qty`` is an ``integer``), not
    # to a guess about it: ``numeric(14, 2)`` holds the whole integer range with
    # room for the interval margin. See migration 015.
    point_forecast: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)  # type: ignore[assignment]
    lower_bound: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)  # type: ignore[assignment]
    upper_bound: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)  # type: ignore[assignment]
    coverage_target: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=90.0)  # type: ignore[assignment]
    feature_set_version: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # True when the served point is a historical average rather than a model
    # output (fewer than 7 days of history in the lookback window). Stored
    # rather than recomputed: see migration 012.
    fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class ForecastExplanation(Base):
    """SHAP explanation persisted alongside a forecast."""

    __tablename__ = "forecast_explanations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    forecast_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("forecasts.id", ondelete="CASCADE"), nullable=False
    )
    feature_name: Mapped[str] = mapped_column(Text, nullable=False)
    feature_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Also in demand units: an attribution with a magnitude that tracks the
    # forecast, so it needs the forecast's range (migration 015).
    shap_impact: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)  # type: ignore[assignment]
