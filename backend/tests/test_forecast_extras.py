"""Tests for the forecast-scoped explanation/policy/drift endpoints.

These three routes were previously dead code: they referenced an undefined
``forecast_router``, called the SQLAlchemy 1.x ``Session.exec`` (removed in 2.x),
and one of them served hardcoded numbers as simulation results. The tests below
pin the properties that make them honest — real inputs in, no fabricated
figures out, workspace-scoped, and the drift index computed from the data.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import create_app
from app.models.forecast import (
    ATTRIBUTION_BOOSTER,
    ATTRIBUTION_STUB,
    Forecast,
    ForecastExplanation,
    ModelVersion,
)
from app.models.location import DemandObservation
from app.models.user import User, UserRole
from app.routers import forecasts as router_mod
from app.routers.forecasts import (
    _population_stability_index,
    forecast_drift_monitoring,
    forecast_explanation_summary,
    forecast_policy_simulation,
)
from app.security.api_key_auth import get_principal
from app.services.policy.simulate import PolicyResult

WS_ID = uuid.uuid4()
LOC_ID = uuid.uuid4()
USER = User(
    id=uuid.uuid4(),
    workspace_id=WS_ID,
    name="Admin",
    email="admin@example.com",
    password_hash="$2b$12$dummy",
    role=UserRole.administrator,
    email_verified=True,
)


def _forecast(
    *, workspace_id: uuid.UUID = WS_ID, model_version_id: uuid.UUID | None = None
) -> Forecast:
    target = datetime.date(2026, 10, 11)
    return Forecast(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        location_id=LOC_ID,
        model_version_id=model_version_id or uuid.uuid4(),
        target_date=target,
        point_forecast=40.0,
        lower_bound=25.0,
        upper_bound=55.0,
        coverage_target=0.90,
        feature_set_version="fs-2026.09.2",
        as_of_timestamp=datetime.datetime(2026, 10, 10, 3, 0, tzinfo=datetime.UTC),
        fallback=False,
        created_at=datetime.datetime(2026, 10, 10, 3, 0, tzinfo=datetime.UTC),
    )


def _scalars_result(rows: list[object]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _fake_db(forecast: Forecast, *, explanation_rows=None, for_model_version=None, drift=None):
    """A minimal session double: ``get`` by model, ``scalars`` in call order."""
    db = MagicMock()

    def _get(model, pk):  # type: ignore[no-untyped-def]
        if model is Forecast:
            return forecast
        if model is ModelVersion:
            return for_model_version
        return None

    db.get.side_effect = _get
    if drift is not None:
        recent, baseline = drift
        db.scalars.side_effect = [_scalars_result(recent), _scalars_result(baseline)]
    elif explanation_rows is not None:
        db.scalars.side_effect = [_scalars_result(explanation_rows)]
    else:
        db.scalars.side_effect = [_scalars_result([])]
    return db


def _explanation(
    forecast_id: uuid.UUID,
    feature: str,
    impact: float,
    source: str = ATTRIBUTION_BOOSTER,
) -> ForecastExplanation:
    return ForecastExplanation(
        forecast_id=forecast_id,
        feature_name=feature,
        feature_value="1",
        shap_impact=Decimal(str(impact)),
        attribution_source=source,
    )


# --------------------------------------------------------------- routes exist


def test_the_three_routes_are_registered() -> None:
    """They must be real routes, not dead code (the previous failure mode)."""
    paths = {route.path for route in router_mod.router.routes}
    assert "/api/v1/forecasts/{forecast_id}/explanation/summary" in paths
    assert "/api/v1/forecasts/{forecast_id}/policy/simulation" in paths
    assert "/api/v1/forecasts/{forecast_id}/drift/monitoring" in paths


# ------------------------------------------------------- explanation summary


def test_explanation_summary_reports_real_drivers() -> None:
    forecast = _forecast()
    rows = [
        _explanation(forecast.id, "roll_mean_7", 12.5),
        _explanation(forecast.id, "dow_mean_8w", -4.0),
        _explanation(forecast.id, "is_holiday", -1.5),
        _explanation(forecast.id, "_residual", 2.0),
    ]
    body = forecast_explanation_summary(
        forecast.id, db=_fake_db(forecast, explanation_rows=rows), current_user=USER
    )

    assert body["attribution_type"] == "predictive"
    assert "not causal" in body["disclaimer"]
    # Which path wrote the rows travels with them: the numbers alone cannot say
    # whether a model produced them.
    assert body["attribution_source"] == ATTRIBUTION_BOOSTER
    # Only model features count as drivers — the residual row does not.
    assert body["total_drivers"] == 3
    assert body["residual_visitors"] == 2.0
    # Impacts are visitor units, and the total is the sum of the drivers.
    assert body["total_impact_visitors"] == 7.0
    assert len(body["positive_factors"]) == 1
    assert len(body["negative_factors"]) == 2
    # Plain-language label from the model's own feature set.
    assert "7-day average demand" in body["positive_factors"][0]
    assert body["summary"].startswith("This forecast is led by")
    # The old response mislabelled visitor units as log1p impacts.
    assert "total_log1p_impact" not in body


def test_explanation_summary_dominant_is_largest_magnitude() -> None:
    """A big negative driver must win — the buggy version preferred positives."""
    forecast = _forecast()
    rows = [
        _explanation(forecast.id, "roll_std_28", -30.0),
        _explanation(forecast.id, "lag_1", 3.0),
    ]
    body = forecast_explanation_summary(
        forecast.id, db=_fake_db(forecast, explanation_rows=rows), current_user=USER
    )
    assert "28-day demand volatility" in body["dominant_factor"]
    assert "-30.0" in body["dominant_factor"]


def test_explanation_summary_reports_a_placeholder_attribution() -> None:
    """A stub-written explanation says so, and a mixed set is not flattened."""
    forecast = _forecast()
    stub_rows = [
        _explanation(forecast.id, "roll_mean_7", 4.0, source=ATTRIBUTION_STUB),
        _explanation(forecast.id, "_residual", 1.0, source=ATTRIBUTION_STUB),
    ]
    body = forecast_explanation_summary(
        forecast.id, db=_fake_db(forecast, explanation_rows=stub_rows), current_user=USER
    )
    assert body["attribution_source"] == ATTRIBUTION_STUB

    mixed_rows = [
        _explanation(forecast.id, "roll_mean_7", 4.0, source=ATTRIBUTION_STUB),
        _explanation(forecast.id, "lag_1", 2.0, source=ATTRIBUTION_BOOSTER),
    ]
    body = forecast_explanation_summary(
        forecast.id, db=_fake_db(forecast, explanation_rows=mixed_rows), current_user=USER
    )
    assert body["attribution_source"] == "mixed"


def test_explanation_summary_is_workspace_scoped() -> None:
    other = _forecast(workspace_id=uuid.uuid4())
    db = _fake_db(other, explanation_rows=[])
    try:
        forecast_explanation_summary(other.id, db=db, current_user=USER)
    except Exception as exc:  # HTTPException
        assert getattr(exc, "status_code", None) == 404
    else:  # pragma: no cover - the endpoint must not answer
        raise AssertionError("another workspace's forecast was summarised")


# -------------------------------------------------------- policy simulation


def _policy_result(policy: str, qty: float, sr: float, ps: float, ud: float) -> PolicyResult:
    return PolicyResult(
        policy=policy,
        prepared_qty=qty,
        surplus_sr=sr,
        surplus_ps=ps,
        unmet_ud=ud,
        surplus_days=1,
        unmet_days=2,
        quantile=0.8 if policy == "uncertainty_aware" else None,
        lookback_days=28 if policy == "historical_average" else None,
        risk_weight=2.0 if policy == "risk_aware" else None,
        realised_demand=38.0,
    )


def test_policy_simulation_echoes_the_real_simulation(monkeypatch) -> None:
    forecast = _forecast()
    results = [
        _policy_result("historical_average", 41.0, 0.0, 3.0, 0.0),
        _policy_result("point_forecast", 40.0, 0.0, 2.0, 0.0),
        _policy_result("uncertainty_aware", 48.0, 8.0, 10.0, 0.0),
        _policy_result("risk_aware", 44.0, 4.0, 6.0, 0.0),
    ]
    monkeypatch.setattr(router_mod, "simulate_policies", lambda *a, **k: results)

    body = forecast_policy_simulation(forecast.id, db=_fake_db(forecast), current_user=USER)

    assert set(body["simulations"]) == {
        "historical_average",
        "point_forecast",
        "uncertainty_aware",
        "risk_aware",
    }
    assert body["simulations"]["uncertainty_aware"]["surplus_ps"] == 10.0
    assert body["realised_demand"] == 38.0
    assert body["units"] == "visitor-equivalents"
    # Derived from the data, not written into the source.
    assert body["lowest_realised_surplus"] == {"policy": "point_forecast", "value": 2.0}
    assert body["lowest_unmet_demand"] == {"policy": "historical_average", "value": 0.0}
    # The old implementation shipped invented figures as "insights".
    assert "562 vs 425" not in str(body)
    assert "insights" not in body


def test_policy_simulation_surfaces_unmeasurable_policies(monkeypatch) -> None:
    forecast = _forecast()
    unavailable = PolicyResult(
        policy="historical_average",
        prepared_qty=None,
        surplus_sr=None,
        surplus_ps=None,
        unmet_ud=None,
        surplus_days=0,
        unmet_days=0,
        quantile=None,
        lookback_days=28,
        unavailable_reason="no_history",
    )
    monkeypatch.setattr(router_mod, "simulate_policies", lambda *a, **k: [unavailable])

    body = forecast_policy_simulation(forecast.id, db=_fake_db(forecast), current_user=USER)
    assert body["simulations"]["historical_average"]["unavailable_reason"] == "no_history"
    assert body["simulations"]["historical_average"]["prepared_qty"] is None
    # Nothing measurable → no best-policy claim.
    assert body["lowest_unmet_demand"] is None
    assert body["lowest_realised_surplus"] is None


def test_policy_simulation_is_workspace_scoped(monkeypatch) -> None:
    other = _forecast(workspace_id=uuid.uuid4())
    monkeypatch.setattr(router_mod, "simulate_policies", lambda *a, **k: [])
    try:
        forecast_policy_simulation(other.id, db=_fake_db(other), current_user=USER)
    except Exception as exc:  # HTTPException
        assert getattr(exc, "status_code", None) == 404
    else:  # pragma: no cover
        raise AssertionError("another workspace's forecast was simulated")


# ------------------------------------------------------------ drift (PSI)


def _obs(qty: int, day: int) -> DemandObservation:
    return DemandObservation(
        workspace_id=WS_ID,
        location_id=LOC_ID,
        date=datetime.date(2026, 1, 1) + datetime.timedelta(days=day),
        demand_qty=qty,
    )


def test_psi_is_zero_for_identical_samples_and_positive_for_a_shift() -> None:
    same = [10, 20, 30, 40, 50] * 5
    assert _population_stability_index(same, list(same)) == 0.0
    shifted = _population_stability_index([100] * 25, [10] * 25)
    assert shifted is not None and shifted > 0.25


def test_drift_monitoring_flags_a_shifted_distribution() -> None:
    forecast = _forecast()
    model_version = ModelVersion(
        id=forecast.model_version_id,
        workspace_id=WS_ID,
        algorithm="lightgbm",
        version="lightgbm-fs-2026.09.2-it268",
        status="production",
        feature_set_version="fs-2026.09.2",
        train_start=datetime.date(2016, 1, 1),
        train_end=datetime.date(2017, 1, 31),
    )
    recent = [_obs(100, i) for i in range(24)]
    baseline = [_obs(10, i) for i in range(24)]
    db = _fake_db(forecast, for_model_version=model_version, drift=(recent, baseline))

    body = forecast_drift_monitoring(forecast.id, db=db, current_user=USER)

    assert body["drift_status"] == "significant_change"
    assert body["psi"] is not None and body["psi"] > 0.25
    assert body["recommendation"]["action"] == "retrain_model"
    assert body["windows"]["recent_count"] == 24
    assert body["windows"]["baseline_count"] == 24
    # The training window is reported as context only, never as the baseline.
    assert body["model_training_window"]["start"] == "2016-01-01"
    assert "not the PSI baseline" in body["model_training_window"]["note"]


def test_drift_monitoring_reports_no_change_for_a_stable_site() -> None:
    forecast = _forecast()
    stable = [_obs(40, i) for i in range(24)]
    db = _fake_db(
        forecast, for_model_version=None, drift=(stable, [_obs(40, i) for i in range(24)])
    )
    body = forecast_drift_monitoring(forecast.id, db=db, current_user=USER)
    assert body["drift_status"] == "no_change"
    assert body["psi"] == 0.0
    assert body["recommendation"]["action"] == "continue_monitoring"


def test_drift_monitoring_needs_both_windows() -> None:
    forecast = _forecast()
    db = _fake_db(forecast, for_model_version=None, drift=([_obs(40, 0)], []))
    body = forecast_drift_monitoring(forecast.id, db=db, current_user=USER)
    assert body["drift_status"] == "insufficient_data"
    assert body["psi"] is None


def test_drift_monitoring_is_workspace_scoped() -> None:
    other = _forecast(workspace_id=uuid.uuid4())
    db = _fake_db(other, for_model_version=None, drift=([], []))
    try:
        forecast_drift_monitoring(other.id, db=db, current_user=USER)
    except Exception as exc:  # HTTPException
        assert getattr(exc, "status_code", None) == 404
    else:  # pragma: no cover
        raise AssertionError("another workspace's forecast was monitored")


def test_policy_simulation_runs_the_real_service_unmocked() -> None:
    """No monkeypatch: the router must be wired to the real simulate_policies.

    The other policy tests stub the service, so none of them can catch a
    signature mismatch between the router and ``app.services.policy.simulate``.
    This one drives the endpoint against the real service with no history (so
    the average rule is reported as unmeasurable) and a real observation for the
    day (so the realised metrics are measured, not forecast-relative).
    """
    forecast = _forecast()
    db = MagicMock()
    db.get.side_effect = lambda model, pk: forecast if model is Forecast else None
    db.scalar.return_value = _obs(38, 0)  # the day's observed demand
    db.scalars.side_effect = lambda stmt: _scalars_result([])  # no history at all

    body = forecast_policy_simulation(forecast.id, db=db, current_user=USER)

    sims = body["simulations"]
    assert set(sims) == {
        "historical_average",
        "point_forecast",
        "uncertainty_aware",
        "risk_aware",
    }
    # The average rule has no window to average, so it reports no quantity.
    assert sims["historical_average"]["prepared_qty"] is None
    assert sims["historical_average"]["unavailable_reason"] == "no_history"
    # The forecast-derived rules are computed from the interval.
    assert sims["point_forecast"]["prepared_qty"] == 40.0
    assert sims["point_forecast"]["surplus_ps"] == 2.0  # 40 prepared, 38 observed
    assert sims["point_forecast"]["unmet_ud"] == 0.0
    assert sims["risk_aware"]["prepared_qty"] > sims["point_forecast"]["prepared_qty"]
    assert body["realised_demand"] == 38.0
    assert body["lowest_realised_surplus"] == {"policy": "point_forecast", "value": 2.0}
    # Nothing measurable beats the point rule on unmet demand, and the
    # unmeasurable average rule is not allowed to claim the win.
    assert body["lowest_unmet_demand"] == {"policy": "point_forecast", "value": 0.0}


# ------------------------------------------------- the vector behind a row


def test_serving_vector_comes_from_the_bundle_when_one_is_served(monkeypatch) -> None:
    """One builder decides, and it is the model's when a bundle is served.

    The point, the interval and the attribution must all read the vector this
    function returns. Choosing the legacy builder here while the booster was
    served the 26 trained columns is finding J: one forecast described by two
    different rows.
    """
    from app.services.forecasting import loader as loader_mod

    calls: list[tuple[str, object]] = []
    holidays = frozenset({datetime.date(2026, 7, 4)})

    def _builder(name: str):
        def _build(db, **kw):  # type: ignore[no-untyped-def]
            calls.append((name, kw.get("holidays")))
            return mark

        return _build

    mark = MagicMock(name="feature_vector")
    monkeypatch.setattr(loader_mod, "build_model_features", _builder("model"))
    monkeypatch.setattr(loader_mod, "build_features", _builder("legacy"))
    monkeypatch.setattr(loader_mod, "bundle_holidays", lambda bundle: holidays)

    kwargs = {
        "db": MagicMock(),
        "location_id": LOC_ID,
        "target_date": datetime.date(2026, 10, 11),
        "as_of": datetime.datetime(2026, 10, 10, 3, 0, tzinfo=datetime.UTC),
    }

    monkeypatch.setattr(loader_mod, "get_bundle", lambda: {"model": {}})
    assert loader_mod.build_serving_vector(**kwargs) is mark
    assert calls == [("model", holidays)], "the served model's own calendar must be used"

    # No bundle: the stub point is derived from the legacy vector, so that is
    # the honest vector for the interval and the explanation too.
    calls.clear()
    monkeypatch.setattr(loader_mod, "get_bundle", lambda: None)
    assert loader_mod.build_serving_vector(**kwargs) is mark
    assert calls == [("legacy", None)]


# ------------------------------------------------------- served over HTTP


def _http_db(forecast: Forecast) -> MagicMock:
    """Session double that answers each statement by its target table.

    The three endpoints are exercised through one app, so the double has to
    tell the explanation query from the two drift windows rather than replay a
    fixed list of results.
    """
    db = MagicMock()

    def _get(model, pk):  # type: ignore[no-untyped-def]
        return forecast if model is Forecast else None

    def _scalars(stmt):  # type: ignore[no-untyped-def]
        text = str(stmt)
        result = MagicMock()
        if "forecast_explanations" in text:
            result.all.return_value = [_explanation(forecast.id, "roll_mean_7", 12.5)]
        elif "demand_observations" in text:
            # The recent window is the limited query; the baseline is not.
            return _scalars_result(
                [_obs(100, i) for i in range(24)]
                if "LIMIT" in text.upper()
                else [_obs(10, i) for i in range(24)]
            )
        else:
            result.all.return_value = []
        return result

    db.get.side_effect = _get
    db.scalars.side_effect = _scalars
    return db


def _client(monkeypatch, db: MagicMock, user: User = USER) -> TestClient:
    monkeypatch.setattr(
        router_mod,
        "simulate_policies",
        lambda *a, **k: [_policy_result("point_forecast", 40.0, 0.0, 2.0, 0.0)],
    )
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


def test_endpoints_serve_json_over_http(monkeypatch) -> None:
    forecast = _forecast()
    client = _client(monkeypatch, _http_db(forecast))

    summary = client.get(f"/api/v1/forecasts/{forecast.id}/explanation/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["total_drivers"] == 1

    policy = client.get(f"/api/v1/forecasts/{forecast.id}/policy/simulation")
    assert policy.status_code == 200, policy.text
    body = policy.json()
    assert body["simulations"]["point_forecast"]["surplus_ps"] == 2.0
    assert body["lowest_realised_surplus"] == {"policy": "point_forecast", "value": 2.0}

    drift = client.get(f"/api/v1/forecasts/{forecast.id}/drift/monitoring")
    assert drift.status_code == 200, drift.text
    assert drift.json()["drift_status"] == "significant_change"


def test_endpoints_404_over_http_for_another_workspace(monkeypatch) -> None:
    other = _forecast(workspace_id=uuid.uuid4())
    client = _client(monkeypatch, _http_db(other))
    for path in ("explanation/summary", "policy/simulation", "drift/monitoring"):
        resp = client.get(f"/api/v1/forecasts/{other.id}/{path}")
        assert resp.status_code == 404, (path, resp.text)
