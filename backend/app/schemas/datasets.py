"""Dataset and waste schemas — governance evidence boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DatasetEntry(BaseModel):
    """Registered dataset entry for ``GET /datasets``."""

    id: str = Field(description="Dataset identifier (recruit | urban-waste)")
    name: str = Field(description="Human-readable dataset name")
    table: str = Field(description="Canonical table (demand_observations | waste_observations)")
    source_dataset: str = Field(description="source_dataset discriminator")
    licence: str = Field(description="Licence string")
    access_conditions: str = Field(description="Access conditions")
    records: int = Field(description="Primary record count")
    records_detail: str = Field(description="Human-readable record summary")
    coverage: str = Field(description="Coverage summary")
    period: str = Field(description="Temporal period")
    period_start: str | None = Field(default=None, description="ISO start date")
    period_end: str | None = Field(default=None, description="ISO end date")
    status: str = Field(description="validated | partial")
    notes: str = Field(description="Audited notes")
    separation_note: str = Field(description="Evidence boundary note")


class DatasetListResponse(BaseModel):
    """Response for ``GET /datasets``."""

    data: list[DatasetEntry]


class ManifestResponse(BaseModel):
    """Reproducibility manifest for ``GET /datasets/{id}/manifest``."""

    id: str
    name: str
    table: str
    source_dataset: str
    licence: str
    record_count: int
    date_range: dict[str, str | None]
    checksums: dict[str, str]
    preprocessing_version: str
    feature_set_version: str | None = Field(default=None)
    schema_ref: str
    generated_at: str
    notes: str
    extra: dict[str, Any] | None = Field(default=None)


class WasteDescriptiveStats(BaseModel):
    """Descriptive statistics from ``waste_observations``."""

    total_records: int
    non_missing_waste: int
    missing_waste: int
    establishments: int
    countries: int
    ordered_portions_available: int
    waste_kg: dict[str, float | int | None]
    establishment_variation: list[dict[str, Any]]
    date_range: dict[str, str | None]
    licence: str
    source_dataset: str


class WasteSummaryResponse(BaseModel):
    """Response for ``GET /waste/summary`` — never joined to demand."""

    evidence_boundary: str
    descriptive_stats: WasteDescriptiveStats
    note: str
    generated_at: str


class AuditStub(BaseModel):
    """Lightweight audit entry for test visibility."""

    action: str
    target: str | None = None
    created_at: datetime | None = None
