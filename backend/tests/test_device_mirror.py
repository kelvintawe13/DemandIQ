"""Mirroring tests — the consent model, asserted rather than assumed.

Live screen mirroring is the one capability in this system that reads something
other than a number: it reads the screen of a till an employee is working on,
mid-shift. Everything below is an assertion that the feature cannot be used
that way by accident.

What these tests pin, in the order the four gates are documented:

* **consent** — no consent, no request, no approval, and a withdrawal stops
  whatever the grant was supporting;
* **two people** — the requester cannot approve *or deny* their own request,
  and the database constraint covers both, so the service must too;
* **role separation** — an administrator holds ``device_mirror:request`` and not
  ``device_mirror:approve``, so the ordinary admin support path cannot approve
  its own request;
* **a hard expiry** — the window is capped whatever was asked for, and a session
  past its ``ends_at`` is not a live view;
* **tenant isolation** — another workspace's device and session are 404, never
  403, because a 403 would confirm the row exists.

Hermetic, in the house style of ``test_devices.py``: the mirror tables are
Postgres-typed, so there is no in-memory engine to run them on.
:class:`FakeSession` reads the *statement* — entity, equality criteria,
``ORDER BY``, ``LIMIT`` — and applies them in Python, so the real ``WHERE``
clauses the service emits are what is under test. That matters most for
isolation: a dropped ``workspace_id`` predicate would otherwise pass every test
here, since each of them only ever holds one workspace's rows.
:func:`test_the_session_double_really_filters` guards the double against being
the reason that stays unnoticed.

Nothing here touches a database, Redis or the network.
"""

from __future__ import annotations

import datetime
import uuid
from functools import partial
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BinaryExpression, Null, UnaryExpression

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.audit import AuditLog
from app.models.device import (
    DEVICE_STATUS_QUARANTINED,
    DEVICE_STATUS_REVOKED,
    MIRROR_ACTIVE,
    MIRROR_DENIED,
    MIRROR_ENDED,
    MIRROR_PENDING,
    Device,
    MirrorSession,
)
from app.models.user import User, UserRole
from app.routers.device_mirror import router
from app.security.jwt import create_access_token
from app.security.permissions import DEVICE_MIRROR_APPROVE, DEVICE_MIRROR_REQUEST, has_capability
from app.services.mirroring import (
    DEFAULT_SESSION_DURATION,
    ELIGIBLE,
    MAX_REASON_LENGTH,
    MAX_REQUEST_AGE,
    MAX_SESSION_DURATION,
    MESSAGES,
    MirrorError,
    active_session_for,
    approve_session,
    can_mirror,
    deny_session,
    eligibility,
    end_session,
    is_expired,
    is_live,
    list_sessions,
    message_for,
    pending_sessions,
    record_consent,
    request_session,
    withdraw_consent,
)

WS = uuid.uuid4()
OTHER_WS = uuid.uuid4()
PREFIX = "did_" + "0" * 8

#: A fixed "now" for the service-level tests, so an expiry assertion is a
#: comparison rather than a race.
NOW = datetime.datetime(2026, 4, 14, 9, 30, tzinfo=datetime.UTC)

#: Wall-clock "now", for the tests that go through HTTP. The service stamps and
#: ages rows against the real clock there, so a request dated to ``NOW`` would
#: be months old by the time the router reads it.
WALL = datetime.datetime.now(datetime.UTC)

#: Consent as it stands for a device that has agreed: a month ago.
CONSENTED = NOW - datetime.timedelta(days=30)

_MIRROR = "/api/v1/device-mirror"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _user(workspace_id: uuid.UUID = WS, role: UserRole = UserRole.administrator) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=f"{role.value} person",
        email=f"{role.value}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=NOW,
    )


def _device(
    workspace_id: uuid.UUID = WS,
    *,
    label: str = "Till 1",
    status: str = "active",
    consent_at: datetime.datetime | None = CONSENTED,
) -> Device:
    return Device(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        location_id=None,
        label=label,
        device_type="pos",
        platform="android",
        app_version=None,
        enrolled_at=NOW - datetime.timedelta(days=90),
        enrolled_by=None,
        credential_prefix=PREFIX,
        credential_hash="$2b$12$dummy",
        scopes=["device:sync"],
        status=status,
        last_seen_at=None,
        revoked_at=None,
        mirroring_consent_at=consent_at,
    )


def _session(
    device: Device,
    *,
    requested_by: uuid.UUID,
    status: str = MIRROR_PENDING,
    decided_by: uuid.UUID | None = None,
    requested_at: datetime.datetime = NOW,
    decided_at: datetime.datetime | None = None,
    ends_at: datetime.datetime | None = None,
    ended_at: datetime.datetime | None = None,
    consent_at: datetime.datetime | None = CONSENTED,
    reason: str = "Till showing an order error",
) -> MirrorSession:
    return MirrorSession(
        id=uuid.uuid4(),
        workspace_id=device.workspace_id,
        device_id=device.id,
        requested_by=requested_by,
        decided_by=decided_by,
        status=status,
        requested_at=requested_at,
        decided_at=decided_at,
        ends_at=ends_at,
        ended_at=ended_at,
        consent_at=consent_at,
        reason=reason,
    )


def _live(
    device: Device,
    *,
    requested_by: uuid.UUID | None = None,
    minutes_left: int = 10,
    now: datetime.datetime = NOW,
) -> MirrorSession:
    """An approved, unexpired session — the thing that occupies a device."""
    return _session(
        device,
        requested_by=requested_by or _user().id,
        status=MIRROR_ACTIVE,
        decided_by=_user(role=UserRole.owner).id,
        decided_at=now,
        ends_at=now + datetime.timedelta(minutes=minutes_left),
    )


# --------------------------------------------------------------------------- #
# The session double
# --------------------------------------------------------------------------- #


def _criteria(stmt: Any) -> list[tuple[str, str, Any]]:
    """Flatten a statement's ``WHERE`` into ``(column, operator, value)`` triples."""
    where = getattr(stmt, "whereclause", None)
    if where is None:
        return []
    out: list[tuple[str, str, Any]] = []
    for node in visitors.iterate(where):
        if isinstance(node, BinaryExpression) and hasattr(node, "left"):
            operator = getattr(node.operator, "__name__", "")
            out.append((node.left.name, operator, getattr(node.right, "value", node.right)))
    return out


