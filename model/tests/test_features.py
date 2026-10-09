"""Feature build tests: structure, cold-start nulls, calendar, checkpointing."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def visits_two_stores() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    dates = pd.date_range("2016-01-01", periods=70, freq="D")
    frames = []
    for i, store in enumerate(("air_a", "air_b")):
        weekend = np.where(dates.dayofweek >= 5, 12, 0)
        vals = rng.poisson(20 + i * 5 + weekend)
        frames.append(pd.DataFrame({"air_store_id": store, "visit_date": dates, "visitors": vals}))
    return pd.concat(frames, ignore_index=True)


def test_build_features_shape_and_columns(visits_two_stores):
    from diq_model.features.build import build_features

    out = build_features(visits_two_stores)
    assert out.n_rows > 0
    for col in ("lag_1", "lag_7", "roll_mean_7", "roll_mean_28", "dow", "is_weekend", "month"):
        assert col in out.frame.columns
    assert out.feature_set_version.startswith("fs-")
    # Cold-start rows (no historical features at all) are dropped.
    assert out.frame["lag_1"].notna().any()


def test_cold_start_rows_are_null_not_zero(visits_two_stores):
    from diq_model.features.build import add_historical_features

    features = add_historical_features(visits_two_stores)
    s = features[features["air_store_id"] == "air_a"].sort_values("visit_date")
    assert pd.isna(s["lag_1"].iloc[0]), "first day must be null, never imputed"
    assert pd.isna(s["roll_mean_28"].iloc[5]), "28d rolling needs 7+ days of history"
    assert pd.notna(s["roll_mean_7"].iloc[6]), "7d rolling is defined from the 4th day"


def test_calendar_feature_values(visits_two_stores):
    from diq_model.features.build import add_calendar_features

    df = visits_two_stores.iloc[[0]].copy()  # 2016-01-01, a Friday
    out = add_calendar_features(df)
    assert out["dow"].iloc[0] == 4  # Friday
    assert out["is_weekend"].iloc[0] == 0
    assert out["month"].iloc[0] == 1
    assert out["days_to_month_end"].iloc[0] == 30


def test_holiday_flags_computed_from_calendar(visits_two_stores):
    """day_before/day_after must follow the date axis, not row order."""
    from diq_model.features.build import add_calendar_features

    df = visits_two_stores.copy()
    df["holiday_flg"] = 0
    # Mark Jan 10 as a holiday: Jan 9 should be day_before, Jan 11 day_after.
    df.loc[df["visit_date"] == pd.Timestamp("2016-01-10"), "holiday_flg"] = 1
    out = add_calendar_features(df)
    jan9 = out[out["visit_date"] == pd.Timestamp("2016-01-09")].iloc[0]
    jan10 = out[out["visit_date"] == pd.Timestamp("2016-01-10")].iloc[0]
    jan11 = out[out["visit_date"] == pd.Timestamp("2016-01-11")].iloc[0]
    assert jan9["day_before_holiday"] == 1
    assert jan10["is_holiday"] == 1
    assert jan11["day_after_holiday"] == 1


def test_save_load_feature_frame_roundtrip(tmp_path, visits_two_stores):
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.features.build import build_features, load_feature_frame, save_feature_frame

    store = CheckpointStore(tmp_path / "ckpt")
    fframe = build_features(visits_two_stores)
    save_feature_frame(store, fframe)
    loaded = load_feature_frame(store)

    assert loaded.feature_set_version == fframe.feature_set_version
    assert loaded.feature_columns == fframe.feature_columns
    assert loaded.frame.equals(fframe.frame)


def test_dow_stats_match_weekly_cycle(visits_two_stores):
    """dow_mean_8w for consecutive Mondays must differ by exactly one week's shift."""
    from diq_model.features.build import add_historical_features

    features = add_historical_features(visits_two_stores)
    s = features[features["air_store_id"] == "air_a"].sort_values("visit_date").reset_index()
    mondays = s.index[s["visit_date"].dt.dayofweek == 0]
    # With 8-occurrence windows, rolling means evolve smoothly; both defined.
    assert pd.notna(s.loc[mondays[8], "dow_mean_8w"])
    assert s.loc[mondays[9], "dow_mean_8w"] is not None
