"""Consent-gated live screen mirroring — the most sensitive read in this system.

**This feature reads a working employee's screen.** A DemandIQ device is a
customer's restaurant till or tablet, on a counter, during a service. A live
mirrored view of it is not a log line and not a metric: it is whatever the
person standing at that till can see, in the middle of their shift, including
the parts of that screen which have nothing to do with DemandIQ. Anyone reading
this file should hold that in mind before changing it — the code below is
written to be read as a consent boundary, not as a permissions helper.

The feature exists because remote support genuinely needs it: "this till is
showing an error, let me look" cannot be answered from a heartbeat. What makes
that support rather than monitoring is the set of gates below. All four must
hold, and each is load-bearing on its own:

1. **Customer consent.** ``devices.mirroring_consent_at`` must not be NULL. It
   is a timestamp rather than a boolean so the grant can be evidenced ("the
   customer agreed on the 3rd, here is when") and withdrawn, and so consent is
   checked *first* — it is the one condition only the customer controls, and it
   is therefore the one the operator is told about before anything else.
2. **Two different people.** A ``MirrorSession`` sits at ``pending`` until a
   second workspace member approves it. ``requested_by <> decided_by`` is a
   database CHECK (``two_person_rule``) and is *also* enforced here, because a
   constraint that only fires at COMMIT answers a caller with a 500 rather than
   with an explanation.
3. **Role separation.** Requesting needs ``device_mirror:request`` and
   approving needs ``device_mirror:approve``; the administrator role holds the
   first and not the second, so the ordinary admin support path cannot approve
   its own request. See ``app.security.permissions``.
4. **A hard expiry.** Every approved session gets ``ends_at = now + duration``,
   and the duration is capped at :data:`MAX_SESSION_DURATION` whatever was
   asked for. A live view that can outlive the support call it was opened for
   is not support.

What this module deliberately does **not** offer, so that adding one is a
conscious decision rather than an oversight:

* no unattended "record it and review later" mirroring — a view is live or it
  does not exist, and nothing here retains frames;
* no single-person path, however senior the requester;
* no way to raise the ceiling — the cap applies to an owner's request exactly
  as it applies to an administrator's;
* no partial withdrawal — ``withdraw_consent`` ends what is live *and* what is
  queued in the same call, because a request that sat in an approver's queue
  after consent was withdrawn is an offer to watch a screen the customer has
  said no to.

Consent is recorded on the device (the *when*), and every state change is
written to ``audit_log`` (the *who* and the *what*). Neither table alone answers
"was that screen view agreed to", which is why both are written on every
decision rather than only on the interesting ones.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.device import (
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_QUARANTINED,
    DEVICE_STATUS_REVOKED,
    MIRROR_ACTIVE,
    MIRROR_DENIED,
    MIRROR_ENDED,
    MIRROR_PENDING,
    Device,
    MirrorSession,
)
from app.models.user import User
from app.security.permissions import DEVICE_MIRROR_APPROVE, has_capability

#: Hard ceiling on one live view. Thirty minutes is longer than any plausible
#: "look at this error" support call — diagnosis and a fix — and far shorter
#: than a shift, so a view that nobody remembers to end cannot run for the rest
#: of a service. A requester may ask for less; nobody may ask for more, and no
#: role is exempt. Changing this constant is a product decision about how long a
#: system may watch someone work, not a tuning knob.
MAX_SESSION_DURATION = datetime.timedelta(minutes=30)

#: What a request gets when it does not ask for something specific. Short by
#: design: the approver who says yes to an undiagnosed error wants the *next*
#: few minutes, and can end the session early at any point.
DEFAULT_SESSION_DURATION = datetime.timedelta(minutes=10)

#: How long a request may sit in the approval queue before it stops being
#: answerable. A request raised before lunch is not a request to watch a screen
#: during dinner service, so an over-age request cannot be approved — the
#: requester raises a fresh one. Over-age requests are hidden from the queue
#: rather than deleted, because the row is the record that the ask was made.
MAX_REQUEST_AGE = datetime.timedelta(hours=1)

#: Ceiling on the operator's "why". A reason is required by policy and is the
#: first thing an auditor reads, so it is bounded rather than free-text into a
#: row that will outlive everyone involved.
MAX_REASON_LENGTH = 500

#: Ceiling on a device's session history read. The fleet console shows a recent
#: window; an unbounded one would walk a table that is deliberately never
#: pruned, because these rows are evidence.
MAX_SESSION_LIMIT = 200

#: The one reason code that is not a refusal.
ELIGIBLE = "eligible"

#: Condition names reported by :func:`eligibility`, in the order they are
#: evaluated. The order is the product decision: consent first, because it is
#: the customer's condition and the one an operator must act on.
CONDITION_CONSENT = "customer_consent"
CONDITION_IN_SERVICE = "device_in_service"
CONDITION_NO_LIVE_VIEW = "no_live_view"

#: Operator-facing text for every reason code. Kept beside the codes so the
#: explanation an operator reads cannot drift from the condition it explains, and
#: written to be safe to show a customer-facing console: it says what is
#: blocked and what to do, never anything about the device's contents.
MESSAGES: dict[str, str] = {
    ELIGIBLE: (
        "Mirroring is possible. A second workspace member holding "
        "device_mirror:approve must still approve each request."
    ),
    "no_consent": (
        "The customer has not consented to screen mirroring for this device. "
        "Record their consent first; without it no view may be requested."
    ),
    "consent_withdrawn": (
        "The customer withdrew consent for this device, so this request can no "
        "longer be approved. Raise a fresh request if consent is granted again."
    ),
    "consent_changed": (
        "The consent recorded on this request is not the consent that is in "
        "force now. Raise a fresh request so the approver decides against a "
        "single, current grant."
    ),
    "device_revoked": "This device has been revoked and cannot be mirrored.",
    "device_quarantined": "This device is quarantined and is not in service.",
    "device_inactive": "This device is not in service.",
    "already_active": (
        "An approved mirror session is already live on this device. Two live "
        "views of one screen are not supported; end the current session first."
    ),
    "two_person_rule": (
        "A mirror request must be decided by a second person. The requester "
        "cannot approve or deny their own request, however senior they are."
    ),
    "not_pending": "This mirror request has already been decided.",
    "not_permitted": (
        "Only the requester or a workspace member holding "
        "device_mirror:approve may end this mirror session."
    ),
    "wrong_workspace": "Not found.",
    "request_expired": (
        "This request is older than the two-person window and can no longer be "
        "approved. Raise a fresh request so the decision is made while the "
        "reason for it still holds."
    ),
    "invalid_duration": "A mirror session must last longer than zero seconds.",
    "reason_required": (
        "A mirror request must state why the screen is being viewed. The reason "
        "is recorded on the session and read by whoever approves it."
    ),
    "reason_too_long": f"The reason must be at most {MAX_REASON_LENGTH} characters.",
    "device_missing": "The device for this mirror session no longer exists.",
}


class MirrorError(ValueError):
    """Domain error for mirroring operations.

    Carries a stable ``code`` from the vocabulary above so a router can choose
    an HTTP status and a body without parsing an English sentence, and so the
    tests can assert on the condition rather than on wording.
    """

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or MESSAGES.get(code, "This request cannot be completed."))
        self.code = code


def message_for(reason: str) -> str:
    """Operator-facing explanation for a reason code. Never raises."""
    return MESSAGES.get(reason, "This device cannot be mirrored.")


@dataclass(frozen=True)
class Condition:
    """One gate, reported to the UI so it can explain itself."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Eligibility:
    """The verdict plus every gate that was checked to reach it."""

    reason: str
    conditions: tuple[Condition, ...]

    @property
    def eligible(self) -> bool:
        return self.reason == ELIGIBLE


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


