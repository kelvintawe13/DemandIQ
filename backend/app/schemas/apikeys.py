"""API key schemas."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class ApiKeyCreate(BaseModel):
    """Body for POST /api-keys (admin)."""

    label: str = Field(min_length=1, max_length=200, description="Human label")
    scopes: list[str] | None = Field(default=None, description="Scopes e.g. read, write")
    expires_at: datetime.datetime | None = Field(default=None)


class ApiKeyResponse(BaseModel):
    """API key read model — secret never returned."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    label: str
    prefix: str
    scopes: list[str]
    last_used_at: datetime.datetime | None = Field(default=None)
    revoked_at: datetime.datetime | None = Field(default=None)
    created_at: datetime.datetime | None = Field(default=None)

    model_config = {"from_attributes": True}


class ApiKeyCreateResponse(ApiKeyResponse):
    """Create / rotate response — full secret returned exactly once."""

    secret: str = Field(description="Full API key. Store securely, shown once")
    warning: str = Field(default="Store this secret securely. It will not be shown again")


class ApiKeyRotateResponse(ApiKeyCreateResponse):
    """Rotate response — same shape as create."""
