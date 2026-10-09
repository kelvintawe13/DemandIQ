"""SHAP explainability (Section 3.24).

Per-forecast feature contributions computed with TreeSHAP and stored
ALONGSIDE the prediction, so the explanation shown months later is the one
generated at prediction time - not a re-derivation against a newer model
(ARCHITECTURE.md section 5).

Framing contract (Section 3.24, FEATURE_SPEC.md): contributions are
**predictive attribution, never causal effect**. Every artifact this module
produces carries that disclaimer; the UI is required to show it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from diq_model.features.build import FEATURE_SET_VERSION

logger = logging.getLogger(__name__)

# Required framing on every artifact and every UI surface (Section 3.24):
ATTRIBUTION_DISCLAIMER = (
    "Predictive attribution, not causal effect: these values show which "
    "features moved the model's prediction, not what causes demand."
)


@dataclass(frozen=True)
class ExplanationBundle:
    """SHAP values for a batch of forecasts, with provenance."""

    frame: pd.DataFrame  # air_store_id, visit_date, feature_name, feature_value, shap_impact
    model_name: str
    feature_set_version: str
    base_value_visitors: float = 0.0  # expm1(expected_value): prediction when all features are "average"
    disclaimer: str = ATTRIBUTION_DISCLAIMER

    def for_forecast(self, store_id: str, visit_date) -> pd.DataFrame:
        """Contributions for one forecast, sorted by |impact| descending."""
        mask = (self.frame["air_store_id"] == store_id) & (
            self.frame["visit_date"] == pd.Timestamp(visit_date)
        )
        return (
            self.frame[mask]
            .sort_values("shap_impact", key=lambda s: s.abs(), ascending=False)
            .reset_index(drop=True)
        )

    def global_importance(self) -> pd.DataFrame:
        """Mean |SHAP| per feature across the batch (model-card material)."""
        imp = (
            self.frame.groupby("feature_name")["shap_impact"]
            .apply(lambda s: float(np.abs(s).mean()))
            .sort_values(ascending=False)
            .rename("mean_abs_shap")
        )
        return imp.reset_index()


def explain_forecasts(
    model,
    df: pd.DataFrame,
    feature_columns: list[str],
    model_name: str = "lightgbm",
    max_rows: int | None = None,
) -> ExplanationBundle:
    """TreeSHAP contributions for every row in ``df`` (long format).

    The model must be the ORIGINAL-scale wrapper (LightGBMModel /
    CatBoostModel): contributions are computed on the log1p training scale
    and inverted to signed visitor units per feature, so impacts on screen
    read in the same units as the forecast itself.
    """
    import shap

    if not feature_columns:
        raise ValueError("feature_columns must not be empty")

    rows = df if max_rows is None else df.head(max_rows)
    X = rows[feature_columns].astype("float64")

    booster = getattr(model, "booster_", None) or getattr(model, "model_", None)
    if booster is None:
        raise RuntimeError("model is not fitted")

    # TreeExplainer on the raw (log1p) output; slow-but-safe `feature_perturbation`
    # default is avoided by using interventional background = tree_path_dependent.
    explainer = shap.TreeExplainer(booster)
    shap_raw = explainer.shap_values(X)  # (n_rows, n_features), log1p units

    # Convert log1p-scale impacts to visitor units per row, distributing via
    # the local linearisation of expm1 at the prediction:
    #   visitors ≈ expm1(raw); Δvisitors_j ≈ exp(raw) * Δraw_j
    raw_pred = explainer.expected_value + shap_raw.sum(axis=1)
    scale = np.exp(raw_pred)  # d(expm1)/d(raw) = exp(raw)
    shap_visitors = shap_raw * scale[:, None]

    # Additivity (up to linearisation): prediction ≈ base_value + Σ contributions.
    base_value_visitors = float(np.expm1(np.asarray(explainer.expected_value).ravel()[0]))

    long = []
    ids = rows[["air_store_id", "visit_date"]].reset_index(drop=True)
    for j, feat in enumerate(feature_columns):
        block = pd.DataFrame(
            {
                "air_store_id": ids["air_store_id"],
                "visit_date": ids["visit_date"],
                "feature_name": feat,
                "feature_value": X[feat].to_numpy(),
                "shap_impact": shap_visitors[:, j],
            }
        )
        long.append(block)
    frame = pd.concat(long, ignore_index=True)

    logger.info("Explained %d forecasts x %d features", len(rows), len(feature_columns))
    return ExplanationBundle(
        frame=frame,
        model_name=model_name,
        feature_set_version=FEATURE_SET_VERSION,
        base_value_visitors=base_value_visitors,
    )
