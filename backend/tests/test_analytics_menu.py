"""Analytics (5) + menu-groups (4) tests per API_ENDPOINTS.md.

All tests are DB-optional via mocked sessions, same style as existing
``test_locations_access.py`` and ``test_forecast_policy.py``.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.location import DemandObservation, Location, MenuGroup
from app.models.user import User, UserRole
from app.models.workspace import Workspace

# ---------------------------------------------------------------- fixtures ---


def _make_workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_user(ws_id: uuid.UUID, role: UserRole = UserRole.owner) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Test User",
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_location(ws_id: uuid.UUID, name: str = "Kigali Central") -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name=name,
        area="Kigali",
        cuisine="Rwandan",
        archived_at=None,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_observations(
    ws_id: uuid.UUID,
    loc_id: uuid.UUID,
    start: datetime.date,
    n: int,
    base: int = 40,
) -> list[DemandObservation]:
    rows: list[DemandObservation] = []
    for i in range(n):
        d = start + datetime.timedelta(days=i)
        rows.append(
            DemandObservation(
                id=i + 1,
                workspace_id=ws_id,
                location_id=loc_id,
                date=d,
                demand_qty=base + (i % 10),
                recorded_at=datetime.datetime.combine(d, datetime.time(12, 0), tzinfo=datetime.UTC),
            )
        )
    return rows


# ---------------------------------------------------------------- helpers ----


def _mock_db_for_analytics(
    observations: list[DemandObservation],
    locations: list[Location],
    menu_groups: list[MenuGroup] | None = None,
) -> MagicMock:
    """Create a MagicMock that mimics scalars/get/add for analytics+menu."""
    menu_groups = menu_groups or []
    mock_db = MagicMock()

    # Store for menu CRUD
    store: dict[uuid.UUID, MenuGroup] = {g.id: g for g in menu_groups}

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt).lower()

        class _S:
            def __init__(self, data):  # type: ignore[no-untyped-def]
                self._data = data

            def all(self):  # type: ignore[no-untyped-def]
                return self._data

        class _R:
            def __init__(self, data):  # type: ignore[no-untyped-def]
                self._data = data

            def scalars(self):  # type: ignore[no-untyped-def]
                return _S(self._data)

        if "demand_observations" in txt:
            return _R([]).scalars()
        if "locations" in txt:
            # Return with all() later handled via mock's scalars() returning locations?
            # We capture via direct scalars mock side_effect; need to return locations.
            # The outer side_effect will be replaced below — keep logic.
            return _R([]).scalars()
        if "menu_groups" in txt:
            return _R([]).scalars()
        return _R([]).scalars()

    # We'll set scalars to dynamic side_effect that checks stmt string properly
    def dynamic_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt).lower()
        if "demand_observations" in txt:
            # Return all observations; analytics code filters in Python
            class _S:
                def all(self):  # type: ignore[no-untyped-def]
                    return observations

            class _R:
                def scalars(self):  # type: ignore[no-untyped-def]
                    return _S()

            return _R().scalars()
        if "locations" in txt:

            class _S:
                def all(self):  # type: ignore[no-untyped-def]
                    return locations

            class _R:
                def scalars(self):  # type: ignore[no-untyped-def]
                    return _S()

            return _R().scalars()
        if "menu_groups" in txt:

            class _S:
                def all(self):  # type: ignore[no-untyped-def]
                    return list(store.values())

            class _R:
                def scalars(self):  # type: ignore[no-untyped-def]
                    return _S()

            return _R().scalars()

        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalars.side_effect = dynamic_scalars
    mock_db.scalar.return_value = None

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == Location:
            for loc in locations:
                if loc.id == pk:
                    return loc
            return None
        if model == MenuGroup:
            return store.get(pk)
        return None

    mock_db.get.side_effect = fake_get

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, MenuGroup):
            store[obj.id] = obj

    mock_db.add.side_effect = fake_add
    mock_db.flush = MagicMock()
    mock_db.delete.side_effect = lambda obj: store.pop(obj.id, None) if isinstance(obj, MenuGroup) else None  # type: ignore[no-untyped-def]

    # expose store for test introspection
    mock_db._store = store  # type: ignore[attr-defined]
    return mock_db


# ---------------------------------------------------------------- analytics ---


def test_analytics_unauthenticated_rejected() -> None:
    app = create_app()
    client = TestClient(app)
    for path in [
        "/api/v1/analytics/summary",
        "/api/v1/analytics/trend",
        "/api/v1/analytics/by-location",
        "/api/v1/analytics/breakdown",
        "/api/v1/analytics/insights",
    ]:
        resp = client.get(path)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


def test_analytics_summary_kpis_and_previous_period() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)
    # Current period: 2026-01-11 to 2026-01-20 (10 days) — average 44.5
    # We'll use today-based default window 30d: create observations for last 60 days
    today = datetime.datetime.now(datetime.UTC).date()
    start_cur = today - datetime.timedelta(days=29)
    start_prev = start_cur - datetime.timedelta(days=30)
    # 60 days observations for loc
    obs = _make_observations(ws.id, loc.id, start_prev, 60, base=50)
    # Make cur period higher by +10 vs prev for growth check
    for r in obs:
        if r.date >= start_cur:
            r.demand_qty += 10

    mock_db = _mock_db_for_analytics(obs, [loc])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    # Default range (30d)
    resp = client.get("/api/v1/analytics/summary")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total_visitors" in body
    assert "total_visitors_previous" in body
    assert "average_daily_demand" in body
    assert "peak_day_visitors" in body
    assert "growth_rate" in body
    assert "period" in body
    assert "previous_period" in body
    # Previous comparison: current should be > previous due to +10 bump
    assert body["total_visitors"] > body["total_visitors_previous"]
    assert body["total_visitors_change_pct"] > 0

    # With explicit from/to
    resp = client.get(
        f"/api/v1/analytics/summary?from={start_cur.isoformat()}&to={(start_cur + datetime.timedelta(days=9)).isoformat()}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["period"]["start"] == start_cur.isoformat()

    # With location_id filter
    resp = client.get(f"/api/v1/analytics/summary?location_id={loc.id}")
    assert resp.status_code == 200

    # Invalid location -> 404
    resp = client.get(f"/api/v1/analytics/summary?location_id={uuid.uuid4()}")
    assert resp.status_code == 404

    # Range param alternative
    resp = client.get("/api/v1/analytics/summary?range=7d")
    assert resp.status_code == 200
    assert resp.json()["period"] is not None

    app.dependency_overrides.clear()


def test_analytics_trend_granularity() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)
    today = datetime.datetime.now(datetime.UTC).date()
    start = today - datetime.timedelta(days=29)
    obs = _make_observations(ws.id, loc.id, start - datetime.timedelta(days=30), 60, base=40)

    mock_db = _mock_db_for_analytics(obs, [loc])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    # Daily granularity
    resp = client.get("/api/v1/analytics/trend?granularity=daily")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granularity"] == "daily"
    assert "data" in body
    assert len(body["data"]) == 30
    first = body["data"][0]
    assert "label" in first
    assert "current" in first
    assert "previous" in first

    # Weekly granularity
    resp = client.get("/api/v1/analytics/trend?granularity=weekly")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granularity"] == "weekly"
    # 30 days -> 5 weeks
    assert len(body["data"]) == 5
    assert all("current" in p and "previous" in p for p in body["data"])

    # With range + location filter
    resp = client.get(f"/api/v1/analytics/trend?range=7d&location_id={loc.id}&granularity=daily")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 7

    app.dependency_overrides.clear()


def test_analytics_by_location_share_and_pagination() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc1 = _make_location(ws.id, name="Downtown Plaza")
    loc2 = _make_location(ws.id, name="Westfield Mall")
    loc3 = _make_location(ws.id, name="Airport Terminal")
    today = datetime.datetime.now(datetime.UTC).date()
    start = today - datetime.timedelta(days=29)
    # loc1 gets higher demand
    obs1 = _make_observations(ws.id, loc1.id, start, 30, base=60)
    obs2 = _make_observations(ws.id, loc2.id, start, 30, base=40)
    obs3 = _make_observations(ws.id, loc3.id, start, 30, base=20)
    obs = obs1 + obs2 + obs3

    mock_db = _mock_db_for_analytics(obs, [loc1, loc2, loc3])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/analytics/by-location")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "period" in body
    assert "has_more" in body
    assert "next_cursor" in body
    assert len(body["data"]) == 3
    # Check ordering: Downtown first (highest)
    assert body["data"][0]["name"] == "Downtown Plaza"
    assert body["data"][0]["share"].endswith("%")
    assert body["data"][0]["visitors"] > body["data"][1]["visitors"]
    # Color present
    assert body["data"][0]["color"].startswith("#")

    # Pagination limit 2
    resp = client.get("/api/v1/analytics/by-location?limit=2")
    assert resp.status_code == 200
    p1 = resp.json()
    assert len(p1["data"]) == 2
    assert p1["has_more"] is True
    assert p1["next_cursor"] is not None

    resp = client.get(f"/api/v1/analytics/by-location?limit=2&cursor={p1['next_cursor']}")
    assert resp.status_code == 200
    p2 = resp.json()
    assert len(p2["data"]) == 1
    assert p2["has_more"] is False

    # Invalid cursor
    resp = client.get("/api/v1/analytics/by-location?cursor=invalid!")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_cursor"

    app.dependency_overrides.clear()


def test_analytics_breakdown_pagination_and_spark() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id, name="City Center")
    loc2 = _make_location(ws.id, name="Other")
    today = datetime.datetime.now(datetime.UTC).date()
    start = today - datetime.timedelta(days=13)
    obs = _make_observations(ws.id, loc.id, start, 14, base=30) + _make_observations(
        ws.id, loc2.id, start, 14, base=10
    )
    # Add previous period for change calc
    obs += _make_observations(ws.id, loc.id, start - datetime.timedelta(days=14), 14, base=25)
    obs += _make_observations(ws.id, loc2.id, start - datetime.timedelta(days=14), 14, base=8)

    mock_db = _mock_db_for_analytics(obs, [loc, loc2])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    # Per-location aggregate mode (no location_id)
    resp = client.get("/api/v1/analytics/breakdown?range=14d")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "has_more" in body
    assert "next_cursor" in body
    assert len(body["data"]) >= 2
    row = body["data"][0]
    assert "visitors" in row
    assert "previous" in row
    assert "change" in row
    assert "isPositive" in row or "is_positive" in row
    assert "spark" in row
    assert isinstance(row["spark"], list)
    assert len(row["spark"]) >= 1

    # Pagination
    resp = client.get("/api/v1/analytics/breakdown?limit=1")
    assert resp.status_code == 200
    assert resp.json()["has_more"] is True

    # Daily breakdown mode with location filter
    resp = client.get(f"/api/v1/analytics/breakdown?range=7d&location_id={loc.id}")
    assert resp.status_code == 200, resp.text
    d_body = resp.json()
    assert len(d_body["data"]) == 7
    assert all("date" in r and "spark" in r for r in d_body["data"])

    # Invalid cursor
    resp = client.get("/api/v1/analytics/breakdown?cursor=bad")
    assert resp.status_code == 400

    app.dependency_overrides.clear()


def test_analytics_insights_generated() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id, name="Downtown Plaza")
    loc2 = _make_location(ws.id, name="Westfield Mall")
    today = datetime.datetime.now(datetime.UTC).date()
    start = today - datetime.timedelta(days=29)
    obs = _make_observations(ws.id, loc.id, start, 30, base=50)
    obs2 = _make_observations(ws.id, loc2.id, start, 30, base=30)
    # Make a peak on last day for loc
    for r in obs:
        if r.date == today:
            r.demand_qty = 300
    obs = obs + obs2

    mock_db = _mock_db_for_analytics(obs, [loc, loc2])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/analytics/insights")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert "period" in body
    assert len(body["data"]) >= 1
    assert "message" in body["data"][0]
    assert "type" in body["data"][0]
    # Also alias topInsights
    assert "topInsights" in body or "top_insights" in body
    insights = body.get("topInsights") or body.get("top_insights") or []
    assert len(insights) >= 1

    # With location filter
    resp = client.get(f"/api/v1/analytics/insights?location_id={loc.id}")
    assert resp.status_code == 200

    # With range
    resp = client.get("/api/v1/analytics/insights?range=7d")
    assert resp.status_code == 200

    app.dependency_overrides.clear()


# ---------------------------------------------------------------- menu groups ---


def test_menu_groups_unauthenticated_rejected() -> None:
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/menu-groups")
    assert resp.status_code == 401


def test_menu_crud_and_admin_guard() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    viewer = _make_user(ws.id, role=UserRole.viewer)

    mock_db = _mock_db_for_analytics([], [])
    # Ensure scalars returns menu groups correctly — already handled

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Create — admin success
    resp = client.post(
        "/api/v1/menu-groups",
        json={"name": "Grill", "item_count": 5, "cover_share": 25.5, "perishability": "high"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Grill"
    assert body["perishability"] == "high"
    gid = body["id"]
    assert body["workspace_id"] == str(ws.id)

    # Create with invalid perishability -> 422
    resp = client.post(
        "/api/v1/menu-groups",
        json={"name": "Bad", "item_count": 1, "perishability": "ultra"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"

    # Invalid cover_share -> 422
    resp = client.post(
        "/api/v1/menu-groups",
        json={"name": "Bad2", "item_count": 1, "perishability": "low", "cover_share": 999},
    )
    assert resp.status_code == 422

    # List paginated
    resp = client.get("/api/v1/menu-groups")
    assert resp.status_code == 200
    lst = resp.json()
    assert "data" in lst
    assert "has_more" in lst
    assert "next_cursor" in lst
    assert len(lst["data"]) == 1

    # Create second for pagination
    resp = client.post(
        "/api/v1/menu-groups",
        json={"name": "Salads", "item_count": 3, "perishability": "medium"},
    )
    assert resp.status_code == 201
    gid2 = resp.json()["id"]

    resp = client.get("/api/v1/menu-groups?limit=1")
    assert resp.status_code == 200
    assert resp.json()["has_more"] is True
    assert resp.json()["next_cursor"] is not None
    cur = resp.json()["next_cursor"]
    resp = client.get(f"/api/v1/menu-groups?limit=1&cursor={cur}")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    # Detail
    resp = client.get(f"/api/v1/menu-groups/{gid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == gid

    # Detail not found
    resp = client.get(f"/api/v1/menu-groups/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"

    # Patch — admin
    resp = client.patch(
        f"/api/v1/menu-groups/{gid}", json={"perishability": "low", "cover_share": 30.0}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["perishability"] == "low"
    assert resp.json()["cover_share"] == 30.0

    # Patch invalid perishability -> 422
    resp = client.patch(f"/api/v1/menu-groups/{gid}", json={"perishability": "bad"})
    assert resp.status_code == 422

    # Workspace scoping: other workspace's group should 404
    other_ws = uuid.uuid4()
    other_group = MenuGroup(
        id=uuid.uuid4(),
        workspace_id=other_ws,
        name="Other",
        item_count=1,
        cover_share=None,
        perishability="low",
    )
    mock_db._store[other_group.id] = other_group  # type: ignore[attr-defined]
    resp = client.get(f"/api/v1/menu-groups/{other_group.id}")
    assert resp.status_code == 404

    # Delete — admin
    resp = client.delete(f"/api/v1/menu-groups/{gid2}")
    assert resp.status_code == 204

    # Verify deleted not found
    resp = client.get(f"/api/v1/menu-groups/{gid2}")
    assert resp.status_code == 404

    # --- Viewer guard: create/patch/delete forbidden ---
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]

    resp = client.post(
        "/api/v1/menu-groups",
        json={"name": "ShouldFail", "item_count": 1, "perishability": "low"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"

    resp = client.patch(f"/api/v1/menu-groups/{gid}", json={"name": "X"})
    assert resp.status_code == 403

    resp = client.delete(f"/api/v1/menu-groups/{gid}")
    assert resp.status_code == 403

    # Viewer can still list and get
    resp = client.get("/api/v1/menu-groups")
    assert resp.status_code == 200
    resp = client.get(f"/api/v1/menu-groups/{gid}")
    assert resp.status_code == 200

    # Invalid cursor on menu list
    resp = client.get("/api/v1/menu-groups?cursor=notvalid!!")
    assert resp.status_code == 400

    app.dependency_overrides.clear()


def test_menu_workspace_scoping_isolation() -> None:
    ws = _make_workspace()
    other_ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.owner)
    # Two groups, one per workspace
    g1 = MenuGroup(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="G1",
        item_count=2,
        cover_share=10.0,
        perishability="high",
    )
    g2 = MenuGroup(
        id=uuid.uuid4(),
        workspace_id=other_ws.id,
        name="G2",
        item_count=5,
        cover_share=20.0,
        perishability="low",
    )
    mock_db = _mock_db_for_analytics([], [], [g1, g2])
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/menu-groups")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Should only see G1
    assert len(data) == 1
    assert data[0]["id"] == str(g1.id)

    app.dependency_overrides.clear()
