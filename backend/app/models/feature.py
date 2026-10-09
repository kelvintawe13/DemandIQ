"""Feature definition ORM model."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FeatureDefinition(Base):
    """Feature store registry entry — mirrors ``feature_definitions`` table."""

    __tablename__ = "feature_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    feature_group: Mapped[str] = mapped_column(Text, nullable=False)
    dtype: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    coverage_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)  # type: ignore[assignment]
    leakage_safe: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    in_production: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
