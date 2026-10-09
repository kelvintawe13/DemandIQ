"""Policy simulation reports what it measured, and only what it measured.

The proposal draws a hard line between *simulated* exposure and *observed*
outcomes (Ch 3.18–3.19, 3.28, 3.29): ``PS`` and ``UD`` are measured against
realised demand ``D``, while ``SR`` is measured against the forecast ``D̂``.

The service used to substitute ``D = D̂`` when no observation existed for the
target day. For tomorrow's forecast that made ``PS`` an exact duplicate of
``SR`` and returned it under a name that reads as a realised result — a model
output presented as an observation, and the one thing the price of the whole
"no synthetic labels" commitment forbids. These tests pin the honest behaviour:
no observation means ``None``, not a copy of the forecast.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from app.models.location import DemandObservation
from app.services.policy.simulate import simulate_period, simulate_policies


def _db(history: list[DemandObservation], observation: DemandObservation | None) -> MagicMock:
    """Session double: ``scalar`` resolves one day, ``scalars`` the lookback window."""
    db = MagicMock()

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        return observation if "demand_observations" in str(stmt) else None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self):  # type: ignore[no-untyped-def]
                return history if "demand_observations" in str(stmt) else []

        return _S()

    db.scalar.side_effect = fake_scalar
    db.scalars.side_effect = fake_scalars
    return db


def _observation(location_id: uuid.UUID, day: datetime.date, qty: float) -> DemandObservation:
    return DemandObservation(
        workspace_id=uuid.uuid4(),
        location_id=location_id,
        date=day,
        demand_qty=qty,
        notes="test",
    )


TARGET = datetime.date(2026, 3, 10)


def test_future_day_reports_no_realised_metrics() -> None:
    """Tomorrow has no outcome: PS/UD must be absent, not a copy of SR."""
    loc_id = uuid.uuid4()
    db = _db([], observation=None)

    results = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        quantile=0.90,
    )
    by_kind = {r.policy: r for r in results}

    # The forecast-relative numbers are always defined.
    point = by_kind["point_forecast"]
    assert point.surplus_sr == 0.0
    assert point.planned_shortfall == 0.0
    assert point.realised_demand is None

    cautious = by_kind["uncertainty_aware"]
    assert cautious.prepared_qty > 50.0  # a buffer above the point forecast
    assert cautious.surplus_sr > 0.0  # preparing above D̂ is planning exposure
    assert cautious.surplus_ps is None  # ... but not a measured surplus
    assert cautious.unmet_ud is None

    # SR and PS must not quietly agree: PS is unknown, not equal to SR.
    assert cautious.surplus_ps != cautious.surplus_sr


def test_realised_day_measures_against_the_observation() -> None:
    loc_id = uuid.uuid4()
    history = [_observation(loc_id, TARGET - datetime.timedelta(days=i), 40.0) for i in range(1, 8)]
    db = _db(history, observation=_observation(loc_id, TARGET, 45.0))

    results = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        quantile=0.90,
    )
    by_kind = {r.policy: r for r in results}

    assert all(r.realised_demand == 45.0 for r in results)

    point = by_kind["point_forecast"]
    assert point.prepared_qty == 50.0
    assert point.surplus_ps == 5.0  # prepared 50, sold 45 -> 5 over
    assert point.unmet_ud == 0.0

    average = by_kind["historical_average"]
    assert average.prepared_qty == 40.0  # the lookback mean
    assert average.surplus_ps == 0.0
    assert average.unmet_ud == 5.0  # 45 came, 40 prepared -> 5 short


def test_a_household_policy_has_no_surplus_when_demand_meets_the_order() -> None:
    """The trade the proposal describes: raising the buffer cuts UD, adds PS."""
    loc_id = uuid.uuid4()
    db = _db([], observation=_observation(loc_id, TARGET, 60.0))

    low = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        policies=["uncertainty_aware"],
        quantile=0.55,
    )[0]
    high = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        policies=["uncertainty_aware"],
        quantile=0.95,
    )[0]

    assert high.prepared_qty > low.prepared_qty
    assert high.unmet_ud is not None and low.unmet_ud is not None
    assert high.unmet_ud <= low.unmet_ud  # more protection against shortfall
    assert high.surplus_ps is not None and low.surplus_ps is not None
    assert high.surplus_ps >= low.surplus_ps  # at the cost of more surplus


def test_explicit_realised_demand_still_overrides_the_lookup() -> None:
    """Callers evaluating a past period keep passing the outcome in."""
    loc_id = uuid.uuid4()
    db = _db([], observation=None)
    result = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        policies=["point_forecast"],
        realised_demand=62.0,
    )[0]
    assert result.realised_demand == 62.0
    assert result.unmet_ud == 12.0
    assert result.surplus_ps == 0.0


def test_period_aggregation_reports_realised_only_gaps() -> None:
    """A period with no observed days has no realised totals to report."""
    loc_id = uuid.uuid4()
    db = _db([], observation=None)

    class _Forecast:
        target_date = TARGET
        point_forecast = 50.0
        lower_bound = 35.0
        upper_bound = 70.0

    agg = simulate_period(db, location_id=loc_id, forecasts=[_Forecast()])
    assert set(agg) == {
        "historical_average",
        "point_forecast",
        "uncertainty_aware",
        "risk_aware",
    }
    for entry in agg.values():
        assert entry["realised_days"] == 0
        assert entry["surplus_ps"] is None
        assert entry["unmet_ud"] is None

    # historical_average has no window history here, so it has no planning-side
    # figures either — a 0.0 would read as "prepare nothing".
    history = agg["historical_average"]
    assert history["prepared_days"] == 0
    assert history["prepared_total"] is None
    assert history["surplus_sr"] is None
    assert history["planned_shortfall"] is None
    assert history["unavailable_reason"] == "no_history"

    # The forecast-derived rules still carry planned-relative exposure.
    for kind in ("point_forecast", "uncertainty_aware", "risk_aware"):
        assert agg[kind]["prepared_days"] == 1
        assert agg[kind]["surplus_sr"] is not None
        assert agg[kind]["planned_shortfall"] is not None
        assert agg[kind]["unavailable_reason"] is None


def test_period_aggregation_sums_realised_metrics_when_days_are_observed() -> None:
    loc_id = uuid.uuid4()
    db = _db([], observation=_observation(loc_id, TARGET, 45.0))

    class _Forecast:
        target_date = TARGET
        point_forecast = 50.0
        lower_bound = 35.0
        upper_bound = 70.0

    agg = simulate_period(db, location_id=loc_id, forecasts=[_Forecast()])
    point = agg["point_forecast"]
    assert point["realised_days"] == 1
    assert point["surplus_ps"] == 5.0
    assert point["unmet_ud"] == 0.0


def test_a_mocked_session_is_not_mistaken_for_an_observation() -> None:
    """A non-ORM row is not evidence: ``float(MagicMock())`` is 1.0.

    Without the ``isinstance`` guard a mocked session would report
    ``realised_demand == 1.0`` — inventing an observed outcome from a test
    double and then computing PS/UD against it.
    """
    db = MagicMock()
    db.scalar.return_value = MagicMock()  # not a DemandObservation
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    results = simulate_policies(
        db,
        location_id=uuid.uuid4(),
        target_date=TARGET,
        point=10.0,
        lower=8.0,
        upper=13.0,
        policies=["historical_average"],
    )
    assert results[0].realised_demand is None
    assert results[0].surplus_ps is None
    assert results[0].unmet_ud is None
    # A test double is not history either: no observations in the window means
    # the rule reports no quantity, not a hardcoded stand-in.
    assert results[0].prepared_qty is None
    assert results[0].surplus_sr is None
    assert results[0].planned_shortfall is None
    assert results[0].unavailable_reason == "no_history"


def test_historical_average_reports_no_quantity_without_history() -> None:
    """No history at the location ⇒ no number, in place of the old ``20.0``.

    The invent-a-number version put an operator-facing preparation quantity in
    front of a site whose demand had never been recorded; the honest answer is
    that there is no basis for the rule.
    """
    loc_id = uuid.uuid4()
    db = _db([], observation=None)  # empty lookback window, no realised day

    result = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        policies=["historical_average"],
    )[0]

    assert result.prepared_qty is None
    assert result.surplus_sr is None
    assert result.planned_shortfall is None
    assert result.surplus_ps is None
    assert result.unmet_ud is None
    assert result.surplus_days == 0 and result.unmet_days == 0
    assert result.unavailable_reason == "no_history"
    # The window it looked in is still reported, so the caller can explain it.
    assert result.lookback_days == 28


def test_historical_average_still_averages_when_history_exists() -> None:
    """The no-history rule must not disable the ordinary path."""
    loc_id = uuid.uuid4()
    history = [
        _observation(loc_id, TARGET - datetime.timedelta(days=n), float(qty))
        for n, qty in ((1, 30.0), (2, 40.0), (3, 50.0))
    ]
    db = _db(history, observation=None)

    result = simulate_policies(
        db,
        location_id=loc_id,
        target_date=TARGET,
        point=50.0,
        lower=35.0,
        upper=70.0,
        policies=["historical_average"],
    )[0]

    assert result.prepared_qty == 40.0  # mean of the three lookback days
    assert result.unavailable_reason is None
    assert result.surplus_sr == 0.0  # P − D̂ = 40 − 50, floored at 0
    assert result.planned_shortfall == 10.0
