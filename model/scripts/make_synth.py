#!/usr/bin/env python3
"""Generate a synthetic Recruit-shaped dataset for smoke runs.

Usage: python scripts/make_synth.py /tmp/diq-smoke
Writes the four Recruit CSVs (air_visit_data, air_store_info, date_info,
air_reserve) into the target directory. Used by 'make smoke' and useful for
trying the pipeline without the Kaggle download.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/diq-smoke")
    root.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(0)
    n_stores, n_days = 8, 150
    stores = [f"air_{i}" for i in range(n_stores)]
    dates = pd.date_range("2016-01-01", periods=n_days, freq="D")

    frames = []
    for i, store in enumerate(stores):
        weekend = np.where(dates.dayofweek >= 5, 8, 0)
        trend = np.linspace(0, 6, n_days)
        frames.append(
            pd.DataFrame(
                {
                    "air_store_id": store,
                    "visit_date": dates,
                    "visitors": rng.poisson(15 + i * 3 + weekend + trend),
                }
            )
        )
    visits = pd.concat(frames)
    visits.to_csv(root / "air_visit_data.csv", index=False)

    ids = sorted(visits["air_store_id"].unique())
    pd.DataFrame(
        {
            "air_store_id": ids,
            "air_genre_name": [f"g{j % 3}" for j in range(len(ids))],
            "air_area_name": [f"a{j % 2}" for j in range(len(ids))],
        }
    ).to_csv(root / "air_store_info.csv", index=False)

    pd.DataFrame(
        {
            "calendar_date": dates,
            "holiday_flg": ((dates.dayofweek >= 5) | (dates.dayofyear % 37 == 0)).astype(int),
        }
    ).to_csv(root / "date_info.csv", index=False)

    res = []
    for store in ids[:4]:
        res.append(
            pd.DataFrame(
                {
                    "air_store_id": store,
                    "visit_datetime": dates[:40],
                    "reserve_datetime": dates[:40] - pd.Timedelta(days=2),
                    "reserve_visitors": rng.integers(1, 6, 40),
                }
            )
        )
    pd.concat(res).to_csv(root / "air_reserve.csv", index=False)

    print(f"synthetic dataset written to {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
