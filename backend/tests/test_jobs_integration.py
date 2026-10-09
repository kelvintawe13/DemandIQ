"""Real-Postgres integration tests for the background job suite.

Deliberately **not** part of the hermetic unit suite: these need a migrated
PostgreSQL database and are skipped unless ``DEMANDIQ_RUN_INTEGRATION`` is set.
Run them with ``make test-backend-integration`` (``backend/scripts/ingest_e2e.sh``),
which boots a disposable cluster, runs ``alembic upgrade head`` and exports the
variable.

Why they exist: the unit tests mock the session, so a job that queries the
wrong table, mis-orders rows, or writes a ``pipeline_runs`` row with a status
the console cannot read would still pass. Here every job runs against real
tables and the run rows it writes are read back in a fresh session.
"""

from __future__ import annotations

import datetime
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db, get_engine
from app.deps import get_current_user
from app.jobs.runner import JOBS, execute_queued, run_job
from app.main import create_app
from app.models.connector_mapping import ConnectorMapping
from app.models.forecast import Calibration, ModelVersion
from app.models.location import Connector, DemandObservation, Location
from app.models.pipeline import PipelineRun
from app.models.quality import Alert
from app.models.report import Report, ReportSchedule
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace
from app.services.drift import compute_drift
from app.services.ingestion import connector_pull

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DEMANDIQ_RUN_INTEGRATION"),
        reason="needs a migrated Postgres — run scripts/ingest_e2e.sh",
    ),
]

NOW = datetime.datetime.now(datetime.UTC)


def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def _seed(prefix: str) -> dict[str, uuid.UUID]:
    """Seed a workspace with 80 days of demand, a production model and a due report."""
    today = datetime.datetime.now(datetime.UTC).date()
    dates = [today - datetime.timedelta(days=i) for i in range(80, 0, -1)]

    with _session_factory()() as db:
        ws = Workspace(id=uuid.uuid4(), name=f"{prefix} Co", country="RW", timezone="Africa/Kigali")
        user = User(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            name=f"{prefix} Owner",
            email=f"{prefix.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            password_hash="x",
            role=UserRole.owner,
            email_verified=True,
        )
        loc = Location(id=uuid.uuid4(), workspace_id=ws.id, name="Kigali Central")
        conn = Connector(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            type="momo",
            name="MoMo till",
            status="active",
        )
        model = ModelVersion(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            algorithm="lightgbm",
            version=f"{prefix.lower()}-prod",
            status="production",
            feature_set_version="fs-2026.09.2",
            train_start=dates[0],
            train_end=dates[29],
            promoted_at=NOW,
            trained_at=NOW,
        )
        # Persist the parents first: the unit of work cannot order an insert
        # that has no relationship() to lean on, so ``model_versions`` must see
        # its workspace committed before it is written.
        db.add_all([ws, user, loc])
        db.commit()
        db.add_all([conn, model])
        db.commit()

        # 80 consecutive days. The last 14 are the live window and are shifted
        # well above the training distribution so drift is genuinely detected.
        observations = []
        for idx, day in enumerate(dates):
            live = idx >= len(dates) - 14
            observations.append(
                DemandObservation(
                    workspace_id=ws.id,
                    location_id=loc.id,
                    date=day,
                    demand_qty=(45 + (idx % 7)) if live else (20 + (idx % 7)),
                    source_id=conn.id,
                    notes="seed",
                )
            )
        db.add_all(observations)
        db.add(
            Calibration(
                id=uuid.uuid4(),
                model_version_id=model.id,
                method="adaptive_conformal",
                target_coverage=90.0,
                evaluated_at=NOW,
            )
        )
        db.add(
            ReportSchedule(
                id=uuid.uuid4(),
                workspace_id=ws.id,
                template="demand_summary",
                cadence="daily",
                recipients=["ops@example.com"],
                format="csv",
                enabled=True,
                next_run_at=NOW - datetime.timedelta(hours=1),
            )
        )
        db.commit()
        return {"workspace": ws.id, "location": loc.id, "model": model.id, "connector": conn.id}


def _run(job_type: str, workspace_id: uuid.UUID) -> PipelineRun:
    with _session_factory()() as db:
        run = run_job(db, workspace_id=workspace_id, job_type=job_type)
        db.commit()
        db.refresh(run)
        return run