def _order_spec(stmt: Any) -> list[tuple[str, bool]]:
    clause = getattr(stmt, "_order_by_clause", None)
    if clause is None:
        return []
    out: list[tuple[str, bool]] = []
    for element in clause.clauses:
        if isinstance(element, UnaryExpression):
            out.append((element.element.name, True))
        else:
            out.append((element.name, False))
    return out


def _matches(row: Any, stmt: Any) -> bool:
    for column, operator, value in _criteria(stmt):
        actual = getattr(row, column, None)
        if operator == "eq" and actual != value:
            return False
        if operator == "is_" and (actual is None) != isinstance(value, Null):
            return False
    return True


def _sort_key(row: Any, column: str) -> tuple[bool, Any]:
    value = getattr(row, column, None)
    return (value is None, value)


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """A ``Session`` that filters the statements the mirroring layer emits."""

    def __init__(
        self,
        *,
        devices: list[Device] | None = None,
        sessions: list[MirrorSession] | None = None,
    ) -> None:
        self.devices = list(devices or [])
        self.sessions = list(sessions or [])
        self.audits: list[AuditLog] = []
        self.added: list[Any] = []
        self.flushes = 0

    def _pool(self, model: Any) -> list[Any] | None:
        pools: dict[Any, list[Any]] = {
            Device: self.devices,
            MirrorSession: self.sessions,
        }
        return pools.get(model)

    def get(self, model: Any, pk: Any) -> Any:
        pool = self._pool(model)
        if pool is None:
            return None
        for row in pool:
            if row.id == pk:
                return row
        return None

    def scalars(self, stmt: Any) -> _Rows:
        entity = stmt.column_descriptions[0]["entity"]
        rows = [row for row in self._pool(entity) or [] if _matches(row, stmt)]
        # Keys applied least-significant first, so a stable sort composes into
        # the multi-key ordering the statement asked for.
        for column, descending in reversed(_order_spec(stmt)):
            rows.sort(key=partial(_sort_key, column=column), reverse=descending)
        limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
        return _Rows(rows[: int(limit)] if limit is not None else rows)

    def add(self, obj: Any) -> None:
        if isinstance(obj, MirrorSession):
            self.sessions.append(obj)
        elif isinstance(obj, AuditLog):
            self.audits.append(obj)
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _client(session: FakeSession, caller: User | None) -> TestClient:
    """The real application with this router mounted and the session stubbed.

    Attached here rather than relying on ``create_app`` having registered the
    router, so these tests assert against the module under test and stay green
    whether or not the application factory has been wired up yet.
    """
    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session  # type: ignore[assignment]
    if caller is not None:
        app.dependency_overrides[get_current_user] = lambda: caller  # type: ignore[assignment]
    return TestClient(app)


# --------------------------------------------------------------------------- #
# Gate 1 — customer consent
# --------------------------------------------------------------------------- #


def test_no_consent_is_refused_with_its_own_reason() -> None:
    """The customer's answer is the first gate, and it is a hard stop."""
    device = _device(consent_at=None)
    session = FakeSession(devices=[device])

    allowed, reason = can_mirror(session, device, now=NOW)

    assert allowed is False
    assert reason == "no_consent"
    # The reason carries text a support engineer can act on, and that text is
    # safe to show: it says what is blocked, not what the device is displaying.
    assert "has not consented" in MESSAGES[reason]

    with pytest.raises(MirrorError) as caught:
        request_session(
            session, device=device, requested_by=_user(), reason="Till is throwing an error"
        )
    assert caught.value.code == "no_consent"
    # Nothing was written: an unapprovable request must not sit in the queue.
    assert session.sessions == []


def test_a_device_with_no_consent_cannot_be_approved_either() -> None:
    """Consent is checked at the second click, not trusted from request time."""
    device = _device(consent_at=None)
    row = _session(device, requested_by=_user().id, consent_at=None)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        approve_session(session, row, decided_by=_user(role=UserRole.owner), now=NOW)

    assert caught.value.code == "no_consent"
    assert row.status == MIRROR_PENDING


def test_approval_is_refused_once_the_customer_withdraws_consent() -> None:
    """ "The customer never said yes" and "the customer said no" differ.

    The session carries the grant it was raised under, so a device whose column
    has since been cleared is a withdrawal — which is the answer an approver
    needs, and not the one they would get from a bare "no consent".
    """
    device = _device(consent_at=None)  # withdrawn since the request was raised
    row = _session(device, requested_by=_user().id, consent_at=CONSENTED)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        approve_session(session, row, decided_by=_user(role=UserRole.owner), now=NOW)

    assert caught.value.code == "consent_withdrawn"
    assert "withdrew consent" in str(caught.value)
    assert (row.status, row.ends_at) == (MIRROR_PENDING, None)


def test_consent_is_evaluated_before_the_device_state() -> None:
    """A device that fails two gates reports the customer's gate.

    Consent is the condition only the customer controls, so it is the one the
    operator is told about first — recording consent on a quarantined till must
    not read as if it had fixed the button.
    """
    device = _device(status=DEVICE_STATUS_QUARANTINED, consent_at=None)
    session = FakeSession(devices=[device])

    assert can_mirror(session, device, now=NOW) == (False, "no_consent")

    # The second failure is still reported, so the console can show both.
    state = eligibility(session, device, now=NOW)
    assert state.reason == "no_consent"
    assert {c.name for c in state.conditions if not c.ok} == {
        "customer_consent",
        "device_in_service",
    }


def test_a_device_out_of_service_reports_its_own_state() -> None:
    """Distinct codes, because "revoke the till" and "restore it" are different."""
    session = FakeSession()
    for status, expected in (
        (DEVICE_STATUS_REVOKED, "device_revoked"),
        (DEVICE_STATUS_QUARANTINED, "device_quarantined"),
        ("retired", "device_inactive"),
    ):
        device = _device(status=status)
        session.devices.append(device)
        assert can_mirror(session, device, now=NOW) == (False, expected), status


