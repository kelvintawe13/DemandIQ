"""Feature definition schemas."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class FeatureDefinitionResponse(BaseModel):
    """Feature definition read model."""

    id: uuid.UUID | None = None
    name: str
    feature_group: str
    dtype: str
    description: str
    coverage_pct: float | None = None
    leakage_safe: bool = True
    in_production: bool = False

    model_config = {"from_attributes": True}


class FeatureCoverageLocation(BaseModel):
    """Coverage for a single location."""

    location_id: uuid.UUID
    location_name: str | None = None
    coverage_pct: float
    available_records: int
    total_records: int


class FeatureCoverageResponse(BaseModel):
    """Per-feature coverage breakdown."""

    feature: str
    feature_group: str | None = None
    leakage_safe: bool = True
    in_production: bool = False
    overall_coverage_pct: float | None = None
    per_location: list[FeatureCoverageLocation]
