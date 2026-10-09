"""Per-device grant router — grant, revoke and explain one device's capabilities.

* GET    /api/v1/device-grants/devices/{device_id}/grants
* POST   /api/v1/device-grants/devices/{device_id}/grants
* DELETE /api/v1/device-grants/devices/{device_id}/grants/{grant_id}
* GET    /api/v1/device-grants/devices/{device_id}/effective-scopes

Operator-facing and JWT-only. Not one of these routes accepts a device
credential: a device asking "what may I do?" is asking a question whose answer
would let it pick a capability to try, so the resolution happens server-side
where a human is asking about it.

The two powers are kept apart. Reading what a device holds, and asking *why* a
device cannot do something, is ``devices:read`` — the same capability that lists
the fleet, because an operator debugging a till already has it. Handing a device
a capability it did not have is ``device_grants:manage``, a distinct power held
by the same roles, and not implied by the read.

Two rules run through the router:

* **A device in another workspace is 404, never 403**, on every route, and so is
  a grant id belonging to another device. A 403 confirms the row exists, which is
  the existence oracle tenant isolation exists to withhold.
* **A refusal of a *write* is a statement about the request, not about the
  tenant**: "this capability does not exist" and "a reason is required" are
  answers the caller could not have known, so they are 400s naming the field.
  That is the opposite of the read rules, and the split is deliberate — a
  validation error teaches the operator, an existence answer teaches an attacker.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.device import Device
from app.models.user import User
from app.security.permissions import DEVICE_GRANTS_MANAGE, DEVICES_READ, has_capability
from app.services.device_grants import (
    DeviceGrantError,
    GrantView,
    describe_grants,
    effective_scopes_report,
    grant_capability,
    revoke_grant,
)

router = APIRouter(prefix="/api/v1/device-grants", tags=["devices"])

logger = logging.getLogger(__name__)


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_read(user: User) -> None:
    if not has_capability(user, DEVICES_READ):
        raise _error(403, "forbidden", "Reading the device fleet requires devices:read")


def _require_grants_manage(user: User) -> None:
    if not has_capability(user, DEVICE_GRANTS_MANAGE):
        raise _error(
            403,
            "forbidden",
            "Granting device capabilities requires device_grants:manage",
        )


def _load_device(db: Session, device_id: uuid.UUID, workspace_id: uuid.UUID) -> Device:
    """Fetch a device inside the caller's workspace, or 404.

    The same funnel the device router uses, written out again rather than
    imported from it: a private helper in another router is not an interface, and
    reaching across module boundaries for one would make the two routers fail
    together when either changed.
    """
    row = db.get(Device, device_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "not_found", "Device not found")
    return row


def _parse_expiry(raw: object) -> datetime.datetime | None:
    """Read an ``expires_at`` from the request body, or refuse it.

    An offset is mandatory. A naive timestamp is either UTC or local time and the
    server cannot tell which, so accepting it would mean guessing when a
    permission lapses — and a permission that lapses a day late is a permission
    nobody revoked.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime.datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.datetime.fromisoformat(raw)
        except ValueError as exc:
            raise _error(
                400,
                "validation_error",
                "expires_at must be an ISO 8601 timestamp with a UTC offset",
                field="expires_at",
            ) from exc
    else:
        raise _error(400, "validation_error", "expires_at must be a timestamp", field="expires_at")
    if parsed.tzinfo is None:
        raise _error(
            400,
            "expiry_requires_timezone",
            "expires_at must carry a UTC offset (e.g. 2026-01-31T18:00:00+00:00)",
            field="expires_at",
        )
    return parsed


def _serialise_grant(view: GrantView) -> dict[str, Any]:
    """Publish a grant with the liveness resolved and the reason for it.

    ``state``, ``effective`` and ``detail`` travel together because a boolean
    alone is what makes this table unreadable: "is this grant on?" invites a
    second request to find out why, whereas one sentence answers it.
    """
    return {
        "id": str(view.id),
        "device_id": str(view.device_id),
        "capability": view.capability,
        "granted_by": str(view.granted_by),
        "granted_at": view.granted_at.isoformat() if view.granted_at else None,
        "expires_at": view.expires_at.isoformat() if view.expires_at else None,
        "revoked_at": view.revoked_at.isoformat() if view.revoked_at else None,
        "revoked_by": str(view.revoked_by) if view.revoked_by else None,
        "reason": view.reason,
        "state": view.state,
        "effective": view.effective,
        "detail": view.detail,
    }


@router.get("/devices/{device_id}/grants", response_model=dict)
def list_device_grants(
    device_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Every grant on a device, live or not, each annotated with why it is not."""
    _require_read(current_user)
    device = _load_device(db, device_id, current_user.workspace_id)
    views = describe_grants(db, device)
    return {"data": [_serialise_grant(view) for view in views]}


@router.post(
    "/devices/{device_id}/grants", response_model=dict, status_code=status.HTTP_201_CREATED
)
def grant_device_capability(
    device_id: uuid.UUID,
    payload: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Grant one capability to a device, optionally time-boxed by ``expires_at``.

    The response carries the device's effective scopes as well as the new grant,
    because the operator who presses this button is the one who needs to see what
    the device can now do — and a second round trip is where a stale console
    comes from.
    """
    _require_grants_manage(current_user)
    device = _load_device(db, device_id, current_user.workspace_id)
    capability = payload.get("capability")
    if not isinstance(capability, str):
        # A non-string is simply not a registered capability, so it is handed to
        # the service as one unknown value and gets the same refusal as a typo —
        # one code path, and no request reaches the database unvalidated.
        capability = ""
    reason = payload.get("reason")
    expires_at = _parse_expiry(payload.get("expires_at"))
    try:
        view = grant_capability(
            db,
            device=device,
            capability=capability,
            granted_by=current_user.id,
            reason=reason if isinstance(reason, str) else None,
            expires_at=expires_at,
        )
    except DeviceGrantError as exc:
        raise _error(exc.status_code, exc.code, str(exc), exc.field) from exc
    return {
        "data": {
            "grant": _serialise_grant(view),
            "effective_scopes": effective_scopes_report(db, device)["effective"],
        }
    }


@router.delete("/devices/{device_id}/grants/{grant_id}", response_model=dict)
def revoke_device_grant(
    device_id: uuid.UUID,
    grant_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Revoke a grant. The row survives — it is the record of the withdrawal.

    Idempotent: a repeated call returns the same grant with the original
    ``revoked_at`` rather than moving the timestamp of the decision.
    """
    _require_grants_manage(current_user)
    device = _load_device(db, device_id, current_user.workspace_id)
    try:
        view = revoke_grant(db, device=device, grant_id=grant_id, revoked_by=current_user.id)
    except DeviceGrantError as exc:
        raise _error(exc.status_code, exc.code, str(exc), exc.field) from exc
    return {
        "data": {
            "grant": _serialise_grant(view),
            "effective_scopes": effective_scopes_report(db, device)["effective"],
        }
    }


@router.get("/devices/{device_id}/effective-scopes", response_model=dict)
def device_effective_scopes(
    device_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """What this device may do right now, and what it may not do and why.

    The endpoint an operator reads when a till cannot do something: each
    effective capability is attributed to its base scope or to a named grant, and
    every capability in the registry that the device lacks is listed with the
    nearest reason it is missing.
    """
    _require_read(current_user)
    device = _load_device(db, device_id, current_user.workspace_id)
    return {"data": effective_scopes_report(db, device)}