def test_withdrawing_consent_ends_an_active_session() -> None:
    """A customer who says no must not be left with a running view.

    Clearing the column alone would leave the view up, which is the whole
    failure this module exists to prevent.
    """
    device = _device()
    row = _live(device)
    session = FakeSession(devices=[device], sessions=[row])
    assert is_live(row, now=NOW)

    ended = withdraw_consent(session, device, now=NOW)

    assert ended == 1
    assert device.mirroring_consent_at is None
    assert row.status == MIRROR_ENDED
    assert row.ended_at == NOW
    assert is_live(row, now=NOW) is False
    assert can_mirror(session, device, now=NOW) == (False, "no_consent")
    # The withdrawal is itself part of the record, and the view it stopped is
    # recorded as a separate, named ending rather than a side effect.
    assert [a.action for a in session.audits] == [
        "mirror.end",
        "mirror.consent_withdrawn",
    ]
    assert session.audits[0].actor_id is None  # the customer, not a colleague


def test_withdrawing_consent_also_clears_the_approval_queue() -> None:
    """A pending request is an offer to watch a screen the customer refused."""
    device = _device()
    pending = _session(device, requested_by=_user().id)
    session = FakeSession(devices=[device], sessions=[pending])

    assert withdraw_consent(session, device, now=NOW) == 1
    assert pending.status == MIRROR_ENDED
    assert pending.decided_by is None  # nobody decided anything
    assert pending_sessions(session, WS, now=NOW) == []


def test_recording_consent_does_not_revive_an_ended_session() -> None:
    """Consent is a grant, not a rewind. A closed view stays closed."""
    device = _device(consent_at=None)
    row = _session(
        device,
        requested_by=_user().id,
        status=MIRROR_ENDED,
        decided_by=_user(role=UserRole.owner).id,
        ended_at=NOW,
        ends_at=NOW,
        consent_at=None,
    )
    session = FakeSession(devices=[device], sessions=[row])

    recorded = record_consent(session, device, now=NOW)

    assert device.mirroring_consent_at == NOW == recorded
    assert row.status == MIRROR_ENDED
    assert can_mirror(session, device, now=NOW) == (True, ELIGIBLE)


def test_approve_refuses_a_request_raised_under_a_different_grant() -> None:
    """Consent that changed between the clicks is not silently carried over.

    The view would be lawful under the new grant, but the request was written
    under the old one, and an approver reading it should be deciding against a
    single current grant.
    """
    device = _device(consent_at=NOW - datetime.timedelta(days=1))  # re-granted since
    row = _session(device, requested_by=_user().id, consent_at=CONSENTED)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        approve_session(session, row, decided_by=_user(role=UserRole.owner), now=NOW)

    assert caught.value.code == "consent_changed"


# --------------------------------------------------------------------------- #
# Gate 2 — two different people
# --------------------------------------------------------------------------- #


def test_the_requester_may_not_approve_their_own_request() -> None:
    """The constraint exists in the database; the service must not wait for it.

    ``two_person_rule`` is a CHECK, so a service that only wrote the row would
    answer the operator with a 500 from flush time instead of an explanation.
    """
    device = _device()
    requester = _user()
    row = _session(device, requested_by=requester.id)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        approve_session(session, row, decided_by=requester, now=NOW)

    assert caught.value.code == "two_person_rule"
    assert "second person" in str(caught.value)
    assert (row.status, row.decided_by, row.ends_at) == (MIRROR_PENDING, None, None)


def test_the_requester_may_not_deny_their_own_request_either() -> None:
    """``two_person_rule`` is on ``decided_by``, so it covers every decision.

    Letting a requester deny their own request would either violate the
    constraint or hand the only legitimate denier the illusion that the queue
    clears itself.
    """
    device = _device()
    requester = _user()
    row = _session(device, requested_by=requester.id)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        deny_session(session, row, decided_by=requester, now=NOW)

    assert caught.value.code == "two_person_rule"
    assert row.status == MIRROR_PENDING


def test_two_different_people_can_complete_a_request() -> None:
    """The supported path: one asks, the other agrees, a view opens."""
    device = _device()
    requester = _user(role=UserRole.administrator)
    approver = _user(role=UserRole.owner)
    row = _session(device, requested_by=requester.id)
    session = FakeSession(devices=[device], sessions=[row])

    approve_session(session, row, decided_by=approver, now=NOW)

    assert row.status == MIRROR_ACTIVE
    assert row.decided_by == approver.id
    assert row.decided_at == NOW
    assert row.ends_at == NOW + DEFAULT_SESSION_DURATION
    assert is_live(row, now=NOW)
    # Both people are on the row, which is what the audit question needs.
    assert (row.requested_by, row.decided_by) == (requester.id, approver.id)


def test_a_decided_session_cannot_be_approved_twice() -> None:
    device = _device()
    row = _live(device)
    session = FakeSession(devices=[device], sessions=[row])
    first_ends_at = row.ends_at

    with pytest.raises(MirrorError) as caught:
        approve_session(session, row, decided_by=_user(role=UserRole.owner), now=NOW)

    assert caught.value.code == "not_pending"
    assert row.ends_at == first_ends_at


def test_a_request_that_sat_too_long_in_the_queue_cannot_be_approved() -> None:
    """A request raised before lunch is not a request for the dinner rush.

    Refused rather than deleted: the row is the record that the ask was made,
    and the requester can raise a fresh one against a reason that still holds.
    """
    device = _device()
    stale = NOW - MAX_REQUEST_AGE - datetime.timedelta(minutes=1)
    row = _session(device, requested_by=_user().id, requested_at=stale)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        approve_session(session, row, decided_by=_user(role=UserRole.owner), now=NOW)

    assert caught.value.code == "request_expired"
    assert row.status == MIRROR_PENDING


# --------------------------------------------------------------------------- #
# Gate 3 — role separation
# --------------------------------------------------------------------------- #


