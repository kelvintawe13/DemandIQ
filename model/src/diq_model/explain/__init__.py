"""Explainability subpackage (Section 3.24): SHAP contributions."""

from diq_model.explain.shap_values import (
    ATTRIBUTION_DISCLAIMER,
    ExplanationBundle,
    explain_forecasts,
)

__all__ = ["explain_forecasts", "ExplanationBundle", "ATTRIBUTION_DISCLAIMER"]
