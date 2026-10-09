"""Real serving-path tests: bundle loading, booster point, Mondrian interval.

The stub path is covered by test_datasets_auth / forecast tests. Here we
verify that when MODEL_BUNDLE_PATH points at a valid serving bundle, the
service serves REAL booster predictions and intervals derived from the
exported Mondrian ACI state — and degrades to the stub when the feature
contract cannot be honoured.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from app.services.feature_build import FeatureVector
from app.services.forecasting import conformal as conf_mod
from app.services.forecasting import loader as loader_mod
from app.services.forecasting.bundle import (
    BUNDLE_FORMAT,
    BUNDLE_VERSION,
    DEFAULT_BUNDLE_CANDIDATES,
    clear_bundle_cache,
    get_bundle,
)

pytest.importorskip("lightgbm")
pytest.importorskip("pandas")
pytest.importorskip("numpy")

FEATURE_COLUMNS = ["lag_1", "lag_7", "roll_mean_7", "dow", "month"]


def _tiny_bundle(tmp_path):
    import joblib
    import lightgbm as lgb
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(3)
    n = 400
    X = pd.DataFrame(
        {
            "lag_1": rng.poisson(20, n).astype(float),
            "lag_7": rng.poisson(20, n).astype(float),
            "roll_mean_7": rng.poisson(20, n).astype(float),
            "dow": rng.integers(0, 7, n).astype(float),
            "month": rng.integers(1, 13, n).astype(float),
        }
    )
    y = np.log1p(0.6 * X["lag_1"] + 0.4 * X["roll_mean_7"] + rng.normal(0, 1, n))
    booster = lgb.train(
        {"objective": "regression", "verbosity": -1, "seed": 7},
        lgb.Dataset(X, label=y.to_numpy()),
        num_boost_round=30,
    )
    residuals = [abs(float(v)) for v in rng.normal(0, 6, 80)]
    bundle = {
        "format": BUNDLE_FORMAT,
        "bundle_version": BUNDLE_VERSION,
        "created_at": "2026-09-20T00:00:00Z",
        "run_dir": str(tmp_path),
        "model": {
            "algorithm": "lightgbm",
            "version": "lightgbm-test-it30",
            "booster": booster,
            "best_iteration": 30,
            "feature_columns": FEATURE_COLUMNS,
            "feature_set_version": "fs-2026.09.2",
            "log_target": True,
        },
        # Point-accuracy block written by scripts/export_serving_bundle.py.
        "metrics": {
            "mae": 6.702,
            "rmse": 11.346,
            "rmsle": 0.4934,
            "mase": 0.634,
            "bias": -1.668,
            "r2": 0.4187,
            "baseline_name": "weekday_mean",
            "baseline_mae": 7.667,
            "mae_lift_vs_baseline": 0.1262,
            "test_rows": 57866,
            "best_iteration": 30,
            "feature_set_version": "fs-2026.09.2",
            "train_start": "2016-01-01",
            "train_end": "2017-01-31",
            "test_start": "2017-02-01",
            "test_end": "2017-04-22",
            "hyperparameters": {"learning_rate": 0.05, "num_leaves": 63, "num_boost_round": 2000},
        },
        # Challenger the same run trained and scored on the same split.
        "comparison": {
            "catboost": {
                "algorithm": "catboost",
                "version": "catboost-fs-2026.09.2-it868",
                "status": "candidate",
                "role": "fallback_primary",
                "metrics": {
                    "mae": 6.69,
                    "rmse": 11.371,
                    "rmsle": 0.4937,
                    "mase": 0.6329,
                    "bias": -1.719,
                    "r2": 0.4182,
                    "test_rows": 57866,
                    "best_iteration": 868,
                    "feature_set_version": "fs-2026.09.2",
                    "train_start": "2016-01-01",
                    "train_end": "2017-01-31",
                    "test_start": "2017-02-01",
                    "test_end": "2017-04-22",
                    "mae_delta_vs_production": -0.012,
                    "hyperparameters": {"depth": 6, "num_boost_round": 2000},
                },
            }
        },
        "conformal": {
            "target": 0.90,
            "gamma": 0.02,
            "window": 200,
            "min_history": 20,
            "min_bucket": 30,
            "bins": [[0.0, 15.0], [15.0, 30.0], [30.0, 60.0], [60.0, None]],
            "achieved_coverage": 0.9,
            "achieved_width": 12.0,
            "global": {"alpha": 0.1, "residuals": list(residuals)},
            "strata": [
                {"alpha": 0.1, "residuals": list(residuals), "scored": 10} for _ in range(4)
            ],
        },
        "provenance": {},
    }
    path = tmp_path / "serving_bundle.joblib"
    joblib.dump(bundle, path)
    return path, bundle


@pytest.fixture
def bundle_env(monkeypatch, tmp_path):
    path, bundle = _tiny_bundle(tmp_path)
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "model_bundle_path", str(path))
    clear_bundle_cache()
    yield bundle
    monkeypatch.setattr(settings, "model_bundle_path", None)
    clear_bundle_cache()


def test_get_bundle_roundtrip(bundle_env):
    bundle = get_bundle()
    assert bundle is not None
    assert bundle["model"]["algorithm"] == "lightgbm"
    assert bundle["conformal"]["target"] == 0.90


def test_serving_status_reports_the_trained_bundle(bundle_env):
    """A configured, loadable bundle is `trained` and passes the boot check."""
    from app.services.forecasting.bundle import (
        assert_serving_configuration,
        serving_status,
    )

    status = serving_status()
    assert status["mode"] == "trained"
    assert status["configured"] is True
    assert status["algorithm"] == "lightgbm"
    assert status["version"] == "lightgbm-test-it30"
    # Must not raise: the operator asked for this bundle and got it.
    assert_serving_configuration()


def test_get_bundle_missing_path_probes_defaults_then_none(monkeypatch, tmp_path):
    """A missing explicit path must fall back to default discovery, then None."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "model_bundle_path", str(tmp_path / "nope.joblib"))
    # Isolate discovery from the developer machine's real training artifact.
    monkeypatch.setattr("app.services.forecasting.bundle._DEFAULT_BUNDLE_CANDIDATES", ())
    clear_bundle_cache()
    assert get_bundle() is None


