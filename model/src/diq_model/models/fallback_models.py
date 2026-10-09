"""Fallback models — Linear Regression, Random Forest and XGBoost (Section 3.13).

The proposal keeps three further models "as baselines and fallback models rather
than dropped from the study", for two stated reasons: to test whether
LightGBM/CatBoost's added complexity is justified by the data, and to have a
fallback family if the primaries prove unstable during tuning. They were absent
from this package, so the comparison the proposal asks for could not be run and
the models named in the abstract could not be produced at all.

They are **feature-matrix models**, so they train on exactly the frame the
primaries train on, with the same chronological split and the same target
transform (``log1p``, inverted with ``expm1`` and clipped at zero). That is the
point of the comparison: same rows, same features, same metric window, only the
estimator differs.

Two implementation notes worth stating plainly:

* ``sklearn``'s LinearRegression and RandomForestRegressor reject NaN, while
  LightGBM and XGBoost accept it natively. Every feature frame contains NaN
  (a store's ``lag_14`` is undefined early in its history), so ``fit`` records
  the **training-window median per column** and fills with it. The imputation
  values are learned from the fit slice only — never from validation or test.
* XGBoost lives in the ``training`` extra and may not be installed. The module
  reports that honestly through :func:`missing_dependencies` rather than
  failing the pipeline or silently comparing two models instead of three.

A third note, added after the first real run: an unregularised linear fit on a
``log1p`` target is an *exponential* model, so a feature row outside the
support of the training data extrapolates without limit — on the Recruit test
window one such row predicted 1.8e10 visitors and, by itself, accounted for the
model's entire reported MAE. :class:`LinearRegressionModel` therefore bounds
its output to the target range observed in the fit slice (halving the bound in
log space, i.e. clipping ``log1p`` predictions to ``[min, max]`` of the fitting
target). The bound is learned from the fit slice only, so it carries no leakage,
and it is opt-in (``clip_to_train_range``) because the tree ensembles are
bounded by construction and must keep behaving exactly as before.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

import numpy as np
import pandas as pd

from diq_model.evaluate.metrics import all_metrics
from diq_model.features.build import FEATURE_SET_VERSION

logger = logging.getLogger(__name__)


class _SklearnFallback:
    """Shared contract for the fallback regressors.

    Mirrors :class:`diq_model.models.lightgbm_model.LightGBMModel` —
    ``fit``/``predict``/``save``/``load``/``evaluate`` — so the evaluation stage
    treats every candidate alike and none of them needs a special case.
    """

    algorithm = "fallback"

    # Bounded-output models set this; see :class:`LinearRegressionModel`.
    clip_to_train_range = False

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = dict(params or {})
        self.estimator_: Any = None
        self.feature_columns_: list[str] | None = None
        self.feature_set_version_: str | None = None
        self.impute_: pd.Series | None = None
        # ``(min, max)`` of the log1p target on the fit slice, or None.
        self.y_bound_: tuple[float, float] | None = None

    # ------------------------------------------------------------- helpers
    def _build(self) -> Any:  # pragma: no cover - overridden
        raise NotImplementedError

    def _matrix(self, df: pd.DataFrame) -> pd.DataFrame:
        """Feature matrix as floats, with the training median filling gaps."""
        if self.feature_columns_ is None:
            raise RuntimeError("model is not fitted")
        X = df[self.feature_columns_].astype("float64")
        if self.impute_ is not None:
            X = X.fillna(self.impute_)
        # A column that was entirely NaN in training has no median to lean on.
        return X.fillna(0.0)

    def _check_version(self, df: pd.DataFrame) -> None:
        """Hard-fail when the artefact predates the installed definitions."""
        if (
            self.feature_set_version_ is not None
            and self.feature_set_version_ != FEATURE_SET_VERSION
        ):
            raise RuntimeError(
                f"feature-set mismatch: model trained on {self.feature_set_version_}, "
                f"installed definitions are {FEATURE_SET_VERSION}"
            )

    # ----------------------------------------------------------------- fit
    def fit(
        self,
        train: pd.DataFrame,
        feature_columns: list[str],
        target: str = "visitors",
        valid: pd.DataFrame | None = None,
        categorical: list[str] | None = None,
    ) -> "_SklearnFallback":
        """Fit on ``log1p(target)``.

        ``valid`` and ``categorical`` are accepted for signature parity with the
        primary models and deliberately unused: these estimators do their own
        internal bagging and take no categorical hints. Making that explicit
        beats letting a caller believe the validation slice is doing early
        stopping here — it is not, and none of these three needs it.
        """
        self.feature_columns_ = list(feature_columns)
        self.feature_set_version_ = FEATURE_SET_VERSION
        self.impute_ = train[list(feature_columns)].astype("float64").median(numeric_only=True)

        X = self._matrix(train)
        y = np.log1p(train[target].to_numpy(dtype="float64"))
        if self.clip_to_train_range:
            # Support of the target on the fit slice. Predictions are censored to
            # it in ``predict`` so an out-of-support feature row cannot make the
            # exponential inverse explode.
            self.y_bound_ = (float(y.min()), float(y.max()))
        self.estimator_ = self._build().fit(X, y)
        logger.info("%s trained on %d rows / %d features", self.algorithm, len(X), X.shape[1])
        return self

    # ------------------------------------------------------------- predict
    def predict(self, df: pd.DataFrame) -> np.ndarray:
        if self.estimator_ is None:
            raise RuntimeError("model is not fitted")
        self._check_version(df)
        raw = np.asarray(self.estimator_.predict(self._matrix(df)), dtype="float64")
        if self.y_bound_ is not None:
            # No extrapolation past the demand range seen in training.
            raw = np.clip(raw, self.y_bound_[0], self.y_bound_[1])
        # Invert the training transform; visitors cannot be negative.
        return np.clip(np.expm1(raw), 0.0, None)

    # ----------------------------------------------------------- evaluate
    def evaluate(
        self,
        test: pd.DataFrame,
        target: str = "visitors",
        y_train: pd.Series | np.ndarray | None = None,
    ) -> dict[str, float]:
        return all_metrics(test[target], self.predict(test), y_train=y_train)

    # -------------------------------------------------------- persistence
    def save(self, store: Any, name: str | None = None) -> str:
        filename = name or f"{self.algorithm}_model.joblib"
        store.save_object(self, filename)
        return str(store.root / filename)

    @classmethod
    def load(cls, store: Any, name: str | None = None) -> "_SklearnFallback":
        filename = name or f"{cls.algorithm}_model.joblib"
        return store.load_object(filename)  # type: ignore[no-any-return]


class LinearRegressionModel(_SklearnFallback):
    """Ordinary least squares on the feature matrix (§3.13).

    The linear baseline: with ``log1p`` applied this is a log-linear demand
    model, and if the boosted trees cannot beat it the extra complexity is not
    paying for itself.

    The fit itself is untouched OLS — the only departure is that predictions are
    censored to the target range of the fit slice. That is a property of the
    model class rather than a tuned hyperparameter: a log-linear model has no
    notion of "too large", so unconstrained it will happily report whichever
    number an out-of-support feature combination exponentiates to.
    """

    algorithm = "linear_regression"
    clip_to_train_range = True

    def _build(self) -> Any:
        from sklearn.linear_model import LinearRegression

        return LinearRegression(**self.params)


class RandomForestModel(_SklearnFallback):
    """Random forest regressor (§3.13)."""

    algorithm = "random_forest"

    DEFAULT_PARAMS: dict[str, Any] = {
        "n_estimators": 200,
        "min_samples_leaf": 5,
        "max_features": 0.5,
        "n_jobs": -1,
        "random_state": 42,
    }

    def _build(self) -> Any:
        from sklearn.ensemble import RandomForestRegressor

        return RandomForestRegressor(**{**self.DEFAULT_PARAMS, **self.params})


class XGBoostModel(_SklearnFallback):
    """XGBoost regressor (§3.13). Requires the ``training`` extra."""

    algorithm = "xgboost"

    DEFAULT_PARAMS: dict[str, Any] = {
        "n_estimators": 600,
        "learning_rate": 0.05,
        "max_depth": 6,
        "subsample": 0.9,
        "colsample_bytree": 0.9,
        "reg_lambda": 1.0,
        "tree_method": "hist",
        "n_jobs": -1,
        "random_state": 42,
    }

    def _build(self) -> Any:
        from xgboost import XGBRegressor

        return XGBRegressor(**{**self.DEFAULT_PARAMS, **self.params})


FALLBACK_FACTORIES: dict[str, type[_SklearnFallback]] = {
    LinearRegressionModel.algorithm: LinearRegressionModel,
    RandomForestModel.algorithm: RandomForestModel,
    XGBoostModel.algorithm: XGBoostModel,
}

# The import each fallback needs, so availability can be reported per model.
_REQUIRED_IMPORTS: dict[str, str] = {
    LinearRegressionModel.algorithm: "sklearn.linear_model",
    RandomForestModel.algorithm: "sklearn.ensemble",
    XGBoostModel.algorithm: "xgboost",
}


def missing_dependencies() -> dict[str, str]:
    """Fallback models that cannot be built here, with the reason.

    Returns:
        ``{algorithm: reason}`` for each unavailable model — empty when the
        environment has every dependency (``pip install -e ".[training]"``).
    """
    missing: dict[str, str] = {}
    for algorithm, module in _REQUIRED_IMPORTS.items():
        try:
            importlib.import_module(module)
        except ImportError as exc:  # pragma: no cover - depends on environment
            missing[algorithm] = f"{module} is not installed ({exc})"
    return missing


def build_fallbacks(
    params: dict[str, dict[str, Any]] | None = None,
) -> dict[str, _SklearnFallback]:
    """Instantiate every fallback whose dependency is present.

    Args:
        params: Optional per-algorithm parameter overrides.

    Returns:
        ``{algorithm: model}``, ready to ``fit``. Unavailable models are
        omitted (see :func:`missing_dependencies` for why) rather than
        substituted, so a comparison never silently reports two models where
        three were asked for.
    """
    missing = missing_dependencies()
    overrides = params or {}
    return {
        algorithm: factory(overrides.get(algorithm))
        for algorithm, factory in FALLBACK_FACTORIES.items()
        if algorithm not in missing
    }
