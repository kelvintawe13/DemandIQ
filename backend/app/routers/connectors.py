"""Connectors admin router — CRUD, test round-trip, mapping.

* GET    /api/v1/connectors
* POST   /api/v1/connectors
* PATCH  /api/v1/connectors/{id}
* DELETE /api/v1/connectors/{id}
* POST   /api/v1/connectors/{id}/test
* GET    /api/v1/connectors/{id}/mapping
* PATCH  /api/v1/connectors/{id}/mapping

All routes are admin-guarded (owner/administrator) and workspace-scoped.
"""

from __future__ import annotations

import datetime
import logging
import time
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.connector_mapping import ConnectorMapping
from app.models.location import Connector
from app.models.user import User
from app.security.permissions import CONNECTORS_MANAGE, has_capability
from app.services.ingestion import connector_pull

router = APIRouter(prefix="/api/v1/connectors", tags=["connectors"])

logger = logging.getLogger(__name__)

# ``manual`` is a real channel (hand entry, see app/services/ingestion/channels.py)
# so it is creatable here too; the enum value arrives in migration 023.
_ALLOWED_TYPES = {"pos", "momo", "file", "ocr", "api", "sms", "manual"}
_ALLOWED_STATUS = {"active", "paused", "error", "available"}
_ALLOWED_CANONICAL = {"location_id", "date", "demand_qty", "notes"}

# Rows returned in a pull response. A provider can return thousands; the caller
# needs to see the shape and a sample, not a copy of the whole payload.
_PULL_PREVIEW_LIMIT = 50


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    if not has_capability(user, CONNECTORS_MANAGE):
        raise _error(403, "forbidden", "Administrator role required")


def _mask_credentials(creds: dict[str, Any] | None) -> dict[str, Any] | None:
    if not creds:
        return creds
    masked: dict[str, Any] = {}
    for k, v in creds.items():
        if "secret" in k.lower() or "key" in k.lower() or "token" in k.lower():
            masked[k] = "***"
        else:
            masked[k] = v
    return masked


