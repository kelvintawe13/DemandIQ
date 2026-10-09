"""Pipeline tests: ingest + baseline stages against synthetic data.

``--test-start`` is set inside the synthetic window; the audited-scale and
temporal-span gates would reject synthetic data, so the ingest stage is
exercised through its loaders and the audit *report* rather than the gate.
"""

from __future__ import annotations

import pandas as pd
import pytest


def _write_csvs(tmp_path, visits_df, stores_df, calendar_df, reservations_df):
    tmp_path.mkdir(parents=True, exist_ok=True)
    visits_df.to_csv(tmp_path / "air_visit_data.csv", index=False)
    stores_df.to_csv(tmp_path / "air_store_info.csv", index=False)
    calendar_df.to_csv(tmp_path / "date_info.csv", index=False)
    reservations_df.to_csv(tmp_path / "air_reserve.csv", index=False)


def _clean(visits_df):
    return (
        visits_df[visits_df["air_store_id"] != "air_unknown"]
        .query("visitors >= 0")
        .drop_duplicates(subset=["air_store_id", "visit_date"], keep="first")
    )


def test_stage_ingest_writes_parquet_checkpoints(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df
):
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_ingest

    _write_csvs(tmp_path / "data", _clean(visits_df), stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")

    metrics = stage_ingest(store, tmp_path / "data", seed=42, strict=False)
    assert metrics["visit_rows"] == len(_clean(visits_df))
    assert store.has("visits.parquet")
    assert store.has("stores.parquet")


def test_stage_ingest_records_source_checksums(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df
):
    """Provenance (3.27/3.28): the ingest manifest pins the source file bytes."""
    pytest.importorskip("pyarrow")
    import hashlib

    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_ingest

    _write_csvs(tmp_path / "data", _clean(visits_df), stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")

    metrics = stage_ingest(store, tmp_path / "data", seed=42, strict=False)
    sums = metrics["source_sha256"]
    assert set(sums) == {
        "air_visit_data.csv",
        "air_store_info.csv",
        "date_info.csv",
        "air_reserve.csv",
    }
    for name, digest in sums.items():
        assert len(digest) == 64 and int(digest, 16) >= 0  # valid hex sha256
        expected = hashlib.sha256((tmp_path / "data" / name).read_bytes()).hexdigest()
        assert digest == expected


def test_stage_baseline_metrics_are_sane(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df
):
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_baseline, stage_ingest

    _write_csvs(tmp_path / "data", _clean(visits_df), stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")
    stage_ingest(store, tmp_path / "data", seed=42, strict=False)

    metrics = stage_baseline(store, seed=42, test_start="2016-02-15")
    # Full EVALUATION.md gauntlet: all five baselines, each with the metric set.
    assert set(metrics["baselines"]) == {
        "naive",
        "seasonal_naive",
        "historical_average",
        "weekday_mean",
        "global_average",
    }
    for name, row in metrics["baselines"].items():
        assert {"mae", "rmse", "rmsle", "mase", "bias", "test_rows"} <= set(row), name
        assert row["mae"] >= 0.0
    assert metrics["best_baseline"] in metrics["baselines"]
    assert metrics["best_baseline_mae"] >= 0.0
    assert store.has("baseline_predictions.parquet")


def test_baseline_respects_chronological_split(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df
):
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_baseline, stage_ingest

    _write_csvs(tmp_path / "data", _clean(visits_df), stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")
    stage_ingest(store, tmp_path / "data", seed=42, strict=False)

    stage_baseline(store, seed=42, test_start="2016-02-15")
    preds = store.load_dataframe("baseline_predictions.parquet")
    split = pd.Timestamp("2016-02-15")
    assert (preds["visit_date"] >= split).all(), "test predictions must all be from the test window"


def test_run_pipeline_skips_completed_stages(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df, capsys
):
    """Second run resumes: stages print [skip] instead of recomputing."""
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    _write_csvs(tmp_path / "data", _clean(visits_df), stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")

    # Run only ingest (baseline excluded) to keep the resume test focused.
    run_pipeline(store, tmp_path / "data", test_start="2016-02-15", stages=["ingest"], strict=False)
    first = capsys.readouterr().out
    assert "[done]" in first

    run_pipeline(store, tmp_path / "data", test_start="2016-02-15", stages=["ingest"], strict=False)
    second = capsys.readouterr().out
    assert "[skip]" in second and "[done]" not in second


def test_audit_gate_blocks_training(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df, capsys
):
    """Dirty data (duplicate rows etc.) must raise SystemExit, not train."""
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_ingest

    _write_csvs(tmp_path / "data", visits_df, stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")

    with pytest.raises(SystemExit):
        stage_ingest(store, tmp_path / "data", seed=42)


def test_subset_run_waives_reference_checks(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df
):
    """strict=False (subset/smoke run) blocks only on HARD integrity failures."""
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_ingest

    _write_csvs(tmp_path / "data", _clean(visits_df), stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")

    # Synthetic scale/span deviations are reported but do not block.
    metrics = stage_ingest(store, tmp_path / "data", seed=42, strict=False)
    assert metrics["visit_rows"] > 0


def test_subset_run_still_blocks_on_hard_failures(
    tmp_path, visits_df, stores_df, calendar_df, reservations_df
):
    """Duplicates/negatives are hard failures even in a subset run."""
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import stage_ingest

    _write_csvs(tmp_path / "data", visits_df, stores_df, calendar_df, reservations_df)
    store = CheckpointStore(tmp_path / "ckpt")

    with pytest.raises(SystemExit):
        stage_ingest(store, tmp_path / "data", seed=42, strict=False)
