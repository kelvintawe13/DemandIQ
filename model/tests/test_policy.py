"""Policy simulation tests: the Section 3.18-3.19 identities and behaviour."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sim_df() -> pd.DataFrame:
    """Five days with a conformal upper bound above the point forecast."""
    return pd.DataFrame(
        {
            "air_store_id": ["s"] * 5,
            "visit_date": pd.date_range("2016-03-01", periods=5),
            "visitors": [20, 30, 10, 40, 25],  # realised D
            "prediction": [22, 28, 12, 35, 30],  # D_hat
            "upper": [26, 33, 16, 42, 38],  # conformal upper (q80-ish)
        }
    )


@pytest.fixture
def visits(sim_df) -> pd.DataFrame:
    """60 days of history before the sim window for the historical-average."""
    rng = np.random.default_rng(2)
    return pd.DataFrame(
        {
            "air_store_id": ["s"] * 60,
            "visit_date": pd.date_range("2016-01-01", periods=60),
            "visitors": rng.poisson(25, 60),
        }
    )


def test_point_forecast_sr_is_zero_by_construction(sim_df, visits):
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    (result,) = simulate_policies(sim_df, visits, [Policy(PolicyKind.point_forecast)])
    assert result.surplus_sr == 0.0, "SR must be identically 0 when P = D_hat"
    assert result.surplus_ps > 0 or result.unmet_ud > 0


def test_exposure_arithmetic_matches_formulas(sim_df, visits):
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    (result,) = simulate_policies(sim_df, visits, [Policy(PolicyKind.point_forecast)])
    d, p = sim_df["visitors"], sim_df["prediction"]
    expected_ps = float(np.clip(p - d, 0, None).sum())
    expected_ud = float(np.clip(d - p, 0, None).sum())
    assert result.surplus_ps == pytest.approx(expected_ps)
    assert result.unmet_ud == pytest.approx(expected_ud)
    assert result.surplus_days == int(((p - d) > 0).sum())
    assert result.unmet_days == int(((d - p) > 0).sum())


def test_uncertainty_aware_reduces_ud_vs_point(sim_df, visits):
    """P = upper (scale 1.0) >= P = D_hat, so UD must not increase
    (Section 3.17 trade-off)."""
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    point, unc = simulate_policies(
        sim_df,
        visits,
        [Policy(PolicyKind.point_forecast), Policy(PolicyKind.uncertainty_aware, quantile=1.0)],
    )
    assert unc.unmet_ud <= point.unmet_ud
    assert unc.surplus_ps >= point.surplus_ps
    # And SR: both carry nonzero planning exposure, unc >= point.
    assert unc.surplus_sr >= point.surplus_sr


def test_scale_sweeps_monotonically(sim_df):
    """Raising the multiplier pads the upper bound: UD falls (weakly) and PS
    rises (weakly) - the trade-off dial the validation sweep turns."""
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    preps = {}
    for scale in (1.0, 1.2, 1.5):
        (r,) = simulate_policies(
            sim_df, pd.DataFrame(columns=["air_store_id"]),
            [Policy(PolicyKind.uncertainty_aware, quantile=scale)],
        )
        preps[scale] = r.prepared_total
    assert preps[1.0] < preps[1.2] < preps[1.5]


def test_historical_average_uses_only_prior_days(sim_df, visits):
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    (result,) = simulate_policies(
        sim_df, visits, [Policy(PolicyKind.historical_average, lookback_days=28)]
    )
    # Frozen at window start: mean of the last 28 history days BEFORE March.
    hist = visits.sort_values("visit_date")
    expected = hist["visitors"].iloc[-28:].mean()
    # Sim window prep total = 5 * that mean (single store); PolicyResult
    # rounds exposures to 2 decimals, so allow that precision.
    assert result.prepared_total == pytest.approx(5 * expected, rel=1e-3)


def test_historical_average_never_reads_window_demand(sim_df, visits):
    """Frozen policy: tampering with in-window realised demand must not move
    prepared_total (PS/UD change; the policy itself cannot)."""
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    base = simulate_policies(
        sim_df, visits, [Policy(PolicyKind.historical_average, lookback_days=28)]
    )
    poisoned = sim_df.copy()
    poisoned["visitors"] = 999_999.0  # tamper with realised demand in window
    tampered = simulate_policies(
        poisoned, visits, [Policy(PolicyKind.historical_average, lookback_days=28)]
    )
    # PS/UD change (they compare against D), but prepared_total must not.
    assert tampered[0].prepared_total == base[0].prepared_total


def test_uncertainty_requires_upper_bound(sim_df, visits):
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    with pytest.raises(ValueError, match="upper"):
        simulate_policies(
            sim_df.drop(columns=["upper"]),
            visits,
            [Policy(PolicyKind.uncertainty_aware, quantile=1.0)],
        )


def test_policy_names_match_schema_enum():
    """Every PolicyKind must exist in the database enum, and vice versa.

    The two are written by hand in different languages, so this is the check
    that keeps them from drifting: adding a policy here without the migration
    (or the reverse) fails.
    """
    from pathlib import Path

    from diq_model.policy.simulate import PolicyKind

    values = {p.value for p in PolicyKind}
    assert values == {
        "historical_average",
        "point_forecast",
        "uncertainty_aware",
        "risk_aware",
    }

    schema = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
    declaration = schema.read_text(encoding="utf-8")
    line = next(
        ln for ln in declaration.splitlines() if ln.startswith("CREATE TYPE policy_kind")
    )
    for value in values:
        assert f"'{value}'" in line, f"{value} missing from schema.sql policy_kind"


def test_tradeoff_table_columns(sim_df, visits):
    from diq_model.policy.simulate import default_policies, simulate_policies, tradeoff_table

    residuals = sim_df["visitors"].to_numpy() - sim_df["prediction"].to_numpy()
    table = tradeoff_table(
        simulate_policies(sim_df, visits, default_policies(), residuals=residuals)
    )
    for col in ("policy", "prepared_total", "surplus_sr", "surplus_ps", "unmet_ud",
                "surplus_days", "unmet_days", "ps_per_day", "ud_per_day"):
        assert col in table.columns
    assert len(table) == 4


# ---------------------------------------------------------------------------
# Risk-aware policy (Section 3.17)
# ---------------------------------------------------------------------------


def test_newsvendor_level_is_the_balanced_cost_fractile():
    from diq_model.policy.simulate import newsvendor_level

    # Equal costing puts the answer at the median of the error distribution.
    assert newsvendor_level(1.0) == pytest.approx(0.5)
    # A missed customer costing more than a leftover prepares above the median.
    assert newsvendor_level(3.0) == pytest.approx(0.75)
    assert newsvendor_level(0.5) == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        newsvendor_level(0.0)


def test_risk_aware_prepares_more_as_a_lost_customer_costs_more(sim_df, visits):
    residuals = np.array([-4.0, -2.0, 0.0, 3.0, 9.0])
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    preps = []
    for weight in (0.5, 1.0, 2.0, 5.0):
        (r,) = simulate_policies(
            sim_df,
            visits,
            [Policy(PolicyKind.risk_aware, weight=weight)],
            residuals=residuals,
        )
        preps.append((weight, r.prepared_total))

    totals = [total for _, total in preps]
    assert totals == sorted(totals), "preparation must not fall as the cost rises"
    assert totals[0] < totals[-1]


def test_risk_aware_at_equal_costing_is_the_point_forecast_plus_bias(sim_df, visits):
    """At weight 1.0 the critical fractile is the median error, so the policy
    reduces to the point forecast plus the model's median bias. This is the
    honest reason the validation sweep may prefer the point policy, and it is a
    property, not a defect - so it is pinned rather than papered over."""
    residuals = np.array([-4.0, -2.0, 0.0, 3.0, 9.0])
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    (point,) = simulate_policies(sim_df, visits, [Policy(PolicyKind.point_forecast)])
    (risk,) = simulate_policies(
        sim_df,
        visits,
        [Policy(PolicyKind.risk_aware, weight=1.0)],
        residuals=residuals,
    )
    median_error = float(np.median(residuals))
    assert risk.prepared_total == pytest.approx(point.prepared_total + 5 * median_error)


def test_risk_aware_sits_between_point_and_the_upper_bound(sim_df, visits):
    """With the calendar's own error sample, a priced rule lands between the
    point forecast and the full conformal bound - the balance it exists to
    strike. Note the qualifier: the ordering follows from the quantile being
    read off the same error distribution the bound was calibrated on, so it is
    a property of a CONSISTENT calibration, not of the newsvendor formula
    alone. An inconsistent residual sample can push any quantile above the
    bound, which is why calibration uses a held-out window.
    """
    residuals = sim_df["visitors"].to_numpy() - sim_df["prediction"].to_numpy()
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    (point,) = simulate_policies(sim_df, visits, [Policy(PolicyKind.point_forecast)])
    (risk,) = simulate_policies(
        sim_df,
        visits,
        [Policy(PolicyKind.risk_aware, weight=5.0)],
        residuals=residuals,
    )
    (bound,) = simulate_policies(sim_df, visits, [Policy(PolicyKind.uncertainty_aware)])
    assert point.prepared_total < risk.prepared_total < bound.prepared_total


def test_risk_aware_requires_calibration_residuals(sim_df, visits):
    """The quantile comes from errors the model made before the window. Without
    them the policy must refuse, not fall back to a made-up number."""
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    with pytest.raises(ValueError, match="calibration residuals"):
        simulate_policies(sim_df, visits, [Policy(PolicyKind.risk_aware, weight=2.0)])


def test_same_residual_window_is_used_for_every_day(sim_df, visits):
    """The quantile is fixed before the window opens: the policy must not react
    to demand inside the period it is planning against."""
    residuals = np.array([1.0, 2.0, 3.0])
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    (r,) = simulate_policies(
        sim_df,
        visits,
        [Policy(PolicyKind.risk_aware, weight=2.0)],
        residuals=residuals,
    )
    offset = float(np.quantile(residuals, 2.0 / 3.0))
    # prepared_total is rounded for reporting, so compare at that resolution.
    assert r.prepared_total == pytest.approx(
        float(sim_df["prediction"].sum()) + 5 * offset, abs=0.01
    )
