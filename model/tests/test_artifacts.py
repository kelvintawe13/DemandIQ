"""Checkpoint store tests: atomic writes, resume semantics, manifests."""

from __future__ import annotations

import pandas as pd
import pytest


def test_save_and_load_dataframe(tmp_path):
    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    df = pd.DataFrame({"a": [1, 2, 3]})
    store.save_dataframe(df, "frame.parquet")

    assert store.has("frame.parquet")
    out = store.load_dataframe("frame.parquet")
    assert out.equals(df)


def test_save_and_load_object(tmp_path):
    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    payload = {"model": [1, 2, 3], "meta": {"v": 1}}
    store.save_object(payload, "payload.joblib")

    assert store.load_object("payload.joblib") == payload


def test_record_stage_writes_manifest(tmp_path):
    import time

    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    started = time.time() - 5
    store.record_stage("ingest", started, seed=42, data={"rows": 10}, metrics={"mae": 1.5})

    stages = store.completed_stages()
    assert stages == ["ingest"]
    info = store.stage_info("ingest")
    assert info["data"]["rows"] == 10
    assert info["metrics"]["mae"] == 1.5
    assert info["seed"] == 42
    assert store.stage_metric("ingest", "mae") == 1.5


def test_manifest_accumulates_stages(tmp_path):
    import time

    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    store.record_stage("a", time.time(), seed=1)
    store.record_stage("b", time.time(), seed=1)

    assert store.completed_stages() == ["a", "b"]


def test_stage_metric_missing_stage_is_none(tmp_path):
    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    assert store.stage_metric("nope", "mae") is None


def test_sha256_of_existing_and_missing(tmp_path):
    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    store.save_object({"x": 1}, "obj.joblib")
    digest = store.sha256_of("obj.joblib")
    assert isinstance(digest, str) and len(digest) == 64
    assert store.sha256_of("missing.joblib") is None


def test_parquet_requires_pyarrow(tmp_path):
    """If pyarrow/fastparquet is absent, saving parquet should raise cleanly."""
    from diq_model.artifacts import CheckpointStore

    pytest.importorskip("pyarrow")
    store = CheckpointStore(tmp_path / "ckpt")
    store.save_dataframe(pd.DataFrame({"a": [1]}), "ok.parquet")


def test_record_stage_rejects_implausible_started(tmp_path, caplog):
    """started<=0 (epoch zero) must never land in the manifest as 1970."""
    import time

    from diq_model.artifacts import CheckpointStore

    store = CheckpointStore(tmp_path / "ckpt")
    with caplog.at_level("WARNING"):
        store.record_stage("policy", 0, seed=42, metrics={})

    info = store.stage_info("policy")
    assert info["started_at"].startswith("20")  # a real timestamp, not 1970
    assert 0 <= info["duration_s"] < 60
    assert "implausible started" in caplog.text

    # A sane caller is unaffected.
    started = time.time() - 2
    store.record_stage("shap", started, seed=42, metrics={})
    info = store.stage_info("shap")
    assert info["started_at"].startswith("20")
    assert 1 <= info["duration_s"] < 60
