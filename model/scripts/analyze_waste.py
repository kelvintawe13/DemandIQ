#!/usr/bin/env python3
"""URBAN-WASTE Food Tracking Device — descriptive analysis (Section 3.21).

Loads the FTD CSV (semicolon-separated, latin-1, comma decimals,
DD/MM/YYYY timestamps), runs the Section 3.7.2 preprocessing checks plus the
proposal's audited-figure gates, and persists a summary dictionary to the
checkpoint store. This analysis is strictly independent of the Recruit
pipeline: no row-level linkage exists (evidence boundary, Figure 4), and all
waste figures here characterise observed waste only — never labels for the
demand models.

Usage:
    python scripts/analyze_waste.py                       # artifacts/runs/latest
    python scripts/analyze_waste.py --data-root data/urban-waste
    python scripts/analyze_waste.py --csv "path/to/Food Tracking Device dataset.csv"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

# Allow running as a plain script (python scripts/analyze_waste.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diq_model.artifacts import CheckpointStore
from diq_model.constants import (
    URBAN_WASTE_DOI,
    URBAN_WASTE_ESTABLISHMENTS,
    URBAN_WASTE_NONMISSING_WASTE,
    URBAN_WASTE_PILOT_LOCATIONS,
    URBAN_WASTE_ROWS,
    URBAN_WASTE_WASTE_AND_ORDERED,
)

FTD_FILENAME = "Food Tracking Device dataset.csv"
PROVENANCE = {
    "source_dataset": "urban-waste-ftd",
    "title": "Food Tracking Device data",
    "creators": "Eriksson & Lindgren (2019)",
    "licence": "CC BY 4.0",
    "doi": URBAN_WASTE_DOI,
}


def load_ftd(csv_path: Path) -> pd.DataFrame:
    """Load the FTD CSV with Section 3.7.2 preprocessing applied."""
    df = pd.read_csv(csv_path, sep=";", encoding="latin-1", dtype=str)
    df["waste_kg"] = pd.to_numeric(
        df["Waste weight sum"].str.replace(",", ".", regex=False), errors="coerce"
    )
    df["date"] = pd.to_datetime(df["Timestamp"], dayfirst=True, errors="coerce")
    df["pilot_location"] = df["City / kitchen"].str.split(" / ").str[0].str.strip()
    df["establishment"] = df["City / kitchen"].str.strip()
    if df["date"].isna().any():
        raise SystemExit(f"URBAN-WASTE: {int(df['date'].isna().sum())} rows have unparseable dates")
    if (df["waste_kg"] < 0).any():
        raise SystemExit("URBAN-WASTE: negative waste weights found")
    return df


def audit_gates(df: pd.DataFrame) -> None:
    """Fail loudly if the file deviates from the proposal's audited figures."""
    gates = {
        "records": (len(df), URBAN_WASTE_ROWS),
        "establishments": (df["establishment"].nunique(), URBAN_WASTE_ESTABLISHMENTS),
        "pilot_locations": (df["pilot_location"].nunique(), URBAN_WASTE_PILOT_LOCATIONS),
        "nonmissing_waste": (int(df["waste_kg"].notna().sum()), URBAN_WASTE_NONMISSING_WASTE),
        "waste_and_ordered": (
            int(
                (
                    df["waste_kg"].notna()
                    & df[[c for c in df.columns if c.startswith("ordered portions")]]
                    .notna()
                    .any(axis=1)
                ).sum()
            ),
            URBAN_WASTE_WASTE_AND_ORDERED,
        ),
    }
    failures = [
        f"{name}: got {got}, expected {want}" for name, (got, want) in gates.items() if got != want
    ]
    if failures:
        raise SystemExit(
            "URBAN-WASTE audit failed against proposal Section 3.4.2:\n  " + "\n  ".join(failures)
        )


