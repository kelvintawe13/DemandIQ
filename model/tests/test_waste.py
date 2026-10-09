"""URBAN-WASTE FTD tests — the second dataset (proposal Sections 3.4.2, 3.7.2, 3.21).

Two layers, deliberately:

* A synthetic FTD fixture (semicolon-separated, latin-1, comma decimals,
  ``DD/MM/YYYY`` timestamps) exercises the parser, the audited-figure gates and
  the descriptive summary without the 172 KB Zenodo download.
* The real CSV, when present on disk, is run through the same gates so the
  figures quoted in the proposal cannot silently drift. It is skipped in CI
  because ``*.csv`` is gitignored (see the repo ``.gitignore``).

The evidence boundary (Figure 4) is asserted here too: no URBAN-WASTE column is
a Recruit join key, and neither loader calls the other.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pandas as pd
import pytest

MODEL_ROOT = Path(__file__).resolve().parents[1]
FTD_FILENAME = "Food Tracking Device dataset.csv"
REAL_FTD = MODEL_ROOT / "data" / "urban-waste" / FTD_FILENAME
REAL_RECRUIT = MODEL_ROOT / "data" / "recruit"


@pytest.fixture(scope="module")
def waste():
    """Import ``scripts/analyze_waste.py`` by path (it is a script, not a package)."""
    script = MODEL_ROOT / "scripts" / "analyze_waste.py"
    spec = importlib.util.spec_from_file_location("analyze_waste", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ftd_csv(tmp_path, waste) -> Path:
    """A minimal FTD CSV in the real encoding, with two pilot locations."""
    rows = []
    # 12 rows, two pilot locations, six establishments: enough for the
    # min_obs=5 guard in by_group to emit a per-location breakdown.
    waste_kg: list[float | None] = [10.5, 4.25, 0.0, None, 22.0, 31.75, 11.5, 5.25, 1.0, 6.0, 23.0, 32.75]
    dates = pd.date_range("2018-03-02", periods=12, freq="D")
    for i, day in enumerate(dates):
        rows.append(
            {
                "Timestamp": day.strftime("%d/%m/%Y") + " 12:30:00",
                # Comma decimal separator, as published.
                "Waste weight sum": "" if waste_kg[i] is None else str(waste_kg[i]).replace(".", ","),
                "City / kitchen": f"City{i % 2} / Kitchen {i % 3}",
                "portions served": str(20 + i),
                # Sparse ordered portions: only one row carries a value.
                "ordered portions 1": "5" if i == 0 else "",
                "ordered portions 2": "",
            }
        )
    frame = pd.DataFrame(rows)
    path = tmp_path / FTD_FILENAME
    frame.to_csv(path, sep=";", index=False, encoding="latin-1")
    return path


def test_load_ftd_parses_published_encoding(ftd_csv, waste):
    df = waste.load_ftd(ftd_csv)

    assert len(df) == 12
    assert df["waste_kg"].dtype.kind == "f"
    # Comma decimals became floats, and the blank cells are missing, not zero.
    assert df["waste_kg"].tolist()[:3] == [10.5, 4.25, 0.0]
    assert int(df["waste_kg"].isna().sum()) == 1
    assert str(df["date"].dtype).startswith("datetime64")
    # "City / kitchen" splits into pilot location and establishment.
    assert set(df["pilot_location"]) == {"City0", "City1"}
    assert df["establishment"].nunique() == 6


def test_load_ftd_rejects_unparseable_dates(tmp_path, waste):
    bad = pd.DataFrame(
        {
            "Timestamp": ["not-a-date"],
            "Waste weight sum": ["1,0"],
            "City / kitchen": ["City0 / Kitchen 0"],
            "portions served": ["1"],
            "ordered portions 1": [""],
        }
    )
    path = tmp_path / FTD_FILENAME
    bad.to_csv(path, sep=";", index=False, encoding="latin-1")

    with pytest.raises(SystemExit, match="unparseable dates"):
        waste.load_ftd(path)


def test_load_ftd_rejects_negative_waste(tmp_path, waste):
    bad = pd.DataFrame(
        {
            "Timestamp": ["02/03/2018"],
            "Waste weight sum": ["-3,5"],
            "City / kitchen": ["City0 / Kitchen 0"],
            "portions served": ["1"],
            "ordered portions 1": [""],
        }
    )
    path = tmp_path / FTD_FILENAME
    bad.to_csv(path, sep=";", index=False, encoding="latin-1")

    with pytest.raises(SystemExit, match="negative waste"):
        waste.load_ftd(path)


def test_audit_gates_fail_loudly_on_a_short_file(ftd_csv, waste):
    """The gates compare against the proposal's audited figures, not a range."""
    df = waste.load_ftd(ftd_csv)
    with pytest.raises(SystemExit, match="URBAN-WASTE audit failed"):
        waste.audit_gates(df)


