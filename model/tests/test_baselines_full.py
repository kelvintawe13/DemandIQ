"""Baseline suite (EVALUATION.md) + persistence, guards, manifest, CLI."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest


# ------------------------------------------------------------- fixtures
@pytest.fixture
def train_test():
    rng = np.random.default_rng(11)
    dates = pd.date_range("2016-01-01", periods=90, freq="D")
    frames = []
    for i, store in enumerate(("air_a", "air_b")):
        weekend = np.where(dates.dayofweek >= 5, 10, 0)
        frames.append(
            pd.DataFrame(
                {
                    "air_store_id": store,
                    "visit_date": dates,
                    "visitors": rng.poisson(20 + i * 5 + weekend),
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    split = pd.Timestamp("2016-03-01")  # day 61: train = 60 days, test = 30
    return df[df["visit_date"] < split], df[df["visit_date"] >= split]


@pytest.fixture
def sim_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "air_store_id": ["s"] * 5,
            "visit_date": pd.date_range("2016-03-01", periods=5),
            "visitors": [20, 30, 10, 40, 25],
            "prediction": [22, 28, 12, 35, 30],
            "upper": [26, 33, 16, 42, 38],
        }
    )


@pytest.fixture
def visits() -> pd.DataFrame:
    rng = np.random.default_rng(2)
    return pd.DataFrame(
        {
            "air_store_id": ["s"] * 60,
            "visit_date": pd.date_range("2016-01-01", periods=60),
            "visitors": rng.poisson(25, 60),
        }
    )


# ------------------------------------------------------------- baselines
def test_naive_baseline_equals_yesterday(train_test):
    """Naive returns exactly the previous day's visitors; day 1 falls back."""
    from diq_model.models.baselines import NaiveBaseline

    train, test = train_test
    preds = NaiveBaseline().fit(train).predict(test)

    s = test[test["air_store_id"] == "air_a"].sort_values("visit_date")
    train_mean = train[train["air_store_id"] == "air_a"]["visitors"].mean()
    # First test day: no in-window predecessor -> store train mean.
    assert preds.loc[s.index[0]] == pytest.approx(train_mean)
    # Every later day: yesterday's actual visitors.
    assert preds.loc[s.index[1]] == s["visitors"].iloc[0]
    assert preds.loc[s.index[2]] == s["visitors"].iloc[1]


def test_seasonal_naive_equals_t_minus_7(train_test):
    from diq_model.models.baselines import SeasonalNaiveBaseline

    train, test = train_test
    preds = SeasonalNaiveBaseline().fit(train).predict(test)

    s = test[test["air_store_id"] == "air_a"].sort_values("visit_date")
    # Day 8 of the window (position 7): its t-7 is position 0.
    assert preds.loc[s.index[7]] == s["visitors"].iloc[0]
    # Days 1-7 have no in-window t-7: train-mean fallback.
    train_mean = train[train["air_store_id"] == "air_a"]["visitors"].mean()
    assert preds.loc[s.index[3]] == pytest.approx(train_mean)


def test_historical_average_frozen_at_window_start(train_test):
    from diq_model.models.baselines import HistoricalAverageBaseline

    train, test = train_test
    preds = HistoricalAverageBaseline(lookback_days=28).fit(train).predict(test)

    expected = train[train["air_store_id"] == "air_a"]["visitors"].tail(28).mean()
    idx = test[test["air_store_id"] == "air_a"].index
    assert ((preds.loc[idx] - expected).abs() < 1e-6).all()


def test_global_average_floor(train_test):
    from diq_model.models.baselines import GlobalAverageBaseline

    train, test = train_test
    preds = GlobalAverageBaseline().fit(train).predict(test)
    assert (preds == train["visitors"].mean()).all()


def test_all_baselines_interface_parity(train_test):
    """Every baseline implements fit/predict/evaluate with full-coverage preds."""
    from diq_model.models.baselines import (
        DayOfWeekBaseline,
        GlobalAverageBaseline,
        HistoricalAverageBaseline,
        NaiveBaseline,
        SeasonalNaiveBaseline,
    )

    train, test = train_test
    models = [
        GlobalAverageBaseline(),
        NaiveBaseline(),
        SeasonalNaiveBaseline(),
        HistoricalAverageBaseline(),
        DayOfWeekBaseline(),
    ]
    for m in models:
        out = m.fit(train).predict(test)
        assert len(out) == len(test) and out.notna().all(), type(m).__name__
        metrics = m.evaluate(test)
        assert {"mae", "rmse", "rmsle"} <= set(metrics), type(m).__name__


