"""Workspace schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WorkspaceCreate(BaseModel):
    """Payload for creating a workspace during registration."""

    name: str = Field(min_length=1, max_length=200)
    business_type: str | None = Field(default=None, max_length=100)
    country: str = Field(default="RW", max_length=10)
    timezone: str = Field(default="Africa/Kigali", max_length=100)


class WorkspaceResponse(BaseModel):
    """Workspace returned by the API."""

    id: uuid.UUID
    name: str
    business_type: str | None
    country: str
    timezone: str
    created_at: datetime

    model_config = {"from_attributes": True}
