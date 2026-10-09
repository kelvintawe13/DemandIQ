"""Device fleet tests — enrollment, credential lifecycle, isolation, heartbeat.

The device tables are Postgres-typed (``JSONB``, ``UUID``, ``text[]``), so
there is no in-memory engine to run them on and every device test stands in for
the session. The usual stand-in is an ordered ``side_effect`` list, which stops
working the moment a route runs a second query and then fails for reasons that
have nothing to do with what the test asserts. :class:`FakeSession` reads the
*statement* instead — the entity, the equality criteria, the ``ORDER BY`` and
the ``LIMIT`` — and applies them in Python, so these tests exercise the real
``WHERE`` clauses the router emits. Tenant isolation in particular is then proven
by the filter rather than by a canned answer;
:func:`test_the_session_double_really_filters` guards the double itself against
being the reason a dropped predicate goes unnoticed.

Nothing here touches a database, Redis or the network.
"""

from __future__ import annotations

import datetime
import itertools
import uuid
from functools import cache, partial
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BinaryExpression, Null, UnaryExpression

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.device import (
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_QUARANTINED,
    DEVICE_STATUS_REVOKED,
    Device,
    DeviceEvent,
)
from app.models.location import Location
from app.models.user import User, UserRole
from app.routers.devices import MAX_EVENT_LIMIT, router
from app.security.device_auth import CREDENTIAL_HEADER, PREFIX_LENGTH
from app.security.hashing import hash_password

# Credentials are built here rather than through ``generate_credential`` so the
# tests check the published format (``did_<12 hex>.<secret>``) instead of the
# generator that produces it.
PREFIX = "did_" + "0" * (PREFIX_LENGTH - 4)
SECRET = "test-device-secret-value-not-a-real-credential"
CREDENTIAL = f"{PREFIX}.{SECRET}"
OTHER_PREFIX = "did_" + "f" * (PREFIX_LENGTH - 4)

WS = uuid.uuid4()
OTHER_WS = uuid.uuid4()

_event_ids = itertools.count(1)


@cache
def _hash(secret: str) -> str:
    """One bcrypt hash per secret — hashing is the slow part of these tests."""
    return hash_password(secret)


def _user(workspace_id: uuid.UUID, role: UserRole = UserRole.administrator) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Fleet Admin",
        email=f"{role.value}@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _location(workspace_id: uuid.UUID) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test Bistro",
        area="Nyarutarama",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _device(
    workspace_id: uuid.UUID = WS,
    *,
    label: str = "Till 1",
    prefix: str = PREFIX,
    secret: str = SECRET,
    status: str = DEVICE_STATUS_ACTIVE,
    scopes: list[str] | None = None,
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
        credential_prefix=prefix,
        credential_hash=_hash(secret),
        scopes=["device:sync"] if scopes is None else scopes,
        status=status,
        last_seen_at=None,
        revoked_at=None,
        mirroring_consent_at=None,
    )


def _event(device: Device, minutes_ago: int = 0, event_type: str = "heartbeat") -> DeviceEvent:
    return DeviceEvent(
        id=next(_event_ids),
        workspace_id=device.workspace_id,
        device_id=device.id,
        event_type=event_type,
        payload={"battery_pct": 80},
        recorded_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago),
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
    # Nones sort first, consistently, so a stable sort stays deterministic.
    return (value is None, value)


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


