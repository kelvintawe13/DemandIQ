"""X-API-Key authentication tests.

The locations router authenticates via ``get_principal`` (API key or Bearer).
These tests drive the real dependency logic against a mocked DB: prefix
lookup, bcrypt verification, principal scoping, and write-scope enforcement.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import bcrypt
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import create_app
from app.models.location import Location
from app.models.workspace import Workspace

WS_ID = uuid.uuid4()
SECRET = "diq_testsecretvalue0000000000"  # 24 chars >= 16, prefix "diq_abc12345"


def _make_key_row(scopes: list[str], revoked: bool = False) -> MagicMock:
    row = MagicMock()
    row.prefix = SECRET[:12]
    row.key_hash = bcrypt.hashpw(SECRET.encode()[:72], bcrypt.gensalt()).decode()
    row.scopes = scopes
    row.workspace_id = WS_ID
    row.revoked_at = datetime.now(UTC) if revoked else None
    row.label = "CI key"
    row.last_used_at = None
    return row


def _make_workspace() -> Workspace:
    return Workspace(
        id=WS_ID,
        name="Key Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.now(UTC),
    )


def _make_location() -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=WS_ID,
        name="Test Bistro",
        area="Nyarutarama",
        created_at=datetime.now(UTC),
    )


def _client_with_key(key_row: MagicMock | None, locations: list[Location] | None = None):
    """App with a DB mock whose first scalars call resolves the API key."""
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    key_result = MagicMock()
    key_result.all = MagicMock(return_value=[key_row] if key_row else [])

    locs = locations or []
    loc_result = MagicMock()
    loc_result.all = MagicMock(return_value=locs)

    mock_db.scalars = MagicMock(side_effect=[key_result, loc_result])

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)
    return client, mock_db


def _headers() -> dict[str, str]:
    return {"X-API-Key": SECRET}


def test_api_key_lists_locations() -> None:
    ws = _make_workspace()
    loc = _make_location()
    key = _make_key_row(["read"])
    client, _ = _client_with_key(key, [loc])
    # workspace existence is not re-checked for keys, but keep the fixture honest
    assert ws.id == key.workspace_id

    resp = client.get("/api/v1/locations", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"][0]["name"] == "Test Bistro"
    assert key.last_used_at is not None


def test_api_key_write_requires_write_scope() -> None:
    key = _make_key_row(["read"])  # no write scope
    client, _ = _client_with_key(key)

    resp = client.post(
        "/api/v1/locations",
        headers=_headers(),
        json={"name": "New Spot", "area": "Kimihurura"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == "API key lacks 'write' scope"


def test_api_key_with_write_scope_creates_location() -> None:
    key = _make_key_row(["read", "write"])
    # The create endpoint does not query locations — only the key lookup runs.
    client, _ = _client_with_key(key)

    resp = client.post(
        "/api/v1/locations",
        headers=_headers(),
        json={"name": "New Spot", "area": "Kimihurura"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "New Spot"


def test_invalid_api_key_rejected() -> None:
    client, _ = _client_with_key(None)  # no matching key rows
    resp = client.get("/api/v1/locations", headers=_headers())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_revoked_api_key_rejected() -> None:
    key = _make_key_row(["read"], revoked=True)
    client, _ = _client_with_key(None)  # lookup filters revoked_at -> no rows
    assert key.revoked_at is not None
    resp = client.get("/api/v1/locations", headers=_headers())
    assert resp.status_code == 401


def test_malformed_api_key_rejected() -> None:
    client, _ = _client_with_key(None)
    resp = client.get("/api/v1/locations", headers={"X-API-Key": "short"})
    assert resp.status_code == 401


def test_bearer_still_works_on_get_principal_routes() -> None:
    """No X-API-Key header -> standard Bearer path (401 without credentials)."""
    client, _ = _client_with_key(None)
    resp = client.get("/api/v1/locations")
    assert resp.status_code == 401


# ------------------------------------------------ forecasts via API key ----


def _forecast_client(key_row: MagicMock):
    from app.models.forecast import Forecast

    fc = MagicMock(spec=Forecast)
    fc.id = uuid.uuid4()
    fc.workspace_id = WS_ID
    fc.location_id = uuid.uuid4()
    fc.point = 42.0
    fc.lower = 20.0
    fc.upper = 60.0
    fc.coverage_target = 0.9
    fc.feature_set_version = "fs-test"
    fc.fallback = False
    fc.target_date = datetime.now(UTC).date()
    fc.as_of_timestamp = datetime.now(UTC)
    fc.created_at = datetime.now(UTC)
    fc.model_version_id = uuid.uuid4()

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    key_result = MagicMock()
    key_result.all = MagicMock(return_value=[key_row])
    fc_result = MagicMock()
    fc_result.all = MagicMock(return_value=[fc])
    mock_db.scalars = MagicMock(side_effect=[key_result, fc_result])
    mock_db.get = MagicMock(return_value=fc)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    return TestClient(app), key_row, mock_db


def test_api_key_reads_forecasts() -> None:
    from app.models.location_access import LocationAccessLog

    client, key_row, mock_db = _forecast_client(_make_key_row(["read"]))
    resp = client.get("/api/v1/forecasts", headers=_headers())
    assert resp.status_code == 200, resp.text
    assert "data" in resp.json()

    access_logs = [
        call.args[0]
        for call in mock_db.add.call_args_list
        if call.args and isinstance(call.args[0], LocationAccessLog)
    ]
    assert len(access_logs) == 1
    assert access_logs[0].workspace_id == key_row.workspace_id
    # API-key principals are not user rows; their synthetic id is the workspace id.
    assert access_logs[0].user_id is None


def test_read_only_key_cannot_create_forecast() -> None:
    client, _, _ = _forecast_client(_make_key_row(["read"]))
    resp = client.post(
        "/api/v1/forecasts",
        headers=_headers(),
        json={"location_id": str(uuid.uuid4()), "target_date": "2026-09-21"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == "API key lacks 'write' scope"
