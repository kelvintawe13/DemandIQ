"""Device grant tests — the AND-not-OR discipline, asserted rather than assumed.

The device tables are Postgres-typed (``UUID``, ``text[]``, ``timestamptz``),
so there is no in-memory engine to run them on and every test here stands in for
the session. The double is the one from ``tests/test_devices.py``, re-stated
because ``tests`` is not a package: it reads the *statement* — the entity, the
equality criteria, the ``ORDER BY`` — and applies them in Python, so the real
``WHERE`` clauses the router and the service emit are what filter the rows. That
matters here more than elsewhere: a query that lost its ``workspace_id``
predicate is precisely the bug that would let one tenant grant a capability on
another tenant's till, and
:func:`test_the_session_double_really_filters_by_workspace` guards the double
itself against being the reason that goes unnoticed.

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
from app.models.device import DEVICE_STATUS_ACTIVE, DEVICE_STATUS_REVOKED, Device, DeviceGrant
from app.models.location import Location
from app.models.user import User, UserRole
from app.routers.device_grants import router
from app.security.permissions import (
    DEVICE_GRANTS_MANAGE,
    DEVICE_SYNC,
    DEVICES_READ,
    POLICIES_WRITE,
    QUALITY_CONFIGURE,
    REPORTS_READ,
    has_capability,
)
from app.services.device_grants import (
    GRANT_ACTIVE,
    GRANT_EXPIRED,
    GRANT_REVOKED,
    READ_ONLY_CAPABILITIES,
    REGISTERED_CAPABILITIES,
    DeviceGrantError,
    describe_grants,
    device_may,
    effective_scopes,
    effective_scopes_report,
    grant_capability,
    grant_state,
    revoke_grant,
)

WS = uuid.uuid4()
OTHER_WS = uuid.uuid4()

NOW = datetime.datetime(2026, 3, 1, 12, 0, tzinfo=datetime.UTC)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _user(workspace_id: uuid.UUID = WS, role: UserRole = UserRole.administrator) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Fleet Admin",
        email=f"{role.value}-{workspace_id.hex[:6]}@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _device(
    workspace_id: uuid.UUID = WS,
    *,
    label: str = "Till 1",
    scopes: list[str] | None = None,
    status: str = DEVICE_STATUS_ACTIVE,
    location_id: uuid.UUID | None = None,
) -> Device:
    return Device(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        location_id=location_id,
        label=label,
        device_type="pos",
        platform="android",
        app_version=None,
        enrolled_at=datetime.datetime.now(datetime.UTC),
        enrolled_by=None,
        credential_prefix="did_000000000000",
        credential_hash="$2b$12$dummy",
        scopes=[DEVICE_SYNC] if scopes is None else scopes,
        status=status,
        last_seen_at=None,
        revoked_at=None,
        mirroring_consent_at=None,
    )


def _location(workspace_id: uuid.UUID = WS) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test Bistro",
        area="Nyarutarama",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _grant(
    device: Device,
    capability: str = POLICIES_WRITE,
    *,
    granted_by: uuid.UUID | None = None,
    granted_at: datetime.datetime | None = None,
    expires_at: datetime.datetime | None = None,
    revoked_at: datetime.datetime | None = None,
    reason: str | None = "Nightly policy refresh",
) -> DeviceGrant:
    return DeviceGrant(
        id=uuid.uuid4(),
        workspace_id=device.workspace_id,
        device_id=device.id,
        capability=capability,
        granted_by=granted_by or uuid.uuid4(),
        granted_at=granted_at or (NOW - datetime.timedelta(days=1)),
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_by=uuid.uuid4() if revoked_at else None,
        reason=reason,
    )


# --------------------------------------------------------------------------- #
# The session double
# --------------------------------------------------------------------------- #


def _criteria(stmt: Any) -> list[tuple[str, str, Any]]:
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
    """A ``Session`` that filters the statements the grant layer emits."""

    def __init__(
        self,
        *,
        devices: list[Device] | None = None,
        grants: list[DeviceGrant] | None = None,
        locations: list[Location] | None = None,
    ) -> None:
        self.devices = list(devices or [])
        self.grants = list(grants or [])
        self.locations = list(locations or [])
        self.added: list[Any] = []
        self.flushes = 0

    def _pool(self, model: Any) -> list[Any] | None:
        pools: dict[Any, list[Any]] = {
            Device: self.devices,
            Location: self.locations,
            DeviceGrant: self.grants,
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
        for column, descending in reversed(_order_spec(stmt)):
            rows.sort(key=partial(_sort_key, column=column), reverse=descending)
        limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
        return _Rows(rows[: int(limit)] if limit is not None else rows)

    def add(self, obj: Any) -> None:
        if isinstance(obj, DeviceGrant):
            self.grants.append(obj)
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _client(session: FakeSession, user: User | None = None) -> TestClient:
    """The real application with this router mounted and the session stubbed.

    The router is attached here rather than relying on ``create_app`` having
    registered it, so these tests assert against the module under test and stay
    green whether or not the application factory has been wired up yet.
    """
    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: (user or _user())  # type: ignore[assignment]
    return TestClient(app)


def _error_of(response: Any) -> dict[str, Any]:
    return dict(response.json()["error"])


# --------------------------------------------------------------------------- #
# The decision: a grant widens, and the union is explicit
# --------------------------------------------------------------------------- #


def test_effective_scopes_are_base_plus_live_grants() -> None:
    """The union, stated out loud: a grant is the only way past ``device:sync``.

    A grant that could only narrow could narrow nothing — ``DEVICE_SCOPES`` is
    the single value enrollment accepts, so the widening case above is the only
    case the layer can express, and it has to be this one.
    """
    device = _device()
    grant = _grant(device, DEVICES_READ)
    session = FakeSession(devices=[device], grants=[grant])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC, DEVICES_READ})
    assert device.scopes == [DEVICE_SYNC], "the base list is a floor, not the answer"
    assert device_may(session, device, DEVICES_READ, now=NOW) is True


def test_a_grant_does_not_lend_the_capabilities_next_to_it() -> None:
    """Holding one capability is not evidence of another.

    This is the OR bug the module exists to prevent, asserted on the resolution
    itself rather than on a call site: a device granted ``devices:read`` still has
    no ``policies:write``, even though both came from the same table.
    """
    device = _device()
    session = FakeSession(devices=[device], grants=[_grant(device, DEVICES_READ)])

    effective = effective_scopes(session, device, now=NOW)
    assert DEVICES_READ in effective
    assert POLICIES_WRITE not in effective
    assert device_may(session, device, POLICIES_WRITE, now=NOW) is False


def test_the_registry_is_the_whole_permission_vocabulary() -> None:
    """Not ``DEVICE_SCOPES``, and not ``capability_groups()``.

    ``capability_groups()`` is a display helper and already omits
    ``quality:configure``; validating grants against it would refuse a real
    capability. The registry is listed name by name for the opposite reason — a
    registry that lost a capability would refuse a grant that should work.
    """
    assert DEVICE_SYNC in REGISTERED_CAPABILITIES
    assert DEVICES_READ in REGISTERED_CAPABILITIES
    assert QUALITY_CONFIGURE in REGISTERED_CAPABILITIES
    assert DEVICE_SYNC not in READ_ONLY_CAPABILITIES
    assert REPORTS_READ in READ_ONLY_CAPABILITIES
    # The read-only set is the registry's ``:read`` verb and nothing else.
    assert READ_ONLY_CAPABILITIES == {
        capability for capability in REGISTERED_CAPABILITIES if capability.endswith(":read")
    }
    assert "quality:configure" not in READ_ONLY_CAPABILITIES


# --------------------------------------------------------------------------- #
# Liveness
# --------------------------------------------------------------------------- #


def test_an_expired_grant_contributes_nothing_and_the_row_survives() -> None:
    device = _device()
    expired = _grant(device, expires_at=NOW - datetime.timedelta(seconds=1))
    session = FakeSession(devices=[device], grants=[expired])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC})
    # The row is the evidence a permission existed; it is compared against, not
    # filtered out of the query and not deleted.
    assert session.grants == [expired]
    assert expired.revoked_at is None
    views = describe_grants(session, device, now=NOW)
    assert [(view.state, view.effective) for view in views] == [(GRANT_EXPIRED, False)]
    assert "expired at" in views[0].detail


def test_a_revoked_grant_contributes_nothing() -> None:
    device = _device()
    revoked = _grant(device, revoked_at=NOW - datetime.timedelta(hours=1))
    session = FakeSession(devices=[device], grants=[revoked])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC})
    view = describe_grants(session, device, now=NOW)[0]
    assert (view.state, view.effective) == (GRANT_REVOKED, False)
    assert "revoked at" in view.detail


def test_revocation_outranks_expiry_when_both_apply() -> None:
    """A deliberate withdrawal is the fact an investigator is looking for."""
    device = _device()
    both = _grant(
        device,
        expires_at=NOW - datetime.timedelta(days=1),
        revoked_at=NOW - datetime.timedelta(hours=1),
    )
    session = FakeSession(devices=[device], grants=[both])

    view = describe_grants(session, device, now=NOW)[0]
    assert view.state == GRANT_REVOKED
    assert grant_state(both, now=NOW) == GRANT_REVOKED


def test_the_expiry_boundary_is_half_open() -> None:
    """``expires_at == now`` is expired; one microsecond later is live.

    The rule: the instant named by ``expires_at`` is the *first* instant at which
    the permission no longer holds, so a grant runs ``[granted_at,
    expires_at)``. A "one hour" grant is therefore exactly one hour, and no
    permission is ever in effect at the instant it lapses.
    """
    device = _device()
    at_the_line = _grant(device, expires_at=NOW)
    session = FakeSession(devices=[device], grants=[at_the_line])

    assert grant_state(at_the_line, now=NOW) == GRANT_EXPIRED
    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC})

    just_before = _grant(device, expires_at=NOW + datetime.timedelta(microseconds=1))
    live = FakeSession(devices=[device], grants=[just_before])
    assert grant_state(just_before, now=NOW) == GRANT_ACTIVE
    assert effective_scopes(live, device, now=NOW) == frozenset({DEVICE_SYNC, POLICIES_WRITE})


def test_a_grant_with_no_expiry_does_not_lapse() -> None:
    device = _device()
    forever = _grant(device, expires_at=None)
    session = FakeSession(devices=[device], grants=[forever])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC, POLICIES_WRITE})
    assert describe_grants(session, device, now=NOW)[0].detail.startswith("in effect since")


# --------------------------------------------------------------------------- #
# The location ceiling — the one genuine AND
# --------------------------------------------------------------------------- #


def test_a_bound_location_can_cap_what_a_grant_provides() -> None:
    device = _device()
    location = _location()
    device.location_id = location.id
    location.device_scopes = [DEVICE_SYNC]  # type: ignore[attr-defined]
    session = FakeSession(devices=[device], grants=[_grant(device)], locations=[location])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC})


def test_a_location_that_declares_nothing_imposes_no_ceiling() -> None:
    """Absence of a column means "unbounded", not "nothing permitted".

    Getting this backwards would leave every bound device unable to do anything,
    which is the failure mode that makes a safety interlock get switched off.
    """
    device = _device()
    location = _location()
    device.location_id = location.id
    session = FakeSession(devices=[device], grants=[_grant(device)], locations=[location])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC, POLICIES_WRITE})


def test_the_ceiling_also_reads_a_plain_scopes_column() -> None:
    device = _device()
    location = _location()
    device.location_id = location.id
    location.scopes = [DEVICE_SYNC]  # type: ignore[attr-defined]
    session = FakeSession(devices=[device], grants=[_grant(device)], locations=[location])

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC})


def test_an_unbound_device_has_no_ceiling() -> None:
    device = _device(location_id=None)
    session = FakeSession(devices=[device], grants=[_grant(device)], locations=[_location()])

    assert POLICIES_WRITE in effective_scopes(session, device, now=NOW)


# --------------------------------------------------------------------------- #
# Grant writes: what the service refuses
# --------------------------------------------------------------------------- #


def test_a_capability_outside_the_registry_is_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    for bad in ("devices:reed", "Device:Read", "device:sync ", "", "sudo"):
        with pytest.raises(DeviceGrantError) as caught:
            grant_capability(
                session, device=device, capability=bad, granted_by=uuid.uuid4(), now=NOW
            )
        assert caught.value.code == "unknown_capability"
        assert caught.value.status_code == 400
        assert caught.value.field == "capability"
    assert session.grants == []


def test_a_non_read_only_grant_without_a_reason_is_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    for reason in (None, "", "   "):
        with pytest.raises(DeviceGrantError) as caught:
            grant_capability(
                session,
                device=device,
                capability=POLICIES_WRITE,
                granted_by=uuid.uuid4(),
                reason=reason,
                now=NOW,
            )
        assert caught.value.code == "reason_required"
        assert caught.value.field == "reason"
    assert session.grants == []


def test_a_read_only_grant_needs_no_reason() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    view = grant_capability(
        session, device=device, capability=REPORTS_READ, granted_by=uuid.uuid4(), now=NOW
    )
    assert view.reason is None
    assert view.effective is True


def test_an_expiry_that_has_already_passed_is_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    for expires_at in (NOW, NOW - datetime.timedelta(minutes=1)):
        with pytest.raises(DeviceGrantError) as caught:
            grant_capability(
                session,
                device=device,
                capability=REPORTS_READ,
                granted_by=uuid.uuid4(),
                expires_at=expires_at,
                now=NOW,
            )
        assert caught.value.code == "expiry_in_past"
    assert session.grants == []


def test_a_naive_expiry_is_refused() -> None:
    """A naive stamp is UTC or local and the server cannot tell which."""
    device = _device()
    session = FakeSession(devices=[device])

    with pytest.raises(DeviceGrantError) as caught:
        grant_capability(
            session,
            device=device,
            capability=REPORTS_READ,
            granted_by=uuid.uuid4(),
            expires_at=datetime.datetime(2026, 4, 1, 12, 0),  # noqa: DTZ001 — the defect under test
            now=NOW,
        )
    assert caught.value.code == "expiry_requires_timezone"


def test_granting_a_base_scope_is_refused() -> None:
    """It would be a row that looks revocable and is not.

    ``device:sync`` comes from ``devices.scopes`` and cannot be time-boxed or
    withdrawn by this layer, so storing a grant for it would produce a permission
    whose revocation silently does nothing. The request is validated before the
    device is consulted, so a base scope asked for without a reason is refused
    for the missing reason first.
    """
    device = _device()
    session = FakeSession(devices=[device])

    with pytest.raises(DeviceGrantError) as caught:
        grant_capability(
            session, device=device, capability=DEVICE_SYNC, granted_by=uuid.uuid4(), now=NOW
        )
    assert caught.value.code == "reason_required"

    with pytest.raises(DeviceGrantError) as caught:
        grant_capability(
            session,
            device=device,
            capability=DEVICE_SYNC,
            granted_by=uuid.uuid4(),
            reason="Because I said so",
            now=NOW,
        )
    assert caught.value.code == "already_a_base_scope"
    assert caught.value.status_code == 409
    assert session.grants == []


def test_a_second_live_grant_for_the_same_capability_is_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device], grants=[_grant(device)])

    with pytest.raises(DeviceGrantError) as caught:
        grant_capability(
            session,
            device=device,
            capability=POLICIES_WRITE,
            granted_by=uuid.uuid4(),
            reason="Second attempt",
            now=NOW,
        )
    assert caught.value.code == "already_granted"
    assert len(session.grants) == 1


def test_a_revoked_device_cannot_be_granted() -> None:
    device = _device(status=DEVICE_STATUS_REVOKED)
    device.revoked_at = NOW - datetime.timedelta(days=1)
    session = FakeSession(devices=[device])

    with pytest.raises(DeviceGrantError) as caught:
        grant_capability(
            session,
            device=device,
            capability=REPORTS_READ,
            granted_by=uuid.uuid4(),
            now=NOW,
        )
    assert caught.value.code == "device_revoked"
    assert caught.value.status_code == 409


def test_a_revocation_keeps_the_row_and_the_first_timestamp() -> None:
    device = _device()
    grant = _grant(device)
    session = FakeSession(devices=[device], grants=[grant])

    first = revoke_grant(
        session, device=device, grant_id=grant.id, revoked_by=uuid.uuid4(), now=NOW
    )
    again = revoke_grant(
        session,
        device=device,
        grant_id=grant.id,
        revoked_by=uuid.uuid4(),
        now=NOW + datetime.timedelta(minutes=5),
    )
    assert first.revoked_at == NOW
    assert again.revoked_at == NOW, "a second click must not move the evidence"
    assert again.revoked_by == first.revoked_by


def test_revoking_a_grant_leaves_the_base_scopes_alone() -> None:
    device = _device()
    grant = _grant(device, DEVICES_READ)
    session = FakeSession(devices=[device], grants=[grant])
    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC, DEVICES_READ})

    revoke_grant(session, device=device, grant_id=grant.id, revoked_by=uuid.uuid4(), now=NOW)

    assert effective_scopes(session, device, now=NOW) == frozenset({DEVICE_SYNC})
    assert device.scopes == [DEVICE_SYNC], "grants never edit devices.scopes"
    assert grant.revoked_at == NOW


def test_revoking_a_grant_of_another_workspace_is_not_found() -> None:
    device = _device()
    foreign = _grant(_device(OTHER_WS))
    session = FakeSession(devices=[device], grants=[foreign])

    with pytest.raises(DeviceGrantError) as caught:
        revoke_grant(session, device=device, grant_id=foreign.id, revoked_by=uuid.uuid4(), now=NOW)
    assert caught.value.status_code == 404
    assert foreign.revoked_at is None


# --------------------------------------------------------------------------- #
# The router
# --------------------------------------------------------------------------- #


def test_listing_annotates_every_grant_with_its_state() -> None:
    device = _device()
    live = _grant(device, DEVICES_READ, granted_at=NOW - datetime.timedelta(days=1))
    lapsed = _grant(
        device,
        REPORTS_READ,
        granted_at=NOW - datetime.timedelta(days=9),
        expires_at=NOW - datetime.timedelta(days=1),
        reason=None,
    )
    taken = _grant(
        device,
        QUALITY_CONFIGURE,
        granted_at=NOW - datetime.timedelta(days=5),
        revoked_at=NOW - datetime.timedelta(days=2),
    )
    session = FakeSession(devices=[device], grants=[live, lapsed, taken])

    rows = _client(session).get(f"/api/v1/device-grants/devices/{device.id}/grants").json()["data"]
    by_capability = {row["capability"]: row for row in rows}
    assert by_capability[DEVICES_READ]["state"] == GRANT_ACTIVE
    assert by_capability[DEVICES_READ]["effective"] is True
    assert by_capability[REPORTS_READ]["state"] == GRANT_EXPIRED
    assert by_capability[REPORTS_READ]["effective"] is False
    assert by_capability[QUALITY_CONFIGURE]["state"] == GRANT_REVOKED
    assert by_capability[QUALITY_CONFIGURE]["revoked_by"] is not None
    # Every row carries a sentence, not just a boolean: a bare ``false`` is what
    # sends the operator back to the API to ask why.
    assert all(row["detail"] for row in rows)
    # Newest first, so the decision an operator just made is the one on top.
    assert [row["capability"] for row in rows][0] == DEVICES_READ


def test_granting_returns_the_new_effective_scopes() -> None:
    device = _device()
    admin = _user()
    session = FakeSession(devices=[device])
    # Relative to the real clock on purpose: the router stamps the grant with
    # the server's now, and an absolute date would silently rot into a test that
    # asserts an expiry in the past is refused.
    expiry = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=30)

    resp = _client(session, admin).post(
        f"/api/v1/device-grants/devices/{device.id}/grants",
        json={"capability": DEVICES_READ, "expires_at": expiry.isoformat()},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["grant"]["capability"] == DEVICES_READ
    assert data["grant"]["effective"] is True
    assert data["grant"]["expires_at"] == expiry.isoformat()
    assert data["effective_scopes"] == sorted([DEVICE_SYNC, DEVICES_READ])
    assert session.grants[0].granted_by == admin.id
    assert session.grants[0].reason is None


def test_the_router_refuses_what_the_service_refuses() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)
    path = f"/api/v1/device-grants/devices/{device.id}/grants"

    unknown = client.post(path, json={"capability": "devices:reed"})
    assert unknown.status_code == 400
    assert _error_of(unknown)["code"] == "unknown_capability"
    assert _error_of(unknown)["field"] == "capability"

    unexplained = client.post(path, json={"capability": POLICIES_WRITE})
    assert unexplained.status_code == 400
    assert _error_of(unexplained)["code"] == "reason_required"

    naive = client.post(
        path, json={"capability": REPORTS_READ, "expires_at": "2026-04-01T18:00:00"}
    )
    assert naive.status_code == 400
    assert _error_of(naive)["code"] == "expiry_requires_timezone"

    assert session.grants == []


def test_a_missing_capability_is_a_validation_error_not_a_500() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    resp = _client(session).post(f"/api/v1/device-grants/devices/{device.id}/grants", json={})
    assert resp.status_code == 400
    assert _error_of(resp)["code"] == "unknown_capability"


def test_revoke_then_effective_scopes_shows_the_capability_gone() -> None:
    device = _device()
    grant = _grant(device, DEVICES_READ)
    session = FakeSession(devices=[device], grants=[grant])
    client = _client(session)
    base = f"/api/v1/device-grants/devices/{device.id}"

    before = client.get(f"{base}/effective-scopes").json()["data"]
    assert before["effective"] == sorted([DEVICE_SYNC, DEVICES_READ])

    resp = client.delete(f"{base}/grants/{grant.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["grant"]["state"] == GRANT_REVOKED
    assert resp.json()["data"]["effective_scopes"] == [DEVICE_SYNC]

    after = client.get(f"{base}/effective-scopes").json()["data"]
    assert after["granted"] == []
    assert after["effective"] == [DEVICE_SYNC]


def test_effective_scopes_answers_why_a_device_cannot_do_something() -> None:
    """The endpoint's whole job: not just what, but the nearest reason."""
    device = _device()
    expired = _grant(
        device,
        REPORTS_READ,
        granted_at=NOW - datetime.timedelta(days=9),
        expires_at=NOW - datetime.timedelta(days=1),
        reason=None,
    )
    revoked = _grant(device, QUALITY_CONFIGURE, revoked_at=NOW - datetime.timedelta(days=2))
    live = _grant(device, DEVICES_READ, reason="Kiosk shows tonight's cover")
    session = FakeSession(devices=[device], grants=[expired, revoked, live])

    data = (
        _client(session)
        .get(f"/api/v1/device-grants/devices/{device.id}/effective-scopes")
        .json()["data"]
    )
    assert data["base_scopes"] == [DEVICE_SYNC]
    assert data["effective"] == sorted([DEVICE_SYNC, DEVICES_READ])
    assert [row["capability"] for row in data["granted"]] == [DEVICES_READ]
    assert data["granted"][0]["reason"] == "Kiosk shows tonight's cover"

    withheld = {row["capability"]: row["detail"] for row in data["withheld"]}
    assert set(withheld) == REGISTERED_CAPABILITIES - set(data["effective"])
    assert withheld[REPORTS_READ].startswith("expired at")
    assert withheld[QUALITY_CONFIGURE].startswith("revoked at")
    assert withheld[POLICIES_WRITE] == "never granted and not among the device's base scopes"
    # The base scope is attributed to the base list, not to a grant.
    assert all(row["capability"] != DEVICE_SYNC for row in data["granted"])


