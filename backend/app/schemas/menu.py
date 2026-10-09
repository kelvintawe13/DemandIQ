"""Menu-group schemas — perishability CHECK high/medium/low."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class MenuGroupCreate(BaseModel):
    """Body for POST /menu-groups — admin only."""

    name: str = Field(min_length=1, max_length=200)
    item_count: int = Field(default=0, ge=0, description="Number of items in group")
    cover_share: float | None = Field(
        default=None, ge=0, le=100, description="Share of covers 0-100"
    )
    perishability: Literal["high", "medium", "low"]


class MenuGroupUpdate(BaseModel):
    """Body for PATCH /menu-groups/{id} — admin only."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    item_count: int | None = Field(default=None, ge=0)
    cover_share: float | None = Field(default=None, ge=0, le=100)
    perishability: Literal["high", "medium", "low"] | None = Field(default=None)


class MenuGroupResponse(BaseModel):
    """Menu group read model."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    item_count: int
    cover_share: float | None = None
    perishability: Literal["high", "medium", "low"]

    model_config = {"from_attributes": True}