@router.get("", response_model=dict)
def list_connectors(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List connector definitions for the workspace (admin)."""
    _require_admin(current_user)
    rows = list(
        db.scalars(
            select(Connector).where(Connector.workspace_id == current_user.workspace_id)
        ).all()
    )
    data: list[dict[str, Any]] = []
    for r in rows:
        created = getattr(r, "created_at", None)
        if created is None:
            created = datetime.datetime.now(datetime.UTC)
        data.append(
            {
                "id": str(r.id),
                "workspace_id": str(r.workspace_id),
                "type": r.type,
                "name": r.name,
                "status": r.status,
                # No `protocol` (a second name for `type`) and no `direction`
                # (always "inbound"): every connector here is inbound, and a
                # constant the client has to read past is noise, not surface.
                "health": "healthy" if r.status in ("active", "available") else "degraded",
                "credentials": _mask_credentials(r.credentials),
                "last_sync_at": r.last_sync_at.isoformat() if r.last_sync_at else None,
                "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
            }
        )
    return {"data": data}


@router.post("", response_model=dict, status_code=201)
def create_connector(
    payload: dict[str, Any],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a connector (admin). Body: type, name, location_ids[], credentials."""
    _require_admin(current_user)
    ctype = payload.get("type")
    name = payload.get("name")
    if not ctype or ctype not in _ALLOWED_TYPES:
        raise _error(
            400, "invalid_type", f"type must be one of {sorted(_ALLOWED_TYPES)}", field="type"
        )
    if not name or not str(name).strip():
        raise _error(400, "validation_error", "name is required", field="name")
    status_val = payload.get("status") or "available"
    if status_val not in _ALLOWED_STATUS:
        raise _error(
            400,
            "invalid_status",
            f"status must be one of {sorted(_ALLOWED_STATUS)}",
            field="status",
        )
    credentials = payload.get("credentials")
    location_ids = payload.get("location_ids")
    # Merge location_ids into credentials for persistence.
    merged_creds: dict[str, Any] | None = None
    if credentials is not None or location_ids is not None:
        merged_creds = dict(credentials) if isinstance(credentials, dict) else {}
        if location_ids is not None:
            if not isinstance(location_ids, list):
                raise _error(
                    400,
                    "invalid_location_ids",
                    "location_ids must be an array",
                    field="location_ids",
                )
            # Validate each is UUID string.
            for lid in location_ids:
                try:
                    uuid.UUID(str(lid))
                except ValueError:
                    raise _error(
                        400, "invalid_location_ids", f"invalid uuid {lid}", field="location_ids"
                    ) from None
            merged_creds["location_ids"] = [str(x) for x in location_ids]
        if not merged_creds:
            merged_creds = None
    conn = Connector(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        type=ctype,
        name=str(name).strip(),
        status=status_val,
        credentials=merged_creds,
        last_sync_at=None,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(conn)
    db.flush()
    created = getattr(conn, "created_at", datetime.datetime.now(datetime.UTC))
    return {
        "id": str(conn.id),
        "workspace_id": str(conn.workspace_id),
        "type": conn.type,
        "name": conn.name,
        "status": conn.status,
        "credentials": conn.credentials,
        "last_sync_at": None,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
    }


@router.patch("/{connector_id}", response_model=dict)
def patch_connector(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a connector (admin)."""
    _require_admin(current_user)
    conn: Connector | None = db.get(Connector, connector_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Connector not found")
    if "type" in payload and payload["type"] is not None:
        if payload["type"] not in _ALLOWED_TYPES:
            raise _error(
                400, "invalid_type", f"type must be one of {sorted(_ALLOWED_TYPES)}", field="type"
            )
        conn.type = payload["type"]
    if "name" in payload and payload["name"] is not None:
        if not str(payload["name"]).strip():
            raise _error(400, "validation_error", "name cannot be empty", field="name")
        conn.name = str(payload["name"]).strip()
    if "status" in payload and payload["status"] is not None:
        if payload["status"] not in _ALLOWED_STATUS:
            raise _error(
                400,
                "invalid_status",
                f"status must be one of {sorted(_ALLOWED_STATUS)}",
                field="status",
            )
        conn.status = payload["status"]
    if "credentials" in payload and payload["credentials"] is not None:
        if not isinstance(payload["credentials"], dict):
            raise _error(
                400, "invalid_credentials", "credentials must be an object", field="credentials"
            )
        base = dict(conn.credentials) if conn.credentials else {}
        base.update(payload["credentials"])
        conn.credentials = base
    if "location_ids" in payload and payload["location_ids"] is not None:
        lids = payload["location_ids"]
        if not isinstance(lids, list):
            raise _error(
                400, "invalid_location_ids", "location_ids must be an array", field="location_ids"
            )
        for lid in lids:
            try:
                uuid.UUID(str(lid))
            except ValueError:
                raise _error(
                    400, "invalid_location_ids", f"invalid uuid {lid}", field="location_ids"
                ) from None
        base = dict(conn.credentials) if conn.credentials else {}
        base["location_ids"] = [str(x) for x in lids]
        conn.credentials = base
    db.flush()
    created = getattr(conn, "created_at", datetime.datetime.now(datetime.UTC))
    return {
        "id": str(conn.id),
        "workspace_id": str(conn.workspace_id),
        "type": conn.type,
        "name": conn.name,
        "status": conn.status,
        "credentials": _mask_credentials(conn.credentials),
        "last_sync_at": conn.last_sync_at.isoformat() if conn.last_sync_at else None,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
    }


@router.delete("/{connector_id}", status_code=204)
def delete_connector(
    connector_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Remove a connector (admin)."""
    _require_admin(current_user)
    conn: Connector | None = db.get(Connector, connector_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Connector not found")
    # Cascade delete mappings.
    try:
        mappings = list(
            db.scalars(
                select(ConnectorMapping).where(ConnectorMapping.connector_id == conn.id)
            ).all()
        )
        for m in mappings:
            db.delete(m)
    except Exception:
        logger.warning(
            "could not delete connector mappings before deleting the connector; they may "
            "be left orphaned",
            exc_info=True,
        )
    db.delete(conn)
    db.flush()


@router.post("/{connector_id}/test", response_model=dict)
def test_connector(
    connector_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Validate a connector's configuration and mapping (admin).

    This runs every precondition a sync depends on and reports what it checked;
    it does not call the provider — the live pull belongs to the
    ``connector_sync`` job (``services/ingestion/connector_pull.py``).
    ``sample_payload`` is the canonical *schema* the connector must produce —
    with mapped source field names when a mapping exists — not an invented
    demand row.
    """
    _require_admin(current_user)
    conn: Connector | None = db.get(Connector, connector_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Connector not found")
    started = time.perf_counter()

    creds = conn.credentials if isinstance(conn.credentials, dict) else {}
    location_ids = creds.get("location_ids")
    try:
        mappings = [
            m
            for m in db.scalars(
                select(ConnectorMapping).where(ConnectorMapping.connector_id == conn.id)
            ).all()
            if isinstance(m, ConnectorMapping)
        ]
    except Exception:
        mappings = []
    canonical = {m.canonical_field for m in mappings}
    source_for = {m.canonical_field: m.source_field for m in mappings}
    missing = {"date", "demand_qty"} - canonical

    checks = [
        {"name": "type", "ok": conn.type in _ALLOWED_TYPES, "detail": f"type={conn.type}"},
        {
            "name": "name",
            "ok": bool(conn.name and conn.name.strip()),
            "detail": conn.name or "(empty)",
        },
        {
            "name": "credentials",
            "ok": bool(conn.credentials),
            "detail": "credentials stored" if conn.credentials else "no credentials configured",
        },
        {
            "name": "locations",
            "ok": bool(location_ids),
            "detail": (
                f"{len(location_ids)} location(s) linked" if location_ids else "no locations linked"
            ),
        },
        {
            "name": "mapping",
            "ok": not missing,
            "detail": (
                f"canonical fields {sorted(canonical)}"
                if canonical
                else "no field mapping configured"
            ),
        },
    ]
    ok = all(c["ok"] for c in checks)
    sample = {
        "date": f"<{source_for.get('date', 'YYYY-MM-DD')}>",
        "demand_qty": f"<{source_for.get('demand_qty', 'integer')}>",
        "location_id": f"<{source_for.get('location_id', 'uuid')}>",
        "notes": f"<{source_for.get('notes', 'string|null')}>",
    }
    return {
        "connector_id": str(conn.id),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "sample_payload": sample,
        "checks": checks,
        "status": "ok" if ok else "blocked",
        "checked_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


def _serialise_pull_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_id": str(row["location_id"]),
        "date": row["date"].isoformat(),
        "demand_qty": row["demand_qty"],
        "notes": row.get("notes"),
    }


@router.post("/{connector_id}/pull", response_model=dict)
def pull_connector_now(
    connector_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    """Pull one connector's provider on demand (admin).

    ``dry_run=true`` fetches and maps but writes nothing, returning the rows a
    real pull would commit — the safe way to check a mapping or a credential on
    a channel that is not active yet. A real pull requires an ``active``
    connector (matching ``connector_sync``, which only sweeps active channels)
    and records the outcome on the connector, so ``GET /sources`` reflects it.

    This is deliberately *not* the scheduled sweep: it pulls the one connector
    named and writes no ``pipeline_runs`` row. It exists so an operator can
    verify a channel end to end without waiting for a worker tick.
    """
    _require_admin(current_user)
    conn: Connector | None = db.get(Connector, connector_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Connector not found")
    if not dry_run and conn.status != "active":
        raise _error(
            409,
            "not_active",
            f"Connector '{conn.name}' is {conn.status}, not active. Activate it, or "
            "use a dry run to preview the pull.",
        )
    if connector_pull.endpoint_of(conn) is None:
        raise _error(
            409,
            "not_configured",
            "This connector has no pull endpoint. Add one in its credentials before pulling.",
        )
    started_at = datetime.datetime.now(datetime.UTC)
    try:
        outcome = connector_pull.pull_connector(db, conn, dry_run=dry_run)
    except httpx.HTTPError as exc:
        raise _error(502, "provider_unavailable", f"Provider request failed: {exc}") from exc

    run_id: str | None = None
    if not dry_run:
        from app.jobs.runner import record_manual_run

        now = datetime.datetime.now(datetime.UTC)
        conn.last_sync_at = now
        conn.last_pull_pulled = outcome.pulled
        conn.last_pull_committed = outcome.committed
        conn.last_pull_skipped = outcome.skipped
        conn.last_pull_detail = outcome.persisted_detail
        # A manual pull must also reach the run views (`GET /pipeline/runs` and
        # the Sources history read one table), so record it like any other run.
        run = record_manual_run(
            db,
            workspace_id=conn.workspace_id,
            job_type="connector_sync",
            detail=f"manual pull {conn.name}: {outcome.detail}",
            rows_processed=outcome.committed,
            started_at=started_at,
        )
        run_id = str(run.id)
        db.flush()

    return {
        "connector_id": str(conn.id),
        "dry_run": dry_run,
        "status": "preview" if dry_run else "pulled",
        "pulled": outcome.pulled,
        "committed": outcome.committed,
        "skipped": outcome.skipped,
        "rows": [_serialise_pull_row(r) for r in outcome.rows[:_PULL_PREVIEW_LIMIT]],
        "errors": list(outcome.errors),
        "detail": outcome.detail,
        "run_id": run_id,
    }


@router.get("/{connector_id}/mapping", response_model=dict)
def get_mapping(
    connector_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Get field mapping to canonical schema (admin)."""
    _require_admin(current_user)
    conn: Connector | None = db.get(Connector, connector_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Connector not found")
    try:
        rows = list(
            db.scalars(
                select(ConnectorMapping).where(ConnectorMapping.connector_id == conn.id)
            ).all()
        )
    except Exception:
        rows = []
    return {
        "connector_id": str(conn.id),
        "mappings": [
            {
                "id": str(r.id),
                "source_field": r.source_field,
                "canonical_field": r.canonical_field,
                "transform": r.transform,
            }
            for r in rows
        ],
    }


@router.patch("/{connector_id}/mapping", response_model=dict)
def patch_mapping(
    connector_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Patch field mapping — full replacement (admin)."""
    _require_admin(current_user)
    conn: Connector | None = db.get(Connector, connector_id)
    if conn is None or conn.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Connector not found")
    mappings_in = payload.get("mappings")
    if mappings_in is None or not isinstance(mappings_in, list):
        raise _error(400, "validation_error", "mappings must be an array", field="mappings")
    # Validate each.
    for idx, item in enumerate(mappings_in):
        if not isinstance(item, dict):
            raise _error(
                400, "validation_error", f"mappings[{idx}] must be an object", field="mappings"
            )
        sf = item.get("source_field")
        cf = item.get("canonical_field")
        if not sf or not str(sf).strip():
            raise _error(
                400,
                "validation_error",
                f"mappings[{idx}].source_field is required",
                field="mappings",
            )
        if cf not in _ALLOWED_CANONICAL:
            raise _error(
                400,
                "invalid_canonical",
                f"canonical_field must be one of {sorted(_ALLOWED_CANONICAL)}",
                field="mappings",
            )
    # Replace: delete existing, insert new — one unit of work. A failure to clear
    # the old rows must abort the request, not be logged and stepped over: that is
    # how the mapping accumulates duplicates. Letting it raise rolls the session
    # back (see `get_db`), so the mapping is either fully replaced or untouched.
    existing = list(
        db.scalars(select(ConnectorMapping).where(ConnectorMapping.connector_id == conn.id)).all()
    )
    for e in existing:
        db.delete(e)
    db.flush()
    new_rows: list[ConnectorMapping] = []
    for item in mappings_in:
        row = ConnectorMapping(
            id=uuid.uuid4(),
            connector_id=conn.id,
            source_field=str(item["source_field"]).strip(),
            canonical_field=str(item["canonical_field"]).strip(),
            transform=item.get("transform"),
        )
        db.add(row)
        new_rows.append(row)
    db.flush()
    return {
        "connector_id": str(conn.id),
        "mappings": [
            {
                "id": str(r.id),
                "source_field": r.source_field,
                "canonical_field": r.canonical_field,
                "transform": r.transform,
            }
            for r in new_rows
        ],
    }
