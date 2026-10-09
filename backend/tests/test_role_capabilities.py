"""Role capability model — the authorisation matrix, asserted end to end.

These tests encode the product decisions, not just the mechanism:

* an **owner** runs the business (members, approvals, billing, audit, pipeline
  visibility) but does **not** get the modelling console;
* an **administrator** runs the platform and models, but cannot change the
  subscription;
* a **data clerk** (``operator``) gets data in and clean, and nothing else;
* an **analyst** adds analysis, simulation and reports;
* a **viewer** only reads.

Same DB-optional style as the other router tests: a mocked session, a
dependency override for the caller, and a status-code assertion.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal
from app.security.permissions import (
    MODELLING_CAPABILITIES,
    ROLE_CAPABILITIES,
    capabilities_for,
    has_capability,
)


def _workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _user(ws_id: uuid.UUID, role: UserRole) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Test User",
        email=f"{role.value}@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _client_for(user: User) -> TestClient:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


def _status_for(role: UserRole, method: str, path: str, **kw: Any) -> int:
    ws = _workspace()
    client = _client_for(_user(ws.id, role))
    resp = client.request(method, path, **kw)
    return resp.status_code


def _allowed(role: UserRole, method: str, path: str, **kw: Any) -> bool:
    """True when the request was not refused with 403 (other failures are fine)."""
    return _status_for(role, method, path, **kw) != 403


# --------------------------------------------------------------------------- #
# The bundles themselves
# --------------------------------------------------------------------------- #


MANAGEMENT = frozenset(
    {"models:manage", "datasets:manage", "experiments:manage", "features:manage"}
)


def test_only_the_administrator_can_manage_the_modelling_console() -> None:
    """The whole point of the split: owners do not train or promote models."""
    ws = _workspace()
    for role in UserRole:
        user = _user(ws.id, role)
        holds = sorted(cap for cap in MANAGEMENT if has_capability(user, cap))
        if role is UserRole.administrator:
            assert holds == sorted(MANAGEMENT)
        else:
            assert holds == [], f"{role.value} should not manage models but holds {holds}"


def test_owner_holds_no_modelling_capability_at_all() -> None:
    """Not even read access: the registry is not the owner's surface."""
    ws = _workspace()
    owner = _user(ws.id, UserRole.owner)
    assert [cap for cap in MODELLING_CAPABILITIES if has_capability(owner, cap)] == []
    # The analyst keeps read access for the forecast explainer.
    analyst = _user(ws.id, UserRole.analyst)
    assert [cap for cap in MODELLING_CAPABILITIES if has_capability(analyst, cap)] == [
        "models:read"
    ]


def test_every_role_bundle_is_a_superset_of_viewer() -> None:
    viewer = ROLE_CAPABILITIES[UserRole.viewer]
    for role, caps in ROLE_CAPABILITIES.items():
        assert viewer <= caps, f"{role.value} does not include the viewer reads"


def test_bundles_grow_with_responsibility() -> None:
    assert ROLE_CAPABILITIES[UserRole.viewer] < ROLE_CAPABILITIES[UserRole.operator]
    assert ROLE_CAPABILITIES[UserRole.operator] < ROLE_CAPABILITIES[UserRole.analyst]
    assert ROLE_CAPABILITIES[UserRole.analyst] < ROLE_CAPABILITIES[UserRole.administrator]


def test_owner_and_administrator_differ_only_on_the_commercial_and_console_edges() -> None:
    """The owner/administrator split is a closed set — assert it exhaustively.

    The point of enumerating both differences is not the specific membership but
    that it is *closed*: a capability landing on exactly one of the two roles
    must show up here or this test fails, so no edge can drift in silently. Two
    edges belong to the owner alone. ``billing:manage`` is commercial. The other
    is ``device_mirror:approve`` — consent-gated remote viewing of a customer's
    live screen, which an administrator may request but never approve, so an
    approved session always carries a second pair of eyes above the requester.
    """
    owner = ROLE_CAPABILITIES[UserRole.owner]
    admin = ROLE_CAPABILITIES[UserRole.administrator]
    assert owner - admin == {"billing:manage", "device_mirror:approve"}
    assert admin - owner == {
        "agent:configure",
        "api_keys:manage",
        "connectors:manage",
        "datasets:manage",
        "experiments:manage",
        "features:manage",
        "models:manage",
        "models:read",
        "pipeline:run",
        "webhooks:manage",
    }


