"""Consent-gated screen mirroring router — the two-person, four-gate API.

* POST /api/v1/device-mirror/devices/{device_id}/mirror-requests
* GET  /api/v1/device-mirror/devices/{device_id}/mirror-requests
* GET  /api/v1/device-mirror/devices/{device_id}/mirror-eligibility
* POST /api/v1/device-mirror/devices/{device_id}/mirror-consent
* GET  /api/v1/device-mirror/mirror-requests/pending
* POST /api/v1/device-mirror/mirror-requests/{session_id}/approve
* POST /api/v1/device-mirror/mirror-requests/{session_id}/deny
* POST /api/v1/device-mirror/mirror-requests/{session_id}/end

Every one of these routes exists to read a customer's device screen while
someone works on it, and every one of them is therefore gated on consent
rather than on a permission alone. The reasoning, and the four gates
themselves, are in ``app/services/mirroring.py`` — read that before changing
anything here, because the shape of these endpoints is the consent model.

Three decisions run through the whole file:

* **Requesting and approving are different capabilities**
  (``device_mirror:request`` / ``device_mirror:approve``) and the administrator
  role holds only the first. The ordinary admin support path — an engineer
  diagnosing a customer's till — therefore *cannot* approve the request it just
  made; it has to find a second person. That is the point of the split, not an
  inconvenience to be routed around.
* **A device or session in another workspace is 404, never 403.** A 403 would
  confirm the row exists, which is the existence oracle tenant isolation
  exists to withhold. ``_load_device`` and ``_load_session`` are the only two
  places either is fetched, so this cannot be forgotten per-route.
* **Refusals name the gate that failed.** ``MirrorError.code`` becomes the
  error code in the body, so the console can explain *which* of the four
  conditions is missing instead of rendering a dead button.

State-changing actions leave two independent records: the ``mirror_sessions``
row itself (who asked, who decided, against which consent, ending when) and an
``audit_log`` row from the service layer. On top of those, ``app.middleware.
audit`` records the method and path of every POST here automatically, because
every route is JWT-authenticated through ``get_current_user``.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.device import MIRROR_ACTIVE, MIRROR_PENDING, Device, MirrorSession
from app.models.user import User
from app.security.permissions import (
    DEVICE_MIRROR_APPROVE,
    DEVICE_MIRROR_REQUEST,
    DEVICES_MANAGE,
    DEVICES_READ,
    has_any_capability,
    has_capability,
)
from app.services import mirroring
from app.services.mirroring import MirrorError

router = APIRouter(prefix="/api/v1/device-mirror", tags=["devices"])

logger = logging.getLogger(__name__)

#: Refusal reason -> HTTP status. Split deliberately rather than funnelled
#: through one code: a missing consent and a device already being watched are
#: both conflicts with the current state (409), refusing to approve your own
#: request is a prohibition (403), and a malformed duration is a bad request
#: (400). Anything unlisted is a state conflict, which is the common case here.
_STATUS_BY_CODE: dict[str, int] = {
    "no_consent": 409,
    "consent_withdrawn": 409,
    "consent_changed": 409,
    "device_revoked": 409,
    "device_quarantined": 409,
    "device_inactive": 409,
    "already_active": 409,
    "not_pending": 409,
    "request_expired": 409,
    "two_person_rule": 403,
    "not_permitted": 403,
    "wrong_workspace": 404,
    "device_missing": 404,
    "invalid_duration": 400,
    "reason_required": 400,
    "reason_too_long": 400,
}


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _refusal(exc: MirrorError) -> HTTPException:
    """Turn a domain refusal into the house error envelope, gate code intact."""
    return _error(_STATUS_BY_CODE.get(exc.code, 409), exc.code, str(exc))


def _require(user: User, *required: str) -> None:
    """403 unless the caller holds every listed capability."""
    if not has_capability(user, *required):
        raise _error(403, "forbidden", "Insufficient permissions for this action")


def _load_device(db: Session, device_id: uuid.UUID, workspace_id: uuid.UUID) -> Device:
    """Fetch a device inside the caller's workspace, or 404. The only funnel."""
    row = db.get(Device, device_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "not_found", "Device not found")
    return row


