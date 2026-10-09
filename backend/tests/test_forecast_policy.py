"""Forecast, policy, explain and quality/alert tests (phase 6-8).

All tests are DB-optional via mocked sessions (same style as
``test_locations_access.py`` / ``test_ingest.py``).
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.forecast import Forecast, ModelVersion
from app.models.location import DemandObservation, Location
from app.models.quality import Alert, QualityIssue, QualityRule
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal
from app.services.forecasting.loader import predict_point
from app.services.policy.simulate import simulate_policies

# ---------------------------------------------------------------- fixtures ---


def _make_workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_user(ws_id: uuid.UUID, role: UserRole = UserRole.owner) -> User:
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


def _make_location(ws_id: uuid.UUID) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Kigali Central",
        area="Kigali",
        cuisine="Rwandan",
        archived_at=None,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _history_rows(
    ws_id: uuid.UUID, loc_id: uuid.UUID, n: int, start_date: datetime.date
) -> list[DemandObservation]:
    rows: list[DemandObservation] = []
    for i in range(n):
        d = start_date + datetime.timedelta(days=i)
        rows.append(
            DemandObservation(
                id=i + 1,
                workspace_id=ws_id,
                location_id=loc_id,
                date=d,
                demand_qty=40 + (i % 10),
                recorded_at=datetime.datetime.combine(d, datetime.time(12, 0), tzinfo=datetime.UTC),
            )
        )
    return rows


# ---------------------------------------------------------------- forecast creation with interval ---


def test_forecast_creation_with_interval() -> None:
    """POST /forecasts returns point, lower, upper, coverage_target, model_version_id."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    # Prepare a history with 40 days (>=30) so fallback is false.
    history = _history_rows(ws.id, loc.id, 40, datetime.date(2026, 1, 1))
    target = datetime.date(2026, 3, 5)

    mock_db = MagicMock()
    # Store for forecasts created
    created_forecasts: dict[uuid.UUID, Forecast] = {}
    model_version = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        algorithm="lightgbm",
        version="v1",
        status="production",
        feature_set_version="v1",
        trained_at=datetime.datetime.now(datetime.UTC),
        promoted_at=datetime.datetime.now(datetime.UTC),
    )

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        text = str(stmt)
        if "model_versions" in text:
            return model_version
        if "calibrations" in text:
            return None
        if "demand_observations" in text and "COUNT" not in text:
            # For realised demand lookup in policy – return None
            return None
        return None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _Scalars:
            def all(self):  # type: ignore[no-untyped-def]
                txt = str(stmt)
                if "demand_observations" in txt:
                    return history
                if "locations" in txt:
                    return [loc]
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _Scalars()

        return _R().scalars()

    mock_db.scalar.side_effect = fake_scalar
    mock_db.scalars.side_effect = fake_scalars

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == Location:
            return loc if pk == loc.id else None
        if model == Forecast:
            return created_forecasts.get(pk)
        return None

    mock_db.get.side_effect = fake_get

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, Forecast):
            created_forecasts[obj.id] = obj
        # For other models (calibration, forecast_explanations, location_access_logs) no-op
        return None

    mock_db.add.side_effect = fake_add
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    resp = client.post(
        "/api/v1/forecasts",
        json={
            "location_id": str(loc.id),
            "target_date": target.isoformat(),
            "horizon_days": 1,
            "granularity": "daily",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "point" in body
    assert "lower" in body
    assert "upper" in body
    assert body["lower"] < body["point"] < body["upper"]
    assert (
        body["coverage_target"] == 0.90
        or body["coverage_target"] == 90.0
        or float(body["coverage_target"]) >= 0.5
    )
    assert "model_version_id" in body
    assert body["fallback"] is False

    app.dependency_overrides.clear()


def test_forecast_fallback_below_seven_days_of_history() -> None:
    """Fewer than 7 days of history: fallback flag is true, point is the average.

    The threshold used to be 30 days and is deliberately 7 now (see
    ``loader.predict_point``): 7 is the minimum that makes ``roll_mean_7`` and
    ``lag_7`` meaningful, so 7-29 days is modelled rather than averaged. The
    companion test below pins that band.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    # Only 3 days of history — below the 7-day fallback threshold.
    history = _history_rows(ws.id, loc.id, 3, datetime.date(2026, 1, 20))
    target = datetime.date(2026, 3, 5)
    as_of = datetime.datetime(2026, 3, 4, 18, 0, tzinfo=datetime.UTC)

    mock_db = MagicMock()
    model_version = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        algorithm="lightgbm",
        version="v1",
        status="production",
        feature_set_version="v1",
        trained_at=datetime.datetime.now(datetime.UTC),
        promoted_at=datetime.datetime.now(datetime.UTC),
    )

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)
        if "model_versions" in txt:
            return model_version
        return None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                txt = str(stmt)
                if "demand_observations" in txt:
                    return history
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalar.side_effect = fake_scalar
    mock_db.scalars.side_effect = fake_scalars
    mock_db.get.return_value = loc
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    result = predict_point(
        mock_db, workspace_id=ws.id, location_id=loc.id, target_date=target, as_of=as_of
    )
    assert result.fallback is True
    # Historical average of the 3 rows: 40, 41, 42 -> 41.0
    assert result.point == 41.0
    assert result.history_days == 3

    # Also test via router POST — should return fallback true
    # Need to recreate mock for router (same history)
    mock_db2 = MagicMock()
    created: dict[uuid.UUID, Forecast] = {}

    def fake_scalar2(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)
        if "model_versions" in txt:
            return model_version
        if "calibrations" in txt:
            return None
        return None

    def fake_scalars2(stmt):  # type: ignore[no-untyped-def]
        class _S2:
            def all(self):  # type: ignore[no-untyped-def]
                txt = str(stmt)
                if "demand_observations" in txt:
                    return history
                return []

        class _R2:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S2()

        return _R2().scalars()

    mock_db2.scalar.side_effect = fake_scalar2
    mock_db2.scalars.side_effect = fake_scalars2
    mock_db2.get.side_effect = lambda m, pk: loc if m == Location else created.get(pk) if m == Forecast else None  # type: ignore[no-untyped-def]
    mock_db2.add.side_effect = lambda obj: created.__setitem__(obj.id, obj) if isinstance(obj, Forecast) else None  # type: ignore[no-untyped-def]
    mock_db2.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db2  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)
    resp = client.post(
        "/api/v1/forecasts",
        json={"location_id": str(loc.id), "target_date": target.isoformat()},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["fallback"] is True

    app.dependency_overrides.clear()


def test_forecast_models_with_seven_to_twenty_nine_days_of_history() -> None:
    """7-29 days of history is modelled, not averaged — no fallback flag.

    Regression guard for the deliberate removal of the old 30-day rule: this
    band used to be answered with a bare historical average and
    ``fallback: true``.
    """
    ws = _make_workspace()
    loc = _make_location(ws.id)

    history = _history_rows(ws.id, loc.id, 10, datetime.date(2026, 1, 20))
    target = datetime.date(2026, 3, 5)
    as_of = datetime.datetime(2026, 3, 4, 18, 0, tzinfo=datetime.UTC)

    mock_db = MagicMock()
    model_version = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        algorithm="lightgbm",
        version="v1",
        status="production",
        feature_set_version="v1",
        trained_at=datetime.datetime.now(datetime.UTC),
        promoted_at=datetime.datetime.now(datetime.UTC),
    )

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        return model_version if "model_versions" in str(stmt) else None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                return history if "demand_observations" in str(stmt) else []

        return _S()

    mock_db.scalar.side_effect = fake_scalar
    mock_db.scalars.side_effect = fake_scalars
    mock_db.get.return_value = loc
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    result = predict_point(
        mock_db, workspace_id=ws.id, location_id=loc.id, target_date=target, as_of=as_of
    )
    assert result.history_days == 10
    assert result.fallback is False
    assert result.point > 0


def test_policy_simulate_sr_ps_ud_tradeoff() -> None:
    """Raising q reduces UD and increases PS — verify trade visible."""
    ws = _make_workspace()
    loc = _make_location(ws.id)
    mock_db = MagicMock()
    # Mock demand lookup for realised demand: no observation, so realised = point
    # But we supply realised_demand explicitly to test trade.
    # For this service-level test we bypass DB lookup by passing realised_demand.
    mock_db.scalar.return_value = None
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    point = 50.0
    lower = 35.0
    upper = 70.0
    # Simulate with low and high quantile
    low = simulate_policies(
        mock_db,
        location_id=loc.id,
        target_date=datetime.date(2026, 3, 10),
        point=point,
        lower=lower,
        upper=upper,
        policies=["uncertainty_aware"],
        quantile=0.70,
        realised_demand=55,
    )[0]
    high = simulate_policies(
        mock_db,
        location_id=loc.id,
        target_date=datetime.date(2026, 3, 10),
        point=point,
        lower=lower,
        upper=upper,
        policies=["uncertainty_aware"],
        quantile=0.95,
        realised_demand=55,
    )[0]

    # higher quantile -> higher prepared qty
    assert high.prepared_qty > low.prepared_qty
    # higher quantile -> lower or equal unmet
    assert high.unmet_ud <= low.unmet_ud
    # higher quantile -> higher or equal surplus_ps
    assert high.surplus_ps >= low.surplus_ps
    # SR is relative to forecast: P - Dhat
    assert high.surplus_sr >= low.surplus_sr


def test_policy_simulate_all_four_via_router() -> None:
    """POST /policies/simulate returns 4 policies with SR/PS/UD in visitor units."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    # Prepare history for historical_average
    history = _history_rows(ws.id, loc.id, 40, datetime.date(2026, 1, 1))

    mock_db = MagicMock()

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                txt = str(stmt)
                if "demand_observations" in txt:
                    return history
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None  # no realised observation
    mock_db.get.return_value = loc
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    resp = client.post(
        "/api/v1/policies/simulate",
        json={
            "location_id": str(loc.id),
            "period_start": "2026-03-10",
            "period_end": "2026-03-10",
            "quantile": 0.80,
            "point": 50,
            "lower": 35,
            "upper": 70,
            "target_date": "2026-03-10",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "results" in body
    assert len(body["results"]) == 4
    kinds = {r["policy"] for r in body["results"]}
    assert kinds == {
        "historical_average",
        "point_forecast",
        "uncertainty_aware",
        "risk_aware",
    }
    for r in body["results"]:
        assert "surplus_sr" in r
        assert "surplus_ps" in r
        assert "unmet_ud" in r
        assert r["unit"] == "visitor"
        # Ensure never kg
        assert "kg" not in str(r).lower()

    # Point forecast SR must be 0 (P == Dhat)
    point_r = next(x for x in body["results"] if x["policy"] == "point_forecast")
    assert point_r["surplus_sr"] == 0.0

    app.dependency_overrides.clear()


def test_policy_simulate_reports_no_basis_without_history() -> None:
    """A location with no recorded demand gets no invented preparation quantity.

    ``historical_average`` used to answer this with a hardcoded ``20.0``. The
    endpoint now reports that the rule has no basis — ``prepared_qty: null`` plus
    ``unavailable_reason`` — while the forecast-derived rules still answer, so one
    un-backable rule cannot stop the comparison.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    mock_db = MagicMock()

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                # Empty lookback window: no demand_observations for this location.
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None  # no realised observation either
    mock_db.get.return_value = loc
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]

    resp = TestClient(app).post(
        "/api/v1/policies/simulate",
        json={
            "location_id": str(loc.id),
            "period_start": "2026-03-10",
            "period_end": "2026-03-10",
            "quantile": 0.80,
            "point": 50,
            "lower": 35,
            "upper": 70,
            "target_date": "2026-03-10",
        },
    )
    assert resp.status_code == 200, resp.text
    by_policy = {r["policy"]: r for r in resp.json()["results"]}

    history = by_policy["historical_average"]
    # No quantity, and nothing derived from one: SR/PS/UD are all absent.
    # (``prepared_total`` is the period-aggregate field, not part of this shape.)
    assert history["prepared_qty"] is None
    assert history["surplus_sr"] is None
    assert history["surplus_ps"] is None
    assert history["unmet_ud"] is None
    assert history["planned_shortfall"] is None
    assert history["unavailable_reason"] == "no_history"

    for kind in ("point_forecast", "uncertainty_aware", "risk_aware"):
        assert by_policy[kind]["prepared_qty"] is not None
        assert by_policy[kind]["unavailable_reason"] is None

    app.dependency_overrides.clear()


def test_explain_endpoint_returns_shap() -> None:
    """GET /forecasts/{id}/explanation returns predictive attribution."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    fc_id = uuid.uuid4()
    model_version_id = uuid.uuid4()
    forecast = Forecast(
        id=fc_id,
        workspace_id=ws.id,
        location_id=loc.id,
        model_version_id=model_version_id,
        target_date=datetime.date(2026, 3, 12),
        point_forecast=50.0,  # type: ignore[assignment]
        lower_bound=35.0,  # type: ignore[assignment]
        upper_bound=70.0,  # type: ignore[assignment]
        coverage_target=90.0,  # type: ignore[assignment]
        feature_set_version="v1",
        as_of_timestamp=datetime.datetime(2026, 3, 11, 23, 59, tzinfo=datetime.UTC),
        created_at=datetime.datetime.now(datetime.UTC),
    )

    # History for explanation build
    history = _history_rows(ws.id, loc.id, 35, datetime.date(2026, 1, 1))

    mock_db = MagicMock()

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        return None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)

        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                if "demand_observations" in txt:
                    return history
                if "forecast_explanations" in txt:
                    return []
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalar.side_effect = fake_scalar
    mock_db.scalars.side_effect = fake_scalars

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == Forecast:
            return forecast if pk == fc_id else None
        if model == Location:
            return loc
        return None

    mock_db.get.side_effect = fake_get
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.delete = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    resp = client.get(f"/api/v1/forecasts/{fc_id}/explanation")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["attribution_type"] == "predictive"
    assert "not causal" in body["disclaimer"].lower()
    assert "contributions" in body
    assert len(body["contributions"]) > 0
    for c in body["contributions"]:
        assert "feature" in c
        assert "impact" in c
        assert "value" in c

    app.dependency_overrides.clear()


def test_forecast_list_and_detail_and_export() -> None:
    """GET /forecasts list, detail and export csv."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)
    fc_id = uuid.uuid4()
    forecast = Forecast(
        id=fc_id,
        workspace_id=ws.id,
        location_id=loc.id,
        model_version_id=uuid.uuid4(),
        target_date=datetime.date(2026, 3, 12),
        point_forecast=42.0,  # type: ignore[assignment]
        lower_bound=30.0,  # type: ignore[assignment]
        upper_bound=55.0,  # type: ignore[assignment]
        coverage_target=90.0,  # type: ignore[assignment]
        feature_set_version="v1",
        as_of_timestamp=datetime.datetime(2026, 3, 11, 23, 59, tzinfo=datetime.UTC),
        created_at=datetime.datetime.now(datetime.UTC),
    )
    history = _history_rows(ws.id, loc.id, 35, datetime.date(2026, 1, 1))

    mock_db = MagicMock()

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        return None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)

        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                if "forecasts" in txt:
                    return [forecast]
                if "demand_observations" in txt:
                    return history
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalar.side_effect = fake_scalar
    mock_db.scalars.side_effect = fake_scalars
    mock_db.get.side_effect = lambda m, pk: forecast if m == Forecast and pk == fc_id else (loc if m == Location else None)  # type: ignore[no-untyped-def]
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    # List
    resp = client.get(f"/api/v1/forecasts?location_id={loc.id}")
    assert resp.status_code == 200, resp.text
    assert "data" in resp.json()
    # Detail
    resp = client.get(f"/api/v1/forecasts/{fc_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(fc_id)
    # Export csv
    resp = client.get(f"/api/v1/forecasts/{fc_id}/export?format=csv")
    assert resp.status_code == 200, resp.text
    assert "text/csv" in resp.headers["content-type"]
    # Export xlsx
    resp = client.get(f"/api/v1/forecasts/{fc_id}/export?format=xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]

    app.dependency_overrides.clear()


def test_quality_and_alert_endpoints() -> None:
    """GET /quality/score, /quality/issues, /alerts smoke tests."""
    ws = _make_workspace()
    user = _make_user(ws.id)

    issue = QualityIssue(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        severity="warning",
        summary="Gap in series",
        suggested_fix="Check ingestion",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    rule = QualityRule(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="gap_detection",
        description="Gap",
        enabled=True,
    )

    mock_db = MagicMock()

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)

        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                if "quality_issues" in txt:
                    return [issue]
                if "quality_rules" in txt:
                    return [rule]
                if "alerts" in txt:
                    return []
                if "alert_rules" in txt:
                    return []
                return []

        class _R:
            def scalars(self):  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None
    mock_db.get.return_value = None
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    resp = client.get("/api/v1/quality/score")
    assert resp.status_code == 200
    assert "overall" in resp.json()

    resp = client.get("/api/v1/quality/issues")
    assert resp.status_code == 200
    assert "data" in resp.json()

    resp = client.get("/api/v1/quality/rules")
    assert resp.status_code == 200

    resp = client.get("/api/v1/alerts")
    assert resp.status_code == 200

    resp = client.get("/api/v1/alerts/rules")
    assert resp.status_code == 200

    resp = client.get("/api/v1/policies")
    assert resp.status_code == 200
    assert "data" in resp.json()

    app.dependency_overrides.clear()


def test_policies_patch_creates_change_request() -> None:
    """PATCH /policies/{id} creates a change_request (admin)."""
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    from app.models.policy import PreparationPolicy

    policy = PreparationPolicy(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        kind="uncertainty_aware",
        quantile=0.80,  # type: ignore[assignment]
        lookback_days=None,
        is_active=False,
    )

    mock_db = MagicMock()
    mock_db.get.side_effect = lambda m, pk: policy if m == PreparationPolicy and pk == policy.id else None  # type: ignore[no-untyped-def]
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalar.return_value = None
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: admin  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    resp = client.patch(f"/api/v1/policies/{policy.id}", json={"quantile": 0.90})
    assert resp.status_code == 200, resp.text
    assert "change_request_id" in resp.json()
    assert resp.json()["status"] == "pending"

    app.dependency_overrides.clear()


def _alert(ws_id: uuid.UUID, *, read: bool = False, resolved: bool = False) -> Alert:
    now = datetime.datetime.now(datetime.UTC)
    return Alert(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        severity="warning",
        title="Freshness below threshold",
        body=None,
        read_at=now if read else None,
        resolved_at=now if resolved else None,
        created_at=now,
    )


def test_alerts_list_reports_exact_unread_count() -> None:
    """GET /alerts carries a workspace-wide unread total, not the page size.

    The sidebar badge reads this number; deriving it from ``len(data)`` would
    make the badge depend on ``limit`` and undercount workspaces with more
    unread alerts than one page holds.
    """
    ws = _make_workspace()
    owner = _make_user(ws.id, role=UserRole.owner)
    page = [_alert(ws.id) for _ in range(3)]

    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=page))
    # The count query is a separate db.scalar() call; the page only holds one row.
    mock_db.scalar.return_value = 11

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: owner  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: owner  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.get("/api/v1/alerts", params={"status": "unread", "limit": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unread_count"] == 11
    assert len(body["data"]) == 1
    assert body["has_more"] is True

    app.dependency_overrides.clear()


def test_alerts_unread_count_is_zero_when_count_query_returns_none() -> None:
    """A NULL count (no rows) is reported as 0, never null.

    The badge treats a missing number as "no badge"; returning None here would
    silently hide a true zero and, worse, break the ``typeof === 'number'``
    guard in the client.
    """
    ws = _make_workspace()
    owner = _make_user(ws.id, role=UserRole.owner)

    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    mock_db.scalar.return_value = None

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: owner  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: owner  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.get("/api/v1/alerts")
    assert resp.status_code == 200, resp.text
    assert resp.json()["unread_count"] == 0
    assert resp.json()["data"] == []

    app.dependency_overrides.clear()
