"""Location CRUD with access tracking.

Every read writes a ``location_access_logs`` row (via the service helper)
and the audit middleware also records privileged writes to ``audit_log``.
Workspace scoping is enforced on every query.
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.location import DemandObservation, Location
from app.models.location_access import LocationAccessLog
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.location import (
    LocationCreate,
    LocationDetailResponse,
    LocationListResponse,
    LocationResponse,
    LocationUpdate,
)
from app.security.api_key_auth import get_principal, require_write_scope
from app.security.permissions import LOCATIONS_WRITE, has_capability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])

# A location can be forecast once it has this many distinct days of demand on
# record. The registry in the app tells the operator "Nd to go", so the two
# have to agree on the bar.
FORECAST_MIN_HISTORY_DAYS = 7

# Simple in-memory idempotency guard for phase 1 (production uses Redis/DB).
_seen_keys: dict[str, uuid.UUID] = {}


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _client_from_request(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    # API clients typically send no browser UA or send an explicit header.
    if request.headers.get("x-client") in ("web", "mobile", "api"):
        return request.headers["x-client"]  # type: ignore[no-any-return]
    return "web"


def _log_access(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user: User | None,
    location_id: uuid.UUID | None,
    action: str,
    request: Request,
) -> None:
    """Persist a location access log row.

    API-key principals are not users (``user_id`` is FK-constrained to
    ``users.id``), so they are logged without user attribution — key usage
    is already tracked via ``api_keys.last_used_at``.
    """
    from app.security.api_key_auth import ApiKeyPrincipal

    is_api_key = isinstance(user, ApiKeyPrincipal)
    try:
        entry = LocationAccessLog(
            workspace_id=workspace_id,
            location_id=location_id,
            user_id=None if is_api_key else (user.id if user else None),
            action=action,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            client=_client_from_request(request),
        )
        db.add(entry)
        db.flush()
    except Exception as exc:  # pragma: no cover; audit must never break the request
        logger.warning("Failed to write location_access_log: %s", exc)


def _encode_cursor(created_at: datetime, loc_id: uuid.UUID) -> str:
    raw = json.dumps({"ts": created_at.isoformat(), "id": str(loc_id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(raw)
        return datetime.fromisoformat(data["ts"]), uuid.UUID(data["id"])
    except Exception as exc:
        raise _error(400, "invalid_cursor", "Invalid pagination cursor") from exc


def _history_stats_by_location(
    db: Session, workspace_id: uuid.UUID, location_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int, float | None]]:
    """Days of demand on record, days missing inside the span, mean covers.

    One grouped query for a whole page, so the list stays a single round
    trip. These are the numbers the registry renders as "History" and the
    reason it can say a location is not forecastable yet; without them the
    column, the gap badge and the "needs history" state were permanently
    empty. A read that fails leaves the fields unset, which the app treats
    as unknown rather than as "no history".
    """
    if not location_ids:
        return {}
    try:
        rows = db.execute(
            select(
                DemandObservation.location_id,
                func.count(func.distinct(DemandObservation.date)),
                func.min(DemandObservation.date),
                func.max(DemandObservation.date),
                func.avg(DemandObservation.demand_qty),
            )
            .where(
                DemandObservation.workspace_id == workspace_id,
                DemandObservation.location_id.in_(location_ids),
            )
            .group_by(DemandObservation.location_id)
        ).all()
    except Exception:
        logger.warning("could not count demand history per location", exc_info=True)
        return {}
    stats: dict[uuid.UUID, tuple[int, int, float | None]] = {}
    for location_id, days, first, last, mean_covers in rows:
        covered = int(days)
        span = (last - first).days + 1 if first is not None and last is not None else covered
        stats[location_id] = (
            covered,
            max(span - covered, 0),
            round(float(mean_covers), 2) if mean_covers is not None else None,
        )
    return stats


def _apply_history(item: LocationListResponse, stats: tuple[int, int, float | None] | None) -> None:
    """Copy the computed history fields onto a response row."""
    if stats is None:
        return
    history_days, gap_days, avg_covers = stats
    item.history_days = history_days
    item.gap_days = gap_days
    item.avg_covers = avg_covers
    item.forecastable = history_days >= FORECAST_MIN_HISTORY_DAYS


def _list_item(row: Location, stats: tuple[int, int, float | None] | None) -> LocationListResponse:
    """Full location read model plus the computed display fields the registry reads."""
    item = LocationListResponse.model_validate(row)
    _apply_history(item, stats)
    return item


@router.get("", response_model=PaginatedResponse[LocationListResponse])
def list_locations(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[LocationListResponse]:
    """List locations for the current workspace (cursor-paginated)."""
    q = (
        select(Location)
        .where(
            Location.workspace_id == current_user.workspace_id,
            Location.archived_at.is_(None),
        )
        .order_by(Location.created_at.desc(), Location.id.desc())
        .limit(limit + 1)
    )

    if cursor:
        ts, _last_id = _decode_cursor(cursor)
        # We page by (created_at, id) tuple; use created_at filter.
        q = (
            select(Location)
            .where(
                Location.workspace_id == current_user.workspace_id,
                Location.archived_at.is_(None),
            )
            .order_by(Location.created_at.desc(), Location.id.desc())
        )
        # Apply cursor: rows strictly before the cursor
        # For simplicity, fetch and filter in python if needed, but we do DB filter:
        # created_at < ts OR (created_at == ts AND id < last_id)
        # Implemented as two-phase: just use created_at < ts for phase 1.
        q = q.where(Location.created_at < ts).limit(limit + 1)

    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None

    _log_access(
        db,
        workspace_id=current_user.workspace_id,
        user=current_user,
        location_id=None,
        action="list",
        request=request,
    )

    history_stats = _history_stats_by_location(db, current_user.workspace_id, [r.id for r in page])

    return PaginatedResponse[LocationListResponse](
        data=[_list_item(r, history_stats.get(r.id)) for r in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post("", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
def create_location(
    payload: LocationCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Location:
    """Create a location — admin only, idempotent via ``Idempotency-Key``."""
    require_write_scope(current_user)
    if not has_capability(current_user, LOCATIONS_WRITE):
        raise _error(status.HTTP_403_FORBIDDEN, "forbidden", "Administrator role required")

    if idempotency_key:
        seen = _seen_keys.get(idempotency_key)
        if seen is not None:
            existing = db.get(Location, seen)
            if existing is not None:
                return existing  # type: ignore[return-value]

    # The id is ours, so the idempotency map is keyed off the same value we
    # assigned — no null to narrow away with an `assert` that `python -O` would
    # strip.
    loc_id = uuid.uuid4()
    loc = Location(
        id=loc_id,
        workspace_id=current_user.workspace_id,
        name=payload.name,
        area=payload.area,
        cuisine=payload.cuisine,
        opened_on=payload.opened_on,
        seating_capacity=payload.seating_capacity,
        service_type=payload.service_type,
        district=payload.district,
        open_days_mask=payload.open_days_mask,
        address=payload.address,
        phone=payload.phone,
        manager_name=payload.manager_name,
        notes=payload.notes,
        created_at=datetime.now(UTC),
    )
    db.add(loc)
    db.flush()

    if idempotency_key:
        _seen_keys[idempotency_key] = loc_id

    _log_access(
        db,
        workspace_id=current_user.workspace_id,
        user=current_user,
        location_id=loc.id,
        action="view",
        request=request,
    )
    logger.info(
        "Location created id=%s workspace=%s by=%s",
        loc.id,
        current_user.workspace_id,
        current_user.id,
    )
    return loc  # type: ignore[return-value]


@router.get("/{location_id}", response_model=LocationDetailResponse)
def get_location(
    location_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> LocationDetailResponse:
    """Detail for one location (workspace-scoped) with access tracking."""
    loc = db.get(Location, location_id)
    if loc is None or loc.workspace_id != current_user.workspace_id or loc.archived_at is not None:
        raise _error(404, "not_found", "Location not found")

    _log_access(
        db,
        workspace_id=current_user.workspace_id,
        user=current_user,
        location_id=loc.id,
        action="detail",
        request=request,
    )

    # Same computed history as the list, so one location's detail cannot report
    # "no history" while the registry shows 84 days for it. `data_gaps` stays
    # unset: the list reports the missing days as a count (`gap_days`).
    detail = LocationDetailResponse(
        id=loc.id,
        workspace_id=loc.workspace_id,
        name=loc.name,
        area=loc.area,
        cuisine=loc.cuisine,
        opened_on=loc.opened_on,
        archived_at=loc.archived_at,
        created_at=loc.created_at,
        data_gaps=None,
    )
    stats = _history_stats_by_location(db, current_user.workspace_id, [loc.id]).get(loc.id)
    if stats is not None:
        detail.history_days = stats[0]
        detail.avg_covers = stats[2]
    return detail


@router.patch("/{location_id}", response_model=LocationResponse)
def update_location(
    location_id: uuid.UUID,
    payload: LocationUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> Location:
    """Update a location — admin only."""
    require_write_scope(current_user)
    if not has_capability(current_user, LOCATIONS_WRITE):
        raise _error(status.HTTP_403_FORBIDDEN, "forbidden", "Administrator role required")

    loc = db.get(Location, location_id)
    if loc is None or loc.workspace_id != current_user.workspace_id or loc.archived_at is not None:
        raise _error(404, "not_found", "Location not found")

    if payload.name is not None:
        loc.name = payload.name
    if payload.area is not None:
        loc.area = payload.area
    if payload.cuisine is not None:
        loc.cuisine = payload.cuisine
    if payload.opened_on is not None:
        loc.opened_on = payload.opened_on
    if payload.seating_capacity is not None:
        loc.seating_capacity = payload.seating_capacity
    if payload.service_type is not None:
        loc.service_type = payload.service_type
    if payload.district is not None:
        loc.district = payload.district
    if payload.open_days_mask is not None:
        loc.open_days_mask = payload.open_days_mask
    if payload.address is not None:
        loc.address = payload.address
    if payload.phone is not None:
        loc.phone = payload.phone
    if payload.manager_name is not None:
        loc.manager_name = payload.manager_name
    if payload.notes is not None:
        loc.notes = payload.notes

    db.flush()
    _log_access(
        db,
        workspace_id=current_user.workspace_id,
        user=current_user,
        location_id=loc.id,
        action="view",
        request=request,
    )
    logger.info("Location updated id=%s by=%s", loc.id, current_user.id)
    return loc  # type: ignore[return-value]


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_location(
    location_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_principal)],
) -> None:
    """Archive (soft-delete) a location — admin only."""
    require_write_scope(current_user)
    if not has_capability(current_user, LOCATIONS_WRITE):
        raise _error(status.HTTP_403_FORBIDDEN, "forbidden", "Administrator role required")

    loc = db.get(Location, location_id)
    if loc is None or loc.workspace_id != current_user.workspace_id or loc.archived_at is not None:
        raise _error(404, "not_found", "Location not found")

    loc.archived_at = datetime.now(UTC)
    db.flush()
    _log_access(
        db,
        workspace_id=current_user.workspace_id,
        user=current_user,
        location_id=loc.id,
        action="view",
        request=request,
    )
    logger.info("Location archived id=%s by=%s", loc.id, current_user.id)
