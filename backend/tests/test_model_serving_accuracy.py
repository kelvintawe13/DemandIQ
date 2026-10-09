"""Live serving state + realised accuracy — the model console's live half.

Two new reads are covered here:

* ``GET /api/v1/models/serving`` — which export the API is actually serving and
  whether it agrees with the row marked production. Before this, the only way to
  find out a deployment had fallen back to rolling means was to issue a forecast
  and read its provenance.
* ``GET /api/v1/models/accuracy`` — the served forecast scored against demand
  that has since been recorded, overall and per location, with the window before
  it for drift and the model's held-out MAE as the reference.

The accuracy fixtures are deliberately small and exact: eight days at a location
that is off by 5 on 100, eight at one that is off by 10 on 10, so every figure
below can be checked by hand.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.forecast import Forecast, ModelVersion
from app.models.location import DemandObservation, Location
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.services.forecasting.bundle import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    bundle_details,
    clear_bundle_cache,
)

# The accuracy service windows against the real clock (``datetime.now(UTC)``), so
# the fixture's anchor day has to move with it. A literal date silently drifts
# out of the recent window and the 8+8 scenario begins losing days a few days
# after it is written — this file was authored against 2026-09-25 and was already
# scoring only 14 of the 16 days by 2026-09-28.
TODAY = datetime.datetime.now(datetime.UTC).date()
WINDOW_DAYS = 28
RECENT_START = TODAY - datetime.timedelta(days=WINDOW_DAYS)


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
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _location(ws_id: uuid.UUID, name: str) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name=name,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _forecast(
    ws_id: uuid.UUID,
    loc_id: uuid.UUID,
    target: datetime.date,
    point: float,
    *,
    actual_pad: float = 10.0,
    fallback: bool = False,
    coverage_target: float = 90.0,
) -> Forecast:
    """A published forecast, written the evening before the day it describes."""
    return Forecast(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        location_id=loc_id,
        model_version_id=uuid.uuid4(),
        target_date=target,
        point_forecast=point,
        lower_bound=point - actual_pad,
        upper_bound=point + actual_pad,
        coverage_target=coverage_target,
        feature_set_version="fs-2026.09.2",
        as_of_timestamp=datetime.datetime.combine(
            target - datetime.timedelta(days=1), datetime.time(18, tzinfo=datetime.UTC)
        ),
        fallback=fallback,
    )


def _observation(
    ws_id: uuid.UUID, loc_id: uuid.UUID, day: datetime.date, qty: int
) -> DemandObservation:
    return DemandObservation(workspace_id=ws_id, location_id=loc_id, date=day, demand_qty=qty)


def _production(ws_id: uuid.UUID, *, mae: float | None = 5.0) -> ModelVersion:
    return ModelVersion(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        algorithm="lightgbm",
        version="v4",
        status="production",
        mae=mae,
        feature_set_version="fs-2026.09.2",
        test_end=datetime.date(2026, 7, 28),
        promoted_at=datetime.datetime.now(datetime.UTC),
    )


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    """Answers the new reads by table name; nothing else is queried here."""

    def __init__(
        self,
        *,
        models: list[Any] = (),
        forecasts: list[Any] = (),
        observations: list[Any] = (),
        locations: list[Any] = (),
    ) -> None:
        self.models = list(models)
        self.forecasts = list(forecasts)
        self.observations = list(observations)
        self.locations = list(locations)

    def get(self, model: type, pk: object) -> object | None:
        if model is ModelVersion:
            return next((m for m in self.models if getattr(m, "id", None) == pk), None)
        return None

    def scalars(self, stmt: object) -> _Rows:  # type: ignore[no-untyped-def]
        text = str(stmt)
        if "model_versions" in text:
            return _Rows(self.models)
        if "forecasts" in text:
            return _Rows(self.forecasts)
        if "demand_observations" in text:
            return _Rows(self.observations)
        if "locations" in text:
            return _Rows(self.locations)
        return _Rows([])


def _client(db: _FakeSession, user: User) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


# ---------------------------------------------------------------------------
# Serving state
# ---------------------------------------------------------------------------


def test_serving_reports_stub_when_no_bundle_is_loaded() -> None:
    ws = _workspace()
    db = _FakeSession(models=[_production(ws.id)])
    client = _client(db, _user(ws.id))

    resp = client.get("/api/v1/models/serving")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["mode"] == "stub"
    assert body["agreement"] == "stub"
    assert body["bundle"] is None
    # The registry still names what production *should* be serving.
    assert body["production"]["version"] == "v4"
    assert "rolling-mean" in body["note"]
    # The boot-log line travels too, so the console and the server agree.
    assert "STUB forecasts" in body["summary"]
    # And the console can say *why*: nothing was configured and none was found.
    assert body["configuration"]["mode"] == "stub"
    assert body["configuration"]["configured"] is False


def test_serving_reports_how_the_export_was_chosen(bundle_path: Any) -> None:
    """`configuration` is the health endpoint's block, carried to the console.

    ``mode: bundle`` says an export is loaded; it does not say whether an operator
    asked for it via ``MODEL_BUNDLE_PATH`` or whether this deployment merely ships
    one. That is the distinction the startup assertion is about, and the console
    needs it to explain a process that lost its bundle *after* boot — a state the
    startup check never gets to see.
    """
    ws = _workspace()
    prod = _production(ws.id)
    prod.version = "lightgbm-it200"

    body = _client(_FakeSession(models=[prod]), _user(ws.id)).get("/api/v1/models/serving").json()

    assert body["configuration"]["mode"] == "trained"
    assert body["configuration"]["configured"] is True
    assert str(body["configuration"]["path"]).endswith("serving_bundle.joblib")
    assert body["configuration"]["reason"] is None


def _tiny_bundle(tmp_path: Any, version: str = "lightgbm-it200") -> Any:
    import joblib

    bundle = {
        "format": BUNDLE_FORMAT,
        "bundle_version": BUNDLE_VERSION,
        "created_at": "2026-09-20T00:00:00Z",
        "model": {
            "algorithm": "lightgbm",
            "version": version,
            "feature_set_version": "fs-2026.09.2",
        },
    }
    path = tmp_path / "serving_bundle.joblib"
    joblib.dump(bundle, path)
    return path


@pytest.fixture
def bundle_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """Point the loader at a real (tiny) export for the duration of a test."""
    from app.config import get_settings

    path = _tiny_bundle(tmp_path)
    monkeypatch.setattr(get_settings(), "model_bundle_path", str(path))
    clear_bundle_cache()
    yield path
    monkeypatch.setattr(get_settings(), "model_bundle_path", None)
    clear_bundle_cache()


def test_bundle_details_describes_the_loaded_export(bundle_path: Any) -> None:
    details = bundle_details()
    assert details is not None
    assert details["path"].endswith("serving_bundle.joblib")
    assert details["algorithm"] == "lightgbm"
    assert details["version"] == "lightgbm-it200"
    assert details["feature_set_version"] == "fs-2026.09.2"
    assert details["exported_at"] == "2026-09-20T00:00:00Z"
    # When the file was last changed is what the loader keys its cache on.
    assert details["changed_at"]


def test_bundle_details_is_none_without_an_export() -> None:
    assert bundle_details() is None


def test_serving_is_in_sync_when_the_export_is_the_production_row(bundle_path: Any) -> None:
    ws = _workspace()
    prod = _production(ws.id)
    prod.version = "lightgbm-it200"
    db = _FakeSession(models=[prod])

    body = _client(db, _user(ws.id)).get("/api/v1/models/serving").json()

    assert body["mode"] == "bundle"
    assert body["agreement"] == "in_sync"
    assert body["bundle"]["version"] == "lightgbm-it200"
    assert body["production"]["model_version_id"] == str(prod.id)
    assert "is the row marked production" in body["note"]


def test_serving_flags_a_mismatch_between_export_and_registry(bundle_path: Any) -> None:
    """The dangerous state: the registry says v4, the API serves something else."""
    ws = _workspace()
    db = _FakeSession(models=[_production(ws.id)])  # version "v4"

    body = _client(db, _user(ws.id)).get("/api/v1/models/serving").json()

    assert body["agreement"] == "version_mismatch"
    assert "lightgbm-it200" in body["note"] and "v4" in body["note"]


def test_serving_flags_a_bundle_with_no_production_row(bundle_path: Any) -> None:
    ws = _workspace()
    candidate = _production(ws.id)
    candidate.status = "candidate"
    db = _FakeSession(models=[candidate])

    body = _client(db, _user(ws.id)).get("/api/v1/models/serving").json()

    assert body["agreement"] == "bundle_unregistered"
    assert body["production"] is None


def test_serving_is_not_swallowed_by_the_model_id_route() -> None:
    """/serving must be declared before /{model_id}, or it answers 422."""
    ws = _workspace()
    resp = _client(_FakeSession(), _user(ws.id)).get("/api/v1/models/serving")
    assert resp.status_code == 200, resp.text


def test_serving_and_accuracy_need_model_read() -> None:
    ws = _workspace()
    viewer = _user(ws.id, role=UserRole.viewer)
    client = _client(_FakeSession(), viewer)
    assert client.get("/api/v1/models/serving").status_code == 403
    assert client.get("/api/v1/models/accuracy").status_code == 403


# ---------------------------------------------------------------------------
# Realised accuracy
# ---------------------------------------------------------------------------


def _accuracy_fixtures() -> tuple[_FakeSession, _FakeSession]:
    """The 8+8 day scenario described in the module docstring."""
    ws = _workspace()
    alpha = _location(ws.id, "Alpha")
    beta = _location(ws.id, "Beta")
    prod = _production(ws.id, mae=5.0)

    forecasts: list[Forecast] = []
    observations: list[DemandObservation] = []
    fallback_forecasts: list[Forecast] = []

    for offset in range(8):
        day = RECENT_START + datetime.timedelta(days=2 + offset)
        # Off by 5 on a demand of 100, inside the published range.
        forecasts.append(_forecast(ws.id, alpha.id, day, 105.0, actual_pad=10.0))
        observations.append(_observation(ws.id, alpha.id, day, 100))
        # Off by 10 on a demand of 10, outside the published range.
        forecasts.append(_forecast(ws.id, beta.id, day, 20.0, actual_pad=1.0))
        observations.append(_observation(ws.id, beta.id, day, 10))

    # The same shape in the window before, so drift is flat.
    for offset in range(8):
        day = RECENT_START - datetime.timedelta(days=8) + datetime.timedelta(days=offset)
        forecasts.append(_forecast(ws.id, alpha.id, day, 105.0, actual_pad=10.0))
        observations.append(_observation(ws.id, alpha.id, day, 100))
        forecasts.append(_forecast(ws.id, beta.id, day, 20.0, actual_pad=1.0))
        observations.append(_observation(ws.id, beta.id, day, 10))

    # One day the fallback answered: counted, never scored.
    fallback_day = RECENT_START + datetime.timedelta(days=12)
    fallback_forecasts.append(_forecast(ws.id, beta.id, fallback_day, 30.0, fallback=True))
    observations.append(_observation(ws.id, beta.id, fallback_day, 10))

    usable = _FakeSession(
        models=[prod],
        forecasts=forecasts + fallback_forecasts,
        observations=observations,
        locations=[alpha, beta],
    )
    empty = _FakeSession(models=[prod])
    return usable, empty


def test_accuracy_scores_the_served_forecast_against_realised_demand() -> None:
    db, _ = _accuracy_fixtures()
    ws = db.models[0].workspace_id
    body = _client(db, _user(ws)).get("/api/v1/models/accuracy").json()

    assert body["window_days"] == WINDOW_DAYS
    assert body["target_coverage"] == 90.0
    # The reference is the model's own held-out figure, not a invented target.
    assert body["reference"]["test_mae"] == 5.0
    assert body["reference"]["version"] == "v4"
    assert body["reference"]["test_end"] == "2026-07-28"

    overall = body["overall"]
    # 8 + 8 scored days; the fallback day is counted separately, never scored.
    assert overall["n"] == 16
    assert overall["fallback_days"] == 1
    assert overall["mae"] == pytest.approx(7.5)  # (5*8 + 10*8) / 16
    assert overall["wape_pct"] == pytest.approx(13.64)  # 120 / 880
    # Signed ``point - actual``: both sites over-forecast, so the bias is positive
    # and does not cancel itself out as MAE would.
    assert overall["bias"] == pytest.approx(7.5)  # (5*8 + 10*8) / 16
    assert overall["coverage_pct"] == pytest.approx(50.0)  # Alpha in range, Beta out
    assert overall["mean_actual"] == pytest.approx(55.0)

    # The window before it, so a slide can be read as a slide.
    assert body["prior"]["n"] == 16
    assert body["prior"]["wape_pct"] == pytest.approx(13.64)


def test_accuracy_names_what_is_wrong_with_severities() -> None:
    db, _ = _accuracy_fixtures()
    ws = db.models[0].workspace_id
    body = _client(db, _user(ws)).get("/api/v1/models/accuracy").json()

    assert body["status"] == "degraded"
    codes = {s["code"]: s for s in body["signals"]}
    # Live MAE 7.5 against the held-out 5.0 is exactly the degraded ratio.
    assert codes["live_vs_test"]["severity"] == "degraded"
    # Ranges held 50% of the time against a 90% target.
    assert codes["coverage"]["severity"] == "degraded"
    # The fallback day is named rather than quietly left in or out.
    assert codes["fallback"]["severity"] == "watch"
    assert "1 scored day" in codes["fallback"]["detail"]
    # A consistent over-forecast is named, not averaged away.
    assert codes["bias"]["severity"] == "watch"
    assert codes["bias"]["label"] == "Forecasts run over"
    assert "above a typical day" in codes["bias"]["detail"]
    # Flat window over window is not reported as drift.
    assert "window_drift" not in codes
    assert body["reason"] is None
    for signal in body["signals"]:
        assert signal["label"] and signal["detail"]


def test_accuracy_ranks_locations_worst_first() -> None:
    db, _ = _accuracy_fixtures()
    ws = db.models[0].workspace_id
    body = _client(db, _user(ws)).get("/api/v1/models/accuracy").json()

    assert [row["name"] for row in body["locations"]] == ["Beta", "Alpha"]
    beta, alpha = body["locations"]
    assert beta["mae"] == pytest.approx(10.0)
    assert beta["wape_pct"] == pytest.approx(100.0)
    assert beta["status"] == "degraded"
    assert alpha["mae"] == pytest.approx(5.0)
    assert alpha["wape_pct"] == pytest.approx(5.0)
    # Equal to the model's held-out error, and inside its range: no alarm.
    assert alpha["status"] == "healthy"
    assert alpha["coverage_pct"] == pytest.approx(100.0)
    assert beta["location_id"] == str(next(loc.id for loc in db.locations if loc.name == "Beta"))


def test_accuracy_is_unknown_rather_than_zero_when_nothing_can_be_scored() -> None:
    _, empty = _accuracy_fixtures()
    ws = empty.models[0].workspace_id
    body = _client(empty, _user(ws)).get("/api/v1/models/accuracy").json()

    # No fabricated zero: an unmeasurable window says so.
    assert body["status"] == "unknown"
    assert body["overall"] is None
    assert body["locations"] == []
    assert "No published forecast" in body["reason"]


def test_accuracy_ignores_a_forecast_written_after_its_own_day() -> None:
    """A row written on the target date records what happened, it does not predict."""
    ws = _workspace()
    loc = _location(ws.id, "Alpha")
    day = RECENT_START + datetime.timedelta(days=3)
    forecast = _forecast(ws.id, loc.id, day, 105.0)
    forecast.as_of_timestamp = datetime.datetime.combine(day, datetime.time(9, tzinfo=datetime.UTC))
    db = _FakeSession(
        models=[_production(ws.id)],
        forecasts=[forecast],
        observations=[_observation(ws.id, loc.id, day, 100)],
        locations=[loc],
    )

    body = _client(db, _user(ws.id)).get("/api/v1/models/accuracy").json()
    assert body["status"] == "unknown"
    assert body["overall"] is None


def test_accuracy_reports_a_fallback_only_window_without_scoring_it() -> None:
    ws = _workspace()
    loc = _location(ws.id, "Alpha")
    day = RECENT_START + datetime.timedelta(days=3)
    db = _FakeSession(
        models=[_production(ws.id)],
        forecasts=[_forecast(ws.id, loc.id, day, 30.0, fallback=True)],
        observations=[_observation(ws.id, loc.id, day, 12)],
        locations=[loc],
    )

    body = _client(db, _user(ws.id)).get("/api/v1/models/accuracy").json()
    assert body["overall"] is None
    codes = {s["code"] for s in body["signals"]}
    assert "fallback" in codes


def test_accuracy_honours_an_explicit_window() -> None:
    db, _ = _accuracy_fixtures()
    ws = db.models[0].workspace_id
    body = _client(db, _user(ws)).get("/api/v1/models/accuracy", params={"window_days": 90}).json()
    assert body["window_days"] == 90
    # An out-of-range window is a validation error, not a silent clamp.
    resp = _client(db, _user(ws)).get("/api/v1/models/accuracy", params={"window_days": 3})
    assert resp.status_code == 422


def test_model_detail_carries_its_importance_in_one_read() -> None:
    """The console's per-model panel needs one request, not three."""
    ws = _workspace()
    model = _production(ws.id)
    model.hyperparameters = {
        "n_estimators": 400,
        "feature_importance": {"lag_7": 0.6, "lag_1": 0.4},
    }
    db = _FakeSession(models=[model])

    detail = _client(db, _user(ws.id)).get(f"/api/v1/models/{model.id}").json()

    assert detail["importance_method"] == "recorded_by_training_run"
    assert [entry["feature"] for entry in detail["importance"]] == ["lag_7", "lag_1"]
    assert detail["importance"][0]["rank"] == 1
    # The standalone endpoint still answers the same thing.
    standalone = _client(db, _user(ws.id)).get(f"/api/v1/models/{model.id}/importance").json()
    assert standalone["importance"] == detail["importance"]
    assert standalone["method"] == detail["importance_method"]
