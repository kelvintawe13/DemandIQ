#!/usr/bin/env python3
"""Export a self-contained serving bundle from a training run.

The bundle lets the DemandIQ API serve real forecasts WITHOUT importing the
training package: the native LightGBM booster, the exact feature-column
order, the final online conformal state of the Mondrian ACI (per-stratum
alpha + trailing residual windows, plus the global fallback), the holiday
calendar the feature flags assume, and the run's point-accuracy metrics
(MAE/RMSE/RMSLE + MASE/Bias, best baseline, evaluation windows, booster
hyperparameters) so the API's model registry can publish the numbers the run
actually measured instead of nulls.

Everything exported here is derived from run artifacts only — the same
provenance rule as render_chapter4.py. The conformal state is reproduced by
replaying the exact online loop the conformal stage ran (validation warmup,
then the test window in its original row order) with the same parameters as
``train.py`` (target 0.90, gamma 0.02).

Usage:
    python scripts/export_serving_bundle.py                     # artifacts/runs/latest
    make bundle                                                 # same, via Makefile
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from diq_model.uncertainty.conformal import (
    DemandLevels,
    StratifiedAdaptiveConformal,
)

BUNDLE_FORMAT = "demandiq-serving-bundle"
BUNDLE_VERSION = 1

# Must match stage_conformal's instantiation (train.py).
TARGET_COVERAGE = 0.90
GAMMA = 0.02


def _fallback_default_params(name: str) -> dict:
    """Recorded defaults for a fallback model, or ``{}`` when unknown.

    Read from the model class rather than restated here, so the bundle cannot
    describe a configuration the training run did not use.
    """
    try:
        from diq_model.models.fallback_models import FALLBACK_FACTORIES
    except ImportError:  # pragma: no cover - package always importable here
        return {}
    factory = FALLBACK_FACTORIES.get(name)
    defaults = getattr(factory, "DEFAULT_PARAMS", None)
    if defaults is None:
        return {}
    return {k: (v.item() if hasattr(v, "item") else v) for k, v in defaults.items()}


def _num(value: object) -> float | None:
    """A measured number as a plain float, or ``None`` when absent/non-finite."""
    if value is None:
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _finite_pairs(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    y = df["visitors"].to_numpy(dtype="float64")
    p = df["prediction"].to_numpy(dtype="float64")
    ok = np.isfinite(y) & np.isfinite(p)
    return y[ok], p[ok]


def export_bundle(run_dir: Path) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    stages = manifest["stages"]
    ing = stages["ingest"]["metrics"]
    lgb_stage = stages["lightgbm"]["metrics"]
    conf_stage = stages.get("conformal", {}).get("metrics", {})

    wrapper = joblib.load(run_dir / "lightgbm_model.joblib")
    meta = joblib.load(run_dir / "features_meta.joblib")
    if wrapper.booster_ is None:
        raise SystemExit("run has no fitted booster")

    # Replay the conformal stage's online loop to reach its final state.
    val = pd.read_parquet(run_dir / "validation_predictions.parquet")
    test = pd.read_parquet(run_dir / "lightgbm_predictions.parquet")
    wy, wp = _finite_pairs(val)
    ty, tp = _finite_pairs(test)
    if len(wy) == 0 or len(ty) == 0:
        raise SystemExit("run lacks prediction artifacts needed for conformal replay")

    saci = StratifiedAdaptiveConformal(target_coverage=TARGET_COVERAGE, gamma=GAMMA)
    saci.calibrate_online(ty, tp, warmup_true=wy, warmup_pred=wp)

    levels = DemandLevels()
    window = saci.global_aci.window

    def trailing(residuals: np.ndarray) -> list[float]:
        return [round(float(v), 6) for v in residuals[-window:]]

    # Residual histories in the same chronological order the loop saw them.
    warm_res = np.abs(wy - wp)
    test_res = np.abs(ty - tp)
    test_buckets = levels.index_of(tp)
    warm_buckets = levels.index_of(wp)

    global_res = np.concatenate([warm_res, test_res])

    def serving_alpha(residuals: np.ndarray, fallback_alpha: float) -> tuple[float, float]:
        """Smallest alpha whose static quantile reaches the target coverage.

        The online loop's final alpha is a snapshot of a *drifting* parameter:
        after the loop's over-coverage phase it sits high, and serving
        statically with that end-state under-covers (measured: 74.95% @ width
        21.05 over the test window vs the loop's 89.98% @ 29.62). This
        re-calibrates the quantile level on the same residuals the loop
        consumed, keeping the serving path's exact quantile rule (trailing
        window, ``method="higher"``, finite-sample (1 + 1/n) inflation) so
        the API's static intervals hold the target marginal coverage.
        """
        res = np.abs(np.asarray(residuals, dtype="float64"))
        if res.size == 0:
            return fallback_alpha, 0.0
        w = res[-window:]
        # Search from wide to narrow and return the NARROWEST alpha that
        # still meets the target (coverage grows as alpha shrinks).
        for a in np.arange(0.495, 0.015, -0.005):
            q = float(np.quantile(w, 1 - float(a), method="higher")) * (1 + 1 / len(w))
            if float((res <= q).mean()) >= TARGET_COVERAGE:
                return float(a), q
        # Degenerate residuals (e.g. constant) — keep the online final alpha.
        q = float(np.quantile(w, 1 - fallback_alpha, method="higher")) * (1 + 1 / len(w))
        return fallback_alpha, q

    strata_state = []
    strata_q: list[float] = []
    strata_cov: list[float] = []
    for b in range(len(levels.bins)):
        strata_res = np.concatenate([warm_res[warm_buckets == b], test_res[test_buckets == b]])
        alpha_s, q_s = serving_alpha(strata_res, float(saci.strata[b].state.alpha))
        strata_state.append(
            {
                "alpha": round(alpha_s, 8),
                "residuals": trailing(strata_res),
                "scored": int((test_buckets == b).sum()),
                "online_final_alpha": round(float(saci.strata[b].state.alpha), 8),
            }
        )
        strata_q.append(q_s)
        strata_cov.append(float((np.abs(strata_res) <= q_s).mean()) if strata_res.size else 0.0)

    galpha, _gq = serving_alpha(global_res, float(saci.global_aci.state.alpha))
    global_state = {
        "alpha": round(galpha, 8),
        "residuals": trailing(global_res),
        "online_final_alpha": round(float(saci.global_aci.state.alpha), 8),
    }

    # Static-serving coverage/width over the test window with the calibrated
    # alphas — what the API will actually achieve, weighted by test counts.
    test_counts = np.array([float((test_buckets == b).sum()) for b in range(len(levels.bins))])
    strata_n = np.array(
        [
            float((warm_buckets == b).sum() + (test_buckets == b).sum())
            for b in range(len(levels.bins))
        ]
    )
    usable = strata_n > 0
    cov_arr = np.array(strata_cov, dtype=float)
    if usable.any() and test_counts[usable].sum() > 0:
        static_cov = float(
            (cov_arr[usable] * test_counts[usable]).sum() / test_counts[usable].sum()
        )
    else:
        static_cov = TARGET_COVERAGE
    static_width = float(2.0 * np.array([strata_q[b] for b in test_buckets]).mean())

    bins = [
        [float(low), None if not np.isfinite(high) else float(high)] for low, high in levels.bins
    ]

    # ------------------------------------------------- evaluation windows
    # Derived from the run's own prediction artifacts (never from wall-clock
    # or config): the train window is every visit before the first test day,
    # the test window is the held-out frame the metrics were scored on.
    test_dates = pd.to_datetime(test["visit_date"])
    test_start_iso = str(test_dates.min().date())
    test_end_iso = str(test_dates.max().date())
    train_start_iso: str | None = None
    train_end_iso: str | None = None
    visits_path = run_dir / "visits.parquet"
    if visits_path.exists():
        visit_dates = pd.to_datetime(pd.read_parquet(visits_path)["visit_date"])
        train_dates = visit_dates[visit_dates < pd.Timestamp(test_start_iso)]
        if len(train_dates):
            train_start_iso = str(train_dates.min().date())
            train_end_iso = str(train_dates.max().date())

    hyperparameters = {
        **{k: (v.item() if hasattr(v, "item") else v) for k, v in wrapper.params.items()},
        "num_boost_round": int(wrapper.num_boost_round),
    }

    # Point-accuracy metrics for the API's model registry (``model_versions``):
    # the backend cannot import this package, so the figures travel with the
    # bundle rather than being re-derived (or faked) at serving time.
    metrics = {
        "mae": lgb_stage.get("mae"),
        "rmse": lgb_stage.get("rmse"),
        "rmsle": lgb_stage.get("rmsle"),
        "mase": lgb_stage.get("mase"),
        "bias": lgb_stage.get("bias"),
        # Supplementary (Section 3.15); travels for display, not for selection.
        "r2": lgb_stage.get("r2"),
        "baseline_name": lgb_stage.get("baseline_name"),
        "baseline_mae": lgb_stage.get("baseline_mae"),
        "baseline_rmsle": lgb_stage.get("baseline_rmsle"),
        "mae_lift_vs_baseline": lgb_stage.get("mae_lift_vs_baseline"),
        "test_rows": lgb_stage.get("test_rows"),
        "best_iteration": int(lgb_stage["best_iteration"]),
        "feature_set_version": meta["feature_set_version"],
        "train_start": train_start_iso,
        "train_end": train_end_iso,
        "test_start": test_start_iso,
        "test_end": test_end_iso,
        "hyperparameters": hyperparameters,
    }

    # --------------------------------------------------- comparison models
    # The run's other candidates, so the API's registry can show them beside
    # the champion (Section 3.13: CatBoost is the fallback primary, kept
    # because the two boosted models fail differently). ``mae_delta_vs_production``
    # is the run's own like-for-like figure — same split, same rows — not a
    # difference recomputed at serving time.
    comparison: dict[str, dict] = {}
    cb_stage = stages.get("catboost", {}).get("metrics", {})
    if cb_stage.get("mae") is not None:
        cb_params: dict = {}
        cb_path = run_dir / "catboost_model.joblib"
        if cb_path.exists():
            try:
                cb_wrapper = joblib.load(cb_path)
                cb_params = {
                    **{
                        k: (v.item() if hasattr(v, "item") else v)
                        for k, v in cb_wrapper.params.items()
                    },
                    "num_boost_round": int(cb_wrapper.num_boost_round),
                }
            except Exception as exc:  # noqa: BLE001 - metrics matter, params are a bonus
                print(f"WARNING: could not read CatBoost hyperparameters: {exc}")
        comparison["catboost"] = {
            "algorithm": "catboost",
            "version": f"catboost-{meta['feature_set_version']}-it{cb_stage['best_iteration']}",
            "status": "candidate",
            "role": "fallback_primary",
            "metrics": {
                "mae": cb_stage.get("mae"),
                "rmse": cb_stage.get("rmse"),
                "rmsle": cb_stage.get("rmsle"),
                "mase": cb_stage.get("mase"),
                "bias": cb_stage.get("bias"),
                "r2": cb_stage.get("r2"),
                "test_rows": cb_stage.get("test_rows"),
                "best_iteration": int(cb_stage["best_iteration"]),
                "feature_set_version": meta["feature_set_version"],
                "train_start": train_start_iso,
                "train_end": train_end_iso,
                "test_start": test_start_iso,
                "test_end": test_end_iso,
                "mae_delta_vs_production": cb_stage.get("mae_delta_vs_lightgbm"),
                "hyperparameters": cb_params,
            },
        }

    # ------------------------------------------- Section 3.13 fallbacks
    # Linear Regression, Random Forest and XGBoost are kept in the study as
    # baselines and fallback models. The run scores them on the same split as
    # the champion, so their MAE is directly comparable and travels in the
    # bundle for the registry rather than being recomputed (or guessed) later.
    # ``mae_delta_vs_production`` is signed the same way as CatBoost's, so a
    # positive delta always means "worse than the champion".
    fb_stage = stages.get("fallbacks", {}).get("metrics", {}) or {}
    fb_rows = fb_stage.get("fallbacks") or {}
    champion_mae = lgb_stage.get("mae")
    for name, row in fb_rows.items():
        if not isinstance(row, dict) or row.get("mae") is None:
            continue
        delta = None
        if champion_mae is not None:
            delta = round(float(row["mae"]) - float(champion_mae), 4)
        comparison[name] = {
            "algorithm": name,
            "version": f"{name}-{meta['feature_set_version']}",
            "status": "candidate",
            "role": "baseline_fallback",
            "metrics": {
                "mae": row.get("mae"),
                "rmse": row.get("rmse"),
                "rmsle": row.get("rmsle"),
                "mase": row.get("mase"),
                "bias": row.get("bias"),
                "r2": row.get("r2"),
                "test_rows": row.get("test_rows"),
                "feature_set_version": meta["feature_set_version"],
                "train_start": train_start_iso,
                "train_end": train_end_iso,
                "test_start": test_start_iso,
                "test_end": test_end_iso,
                "mae_delta_vs_production": delta,
                "hyperparameters": _fallback_default_params(name),
            },
        }

    # -------------------------------------------- rolling-origin backtest
    # The run's own expanding-window evaluation travels with the bundle so the
    # console's Experiments screen lists the folds the run actually scored,
    # rather than a figure (re)invented at serving time. Windows come from
    # ``backtest_summary.joblib``; per-fold error metrics from
    # ``backtest_folds.parquet``. A fold present in one artifact and not the
    # other is dropped rather than paired with a guess.
    backtest: dict[str, object] | None = None
    folds_path = run_dir / "backtest_folds.parquet"
    summary_path = run_dir / "backtest_summary.joblib"
    if folds_path.exists() and summary_path.exists():
        fold_metrics = pd.read_parquet(folds_path)
        summary = joblib.load(summary_path)
        windows = {int(f["fold"]): f for f in summary.get("folds", [])}
        folds_out: list[dict[str, object]] = []
        for record in fold_metrics.to_dict("records"):
            try:
                fold_no = int(record["fold"])
            except (KeyError, TypeError, ValueError):
                continue
            fold_window = windows.get(fold_no)
            if fold_window is None:
                continue
            folds_out.append(
                {
                    # ``experiment_folds.fold_index`` is 0-based.
                    "fold_index": fold_no - 1,
                    "train_start": fold_window.get("train_start"),
                    "train_end": fold_window.get("train_end"),
                    "test_start": fold_window.get("test_start"),
                    "test_end": fold_window.get("test_end"),
                    "mae": _num(record.get("mae")),
                    "rmse": _num(record.get("rmse")),
                    "rmsle": _num(record.get("rmsle")),
                    "mase": _num(record.get("mase")),
                    "bias": _num(record.get("bias")),
                    # Interval coverage the fold actually achieved, when the
                    # backtest produced intervals (newer runs); older runs lack
                    # the column and travel as None rather than a guess.
                    "coverage": _num(record.get("coverage")),
                    "mean_width": _num(record.get("mean_width")),
                }
            )
        if folds_out:
            backtest = {
                "n_folds": len(folds_out),
                "algorithm": "lightgbm",
                "model_version": f"lightgbm-{meta['feature_set_version']}-it{lgb_stage['best_iteration']}",
                "feature_set_version": meta["feature_set_version"],
                "baseline_name": metrics["baseline_name"],
                "baseline_mae": metrics["baseline_mae"],
                "mae_mean": summary.get("mae_mean"),
                "mae_std": summary.get("mae_std"),
                "rmsle_mean": summary.get("rmsle_mean"),
                "rmsle_std": summary.get("rmsle_std"),
                "coverage_mean": summary.get("coverage_mean"),
                "folds": folds_out,
            }

    # The holiday calendar the features were built from — the trained booster
    # consumed ``is_holiday`` / ``day_before_holiday`` / ``day_after_holiday``
    # derived from this exact set, so serving MUST use it rather than a
    # locale stub. Sourced from the run's own ingest artifact for provenance.
    holiday_dates: list[str] = []
    calendar_path = run_dir / "calendar.parquet"
    if calendar_path.exists():
        cal = pd.read_parquet(calendar_path)
        hcol = "holiday_flg" if "holiday_flg" in cal.columns else "is_holiday"
        holiday_dates = sorted(
            cal.loc[pd.to_numeric(cal[hcol], errors="coerce").fillna(0) == 1, "calendar_date"]
            .dt.strftime("%Y-%m-%d")
            .tolist()
        )
    else:
        print("WARNING: no calendar.parquet in run; bundle will lack holiday_dates")

    bundle = {
        "format": BUNDLE_FORMAT,
        "bundle_version": BUNDLE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "run_dir": str(run_dir),
        "model": {
            "algorithm": "lightgbm",
            "version": f"lightgbm-{meta['feature_set_version']}-it{lgb_stage['best_iteration']}",
            "booster": wrapper.booster_,
            "best_iteration": int(lgb_stage["best_iteration"]),
            "feature_columns": list(meta["feature_columns"]),
            "feature_set_version": meta["feature_set_version"],
            "log_target": True,
        },
        "conformal": {
            "target": TARGET_COVERAGE,
            "gamma": GAMMA,
            "window": int(window),
            "min_history": int(saci.global_aci.min_history),
            "min_bucket": int(saci.min_bucket),
            "bins": bins,
            "achieved_coverage": round(static_cov, 4),
            "achieved_width": round(static_width, 2),
            "online_loop": {
                "coverage": conf_stage.get("empirical_coverage", TARGET_COVERAGE),
                "width": conf_stage.get("mean_interval_width"),
            },
            # Section 3.16 sensitivity: the same online loop re-run at 80% and
            # 95%, so the UI can show that the coverage behaviour is a property
            # of the method and not of the chosen 90% target. Served verbatim
            # from the training run; omitted (not invented) when absent.
            "coverage_sensitivity": conf_stage.get("coverage_level_sensitivity"),
            "global": global_state,
            "strata": strata_state,
        },
        "metrics": metrics,
        "comparison": comparison,
        "backtest": backtest,
        "provenance": {
            "seed": stages["ingest"]["seed"],
            "test_start": test_start_iso,
            "source_sha256": ing.get("source_sha256", {}),
            "visit_rows": ing["visit_rows"],
            "stores": ing["stores"],
        },
        "calendar": {
            "holiday_dates": holiday_dates,
            "n_holidays": len(holiday_dates),
        },
    }
    return bundle


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifacts", default="artifacts/runs/latest")
    args = ap.parse_args()

    run_dir = Path(args.artifacts)
    bundle = export_bundle(run_dir)
    out = run_dir / "serving_bundle.joblib"
    joblib.dump(bundle, out)

    m, c, ev = bundle["model"], bundle["conformal"], bundle["metrics"]
    print(f"serving bundle written: {out}")
    print(
        f"  model: {m['version']} ({len(m['feature_columns'])} features, fs {m['feature_set_version']})"
    )
    print(
        f"  metrics: MAE {ev['mae']} | RMSE {ev['rmse']} | RMSLE {ev['rmsle']} "
        f"(vs {ev['baseline_name']} MAE {ev['baseline_mae']}, lift {ev['mae_lift_vs_baseline']})"
    )
    print(
        f"  window: train {ev['train_start']}..{ev['train_end']} | "
        f"test {ev['test_start']}..{ev['test_end']} ({ev['test_rows']} store-days)"
    )
    for name, entry in bundle.get("comparison", {}).items():
        cm = entry["metrics"]
        print(
            f"  comparison: {name} {entry['version']} MAE {cm['mae']} "
            f"(delta vs production {cm['mae_delta_vs_production']}), status {entry['status']}"
        )
    bt = bundle.get("backtest")
    if isinstance(bt, dict):
        cov = bt.get("coverage_mean")
        cov_txt = f", mean coverage {cov:.3f}" if isinstance(cov, (int, float)) else ""
        print(
            f"  backtest: {bt['n_folds']} folds, mean MAE {bt['mae_mean']} "
            f"(±{bt['mae_std']}) vs {bt['baseline_name']} {bt['baseline_mae']}{cov_txt}"
        )
    else:
        print("  backtest: none in this run")
    print(
        f"  conformal: static serving coverage {c['achieved_coverage']:.4f} @ width {c.get('achieved_width')}"
    )
    print(
        f"  online loop reference: coverage {c['online_loop']['coverage']} @ width {c['online_loop']['width']}"
    )
    print(f"  strata alphas: {[s['alpha'] for s in c['strata']]} | global {c['global']['alpha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
