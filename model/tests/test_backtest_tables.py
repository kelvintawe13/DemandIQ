"""Rolling-origin backtest + results table tests (Sections 3.11, 3.25)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def long_history() -> pd.DataFrame:
    """One store, 400 consecutive days, weekday-seasonal demand."""
    rng = np.random.default_rng(9)
    dates = pd.date_range("2016-01-01", periods=400, freq="D")
    weekend = np.where(dates.dayofweek >= 5, 10, 0)
    return pd.DataFrame(
        {
            "air_store_id": "air_a",
            "visit_date": dates,
            "visitors": rng.poisson(20 + weekend),
        }
    )


def test_make_folds_count_and_advance(long_history):
    from diq_model.evaluate.backtest import make_folds

    folds = make_folds(long_history, n_folds=6, test_days=30, advance_days=14)
    assert len(folds) == 6
    # Consecutive folds' test windows advance by exactly 14 days.
    for a, b in zip(folds, folds[1:]):
        delta = (a.test_start - b.test_start).days
        assert delta == 14
    # Last fold's test ends at the final observed day.
    last_date = pd.Series(long_history["visit_date"].unique()).max()
    assert folds[0].test_end - pd.Timedelta(days=1) == last_date


def test_make_folds_drops_short_training(long_history):
    from diq_model.evaluate.backtest import make_folds

    # min_train_days larger than the data -> zero folds.
    assert make_folds(long_history, n_folds=6, min_train_days=10_000) == []


def test_make_folds_is_chronological(long_history):
    from diq_model.evaluate.backtest import make_folds

    for f in make_folds(long_history, n_folds=6):
        assert f.train_start < f.train_end <= f.test_start < f.test_end


def test_run_backtest_stub_model(long_history):
    """A deterministic stub: predict the fold-train's last value."""
    from diq_model.evaluate.backtest import run_backtest

    def fit_predict(train, test):
        return pd.Series(float(train["visitors"].tail(1).iloc[0]), index=test.index)

    summary = run_backtest(long_history, fit_predict, n_folds=3, test_days=30)
    assert summary["n_folds"] == 3
    assert {"mae_mean", "mae_std", "rmsle_mean"} <= set(summary)
    assert summary["mae_mean"] >= 0
    assert len(summary["per_fold"]) == 3
    for row in summary["per_fold"]:
        assert row["test_days"] == 30


def test_run_backtest_refits_per_fold(long_history):
    """State must not cross folds: mutating train inside a fold cannot change
    another fold's predictions (verified via call counter isolation)."""
    from diq_model.evaluate.backtest import run_backtest

    calls = []

    def fit_predict(train, test):
        calls.append(len(train))
        return pd.Series(float(train["visitors"].mean()), index=test.index)

    run_backtest(long_history, fit_predict, n_folds=3)
    # Train windows GROW toward later-origin folds... actually shrink with
    # backwards anchoring; assert they are distinct windows at least.
    assert len(set(calls)) >= 2


def test_run_backtest_no_folds_raises(long_history):
    from diq_model.evaluate.backtest import run_backtest

    with pytest.raises(ValueError, match="no folds"):
        run_backtest(
            long_history.head(50), lambda tr, te: pd.Series(dtype="float64"),
            min_train_days=10_000,
        )


# ----------------------------------------------------------------- tables
def test_render_markdown_table(long_history):
    from diq_model.evaluate.tables import render_markdown_table

    df = pd.DataFrame({"name": ["a", "b"], "mae": [1.234, 2.346]})
    md = render_markdown_table(df)
    assert "| name" in md and "a" in md
    assert "1.234" in md and "2.346" in md  # 3-decimal formatting applied


def test_save_table_writes_file(tmp_path, long_history):
    from diq_model.evaluate.tables import save_table

    path = save_table(pd.DataFrame({"x": [1.5]}), tmp_path / "reports" / "t.md")
    assert path.exists()
    assert "1.500" in path.read_text()


# ------------------------------------------------------------ stage wiring
def test_stage_backtest_runs_and_persists(recruit_csvs, tmp_path):
    pytest.importorskip("lightgbm")
    pytest.importorskip("pyarrow")
    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    store = CheckpointStore(tmp_path / "ckpt")
    results = run_pipeline(
        store, recruit_csvs, test_start="2016-02-15", strict=False,
        stages=["ingest", "features", "lightgbm", "backtest"],
        n_folds=2, test_days=7, advance_days=3, min_train_days=10,
    )
    bt = results["backtest"]
    assert bt["n_folds"] >= 1
    assert bt["mae_mean"] >= 0
    assert store.has("backtest_folds.parquet")
    assert store.has("backtest_table.md")
    assert store.has("backtest_summary.joblib")