def test_get_bundle_default_discovery_finds_run_artifact(monkeypatch):
    """With no MODEL_BUNDLE_PATH, the standard run export is served live.

    The suite disables discovery by default (tests/conftest.py) so that a
    developer with a trained artifact and a CI runner without one behave the
    same. This test is the one that wants the real probe, so it puts the
    candidate list back.
    """
    import os

    import pytest

    from app.config import get_settings
    from app.services.forecasting import bundle as bundle_mod

    artifact = "../model/artifacts/runs/latest/serving_bundle.joblib"
    if not os.path.exists(artifact):
        pytest.skip("training artifact not present")
    monkeypatch.setattr(bundle_mod, "_DEFAULT_BUNDLE_CANDIDATES", DEFAULT_BUNDLE_CANDIDATES)
    settings = get_settings()
    monkeypatch.setattr(settings, "model_bundle_path", None)
    clear_bundle_cache()
    bundle = get_bundle()
    assert bundle is not None
    assert bundle["model"]["feature_set_version"].startswith("fs-")
    clear_bundle_cache()


class _FakeDb:
    """Minimal Session double: records inserts and answers the two lookups."""

    def __init__(self) -> None:
        self.added: list = []
        self.flushes = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def scalar(self, stmt: object) -> object | None:  # type: ignore[no-untyped-def]
        # Both queries select every column, so tell them apart by the WHERE
        # bind: the production lookup filters on status, the sync on version.
        text = str(stmt)
        if ":status_" in text:
            return next((r for r in self.added if getattr(r, "status", None) == "production"), None)
        return None  # per-version lookup: nothing registered yet


