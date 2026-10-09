"""Integration tests: features + lightgbm stages over the checkpoint store."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def recruit_csvs(tmp_path, visits_df, stores_df, calendar_df, reservations_df):
    """Cleaned synthetic CSVs: hard audit failures removed, subset run proceeds."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    clean = (
        visits_df[visits_df["air_store_id"] != "air_unknown"]
        .query("visitors >= 0")
        .drop_duplicates(subset=["air_store_id", "visit_date"], keep="first")
    )
    clean.to_csv(tmp_path / "air_visit_data.csv", index=False)
    stores_df.to_csv(tmp_path / "air_store_info.csv", index=False)
    calendar_df.to_csv(tmp_path / "date_info.csv", index=False)
    reservations_df.to_csv(tmp_path / "air_reserve.csv", index=False)
    return tmp_path


def test_stage_features_and_lightgbm_end_to_end(recruit_csvs, visits_df, tmp_path):
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    store = CheckpointStore(tmp_path / "ckpt")
    results = run_pipeline(store, recruit_csvs, test_start="2016-02-15", strict=False)

    assert set(results) == {
        "ingest", "baseline", "features", "fallbacks", "lightgbm", "catboost",
        "conformal", "policy", "shap",
    }
    feats = results["features"]
    assert feats["rows"] > 0 and feats["features"] >= 10
    assert feats["feature_set_version"].startswith("fs-")

    # §3.13 fallbacks train on the same frame and produce a ranking table.
    fallbacks = results["fallbacks"]
    assert fallbacks["best_fallback"] in fallbacks["fallbacks"]
    assert fallbacks["best_fallback_mae"] > 0
    for name, row in fallbacks["fallbacks"].items():
        assert {"mae", "rmse", "rmsle", "mase"} <= set(row), name
        assert row["test_rows"] > 0
    # A model that could not be loaded here is reported, not faked.
    assert set(fallbacks["fallbacks"]) & set(fallbacks["skipped"]) == set()
    assert store.has("fallback_table.parquet")

    # The §3.13 comparison must reach the API's registry, not stop at the
    # notebook: the serving bundle carries each fallback's measured MAE.
    import importlib.util
    import joblib

    script = Path(__file__).resolve().parents[1] / "scripts" / "export_serving_bundle.py"
    spec = importlib.util.spec_from_file_location("export_serving_bundle", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    bundle = module.export_bundle(store.root)
    comparison = bundle["comparison"]
    assert comparison["catboost"]["role"] == "fallback_primary"
    for name in fallbacks["fallbacks"]:
        entry = comparison[name]
        assert entry["role"] == "baseline_fallback"
        assert entry["status"] == "candidate"
        assert entry["metrics"]["mae"] == fallbacks["fallbacks"][name]["mae"]
        assert entry["metrics"]["mae_delta_vs_production"] is not None
    # Round-trip: the bundle the API loads still describes all three families.
    assert set(comparison) >= {"catboost", *fallbacks["fallbacks"]}
    assert joblib.load(store.root / "lightgbm_model.joblib") is not None

    lgb = results["lightgbm"]
    assert {"mae", "rmse", "rmsle", "baseline_mae", "best_iteration"} <= set(lgb)
    assert lgb["test_rows"] > 0
    assert store.has("features.parquet")
    assert store.has("lightgbm_predictions.parquet")

    # Section 3.16 asks for sensitivity at coverage levels other than 90%.
    conf = results["conformal"]
    assert conf["target_coverage"] == 0.90
    assert set(conf["coverage_level_sensitivity"]) == {"0.80", "0.95"}
    for level, block in conf["coverage_level_sensitivity"].items():
        assert block["target_coverage"] == float(level)
        assert 0.0 <= block["empirical_coverage"] <= 1.0
        assert block["mean_interval_width"] > 0

    # Section 3.17: the risk-aware policy is simulated alongside the three
    # mandated ones, and its weight is chosen on validation, not asserted.
    from diq_model.train import RISK_WEIGHT_SWEEP

    pol = results["policy"]
    assert pol["risk_weight"] in RISK_WEIGHT_SWEEP
    assert 0.0 < pol["risk_newsvendor_level"] < 1.0
    assert any(k.startswith("risk_aware") for k in pol["policies"])
    verdict = pol["scale_selection"]["verdict"]
    assert verdict in {
        "point_forecast_preferred_under_this_costing",
    } or verdict.startswith(("uncertainty_aware_preferred_at_q", "risk_aware_preferred_at_w"))


def test_stage_sensitivity_ablates_each_feature_group(recruit_csvs, tmp_path):
    """Section 3.25: the ablation must report every group that is populated in
    the frame, and the no-reservations variant must be decided, not assumed."""
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.features.build import FEATURE_GROUPS
    from diq_model.train import run_pipeline, stage_sensitivity

    store = CheckpointStore(tmp_path / "ckpt")
    run_pipeline(store, recruit_csvs, test_start="2016-02-15", strict=False)
    out = stage_sensitivity(store, seed=42, test_start="2016-02-15")

    populated = set(out["feature_group_ablation"])
    assert populated, "the ablation reported no groups"
    assert populated <= set(FEATURE_GROUPS)
    assert out["features_full"] > 0

    for group, info in out["feature_group_ablation"].items():
        assert "dropped_features" in info
        if info.get("defined"):
            # A defined ablation carries a real measurement and, if it helped or
            # hurt, a signed delta - never a placeholder.
            assert info["mae"] > 0
            assert info["features"] == out["features_full"] - info["dropped_features"]
            assert isinstance(info["mae_delta_vs_full"], float)
        else:
            assert info["reason"]

    res = out["reservation_availability"]
    assert "reservation_features_present" in res
    if res["defined"]:
        assert res["with_reservations_mae"] > 0
        assert res["verdict"] in {
            "reservations_are_supplementary",
            "production_model_degrades_without_reservations",
        }
    else:
        assert res["reason"]

    assert out["most_important_group"] in populated
    assert store.has("sensitivity_ablation.parquet")


def test_sensitivity_is_opt_in(recruit_csvs, tmp_path, capsys):
    """The default run stays the nine stages; the ablation is a --with-sensitivity
    stage because it refits the primary model once per subset."""
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    store = CheckpointStore(tmp_path / "ckpt")
    results = run_pipeline(store, recruit_csvs, test_start="2016-02-15", strict=False)
    assert "sensitivity" not in results
    assert "backtest" not in results

    run_pipeline(
        store,
        recruit_csvs,
        test_start="2016-02-15",
        strict=False,
        stages=["sensitivity"],
    )
    assert store.has("sensitivity_ablation.parquet")


def test_stage_resume_skips_lightgbm(recruit_csvs, tmp_path, capsys):
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    store = CheckpointStore(tmp_path / "ckpt")
    run_pipeline(store, recruit_csvs, test_start="2016-02-15", strict=False)
    run_pipeline(store, recruit_csvs, test_start="2016-02-15", strict=False)
    out = capsys.readouterr().out
    assert out.count("[skip]") == 9, "second run must skip all nine stages"
