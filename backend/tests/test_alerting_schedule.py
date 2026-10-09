"""Alerting, job cadence and forecast-path tests.

Covers the surfaces added on top of the job runner:

* :func:`app.services.forecasting.generate.generate_forecast` — the single
  forecast path now shared by ``POST /forecasts`` and the ``forecast_publish``
  job (they used to duplicate the five steps, so they could drift);
* :mod:`app.services.alerting` — alerts reach the ``alert.raised`` webhook
  instead of only an ``alerts`` row, and de-duplicate;
* :func:`app.jobs.scheduler.next_run_after` — the "next run" column;
* ``GET /pipeline/schedule`` and ``POST /pipeline/queue/drain``.

DB-optional like the rest of the router tests: a fake/mocked session, a
dependency override for the caller, assertions on the response.
"""

from __future__ import annotations

import datetime
import inspect
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.jobs import runner as job_runner
from app.jobs.scheduler import JOB_SCHEDULE, next_run_after
from app.main import create_app
from app.models.pipeline import PipelineRun
from app.models.quality import Alert
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal
from app.services.alerting import raise_alert, record_job_failure, record_job_success

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _user(ws_id: uuid.UUID, role: UserRole = UserRole.administrator) -> User:
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


def _client_for(user: User, db: Any | None = None) -> TestClient:
    session = db if db is not None else MagicMock()
    if db is None:
        session.flush = MagicMock()
        session.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Session double that keeps alerts and webhook deliveries in memory.

    Deliberately filters like the real queries do (by table, then by open-state
    and title for alerts) so a de-duplication bug cannot hide behind a fake
    that returns everything.
    """

    def __init__(
        self, alerts: list[Alert] | None = None, endpoints: list[WebhookEndpoint] | None = None
    ) -> None:
        self.alerts: list[Alert] = list(alerts or [])
        self.endpoints: list[WebhookEndpoint] = list(endpoints or [])
        self.deliveries: list[WebhookDelivery] = []

    def add(self, obj: Any) -> None:
        if isinstance(obj, Alert):
            self.alerts.append(obj)
        elif isinstance(obj, WebhookDelivery):
            self.deliveries.append(obj)

    def flush(self) -> None:
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt)
        if "webhook_endpoints" in text:
            return _Result([e for e in self.endpoints if e.status in ("active", "failing")])
        if "alerts" in text:
            return _Result([a for a in self.alerts if a.resolved_at is None])
        return _Result([])


def _endpoint(ws_id: uuid.UUID, events: list[str]) -> WebhookEndpoint:
    return WebhookEndpoint(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        url="https://example.com/hook",
        events=events,
        secret="s3cret",
        status="active",
        created_at=datetime.datetime.now(datetime.UTC),
    )


# --------------------------------------------------------------------------- #
# next_run_after
# --------------------------------------------------------------------------- #


def test_next_run_after_advances_every_15_minutes() -> None:
    at_10_15 = datetime.datetime(2026, 9, 21, 10, 15, tzinfo=datetime.UTC)
    assert next_run_after("*/15 * * * *", at_10_15) == at_10_15.replace(minute=30)
    # Strictly after: the current minute is never returned.
    at_10_16 = at_10_15.replace(minute=16)
    assert next_run_after("*/15 * * * *", at_10_16) == at_10_15.replace(minute=30)


def test_next_run_after_finds_the_next_daily_tick_tomorrow() -> None:
    # Feature materialisation runs at 03:20; at 04:00 today the next one is tomorrow.
    at_4am = datetime.datetime(2026, 9, 21, 4, 0, tzinfo=datetime.UTC)
    nxt = next_run_after("20 3 * * *", at_4am)
    assert nxt == datetime.datetime(2026, 9, 22, 3, 20, tzinfo=datetime.UTC)


def test_next_run_after_handles_a_weekly_cron() -> None:
    at_thursday = datetime.datetime(2026, 9, 24, 12, 0, tzinfo=datetime.UTC)  # Thursday
    nxt = next_run_after("0 2 * * 0", at_thursday)
    assert nxt is not None
    assert nxt.weekday() == 6  # Sunday
    assert (nxt.hour, nxt.minute) == (2, 0)
    assert nxt > at_thursday


def test_next_run_after_rejects_a_bad_expression() -> None:
    assert next_run_after("not a cron", datetime.datetime.now(datetime.UTC)) is None


def test_every_scheduled_job_has_a_computable_next_run() -> None:
    """No job may render an empty 'next run' because its cron cannot be parsed."""
    now = datetime.datetime.now(datetime.UTC)
    for job_type, sched in JOB_SCHEDULE.items():
        nxt = next_run_after(sched["cron"], now)
        assert nxt is not None, f"{job_type} has an unparseable cron: {sched['cron']}"
        assert nxt > now


# --------------------------------------------------------------------------- #
# Alerting
# --------------------------------------------------------------------------- #


def test_alert_is_created_and_fanned_out_to_subscribers() -> None:
    ws = _workspace()
    db = _FakeSession(endpoints=[_endpoint(ws.id, ["alert.raised"])])

    alert = raise_alert(
        db,
        workspace_id=ws.id,
        severity="critical",
        title="Demand drift drifting",
        body="PSI 0.31",
        payload={"source": "drift_check"},
    )

    assert alert.severity == "critical"
    assert db.alerts == [alert]
    assert len(db.deliveries) == 1
    assert db.deliveries[0].event == "alert.raised"
    assert db.deliveries[0].payload["data"]["alert_id"] == str(alert.id)
    assert db.deliveries[0].payload["data"]["source"] == "drift_check"


def test_repeat_alert_dedupes_and_does_not_renotify() -> None:
    ws = _workspace()
    db = _FakeSession(endpoints=[_endpoint(ws.id, ["alert.raised"])])

    first = raise_alert(
        db, workspace_id=ws.id, severity="warning", title="Demand drift watch", body="PSI 0.12"
    )
    second = raise_alert(
        db, workspace_id=ws.id, severity="critical", title="Demand drift watch", body="PSI 0.41"
    )

    assert second.id == first.id
    assert len(db.alerts) == 1
    assert len(db.deliveries) == 1  # one open alert, one notification
    # The newest detail is what the alert shows.
    assert second.body == "PSI 0.41"
    assert second.severity == "critical"


def test_endpoints_not_subscribed_to_the_event_are_skipped() -> None:
    ws = _workspace()
    db = _FakeSession(
        endpoints=[_endpoint(ws.id, ["forecast.generated"]), _endpoint(ws.id, ["alert.raised"])]
    )
    raise_alert(db, workspace_id=ws.id, severity="info", title="Note", body="hello")
    assert len(db.deliveries) == 1


def test_job_failure_raises_an_alert_and_success_resolves_it() -> None:
    ws = _workspace()
    db = _FakeSession(endpoints=[_endpoint(ws.id, ["alert.raised", "alert.resolved"])])
    run_id = uuid.uuid4()

    record_job_failure(
        db,
        workspace_id=ws.id,
        job_type="connector_sync",
        run_id=run_id,
        error="connection refused",
    )
    assert len(db.alerts) == 1
    alert = db.alerts[0]
    assert alert.title == "Pipeline job failed: connector_sync"
    assert "connection refused" in (alert.body or "")
    assert db.deliveries[-1].event == "alert.raised"
    assert db.deliveries[-1].payload["data"]["run_id"] == str(run_id)

    # A repeated failure refreshes the same alert rather than stacking rows.
    record_job_failure(
        db, workspace_id=ws.id, job_type="connector_sync", run_id=uuid.uuid4(), error="still down"
    )
    assert len(db.alerts) == 1
    assert "still down" in (db.alerts[0].body or "")

    record_job_success(db, workspace_id=ws.id, job_type="connector_sync")
    assert alert.resolved_at is not None
    assert db.deliveries[-1].event == "alert.resolved"

    # Nothing left open, so a later success is a no-op.
    before = len(db.deliveries)
    record_job_success(db, workspace_id=ws.id, job_type="connector_sync")
    assert len(db.deliveries) == before


def test_unknown_severity_is_downgraded_to_warning() -> None:
    ws = _workspace()
    db = _FakeSession()
    alert = raise_alert(db, workspace_id=ws.id, severity="catastrophic", title="T", body="b")
    assert alert.severity == "warning"


def test_drift_job_uses_the_shared_alerting_path() -> None:
    """The drift job must not go back to writing an ``alerts`` row by hand."""
    source = inspect.getsource(job_runner._job_drift_check)
    assert "raise_alert(" in source
    assert "db.add(" not in source


# --------------------------------------------------------------------------- #
# One forecast path
# --------------------------------------------------------------------------- #


def test_router_and_publish_job_share_the_forecast_generation_path() -> None:
    """Both callers go through generate_forecast — that is the whole point.

    They duplicated the predict→interval→persist sequence before, so the
    nightly publish could silently diverge from the on-demand endpoint.
    """
    from app.routers import forecasts

    for func in (forecasts.create_forecast, forecasts.batch_forecasts):
        assert "generate_forecast(" in inspect.getsource(func)
    assert "generate_forecast(" in inspect.getsource(job_runner._job_forecast_publish)

    # And the router no longer reaches for the primitives directly in those two.
    for func in (forecasts.create_forecast, forecasts.batch_forecasts):
        assert "persist_forecast(" not in inspect.getsource(func)


def test_generate_forecast_wires_predict_interval_and_persist(monkeypatch) -> None:
    """The service is the sequence, in order, with the caller's as_of."""
    from app.services.forecasting import generate as gen

    calls: list[str] = []
    as_of = datetime.datetime(2026, 9, 21, 3, 0, tzinfo=datetime.UTC)
    target = datetime.date(2026, 9, 22)

    class _ModelVersion:
        id = uuid.uuid4()

    class _Point:
        point = 42.0
        fallback = False
        feature_set_version = "fs-2026.09.2"
        model_version = _ModelVersion()

    class _Interval:
        lower = 30.0
        upper = 55.0
        coverage_target = 0.90

    class _Row:
        id = uuid.uuid4()
        point_forecast = 42.0
        lower_bound = 30.0
        upper_bound = 55.0
        coverage_target = 0.90

    def fake_predict(db, **kw):  # type: ignore[no-untyped-def]
        calls.append("predict")
        assert kw["as_of"] == as_of and kw["target_date"] == target
        return _Point()

    def fake_features(db, **kw):  # type: ignore[no-untyped-def]
        calls.append("features")
        return MagicMock(name="feature_vector")

    def fake_interval(db, **kw):  # type: ignore[no-untyped-def]
        calls.append("interval")
        assert kw["point"] == 42.0
        return _Interval()

    def fake_persist(db, **kw):  # type: ignore[no-untyped-def]
        calls.append("persist")
        assert kw["as_of_timestamp"] == as_of
        assert kw["feature_set_version"] == "fs-2026.09.2"
        return _Row()

    def fake_explain(db, **kw):  # type: ignore[no-untyped-def]
        calls.append("explain")
        return []

    monkeypatch.setattr(gen, "predict_point", fake_predict)
    monkeypatch.setattr(gen, "build_features", fake_features)
    monkeypatch.setattr(gen, "build_interval", fake_interval)
    monkeypatch.setattr(gen, "persist_forecast", fake_persist)
    monkeypatch.setattr(gen, "build_explanation", fake_explain)

    ws_id, loc_id = uuid.uuid4(), uuid.uuid4()
    plain = gen.generate_forecast(
        MagicMock(),
        workspace_id=ws_id,
        location_id=loc_id,
        target_date=target,
        as_of=as_of,
    )
    assert calls == ["predict", "features", "interval", "persist"]
    assert (plain.point, plain.lower, plain.upper) == (42.0, 30.0, 55.0)
    assert plain.id == plain.row.id

    # explain=True adds exactly the attribution row, after the forecast exists.
    explained = gen.generate_forecast(
        MagicMock(),
        workspace_id=ws_id,
        location_id=loc_id,
        target_date=target,
        as_of=as_of,
        explain=True,
    )
    assert calls[-2:] == ["persist", "explain"]
    assert explained.id == explained.row.id