class FakeSession:
    """A ``Session`` that filters the statements the device router emits."""

    def __init__(
        self,
        *,
        devices: list[Device] | None = None,
        events: list[DeviceEvent] | None = None,
        locations: list[Location] | None = None,
    ) -> None:
        self.devices = list(devices or [])
        self.events = list(events or [])
        self.locations = list(locations or [])
        self.added: list[Any] = []
        self.flushes = 0

    def _pool(self, model: Any) -> list[Any] | None:
        pools: dict[Any, list[Any]] = {
            Device: self.devices,
            Location: self.locations,
            DeviceEvent: self.events,
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
        # Keys are applied least-significant first so a stable sort composes
        # into the multi-key ordering the statement asked for.
        for column, descending in reversed(_order_spec(stmt)):
            rows.sort(key=partial(_sort_key, column=column), reverse=descending)
        limit = getattr(getattr(stmt, "_limit_clause", None), "value", None)
        return _Rows(rows[: int(limit)] if limit is not None else rows)

    def add(self, obj: Any) -> None:
        if isinstance(obj, DeviceEvent):
            if obj.id is None:
                # Stands in for what the database does on insert; the id is a
                # BIGSERIAL, so nothing in the application can supply one.
                obj.id = next(_event_ids)
            self.events.append(obj)
        elif isinstance(obj, Device):
            self.devices.append(obj)
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def _build_app(session: FakeSession, caller: User | None) -> TestClient:
    """The real application with this router mounted and the session stubbed.

    The router is attached here rather than relying on ``create_app`` having
    registered it, so these tests assert against the module under test and stay
    green whether or not the application factory has been wired up yet.
    """
    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session  # type: ignore[assignment]
    if caller is not None:
        app.dependency_overrides[get_current_user] = lambda: caller  # type: ignore[assignment]
    return TestClient(app)


def _client(session: FakeSession, role: UserRole = UserRole.administrator) -> TestClient:
    return _build_app(session, _user(WS, role))


def _anonymous_client(session: FakeSession) -> TestClient:
    return _build_app(session, None)


def _device_headers(credential: str = CREDENTIAL) -> dict[str, str]:
    return {CREDENTIAL_HEADER: credential}


def _without_reference(body: dict[str, Any]) -> dict[str, Any]:
    """An error body minus the per-request reference the middleware injects."""
    error = dict(body["error"])
    error.pop("reference", None)
    return error


def _heartbeat(
    session: FakeSession, device: Device, credential: str = CREDENTIAL, body: Any = None
) -> Any:
    client = _client(session)
    kwargs: dict[str, Any] = {"headers": _device_headers(credential)}
    if body is not None:
        kwargs["json"] = body
    return client.post(f"/api/v1/devices/{device.id}/heartbeat", **kwargs)


# --------------------------------------------------------------------------- #
# Enrollment and the one-time secret
# --------------------------------------------------------------------------- #


def test_enroll_returns_the_secret_exactly_once() -> None:
    session = FakeSession()
    admin = _user(WS)
    client = _build_app(session, admin)

    resp = client.post("/api/v1/devices", json={"label": "Till 1", "device_type": "pos"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["secret"].startswith(body["credential_prefix"] + ".")
    assert "Store this secret securely" in body["warning"]

    # What is persisted is the prefix and a hash — never the secret.
    stored = session.devices[0]
    assert stored.credential_prefix == body["credential_prefix"]
    assert body["secret"] not in stored.credential_hash
    assert stored.credential_hash.startswith("$2")
    assert stored.workspace_id == WS
    assert stored.scopes == ["device:sync"]
    assert stored.status == DEVICE_STATUS_ACTIVE
    # The enrollment is attributable — the row keeps its human author.
    assert stored.enrolled_by == admin.id


def test_the_enrolled_secret_is_never_returned_again() -> None:
    session = FakeSession()
    client = _client(session)
    created = client.post("/api/v1/devices", json={"label": "Till 1"}).json()
    device_id = created["id"]

    for path, rows in (
        ("/api/v1/devices", client.get("/api/v1/devices").json()["data"]),
        (
            f"/api/v1/devices/{device_id}",
            [client.get(f"/api/v1/devices/{device_id}").json()["data"]],
        ),
    ):
        assert rows
        for row in rows:
            assert "secret" not in row
            assert "credential_hash" not in row
            assert row["credential_prefix"] == created["credential_prefix"], path


def test_enroll_rejects_a_label_that_is_missing_or_blank() -> None:
    client = _client(FakeSession())
    for payload in ({}, {"label": "   "}, {"label": None}):
        resp = client.post("/api/v1/devices", json=payload)
        assert resp.status_code == 400, (payload, resp.text)
        assert resp.json()["error"]["field"] == "label"


def test_enroll_rejects_a_scope_outside_device_scopes() -> None:
    """A typo must fail loudly, not create a permission that does nothing."""
    client = _client(FakeSession())
    for bad in ("devices:read", "device:sync ", "sync", "forecasts:read", ""):
        resp = client.post("/api/v1/devices", json={"label": "Till 1", "scopes": [bad]})
        assert resp.status_code == 400, (bad, resp.text)
        assert resp.json()["error"]["code"] == "invalid_scope"
        assert resp.json()["error"]["field"] == "scopes"


def test_enroll_rejects_a_non_array_or_empty_scope_list() -> None:
    client = _client(FakeSession())
    for scopes in ("device:sync", [], [1]):
        resp = client.post("/api/v1/devices", json={"label": "Till 1", "scopes": scopes})
        assert resp.status_code == 400, (scopes, resp.text)


def test_enroll_refuses_a_location_from_another_workspace() -> None:
    foreign = _location(OTHER_WS)
    session = FakeSession(locations=[foreign])
    client = _client(session)

    resp = client.post("/api/v1/devices", json={"label": "Till 1", "location_id": str(foreign.id)})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_location"
    assert session.devices == []


# --------------------------------------------------------------------------- #
# Credential authentication
# --------------------------------------------------------------------------- #


def test_a_correct_credential_authenticates_the_device() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    resp = _heartbeat(session, device, body={"battery_pct": 55})
    assert resp.status_code == 200, resp.text


def test_a_wrong_secret_is_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    resp = _heartbeat(session, device, f"{PREFIX}.not-the-real-secret", body={})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert device.last_seen_at is None


def test_unknown_prefix_and_wrong_secret_are_indistinguishable() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)

    unknown = client.post(
        f"/api/v1/devices/{device.id}/heartbeat",
        headers=_device_headers(f"{OTHER_PREFIX}.anything"),
        json={},
    )
    wrong = client.post(
        f"/api/v1/devices/{device.id}/heartbeat",
        headers=_device_headers(f"{PREFIX}.anything"),
        json={},
    )
    assert unknown.status_code == wrong.status_code == 401
    # Byte-identical apart from the per-request reference: a prefix probe must
    # not be answerable from anything the caller can see.
    assert _without_reference(unknown.json()) == _without_reference(wrong.json())


def test_malformed_and_missing_credentials_are_refused() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)

    header_cases: list[dict[str, str]] = [
        {},
        _device_headers("short"),
        _device_headers(PREFIX),
        _device_headers("no-dot"),
    ]
    for header in header_cases:
        resp = client.post(f"/api/v1/devices/{device.id}/heartbeat", headers=header, json={})
        assert resp.status_code == 401, header
        assert resp.json()["error"]["code"] == "unauthorized"


def test_a_revoked_device_is_refused_with_its_own_code() -> None:
    device = _device(status=DEVICE_STATUS_REVOKED)
    device.revoked_at = datetime.datetime.now(datetime.UTC)
    session = FakeSession(devices=[device])

    resp = _heartbeat(session, device, body={})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "device_revoked"


def test_a_quarantined_device_is_refused() -> None:
    device = _device(status=DEVICE_STATUS_QUARANTINED)
    session = FakeSession(devices=[device])

    resp = _heartbeat(session, device, body={})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "device_quarantined"


def test_authentication_bumps_last_seen_at() -> None:
    device = _device()
    assert device.last_seen_at is None
    session = FakeSession(devices=[device])

    assert _heartbeat(session, device, body={}).status_code == 200
    assert device.last_seen_at is not None


def test_the_device_credential_does_not_stand_in_for_a_user() -> None:
    """A device key must never satisfy a route that takes a JWT."""
    client = _anonymous_client(FakeSession(devices=[_device()]))

    assert client.get("/api/v1/devices", headers=_device_headers()).status_code == 401


# --------------------------------------------------------------------------- #
# Rotation
# --------------------------------------------------------------------------- #


def test_rotation_invalidates_the_previous_credential() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)

    resp = client.post(f"/api/v1/devices/{device.id}/rotate")
    assert resp.status_code == 200, resp.text
    rotated = resp.json()["data"]
    assert rotated["credential_prefix"] != PREFIX
    assert rotated["secret"].startswith(rotated["credential_prefix"] + ".")

    assert _heartbeat(session, device, CREDENTIAL, body={}).status_code == 401
    assert _heartbeat(session, device, rotated["secret"], body={}).status_code == 200


