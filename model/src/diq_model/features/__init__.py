"""Features subpackage: leakage-safe feature build (Sections 3.10, 3.26)."""

from diq_model.features.build import (
    FEATURE_SET_VERSION,
    FeatureFrame,
    FeatureValidationError,
    build_features,
    load_feature_frame,
    save_feature_frame,
)

__all__ = [
    "FEATURE_SET_VERSION",
    "FeatureFrame",
    "FeatureValidationError",
    "build_features",
    "save_feature_frame",
    "load_feature_frame",
]
