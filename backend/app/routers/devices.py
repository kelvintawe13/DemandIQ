"""Device fleet admin router — enroll, inspect, rotate, revoke, heartbeat.

* GET    /api/v1/devices
* POST   /api/v1/devices
* GET    /api/v1/devices/{device_id}
* PATCH  /api/v1/devices/{device_id}
* POST   /api/v1/devices/{device_id}/rotate
* POST   /api/v1/devices/{device_id}/revoke
* POST   /api/v1/devices/{device_id}/quarantine
* POST   /api/v1/devices/{device_id}/restore
* POST   /api/v1/devices/{device_id}/heartbeat
* POST   /api/v1/devices/{device_id}/demand
* GET    /api/v1/devices/{device_id}/events

Ten of these eleven routes are operator-facing and take a JWT. The other two —
the heartbeat and the demand write — are the only ones a device may call, and
they authenticate with the device credential in ``X-Device-Key`` rather than with
a person. A route that accepts both would let a device's key stand in for a
human's session, which is the one thing the device layer must never allow; so
the split is at the route, not inside a dependency.

Two rules run through all of it:

* **A device credential is shown once and never again.** Only the bcrypt hash
  and the non-secret prefix are stored, so there is no code path that could
  return the secret a second time.
* **A device in another workspace is 404, never 403.** A 403 would confirm the
  row exists, which is precisely the existence oracle tenant isolation exists to
  withhold. ``_load_device`` is the only place a device is fetched, so this
  cannot be forgotten per-route.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.device import (
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_QUARANTINED,
    DEVICE_STATUS_REVOKED,
    Device,
    DeviceEvent,
)
from app.models.location import Location
from app.models.user import User
from app.security.device_auth import (
    DevicePrincipal,
    generate_credential,
    get_device_principal,
    require_device_scope,
)
from app.security.hashing import hash_password
from app.security.permissions import (
    DEVICE_SCOPES,
    DEVICE_SYNC,
    DEVICES_MANAGE,
    DEVICES_READ,
    has_capability,
)
from app.security.rate_limit import rate_limit_dependency
from app.services.ingestion.channels import channel_for_source, record_import
from app.services.ingestion.normaliser import NormaliserError, commit_demand

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

logger = logging.getLogger(__name__)

_DEVICE_STATUSES = (DEVICE_STATUS_ACTIVE, DEVICE_STATUS_REVOKED, DEVICE_STATUS_QUARANTINED)

#: Ceiling on ``GET /events``. The fleet console reads a recent window; an
#: unbounded one would let a single request walk a table that is append-only by
#: design and therefore never short of rows.
MAX_EVENT_LIMIT = 200

#: Telemetry keys a heartbeat may carry. ``device_events`` is the
#: highest-volume table in the system and its payload is a JSONB blob written by a
#: remote device, so what it is allowed to contain is decided here rather than
#: trusted: an allowlist bounds the row exactly.
_TELEMETRY_KEYS = frozenset(
    {"app_version", "battery_pct", "battery_level", "network", "network_type", "os"}
)
#: Belt-and-braces ceiling on the allowlist itself, so widening the list above
#: cannot silently widen every stored heartbeat.
_MAX_TELEMETRY_KEYS = 12


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_read(user: User) -> None:
    if not has_capability(user, DEVICES_READ):
        raise _error(403, "forbidden", "Reading the device fleet requires devices:read")


def _require_manage(user: User) -> None:
    if not has_capability(user, DEVICES_MANAGE):
        raise _error(403, "forbidden", "Managing the device fleet requires devices:manage")


def _load_device(db: Session, device_id: uuid.UUID, workspace_id: uuid.UUID) -> Device:
    """Fetch a device inside the caller's workspace, or 404.

    Single funnel on purpose: tenant isolation that lives in each handler is
    isolation that one of them will eventually forget.
    """
    row = db.get(Device, device_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "not_found", "Device not found")
    return row


def _check_location(db: Session, location_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    """Refuse a location that is not in the caller's workspace.

    A device bound to somebody else's location would inherit that location's
    demand data the next time a sync runs, so the binding is checked at the
    moment it is set rather than trusted. Unknown and foreign ids get the same
    answer, so this is not a probe for which location ids exist.
    """
    location = db.get(Location, location_id)
    if location is None or location.workspace_id != workspace_id:
        raise _error(
            400,
            "invalid_location",
            "location_id does not belong to this workspace",
            field="location_id",
        )


def _parse_location(payload: dict[str, Any]) -> uuid.UUID | None:
    raw = payload.get("location_id")
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise _error(
            400, "validation_error", "location_id must be a UUID", field="location_id"
        ) from exc


def _telemetry(payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce a heartbeat body to the allowed scalar telemetry keys.

    Scalars only — a nested blob from a device is not telemetry, and it would be
    stored verbatim — so a device that mis-packages or misbehaves cannot grow the
    fleet console's event reads one request at a time.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if len(out) >= _MAX_TELEMETRY_KEYS:
            break
        if key not in _TELEMETRY_KEYS:
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            out[key] = value
    return out


class DeviceDemandRow(BaseModel):
    """One demand row as a device reports it.

    Deliberately has no ``location_id`` and no ``workspace_id``. Those are not
    optional here, they are absent: the route fills both from the credential and
    the device's own binding, so there is no field for a caller to point
    somewhere else.
    """

    date: datetime.date
    demand_qty: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=500)


def _serialise(row: Device, include_secret: str | None = None) -> dict[str, Any]:
    """Response shape for a device.

    ``credential_prefix`` is published deliberately: it is the non-secret half,
    the console needs it to tell two rotated credentials apart, and it grants
    nothing. ``credential_hash`` has no branch here at all — the secret exists in
    exactly one response, the one that mints it.
    """
    base: dict[str, Any] = {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "label": row.label,
        "device_type": getattr(row, "device_type", None),
        "platform": getattr(row, "platform", None),
        "app_version": row.app_version,
        "location_id": str(row.location_id) if row.location_id else None,
        "credential_prefix": row.credential_prefix,
        "scopes": list(row.scopes or []),
        "status": row.status,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "enrolled_at": row.enrolled_at.isoformat() if row.enrolled_at else None,
        "enrolled_by": str(row.enrolled_by) if row.enrolled_by else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "mirroring_consent_at": (
            row.mirroring_consent_at.isoformat() if row.mirroring_consent_at else None
        ),
    }
    if include_secret is not None:
        base["secret"] = include_secret
        base["warning"] = "Store this secret securely. It will not be shown again"
    return base


@router.get("", response_model=dict)
def list_devices(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    device_status: Annotated[str | None, Query(alias="status")] = None,
    location_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """List the caller's fleet — label, status, last_seen, app_version."""
    _require_read(current_user)
    if device_status is not None and device_status not in _DEVICE_STATUSES:
        raise _error(
            400,
            "validation_error",
            "status must be one of active, revoked, quarantined",
            field="status",
        )

    stmt = select(Device).where(Device.workspace_id == current_user.workspace_id)
    if device_status is not None:
        stmt = stmt.where(Device.status == device_status)
    if location_id is not None:
        stmt = stmt.where(Device.location_id == location_id)
    rows = list(db.scalars(stmt.order_by(Device.label)).all())
    return {"data": [_serialise(row) for row in rows]}


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
def enroll_device(
    payload: dict[str, Any],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Enroll a device — the credential is returned exactly once, here."""
    _require_manage(current_user)

    label = payload.get("label")
    if not label or not str(label).strip():
        raise _error(400, "validation_error", "label is required", field="label")

    scopes = payload.get("scopes")
    if scopes is None:
        # The registry defines "what a device may hold on its own row", so
        # enrollment defaults to exactly that; a caller who wants narrower says
        # so explicitly.
        scopes = sorted(DEVICE_SCOPES)
    if not isinstance(scopes, list) or not scopes:
        raise _error(400, "validation_error", "scopes must be a non-empty array", field="scopes")
    for scope in scopes:
        if not isinstance(scope, str) or scope not in DEVICE_SCOPES:
            # Refused rather than filtered. A typo that survived enrollment
            # would produce a device whose console shows a permission and whose
            # sync endpoint refuses it — the most expensive kind of silence.
            raise _error(
                400,
                "invalid_scope",
                f"scope must be one of: {', '.join(sorted(DEVICE_SCOPES))}",
                field="scopes",
            )

    device_location = _parse_location(payload)
    if device_location is not None:
        _check_location(db, device_location, current_user.workspace_id)

    prefix, secret, presented = generate_credential()
    now = datetime.datetime.now(datetime.UTC)
    row = Device(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        location_id=device_location,
        label=str(label).strip(),
        device_type=str(payload.get("device_type") or "pos"),
        platform=str(payload.get("platform") or "unknown"),
        app_version=None,
        # Stamped client-side because the enrollment response reports it; the
        # column's server default only covers writes that skip this router.
        enrolled_at=now,
        enrolled_by=current_user.id,
        credential_prefix=prefix,
        credential_hash=hash_password(secret),
        scopes=[str(scope) for scope in scopes],
        status=DEVICE_STATUS_ACTIVE,
        last_seen_at=None,
        revoked_at=None,
    )
    db.add(row)
    db.flush()
    logger.info(
        "Device enrolled id=%s workspace=%s by=%s", row.id, row.workspace_id, current_user.id
    )
    return _serialise(row, include_secret=presented)


@router.get("/{device_id}", response_model=dict)
def get_device(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """One device with its base scopes (no credential material)."""
    _require_read(current_user)
    return {"data": _serialise(_load_device(db, device_id, current_user.workspace_id))}


@router.patch("/{device_id}", response_model=dict)
def update_device(
    device_id: uuid.UUID,
    payload: dict[str, Any],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Rename a device, rebind it to a location, or record its app version."""
    _require_manage(current_user)
    row = _load_device(db, device_id, current_user.workspace_id)

    if "label" in payload:
        label = payload["label"]
        if not label or not str(label).strip():
            raise _error(400, "validation_error", "label cannot be empty", field="label")
        row.label = str(label).strip()
    # An explicit null clears the binding, which is why this is a presence check
    # rather than an ``is not None`` test.
    if "location_id" in payload:
        device_location = _parse_location(payload)
        if device_location is not None:
            _check_location(db, device_location, current_user.workspace_id)
        row.location_id = device_location
    if "app_version" in payload:
        version = payload["app_version"]
        row.app_version = str(version) if version else None

    db.flush()
    return {"data": _serialise(row)}


@router.post("/{device_id}/rotate", response_model=dict)
def rotate_device(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Replace the credential — the previous secret stops working immediately."""
    _require_manage(current_user)
    row = _load_device(db, device_id, current_user.workspace_id)
    if row.status == DEVICE_STATUS_REVOKED:
        # Rotation restores a working credential, so allowing it on a revoked
        # device would undo a revocation with the click of a "rotate" button.
        # A revoked device is re-enrolled, not revived.
        raise _error(409, "device_revoked", "A revoked device cannot be rotated")

    prefix, secret, presented = generate_credential()
    row.credential_prefix = prefix
    row.credential_hash = hash_password(secret)
    # ``last_seen_at`` deliberately survives: it is fleet health, not credential
    # state, and clearing it would make a healthy device read as never-seen.
    db.flush()
    logger.info("Device credential rotated id=%s by=%s", row.id, current_user.id)
    return {"data": _serialise(row, include_secret=presented)}


@router.post("/{device_id}/revoke", response_model=dict)
def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Revoke a device — immediate for authentication, terminal for the row.

    The row is kept because audit entries must still name a real subject, and
    ``revoked_at`` keeps its first value so "when was it lost" survives a
    repeated click.
    """
    _require_manage(current_user)
    row = _load_device(db, device_id, current_user.workspace_id)
    if row.status != DEVICE_STATUS_REVOKED:
        row.status = DEVICE_STATUS_REVOKED
        row.revoked_at = datetime.datetime.now(datetime.UTC)
        db.flush()
        logger.info("Device revoked id=%s by=%s", row.id, current_user.id)
    return {"data": _serialise(row)}


@router.post("/{device_id}/quarantine", response_model=dict)
def quarantine_device(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Stop a device without destroying it — the reversible half of revocation."""
    _require_manage(current_user)
    row = _load_device(db, device_id, current_user.workspace_id)
    if row.status == DEVICE_STATUS_REVOKED:
        raise _error(409, "device_revoked", "A revoked device cannot be quarantined")
    if row.status == DEVICE_STATUS_QUARANTINED:
        raise _error(409, "already_quarantined", "This device is already quarantined")
    row.status = DEVICE_STATUS_QUARANTINED
    db.flush()
    logger.info("Device quarantined id=%s by=%s", row.id, current_user.id)
    return {"data": _serialise(row)}


@router.post("/{device_id}/restore", response_model=dict)
def restore_device(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Bring a quarantined device back into service."""
    _require_manage(current_user)
    row = _load_device(db, device_id, current_user.workspace_id)
    if row.status == DEVICE_STATUS_REVOKED:
        # Restoration is what makes quarantine reversible; letting it reverse a
        # revocation would give the pair a single combined meaning.
        raise _error(409, "device_revoked", "A revoked device cannot be restored")
    if row.status == DEVICE_STATUS_ACTIVE:
        raise _error(409, "not_quarantined", "This device is not quarantined")
    row.status = DEVICE_STATUS_ACTIVE
    db.flush()
    logger.info("Device restored id=%s by=%s", row.id, current_user.id)
    return {"data": _serialise(row)}


@router.post(
    "/{device_id}/heartbeat",
    response_model=dict,
    # A device credential is bcrypt-guarded, but this is the one endpoint an
    # unauthenticated caller can hammer, and every attempt costs a hash. The
    # limiter keys on the peer address the ASGI server saw rather than a header
    # the caller picks, and is set well above any sane heartbeat cadence so a
    # whole restaurant's tills behind one NAT are never throttled by it.
    dependencies=[Depends(rate_limit_dependency(300, 60, "device_heartbeat"))],
)
def heartbeat(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[DevicePrincipal, Depends(get_device_principal)],
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """Record liveness from the device itself — device-authenticated, not JWT.

    The device may only report on itself: a path naming another device is a 404
    even though the caller is authenticated, because the credential already
    named the device and the path is not allowed to disagree with it.
    """
    if device_id != principal.device_id:
        raise _error(404, "not_found", "Device not found")
    row = _load_device(db, principal.device_id, principal.workspace_id)

    body = payload or {}
    now = datetime.datetime.now(datetime.UTC)
    version = body.get("app_version")
    if isinstance(version, str) and version.strip():
        row.app_version = version.strip()
    row.last_seen_at = now

    event = DeviceEvent(
        workspace_id=row.workspace_id,
        device_id=row.id,
        event_type="heartbeat",
        payload=_telemetry(body),
        recorded_at=now,
    )
    db.add(event)
    db.flush()

    # Hand the audit middleware something to attribute this write to. It reads
    # the JWT for a person, and a heartbeat has no JWT — the caller is a device,
    # not a user — so without this the row would be written with no actor and no
    # workspace and the middleware would skip it entirely. A device-authenticated
    # POST is still a privileged write against a customer's location and belongs
    # in the trail. ``actor_id`` stays NULL (a device is not a user, and the
    # column is an FK to ``users``); the identity goes in ``meta_data``.
    request.state.workspace_id = row.workspace_id  # type: ignore[attr-defined]
    request.state.device_id = row.id  # type: ignore[attr-defined]

    return {
        "data": {
            "device": _serialise(row),
            "event": {
                "id": str(event.id) if event.id is not None else None,
                "event_type": event.event_type,
                "payload": event.payload,
                "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
            },
        }
    }


@router.post(
    "/{device_id}/demand",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    # Same reasoning as the heartbeat: unauthenticated callers can reach this,
    # and every attempt costs a bcrypt hash.
    dependencies=[Depends(rate_limit_dependency(300, 60, "device_demand"))],
)
def device_demand(
    device_id: uuid.UUID,
    payload: Annotated[list[DeviceDemandRow], Body()],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[DevicePrincipal, Depends(get_device_principal)],
) -> dict[str, Any]:
    """Persist demand reported by the device itself — device-authenticated.

    The WebSocket already lets a device *announce* demand, and that message
    reaches subscribed screens. It is fan-out only: nothing is written, so a
    till that only ever published over the socket would leave no record behind
    and the next reconciliation would overwrite it. This is the durable half of
    the same round trip.

    Four rules, all of them about not letting a key become a licence to write
    anywhere:

    * **The credential names the device.** A path naming another device is a
      404, exactly as on the heartbeat.
    * **The tenant comes from the credential**, never from the body, so there is
      no field a caller could aim at another workspace.
    * **The location comes from the device's own binding**, never from the
      body. A device is enrolled against one location, and taking ``location_id``
      from the request would let a compromised till write into a sibling
      location's forecast.
    * **An unbound device is refused** rather than guessed at. A device with no
      location has no correct answer for where its numbers belong, and picking
      one silently would corrupt whichever was chosen.
    """
    if device_id != principal.device_id:
        raise _error(404, "not_found", "Device not found")
    require_device_scope(principal, DEVICE_SYNC)

    row = _load_device(db, principal.device_id, principal.workspace_id)
    if row.location_id is None:
        raise _error(
            409,
            "device_unbound",
            "Device is not bound to a location, so its demand has nowhere to go",
        )

    committed = 0
    reconciled = 0
    errors: list[dict[str, Any]] = []
    # Till pushes are a channel too. Without one, demand that arrived from the
    # tills was unattributable and appeared on Sources as nothing at all.
    channel = channel_for_source(db, principal.workspace_id, "device")

    for idx, item in enumerate(payload):
        try:
            # ``commit_demand`` is the only writer of demand observations, so
            # this path inherits its idempotency, its last-write-wins
            # reconciliation and its realtime publication unchanged. Writing
            # anything here directly would give devices a second, divergent set
            # of rules.
            result = commit_demand(
                db,
                workspace_id=principal.workspace_id,
                location_id=row.location_id,
                date=item.date,
                demand_qty=item.demand_qty,
                source="device",
                source_id=channel.id if channel is not None else None,
                notes=item.notes,
            )
            if result.created:
                committed += 1
            else:
                reconciled += 1
        except NormaliserError as exc:
            errors.append({"index": idx, "code": exc.code, "message": str(exc)})

    # Attribute the write for the audit trail, as the heartbeat does: no user
    # actor, because the caller is a device, but still a privileged write
    # against a customer's forecast.
    request.state.workspace_id = principal.workspace_id  # type: ignore[attr-defined]
    request.state.device_id = row.id  # type: ignore[attr-defined]

    if channel is not None:
        record_import(
            db,
            channel,
            committed=committed,
            updated=reconciled,
            skipped=len(errors),
            detail=f"device {row.id}: {committed} new, {reconciled} corrected",
        )

    return {
        "data": {
            "committed": committed,
            "reconciled": reconciled,
            "errors": errors,
        }
    }


@router.get("/{device_id}/events", response_model=dict)
def list_device_events(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=MAX_EVENT_LIMIT)] = 50,
) -> dict[str, Any]:
    """Recent events for one device, newest first."""
    _require_read(current_user)
    _load_device(db, device_id, current_user.workspace_id)
    rows = db.scalars(
        select(DeviceEvent)
        .where(
            DeviceEvent.workspace_id == current_user.workspace_id,
            DeviceEvent.device_id == device_id,
        )
        .order_by(DeviceEvent.recorded_at.desc())
        .limit(limit)
    ).all()
    return {
        "data": [
            {
                "id": str(row.id) if row.id is not None else None,
                "device_id": str(row.device_id),
                "event_type": row.event_type,
                "payload": row.payload,
                "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
            }
            for row in rows
        ]
    }