def summarize(df: pd.DataFrame) -> dict:
    """Descriptive statistics for Section 3.21 (waste characterization only)."""
    w = df["waste_kg"]
    portion_cols = [c for c in df.columns if c.startswith("portions")]
    ordered_cols = [c for c in df.columns if c.startswith("ordered portions")]
    p_any = df[portion_cols].notna().any(axis=1)
    o_any = df[ordered_cols].notna().any(axis=1)

    def by_group(frame: pd.DataFrame, key: str, min_obs: int = 5) -> list[dict]:
        g = frame.groupby(key)["waste_kg"].agg(
            ["count", "mean", "median", "std", "min", "max"]
        )
        g = g[g["count"] >= min_obs]
        g = g.sort_values("median", ascending=False)
        return [
            {
                "group": idx,
                "n": int(r["count"]),
                "mean_kg": round(float(r["mean"]), 2),
                "median_kg": round(float(r["median"]), 2),
                "std_kg": round(float(r["std"]), 2),
                "min_kg": round(float(r["min"]), 2),
                "max_kg": round(float(r["max"]), 2),
            }
            for idx, r in g.iterrows()
        ]

    summary = {
        "provenance": PROVENANCE,
        "total_records": len(df),
        "establishments": int(df["establishment"].nunique()),
        "pilot_locations": int(df["pilot_location"].nunique()),
        "date_min": df["date"].min().strftime("%Y-%m-%d"),
        "date_max": df["date"].max().strftime("%Y-%m-%d"),
        "nonmissing_waste": int(w.notna().sum()),
        "missing_waste": int(w.isna().sum()),
        "zero_waste_days": int((w == 0).sum()),
        "mean_kg": round(float(w.mean()), 3),
        "median_kg": round(float(w.median()), 3),
        "std_kg": round(float(w.std()), 3),
        "min_kg": round(float(w.min()), 3),
        "max_kg": round(float(w.max()), 3),
        "quantiles_kg": {
            f"p{int(q * 100)}": round(float(w.quantile(q)), 2) for q in (0.25, 0.75, 0.9, 0.95)
        },
        "rows_with_any_portions": int(p_any.sum()),
        "rows_with_any_ordered_portions": int(o_any.sum()),
        "rows_waste_and_ordered": int((w.notna() & o_any).sum()),
        "by_pilot_location": by_group(df, "pilot_location"),
        "establishment_level": by_group(df, "establishment", min_obs=5),
        "monthly_median_kg": {
            k.strftime("%Y-%m"): round(float(v), 2)
            for k, v in df.dropna(subset=["waste_kg"])
            .groupby(df["date"].dt.to_period("M"))["waste_kg"]
            .median()
            .items()
        },
    }
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", help="Path to the FTD CSV (default: <data-root>/" + FTD_FILENAME + ")")
    ap.add_argument("--data-root", default="data/urban-waste")
    ap.add_argument("--artifacts", default="artifacts/runs/latest")
    args = ap.parse_args()

    csv_path = Path(args.csv) if args.csv else Path(args.data_root) / FTD_FILENAME
    if not csv_path.exists():
        print(
            f"error: {csv_path} not found — download the URBAN-WASTE FTD CSV first", file=sys.stderr
        )
        return 1

    df = load_ftd(csv_path)
    audit_gates(df)

    started = time.time()
    summary = summarize(df)
    store = CheckpointStore(args.artifacts)
    store.save_object(summary, "waste_summary.joblib")
    store.record_stage(
        "waste_summary",
        started,
        seed=0,  # descriptive stage: deterministic, no randomness involved
        data={"data_root": str(Path(args.data_root)), "csv": csv_path.name},
        metrics={
            "total_records": summary["total_records"],
            "establishments": summary["establishments"],
            "pilot_locations": summary["pilot_locations"],
            "nonmissing_waste": summary["nonmissing_waste"],
            "mean_kg": summary["mean_kg"],
            "median_kg": summary["median_kg"],
        },
    )

    print(f"waste summary written to {args.artifacts}/waste_summary.joblib")
    print(
        f"  records={summary['total_records']}, establishments={summary['establishments']}, "
        f"locations={summary['pilot_locations']}, nonmissing={summary['nonmissing_waste']}"
    )
    print(
        f"  mean={summary['mean_kg']} kg, median={summary['median_kg']} kg, "
        f"sd={summary['std_kg']} kg, max={summary['max_kg']} kg"
    )
    print(f"  period: {summary['date_min']} -> {summary['date_max']}")
    print(json.dumps(summary["by_pilot_location"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