def is_expired(session: MirrorSession, *, now: datetime.datetime | None = None) -> bool:
    """True when the session's window has closed — or was never set.

    A missing ``ends_at`` counts as expired. The column is populated by
    :func:`approve_session` in the same statement that sets the status, so a
    ``None`` there means some other writer bypassed the service, and the safe
    reading of a view with no stated end is that it has ended.
    """
    moment = now if now is not None else _now()
    return session.ends_at is None or session.ends_at <= moment


def is_live(session: MirrorSession, *, now: datetime.datetime | None = None) -> bool:
    """True when this session is an unexpired live view.

    This is the predicate that decides whether frames may be delivered for a
    session, so it fails closed: anything not provably inside its window is not
    live.
    """
    return session.status == MIRROR_ACTIVE and not is_expired(session, now=now)


def occupies_device(session: MirrorSession, *, now: datetime.datetime | None = None) -> bool:
    """True when this session blocks a second live view of its device.

    Deliberately not ``is_live``. The question here is "may somebody else start
    looking at this screen too", and that must fail closed in the other
    direction from the question above: an ``active`` row with no ``ends_at``
    cannot be shown to be finished, so it keeps the device occupied. Only a
    timestamp demonstrably in the past releases it.
    """
    moment = now if now is not None else _now()
    if session.status != MIRROR_ACTIVE:
        return False
    return session.ends_at is None or session.ends_at > moment


