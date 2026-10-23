"""Bounded hyperparameter search on the validation tail (Sections 3.14/3.25).

The search's contract is narrow on purpose: it is given the fit and validation
frames only, it evaluates a fixed bounded grid (default first), and it selects
on validation MAE. These tests pin the selection rule, the bound, the
default-first behaviour, and that a real LightGBM fit round-trips through the
wrapper.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from diq_model.models import search as search_module
from diq_model.models.lightgbm_model import DEFAULT_PARAMS
from diq_model.models.search import TUNING_GRID, search_lightgbm


def _frames(rows: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2017-01-01", periods=rows, freq="D")
    fit = pd.DataFrame(
        {
            "visit_date": dates[:40],
            "visitors": np.arange(40.0) % 17 + 1,
            "dow": [d.dayofweek for d in dates[:40]],
            "month": [d.month for d in dates[:40]],
        }
    )
    valid = pd.DataFrame(
        {
            "visit_date": dates[40:],
            "visitors": np.arange(rows - 40, dtype=float) % 13 + 2,
            "dow": [d.dayofweek for d in dates[40:]],
            "month": [d.month for d in dates[40:]],
        }
    )
    return fit, valid


def _predict_from_learning_rate(rate: float, n: int) -> np.ndarray:
    """A deterministic stand-in whose error depends on the swept parameter."""
    return np.full(n, rate * 200.0)


def test_selects_lowest_validation_mae_and_marks_one_trial(monkeypatch) -> None:
    seen_valid: list[pd.DataFrame] = []

    class FakeModel:
        def __init__(self, params=None, num_boost_round=2000):
            self.params = {**DEFAULT_PARAMS, **(params or {})}
            self.best_iteration_ = 7

        def fit(self, train, feature_columns, valid=None, categorical=None):
            seen_valid.append(valid)
            return self

        def predict(self, df):
            return _predict_from_learning_rate(float(self.params["learning_rate"]), len(df))

    monkeypatch.setattr(search_module, "LightGBMModel", FakeModel)
    fit, valid = _frames()
    result = search_lightgbm(fit, valid, ["dow", "month"], max_trials=3)

    # Bounded by max_trials, never the whole grid.
    assert len(result.trials) == 3
    assert result.selection_metric == "validation_mae"
    # Trial 0 is the documented production default, so "nothing beat it" is
    # attributable to the default rather than to an arbitrary start point.
    assert result.trials[0].params["learning_rate"] == DEFAULT_PARAMS["learning_rate"]
    assert result.trials[0].params["num_leaves"] == DEFAULT_PARAMS["num_leaves"]
    # The lr=0.03 trial predicts inside the validation frame's demand range
    # while the default overshoots it, so it wins; trial 2 ties on MAE/RMSE and
    # loses on trial order, which keeps selection deterministic.
    assert result.best_trial == 1
    assert [t.selected for t in result.trials] == [False, True, False]
    assert result.best.validation_mae == min(t.validation_mae for t in result.trials)
    assert result.improvement == round(
        result.trials[0].validation_mae - result.best.validation_mae, 4
    )
    # Every trial saw the validation tail and the same fit frame; the function
    # accepts no test window at all.
    assert seen_valid and all(v is valid for v in seen_valid)


def test_single_trial_is_the_default_and_improvement_is_zero(monkeypatch) -> None:
    class FakeModel:
        def __init__(self, params=None, num_boost_round=2000):
            self.params = {**DEFAULT_PARAMS, **(params or {})}
            self.best_iteration_ = 3

        def fit(self, train, feature_columns, valid=None, categorical=None):
            return self

        def predict(self, df):
            return np.full(len(df), 4.0)

    monkeypatch.setattr(search_module, "LightGBMModel", FakeModel)
    fit, valid = _frames()
    result = search_lightgbm(fit, valid, ["dow", "month"], max_trials=1)

    assert len(result.trials) == 1
    assert result.best_trial == 0
    assert result.best.selected
    assert result.improvement == 0.0


def test_max_trials_is_clamped_to_the_grid(monkeypatch) -> None:
    class FakeModel:
        def __init__(self, params=None, num_boost_round=2000):
            self.params = {**DEFAULT_PARAMS, **(params or {})}
            self.best_iteration_ = 1

        def fit(self, train, feature_columns, valid=None, categorical=None):
            return self

        def predict(self, df):
            return np.full(len(df), 2.0)

    monkeypatch.setattr(search_module, "LightGBMModel", FakeModel)
    fit, valid = _frames()
    result = search_lightgbm(fit, valid, ["dow", "month"], max_trials=99)
    assert len(result.trials) == len(TUNING_GRID)


@pytest.mark.parametrize(
    ("fit_empty", "valid_empty", "columns", "trials"),
    [
        (True, False, ["dow"], 2),
        (False, True, ["dow"], 2),
        (False, False, [], 2),
        (False, False, ["dow"], 0),
    ],
)
def test_rejects_unusable_inputs(fit_empty, valid_empty, columns, trials) -> None:
    fit, valid = _frames()
    if fit_empty:
        fit = fit.iloc[0:0]
    if valid_empty:
        valid = valid.iloc[0:0]
    with pytest.raises(ValueError):
        search_lightgbm(fit, valid, columns, max_trials=trials)


def test_real_lightgbm_fit_scores_the_validation_tail() -> None:
    """The wrapper contract (log target, early stopping) survives the search."""
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(7)
    rows = 600
    dates = pd.date_range("2016-01-01", periods=rows, freq="D")
    frame = pd.DataFrame(
        {
            "visit_date": dates,
            "visitors": rng.integers(1, 60, rows).astype(float),
            "dow": [d.dayofweek for d in dates],
            "month": [d.month for d in dates],
            "store_code": rng.integers(0, 20, rows),
            "lag_7": rng.integers(1, 60, rows).astype(float),
            "roll_mean_7": rng.integers(1, 60, rows).astype(float),
        }
    )
    fit = frame.iloc[: int(rows * 0.8)]
    valid = frame.iloc[int(rows * 0.8) :]

    result = search_lightgbm(
        fit,
        valid,
        ["dow", "month", "store_code", "lag_7", "roll_mean_7"],
        max_trials=2,
        num_boost_round=40,
    )

    assert len(result.trials) == 2
    assert sum(t.selected for t in result.trials) == 1
    assert all(t.validation_mae > 0 for t in result.trials)
    assert result.best.validation_mae == min(t.validation_mae for t in result.trials)
    assert result.best.best_iteration > 0