def test_rotation_refuses_a_revoked_device() -> None:
    device = _device(status=DEVICE_STATUS_REVOKED)
    session = FakeSession(devices=[device])

    resp = _client(session).post(f"/api/v1/devices/{device.id}/rotate")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "device_revoked"


# --------------------------------------------------------------------------- #
# Revocation, quarantine, restore
# --------------------------------------------------------------------------- #


def test_revoke_is_immediate_for_authentication() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)

    resp = client.post(f"/api/v1/devices/{device.id}/revoke")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == DEVICE_STATUS_REVOKED
    assert device.revoked_at is not None

    assert _heartbeat(session, device, body={}).status_code == 403


def test_repeated_revocation_keeps_the_original_revoked_at() -> None:
    """The first timestamp is the evidence; a second click must not move it."""
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)
    client.post(f"/api/v1/devices/{device.id}/revoke")
    original = device.revoked_at

    assert client.post(f"/api/v1/devices/{device.id}/revoke").status_code == 200
    assert device.revoked_at == original


def test_quarantine_and_restore_are_reversible() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)

    resp = client.post(f"/api/v1/devices/{device.id}/quarantine")
    assert resp.status_code == 200, resp.text
    assert device.status == DEVICE_STATUS_QUARANTINED
    # Quarantine stops the device, it does not merely distrust it.
    assert _heartbeat(session, device, body={}).status_code == 403

    restored = client.post(f"/api/v1/devices/{device.id}/restore")
    assert restored.status_code == 200, restored.text
    assert device.status == DEVICE_STATUS_ACTIVE
    assert _heartbeat(session, device, body={}).status_code == 200


