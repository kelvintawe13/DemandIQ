"""Leakage boundary tests (IMPLEMENTATION_ROADMAP.md: "fails the build").

Strategy: rebuild features on doctored data where target-day values are
replaced with poison, then assert no historical feature on that row changed.
Applied per store so a leak hidden at a groupby boundary cannot hide.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def visits_two_stores() -> pd.DataFrame:
    """Two stores x 70 consecutive days with weekday-driven signal."""
    rng = np.random.default_rng(11)
    dates = pd.date_range("2016-01-01", periods=70, freq="D")
    frames = []
    for i, store in enumerate(("air_a", "air_b")):
        weekend = np.where(dates.dayofweek >= 5, 12, 0)
        vals = rng.poisson(20 + i * 5 + weekend)
        frames.append(pd.DataFrame({"air_store_id": store, "visit_date": dates, "visitors": vals}))
    return pd.concat(frames, ignore_index=True)


def _poison_probe_all_stores(visits: pd.DataFrame) -> pd.DataFrame:
    """Poison exactly ONE day per store (its last day): the probe target."""
    doctored = visits.copy()
    probe = doctored.sort_values(["air_store_id", "visit_date"]).groupby("air_store_id").tail(1)
    doctored.loc[probe.index, "visitors"] = -777_777.0
    return doctored, probe


def test_no_feature_reads_own_target_row(visits_two_stores):
    """The probe row's OWN features must be blind to its own target value."""
    from diq_model.features.build import build_features

    clean = build_features(visits_two_stores)
    doctored, probe = _poison_probe_all_stores(visits_two_stores)
    dirty = build_features(doctored)

    key = ["air_store_id", "visit_date"]
    merged = clean.frame.merge(dirty.frame, on=key, suffixes=("", "_poison"))
    probe_keys = probe[key]
    on_probe = merged.merge(probe_keys, on=key)

    hist_cols = [
        "lag_1", "lag_7", "lag_14",
        "roll_mean_7", "roll_mean_28", "roll_std_28",
        "dow_mean_8w", "dow_std_8w", "trend_7_28",
    ]
    for col in hist_cols:
        a, b = on_probe[col], on_probe[f"{col}_poison"]
        both_nan = a.isna() & b.isna()
        same = (a == b) | both_nan
        assert same.all(), (
            f"LEAKAGE in '{col}': the probe row's feature changed when its own "
            f"target value was poisoned"
        )


def test_cascade_changes_are_strictly_downstream(visits_two_stores):
    """Poisoning day T may only change features on days AFTER T - never on or
    before it. This is the true boundary: history flows forward only."""
    from diq_model.features.build import build_features

    clean = build_features(visits_two_stores)
    doctored, probe = _poison_probe_all_stores(visits_two_stores)
    dirty = build_features(doctored)

    key = ["air_store_id", "visit_date"]
    merged = clean.frame.merge(dirty.frame, on=key, suffixes=("", "_poison"))

    hist_cols = [
        "lag_1", "lag_7", "lag_14",
        "roll_mean_7", "roll_mean_28", "roll_std_28",
        "dow_mean_8w", "dow_std_8w", "trend_7_28",
    ]
    for store, pdate in probe[key].itertuples(index=False):
        block = merged[merged["air_store_id"] == store]
        at_or_before = block[block["visit_date"] <= pdate]
        for col in hist_cols:
            a, b = at_or_before[col], at_or_before[f"{col}_poison"]
            both_nan = a.isna() & b.isna()
            same = (a == b) | both_nan
            assert same.all(), (
                f"LEAKAGE in '{col}' ({store}): rows at or before the poisoned "
                f"date {pdate.date()} changed"
            )


def test_lag_1_equals_prior_day(visits_two_stores):
    from diq_model.features.build import add_historical_features

    features = add_historical_features(visits_two_stores)
    s = features[features["air_store_id"] == "air_a"].sort_values("visit_date")
    prior = s["visitors"].shift(1)
    # First row has no prior day -> null; all others must equal it exactly.
    assert s["lag_1"].iloc[1:].equals(prior.iloc[1:])


def test_roll_mean_7_excludes_target_day(visits_two_stores):
    from diq_model.features.build import add_historical_features

    features = add_historical_features(visits_two_stores)
    s = features[features["air_store_id"] == "air_a"].sort_values("visit_date").reset_index()
    for idx in range(10, len(s)):
        window = s["visitors"].iloc[idx - 7 : idx]  # t-7..t-1, target excluded
        expected = window.mean()
        got = s["roll_mean_7"].iloc[idx]
        assert abs(got - expected) < 1e-9, f"row {idx}: {got} != mean(t-7..t-1) {expected}"


def test_dow_mean_uses_same_weekday_only(visits_two_stores):
    from diq_model.features.build import add_historical_features

    features = add_historical_features(visits_two_stores)
    s = features[features["air_store_id"] == "air_a"].sort_values("visit_date").reset_index()
    dow = s["visit_date"].dt.dayofweek
    # 10th Monday: mean of the previous 8 Mondays.
    mondays = s.index[dow == 0]
    idx = mondays[9]
    expected = s.loc[mondays[1:9], "visitors"].mean()
    assert abs(s.loc[idx, "dow_mean_8w"] - expected) < 1e-9


def test_gate_blocks_feature_rows_without_demand(visits_two_stores):
    """Validation gate 3: a feature row for a day with no demand row must fail."""
    from diq_model.features.build import (
        FeatureValidationError,
        assert_feature_rows_have_demand,
    )

    features = visits_two_stores.iloc[:10].copy()
    orphan = features.iloc[[0]].copy()
    orphan["visit_date"] = pd.Timestamp("2099-01-01")
    with pytest.raises(FeatureValidationError, match="demand rows"):
        assert_feature_rows_have_demand(pd.concat([features, orphan]), visits_two_stores)


def test_feature_set_version_present(visits_two_stores):
    from diq_model.features.build import FEATURE_SET_VERSION, build_features

    out = build_features(visits_two_stores)
    assert out.feature_set_version == FEATURE_SET_VERSION
    assert FEATURE_SET_VERSION.startswith("fs-")


def test_calendar_features_known_in_advance(visits_two_stores):
    """Calendar features must be unaffected by poisoning demand (no leak path)."""
    from diq_model.features.build import build_features

    clean = build_features(visits_two_stores)
    doctored, _ = _poison_probe_all_stores(visits_two_stores)
    dirty = build_features(doctored)
    cal_cols = ["dow", "is_weekend", "month", "week_of_year", "is_month_end", "days_to_month_end"]
    key = ["air_store_id", "visit_date"]
    merged = clean.frame.merge(dirty.frame, on=key, suffixes=("", "_poison"))
    for col in cal_cols:
        assert (merged[col] == merged[f"{col}_poison"]).all(), f"calendar feature {col} leaked"
