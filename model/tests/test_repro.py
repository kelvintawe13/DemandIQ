"""Seed helper test (Section 3.27 reproducibility)."""

from __future__ import annotations

import numpy as np


def test_set_global_seed_makes_numpy_deterministic():
    from diq_model.repro import set_global_seed

    set_global_seed(123)
    a = np.random.randint(0, 1000, size=10)
    set_global_seed(123)
    b = np.random.randint(0, 1000, size=10)
    assert (a == b).all()
