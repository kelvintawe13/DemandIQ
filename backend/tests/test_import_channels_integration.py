"""Real-Postgres round trip for import channels and the sync-history filter.

Two things a mocked session cannot show:

* that an import actually *creates* the channel row and stamps
  ``demand_observations.source_id`` with it — the mock answers every query, and
  ``channel_for_source`` deliberately ignores a non-``Connector`` answer; and
* that ``GET /sources/sync-history`` filters in the database rather than in the
  client. The mocked tests can only read the SQL, not its effect.

Skipped unless ``DEMANDIQ_RUN_INTEGRATION`` is set; run them with
``backend/scripts/ingest_e2e.sh`` or against the compose database.
"""

from __future__ import annotations

import datetime
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db, get_engine
from app.deps import get_current_user
from app.main import create_app
from app.models.location import Connector, DemandObservation, Location
from app.models.pipeline import PipelineRun
from app.models.user import User, UserRole
from app.models.workspace import Workspace

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DEMANDIQ_RUN_INTEGRATION"),
        reason="needs a migrated Postgres — run scripts/ingest_e2e.sh",
    ),
]


def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def _seed(prefix: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    with _session_factory()() as db:
        ws = Workspace(id=uuid.uuid4(), name=f"{prefix} Co", country="RW", timezone="Africa/Kigali")
        user = User(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            name=f"{prefix} User",
            email=f"{prefix.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            password_hash="x",
            role=UserRole.owner,
            email_verified=True,
        )
        loc = Location(id=uuid.uuid4(), workspace_id=ws.id, name="Kigali Central")
        db.add_all([ws, user, loc])
        db.commit()
        return ws.id, user.id, loc.id


def _client(user_id: uuid.UUID) -> TestClient:
    """Each request opens its own session, exactly like production."""
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

    def override_user() -> User | None:
        with _session_factory()() as db:
            return db.get(User, user_id)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return TestClient(app)


def _sources(client: TestClient) -> list[dict]:
    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


# --------------------------------------------------------------------------- #
# Manual entry
# --------------------------------------------------------------------------- #


def test_manual_entry_creates_and_reuses_one_channel() -> None:
    """The screen used to show nothing at all for hand-entered demand."""
    ws_id, user_id, loc_id = _seed("ManualChannel")
    client = _client(user_id)

    body = [{"location_id": str(loc_id), "date": "2026-02-01", "demand_qty": 42}]
    resp = client.post("/api/v1/ingest/manual", json=body)
    assert resp.status_code == 201, resp.text

    sources = _sources(client)
    assert len(sources) == 1, sources
    channel = sources[0]
    assert channel["name"] == "Manual entry"
    assert channel["type"] == "manual"
    assert channel["import_kind"] == "manual"
    # Not active: the sync job pulls *active* connectors from a provider
    # endpoint, and a hand-entry channel has no endpoint to pull.
    assert channel["status"] == "available"
    assert channel["volume"] == 1
    assert channel["last_sync_at"] is not None
    assert channel["last_pull"]["committed"] == 1

    # The row itself is attributed, so the channel's volume is not a guess.
    with _session_factory()() as db:
        obs = db.scalars(
            select(DemandObservation).where(DemandObservation.workspace_id == ws_id)
        ).one()
        assert obs.source_id == uuid.UUID(channel["id"])

    # A second entry reuses the channel instead of inventing a second one.
    resp = client.post(
        "/api/v1/ingest/manual",
        json=[{"location_id": str(loc_id), "date": "2026-02-02", "demand_qty": 30}],
    )
    assert resp.status_code == 201, resp.text
    sources = _sources(client)
    assert len(sources) == 1
    assert sources[0]["volume"] == 2
    assert sources[0]["last_pull"]["committed"] == 1  # this import created one row

    with _session_factory()() as db:
        channels = db.scalars(
            select(Connector).where(
                Connector.workspace_id == ws_id, Connector.name == "Manual entry"
            )
        ).all()
        assert len(channels) == 1


def test_correcting_a_day_does_not_grow_the_channel_volume() -> None:
    """An upsert updates the row it already had; volume counts rows, not writes."""
    ws_id, user_id, loc_id = _seed("ManualUpsert")
    client = _client(user_id)

    row = {"location_id": str(loc_id), "date": "2026-02-05", "demand_qty": 10}
    assert client.post("/api/v1/ingest/manual", json=[row]).status_code == 201
    assert client.post("/api/v1/ingest/manual", json=[{**row, "demand_qty": 18}]).status_code == 201

    sources = _sources(client)
    assert len(sources) == 1
    assert sources[0]["volume"] == 1
    # ... and the channel reports the correction as a correction.
    detail = sources[0]["last_pull"]["detail"]
    assert "corrected" in detail


# --------------------------------------------------------------------------- #
# Sync history
# --------------------------------------------------------------------------- #


def test_sync_history_reports_ingestion_runs_only() -> None:
    """A panel titled "Recent ingestion runs" must not answer with report jobs.

    The scheduler writes ``report_generation`` once a minute, so before the
    filter every visible row was a report render and the connector syncs
    underneath were unreachable from the screen.
    """
    ws_id, user_id, _ = _seed("SyncHistory")
    now = datetime.datetime.now(datetime.UTC)
    with _session_factory()() as db:
        for i in range(3):
            db.add(
                PipelineRun(
                    id=uuid.uuid4(),
                    workspace_id=ws_id,
                    job_type="report_generation",
                    status="success",
                    rows_processed=0,
                    detail="no queued reports",
                    started_at=now - datetime.timedelta(seconds=i),
                )
            )
        db.add(
            PipelineRun(
                id=uuid.uuid4(),
                workspace_id=ws_id,
                job_type="connector_sync",
                status="success",
                rows_processed=12,
                detail="pulled 12 rows",
                started_at=now - datetime.timedelta(minutes=5),
            )
        )
        db.commit()

    client = _client(user_id)
    resp = client.get("/api/v1/sources/sync-history?limit=20")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert [run["job_type"] for run in data] == ["connector_sync"]
    assert data[0]["rows_processed"] == 12
