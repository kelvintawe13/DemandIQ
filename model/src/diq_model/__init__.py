"""DemandIQ ML pipeline package.

This package is intentionally free of any notebook- or platform-specific
logic: the same code runs locally, in Docker, or driven from a Colab
notebook. Only the data/artifact paths differ between environments.
"""

from diq_model.data.load import load_recruit
from diq_model.data.audit import audit_recruit
from diq_model.repro import set_global_seed

__version__ = "0.1.0"

__all__ = [
    "load_recruit",
    "audit_recruit",
    "set_global_seed",
    "__version__",
]