# ---------------------------------------------------------- model save()
def test_lightgbm_save_returns_path(tmp_path, visits_two_stores):
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.features.build import build_features
    from diq_model.models.lightgbm_model import LightGBMModel

    store = CheckpointStore(tmp_path / "ckpt")
    fframe = build_features(visits_two_stores)
    model = LightGBMModel(num_boost_round=10).fit(
        fframe.frame, feature_columns=fframe.feature_columns
    )
    assert str(model.save(store)).endswith("lightgbm_model.joblib")


def test_catboost_save_returns_path(tmp_path, visits_two_stores):
    pytest.importorskip("catboost")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.features.build import build_features
    from diq_model.models.catboost_model import CatBoostModel

    store = CheckpointStore(tmp_path / "ckpt")
    fframe = build_features(visits_two_stores)
    model = CatBoostModel(num_boost_round=10).fit(
        fframe.frame, feature_columns=fframe.feature_columns
    )
    assert str(model.save(store)).endswith("catboost_model.joblib")


# ------------------------------------------------------------- artifacts
def test_manifest_to_json_roundtrip(tmp_path):
    from diq_model.artifacts import StageManifest

    m = StageManifest(
        stage="x", started_at="t0", finished_at="t1", duration_s=1.0, seed=42,
        python_version="3.11", platform="test", versions={},
    )
    parsed = json.loads(m.to_json())
    assert parsed["stage"] == "x" and parsed["seed"] == 42


def test_tmp_file_removed_after_atomic_write(tmp_path):
    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    store.save_object({"a": 1}, "x.joblib")
    assert list(store.root.glob("*.tmp")) == []


# --------------------------------------------------- feature-build guards
def test_leakage_gate_raises_on_empty(visits_two_stores):
    from diq_model.features.build import FeatureValidationError, assert_no_window_overlap

    with pytest.raises(FeatureValidationError, match="no dated rows"):
        assert_no_window_overlap(visits_two_stores.iloc[0:0])


def test_leakage_gate_raises_on_actual_leak(visits_two_stores):
    """Negative control: the gate must CATCH a deliberately leaky build."""
    from diq_model.features import build as fb

    def leaky_build(df):
        df = df.sort_values(["air_store_id", "visit_date"]).copy()
        df["lag_1"] = df.groupby("air_store_id")["visitors"].shift(0)  # reads t!
        return df

    original = fb.add_historical_features
    fb.add_historical_features = leaky_build
    try:
        with pytest.raises(fb.FeatureValidationError, match="reads the target day"):
            fb.assert_no_window_overlap(visits_two_stores)
    finally:
        fb.add_historical_features = original


def test_calendar_features_skipped_without_holiday_column(visits_two_stores):
    from diq_model.features.build import add_calendar_features

    df = visits_two_stores.copy()
    df["holiday_flg"] = 0
    out_with = add_calendar_features(df)
    out_without = add_calendar_features(df.drop(columns=["holiday_flg"]))
    for col in ("is_holiday", "day_before_holiday", "day_after_holiday"):
        assert col in out_with.columns
        assert col not in out_without.columns


# ------------------------------------------------------------- policy
def test_historical_average_requires_lookback(sim_df, visits):
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    with pytest.raises(ValueError, match="lookback_days"):
        simulate_policies(
            sim_df, visits, [Policy(PolicyKind.historical_average)]
        )


def test_historical_average_short_history_degrades(sim_df):
    """A young store (< 7 history days) must not crash the simulation: it
    degrades to whatever history exists, with a visible warning."""
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    tiny_history = pd.DataFrame(
        {
            "air_store_id": ["s"] * 5,
            "visit_date": pd.date_range("2016-01-01", periods=5),
            "visitors": [10, 11, 12, 13, 14],
        }
    )
    with pytest.warns(RuntimeWarning, match="history days"):
        (result,) = simulate_policies(
            sim_df,
            tiny_history,
            [Policy(PolicyKind.historical_average, lookback_days=28)],
        )
    assert result.prepared_total == pytest.approx(float(np.mean([10, 11, 12, 13, 14])) * 5)


def test_historical_average_no_history_falls_back_to_global_mean(sim_df):
    """A store with NO pre-window history gets the global pre-window mean
    (with a warning) - it must still be comparable on the same rows."""
    from diq_model.policy.simulate import Policy, PolicyKind, simulate_policies

    other_store_history = pd.DataFrame(
        {
            "air_store_id": ["s1"] * 10,
            "visit_date": pd.date_range("2016-01-01", periods=10),
            "visitors": [100.0] * 10,
        }
    )
    with pytest.warns(RuntimeWarning, match="no pre-window history"):
        (result,) = simulate_policies(
            sim_df,  # sim_df's store 's' has no rows in this history
            other_store_history,
            [Policy(PolicyKind.historical_average, lookback_days=28)],
        )
    assert result.prepared_total == pytest.approx(100.0 * 5)