def test_unknown_role_falls_back_to_read_only() -> None:
    """A role the model doesn't know must never be more powerful than a viewer."""
    ws = _workspace()
    user = _user(ws.id, UserRole.owner)
    user.role = type("R", (), {"value": "auditor"})()  # type: ignore[assignment]
    assert capabilities_for(user) == ROLE_CAPABILITIES[UserRole.viewer]


# --------------------------------------------------------------------------- #
# What the API actually allows
# --------------------------------------------------------------------------- #

OWNER_ALLOWED = [
    ("get", "/api/v1/members"),
    ("get", "/api/v1/change-requests"),
    ("get", "/api/v1/audit-log"),
    ("get", "/api/v1/roles"),
    ("get", "/api/v1/pipeline/runs"),
    ("get", "/api/v1/pipeline/drift"),
    ("get", "/api/v1/pipeline/schedule"),
    ("get", "/api/v1/billing/status"),
    ("post", "/api/v1/policies/simulate"),
]
OWNER_REFUSED = [
    ("post", "/api/v1/models/train", {"json": {"algorithm": "lightgbm", "feature_set": "v1"}}),
    ("get", "/api/v1/datasets"),
    ("get", "/api/v1/experiments"),
    ("get", "/api/v1/features"),
    ("get", "/api/v1/connectors"),
    ("get", "/api/v1/webhooks"),
    ("get", "/api/v1/api-keys"),
    ("post", "/api/v1/pipeline/runs/11111111-1111-1111-1111-111111111111/rerun"),
    ("post", "/api/v1/pipeline/jobs/drift_check/run"),
    ("post", "/api/v1/pipeline/queue/drain"),
]

ADMIN_ALLOWED = OWNER_REFUSED + [
    ("get", "/api/v1/models"),
    ("get", "/api/v1/models/11111111-1111-1111-1111-111111111111/importance"),
    ("get", "/api/v1/pipeline/runs"),
]
ADMIN_REFUSED = [
    ("post", "/api/v1/billing/subscription", {"json": {"plan_id": "growth"}}),
]

CLERK_ALLOWED = [
    ("get", "/api/v1/alerts"),
    ("get", "/api/v1/reports/templates"),
    ("get", "/api/v1/sources"),
    ("get", "/api/v1/forecasts"),
    ("get", "/api/v1/analytics/summary"),
    (
        "post",
        "/api/v1/ingest/manual",
        {"json": [{"location_id": "x", "date": "2026-01-01", "demand_qty": 1}]},
    ),
    ("post", "/api/v1/quality/issues/11111111-1111-1111-1111-111111111111/apply-fix"),
]
CLERK_REFUSED = [
    ("get", "/api/v1/members"),
    ("get", "/api/v1/audit-log"),
    ("get", "/api/v1/roles"),
    ("get", "/api/v1/pipeline/runs"),
    ("post", "/api/v1/models/train", {"json": {"algorithm": "lightgbm", "feature_set": "v1"}}),
    (
        "patch",
        "/api/v1/quality/rules/11111111-1111-1111-1111-111111111111",
        {"json": {"enabled": False}},
    ),
    (
        "patch",
        "/api/v1/alerts/rules/11111111-1111-1111-1111-111111111111",
        {"json": {"enabled": False}},
    ),
    ("post", "/api/v1/policies/simulate"),
    ("post", "/api/v1/reports/generate", {"json": {"template_id": "demand_summary"}}),
]

ANALYST_ALLOWED = [
    ("post", "/api/v1/policies/simulate"),
    ("post", "/api/v1/reports/generate", {"json": {"template_id": "demand_summary"}}),
    ("get", "/api/v1/models"),
    (
        "post",
        "/api/v1/ingest/manual",
        {"json": [{"location_id": "x", "date": "2026-01-01", "demand_qty": 1}]},
    ),
]
ANALYST_REFUSED = [
    ("get", "/api/v1/members"),
    ("get", "/api/v1/audit-log"),
    ("get", "/api/v1/roles"),
    ("get", "/api/v1/pipeline/runs"),
    ("post", "/api/v1/models/train", {"json": {"algorithm": "lightgbm", "feature_set": "v1"}}),
    ("get", "/api/v1/datasets"),
]