def test_summarize_characterizes_observed_waste(ftd_csv, waste):
    summary = waste.summarize(waste.load_ftd(ftd_csv))

    assert summary["total_records"] == 12
    assert summary["establishments"] == 6
    assert summary["pilot_locations"] == 2
    assert summary["nonmissing_waste"] == 11
    assert summary["missing_waste"] == 1
    assert summary["zero_waste_days"] == 1
    assert summary["mean_kg"] == round(summary["mean_kg"], 3)
    assert set(summary["quantiles_kg"]) == {"p25", "p75", "p90", "p95"}
    # Ordered portions are too sparse for supervised use — the summary states
    # the overlap rather than hiding it.
    assert summary["rows_with_any_ordered_portions"] == 1
    assert summary["rows_waste_and_ordered"] == 1
    assert summary["provenance"]["licence"] == "CC BY 4.0"
    assert summary["provenance"]["source_dataset"] == "urban-waste-ftd"
    # Descriptive only: no per-row linkage back to demand.
    assert {g["group"] for g in summary["by_pilot_location"]} == {"City0", "City1"}
    assert all(set(g) >= {"n", "median_kg", "max_kg"} for g in summary["by_pilot_location"])


# --- Evidence boundary (Figure 4): never joined to Recruit --------------------


def test_waste_columns_share_no_recruit_join_key(ftd_csv, waste):
    """No URBAN-WASTE column can join a Recruit row, by construction."""
    from diq_model.data.load import load_recruit

    waste_cols = set(waste.load_ftd(ftd_csv).columns)
    recruit_source = inspect.getsource(load_recruit)
    # Recruit identifies rows by (store, date); URBAN-WASTE by (city/kitchen, timestamp).
    for key in ("air_store_id", "visitors", "visit_date", "reserve_visitors"):
        assert key not in waste_cols
    for key in ("Waste weight sum", "waste_kg", "City / kitchen", "pilot_location"):
        assert key not in recruit_source
    # Neither loader imports the other.
    assert "analyze_waste" not in recruit_source
    assert "load_recruit" not in inspect.getsource(waste.load_ftd)


# --- Real data, when present -------------------------------------------------


requires_ftd = pytest.mark.skipif(
    not REAL_FTD.exists(),
    reason=(
        f"{REAL_FTD.relative_to(MODEL_ROOT)} missing — download the URBAN-WASTE FTD CSV "
        "(Zenodo 3355300) to exercise the real dataset; CI skips it (CSV gitignored)"
    ),
)


@requires_ftd
def test_real_ftd_matches_the_proposal_s_audited_figures(waste):
    """Section 3.4.2: 3,321 records, 34 establishments, 3,160 waste values."""
    from diq_model.constants import (
        URBAN_WASTE_ESTABLISHMENTS,
        URBAN_WASTE_NONMISSING_WASTE,
        URBAN_WASTE_PILOT_LOCATIONS,
        URBAN_WASTE_ROWS,
        URBAN_WASTE_WASTE_AND_ORDERED,
    )

    df = waste.load_ftd(REAL_FTD)
    waste.audit_gates(df)  # raises SystemExit if any figure drifted

    assert len(df) == URBAN_WASTE_ROWS
    assert df["establishment"].nunique() == URBAN_WASTE_ESTABLISHMENTS
    assert df["pilot_location"].nunique() == URBAN_WASTE_PILOT_LOCATIONS
    assert int(df["waste_kg"].notna().sum()) == URBAN_WASTE_NONMISSING_WASTE
    assert int((df["waste_kg"].notna() & df[[c for c in df if c.startswith("ordered portions")]].notna().any(axis=1)).sum()) == URBAN_WASTE_WASTE_AND_ORDERED

    summary = waste.summarize(df)
    assert summary["total_records"] == URBAN_WASTE_ROWS
    assert summary["by_pilot_location"], "real run must yield a per-location breakdown"
    assert 2018 <= int(summary["date_min"][:4]) <= int(summary["date_max"][:4]) + 1