def test_the_effective_scopes_report_is_built_from_the_same_resolution() -> None:
    """The report and the check a route performs must not be able to disagree."""
    device = _device()
    session = FakeSession(devices=[device], grants=[_grant(device, DEVICES_READ)])

    report = effective_scopes_report(session, device, now=NOW)
    assert report["effective"] == sorted(effective_scopes(session, device, now=NOW))
    assert report["base_scopes"] == [DEVICE_SYNC]


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


def test_a_device_in_another_workspace_is_404_on_every_route() -> None:
    foreign = _device(OTHER_WS, label="Theirs")
    foreign_grant = _grant(foreign, DEVICES_READ)
    session = FakeSession(devices=[foreign], grants=[foreign_grant])
    client = _client(session)  # the caller belongs to WS
    base = f"/api/v1/device-grants/devices/{foreign.id}"

    attempts = (
        ("get", f"{base}/grants", None),
        ("post", f"{base}/grants", {"capability": DEVICES_READ}),
        ("delete", f"{base}/grants/{foreign_grant.id}", None),
        ("get", f"{base}/effective-scopes", None),
    )
    for method, path, body in attempts:
        resp = client.request(method, path, json=body)
        assert resp.status_code == 404, (method, path, resp.text)
        assert _error_of(resp)["code"] == "not_found"
    # Nothing was mutated on the way out.
    assert foreign_grant.revoked_at is None
    assert foreign.label == "Theirs"


