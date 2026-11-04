"""Paired per-restaurant tests (Table 10) and cost-ratio sweep (Table 11).

Reads the saved artifacts of a training run; no model is refit.

    .venv/bin/python scripts/extra_analyses.py [artifacts/runs/latest]
"""

from __future__ import annotations

import sys
from pathlib import Path

import joblib
import pandas as pd
from scipy.stats import wilcoxon

KEYS = ["air_store_id", "visit_date"]
RATIOS = [0.5, 1, 2, 3, 5, 10]


def abs_error(path: Path, name: str) -> pd.DataFrame:
    d = pd.read_parquet(path)[KEYS + ["visitors", "prediction"]]
    d[name] = (d["visitors"] - d["prediction"]).abs()
    return d[KEYS + [name]]


def paired_tests(run: Path) -> None:
    m = (
        abs_error(run / "lightgbm_predictions.parquet", "lgb")
        .merge(abs_error(run / "catboost_predictions.parquet", "cb"), on=KEYS)
        .merge(abs_error(run / "baseline_predictions.parquet", "wm"), on=KEYS)
    )
    per = m.groupby("air_store_id")[["lgb", "cb", "wm"]].mean()
    print(f"restaurants: {len(per)}")

    def compare(label: str, a: pd.Series, b: pd.Series) -> None:
        diff = a - b
        p = wilcoxon(a, b).pvalue
        print(
            f"{label}: median MAE diff {diff.median():.4f}, "
            f"share better {(diff < 0).mean():.3f}, p={p:.3g}"
        )

    compare("LightGBM vs weekday mean", per["lgb"], per["wm"])
    compare("LightGBM vs CatBoost", per["lgb"], per["cb"])

    # Sparse restaurants: share of observed days over each restaurant's own
    # first-to-last date span, from the raw visits file (report Section 4.3.5).
    visits = pd.read_parquet(run / "visits.parquet")
    dates = pd.to_datetime(visits["visit_date"])
    span = visits.assign(d=dates).groupby("air_store_id")["d"].agg(["min", "max", "count"])
    observed = span["count"] / ((span["max"] - span["min"]).dt.days + 1)
    low = per.index.intersection(observed[observed < 0.8].index)
    sub = per.loc[low]
    compare(f"LightGBM vs weekday mean, {len(low)} restaurants below 80% coverage", sub["lgb"], sub["wm"])


def cost_sweep(run: Path) -> None:
    policies = joblib.load(run / "policy_results.joblib").set_index("policy")
    print("cost ratio r -> total cost (surplus + r * unmet demand)")
    for r in RATIOS:
        totals = policies["surplus_ps"] + r * policies["unmet_ud"]
        print(f"  r={r}: " + ", ".join(f"{k} {v:,.0f}" for k, v in totals.items()))
    pt = policies.loc["point_forecast"]
    ub = policies.loc["uncertainty_aware-q0.6"]
    breakeven = (ub["surplus_ps"] - pt["surplus_ps"]) / (pt["unmet_ud"] - ub["unmet_ud"])
    print(f"break-even ratio, uncertainty buffer vs point forecast: {breakeven:.2f}")


def main() -> None:
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/runs/latest")
    paired_tests(run)
    cost_sweep(run)


if __name__ == "__main__":
    main()