def test_quarantine_is_not_repeatable_and_restore_demands_it() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session)

    premature = client.post(f"/api/v1/devices/{device.id}/restore")
    assert premature.status_code == 409
    assert premature.json()["error"]["code"] == "not_quarantined"

    client.post(f"/api/v1/devices/{device.id}/quarantine")
    again = client.post(f"/api/v1/devices/{device.id}/quarantine")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_quarantined"


def test_a_revoked_device_cannot_be_quarantined_or_restored() -> None:
    """The two states must not collapse into one reversible switch."""
    device = _device(status=DEVICE_STATUS_REVOKED)
    client = _client(FakeSession(devices=[device]))

    for action in ("quarantine", "restore"):
        resp = client.post(f"/api/v1/devices/{device.id}/{action}")
        assert resp.status_code == 409, action
        assert resp.json()["error"]["code"] == "device_revoked"
    assert device.status == DEVICE_STATUS_REVOKED


# --------------------------------------------------------------------------- #
# Tenant isolation
# --------------------------------------------------------------------------- #


def test_a_device_in_another_workspace_is_404_not_403() -> None:
    foreign = _device(OTHER_WS, prefix=OTHER_PREFIX, secret="other-workspace-secret")
    session = FakeSession(devices=[foreign])
    client = _client(session)  # the caller belongs to WS

    attempts = (
        ("get", f"/api/v1/devices/{foreign.id}", None),
        ("patch", f"/api/v1/devices/{foreign.id}", {"label": "mine now"}),
        ("post", f"/api/v1/devices/{foreign.id}/rotate", None),
        ("post", f"/api/v1/devices/{foreign.id}/revoke", None),
        ("post", f"/api/v1/devices/{foreign.id}/quarantine", None),
        ("get", f"/api/v1/devices/{foreign.id}/events", None),
    )
    for method, path, body in attempts:
        resp = client.request(method, path, json=body)
        assert resp.status_code == 404, (method, path, resp.text)
        assert resp.json()["error"]["code"] == "not_found"
    # Nothing was mutated on the way out.
    assert foreign.label == "Till 1"
    assert foreign.status == DEVICE_STATUS_ACTIVE
    assert foreign.credential_prefix == OTHER_PREFIX


def test_the_list_never_shows_another_workspace() -> None:
    mine = _device(label="Mine")
    theirs = _device(OTHER_WS, label="Theirs", prefix=OTHER_PREFIX, secret="theirs-secret")
    client = _client(FakeSession(devices=[mine, theirs]))

    assert [row["label"] for row in client.get("/api/v1/devices").json()["data"]] == ["Mine"]


def test_a_device_may_only_report_on_itself() -> None:
    mine = _device()
    other = _device(label="Another till", prefix=OTHER_PREFIX, secret="another-secret")
    session = FakeSession(devices=[mine, other])

    resp = _heartbeat(session, other, body={})  # authenticated as ``mine``
    assert resp.status_code == 404, resp.text
    assert other.last_seen_at is None
    assert session.events == []


# --------------------------------------------------------------------------- #
# Heartbeat and events
# --------------------------------------------------------------------------- #


def test_heartbeat_records_an_event_and_bumps_last_seen() -> None:
    device = _device()
    session = FakeSession(devices=[device])
    before = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)

    resp = _heartbeat(session, device, body={"app_version": "4.2.0", "battery_pct": 71})
    assert resp.status_code == 200, resp.text

    assert device.last_seen_at is not None
    assert device.last_seen_at >= before
    assert device.app_version == "4.2.0"

    assert len(session.events) == 1
    event = session.events[0]
    assert event.event_type == "heartbeat"
    assert event.workspace_id == WS
    assert event.device_id == device.id
    assert event.payload == {"app_version": "4.2.0", "battery_pct": 71}
    assert resp.json()["data"]["event"]["id"] == str(event.id)