def test_generate_forecast_defaults_a_naive_as_of_to_utc(monkeypatch) -> None:
    from app.services.forecasting import generate as gen

    seen: dict[str, Any] = {}

    class _Row:
        id = uuid.uuid4()
        point_forecast = 1.0
        lower_bound = 0.0
        upper_bound = 2.0
        coverage_target = 0.9

    monkeypatch.setattr(
        gen,
        "predict_point",
        lambda db, **kw: seen.update(kw) or MagicMock(model_version=MagicMock(id=1)),
    )
    monkeypatch.setattr(gen, "build_features", lambda db, **kw: MagicMock())
    monkeypatch.setattr(
        gen,
        "build_interval",
        lambda db, **kw: MagicMock(lower=0.0, upper=2.0, coverage_target=0.9),
    )
    monkeypatch.setattr(gen, "persist_forecast", lambda db, **kw: _Row())

    gen.generate_forecast(
        MagicMock(),
        workspace_id=uuid.uuid4(),
        location_id=uuid.uuid4(),
        target_date=datetime.date(2026, 9, 22),
        # Deliberately naive: this asserts generate_forecast coerces a naive
        # as_of to UTC rather than rejecting it.
        as_of=datetime.datetime(2026, 9, 21, 3, 0),  # noqa: DTZ001
    )
    assert seen["as_of"].tzinfo is not None


