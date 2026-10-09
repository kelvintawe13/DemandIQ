"""Location CRUD and access-tracking tests (DB-optional).

Tests run with mocked DB sessions so they do not require a live PostgreSQL.
Access-log filtering, pagination shape and workspace scoping are exercised
via dependency overrides — the same paths used in production.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.location import Location
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal

# ---------------------------------------------------------------- fixtures ---


def _make_workspace() -> Workspace:
    ws = Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.now(UTC),
    )
    return ws


def _make_user(ws_id: uuid.UUID, role: UserRole = UserRole.owner) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Test User",
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.now(UTC),
    )


def _make_location(ws_id: uuid.UUID) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Kigali Central",
        area="Kigali",
        cuisine="Rwandan",
        archived_at=None,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------- helpers ----


def _mock_scalars(rows: list):  # type: ignore[no-untyped-def]
    """Helper that mimics ``db.scalars(select(...)).all()``."""

    class _Scalars:
        def __init__(self, data: list) -> None:
            self._data = data

        def all(self) -> list:
            return self._data

    class _Result:
        def __init__(self, data: list) -> None:
            self._data = data

        def scalars(self) -> _Scalars:
            return _Scalars(self._data)

    return _Result(rows)


# ---------------------------------------------------------------- tests ------


def test_unauthenticated_locations_rejected() -> None:
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/locations")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_list_locations_paginated_shape() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    locs = [_make_location(ws.id) for _ in range(2)]

    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=locs))
    # ``db.add`` / ``flush`` for access log — no-op
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]

    # Mock scalars after override — patch MagicMock to simulate scalars chain
    # We replace ``scalars`` with a callable returning object with ``all``
    class _Scalars:
        def all(self) -> list:
            return locs  # type: ignore[no-any-return]

    mock_db.scalars.return_value = _Scalars()

    # Need to handle the query shape: list endpoint does ``db.scalars(q).all()``
    # The mock above already returns locs.

    client = TestClient(app)
    resp = client.get("/api/v1/locations")
    # The endpoint writes an access log via db.add/flush — those are mocked.
    # Response shape is paginated.
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "has_more" in body
    assert "next_cursor" in body

    app.dependency_overrides.clear()


def test_list_locations_reports_history_and_forecastability() -> None:
    """History and forecastability are derived, not placeholders.

    The registry prints the History column, the gap badge and the "needs
    history" state from these fields. Nothing computed them, so History was
    permanently "—" and every location claimed to be forecasting.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    ready = _make_location(ws.id)
    young = _make_location(ws.id)

    mock_db = MagicMock()
    mock_db.scalars.return_value = _mock_scalars([ready, young]).scalars()
    mock_db.execute.return_value = MagicMock(
        all=MagicMock(
            return_value=[
                # 30 distinct days across a 30-day span: forecastable, no gaps.
                (ready.id, 30, date(2026, 1, 1), date(2026, 1, 30), 47.5),
                # 3 days across a 5-day span: too little history, 2 days missing.
                (young.id, 3, date(2026, 1, 1), date(2026, 1, 5), None),
            ]
        )
    )
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    resp = TestClient(app).get("/api/v1/locations")
    app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    rows = {row["id"]: row for row in resp.json()["data"]}

    assert rows[str(ready.id)]["history_days"] == 30
    assert rows[str(ready.id)]["gap_days"] == 0
    assert rows[str(ready.id)]["avg_covers"] == 47.5
    assert rows[str(ready.id)]["forecastable"] is True

    assert rows[str(young.id)]["history_days"] == 3
    assert rows[str(young.id)]["gap_days"] == 2
    assert rows[str(young.id)]["avg_covers"] is None
    assert rows[str(young.id)]["forecastable"] is False


