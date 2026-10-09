"""Rolling-origin backtesting (Section 3.11 validation; EVALUATION.md).

Six folds, each advancing the origin by 14 days, so the reported figure is
a mean over folds rather than a lucky window:

    fold 1  train ──────────┤ test ──┤
    fold 2  train ──────────────────┤ test ──┤
    ...

Boundary discipline per fold: the model factory refits on that fold's train
window only; the fold's test days are scored once and never tune anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from diq_model.evaluate.metrics import all_metrics

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fold:
    """One rolling-origin split of the timeline."""

    index: int  # 1-based
    train_start: pd.Timestamp
    train_end: pd.Timestamp  # exclusive
    test_start: pd.Timestamp  # exclusive of train
    test_end: pd.Timestamp  # exclusive

    def as_dict(self) -> dict:
        return {
            "fold": self.index,
            "train_start": str(self.train_start.date()),
            "train_end": str(self.train_end.date()),
            "test_start": str(self.test_start.date()),
            "test_end": str(self.test_end.date()),
        }


def make_folds(
    df: pd.DataFrame,
    n_folds: int = 6,
    test_days: int = 30,
    advance_days: int = 14,
    min_train_days: int = 180,
) -> list[Fold]:
    """Anchor folds at the END of the data, walking backwards.

    The last fold's test window ends at the final observed day; each earlier
    fold's test window starts ``advance_days`` earlier. Folds whose train
    window would fall below ``min_train_days`` are dropped - so short
    synthetic subsets naturally produce fewer folds than 6.
    """
    dates = pd.Series(df["visit_date"].unique()).sort_values()
    last = dates.iloc[-1]
    first = dates.iloc[0]
    total_days = (last - first).days + 1

    folds: list[Fold] = []
    for k in range(n_folds):
        test_end = last - pd.Timedelta(days=k * advance_days) + pd.Timedelta(days=1)
        test_start = test_end - pd.Timedelta(days=test_days)
        train_end = test_start
        train_start = first
        if (train_end - train_start).days < min_train_days:
            break
        folds.append(
            Fold(
                index=k + 1,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    return folds


def run_backtest(
    df: pd.DataFrame,
    fit_predict: Callable[
        [pd.DataFrame, pd.DataFrame],
        pd.Series | tuple[pd.Series, np.ndarray, np.ndarray],
    ],
    n_folds: int = 6,
    test_days: int = 30,
    advance_days: int = 14,
    min_train_days: int = 180,
) -> dict:
    """Run ``fit_predict(train_df, test_df) -> preds`` over rolling folds.

    ``fit_predict`` receives the fold's train and test frames (both with
    ``air_store_id``/``visit_date``/``visitors``) and must return a Series
    of predictions aligned to ``test_df``. Every fold refits from scratch -
    no state crosses the fold boundary.

    It may instead return ``(preds, lower, upper)``; when it does, each fold
    also records the **interval coverage** (share of test days whose actual
    demand fell inside ``[lower, upper]``) and the mean interval width, and the
    summary carries ``coverage_mean``. Point-only callers are unaffected.

    Returns per-fold metrics plus the mean/std summary EVALUATION.md asks
    for ("a mean over folds rather than a lucky window").
    """
    folds = make_folds(
        df,
        n_folds=n_folds,
        test_days=test_days,
        advance_days=advance_days,
        min_train_days=min_train_days,
    )
    if not folds:
        raise ValueError(
            "no folds fit the data: need at least min_train_days + test_days " "of history"
        )

    fold_rows: list[dict] = []
    for fold in folds:
        train = df[df["visit_date"] < fold.train_end]
        test = df[(df["visit_date"] >= fold.test_start) & (df["visit_date"] < fold.test_end)]
        if train.empty or test.empty:
            logger.warning("fold %d produced an empty split; skipping", fold.index)
            continue

        out = fit_predict(train, test)
        interval: tuple[np.ndarray, np.ndarray] | None = None
        if isinstance(out, tuple):
            preds, lower, upper = out
            interval = (np.asarray(lower, dtype="float64"), np.asarray(upper, dtype="float64"))
        else:
            preds = out
        metrics = all_metrics(test["visitors"], preds, y_train=train["visitors"])
        row: dict = {
            "fold": fold.index,
            "train_days": int((fold.train_end - fold.train_start).days),
            "test_days": int(len(test)),
            **metrics,
        }
        if interval is not None:
            y = np.asarray(test["visitors"], dtype="float64")
            lo, hi = interval
            row["coverage"] = round(float(np.mean((y >= lo) & (y <= hi))), 4)
            row["mean_width"] = round(float(np.mean(hi - lo)), 3)
        fold_rows.append(row)
        logger.info("fold %d: MAE %.3f RMSLE %.4f", fold.index, metrics["mae"], metrics["rmsle"])

    if not fold_rows:
        raise ValueError("all folds produced empty splits")

    frame = pd.DataFrame(fold_rows)
    coverage_mean: float | None = None
    if "coverage" in frame.columns:
        coverage_mean = round(float(frame["coverage"].mean()), 4)
    summary = {
        "n_folds": int(len(frame)),
        "folds": [f.as_dict() for f in folds[: len(frame)]],
        "mae_mean": round(float(frame["mae"].mean()), 3),
        "mae_std": round(float(frame["mae"].std(ddof=1)) if len(frame) > 1 else 0.0, 3),
        "rmse_mean": round(float(frame["rmse"].mean()), 3),
        "rmsle_mean": round(float(frame["rmsle"].mean()), 4),
        "rmsle_std": round(float(frame["rmsle"].std(ddof=1)) if len(frame) > 1 else 0.0, 4),
        "coverage_mean": coverage_mean,
        "per_fold": frame.to_dict("records"),
    }
    return summary