def test_bundle_registers_challenger_beside_champion(bundle_env):
    """A run's challenger lands in the registry next to the champion."""
    db = _FakeDb()
    loader_mod.ensure_bundle_models(db, uuid.uuid4())

    by_version = {r.version: r for r in db.added}
    assert len(db.added) == 2, [r.version for r in db.added]
    champion = by_version["lightgbm-test-it30"]
    challenger = by_version["catboost-fs-2026.09.2-it868"]

    assert champion.status == "production"
    assert float(champion.mae) == pytest.approx(6.702)
    # The fallback primary is a candidate: promoting it stays a governance call.
    assert challenger.status == "candidate"
    assert challenger.promoted_at is None
    assert challenger.algorithm == "catboost"
    assert float(challenger.mae) == pytest.approx(6.69)
    assert float(challenger.rmse) == pytest.approx(11.371)
    assert challenger.hyperparameters["depth"] == 6

    # The delta the training run measured travels as evaluation detail.
    ev = loader_mod.bundle_evaluation(challenger)
    assert ev["role"] == "fallback_primary"
    assert ev["mae_delta_vs_production"] == pytest.approx(-0.012)
    # The supplementary §3.15 statistic travels with the error metrics.
    assert ev["r2"] == pytest.approx(0.4182)
    assert loader_mod.bundle_evaluation(champion)["role"] == "champion"
    # An unrelated version gets no detail at all (never a guessed number).
    from app.models.forecast import ModelVersion

    stranger = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="xgboost",
        version="xgboost-v9",
        status="candidate",
        feature_set_version="fs-2026.09.2",
    )
    assert loader_mod.bundle_evaluation(stranger) is None


def test_production_model_publishes_bundle_metrics(bundle_env):
    """The registry row a training run creates carries its measured figures."""
    bundle = get_bundle()
    db = _FakeDb()

    row = loader_mod.get_production_model(db, uuid.uuid4())

    assert row.algorithm == "lightgbm"
    assert row.status == "production"
    assert row.feature_set_version == "fs-2026.09.2"
    assert row.artifact_uri == bundle["run_dir"]
    # The details that used to arrive as NULL (blank MAE/RMSE/RMSLE in the UI).
    assert float(row.mae) == pytest.approx(6.702)
    assert float(row.rmse) == pytest.approx(11.346)
    assert float(row.rmsle) == pytest.approx(0.4934)
    assert row.train_start == date(2016, 1, 1)
    assert row.train_end == date(2017, 1, 31)
    assert row.test_start == date(2017, 2, 1)
    assert row.test_end == date(2017, 4, 22)
    assert row.hyperparameters["num_leaves"] == 63


def test_production_row_backfills_missing_metrics(bundle_env):
    """A row created before the bundle carried metrics fills in on next read."""
    from app.models.forecast import ModelVersion

    row = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="lightgbm",
        version="lightgbm-fs-2026.09.2-it200",
        status="production",
        feature_set_version="fs-2026.09.2",
        mae=None,
        rmse=None,
        rmsle=None,
    )
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.scalar.return_value = row

    out = loader_mod.get_production_model(db, row.workspace_id)

    assert out is row  # the existing production row is reused, not duplicated
    db.add.assert_not_called()
    assert float(out.rmse) == pytest.approx(11.346)
    assert out.test_end == date(2017, 4, 22)
    assert out.hyperparameters["learning_rate"] == pytest.approx(0.05)


def test_bundle_feature_importance_comes_from_the_booster(bundle_env):
    """Importance is the trained booster's gain importance, not a static list."""
    from app.models.forecast import ModelVersion

    champion = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="lightgbm",
        version="lightgbm-test-it30",
        status="production",
        feature_set_version="fs-2026.09.2",
    )
    imp = loader_mod.bundle_feature_importance(champion)
    assert imp is not None
    assert len(imp) == len(FEATURE_COLUMNS)
    assert imp[0]["rank"] == 1
    assert sum(e["importance"] for e in imp) == pytest.approx(1.0, abs=1e-3)

    # A challenger carries no booster in the bundle, so callers fall back.
    challenger = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="catboost",
        version="catboost-fs-2026.09.2-it868",
        status="candidate",
        feature_set_version="fs-2026.09.2",
    )
    assert loader_mod.bundle_feature_importance(challenger) is None
    assert (
        loader_mod.bundle_feature_importance(
            ModelVersion(
                id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                algorithm="xgboost",
                version="unrelated-v9",
                status="candidate",
                feature_set_version="fs-2026.09.2",
            )
        )
        is None
    )


