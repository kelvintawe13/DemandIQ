"""Model registry schemas."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class TrainRequest(BaseModel):
    """Body for POST /models/train."""

    algorithm: str = Field(description="lightgbm|catboost|xgboost|rf|linear|historical_avg")
    feature_set: str = Field(description="Feature set version, e.g. v2")
    params: dict[str, Any] | None = Field(default=None, description="Hyperparameters")


class ModelVersionResponse(BaseModel):
    """Model version read model."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    algorithm: str
    version: str
    status: str
    mae: float | None
    rmse: float | None
    rmsle: float | None
    hyperparameters: dict[str, Any] | None
    feature_set_version: str
    train_start: datetime.date | None
    train_end: datetime.date | None
    test_start: datetime.date | None
    test_end: datetime.date | None
    artifact_uri: str | None
    trained_at: datetime.datetime | None
    promoted_at: datetime.datetime | None

    model_config = {"from_attributes": True}


class CalibrationResponse(BaseModel):
    """Calibration read model."""

    id: uuid.UUID
    model_version_id: uuid.UUID
    method: str
    target_coverage: float
    empirical_coverage: float | None
    mean_interval_width: float | None
    evaluated_at: datetime.datetime

    model_config = {"from_attributes": True}


class ImportanceItem(BaseModel):
    """Single feature importance entry."""

    feature: str
    importance: float
    rank: int | None = None


class ImportanceResponse(BaseModel):
    """Aggregate feature importance."""

    model_version_id: uuid.UUID
    importance: list[ImportanceItem]
    method: str = "gain"
