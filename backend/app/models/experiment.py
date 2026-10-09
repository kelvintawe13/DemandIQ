"""Experiment and fold ORM models."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, MutableJSONB


class Experiment(Base):
    """Backtest experiment — mirrors ``experiments`` table."""

    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(MutableJSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    mae: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)  # type: ignore[assignment]
    baseline_mae: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)  # type: ignore[assignment]
    coverage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # type: ignore[assignment]
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class ExperimentFold(Base):
    """Per-fold result for an experiment."""

    __tablename__ = "experiment_folds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    fold_index: Mapped[int] = mapped_column(Integer, nullable=False)
    train_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    train_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    test_start: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    test_end: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    mae: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)  # type: ignore[assignment]
    coverage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # type: ignore[assignment]