def test_bundle_calibration_comes_from_conformal_state(bundle_env):
    """Calibration reads the run's measured conformal coverage/width."""
    from app.models.forecast import ModelVersion

    champion = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="lightgbm",
        version="lightgbm-test-it30",
        status="production",
        feature_set_version="fs-2026.09.2",
    )
    cal = loader_mod.bundle_calibration(champion)
    assert cal is not None
    assert cal["target_coverage"] == pytest.approx(90.0)
    assert cal["empirical_coverage"] == pytest.approx(90.0)  # 0.90 achieved
    assert cal["mean_interval_width"] == pytest.approx(12.0)
    assert cal["method"] == "adaptive_conformal_mondrian"

    # The conformal state is the champion's; a challenger gets nothing.
    challenger = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="catboost",
        version="catboost-fs-2026.09.2-it868",
        status="candidate",
        feature_set_version="fs-2026.09.2",
    )
    assert loader_mod.bundle_calibration(challenger) is None


def test_bundle_calibration_carries_coverage_level_sensitivity(monkeypatch, bundle_env):
    """Section 3.16 sensitivity is passed through as percentages, or omitted."""
    from app.models.forecast import ModelVersion

    # `get_bundle()` reloads from disk, so serve the (mutable) fixture directly.
    monkeypatch.setattr(loader_mod, "get_bundle", lambda: bundle_env)

    champion = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        algorithm="lightgbm",
        version="lightgbm-test-it30",
        status="production",
        feature_set_version="fs-2026.09.2",
    )

    # Not measured on this run -> the key is absent, not an empty/zero stub.
    assert "coverage_sensitivity" not in loader_mod.bundle_calibration(champion)

    # The training run recorded 80% and 95% re-runs of the same online loop.
    bundle_env["conformal"]["coverage_sensitivity"] = {
        "0.80": {
            "target_coverage": 0.8,
            "empirical_coverage": 0.7893,
            "mean_interval_width": 17.66,
        },
        "0.95": {
            "target_coverage": 0.95,
            "empirical_coverage": 0.9482,
            "mean_interval_width": 29.14,
        },
    }

    cal = loader_mod.bundle_calibration(champion)
    assert cal is not None
    # Converted once, to the same percent scale as target_coverage/empirical_coverage.
    assert cal["coverage_sensitivity"]["0.80"]["target_coverage"] == pytest.approx(80.0)
    assert cal["coverage_sensitivity"]["0.80"]["empirical_coverage"] == pytest.approx(78.93)
    assert cal["coverage_sensitivity"]["0.95"]["mean_interval_width"] == pytest.approx(29.14)

    # A malformed block is dropped rather than crashing the endpoint.
    bundle_env["conformal"]["coverage_sensitivity"] = {"0.80": "not-a-block"}
    assert loader_mod.bundle_calibration(champion)["coverage_sensitivity"] == {}


def test_point_from_bundle_matches_booster(bundle_env):
    import numpy as np
    import pandas as pd

    bundle = get_bundle()
    features = {"lag_1": 25.0, "lag_7": 22.0, "roll_mean_7": 23.5, "dow": 4, "month": 3}
    point = loader_mod._point_from_bundle(bundle, features)
    X = pd.DataFrame([{c: features[c] for c in FEATURE_COLUMNS}], columns=FEATURE_COLUMNS).astype(
        "float64"
    )
    expected = float(np.expm1(bundle["model"]["booster"].predict(X)[0]))
    assert point == pytest.approx(max(0.0, expected), abs=1e-2)


def test_point_from_bundle_returns_none_on_contract_miss(bundle_env):
    bundle = get_bundle()
    # Missing nearly every column the booster requires.
    assert loader_mod._point_from_bundle(bundle, {"dow": 4}) is None


