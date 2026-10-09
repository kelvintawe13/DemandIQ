"""Policy and simulation ORM models."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PreparationPolicy(Base):
    """Configurable preparation policy."""

    __tablename__ = "preparation_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    quantile: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)  # type: ignore[assignment]
    lookback_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # risk_aware only: the cost of one unmet customer relative to one leftover
    # portion. A parameter of the policy, not a display value, which is why it
    # has its own column rather than sharing `quantile`.
    risk_weight: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)  # type: ignore[assignment]
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PolicySimulation(Base):
    """Result of a policy simulation."""

    __tablename__ = "policy_simulations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("preparation_policies.id"), nullable=False
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id"), nullable=True
    )
    period_start: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    period_end: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    prepared_total: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # type: ignore[assignment]
    surplus_sr: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # type: ignore[assignment]
    surplus_ps: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # type: ignore[assignment]
    unmet_ud: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)  # type: ignore[assignment]
    surplus_days: Mapped[int] = mapped_column(Integer, nullable=False)
    unmet_days: Mapped[int] = mapped_column(Integer, nullable=False)
    simulated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