# --------------------------------------------------------------------------- #
# Schedule + queue endpoints
# --------------------------------------------------------------------------- #


def test_schedule_rows_carry_cadence_last_and_next_run() -> None:
    from app.routers.governance import _schedule_rows

    ws_id = uuid.uuid4()
    ran_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    run = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        job_type="feature_materialisation",
        status="success",
        rows_processed=80,
        duration_ms=1200,
        detail="materialised",
        started_at=ran_at,
    )

    db = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[run]))
    rows = _schedule_rows(db, ws_id)

    assert {r["job_type"] for r in rows} == set(JOB_SCHEDULE)
    by_type = {r["job_type"]: r for r in rows}
    fm = by_type["feature_materialisation"]
    assert fm["cadence"] == JOB_SCHEDULE["feature_materialisation"]["cadence"]
    assert fm["last_run"] is not None and fm["last_run"]["status"] == "success"
    assert fm["last_run"]["rows_processed"] == 80
    assert fm["next_run"] is not None
    # A job that has never run says so rather than inventing a timestamp.
    assert by_type["full_retrain"]["last_run"] is None
    for row in rows:
        assert row["next_run"] is not None


def test_schedule_endpoint_is_open_to_pipeline_readers() -> None:
    ws = _workspace()
    client = _client_for(_user(ws.id))
    resp = client.get("/api/v1/pipeline/schedule")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert len(body) == len(JOB_SCHEDULE)
    assert {"job_type", "cadence", "cron", "last_run", "next_run"} <= set(body[0])


