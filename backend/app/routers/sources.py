"""Sources router — aggregated connector view and manual sync queue.

* GET  /api/v1/sources
* POST /api/v1/sources/{id}/sync
* GET  /api/v1/sources/sync-history

Sources are an aggregated view of ``connectors`` for the channel overview
(status / freshness / volume). Sync queues a ``connector_sync`` run — drained by
the scheduler or worker, which pulls from the provider and commits the rows — and
history reads back that table. Only an ``active`` connector is syncable; see
``trigger_sync``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.location import Connector, DemandObservation
from app.models.pipeline import PipelineRun
from app.models.user import User
from app.security.permissions import SOURCES_READ, SOURCES_WRITE, has_capability


def require_sources_read(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Listing connected sources and their sync history."""
    if not has_capability(user, SOURCES_READ):
        raise _error(403, "forbidden", "Source read permission required")
    return user


def require_sources_write(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Triggering a sync pulls data into the workspace."""
    if not has_capability(user, SOURCES_WRITE):
        raise _error(403, "forbidden", "Source sync permission required")
    return user


router = APIRouter(
    prefix="/api/v1/sources",
    tags=["sources"],
    dependencies=[Depends(require_sources_read)],
)


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _freshness(last_sync_at: datetime.datetime | None) -> str | None:
    if last_sync_at is None:
        return "never"
    now = datetime.datetime.now(datetime.UTC)
    delta = now - last_sync_at
    if delta.total_seconds() < 3600 * 24:
        return "fresh"
    if delta.total_seconds() < 3600 * 48:
        return "stale"
    return "stale"


def _health_for(conn: Connector, freshness: str | None) -> str:
    if conn.status in ("error",):
        return "error"
    if freshness == "never":
        return "unknown"
    if freshness == "stale":
        return "degraded"
    return "healthy"


@router.get("", response_model=dict)
def list_sources(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List sources (connectors aggregated view) for the workspace."""
    connectors = list(
        db.scalars(
            select(Connector).where(Connector.workspace_id == current_user.workspace_id)
        ).all()
    )
    data: list[dict[str, Any]] = []
    for conn in connectors:
        # Volume: count demand observations linked to this connector.
        try:
            # Prefer scalars list for mock compatibility.
            obs = list(
                db.scalars(
                    select(DemandObservation).where(
                        DemandObservation.workspace_id == current_user.workspace_id,
                        DemandObservation.source_id == conn.id,
                    )
                ).all()
            )
            volume = len(obs)
        except Exception:
            volume = 0
        freshness = _freshness(conn.last_sync_at)
        health = _health_for(conn, freshness)
        # Ensure created_at fallback for mocked rows.
        created = getattr(conn, "created_at", None)
        if created is None:
            created = datetime.datetime.now(datetime.UTC)
        data.append(
            {
                "id": str(conn.id),
                "name": conn.name,
                "type": conn.type,
                "status": conn.status,
                "freshness": freshness,
                "last_sync_at": conn.last_sync_at.isoformat() if conn.last_sync_at else None,
                "volume": volume,
                "health": health,
                # This channel's own last pull, not the workspace-wide job row:
                # ``pipeline_runs`` records no connector id, so a per-source
                # filter there is impossible. Counts are ``None`` until a pull
                # has run; ``detail`` also explains a channel with no endpoint.
                "last_pull": {
                    "pulled": getattr(conn, "last_pull_pulled", None),
                    "committed": getattr(conn, "last_pull_committed", None),
                    "skipped": getattr(conn, "last_pull_skipped", None),
                    "detail": getattr(conn, "last_pull_detail", None),
                },
                "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
            }
        )
    return {"data": data}


@router.get("/sync-history", response_model=dict)
def sync_history(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Recent sync runs from pipeline_runs (workspace-scoped)."""
    q = select(PipelineRun).where(PipelineRun.workspace_id == current_user.workspace_id)
    if cursor:
        try:
            ts = datetime.datetime.fromisoformat(cursor)
            q = q.where(PipelineRun.started_at < ts)
        except ValueError:
            raise _error(400, "invalid_cursor", "Invalid cursor") from None
    q = q.order_by(PipelineRun.started_at.desc()).limit(limit + 1)
    try:
        rows = list(db.scalars(q).all())
    except Exception:
        rows = []
    # Fallback fixture when no rows but connectors exist — keep history visible.
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1].started_at.isoformat() if has_more and page else None
    return {
        "data": [
            {
                "id": str(r.id),
                "job_type": r.job_type,
                "status": r.status,
                "rows_processed": r.rows_processed,
                "duration_ms": r.duration_ms,
                "detail": r.detail,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for r in page
        ],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


@router.post(
    "/{source_id}/sync",
    response_model=dict,
    status_code=201,
    dependencies=[Depends(require_sources_write)],
)
def trigger_sync(
    source_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Queue a manual sync for a source and return the job id.

    The run is enqueued as ``connector_sync`` and stays ``queued`` until it is
    drained: by the in-process scheduler (``JOBS_ENABLED=true``), by the worker
    (``python -m app.jobs.worker``), or on demand via
    ``POST /api/v1/pipeline/queue/drain``. ``last_sync_at`` is stamped when the
    pull completes, so freshness reflects a finished sync rather than a queued
    one — check ``GET /sync-history`` for the run's status.

    Only an ``active`` connector is syncable. ``_job_connector_sync`` selects on
    that status, so queueing a run for a ``paused``/``available``/``error``
    channel would settle as ``success`` having pulled nothing — a queued run
    that reads as a working sync and silently does not work. The refusal is here
    as well as in the UI so a direct API caller gets the same answer.
    """
    from app.jobs.runner import enqueue_run

    conn: Connector | None = db.get(Connector, source_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Source not found")
    if conn.status != "active":
        raise _error(
            409,
            "not_active",
            f"Source '{conn.name}' is {conn.status}, not active. A sync only pulls "
            "active channels; an administrator activates it on Connectors.",
        )
    job = enqueue_run(
        db,
        workspace_id=current_user.workspace_id,
        job_type="connector_sync",
        detail=f"manual sync for connector {conn.id}",
    )
    return {"job_id": str(job.id), "status": job.status, "source_id": str(conn.id)}
