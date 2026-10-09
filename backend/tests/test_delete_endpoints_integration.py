"""Real-Postgres integration tests for every ``DELETE`` route.

Deliberately **not** part of the hermetic unit suite: these need a migrated
PostgreSQL database and are skipped unless ``DEMANDIQ_RUN_INTEGRATION`` is set.
Run them with ``make test-backend-integration``.

Why they exist: fifteen delete routes were verified by hand against a running
server, and a hand-run check is worth nothing the second time. More importantly,
these routes do **not** all mean the same thing by "delete", and the difference
is invisible to a status code:

* ``menu_groups``, ``webhooks``, ``connectors``, ``alert_rules`` and report
  schedules remove the row;
* ``locations``, ``api_keys``, ``sessions``, ``agent_sessions`` and device
  grants **keep** it and stamp a column, because the row is the record that the
  thing existed — an archived location still has forecasts against it, and a
  revoked key is the evidence a key was issued.

A caller cannot tell those apart from the response, so each test asserts the
state actually left in the database, and the two classes are distinguished by
their documented repeat behaviour: 404 for a removed row, and idempotent success
for a stamped one.

One more asymmetry is pinned here rather than left to be rediscovered: the
``owner`` seat is **not** a superset of ``administrator``. ``_OWNER`` subtracts
the integration console (connectors, webhooks, API keys) and the modelling
capabilities, so an owner is refused on those routes on purpose. The tests below
authenticate as whichever seat the route is actually for.
"""

from __future__ import annotations

import io
import os
import uuid

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db, get_engine
from app.deps import get_current_user
from app.main import create_app
from app.models.api_key import ApiKey
from app.models.device import Device, DeviceGrant
from app.models.location import Connector, DemandObservation, IngestUpload, Location, MenuGroup
from app.models.quality import AlertRule
from app.models.report import ReportSchedule
from app.models.user import Session as UserSession
from app.models.user import User, UserIdentity, UserRole
from app.models.webhook import WebhookEndpoint
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DEMANDIQ_RUN_INTEGRATION"),
        reason="needs a migrated Postgres — run scripts/ingest_e2e.sh",
    ),
]


def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


class Seeded:
    """A workspace with one seat of each role, plus a location and a device."""

    def __init__(self, prefix: str) -> None:
        stamp = uuid.uuid4().hex[:10]
        with _session_factory()() as db:
            ws = Workspace(
                id=uuid.uuid4(), name=f"{prefix} Co {stamp}", country="RW", timezone="Africa/Kigali"
            )
            seats = {
                role: User(
                    id=uuid.uuid4(),
                    workspace_id=ws.id,
                    name=f"{prefix} {role.value}",
                    email=f"{prefix.lower()}-{role.value}-{stamp}@example.com",
                    password_hash="x",
                    role=role,
                    email_verified=True,
                )
                for role in (UserRole.owner, UserRole.administrator, UserRole.viewer)
            }
            loc = Location(id=uuid.uuid4(), workspace_id=ws.id, name=f"{prefix} Central {stamp}")
            device = Device(
                id=uuid.uuid4(),
                workspace_id=ws.id,
                location_id=loc.id,
                label=f"{prefix} till {stamp}",
                device_type="pos",
                platform="android",
                credential_prefix=f"probe-{stamp}",
                credential_hash="x",
                scopes=[],
                status="active",
                enrolled_by=seats[UserRole.owner].id,
            )
            # Flushed in dependency order rather than all at once: ``devices``
            # has no ORM relationship to ``workspaces`` (only the FK columns),
            # so SQLAlchemy is free to insert it first and hit the foreign key.
            # The workspace first, then everything that references it, then the
            # device (which references the location and the owner as well).
            db.add(ws)
            db.flush()
            db.add_all([loc, *seats.values()])
            db.flush()
            db.add(device)
            db.commit()

            self.workspace_id = ws.id
            self.location_id = loc.id
            self.device_id = device.id
            self.owner_id = seats[UserRole.owner].id
            self.admin_id = seats[UserRole.administrator].id
            self.viewer_id = seats[UserRole.viewer].id

    def user(self, role: UserRole) -> uuid.UUID:
        return {
            UserRole.owner: self.owner_id,
            UserRole.administrator: self.admin_id,
            UserRole.viewer: self.viewer_id,
        }[role]