def test_an_administrator_may_request_but_never_approve() -> None:
    """The split is the product decision; this pins which side admin is on."""
    admin = _user(role=UserRole.administrator)
    assert has_capability(admin, DEVICE_MIRROR_REQUEST)
    assert not has_capability(admin, DEVICE_MIRROR_APPROVE)

    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session, admin)

    created = client.post(
        f"{_MIRROR}/devices/{device.id}/mirror-requests",
        json={"reason": "Till 1 is throwing an order error"},
    )
    assert created.status_code == 201, created.text
    session_id = created.json()["data"]["id"]

    refused = client.post(f"{_MIRROR}/mirror-requests/{session_id}/approve")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"

    # The queue they cannot act on is not shown to them either.
    assert client.get(f"{_MIRROR}/mirror-requests/pending").status_code == 403
    assert session.sessions[0].status == MIRROR_PENDING


def test_an_owner_can_both_request_and_approve_but_not_both_at_once() -> None:
    """Holding both capabilities is not the same as bypassing the two people."""
    owner = _user(role=UserRole.owner)
    assert has_capability(owner, DEVICE_MIRROR_REQUEST)
    assert has_capability(owner, DEVICE_MIRROR_APPROVE)

    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session, owner)

    created = client.post(
        f"{_MIRROR}/devices/{device.id}/mirror-requests",
        json={"reason": "Till 1 is throwing an order error"},
    )
    assert created.status_code == 201
    session_id = created.json()["data"]["id"]

    own = client.post(f"{_MIRROR}/mirror-requests/{session_id}/approve")
    assert own.status_code == 403
    assert own.json()["error"]["code"] == "two_person_rule"

    # A second owner completes it, and the two-person pair is on the row.
    colleague = _user(role=UserRole.owner)
    app = client.app
    app.dependency_overrides[get_current_user] = lambda: colleague
    approved = client.post(f"{_MIRROR}/mirror-requests/{session_id}/approve")
    assert approved.status_code == 200, approved.text
    row = session.sessions[0]
    assert (row.requested_by, row.decided_by) == (owner.id, colleague.id)
    assert row.status == MIRROR_ACTIVE


def test_a_data_clerk_can_neither_request_nor_approve() -> None:
    """Mirroring is not part of the data-ingest role bundle."""
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session, _user(role=UserRole.operator))

    requested = client.post(
        f"{_MIRROR}/devices/{device.id}/mirror-requests", json={"reason": "sneaky"}
    )
    assert requested.status_code == 403

    approved = client.post(f"{_MIRROR}/mirror-requests/{uuid.uuid4()}/approve")
    assert approved.status_code == 403
    assert session.sessions == []


# --------------------------------------------------------------------------- #
# Gate 4 — the hard expiry
# --------------------------------------------------------------------------- #


def test_a_session_cannot_exceed_the_maximum_duration() -> None:
    """Whatever was asked for, ``ends_at`` is now + the ceiling and no further."""
    device = _device()
    row = _session(device, requested_by=_user().id)
    session = FakeSession(devices=[device], sessions=[row])

    approve_session(
        session,
        row,
        decided_by=_user(role=UserRole.owner),
        duration=datetime.timedelta(hours=8),  # a whole shift
        now=NOW,
    )

    assert row.ends_at == NOW + MAX_SESSION_DURATION
    assert row.ends_at == NOW + datetime.timedelta(minutes=30)
    # The window runs from the decision, not from the request.
    assert row.ends_at - row.decided_at == MAX_SESSION_DURATION


def test_a_session_may_be_shorter_than_the_ceiling() -> None:
    device = _device()
    row = _session(device, requested_by=_user().id)
    session = FakeSession(devices=[device], sessions=[row])

    approve_session(
        session,
        row,
        decided_by=_user(role=UserRole.owner),
        duration=datetime.timedelta(minutes=2),
        now=NOW,
    )

    assert row.ends_at == NOW + datetime.timedelta(minutes=2)


def test_a_zero_length_session_is_refused() -> None:
    device = _device()
    row = _session(device, requested_by=_user().id)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        approve_session(
            session,
            row,
            decided_by=_user(role=UserRole.owner),
            duration=datetime.timedelta(0),
            now=NOW,
        )

    assert caught.value.code == "invalid_duration"
    assert row.status == MIRROR_PENDING


def test_an_expired_session_is_not_treated_as_active() -> None:
    """``ends_at`` is the authority, not ``status``.

    A console that trusted the status column would keep showing "watching" on a
    session whose window closed, and the device would look occupied forever.
    """
    device = _device()
    row = _live(device, minutes_left=0)
    session = FakeSession(devices=[device], sessions=[row])

    assert is_expired(row, now=NOW)
    assert is_live(row, now=NOW) is False
    # ... and it does not hold the device, so a fresh request is possible.
    assert active_session_for(session, device, now=NOW) is None
    assert can_mirror(session, device, now=NOW) == (True, ELIGIBLE)

    fresh = request_session(
        session, device=device, requested_by=_user(), reason="Second look", now=NOW
    )
    assert fresh.status == MIRROR_PENDING


def test_a_session_with_no_end_time_is_refused_by_every_predicate() -> None:
    """A live view with no stated end is not one. Both checks fail closed."""
    device = _device()
    row = _session(
        device,
        requested_by=_user().id,
        status=MIRROR_ACTIVE,
        decided_by=_user(role=UserRole.owner).id,
        ends_at=None,
    )
    session = FakeSession(devices=[device], sessions=[row])

    assert is_expired(row, now=NOW)
    assert is_live(row, now=NOW) is False  # no frames may be delivered
    # ... and it still occupies the device, so nobody else starts looking.
    assert active_session_for(session, device, now=NOW) is row
    assert can_mirror(session, device, now=NOW) == (False, "already_active")


# --------------------------------------------------------------------------- #
# One live view at a time
# --------------------------------------------------------------------------- #


def test_a_second_concurrent_session_is_refused() -> None:
    """Two live views of one screen is not a support feature."""
    device = _device()
    live = _live(device)
    session = FakeSession(devices=[device], sessions=[live])

    assert can_mirror(session, device, now=NOW) == (False, "already_active")

    with pytest.raises(MirrorError) as caught:
        request_session(
            session, device=device, requested_by=_user(), reason="Also looking", now=NOW
        )
    assert caught.value.code == "already_active"
    assert len(session.sessions) == 1


def test_an_approval_that_would_create_a_second_view_is_refused() -> None:
    """Checked at approval too: two approvers can click at the same moment."""
    device = _device()
    first = _live(device)
    second = _session(device, requested_by=_user().id)
    session = FakeSession(devices=[device], sessions=[first, second])

    with pytest.raises(MirrorError) as caught:
        approve_session(session, second, decided_by=_user(role=UserRole.owner), now=NOW)

    assert caught.value.code == "already_active"
    assert second.status == MIRROR_PENDING


