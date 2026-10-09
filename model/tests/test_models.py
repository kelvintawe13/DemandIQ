"""Model wrapper tests: baselines and LightGBM (skipped if lgbm missing)."""

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


def test_dow_baseline_interface(visits_two_stores):
    from diq_model.models.baselines import DayOfWeekBaseline

    split = pd.Timestamp("2016-02-20")
    train = visits_two_stores[visits_two_stores["visit_date"] < split]
    test = visits_two_stores[visits_two_stores["visit_date"] >= split]

    model = DayOfWeekBaseline().fit(train)
    preds = model.predict(test)
    assert len(preds) == len(test)
    assert preds.notna().all()
    metrics = model.evaluate(test)
    assert metrics["mae"] >= 0


def test_dow_baseline_fallback_ladder(visits_two_stores):
    """Unseen store/weekday falls back to store mean, then global mean."""
    from diq_model.models.baselines import DayOfWeekBaseline

    train = visits_two_stores[visits_two_stores["air_store_id"] == "air_a"]
    model = DayOfWeekBaseline().fit(train)

    # air_b was never seen in training.
    unseen = visits_two_stores[visits_two_stores["air_store_id"] == "air_b"].iloc[[0]]
    preds = model.predict(unseen)
    assert preds.notna().iloc[0]
    # Falls back to the GLOBAL mean (air_b has no store mean in train).
    assert preds.iloc[0] == pytest.approx(train["visitors"].mean())


def test_lightgbm_fit_predict(visits_two_stores):
    pytest.importorskip("lightgbm")
    from diq_model.features.build import build_features
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = build_features(visits_two_stores)
    df = fframe.frame
    split = pd.Timestamp("2016-02-20")
    train, test = df[df["visit_date"] < split], df[df["visit_date"] >= split]

    model = (
        LightGBMModel(num_boost_round=50)
        .fit(train, feature_columns=fframe.feature_columns, valid=test)
    )
    preds = model.predict(test)
    assert len(preds) == len(test)
    assert (preds >= 0).all(), "log1p inversion + clip must keep predictions non-negative"


def test_lightgbm_version_mismatch_is_hard_error(visits_two_stores, monkeypatch):
    pytest.importorskip("lightgbm")
    from diq_model.features import build as fb
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = fb.build_features(visits_two_stores)
    df = fframe.frame
    model = LightGBMModel(num_boost_round=5).fit(df, feature_columns=fframe.feature_columns)

    # Pretend the installed definitions moved on (a new feature-set version).
    monkeypatch.setattr(fb, "FEATURE_SET_VERSION", "fs-9999.99.9")
    with pytest.raises(RuntimeError, match="feature-set mismatch"):
        model.predict(df)


def test_lightgbm_save_load_roundtrip(tmp_path, visits_two_stores):
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.features.build import build_features
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = build_features(visits_two_stores)
    df = fframe.frame
    model = LightGBMModel(num_boost_round=10).fit(df, feature_columns=fframe.feature_columns)

    store = CheckpointStore(tmp_path / "ckpt")
    model.save(store)
    loaded = LightGBMModel.load(store)

    preds_a = model.predict(df)
    preds_b = loaded.predict(df)
    assert np.allclose(preds_a, preds_b)
