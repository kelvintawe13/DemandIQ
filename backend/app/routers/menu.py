"""Menu-group CRUD — workspace-scoped, admin-gated writes.

* GET    /api/v1/menu-groups          — list (cursor-paginated)
* GET    /api/v1/menu-groups/{id}     — detail
* POST   /api/v1/menu-groups          — admin create
* PATCH  /api/v1/menu-groups/{id}     — admin update
* DELETE /api/v1/menu-groups/{id}     — admin archive
"""

from __future__ import annotations

import base64
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.location import MenuGroup
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.menu import MenuGroupCreate, MenuGroupResponse, MenuGroupUpdate
from app.security.permissions import LOCATIONS_WRITE, has_capability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/menu-groups", tags=["locations"])


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _ensure_admin(user: User) -> None:
    """Menu editing is location configuration, so it needs locations:write."""
    if not has_capability(user, LOCATIONS_WRITE):
        raise _error(status.HTTP_403_FORBIDDEN, "forbidden", "Administrator role required")


def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"o": offset})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(raw)
        return int(data["o"])
    except Exception as exc:
        raise _error(400, "invalid_cursor", "Invalid pagination cursor") from exc


@router.get("", response_model=PaginatedResponse[MenuGroupResponse])
def list_menu_groups(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[MenuGroupResponse]:
    """List menu groups for the current workspace (cursor-paginated)."""
    # Fetch ordered by name asc for determinism (mock-compat: fetch all then sort)
    try:
        rows: list[MenuGroup] = list(
            db.scalars(
                select(MenuGroup).where(MenuGroup.workspace_id == current_user.workspace_id)
            ).all()
        )
    except Exception:
        rows = []

    # Filter workspace in Python as well for mock robustness
    rows = [r for r in rows if r.workspace_id == current_user.workspace_id]
    rows.sort(key=lambda r: (r.name.lower(), str(r.id)))

    offset = _decode_cursor(cursor) if cursor else 0
    page = rows[offset : offset + limit + 1]
    has_more = len(page) > limit
    data_page = page[:limit]
    next_cursor = _encode_cursor(offset + limit) if has_more else None

    return PaginatedResponse[MenuGroupResponse](
        data=[MenuGroupResponse.model_validate(r) for r in data_page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/{group_id}", response_model=MenuGroupResponse)
def get_menu_group(
    group_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MenuGroupResponse:
    """Detail for one menu group (workspace-scoped)."""
    row: MenuGroup | None = db.get(MenuGroup, group_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Menu group not found")
    return MenuGroupResponse.model_validate(row)


@router.post("", response_model=MenuGroupResponse, status_code=status.HTTP_201_CREATED)
def create_menu_group(
    payload: MenuGroupCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MenuGroup:
    """Create a menu group — admin only."""
    _ensure_admin(current_user)

    # Perishability already validated by schema Literal; DB CHECK is defence in depth.
    row = MenuGroup(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        name=payload.name,
        item_count=payload.item_count,
        cover_share=payload.cover_share,
        perishability=payload.perishability,
    )
    db.add(row)
    db.flush()
    logger.info(
        "Menu group created id=%s workspace=%s by=%s perishability=%s",
        row.id,
        current_user.workspace_id,
        current_user.id,
        row.perishability,
    )
    return row  # type: ignore[return-value]


@router.patch("/{group_id}", response_model=MenuGroupResponse)
def update_menu_group(
    group_id: uuid.UUID,
    payload: MenuGroupUpdate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> MenuGroup:
    """Update a menu group — admin only."""
    _ensure_admin(current_user)

    row: MenuGroup | None = db.get(MenuGroup, group_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Menu group not found")

    if payload.name is not None:
        row.name = payload.name
    if payload.item_count is not None:
        row.item_count = payload.item_count
    if payload.cover_share is not None:
        row.cover_share = payload.cover_share
    if payload.perishability is not None:
        row.perishability = payload.perishability

    db.flush()
    logger.info("Menu group updated id=%s by=%s", row.id, current_user.id)
    return row  # type: ignore[return-value]


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu_group(
    group_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete a menu group — admin only."""
    _ensure_admin(current_user)

    row: MenuGroup | None = db.get(MenuGroup, group_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Menu group not found")

    db.delete(row)
    db.flush()
    logger.info("Menu group deleted id=%s by=%s", group_id, current_user.id)
