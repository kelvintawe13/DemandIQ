"""Bounded hyperparameter search on the validation tail (Sections 3.14/3.25).

The pipeline's test window is scored exactly once and tunes nothing. Everything
that *does* need a choice — early stopping, the conformal warmup, the policy
multiplier — is decided on the last 10% of the training window. This module
adds the booster's hyperparameters to that list, with two constraints that keep
the claim honest:

* **Bounded.** The search is a fixed, small grid (``TUNING_GRID``) with a
  caller-supplied ceiling (``max_trials``); there is no unbounded sampler and no
  dependence on wall-clock or randomness beyond the fixed seed. Trial 0 is the
  documented production default, so "the search found something better" can be
  compared with the configuration the study would otherwise have shipped.
* **Validation only.** ``search_lightgbm`` accepts the fit and validation frames
  and nothing else. The held-out window is not a parameter, so it cannot be
  read here by accident.

Selection is by validation MAE, the operational metric in
``model/EVALUATION.md``; RMSE breaks ties before trial order does. MASE, bias
and R² are recorded per trial for inspection but never select.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from diq_model.evaluate.metrics import all_metrics
from diq_model.models.lightgbm_model import DEFAULT_PARAMS, LightGBMModel
from diq_model.repro import DEFAULT_SEED

# The parameters the search is allowed to move. Every other entry in
# ``DEFAULT_PARAMS`` (objective, determinism, bagging, L2) is held fixed so a
# trial differs only in capacity and learning speed.
SWEPT_PARAMETERS: tuple[str, ...] = (
    "learning_rate",
    "num_leaves",
    "min_data_in_leaf",
    "feature_fraction",
)

# A small, explicitly enumerated grid: eight configurations, default first.
# Bounded on purpose — this is a design check on the documented defaults, not a
# benchmark of LightGBM. Trial 0 is the production default, so a search that
# finds nothing better reports that rather than inventing a winner.
TUNING_GRID: tuple[dict[str, Any], ...] = (
    {key: DEFAULT_PARAMS[key] for key in SWEPT_PARAMETERS},
    {"learning_rate": 0.03, "num_leaves": 31, "min_data_in_leaf": 20, "feature_fraction": 0.9},
    {"learning_rate": 0.03, "num_leaves": 63, "min_data_in_leaf": 40, "feature_fraction": 0.7},
    {"learning_rate": 0.03, "num_leaves": 127, "min_data_in_leaf": 20, "feature_fraction": 0.9},
    {"learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 40, "feature_fraction": 0.9},
    {"learning_rate": 0.05, "num_leaves": 127, "min_data_in_leaf": 20, "feature_fraction": 0.7},
    {"learning_rate": 0.08, "num_leaves": 63, "min_data_in_leaf": 80, "feature_fraction": 0.9},
    {"learning_rate": 0.08, "num_leaves": 127, "min_data_in_leaf": 40, "feature_fraction": 0.7},
)

DEFAULT_MAX_TRIALS: int = len(TUNING_GRID)


@dataclass(frozen=True, slots=True)
class TrialResult:
    """One configuration scored on the validation tail."""

    trial: int
    params: dict[str, Any]
    validation_mae: float
    validation_rmse: float
    validation_rmsle: float
    validation_mase: float
    best_iteration: int
    duration_s: float
    selected: bool = False


@dataclass(frozen=True, slots=True)
class SearchResult:
    """The trials and the one selected by validation MAE."""

    trials: tuple[TrialResult, ...]
    best_trial: int
    default_mae: float
    selection_metric: str = "validation_mae"

    @property
    def best(self) -> TrialResult:
        """The selected trial (trial 0 when nothing beat the default)."""
        return next(t for t in self.trials if t.trial == self.best_trial)

    @property
    def best_params(self) -> dict[str, Any]:
        """A mutable copy of the selected parameters."""
        return dict(self.best.params)

    @property
    def improvement(self) -> float:
        """Validation MAE the search removed against the default configuration.

        Zero or negative is a legitimate outcome and must be reported as such:
        the default stays selected when nothing beats it.
        """
        return round(float(self.default_mae - self.best.validation_mae), 4)


def search_lightgbm(
    fit_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    feature_columns: list[str],
    *,
    seed: int = DEFAULT_SEED,
    max_trials: int = DEFAULT_MAX_TRIALS,
    num_boost_round: int = 2000,
    categorical: tuple[str, ...] = ("dow", "month"),
) -> SearchResult:
    """Fit up to ``max_trials`` configurations and select on validation MAE.

    Args:
        fit_df: Rows the model is fitted on (the early-stopping tail excluded).
        valid_df: The chronological tail of the training window. This is the
            only frame used to choose a configuration; it is never the test
            window.
        feature_columns: The model's declared input columns.
        seed: Fixed seed applied to every trial.
        max_trials: Ceiling on configurations evaluated, clamped to
            ``len(TUNING_GRID)``. ``1`` evaluates only the default.
        num_boost_round: Boosting-round ceiling; early stopping on the
            validation tail still decides the actual round count.
        categorical: Columns passed to LightGBM as categorical.

    Returns:
        :class:`SearchResult` with every trial and the selected one.

    Raises:
        ValueError: If either frame is empty, no feature columns are supplied,
            or ``max_trials`` is below 1.
    """
    if fit_df.empty:
        raise ValueError("fit_df is empty: there is nothing to train on")
    if valid_df.empty:
        raise ValueError("valid_df is empty: the validation tail cannot be scored")
    if not feature_columns:
        raise ValueError("feature_columns is empty")
    if max_trials < 1:
        raise ValueError("max_trials must be >= 1")

    limit = min(int(max_trials), len(TUNING_GRID))
    y_fit = fit_df["visitors"]
    trials: list[TrialResult] = []

    for index, swept in enumerate(TUNING_GRID[:limit]):
        started = time.perf_counter()
        model = LightGBMModel(
            params={**swept, "seed": seed},
            num_boost_round=num_boost_round,
        )
        model.fit(
            fit_df,
            feature_columns=feature_columns,
            valid=valid_df,
            categorical=list(categorical),
        )
        metrics = all_metrics(valid_df["visitors"], model.predict(valid_df), y_train=y_fit)
        trials.append(
            TrialResult(
                trial=index,
                # The wrapper's full effective params, not just the swept keys:
                # the bundle and the registry should describe the model that
                # actually ran, including the fixed values.
                params={
                    key: (value.item() if hasattr(value, "item") else value)
                    for key, value in model.params.items()
                },
                validation_mae=float(metrics["mae"]),
                validation_rmse=float(metrics["rmse"]),
                validation_rmsle=float(metrics["rmsle"]),
                validation_mase=float(metrics["mase"]),
                best_iteration=int(model.best_iteration_ or 0),
                duration_s=round(time.perf_counter() - started, 3),
            )
        )

    # MAE first; RMSE then trial order make ties deterministic rather than
    # dependent on dict/iteration order.
    best = min(trials, key=lambda t: (t.validation_mae, t.validation_rmse, t.trial))
    marked = tuple(
        TrialResult(
            trial=t.trial,
            params=t.params,
            validation_mae=t.validation_mae,
            validation_rmse=t.validation_rmse,
            validation_rmsle=t.validation_rmsle,
            validation_mase=t.validation_mase,
            best_iteration=t.best_iteration,
            duration_s=t.duration_s,
            selected=t.trial == best.trial,
        )
        for t in trials
    )
    return SearchResult(
        trials=marked,
        best_trial=best.trial,
        default_mae=float(trials[0].validation_mae),
    )
