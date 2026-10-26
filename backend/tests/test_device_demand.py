"""Device-authenticated demand persistence.

The WebSocket already lets a device *announce* demand, and that message reaches
subscribed screens — but it is fan-out only, so nothing is written. These tests
pin the durable half of that round trip, and mostly pin what it must refuse to
do: a device credential is a statement about one device in one tenant, and
nothing in the request body may widen it.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import create_app
from app.models.device import Device
from app.models.location import Location
from app.routers.devices import router
from app.security.device_auth import CREDENTIAL_HEADER
from tests.test_devices import CREDENTIAL, WS, _device  # noqa: F401

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _location(workspace_id: uuid.UUID = WS) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Session:
    """Session stub that answers ``db.get`` per model.

    ``commit_demand`` reads the ``Location`` and looks for an existing
    observation, while ``_load_device`` reads the ``Device`` — so the stub has
    to dispatch on the model rather than return one object for everything.
    Device authentication additionally goes through ``db.scalars``, which
    resolves the credential's prefix to candidate rows.
    """

    def __init__(self, *, device: Device | None = None, location: Location | None = None) -> None:
        self._device = device
        self._location = location
        self.added: list[Any] = []
        self.state: dict[str, Any] = {}

    def get(self, model: Any, pk: Any) -> Any:
        if model is Device:
            return self._device
        if model is Location:
            return self._location
        return None

    def scalars(self, stmt: Any) -> _Rows:
        return _Rows([self._device] if self._device is not None else [])

    def scalar(self, *_args: Any, **_kwargs: Any) -> None:
        # No existing observation: every row in these tests is a first write.
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def execute(self, _stmt: Any) -> None:
        # The import channels are inserted with a Postgres ``INSERT ... ON
        # CONFLICT``; nothing here asserts on that row, it only has to exist for
        # the call to type-check against a session-like object.
        return None

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def _client(session: _Session, seen: dict[str, Any] | None = None) -> TestClient:
    app = create_app()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session  # type: ignore[assignment]

    if seen is not None:

        @app.middleware("http")
        async def _capture(request: Any, call_next: Any) -> Any:
            # Read the state the route set, the way the audit middleware does.
            response = await call_next(request)
            seen["workspace_id"] = getattr(request.state, "workspace_id", None)
            seen["device_id"] = getattr(request.state, "device_id", None)
            return response

    return TestClient(app)


def _headers(credential: str = CREDENTIAL) -> dict[str, str]:
    return {CREDENTIAL_HEADER: credential}


def _payload(**overrides: Any) -> list[dict[str, Any]]:
    row: dict[str, Any] = {"date": "2026-01-05", "demand_qty": 42}
    row.update(overrides)
    return [row]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_a_device_persists_its_own_demand() -> None:
    device = _device(location_id=uuid.uuid4())
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand", json=_payload(), headers=_headers()
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["committed"] == 1
    assert body["reconciled"] == 0
    assert body["errors"] == []

    # The row went through the canonical writer, so it is a real observation
    # tagged with the device as its source.
    assert len(session.added) == 1
    observation = session.added[0]
    assert observation.location_id == loc.id
    assert observation.workspace_id == WS
    assert observation.demand_qty == 42


def test_a_batch_writes_every_row() -> None:
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand",
        json=[
            {"date": "2026-01-05", "demand_qty": 10},
            {"date": "2026-01-06", "demand_qty": 20},
        ],
        headers=_headers(),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["committed"] == 2
    assert len(session.added) == 2


# ---------------------------------------------------------------------------
# The credential is the boundary
# ---------------------------------------------------------------------------


def test_a_path_naming_another_device_is_404() -> None:
    """The credential already named the device; the path may not disagree."""
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    resp = _client(session).post(
        f"/api/v1/devices/{uuid.uuid4()}/demand", json=_payload(), headers=_headers()
    )

    assert resp.status_code == 404, resp.text
    assert session.added == []


def test_a_device_without_the_sync_scope_is_refused() -> None:
    device = _device(scopes=[])
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand", json=_payload(), headers=_headers()
    )

    assert resp.status_code == 403, resp.text
    assert session.added == []


def test_an_unbound_device_is_refused_rather_than_guessed_at() -> None:
    """No location means no correct answer, and picking one corrupts a forecast."""
    device = _device(location_id=None)
    session = _Session(device=device, location=None)

    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand", json=_payload(), headers=_headers()
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "device_unbound"
    assert session.added == []


def test_a_missing_credential_is_unauthenticated() -> None:
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    resp = _client(session).post(f"/api/v1/devices/{device.id}/demand", json=_payload())

    assert resp.status_code == 401, resp.text
    assert session.added == []


# ---------------------------------------------------------------------------
# The body must not be able to aim the write
# ---------------------------------------------------------------------------


def test_the_body_cannot_choose_a_location() -> None:
    """A compromised till must not be able to write into a sibling location."""
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    other = uuid.uuid4()
    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand",
        json=_payload(location_id=str(other), workspace_id=str(uuid.uuid4())),
        headers=_headers(),
    )

    # The schema has no such fields, so the extras are dropped rather than
    # honoured: the write lands on the device's own binding and the supplied
    # ids go nowhere.
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["committed"] == 1
    assert session.added[0].location_id == loc.id
    assert session.added[0].workspace_id == WS


def test_a_location_from_another_workspace_is_refused() -> None:
    """The normaliser's own tenant check, reached through the device path."""
    device = _device()
    foreign = _location(uuid.uuid4())
    device.location_id = foreign.id
    session = _Session(device=device, location=foreign)

    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand", json=_payload(), headers=_headers()
    )

    # 201 with a per-row error: the batch reports rather than aborting, and the
    # row never reaches the database.
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["committed"] == 0
    assert resp.json()["data"]["errors"][0]["code"] == "not_found"
    assert session.added == []


def test_a_future_date_is_reported_per_row() -> None:
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    tomorrow = (datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=1)).isoformat()
    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand",
        json=_payload(date=tomorrow),
        headers=_headers(),
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["committed"] == 0
    assert session.added == []


def test_a_negative_quantity_is_rejected_by_the_schema() -> None:
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)

    resp = _client(session).post(
        f"/api/v1/devices/{device.id}/demand",
        json=_payload(demand_qty=-1),
        headers=_headers(),
    )

    assert resp.status_code == 422, resp.text
    assert session.added == []


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_the_write_is_attributable_to_a_device_and_not_to_a_user() -> None:
    """A privileged write against a customer's forecast still belongs in the trail.

    The audit middleware needs a workspace and a device on ``request.state`` to
    record anything, because there is no JWT to read. Without them this write
    would be dropped from the trail entirely — silently, which is the failure
    mode that matters.
    """
    device = _device()
    loc = _location()
    device.location_id = loc.id
    session = _Session(device=device, location=loc)
    seen: dict[str, Any] = {}

    resp = _client(session, seen).post(
        f"/api/v1/devices/{device.id}/demand", json=_payload(), headers=_headers()
    )

    assert resp.status_code == 201, resp.text
    assert seen["workspace_id"] == WS
    assert seen["device_id"] == device.id
