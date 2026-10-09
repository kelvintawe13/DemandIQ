"""LightGBM primary model (Sections 3.13, 3.14).

Design points from MODEL_LIFECYCLE.md:
- trains on ``log1p(visitors)`` (right-skewed target), predictions inverted
  with ``expm1`` at predict time, clipped at zero (visitors >= 0);
- early stopping on a chronological validation slice - never a random one;
- fixed seed (Section 3.27);
- carries the feature-set version it was trained on; a mismatch with the
  frame it is asked to predict on is a hard error, not a warning.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from diq_model.features.build import FEATURE_SET_VERSION

logger = logging.getLogger(__name__)

DEFAULT_PARAMS: dict = {
    "objective": "regression",
    "metric": "rmse",  # on log1p scale; final metrics reported on original scale
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.9,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbosity": -1,
    "seed": 42,
    "deterministic": True,
    "force_col_wise": True,
}


class LightGBMModel:
    """Thin, explicit wrapper: no pipelines, no magic - just the contract."""

    def __init__(self, params: dict | None = None, num_boost_round: int = 2000) -> None:
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.num_boost_round = num_boost_round
        self.booster_ = None
        self.best_iteration_ = None
        self.feature_columns_: list[str] | None = None
        self.feature_set_version_: str | None = None

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _matrix(df: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
        return df[feature_columns].astype("float64")

    def _check_version(self, df: pd.DataFrame) -> None:
        if self.feature_set_version_ is not None:
            from diq_model.features.build import FEATURE_SET_VERSION as current

            # The trained version must match the CURRENT definitions at
            # prediction time; a mismatch means the artefact is stale.
            if self.feature_set_version_ != current:
                raise RuntimeError(
                    f"feature-set mismatch: model trained on "
                    f"{self.feature_set_version_}, installed definitions are {current}"
                )

    # ----------------------------------------------------------------- fit
    def fit(
        self,
        train: pd.DataFrame,
        feature_columns: list[str],
        target: str = "visitors",
        valid: pd.DataFrame | None = None,
        categorical: list[str] | None = None,
    ) -> "LightGBMModel":
        import lightgbm as lgb

        self.feature_columns_ = list(feature_columns)
        self.feature_set_version_ = FEATURE_SET_VERSION

        X = self._matrix(train, feature_columns)
        y = np.log1p(train[target].to_numpy(dtype="float64"))

        callbacks: list = [lgb.log_evaluation(0)]
        valid_sets: list[lgb.Dataset] = []
        if valid is not None and len(valid):
            Xv = self._matrix(valid, feature_columns)
            yv = np.log1p(valid[target].to_numpy(dtype="float64"))
            valid_sets = [lgb.Dataset(Xv, label=yv, reference=lgb.Dataset(X, label=y), free_raw_data=False)]
            callbacks.append(lgb.early_stopping(100, verbose=False))

        self.booster_ = lgb.train(
            self.params,
            train_set=lgb.Dataset(X, label=y, categorical_feature=categorical or []),
            num_boost_round=self.num_boost_round,
            valid_sets=valid_sets,
            callbacks=callbacks,
        )
        self.best_iteration_ = (
            self.booster_.best_iteration if self.booster_.best_iteration else self.num_boost_round
        )
        logger.info("LightGBM trained: best_iteration=%d", self.best_iteration_)
        return self

    # ------------------------------------------------------------- predict
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.booster_ is None:
            raise RuntimeError("model is not fitted")
        self._check_version(df)
        X = self._matrix(df, self.feature_columns_)
        raw = self.booster_.predict(X, num_iteration=self.best_iteration_)
        # invert log1p training transform; demand cannot be negative.
        return np.clip(np.expm1(raw), 0.0, None)

    # -------------------------------------------------------- persistence
    def save(self, store, name: str = "lightgbm_model.joblib") -> str:
        store.save_object(self, name)
        return str(store.root / name)

    @classmethod
    def load(cls, store, name: str = "lightgbm_model.joblib") -> "LightGBMModel":
        return store.load_object(name)