def test_every_job_runs_and_records_a_pipeline_run() -> None:
    ids = _seed("Jobs")
    ws_id = ids["workspace"]

    for job_type in JOBS:
        run = _run(job_type, ws_id)
        assert run.status == "success", f"{job_type}: {run.detail}"
        assert run.duration_ms is not None

    # Every execution left exactly one row, all successful.
    with _session_factory()() as db:
        runs = db.scalars(select(PipelineRun).where(PipelineRun.workspace_id == ws_id)).all()
        assert len(runs) == len(JOBS)
        assert {r.job_type for r in runs} == set(JOBS)
        assert all(r.status == "success" for r in runs)

    # feature_materialisation counts the whole demand history.
    with _session_factory()() as db:
        fm = db.scalar(
            select(PipelineRun).where(
                PipelineRun.workspace_id == ws_id, PipelineRun.job_type == "feature_materialisation"
            )
        )
        assert fm is not None and fm.rows_processed == 80

    # forecast_publish wrote tomorrow's forecast for the seeded location.
    with _session_factory()() as db:
        fp = db.scalar(
            select(PipelineRun).where(
                PipelineRun.workspace_id == ws_id, PipelineRun.job_type == "forecast_publish"
            )
        )
        assert fp is not None and (fp.rows_processed or 0) >= 1

    # full_retrain queued a new training version.
    with _session_factory()() as db:
        training = db.scalars(
            select(ModelVersion).where(
                ModelVersion.workspace_id == ws_id, ModelVersion.status == "training"
            )
        ).all()
        assert len(training) == 1

    # report_delivery generated the due report.
    with _session_factory()() as db:
        reports = db.scalars(select(Report).where(Report.workspace_id == ws_id)).all()
        assert len(reports) >= 1


def test_drift_check_detects_the_shift_and_raises_one_alert() -> None:
    ids = _seed("Drift")
    ws_id = ids["workspace"]

    with _session_factory()() as db:
        result = compute_drift(db, ws_id)
    assert result["overall"] in ("watch", "drifting")
    assert len(result["signals"]) == 1
    assert result["signals"][0]["drift_score"] > 0.1

    run = _run("drift_check", ws_id)
    assert run.status == "success"
    assert (run.rows_processed or 0) >= 1

    with _session_factory()() as db:
        alerts = db.scalars(
            select(Alert).where(Alert.workspace_id == ws_id, Alert.resolved_at.is_(None))
        ).all()
        assert len(alerts) == 1
        assert alerts[0].severity in ("critical", "warning")

    # A second pass must not duplicate the alert.
    _run("drift_check", ws_id)
    with _session_factory()() as db:
        alerts = db.scalars(
            select(Alert).where(Alert.workspace_id == ws_id, Alert.resolved_at.is_(None))
        ).all()
        assert len(alerts) == 1