# --------------------------------------------------------------------------- #
# Ending
# --------------------------------------------------------------------------- #


def test_ending_is_idempotent_and_keeps_the_first_timestamp() -> None:
    """A retried "end" must never rewrite when a view actually stopped."""
    device = _device()
    approver = _user(role=UserRole.owner)
    row = _live(device)
    session = FakeSession(devices=[device], sessions=[row])

    end_session(session, row, ended_by=approver, now=NOW)
    original = row.ended_at
    assert (row.status, original) == (MIRROR_ENDED, NOW)

    end_session(session, row, ended_by=approver, now=NOW + datetime.timedelta(minutes=5))

    assert row.ended_at == original
    assert [a.action for a in session.audits] == ["mirror.end"]  # no second entry


def test_ending_a_denied_session_is_a_no_op() -> None:
    device = _device()
    owner = _user(role=UserRole.owner)
    row = _session(
        device,
        requested_by=_user().id,
        status=MIRROR_DENIED,
        decided_by=owner.id,
        decided_at=NOW - datetime.timedelta(minutes=1),
    )
    session = FakeSession(devices=[device], sessions=[row])

    end_session(session, row, ended_by=owner, now=NOW)

    assert row.status == MIRROR_DENIED
    assert row.ended_at is None
    assert session.audits == []


def test_the_requester_may_end_their_own_view() -> None:
    """Changing your mind must not require finding a second person."""
    device = _device()
    requester = _user()
    row = _live(device, requested_by=requester.id)
    session = FakeSession(devices=[device], sessions=[row])

    end_session(session, row, ended_by=requester, now=NOW)

    assert row.status == MIRROR_ENDED


def test_a_colleague_without_approval_power_may_not_end_someone_elses_view() -> None:
    """Requesting is not a licence to close a view somebody else is watching."""
    device = _device()
    colleague = _user(role=UserRole.administrator)  # holds REQUEST, not APPROVE
    row = _live(device)
    session = FakeSession(devices=[device], sessions=[row])

    with pytest.raises(MirrorError) as caught:
        end_session(session, row, ended_by=colleague, now=NOW)

    assert caught.value.code == "not_permitted"
    assert row.status == MIRROR_ACTIVE
    assert session.audits == []


def test_a_requester_may_withdraw_their_own_pending_request() -> None:
    """Cancelling an ask is not a decision, so ``decided_by`` stays NULL."""
    device = _device()
    requester = _user()
    row = _session(device, requested_by=requester.id)
    session = FakeSession(devices=[device], sessions=[row])

    end_session(session, row, ended_by=requester, now=NOW)

    assert row.status == MIRROR_ENDED
    assert row.decided_by is None
    assert pending_sessions(session, WS, now=NOW) == []


# --------------------------------------------------------------------------- #
# Reasons
# --------------------------------------------------------------------------- #


def test_a_request_without_a_reason_is_refused() -> None:
    """The reason is what the second person reads before agreeing."""
    device = _device()
    session = FakeSession(devices=[device])

    for bad in ("", "   ", None):
        with pytest.raises(MirrorError) as caught:
            request_session(session, device=device, requested_by=_user(), reason=bad, now=NOW)
        assert caught.value.code == "reason_required", bad
    assert session.sessions == []


def test_an_unbounded_reason_is_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    with pytest.raises(MirrorError) as caught:
        request_session(
            session,
            device=device,
            requested_by=_user(),
            reason="x" * (MAX_REASON_LENGTH + 1),
            now=NOW,
        )

    assert caught.value.code == "reason_too_long"


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


def test_a_device_in_another_workspace_is_404_not_403() -> None:
    """A 403 would confirm the row exists — the oracle isolation exists to stop."""
    foreign = _device(OTHER_WS, label="Their till", consent_at=None)
    session = FakeSession(devices=[foreign])
    client = _client(session, _user(WS, role=UserRole.owner))

    attempts = (
        ("get", f"{_MIRROR}/devices/{foreign.id}/mirror-requests"),
        ("get", f"{_MIRROR}/devices/{foreign.id}/mirror-eligibility"),
        ("post", f"{_MIRROR}/devices/{foreign.id}/mirror-requests"),
        ("post", f"{_MIRROR}/devices/{foreign.id}/mirror-consent"),
    )
    for method, path in attempts:
        resp = client.request(method, path, json={"reason": "x", "granted": True})
        assert resp.status_code == 404, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "not_found"
    # Nothing was mutated on the way out — in particular no consent was granted.
    assert foreign.mirroring_consent_at is None
    assert session.sessions == []


def test_a_mirror_session_in_another_workspace_is_404_not_403() -> None:
    foreign_device = _device(OTHER_WS)
    row = _session(foreign_device, requested_by=_user(OTHER_WS).id)
    session = FakeSession(devices=[foreign_device], sessions=[row])
    client = _client(session, _user(WS, role=UserRole.owner))

    for action in ("approve", "deny", "end"):
        resp = client.post(f"{_MIRROR}/mirror-requests/{row.id}/{action}")
        assert resp.status_code == 404, action
        assert resp.json()["error"]["code"] == "not_found"
    assert (row.status, row.decided_by) == (MIRROR_PENDING, None)


def test_the_approval_queue_only_shows_this_workspace() -> None:
    mine = _device(WS)
    theirs = _device(OTHER_WS)
    mine_pending = _session(
        mine, requested_by=_user().id, requested_at=WALL - datetime.timedelta(minutes=4)
    )
    theirs_pending = _session(
        theirs,
        requested_by=_user(OTHER_WS).id,
        requested_at=WALL - datetime.timedelta(minutes=2),
    )
    session = FakeSession(devices=[mine, theirs], sessions=[mine_pending, theirs_pending])
    client = _client(session, _user(WS, role=UserRole.owner))

    queue = client.get(f"{_MIRROR}/mirror-requests/pending")

    assert queue.status_code == 200, queue.text
    ids = [row["id"] for row in queue.json()["data"]]
    assert ids == [str(mine_pending.id)]
    assert str(theirs_pending.id) not in ids


