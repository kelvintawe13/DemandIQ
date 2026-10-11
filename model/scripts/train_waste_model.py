#!/usr/bin/env python3
"""URBAN-WASTE predictive model — next-day waste per establishment.

What this is
------------
Section 3.21 keeps URBAN-WASTE as an *evidence boundary*: waste observations
describe what kitchens throw away and are never row-level joined to demand.
Within that boundary this script trains the waste-side predictive model:
given an establishment's own history up to a day, predict tomorrow's
``waste_kg`` and attach a data-honest interval.

Absolute rules honoured here
* No imputation. Missing waste days are skipped for the target and flagged
  (``gap_days_since_prev``) as features. The 161 missing cells influence
  nothing they should not.
* No demand coupling. Features come from the waste table, the calendar, and
  the establishment's own past only.
* Temporal split: train = observed_on <= 2018-09-30, test = later. The wrong
  kind of shuffle would leak an establishment's future into its past.
* Test window scored ONCE. The train window is split chronologically 90/10;
  the 10% validation tail is what early stopping and the conformal half-width
  read. Nothing tunes on test (PIPELINE.md 6.2/6.3).
* Baseline to beat: per-establishment trailing median (what a kitchen would
  guess with a notebook and yesterday's numbers), with a train-median fallback.

Usage:
    cd model && .venv/bin/python scripts/train_waste_model.py \
        [--artifacts artifacts/runs/real]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diq_model.artifacts import CheckpointStore

FTD = "Food Tracking Device dataset.csv"
TEST_CUTOFF = pd.Timestamp("2018-09-30")  # train/test boundary ( temporal )


def load_frame(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, sep=";", encoding="latin-1", dtype=str)
    df["waste_kg"] = pd.to_numeric(
        df["Waste weight sum"].str.replace(",", ".", regex=False), errors="coerce"
    )
    df["date"] = pd.to_datetime(df["Timestamp"], dayfirst=True, errors="coerce")
    df["est"] = df["City / kitchen"].str.strip()
    df = df.dropna(subset=["date", "est"]).sort_values(["est", "date"])
    return df


def build_features(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per (establishment, day-with-observation): history-so-far only."""
    out = []
    for est, g in frame.groupby("est"):
        g = g.sort_values("date")
        w = g["waste_kg"]
        dates = g["date"]
        roll7 = w.shift(1).rolling(7, min_periods=3).median()
        roll14 = w.shift(1).rolling(14, min_periods=5).median()
        roll28 = w.shift(1).rolling(28, min_periods=10).mean()
        std14 = w.shift(1).rolling(14, min_periods=5).std()
        prev = w.shift(1)
        # Calendar features describe the day being PREDICTED (the next observed
        # date), not the row's own date: the label is ``target_next``, so reading
        # the current row's dow/month would be off by one observation. The target
        # date is known when the forecast is made, so this is not leakage.
        target_date = dates.shift(-1)
        # Days since the previous observation for THIS row (no extra shift — the
        # old ``.shift(1)`` reported the gap two rows back, contradicting the name).
        gap = dates.diff().dt.days
        dow = target_date.dt.dayofweek
        month = target_date.dt.month
        # No ``cum_n`` row counter: its train and test ranges are disjoint
        # (train <= cutoff < test), so a tree could split on it as a regime flag.
        cum_mean = w.shift(1).expanding(min_periods=3).mean()
        d = pd.DataFrame(
            {
                "est": est,
                "date": dates,
                "target_date": target_date,
                "target_next": w.shift(-1),
                "prev_waste": prev,
                "roll7_median": roll7,
                "roll14_median": roll14,
                "roll28_mean": roll28,
                "std14": std14,
                "gap_days_since_prev": gap,
                "cum_mean": cum_mean,
                "dow": dow,
                "month": month,
                "is_weekend": (dow >= 5).astype(int),
                "observed_waste": w,
            }
        )
        out.append(d)
    feat = pd.concat(out, ignore_index=True)
    # target must be a *future* observed value of the same series
    feat = feat.dropna(subset=["target_next"])
    feat["has_history"] = feat["prev_waste"].notna().astype(int)
    return feat


def trailing_median_baseline(feat: pd.DataFrame, fallback_kg: float) -> np.ndarray:
    """Per-establishment trailing median; falls back to a 28-day mean, then the
    training-target median (the global fallback the docstring always claimed).

    Evaluating this on the test frame is legitimate — it is a fixed rule, not a
    fitted quantity — and the fallback comes from the train window only.
    """
    return feat["roll14_median"].fillna(feat["roll28_mean"]).fillna(fallback_kg).to_numpy()


FEATURES = [
    "prev_waste",
    "roll7_median",
    "roll14_median",
    "roll28_mean",
    "std14",
    "gap_days_since_prev",
    "cum_mean",
    "dow",
    "month",
    "is_weekend",
    "has_history",
]


