"""Loader tests: writes real CSVs to a temp dir and reads them back."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

MODEL_ROOT = Path(__file__).resolve().parents[1]
REAL_RECRUIT = MODEL_ROOT / "data" / "recruit"


def test_load_recruit_roundtrip(tmp_path, visits_df, stores_df, calendar_df, reservations_df):
    visits_df.to_csv(tmp_path / "air_visit_data.csv", index=False)
    stores_df.to_csv(tmp_path / "air_store_info.csv", index=False)
    calendar_df.to_csv(tmp_path / "date_info.csv", index=False)
    reservations_df.to_csv(tmp_path / "air_reserve.csv", index=False)

    from diq_model.data.load import load_recruit

    data = load_recruit(tmp_path)

    assert data.n_rows == len(visits_df)
    assert data.n_stores == visits_df["air_store_id"].nunique()
    assert str(data.visits["visit_date"].dtype).startswith("datetime64")
    assert data.visits["visitors"].dtype == "int64"


def test_load_reservations_tolerates_missing_file(tmp_path, visits_df, stores_df, calendar_df):
    visits_df.to_csv(tmp_path / "air_visit_data.csv", index=False)
    stores_df.to_csv(tmp_path / "air_store_info.csv", index=False)
    calendar_df.to_csv(tmp_path / "date_info.csv", index=False)
    # No air_reserve.csv written.

    from diq_model.data.load import load_recruit

    data = load_recruit(tmp_path)
    assert len(data.reservations) == 0


def test_missing_visit_file_raises(tmp_path):
    from diq_model.data.load import load_recruit

    with pytest.raises(FileNotFoundError):
        load_recruit(tmp_path)


# --- Real data, when present -------------------------------------------------
# CI skips this because the raw Kaggle CSVs are gitignored (see .gitignore).
# Locally it is the check that the dataset quoted in the proposal is the one the
# pipeline actually reads: 252,108 observations over 829 restaurants.


@pytest.mark.skipif(
    not (REAL_RECRUIT / "air_visit_data.csv").exists(),
    reason=(
        "model/data/recruit/air_visit_data.csv missing — fetch the Recruit Kaggle extract "
        "to exercise the real dataset; CI skips it (CSVs gitignored)"
    ),
)
def test_real_recruit_matches_the_proposal_s_audited_figures():
    from diq_model.data.load import load_recruit

    data = load_recruit(REAL_RECRUIT)

    assert data.n_rows == 252_108
    assert data.n_stores == 829
    # Calendar covers 2016-01-01 -> 2017-04-22 (proposal Section 3.4.1).
    assert str(data.visits["visit_date"].min())[:10] == "2016-01-01"
    assert str(data.visits["visit_date"].max())[:10] <= "2017-04-22"
    # Reservations are supplementary, not required: the loader tolerates their
    # absence, and where present they must not outnumber visits.
    if len(data.reservations):
        assert {"air_store_id", "visit_datetime", "reserve_visitors"} <= set(data.reservations.columns)
