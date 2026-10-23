"""Location and related operational models."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, MutableJSONB


class Location(Base):
    """Physical service location (restaurant / outlet)."""

    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    area: Mapped[str | None] = mapped_column(Text, nullable=True)
    cuisine: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_on: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    seating_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    service_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    district: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_days_mask: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    manager_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="locations")  # type: ignore[name-defined]


class MenuGroup(Base):
    """Menu grouping for perishability modelling."""

    __tablename__ = "menu_groups"
    __table_args__ = (
        CheckConstraint(
            "perishability IN ('high','medium','low')", name="ck_menu_groups_perishability"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cover_share: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    perishability: Mapped[str] = mapped_column(Text, nullable=False)


class Connector(Base):
    """External data connector definition."""

    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="available")
    credentials: Mapped[dict | None] = mapped_column(MutableJSONB, nullable=True)
    last_sync_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # What the most recent pull reported, per connector. ``last_sync_at`` says
    # *when* a channel last pulled; these say *what happened*, so the Sources
    # page can show a channel's own outcome instead of only job-level rows.
    # Null until a pull has run; left unchanged by a channel with no endpoint.
    last_pull_pulled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_pull_committed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_pull_skipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_pull_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class IngestUpload(Base):
    """Staged upload for CSV/XLSX/image ingestion."""

    __tablename__ = "ingest_uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="parsing")
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preview: Mapped[dict | None] = mapped_column(MutableJSONB, nullable=True)
    # The original photo, kept so an operator can put the image back next to the
    # rows it produced — the preview holds only the parsed rows, which is
    # useless for checking a misread by eye. Deferred: listing uploads must not
    # drag megabytes per row into the session.
    source_image: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    # Sniffed at upload time from the bytes, never the client's Content-Type.
    source_mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class DemandObservation(Base):
    """Canonical demand fact table."""

    __tablename__ = "demand_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    demand_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("connectors.id"), nullable=True
    )
    upload_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingest_uploads.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    __table_args__ = (  # type: ignore[assignment]
        CheckConstraint("demand_qty >= 0", name="ck_demand_observations_demand_qty"),
    )