def test_list_locations_leaves_history_unknown_when_the_count_fails() -> None:
    """A failed count is reported as unknown, never as "no history at all".

    The registry treats an absent ``forecastable`` as "not yet known", so a
    mocked or unreadable database must not mark every location as blocked.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    mock_db = MagicMock()
    mock_db.scalars.return_value = _mock_scalars([loc]).scalars()
    mock_db.execute.side_effect = RuntimeError("no such table: demand_observations")
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    resp = TestClient(app).get("/api/v1/locations")
    app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    row = resp.json()["data"][0]
    assert row["history_days"] is None
    assert row["gap_days"] is None
    assert row["forecastable"] is None


def test_create_location_requires_admin() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)

    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: viewer  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.post("/api/v1/locations", json={"name": "New Spot"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"

    app.dependency_overrides.clear()


def test_access_logs_requires_admin() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)

    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    # access-logs lives in access.py, which still authenticates via get_current_user
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.get("/api/v1/locations/access-logs")
    assert resp.status_code == 403

    app.dependency_overrides.clear()


def test_location_detail_not_found_shape() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)

    mock_db = MagicMock()
    mock_db.get.return_value = None
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.get(f"/api/v1/locations/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"

    app.dependency_overrides.clear()


def test_idempotency_key_header_accepted() -> None:
    """POST with Idempotency-Key should succeed (or reuse)."""
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    loc = _make_location(ws.id)

    mock_db = MagicMock()
    mock_db.get.return_value = loc
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    # For list filter etc.
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: admin  # type: ignore[assignment]

    # We mock ``db.get`` to return None first call (create) — let flush set id.
    # Simpler: mock the create path — patch ``Location`` creation by ensuring
    # ``db.add`` is called and ``loc.id`` is returned.
    # The endpoint creates a new Location; we just verify 201.

    # Override get to return None for initial lookup, then the created loc on second
    call_count = {"n": 0}

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == Location:
            # First call after idempotency check: no existing key -> create
            # Second call (idempotency reuse) would return loc
            if call_count["n"] == 1:
                return loc
            call_count["n"] += 1
            return None
        return None

    mock_db.get.side_effect = fake_get

    client = TestClient(app)
    resp = client.post(
        "/api/v1/locations",
        json={"name": "Idempotent Spot"},
        headers={"Idempotency-Key": "test-key-123"},
    )
    assert resp.status_code in (200, 201)
    body = resp.json()
    # Response is a location object
    assert "id" in body or "name" in body

    app.dependency_overrides.clear()


def test_audit_log_requires_admin() -> None:
    ws = _make_workspace()
    analyst = _make_user(ws.id, role=UserRole.analyst)
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    # audit-log lives in access.py, which still authenticates via get_current_user
    app.dependency_overrides[get_current_user] = lambda: analyst  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.get("/api/v1/audit-log")
    assert resp.status_code == 403

    app.dependency_overrides.clear()


def test_audit_log_resolves_the_actor_to_an_email() -> None:
    """The audit row stores only ``actor_id``; the endpoint resolves it.

    The activity feed reads ``actor_email``. Without the resolution it showed a
    blank where the person belongs — or, if a client had rendered the id, an
    opaque reference. The raw ``action``/``target`` stay in the payload; the
    client is what turns them into words.
    """
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    actor_id = uuid.uuid4()

    row = SimpleNamespace(
        id=1,
        workspace_id=ws.id,
        actor_id=actor_id,
        action="POST /api/v1/agent/messages",
        target="/api/v1/agent/messages",
        client="web",
        meta_data={"method": "POST"},
        created_at=datetime.now(UTC),
    )
    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[row]))
    mock_db.execute.return_value = MagicMock(
        all=MagicMock(return_value=[(actor_id, "owner@example.com")])
    )

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    resp = TestClient(app).get("/api/v1/audit-log")
    assert resp.status_code == 200, resp.text

    entry = resp.json()["data"][0]
    assert entry["actor_email"] == "owner@example.com"
    assert entry["action"] == "POST /api/v1/agent/messages"

    app.dependency_overrides.clear()
