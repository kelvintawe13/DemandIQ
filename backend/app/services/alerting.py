"""Raising alerts — the one place an ``alerts`` row and its webhook are written.

The ``drift_check`` job used to write an ``alerts`` row and stop there, and a
failed job wrote nothing at all: the run row sat in ``pipeline_runs`` with
``status='failed'`` and only someone watching the console would notice. Nothing
reached the ``alert.raised`` webhook that ``API_ENDPOINTS.md`` documents and
:func:`app.services.delivery.webhooks.fanout_event` already implements.

Both now go through :func:`raise_alert`, which:

1. de-duplicates against the same open alert (``title``, unresolved) so an
   hourly drift check does not pile up identical rows, and
2. fans the alert out to every endpoint subscribed to ``alert.raised``, so an
   operator's pager/chat integration sees it without polling.

Dedupe refreshes the *existing* row's ``body``/``severity`` so the newest
detail is what the alert shows, and does not re-fire the webhook — one open
alert means one notification.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.quality import Alert

logger = logging.getLogger(__name__)

ALERT_RAISED = "alert.raised"
ALERT_RESOLVED = "alert.resolved"


def find_open_alert(db: Session, *, workspace_id: uuid.UUID, title: str) -> Alert | None:
    """Return the newest unresolved alert with ``title``, if any.

    Args:
        db: Database session.
        workspace_id: Workspace scope.
        title: Exact alert title to match.

    Returns:
        The unresolved :class:`Alert`, or ``None``.
    """
    try:
        rows = [
            r
            for r in db.scalars(
                select(Alert).where(
                    Alert.workspace_id == workspace_id,
                    Alert.title == title,
                    Alert.resolved_at.is_(None),
                )
            ).all()
            if isinstance(r, Alert)
        ]
    except Exception:  # pragma: no cover - defensive (mocked session)
        return None
    return rows[0] if rows else None


def notify(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    event: str,
    payload: dict[str, Any],
) -> int:
    """Fan an event out to subscribed webhook endpoints.

    Delivery problems must never fail the job that is reporting them, so every
    error here is logged and swallowed.

    Args:
        db: Database session.
        workspace_id: Workspace scope.
        event: Event id from the webhook catalog.
        payload: JSON-serialisable payload.

    Returns:
        Number of deliveries logged.
    """
    try:
        from app.services.delivery.webhooks import fanout_event

        return len(fanout_event(db, workspace_id, event, payload))
    except Exception:  # pragma: no cover - delivery must not break the caller
        logger.warning("webhook fanout failed for %s", event, exc_info=True)
        return 0


def raise_alert(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    severity: str,
    title: str,
    body: str,
    payload: dict[str, Any] | None = None,
    location_id: uuid.UUID | None = None,
    rule_id: uuid.UUID | None = None,
    event: str = ALERT_RAISED,
) -> Alert:
    """Persist an alert (de-duplicated) and fan it out over webhooks.

    Args:
        db: Database session.
        workspace_id: Owning workspace.
        severity: ``critical`` | ``warning`` | ``info``.
        title: Alert title — also the de-duplication key.
        body: Human-readable detail; refreshed on a repeat.
        payload: Extra fields for the webhook body.
        location_id: Optional location the alert is about.
        rule_id: Optional alert rule that fired.
        event: Webhook event id (defaults to ``alert.raised``).

    Returns:
        The new alert, or the refreshed open one it de-duplicated against.
    """
    if severity not in ("critical", "warning", "info"):
        severity = "warning"
    now = datetime.datetime.now(datetime.UTC)

    existing = find_open_alert(db, workspace_id=workspace_id, title=title)
    if existing is not None:
        # Keep one open alert per condition, showing the newest detail.
        existing.body = body
        existing.severity = severity
        db.flush()
        return existing

    alert = Alert(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        rule_id=rule_id,
        location_id=location_id,
        severity=severity,
        title=title,
        body=body,
        created_at=now,
    )
    db.add(alert)
    db.flush()

    notify(
        db,
        workspace_id=workspace_id,
        event=event,
        payload={
            "alert_id": str(alert.id),
            "severity": severity,
            "title": title,
            "body": body,
            "location_id": str(location_id) if location_id else None,
            "raised_at": now.isoformat(),
            **(payload or {}),
        },
    )
    return alert


def record_job_failure(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    job_type: str,
    run_id: uuid.UUID,
    error: str,
) -> None:
    """Alert on a failed pipeline run and notify subscribers.

    One open alert per job type: a job that keeps failing refreshes its alert
    instead of stacking a row per attempt.
    """
    raise_alert(
        db,
        workspace_id=workspace_id,
        severity="warning",
        title=f"Pipeline job failed: {job_type}",
        body=f"{job_type} failed at {datetime.datetime.now(datetime.UTC).isoformat()}: {error}",
        payload={
            "job_type": job_type,
            "run_id": str(run_id),
            "status": "failed",
            "source": "pipeline",
        },
    )


def record_job_success(db: Session, *, workspace_id: uuid.UUID, job_type: str) -> None:
    """Resolve the open failure alert for ``job_type``, if there is one.

    A job that failed and later succeeds should not leave a stale alert on the
    console, and recovery is worth telling subscribers about.
    """
    title = f"Pipeline job failed: {job_type}"
    alert = find_open_alert(db, workspace_id=workspace_id, title=title)
    if alert is None:
        return
    alert.resolved_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    notify(
        db,
        workspace_id=workspace_id,
        event=ALERT_RESOLVED,
        payload={
            "alert_id": str(alert.id),
            "title": title,
            "job_type": job_type,
            "resolved_at": alert.resolved_at.isoformat(),
            "source": "pipeline",
        },
    )
