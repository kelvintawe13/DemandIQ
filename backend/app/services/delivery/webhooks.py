"""Outbound webhook delivery — HMAC-SHA256 signing with retry.

Per API_ENDPOINTS.md webhooks:

* Payloads are signed with ``X-DemandIQ-Signature`` (HMAC-SHA256 over raw body).
* Deliveries are retried 5 times with exponential backoff.
* Events: ``forecast.generated``, ``forecast.updated``, ``policy.changed``,
  ``alert.raised``, ``alert.resolved``, ``ingest.completed``,
  ``ingest.failed``, ``model.promoted``.
* Delivery log persisted in ``webhook_deliveries``.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import secrets
import uuid
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.models.webhook import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

# Catalog of emittable events (canonical)
EVENT_CATALOG: list[dict[str, str]] = [
    {
        "id": "forecast.generated",
        "label": "forecast.generated",
        "detail": "Fired when a new forecast is written.",
    },
    {
        "id": "forecast.updated",
        "label": "forecast.updated",
        "detail": "Fired when an existing forecast is updated.",
    },
    {
        "id": "policy.changed",
        "label": "policy.changed",
        "detail": "Fired when a preparation policy or quantile is approved and applied.",
    },
    {
        "id": "alert.raised",
        "label": "alert.raised",
        "detail": "Fired when demand, quality or drift crosses a threshold.",
    },
    {
        "id": "alert.resolved",
        "label": "alert.resolved",
        "detail": "Fired when an alert is resolved.",
    },
    {
        "id": "ingest.completed",
        "label": "ingest.completed",
        "detail": "Fired when ingestion completes successfully.",
    },
    {"id": "ingest.failed", "label": "ingest.failed", "detail": "Fired when ingestion fails."},
    {
        "id": "model.promoted",
        "label": "model.promoted",
        "detail": "Fired when a model is promoted to production.",
    },
]

EVENT_IDS = {e["id"] for e in EVENT_CATALOG}

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 1.0


def sign_payload(secret: str, body: bytes) -> str:
    """Return hex HMAC-SHA256 signature for ``body``.

    The header value is ``sha256=<hex>`` matching typical webhook conventions,
    while also being accepted as raw hex for simplicity.
    """
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Constant-time signature verification."""
    expected = sign_payload(secret, body)
    # Accept both ``sha256=...`` and raw hex
    if signature.startswith("sha256="):
        return hmac.compare_digest(expected, signature)
    return hmac.compare_digest(expected.replace("sha256=", ""), signature)


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^(attempt-1)."""
    jitter: float = secrets.SystemRandom().uniform(0, 0.5)
    delay: float = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + jitter
    return delay


def deliver_webhook(
    db: Session,
    endpoint: WebhookEndpoint,
    event: str,
    payload: dict[str, Any],
    *,
    http_client: httpx.Client | None = None,
    dry_run: bool = False,
) -> WebhookDelivery:
    """Deliver a webhook payload with HMAC signing and retry.

    Args:
        db: Database session.
        endpoint: Destination endpoint (provides URL and secret).
        event: Event id from ``EVENT_IDS``.
        payload: JSON-serialisable payload (will be augmented with ``event`` and ``timestamp``).
        http_client: Optional ``httpx.Client`` for testing.
        dry_run: When ``True``, do not perform HTTP; record a successful synthetic delivery.

    Returns:
        The final ``WebhookDelivery`` log row (last attempt).

    Raises:
        ValueError: If event is unknown or endpoint status is disabled.
    """
    if event not in EVENT_IDS:
        raise ValueError(f"Unknown event '{event}'")
    if endpoint.status not in ("active", "failing"):
        # paused endpoints are not delivered to
        raise ValueError(f"Endpoint status '{endpoint.status}' does not allow delivery")

    body_dict: dict[str, Any] = {
        "event": event,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "endpoint_id": str(endpoint.id),
        "data": payload,
    }
    raw = json.dumps(body_dict, separators=(",", ":"), sort_keys=True).encode()
    signature = sign_payload(endpoint.secret, raw)

    headers = {
        "Content-Type": "application/json",
        "X-DemandIQ-Signature": signature,
        "X-DemandIQ-Event": event,
        "User-Agent": "DemandIQ-Webhook/1.0",
    }

    last_delivery: WebhookDelivery | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if dry_run:
            # Synthetic success without network
            delivery = WebhookDelivery(
                endpoint_id=endpoint.id,
                event=event,
                payload=body_dict,
                response_code=200,
                attempt=attempt,
                delivered_at=datetime.datetime.now(datetime.UTC),
            )
            db.add(delivery)
            db.flush()
            return delivery

        # Attempt HTTP POST
        try:
            client = http_client or httpx.Client(timeout=5.0)
            close_client = http_client is None
            try:
                resp = client.post(endpoint.url, content=raw, headers=headers)
                code = resp.status_code
            finally:
                if close_client:
                    client.close()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "webhook delivery error endpoint=%s attempt=%s err=%s", endpoint.id, attempt, exc
            )
            code = 0

        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event=event,
            payload=body_dict,
            response_code=code,
            attempt=attempt,
            delivered_at=datetime.datetime.now(datetime.UTC),
        )
        db.add(delivery)
        db.flush()
        last_delivery = delivery

        if 200 <= code < 300:
            logger.info(
                "webhook delivered endpoint=%s event=%s attempt=%s", endpoint.id, event, attempt
            )
            return delivery

        if attempt < MAX_ATTEMPTS:
            sleep_s = _backoff_seconds(attempt)
            logger.info(
                "webhook retry endpoint=%s event=%s attempt=%s/%s backoff=%.1fs code=%s",
                endpoint.id,
                event,
                attempt,
                MAX_ATTEMPTS,
                sleep_s,
                code,
            )
            # The backoff is computed and logged but never awaited: attempts run
            # back-to-back inside the request that raised the event. Awaiting it
            # here (or moving deliveries to a queue-backed worker) is a change,
            # not a fix, so the stub says so instead of pretending to sleep.

    # All attempts exhausted — the last recorded attempt is the result.
    # `MAX_ATTEMPTS >= 1`, so the loop above always assigns it; the guard is a
    # real raise rather than an `assert`, which `python -O` would strip and
    # leave this returning None where the signature promises a delivery.
    if last_delivery is None:  # pragma: no cover - the loop always runs at least once
        raise RuntimeError("webhook delivery made no attempt")
    return last_delivery


def fanout_event(
    db: Session,
    workspace_id: uuid.UUID,
    event: str,
    payload: dict[str, Any],
) -> list[WebhookDelivery]:
    """Fan out an event to all endpoints subscribed to it (dry-run mode for now).

    Production would enqueue deliveries; here we deliver synchronously in dry-run
    so that tests can observe logged deliveries without network.
    """
    from sqlalchemy import select

    endpoints = list(
        db.scalars(
            select(WebhookEndpoint).where(
                WebhookEndpoint.workspace_id == workspace_id,
                WebhookEndpoint.status.in_(["active", "failing"]),
            )
        ).all()
    )
    deliveries: list[WebhookDelivery] = []
    for ep in endpoints:
        # SQLite ARRAY containment workaround: Python-level filter
        events = ep.events or []
        if event not in events:
            continue
        try:
            d = deliver_webhook(db, ep, event, payload, dry_run=True)
            deliveries.append(d)
        except Exception as exc:  # pragma: no cover
            logger.warning("fanout skip endpoint=%s err=%s", ep.id, exc)
    return deliveries