# --------------------------------------------------------------------------- #
# Gates 1, 3, 4 — eligibility
# --------------------------------------------------------------------------- #


def active_session_for(
    db: Session, device: Device, *, now: datetime.datetime | None = None
) -> MirrorSession | None:
    """The live session occupying ``device``, or ``None``.

    Scoped to the device *and* the workspace, so a caller holding a device row
    from one tenant can never be shown a session belonging to another even if
    the device id were somehow shared.
    """
    moment = now if now is not None else _now()
    rows = db.scalars(
        select(MirrorSession).where(
            MirrorSession.workspace_id == device.workspace_id,
            MirrorSession.device_id == device.id,
        )
    ).all()
    occupying = [
        row for row in rows if isinstance(row, MirrorSession) and occupies_device(row, now=moment)
    ]
    if not occupying:
        return None
    # A device should never hold two; if it somehow does, the one that blocks
    # for longest is the one reported, so the operator is told about the view
    # that is hardest to get rid of. A row with no ``ends_at`` sorts first
    # rather than raising — the comparison is between aware datetimes, so the
    # sentinel has to be aware too.
    floor = datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return max(occupying, key=lambda row: row.ends_at or floor)


def eligibility(
    db: Session, device: Device, *, now: datetime.datetime | None = None
) -> Eligibility:
    """Evaluate every device-level gate and report the first failure.

    All of the failures are reported, not just the first, because the console
    has to explain the button: "the customer has not consented" and "a view is
    already live" call for different fixes. ``reason`` is the canonical one and
    is the *first* in evaluation order, which puts consent first.
    """
    moment = now if now is not None else _now()
    failures: list[str] = []
    conditions: list[Condition] = []

    consent_at = device.mirroring_consent_at
    if consent_at is None:
        failures.append("no_consent")
        conditions.append(Condition(CONDITION_CONSENT, False, MESSAGES["no_consent"]))
    else:
        conditions.append(
            Condition(
                CONDITION_CONSENT,
                True,
                f"Customer consent recorded at {consent_at.isoformat()}.",
            )
        )

    if device.status == DEVICE_STATUS_ACTIVE:
        conditions.append(Condition(CONDITION_IN_SERVICE, True, "This device is in service."))
    elif device.status == DEVICE_STATUS_REVOKED:
        failures.append("device_revoked")
        conditions.append(Condition(CONDITION_IN_SERVICE, False, MESSAGES["device_revoked"]))
    elif device.status == DEVICE_STATUS_QUARANTINED:
        failures.append("device_quarantined")
        conditions.append(Condition(CONDITION_IN_SERVICE, False, MESSAGES["device_quarantined"]))
    else:
        # An unknown status is refused, not treated as fine: a status this
        # release has never heard of is not evidence that a device is in use.
        failures.append("device_inactive")
        conditions.append(Condition(CONDITION_IN_SERVICE, False, MESSAGES["device_inactive"]))

    occupying = active_session_for(db, device, now=moment)
    if occupying is None:
        conditions.append(
            Condition(CONDITION_NO_LIVE_VIEW, True, "No mirror session is live on this device.")
        )
    else:
        failures.append("already_active")
        conditions.append(Condition(CONDITION_NO_LIVE_VIEW, False, MESSAGES["already_active"]))

    return Eligibility(
        reason=failures[0] if failures else ELIGIBLE,
        conditions=tuple(conditions),
    )


def can_mirror(
    db: Session, device: Device, *, now: datetime.datetime | None = None
) -> tuple[bool, str]:
    """Whether ``device`` may be mirrored at all, and the specific reason.

    Returns ``(False, <code>)`` rather than a bare ``False`` on purpose. A
    caller that cannot tell *which* gate failed will either show a dead button
    with no explanation or, worse, quietly re-try — and the whole point of the
    four gates is that each one deserves its own answer to the operator.
    """
    state = eligibility(db, device, now=now)
    return state.eligible, state.reason


# --------------------------------------------------------------------------- #
# Gate 2 — the two-person lifecycle
# --------------------------------------------------------------------------- #


