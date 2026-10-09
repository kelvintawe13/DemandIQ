"""Model wrappers: baselines, the primaries, and the Section 3.13 fallbacks."""

from diq_model.models.baselines import DayOfWeekBaseline
from diq_model.models.catboost_model import CatBoostModel
from diq_model.models.fallback_models import (
    LinearRegressionModel,
    RandomForestModel,
    XGBoostModel,
    build_fallbacks,
    missing_dependencies,
)
from diq_model.models.lightgbm_model import LightGBMModel

__all__ = [
    "DayOfWeekBaseline",
    "LightGBMModel",
    "CatBoostModel",
    "LinearRegressionModel",
    "RandomForestModel",
    "XGBoostModel",
    "build_fallbacks",
    "missing_dependencies",
]
