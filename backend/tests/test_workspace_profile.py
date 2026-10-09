"""Workspace profile — every seat reads it, only governance changes it.

This is the tenant's own settings, so the boundary is different from the Site
Admin console (platform-wide, flag-gated):

* ``workspace:read``   — everyone. Which workspace you are in is context.
* ``workspace:manage`` — owner and administrator only, and audited.

Same DB-optional style as the other router tests: a mocked session, a
dependency override for the caller, and a status assertion.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.security.permissions import ROLE_CAPABILITIES, has_capability

WS_ID = uuid.uuid4()


def _workspace(name: str = "DemandIQ Demo") -> Workspace:
    return Workspace(
        id=WS_ID,
        name=name,
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _user(role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=WS_ID,
        name="Tester",
        email=f"{role.value}@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _client(role: UserRole, workspace: Workspace | None = None) -> tuple[TestClient, MagicMock]:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.get.return_value = workspace if workspace is not None else _workspace()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: _user(role)  # type: ignore[assignment]
    return TestClient(app), db


# --------------------------------------------------------------------------- #
# The capabilities themselves
# --------------------------------------------------------------------------- #


def test_only_governance_seats_may_manage_the_workspace() -> None:
    for role in UserRole:
        holds = has_capability(_user(role), "workspace:manage")
        expected = role in (UserRole.owner, UserRole.administrator)
        assert holds is expected, f"{role.value} workspace:manage should be {expected}"


def test_every_seat_may_read_its_workspace() -> None:
    for role, caps in ROLE_CAPABILITIES.items():
        assert "workspace:read" in caps, f"{role.value} cannot read its own workspace"


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #


def test_a_data_clerk_reads_the_workspace_but_cannot_edit_it() -> None:
    client, _ = _client(UserRole.operator)
    resp = client.get("/api/v1/workspace")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "DemandIQ Demo"
    assert body["country"] == "RW"
    assert body["can_manage"] is False  # the UI renders a read-only profile
    assert set(body["counts"]) == {"users", "locations", "forecasts"}


def test_a_clerk_cannot_change_workspace_settings() -> None:
    client, _ = _client(UserRole.operator)
    resp = client.patch("/api/v1/workspace", json={"name": "Renamed by clerk"})
    assert resp.status_code == 403
    assert "cannot change workspace settings" in resp.json()["error"]["message"].lower()


def test_an_analyst_cannot_change_workspace_settings_either() -> None:
    client, _ = _client(UserRole.analyst)
    assert client.patch("/api/v1/workspace", json={"country": "KE"}).status_code == 403


def test_an_owner_renames_the_workspace_and_it_is_audited() -> None:
    client, db = _client(UserRole.owner)
    resp = client.patch("/api/v1/workspace", json={"name": "Demo HQ"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Demo HQ"

    audits = [c.args[0] for c in db.add.call_args_list if hasattr(c.args[0], "action")]
    assert audits, "the rename was not audited"
    entry = audits[-1]
    assert entry.action == "workspace.update"
    assert entry.meta_data["before"]["name"] == "DemandIQ Demo"
    assert entry.meta_data["after"]["name"] == "Demo HQ"


def test_an_administrator_changes_locale_metadata() -> None:
    client, _ = _client(UserRole.administrator)
    resp = client.patch("/api/v1/workspace", json={"country": "KE", "timezone": "Africa/Nairobi"})
    assert resp.status_code == 200
    assert resp.json() == {"id": str(WS_ID), "country": "KE", "timezone": "Africa/Nairobi"}


def test_the_owner_reports_can_manage_true() -> None:
    client, _ = _client(UserRole.owner)
    assert client.get("/api/v1/workspace").json()["can_manage"] is True


def test_an_empty_patch_is_refused() -> None:
    client, _ = _client(UserRole.owner)
    resp = client.patch("/api/v1/workspace", json={})
    assert resp.status_code == 422


def test_a_one_letter_country_is_refused() -> None:
    client, _ = _client(UserRole.owner)
    resp = client.patch("/api/v1/workspace", json={"country": "R"})
    assert resp.status_code == 422


def test_a_missing_workspace_is_a_404() -> None:
    client, db = _client(UserRole.owner)
    db.get.return_value = None
    assert client.get("/api/v1/workspace").status_code == 404
