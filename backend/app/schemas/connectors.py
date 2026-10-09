"""Connector schemas."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class ConnectorCreate(BaseModel):
    """Body for POST /connectors (admin)."""

    type: str = Field(description="Connector type: pos|momo|file|ocr|api|sms")
    name: str = Field(min_length=1, max_length=200, description="Display name")
    location_ids: list[uuid.UUID] | None = Field(default=None, description="Bound locations")
    credentials: dict[str, Any] | None = Field(default=None, description="Opaque credentials")
    status: str | None = Field(default=None, description="active|paused|error|available")


class ConnectorUpdate(BaseModel):
    """Body for PATCH /connectors/{id} (admin)."""

    type: str | None = Field(default=None)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location_ids: list[uuid.UUID] | None = Field(default=None)
    credentials: dict[str, Any] | None = Field(default=None)
    status: str | None = Field(default=None)


class ConnectorResponse(BaseModel):
    """Connector read model."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    name: str
    status: str
    credentials: dict[str, Any] | None = Field(default=None)
    last_sync_at: datetime.datetime | None = Field(default=None)
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class ConnectorTestResponse(BaseModel):
    """Round-trip test result."""

    connector_id: uuid.UUID
    latency_ms: int = Field(description="Simulated latency")
    sample_payload: dict[str, Any] = Field(description="Sample record shape")
    status: str = Field(description="ok|error")
    checked_at: datetime.datetime


class ConnectorMappingItem(BaseModel):
    """Single field mapping row."""

    id: uuid.UUID | None = Field(default=None)
    source_field: str = Field(description="Source column/field name")
    canonical_field: str = Field(description="location_id|date|demand_qty|notes")
    transform: str | None = Field(default=None)


class ConnectorMappingResponse(BaseModel):
    """GET /connectors/{id}/mapping — list of mappings."""

    connector_id: uuid.UUID
    mappings: list[ConnectorMappingItem]


class ConnectorMappingPatch(BaseModel):
    """PATCH /connectors/{id}/mapping — replace mappings."""

    mappings: list[ConnectorMappingItem] = Field(description="Full replacement set")
