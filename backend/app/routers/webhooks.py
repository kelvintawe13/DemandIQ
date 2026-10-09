"""Webhook endpoints per API_ENDPOINTS.md webhooks — admin.

* GET  /api/v1/webhooks
* POST /api/v1/webhooks
* PATCH /api/v1/webhooks/{id}
* DELETE /api/v1/webhooks/{id}
* POST /api/v1/webhooks/{id}/test
* GET  /api/v1/webhooks/{id}/deliveries
* GET  /api/v1/webhooks/events
"""

from __future__ import annotations

import logging
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.security.permissions import WEBHOOKS_MANAGE, has_capability
from app.services.delivery.webhooks import EVENT_CATALOG, EVENT_IDS, deliver_webhook, sign_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    if not has_capability(user, WEBHOOKS_MANAGE):
        raise _error(403, "forbidden", "Administrator role required")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WebhookCreateRequest(BaseModel):
    """Body for POST /webhooks."""

    url: str = Field(description="HTTPS endpoint URL")
    events: list[str] = Field(description="Subscribed events")
    secret: str | None = Field(default=None, description="Optional secret; generated if omitted")
    status: str | None = Field(default="active", description="active|paused")


class WebhookPatchRequest(BaseModel):
    """Body for PATCH /webhooks/{id}."""

    url: str | None = Field(default=None)
    events: list[str] | None = Field(default=None)
    secret: str | None = Field(default=None)
    status: str | None = Field(default=None, description="active|paused|failing")


# ---------------------------------------------------------------------------
# GET /events  (catalog)
# ---------------------------------------------------------------------------