def test_a_grant_of_another_device_is_404_from_the_router() -> None:
    device = _device()
    other = _device(label="Another till")
    other_grant = _grant(other, DEVICES_READ)
    session = FakeSession(devices=[device, other], grants=[other_grant])

    resp = _client(session).delete(
        f"/api/v1/device-grants/devices/{device.id}/grants/{other_grant.id}"
    )
    assert resp.status_code == 404
    assert other_grant.revoked_at is None


def test_the_list_never_shows_another_workspace_s_grants() -> None:
    device = _device()
    theirs = _grant(_device(OTHER_WS), DEVICES_READ)
    session = FakeSession(devices=[device], grants=[_grant(device), theirs])

    rows = _client(session).get(f"/api/v1/device-grants/devices/{device.id}/grants").json()["data"]
    assert len(rows) == 1
    assert str(theirs.id) not in {row["id"] for row in rows}


# --------------------------------------------------------------------------- #
# Capability enforcement
# --------------------------------------------------------------------------- #


def test_a_user_without_device_grants_manage_cannot_grant_or_revoke() -> None:
    """Reading the fleet is not the power to widen a device."""
    device = _device()
    grant = _grant(device, DEVICES_READ)
    session = FakeSession(devices=[device], grants=[grant])
    base = f"/api/v1/device-grants/devices/{device.id}"

    for role in (UserRole.viewer, UserRole.operator, UserRole.analyst):
        assert not has_capability(_user(role=role), DEVICE_GRANTS_MANAGE)
        client = _client(session, _user(role=role))
        created = client.post(f"{base}/grants", json={"capability": REPORTS_READ})
        assert created.status_code == 403, role
        assert _error_of(created)["code"] == "forbidden"
        revoked = client.delete(f"{base}/grants/{grant.id}")
        assert revoked.status_code == 403, role
    assert grant.revoked_at is None
    assert len(session.grants) == 1