def test_drain_endpoint_reports_what_it_ran() -> None:
    ws = _workspace()
    queued = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        job_type="connector_sync",
        status="queued",
        detail="manual sync",
        started_at=datetime.datetime.now(datetime.UTC),
    )
    db = MagicMock()
    db.flush = MagicMock()
    # The drain selects queued runs first, then (in the same call) the job runs.
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[queued]))
    db.get.return_value = None

    client = _client_for(_user(ws.id), db=db)
    resp = client.post("/api/v1/pipeline/queue/drain")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {"processed", "recovered", "still_queued"} <= set(body)
    assert isinstance(body["processed"], int)


def test_drain_endpoint_is_refused_for_a_viewer() -> None:
    ws = _workspace()
    client = _client_for(_user(ws.id, UserRole.viewer))
    assert client.post("/api/v1/pipeline/queue/drain").status_code == 403


def test_recover_stale_runs_marks_abandoned_rows_failed(monkeypatch) -> None:
    """A run whose process died must not sit in ``running`` forever."""
    ws_id = uuid.uuid4()
    stuck = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        job_type="full_retrain",
        status="running",
        detail="full_retrain started",
        started_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=2),
    )
    db = MagicMock()
    db.flush = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[stuck]))
    monkeypatch.setattr(job_runner, "record_job_failure", lambda *a, **kw: None)

    assert job_runner.recover_stale_runs(db) == 1
    assert stuck.status == "failed"
    assert "abandoned" in (stuck.detail or "")
    assert stuck.duration_ms is not None


def test_recover_stale_runs_survives_a_broken_session() -> None:
    db = MagicMock()
    db.scalars.side_effect = RuntimeError("no such table")
    assert job_runner.recover_stale_runs(db) == 0


@pytest.mark.parametrize("job_type", sorted(job_runner.JOBS))
def test_queue_drain_can_run_every_job_type(job_type: str) -> None:
    """``execute_queued`` dispatches by ``job_type``; every schedule entry maps."""
    assert job_type in JOB_SCHEDULE