def request_session(
    db: Session,
    *,
    device: Device,
    requested_by: User,
    reason: str,
    now: datetime.datetime | None = None,
    client: str = "web",
) -> MirrorSession:
    """Open a ``pending`` mirror request. It views nothing yet.

    The request is the first of two people, not a grant. It is refused outright
    — rather than created and left to fail later — when the device is not
    mirrorable at all, so the approval queue never offers a view that could
    never lawfully start.

    ``consent_at`` is copied from the device here so the session carries the
    grant it was asked under, and re-checked at approval: consent that changed
    in between must not silently legitimise a request raised under the old one.
    """
    moment = now if now is not None else _now()
    if not reason or not reason.strip():
        raise MirrorError("reason_required")
    if len(reason) > MAX_REASON_LENGTH:
        raise MirrorError("reason_too_long")
    if requested_by.workspace_id != device.workspace_id:
        raise MirrorError("wrong_workspace")

    state = eligibility(db, device, now=moment)
    if not state.eligible:
        raise MirrorError(state.reason)

    row = MirrorSession(
        id=uuid.uuid4(),
        workspace_id=device.workspace_id,
        device_id=device.id,
        requested_by=requested_by.id,
        decided_by=None,
        status=MIRROR_PENDING,
        requested_at=moment,
        decided_at=None,
        ends_at=None,
        ended_at=None,
        consent_at=device.mirroring_consent_at,
        reason=reason.strip(),
    )
    db.add(row)
    _audit(
        db,
        workspace_id=row.workspace_id,
        actor_id=requested_by.id,
        action="mirror.request",
        target=str(row.id),
        client=client,
        meta={
            "device_id": str(device.id),
            "reason": row.reason,
            "consent_at": _iso(device.mirroring_consent_at),
        },
        moment=moment,
    )
    db.flush()
    return row


def approve_session(
    db: Session,
    session: MirrorSession,
    *,
    decided_by: User,
    duration: datetime.timedelta | None = None,
    now: datetime.datetime | None = None,
    client: str = "web",
) -> MirrorSession:
    """Turn a ``pending`` request into a live, time-boxed view.

    The second person. ``decided_by`` must be somebody other than the
    requester — the database says so too, but a CHECK surfaces at flush time as
    a 500, and an operator deserves to be told which rule they hit.

    Every gate is re-evaluated here, not trusted from request time: consent may
    have been withdrawn, the device may have been revoked, and another approver
    may have opened a view in the meantime.

    The requested ``duration`` is capped at :data:`MAX_SESSION_DURATION`
    rather than refused, and ``ends_at`` is the only authority on how long the
    view lasts.
    """
    moment = now if now is not None else _now()

    if session.status != MIRROR_PENDING:
        raise MirrorError("not_pending")
    if decided_by.workspace_id != session.workspace_id:
        raise MirrorError("wrong_workspace")
    if decided_by.id == session.requested_by:
        raise MirrorError("two_person_rule")
    if session.requested_at is not None and moment - session.requested_at > MAX_REQUEST_AGE:
        raise MirrorError("request_expired")

    device = db.get(Device, session.device_id)
    if device is None or not isinstance(device, Device):
        raise MirrorError("device_missing")

    # Consent is re-read from the device, not from the session's copy. A NULL
    # device column after a non-NULL session copy is a withdrawal, and gets its
    # own message because "the customer said no" is not the same answer as
    # "the customer never said yes".
    if device.mirroring_consent_at is None:
        raise MirrorError("consent_withdrawn" if session.consent_at else "no_consent")
    if device.mirroring_consent_at != session.consent_at:
        # Consent was withdrawn and granted again between the two clicks. The
        # view would be lawful under the new grant, but the request was written
        # under the old one, and an approver reading it should be looking at a
        # single current grant. Refuse; the requester raises a fresh request.
        raise MirrorError("consent_changed")

    state = eligibility(db, device, now=moment)
    if not state.eligible:
        raise MirrorError(state.reason)

    window = DEFAULT_SESSION_DURATION if duration is None else duration
    if window <= datetime.timedelta(0):
        raise MirrorError("invalid_duration")
    window = min(window, MAX_SESSION_DURATION)
    ends_at = moment + window

    session.status = MIRROR_ACTIVE
    session.decided_by = decided_by.id
    session.decided_at = moment
    session.ends_at = ends_at
    _audit(
        db,
        workspace_id=session.workspace_id,
        actor_id=decided_by.id,
        action="mirror.approve",
        target=str(session.id),
        client=client,
        meta={
            "device_id": str(session.device_id),
            "requested_by": str(session.requested_by),
            "decided_by": str(decided_by.id),
            "duration_seconds": int(window.total_seconds()),
            "ends_at": ends_at.isoformat(),
        },
        moment=moment,
    )
    db.flush()
    return session