def test_heartbeat_keeps_only_bounded_scalar_telemetry() -> None:
    """The events table is the high-volume one; a device must not inflate it."""
    device = _device()
    session = FakeSession(devices=[device])

    resp = _heartbeat(
        session,
        device,
        body={
            "battery_pct": 30,
            "nested": {"anything": [1, 2, 3]},
            "unknown_key": "dropped",
            **{f"extra_{index}": index for index in range(20)},
        },
    )
    assert resp.status_code == 200, resp.text
    assert session.events[0].payload == {"battery_pct": 30}


def test_heartbeat_works_without_a_body() -> None:
    device = _device()
    session = FakeSession(devices=[device])

    resp = _heartbeat(session, device)
    assert resp.status_code == 200, resp.text
    assert session.events[0].payload == {}


def test_events_are_listed_newest_first_and_capped() -> None:
    device = _device()
    old = _event(device, minutes_ago=30)
    new = _event(device, minutes_ago=0, event_type="sync")
    other_workspace = _event(
        _device(OTHER_WS, prefix=OTHER_PREFIX, secret="theirs"), minutes_ago=5, event_type="error"
    )
    client = _client(FakeSession(devices=[device], events=[old, new, other_workspace]))

    rows = client.get(f"/api/v1/devices/{device.id}/events").json()["data"]
    assert [row["event_type"] for row in rows] == ["sync", "heartbeat"]
    # The other workspace's event is absent, not merely further down the list.
    assert str(other_workspace.id) not in {row["id"] for row in rows}

    limited = client.get(f"/api/v1/devices/{device.id}/events?limit=1").json()["data"]
    assert len(limited) == 1

    over_cap = client.get(f"/api/v1/devices/{device.id}/events?limit={MAX_EVENT_LIMIT + 1}")
    assert over_cap.status_code == 422
    assert client.get(f"/api/v1/devices/{device.id}/events?limit=0").status_code == 422


# --------------------------------------------------------------------------- #
# Listing and editing
# --------------------------------------------------------------------------- #


def test_list_filters_by_status_and_location() -> None:
    location = _location(WS)
    active = _device(label="Active", location_id=location.id)
    quarantined = _device(label="Quarantined", status=DEVICE_STATUS_QUARANTINED)
    client = _client(FakeSession(devices=[active, quarantined], locations=[location]))

    by_status = client.get("/api/v1/devices?status=quarantined").json()["data"]
    assert [row["label"] for row in by_status] == ["Quarantined"]

    by_location = client.get(f"/api/v1/devices?location_id={location.id}").json()["data"]
    assert [row["label"] for row in by_location] == ["Active"]

    everything = client.get("/api/v1/devices").json()["data"]
    assert [row["label"] for row in everything] == ["Active", "Quarantined"]
    assert everything[0]["status"] == DEVICE_STATUS_ACTIVE
    assert everything[0]["last_seen_at"] is None
    assert everything[0]["location_id"] == str(location.id)


def test_list_rejects_an_unknown_status_filter() -> None:
    client = _client(FakeSession())
    resp = client.get("/api/v1/devices?status=retired")
    assert resp.status_code == 400
    assert resp.json()["error"]["field"] == "status"


def test_get_device_reports_base_scopes() -> None:
    device = _device(scopes=["device:sync"])
    client = _client(FakeSession(devices=[device]))

    data = client.get(f"/api/v1/devices/{device.id}").json()["data"]
    assert data["scopes"] == ["device:sync"]
    assert data["credential_prefix"] == PREFIX
    assert "secret" not in data


def test_an_unknown_device_id_is_404() -> None:
    assert _client(FakeSession()).get(f"/api/v1/devices/{uuid.uuid4()}").status_code == 404


def test_patch_updates_label_location_and_app_version() -> None:
    location = _location(WS)
    device = _device()
    client = _client(FakeSession(devices=[device], locations=[location]))

    resp = client.patch(
        f"/api/v1/devices/{device.id}",
        json={"label": "Till 7", "location_id": str(location.id), "app_version": "4.2.0"},
    )
    assert resp.status_code == 200, resp.text
    assert (device.label, device.app_version) == ("Till 7", "4.2.0")
    assert device.location_id == location.id

    # An explicit null clears the binding.
    cleared = client.patch(f"/api/v1/devices/{device.id}", json={"location_id": None})
    assert cleared.status_code == 200, cleared.text
    assert device.location_id is None