def _client(user_id: uuid.UUID) -> TestClient:
    """App in which every request opens its own session, like production."""
    app = create_app()

    def override_db():
        db = _session_factory()()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def override_user(db: Session = Depends(get_db)) -> User | None:
        # Loaded from the *request's* session, as production does. Returning a
        # user fetched from a throwaway session hands the route a detached
        # object: ``current_user.mfa_enabled = False`` would then mutate nothing
        # and persist nothing, and the test would be asserting against a
        # harness artefact rather than the route.
        return db.get(User, user_id)

    # ``get_principal`` resolves the caller itself (it also accepts an API key)
    # rather than chaining off ``get_current_user``, so routes using it — the
    # location and report deletes — need this override as well.
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_principal] = override_user
    return TestClient(app)


def _row(model: type, pk: uuid.UUID):
    with _session_factory()() as db:
        return db.get(model, pk)


def _scalar(query: str, **params):
    with _session_factory()() as db:
        return db.execute(text(query), params).scalar()


# --------------------------------------------------------------------------- #
# Rows that are removed
# --------------------------------------------------------------------------- #


def test_delete_menu_group_removes_the_row() -> None:
    seed = Seeded("MenuGroup")
    client = _client(seed.owner_id)

    created = client.post("/api/v1/menu-groups", json={"name": "Lunch", "perishability": "high"})
    assert created.status_code == 201, created.text
    group_id = uuid.UUID(created.json()["id"])

    resp = client.delete(f"/api/v1/menu-groups/{group_id}")
    assert resp.status_code == 204, resp.text
    assert _row(MenuGroup, group_id) is None
    # Removed, so a repeat is a genuine absence rather than a repeat success.
    assert client.delete(f"/api/v1/menu-groups/{group_id}").status_code == 404


def test_delete_webhook_removes_the_row() -> None:
    seed = Seeded("Webhook")
    client = _client(seed.admin_id)  # WEBHOOKS_MANAGE is administrator-only

    created = client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/probe", "events": ["forecast.generated"]},
    )
    assert created.status_code == 201, created.text
    hook_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/webhooks/{hook_id}").status_code == 204
    assert _row(WebhookEndpoint, hook_id) is None
    assert client.delete(f"/api/v1/webhooks/{hook_id}").status_code == 404


def test_delete_connector_removes_the_row() -> None:
    seed = Seeded("Connector")
    client = _client(seed.admin_id)  # CONNECTORS_MANAGE is administrator-only

    created = client.post("/api/v1/connectors", json={"type": "api", "name": "Probe"})
    assert created.status_code == 201, created.text
    connector_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/connectors/{connector_id}").status_code == 204
    assert _row(Connector, connector_id) is None
    assert client.delete(f"/api/v1/connectors/{connector_id}").status_code == 404


def test_delete_alert_rule_removes_the_row() -> None:
    seed = Seeded("AlertRule")
    client = _client(seed.owner_id)  # ALERTS_CONFIGURE is held by owner and admin

    created = client.post("/api/v1/alerts/rules", json={"name": "Probe rule"})
    assert created.status_code == 201, created.text
    rule_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/alerts/rules/{rule_id}").status_code == 204
    assert _row(AlertRule, rule_id) is None
    assert client.delete(f"/api/v1/alerts/rules/{rule_id}").status_code == 404


def test_delete_report_schedule_removes_the_row() -> None:
    seed = Seeded("Schedule")
    client = _client(seed.owner_id)

    created = client.post(
        "/api/v1/reports/schedules",
        json={"template": "demand_summary", "cadence": "weekly", "recipients": ["a@example.com"]},
    )
    assert created.status_code == 201, created.text
    schedule_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/reports/schedules/{schedule_id}").status_code == 204
    assert _row(ReportSchedule, schedule_id) is None
    assert client.delete(f"/api/v1/reports/schedules/{schedule_id}").status_code == 404


def test_delete_demand_observation_removes_the_row() -> None:
    seed = Seeded("Demand")
    client = _client(seed.owner_id)

    created = client.post(
        "/api/v1/ingest/manual",
        json=[{"location_id": str(seed.location_id), "date": "2026-01-10", "demand_qty": 42}],
    )
    assert created.status_code == 201, created.text
    listed = client.get("/api/v1/ingest/demand")
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert len(rows) == 1, rows
    observation_id = rows[0]["id"]

    assert client.delete(f"/api/v1/ingest/demand/{observation_id}").status_code == 204
    assert _row(DemandObservation, observation_id) is None
    assert client.delete(f"/api/v1/ingest/demand/{observation_id}").status_code == 404


