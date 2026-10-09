"""Location access log — NEW table for location access tracking.

This table does not exist in the original ``database/schema.sql``; it is
introduced in phase 1 to satisfy the location access tracking requirement.

It records every read of location data (list / detail / forecast / policy)
with workspace / location / user scoping, IP, user-agent and client type.
Alongside ``audit_log`` it provides a complete trace of who accessed what
and when.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class LocationAccessLog(Base):
    """Access log for location reads."""

    __tablename__ = "location_access_logs"
    __table_args__ = (
        CheckConstraint(
            "client IN ('web','mobile','api')",
            name="ck_location_access_logs_client",
        ),
        CheckConstraint(
            "action IN ('view','list','detail','forecast','policy')",
            name="ck_location_access_logs_action",
        ),
        Index("ix_location_access_logs_workspace_created", "workspace_id", "created_at"),
        Index("ix_location_access_logs_location_created", "location_id", "created_at"),
        Index("ix_location_access_logs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    client: Mapped[str] = mapped_column(Text, nullable=False, default="web")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
