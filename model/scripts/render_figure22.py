"""Redraw Figure 22 (Range Coverage by Demand Level) from the run's artifact.

Both series use the bins the pipeline itself uses — ``CoverageByLevel`` in
``diq_model/uncertainty/conformal.py``, which are half-open::

    [0, 15)  [15, 30)  [30, 60)  [60, inf)

So a day with 15 visitors falls in the second band, not the first. The left
bars group by the model's own ``calibration_bucket`` (the stratum assigned from
the prediction), which is what "by predicted demand" means; the right bars use
``CoverageByLevel`` on realized demand.

The script asserts its realized numbers against the run manifest, so it fails
loudly if the figure ever drifts from the pipeline. It writes the same 1272x800
image the document embeds.

Usage:  python scripts/render_figure22.py [out.png]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from diq_model.uncertainty.conformal import CoverageByLevel  # noqa: E402

W, H = 1272, 800
TARGET = 0.90
RUN = Path("artifacts/runs/latest")
LABELS = ["0-15", "15-30", "30-60", "60+"]


def main(out: str) -> int:
    artifact = RUN / "conformal_predictions.parquet"
    if not artifact.exists():
        print(f"missing artifact: {artifact}", file=sys.stderr)
        return 1
    df = pd.read_parquet(artifact)
    df["hit"] = (df["visitors"] >= df["lower"]) & (df["visitors"] <= df["upper"])

    # Right bars: realized demand, binned by the pipeline's own rule.
    realized = CoverageByLevel().coverage(
        df["visitors"].to_numpy(float),
        df["lower"].to_numpy(float),
        df["upper"].to_numpy(float),
    )
    # Left bars: the stratum the model assigned from its prediction.
    predicted = df.groupby("calibration_bucket")["hit"].mean().to_dict()

    missing = [k for k in LABELS if k not in realized or k not in predicted]
    if missing:
        print(f"artifact has no rows for {missing}", file=sys.stderr)
        return 1

    manifest = json.loads((RUN / "manifest.json").read_text())
    recorded = manifest["stages"]["conformal"]["metrics"]["coverage_by_demand_level"]
    drift = {k: (round(realized[k], 4), recorded[k]) for k in LABELS
             if abs(realized[k] - recorded[k]) > 5e-4}
    if drift:
        print(f"figure would disagree with the manifest: {drift}", file=sys.stderr)
        return 1

    plt.rcParams.update({"font.family": "serif", "font.size": 17})
    fig, ax = plt.subplots(figsize=(W / 100, H / 100), dpi=100)
    x = range(len(LABELS))
    w = 0.40
    bars_a = ax.bar([i - w / 2 for i in x], [100 * predicted[k] for k in LABELS], w,
                    color="#e9e9e9", edgecolor="black", linewidth=1.1,
                    label="By predicted demand (known at forecast time)")
    bars_b = ax.bar([i + w / 2 for i in x], [100 * realized[k] for k in LABELS], w,
                    color="#8f8f8f", edgecolor="black", linewidth=1.1, hatch="\\\\",
                    label="By realized demand (known afterwards)")

    for bars in (bars_a, bars_b):
        for bar in bars:
            ax.annotate(f"{bar.get_height():.1f}",
                        (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        textcoords="offset points", xytext=(0, 4),
                        ha="center", fontsize=15)

    ax.axhline(TARGET * 100, color="black", linestyle=(0, (4, 2.5)), linewidth=1.4)
    ax.annotate("90% target", (len(LABELS) - 0.45, TARGET * 100),
                textcoords="offset points", xytext=(0, 6), ha="right", fontsize=15)

    ax.set_xticks(list(x))
    ax.set_xticklabels(LABELS)
    ax.set_ylim(0, 107)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.set_yticklabels(["0.0", "0.2", "0.4", "0.6", "0.8", "1.0"])
    ax.set_xlabel("Demand level (visitors per restaurant-day)", fontsize=19)
    ax.set_ylabel("Empirical coverage", fontsize=19)
    ax.grid(axis="y", color="#dddddd", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False, fontsize=15)

    fig.tight_layout()
    # Fixed size: the document sets this image's drawing extent, so a cropped
    # save would change the aspect ratio and stretch the figure on the page.
    fig.savefig(out, dpi=100, facecolor="white")
    plt.close(fig)

    print("predicted:", {k: round(100 * predicted[k], 1) for k in LABELS})
    print("realized: ", {k: round(100 * realized[k], 1) for k in LABELS})
    print("overall:   %.2f%%" % (100 * df["hit"].mean()))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fig22_check.png"))
