"""Site-admin API tests (mocked DB)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.user import User, UserRole

WS_ID = uuid.uuid4()


def _user(role: UserRole = UserRole.owner) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=WS_ID,
        name="Admin",
        email="admin@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        is_site_admin=True,
        created_at=datetime.now(UTC),
    )


def _client(mock_db: MagicMock, user: User) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


def test_site_admin_requires_admin():
    mock_db = MagicMock()
    viewer = _user(UserRole.viewer)
    viewer.is_site_admin = False
    client = _client(mock_db, viewer)
    resp = client.get("/api/v1/admin/site/overview")
    assert resp.status_code == 403


def test_overview_returns_stats():
    mock_db = MagicMock()
    user = _user()
    mock_db.scalar.return_value = 7
    mock_db.execute.return_value.all.return_value = [("owner", 2), ("viewer", 5)]
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    client = _client(mock_db, user)
    resp = client.get("/api/v1/admin/site/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["users"]["total"] == 7
    assert body["roles"]["owner"] == 2


def test_role_change_blocked_for_self_demotion():
    mock_db = MagicMock()
    user = _user()
    mock_db.get.return_value = user  # target == current user
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    client = _client(mock_db, user)
    resp = client.patch(f"/api/v1/admin/site/users/{user.id}", json={"role": "viewer"})
    assert resp.status_code == 422
    assert "your own role" in resp.json()["error"]["message"].lower()


def test_self_deactivation_blocked():
    mock_db = MagicMock()
    user = _user()
    mock_db.get.return_value = user
    client = _client(mock_db, user)
    resp = client.patch(f"/api/v1/admin/site/users/{user.id}", json={"deactivated": True})
    assert resp.status_code == 422


def test_password_reset_requires_length():
    mock_db = MagicMock()
    user = _user()
    target = _user(UserRole.viewer)
    target.id = uuid.uuid4()
    mock_db.get.return_value = target
    mock_db.add = MagicMock()
    mock_db.execute = MagicMock()
    mock_db.flush = MagicMock()
    client = _client(mock_db, user)
    resp = client.post(
        f"/api/v1/admin/site/users/{target.id}/reset-password", json={"new_password": "short"}
    )
    assert resp.status_code == 422
    resp2 = client.post(
        f"/api/v1/admin/site/users/{target.id}/reset-password",
        json={"new_password": "a-Long-Enough-Passw0rd"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["password_reset"] is True


def test_settings_upsert_and_get():
    mock_db = MagicMock()
    user = _user()
    mock_db.execute.return_value.all.return_value = []
    client = _client(mock_db, user)
    resp = client.patch(
        "/api/v1/admin/site/settings", json={"settings": {"registration_open": False}}
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] == ["registration_open"]
    resp2 = client.get("/api/v1/admin/site/settings")
    assert resp2.status_code == 200
    assert "registration_open" in resp2.json()["data"]


def test_unknown_role_rejected():
    mock_db = MagicMock()
    user = _user()
    client = _client(mock_db, user)
    resp = client.get("/api/v1/admin/site/users", params={"role": "superhero"})
    assert resp.status_code == 422


def test_unknown_status_filter_rejected():
    mock_db = MagicMock()
    user = _user()
    client = _client(mock_db, user)
    resp = client.get("/api/v1/admin/site/users", params={"status": "ghost"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Directory writes obey the same hierarchy as member management. The site
# directory must not become the loophole that grants ownership.
# ---------------------------------------------------------------------------


def _patch_target(mock_db: MagicMock, actor: User, target: User) -> TestClient:
    mock_db.get.return_value = target
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    return _client(mock_db, actor)


def test_site_admin_cannot_grant_ownership():
    """An administrator site-admin may not mint an owner through the directory."""
    mock_db = MagicMock()
    actor = _user(UserRole.administrator)
    target = _user(UserRole.viewer)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    client = _patch_target(mock_db, actor, target)
    resp = client.patch(f"/api/v1/admin/site/users/{target.id}", json={"role": "owner"})
    assert resp.status_code == 403
    assert "grant owner" in resp.json()["error"]["message"].lower()


def test_site_admin_cannot_grant_peer_role():
    """Strictly below only: an administrator cannot create another."""
    mock_db = MagicMock()
    actor = _user(UserRole.administrator)
    target = _user(UserRole.viewer)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    client = _patch_target(mock_db, actor, target)
    resp = client.patch(f"/api/v1/admin/site/users/{target.id}", json={"role": "administrator"})
    assert resp.status_code == 403


def test_site_admin_cannot_touch_account_above_their_rank():
    mock_db = MagicMock()
    actor = _user(UserRole.administrator)
    target = _user(UserRole.owner)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    target.is_site_admin = False
    client = _patch_target(mock_db, actor, target)
    resp = client.patch(f"/api/v1/admin/site/users/{target.id}", json={"role": "analyst"})
    assert resp.status_code == 403
    assert "at or above your role" in resp.json()["error"]["message"].lower()


def test_owner_cannot_deactivate_a_site_administrator():
    """The platform flag is moved deliberately, not by directory edit."""
    mock_db = MagicMock()
    actor = _user(UserRole.owner)
    target = _user(UserRole.administrator)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    target.is_site_admin = True
    client = _patch_target(mock_db, actor, target)
    resp = client.patch(f"/api/v1/admin/site/users/{target.id}", json={"deactivated": True})
    assert resp.status_code == 403
    assert "not deactivated" in resp.json()["error"]["message"].lower()


def test_owner_cannot_re_role_a_site_administrator():
    mock_db = MagicMock()
    actor = _user(UserRole.owner)
    target = _user(UserRole.administrator)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    target.is_site_admin = True
    client = _patch_target(mock_db, actor, target)
    resp = client.patch(f"/api/v1/admin/site/users/{target.id}", json={"role": "analyst"})
    assert resp.status_code == 403
    assert "not re-roled" in resp.json()["error"]["message"].lower()


def test_owner_may_grant_a_role_below_their_rank():
    mock_db = MagicMock()
    actor = _user(UserRole.owner)
    target = _user(UserRole.viewer)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    target.is_site_admin = False  # an ordinary member of the workspace
    client = _patch_target(mock_db, actor, target)
    resp = client.patch(f"/api/v1/admin/site/users/{target.id}", json={"role": "analyst"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "analyst"


# ---------------------------------------------------------------------------
# Read endpoints behind the console's sections
# ---------------------------------------------------------------------------


def test_user_detail_returns_sessions_and_workspace():
    mock_db = MagicMock()
    actor = _user()
    target = _user(UserRole.analyst)
    target.id = uuid.uuid4()
    target.workspace_id = actor.workspace_id
    target.job_title = "Planner"
    workspace = MagicMock()
    workspace.id = target.workspace_id
    workspace.name = "DemandIQ Demo"
    workspace.business_type = "cafe"
    mock_db.get.side_effect = lambda model, _id: target if model is User else workspace
    session = MagicMock()
    session.id = uuid.uuid4()
    session.device = "Chrome on macOS"
    session.origin = "127.0.0.1"
    session.trusted_device = True
    session.last_seen_at = datetime.now(UTC)
    mock_db.scalars.side_effect = [
        MagicMock(all=MagicMock(return_value=[session])),  # sessions
        MagicMock(all=MagicMock(return_value=[])),  # identities
        MagicMock(all=MagicMock(return_value=[])),  # audit events
    ]
    client = _client(mock_db, actor)
    resp = client.get(f"/api/v1/admin/site/users/{target.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == target.email
    assert body["role_label"] == "Analyst"
    assert body["workspace"]["name"] == "DemandIQ Demo"
    assert body["sessions"][0]["device"] == "Chrome on macOS"
    assert body["last_seen_at"] is not None
    assert body["can_manage"] is True  # owner outranks analyst


def test_user_detail_404_for_unknown_account():
    mock_db = MagicMock()
    mock_db.get.return_value = None
    client = _client(mock_db, _user())
    resp = client.get(f"/api/v1/admin/site/users/{uuid.uuid4()}")
    assert resp.status_code == 404


def test_health_reports_runtime_facts():
    mock_db = MagicMock()
    mock_db.execute.return_value = MagicMock(
        first=MagicMock(return_value=MagicMock(__getitem__=lambda _s, _i: "deadbeef"))
    )
    mock_db.scalar.side_effect = ["deadbeef", 4, datetime.now(UTC), datetime.now(UTC)]
    client = _client(mock_db, _user())
    resp = client.get("/api/v1/admin/site/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"]["status"] == "ok"
    assert body["database"]["latency_ms"] is not None
    assert body["migration_revision"] == "deadbeef"
    assert body["sessions"]["active"] == 4
    assert "version" in body["app"]
    assert body["pipeline"]["latest_forecast_at"] is not None


def test_health_degrades_when_db_is_down():
    mock_db = MagicMock()
    mock_db.execute.side_effect = RuntimeError("connection refused")
    mock_db.scalar.return_value = None
    client = _client(mock_db, _user())
    resp = client.get("/api/v1/admin/site/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"]["status"] == "unavailable"
    assert body["database"]["latency_ms"] is None


def test_workspace_detail_lists_footprint_and_members():
    mock_db = MagicMock()
    actor = _user()
    ws = MagicMock()
    ws.id = uuid.uuid4()
    ws.name = "DemandIQ Demo"
    ws.business_type = "cafe"
    ws.country = "RW"
    ws.timezone = "Africa/Kigali"
    ws.created_at = datetime.now(UTC)
    mock_db.get.return_value = ws
    member = _user(UserRole.analyst)
    member.workspace_id = ws.id
    mock_db.scalar.side_effect = lambda _stmt: 3
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[member]))
    client = _client(mock_db, actor)
    resp = client.get(f"/api/v1/admin/site/workspaces/{ws.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "DemandIQ Demo"
    assert body["counts"] == {"users": 3, "locations": 3, "forecasts": 3, "models": 3}
    assert body["members"][0]["role"] == "analyst"


def test_audit_filters_and_reports_totals():
    mock_db = MagicMock()
    actor = _user()
    entry = MagicMock()
    entry.id = 1
    entry.action = "site.user.update"
    entry.target = str(uuid.uuid4())
    entry.actor_id = actor.id
    entry.client = "web"
    entry.meta_data = {"after": {"role": "viewer"}}
    entry.created_at = datetime.now(UTC)
    mock_db.scalar.return_value = 40
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[entry]))
    mock_db.execute.return_value = MagicMock(
        all=MagicMock(return_value=[(str(actor.id), "admin@example.com")])
    )
    client = _client(mock_db, actor)
    resp = client.get("/api/v1/admin/site/audit", params={"client": "web", "limit": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 40
    assert body["has_more"] is True
    assert body["data"][0]["actor_email"] == "admin@example.com"


def test_audit_requires_site_admin():
    mock_db = MagicMock()
    viewer = _user(UserRole.viewer)
    viewer.is_site_admin = False
    client = _client(mock_db, viewer)
    assert client.get("/api/v1/admin/site/audit").status_code == 403
    assert client.get("/api/v1/admin/site/health").status_code == 403
    assert client.get(f"/api/v1/admin/site/users/{uuid.uuid4()}").status_code == 403
