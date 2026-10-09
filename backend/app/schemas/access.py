"""Schemas for access-log and audit-log reads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LocationAccessLogResponse(BaseModel):
    """Single location access log entry."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    location_id: uuid.UUID | None
    user_id: uuid.UUID | None
    action: str
    ip_address: str | None
    user_agent: str | None
    client: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    """Single audit log entry.

    ``actor_email`` is resolved by the router, one lookup per page, so a client
    can attribute a row to a person instead of leaving it blank or printing the
    opaque ``actor_id``. The id stays in the payload as the stable key.
    """

    id: int
    workspace_id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_email: str | None = None
    action: str
    target: str | None
    client: str
    metadata: dict | None = Field(default=None, alias="meta_data")
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class AccessLogListParams(BaseModel):
    """Query params for ``GET /locations/access-logs``."""

    location_id: uuid.UUID | None = Field(default=None)
    user_id: uuid.UUID | None = Field(default=None)
    action: str | None = Field(default=None)
    cursor: str | None = Field(default=None)
    limit: int = Field(default=20, ge=1, le=100)


class AuditLogListParams(BaseModel):
    """Query params for ``GET /audit-log``."""

    actor_id: uuid.UUID | None = Field(default=None)
    action: str | None = Field(default=None)
    client: str | None = Field(default=None)
    cursor: str | None = Field(default=None)
    limit: int = Field(default=20, ge=1, le=100)