def test_failed_job_alerts_subscribers_and_success_clears_it(monkeypatch) -> None:
    """A failed run must reach the console *and* the alert.raised webhook.

    The run row alone is only visible to someone already looking at the
    pipeline page; the webhook is what pages an operator.
    """
    ids = _seed("Failing")
    ws_id = ids["workspace"]

    with _session_factory()() as db:
        db.add(
            WebhookEndpoint(
                id=uuid.uuid4(),
                workspace_id=ws_id,
                url="https://example.com/hook",
                events=["alert.raised", "alert.resolved"],
                secret="s3cret",
                status="active",
                created_at=datetime.datetime.now(datetime.UTC),
            )
        )
        db.commit()

    def boom(db: Session, workspace_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("connector refused the connection")

    monkeypatch.setitem(JOBS, "connector_sync", boom)

    run = _run("connector_sync", ws_id)
    assert run.status == "failed"
    assert "connector refused the connection" in (run.detail or "")

    with _session_factory()() as db:
        alerts = db.scalars(
            select(Alert).where(Alert.workspace_id == ws_id, Alert.resolved_at.is_(None))
        ).all()
        assert len(alerts) == 1
        assert alerts[0].title == "Pipeline job failed: connector_sync"
        assert "connector refused the connection" in (alerts[0].body or "")

        raised = db.scalars(
            select(WebhookDelivery).where(WebhookDelivery.event == "alert.raised")
        ).all()
        assert len(raised) == 1
        assert raised[0].payload["data"]["run_id"] == str(run.id)

    # A later success resolves it, so the console does not keep a stale alert.
    monkeypatch.undo()
    ok = _run("connector_sync", ws_id)
    assert ok.status == "success"
    with _session_factory()() as db:
        assert (
            db.scalars(
                select(Alert).where(Alert.workspace_id == ws_id, Alert.resolved_at.is_(None))
            ).all()
            == []
        )
        resolved = db.scalars(
            select(WebhookDelivery).where(WebhookDelivery.event == "alert.resolved")
        ).all()
        assert len(resolved) == 1


def test_queued_run_is_drained_by_execute_queued() -> None:
    """POST /sources/{id}/sync queues a run; the worker side must complete it."""
    from app.jobs.runner import enqueue_run, execute_queued

    ids = _seed("Queue")
    ws_id = ids["workspace"]

    with _session_factory()() as db:
        queued = enqueue_run(
            db, workspace_id=ws_id, job_type="connector_sync", detail="manual sync"
        )
        db.commit()
        assert queued.status == "queued"

        assert execute_queued(db, workspace_id=ws_id) == 1
        db.commit()
        db.refresh(queued)
        assert queued.status == "success"
        assert queued.duration_ms is not None

    with _session_factory()() as db:
        conn = db.get(Connector, ids["connector"])
        # The seeded connector has no pull endpoint, so the drained run reports
        # that and leaves freshness alone rather than claiming a completed sync.
        assert conn is not None and conn.last_sync_at is None
        assert "no pull endpoint" in (queued.detail or "")


def test_recover_stale_runs_reaps_an_abandoned_running_row() -> None:
    from app.jobs.runner import recover_stale_runs

    ids = _seed("Stale")
    ws_id = ids["workspace"]
    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=3)

    with _session_factory()() as db:
        orphan = PipelineRun(
            id=uuid.uuid4(),
            workspace_id=ws_id,
            job_type="forecast_publish",
            status="running",
            detail="forecast_publish started",
            started_at=stale_at,
        )
        db.add(orphan)
        db.commit()

        assert recover_stale_runs(db, workspace_id=ws_id) == 1
        db.commit()
        db.refresh(orphan)
        assert orphan.status == "failed"
        assert "abandoned" in (orphan.detail or "")

        # It stays failed: a second pass has nothing left to reap.
        assert recover_stale_runs(db, workspace_id=ws_id) == 0


# --------------------------------------------------------------------------- #
# The sync a POS / MoMo screen queues, end to end
# --------------------------------------------------------------------------- #


def _http_client(user_id: uuid.UUID) -> TestClient:
    """App in which each request opens its own real session, like production."""
    app = create_app()

    def override_db():  # type: ignore[no-untyped-def]
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

    app.dependency_overrides[get_db] = override_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = override_user  # type: ignore[assignment]
    return TestClient(app)


def _seed_sync_targets(prefix: str, *, role: UserRole = UserRole.owner) -> dict[str, uuid.UUID]:
    """A workspace with one ``active`` connector and one that is only ``available``."""
    with _session_factory()() as db:
        ws = Workspace(id=uuid.uuid4(), name=f"{prefix} Co", country="RW", timezone="Africa/Kigali")
        user = User(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            name=f"{prefix} Owner",
            email=f"{prefix.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            password_hash="x",
            role=role,
            email_verified=True,
        )
        loc = Location(id=uuid.uuid4(), workspace_id=ws.id, name="Kigali Central")
        db.add_all([ws, user, loc])
        db.commit()
        active = Connector(
            id=uuid.uuid4(), workspace_id=ws.id, type="pos", name="Active till", status="active"
        )
        idle = Connector(
            id=uuid.uuid4(), workspace_id=ws.id, type="momo", name="Idle till", status="available"
        )
        db.add_all([active, idle])
        db.commit()
        return {
            "workspace": ws.id,
            "user": user.id,
            "location": loc.id,
            "active": active.id,
            "idle": idle.id,
        }


def _observation_count(ws_id: uuid.UUID) -> int:
    with _session_factory()() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(DemandObservation)
                .where(DemandObservation.workspace_id == ws_id)
            )
            or 0
        )


def _configure_pull(
    connector_id: uuid.UUID,
    location_id: uuid.UUID,
    *,
    endpoint: str = "https://pos.example/demand",
) -> None:
    """Give a connector a provider endpoint and one linked location."""
    with _session_factory()() as db:
        conn = db.get(Connector, connector_id)
        assert conn is not None
        conn.credentials = {"endpoint": endpoint, "location_ids": [str(location_id)]}
        db.commit()


