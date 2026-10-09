"""CatBoost secondary primary model (Sections 3.13-3.14).

Kept alongside LightGBM because the two fail differently: CatBoost's native
categorical handling and lower tuning sensitivity make it a genuine fallback
rather than a re-run of the same idea (MODEL_LIFECYCLE.md section 4).

Same contract as ``LightGBMModel``: log1p training with expm1 inversion,
chronological early stopping, fixed seed, feature-set version check as a
hard error.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from diq_model.features.build import FEATURE_SET_VERSION

logger = logging.getLogger(__name__)

DEFAULT_PARAMS: dict = {
    "loss_function": "RMSE",  # on log1p scale; metrics reported on original scale
    "learning_rate": 0.05,
    "depth": 6,
    "l2_leaf_reg": 3.0,
    "random_seed": 42,
    "verbose": False,
    "allow_writing_files": False,
}


class CatBoostModel:
    """Interface-compatible with LightGBMModel - the stage code is shared."""

    def __init__(self, params: dict | None = None, num_boost_round: int = 2000) -> None:
        self.params = {**DEFAULT_PARAMS, **(params or {})}
        self.num_boost_round = num_boost_round
        self.model_ = None
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
        categorical: list[str] | None = None,  # accepted for interface parity
    ) -> "CatBoostModel":
        from catboost import CatBoostRegressor, Pool

        self.feature_columns_ = list(feature_columns)
        self.feature_set_version_ = FEATURE_SET_VERSION

        X = self._matrix(train, feature_columns)
        y = np.log1p(train[target].to_numpy(dtype="float64"))

        eval_set = None
        if valid is not None and len(valid):
            Xv = self._matrix(valid, feature_columns)
            yv = np.log1p(valid[target].to_numpy(dtype="float64"))
            eval_set = Pool(Xv, label=yv)

        self.model_ = CatBoostRegressor(
            iterations=self.num_boost_round,
            early_stopping_rounds=100 if eval_set is not None else None,
            use_best_model=eval_set is not None,
            **self.params,
        )
        self.model_.fit(X, y, eval_set=eval_set)
        self.best_iteration_ = (
            int(self.model_.get_best_iteration())
            if self.model_.get_best_iteration()
            else self.num_boost_round
        )
        logger.info("CatBoost trained: best_iteration=%s", self.best_iteration_)
        return self

    # ------------------------------------------------------------- predict
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.model_ is None:
            raise RuntimeError("model is not fitted")
        self._check_version(df)
        X = self._matrix(df, self.feature_columns_)
        raw = self.model_.predict(X)
        return np.clip(np.expm1(raw), 0.0, None)

    # -------------------------------------------------------- persistence
    def save(self, store, name: str = "catboost_model.joblib") -> str:
        store.save_object(self, name)
        return str(store.root / name)

    @classmethod
    def load(cls, store, name: str = "catboost_model.joblib") -> "CatBoostModel":
        return store.load_object(name)
