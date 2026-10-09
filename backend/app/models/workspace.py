"""Workspace model."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Workspace(Base):
    """Tenant root. Every other row belongs to a workspace."""

    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    business_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str] = mapped_column(Text, nullable=False, default="RW")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="Africa/Kigali")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    # Relationships (optional, for convenience)
    users: Mapped[list["User"]] = relationship("User", back_populates="workspace", cascade="all, delete-orphan")  # type: ignore[name-defined]
    locations: Mapped[list["Location"]] = relationship("Location", back_populates="workspace", cascade="all, delete-orphan")  # type: ignore[name-defined]