def test_the_queue_is_ordered_oldest_first_and_hides_over_age_requests() -> None:
    device = _device(WS)
    stale = _session(
        device,
        requested_by=_user().id,
        requested_at=NOW - MAX_REQUEST_AGE - datetime.timedelta(minutes=1),
    )
    older = _session(
        device, requested_by=_user().id, requested_at=NOW - datetime.timedelta(minutes=30)
    )
    newer = _session(
        device, requested_by=_user().id, requested_at=NOW - datetime.timedelta(minutes=5)
    )
    session = FakeSession(devices=[device], sessions=[stale, older, newer])

    rows = pending_sessions(session, WS, now=NOW)

    assert [row.id for row in rows] == [older.id, newer.id]
    # The stale row is hidden from the queue, not deleted: it is the record that
    # the ask was made.
    assert stale.id in {row.id for row in session.sessions}


def test_the_queue_flags_the_callers_own_request_as_unapprovable() -> None:
    """Greyed out in the console, rather than a button that always fails."""
    owner = _user(WS, role=UserRole.owner)
    device = _device(WS)
    mine = _session(device, requested_by=owner.id, requested_at=WALL)
    theirs = _session(device, requested_by=_user().id, requested_at=WALL)
    session = FakeSession(devices=[device], sessions=[theirs, mine])

    data = _client(session, owner).get(f"{_MIRROR}/mirror-requests/pending").json()

    by_id = {row["id"]: row for row in data["data"]}
    assert by_id[str(mine.id)]["approvable"] is False
    assert by_id[str(theirs.id)]["approvable"] is True
    assert data["cannot_approve"] == [str(mine.id)]


def test_a_viewer_who_cannot_approve_is_never_offered_the_approve_button() -> None:
    """``approvable`` means "permitted", not merely "not the requester".

    The administrator is the caller this actually matters for. It holds
    ``devices:read`` — so it can read any device's request history — and holds
    ``device_mirror:request`` but deliberately *not* ``device_mirror:approve``.
    It is therefore shown other people's requests, and a flag that only asked
    "is this yours?" would mark every one of them approvable, offering a button
    whose only possible outcome is a 403. The flag is what the console renders,
    so it has to carry the whole answer.
    """
    admin = _user(WS, role=UserRole.administrator)
    device = _device(WS)
    someone_elses = _session(
        device, requested_by=_user(WS, role=UserRole.owner).id, requested_at=WALL
    )
    session = FakeSession(devices=[device], sessions=[someone_elses])

    # The queue is gated on approve, so the administrator is turned away there.
    queue = _client(session, admin).get(f"{_MIRROR}/mirror-requests/pending")
    assert queue.status_code == 403

    # On the history it *is* allowed to read, somebody else's request — the one
    # case the flag used to get wrong — reports no action.
    history = _client(session, admin).get(f"{_MIRROR}/devices/{device.id}/mirror-requests")
    assert history.status_code == 200
    row = history.json()["data"][0]
    assert row["requested_by"] != str(admin.id)
    assert row["approvable"] is False


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_a_request_does_not_open_a_view_and_says_so() -> None:
    """A 201 means "in the queue", not "you can see the screen"."""
    device = _device()
    requester = _user()
    session = FakeSession(devices=[device])
    client = _client(session, requester)

    resp = client.post(
        f"{_MIRROR}/devices/{device.id}/mirror-requests",
        json={"reason": "Till 1 is throwing an order error"},
    )

    assert resp.status_code == 201, resp.text
    row = session.sessions[0]
    assert (row.status, row.decided_by, row.ends_at) == (MIRROR_PENDING, None, None)
    assert row.consent_at == device.mirroring_consent_at  # the grant it was raised under
    assert row.requested_at is not None
    assert row.reason == "Till 1 is throwing an order error"

    body = resp.json()["data"]
    assert body["awaiting_second_person"] is True
    assert "views nothing yet" in body["notice"]
    assert body["requested_by"] == str(requester.id)
    assert body["live"] is False


def test_a_request_over_http_reports_the_exact_failing_condition() -> None:
    device = _device(consent_at=None)
    session = FakeSession(devices=[device])
    client = _client(session, _user())

    resp = client.post(f"{_MIRROR}/devices/{device.id}/mirror-requests", json={"reason": "looking"})

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "no_consent"
    assert "has not consented" in error["message"]
    assert session.sessions == []


def test_a_request_over_http_demands_a_reason() -> None:
    device = _device()
    client = _client(FakeSession(devices=[device]), _user())

    for body in ({}, {"reason": ""}, {"reason": "   "}, {"reason": None}):
        resp = client.post(f"{_MIRROR}/devices/{device.id}/mirror-requests", json=body)
        assert resp.status_code == 400, body
        assert resp.json()["error"]["field"] == "reason"


