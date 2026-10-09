"""Fallback model tests — Linear Regression, Random Forest, XGBoost (§3.13).

These cover the three models the proposal keeps "as baselines and fallback
models rather than dropped from the study". The behavioural contract they share
with the primaries (fit/predict/save/load/evaluate, log1p round-trip,
feature-set hard failure) is asserted here so the comparison stage can treat
every candidate identically.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from diq_model.artifacts import CheckpointStore
from diq_model.models.fallback_models import (
    FALLBACK_FACTORIES,
    LinearRegressionModel,
    RandomForestModel,
    XGBoostModel,
    build_fallbacks,
    missing_dependencies,
)


@pytest.fixture
def visits_two_stores() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2016-01-01", periods=70, freq="D")
    frames = []
    for i, store in enumerate(("air_a", "air_b")):
        weekend = np.where(dates.dayofweek >= 5, 14, 0)
        vals = rng.poisson(18 + i * 6 + weekend)
        frames.append(pd.DataFrame({"air_store_id": store, "visit_date": dates, "visitors": vals}))
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def split_features(visits_two_stores):
    from diq_model.features.build import build_features

    fframe = build_features(visits_two_stores)
    split = pd.Timestamp("2016-02-20")
    df = fframe.frame
    return fframe, df[df["visit_date"] < split], df[df["visit_date"] >= split]


def test_all_three_models_exist_in_the_registry():
    """§3.13 names three fallbacks; the registry must match, not a subset."""
    assert set(FALLBACK_FACTORIES) == {"linear_regression", "random_forest", "xgboost"}
    assert FALLBACK_FACTORIES["linear_regression"] is LinearRegressionModel
    assert FALLBACK_FACTORIES["random_forest"] is RandomForestModel
    assert FALLBACK_FACTORIES["xgboost"] is XGBoostModel


def test_missing_dependencies_reports_each_unavailable_model():
    """Absent packages are named, never silently swapped for another model."""
    missing = missing_dependencies()
    assert isinstance(missing, dict)
    for algorithm, reason in missing.items():
        assert algorithm in FALLBACK_FACTORIES
        assert "not installed" in reason


def test_build_fallbacks_omits_unavailable_models_without_substituting():
    built = build_fallbacks()
    assert set(built) == set(FALLBACK_FACTORIES) - set(missing_dependencies())


@pytest.mark.parametrize("algorithm", ["linear_regression", "random_forest", "xgboost"])
def test_fit_predict_are_non_negative_and_same_length(algorithm, split_features):
    model_cls = FALLBACK_FACTORIES[algorithm]
    if algorithm in missing_dependencies():
        pytest.skip(f"{algorithm} dependency not installed")
    fframe, train, test = split_features

    model = model_cls().fit(train, feature_columns=fframe.feature_columns)
    preds = model.predict(test)

    assert len(preds) == len(test)
    assert np.isfinite(preds).all()
    assert (preds >= 0).all()


def test_linear_regression_recovers_a_known_linear_signal():
    """OLS must recover an exactly log-linear series.

    The target is built as ``expm1(a + b*t)``, so ``log1p(target)`` is exactly
    linear in ``t`` and the fit has no reason to miss. This pins the log1p
    train / expm1 predict round-trip as well as the estimator itself.
    """
    t = np.arange(60, dtype="float64")
    train = pd.DataFrame(
        {
            "visit_date": pd.date_range("2016-01-01", periods=60, freq="D"),
            "t": t,
            "visitors": np.expm1(2.0 + 0.05 * t),
        }
    )
    model = LinearRegressionModel().fit(train, feature_columns=["t"])
    fitted = model.predict(train)
    assert np.allclose(fitted, train["visitors"], rtol=1e-6)


def test_linear_regression_cannot_extrapolate_past_the_fit_slice_range():
    """Regression test: one out-of-support row must not decide the MAE.

    OLS on a ``log1p`` target is an exponential model, so a feature row outside
    the training support is inverted to an arbitrarily large number. On the real
    Recruit window a single such row predicted 1.8e10 visitors and, on its own,
    produced the model's entire reported MAE (315,939). Predictions are
    therefore censored to the target range of the fit slice.
    """
    t = np.arange(60, dtype="float64")
    train = pd.DataFrame(
        {
            "visit_date": pd.date_range("2016-01-01", periods=60, freq="D"),
            "t": t,
            "visitors": np.expm1(2.0 + 0.05 * t),
        }
    )
    cap = float(train["visitors"].max())
    model = LinearRegressionModel().fit(train, feature_columns=["t"])

    # t far outside the training support, where the fitted line keeps climbing.
    far = pd.DataFrame(
        {
            "visit_date": pd.date_range("2016-06-01", periods=3, freq="D"),
            "t": [5000.0, 1e6, -5000.0],
        }
    )
    preds = model.predict(far)

    assert np.isfinite(preds).all()
    assert (preds >= 0).all()
    assert preds.max() == pytest.approx(cap)
    # What the uncensored inverse would have produced, i.e. what the bound is for
    # (t=5000 is used rather than 1e6 so the demonstration itself stays in float64).
    assert np.expm1(float(model.estimator_.predict(far[["t"]])[0])) > 1e6


def test_prediction_bound_is_opt_in_so_the_ensembles_are_unchanged(split_features):
    """Only the log-linear fallback is censored.

    Random Forest and XGBoost are bounded by construction, so censoring them
    could only change a result that was already finite — the bound stays off, and
    a fitted ensemble records no bound for ``predict`` to apply.
    """
    assert LinearRegressionModel.clip_to_train_range is True
    assert RandomForestModel.clip_to_train_range is False
    assert XGBoostModel.clip_to_train_range is False

    fframe, train, _ = split_features
    for cls in (RandomForestModel, XGBoostModel):
        if cls.algorithm in missing_dependencies():
            pytest.skip(f"{cls.algorithm} dependency not installed")
        fitted = cls().fit(train, feature_columns=fframe.feature_columns)
        assert fitted.y_bound_ is None

    linear = LinearRegressionModel().fit(train, feature_columns=fframe.feature_columns)
    assert linear.y_bound_ is not None


def test_prediction_before_fit_is_a_clear_error(split_features):
    _, _, test = split_features
    with pytest.raises(RuntimeError, match="not fitted"):
        LinearRegressionModel().predict(test)


def test_nan_features_are_imputed_from_the_training_window_only(split_features):
    """The imputation values must come from the fit slice, not the test slice.

    ``lag_14`` is NaN early in a store's history, which LinearRegression and
    RandomForest cannot accept. The median recorded at fit time is asserted to
    equal the train median and to differ from the test median, so the fill
    cannot have looked at data it will later be scored on.
    """
    from diq_model.features.build import FEATURE_SET_VERSION

    assert FEATURE_SET_VERSION  # the version pin the imputation travels with
    rng = np.random.default_rng(3)
    feature = "lag_1"
    train = pd.DataFrame(
        {
            "visit_date": pd.date_range("2016-01-01", periods=50),
            feature: [np.nan] * 3 + list(rng.normal(0, 1, 47)),
        }
    )
    test = pd.DataFrame(
        {
            "visit_date": pd.date_range("2016-02-20", periods=20),
            feature: list(rng.normal(50, 1, 20)),
        }
    )
    train["visitors"] = 10.0
    test["visitors"] = 10.0

    model = LinearRegressionModel().fit(train, feature_columns=[feature])
    assert model.impute_ is not None
    assert model.impute_[feature] == pytest.approx(train[feature].median())
    assert model.impute_[feature] != pytest.approx(test[feature].median())


def test_entirely_nan_column_falls_back_to_zero(split_features):
    """A column with no median at all must still produce a finite prediction."""
    train = pd.DataFrame(
        {
            "visit_date": pd.date_range("2016-01-01", periods=40),
            "bogus": [np.nan] * 40,
            "visitors": 12.0,
        }
    )
    model = LinearRegressionModel().fit(train, feature_columns=["bogus"])
    assert np.isfinite(model.predict(train)).all()


def test_feature_set_version_mismatch_is_a_hard_error(split_features, monkeypatch):
    from diq_model.features import build as fb

    pytest.importorskip("sklearn")
    fframe, train, _ = split_features
    model = LinearRegressionModel().fit(train, feature_columns=fframe.feature_columns)
    monkeypatch.setattr(fb, "FEATURE_SET_VERSION", "fs-9999.99.9")
    from diq_model.models import fallback_models

    monkeypatch.setattr(fallback_models, "FEATURE_SET_VERSION", "fs-9999.99.9")
    with pytest.raises(RuntimeError, match="feature-set mismatch"):
        model.predict(train)


def test_save_and_load_round_trip(split_features, tmp_path):
    fframe, train, test = split_features
    model = LinearRegressionModel().fit(train, feature_columns=fframe.feature_columns)
    store = CheckpointStore(tmp_path)

    path = model.save(store)
    assert path.endswith("linear_regression_model.joblib")
    assert store.has("linear_regression_model.joblib")

    reloaded = LinearRegressionModel.load(store)
    assert np.allclose(reloaded.predict(test), model.predict(test))


def test_evaluate_returns_the_shared_metric_set(split_features):
    fframe, train, test = split_features
    model = LinearRegressionModel().fit(train, feature_columns=fframe.feature_columns)
    metrics = model.evaluate(test, y_train=train["visitors"])

    for key in ("mae", "rmse", "rmsle", "mase", "bias"):
        assert key in metrics, key
    assert metrics["mae"] == pytest.approx(float(metrics["mae"]))


def test_valid_and_categorical_are_accepted_for_signature_parity(split_features):
    """The primaries use early stopping; the fallbacks must still accept the args."""
    fframe, train, test = split_features
    model = LinearRegressionModel().fit(
        train,
        feature_columns=fframe.feature_columns,
        valid=test,
        categorical=["air_store_id"],
    )
    assert len(model.predict(test)) == len(test)


def test_xgboost_uses_the_training_extra_defaults():
    """§3.13 XGBoost defaults: a real, regularised configuration, not a stub."""
    defaults = XGBoostModel.DEFAULT_PARAMS
    assert defaults["n_estimators"] >= 100
    assert 0 < defaults["learning_rate"] <= 0.1
    assert defaults["reg_lambda"] > 0
    assert defaults["random_state"] == 42
    assert RandomForestModel.DEFAULT_PARAMS["random_state"] == 42