def _fetch_stub(rows: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    """Stand in for the provider HTTP call with a fixed payload."""

    def _fetch(endpoint, headers, *, since=None, http_client=None):  # type: ignore[no-untyped-def]
        return {"rows": rows}

    return _fetch


def _sync_and_drain(client: TestClient, connector_id: uuid.UUID, ws_id: uuid.UUID) -> PipelineRun:
    """POST a sync, drain it, and return the settled run row."""
    resp = client.post(f"/api/v1/sources/{connector_id}/sync")
    assert resp.status_code == 201, resp.text
    job_id = uuid.UUID(resp.json()["job_id"])
    with _session_factory()() as db:
        assert execute_queued(db, workspace_id=ws_id) == 1
        db.commit()
        run = db.get(PipelineRun, job_id)
        assert run is not None and run.status == "success", run.detail if run else "no run"
        return run


def test_sync_end_to_end_pulls_commits_and_stamps_freshness(monkeypatch) -> None:
    """`POST /sources/{id}/sync` -> queue -> worker -> committed rows + freshness.

    The whole chain the POS / MoMo screen drives, against real tables and a
    request-scoped session: the endpoint the UI calls, the pull the worker runs,
    the ``demand_observations`` rows it commits, and the freshness stamp it sets
    only once the pull is done.

    Queueing must not write or stamp anything: the sources list would otherwise
    report a pull before it has happened. The settled run must leave rows
    attributed to the connector (``source_id``) so a later sync can tell what
    arrived.
    """
    ids = _seed_sync_targets("Sync E2E")
    _configure_pull(ids["active"], ids["location"])
    today = datetime.datetime.now(datetime.UTC).date()
    rows = [
        {"date": (today - datetime.timedelta(days=1)).isoformat(), "demand_qty": 51},
        {"date": (today - datetime.timedelta(days=2)).isoformat(), "demand_qty": 47},
    ]
    monkeypatch.setattr(connector_pull, "_fetch_payload", _fetch_stub(rows))

    client = _http_client(ids["user"])
    resp = client.post(f"/api/v1/sources/{ids['active']}/sync")
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "queued"
    job_id = uuid.UUID(resp.json()["job_id"])

    # Queueing is not syncing: no rows, no freshness stamp yet.
    with _session_factory()() as db:
        queued = db.get(Connector, ids["active"])
        assert queued is not None and queued.last_sync_at is None
        run = db.get(PipelineRun, job_id)
        assert run is not None and run.status == "queued"
    assert _observation_count(ids["workspace"]) == 0

    # The worker side of the same queue performs the pull and commits the rows.
    with _session_factory()() as db:
        assert execute_queued(db, workspace_id=ids["workspace"]) == 1
        db.commit()

    with _session_factory()() as db:
        run = db.get(PipelineRun, job_id)
        assert run is not None
        assert run.status == "success", run.detail
        assert run.duration_ms is not None
        assert run.rows_processed == 2
        assert db.get(Connector, ids["active"]).last_sync_at is not None
        # The `available` connector is not `active`, so the job never selects it
        # and the endpoint refuses a sync for it — it stays untouched.
        idle = db.get(Connector, ids["idle"])
        assert idle is not None and idle.last_sync_at is None

        committed = db.scalars(
            select(DemandObservation).where(
                DemandObservation.workspace_id == ids["workspace"],
                DemandObservation.source_id == ids["active"],
            )
        ).all()
        assert {o.date for o in committed} == {
            today - datetime.timedelta(days=1),
            today - datetime.timedelta(days=2),
        }
        # The channel now carries its own outcome, not just a timestamp.
        conn = db.get(Connector, ids["active"])
        assert conn is not None
        assert (conn.last_pull_pulled, conn.last_pull_committed, conn.last_pull_skipped) == (
            2,
            2,
            0,
        )
        assert "committed 2" in (conn.last_pull_detail or "")

    # And the freshness the operator sees now reports the completed pull, with
    # the channel's own outcome attached.
    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["data"] if r["id"] == str(ids["active"]))
    idle_row = next(r for r in resp.json()["data"] if r["id"] == str(ids["idle"]))
    assert row["last_sync_at"] is not None
    assert row["freshness"] != "never"
    assert row["last_pull"]["committed"] == 2
    assert row["last_pull"]["pulled"] == 2
    assert idle_row["last_sync_at"] is None
    assert idle_row["freshness"] == "never"
    assert idle_row["last_pull"]["committed"] is None


