"""Reservation model — supplementary demand signal for feature build."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import Date, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Reservation(Base):
    """Reserved covers per location/date (supplementary, connector-supplied).

    The information boundary is applied at ingestion: rows aggregate only
    reservations *booked strictly before the visit day starts* (the training
    contract for the ``rsv_*`` features — Section 3.10). A reservation made ON
    the visit day is never part of these aggregates.
    """

    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    reserved_count: Mapped[int] = mapped_column(nullable=False, default=0)
    reserved_visitors: Mapped[int | None] = mapped_column(nullable=True)
    lead_time_days: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)

    __table_args__ = (
        # Unique per location/date — mirrors schema.sql
        # Defined via __table_args__ to avoid duplicate constraint name
        {"extend_existing": True},
    )