def test_delete_uncommitted_upload_removes_the_row() -> None:
    seed = Seeded("Upload")
    client = _client(seed.owner_id)

    created = client.post(
        "/api/v1/ingest/file",
        files={
            "file": (
                "probe.csv",
                io.BytesIO(b"date,demand_qty\n2026-01-10,40\n"),
                "text/csv",
            )
        },
    )
    assert created.status_code == 201, created.text
    upload_id = uuid.UUID(created.json()["upload_id"])

    assert client.delete(f"/api/v1/ingest/uploads/{upload_id}").status_code == 204
    assert _row(IngestUpload, upload_id) is None
    assert client.delete(f"/api/v1/ingest/uploads/{upload_id}").status_code == 404


# --------------------------------------------------------------------------- #
# Rows that survive, stamped
# --------------------------------------------------------------------------- #


def test_delete_location_archives_rather_than_removes() -> None:
    """The row stays: forecasts and access logs still point at it."""
    seed = Seeded("Archive")
    client = _client(seed.owner_id)

    created = client.post("/api/v1/locations", json={"name": "Probe site"})
    assert created.status_code == 201, created.text
    location_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/locations/{location_id}").status_code == 204
    archived = _row(Location, location_id)
    assert archived is not None
    assert archived.archived_at is not None
    # An archived location is excluded from the delete path, so this is a 404
    # even though the row is present — deliberate, and worth pinning.
    assert client.delete(f"/api/v1/locations/{location_id}").status_code == 404


def test_revoke_api_key_keeps_the_row_as_evidence() -> None:
    seed = Seeded("ApiKey")
    client = _client(seed.admin_id)  # API_KEYS_MANAGE is administrator-only

    created = client.post("/api/v1/api-keys", json={"label": "probe", "scopes": ["read"]})
    assert created.status_code == 201, created.text
    key_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/api-keys/{key_id}").status_code == 204
    key = _row(ApiKey, key_id)
    assert key is not None and key.revoked_at is not None
    # Revoking a revoked key is not an error: the outcome asked for already holds.
    assert client.delete(f"/api/v1/api-keys/{key_id}").status_code == 204


def test_revoke_session_keeps_the_row_with_a_revoked_at() -> None:
    seed = Seeded("Session")
    client = _client(seed.owner_id)

    with _session_factory()() as db:
        session = UserSession(id=uuid.uuid4(), user_id=seed.owner_id, refresh_hash="x")
        db.add(session)
        db.commit()
        session_id = session.id

    assert client.delete(f"/api/v1/me/sessions/{session_id}").status_code == 204
    stored = _row(UserSession, session_id)
    assert stored is not None and stored.revoked_at is not None
    assert client.delete(f"/api/v1/me/sessions/{session_id}").status_code == 204


def test_end_agent_session_stamps_ended_at() -> None:
    seed = Seeded("Agent")
    client = _client(seed.owner_id)

    # Inserted directly: the create route spends a daily assistant question, and
    # this test is about the delete half. ``agent_sessions`` has no ORM model.
    session_id = uuid.uuid4()
    with _session_factory()() as db:
        db.execute(
            text(
                "INSERT INTO agent_sessions (id, workspace_id, user_id, started_at, last_seen_at,"
                " turn_count) VALUES (:id, :ws, :user, now(), now(), 0)"
            ),
            {"id": str(session_id), "ws": str(seed.workspace_id), "user": str(seed.owner_id)},
        )
        db.commit()

    assert client.delete(f"/api/v1/agent/sessions/{session_id}").status_code == 204
    ended = _scalar("SELECT ended_at FROM agent_sessions WHERE id = :id", id=str(session_id))
    assert ended is not None
    assert client.delete(f"/api/v1/agent/sessions/{session_id}").status_code == 204


