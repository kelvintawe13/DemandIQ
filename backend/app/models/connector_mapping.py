"""Connector mapping ORM model — per schema.sql connector_mappings table."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ConnectorMapping(Base):
    """Field mapping from a connector source_field to canonical schema."""

    __tablename__ = "connector_mappings"
    __table_args__ = (
        CheckConstraint(
            "canonical_field IN ('location_id','date','demand_qty','notes')",
            name="ck_connector_mappings_canonical",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connector_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("connectors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_field: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_field: Mapped[str] = mapped_column(Text, nullable=False)
    transform: Mapped[str | None] = mapped_column(Text, nullable=True)