def test_half_width_from_state_matches_quantile_rule(bundle_env):
    import numpy as np

    conf = get_bundle()["conformal"]
    half = conf_mod._half_width_from_state(conf, 40.0)  # lands in 30-60 stratum
    stratum = conf["strata"][2]
    residuals = np.asarray(stratum["residuals"][-conf["window"] :])
    alpha = min(max(stratum["alpha"], 1e-6), 1 - 1e-6)
    q = float(np.quantile(residuals, 1 - alpha, method="higher"))
    expected = q * (1 + 1 / len(residuals))
    assert half == pytest.approx(expected)


def test_build_interval_serves_mondrian_state(bundle_env):
    conf = get_bundle()["conformal"]
    half = conf_mod._half_width_from_state(conf, 40.0)
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.scalar.return_value = None
    out = conf_mod.build_interval(db, model_version_id=uuid.uuid4(), point=40.0)
    assert out.coverage_target == 0.90
    assert out.step_size == conf["gamma"]
    assert out.lower == pytest.approx(max(0.0, 40.0 - half), abs=0.011)
    assert out.upper == pytest.approx(40.0 + half, abs=0.011)


def test_predict_point_serves_bundle(bundle_env, monkeypatch):
    import numpy as np
    import pandas as pd

    bundle = get_bundle()
    ws, loc = uuid.uuid4(), uuid.uuid4()
    target = date(2026, 9, 21)
    as_of = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)

    features = {
        "lag_1": 25.0,
        "lag_7": 22.0,
        "lag_14": 24.0,
        "roll_mean_7": 23.5,
        "roll_mean_28": 22.8,
        "roll_std_28": 4.1,
        "roll_max_7": 31.0,
        "roll_max_28": 33.0,
        "roll_p90_28": 29.0,
        "dow_mean_8w": 24.0,
        "dow_std_8w": 3.2,
        "trend_7_28": 1.03,
        "dow": 0,
        "is_weekend": 0,
        "month": 9,
        "week_of_year": 38,
        "is_holiday": 0,
        "day_before_holiday": 0,
        "day_after_holiday": 0,
        "is_weekend_or_holiday": 0,
        "is_weekend_holiday": 0,
        "is_month_end": 0,
        "days_to_month_end": 9,
        "rsv_visitors": None,
        "rsv_count": None,
        "rsv_visitors_ratio": None,
    }
    fv = FeatureVector(
        location_id=loc, target_date=target, as_of=as_of, features=features, history_days=60
    )
    monkeypatch.setattr(loader_mod, "build_model_features", lambda *a, **k: fv)

    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    db.scalar.return_value = None  # no production model row yet

    res = loader_mod.predict_point(
        db, workspace_id=ws, location_id=loc, target_date=target, as_of=as_of
    )
    assert res.fallback is False
    assert res.artifact.algorithm == "lightgbm"
    assert res.feature_set_version == "fs-2026.09.2"

    cols = bundle["model"]["feature_columns"]
    X = pd.DataFrame([{c: features[c] for c in cols}], columns=cols).astype("float64")
    expected = float(np.clip(np.expm1(bundle["model"]["booster"].predict(X)[0]), 0, None))
    assert res.point == pytest.approx(round(expected, 2), abs=0.01)


def test_predict_point_falls_back_on_contract_miss(bundle_env, monkeypatch):
    ws, loc = uuid.uuid4(), uuid.uuid4()
    target = date(2026, 9, 21)
    as_of = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)

    fv = FeatureVector(
        location_id=loc,
        target_date=target,
        as_of=as_of,
        features={"roll_mean_7": 20.0, "roll_mean_28": 19.0, "same_weekday_mean": 21.0},
        history_days=60,
    )
    monkeypatch.setattr(loader_mod, "build_model_features", lambda *a, **k: fv)

    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    db.scalar.return_value = None

    res = loader_mod.predict_point(
        db, workspace_id=ws, location_id=loc, target_date=target, as_of=as_of
    )
    # Contract miss -> stub serves; point derives from roll_mean_7 + jitter.
    assert res.artifact.algorithm == "historical_avg"
    assert 19.0 <= res.point <= 21.5