def test_sync_pulls_provider_rows_through_the_mapping(monkeypatch) -> None:
    """A pull maps the provider's own field names and skips what it cannot commit.

    The provider speaks ``txn_date``/``covers``/``outlet``; the connector's
    mapping translates them. A row the mapping cannot complete is *skipped* and
    named in the run detail, never guessed at. Re-running the same sync
    reconciles on ``(location_id, date)`` instead of duplicating the day.
    """
    ids = _seed_sync_targets("Sync Mapping")
    _configure_pull(ids["active"], ids["location"])
    with _session_factory()() as db:
        db.add_all(
            [
                ConnectorMapping(
                    id=uuid.uuid4(),
                    connector_id=ids["active"],
                    source_field="txn_date",
                    canonical_field="date",
                ),
                ConnectorMapping(
                    id=uuid.uuid4(),
                    connector_id=ids["active"],
                    source_field="covers",
                    canonical_field="demand_qty",
                    transform="int",
                ),
                ConnectorMapping(
                    id=uuid.uuid4(),
                    connector_id=ids["active"],
                    source_field="outlet",
                    canonical_field="location_id",
                ),
            ]
        )
        db.commit()

    today = datetime.datetime.now(datetime.UTC).date()
    day = today - datetime.timedelta(days=1)
    rows = [
        {
            "txn_date": day.isoformat(),
            "covers": "62",
            "outlet": str(ids["location"]),
            "reason": "dinner",
        },
        {
            "txn_date": (today - datetime.timedelta(days=2)).isoformat(),
            "outlet": str(ids["location"]),
        },
    ]
    monkeypatch.setattr(connector_pull, "_fetch_payload", _fetch_stub(rows))

    client = _http_client(ids["user"])
    run = _sync_and_drain(client, ids["active"], ids["workspace"])
    # One row mapped and committed; the one missing `covers` is skipped, not guessed.
    assert run.rows_processed == 1
    assert "committed 1" in (run.detail or "")
    assert "skipped 1" in (run.detail or "")

    with _session_factory()() as db:
        obs = db.scalars(
            select(DemandObservation).where(
                DemandObservation.workspace_id == ids["workspace"],
                DemandObservation.source_id == ids["active"],
            )
        ).all()
        assert len(obs) == 1
        assert obs[0].date == day
        assert obs[0].demand_qty == 62

    # A second identical pull reconciles the same day rather than duplicating it.
    again = _sync_and_drain(client, ids["active"], ids["workspace"])
    assert again.rows_processed == 1
    assert _observation_count(ids["workspace"]) == 1


def test_sync_without_a_pull_endpoint_ingests_nothing_and_stamps_nothing() -> None:
    """An active channel with no provider endpoint is reported honestly.

    The run still succeeds — there is nothing to fail — but it writes no demand
    and leaves ``last_sync_at`` alone, because no pull happened. The detail says
    why, so a ``success`` row cannot be read as an ingestion.
    """
    ids = _seed_sync_targets("Sync Bare")
    client = _http_client(ids["user"])

    run = _sync_and_drain(client, ids["active"], ids["workspace"])
    assert run.rows_processed == 0
    assert "no pull endpoint" in (run.detail or "")
    assert _observation_count(ids["workspace"]) == 0
    with _session_factory()() as db:
        conn = db.get(Connector, ids["active"])
        assert conn is not None and conn.last_sync_at is None

    # The non-active channel is refused outright, so no dead run is queued.
    resp = client.post(f"/api/v1/sources/{ids['idle']}/sync")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "not_active"


# --------------------------------------------------------------------------- #
# The on-demand pull an operator runs from the Connectors page
# --------------------------------------------------------------------------- #


