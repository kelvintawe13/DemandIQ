"""Experiment schemas."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class ExperimentCreateRequest(BaseModel):
    """Body for POST /experiments."""

    name: str = Field(min_length=1, max_length=200)
    hypothesis: str | None = Field(default=None, max_length=1000)
    algorithm: str = Field(description="lightgbm|catboost|xgboost|rf|linear|historical_avg")
    feature_set: str = Field(description="Feature set version")
    folds: int = Field(default=3, ge=1, le=10, description="Number of CV folds")


class ExperimentFoldResponse(BaseModel):
    """Fold-level result."""

    id: uuid.UUID | None = None
    fold_index: int
    train_start: datetime.date | None = None
    train_end: datetime.date | None = None
    test_start: datetime.date | None = None
    test_end: datetime.date | None = None
    mae: float | None = None
    coverage: float | None = None
    baseline_mae: float | None = None
    baseline_delta: float | None = None


class ExperimentResponse(BaseModel):
    """Experiment read model."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    hypothesis: str | None
    config: dict[str, Any]
    status: str
    mae: float | None
    baseline_mae: float | None
    coverage: float | None
    created_by: uuid.UUID | None
    finished_at: datetime.datetime | None
    created_at: datetime.datetime
    folds: list[ExperimentFoldResponse] | None = None

    model_config = {"from_attributes": True}
