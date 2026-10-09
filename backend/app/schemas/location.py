"""Location schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field


class LocationCreate(BaseModel):
    """Create location — admin only."""

    name: str = Field(min_length=1, max_length=200)
    area: str | None = Field(default=None, max_length=200)
    cuisine: str | None = Field(default=None, max_length=200)
    connector_id: uuid.UUID | None = Field(default=None)
    opened_on: date | None = Field(default=None)
    seating_capacity: int | None = Field(default=None, ge=1, le=5000)
    service_type: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    open_days_mask: str | None = Field(
        default=None, max_length=20, description="e.g. 1111110 for Mon-Sat"
    )
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    manager_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class LocationUpdate(BaseModel):
    """Patch location — admin only."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    area: str | None = Field(default=None, max_length=200)
    cuisine: str | None = Field(default=None, max_length=200)
    opened_on: date | None = Field(default=None)
    seating_capacity: int | None = Field(default=None, ge=1, le=5000)
    service_type: str | None = Field(default=None, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    open_days_mask: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=50)
    manager_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class LocationResponse(BaseModel):
    """Location read model."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    area: str | None
    cuisine: str | None
    opened_on: date | None
    seating_capacity: int | None = None
    service_type: str | None = None
    district: str | None = None
    open_days_mask: str | None = None
    address: str | None = None
    phone: str | None = None
    manager_name: str | None = None
    notes: str | None = None
    archived_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LocationListResponse(LocationResponse):
    """List item with computed display fields for the UI.

    Extends the full read model rather than narrowing it: the registry's
    edit and detail panels are driven by the row it was opened from, so a
    slimmer payload silently blanked their fields.
    """

    # Derived from ``demand_observations`` — the days of history a location
    # actually has, the days missing inside that span, and whether there is
    # enough history to forecast it.
    history_days: int | None = Field(default=None)
    gap_days: int | None = Field(default=None)
    avg_covers: float | None = Field(default=None)
    forecastable: bool | None = Field(default=None)

    model_config = {"from_attributes": True}


class LocationDetailResponse(LocationResponse):
    """Detail with the same computed history the list reports."""

    history_days: int | None = Field(default=None)
    avg_covers: float | None = Field(default=None)
    # Missing days as ISO dates. The list reports them as a count (`gap_days`).
    data_gaps: list[str] | None = Field(default=None)