def test_revoke_device_grant_keeps_the_row_and_returns_its_scopes() -> None:
    seed = Seeded("Grant")
    client = _client(seed.owner_id)

    created = client.post(
        f"/api/v1/device-grants/devices/{seed.device_id}/grants",
        json={"capability": "access:read", "reason": "integration probe"},
    )
    assert created.status_code == 201, created.text
    grant_id = uuid.UUID(created.json()["data"]["grant"]["id"])

    resp = client.delete(f"/api/v1/device-grants/devices/{seed.device_id}/grants/{grant_id}")
    # Unlike the others this answers with a body: the caller who pressed the
    # button needs the device's effective scopes in the same round trip.
    assert resp.status_code == 200, resp.text
    assert "effective_scopes" in resp.json()["data"]

    grant = _row(DeviceGrant, grant_id)
    assert grant is not None and grant.revoked_at is not None
    assert (
        client.delete(
            f"/api/v1/device-grants/devices/{seed.device_id}/grants/{grant_id}"
        ).status_code
        == 200
    )


def test_delete_member_deactivates_rather_than_deletes() -> None:
    """Revoking access is a soft act, and it leaves a trail.

    The invited row survives with ``deactivated_at`` set, and the workspace's
    audit log gains the entry that says who did it. Both matter: a deleted user
    row would take its audit trail with it through the foreign key.
    """
    seed = Seeded("Members")
    client = _client(seed.owner_id)

    created = client.post(
        "/api/v1/members/invite",
        json={"email": f"revoke-{uuid.uuid4().hex[:8]}@example.com", "role": "viewer"},
    )
    assert created.status_code == 201, created.text
    member_id = uuid.UUID(created.json()["id"])

    assert client.delete(f"/api/v1/members/{member_id}").status_code == 204
    member = _row(User, member_id)
    assert member is not None and member.deactivated_at is not None
    assert (
        _scalar(
            "SELECT count(*) FROM audit_log WHERE actor_id = :actor AND action = 'member.revoke'",
            actor=str(seed.owner_id),
        )
        >= 1
    )
    assert client.delete(f"/api/v1/members/{member_id}").status_code == 204


def test_unlink_identity_removes_only_that_identity() -> None:
    seed = Seeded("Identity")
    client = _client(seed.owner_id)

    with _session_factory()() as db:
        identity = UserIdentity(
            id=uuid.uuid4(), user_id=seed.owner_id, provider="google", provider_uid=uuid.uuid4().hex
        )
        db.add(identity)
        db.commit()
        identity_id = identity.id

    assert client.delete("/api/v1/me/identities/google").status_code == 204
    assert _row(UserIdentity, identity_id) is None
    # The seat still has its password, so this is absence rather than a refusal.
    assert client.delete("/api/v1/me/identities/google").status_code == 404


def test_disable_mfa_is_allowed_for_a_owner_and_refused_for_an_administrator() -> None:
    """The one delete route whose answer depends on the caller's seat.

    An administrator holds more capabilities than an owner, and may not turn
    their own second factor off; the owner may. Pinned because "administrator
    can do everything" is the intuition this contradicts.
    """
    seed = Seeded("Mfa")
    with _session_factory()() as db:
        owner = db.get(User, seed.owner_id)
        assert owner is not None
        owner.mfa_enabled = True
        db.commit()

    assert _client(seed.owner_id).delete("/api/v1/me/mfa").status_code == 204
    with _session_factory()() as db:
        owner = db.get(User, seed.owner_id)
        assert owner is not None and owner.mfa_enabled is False

    assert _client(seed.admin_id).delete("/api/v1/me/mfa").status_code == 403


# --------------------------------------------------------------------------- #
# Who may delete at all
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/locations/{id}",
        "/api/v1/menu-groups/{id}",
        "/api/v1/webhooks/{id}",
        "/api/v1/alerts/rules/{id}",
        "/api/v1/api-keys/{id}",
        "/api/v1/members/{id}",
        "/api/v1/connectors/{id}",
    ],
)
def test_a_viewer_cannot_delete_anything(path: str) -> None:
    """A viewer is refused before the lookup, so the answer is 403 not 404.

    A 404 here would be a smaller answer that leaks less, but the permission is
    a property of the route rather than of the id, and the tests below the real
    ones rely on that ordering: an unauthorised caller must not be able to probe
    which ids exist.
    """
    seed = Seeded("Viewer")
    client = _client(seed.viewer_id)
    missing = uuid.uuid4()
    assert client.delete(path.format(id=missing)).status_code == 403