def test_patch_refuses_a_foreign_location_and_a_blank_label() -> None:
    foreign = _location(OTHER_WS)
    device = _device()
    client = _client(FakeSession(devices=[device], locations=[foreign]))

    resp = client.patch(f"/api/v1/devices/{device.id}", json={"location_id": str(foreign.id)})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_location"
    assert device.location_id is None

    blank = client.patch(f"/api/v1/devices/{device.id}", json={"label": "  "})
    assert blank.status_code == 400
    assert device.label == "Till 1"


# --------------------------------------------------------------------------- #
# Capability enforcement
# --------------------------------------------------------------------------- #


def test_a_data_clerk_can_neither_enroll_nor_revoke() -> None:
    """Fleet administration is not part of the data-ingest role bundle."""
    device = _device()
    session = FakeSession(devices=[device])
    client = _client(session, role=UserRole.operator)

    resp = client.post("/api/v1/devices", json={"label": "Sneaky"})
    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == "Managing the device fleet requires devices:manage"
    assert session.devices == [device]

    assert client.post(f"/api/v1/devices/{device.id}/revoke").status_code == 403
    assert device.status == DEVICE_STATUS_ACTIVE
    assert client.get("/api/v1/devices").status_code == 403


def test_a_data_clerk_is_refused_the_whole_operator_fleet_surface() -> None:
    device = _device()
    client = _client(FakeSession(devices=[device]), role=UserRole.operator)

    attempts = (
        ("get", f"/api/v1/devices/{device.id}", None),
        ("patch", f"/api/v1/devices/{device.id}", {"label": "x"}),
        ("post", f"/api/v1/devices/{device.id}/rotate", None),
        ("post", f"/api/v1/devices/{device.id}/quarantine", None),
        ("post", f"/api/v1/devices/{device.id}/restore", None),
        ("get", f"/api/v1/devices/{device.id}/events", None),
    )
    for method, path, body in attempts:
        assert client.request(method, path, json=body).status_code == 403, path
    assert device.status == DEVICE_STATUS_ACTIVE


def test_the_heartbeat_needs_no_operator_capability() -> None:
    """A device is not a seat: liveness is the one route it may always call."""
    device = _device()
    client = _client(FakeSession(devices=[device]), role=UserRole.viewer)

    resp = client.post(f"/api/v1/devices/{device.id}/heartbeat", headers=_device_headers(), json={})
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# The router's own surface, and the double's own honesty
# --------------------------------------------------------------------------- #


def test_every_documented_route_exists_with_the_declared_prefix() -> None:
    assert router.prefix == "/api/v1/devices"
    routes = {
        (method, getattr(route, "path", None))
        for route in router.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert routes == {
        ("GET", "/api/v1/devices"),
        ("POST", "/api/v1/devices"),
        ("GET", "/api/v1/devices/{device_id}"),
        ("PATCH", "/api/v1/devices/{device_id}"),
        ("POST", "/api/v1/devices/{device_id}/rotate"),
        ("POST", "/api/v1/devices/{device_id}/revoke"),
        ("POST", "/api/v1/devices/{device_id}/quarantine"),
        ("POST", "/api/v1/devices/{device_id}/restore"),
        ("POST", "/api/v1/devices/{device_id}/heartbeat"),
        ("POST", "/api/v1/devices/{device_id}/demand"),
        ("GET", "/api/v1/devices/{device_id}/events"),
    }


def test_the_session_double_really_filters() -> None:
    """Guard on the double: a dropped predicate must not pass unnoticed.

    Without this, a router that stopped scoping by ``workspace_id`` would still
    pass every test above, because each of them only ever holds rows belonging to
    one workspace at a time.
    """
    mine = _device()
    theirs = _device(OTHER_WS, prefix=OTHER_PREFIX, secret="theirs")
    session = FakeSession(devices=[mine, theirs])

    matched = session.scalars(
        select(Device).where(Device.workspace_id == WS).order_by(Device.label).limit(5)
    ).all()
    assert [row.id for row in matched] == [mine.id]


def test_the_session_double_honours_order_and_limit() -> None:
    device = _device()
    events = [_event(device, minutes_ago=minutes) for minutes in (30, 10, 20)]
    session = FakeSession(devices=[device], events=events)

    rows = session.scalars(
        select(DeviceEvent)
        .where(DeviceEvent.device_id == device.id)
        .order_by(DeviceEvent.recorded_at.desc())
        .limit(2)
    ).all()
    newest_first = sorted((row.recorded_at for row in events), reverse=True)[:2]
    assert [row.recorded_at for row in rows] == newest_first