VIEWER_ALLOWED = [("get", "/api/v1/forecasts"), ("get", "/api/v1/analytics/summary")]
VIEWER_REFUSED = [
    ("get", "/api/v1/reports/templates"),
    (
        "post",
        "/api/v1/ingest/manual",
        {"json": [{"location_id": "x", "date": "2026-01-01", "demand_qty": 1}]},
    ),
    ("post", "/api/v1/sources/11111111-1111-1111-1111-111111111111/sync"),
    ("get", "/api/v1/members"),
]


def _run(role: UserRole, cases: list[tuple[Any, ...]], *, expect: bool) -> None:
    for case in cases:
        method, path, *rest = case
        kwargs = rest[0] if rest else {}
        status = _status_for(role, method, path, **kwargs)
        if expect:
            assert (
                status != 403
            ), f"{role.value} should be allowed {method.upper()} {path} (got {status})"
        else:
            assert (
                status == 403
            ), f"{role.value} should be refused {method.upper()} {path} (got {status})"


def test_owner_runs_the_business_without_the_modelling_console() -> None:
    _run(UserRole.owner, OWNER_ALLOWED, expect=True)
    _run(UserRole.owner, OWNER_REFUSED, expect=False)


def test_only_the_administrator_may_trigger_pipeline_work() -> None:
    """Watching pipeline health and spending compute on it are different powers."""
    run = "11111111-1111-1111-1111-111111111111"
    assert _allowed(UserRole.owner, "get", "/api/v1/pipeline/runs")
    assert not _allowed(UserRole.owner, "post", f"/api/v1/pipeline/runs/{run}/rerun")
    assert _allowed(UserRole.administrator, "post", f"/api/v1/pipeline/runs/{run}/rerun")
    # Running a job by name is the same power as re-running one.
    assert not _allowed(UserRole.owner, "post", "/api/v1/pipeline/jobs/drift_check/run")
    assert _allowed(UserRole.administrator, "post", "/api/v1/pipeline/jobs/drift_check/run")
    assert not _allowed(UserRole.owner, "post", "/api/v1/pipeline/queue/drain")
    assert _allowed(UserRole.administrator, "post", "/api/v1/pipeline/queue/drain")


def test_administrator_runs_the_console_but_not_the_subscription() -> None:
    _run(UserRole.administrator, ADMIN_ALLOWED, expect=True)
    _run(UserRole.administrator, ADMIN_REFUSED, expect=False)


def test_data_clerk_gets_data_in_and_clean_only() -> None:
    _run(UserRole.operator, CLERK_ALLOWED, expect=True)
    _run(UserRole.operator, CLERK_REFUSED, expect=False)


def test_analyst_adds_analysis_simulation_and_reports() -> None:
    _run(UserRole.analyst, ANALYST_ALLOWED, expect=True)
    _run(UserRole.analyst, ANALYST_REFUSED, expect=False)


def test_viewer_only_reads() -> None:
    _run(UserRole.viewer, VIEWER_ALLOWED, expect=True)
    _run(UserRole.viewer, VIEWER_REFUSED, expect=False)


# --------------------------------------------------------------------------- #
# The published contract
# --------------------------------------------------------------------------- #


def test_me_reports_the_role_bundle() -> None:
    ws = _workspace()
    user = _user(ws.id, UserRole.operator)
    # Columns the response model reads but the hand-built instance doesn't set.
    user.mfa_enabled = False  # type: ignore[assignment]
    user.profile_completed = True  # type: ignore[assignment]
    client = _client_for(user)
    # /auth/me looks the workspace up through db.get.
    client.app.dependency_overrides[get_db] = lambda: type(  # type: ignore[attr-defined]
        "DB", (), {"get": lambda self, model, pk: ws}
    )()
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200, resp.text
    perms = resp.json()["permissions"]
    assert perms == sorted(ROLE_CAPABILITIES[UserRole.operator])
    assert "ingest:write" in perms
    assert "models:manage" not in perms


def test_roles_endpoint_is_generated_from_the_model() -> None:
    ws = _workspace()
    client = _client_for(_user(ws.id, UserRole.administrator))
    resp = client.get("/api/v1/roles")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_role = {row["role"]: row for row in body["data"]}
    assert set(by_role) == {role.value for role in UserRole}
    assert by_role["operator"]["label"] == "Data Clerk"
    assert by_role["operator"]["permissions"] == sorted(ROLE_CAPABILITIES[UserRole.operator])
    assert by_role["owner"]["permissions"] == sorted(ROLE_CAPABILITIES[UserRole.owner])
    assert any(group["group"] == "Platform" for group in body["groups"])
