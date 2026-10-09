"""Baseline models (Section 3.12; EVALUATION.md "Baselines").

A model that cannot beat these has no right to production. The full set:

- ``NaiveBaseline``             - yesterday's demand
- ``SeasonalNaiveBaseline``     - the same weekday last week
- ``HistoricalAverageBaseline`` - mean of the last 28 comparable days (frozen)
- ``DayOfWeekBaseline``         - weekday mean per store (strongest simple signal)
- ``GlobalAverageBaseline``     - the floor

All implement the same fit/predict/evaluate interface as the gradient
boosting wrappers, so the evaluation stage treats every candidate alike.
"""

from __future__ import annotations

import pandas as pd

from diq_model.evaluate.metrics import all_metrics


class _BaseBaseline:
    """Shared evaluate helper; subclasses implement fit/predict."""

    def evaluate(self, test: pd.DataFrame, target: str = "visitors") -> dict[str, float]:
        preds = self.predict(test)
        return all_metrics(test[target], preds)


class GlobalAverageBaseline(_BaseBaseline):
    """Predicts the training-set mean for every store and day (floor model)."""

    def __init__(self) -> None:
        self.mean_: float | None = None

    def fit(self, train: pd.DataFrame, target: str = "visitors") -> "GlobalAverageBaseline":
        self.mean_ = float(train[target].mean())
        return self

    def predict(self, test: pd.DataFrame) -> pd.Series:
        return pd.Series(self.mean_, index=test.index)


class NaiveBaseline(_BaseBaseline):
    """Yesterday's demand (EVALUATION.md): per store, the previous observed
    day's visitors. The first day of each store's evaluation window has no
    in-window predecessor and falls back to the store's train mean."""

    def __init__(self) -> None:
        self.store_mean_: pd.Series | None = None
        self.global_mean_: float | None = None

    def fit(self, train: pd.DataFrame, target: str = "visitors") -> "NaiveBaseline":
        self.store_mean_ = train.groupby("air_store_id")[target].mean()
        self.global_mean_ = float(train[target].mean())
        return self

    def predict(self, test: pd.DataFrame) -> pd.Series:
        df = test.sort_values(["air_store_id", "visit_date"]).copy()
        prev = df.groupby("air_store_id")["visitors"].shift(1)
        fallback = df["air_store_id"].map(self.store_mean_)
        # Ladder: previous in-window day -> store train mean -> global train
        # mean. The last rung covers stores that opened inside the window
        # (no train history at all); a NaN prediction would poison every
        # metric at once (real Recruit data has such stores).
        return prev.fillna(fallback).fillna(self.global_mean_).reindex(test.index)


class SeasonalNaiveBaseline(_BaseBaseline):
    """The same weekday last week: visitors at t-7. Days without an in-window
    t-7 observation (the first week per store) fall back to the store's
    train mean."""

    def __init__(self) -> None:
        self.store_mean_: pd.Series | None = None
        self.global_mean_: float | None = None

    def fit(self, train: pd.DataFrame, target: str = "visitors") -> "SeasonalNaiveBaseline":
        self.store_mean_ = train.groupby("air_store_id")[target].mean()
        self.global_mean_ = float(train[target].mean())
        return self

    def predict(self, test: pd.DataFrame) -> pd.Series:
        df = test.sort_values(["air_store_id", "visit_date"]).copy()
        lag7 = df.groupby("air_store_id")["visitors"].shift(7)
        fallback = df["air_store_id"].map(self.store_mean_)
        # Same ladder as NaiveBaseline (see its predict for the rationale).
        return lag7.fillna(fallback).fillna(self.global_mean_).reindex(test.index)


class HistoricalAverageBaseline(_BaseBaseline):
    """Mean of the last ``lookback_days`` demand days per store, frozen at
    the evaluation window start (the policy simulation's historical_average
    rule, as an evaluation baseline)."""

    def __init__(self, lookback_days: int = 28) -> None:
        self.lookback_days = lookback_days
        self.frozen_: pd.Series | None = None
        self.global_mean_: float | None = None

    def fit(self, train: pd.DataFrame, target: str = "visitors") -> "HistoricalAverageBaseline":
        self.frozen_ = train.groupby("air_store_id")[target].apply(
            lambda s: float(s.tail(self.lookback_days).mean())
        )
        self.global_mean_ = float(train[target].mean())
        return self

    def predict(self, test: pd.DataFrame) -> pd.Series:
        # Stores unseen in train (opened mid-window) get the global mean.
        return test["air_store_id"].map(self.frozen_).fillna(self.global_mean_)


class DayOfWeekBaseline(_BaseBaseline):
    """Per-store, per-weekday historical mean (Section 3.12).

    Falls back to the store's overall train mean, then the global train mean,
    for unseen store/weekday combinations.
    """

    def __init__(self) -> None:
        self.dow_mean_: pd.DataFrame | None = None
        self.store_mean_: pd.Series | None = None
        self.global_mean_: float | None = None

    def fit(self, train: pd.DataFrame, target: str = "visitors") -> "DayOfWeekBaseline":
        df = train.assign(_dow=train["visit_date"].dt.dayofweek)
        self.dow_mean_ = (
            df.groupby(["air_store_id", "_dow"])[target].mean().rename("prediction").reset_index()
        )
        self.store_mean_ = df.groupby("air_store_id")[target].mean()
        self.global_mean_ = float(df[target].mean())
        return self

    def predict(self, test: pd.DataFrame) -> pd.Series:
        df = test.assign(_dow=test["visit_date"].dt.dayofweek)
        merged = df.merge(self.dow_mean_, on=["air_store_id", "_dow"], how="left")

        # Fallback ladder: dow mean -> store mean -> global mean.
        store_fallback = df["air_store_id"].map(self.store_mean_)
        merged["prediction"] = (
            merged["prediction"].fillna(store_fallback).fillna(self.global_mean_)
        )
        return merged["prediction"]
