"""Waste observations — independent URBAN-WASTE evidence.

Deliberately has **no foreign key** to locations or demand_observations.
See ``database/schema.sql:268`` — evidence boundary, Figure 4.
"""

from __future__ import annotations

import datetime

from sqlalchemy import BigInteger, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class WasteObservation(Base):
    """Single URBAN-WASTE waste measurement.

    Attributes:
        id: Auto-increment primary key.
        establishment_code: Establishment identifier (34 distinct codes).
        country: Country of the establishment.
        observed_on: Observation date (DD/MM/YYYY source → DATE).
        waste_kg: Measured waste in kilograms (nullable, 161 missing of 3321).
        ordered_portions: Ordered portions (sparse, 9 non-null of 3321).
        source_dataset: Dataset identifier, default ``urban-waste-ftd``.
        licence: Licence, default ``CC BY 4.0``.
    """

    __tablename__ = "waste_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    establishment_code: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_on: Mapped[datetime.date | None] = mapped_column(nullable=True)
    waste_kg: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    ordered_portions: Mapped[int | None] = mapped_column(nullable=True)
    source_dataset: Mapped[str] = mapped_column(Text, nullable=False, default="urban-waste-ftd")
    licence: Mapped[str] = mapped_column(Text, nullable=False, default="CC BY 4.0")