def test_manual_pull_commits_and_records_the_outcome(monkeypatch) -> None:
    """`POST /connectors/{id}/pull` pulls one channel and records what happened."""
    ids = _seed_sync_targets("Pull Now", role=UserRole.administrator)
    _configure_pull(ids["active"], ids["location"])
    today = datetime.datetime.now(datetime.UTC).date()
    rows = [
        {"date": (today - datetime.timedelta(days=1)).isoformat(), "demand_qty": 33},
        {"date": (today - datetime.timedelta(days=2)).isoformat(), "demand_qty": 29},
    ]
    monkeypatch.setattr(connector_pull, "_fetch_payload", _fetch_stub(rows))
    client = _http_client(ids["user"])

    resp = client.post(f"/api/v1/connectors/{ids['active']}/pull")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is False
    assert body["status"] == "pulled"
    assert (body["pulled"], body["committed"], body["skipped"]) == (2, 2, 0)
    assert {r["date"] for r in body["rows"]} == {
        (today - datetime.timedelta(days=1)).isoformat(),
        (today - datetime.timedelta(days=2)).isoformat(),
    }

    with _session_factory()() as db:
        conn = db.get(Connector, ids["active"])
        assert conn is not None
        assert conn.last_sync_at is not None
        assert (conn.last_pull_pulled, conn.last_pull_committed, conn.last_pull_skipped) == (
            2,
            2,
            0,
        )
    assert _observation_count(ids["workspace"]) == 2

    # The pull also leaves a run row, so it is visible in the run views and the
    # Sources history rather than only on the channel.
    assert body["run_id"]
    with _session_factory()() as db:
        run = db.get(PipelineRun, uuid.UUID(body["run_id"]))
        assert run is not None and run.status == "success"
        assert run.rows_processed == 2
        assert "manual pull" in (run.detail or "")
    history = client.get("/api/v1/sources/sync-history").json()["data"]
    assert any(r["id"] == body["run_id"] for r in history)

    # And the channel's own outcome surfaces on the Sources list.
    row = next(
        r for r in client.get("/api/v1/sources").json()["data"] if r["id"] == str(ids["active"])
    )
    assert row["last_pull"]["committed"] == 2


def test_manual_pull_dry_run_previews_without_writing(monkeypatch) -> None:
    """A dry run returns the mapped rows and touches neither table nor stamp."""
    ids = _seed_sync_targets("Pull Dry", role=UserRole.administrator)
    _configure_pull(ids["active"], ids["location"])
    today = datetime.datetime.now(datetime.UTC).date()
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _fetch_stub([{"date": (today - datetime.timedelta(days=1)).isoformat(), "demand_qty": 44}]),
    )
    client = _http_client(ids["user"])

    resp = client.post(f"/api/v1/connectors/{ids['active']}/pull", params={"dry_run": "true"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    assert body["status"] == "preview"
    assert body["committed"] == 0
    assert body["run_id"] is None
    assert len(body["rows"]) == 1
    assert body["rows"][0]["demand_qty"] == 44

    assert _observation_count(ids["workspace"]) == 0
    with _session_factory()() as db:
        conn = db.get(Connector, ids["active"])
        assert conn is not None
        assert conn.last_sync_at is None
        assert conn.last_pull_committed is None
        # A preview is not a run: no pipeline_runs row is written either.
        assert (
            db.scalars(
                select(PipelineRun).where(PipelineRun.workspace_id == ids["workspace"])
            ).all()
            == []
        )


def test_manual_pull_dry_runs_a_non_active_channel_but_a_real_pull_refuses_it(monkeypatch) -> None:
    """Previewing an unactivated channel is allowed; ingesting from it is not."""
    ids = _seed_sync_targets("Pull Idle", role=UserRole.administrator)
    _configure_pull(ids["idle"], ids["location"])
    today = datetime.datetime.now(datetime.UTC).date()
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _fetch_stub([{"date": (today - datetime.timedelta(days=1)).isoformat(), "demand_qty": 5}]),
    )
    client = _http_client(ids["user"])

    resp = client.post(f"/api/v1/connectors/{ids['idle']}/pull")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "not_active"

    resp = client.post(f"/api/v1/connectors/{ids['idle']}/pull", params={"dry_run": "true"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] == 0
    assert _observation_count(ids["workspace"]) == 0


def test_manual_pull_refuses_a_channel_without_an_endpoint() -> None:
    """No endpoint is a 409, not a pull that silently returns nothing."""
    ids = _seed_sync_targets("Pull Bare", role=UserRole.administrator)
    client = _http_client(ids["user"])

    resp = client.post(f"/api/v1/connectors/{ids['active']}/pull")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "not_configured"


def test_manual_pull_reports_a_provider_failure_as_502(monkeypatch) -> None:
    """A transport failure is a 502 with nothing written."""
    ids = _seed_sync_targets("Pull Down", role=UserRole.administrator)
    _configure_pull(ids["active"], ids["location"])

    def _boom(endpoint, headers, *, since=None, http_client=None):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("connection refused", request=httpx.Request("GET", endpoint))

    monkeypatch.setattr(connector_pull, "_fetch_payload", _boom)
    client = _http_client(ids["user"])

    resp = client.post(f"/api/v1/connectors/{ids['active']}/pull")
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"]["code"] == "provider_unavailable"
    assert _observation_count(ids["workspace"]) == 0