def deny_session(
    db: Session,
    session: MirrorSession,
    *,
    decided_by: User,
    now: datetime.datetime | None = None,
    client: str = "web",
) -> MirrorSession:
    """Refuse a ``pending`` request.

    Self-denial is refused for the same reason self-approval is: the
    ``two_person_rule`` CHECK is on ``decided_by``, so it constrains every
    decision and not just the permissive one. Letting a requester deny their own
    request would either violate the constraint or, worse, quietly hand the
    only legitimate denier the illusion that the queue will clear itself.

    ``ended_at`` is deliberately left NULL: a denied session never started, so
    there is no view to have ended. ``status`` is the authority on whether a
    session is open, ``ended_at`` only records when a live view stopped.
    """
    moment = now if now is not None else _now()

    if session.status != MIRROR_PENDING:
        raise MirrorError("not_pending")
    if decided_by.workspace_id != session.workspace_id:
        raise MirrorError("wrong_workspace")
    if decided_by.id == session.requested_by:
        raise MirrorError("two_person_rule")

    session.status = MIRROR_DENIED
    session.decided_by = decided_by.id
    session.decided_at = moment
    _audit(
        db,
        workspace_id=session.workspace_id,
        actor_id=decided_by.id,
        action="mirror.deny",
        target=str(session.id),
        client=client,
        meta={
            "device_id": str(session.device_id),
            "requested_by": str(session.requested_by),
            "decided_by": str(decided_by.id),
        },
        moment=moment,
    )
    db.flush()
    return session


def end_session(
    db: Session,
    session: MirrorSession,
    *,
    ended_by: User | None = None,
    now: datetime.datetime | None = None,
    client: str = "web",
) -> MirrorSession:
    """Stop a view. Always allowed for the right people, never an error twice.

    Ending is the one operation that must not fail: a session that cannot be
    ended is the failure mode this whole module exists to prevent, so the
    requester may always drop their own view, an approver may drop anyone's, and
    a second call is a no-op that leaves the original ``ended_at`` intact — the
    first ending is the evidence.

    ``ended_by`` of ``None`` means the system ended it rather than a person
    (a consent withdrawal, or expiry being reaped). That bypass is deliberate
    and is the only one: the entitlement rule below exists to stop a colleague
    quietly closing someone else's view, and an administrative withdrawal is
    not that. The actor is recorded in the audit row either way.

    A ``pending`` request may be ended too — that is the requester withdrawing
    their ask — and leaves ``decided_by`` NULL, because nobody decided anything.
    """
    moment = now if now is not None else _now()

    if ended_by is not None and not _may_end(session, ended_by):
        raise MirrorError("not_permitted")
    if session.status in (MIRROR_ENDED, MIRROR_DENIED):
        return session

    previous = session.status
    session.status = MIRROR_ENDED
    session.ended_at = moment
    _audit(
        db,
        workspace_id=session.workspace_id,
        actor_id=ended_by.id if ended_by is not None else None,
        action="mirror.end",
        target=str(session.id),
        client=client,
        meta={
            "device_id": str(session.device_id),
            # ``pending`` here is a requester withdrawing their own ask rather
            # than a live view being stopped, and an auditor reads the two
            # differently.
            "from_status": previous,
            "actor": "system" if ended_by is None else str(ended_by.id),
        },
        moment=moment,
    )
    db.flush()
    return session


def _may_end(session: MirrorSession, actor: User) -> bool:
    """The requester may end their own view; ending anyone else's needs APPROVE."""
    if actor.workspace_id != session.workspace_id:
        return False
    return actor.id == session.requested_by or has_capability(actor, DEVICE_MIRROR_APPROVE)


# --------------------------------------------------------------------------- #
# Consent itself
# --------------------------------------------------------------------------- #


def record_consent(
    db: Session,
    device: Device,
    *,
    recorded_by: User | None = None,
    now: datetime.datetime | None = None,
    client: str = "web",
) -> datetime.datetime:
    """Record the customer's consent to mirror this device. The *when*.

    This is the customer's grant, entered by an operator, so the column holds
    only the time it was taken. Who recorded it lives in the audit row written
    here, and the two together are the evidence a customer asking "did you ever
    have permission?" is answered with.

    Granting consent does not revive anything. Sessions ended by a withdrawal
    stay ended, and a request raised under a previous grant still has to be
    raised again — otherwise consent would be a switch that silently re-opens
    views the customer had already closed.
    """
    moment = now if now is not None else _now()
    device.mirroring_consent_at = moment
    _audit(
        db,
        workspace_id=device.workspace_id,
        actor_id=recorded_by.id if recorded_by is not None else None,
        action="mirror.consent_recorded",
        target=str(device.id),
        client=client,
        meta={"device_id": str(device.id), "consent_at": moment.isoformat()},
        moment=moment,
    )
    db.flush()
    return moment