def test_a_request_cannot_ask_for_its_own_window() -> None:
    """The approver sets the window, and a dropped field is a broken promise.

    A requester who sent "just two minutes" and was silently ignored would
    believe something the API never agreed to — and the window is exactly the
    part of this feature that decides how long somebody's screen is watched.
    """
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session, _user())

    resp = client.post(
        f"{_MIRROR}/devices/{device.id}/mirror-requests",
        json={"reason": "Till error", "duration_minutes": 2},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == "duration_minutes"
    assert session.sessions == []


def test_approval_over_http_caps_the_window_and_reports_it() -> None:
    device = _device()
    row = _session(device, requested_by=_user().id, requested_at=WALL)
    session = FakeSession(devices=[device], sessions=[row])
    client = _client(session, _user(role=UserRole.owner))

    before = datetime.datetime.now(datetime.UTC)
    resp = client.post(
        f"{_MIRROR}/mirror-requests/{row.id}/approve", json={"duration_minutes": 600}
    )
    after = datetime.datetime.now(datetime.UTC)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == MIRROR_ACTIVE
    assert data["live"] is True
    assert data["max_session_minutes"] == 30
    assert data["decided_by"] == str(session.audits[-1].actor_id)
    ends_at = datetime.datetime.fromisoformat(str(data["ends_at"]))
    assert before + MAX_SESSION_DURATION <= ends_at <= after + MAX_SESSION_DURATION


def test_a_malformed_duration_is_refused_rather_than_defaulted() -> None:
    device = _device()
    row = _session(device, requested_by=_user().id, requested_at=WALL)
    session = FakeSession(devices=[device], sessions=[row])
    client = _client(session, _user(role=UserRole.owner))

    for bad in ("30", True, 0, -5):
        resp = client.post(
            f"{_MIRROR}/mirror-requests/{row.id}/approve", json={"duration_minutes": bad}
        )
        assert resp.status_code == 400, bad
        assert resp.json()["error"]["field"] == "duration_minutes"
    assert row.status == MIRROR_PENDING


def test_mirror_eligibility_explains_the_button() -> None:
    """The console must be able to say which of the four gates is missing."""
    device = _device(consent_at=None)
    session = FakeSession(devices=[device])
    client = _client(session, _user(role=UserRole.administrator))

    resp = client.get(f"{_MIRROR}/devices/{device.id}/mirror-eligibility")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["eligible"] is False
    assert data["reason"] == "no_consent"
    assert "has not consented" in data["message"]
    conditions = {c["condition"]: c for c in data["conditions"]}
    assert conditions["customer_consent"]["ok"] is False
    assert conditions["device_in_service"]["ok"] is True
    assert conditions["no_live_view"]["ok"] is True
    # The seat's own reach, so the console can disable the wrong button.
    assert data["can_request"] is True
    assert data["can_approve"] is False
    assert data["max_session_minutes"] == 30
    assert data["consent_at"] is None
    assert data["active_session_id"] is None


def test_mirror_eligibility_names_a_live_view_as_the_obstacle() -> None:
    device = _device()
    live = _live(device, minutes_left=5, now=WALL)
    session = FakeSession(devices=[device], sessions=[live])
    client = _client(session, _user(role=UserRole.owner))

    data = client.get(f"{_MIRROR}/devices/{device.id}/mirror-eligibility").json()["data"]

    assert data["reason"] == "already_active"
    assert data["active_session_id"] == str(live.id)
    conditions = {c["condition"]: c for c in data["conditions"]}
    assert conditions["no_live_view"]["ok"] is False
    assert conditions["customer_consent"]["ok"] is True


def test_consent_withdrawal_over_http_ends_the_live_view() -> None:
    device = _device()
    live = _live(device, minutes_left=5, now=WALL)
    session = FakeSession(devices=[device], sessions=[live])
    client = _client(session, _user(role=UserRole.owner))
    device_path = f"{_MIRROR}/devices/{device.id}/mirror-consent"

    granted = client.post(device_path, json={"granted": True})
    assert granted.status_code == 200, granted.text
    assert granted.json()["data"]["consent_at"] is not None

    withdrawn = client.post(device_path, json={"granted": False})

    assert withdrawn.status_code == 200, withdrawn.text
    data = withdrawn.json()["data"]
    assert data["granted"] is False
    assert data["consent_at"] is None
    assert data["sessions_ended"] == 1
    assert device.mirroring_consent_at is None
    assert live.status == MIRROR_ENDED
    assert live.ended_at is not None


def test_consent_requires_a_boolean_and_manage_power() -> None:
    device = _device()
    path = f"{_MIRROR}/devices/{device.id}/mirror-consent"
    client = _client(FakeSession(devices=[device]), _user(role=UserRole.owner))

    assert client.post(path, json={}).status_code == 400
    assert client.post(path, json={"granted": "yes"}).status_code == 400
    assert client.post(path, json={"granted": True}).status_code == 200

    # A data clerk administers no fleet, so cannot write down what a customer
    # agreed to either.
    clerk = _client(FakeSession(devices=[device]), _user(role=UserRole.operator))
    assert clerk.post(path, json={"granted": True}).status_code == 403


def test_the_device_history_is_newest_first() -> None:
    device = _device(WS)
    old = _session(device, requested_by=_user().id, requested_at=WALL - datetime.timedelta(hours=2))
    new = _session(device, requested_by=_user().id, requested_at=WALL - datetime.timedelta(hours=1))
    session = FakeSession(devices=[device], sessions=[old, new])

    rows = (
        _client(session, _user(WS, role=UserRole.owner))
        .get(f"{_MIRROR}/devices/{device.id}/mirror-requests")
        .json()["data"]
    )

    assert [row["id"] for row in rows] == [str(new.id), str(old.id)]
    assert rows[0]["reason"] == "Till showing an order error"


def test_ending_over_http_is_available_to_the_requester() -> None:
    device = _device()
    requester = _user()
    live = _live(device, requested_by=requester.id, minutes_left=5, now=WALL)
    session = FakeSession(devices=[device], sessions=[live])
    client = _client(session, requester)

    ended = client.post(f"{_MIRROR}/mirror-requests/{live.id}/end")
    again = client.post(f"{_MIRROR}/mirror-requests/{live.id}/end")

    assert ended.status_code == 200, ended.text
    assert ended.json()["data"]["status"] == MIRROR_ENDED
    assert again.status_code == 200
    assert again.json()["data"]["ended_at"] == ended.json()["data"]["ended_at"]


# --------------------------------------------------------------------------- #
# Audit coverage
# --------------------------------------------------------------------------- #


def test_every_state_change_writes_its_own_audit_row() -> None:
    """The middleware records method+path; this records who and what.

    ``app/middleware/audit`` already writes a row for every non-GET here,
    because every route is JWT-authenticated. That row says which route was
    called; it cannot say that an approval records two different people, or that
    a withdrawal stopped a live view. So the service layer writes its own, and
    these are the actions an auditor will look for by name.
    """
    device = _device()
    requester = _user()
    owner = _user(role=UserRole.owner)
    session = FakeSession(devices=[device])

    row = request_session(
        session, device=device, requested_by=requester, reason="Till error", now=NOW
    )
    approve_session(session, row, decided_by=owner, now=NOW)
    end_session(session, row, ended_by=owner, now=NOW + datetime.timedelta(minutes=2))
    withdraw_consent(session, device, now=NOW + datetime.timedelta(minutes=3))

    assert [a.action for a in session.audits] == [
        "mirror.request",
        "mirror.approve",
        "mirror.end",
        "mirror.consent_withdrawn",
    ]
    approval = next(a for a in session.audits if a.action == "mirror.approve")
    # The two-person pair is on the row and in the log, so "who agreed to this
    # view" never depends on the request log being complete.
    assert approval.meta_data is not None
    assert approval.meta_data["requested_by"] == str(requester.id)
    assert approval.meta_data["decided_by"] == str(owner.id)
    assert approval.meta_data["device_id"] == str(device.id)
    assert approval.actor_id == owner.id


def test_every_mirror_write_route_needs_a_person_not_a_device() -> None:
    """The audit coverage argument, asserted rather than assumed.

    ``app.middleware.audit`` identifies the actor by decoding the JWT, so a
    mirror route that accepted a device credential would write an audit row with
    no actor — the one thing a screen-watching decision must never have.
    """
    client = TestClient(_app_without_overrides())
    device_id, session_id = uuid.uuid4(), uuid.uuid4()

    for method, path in (
        ("post", f"{_MIRROR}/devices/{device_id}/mirror-requests"),
        ("post", f"{_MIRROR}/devices/{device_id}/mirror-consent"),
        ("post", f"{_MIRROR}/mirror-requests/{session_id}/approve"),
        ("post", f"{_MIRROR}/mirror-requests/{session_id}/deny"),
        ("post", f"{_MIRROR}/mirror-requests/{session_id}/end"),
    ):
        assert client.request(method, path, json={}).status_code == 401, path


def test_the_audit_middleware_records_a_mirror_write_with_its_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the real middleware over a mirror route and read the row it writes.

    Coverage is claimed in the router docstring, so it is measured here rather
    than asserted in prose. The middleware opens its own session, so its factory
    is stubbed and the resulting ``AuditLog`` is inspected: a mirror approval
    has to land in ``audit_log`` attributed to the person who pressed the
    button, in the right workspace, under a POST.
    """
    user = _user(role=UserRole.owner)
    token = create_access_token(
        subject=str(user.id), workspace_id=str(WS), role=UserRole.owner.value
    )
    session = FakeSession(devices=[_device(WS)])
    writer = AuditCapture()
    monkeypatch.setattr("app.middleware.audit.get_session_factory", lambda: writer)

    app = _app_without_overrides()
    app.dependency_overrides[get_db] = lambda: session  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    with TestClient(app) as client:
        resp = client.post(
            f"{_MIRROR}/devices/{session.devices[0].id}/mirror-requests",
            json={"reason": "Till 1 is throwing an order error"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text

    assert len(writer.rows) == 1
    row = writer.rows[0]
    assert row.workspace_id == WS
    assert row.actor_id == user.id
    assert row.client in ("web", "mobile", "api", "system")
    assert row.action.startswith("POST ")
    assert "mirror-requests" in row.target
    assert row.meta_data is not None
    assert row.meta_data["status_code"] == 201
    # Two independent records, from two places: the middleware's route-level row
    # above, and the service's decision-level row here. The service row is the
    # one that knows *what* was asked for; the middleware row is the one that
    # exists even if the service forgets.
    assert [a.action for a in session.audits] == ["mirror.request"]


class AuditCapture:
    """A session factory stand-in that keeps whatever the middleware adds."""

    def __init__(self) -> None:
        self.rows: list[AuditLog] = []
        self.session = self

    def __call__(self) -> AuditCapture:
        return self

    def add(self, obj: Any) -> None:
        if isinstance(obj, AuditLog):
            self.rows.append(obj)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _app_without_overrides() -> Any:
    app = create_app()
    app.include_router(router)
    return app


# --------------------------------------------------------------------------- #
# The router's own surface, and the double's own honesty
# --------------------------------------------------------------------------- #


def test_every_documented_route_exists_with_the_declared_prefix() -> None:
    assert router.prefix == _MIRROR
    routes = {
        (method, getattr(route, "path", None))
        for route in router.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert routes == {
        ("POST", "/api/v1/device-mirror/devices/{device_id}/mirror-requests"),
        ("GET", "/api/v1/device-mirror/devices/{device_id}/mirror-requests"),
        ("GET", "/api/v1/device-mirror/devices/{device_id}/mirror-eligibility"),
        ("POST", "/api/v1/device-mirror/devices/{device_id}/mirror-consent"),
        ("GET", "/api/v1/device-mirror/mirror-requests/pending"),
        ("POST", "/api/v1/device-mirror/mirror-requests/{session_id}/approve"),
        ("POST", "/api/v1/device-mirror/mirror-requests/{session_id}/deny"),
        ("POST", "/api/v1/device-mirror/mirror-requests/{session_id}/end"),
    }


def test_every_reason_code_has_text_an_operator_can_read() -> None:
    """A refusal with no explanation gets retried, and a retry is a new attempt."""
    for code in (
        "no_consent",
        "consent_withdrawn",
        "consent_changed",
        "device_revoked",
        "device_quarantined",
        "device_inactive",
        "already_active",
        "two_person_rule",
        "not_pending",
        "not_permitted",
        "request_expired",
        "invalid_duration",
        "reason_required",
        "reason_too_long",
        "device_missing",
    ):
        assert MESSAGES[code].strip(), code
        assert MESSAGES[code] == message_for(code), code


def test_the_session_double_really_filters() -> None:
    """Guard on the double: a dropped predicate must not pass unnoticed.

    Without this, a service that stopped scoping by ``workspace_id`` would still
    pass every isolation test above, because each of them only ever holds one
    workspace's rows at a time.
    """
    mine = _device(WS)
    theirs = _device(OTHER_WS)
    their_session = _session(theirs, requested_by=_user(OTHER_WS).id)
    session = FakeSession(devices=[mine, theirs], sessions=[their_session])

    matched = session.scalars(select(Device).where(Device.workspace_id == WS)).all()
    assert [row.id for row in matched] == [mine.id]
    # The device belongs to somebody else, so no history of it is ours to read.
    assert list_sessions(session, workspace_id=WS, device_id=theirs.id) == []
    assert [row.id for row in list_sessions(session, workspace_id=WS, device_id=mine.id)] == []
    # ... and the same holds for the queue, which is the other workspace-wide read.
    assert pending_sessions(session, WS, now=NOW) == []
