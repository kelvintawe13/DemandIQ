"""Common Pydantic schemas: pagination, errors, health."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """Single error envelope per ``IMPLEMENTATION_GUIDE.md``."""

    code: str = Field(description="Machine-readable error code")
    message: str = Field(description="Human-readable message")
    field: str | None = Field(default=None, description="Field that caused the error")
    reference: str | None = Field(default=None, description="Request id for support")


class ErrorResponse(BaseModel):
    """Top-level error shape ``{error: {code, message, field, reference}}``."""

    error: ErrorDetail


class PaginationParams(BaseModel):
    """Cursor pagination query parameters."""

    cursor: str | None = Field(default=None, description="Opaque cursor from previous page")
    limit: int = Field(default=20, ge=1, le=100, description="Page size")


class PaginatedResponse(BaseModel, Generic[T]):
    """Cursor-paginated response."""

    data: list[T]
    next_cursor: str | None = Field(default=None)
    has_more: bool = Field(default=False)


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(default="ok")
    version: str
    timestamp: datetime | None = Field(default=None)
    database: str | None = Field(default=None)
    redis: str | None = Field(default=None)
    # Which forecasting path is live: `{mode: trained|stub|error, ...}`. Present
    # on the detailed probe so an operator (or a monitor) can see that a
    # configured bundle is not being served — a fact the response body used to
    # leave to the boot log.
    serving: dict[str, Any] | None = Field(default=None)


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
    id: uuid.UUID | None = Field(default=None)
    details: dict[str, Any] | None = Field(default=None)