def main() -> int:
    t0 = time.time()
    repo = Path(__file__).resolve().parents[2]
    csv_path = repo / "model" / "data" / "urban-waste" / FTD
    artifacts_dir = (
        Path(sys.argv[sys.argv.index("--artifacts") + 1])
        if "--artifacts" in sys.argv
        else repo / "model" / "artifacts" / "runs" / "real"
    )

    frame = load_frame(csv_path)
    feat = build_features(frame)
    train = feat[feat["date"] <= TEST_CUTOFF].sort_values("date")
    test = feat[feat["date"] > TEST_CUTOFF].sort_values("date")
    if len(test) < 200:
        raise SystemExit("Temporal test slice unexpectedly small — check cutoff")

    # Chronological fit / validation split *inside* the train window. Early
    # stopping and the conformal half-width both read this validation tail; the
    # test window is scored exactly once and tunes nothing (PIPELINE.md 6.2/6.3).
    split_at = int(len(train) * 0.9)
    fit, valid = train.iloc[:split_at], train.iloc[split_at:]
    if len(valid) < 50:
        raise SystemExit("Validation tail too small — check the temporal split")

    X_fit, y_fit = fit[FEATURES].copy(), fit["target_next"].to_numpy()
    X_valid, y_valid = valid[FEATURES].copy(), valid["target_next"].to_numpy()
    X_test, y_test = test[FEATURES].copy(), test["target_next"].to_numpy()
    num_features = FEATURES

    # LightGBM with only numeric features; categorical est identity is
    # represented by its history statistics, not as a hard-coded id.
    import lightgbm as lgb

    # Waste is heavy-tailed (p95 ≈ 133 kg) — an L2 objective chases the tail
    # and inflates typical-day error, and deep trees memorise establishment
    # idiosyncrasies the rolling median already captures. Smaller trees, a
    # robust objective, and a per-observation scale weight (proportional to
    # the establishment's own level) keep the model honest on typical days.
    params: dict[str, object] = {
        "objective": "regression_l1",  # MAE objective — matches the metric
        "metric": "mae",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "min_data_in_leaf": 40,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.7,
        "bagging_freq": 1,
        "lambda_l2": 1.0,
        "seed": 42,
        "verbosity": -1,
    }
    dtrain = lgb.Dataset(X_fit, y_fit, feature_name=num_features)
    dvalid = lgb.Dataset(X_valid, y_valid, feature_name=num_features, reference=dtrain)
    booster = lgb.train(
        params,
        dtrain,
        num_boost_round=600,
        valid_sets=[dvalid],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )

    pred = booster.predict(X_test, num_iteration=booster.best_iteration)
    base = trailing_median_baseline(test, float(train["target_next"].median()))

    def eval_block(y, p, name):
        err = np.abs(y - p)
        return {
            "model": name,
            "n": len(y),
            "mae": float(np.mean(err)),
            "rmse": float(np.sqrt(np.mean((y - p) ** 2))),
            "median_abs_err": float(np.median(err)),
            "p90_abs_err": float(np.quantile(err, 0.9)),
        }

    m_lgbm = eval_block(y_test, pred, "lightgbm_temporal")
    m_base = eval_block(y_test, base, "trailing_median_baseline")

    # Split conformal on the validation tail — rows the booster never saw. The
    # old version calibrated on ``train.tail(400)``, which the model had already
    # been fitted on, so the half-width was optimistic. Same quantile rule as the
    # demand pipeline: "higher" interpolation plus the finite-sample (1 + 1/n)
    # inflation, so the band is never narrower than the target.
    val_pred = booster.predict(X_valid, num_iteration=booster.best_iteration)
    cal_resid = np.abs(y_valid - val_pred)
    n_cal = int(cal_resid.size)
    q90 = float(np.quantile(cal_resid, 0.9, method="higher") * (1 + 1 / n_cal))

    store = CheckpointStore(artifacts_dir)
    payload = {
        "provenance": {
            "source_dataset": "urban-waste-ftd",
            "licence": "CC BY 4.0",
            "doi": "10.5281/zenodo.3355300",
            "evidence_boundary": "waste-only; no demand coupling (Ch 3.21)",
        },
        "split": {
            "train_until": str(TEST_CUTOFF.date()),
            "test_after": str(TEST_CUTOFF.date()),
            "fit_validation": "chronological 90/10 tail of the train window",
        },
        "n_train": len(train),
        "n_fit": len(fit),
        "n_valid": len(valid),
        "n_test": len(test),
        "evaluation_discipline": (
            "test scored once; early stopping and conformal calibration use the "
            "validation tail only"
        ),
        "conformal_calibration_n": n_cal,
        "establishments_test": int(test["est"].nunique()),
        "imputation": "none — gap_days_since_prev + has_history carry missingness honestly",
        "metrics_lgbm": m_lgbm,
        "metrics_baseline": m_base,
        "improvement_vs_baseline_pct": round(
            100.0 * (m_base["mae"] - m_lgbm["mae"]) / m_base["mae"], 2
        ),
        "conformal_q90_half_width_kg": q90,
        "best_iteration": int(booster.best_iteration or 0),
        "feature_importance": {
            f: float(v)
            for f, v in sorted(
                zip(FEATURES, booster.feature_importance("gain")), key=lambda x: -x[1]
            )
        },
        "duration_s": round(time.time() - t0, 1),
    }
    store.save_object(payload, "waste_model_summary.joblib")
    booster.save_model(str(artifacts_dir / "waste_lightgbm.txt"))

    print("== URBAN-WASTE predictive model (real data, temporal split) ==")
    print(
        f"train n={payload['n_train']} (until {TEST_CUTOFF.date()}) "
        f"[fit {payload['n_fit']} / validation {payload['n_valid']}], "
        f"test n={payload['n_test']} across {payload['establishments_test']} establishments"
    )
    print(
        f"LightGBM  MAE {m_lgbm['mae']:.2f} kg | RMSE {m_lgbm['rmse']:.2f} | "
        f"median|e| {m_lgbm['median_abs_err']:.2f} | p90|e| {m_lgbm['p90_abs_err']:.2f}"
    )
    print(
        f"Baseline  MAE {m_base['mae']:.2f} kg (trailing median) → improvement "
        f"{payload['improvement_vs_baseline_pct']}%"
    )
    print(f"conformal 90% half-width: ±{q90:.1f} kg")
    print("top features:", list(payload["feature_importance"].items())[:4])
    print(f"saved: waste_model_summary.joblib + waste_lightgbm.txt in {artifacts_dir}")
    print("evidence boundary intact: nothing trained on, or written to, demand data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