def _load_session(db: Session, session_id: uuid.UUID, workspace_id: uuid.UUID) -> MirrorSession:
    """Fetch a mirror session inside the caller's workspace, or 404."""
    row = db.get(MirrorSession, session_id)
    if row is None or row.workspace_id != workspace_id:
        raise _error(404, "not_found", "Mirror request not found")
    return row


def _client_from_request(request: Request) -> str:
    """Audit client label. Constrained to the values ``audit_log`` accepts."""
    header = request.headers.get("x-client")
    if header in ("web", "mobile", "api", "system"):
        return header
    return "web"


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _duration_minutes(raw: Any) -> datetime.timedelta | None:
    """``duration_minutes`` from a body, or ``None`` for the default window.

    Numbers only, and never a bool: a client that sent ``"30"`` or ``true`` for
    a *screen-watching* window must be told the field is wrong, not quietly
    given the default. A wrong value here is not a formatting nuisance — it is
    somebody's statement about how long they intend to look at a working
    employee's screen, and guessing at it would be guessing at that.
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _error(
            400,
            "validation_error",
            "duration_minutes must be a number",
            field="duration_minutes",
        )
    if raw <= 0:
        raise _error(
            400,
            "validation_error",
            "duration_minutes must be greater than zero",
            field="duration_minutes",
        )
    return datetime.timedelta(minutes=float(raw))


def _serialise(
    row: MirrorSession, *, viewer: User | None = None, now: datetime.datetime | None = None
) -> dict[str, Any]:
    """A mirror session as the console sees it.

    ``live`` is computed, never read from ``status``: an ``active`` row whose
    window has closed is a view that has stopped, and a console that trusted
    the status would keep showing "watching" on a session nobody can watch.
    ``approvable`` is likewise computed, and it asks the two questions that
    actually decide it — does this viewer hold ``device_mirror:approve``, and is
    the request theirs — so the queue greys out the requester's own request
    instead of offering a button that always fails, and a read-only viewer is
    never shown an action they are not allowed to take. Offering a button
    somebody cannot press is how a refusal that should never have been
    reachable becomes a support ticket.
    """
    moment = now if now is not None else datetime.datetime.now(datetime.UTC)
    decided_by = row.decided_by
    return {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "device_id": str(row.device_id),
        "status": row.status,
        "requested_by": str(row.requested_by),
        "decided_by": str(decided_by) if decided_by is not None else None,
        "requested_at": _iso(row.requested_at),
        "decided_at": _iso(row.decided_at),
        "ends_at": _iso(row.ends_at),
        "ended_at": _iso(row.ended_at),
        "consent_at": _iso(row.consent_at),
        "reason": row.reason,
        "live": mirroring.is_live(row, now=moment),
        "expired": mirroring.is_expired(row, now=moment),
        "approvable": row.status == MIRROR_PENDING
        and viewer is not None
        and has_capability(viewer, DEVICE_MIRROR_APPROVE)
        and viewer.id != row.requested_by,
    }


# --------------------------------------------------------------------------- #
# Consent — the customer's grant
# --------------------------------------------------------------------------- #


@router.post("/devices/{device_id}/mirror-consent", response_model=dict)
def set_mirror_consent(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """Record or withdraw the customer's consent to mirror this device.

    ``devices:manage`` rather than a mirroring capability: recording what a
    customer agreed to is fleet administration, and it is the *operator* who
    must not be able to open a view, not the operator who must not be able to
    write down the customer's answer. Withdrawing is the load-bearing half —
    it clears the grant and ends every session the grant was supporting in the
    same call, so a customer who says no is not left with a running view.
    """
    _require(current_user, DEVICES_MANAGE)
    device = _load_device(db, device_id, current_user.workspace_id)
    body = payload or {}
    if "granted" not in body:
        raise _error(
            400, "validation_error", "granted is required (true or false)", field="granted"
        )
    granted = body.get("granted")
    if not isinstance(granted, bool):
        raise _error(400, "validation_error", "granted must be true or false", field="granted")

    client = _client_from_request(request)
    if granted:
        # Read the column rather than the return value, so the response cannot
        # claim a consent time that was not actually stored.
        mirroring.record_consent(db, device, recorded_by=current_user, client=client)
        ended = 0
    else:
        ended = mirroring.withdraw_consent(db, device, withdrawn_by=current_user, client=client)
    db.flush()

    logger.info(
        "Device mirror consent %s id=%s by=%s sessions_ended=%d",
        "granted" if granted else "withdrawn",
        device.id,
        current_user.id,
        ended,
    )
    return {
        "data": {
            "device_id": str(device.id),
            "granted": granted,
            "consent_at": _iso(device.mirroring_consent_at),
            "sessions_ended": ended,
        }
    }


@router.get("/devices/{device_id}/mirror-eligibility", response_model=dict)
def mirror_eligibility(
    device_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Why this device can or cannot be mirrored right now.

    Exists so the console can *explain* itself. A button that silently does
    nothing when consent is missing is indistinguishable from a broken
    deployment, and the operator ends up filing a support ticket about a consent
    problem. Returns the canonical failing condition, every condition with its
    own detail, and what the calling seat is actually allowed to do.
    """
    _require(current_user, DEVICES_READ)
    device = _load_device(db, device_id, current_user.workspace_id)
    state = mirroring.eligibility(db, device)
    occupying = mirroring.active_session_for(db, device)

    return {
        "data": {
            "device_id": str(device.id),
            "eligible": state.eligible,
            "reason": state.reason,
            "message": mirroring.message_for(state.reason),
            "conditions": [
                {"condition": cond.name, "ok": cond.ok, "detail": cond.detail}
                for cond in state.conditions
            ],
            "consent_at": _iso(device.mirroring_consent_at),
            "active_session_id": str(occupying.id) if occupying is not None else None,
            "can_request": has_capability(current_user, DEVICE_MIRROR_REQUEST),
            "can_approve": has_capability(current_user, DEVICE_MIRROR_APPROVE),
            "max_session_minutes": int(mirroring.MAX_SESSION_DURATION.total_seconds() // 60),
        }
    }


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #


@router.post(
    "/devices/{device_id}/mirror-requests",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
)
def create_mirror_request(
    device_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """Ask to watch a device's screen. Grants nothing on its own.

    A 201 here means "your request is in the queue for somebody else", not "you
    can see the screen". The response says so explicitly, because a requester
    who reads 201 as a green light has been told a falsehood by an HTTP code.

    The window is not part of this request. It is set by whoever approves, and
    the requester is the *less* trusted of the two parties here — a field they
    sent and nobody stored would be a promise the API had not kept. It is
    refused explicitly rather than ignored for the same reason: a silently
    dropped ``duration_minutes`` is a requester who believes their "just two
    minutes" was agreed to.
    """
    _require(current_user, DEVICE_MIRROR_REQUEST)
    device = _load_device(db, device_id, current_user.workspace_id)
    body = payload or {}
    if "duration_minutes" in body:
        raise _error(
            400,
            "validation_error",
            "duration_minutes is set when the request is approved, not when it "
            "is raised; the approver chooses the window",
            field="duration_minutes",
        )
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise _error(
            400,
            "validation_error",
            "reason is required: say why the screen is being viewed",
            field="reason",
        )

    try:
        row = mirroring.request_session(
            db,
            device=device,
            requested_by=current_user,
            reason=reason,
            client=_client_from_request(request),
        )
    except MirrorError as exc:
        raise _refusal(exc) from exc

    logger.info("Mirror request opened id=%s device=%s by=%s", row.id, device.id, current_user.id)
    return {
        "data": {
            **_serialise(row, viewer=current_user),
            "awaiting_second_person": True,
            "notice": (
                "This request views nothing yet. A second workspace member "
                "holding device_mirror:approve must approve it first, and you "
                "cannot approve your own request."
            ),
        }
    }


@router.get("/devices/{device_id}/mirror-requests", response_model=dict)
def list_mirror_requests(
    device_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    session_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=mirroring.MAX_SESSION_LIMIT)] = 100,
) -> dict[str, Any]:
    """This device's mirroring history — the audit view, newest first.

    Readable by anyone who can read the fleet, because the record of *whether
    somebody watched a customer's screen* has to be available to whoever is
    asked about it, not only to the person who asked.
    """
    _require(current_user, DEVICES_READ)
    _load_device(db, device_id, current_user.workspace_id)
    rows = mirroring.list_sessions(
        db,
        workspace_id=current_user.workspace_id,
        device_id=device_id,
        status=session_status,
        limit=limit,
    )
    return {"data": [_serialise(row, viewer=current_user) for row in rows]}


