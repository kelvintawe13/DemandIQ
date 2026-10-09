"""Recruit dataset loaders (Sections 3.4.1, 3.6, 3.7.1).

All loaders normalise into the canonical daily-demand shape the rest of the
pipeline consumes:

    air_visit_data.csv -> visits:       air_store_id, visit_date, visitors
    air_store_info.csv -> stores:       air_store_id, air_genre_name, air_area_name
    date_info.csv      -> calendar:     calendar_date, holiday_flg
    air_reserve.csv    -> reservations: air_store_id, visit_datetime, reserve_datetime, reserve_visitors

Paths are environment-agnostic: locally pass the extracted Kaggle folder,
on Colab the notebook mounts Drive and passes the same folder. Nothing here
knows about notebooks, GPUs, or the cloud.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from diq_model.constants import (
    CALENDAR_FILE,
    RESERVE_FILE,
    STORE_FILE,
    VISIT_FILE,
)

logger = logging.getLogger(__name__)

# Canonical dtypes enforced at load time so downstream code never re-guesses.
VISIT_DTYPES = {"air_store_id": "string", "visitors": "int64"}
STORE_DTYPES = {"air_store_id": "string", "air_genre_name": "string", "air_area_name": "string"}
CALENDAR_DTYPES = {"holiday_flg": "int8"}
RESERVE_DTYPES = {
    "air_store_id": "string",
    "reserve_visitors": "int64",
}


@dataclass(frozen=True)
class RecruitData:
    """The four Recruit tables, normalised and typed."""

    visits: pd.DataFrame
    stores: pd.DataFrame
    calendar: pd.DataFrame
    reservations: pd.DataFrame

    @property
    def n_stores(self) -> int:
        return int(self.visits["air_store_id"].nunique())

    @property
    def n_rows(self) -> int:
        return int(len(self.visits))


def _require(name: str) -> Path:
    path = Path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"Required Recruit file '{name}' not found. Expected the extracted "
            "Kaggle competition folder (air_visit_data.csv, air_store_info.csv, "
            "date_info.csv, air_reserve.csv)."
        )
    return path


def load_visits(root: str | Path) -> pd.DataFrame:
    """air_visit_data.csv -> visits with a proper datetime ``visit_date``."""
    path = _require(Path(root) / VISIT_FILE)
    df = pd.read_csv(path, dtype=VISIT_DTYPES, parse_dates=["visit_date"])
    return df


def load_stores(root: str | Path) -> pd.DataFrame:
    """air_store_info.csv -> static store attributes (genre, area)."""
    path = _require(Path(root) / STORE_FILE)
    return pd.read_csv(path, dtype=STORE_DTYPES)


def load_calendar(root: str | Path) -> pd.DataFrame:
    """date_info.csv -> calendar with ``calendar_date`` as datetime."""
    path = _require(Path(root) / CALENDAR_FILE)
    return pd.read_csv(path, dtype=CALENDAR_DTYPES, parse_dates=["calendar_date"])


def load_reservations(root: str | Path) -> pd.DataFrame:
    """air_reserve.csv -> reservations with datetimes parsed.

    Missing file is tolerated: reservation features are supplementary
    (Section 3.10), and only 314 of 821 stores have logs anyway.
    """
    path = Path(root) / RESERVE_FILE
    if not path.exists():
        logger.warning("air_reserve.csv not found - reservations loaded as empty frame")
        return pd.DataFrame(
            {
                "air_store_id": pd.Series(dtype="string"),
                "visit_datetime": pd.Series(dtype="datetime64[ns]"),
                "reserve_datetime": pd.Series(dtype="datetime64[ns]"),
                "reserve_visitors": pd.Series(dtype="int64"),
            }
        )
    return pd.read_csv(path, dtype=RESERVE_DTYPES, parse_dates=["visit_datetime", "reserve_datetime"])


def load_recruit(root: str | Path) -> RecruitData:
    """Load all four Recruit tables from an extracted dataset folder."""
    root = Path(root)
    data = RecruitData(
        visits=load_visits(root),
        stores=load_stores(root),
        calendar=load_calendar(root),
        reservations=load_reservations(root),
    )
    logger.info(
        "Loaded Recruit: %d visit rows, %d stores, %d reservation rows",
        data.n_rows,
        data.n_stores,
        len(data.reservations),
    )
    return data