def withdraw_consent(
    db: Session,
    device: Device,
    *,
    withdrawn_by: User | None = None,
    now: datetime.datetime | None = None,
    client: str = "web",
) -> int:
    """Withdraw consent, and end every session the grant was supporting.

    Clearing the column alone is not enough. A live view is a running process on
    somebody's counter, and a customer who says no is not satisfied by a flag
    flipping while the view keeps going — so what is live is ended here, in the
    same call, and the caller is told how many sessions were stopped.

    Outstanding *requests* are ended too. A pending request is an offer to watch
    a screen the customer has just refused, and leaving it in an approver's
    queue would be the worst outcome: it would be one click from a view the
    customer declined.

    Returns the number of sessions ended.
    """
    moment = now if now is not None else _now()
    device.mirroring_consent_at = None

    rows = db.scalars(
        select(MirrorSession).where(
            MirrorSession.workspace_id == device.workspace_id,
            MirrorSession.device_id == device.id,
        )
    ).all()
    ended = 0
    for row in rows:
        if not isinstance(row, MirrorSession):
            continue
        if row.status in (MIRROR_ACTIVE, MIRROR_PENDING):
            # ``ended_by=None``: this is the customer's decision, not a person's
            # request to close a colleague's view, so the entitlement rule is
            # not the thing being applied here.
            end_session(db, row, ended_by=None, now=moment, client=client)
            ended += 1

    _audit(
        db,
        workspace_id=device.workspace_id,
        actor_id=withdrawn_by.id if withdrawn_by is not None else None,
        action="mirror.consent_withdrawn",
        target=str(device.id),
        client=client,
        meta={
            "device_id": str(device.id),
            "sessions_ended": ended,
            "actor": "system" if withdrawn_by is None else str(withdrawn_by.id),
        },
        moment=moment,
    )
    db.flush()
    return ended


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def pending_sessions(
    db: Session, workspace_id: uuid.UUID, *, now: datetime.datetime | None = None
) -> list[MirrorSession]:
    """The approval queue: requests awaiting a second person, oldest first.

    Over-age requests are excluded rather than deleted — the row is the record
    that the ask was made, but it is no longer a decision anyone should be
    making. The caller's own requests are included on purpose: the console
    greys them out instead of leaving someone to wonder whether their request
    was ever seen.
    """
    moment = now if now is not None else _now()
    rows = db.scalars(
        select(MirrorSession)
        .where(
            MirrorSession.workspace_id == workspace_id,
            MirrorSession.status == MIRROR_PENDING,
        )
        .order_by(MirrorSession.requested_at)
    ).all()
    return [
        row
        for row in rows
        if isinstance(row, MirrorSession)
        and not (row.requested_at is not None and moment - row.requested_at > MAX_REQUEST_AGE)
    ]


def list_sessions(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    device_id: uuid.UUID,
    status: str | None = None,
    limit: int | None = None,
) -> list[MirrorSession]:
    """A device's session history, newest first, for the audit view."""
    stmt = select(MirrorSession).where(
        MirrorSession.workspace_id == workspace_id,
        MirrorSession.device_id == device_id,
    )
    if status is not None:
        stmt = stmt.where(MirrorSession.status == status)
    stmt = stmt.order_by(MirrorSession.requested_at.desc())
    if limit is not None:
        stmt = stmt.limit(min(limit, MAX_SESSION_LIMIT))
    return [row for row in db.scalars(stmt).all() if isinstance(row, MirrorSession)]


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def _iso(value: datetime.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _audit(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    action: str,
    target: str,
    client: str,
    meta: dict[str, object],
    moment: datetime.datetime,
) -> None:
    """Write the row that answers "who did this to whose screen, and when".

    ``app.middleware.audit`` already records the method and path of every
    non-GET request, but it cannot see inside the request: it does not know
    that ``mirror.approve`` records two different people, nor that a withdrawal
    stopped three live views. This row carries that, and the mirror session
    itself carries the two-person pair, so the pair is answerable without
    reading logs.
    """
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action=action,
            target=target,
            client=client,
            meta_data=dict(meta),
            created_at=moment,
        )
    )
