"""CatBoost + SHAP tests (Sections 3.13, 3.24)."""

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


def test_catboost_fit_predict_and_nonnegativity(visits_two_stores):
    pytest.importorskip("catboost")
    from diq_model.features.build import build_features
    from diq_model.models.catboost_model import CatBoostModel

    fframe = build_features(visits_two_stores)
    df = fframe.frame
    split = pd.Timestamp("2016-02-20")
    train, test = df[df["visit_date"] < split], df[df["visit_date"] >= split]

    model = CatBoostModel(num_boost_round=60).fit(
        train, feature_columns=fframe.feature_columns, valid=test
    )
    preds = model.predict(test)
    assert len(preds) == len(test)
    assert (preds >= 0).all()


def test_catboost_version_mismatch_is_hard_error(visits_two_stores, monkeypatch):
    pytest.importorskip("catboost")
    from diq_model.features import build as fb
    from diq_model.models.catboost_model import CatBoostModel

    fframe = fb.build_features(visits_two_stores)
    model = CatBoostModel(num_boost_round=5).fit(
        fframe.frame, feature_columns=fframe.feature_columns
    )
    monkeypatch.setattr(fb, "FEATURE_SET_VERSION", "fs-9999.99.9")
    with pytest.raises(RuntimeError, match="feature-set mismatch"):
        model.predict(fframe.frame)


def test_catboost_lightgbm_interface_parity(visits_two_stores):
    """Both primaries expose the same fit/predict/save/load contract."""
    pytest.importorskip("catboost")
    from diq_model.features.build import build_features
    from diq_model.models.catboost_model import CatBoostModel
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = build_features(visits_two_stores)
    df = fframe.frame
    for cls in (LightGBMModel, CatBoostModel):
        m = cls(num_boost_round=10).fit(df, feature_columns=fframe.feature_columns)
        preds = m.predict(df)
        assert len(preds) == len(df) and (preds >= 0).all()
        assert hasattr(m, "save") and hasattr(m, "load")


def test_shap_contributions_structure_and_attribution(visits_two_stores):
    pytest.importorskip("shap")
    pytest.importorskip("lightgbm")
    from diq_model.explain.shap_values import (
        ATTRIBUTION_DISCLAIMER,
        explain_forecasts,
    )
    from diq_model.features.build import build_features
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = build_features(visits_two_stores)
    model = LightGBMModel(num_boost_round=30).fit(
        fframe.frame, feature_columns=fframe.feature_columns
    )
    bundle = explain_forecasts(
        model, fframe.frame.head(20), feature_columns=fframe.feature_columns
    )

    # Long format, one block per (row, feature).
    assert set(bundle.frame.columns) == {
        "air_store_id", "visit_date", "feature_name", "feature_value", "shap_impact",
    }
    assert len(bundle.frame) == 20 * len(fframe.feature_columns)

    # Each forecast's contributions must sum to a finite number (the additive
    # property holds in log space; visitor-unit linearisation keeps it stable).
    sums = bundle.frame.groupby(["air_store_id", "visit_date"])["shap_impact"].sum()
    assert np.isfinite(sums).all()

    # Global importance: mean |SHAP| per feature, all non-negative.
    imp = bundle.global_importance()
    assert (imp["mean_abs_shap"] >= 0).all()

    # The disclaimer is mandatory provenance.
    assert bundle.disclaimer == ATTRIBUTION_DISCLAIMER
    assert "not causal" in bundle.disclaimer


def test_shap_works_on_catboost(visits_two_stores):
    """Section 3.24 applies to BOTH primaries: the explainer must resolve the
    CatBoost artefact (``model_``, not LightGBM's ``booster_``) and produce
    the same long-format contract with visitor-unit, additively finite
    contributions."""
    pytest.importorskip("shap")
    pytest.importorskip("catboost")
    from diq_model.explain.shap_values import explain_forecasts
    from diq_model.features.build import build_features
    from diq_model.models.catboost_model import CatBoostModel

    fframe = build_features(visits_two_stores)
    model = CatBoostModel(num_boost_round=30).fit(
        fframe.frame, feature_columns=fframe.feature_columns
    )
    bundle = explain_forecasts(
        model,
        fframe.frame.head(10),
        feature_columns=fframe.feature_columns,
        model_name="catboost",
    )

    assert set(bundle.frame.columns) == {
        "air_store_id", "visit_date", "feature_name", "feature_value", "shap_impact",
    }
    assert len(bundle.frame) == 10 * len(fframe.feature_columns)
    assert bundle.model_name == "catboost"
    assert np.isfinite(bundle.frame["shap_impact"]).all()
    # Per-forecast lookup works for CatBoost too.
    row = bundle.frame.iloc[0]
    one = bundle.for_forecast(row["air_store_id"], row["visit_date"])
    assert len(one) == len(fframe.feature_columns)


def test_shap_per_forecast_lookup(visits_two_stores):
    pytest.importorskip("shap")
    pytest.importorskip("lightgbm")
    from diq_model.explain.shap_values import explain_forecasts
    from diq_model.features.build import build_features
    from diq_model.models.lightgbm_model import LightGBMModel

    fframe = build_features(visits_two_stores)
    model = LightGBMModel(num_boost_round=30).fit(
        fframe.frame, feature_columns=fframe.feature_columns
    )
    bundle = explain_forecasts(
        model, fframe.frame.head(10), feature_columns=fframe.feature_columns
    )
    row = bundle.frame.iloc[0]
    one = bundle.for_forecast(row["air_store_id"], row["visit_date"])
    assert len(one) == len(fframe.feature_columns)
    # Sorted by |impact| descending.
    assert one["shap_impact"].abs().is_monotonic_decreasing
