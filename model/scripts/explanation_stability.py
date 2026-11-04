"""Explanation stability (Objective 6) from the saved SHAP contributions.

The objective asks whether the same features lead the explanation when the data
changes. The run does not store a separate SHAP pass per backtest fold or per
seed, so this script answers the closest question the saved artifacts can
answer: split the test window into six contiguous date blocks (the same number
as the backtest folds), rank features by mean absolute attribution inside each
block, and correlate the rankings between every pair of blocks.

Run:  python scripts/explanation_stability.py
"""

from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

RUN = Path(__file__).resolve().parents[1] / "artifacts" / "runs" / "latest"
BLOCKS = 6


def main() -> None:
    contrib = pd.read_parquet(RUN / "shap_contributions.parquet")
    contrib["visit_date"] = pd.to_datetime(contrib["visit_date"])

    # Mean absolute attribution per feature: the quantity the report ranks.
    overall = (
        contrib.groupby("feature_name")["shap_impact"]
        .apply(lambda s: float(np.abs(s).mean()))
        .sort_values(ascending=False)
    )

    dates = np.array(sorted(contrib["visit_date"].unique()))
    edges = np.linspace(0, len(dates), BLOCKS + 1).astype(int)
    blocks = [dates[edges[i] : edges[i + 1]] for i in range(BLOCKS)]

    rankings: list[pd.Series] = []
    print("Block          days  top feature (mean |SHAP|)")
    for i, block in enumerate(blocks, start=1):
        sub = contrib[contrib["visit_date"].isin(block)]
        ranked = (
            sub.groupby("feature_name")["shap_impact"]
            .apply(lambda s: float(np.abs(s).mean()))
            .sort_values(ascending=False)
        )
        rankings.append(ranked)
        print(
            f"block {i}  {block[0].date()}..{block[-1].date()}  "
            f"{len(block):>3}d  {ranked.index[0]} ({ranked.iloc[0]:.3f})"
        )

    rhos = [
        spearmanr(rankings[i], rankings[j]).statistic
        for i, j in itertools.combinations(range(BLOCKS), 2)
    ]
    print(f"\nPairwise Spearman rho across {BLOCKS} blocks (n={len(rhos)} pairs)")
    print(f"  mean   {np.mean(rhos):.3f}")
    print(f"  min    {np.min(rhos):.3f}")
    print(f"  max    {np.max(rhos):.3f}")

    top_overall = list(overall.index[:5])
    print("\nTop 5 features overall:", ", ".join(top_overall))
    # How often each of those five also lands in a block's top five.
    hits = sum(
        1 for r in rankings for f in top_overall if f in list(r.index[:5])
    )
    print(f"They appear in a block's top five {hits} of {5 * BLOCKS} chances"
          f" ({hits / (5 * BLOCKS):.1%}).")


if __name__ == "__main__":
    main()
