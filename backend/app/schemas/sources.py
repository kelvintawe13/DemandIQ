"""Sources schemas — aggregated connector view."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class SourceResponse(BaseModel):
    """Aggregated connector channel with status, freshness and volume."""

    id: uuid.UUID = Field(description="Connector id acting as source id")
    name: str = Field(description="Source name")
    type: str = Field(description="Connector type: pos|momo|file|ocr|api|sms")
    status: str = Field(description="Connector status")
    freshness: str | None = Field(default=None, description="fresh|stale|never")
    last_sync_at: datetime.datetime | None = Field(default=None)
    volume: int = Field(default=0, description="Count of demand observations")
    health: str = Field(default="unknown", description="Health indicator")

    model_config = {"from_attributes": True}


class SourceSyncResponse(BaseModel):
    """Manual sync trigger response."""

    job_id: uuid.UUID = Field(description="Queued pipeline job id")
    status: str = Field(default="queued")
    source_id: uuid.UUID


class SyncHistoryItem(BaseModel):
    """Single sync / pipeline run entry."""

    id: uuid.UUID
    job_type: str
    status: str
    rows_processed: int | None = Field(default=None)
    duration_ms: int | None = Field(default=None)
    detail: str | None = Field(default=None)
    started_at: datetime.datetime

    model_config = {"from_attributes": True}
