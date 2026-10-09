"""Reproducibility helpers: global seeding.

Section 3.27 requires fixed random seeds. Every entry point calls
``set_global_seed`` once, before any model code runs.
"""

from __future__ import annotations

import os
import random

import numpy as np

DEFAULT_SEED = 42


def set_global_seed(seed: int = DEFAULT_SEED) -> None:
    """Seed python, numpy and any seedable env for one reproducible run."""
    random.seed(seed)
    np.random.seed(seed)
    os.environ["PYTHONHASHSEED"] = str(seed)