@router.get("/events", response_model=dict)
def list_events(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Catalog of emittable events."""
    _ = db
    _ = current_user
    return {"data": EVENT_CATALOG}


# ---------------------------------------------------------------------------
# GET /  and POST /
# ---------------------------------------------------------------------------


@router.get("", response_model=dict)
def list_webhooks(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List webhook endpoints for the workspace."""
    _require_admin(current_user)
    rows = list(
        db.scalars(
            select(WebhookEndpoint).where(WebhookEndpoint.workspace_id == current_user.workspace_id)
        ).all()
    )
    return {
        "data": [
            {
                "id": str(r.id),
                "url": r.url,
                "events": r.events,
                "secret": "***",
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    }


@router.post("", response_model=dict, status_code=201)
def create_webhook(
    payload: WebhookCreateRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a webhook endpoint (admin)."""
    _require_admin(current_user)

    # Validate URL
    if not payload.url.startswith("https://") and not payload.url.startswith("http://"):
        raise _error(400, "invalid_url", "URL must start with https://", field="url")

    # Validate events
    if not payload.events:
        raise _error(400, "invalid_events", "At least one event is required", field="events")
    for ev in payload.events:
        if ev not in EVENT_IDS:
            raise _error(400, "invalid_event", f"Unknown event '{ev}'", field="events")

    # Validate status if supplied
    if payload.status is not None and payload.status not in ("active", "paused", "failing"):
        raise _error(400, "invalid_status", "status must be active|paused|failing", field="status")

    secret = payload.secret or secrets.token_hex(16)

    ep = WebhookEndpoint(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        url=payload.url,
        events=payload.events,
        secret=secret,
        status=payload.status or "active",
    )
    # Ensure created_at for mocked DB sessions that bypass SQLAlchemy defaults
    if ep.created_at is None:  # type: ignore[has-type]
        import datetime

        ep.created_at = datetime.datetime.now(datetime.UTC)  # type: ignore[assignment]
    db.add(ep)
    db.flush()

    logger.info(
        "webhook created id=%s workspace=%s by=%s",
        ep.id,
        current_user.workspace_id,
        current_user.id,
    )
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "secret": ep.secret,
        "status": ep.status,
        "created_at": ep.created_at.isoformat() if ep.created_at else None,
    }


# ---------------------------------------------------------------------------
# PATCH /{id}  DELETE /{id}
# ---------------------------------------------------------------------------


@router.patch("/{webhook_id}", response_model=dict)
def patch_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookPatchRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a webhook endpoint (admin)."""
    _require_admin(current_user)
    ep: WebhookEndpoint | None = db.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Webhook not found")

    if payload.url is not None:
        if not payload.url.startswith("https://") and not payload.url.startswith("http://"):
            raise _error(400, "invalid_url", "URL must start with https://", field="url")
        ep.url = payload.url
    if payload.events is not None:
        if not payload.events:
            raise _error(400, "invalid_events", "At least one event is required", field="events")
        for ev in payload.events:
            if ev not in EVENT_IDS:
                raise _error(400, "invalid_event", f"Unknown event '{ev}'", field="events")
        ep.events = payload.events
    if payload.secret is not None:
        ep.secret = payload.secret
    if payload.status is not None:
        if payload.status not in ("active", "paused", "failing"):
            raise _error(
                400, "invalid_status", "status must be active|paused|failing", field="status"
            )
        ep.status = payload.status

    db.flush()
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": ep.events,
        "secret": "***",
        "status": ep.status,
        "created_at": ep.created_at.isoformat() if ep.created_at else None,
    }


@router.delete("/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete a webhook endpoint (admin)."""
    _require_admin(current_user)
    ep: WebhookEndpoint | None = db.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Webhook not found")
    db.delete(ep)
    db.flush()


# ---------------------------------------------------------------------------
# POST /{id}/test
# ---------------------------------------------------------------------------


@router.post("/{webhook_id}/test", response_model=dict)
def test_webhook(
    webhook_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Send a signed sample payload to the endpoint (dry-run)."""
    _require_admin(current_user)
    ep: WebhookEndpoint | None = db.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Webhook not found")

    # Use the first subscribed event, or forecast.generated
    event = ep.events[0] if ep.events else "forecast.generated"
    sample_payload: dict[str, Any] = {
        "sample": True,
        "message": "DemandIQ webhook test",
        "workspace_id": str(current_user.workspace_id),
    }
    delivery = deliver_webhook(db, ep, event, sample_payload, dry_run=True)

    raw_preview = sample_payload
    _ = sign_payload  # referenced for mypy strict
    return {
        "endpoint_id": str(ep.id),
        "event": event,
        "attempt": delivery.attempt,
        "response_code": delivery.response_code,
        "delivered_at": delivery.delivered_at.isoformat(),
        "payload": raw_preview,
        "signature_header": "X-DemandIQ-Signature",
    }


# ---------------------------------------------------------------------------
# GET /{id}/deliveries
# ---------------------------------------------------------------------------


@router.get("/{webhook_id}/deliveries", response_model=dict)
def list_deliveries(
    webhook_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Delivery log with response codes and attempts."""
    _require_admin(current_user)
    ep: WebhookEndpoint | None = db.get(WebhookEndpoint, webhook_id)
    if ep is None or ep.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Webhook not found")

    q = select(WebhookDelivery).where(WebhookDelivery.endpoint_id == ep.id)
    if cursor:
        try:
            # cursor is delivered_at iso string
            ts = __import__("datetime").datetime.fromisoformat(cursor)
            q = q.where(WebhookDelivery.delivered_at < ts)
        except ValueError:
            raise _error(400, "invalid_cursor", "Invalid cursor") from None
    q = q.order_by(WebhookDelivery.delivered_at.desc(), WebhookDelivery.id.desc()).limit(limit + 1)
    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].delivered_at.isoformat() if has_more and page else None

    return {
        "data": [
            {
                "id": str(r.id),
                "endpoint_id": str(r.endpoint_id),
                "event": r.event,
                "response_code": r.response_code,
                "attempt": r.attempt,
                "delivered_at": r.delivered_at.isoformat(),
            }
            for r in page
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