def test_reading_grants_needs_devices_read() -> None:
    device = _device()
    session = FakeSession(devices=[device], grants=[_grant(device)])
    base = f"/api/v1/device-grants/devices/{device.id}"

    for role in (UserRole.viewer, UserRole.operator):
        client = _client(session, _user(role=role))
        assert client.get(f"{base}/grants").status_code == 403, role
        assert client.get(f"{base}/effective-scopes").status_code == 403, role

    # Both the administrator and the owner may read, and the owner may also grant.
    for role in (UserRole.administrator, UserRole.owner):
        client = _client(session, _user(role=role))
        assert client.get(f"{base}/grants").status_code == 200, role
        assert client.get(f"{base}/effective-scopes").status_code == 200, role
    assert has_capability(_user(role=UserRole.owner), DEVICE_GRANTS_MANAGE)


def test_a_device_credential_does_not_stand_in_for_a_person() -> None:
    """These routes are JWT-only; a device may not read its own permissions."""
    device = _device()
    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: FakeSession(devices=[device])  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get(
        f"/api/v1/device-grants/devices/{device.id}/effective-scopes",
        headers={"X-Device-Key": "did_000000000000.anything"},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# The router's own surface, and the double's own honesty
# --------------------------------------------------------------------------- #


def test_every_documented_route_exists_with_the_declared_prefix() -> None:
    assert router.prefix == "/api/v1/device-grants"
    assert {tag for route in router.routes for tag in route.tags} == {"devices"}
    routes = {
        (method, getattr(route, "path", None))
        for route in router.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert routes == {
        ("GET", "/api/v1/device-grants/devices/{device_id}/grants"),
        ("POST", "/api/v1/device-grants/devices/{device_id}/grants"),
        ("DELETE", "/api/v1/device-grants/devices/{device_id}/grants/{grant_id}"),
        ("GET", "/api/v1/device-grants/devices/{device_id}/effective-scopes"),
    }


def test_the_session_double_really_filters_by_workspace() -> None:
    """Guard on the double: a dropped predicate must not pass unnoticed.

    Without this, a service that stopped scoping by ``workspace_id`` would still
    pass every test above, because each of them only ever holds one workspace's
    rows at a time.
    """
    mine_device, their_device = _device(), _device(OTHER_WS)
    mine = _grant(mine_device)
    theirs = _grant(their_device)
    session = FakeSession(devices=[mine_device, their_device], grants=[mine, theirs])

    matched = session.scalars(
        select(DeviceGrant)
        .where(DeviceGrant.workspace_id == WS, DeviceGrant.device_id == mine.device_id)
        .order_by(DeviceGrant.granted_at.desc())
    ).all()
    assert [row.id for row in matched] == [mine.id]


def test_an_unknown_device_id_is_404() -> None:
    assert (
        _client(FakeSession())
        .get(f"/api/v1/device-grants/devices/{uuid.uuid4()}/grants")
        .status_code
        == 404
    )