def test_naive_young_store_gets_global_mean_fallback(train_test):
    """Naive must return a number for EVERY test row, even for a store that
    opened inside the evaluation window (real Recruit data has these) - a
    NaN prediction would poison every metric."""
    train, test = train_test
    from diq_model.models.baselines import GlobalAverageBaseline, NaiveBaseline

    young_store = test[test["air_store_id"] == "air_a"].head(5).copy()
    young_store["air_store_id"] = "air_new"  # absent from train entirely

    for cls in (NaiveBaseline, GlobalAverageBaseline):
        model = cls().fit(train)
        preds = model.predict(young_store)
        assert preds.notna().all(), f"{cls.__name__} emitted NaN predictions"


def test_unknown_policy_kind_raises(sim_df, visits):
    from diq_model.policy.simulate import Policy, simulate_policies

    with pytest.raises(ValueError, match="history, not forecasts"):
        simulate_policies(
            sim_df, visits, [Policy(kind="bogus", quantile=0.8)], upper_col="upper"
        )


# ------------------------------------------------------------ conformal
def test_conformal_shape_mismatch_raises():
    from diq_model.uncertainty.conformal import AdaptiveConformal

    with pytest.raises(ValueError, match="shape mismatch"):
        AdaptiveConformal(min_history=1).calibrate_online([1.0, 2.0], [1.0])


def test_conformal_coverage_helpers_nan_safe():
    from diq_model.uncertainty.conformal import AdaptiveConformal

    y = np.array([10.0, 20.0])
    lo = np.array([np.nan, np.nan])
    hi = np.array([np.nan, np.nan])
    assert np.isnan(AdaptiveConformal.empirical_coverage(y, lo, hi))
    assert np.isnan(AdaptiveConformal.mean_interval_width(lo, hi))


# ------------------------------------------------------------------ CLI
def test_cli_audit_rejects_missing_dir(tmp_path, capsys):
    from diq_model.train import main

    # Function-style main: missing data dir -> clean exit code 1, no traceback.
    assert main(["audit", "--data-root", str(tmp_path / "missing")]) == 1
    assert "error:" in capsys.readouterr().err


def test_model_predict_before_fit_raises():
    from diq_model.models.catboost_model import CatBoostModel
    from diq_model.models.lightgbm_model import LightGBMModel

    for cls in (LightGBMModel, CatBoostModel):
        with pytest.raises(RuntimeError, match="not fitted"):
            cls().predict(pd.DataFrame())


def test_cli_requires_command():
    from diq_model.train import main

    with pytest.raises(SystemExit):
        main([])


def test_version_import():
    import diq_model

    assert diq_model.__version__ == "0.1.0"


# ---------------------------------------------- remaining branch coverage
def test_shap_rejects_empty_feature_columns(visits_two_stores):
    pytest.importorskip("shap")
    from diq_model.explain.shap_values import explain_forecasts

    with pytest.raises(ValueError, match="feature_columns"):
        explain_forecasts(object(), visits_two_stores, feature_columns=[])


def test_same_weekday_stat_skips_absent_weekdays(visits_two_stores):
    """A store with no Friday rows: the dow loop's skip branch must hold and
    remaining weekdays still get stats (NaN early, values once 2+ occurrences
    of each present weekday exist)."""
    from diq_model.features.build import add_historical_features

    df = visits_two_stores[visits_two_stores["visit_date"].dt.dayofweek != 4]
    out = add_historical_features(df)
    assert "dow_mean_8w" in out.columns
    assert out["dow_mean_8w"].notna().any(), "present weekdays must eventually get stats"


def test_audit_recruit_root_convenience(tmp_path, visits_df, stores_df, calendar_df, reservations_df):
    from diq_model.data.audit import audit_recruit_root

    clean = (
        visits_df[visits_df["air_store_id"] != "air_unknown"]
        .query("visitors >= 0")
        .drop_duplicates(subset=["air_store_id", "visit_date"], keep="first")
    )
    clean.to_csv(tmp_path / "air_visit_data.csv", index=False)
    stores_df.to_csv(tmp_path / "air_store_info.csv", index=False)
    calendar_df.to_csv(tmp_path / "date_info.csv", index=False)
    reservations_df.to_csv(tmp_path / "air_reserve.csv", index=False)
    report = audit_recruit_root(tmp_path)
    assert "duplicate_store_days" in report.checks