@router.get("/mirror-requests/pending", response_model=dict)
def list_pending_mirror_requests(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """The approval queue: requests waiting for a second person, oldest first.

    Gated on ``device_mirror:approve`` rather than on read access, because this
    screen is the one place a workspace's *outstanding asks to watch a staff
    member's screen* are collected. A caller who may not approve has no business
    browsing the queue; they can still see their own request on the device.

    The caller's own rows are included, flagged ``approvable: false``, so the
    console can show "yours, waiting" rather than leaving a requester to wonder
    whether it was ever received.
    """
    _require(current_user, DEVICE_MIRROR_APPROVE)
    rows = mirroring.pending_sessions(db, current_user.workspace_id)
    return {
        "data": [_serialise(row, viewer=current_user) for row in rows],
        "cannot_approve": [str(row.id) for row in rows if row.requested_by == current_user.id],
    }


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


@router.post("/mirror-requests/{session_id}/approve", response_model=dict)
def approve_mirror_request(
    session_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    payload: Annotated[dict[str, Any] | None, Body()] = None,
) -> dict[str, Any]:
    """Approve a request as the second person, opening a time-boxed view.

    ``device_mirror:approve`` — which the administrator role deliberately does
    not hold — and never your own request. ``ends_at`` is set here and capped
    at ``MAX_SESSION_DURATION``; it is the only thing that stops the view, so
    the response reports it and the console shows a countdown against it.
    """
    _require(current_user, DEVICE_MIRROR_APPROVE)
    row = _load_session(db, session_id, current_user.workspace_id)
    duration = _duration_minutes((payload or {}).get("duration_minutes"))

    try:
        updated = mirroring.approve_session(
            db,
            row,
            decided_by=current_user,
            duration=duration,
            client=_client_from_request(request),
        )
    except MirrorError as exc:
        raise _refusal(exc) from exc

    logger.info(
        "Mirror request approved id=%s device=%s by=%s ends_at=%s",
        updated.id,
        updated.device_id,
        current_user.id,
        updated.ends_at,
    )
    return {
        "data": {
            **_serialise(updated, viewer=current_user),
            "max_session_minutes": int(mirroring.MAX_SESSION_DURATION.total_seconds() // 60),
        }
    }


@router.post("/mirror-requests/{session_id}/deny", response_model=dict)
def deny_mirror_request(
    session_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Refuse a request. Also two-person: a requester may not deny their own.

    That is not an oddity of this route. The database's ``two_person_rule``
    constraint is on ``decided_by``, so it covers every decision; a requester
    who could deny their own request would have a route to a decided row that
    never went past them.
    """
    _require(current_user, DEVICE_MIRROR_APPROVE)
    row = _load_session(db, session_id, current_user.workspace_id)

    try:
        updated = mirroring.deny_session(
            db, row, decided_by=current_user, client=_client_from_request(request)
        )
    except MirrorError as exc:
        raise _refusal(exc) from exc

    logger.info("Mirror request denied id=%s by=%s", updated.id, current_user.id)
    return {"data": _serialise(updated, viewer=current_user)}


@router.post("/mirror-requests/{session_id}/end", response_model=dict)
def end_mirror_request(
    session_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """End a live view — or withdraw a pending request.

    Open to the requester as well as an approver, because a person who opened a
    view and changed their mind must not have to go and find a second person to
    close it. Ending is idempotent: calling it on an already-ended session
    returns the same record with the original ``ended_at`` intact, so a retried
    request can never rewrite when a view actually stopped.
    """
    if not has_any_capability(current_user, DEVICE_MIRROR_REQUEST, DEVICE_MIRROR_APPROVE):
        raise _error(403, "forbidden", "Insufficient permissions for this action")
    row = _load_session(db, session_id, current_user.workspace_id)
    # Read before ending: "a live view was stopped" is worth a log line and
    # "a request was withdrawn" is not the same event.
    was_active = row.status == MIRROR_ACTIVE

    try:
        updated = mirroring.end_session(
            db, row, ended_by=current_user, client=_client_from_request(request)
        )
    except MirrorError as exc:
        raise _refusal(exc) from exc

    if was_active:
        logger.info("Mirror session ended id=%s by=%s", updated.id, current_user.id)
    return {"data": _serialise(updated, viewer=current_user)}
