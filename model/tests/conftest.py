"""Shared fixtures: synthetic Recruit-shaped data.

Shaped to match the audited Recruit schema (proposal Section 3.4.1) so tests
exercise the real code paths without the 20 MB Kaggle download.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def visits_df() -> pd.DataFrame:
    """40 stores x 60 consecutive days, no duplicates, no gaps in span.

    Deliberately injected defects exercise the audit's failure paths:
    a negative visitor count, a duplicate (store, date) row, and an
    unknown store id.
    """
    rng = np.random.default_rng(42)
    stores = [f"air_{i:03d}" for i in range(40)]
    dates = pd.date_range("2016-01-01", periods=60, freq="D")

    frames = []
    for i, store in enumerate(stores):
        base = 10 + i * 2
        n = len(dates) - (i % 3)  # small gaps for some stores
        vals = rng.poisson(base, size=n)
        frames.append(
            pd.DataFrame(
                {
                    "air_store_id": store,
                    "visit_date": dates[:n],
                    "visitors": vals,
                }
            )
        )

    visits = pd.concat(frames, ignore_index=True)

    # A few zero days (allowed), one negative (audit failure).
    visits.loc[visits.index[:3], "visitors"] = 0
    visits.loc[visits.index[-1], "visitors"] = -5

    # One duplicate (store, date) row (audit failure).
    dup = visits.iloc[[0]].copy()
    dup["visitors"] = 99
    visits = pd.concat([visits, dup], ignore_index=True)

    # One unknown store id (identifier check failure).
    orphan = pd.DataFrame(
        {"air_store_id": ["air_unknown"], "visit_date": [dates[0]], "visitors": [7]}
    )
    return pd.concat([visits, orphan], ignore_index=True)


@pytest.fixture
def stores_df(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Store info covering every store in visits, plus genre/area."""
    ids = sorted(visits_df["air_store_id"].unique())
    return pd.DataFrame(
        {
            "air_store_id": ids,
            "air_genre_name": [f"genre_{i % 5}" for i in range(len(ids))],
            "air_area_name": [f"area_{i % 4}" for i in range(len(ids))],
        }
    )


@pytest.fixture
def calendar_df(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Calendar spanning the visit window with a weekend holiday flag."""
    dates = pd.date_range(visits_df["visit_date"].min(), visits_df["visit_date"].max(), freq="D")
    return pd.DataFrame(
        {
            "calendar_date": dates,
            "holiday_flg": (dates.dayofweek >= 5).astype("int8"),
        }
    )


@pytest.fixture
def reservations_df(visits_df: pd.DataFrame) -> pd.DataFrame:
    """Reservations for a third of the stores (supplementary coverage)."""
    store_ids = sorted(visits_df["air_store_id"].unique())[:13]
    rng = np.random.default_rng(7)
    frames = []
    for store in store_ids:
        n = 20
        frames.append(
            pd.DataFrame(
                {
                    "air_store_id": store,
                    "visit_datetime": pd.date_range("2016-01-05", periods=n, freq="D"),
                    "reserve_datetime": pd.date_range("2016-01-01", periods=n, freq="D"),
                    "reserve_visitors": rng.integers(1, 8, size=n),
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def clean_visits(visits_df: pd.DataFrame) -> pd.DataFrame:
    """The same visits with the injected defects removed (all checks pass)."""
    v = visits_df[~(visits_df["air_store_id"] == "air_unknown")]
    v = v[v["visitors"] >= 0]
    v = v.drop_duplicates(subset=["air_store_id", "visit_date"], keep="first")
    return v


@pytest.fixture
def visits_two_stores() -> pd.DataFrame:
    """Two stores x 70 days with a weekend signal - shared by model/SHAP tests."""
    rng = np.random.default_rng(11)
    dates = pd.date_range("2016-01-01", periods=70, freq="D")
    frames = []
    for i, store in enumerate(("air_a", "air_b")):
        weekend = np.where(dates.dayofweek >= 5, 12, 0)
        vals = rng.poisson(20 + i * 5 + weekend)
        frames.append(
            pd.DataFrame({"air_store_id": store, "visit_date": dates, "visitors": vals})
        )
    return pd.concat(frames, ignore_index=True)


@pytest.fixture
def recruit_csvs(tmp_path, visits_df, stores_df, calendar_df, reservations_df):
    """Cleaned synthetic Recruit CSVs on disk (hard audit failures removed)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    clean = (
        visits_df[visits_df["air_store_id"] != "air_unknown"]
        .query("visitors >= 0")
        .drop_duplicates(subset=["air_store_id", "visit_date"], keep="first")
    )
    clean.to_csv(tmp_path / "air_visit_data.csv", index=False)
    stores_df.to_csv(tmp_path / "air_store_info.csv", index=False)
    calendar_df.to_csv(tmp_path / "date_info.csv", index=False)
    reservations_df.to_csv(tmp_path / "air_reserve.csv", index=False)
    return tmp_path
